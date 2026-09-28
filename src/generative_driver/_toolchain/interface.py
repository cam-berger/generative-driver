"""Schema-3 integration of the shared runtime into the original pipeline."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import core

import interface_runtime
from interface_runtime.engine import SCHEMAS, validate_model
from interface_runtime.evidence import read_json, read_json_hashed, run_samples, check_probe, MAX_EVIDENCE_BYTES, probe_coverage, COVERAGE_SCOPE
from interface_runtime.package import digest, package_files, verify_package, inspect_package
from interface_runtime.faults import classify_failure, LEGACY_TRANSPORT_CODES as TRANSPORT_CODES
from interface_runtime.transports import TransportError, capabilities, validate_binding

RUNTIME = Path(interface_runtime.__file__).resolve().parent
TEMPLATE = Path(core.BENCH)/'toolchain/template/interface_driver'


def fault_class(code, identity_verified):
    """Compatibility for older callers; new callers consume the runtime's classified failure result."""
    fault = classify_failure({'ok':False, 'identity_verified':identity_verified, 'error':{'code':code}})
    return 'model' if fault == 'model' else 'transport'
# The package's own command line, run from a verified snapshot without the caller's import path.
BOOT = 'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")'
# The trusted binding a package needs, per channel type: the --binding form and what the operator supplies.
BINDINGS = {
    'uart': ('{"port": "<serial device path>"}', 'port is the host serial device the operator chooses.'),
    'tcp': ('{"host": "<host name or address>", "port": <int>}', 'port is a JSON integer.'),
    'i2c': ('{"url": "ftdi://<adapter>/<interface>"}', 'url selects the FTDI adapter and must start with ftdi://.'),
    'spi': ('{"url": "ftdi://<adapter>/<interface>"}', 'url selects the FTDI adapter and must start with ftdi://.'),
    'usb': ('{"vendor_id": <int>, "product_id": <int>, "serial_number": "<str>"}',
            'vendor_id and product_id are JSON integers (decimal, never hex strings) that the operator supplies; '
            'serial_number selects one unit and may be left out only when exactly one matching device is attached. '
            'Two optional booleans, both false by default, widen what a session may do: allow_set_configuration lets '
            'it select the default configuration of an unconfigured device, and allow_detach_kernel_driver lets it '
            'detach a kernel driver from the interface the model names and reattach it on close.'),
}


def path(value):
    p = Path(value)
    return p if p.is_absolute() else Path(core.BENCH)/p


def load(model_dir):
    model_path = path(model_dir)/'model.json'
    return read_json_hashed(model_path, 1048576)


@core.tool('interface_describe')
def interface_describe(model_dir, **_):
    model, sha = load(model_dir)
    checked = validate_model(model)
    if not checked['ok']:
        return {**checked,'_exit':1}
    return {'ok':True,'schema':model['schema'],'model_sha256':sha,'device':model['device'],
            'channel':model['channel'],'identity':model['identity'],'operations':model['operations'],
            'capabilities':capabilities()}


@core.tool('interface_execute')
def interface_execute(model_dir, operation, parameters=None, binding=None, allow_effects=None, replay=None, **_):
    model, sha = load(model_dir)
    execution = run_samples(model, operation, parameters, binding=binding,
                            allow_effects=() if allow_effects is None else allow_effects,
                            replay=read_json(path(replay)) if replay is not None else None)
    result = execution['results'][0]
    return {**result,'evidence_kind':execution['evidence_kind'],'timing_policy':execution['timing_policy'],
            'model_sha256':sha,
            'transactions':execution['transactions'],'_exit':0 if execution['ok'] else 1}


def probe_run(run_dir, model_dir, n=1, operation=None, parameters=None, binding=None, allow_effects=None, replay=None):
    model, sha = load(model_dir)
    if operation is None:
        raise ValueError('schema 3 probe requires an explicit operation name')
    grants = [] if allow_effects is None else allow_effects
    result = run_samples(model, operation, parameters, n=n, binding=binding, allow_effects=grants,
                         replay=read_json(path(replay)) if replay is not None else None)
    envelope = {'schema':'interface-probe/1','model_sha256':sha,'operation':operation,
                'parameters':{} if parameters is None else parameters,'allow_effects':grants,**result}
    folder = Path(core.resolve_run(run_dir))/'probe'; folder.mkdir(parents=True, exist_ok=True)
    # Append artifacts so a later failure cannot replace the earlier evidence.
    index = 1
    while (folder/f'interface-{index}.json').exists():
        index += 1
    target = folder/f'interface-{index}.json'
    serialized = json.dumps(envelope, indent=2, allow_nan=False)
    if len(serialized.encode('utf-8')) > MAX_EVIDENCE_BYTES:
        raise ValueError('probe serialization exceeds evidence reader bound')
    with target.open('x', encoding="utf-8") as stream:
        stream.write(serialized)
    code, message, fault = failure(result['results']) if not result['ok'] else (None, None, None)
    return {'ok':result['ok'],'probe':str(target),'evidence_kind':result['evidence_kind'],
            'results':result['results'],'error_code':code,'error_message':message,'fault':fault,
            '_exit':0 if result['ok'] else 1 if fault == 'model' else 4,
            '_files':{str(target):digest(target)}}


def failure(results):
    """The error code, message and fault class of the last failed result; run_samples stops at the first
    failure."""
    for result in reversed(results):
        if type(result) is dict and result.get('ok') is not True:
            error = result.get('error') if type(result.get('error')) is dict else {}
            return error.get('code'), error.get('message'), classify_failure(result)
    return None, None, None


def _failure_defects(evidence):
    """One defect per failed result in the runtime's own words: where its transcript stopped, the error code
    and message, and that step's wire bytes, so an interpret retry sees what the device did."""
    defects = []
    for sample, result in enumerate(evidence['results'], 1):
        if type(result) is not dict or result.get('ok') is not False:
            continue
        error = result.get('error') if type(result.get('error')) is dict else {}
        transcript = result.get('transcript') if type(result.get('transcript')) is list else []
        last = transcript[-1] if transcript and type(transcript[-1]) is dict else None
        code = error.get('code')
        fault = classify_failure(result)
        if last is None:
            where = ('transport' if fault != 'model' else 'channel' if code == 'channel'
                     else f"operations.{result.get('operation')}")
            wire = {'tx_hex':'','rx_hex':'','status':'not_started'}
        else:
            where = f"operations.{last.get('operation')}.steps.{last.get('step')}"
            wire = {key:last.get(key) for key in ('tx_hex','rx_hex','status')}
            if 'setup' in last:
                wire['setup'] = last['setup']
        defects.append({'where':where,'what':error.get('message') or 'execution failed without a message',
                        'code':code,'fault':fault,'sample':sample,'evidence':wire})
    return defects


def probe_diff(model_dir, probe):
    """Replay consistency for a successful probe; for a failed one, the failure itself as defects. Exit 4 when
    every failure is an operator or host fault, since that probe says nothing about the model."""
    model, sha = load(model_dir)
    evidence = read_json(path(probe))
    checked = check_probe(model, evidence, sha)
    if checked['ok']:
        return {**checked,'_exit':0}
    failed = []
    if (type(evidence) is dict and evidence.get('schema') == 'interface-probe/1' and evidence.get('model_sha256') == sha
            and evidence.get('ok') is False and type(evidence.get('results')) is list):
        failed = _failure_defects(evidence)
    defects = [d for d in failed if d['fault'] == 'model']
    diagnostics = [d for d in failed if d['fault'] != 'model']
    # A failed observation need not replay as success. Keep envelope/replay diagnostics away from
    # interpret too: neither a missing grant nor damaged evidence establishes a model defect.
    diagnostics.extend({**d,'fault':'host'} for d in checked['defects'])
    return {**checked,'defects':defects,'diagnostics':diagnostics,'_exit':1 if defects else 4}


def binding_reference(model):
    """REFERENCE.md paragraphs naming the binding form for the model's channel and the effect grants its
    operations need; rendered from the channel type and the model, never from the device."""
    kind = model['channel']['type']
    form, note = BINDINGS[kind]
    effects = {}
    for name, operation in model['operations'].items():
        effects.setdefault(operation['effect'], []).append(name)
    grants = ' '.join(f'Operations that need `--allow-effect {effect}`: {", ".join(effects[effect])}.'
                      for effect in ('write', 'actuate') if effect in effects) or 'Every operation is a read.'
    return (f'The channel is {kind}. Supply its trusted physical binding as JSON:\n\n'
            f"```sh\npython -m driver execute {model['identity']['operation']} --binding '{form}'\n```\n\n"
            f'{note} The binding is the operator\'s choice; models cannot select host paths, devices or network '
            'destinations.\n\n'
            'Read effects are allowed by default; write and actuate each require an explicit grant, '
            '`--allow-effect write` or `--allow-effect actuate`, repeated once per effect. '
            f'{grants} These are grants to declarations, not proof arbitrary bytes are safe.\n\n')


def coverage_reference(model, evidence):
    rows = ['| Operation | Effect | Bundled status | Evidence | Samples attempted / passed / failed | Parameters |',
            '| --- | --- | --- | --- | --- | --- |']
    for name, entry in probe_coverage(model, evidence).items():
        counts = entry['samples']
        parameters = json.dumps(entry['parameters'],sort_keys=True,ensure_ascii=True).replace('|','\\u007c')
        rows.append(f"| {name} | {entry['effect']} | {entry['status']} | {entry['evidence_kind'] or 'none'} | "
                    f"{counts['attempted']} / {counts['passed']} / {counts['failed']} | `{parameters}` |")
    return '## Bundled probe coverage\n\n' + '\n'.join(rows) + '\n\n' + COVERAGE_SCOPE + '\n\n'


def emit_package(run_dir, model_dir, probe, name=None, version='1.0.0', **_):
    model, sha = load(model_dir)
    evidence = read_json(path(probe))
    checked = check_probe(model, evidence, sha)
    if not checked['ok']:
        return {**checked,'_exit':1}
    parent = Path(core.resolve_run(run_dir))/'emit'; parent.mkdir(parents=True, exist_ok=True)
    pkg = Path(tempfile.mkdtemp(prefix='interface-package-', dir=parent))
    shutil.copytree(TEMPLATE, pkg/'driver', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(RUNTIME, pkg/'interface_runtime', ignore=shutil.ignore_patterns('tests','__pycache__'))
    shutil.copyfile(path(model_dir)/'model.json', pkg/'driver/model.json')
    shutil.copyfile(path(probe), pkg/'probe.json')
    if digest(pkg/'driver/model.json') != sha or read_json(pkg/'probe.json') != evidence:
        shutil.rmtree(pkg)
        raise ValueError('model or probe changed during packaging')
    requires = {'uart':['pyserial>=3.5'],'usb':['pyusb>=1.2'],'i2c':['pyftdi>=0.55'],
                'spi':['pyftdi>=0.55'],'tcp':[]}[model['channel']['type']]
    (pkg/'requirements.txt').write_text('\n'.join(requires)+'\n', encoding="utf-8")
    reference = (
        '# Executable device interface\n\n'
        f'This package executes its bundled {model["schema"]} with the same shared runtime as the toolchain. '
        'Provenance entries are model claims. Replay confirms internal consistency, not physical correctness.\n\n'
        f'Source evidence: **{evidence["evidence_kind"]}**. Model SHA-256: `{sha}`. '
        'No accuracy, full device coverage or formal standard conformance is implied.\n\n'
        'Use Python 3.11+ from this directory. Offline replay needs only the standard library. '
        'Live transports require the optional packages listed in requirements.txt and OS/backend drivers.\n\n'
        '```sh\npython -m driver describe\npython -m driver replay\n'
        'python -m driver execute OPERATION --parameters \'{}\' --binding \'{}\'\n```\n\n'
        + binding_reference(model) +
        coverage_reference(model, evidence) +
        'Identity is checked before the selected operation, which runs once without whole-command retry.\n\n'
        'Manifest and file hashes detect alteration. Retain the emitted manifest SHA-256 outside the package '
        'to verify against a trusted pin; an attacker replacing every file and checksum cannot be detected '
        'by a self-contained checksum alone. Hardware verification requires a separate physical test.\n')
    (pkg/'REFERENCE.md').write_text(reference, encoding="utf-8")
    manifest = {'schema':'interface-package/1','name':name or 'device-interface','version':version,
                'model_sha256':sha,'probe_sha256':digest(pkg/'probe.json'),
                'source_evidence_kind':evidence['evidence_kind'],'files':package_files(pkg)}
    (pkg/'manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n', encoding="utf-8")
    manifest_sha = digest(pkg/'manifest.json')
    (pkg/'manifest.sha256').write_text(manifest_sha+'\n', encoding="utf-8")
    return {'ok':True,'package_dir':str(pkg),'manifest_sha256':manifest_sha,
            'evidence_kind':evidence['evidence_kind'],'_files':{str(pkg/'manifest.json'):manifest_sha}}


def _trusted_python():
    trusted = {'interface_runtime/'+p.name:digest(p) for p in RUNTIME.glob('*.py')}
    trusted.update({'driver/'+p.name:digest(p) for p in TEMPLATE.glob('*.py')})
    return trusted


def _snapshot(pkg, sha, expected_manifest_sha256, temporary):
    """Copy a verified package's allowlisted files into `temporary` and verify the copy, whose Python must equal
    the trusted runtime and template. Returns (snapshot, manifest, None) or (None, None, refusal)."""
    trusted = _trusted_python()
    checked = inspect_package(pkg, trusted, sha, expected_manifest_sha256)
    if not checked['ok']:
        return None, None, {**checked,'_exit':1}
    if not checked['runtime_current']:
        return None, None, {**checked,'ok':False,'replay_status':'not_run',
                            'defects':[{'where':'runtime','what':'execution requires current trusted runtime/templates'}],'_exit':1}
    allowlist = set(trusted) | {'driver/model.json','probe.json','REFERENCE.md','requirements.txt'}
    if set(checked['manifest']['files']) != allowlist:
        return None, None, {'ok':False,'defects':[{'where':'package','what':'unexpected or missing package artifact'}],'_exit':1}
    # Execute only a freshly verified allowlisted snapshot. Existing bytecode or native import sidecars
    # never enter it. Preserve symlinks during copying so verification rejects them without following.
    snapshot = Path(temporary)/'package'; snapshot.mkdir()
    for name in sorted(allowlist | {'manifest.json','manifest.sha256'}):
        target = snapshot/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(pkg/name, target, follow_symlinks=False)
    verified = verify_package(snapshot, sha, expected_manifest_sha256)
    if not verified['ok']:
        return None, None, {**verified,'_exit':1}
    if trusted != {k:v for k,v in verified['manifest']['files'].items() if k.endswith('.py')}:
        return None, None, {'ok':False,'defects':[{'where':'runtime','what':'bundled Python differs from trusted runtime/templates'}],'_exit':1}
    return snapshot, verified['manifest'], None


def emit_check(package_dir, model_dir=None, expected_manifest_sha256=None):
    pkg = path(package_dir)
    model, sha = load(pkg/'driver' if model_dir is None else model_dir)
    inspected = inspect_package(pkg, _trusted_python(), sha, expected_manifest_sha256)
    if not inspected['ok']:
        return {**inspected,'replay_status':'not_run','_exit':1}
    if not inspected['runtime_current']:
        return {'ok':True,'defects':[],'integrity_ok':True,'runtime_current':False,
                'manifest_pin_verified':expected_manifest_sha256 is not None,
                'replay_status':'not_run','replay_reason':'bundled Python differs from current trusted runtime/templates; integrity only',
                'source_evidence_kind':inspected['manifest']['source_evidence_kind'],'_exit':0}
    with tempfile.TemporaryDirectory() as temporary:
        snapshot, manifest, refused = _snapshot(pkg, sha, expected_manifest_sha256, temporary)
        if refused is not None:
            return refused
        result = _check_snapshot(snapshot, model, sha, manifest, expected_manifest_sha256)
        return {**result,'integrity_ok':True,'runtime_current':True,
                'replay_status':'passed' if result['ok'] else 'failed'}


def _check_snapshot(pkg, model, sha, manifest, expected_manifest_sha256):
    evidence = read_json(pkg/'probe.json')
    compared = check_probe(model, evidence, sha)
    if manifest['source_evidence_kind'] != evidence.get('evidence_kind'):
        compared = {'ok':False,'defects':[{'where':'evidence_kind','what':'manifest and probe disagree'}]}
    if not compared['ok']:
        return {**compared,'_exit':1}
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run([sys.executable,'-I','-B','-c',BOOT,
            str(pkg.resolve()),'replay'], cwd=cwd, env={**os.environ,'PYTHONPATH':''},
            capture_output=True,text=True,timeout=260)
    try:
        actual = json.loads(result.stdout)
    except ValueError:
        actual = None
    ok = result.returncode == 0 and actual == compared
    return {'ok':ok,'defects':[] if ok else [{'where':'package replay','what':'standalone replay differs'}],
            'evidence_kind':'replay','source_evidence_kind':evidence['evidence_kind'],
            'manifest_pin_verified':expected_manifest_sha256 is not None,'_exit':0 if ok else 1}


def is_interface_package(package_dir):
    try:
        manifest = read_json(path(package_dir)/'manifest.json', 1048576)
    except (OSError, ValueError):
        return False
    return type(manifest) is dict and manifest.get('schema') == 'interface-package/1'


def _live_call(operation, command):
    """One package command-line call; the verdict needs exit 0, ok true and a verified identity."""
    with tempfile.TemporaryDirectory() as cwd:
        try:
            done = subprocess.run(command, cwd=cwd, env={**os.environ,'PYTHONPATH':''},
                                  capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {'operation':operation,'command':command,'returncode':None,'stdout_json':None,'stderr':'',
                    'ok':False,'error_code':'timeout','error_message':'package call exceeded 300 s','fault':'host'}
        except OSError as exc:
            return {'operation':operation,'command':command,'returncode':None,'stdout_json':None,'stderr':'',
                    'ok':False,'error_code':'open','error_message':str(exc),'fault':'host'}
    try:
        result = json.loads(done.stdout)
    except ValueError:
        result = None
    ok = (done.returncode == 0 and type(result) is dict and result.get('ok') is True
          and result.get('identity_verified') is True)
    error = result.get('error') if type(result) is dict and type(result.get('error')) is dict else {}
    if ok:
        code = message = None
    elif type(result) is not dict:
        code, message = 'package_output', 'package printed no JSON result'
    elif error:
        code, message = error.get('code'), error.get('message')
    else:
        code, message = 'identity_unverified', 'package reported success without a verified identity'
    fault = None if ok else classify_failure({'ok':False,
        'identity_verified':result.get('identity_verified') if type(result) is dict else None,
        'error':error or {'code':code,'message':message,'fault':'host'}})
    return {'operation':operation,'command':command,'returncode':done.returncode,'stdout_json':result,
            'stderr':done.stderr[-2000:],'ok':ok,'error_code':code,'error_message':message,'fault':fault}


def emit_test_live(run_dir, package_dir, binding, operations=None, parameters=None, allow_effects=None,
                   model_dir=None, expected_manifest_sha256=None):
    """Run each named operation once through the package's own command line against the live device.

    The package runs from a freshly verified allowlisted snapshot whose Python equals the trusted runtime and
    template, under the bench interpreter in isolated mode, so a pass is evidence about these package bytes and
    this binding. The binding is the operator's and is passed through unchanged; it is checked against the
    model's channel, and every named operation's effect against the grants, before any call, so an operator
    error is refused (exit 1) with no live record instead of being recorded as a package failure. The plan
    stops at the first operator or host fault (exit 4, inconclusive). Every call is recorded append-only in
    emit/live-N.json."""
    if type(binding) is not dict:
        raise ValueError('an interface package live test requires a binding object')
    grants = [] if allow_effects is None else allow_effects
    if type(grants) is not list or any(g not in ('read','write','actuate') for g in grants):
        raise ValueError('allow_effects must be a list of read, write or actuate')
    arguments = {} if parameters is None else parameters
    if type(arguments) is not dict or any(type(v) is not dict for v in arguments.values()):
        raise ValueError('parameters must map operation names to argument objects')
    sha = load(model_dir)[1] if model_dir is not None else None
    pkg = path(package_dir)
    started = core.now()
    with tempfile.TemporaryDirectory() as temporary:
        snapshot, manifest, refused = _snapshot(pkg, sha, expected_manifest_sha256, temporary)
        if refused is not None:
            return refused
        manifest_sha = digest(snapshot/'manifest.json')
        model = read_json(snapshot/'driver/model.json', 1048576)
        names = [model['identity']['operation']] if operations is None else operations
        if (type(names) is not list or not names
                or any(type(n) is not str or n not in model['operations'] for n in names)):
            raise ValueError("operations must be a nonempty list of the model's operation names")
        if set(arguments) - set(names):
            raise ValueError('parameters name an operation that is not tested')
        try:
            validate_binding(model['channel'], binding)
        except TransportError as exc:
            raise ValueError(f'binding refused before any call: {exc}') from None
        ungranted = [n for n in names if model['operations'][n]['effect'] not in ['read', *grants]]
        if ungranted:
            raise ValueError('operations need an effect grant that allow_effects does not give: '
                             + ', '.join(f"{n} ({model['operations'][n]['effect']})" for n in ungranted))
        flags = [flag for g in grants if g != 'read' for flag in ('--allow-effect', g)]
        results = []
        for name in names:
            results.append(_live_call(name, [core.VENV_PY,'-I','-B','-c',BOOT,str(snapshot),'execute',name,
                '--parameters',json.dumps(arguments.get(name,{}),allow_nan=False),
                '--binding',json.dumps(binding,allow_nan=False),*flags]))
            if results[-1]['fault'] in ('operator','host'):
                break
    passed = sum(1 for r in results if r['ok'])
    not_run = names[len(results):]
    ok = passed == len(names)
    transport = any(r['fault'] in ('operator','host') for r in results)
    verdict = 'pass' if ok else 'inconclusive' if transport else 'fail'
    record = {'schema':'interface-live-test/1','package_dir':str(pkg),'manifest_sha256':manifest_sha,
              'model_sha256':manifest['model_sha256'],'interpreter':core.VENV_PY,'binding':binding,
              'operations':names,'parameters':arguments,'allow_effects':grants,'started':started,
              'finished':core.now(),'results':results,'passed':passed,'failed':len(results)-passed,
              'not_run':not_run,'ok':ok,'verdict':verdict}
    folder = Path(core.resolve_run(run_dir or 'scratch'))/'emit'; folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while (folder/f'live-{index}.json').exists():
        index += 1
    target = folder/f'live-{index}.json'
    with target.open('x', encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False))
    summary = [{'operation':r['operation'],'ok':r['ok'],'returncode':r['returncode'],'error_code':r['error_code'],
                'error_message':r['error_message'],'fault':r['fault'],
                **{k:r['stdout_json'].get(k) for k in ('identity_verified','outputs','units')
                   if type(r['stdout_json']) is dict}} for r in results]
    return {'ok':ok,'passed':passed,'failed':len(results)-passed,'not_run':not_run,'verdict':verdict,
            'results':summary,'live_record':str(target),'manifest_sha256':manifest_sha,
            '_exit':0 if ok else 4 if transport else 1,'_files':{str(target):digest(target)}}


def model_qualify(run_dir, model_dir, replies, **_):
    """Offline qualification of a schema-3/4 stream model against supplied replies; never opens hardware."""
    from interface_runtime.qualify import QualifyError, qualify, summary
    model, sha = load(model_dir)
    try:
        report = qualify(model, read_json(path(replies), 4194304))
    except QualifyError as exc:
        return {'ok':False,'defects':[{'where':'qualify','what':str(exc)}],'model_sha256':sha,'_exit':1}
    folder = Path(core.resolve_run(run_dir))/'interpret'; folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while (folder/f'qualification-{index}.json').exists():
        index += 1
    target = folder/f'qualification-{index}.json'
    with target.open('x', encoding="utf-8") as stream:
        json.dump({'model_sha256':sha,'replies_sha256':digest(path(replies)),**report}, stream, indent=2, allow_nan=False)
    return {'ok':report['all_supplied_qualify'],'qualified':report['qualified'],'not_qualified':report['not_qualified'],
            'uncovered':report['uncovered'],'summary':summary(report),'report':str(target),'model_sha256':sha,
            '_exit':0 if report['all_supplied_qualify'] else 1,'_files':{str(target):digest(target)}}

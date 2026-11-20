"""The tools that run an interface model and its package on the shared runtime.

A package is five files of data. The kit builds it, checks it and runs it with its own installed runtime
(docs/adr/0010-a-package-is-data-in-one-format.md)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import core

import interface_runtime
from interface_runtime.engine import format_refusal, validate_model
from interface_runtime.evidence import (read_json, read_json_hashed, run_samples, check_probe, MAX_EVIDENCE_BYTES,
                                        ReplayFactory, _evidence_bound)
from interface_runtime.package import digest, package_files, text_defect, verify_package, the_driver
from interface_runtime.package import SCHEMA as PACKAGE
from interface_runtime.faults import classify_failure, LEGACY_TRANSPORT_CODES as TRANSPORT_CODES
from interface_runtime.transports import TransportError, capabilities, validate_binding

RUNTIME = Path(interface_runtime.__file__).resolve().parent


def fault_class(code, identity_verified):
    """Compatibility for older callers; new callers consume the runtime's classified failure result."""
    fault = classify_failure({'ok':False, 'identity_verified':identity_verified, 'error':{'code':code}})
    return 'model' if fault == 'model' else 'transport'


def path(value):
    p = Path(value)
    return p if p.is_absolute() else Path(core.BENCH)/p


def document_bytes(document):
    """The bytes a JSON file of a package holds for `document`, the same on every host."""
    return (json.dumps(document, indent=2, allow_nan=False) + '\n').encode('utf-8')


def load(model_dir):
    model_path = path(model_dir)/'model.json'
    return read_json_hashed(model_path, 1048576)


def unknown_format(model):
    """Why the kit refuses `model` for its format, naming the schema found and the one the installed runtime reads, or
    None for that one format. A tool asks this before it reads any other file: a model is data the installed runtime
    executes, so a model of another format is refused and nothing of its directory is loaded
    (docs/adr/0010-a-package-is-data-in-one-format.md)."""
    return format_refusal(model)


@core.tool('interface_describe')
def interface_describe(model_dir, **_):
    model, sha = load(model_dir)
    checked = validate_model(model)
    if not checked['ok']:
        return {**checked,'_exit':1}
    return {'ok':True,'schema':model['schema'],'model_sha256':sha,'device':model['device'],
            'channel':model['channel'],'identity':model['identity'],'operations':model['operations'],
            'capabilities':capabilities()}


def before_contact(error, **extra):
    """A tool's refusal before it contacted the device: no transport was opened, so nothing reached the device."""
    return {'ok':False,'error':error,**extra,'transport_open_attempted':False,'_exit':1}


def host_before_contact(exc):
    """A host failure before a tool contacted the device (its temporary folder, a snapshot copy it cannot read back):
    nothing reached the device, and it says nothing about the tool's input. Fault host, exit 4."""
    return {'ok':False,'fault':'host','error':f'{type(exc).__name__}: {exc}','transport_open_attempted':False,'_exit':4}


def _preflight(model, operation, parameters, allow_effects, replay, n=1):
    """The checks run_samples makes before it opens a transport, made here so that each refusal can be declared:
    the evidence bound of a validated model for `n` samples (which also names an unknown operation) and the replay
    fixture."""
    if _evidence_bound(model, operation, parameters, allow_effects, n) > MAX_EVIDENCE_BYTES:
        raise ValueError('requested probe exceeds evidence bound; reduce samples, steps or output expansion')
    if replay is not None:
        ReplayFactory(replay)


@core.tool('interface_execute')
def interface_execute(model_dir, operation, parameters=None, binding=None, allow_effects=None, replay=None, **_):
    """Execute one operation of a model once. Every refusal before the transport opens (a model.json that is missing,
    unreadable or not strict bounded JSON, an invalid model, an unknown operation, an evidence bound the operation
    exceeds, a replay fixture) is returned with transport_open_attempted false and exit 1."""
    grants = () if allow_effects is None else allow_effects
    try:
        if not (path(model_dir)/'model.json').is_file():
            raise ValueError('model_dir holds no model.json')
        model, sha = load(model_dir)
    except (OSError, ValueError, TypeError, LookupError, RecursionError) as exc:
        return before_contact(f'{type(exc).__name__}: {exc}')
    checked = validate_model(model)
    if not checked['ok']:
        # The runtime refuses this model before it opens any transport; say so, in the runtime's words.
        return before_contact('ValueError: invalid model: ' + str(checked['defects']), model_sha256=sha)
    try:
        fixture = read_json(path(replay)) if replay is not None else None
        _preflight(model, operation, parameters, grants, fixture)
    except (OSError, ValueError, TypeError, LookupError, RecursionError) as exc:
        return before_contact(f'{type(exc).__name__}: {exc}', model_sha256=sha)
    execution = run_samples(model, operation, parameters, binding=binding, allow_effects=grants, replay=fixture)
    result = execution['results'][0]
    return {**result,'evidence_kind':execution['evidence_kind'],'timing_policy':execution['timing_policy'],
            'model_sha256':sha,
            'transactions':execution['transactions'],'_exit':0 if execution['ok'] else 1}


def probe_run(run_dir, model_dir, n=1, operation=None, parameters=None, binding=None, allow_effects=None, replay=None):
    """Run one operation of a model `n` times and record the evidence. Every refusal before the transport opens (a
    model.json that is missing, unreadable or not strict bounded JSON, a format the runtime does not know, an invalid
    model, a missing or unknown operation, a sample count outside 1..100, an evidence bound the samples exceed, a replay
    fixture) is returned with transport_open_attempted false and exit 1, and writes no record."""
    grants = [] if allow_effects is None else allow_effects
    try:
        if not (path(model_dir)/'model.json').is_file():
            raise ValueError('model_dir holds no model.json')
        model, sha = load(model_dir)
    except (OSError, ValueError, TypeError, LookupError, RecursionError) as exc:
        return before_contact(f'{type(exc).__name__}: {exc}')
    try:
        if unknown_format(model):
            raise ValueError(unknown_format(model))
        if operation is None:
            raise ValueError('a probe requires an explicit operation name')
        if type(n) is not int or not 1 <= n <= 100:
            raise ValueError('sample count must be integer 1..100')
        checked = validate_model(model)
        if not checked['ok']:
            raise ValueError('invalid model: ' + str(checked['defects']))
        fixture = read_json(path(replay)) if replay is not None else None
        _preflight(model, operation, parameters, grants, fixture, n)
    except (OSError, ValueError, TypeError, LookupError, RecursionError) as exc:
        return before_contact(f'{type(exc).__name__}: {exc}', model_sha256=sha)
    result = run_samples(model, operation, parameters, n=n, binding=binding, allow_effects=grants, replay=fixture)
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
    every failure is an operator or host fault, since that probe says nothing about the model. A model of a format the
    runtime does not know is refused as a defect (exit 1) before the probe is read."""
    model, sha = load(model_dir)
    if unknown_format(model):
        return {'ok':False,'defects':[{'where':'schema','what':unknown_format(model)}],'_exit':1}
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


def rendered_files(drivers, evidence):
    """The bytes of the two files of a package that the tool renders, `mcp.json` and `README.md`, for `drivers`, which
    maps each driver's name to its interface model, and `evidence`, which maps the same names to their probe records."""
    from interface_runtime.catalog import render_mcp
    from .readme import render_readme
    return {'mcp.json':document_bytes(render_mcp(drivers)),
            'README.md':render_readme(drivers, evidence).encode('utf-8')}


def emit_package(run_dir, model_dir, probe, name=None, version='1.0.0', **_):
    """Build the package of a model and the probe record it wrote, in a new folder of the run's emit directory: five
    files of data and no code (docs/adr/0010-a-package-is-data-in-one-format.md). A model of a format the runtime does
    not know is refused before the probe is read, and no folder is made.

    The driver file is the model byte for byte and the evidence file is the probe record byte for byte. `mcp.json` and
    `README.md` are what the tool renders from them, so nothing else in the model directory is read. `name` names the
    package; its driver is named after the device. A `name` or a `version` the package check would refuse in a
    manifest is refused by the argument's name before anything is read or written."""
    from interface_runtime.catalog import driver_name
    # A package without a name of its own has its driver's name, which the check always accepts.
    given = ([('name', name)] if name else []) + [('version', version)]
    refused = [{'where':argument,'what':text_defect(argument, value)} for argument, value in given
               if text_defect(argument, value) is not None]
    if refused:
        return {'ok':False,'defects':refused,'_exit':1}
    model, sha = load(model_dir)
    if unknown_format(model):
        return {'ok':False,'defects':[{'where':'schema','what':unknown_format(model)}],'_exit':1}
    evidence = read_json(path(probe))
    checked = check_probe(model, evidence, sha)
    if not checked['ok']:
        return {**checked,'_exit':1}
    driver = driver_name(model)
    entry = {'name':driver,'model':f'drivers/{driver}.json','evidence':f'evidence/{driver}.probe.json',
             'evidence_kind':evidence['evidence_kind']}
    parent = Path(core.resolve_run(run_dir))/'emit'; parent.mkdir(parents=True, exist_ok=True)
    pkg = Path(tempfile.mkdtemp(prefix='interface-package-', dir=parent))
    for source, target in ((path(model_dir)/'model.json', entry['model']), (path(probe), entry['evidence'])):
        (pkg/target).parent.mkdir()
        shutil.copyfile(source, pkg/target)
    if digest(pkg/entry['model']) != sha or read_json(pkg/entry['evidence']) != evidence:
        shutil.rmtree(pkg)
        raise ValueError('model or probe changed during packaging')
    for filename, rendered in rendered_files({driver:model}, {driver:evidence}).items():
        (pkg/filename).write_bytes(rendered)
    manifest = {'schema':PACKAGE,'name':name or driver,'version':version,'drivers':[entry],
                'files':package_files(pkg)}
    (pkg/'manifest.json').write_bytes(document_bytes(manifest))
    manifest_sha = digest(pkg/'manifest.json')
    return {'ok':True,'package_dir':str(pkg),'manifest_sha256':manifest_sha,
            'evidence_kind':evidence['evidence_kind'],'_files':{str(pkg/'manifest.json'):manifest_sha}}


def _read_back(copy):
    """Read every file of a package's copy, raising the host's OSError."""
    package_files(copy)
    digest(copy/'manifest.json')


def refusal_text(refused):
    """The reasons of a package's refusal as one line of text."""
    return '; '.join(str(defect.get('what')) for defect in refused.get('defects') or []) or str(refused)


def checked_copy(pkg, sha, expected_manifest_sha256, temporary):
    """Copy a package's files into `temporary`/package and check the copy, so that what runs afterwards is bytes that
    passed the check whatever happens to the source. Returns (copy, the copy's check, None) or (None, None, refusal)."""
    checked = verify_package(pkg, sha, expected_manifest_sha256)
    if not checked['ok']:
        return None, None, {'ok':False,'defects':checked['defects'],'_exit':1}
    # Copy only the files the manifest lists, without following a link put there since the check.
    copy = Path(temporary)/'package'; copy.mkdir()
    for name in sorted({*checked['manifest']['files'], 'manifest.json'}):
        target = copy/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(pkg/name, target, follow_symlinks=False)
    # The copy holds bytes that just passed the check at the source, so a copy the host cannot read back (a lock an
    # antivirus or indexer holds, an I/O error) is the host's failure around the check, never the package's defect.
    # The runtime's check reports any read error as a defect, so the copy is read here first, and again after a
    # failed check, letting the host's OSError propagate.
    _read_back(copy)
    verified = verify_package(copy, sha, checked['manifest_sha256'])
    if not verified['ok']:
        _read_back(copy)
        return None, None, {'ok':False,'defects':verified['defects'],'_exit':1}
    return copy, verified, None


def emit_check(package_dir, model_dir=None, expected_manifest_sha256=None):
    """Check a package and replay its probe record in this process, once, on the installed runtime. With `model_dir`
    the package's driver must be that model's bytes; with `expected_manifest_sha256` its manifest must have that hash.
    `integrity_ok` says that the package passed the check, `replay_status` whether the replay passed, failed or was
    not run."""
    pkg = path(package_dir)
    sha = None
    if model_dir is not None:
        # os.path.isfile is false on any OSError, such as a folder the worker left unsearchable, where Path.is_file
        # raises. A model directory without a model, or with one that cannot be read, is refused; it is no host fault.
        refused = None
        if not os.path.isfile(path(model_dir)/'model.json'):
            refused = 'model.json is missing or its folder cannot be searched'
        else:
            try:
                sha = load(model_dir)[1]
            except (OSError, ValueError, RecursionError) as exc:
                refused = f'model.json is unreadable: {type(exc).__name__}: {exc}'
        if refused is not None:
            return {'ok':False,'integrity_ok':False,'replay_status':'not_run','_exit':1,
                    'defects':[{'where':'model_dir','what':refused}]}
    # The check runs on a copy that passed the check, so what it replays and compares is what it hashed. A folder the
    # host cannot remove at once (a file an indexer or antivirus holds open on Windows) is left behind; it never
    # discards a check that ran.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        copy, checked, refused = checked_copy(pkg, sha, expected_manifest_sha256, temporary)
        if refused is not None:
            return {**refused,'integrity_ok':False,'replay_status':'not_run'}
        return _check_copy(copy, checked, expected_manifest_sha256)


def _check_copy(pkg, checked, expected_manifest_sha256):
    """The result of `emit_check` for `pkg`, a copy of a package that passed the check with the result `checked`."""
    entry, model = the_driver(checked)
    try:
        evidence = read_json(pkg/entry['evidence'])
    except (ValueError, RecursionError) as exc:
        return {'ok':False,'defects':[{'where':'probe','what':f'{type(exc).__name__}: {exc}'}],'integrity_ok':True,
                'replay_status':'not_run','_exit':1}
    compared = check_probe(model, evidence, checked['manifest']['files'][entry['model']])
    if compared['ok'] and entry['evidence_kind'] != evidence['evidence_kind']:
        compared = {**compared,'ok':False,'defects':[{'where':'evidence_kind','what':'manifest and probe disagree'}]}
    if compared['ok']:
        # A package says what the tool would say: a worker cannot edit the two files the tool renders, even with the
        # manifest made again to match.
        edited = [{'where':name,'what':name + ' differs from what the tool renders from the package\'s driver and '
                                      'evidence'}
                  for name, rendered in rendered_files(checked['drivers'], {entry['name']:evidence}).items()
                  if (pkg/name).read_bytes() != rendered]
        if edited:
            return {'ok':False,'defects':edited,'integrity_ok':False,'replay_status':'passed','_exit':1}
    return {'ok':compared['ok'],'defects':compared['defects'],'integrity_ok':True,
            'replay_status':'passed' if compared['ok'] else 'failed','evidence_kind':'replay',
            'source_evidence_kind':compared['source_evidence_kind'],
            'manifest_pin_verified':expected_manifest_sha256 is not None,'_exit':0 if compared['ok'] else 1}


def run_child(package, operation, parameters, binding, grants):
    """One operation of the checked package in the folder `package`, run by the installed runtime in a child process
    (`interface_runtime.child`), which is given this process's import path. Every use of a package starts it here. The
    call counts as passed with exit 0, ok true and a verified identity. A host failure around the call (its working
    folder, starting the process, a timeout) is this call's host fault."""
    from interface_runtime import child
    command = child.command()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:
            done = subprocess.run(command, cwd=cwd, env={**os.environ,'PYTHONPATH':''},
                                  input=child.request(package, operation, parameters, binding, grants),
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
    """Run each named operation once on the live device, on the installed runtime over the package's data.

    Each call runs in a child process over a freshly checked copy of the package, so a pass is evidence about these
    package data and this binding; the result's manifest_sha256 names the package whose copy was checked. The binding
    is the operator's and is passed through unchanged; it is checked against the model's channel, and every named
    operation's effect against the grants, before any call. Every refusal before the first call (arguments, binding,
    grants, a package the check refuses) is returned with transport_open_attempted false and exit 1, with no live
    record, instead of being recorded as a package failure. A host failure before the first call (its temporary
    folder, a copy it cannot read back) is no refusal of the package: it is returned as fault host with exit 4, also
    with transport_open_attempted false, since nothing reached the device. The plan stops at the first operator or
    host fault (exit 4, inconclusive).
    Every call is recorded append-only in emit/live-N.json; when the host cannot write that record, the results are
    still returned, as a host fault with exit 4, since the device was contacted."""
    try:
        if type(binding) is not dict:
            raise ValueError('an interface package live test requires a binding object')
        grants = [] if allow_effects is None else allow_effects
        if type(grants) is not list or any(g not in ('read','write','actuate','reset') for g in grants):
            raise ValueError('allow_effects must be a list of read, write, actuate or reset')
        arguments = {} if parameters is None else parameters
        if type(arguments) is not dict or any(type(v) is not dict for v in arguments.values()):
            raise ValueError('parameters must map operation names to argument objects')
        json.dumps([binding, arguments], allow_nan=False)
        if model_dir is not None and not (path(model_dir)/'model.json').is_file():
            raise ValueError('model_dir holds no model.json')
        sha = load(model_dir)[1] if model_dir is not None else None
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return before_contact(f'{type(exc).__name__}: {exc}')
    pkg = path(package_dir)
    started = core.now()
    try:
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except OSError as exc:
        return host_before_contact(exc)
    with folder as temporary:
        try:
            copy, checked, refusal = checked_copy(pkg, sha, expected_manifest_sha256, temporary)
            if refusal is not None:
                return {**refusal, 'transport_open_attempted':False}
            manifest_sha = checked['manifest_sha256']
            entry, model = the_driver(checked)
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
        except OSError as exc:
            # The copy holds bytes that passed the check; a host that cannot copy or read them back failed before
            # any call.
            return host_before_contact(exc)
        except (ValueError, TypeError, LookupError, RecursionError) as exc:
            return before_contact(f'{type(exc).__name__}: {exc}')
        results = []
        for name in names:
            try:
                results.append(run_child(copy, name, arguments.get(name,{}), binding,
                                         [g for g in grants if g != 'read']))
            except Exception as exc:
                # The host failed around the call, which may already have reached the device.
                results.append({'operation':name,'command':None,'returncode':None,'stdout_json':None,'stderr':'',
                                'ok':False,'error_code':'host','error_message':f'{type(exc).__name__}: {exc}',
                                'fault':'host'})
            if results[-1]['fault'] in ('operator','host'):
                break
    passed = sum(1 for r in results if r['ok'])
    not_run = names[len(results):]
    ok = passed == len(names)
    transport = any(r['fault'] in ('operator','host') for r in results)
    verdict = 'pass' if ok else 'inconclusive' if transport else 'fail'
    # The runtime says whether it tried to open the transport, so a refusal of the caller's parameters before any
    # device contact stays distinguishable from a call that reached the device.
    summary = [{'operation':r['operation'],'ok':r['ok'],'returncode':r['returncode'],'error_code':r['error_code'],
                'error_message':r['error_message'],'fault':r['fault'],
                **{k:r['stdout_json'].get(k) for k in ('identity_verified','outputs','units')
                   if type(r['stdout_json']) is dict},
                **{k:r['stdout_json'][k] for k in ('transport_open_attempted',)
                   if type(r['stdout_json']) is dict and type(r['stdout_json'].get(k)) is bool}} for r in results]
    out = {'ok':ok,'passed':passed,'failed':len(results)-passed,'not_run':not_run,'verdict':verdict,
           'results':summary,'manifest_sha256':manifest_sha}
    try:
        record = json.dumps({'schema':'interface-live-test/1','package_dir':str(pkg),'manifest_sha256':manifest_sha,
                             'model_sha256':checked['manifest']['files'][entry['model']],
                             'interpreter':sys.executable,'binding':binding,
                             'operations':names,'parameters':arguments,'allow_effects':grants,'started':started,
                             'finished':core.now(),'results':results,'passed':passed,'failed':len(results)-passed,
                             'not_run':not_run,'ok':ok,'verdict':verdict}, indent=2, allow_nan=False)
        folder = Path(core.resolve_run(run_dir or 'scratch'))/'emit'; folder.mkdir(parents=True, exist_ok=True)
        index = 1
        while (folder/f'live-{index}.json').exists():
            index += 1
        target = folder/f'live-{index}.json'
        with target.open('x', encoding="utf-8") as stream:
            stream.write(record)
        files = {str(target):digest(target)}
    except Exception as exc:
        # The device was contacted: keep what it did. Without its record the test is inconclusive for the host,
        # unless the package already failed on the device.
        return {**out, 'ok':False, 'verdict':'fail' if verdict == 'fail' else 'inconclusive', 'fault':'host',
                'error':f'live record not written: {type(exc).__name__}: {exc}', 'live_record':None, '_exit':4}
    return {**out, 'live_record':str(target), '_exit':0 if ok else 4 if transport else 1, '_files':files}


def model_qualify(run_dir, model_dir, replies, **_):
    """Offline qualification of a model against supplied replies; never opens hardware. A model of a format the
    runtime does not know is refused before the replies are read."""
    from interface_runtime.qualify import QualifyError, qualify, summary
    model, sha = load(model_dir)
    if unknown_format(model):
        return {'ok':False,'defects':[{'where':'schema','what':unknown_format(model)}],'model_sha256':sha,'_exit':1}
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

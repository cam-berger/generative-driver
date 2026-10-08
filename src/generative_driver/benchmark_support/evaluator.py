"""The evaluator: the only code that grades a frozen driver against the case truth. The bench harness runs it once a
configurator run is terminal and the run's device stop is confirmed: it grades the driver the run accepted on a fresh
emulator of its own, keeps its evaluations in an encrypted draft and seals the run evidence in the trial directory.
Neither the configurator nor its device modules import it, and nothing in a stage calls it."""
import hashlib
import json
import shutil
from pathlib import Path

from .cases import _write
from .instruments import family_for

DRAFT, EVIDENCE = 'evaluator-draft.enc', 'run-evidence.enc'
SCHEMA = 'equivalence/1'
TEXT = 300  # The configurator's limit on an equivalence/1 text field.


def case_truth(case_id, run_dir, options):
    """The case truth, unlocked with the evaluator password file in `options`: the bench harness takes the device recipe
    from it, and the evaluator grades with it. The manifest is the run's pinned snapshot when `run_dir` holds one."""
    from ..benchmark import case_root
    from .emulated import _inputs
    from .emulator import truth_for_case
    _, manifest = _inputs(case_id, run_dir)
    truth = truth_for_case(case_root(), manifest, options)
    if case_id in ('sampled-sensor-v1', 'parameter-store-v1'):
        from .calibration import hydrate_truth
        return hydrate_truth(truth)
    return truth


def reference_execute(pin, truth, model, capabilities, *, renode, image, output_dir,
                      phase='final', package=None, expected_package_sha256=None, package_final=True, private_dir=None):
    """Measured evaluator-only seam using normal models and frozen packages, on a fresh emulator it starts and stops.
    The emulator session keeps its files (its log, the monitor commands it rendered) in `private_dir` when given."""
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    if phase not in ('diagnostic', 'final'):
        raise ValueError('Unknown behavior phase')
    from .native import NativeSession
    from .emulated_actions import execute_plan, model_invoker, canonical_package_invoker, run_call
    from .behavior import validate_records
    from .snapshots import canonical_digest
    from ..configurator import digest
    from ..toolkit import call_tool
    from interface_runtime.engine import validate_model
    candidate = {**model, 'channel': {'type': 'tcp'}}
    if not validate_model(candidate)['ok']:
        raise ValueError('Reference candidates must be structurally valid before native calibration')
    if package is not None and (not expected_package_sha256 or digest(package) != expected_package_sha256):
        raise ValueError('Frozen package changed before reference execution')
    family = family_for(pin.get('case_id', 'tq9-v2'))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    model_dir = output/'model'
    from .calibration import reference_model_bytes
    model_dir.mkdir(parents=True, exist_ok=True)
    # A frozen package is checked against its own model bytes, whatever newline convention the host that wrote them
    # used; emit_check holds them to the package's manifest. A model with no package is written in the portable form.
    (model_dir/'model.json').write_bytes((Path(package)/'driver/model.json').read_bytes() if package is not None
                                         else reference_model_bytes(candidate))
    model_hash = hashlib.sha256((model_dir/'model.json').read_bytes()).hexdigest()
    probes = []
    session = NativeSession.start(renode=renode, image=image, recipe=truth['recipe'], private_dir=private_dir)
    try:
        frozen = None
        if phase == 'final' and package_final:
            if package is None:
                probed = run_call(session, lambda operation, parameters: call_tool('probe_run', {
                    'run_dir': str(output/'tools'), 'model_dir': str(model_dir), 'operation': operation,
                    'parameters': {}, 'binding': session.binding, 'n': 1}),
                    {'operation': candidate['identity']['operation'], 'parameters': {}})
                if not probed.get('ok') or not probed.get('probe'):
                    raise ValueError('Reference identity probe could not support package emission')
                probes.append(probed['probe'])
                emitted = call_tool('emit_package', {'run_dir': str(output/'tools'),
                    'model_dir': str(model_dir), 'probe': probed['probe']})
                if not emitted.get('ok'):raise ValueError('Reference package emission failed')
                package = Path(emitted['package_dir'])
                expected_package_sha256 = digest(package)
            checked = call_tool('emit_check', {'run_dir': str(output/'tools'),
                'model_dir': str(model_dir), 'package_dir': str(package)})
            if not all(checked.get(k) for k in ('ok', 'integrity_ok', 'runtime_current')):
                raise ValueError('Reference package integrity failed')
            frozen = digest(package)
            if frozen != expected_package_sha256:
                raise ValueError('Frozen package changed before final episode')
            invoke = canonical_package_invoker(package, capabilities, session.binding)
            runtime_root = Path(package)/'interface_runtime'
        else:
            invoke = model_invoker(model_dir, capabilities, session.binding, output/'tools', probes)
            import interface_runtime
            runtime_root = Path(interface_runtime.__file__).parent
        runtime_hash = canonical_digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in sorted(runtime_root.glob('*.py'))})
        local = {**truth, 'artifact_sha256': frozen or model_hash}
        contract = family.contract(pin, local, phase)
        records = execute_plan(session, invoke, family.build_plan(pin, local, phase), contract,
            output/'raw.json', family.observations, truth['contracts']['time_policy']['sample_settle_seconds'])
        if frozen and digest(package) != frozen:
            raise ValueError('Frozen package changed during final episode')
        result = {'execution': 'reference-calibration', 'backend': 'native-renode',
            'native_process_observed': session.process is not None and session.process.poll() is None,
            'process': session.info, 'contract': contract, 'records': records,
            'grade': validate_records(contract, records), 'probes': probes,
            'model_sha256': model_hash, 'capabilities_sha256': canonical_digest(capabilities), 'package_sha256': frozen,
            'artifact_execution': 'emitted-package' if frozen else 'model-runtime',
            'package_dir': str(package) if package is not None else None, 'runtime_sha256': runtime_hash}
        _write(output/'evaluation.json', result)
        return result
    finally:
        session.stop()


def _draft(trial_dir, password_file, value=None):
    """The evaluator's encrypted draft in the trial directory: every final it graded, with its records and raw evidence."""
    from .truth import seal, unlock
    path = Path(trial_dir)/DRAFT
    password = Path(password_file).read_text(encoding='utf-8').strip()
    if value is not None:
        seal(value, path, password)
        return value
    if not path.exists():
        return {'evaluations': []}
    return unlock(path, password, hashlib.sha256(path.read_bytes()).hexdigest())


def emitted(accepted):
    """The frozen driver the run accepted: (path, recorded sha256, revision, index) of the latest emit handoff that handed
    on a package, or None when no emit did."""
    for index in range(len(accepted) - 1, -1, -1):
        handoff = accepted[index]
        if handoff.get('stage') == 'emit' and handoff.get('artifacts'):
            artifact = handoff['artifacts'][0]
            return Path(artifact['path']), artifact['sha256'], handoff.get('revision', 0), index
    return None


def mapping(accepted, index):
    """(path, recorded sha256) of the capability mapping of the probe handoff whose model emit packaged: the latest probe
    handoff with a model.json before that emit handoff. Never another model's mapping, so None when that one has none."""
    from .emulated import _artifact
    probe = next((h for h in reversed(accepted[:index]) if h.get('stage') == 'probe' and _artifact(h, 'model.json')), None)
    return _artifact(probe, 'capabilities.json')


def _copy(found, target, what):
    """A private copy of an accepted artifact, re-hashed against the hash recorded when it was accepted."""
    from ..configurator import IntegrityError, digest
    path, expected = found
    try:
        (shutil.copytree if Path(path).is_dir() else shutil.copyfile)(path, target)
        observed = digest(target)
    except (OSError, ValueError):
        observed = None
    if observed != expected:
        raise IntegrityError('Accepted ' + what + ' bytes changed')
    return Path(target)


def _text(value, empty='unnamed'):
    """A public text field within the configurator's equivalence/1 limit of 300 characters."""
    return str(value)[:TEXT] or empty


def _mission_fields(found):
    """The public mission fields, their text within the record's limit."""
    if not isinstance(found, dict):
        return None
    return {key: found[key] if key == 'passed' else _text(found[key], '')
            for key in ('name', 'passed', 'reason', 'fault') if key in found}


def record(status, equivalent, checks, diff, frozen, capabilities_sha256=None, mission=None):
    """A public equivalence/1 record: check ids with passed flags and the failing ids with their reasons. Expected values,
    tolerances, witnesses and raw records stay sealed. Text is held to the configurator's length limit."""
    from ..configurator import PROTOCOL
    checks = [{'id': _text(c['id']), 'passed': c['passed']} for c in checks]
    diff = [{'id': _text(d['id']), 'reason': _text(d['reason'], 'failed')} for d in diff]
    return {'schema': SCHEMA, 'status': status, 'equivalent': equivalent, 'basis': 'evaluator', 'method': 'fresh-final',
            'checks': checks, 'diff': diff, 'passed': sum(c['passed'] is True for c in checks), 'total': len(checks),
            'mission': _mission_fields(mission), 'frozen_artifact_sha256': frozen,
            'capabilities_sha256': capabilities_sha256, 'protocol': PROTOCOL}


def unscored(reason, frozen):
    return record('unscored', None, [], [{'id': 'evaluation', 'reason': reason}], frozen)


def reuse_mission(case_id, run_dir, accepted, frozen, capabilities):
    """Whether fresh reuse did the public mission with the frozen package, from the package events the run recorded for
    its accepted reuse assignment: a secondary check beside the grade, never the verdict."""
    from .emulated import _mission
    reuse = next((h for h in reversed(accepted) if h.get('stage') == 'reuse' and h.get('artifacts')), None)
    if reuse is None or frozen is None:
        return {'name': 'fresh_worker_mission', 'passed': None, 'reason': 'Fresh reuse did not run the frozen package'}
    if not isinstance(capabilities, dict):
        return {'name': 'fresh_worker_mission', 'passed': None, 'reason': 'No readable capability mapping was handed on'}
    path = Path(run_dir)/'benchmark/package-events.jsonl'
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []
    events = [row for row in rows if row.get('actor') == 'worker' and row.get('stage') == 'reuse'
              and row.get('assignment_id') == reuse.get('assignment_id') and row.get('package_sha256') == frozen]
    return _mission_fields(_mission(case_id, events, capabilities))


def _held(draft, run_id, sha):
    """The final the draft holds for this run's frozen driver, or None."""
    return next((e for e in draft['evaluations'] if (e.get('run_id'), e.get('phase'), e.get('frozen_artifact_sha256'))
                 == (run_id, 'final', sha)), None)


def needs_grading(trial_dir, result, *, password_file):
    """Whether concluding this run would grade its frozen driver on a fresh emulator: a completed run whose verdict is
    pending, which emitted a package and handed on the mapping of its model, and whose final the draft does not hold."""
    if (result.get('equivalence') is not None or result.get('status') != 'completed'
            or (result.get('progress') or {}).get('equivalence') != 'pending'):
        return False
    accepted = result.get('accepted_handoffs') or []
    frozen = emitted(accepted)
    if frozen is None or mapping(accepted, frozen[3]) is None:
        return False
    return _held(_draft(trial_dir, password_file), result['run_id'], frozen[1]) is None


def grade(case_id, trial_dir, run_dir, result, *, password_file, renode=None):
    """Grade a completed run's frozen driver once: (public equivalence/1 without its evidence hash, the final evaluation
    for the seal or None). It checks the run's snapshot and pin, copies the accepted package and the mapping of the model
    it packages and re-hashes both, unlocks the truth and runs the final phase on a fresh emulator. A final the draft
    already holds for this run and package is rebuilt and never graded again. A changed record raises IntegrityError."""
    from ..configurator import IntegrityError
    from .behavior import validate_records
    from .emulated import _inputs
    from .registry import pin_case
    from .snapshots import require_snapshot
    trial_dir, run_dir = Path(trial_dir), Path(run_dir)
    pin = result['case_pin']
    try:
        snapshot = require_snapshot(run_dir, pin)
    except (OSError, ValueError) as error:
        raise IntegrityError('Execution snapshot unverifiable: ' + str(error)) from None
    if snapshot['snapshot_sha256'] != result.get('snapshot_sha256'):
        raise IntegrityError('Execution snapshot differs from the run record')
    if pin_case(case_id, pin.get('scenario_id'), pin.get('case_seed', 0)) != pin:
        raise IntegrityError('The installed case differs from the run\'s pinned case')
    accepted = result.get('accepted_handoffs') or []
    frozen = emitted(accepted)
    if frozen is None:
        return record('no-driver', False, [], [{'id': 'frozen_package', 'reason': 'no driver emitted'}], None,
                      mission=reuse_mission(case_id, run_dir, accepted, None, None)), None
    source, sha, revision, index = frozen
    private = trial_dir/'private'
    shutil.rmtree(private, ignore_errors=True)
    private.mkdir(parents=True, mode=0o700)
    package = _copy((source, sha), private/'package', 'package')
    found, capabilities, capabilities_sha = mapping(accepted, index), None, None
    if found is not None:
        copy, capabilities_sha = _copy(found, private/'capabilities.json', 'capability mapping'), found[1]
        try:
            capabilities = json.loads(copy.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            capabilities = None
    checked = reuse_mission(case_id, run_dir, accepted, sha, capabilities)
    if not isinstance(capabilities, dict):
        return record('graded', False, [], [{'id': 'capability_mapping', 'reason': 'no capability mapping'}], sha,
                      capabilities_sha, checked), None
    draft = _draft(trial_dir, password_file)
    final = _held(draft, result['run_id'], sha)
    if final is None:
        from interface_runtime.engine import validate_model
        try:
            model = json.loads((package/'driver/model.json').read_text(encoding='utf-8'))
            valid = isinstance(model, dict) and validate_model({**model, 'channel': {'type': 'tcp'}})['ok']
        except (OSError, ValueError, UnicodeError, TypeError, AttributeError, KeyError):
            valid = False
        if not valid:
            return record('graded', False, [], [{'id': 'frozen_package', 'reason': 'the frozen driver\'s model is not '
                          'structurally valid'}], sha, capabilities_sha, checked), None
        truth = case_truth(case_id, run_dir, {'evaluator_password_file': str(password_file)})
        inputs, _ = _inputs(case_id, run_dir)
        output = private/'final'
        # The session's own files, the monitor commands that carry hidden stimuli among them, stay under `private`,
        # which the harness removes however grading ends.
        evaluation = reference_execute({**pin, 'revision': revision}, truth, model, capabilities, renode=renode,
                                       image=inputs/'firmware.bin', output_dir=output, phase='final', package=package,
                                       expected_package_sha256=sha, private_dir=private/'native')
        raw = output/'raw.json'
        final = {'run_id': result['run_id'], 'phase': 'final', 'revision': revision, 'frozen_artifact_sha256': sha,
                 'capabilities_sha256': capabilities_sha, 'contract': evaluation['contract'], 'records': evaluation['records'],
                 'raw': json.loads(raw.read_text(encoding='utf-8')) if raw.is_file() else None}
        draft['evaluations'].append(final)
        _draft(trial_dir, password_file, draft)
    graded = validate_records(final['contract'], final['records'])
    checks = [{'id': str(row['id']), 'passed': row['passed'] is True} for row in graded['checks']]
    diff = [{'id': str(row['id']), 'reason': str(row.get('reason') or 'failed')} for row in graded['checks'] if row['passed'] is not True]
    return record('graded', graded['verdict'] == 'passed', checks, diff, sha, capabilities_sha, checked), final


def evidence(trial_dir, run_dir, result, *, final=None, equivalence=None, mission=None, error=None):
    """The benchmark-run-evidence/2 payload of a terminal run: its pinned case and snapshot, every accepted artifact and
    notes.json hash, the single final evaluation (None for a stopped or ungraded run), the public verdict, the mission
    check, the provider's recovery receipts and the hash of its device journal. A snapshot that no longer verifies raises
    IntegrityError."""
    from ..configurator import IntegrityError, PROTOCOL
    from .snapshots import read_snapshot
    try:
        snapshot = read_snapshot(run_dir)
    except (OSError, ValueError) as error:
        raise IntegrityError('Execution snapshot unverifiable: ' + str(error)) from None
    rows = []
    for handoff in result.get('accepted_handoffs') or []:
        identity = {key: handoff.get(key) for key in ('stage', 'revision', 'assignment_id')}
        rows += [{**identity, 'kind': 'artifact', 'path': a['path'], 'sha256': a['sha256']} for a in handoff.get('artifacts', [])]
        notes = handoff.get('notes') or {}
        if notes.get('path'):
            rows.append({**identity, 'kind': 'notes', 'path': notes['path'], 'sha256': notes['sha256']})
    journal = Path(trial_dir)/'device/journal.jsonl'
    return {'schema': 'benchmark-run-evidence/2', 'protocol': PROTOCOL, 'run_id': result['run_id'],
            'run': {key: result.get(key) for key in ('status', 'outcome_category')},
            'case_pin': snapshot['case_pin'], 'execution_snapshot': snapshot, 'accepted_artifacts': rows,
            'final': final, 'equivalence': equivalence, 'mission': mission, 'evaluation_error': error,
            'device': {'receipts': list(((result.get('progress') or {}).get('device') or {}).get('receipts', [])),
                       'journal_sha256': hashlib.sha256(journal.read_bytes()).hexdigest() if journal.is_file() else None}}


def seal(trial_dir, run_dir, result, *, password_file, **content):
    """Seal the run evidence in the trial directory: {path, sha256}. Nothing is sealed whose identity does not hold."""
    from .evidence import seal_run_evidence_v2
    return seal_run_evidence_v2(evidence(trial_dir, run_dir, result, **content), Path(trial_dir)/EVIDENCE, password_file)

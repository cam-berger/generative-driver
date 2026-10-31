"""The evaluator: the only code that grades a frozen driver against the case truth. The bench harness runs it once a
configurator run is terminal and the run's device stop is confirmed: it grades the driver the run accepted on a fresh
emulator of its own (`fresh-final`), running the frozen package's own bundled runtime; it keeps its evaluations in an
encrypted draft and seals the run evidence in the trial directory. Legacy tq9 and bme280 are graded from the fresh
reuse their run recorded (`recorded-reuse`) by `recorded_grading`, outside the grader. Neither the configurator nor its
device modules import it, and nothing in a stage calls it."""
import hashlib
import json
import shutil
from pathlib import Path

from .case_assets import write_json
from .instruments import family_for

DRAFT, EVIDENCE = 'evaluator-draft.enc', 'run-evidence.enc'
SCHEMA = 'equivalence/1'
TEXT = 300  # The configurator's limit on an equivalence/1 text field.
# Cases graded from the fresh reuse their run recorded; every other case is graded on a fresh emulator.
RECORDED = {'tq9': 'recorded-reuse', 'bme280': 'recorded-reuse'}


def method_for(case_id):
    """How the evaluator grades a case's frozen driver."""
    return RECORDED.get(case_id, 'fresh-final')


def case_truth(case_id, run_dir, options):
    """The case truth, unlocked with the evaluator password file in `options`: the bench harness takes the device recipe
    from it, and the evaluator grades with it. The manifest is the run's pinned snapshot when `run_dir` holds one."""
    from .case_assets import case_inputs, case_root
    from .emulator import truth_for_case
    _, manifest = case_inputs(case_id, run_dir)
    truth = truth_for_case(case_root(), manifest, options)
    if truth.get('schema') == 'benchmark-upstream-truth/1':
        from .upstream import hydrate_truth
        return hydrate_truth(truth)
    if case_id in ('sampled-sensor-v1', 'parameter-store-v1'):
        from .calibration import hydrate_truth
        return hydrate_truth(truth)
    return truth


def accept_frozen_package(package, expected_sha256):
    """The digest of a frozen package, which must be the one recorded when the run froze it, else ValueError: that
    digest is the package's integrity check. The package is graded by its own bytes, never by comparing its bundled
    runtime with this checkout's, so the package of a changed system is graded like any other."""
    from .integrity import digest
    frozen = digest(package)
    if frozen != expected_sha256:
        raise ValueError('Frozen package changed before final episode')
    return frozen


def reference_execute(pin, truth, capabilities, *, renode, image, output_dir, package, expected_package_sha256,
                      private_dir=None, healthy_device_failures=False):
    """Grade a frozen package's final phase on a fresh emulator it starts and stops, through the package's own bundled
    runtime. The emulator session keeps its files (its log, the monitor commands it rendered) in `private_dir` when given.
    Candidate models and reference emission run outside the grader (`reference_calibration.run_reference`)."""
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    from .execution import start_session
    from .emulated_actions import execute_plan, canonical_package_invoker
    from .behavior import validate_records
    from .snapshots import canonical_digest
    from .integrity import digest
    if not expected_package_sha256 or digest(package) != expected_package_sha256:
        raise ValueError('Frozen package changed before reference execution')
    family = family_for(pin.get('case_id', 'tq9-v2'))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    model_dir = output/'model'
    model_dir.mkdir(parents=True, exist_ok=True)
    # A frozen package's model is its own bytes, whatever newline convention the host that wrote them used.
    (model_dir/'model.json').write_bytes((Path(package)/'driver/model.json').read_bytes())
    model_hash = hashlib.sha256((model_dir/'model.json').read_bytes()).hexdigest()
    session = start_session(renode=renode, image=image, recipe=truth['recipe'], private_dir=private_dir)
    try:
        frozen = accept_frozen_package(package, expected_package_sha256)
        invoke = canonical_package_invoker(package, capabilities, session.binding)
        runtime_root = Path(package)/'interface_runtime'
        runtime_hash = canonical_digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in sorted(runtime_root.glob('*.py'))})
        local = {**truth, 'artifact_sha256': frozen}
        contract = family.contract(pin, local, 'final')
        records = execute_plan(session, invoke, family.build_plan(pin, local, 'final'), contract,
            output/'raw.json', family.observations, truth['contracts']['time_policy']['sample_settle_seconds'],
            healthy_device_failures=healthy_device_failures)
        if digest(package) != frozen:
            raise ValueError('Frozen package changed during final episode')
        result = {'execution': 'reference-calibration',
            'backend': 'native-sim' if truth['recipe'].get('backend') == 'native-sim' else 'native-renode',
            'native_process_observed': session.process is not None and session.process.poll() is None,
            'process': session.info, 'contract': contract, 'records': records,
            'grade': validate_records(contract, records), 'probes': [],
            'model_sha256': model_hash, 'capabilities_sha256': canonical_digest(capabilities), 'package_sha256': frozen,
            'artifact_execution': 'emitted-package', 'package_dir': str(package), 'runtime_sha256': runtime_hash}
        write_json(output/'evaluation.json', result)
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
    from .case_assets import accepted_artifact
    probe = next((h for h in reversed(accepted[:index]) if h.get('stage') == 'probe' and accepted_artifact(h, 'model.json')), None)
    return accepted_artifact(probe, 'capabilities.json')


def _copy(found, target, what):
    """A private copy of an accepted artifact, re-hashed against the hash recorded when it was accepted."""
    from .integrity import IntegrityError, digest
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


def _protocol(result):
    """The protocol a run was under, from its progress: the evaluator stamps its grade and its evidence with it. The
    evaluator holds no protocol of its own, so a result that names none raises ValueError and no default stands in."""
    protocol = (result.get('progress') or {}).get('protocol')
    if not isinstance(protocol, str) or not protocol:
        raise ValueError('The run result names no protocol: its grade and evidence cannot be stamped')
    return protocol


def record(status, equivalent, checks, diff, frozen, capabilities_sha256=None, mission=None, method='fresh-final', *,
           protocol: str):
    """A public equivalence/1 record: check ids with passed flags and the failing ids with their reasons. Expected values,
    tolerances, witnesses and raw records stay sealed. Text is held to the configurator's length limit. It names the
    `protocol` of the run it grades."""
    checks = [{'id': _text(c['id']), 'passed': c['passed']} for c in checks]
    diff = [{'id': _text(d['id']), 'reason': _text(d['reason'], 'failed')} for d in diff]
    return {'schema': SCHEMA, 'status': status, 'equivalent': equivalent, 'basis': 'evaluator', 'method': method,
            'checks': checks, 'diff': diff, 'passed': sum(c['passed'] is True for c in checks), 'total': len(checks),
            'mission': _mission_fields(mission), 'frozen_artifact_sha256': frozen,
            'capabilities_sha256': capabilities_sha256, 'protocol': protocol}


def unscored(reason, frozen, method='fresh-final', *, protocol: str):
    return record('unscored', None, [], [{'id': 'evaluation', 'reason': reason}], frozen, method=method, protocol=protocol)


def reuse_mission(case_id, run_dir, accepted, frozen, capabilities):
    """Whether fresh reuse did the public mission with the frozen package, from the package events the run recorded for
    its accepted reuse assignment. Required generated reuse must complete this mission; historical evidence retains
    this as a secondary check beside the grade."""
    from .case_assets import reuse_mission as _mission
    from .mcp_reuse import owner_evidence, required, verified
    from .snapshots import read_snapshot
    proof = owner_evidence(run_dir, accepted)
    snapshot = read_snapshot(run_dir) if (Path(run_dir)/'benchmark/execution.json').is_file() else {}
    if required(proof, snapshot) and not verified(proof, frozen):
        return {'name': 'fresh_worker_mission', 'passed': False, 'reason': 'Missing or inconsistent owner proof of generated MCP reuse'}
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
            or (result.get('progress') or {}).get('equivalence') != 'pending'
            or method_for((result.get('case_pin') or {}).get('case_id')) != 'fresh-final'):
        return False
    accepted = result.get('accepted_handoffs') or []
    frozen = emitted(accepted)
    if frozen is None or mapping(accepted, frozen[3]) is None:
        return False
    return _held(_draft(trial_dir, password_file), result['run_id'], frozen[1]) is None


def accepted_driver(case_id, trial_dir, run_dir, result):
    """The frozen driver a completed run accepted, checked and copied for grading: (snapshot, driver). It checks the
    run's snapshot and pin. Then, when an emit handoff handed a package on, it first deletes `trial_dir/private` and
    copies into a fresh one the package of the latest such handoff and the mapping of the model it packages, re-hashing
    both. `driver` is None when no emit handed a package on, else (package copy, recorded sha256, revision, capability
    mapping or None, mapping sha256 or None). A changed record raises IntegrityError."""
    from .integrity import IntegrityError
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
        return snapshot, None
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
    return snapshot, (package, sha, revision, capabilities, capabilities_sha)


def grade(case_id, trial_dir, run_dir, result, *, password_file, renode=None):
    """Grade a completed run's frozen driver once, on a fresh emulator: (public equivalence/1 without its evidence hash,
    the final evaluation for the seal or None). It takes the accepted driver (`accepted_driver`), unlocks the truth and
    runs the final phase on a fresh emulator; a driver without a capability mapping, or whose model is not a JSON object,
    fails before either. A final the draft already holds for this run and package is rebuilt and never graded again. A
    changed record raises IntegrityError. Every record names the protocol the run's progress names, and a result that
    names none raises ValueError. Legacy tq9 and bme280 are graded from their recorded reuse by `recorded_grading.grade`
    instead."""
    from .case_assets import case_inputs
    protocol = _protocol(result)
    trial_dir, run_dir = Path(trial_dir), Path(run_dir)
    pin = result['case_pin']
    snapshot, driver = accepted_driver(case_id, trial_dir, run_dir, result)
    accepted = result.get('accepted_handoffs') or []
    if driver is None:
        return record('no-driver', False, [], [{'id': 'frozen_package', 'reason': 'no driver emitted'}], None,
                      mission=reuse_mission(case_id, run_dir, accepted, None, None), method=method_for(case_id),
                      protocol=protocol), None
    package, sha, revision, capabilities, capabilities_sha = driver
    private = trial_dir/'private'
    checked = reuse_mission(case_id, run_dir, accepted, sha, capabilities)
    if not isinstance(capabilities, dict):
        return record('graded', False, [], [{'id': 'capability_mapping', 'reason': 'no capability mapping'}], sha,
                      capabilities_sha, checked, protocol=protocol), None
    try:
        model = json.loads((package/'driver/model.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, RecursionError):
        model = None
    if not isinstance(model, dict):
        # The model is the driver's own bytes: one that is not a JSON object fails the grade, before any truth is
        # unlocked or device touched.
        reason = "the frozen driver's model is missing or not JSON"
        return record('graded', False, [], [{'id': 'frozen_package', 'reason': reason}], sha, capabilities_sha, checked,
                      protocol=protocol), None
    draft = _draft(trial_dir, password_file)
    final = _held(draft, result['run_id'], sha)
    if final is None:
        truth = case_truth(case_id, run_dir, {'evaluator_password_file': str(password_file)})
        inputs, manifest = case_inputs(case_id, run_dir)
        from .execution import artifact_name
        output = private/'final'
        # The session's own files, the monitor commands that carry hidden stimuli among them, stay under `private`,
        # which the harness removes however grading ends.
        evaluation = reference_execute({**pin, 'revision': revision}, truth, capabilities, renode=renode,
                                       image=inputs/artifact_name(manifest), output_dir=output, package=package,
                                       expected_package_sha256=sha, private_dir=private/'native',
                                       healthy_device_failures=True)
        raw = output/'raw.json'
        final = {'run_id': result['run_id'], 'phase': 'final', 'revision': revision, 'frozen_artifact_sha256': sha,
                 'capabilities_sha256': capabilities_sha, 'contract': evaluation['contract'], 'records': evaluation['records'],
                 'raw': json.loads(raw.read_text(encoding='utf-8')) if raw.is_file() else None}
        draft['evaluations'].append(final)
        _draft(trial_dir, password_file, draft)
    from .mcp_reuse import owner_evidence, required, verified
    proof = owner_evidence(run_dir, accepted)
    if required(proof, snapshot):
        final = {**final, 'generated_reuse_required': True, 'generated_reuse_verified': verified(proof, sha)}
    return final_record(final, capabilities_sha, checked, protocol=protocol), final


def final_record(final, capabilities_sha256=None, mission=None, *, protocol: str):
    """The public equivalence/1 record of a final evaluation, graded from its contract and records: equivalent when every
    check passed. Grading after the run and an offline regrade of the sealed evidence both build it here, and it names
    the `protocol` of the run they grade."""
    from .behavior import validate_records
    graded = validate_records(final['contract'], final['records'])
    checks = [{'id': str(row['id']), 'passed': row['passed'] is True} for row in graded['checks']]
    diff = [{'id': str(row['id']), 'reason': str(row.get('reason') or 'failed')} for row in graded['checks'] if row['passed'] is not True]
    if final.get('generated_reuse_required'):
        passed = final.get('generated_reuse_verified') is True
        checks.append({'id': 'generated_mcp_reuse', 'passed': passed})
        if not passed:
            diff.append({'id': 'generated_mcp_reuse', 'reason': 'Missing or inconsistent owner proof of generated MCP reuse'})
        mission_passed = isinstance(mission, dict) and mission.get('passed') is True
        checks.append({'id': 'fresh_worker_mission', 'passed': mission_passed})
        if not mission_passed:
            diff.append({'id': 'fresh_worker_mission', 'reason': (mission or {}).get('reason')
                         or 'Fresh generated MCP worker did not complete the required mission'})
    return record('graded', all(c['passed'] for c in checks), checks, diff, final['frozen_artifact_sha256'], capabilities_sha256,
                  mission, final.get('method', 'fresh-final'), protocol=protocol)


def evidence(trial_dir, run_dir, result, *, final=None, equivalence=None, mission=None, error=None):
    """The benchmark-run-evidence/2 payload of a terminal run: the protocol its progress names, its pinned case and
    snapshot, every accepted artifact and notes.json hash, the single final evaluation (None for a stopped or ungraded
    run), the public verdict, the mission check, the provider's recovery receipts and the hash of its device journal. A
    snapshot that no longer verifies raises IntegrityError; a result that names no protocol raises ValueError."""
    from .integrity import IntegrityError
    from .snapshots import read_snapshot
    protocol = _protocol(result)
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
    from .mcp_reuse import owner_evidence
    proof = owner_evidence(run_dir, result.get('accepted_handoffs') or [])
    return {'schema': 'benchmark-run-evidence/2', 'mcp_reuse': proof, 'protocol': protocol, 'run_id': result['run_id'],
            'run': {key: result.get(key) for key in ('status', 'outcome_category')},
            'case_pin': snapshot['case_pin'], 'execution_snapshot': snapshot, 'accepted_artifacts': rows,
            'final': final, 'equivalence': equivalence, 'mission': mission, 'evaluation_error': error,
            'device': {'receipts': list(((result.get('progress') or {}).get('device') or {}).get('receipts', [])),
                       'journal_sha256': hashlib.sha256(journal.read_bytes()).hexdigest() if journal.is_file() else None}}


def seal(trial_dir, run_dir, result, *, password_file, **content):
    """Seal the run evidence in the trial directory: {path, sha256}. Nothing is sealed whose identity does not hold."""
    from .evidence import seal_run_evidence_v2
    return seal_run_evidence_v2(evidence(trial_dir, run_dir, result, **content), Path(trial_dir)/EVIDENCE, password_file)

"""Recorded-reuse grading of legacy tq9 and bme280: the bench harness grades such a run's frozen driver once, after the
run, from the fresh reuse of it the run recorded, never touching a device. It is system code, outside the grader
identity: legacy cases are not in the baseline, and bme280's measurement rules live in the physical profile
(`physical.valid_rows`). It shares the evaluator's checks of the run record, its draft and its equivalence/1 records."""
import hashlib
import json
import math
import time
from pathlib import Path

UNITS = {'temperature': 'degC', 'humidity': '%RH', 'pressure': 'Pa'}


class Unscorable(ValueError):
    """The recorded evidence cannot grade the driver for a reason outside it, such as an operator reference outside the
    evaluator's limits. The message is public: it names no expected value or limit."""


def grade(case_id, trial_dir, run_dir, result, *, password_file, protocol):
    """Grade a completed legacy run's frozen driver once from the fresh reuse of it the run recorded (`recorded-reuse`):
    (public equivalence/1 without its evidence hash, the final evaluation for the seal or None). It takes the accepted
    driver as the evaluator does (`evaluator.accepted_driver`). A final the draft already holds for this run and package
    is rebuilt and never graded again. A changed record raises IntegrityError; recorded evidence that cannot grade the
    driver for a reason outside it raises Unscorable. Every record names `protocol`, the run's; none raises ValueError,
    as the evaluator's grade does."""
    from .evaluator import accepted_driver, final_record, method_for, record, reuse_mission
    from .mcp_reuse import owner_evidence, required, verified
    if not isinstance(protocol, str) or not protocol:
        raise ValueError('The run result names no protocol: its grade and evidence cannot be stamped')
    trial_dir, run_dir = Path(trial_dir), Path(run_dir)
    snapshot, driver = accepted_driver(case_id, trial_dir, run_dir, result)
    accepted = result.get('accepted_handoffs') or []
    if driver is None:
        return record('no-driver', False, [], [{'id': 'frozen_package', 'reason': 'no driver emitted'}], None,
                      mission=reuse_mission(case_id, run_dir, accepted, None, None), method=method_for(case_id),
                      protocol=protocol), None
    _, sha, revision, capabilities, capabilities_sha = driver
    verdict, final = _recorded(case_id, trial_dir, run_dir, result, sha, revision, capabilities, capabilities_sha, password_file,
                               protocol=protocol)
    proof = owner_evidence(run_dir, accepted)
    if final is not None and required(proof, snapshot):
        final = {**final, 'generated_reuse_required': True, 'generated_reuse_verified': verified(proof, sha)}
        verdict = final_record(final, capabilities_sha, verdict.get('mission'), protocol=protocol)
    return verdict, final


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _check(identifier, revision, expected, tolerance, channel, unit):
    return {'id': identifier, 'revision': revision, 'kind': 'number', 'expected': expected,
            'absolute_tolerance': tolerance, 'channel': channel, 'unit': unit}


def _observation(check, sha, value):
    """The record of one observed value for a check, or None when nothing was observed."""
    if value is None:
        return None
    return {'id': check['id'], 'task_id': check['id'], 'revision': check['revision'], 'artifact_sha256': sha,
            'channel': check['channel'], 'unit': check['unit'], 'value': value}


def _legacy_tq9(truth, events, capabilities, sha, revision):
    """Legacy tq9's reuse goal over the frozen package's recorded calls: a temperature read within the truth's
    tolerance, the requested output reached and the output disarmed, read from the instrument's duty after each call.
    The reading nearest each goal stands for that goal: one within tolerance means the goal was met. A missed goal is
    the driver's when the recording contradicts the goal, observed every channel or shows no failure of the host or the
    operator; otherwise the recording cannot grade the driver (Unscorable). A failure elsewhere never erases an
    observed contradiction."""
    goal, reference = truth['reuse_goal'], next(c for c in truth['checks'] if c['id'] == 'temperature')
    spec = capabilities['temperature']
    mine = [e for e in events if e.get('package_sha256') == sha and isinstance(e.get('result'), dict)]
    done = [e for e in mine if e['result'].get('ok') is True]
    duties = [e['observation'].get('duty') for e in done if isinstance(e.get('observation'), dict) and 'duty' in e['observation']]
    readings = [(e['result'].get('outputs') or {}).get(spec['output']) for e in done if e.get('operation') == spec['operation']]
    nearest = lambda values, target: min((v for v in values if _finite(v)), key=lambda v: abs(v - target), default=None)
    within = lambda value, target: _finite(value) and abs(value - target) <= goal['tolerance']
    read_ok = any(_finite(v) and abs(v - reference['expected']) <= reference['absolute_tolerance'] for v in readings)
    reached = any(within(d, goal['fraction']) for d in duties)
    disarmed = bool(duties) and within(duties[-1], goal['final_fraction'])
    contradiction = bool(readings) and not read_ok or bool(duties) and _finite(duties[-1]) and not disarmed
    observed = contradiction or bool(readings) and bool(duties) and all(_finite(d) for d in duties)
    faults = {e['result']['error'].get('fault') if isinstance(e['result'].get('error'), dict) else e['result'].get('fault')
              for e in mine if e['result'].get('ok') is not True}
    # Whose a missed goal is: an observed contradiction is the driver's whatever else failed; otherwise calls that failed
    # for the operator or the host leave the recording unable to grade it.
    fault = (None if read_ok and reached and disarmed else 'model' if contradiction else
             'operator' if 'operator' in faults else 'host' if 'host' in faults else 'model' if observed else None)
    if fault in ('operator', 'host'):
        raise Unscorable('Fresh reuse calls of the frozen driver failed for the ' + fault + ': the recorded reuse '
                         'cannot grade it')
    checks = [_check('fresh_package_temperature', revision, reference['expected'], reference['absolute_tolerance'],
                     'runtime-transcript', 'decoded'),
              _check('fresh_package_requested_output', revision, goal['fraction'], goal['tolerance'], 'independent-monitor', 'fraction'),
              _check('fresh_package_disarm', revision, goal['final_fraction'], goal['tolerance'], 'independent-monitor', 'fraction')]
    values = (nearest(readings, reference['expected']), nearest(duties, goal['fraction']), duties[-1] if duties else None)
    records = [row for row in (_observation(c, sha, v) for c, v in zip(checks, values)) if row is not None]
    return checks, records, {'events': events}


def grade_physical_reference(observed, truth, measurement_time=None):
    """Whether an operator reference may grade a physical measurement: a channel, a finite timestamp within the truth's
    age limit of the measurement, its evidence file unchanged, and every value with a tolerance within the truth's."""
    measured=time.time() if measurement_time is None else measurement_time
    if (not observed.get('channel') or type(observed.get('observed_at')) not in (int,float)
            or not math.isfinite(observed['observed_at']) or abs(measured-observed['observed_at'])>truth['max_reference_age_seconds']):
        return {'ok':False,'reason':'Reference channel/timestamp missing or stale'}
    path=Path(observed.get('evidence_path',''))
    if not path.is_file():return {'ok':False,'reason':'Independent photo/log evidence file missing'}
    refs=observed.get('reference',{})
    if set(refs)!=set(truth['max_tolerances']):return {'ok':False,'reason':'All three independent reference values are required'}
    for key,maximum in truth['max_tolerances'].items():
        row=refs[key]
        if not all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) for k in ('value','absolute_tolerance')) or not 0<row['absolute_tolerance']<=maximum:
            return {'ok':False,'reason':'Reference values/tolerances are invalid or too broad'}
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if observed.get('evidence_sha256',actual)!=actual:
        return {'ok':False,'reason':'Independent reference evidence changed'}
    return {'ok':True,'evidence_sha256':actual}


def _physical(truth, events, capabilities, sha, revision, reuse, private):
    """bme280's latest finite fresh measurement by the frozen package, against the operator reference recorded with
    fresh reuse, which must hold within the truth's age and tolerance limits of that measurement. None when fresh reuse
    measured nothing finite."""
    from .case_assets import accepted_artifact
    from .evaluator import _copy
    from .physical import REFERENCE, valid_rows
    valid = valid_rows(events, capabilities)
    if not valid:
        return None
    measurement = valid[-1]
    found = accepted_artifact(reuse, REFERENCE)
    if found is None:
        raise Unscorable('No operator reference was recorded with fresh reuse')
    saved = json.loads(_copy(found, private/REFERENCE, 'operator reference').read_text(encoding='utf-8'))
    evidence = next(((Path(a['path']), a['sha256']) for a in reuse['artifacts'] if a['sha256'] == saved.get('evidence_sha256')), None)
    if evidence is None:
        raise Unscorable('The operator reference evidence was not recorded with fresh reuse')
    copy = _copy(evidence, private/('reference-evidence' + evidence[0].suffix), 'operator reference evidence')
    checked = grade_physical_reference({**saved, 'evidence_path': str(copy)}, truth, measurement['time'])
    if not checked['ok']:
        raise Unscorable('The operator reference cannot grade the fresh measurement: ' + checked['reason'])
    checks = [_check(key, revision, saved['reference'][key]['value'], saved['reference'][key]['absolute_tolerance'],
                     'runtime-transcript', UNITS[key]) for key in ('temperature', 'humidity', 'pressure')]
    records = [_observation(c, sha, measurement['values'][capabilities[c['id']]]) for c in checks]
    return checks, records, {'measurement': measurement, 'reference': saved}


def _recorded(case_id, trial_dir, run_dir, result, sha, revision, capabilities, capabilities_sha, password_file, *, protocol):
    """Grade the frozen driver from the fresh reuse of it the run recorded (`recorded-reuse`): the accepted reuse
    handoff's package calls, re-hashed, against the truth's reuse goal (legacy tq9), or against the operator reference
    within the truth's limits (bme280). No device is touched. A final the draft holds for this run and package is
    rebuilt, never graded again. Its records name the run's `protocol`."""
    from .case_assets import REUSE_EVENTS, accepted_artifact, mapping_errors as tq9_errors, reuse_mission as _mission
    from .evaluator import _copy, _draft, _held, _mission_fields, case_truth, final_record, method_for, record
    from .physical import mapping_errors as physical_errors, valid_rows
    accepted, private = result.get('accepted_handoffs') or [], Path(trial_dir)/'private'
    reuse = next((h for h in reversed(accepted) if h.get('stage') == 'reuse' and h.get('artifacts')), None)
    found = accepted_artifact(reuse, REUSE_EVENTS) if reuse else None
    events = None
    if found is not None:
        copy = _copy(found, private/REUSE_EVENTS, 'fresh reuse record')
        events = [json.loads(line) for line in copy.read_text(encoding='utf-8').splitlines() if line.strip()]
    if case_id == 'tq9':
        model = json.loads((private/'package/driver/model.json').read_text(encoding='utf-8')) if (private/'package/driver/model.json').is_file() else {}
        invalid = tq9_errors(capabilities, model) if isinstance(capabilities, dict) else True
        mission = _mission(case_id, events or [], capabilities) if not invalid else None
    else:
        invalid = physical_errors(capabilities)
        measured = bool(valid_rows(events or [], capabilities)) if not invalid else False
        mission = {'name': 'fresh_physical_measurement', 'passed': measured}
    method = method_for(case_id)
    if invalid:
        return record('graded', False, [], [{'id': 'capability_mapping', 'reason': 'no capability mapping'}], sha,
                      capabilities_sha, _mission_fields(mission), method, protocol=protocol), None
    if events is None:
        return record('graded', False, [], [{'id': 'fresh_reuse', 'reason': 'fresh reuse recorded no call of the frozen driver'}],
                      sha, capabilities_sha, _mission_fields(mission), method, protocol=protocol), None
    draft = _draft(trial_dir, password_file)
    final = _held(draft, result['run_id'], sha)
    if final is None:
        truth = case_truth(case_id, run_dir, {'evaluator_password_file': str(password_file)})
        built = (_legacy_tq9(truth, events, capabilities, sha, revision) if case_id == 'tq9' else
                 _physical(truth, events, capabilities, sha, revision, reuse, private))
        if built is None:
            return record('graded', False, [], [{'id': 'fresh_physical_measurement', 'reason': 'No finite measurement of '
                          'every mapped output by the frozen package'}], sha, capabilities_sha, _mission_fields(mission), method,
                          protocol=protocol), None
        checks, records, raw = built
        final = {'run_id': result['run_id'], 'phase': 'final', 'method': method, 'revision': revision,
                 'frozen_artifact_sha256': sha, 'capabilities_sha256': capabilities_sha,
                 'contract': {'schema': 'benchmark-behavior/1', 'artifact_sha256': sha, 'checks': checks},
                 'records': records, 'raw': raw}
        draft['evaluations'].append(final)
        _draft(trial_dir, password_file, draft)
    return final_record(final, capabilities_sha, _mission_fields(mission), protocol=protocol), final

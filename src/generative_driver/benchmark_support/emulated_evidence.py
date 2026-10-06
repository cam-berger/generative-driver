"""Private encrypted evaluations and controller-accepted evidence assembly."""
import hashlib
import json
from pathlib import Path
from .cases import _state, _write
from .snapshots import read_snapshot

def _private(run_dir, options, value=None):
    from .truth import seal, unlock
    path = Path(run_dir)/'benchmark/evaluator-draft.enc'
    password = Path(options['evaluator_password_file']).read_text().strip()
    if value is not None:
        seal(value, path, password)
        return value
    if not path.exists():
        return {'evaluations': []}
    return unlock(path, password, hashlib.sha256(path.read_bytes()).hexdigest())


def _diagnose(case_id, run_dir, model_dir, capabilities, options):
    from .emulated import _session, _truth
    from .emulated import family_for
    family = family_for(case_id)
    from .behavior import validate_records
    from .emulated_actions import execute_plan, model_invoker
    session = _session(case_id, run_dir, options)
    path, state = _state(run_dir)
    truth = _truth(case_id, run_dir, options)
    saved = read_snapshot(run_dir)
    pin = {**saved['case_pin'], 'revision': state.get('revision', 0)}
    artifact = hashlib.sha256((Path(model_dir)/'model.json').read_bytes()).hexdigest()
    local = {**truth, 'artifact_sha256': artifact}
    contract = family.contract(pin, local, 'diagnostic')
    probes = []
    output = Path(session.info['private_dir'])/'diagnostic'
    output.mkdir(parents=True, exist_ok=True)
    invoke = model_invoker(model_dir, capabilities, session.binding, Path(run_dir)/'benchmark/probes', probes)
    records = execute_plan(session, invoke, family.build_plan(pin, local, 'diagnostic'), contract,
                           output/'raw.json', family.observations, truth['contracts']['time_policy']['sample_settle_seconds'])
    grade = validate_records(contract, records)
    payload = _private(run_dir, options)
    evaluation = {'phase': 'diagnostic', 'revision': pin['revision'], 'frozen_artifact_sha256': artifact,
                  'contract': contract, 'records': records, 'raw': json.loads((output/'raw.json').read_text())}
    payload['evaluations'] = [e for e in payload['evaluations'] if (e['phase'], e['revision']) != ('diagnostic', pin['revision'])] + [evaluation]
    _private(run_dir, options, payload)
    observed = Path(run_dir)/'benchmark'/f'diagnostic-{pin["revision"]}'/'observations.json'
    _write(observed, {'schema': 'benchmark-observations/2', 'model_sha256': artifact,
                      'diagnostics': [{'phase': 'diagnostic', 'records': records, 'checks': grade['checks']}]})
    state.setdefault('evaluations', []).append({key: evaluation[key] for key in ('phase', 'revision', 'frozen_artifact_sha256')} | {k: grade[k] for k in ('verdict', 'passed', 'total')})
    state['diagnostic'] = {'passed': grade['verdict'] == 'passed', 'probes': probes, 'observations': str(observed)}
    _write(path, state)
    return grade, records, probes, observed


def _final(case_id, run_dir, package, capabilities, options):
    from .emulated import _session, _truth
    from .emulated_actions import execute_plan, canonical_package_invoker
    from .emulated import family_for
    family = family_for(case_id)
    from .evidence import frozen_final_decision
    from ..configurator import digest
    path, state = _state(run_dir)
    payload = _private(run_dir, options)
    if any(e['phase'] == 'final' and e['revision'] == state.get('revision', 0) for e in payload['evaluations']):
        raise ValueError('Frozen final submission cannot be replayed')
    frozen = digest(package)
    if frozen != state['package_sha256']:
        raise ValueError('Frozen package changed before final grading')
    session = _session(case_id, run_dir, options)
    truth = _truth(case_id, run_dir, options)
    pin = {**read_snapshot(run_dir)['case_pin'], 'revision': state.get('revision', 0)}
    local = {**truth, 'artifact_sha256': frozen}
    contract = family.contract(pin, local, 'final')
    output = Path(session.info['private_dir'])/'final.json'
    records = execute_plan(session, canonical_package_invoker(package, capabilities, session.binding),
        family.build_plan(pin, local, 'final'), contract, output, family.observations,
        truth['contracts']['time_policy']['sample_settle_seconds'])
    if digest(package) != frozen:
        raise ValueError('Frozen package changed during final grading')
    evaluation = {'phase': 'final', 'revision': pin['revision'], 'frozen_artifact_sha256': frozen,
                  'contract': contract, 'records': records, 'raw': json.loads(output.read_text())}
    payload['evaluations'].append(evaluation)
    _private(run_dir, options, payload)
    decision = frozen_final_decision(contract, records)
    summary = {k: decision['evaluator'][k] for k in ('verdict', 'passed', 'total')}
    state.setdefault('evaluations', []).append({k: evaluation[k] for k in ('phase', 'revision', 'frozen_artifact_sha256')} | summary)
    state['final_evaluation'] = summary
    _write(path, state)
    return decision


def finalize(case_id, run_dir, accepted, options):
    """Seal actual copied controller handoffs, including partial/failed trials."""
    from .evidence import seal_run_evidence
    path, state = _state(run_dir)
    payload = _private(run_dir, options)
    snapshot = read_snapshot(run_dir)
    payload.update(schema='benchmark-run-evidence/1', case_pin=snapshot['case_pin'], execution_snapshot=snapshot,
                   accepted_artifacts=[], accepted_gates=[])
    summaries=[]
    for handoff in accepted:
        identity={key:handoff[key] for key in ('stage','revision','assignment_id')}
        artifacts=handoff['artifacts']
        for artifact in artifacts:
            payload['accepted_artifacts'].append({**identity, 'path':artifact['path'], 'sha256':artifact['sha256']})
        if not artifacts:
            continue
        selected=artifacts[0]
        refs=[]
        phase='final' if handoff['stage'] == 'reuse' else 'diagnostic' if handoff['stage'] in ('probe','ground') else None
        if phase and any(e['phase']==phase and e['revision']==handoff['revision'] for e in payload['evaluations']):
            refs=[{'phase':phase,'revision':handoff['revision']}]
        checks=[{'id':str(c.get('id',c.get('name'))),'passed':c.get('passed') is True} for c in handoff['checks']]
        gate={**identity,'artifact_sha256':selected['sha256'],'checks':checks,'evaluation_refs':refs, 'route':handoff.get('route')}
        payload['accepted_gates'].append(gate)
        summaries.append({**identity,'artifact_sha256':selected['sha256'],'verdict':'passed' if checks and all(c['passed'] for c in checks) else 'failed',
                          'passed':sum(c['passed'] for c in checks),'total':len(checks)})
    sealed=seal_run_evidence(payload,Path(run_dir)/'benchmark/run-evidence.enc',options['evaluator_password_file'])
    state['accepted_gates']=summaries
    state.setdefault('final_evaluation',{'verdict':'failed','passed':0,'total':0})['evidence_sha256']=sealed['sha256']
    state['evidence_sealed']=True
    _write(path,state)

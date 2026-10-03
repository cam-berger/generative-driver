"""Historical benchmark package execution, cleanup, and scoring."""
import json
import math
import subprocess
import sys
import time
from pathlib import Path

from ..benchmark import _read, case_root, score_observations


def package_execute(case_id, run_dir, stage, package_dir, operation, parameters=None,
                    binding=None, allow_effects=None, options=None):
    from .cases import _state, _load_case
    from .emulator import RenodeSession
    from ..toolkit import call_tool
    _, state = _state(run_dir)
    package = Path(package_dir).resolve()
    expected = Path(state.get('package_copies', {}).get(stage, '')).resolve()
    if package != expected:
        return {'ok': False, 'error': {'code': 'package', 'message': 'Only the stage-assigned package may execute'}}
    checked = call_tool('emit_check', {'run_dir': str(Path(run_dir).resolve()/'benchmark/package-checks'),
                                      'package_dir': str(package),
                                      'expected_manifest_sha256': state['package_sha256']})
    if not (checked.get('ok') and checked.get('integrity_ok') and checked.get('runtime_current')
            and checked.get('replay_status') == 'passed'):
        return {'ok': False, 'error': {'code': 'package', 'message': 'Package integrity/current-runtime check failed'}}
    session = state['session']
    if binding != session['binding']:
        return {'ok': False, 'error': {'code': 'binding', 'message': 'Package binding differs from evaluator-selected emulator'}}
    command = [sys.executable, '-I', '-S', '-B', '-c',
        'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
        str(package), 'execute', operation, '--parameters', json.dumps(parameters or {}),
        '--binding', json.dumps(binding)]
    for effect in allow_effects or []:
        # Reads are implicit in the emitted CLI; only side effects need a flag.
        if effect != 'read':
            command.extend(['--allow-effect', effect])
    emulator = RenodeSession(session)
    emulator.monitor('start')
    try:
        completed = subprocess.run(command, cwd=package.parent, capture_output=True, text=True, timeout=30)
    finally:
        emulator.monitor('pause')
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        result = {'ok': False, 'error': {'code': 'package', 'message': completed.stderr[-1000:]}}
    _, _, truth = _load_case(options or {})
    observed = emulator.observe(truth)
    row = {'stage': stage, 'revision': state.get('revision', 0), 'attempt_id': state['package_attempts'][stage], 'operation': operation,
           'parameters': parameters or {}, 'ok': completed.returncode == 0 and result.get('ok') is True,
           'result': result, 'observation': observed, 'time': time.time(), 'package_sha256': state['package_sha256'], 'time_policy': 'emulation paused between package calls'}
    path = Path(run_dir)/'benchmark/package-events.jsonl'
    with path.open('a') as stream:
        stream.write(json.dumps(row, allow_nan=False)+'\n')
    return {**result, 'observation': observed, 'evidence': str(path)}


def cleanup(case_id, run_dir, options=None):
    """Stop only the evaluator-owned emulator; preserve evidence for diagnosis."""
    from .cases import _state, _write
    from .emulator import RenodeSession
    path, state = _state(run_dir)
    if state.get('session'):
        RenodeSession(state['session']).stop()
        state['previous_session'] = state.pop('session')
        _write(path, state)
    return {'ok': True}


def score(report, password_file=None):
    """Regrade recorded behavior; missing stage evidence cannot become overall success."""
    data = _read(report)
    case = data.get('case')
    if isinstance(case, dict):
        case = case['id']
    if case == 'setup-smoke':
        grade = score_observations({'checks':[{'id':'temperature','expected':21.5,'absolute_tolerance':0}]},
                                   data.get('observations', {}))
        return {'schema':'benchmark-score/1','case':case,'execution':'scripted-replay',
                'model_benchmark':False, **grade}
    manifest = json.loads((case_root()/'cases'/str(case)/'case.json').read_text())
    for key, expected in {'case_version':manifest['version'], 'evaluator_version':manifest['evaluator_version'], 'truth_sha256':manifest['truth']['sha256']}.items():
        if key in data and data[key] != expected:
            raise ValueError('Report '+key+' does not match installed case')
    from .registry import validate_workflow_manifest
    validate_workflow_manifest(manifest)
    from .emulator import truth_for_case
    truth = truth_for_case(case_root(), manifest, {'evaluator_password_file':password_file})
    state = data.get('case_state', {})
    run_dir = data.get('run_dir')
    if not state and run_dir:
        path = Path(run_dir)/'benchmark/state.json'
        if path.is_file():
            state = json.loads(path.read_text())
    observations = data.get('observations', state.get('probe_evaluation', {}).get('observations', {}))
    if case == 'bme280':
        grounding = state.get('physical_grounding', {})
        observations = state.get('physical_final', {}).get('observations', {})
        reference = state.get('physical_final', {}).get('reference', grounding.get('reference', {})).get('reference', {})
        if set(reference) != set(truth['max_tolerances']) or any(
                type(v.get('absolute_tolerance')) not in (int,float) or not 0<v['absolute_tolerance']<=truth['max_tolerances'][k]
                for k,v in reference.items()):
            raise ValueError('Recorded physical reference is incomplete or exceeds evaluator uncertainty limits')
        final=state.get('physical_final', {})
        measured=final.get('measurement_time')
        observed_at=final.get('reference', {}).get('observed_at')
        if (type(measured) not in (int,float) or type(observed_at) not in (int,float)
                or not math.isfinite(measured) or not math.isfinite(observed_at)
                or abs(measured-observed_at)>truth['max_reference_age_seconds']):
            raise ValueError('Recorded final measurement lacks a current independent reference')
        contract = {'checks':[{'id':k,'expected':v['value'],'absolute_tolerance':v['absolute_tolerance']} for k,v in reference.items()]}
        grade = score_observations(contract, observations)
    else:
        grade = score_observations(truth, observations) if truth.get('checks') else {'verdict':'unscored','checks':[]}
    stages = data.get('stages', state.get('stage_verdicts', {}))
    missing = [s for s in manifest['required_stages']
               if stages.get(s, {}).get('evaluator_status', stages.get(s, {}).get('status')) != 'passed']
    if data.get('schema') == 'benchmark-report/1':
        if data.get('workflow_status') != 'completed':
            missing.append('completed_workflow')
        missing.extend(s+'_accepted_handoff' for s in manifest['required_stages'] if stages.get(s, {}).get('workflow_status') != 'accepted')
    return {'schema':'benchmark-score/1','case':case,'execution':manifest['execution'],
            'model_benchmark':True,'verdict':'passed' if grade['verdict']=='passed' and not missing else 'failed',
            'behavior':grade,'missing_required_gates':missing,'truth_sha256':manifest['truth']['sha256'],
            'scope':'Recorded evidence regraded; does not rerun a model or a device'}

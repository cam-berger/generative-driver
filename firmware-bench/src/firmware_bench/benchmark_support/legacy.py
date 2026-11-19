"""Offline regrade of reports recorded under the earlier protocol, whose legacy stages were graded inside the run."""
import json
from pathlib import Path

from ..benchmark import _read, case_root, score_observations


def score(report, password=None, *, evidence_path=None):
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
    required = manifest['required_stages']
    if (data.get('verdict') == 'incompatible'
            or ('workflow_stages' in data and data['workflow_stages'] != required)):
        raise ValueError('Report workflow is incompatible with the installed fresh reuse case')
    state = data.get('case_state', {})
    run_dir = data.get('run_dir')
    if not state and run_dir:
        path = Path(run_dir)/'benchmark/state.json'
        if path.is_file():
            state = json.loads(path.read_text())
    if (set(data.get('stages', {})) - set(required)
            or set(state.get('stage_verdicts', {})) - set(required)):
        raise ValueError('Report stage evidence is incompatible with the installed fresh reuse case')
    from .emulator import truth_for_case
    truth = truth_for_case(case_root(), manifest, password)
    observations = data.get('observations', state.get('probe_evaluation', {}).get('observations', {}))
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

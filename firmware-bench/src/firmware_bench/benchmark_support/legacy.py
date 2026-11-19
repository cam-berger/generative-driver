"""Offline regrade of the report of the installation replay, `setup-smoke`."""
from ..benchmark import _read, score_observations


def score(report, password=None, *, evidence_path=None):
    """Regrade the recorded observations of the installation replay; a report of another case gives None."""
    data = _read(report)
    case = data.get('case')
    if isinstance(case, dict):
        case = case['id']
    if case == 'setup-smoke':
        grade = score_observations({'checks':[{'id':'temperature','expected':21.5,'absolute_tolerance':0}]},
                                   data.get('observations', {}))
        return {'schema':'benchmark-score/1','case':case,'execution':'scripted-replay',
                'model_benchmark':False, **grade}

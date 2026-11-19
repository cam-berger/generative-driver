"""Offline regrade of the report of the installation replay, `setup-smoke`.

The regrade of the two dropped cases went with their code (Feature 12 of the untangle Run,
docs/features/untangle/12-firmware-bench.md, 12-B3).
"""
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

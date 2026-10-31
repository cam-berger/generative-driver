"""The words a run's verdict and execution are reported with. The configurator, the reports and the evaluator all use
them; they live here so the evaluator imports neither the configurator nor the replay executor."""

RUNTIME = 'replay'  # the runtime name of the replay executor


def equivalence_label(equivalence):
    return {True: 'equivalent', False: 'not equivalent'}.get((equivalence or {}).get('equivalent'), 'unscored')


def verdict_reason(equivalence):
    """The completed run's reason: its verdict and, unless equivalent, the first differences."""
    label = equivalence_label(equivalence)
    reasons = [d['reason'] for d in equivalence.get('diff') or [] if isinstance(d, dict) and isinstance(d.get('reason'), str)]
    detail = '; '.join(reasons[:3]) + ('; ' + str(len(reasons) - 3) + ' more' if len(reasons) > 3 else '')
    return 'Final verdict: ' + label + (': ' + detail if label != 'equivalent' and reasons else '')


def execution_label(case_execution, runtime):
    """How a benchmark run executed: a candidate replayed on an emulated case is `replay-emulation`, since no model ran
    it, and never an actual-agent result."""
    return 'replay-emulation' if runtime == RUNTIME and case_execution == 'actual-agent-emulation' else case_execution

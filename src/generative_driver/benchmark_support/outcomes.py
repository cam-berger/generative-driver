"""The one outcome of every planned trial, and the pass rate counted from the outcomes.

Reporting code, outside the grader's identity: it classifies what a run and its grade recorded and grades nothing.
Passes and failures are the graded trials: the evaluator's grade, a stop the system caused or a spent budget. A trial
that a host or operator problem, or missing evidence, kept from a grade is a `fault`, counted but not graded; one whose
grade left a step undecided is `unknown`. The rest are `flagged` (the transcript audit found a worker touching a
forbidden path), `running` and `not_run`."""
import math

# Each outcome, with the key of the count it adds to in `pass_rate`.
COUNT_KEYS = {'pass': 'passes', 'failure': 'failures', 'unknown': 'unknown', 'fault': 'faults', 'flagged': 'flagged',
              'running': 'running', 'not_run': 'not_run'}
OUTCOMES = tuple(COUNT_KEYS)
IN_FLIGHT = ('pending', 'launching', 'running', 'queued')
STOPPED = ('blocked', 'failed', 'cancelled')
# The stops the system under test causes itself.
SELF_CAUSED = ('safety', 'integrity', 'model')


def _budget_spent(report: dict) -> bool:
    """Whether the report shows the run used its whole budget: both times are finite numbers and the elapsed time is
    not less than the budget. An export without `effective_budget_seconds` shows nothing."""
    elapsed, budget = report.get('elapsed_seconds'), report.get('effective_budget_seconds')
    numbers = all(type(value) in (int, float) and math.isfinite(value) for value in (elapsed, budget))
    return numbers and elapsed >= budget


def classify(trial: dict, report: dict | None, *, flagged: bool = False) -> str:
    """The outcome of a planned trial from its suite row and the report of its run, by the first rule that applies:

    1. `not_run` when it has no run.
    2. `flagged` when `flagged`.
    3. `running` while the run is pending, launching, running or queued, or its grade is pending.
    4. A stopped run (blocked, failed or cancelled) is a `failure` when the stop was safety, integrity or model, or
       an operator stop with the budget spent, and otherwise a `fault`.
    5. By the equivalence status: `graded` is a `pass` when equivalent and else a `failure`, `no-driver` is a
       `failure`, `unknown` is `unknown`, and `unscored` or no status is a `fault`.

    The run's status is the report's `workflow_status` when it has one, else the row's `status`."""
    if not trial.get('run_id'):
        return 'not_run'
    if flagged:
        return 'flagged'
    report = report if isinstance(report, dict) else {}
    status = report.get('workflow_status') or trial.get('status')
    grade = report.get('equivalence_status')
    if status in IN_FLIGHT or grade == 'pending':
        return 'running'
    if status in STOPPED:
        category = trial.get('outcome_category')
        spent = category == 'operator' and _budget_spent(report)
        return 'failure' if category in SELF_CAUSED or spent else 'fault'
    if grade == 'graded':
        equivalence = report.get('equivalence')
        return 'pass' if isinstance(equivalence, dict) and equivalence.get('equivalent') is True else 'failure'
    if grade == 'no-driver':
        return 'failure'
    return 'unknown' if grade == 'unknown' else 'fault'


def pass_rate(outcomes: list[str]) -> dict:
    """Every planned trial in exactly one count, and `rate`, passes / (passes + failures), or None while none is
    graded. A name that is not an outcome raises ValueError instead of going uncounted."""
    outcomes = list(outcomes)
    for outcome in outcomes:
        if outcome not in OUTCOMES:
            raise ValueError('Unknown trial outcome: ' + repr(outcome))
    counts = {key: outcomes.count(outcome) for outcome, key in COUNT_KEYS.items()}
    graded = counts['passes'] + counts['failures']
    return {**counts, 'planned': len(outcomes), 'rate': counts['passes'] / graded if graded else None}


def pass_rate_line(rate: dict) -> str:
    """The pass rate on one line, such as `7/9 pass, 1 unknown, 2 faults, 12 planned`, with a flagged count after it
    when any trial was flagged."""
    line = (f"{rate['passes']}/{rate['passes'] + rate['failures']} pass, {rate['unknown']} unknown, "
            f"{rate['faults']} faults, {rate['planned']} planned")
    if rate['flagged']:
        line += f", {rate['flagged']} flagged"
    return line

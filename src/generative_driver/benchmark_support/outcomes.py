"""The one outcome of every planned trial, and the pass rate counted from the outcomes.

Reporting code, outside the grader's identity: it classifies what a run and its grade recorded and grades nothing.
Passes and failures are the graded trials: the evaluator's grade, a stop the system caused or a spent budget. A trial
that a host or operator problem, or missing evidence (no grade, an unscored one, or one the exporter could not
confirm), kept from a grade is a `fault`, counted but not graded; one whose grade left a step undecided is `unknown`.
The rest are `flagged` (the transcript audit found a worker touching a forbidden path), `running` (a grade counts only
once its slot has settled) and `not_run`."""
import math

# Each outcome, with the key of the count it adds to in `pass_rate`.
COUNT_KEYS = {'pass': 'passes', 'failure': 'failures', 'unknown': 'unknown', 'fault': 'faults', 'flagged': 'flagged',
              'running': 'running', 'not_run': 'not_run'}
OUTCOMES = tuple(COUNT_KEYS)
IN_FLIGHT = ('pending', 'launching', 'running', 'queued')
STOPPED = ('blocked', 'failed', 'cancelled')
# The stops the system under test causes itself.
SELF_CAUSED = ('safety', 'integrity', 'model')
# The equivalence statuses of a grade the evaluator recorded; any other status leaves the trial without one.
RECORDED = ('graded', 'no-driver', 'unknown')


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
    5. `fault` when the report's verdict is `unqualified`: the exporter could not confirm the grade.
    6. By the equivalence status: `graded` is a `pass` when equivalent and else a `failure`, `no-driver` is a
       `failure` and `unknown` is `unknown`, each only once the row is `finished` (its slot settled) and `running`
       before that. Any other status (`unscored`, none, or one not named here) is a `fault`, settled or not.

    Rules 3 and 4 take the run's status from the report's `workflow_status` when it has one, else from the row's
    `status`; rule 4 also counts a stop on the row alone, as a suite report blocks the row of a completed child that
    is still stopping or has an uncertain effect and leaves its report completed. A row a suite report exports can
    stand for both arguments, but it has no `workflow_status` or `effective_budget_seconds`, so a spent budget does
    not show in it, and it reads `unqualified` for a graded equivalent driver until its slot settles."""
    if not trial.get('run_id'):
        return 'not_run'
    if flagged:
        return 'flagged'
    report = report if isinstance(report, dict) else {}
    status = report.get('workflow_status') or trial.get('status')
    grade = report.get('equivalence_status')
    if status in IN_FLIGHT or grade == 'pending':
        return 'running'
    if status in STOPPED or trial.get('status') in STOPPED:
        category = trial.get('outcome_category')
        spent = category == 'operator' and _budget_spent(report)
        return 'failure' if category in SELF_CAUSED or spent else 'fault'
    if report.get('verdict') == 'unqualified':
        return 'fault'
    if grade not in RECORDED:
        return 'fault'
    if trial.get('status') != 'finished':
        return 'running'
    if grade == 'graded':
        equivalence = report.get('equivalence')
        return 'pass' if isinstance(equivalence, dict) and equivalence.get('equivalent') is True else 'failure'
    return 'unknown' if grade == 'unknown' else 'failure'


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

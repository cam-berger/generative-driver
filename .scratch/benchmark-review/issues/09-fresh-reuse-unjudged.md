# Fresh reuse is not judged on generated devices, yet a joint verdict is printed

Status: needs-triage

Checked: read (`benchmark_support/generated.py:230-232`, `scorecard.py:735-738`).

`reuse_passed` for the generated family returns None: no Feature holds the judgement yet ([bench harness notes](../../../docs/features/bench-harness/noticed.md), Feature 03). The scorecard still prints the earlier studies' joint verdict, an equivalent driver and a successful fresh mission, which on a generated set can only read 0 of N.

The baseline spec's call was to judge fresh reuse by the grader's memory reads after the reuse stage against the state the answer key's `mission.state` predicts.

## Direction

Either build that judgement before the baseline, or leave the joint verdict off a generated set's scorecard and say that fresh reuse is not judged there.

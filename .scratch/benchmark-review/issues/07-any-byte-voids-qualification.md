# Any byte changed in grading code voids every qualification

Status: needs-triage

Checked: the hashing was read (`benchmark_support/grader_identity.py:100-125`); the size of the core is reported.

A grader identity is the sha256 of the raw source bytes of every module the grader imports. A comment, a docstring or a blank line changes it. The shared core is about 22 modules and 3,800 lines (reported), and it still holds some families' code ([bench harness notes](../../../docs/features/bench-harness/noticed.md), Feature 01), so most edits move every case's identity. Each move means a fresh native calibration, new pins, and comparisons that no longer line up with the stored baseline.

A set's qualification record also holds each run's wall-clock `seconds` (`set_qualification.py:165`), so two qualifications of the same set under the same grader never give the same digest (the field was read; that the digest covers it is reported).

## Direction

- Hash each module's syntax tree with docstrings removed, so only a change in behaviour moves an identity.
- Move the remaining family code and the report projections out of the core.
- Leave timings out of the digest and keep them beside it.

Do this before the next calibration: every identity is already stale, so the move costs nothing now.

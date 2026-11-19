# No result exists on devices the system was not developed against

Status: needs-triage

Checked: read.

- `resources/registrations/sets.json` registers no generated set, so no scoring or development set has run.
- Every recorded model result is three repetitions of a familiar case: 15/15 across five profiles on 2026-10-27, and 9/9 on 2026-10-28–29 under the earlier gated workflow. The exact 95% interval for 3/3 is 29% to 100%.
- Since that study's revision (`625f55a`), `benchmark.py` and `benchmark_support/` changed by 7,380 added and 1,519 removed lines over 79 commits. Fixture devices and scripted workers exercised all of it; no model, real emulator or real vault has.
- The shipped calibration records are stale ([design, Limits](../../../docs/design.md)), and `tq9-v3`, which the latest 9/9 used, is registered without its assets (reported).

## Direction

Before the baseline: requalify the anchor cases on the pinned revision, then record one end-to-end dry run of a small development set with a real model and real Renode, through admission, the vault gate, a faulted slot's rerun, sealing and the scorecard. The baseline's cost (about 150 trials) is too high to be the first real run of that path.

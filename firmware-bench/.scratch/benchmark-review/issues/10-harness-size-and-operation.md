# The bench harness holds about fifteen jobs in one file and needs hand steps at every stop

Status: needs-triage

Checked: reported, except the test variable, which was read.

- `benchmark_support/harness.py` is about 1,900 lines: process launch, the password, admission, the device provider's sockets, the token, the vault gate, slot reservation, trials, reruns, the audit, sealing and resume. `_suite_trial` takes 13 parameters, and other modules import its private names.
- `GD_TEST_HARNESS_FIXTURE` runs a file inside the process that holds the passwords (`harness.py`, near its entry point). The refusal of a shipped set under that variable catches mistakes only.
- An emulator whose stop cannot be confirmed blocks a suite that is not a scoring suite for good.
- Suites run one trial at a time under a seven-day cap, so a 150-device set at seven minutes a trial has little slack once stops are counted.
- Every stop needs the operator to mount the vault, resume and unmount it again.

## Direction

Split the harness along the jobs above before adding to it; the trial store and the vault gate are already separate modules to copy. Move the test variable's hook out of the production entry point. Time a resume cycle and a full set's admission before the first scoring run.

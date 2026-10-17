# Benchmark handoff — 2026-10-06

Latest frozen trial: `272c1b1`, `gpt-6.1-sol`, medium reasoning, version 5. Result: 2/3 autonomous six-stage completions. TQ9 passed after one repair/emulator recovery; sensor passed after one repair. Store reached 364/366 diagnostics, then stopped on an intentional idle-commit rejection with exhausted repair budget. Pending-update/state checks passed; its final candidate was never graded. The historical 3/3 condition for pushing a PR was unmet. See [trial results](../research/2026-10-06-transaction-recovery-results.md) and [index](transaction-recovery-trial-verification.json).

All 127 managed operations finished; identities/settings stayed unchanged. The service stopped and emulator processes were absent at postflight. Store uncertainty remains recorded. No extra trial or rescue replaced the failure.

New runs use `assistive/1`, a [new series](../design.md#assistive-protocol-amendment--2026-10-07). Run fresh native calibration with the password holder before a separately authorized nine-trial model test. Earlier gated results below retain their meaning.

The user later authorized [PR #5](https://github.com/cam-berger/generative-driver/pull/5), combining pipeline `359f5be` with the dashboard despite the original 3/3 condition. Its [integration index](pr5-integration-verification.json) records 471 source/installed tests per environment, zero archive skips, browser/replay checks and authenticated admissions. No new model trial ran.

## Gated-series follow-up

1. Reproduce the expected-negative-probe stop through existing runtime/evaluator/configurator seams. Distinguish independently verified rejection from model discrepancy without accepting candidate claims, replaying uncertain writes or expanding budgets.
2. Keep any new trial separately frozen with explicit settings/budgets. After feasibility closes, run fixed-configuration repetitions; the nine-slot study remains unrun.
3. Follow the proposed [growth roadmap](../research/2026-10-03-benchmark-growth-plan.md): admit P2IM Gateway, P2IM CNC and Zephyr Modbus with independent contracts/provenance before choosing the next intake batch.

## Evidence to preserve

| Snapshot | Outcome / scope |
|---|---|
| [Pipeline repair](../research/2026-10-05-pipeline-repair-results.md) | 0/3; tool-use progress exposed mapping/refusal faults |
| [Capability repair](../research/2026-10-05-capability-repair-results.md) | 0/3; feedback and ground-routing gaps |
| [Context repair](../research/2026-10-05-context-repair-results.md), `21359b4` | 2/3; TQ9 9/9 and sensor 85/85 final; store pending update blocked; [441-test/30-native index](context-repair-verification.json) |
| [Unchanged store repeat](../research/2026-10-05-store-repeat-results.md) | Separate 1/1, 696/696 final; visible probes missed pending update; [index](store-repeat-verification.json) |
| [Transaction/recovery plan](transaction-recovery-plan.md) | Mandatory state coverage and trusted emulator replacement; [V4 review gap retained](transaction-recovery-v4-verification.json) |
| [V5 qualification](transaction-recovery-verification.json) | 461 source/installed tests each, zero archive skips; 32 native grades, three source/six installed admissions; unchanged firmware |

Version 3 passes do not imply version 5 coverage. Version 5 native adapter recovery and deterministic controller durability were separate proofs; Windows was unrun. Model trials remain distinct from scripted/native qualification.

Historical [PR #2](https://github.com/cam-berger/generative-driver/pull/2) followed [PR #1](https://github.com/cam-berger/generative-driver/pull/1) at `24d8861`, correcting historical scoring and checkout integrity. See [review](../research/2026-10-03-pr1-review.md), [follow-up index](pr1-followup-verification.json), [original index](benchmark-final-verification.json) and [verification limits](../verification.md). That follow-up used no model inference or physical device.

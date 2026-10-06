# Transaction coverage and recovery trial — 2026-10-06

One fresh trial ran on each of the three original firmware families at source `272c1b1028cd11f8307d55ce12c1c60e0646ce2e`, using the tested installed Python 3.13.12 distribution, Codex CLI 0.159.2, `gpt-6.1-sol` and medium reasoning. Case/evaluator version 5 adds the transaction and independent rejection checks described in the [implementation verification](../verification.md#transaction-coverage-and-emulator-recovery--2026-10-06). Original firmware images, case seed 0, three-hour child budgets and the two-repair limit were retained. One sequential queue had a nine-hour overall deadline.

The decision frozen before inference required three autonomous completions, all six accepted stages, fresh package-only reuse and independent sealed regrading. No failed trial was replaced or repeated. No candidate or advice from an earlier run, extra authorization or budget extension was supplied. Prior candidate artifacts and normal feedback generated within the same run remained available for bounded repair.

## Results

Recorded and independent regrades agree on **2/3 completed workflows**. The three trials took 35.2 minutes overall. The original 3/3 publication condition was not met, so publication was initially held. The user subsequently authorized including the pipeline fixes and all results in [PR #5](https://github.com/cam-berger/generative-driver/pull/5). This publication decision does not change the trial outcome.

| Family | Current accepted stages | Diagnostic history | Final behavior | Model repairs | Emulator recoveries | Workflow minutes | Reported tokens |
|---|---:|---|---|---:|---:|---:|---:|
| TQ9 | 6/6 | 9/9 after recovery | 9/9 passed | 1 | 1 | 10.2 | 2,087,841 |
| Sampled sensor | 6/6 | 14/17 → 17/17 | 85/85 passed | 1 | 0 | 10.8 | 1,622,834 |
| Parameter store | 2/6 | Mapping refused; then 364/366 | Not reached | 2 | 0 | 14.2 | 2,299,681 |

All 23 worker assignments reported usage: 6,010,356 total tokens, including 5,066,624 cached input tokens as a subset. These are runtime-reported usage counts, not a billing estimate. There were zero operator inputs or interventions. All 127 managed operations finished and their worker dispatch identities correlate; the store's unresolved-effect flag remains recorded. Completing an operation does not imply its device effect was accepted.

### Stage performance

Cumulative worker seconds include every attempt at that stage. Tool time can overlap worker time, and workflow time also includes preparation and evaluation, so these columns should not be added to obtain end-to-end latency. Tool counts cover configurator-managed operations; worker shell commands are separate. The [verification index](../implementation/transaction-recovery-trial-verification.json) includes attempt counts, tool activity and stage usage.

| Stage | TQ9 seconds | Sensor seconds | Store seconds |
|---|---:|---:|---:|
| Acquire | 25.3 | 28.7 | 29.4 |
| Interpret | 235.0 | 281.8 | 453.5 |
| Probe | 151.9 | 124.4 | 338.0 |
| Ground | 76.9 | 88.4 | Not reached |
| Emit | 31.4 | 33.7 | Not reached |
| Reuse | 57.2 | 51.3 | Not reached |

## What the run established

TQ9 exercised the new recovery path during an actual model workflow: a completed model-failure outcome led to verified disposal and replacement of its owned emulator, then a bounded interpretation revision. The failed operation remained failed; it was not replayed. The revised model passed diagnostics, fresh reuse and final behavior. The sensor repaired three diagnostic failures and completed without emulator replacement.

The store's first repair corrected a capability mapping that named no operation in the candidate model. Its next diagnostic evaluation passed 364/366 checks. Both failures concerned independently verified rejection behavior; the new pending-update and ending-state checks in that diagnostic evaluation passed. The second repair addressed rejection handling, but its subsequent probe deliberately called commit with no active transaction. The device returned a complete rejection, the runtime reported a response-expectation mismatch, and the configurator retained the effect as unresolved. With both repairs consumed, it stopped before grounding, packaging, reuse or final grading.

This stop does not establish that the second revision fails the required valid transaction behavior: that revision never completed evaluation. Nor does candidate-controlled rejection metadata establish that clearing uncertainty would be safe. The observed boundary between an expected negative probe, a model discrepancy and trusted effect reconciliation needs a focused controller regression. The system's recorded model-fault attribution is retained; a framework defect is a hypothesis to investigate, not a demonstrated conclusion. The partial regrade's failed 0/0 final summary is not an executed final test result.

## Verification and limits

The existing 461-test source and installed Python 3.11/3.13 suites, 32 native qualification sessions and authenticated version 5 admissions were reused after exact identity checks. Fresh setup replay, installation payload checks and preflight passed. Postflight confirmed unchanged installed implementation, evaluator, CLI, native tools, settings, accepted artifacts and budgets. Independent design and visible-execution reviews checked the result. The private service stopped, its process exit was confirmed, and the retained final emulator processes are absent; no failure was reconciled merely to change its outcome.

Visible review covers assigned inputs, tool allowlists and retained worker transcripts. Both successful reuse assignments had the emitted package as their sole input. Full transmitted stdin prompts and complete launch argument arrays were not separately retained; their construction can be reviewed in the frozen implementation. This is not proof of filesystem isolation.

These are outcome-aware engineering trials on familiar authored emulated cases. The earlier [version 3 three-family result](2026-10-05-context-repair-results.md) remains 2/3 and the [unchanged store repeat](2026-10-05-store-repeat-results.md) remains a separate 1/1. Version 5 changes coverage, so scores are not directly comparable. One attempt per family establishes neither general reliability nor a causal improvement estimate. No physical devices, Windows execution, live client UI validation or broader model study were included.

Next: resolve and test the expected-negative-probe contract through the existing runtime, evaluator and configurator seams, preserving independent evidence and the no-uncertain-write-replay rule. Preserve this failed trial and freeze any later model experiment separately.

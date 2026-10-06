# Unchanged parameter-store repeat — 2026-10-05

The user requested one fresh repeat of the blocked parameter-store trial, with nothing additional supplied. The repeat used the identical frozen installation from `21359b466f24d58cdb9a0bfb838a64c58de713b0`, `gpt-6.1-sol`, medium reasoning, Codex CLI 0.159.2, original firmware, case seed 0, tools, evaluator, write grant, three-hour workflow budget and two-repair limit. A new controller, emulator, workspaces and agent sessions started from acquisition. No prior candidate, report, diagnostic feedback, suggested fix or extra instruction was assigned to the workers. Normal feedback generated within the new run remained available under the existing workflow.

## Recorded and independently regraded result

| Measurement | Previous store trial | Fresh unchanged repeat |
|---|---|---|
| Workflow | Blocked during revised probe | All six stages and fresh reuse completed |
| Current accepted gates | 2/6 | 6/6 |
| Diagnostic checks | 174/174 before ground-requested repair | 174/174 |
| Final checks | Not reached | 696/696 |
| Model repairs | 1 | 0 |
| Workflow minutes | 10.9 | 10.1 |
| Reported tokens | 1,861,857 | 1,304,078 |
| Operator inputs | One cleanup-only reconciliation | 0 |

Independent sealed regrading agrees with the repeat's qualified completion, 1/1. Six workers reported usage, including 1,072,384 cached input tokens as a subset of the total. Workflow time was 606.110 seconds. All 38 managed operations finished. Postflight verified unchanged installed code, evaluator, CLI, native tools and model settings, with no uncertain effects, stopping workers or interventions. The repeat's private service was stopped and its process confirmed absent.

## What changed in the observed behavior

The repeat completed the benchmark without demonstrating recovery from the earlier failure condition. Its generated update still follows open transaction → stage value → apply, assuming no transaction is already pending. It has no preceding cancellation or other transaction-state normalization. All eight visible probe updates started with no pending transaction; pending transactions were instead followed by reads and abort or commit. Fresh reuse likewise updated before staging and aborting.

Consequently, the previously failing update-while-pending sequence was not exercised in the visible repeat. Code comparison retains the same assumption that caused the earlier rejection. The 696/696 final result establishes the evaluator's tested behavior; it does not establish that this known edge case is fixed. No extra probe was added after the run to change its scope or outcome.

This exposes a coverage gap: benchmark completion can depend on whether an agent's exploratory probes encounter an unsupported state transition. The repeat answers the user's question descriptively—it completed under unchanged conditions—while the known transaction-state issue remains open. It supplies no evidence that a code fix occurred or that the workflow is reliably successful across repetitions.

## Provenance and scope

The previous [three-family result](2026-10-05-context-repair-results.md) remains 2/3; this selected repeat is recorded separately with denominator 1. It does not replace the failed trial or convert the earlier suite into 3/3. Same case seed fixes the case selection, not the provider's unexposed model sampling.

The existing 441-test installed Python 3.11/3.13 checks and native qualification remain applicable because installation, evaluator and tool identities matched before and after this repeat. The selected case passed authenticated admission, and a fresh scripted setup replay passed before launch; no additional model trials or native qualification runs were performed. [The verification index](../implementation/store-repeat-verification.json) records the retained identities and measurements. Visible trace review is not proof of filesystem isolation, and emulated behavior is not physical-device validation.

The next benchmark change should add the known transaction-state sequence to a fixed evaluation path, then separately evaluate candidate recovery and trusted emulator reconciliation. No implementation or evaluation-contract changes were made for this repeat. See [the handoff](../implementation/benchmark-handoff.md).

# Context repair experiment — 2026-10-05

## Frozen setup

This engineering rerun follows the [capability repair experiment](2026-10-05-capability-repair-results.md). It uses the same `gpt-6.1-sol`, medium reasoning, Codex CLI 0.159.2 and native macOS emulation: one original-image trial per authored firmware family, seed 0, sequential execution, three hours per workflow and at most two model repairs. Acceptance requires all six stages, fresh package-only reuse and independent final behavior. No physical device was used.

Implementation commit `556d5e0` supplies explicit repair instructions, public task semantics and sealed prior candidate/diagnostic evidence; preserves capability mappings, transactions and paired independent observations for grounding; and routes ground discrepancies through the remaining repair budget. It also distinguishes final binding failures from host execution failures and rejects external links when copying candidate context. The frozen tested source is `21359b466f24d58cdb9a0bfb838a64c58de713b0`, including refreshed paired qualification assets. Firmware and authored behavioral contracts are unchanged. The evaluator identity is `f5eb06ffcf1584cbf0435fc1d0298183c44d0f71474956e6fa6efee236d74914`.

## Results

Qualified autonomous completion was **2/3**, with recorded and independently regraded success outcomes agreeing for every family. The store's final evaluation was not reached; its zero-check partial failed verdict is not an executed final failure.

| Family | Current accepted gates | Repairs | Diagnostic checks by revision | Final checks | Minutes | Reported tokens |
|---|---:|---:|---|---|---:|---:|
| TQ9 | 6/6 | 0 | 9/9 | 9/9 | 9.6 | 1,193,315 |
| Sampled sensor | 6/6 | 1 | 14/17 → 17/17 | 85/85 | 11.3 | 1,740,158 |
| Parameter store | 2/6 | 1 | 174/174; revised probe blocked before grading | Not reached | 10.9 | 1,861,857 |

TQ9 completed acquisition, interpretation, probing, grounding, emission and fresh reuse without repair. The fresh agent read the emitted package and used its gateway to read temperature, arm, set duty and disarm; measured emulator effects agreed. This trial demonstrates the complete path, but does not exercise self-repair.

The sensor initially recovered acquisition, retained reads and sequence behavior but left physical temperature unsupported. Its repair read the supplied task contract, previous model/mapping, defect feedback, transactions and paired independent measurements. It added a dynamic temperature conversion while preserving the already-passing protocol behavior. Diagnostics improved from 14/17 to 17/17. A fresh package-only agent then performed two acquisitions and corresponding retained reads; final behavioral checks passed 85/85. This is observed use of the repaired evidence handoff, beyond merely receiving a passing label.

The store initially passed 174/174 diagnostics. Grounding identified incomplete framing of a rejection response outside those successful canonical cases and requested revision. The new route worked: the repaired model captured the complete rejection. Its update operation still assumed no transaction was pending, however. A later probe attempted an update while a transaction was active; the device rejected opening another transaction before staging or committing the requested value. The controller conservatively blocked further writes because an effectful call had failed after opening the transport. One repair remained available, but uncertain state prevented further execution. The stored workflow category is `unknown`; the triggering runtime failure is a model fault. Historical probe acceptance belongs to revision 0 and does not make revision 1 accepted.

The stopped store outcome is preserved. Native cleanup had already disposed of its private emulator; process absence, no active session and stopped workers were verified before one explicit cleanup-only operator response cleared the recorded uncertainty flag. That response did not establish that the failed command was side-effect-free. No candidate was edited, trial resumed, budget extended or extra model repetition launched. The response is counted as an intervention; the store is neither an autonomous nor assisted completion.

## Time and usage

| Cumulative worker seconds, all attempts | Acquire | Interpret | Probe | Ground | Emit | Reuse |
|---|---:|---:|---:|---:|---:|---:|
| TQ9 | 35.8 | 176.0 | 95.3 | 127.4 | 47.7 | 60.1 |
| Sampled sensor | 44.0 | 217.5 | 154.8 | 123.7 | 39.9 | 62.1 |
| Parameter store | 32.7 | 271.9 | 253.2 | 82.6 | — | — |

Worker time includes repeated stages and is distinct from total workflow latency. Managed tool durations can overlap worker time and should not be added to it. The [verification index](../implementation/context-repair-verification.json) retains stage usage, attempt counts, tool counts and selected evidence hashes.

Twenty workers reported 4,795,330 total tokens, including 3,967,744 cached input tokens, with 20/20 usage coverage. Cached input is a subset of total usage. Two repairs added four assignments. Suite creation through the last workflow outcome took 1,909.0 seconds (31.8 minutes), excluding preflight and later cleanup/verification. All 99 managed operations finished. Postflight confirms unchanged installed code, evaluator, CLI, native tools and executor settings, with no remaining uncertain effects or stopping workers. The private experiment service was stopped and its process confirmed absent.

## Software and evaluator checks

| Local macOS environment | Tests | Seconds | Skips | Result |
|---|---:|---:|---:|---|
| Source, Python 3.13.12 | 441 | 84.782 | 3 archive gates | Passed |
| Installed wheel, Python 3.11.15 | 441 | 81.807 | 0 | Passed |
| Installed wheel, Python 3.13.12 | 441 | 89.967 | 0 | Passed |

Installed tests ran from extracted source archives outside the checkout. All 120 tracked runtime/resource files matched the wheel and both installations. Publication checks, installation replay and relocated standalone replay passed. Thirty native sessions comprised 12 correct-reference sessions and 18 required behavioral mutants: references passed, mutants were rejected and every saved grade independently reproduced. All three authenticated admissions passed. These checks are separate from model results.

## Interpretation and next step

The prior run completed 0/3; this separately frozen run completed 2/3 with the model and reasoning level unchanged. This supports continued work on pipeline execution and demonstrates measured self-repair in the sensor trial. It cannot isolate which combined change caused the improvement or estimate general reliability: these are single, outcome-aware reruns on familiar authored cases. Visible traces show no forbidden credential/oracle access; filesystem and direct-device isolation remain unverified. Controller ground acceptance is procedural, and these observations establish emulated rather than physical behavior.

Next, test transaction-state normalization in the store update composition and design a trusted emulator reconciliation path that permits bounded repair after verified disposal/reset. Preserve the physical-device uncertainty guard and the recorded failure. Freeze and verify any subsequent experiment separately before expanding repetitions or admitting external firmware. [The handoff](../implementation/benchmark-handoff.md) records the checkpoint; [the benchmark guide](../../bench/README.md) explains setup and execution.

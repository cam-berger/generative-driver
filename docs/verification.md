# Verification

All workflows end at acquire, interpret, probe, ground, emit, reuse. Scripted tests, installation replay, native reference qualification, model execution and physical observations retain separate labels.

## Transaction coverage and emulator recovery — 2026-10-06

The [approved plan](implementation/transaction-recovery-plan.md) and final rejection follow-up are complete. Rejection checks use evaluator-owned deterministic complete-response witnesses against captured bytes, independently of candidate receive delimiters and reject prefixes; equivalent request encodings remain valid. Parameter-store evaluation now always includes idle update, pending same-bank update, pending different-bank update, stage/commit, stage/abort and complete rejection, with independent ending-state observations. The configurator can use remaining repair budget after every unresolved effectful dispatch has a trusted completed model-failure outcome and an owned emulator is replaced with independently verified initial state. Failed command evidence remains failed; recovery never replays the uncertain write.

The three emulated cases use case/evaluator version 5. Thirty-two native reference/mutant sessions qualify the measured version 5 working-tree implementation: 12 correct-reference and 20 mutant sessions. All saved grades reproduce and all three authenticated admissions pass; installed admissions pass in both Python environments. Store mutant failure sets match independent predictions frozen before measurement. Firmware sources and loaded flash binaries are unchanged. The new standalone-commit short-frame mutant fails exactly five predeclared rejection checks while positive commit, composed update and state checks remain correct. Prior version 4 attempts/evidence are preserved separately.

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 | 461 | 117.210 s | Passed |
| Installed Python 3.11.15 | 461 | 112.210 s | Passed |
| Installed Python 3.13.12 | 461 | 120.620 s | Passed |

All three suites enable both archive gates and have zero skips. Installed suites run from the extracted source archive outside the checkout with installed imports and evaluator identity verified, and checkout/credential/native overrides removed. Both installations pass explicit setup smoke, relocated package replay and Codex/Goose setup asset checks. The tested archives contain the final runtime, resources and tests; final measurement/reporting prose follows archive construction. The [verification index](implementation/transaction-recovery-verification.json) records hashes, exact later documentation differences, preserved failures and limits.

Real native recovery confirms disposal of the old owned Renode process, a distinct live replacement/binding, independently matching startup readings, encrypted proof linkage, retained failed-write evidence and cleanup without replay. This adapter lifecycle proof is paired with the public-controller deterministic regression for durable revision, leases, bounded repair, six accepted gates, fresh reuse and independent regrading. It is not a native controller trial or a model result. Evaluator-only reference reply robustness diagnostics retain inherited unqualified acknowledgment-only/empty-output operations; native functional qualification and admission passed.

Windows CI has not run for these commits. This release qualification used no model trial or physical operation; the separately frozen model trial follows below. Live Goose/Codex UI behavior and worker filesystem isolation remain unverified. Prior feasibility experiments retain their original outcomes and coverage.

## Version 5 model trial — 2026-10-06

The [three-family trial](research/2026-10-06-transaction-recovery-results.md) at `272c1b1`, using the exact qualified installation with `gpt-6.1-sol` and medium reasoning, completes **2/3** in 35.2 minutes. TQ9 passes all six stages and 9/9 final checks after one repair and one automatic emulator recovery. Sensor passes all six stages and 85/85 final checks after one repair. Store corrects a capability mapping, then passes 364/366 diagnostic checks; its second revised probe stops on an intentional idle-commit rejection with exhausted repair budget and unresolved effect. Store final behavior was not reached. Its pending-update and ending-state checks passed in the completed diagnostic evaluation.

Recorded and sealed regrades agree. All 23 assignments report usage, all 127 managed operations finish, and postflight confirms unchanged implementation, evaluator, tools, settings, artifacts and budgets. No operator inputs, rescue, extra trials or deadline extensions occurred. The service is stopped and final emulator processes are absent; store uncertainty is retained. Independent result reviews support the negative primary result. The [trial index](implementation/transaction-recovery-trial-verification.json) records stage metrics, hashes and limits. The 3/3 condition for pushing a PR was not met; the next investigation is the expected-negative-probe/reconciliation contract, without claiming the ungraded final candidate is incorrect.

## Version 4 snapshot — superseded after rejection review

The [frozen version 4 index](implementation/transaction-recovery-v4-verification.json) preserves the checks measured at `f6c055d`. Final integration review then reproduced candidate first-line receive framing earning rejection credit. That snapshot does not qualify the independent complete-response repair; version 5 adds the private witness and short-frame mutant.

Version 4 had 31 native sessions (12 correct-reference, 19 mutant), all grades reproduced under its frozen implementation, and three source plus six installed admissions passed. Its software results remain:

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 | 457 | 117.526 s | Passed |
| Installed Python 3.11.15 | 457 | 112.503 s | Passed |
| Installed Python 3.13.12 | 457 | 119.643 s | Passed |

All suites had zero archive skips. One store attempt stopped after a diagnostic session on a Renode monitor-port collision; the partial record/log and unchanged successful retry remain preserved. Two native proof-harness preflight mistakes were also retained. Native adapter lifecycle and deterministic controller durability were separate proofs. Firmware sources and flash binaries were unchanged; Windows, live clients, physical devices and a new model trial were unrun. These observations retain their original scope and review gap.

## Pipeline repair — 2026-10-05

The [pipeline report](research/2026-10-05-pipeline-repair-results.md) preserves earlier same-model feasibility experiments and their [verification index](implementation/pipeline-repair-verification.json). Subsequent corrections add candidate-only capability validation, mapping feedback, pre-transport refusal classification and atomic cleanup status. Source tests pass 432 checks with three archive skips; installed Python 3.11.15 and 3.13.12 each pass 432 with zero skips. Final archives and installation replay pass. Thirty native reference/mutant sessions reproduce all saved grades and three authenticated admissions qualify the refreshed evaluator.

The separately frozen [capability repair run](research/2026-10-05-capability-repair-results.md) completes 0/3 fresh-reuse workflows at `gpt-6.1-sol`, medium reasoning. TQ9 passes all diagnostic checks but stops at grounding; sensor and store exhaust repairs. Recorded and independent regrades agree, with final behavior not reached. The [new index](implementation/capability-repair-verification.json) records measurements and log identities. Model outcomes retain their frozen revisions; passing software tests do not establish model success.

The separately frozen [context repair experiment](research/2026-10-05-context-repair-results.md) at `21359b4` completes **2/3** with the same model and reasoning level. TQ9 passes all six stages and 9/9 final checks without repair; sensor consumes measured repair feedback and passes all six stages plus 85/85 final checks after one repair. Store repairs response framing after a ground-stage request, then blocks on an update during a pending transaction; final behavior is not reached. Independent sealed regrades agree. One cleanup-only response reconciles the disposed store emulator without resuming the run. Runtime/tool identities remain unchanged, all 99 managed operations finish, and the private service is stopped.

Implementation `556d5e0` corrects repair instructions/context, ground evidence/routing and invocation-failure attribution. Scripted regressions cover measured decoder repair, ground-to-interpret revision and terminal final binding failure through external workers and an emitted package. Source verification passes 441 tests in 84.782 seconds with three archive-only skips. Installed Python 3.11.15 and 3.13.12 pass 441 tests in 81.807 and 89.967 seconds respectively, with zero skips. Thirty matching native sessions pass and all saved grades reproduce; three authenticated admissions pass with unchanged authored inputs. Publication checks, installation replay and relocated standalone replay pass. Independent source/wheel/install correspondence and visible candidate-boundary reviews are clear. The [context verification index](implementation/context-repair-verification.json) retains measurements and log identities. These observations verify an outcome-aware engineering rerun on three familiar emulated families, not model ranking, physical performance or general reliability.

The user's separately requested [unchanged store repeat](research/2026-10-05-store-repeat-results.md) completes 1/1, with all six stages, fresh reuse and 696/696 final checks, zero repairs and no intervention. Independent regrading agrees; existing test/qualification evidence was reused after identity checks, and the private service is stopped. The original three-family result remains 2/3. The repeat's visible probes omit the previously failing update-while-pending sequence and its candidate retains that state assumption, so completion does not close the known coverage gap. The [repeat index](implementation/store-repeat-verification.json) records the unchanged setup and observed result.

## PR #1 follow-up — 2026-10-03

The [review](research/2026-10-03-pr1-review.md) found an offline historical-scoring defect and an existing Windows checkout defect. The follow-up rejects incompatible historical evidence and preserves the bytes hashed by benchmark manifests and evaluator identities.

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 (final) | 420 | 66.792 s | Passed |
| Installed wheel, Python 3.11.15 | 420 | 62.015 s | Passed |
| Installed wheel, Python 3.13.14 | 420 | 68.856 s | Passed |

The final source run includes the native path assertion correction. Installed runs preceded that test-only correction; all 125 final wheel entries are byte-identical to the installed-tested wheel. The source archive audit resolved all 72 local Markdown links. All three runs enabled wheel/source-archive gates and had zero skips, failures or errors. Installed suites ran from the extracted source archive outside the checkout, with installed imports verified and checkout/credential/native environment overrides removed. Both installations passed setup replay, relocated standalone replay and all three authenticated family admissions. Dependencies were mcp 2.2.0 and cryptography 50.0.1.

Fresh native qualification executed 12 correct-reference and 18 mutant sessions across the three families, plus two legacy sessions. All 32 saved grades reproduced, current authenticated admissions passed and stale qualifications were rejected. The evaluator identity is `612b1a83d061e848e5596917b6c0776fbebfc77302deac998bfef6039d16ea33`. One earlier TQ9 attempt stopped on a Renode monitor-port collision after eight sessions; its complete unchanged retry passed, with the original partial record and startup log retained. These observations qualify the evaluator, not a model's ability to recover a driver.

The [follow-up index](implementation/pr1-followup-verification.json) records counts, log hashes, input/evidence identities and preserved failures. PR #1's earlier 417-test qualification remains in its [historical index](implementation/benchmark-final-verification.json). Raw native evidence, passwords, machine paths and exact local archives stay evaluator-owned. The reviewed growth roadmap and source review are now public documentation.

## Reproduce software checks

Build from a clean revision so unrelated untracked files cannot enter the source archive:

```sh
python -m build
```

Set `GD_TEST_WHEEL` and `GD_TEST_SDIST` to the absolute paths of those archives, then run `python -m unittest discover -s tests -v`. In each clean Python 3.11/3.13 verification environment install the wheel and the qualified evaluator dependencies (`mcp==2.2.0`, `cryptography==50.0.1`); these versions describe the measured verification environment, not new project dependency pins. Extract the source archive to a separate directory, change to it, remove checkout `PYTHONPATH` and evaluator credential/native variables, then run the same test command with that environment's Python. Keep the archive gate variables set to the same built bytes. Confirm `generative_driver.__file__` and `interface_runtime.__file__` resolve to the new installation.

```sh
python -m generative_driver benchmark cases
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m generative_driver setup --host codex --output "codex setup"
python -m generative_driver setup --host goose --output "goose setup"
```

Use new output directories. From `smoke output/relocated package`, run `python -m driver replay`. These commands perform no model inference or physical operations. [Groundtruth setup](../bench/groundtruth/README.md) describes password-holder recovery, authenticated native evidence and explicit-sidecar offline score commands; [the benchmark guide](../bench/README.md) describes final acceptance and compatible comparisons.

## CI and limits

The initial fix commit's [CI run](https://github.com/cam-berger/generative-driver/actions/runs/37155861839) passed macOS Python 3.11 and 3.13 and eliminated Windows truth-hash failures. Both Windows jobs then exposed one existing test that assumed POSIX path separators. The assertion now checks native path containment; the focused local suite passes. A later Windows 3.13 job exposed a gateway test that raced the transition to running; the test now waits for both assignment and public running state. A delayed-transition reproduction failed before and passed after correction, and all ten configurator tests pass. These later changes affect tests only. The follow-up PR checks provide the final platform results. Initial failed jobs and temporary GitHub log-access failures are retained in the review record.

PR #1 also recorded an intermittent macOS suite reconnect failure. The unchanged 11-test interface suite and 12 consecutive repetitions of the affected test passed locally. No scheduler fix is claimed. Earlier failures and retries remain part of the evidence.

The nine-trial model study has not run. These checks do not establish physical-device performance, native Windows Renode/Ghidra, Windows 11 workstation UI setup or live Goose behavior. Worker filesystem isolation remains unverified. Earlier observations retain their original revisions and meaning.

# Verification

All workflows end at acquire, interpret, probe, ground, emit, reuse. Scripted tests, installation replay, native reference qualification, model execution and physical observations retain separate labels.

## Consolidated firmware pipeline

[PR #16](https://github.com/cam-berger/generative-driver/pull/16) consolidates PRs #6–15. Its regressions exercise real USB/FTDI acquisition through the configurator, retention of observed evidence, the shared sealed firmware-analysis kit, tamper rejection during repair, and the intersection of independent equivalence with successful fresh reuse. The native package matrix enables both wheel and source-archive gates.

The real Ghidra lane [passed on macOS 26 and Windows Server 2025](https://github.com/cam-berger/generative-driver/actions/runs/38845379254) for implementation `e26d865`. This qualifies the lane's Cortex-M3 and Xtensa fixtures and process cleanup on those CI hosts. The earlier unrun status below describes the lane before this consolidated run. It establishes neither Windows Renode behavior nor model performance.

The [Codex acceptance lane](codex-acceptance.md) regrades sealed evidence from a completed native model suite and requires equivalent drivers plus successful fresh reuse in every planned trial. It remains unrun for this integration: fresh evaluator calibration and operator-owned passwords are prerequisites. Current scripted regressions and replay do not supply that model evidence.

## Ghidra navigation export — 2026-10-15

`ExportDecomp.java` also writes `references.tsv`, `strings.tsv` and `switches.tsv`, each bounded at 16 MiB unless `navigation_max_bytes` sets another bound, and `navigation.json`, which records each table's rows written, rows found and truncation. `ghidra_run` verifies them and lists truncated tables in `exports.truncated`. `functions.tsv`, `function_*.c` and `disassembly.txt` are written as before. The tables are static facts; they do not show that code runs or is reachable.

- **Scripted contract tests** (`tests/test_ghidra_exports.py`, part of the 789-test unit suite that passed from source with 3 archive-gate skips): a Python stand-in for `analyzeHeadless` writes the exports. They establish `ghidra_run`'s verification, truncation reporting, hashing and bound handling, and nothing about Ghidra.
- **Native observations** on macOS with Ghidra 12.1.3 and OpenJDK 21.0.12.1, on a developer installation whose macOS arm64 `decompile` and `sleigh` natives were built locally on 2026-09-10 from the release's sources (the release ships none; see the lane below), using self-built fixtures only (no benchmark source, answer key, case firmware or banner-node firmware): the explicit gate `tests/native_ghidra_exports.py` passed its five tests on a hand-assembled Cortex-M3 raw image and on an Xtensa ELF compiled from its own source with ESP GCC 14.2.0, which Ghidra's ELF loader imported as `Xtensa:LE:32:default`. For the Cortex-M3 image the previous and the new exporter wrote byte-identical `functions.tsv`, `function_*.c` and `disassembly.txt`. A larger self-written Xtensa program, with a switch with holes, UTF-16 and escaped strings and a struct table of command names, also exported and verified. Windows Ghidra execution remains unqualified.
- **Calibration**: `ghidra_run.py` and `ExportDecomp.java` belong to `calibration_identity()`, whose digest changed from `25e824a0…` to `7228dcf7…`; the grading closure is unchanged. No calibration, model trial or device operation ran, and the fresh native calibration required below still precedes the nine-trial end test.

## Real Ghidra lane — 2026-10-15

The Real Ghidra workflow, `.github/workflows/real-ghidra.yml`, qualifies headless Ghidra by running it. On pull requests that touch the Ghidra tool, its Java scripts or the gates, and on manual dispatch, it installs the package on GitHub-hosted macOS 26 arm64 and Windows Server 2025 x64 runners and runs Ghidra 12.1.3 with Temurin JDK 21.0.12.1, matching the Ghidra and Java versions of the native reference setup, and Espressif's Xtensa GCC 14.2.0 (release `esp-14.2.0_20260121`). Each download must match the SHA-256 its publisher lists, checked again after every cache restore. Ghidra 12.1.3 ships native binaries only for Linux and Windows x86-64, so on macOS the job builds the decompiler, the SLEIGH compiler, both GNU demanglers and lzfse from the release's own sources with Ghidra's documented `gradle buildNatives`: offline, with Gradle 9.6.1 (the version of the release's wrapper, pinned by SHA-256), the pinned JDK and the runner's Xcode clang. It caches them keyed by the Ghidra, JDK and Gradle hashes. `natives.json` in the evidence records their origin, the run that built them, the compiler and their hashes, and the gate's `host.json` lists every decompiler binary present.

Both images come from `tests/native_ghidra_exports.py`: a hand-assembled Cortex-M3 raw image built in Python from the listing in that file, and an Xtensa ELF that the Espressif GCC compiles from the C source in that file. Neither derives from a benchmark source, answer key or device firmware. The lane runs all of that module's export tests and the gate `tests/native_ghidra.py`, which calls `python -m generative_driver tool ghidra_run --arguments FILE` on the Cortex-M3 image. The gate passes only when the result is `ok`, the log reports import, analysis, post-script and save success with the raw-binary loader, the Cortex language and the requested base address, and the reset handler and the five functions it calls are each in `functions.tsv` with a completed decompilation and a disassembled entry. It also requires the image unchanged, Ghidra's default per-user settings, cache and temporary directories untouched, and no process left that names the run directory. A second run holds the analyzer in a pre-script until `ghidra_run`'s 90-second timeout and then requires the process tree gone. Each host uploads its arguments, results, logs and exports as the `real-ghidra-<runner>` artifact.

A pass establishes, on each host, a real Ghidra import, analysis and export of one small Cortex-M3 raw image and of one Xtensa ELF through Ghidra's ELF loader, the Windows batch launcher, timeout termination of a running Ghidra JVM, and Ghidra state kept in the run workspace; on macOS it does so with natives built in CI from the release's sources. That workspace placement is not a sandbox. A pass does not establish a release-shipped macOS decompiler (there is none), model behavior, emulator or Renode runs, device operation, other architectures or loaders, larger firmware, or export correctness beyond the checked functions and tables.

**Status: unrun.** The workflow has not run on GitHub, so Windows Ghidra execution and the CI-built macOS natives remain unobserved. The lane's earlier local passes ran on a developer installation whose macOS natives had been built locally on 2026-09-10; this check supersedes them. A tree rebuilt from that installation with only the 5,218 files the release zip lists, each matching the size and CRC-32 that the zip records, failed the gate without macOS natives: `ExportDecomp.java` ended in a script error and wrote no export. After `gradle --offline buildNatives` built the five natives in that tree (Homebrew Gradle 9.7.1 from an empty Gradle home; the lane's pinned 9.6.1 is unobserved locally), the lane's seven tests passed in 107 s with Homebrew OpenJDK 21.0.12.1 and Espressif GCC `esp-14.2.0_20260121`, from the source checkout rather than an installed package. In the unit suite, `tests/test_ghidra_lane.py` checks the gate's verdict without Ghidra. That suite passed 793 tests from source with 3 archive-gate skips; a run before the final documentation edits had one temporary-directory cleanup error in `test_suite_service`, which passed three separate reruns of its module.

## Assistive protocol — 2026-10-08

New runs follow `assistive/1` ([design amendment](design.md#assistive-protocol-amendment--2026-10-07)): stages hand off with notes, only safety, integrity, operator and host faults stop a run, device differences drive repair and each run records one equivalence verdict. For benchmarks a bench harness holds the evaluator password, owns the emulator and grades the frozen driver once after the run; the configurator holds no password, recipe or truth. Workers see instrument readings of the emulated device as device evidence. Workers are not read-isolated until a real sandbox exists; separation from the answer keys is password-based.

Its results are a new series. The results below, the 2/3 trial included, keep their gated meaning and are not compared with it. This protocol changes the evaluator implementation, so the evaluator identities and calibrations recorded below describe their own revisions only; the version 5 calibration was already stale at `52eb018`. A fresh native calibration by the password holder must precede the nine-trial end test, which also needs explicit authorization for an actual model run. Sealed evidence of an earlier revision regrades only from a preserved checkout of that revision. So far only scripted contract tests exercise this protocol: no model trial, native calibration, Renode session or physical operation has run under it.

## Replay executor — 2026-10-15

`benchmark run --executor replay` replays a saved candidate through all six stages without a language model ([replay a saved candidate](../bench/README.md#replay-a-saved-candidate)). Only scripted contract tests exercise it so far, in `tests/test_replay_executor.py`. The test setup is a synthetic TCP peer for the device, a toy sealed truth, a configurator daemon in the test process, the real MCP stage gateway and a detached bench harness. In it, the tq9 fixture candidate completes every stage through the gateway. The harness then grades it equivalent once after the run and seals the evidence. The report says `replay-emulation`, `score` regrades it, and `compare` refuses it beside a model run. A scripted replay suite of two tq9-v2 children, driven by a detached suite harness, completes with both graded equivalent; its report says `replay-emulation`, and `suite compare` refuses it beside a model suite. No native calibration, Renode session or native replay trial has run, and nothing ran on Windows. Replay results never count as model performance.

## PR #5 pipeline and dashboard integration — 2026-10-06

The user authorized combining pipeline branch `359f5be` with dashboard branch `ca7599b` in [PR #5](https://github.com/cam-berger/generative-driver/pull/5), including all previous results despite the unmet original 3/3 publication condition. The original model trial remains **2/3**: TQ9 and sensor completed fresh reuse and final grading; store remains blocked with unresolved effect and final behavior not reached. No new model or native reference trial ran for this integration.

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 | 471 | 105.311 s | Passed |
| Installed wheel, Python 3.11.15 | 471 | 114.168 s | Passed |
| Installed wheel, Python 3.13.12 | 471 | 122.858 s | Passed |

All three suites enabled both archive gates and had zero skips. Clean installed tests ran outside the checkout from the extracted source archive; installed imports were verified. Browser contracts passed from source and archive, and both installations passed setup replay and relocated-driver replay. The pinned installed evaluator identity remains `7330e0a99136922d04165e094a3abb465d8dc0fc6c497bebf71c7270e2904c04`; all three read-only authenticated version 5 case admissions passed in the fresh installed Python 3.11 environment. Public/private trial fields and all ten indexed evidence hashes matched. The [integration index](implementation/pr5-integration-verification.json) separates this combined software qualification from historical model/native measurements and records dependency versions. [Current PR checks](https://github.com/cam-berger/generative-driver/pull/5/checks) give the platform status; earlier Windows observations retain their original scope.

## Dashboard — 2026-10-06

The [dashboard verification](dashboard-verification.md) preserves the original 430-test observer qualification and one successful observed TQ9 run. PR #5 now also includes the previously qualified pipeline fixes and the three-family results below. Historical measurements retain their original revisions; integration checks are recorded separately.
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

Recorded and sealed regrades agree. All 23 assignments report usage, all 127 managed operations finish, and postflight confirms unchanged implementation, evaluator, tools, settings, artifacts and budgets. No operator inputs, rescue, extra trials or deadline extensions occurred. The service is stopped and final emulator processes are absent; store uncertainty is retained. Independent result reviews support the negative primary result. The [trial index](implementation/transaction-recovery-trial-verification.json) records stage metrics, hashes and limits. The original 3/3 publication condition was not met. The user subsequently authorized including the pipeline fixes and all results in [PR #5](https://github.com/cam-berger/generative-driver/pull/5), preserving the 2/3 outcome. The next investigation is the expected-negative-probe/reconciliation contract, without claiming the ungraded final candidate is incorrect.

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

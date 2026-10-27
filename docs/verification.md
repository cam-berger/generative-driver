# Verification

Separate scripted tests, replay, native reference qualification, model execution and physical observations. All current workflows end at acquire → interpret → probe → ground → emit → reuse. Results below describe dated snapshots, not every later checkout.

## Whole-benchmark model study — 2026-10-27

At implementation `9df3887`, actual `gpt-6.1-sol` workers produced equivalent drivers with successful fresh reuse in **7/9 core trials**, **3/3 runnable Zephyr trials** and **3/3 supplemental legacy TQ9 trials**. All 15 missions succeeded; two store drivers failed five complete-rejection checks each. Every sealed final regraded identically; two independent audits verified counts, identities, usage and public mission observations. Four automatic repairs, no manual trial repairs or budget extensions, and 98/98 usage attempts were recorded.

The upstream catalog retains 21 selected slots: three passed and 18 blocked across six lineages, so demonstrated coverage is one of seven lineages. The native-ELF pilot, scripted smoke and physical BME280 are separate. This is a local macOS development-case result, not physical timing, Windows performance or a general firmware success rate. See the [results](research/2026-10-27-benchmark-performance-results.md), [frozen plan](research/2026-10-27-benchmark-performance-plan.md) and [verification index](implementation/whole-benchmark-performance-verification.json).

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

The [plan](implementation/transaction-recovery-plan.md) and rejection follow-up are complete. Version 5 adds independent complete-response witnesses and six mandatory store scenarios with ending-state checks. Candidate framing cannot establish rejection; equivalent requests remain valid. Checked owned-emulator replacement can consume remaining repair budget after trusted completed model-failure evidence for every unresolved effectful dispatch. Failed writes remain recorded and are never replayed.

| Local macOS environment | Tests | Seconds |
|---|---:|---:|
| Source Python 3.13.12 | 461 | 117.210 |
| Installed Python 3.11.15 | 461 | 112.210 |
| Installed Python 3.13.12 | 461 | 120.620 |

All passed with both archive gates and zero skips. Installed tests ran outside checkout with imports/identity checked and credential/native overrides removed. Both installations passed smoke, relocated replay and Codex/Goose setup checks. Final reporting prose followed archive construction; the [index](implementation/transaction-recovery-verification.json) records exact hashes and later documentation differences.

All 32 native grades reproduced: 12 reference and 20 mutant sessions. Three source and six installed admissions passed. Firmware/source bytes stayed unchanged. The short-frame commit mutant failed exactly five independently predeclared rejection checks while positive commit/update/state checks passed.

Native recovery proved old-process disposal, a distinct replacement/binding, independent initial readings, encrypted proof linkage, retained failure evidence and cleanup without replay. A separate deterministic public-controller regression proved durable repair through six gates and regrading. These were separate proofs, not a native controller model trial. Acknowledgment-only/empty-output reply robustness remains unqualified. Windows, live UI behavior, physical devices and worker filesystem isolation were not qualified.

## Version 5 model trial — 2026-10-06

At `272c1b1`, `gpt-6.1-sol` with medium reasoning completed 2/3 in 35.2 minutes using the qualified installation. TQ9 passed 9/9 final checks after one repair/recovery; sensor passed 85/85 after one repair. Store reached 364/366 diagnostics, then its second revised probe stopped on intentional idle-commit rejection with no repair budget. Pending-update/state checks passed; the final candidate was never graded.

Recorded and sealed regrades agree. All 23 assignments reported usage; all 127 managed operations finished. Settings/identities stayed unchanged; no interventions, extra trials or extensions occurred. Service/emulators stopped; store uncertainty remains. The original 3/3 publication condition was unmet; the user later authorized PR #5 with the 2/3 result preserved. See [results](research/2026-10-06-transaction-recovery-results.md) and [trial index](implementation/transaction-recovery-trial-verification.json). Next investigation: the expected-negative-probe contract.

## Version 4 snapshot — superseded after rejection review

At `f6c055d`, candidate first-line framing could earn rejection credit. Version 5 closes that gap; [version 4 evidence](implementation/transaction-recovery-v4-verification.json) retains its original scope.

| Local macOS environment | Tests | Seconds |
|---|---:|---:|
| Source Python 3.13.12 | 457 | 117.526 |
| Installed Python 3.11.15 | 457 | 112.503 |
| Installed Python 3.13.12 | 457 | 119.643 |

All passed with zero archive skips. All 31 native grades (12 reference, 19 mutant) reproduced; three source and six installed admissions passed. Preserve the monitor-port collision, unchanged retry and two proof-harness preflight mistakes. Firmware stayed unchanged. Adapter lifecycle and controller durability were separate proofs; Windows, clients, physical devices and new model trials were unrun.

## Pipeline repair — 2026-10-05

These outcome-aware development reruns used three familiar emulated families and `gpt-6.1-sol`, medium. They do not establish ranking, causality or general reliability.

| Frozen run | Fresh-reuse result | Main observation | Evidence |
|---|---|---|---|
| [Pipeline repair](research/2026-10-05-pipeline-repair-results.md) | 0/3 | Tools used; mappings and refusal classification blocked progress | [Index](implementation/pipeline-repair-verification.json) |
| [Capability repair](research/2026-10-05-capability-repair-results.md) | 0/3 | TQ9 diagnostics passed but grounding blocked; sensor/store exhausted repairs | [Index](implementation/capability-repair-verification.json) |
| [Context repair](research/2026-10-05-context-repair-results.md), `21359b4` | 2/3 | TQ9 9/9 final; sensor 85/85; store pending update blocked before final | [Index](implementation/context-repair-verification.json) |
| [Unchanged store repeat](research/2026-10-05-store-repeat-results.md) | Separate 1/1 | 696/696 final, zero repairs; visible probes omitted pending update | [Index](implementation/store-repeat-verification.json) |

Capability qualification passed 432 tests: source had three archive skips; installed Python 3.11/3.13 had none. Thirty native grades reproduced and three admissions passed.

Context implementation `556d5e0` repaired instructions/feedback, ground routing and invocation-fault attribution. Source passed 441 tests in 84.782 s with three archive skips; installed 3.11/3.13 passed 441 in 81.807/89.967 s with none. Thirty native sessions, three admissions, distribution/replay and source-to-install correspondence passed with unchanged authored inputs. All 99 managed operations finished; one cleanup-only response reconciled the disposed store emulator without resuming it. Services stopped. The unchanged store repeat reused identity-matched qualification; it does not change the prior 2/3 result or close version 3's pending-update coverage gap.

## PR #1 follow-up — 2026-10-03

The [review](research/2026-10-03-pr1-review.md) corrected incompatible historical scoring and Windows checkout hash changes.

| Local macOS environment | Tests | Seconds |
|---|---:|---:|
| Final source Python 3.13.12 | 420 | 66.792 |
| Installed Python 3.11.15 | 420 | 62.015 |
| Installed Python 3.13.14 | 420 | 68.856 |

All passed with zero archive skips. Installed runs preceded a test-only native-path fix; all 125 final wheel entries matched tested bytes. The source archive resolved 72 local Markdown links. Both installs passed replay and three admissions. Measured dependencies: mcp 2.2.0, cryptography 50.0.1.

All 32 native grades reproduced: 12 reference, 18 mutant, two legacy. Stale calibration was rejected. Preserve the TQ9 monitor collision after eight sessions and unchanged successful retry. The [follow-up index](implementation/pr1-followup-verification.json) pins evaluator identity and logs; the original 417-test result stays in the [historical index](implementation/benchmark-final-verification.json). Private archives, paths, passwords and raw evidence remain evaluator-owned.

## Reproduce software checks

Build from a clean revision to exclude unrelated untracked files:

```sh
python -m build
```

Set `GD_TEST_WHEEL` and `GD_TEST_SDIST` to absolute archive paths; run `python -m unittest discover -s tests -v`. For installed checks, install the wheel in clean Python 3.11/3.13 environments, extract the source archive elsewhere and run tests there. Remove checkout `PYTHONPATH` and credential/native overrides; retain archive variables. Confirm `generative_driver.__file__` and `interface_runtime.__file__` resolve to the installation. Historical dependency versions above describe measured environments, not new project pins.

```sh
python -m generative_driver benchmark cases
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m generative_driver setup --host codex --output "codex setup"
python -m generative_driver setup --host goose --output "goose setup"
```

Use new directories; run `python -m driver replay` inside `smoke output/relocated package`. These checks use no inference/physical devices. See [groundtruth qualification/scoring](../bench/groundtruth/README.md) and [benchmark acceptance](../bench/README.md).

## CI and limits

The [follow-up CI](https://github.com/cam-berger/generative-driver/actions/runs/37155861839) passed macOS and removed Windows truth-hash failures. Later Windows failures exposed POSIX-only test paths and a test racing the running-state transition; focused reproductions passed after test corrections. Production gateway behavior was unchanged. Final platform outcomes belong to the follow-up PR checks; failed jobs and log-access failures remain recorded.

An intermittent macOS suite reconnect failure was not reproduced: the unchanged 11-test suite and 12 repetitions passed locally. No scheduler fix is claimed.

The nine-trial local model study is recorded above for `9df3887` (7/9 joint successes). Physical performance, native Windows Renode model runs, Windows 11 UI setup, live Goose and worker filesystem isolation remain unverified. Historical results retain their measured revisions.

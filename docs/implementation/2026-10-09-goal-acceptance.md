# Goal acceptance: assistive configurator branch

## Status after the finishing pass (2026-10-09)

The most important fixes below are done; the assessment that follows is kept as written at `0e8e0fe`.

- **Kit (goal 1).**
  - MCP and CLI stdio tests now test the package under test.
  - A missing pyserial is a host fault, never a verdict about the driver.
  - Every start resolves an omitted runtime to the one configured. A start, a resumed run or a benchmark naming an unconfigured runtime is refused or stops for the operator, instead of launching whatever has that name on `PATH`.
  - A client refuses to drive a configurator running other code, and the configurator refuses to start under another installation's record. Worker gateways are exempt, so a host change never cuts a stage off from its tools.
- **Benchmark (goal 2).**
  - A candidate driver whose call fails on the evaluator's healthy device is graded against it. Healthy means the emulator runs, answers its monitor and accepts a connection on the driver's channel. Before this pass such a trial was left unscored and blocked its suite.
  - A package process that hangs past 300 s or prints nothing, a dead device, and every host fault during calibration stay unscored.
  - The evaluator identity pins only the grading closure, so a kit change no longer voids a calibration.
  - `benchmark truth calibrate` runs the family calibrations and exits 1 when one fails.
- **Verified.**
  - The source suite and the CI-equivalent run pass: an installed wheel in a clean venv whose path has spaces, without the hardware extras, outside the checkout, with the archive gates and the setup smoke.
  - An independent review of the pass was addressed.
- **Replay executor (G2-4), 2026-10-15.** `benchmark run --executor replay` replays a saved candidate through every stage without a model, and `benchmark suite start --executor replay` replays one per case; scripted contract tests only.
- **Still open, needing the operator.**
  - Recalibrate the three emulated families natively with the evaluator password.
  - Run one replay trial per emulated family from saved candidates the operator chooses.
  - Authorize the nine-trial end test.
- **Not done (lower priority).**
  - Reset-on-open as a real stop (G1-4) and Goose adapter parity (G1-5).
  - Report and comparison improvements (G2-5 to G2-15).
  - The simplification candidates below.


Branch `claude/goose-new-device-trial-5c6f78` at 0e8e0fe. It is compared against `d1121ad`, the branch point before the refactor, and `main`, the previous release line. The objective is `docs/superpowers/specs/2026-10-07-assistive-configurator.md`. This report combines four assessments: deployment, modularity, benchmark robustness and benchmark usefulness.

**Scope of the evidence.** All results come from scripted runs, a silent stub runtime, local TCP peers, the scripted AssistiveFixture and reading the code. No real model ran. No Renode session or native grading ran. No physical device was touched, and nothing ran on Windows. Findings marked "code read" were not reproduced.

## Bottom line

Both goals are **partly met**. The branch is clearly better than the last version on both goals, and it installs and runs from a wheel for both Goose and Codex. It is not ready to merge: the test run from the installed wheel fails. It is also not ready for the 9-trial end test. All three v2 cases will be refused at admission, and the final grader can record a driver failure as an unscored host fault.

### Test status

| Environment | Result |
|---|---|
| Worktree source with the main `.venv` and `PYTHONPATH` (the documented recipe) | 764 OK, 3 skipped |
| Installed wheel outside the checkout, using the main venv's site-packages (pyserial present) | 764 run, 1 error |
| Installed wheel in a clean venv with no extras (the same setup as CI's "Test outside the checkout" step) | 764 run, 1 failure, 1 error, 3 skipped |

The documented recipe hides both problems. The MCP stdio tests start their server with only `env={'GENERATIVE_DRIVER_HOME': ...}`. That child process loses `PYTHONPATH`, so it imports the main checkout's editable install (another checkout's `src`, with local edits) instead of the worktree. Under that recipe, every stdio test in `tests/test_mcp.py` tests main's server, and so do the MCP subprocesses in `tests/test_cli.py`.

Sizes used below: S is under about 50 lines or a few hours. M is about 50 to 200 lines, or about a day. L is several days or needs operator tooling.

## Goal 1: the toolchain is better, deployable, and modular across Goose and Codex

**Verdict: partly.** It is better than the last version and installs from a wheel for both front-ends. Four problems remain:
- the CI-equivalent test run fails;
- the client never checks the daemon's version;
- a Goose user can silently launch an unrelated `codex`;
- no test covers the Goose adapter's device path.

### Better than the last version

- **Generic device runs reach a recorded verdict.** With the same scripted runtime that writes no report, HEAD completes all six stages and records `equivalence {equivalent: false, basis: self-evidence}` with protocol `assistive/1`. `main` and `d1121ad` fail at acquire as a host fault and record no verdict.
- **Generic ground runs a worker.** It is no longer hard-blocked in `_prepare`.
- **Every stage hands off hashed notes.** `driver_status` returns notes for each stage: open questions, failed checks and the outcome. The report shows "Equivalence (self-evidence)" and the protocol. A missing runtime stops a generic run with an `operator` stop.
- **Codex and Goose share one stage contract.** They use the same `StageRequest`, `REPORT_SCHEMA` and `STAGE_BOUNDARY`, one `execute()`, one worker gateway and one prompt builder. The new `cause` field lets `_outcome` classify stops the same way for every runtime.
- **Packaging works.**
  - The sdist-to-wheel build works offline, and the wheel installs into a venv whose path contains spaces.
  - From the installed package, doctor, setup, service start/status/stop, the smoke benchmark, report and the dashboard all work.
  - The generated Goose and Codex assets name only the installed interpreter and home, with no checkout paths.
  - Codex CLI 0.160.0 installs the generated plugin and sees the MCP server. `goose recipe validate` (1.53.0) passes both recipes.
  - With `PATH=/usr/bin:/bin`, the MCP server initializes over stdio in 0.26 s and lists 15 tools.
- **The skill is consistent and accurate.** SKILL.md is byte-identical in all five copies and matches the recipe instructions. It describes the notes handoff, the single verdict and the password-based separation accurately.
- **Core imports are light and portable.** Core modules import without `mcp` or `cryptography`. Windows-specific code paths are guarded: AF_PIPE, the msvcrt lock, DETACHED_PROCESS and the job object. No test or run wrote into the installed package.

### Fixes, ranked

**P0: before merge**

G1-1. **Make the installed-wheel test run pass, and stop the recipe from hiding failures.** [S-M] Files: `tests/test_mcp.py`, `tests/test_cli.py`, `src/interface_runtime/transports.py`
- `test_benchmark_home_must_match_the_mcp_configurator` expects bme280 to start without an evaluator password file. Since afddff1, that start is refused. Change the test to assert the refusal, or give it a fixture password file.
- Pass the test interpreter's import path into the env of every `StdioServerParameters`, for example by putting the parent of `generative_driver.__file__` on `PYTHONPATH`. Worktree runs then test the worktree code.
- Without pyserial, a host problem is graded against the driver. `_dependency` (transports.py:179-183) raises `TransportError('dependency')` with no fault, so `_binding_state` sees no host fault. The generic verdict then becomes `equivalent: false` (UNLISTENED) instead of unscored. Raise it with `Fault.HOST`. A monkeypatch confirmed that the verdict then becomes None (UNHEARD). Also make the `unfound` subcase in `FinalVerdictTests` pass whether or not pyserial is installed.
- Validate the branch with the installed-wheel run, and run the Windows and macOS CI jobs. Windows CI has not run on this branch (docs/verification.md:42).

**P1: before users rely on it**

G1-2. **Do not launch a `codex` that was never configured.** [S] Files: `src/generative_driver/mcp.py:25, 126`, the configurator's executor lookup, doctor
- `driver_start` and `driver_benchmark_suite_start` default `executor` to `codex`. The configurator then falls back to whatever bare `codex` is on PATH.
- In one run, a Goose-only user got 4 launches of an unverified binary. On the test host, `codex` on PATH was an unrelated Python app, and doctor reports it as available.
- Fix: default to the single configured executor, and refuse an executor that is not configured with an operator stop. Have doctor mark a runtime found only on PATH as unverified.

G1-3. **Add a version handshake between client and daemon.** [S-M] Files: `src/generative_driver/daemon.py:55`, `client.py`
- After an upgrade, a configurator still running from the old install keeps serving the new CLI and MCP.
- Observed: HEAD's client drove main's daemon, which applied the old gated behavior and failed at acquire, while `service status` reported ok.
- Fix: put the package version and the protocol in `service.json` and in the ping reply. On a mismatch, refuse and tell the user to restart the service.

G1-4. **Make reset-on-open a real safety stop.** [M-L, code read] File: `src/generative_driver/configurator.py` (around 3267-3269 and 1336-1408)
- Without the reset grant, an ESP32-style run continues after acquire observes reset-on-open. The gateway then refuses every later connection, so the probe, ground, emit and reuse workers spend budget without the device, and the run ends unscored.
- If the acquire worker forgets to listen, the run loses the device entirely.
- `resume` cannot add the grant, yet the objective lists the reset rule as a stop.
- Fix: have the configurator do the reset listen itself when it binds a serial device. Stop for the operator when reset is observed and not granted. Let `resume` add an explicitly approved reset grant and record it as an event.
- Do this before the ESP32-S3 smoke test.

G1-5. **Bring the Goose adapter to parity with Codex, and test it.** [S-M] Files: `src/generative_driver/agents.py`, `tests/test_agents.py`
- Goose ignores `approve_scoped_tools`: the command, env and recipe are byte-identical with or without approval, and `GOOSE_MODE` is never set. Whether headless Goose workers may call gateway tools therefore depends on the operator's Goose profile.
- No test covers the Goose `stage` extension, which is the device path, and no test runs a configurator stage with executor goose.
- Fix: set `GOOSE_MODE` from the approval setting, and add a Goose contract test that mirrors test_agents.py:40.
- Until this is fixed, Goose and Codex benchmark results are confounded.

G1-6. **Do not spend worker sessions on stages that cannot act.** [M] File: `src/generative_driver/configurator.py`
- Given no binding and no inputs, HEAD launched acquire, probe, ground and emit workers. Each was told "No interpreted model available", and the run completed "not equivalent".
- The objective and docs/design.md:121 treat a missing binding as an operator stop.
- Fix: refuse or stop a start that has neither a binding nor inputs. When a stage's required input is missing, hand off empty with a note and skip the worker, as interpret and reuse already do.

**P2: polish**

G1-7. **Goose credentials.** [S] `_environment` passes Codex credential variables to workers but no Goose provider variables. The `env_keys` escape hatch is undocumented and untested, and `configure` cannot set it. Add a repeatable `configure --env-key`, keep the GROUNDTRUTH/EVALUATOR filter, and document the option in docs/setup.md and docs/goose.md.

G1-8. **Run `driver_doctor` in the configurator.** [S-M] It runs in the MCP client's environment. Under a desktop PATH it reported codex, goose and docker unavailable even though the daemon finds them. The reverse case misleads too. Route it through the configurator, fall back to a local check when the daemon is unreachable, and label which process the result describes.

G1-9. **Honest labels in the generic report.** [S-M] `reporting.py` marks empty handoffs with failed checks as "accepted". In the JSON, `verdict: unscored` sits next to `equivalence: not equivalent`. Label empty handoffs distinctly, and nest or rename the independent verdict so it cannot be confused with equivalence.

G1-10. **Install docs and hints.** [S-M]
- The README covers only clone plus editable install, and its hardware extra command works only in a checkout. Document wheel and git-URL installs and `pip install "generative-driver[hardware]"`.
- Have doctor name the extra that enables serial listing.
- Put the binding shapes and the hint `<core.executable> -m generative_driver configure` in the skill or in the `driver_start` description.

G1-11. **Small CLI and CI gaps.** [S]
- Re-running `setup --host` into an existing directory prints a raw `FileExistsError` traceback. Print a one-line message or add `--force`.
- CI sets `GD_TEST_WHEEL` but not `GD_TEST_SDIST` (`.github/workflows/test.yml:44`), so the two sdist gate tests always skip.

G1-12. **Decouple the generic configurator from the benchmark package.** [S-M] `Controller.__init__` always imports `benchmark_support.suite_store`. With only that module allowed and every other benchmark and cryptography import blocked, 64 of 65 generic tests pass. Create the store on the first `suite_*` call, and add a test that blocks benchmark imports. This coupling predates the branch.

## Goal 2: the benchmark is robust and useful for judging the tool

**Verdict: partly.** The evaluator is now independent of the run and the evidence is tamper-evident, which is a large step up. But the benchmark cannot yet produce a trustworthy 9-trial number or a meaningful comparison between versions:
- all three v2 cases will be refused at admission;
- any change to the toolchain code makes results incomparable;
- final grading can record driver failures as host faults;
- the export hides why trials failed.

### Better than the last version

- **The evaluator is out of the stage loop.** At `d1121ad`, the configurator called `check_stage` on every stage and fed `project_feedback` grades into repair. Now no configurator path reaches truth:
  - no static import path leads from the configurator to truth;
  - of 133 truth, evaluator or decryption events traced at runtime, none had configurator.py on the stack;
  - the running daemon loads no truth, evaluator or cryptography module.

  The harness grades once, after the run, on a fresh emulator.
- **Each run gets one verdict, bound to a token.** The configurator holds only the hash of a token sealed under the evaluator password. A second, different record is refused, and so is a wrong token.
- **Evidence is tamper-evident.** Run evidence is sealed with AES-GCM under a pinned sha256, and accepted artifacts are re-hashed before sealing. Evidence with one flipped byte is refused. A forged verdict in a report is replaced by the regrade of the sealed records.
- **Faults are not scored as driver outcomes.**
  - An unconfirmed device stop means no grade.
  - Evaluator host faults and integrity faults are recorded as unscored.
  - A suite advances only on a graded or no-driver verdict.

  In the traced 3-child suite, the child with a host fault blocked the suite, and resume regraded only that child.
- **Protocols stay separate.** assistive/1 results are a new series (benchmark report version 3). Run and suite comparisons across protocols are refused, and earlier gated reports keep their own scoring path.
- **The candidate side never sees the device setup or the password.** The harness's DeviceProvider gives the configurator and workers only a descriptor. Tests check that no password, password file or emulator path appears in configurator files.
- **Accounting is clearer.**
  - Every planned trial is counted exactly once: equivalent, not equivalent, no driver, unscored, stopped (by category) or not run.
  - Each trial lists its check ids with categorical diff reasons and no expected values.
  - Fresh mission is a separate metric.
  - Calibration requires every mutant to fail exactly its declared checks.

### Fixes, ranked

**P0: before the 9-trial end test**

G2-1. **Narrow the evaluator identity before recalibrating.** [L] Files: `benchmark_support/reference_calibration.py:21-41`, `suite_reporting.py:350`, `configurator.py:1928`
- `calibration_identity` hashes configurator.py, toolkit.py, reporting.py, `_toolchain` and the skills, so any change to the stage loop voids admission.
- Recalibration reseals the truth with a random salt, which changes the truth and manifest hashes. `compare_suites` then answers "Frozen experiment identities differ".
- So two toolchain versions can never be compared, and comparing versions is what the benchmark is for.
- Fix:
  - Limit the identity to the code that grades: the grading parts of benchmark_support, interface_runtime, the emit check and the cryptography dependency.
  - Decide whether two suites are comparable from what the truth contains (calibration input hashes and contract digests), plus a passed calibration on each side for its own evaluator.
  - Report an evaluator change as a changed dimension.
- Do this first. Otherwise the next commit voids the recalibration in G2-2.

G2-2. **Recalibrate, and show a stale calibration before any trial starts.** [M code; L operator work] Files: `benchmark_support/registry.py:98`, `suites.py:71`, `harness.py:214`
- HEAD's calibration identity is c7d03a1b…, while the qualified one is 7330e0a9…. All three v2 cases (tq9-v2, sampled-sensor-v1, parameter-store-v1) will be refused at the first trial, yet `benchmark cases` lists them as "passed".
- Fix:
  - Put a public `evaluator_identity_sha256` in each manifest, and compare it in `cases` and in the suite preflight.
  - Add a read-only `benchmark truth admit --case X --password-file F`.
  - Have `run_suite` admit every entry before `suite_start`.
- Then, with authorization, freeze the revision and recalibrate natively with Renode 1.16.1, Ghidra 12.1.3, Java 21 and a pinned arm-none-eabi-gcc.
- Also fix `benchmark truth calibrate --case sampled-sensor-v1|parameter-store-v1` [S]. It crashes with `KeyError: 'authoring_root'` (`evaluator_cli.py`; reference_calibration.py:124).

G2-3. **Stop final grading from blaming the host for driver failures.** [M] Files: `benchmark_support/emulated_actions.py:57-58, 152`, the runtime's `faults.py:34-35`, `harness.py:309`
- The evaluator's own emulator has passed a health check. Even so, a driver whose identity request gets no reply is classified as a host fault, and the trial becomes unscored.
- The same happens to a package call killed by the 30 s subprocess timeout, although a valid model may run 60 s steps and 120 s operations.
- The suite then blocks, and every resume regrades and blocks again.
- A flaky driver is retried until one attempt passes. That biases the result toward the best of N attempts, because the records of discarded attempts are deleted.
- Fix:
  - Check the channel with a liveness probe that the evaluator owns. If the device is healthy, score the failure as the driver's.
  - Raise the package timeout above 120 s plus startup time, or count a timeout as the driver's failure.
  - Keep each discarded attempt's records, and the attempt count, in the sealed evidence.

G2-4. **Add a replay executor, which needs no model, to qualify the native path.** [M] File: `src/generative_driver/benchmark.py:59, 89`
- The only executors are codex and goose. No Renode session or native grading has run under assistive/1 (docs/verification.md:9).
- So the first native test of the harness, DeviceProvider, instruments, fresh-final grading and sealing would be the paid 9-trial run.
- Fix: ship a replay executor that replays a saved candidate's stage reports and artifacts, with no truth. After recalibration, run one native trial per family with it.

**P1: needed before people can trust and compare the numbers**

G2-5. **Make every exported result say why.** [M] Files: `benchmark_support/suite_reporting.py:495-562`, `evidence.py:44-123`, `configurator.py:1899`
- The owner record holds:
  - the stop reason, for example "Cannot start configured runtime";
  - the unscored reason, for example "evaluator host fault: RuntimeError";
  - the repair count;
  - stage notes that contain no truth.
- `report_suite` drops all of it, and `public_v3_report` has no reason field.
- Under assistive/1, `stage_counts.accepted` always equals the number of trials that reached the stage, so it tells nothing about which stage failed.
- Fix:
  - Add to each trial row: `outcome`, `reason` (scrubbed through `reporting._public`), `stopped_stage`, `repairs`, and a reduced stage-notes record.
  - Replace `stage_counts` with these counts per stage: reached, worker did not complete, configurator check failed, device diff, empty handoff, stopped here.
  - Show why a suite blocked, and why a trial is unscored, in `suite status` and in events.
  - Build `report.md` from these fields. Today it is one count plus the keys of the unscored trials. It should show the dimensions, k/N with a 95% interval, k/n per family, an outcome table with reasons, time, tokens, repairs and the most frequent failing check ids.

G2-6. **Make comparisons say when they are inconclusive.** [S-M] File: `suite_reporting.py:336-360`
- `compare_suites` returns `compatible: true` and a bare rate difference even when a side is provisional or blocked, or has stopped, unscored or unrun trials. A suite where 2 of 3 trials never ran compared as -33 points.
- At n=9, most differences are noise. For 3/9, the 95% interval is 0.07 to 0.70, and 3/9 against 6/9 gives Fisher p=0.35.
- Fix:
  - Return `conclusive: false` with reasons.
  - Add each side's Clopper-Pearson interval and an exact Fisher p-value, and label p >= 0.05 as "within noise at this n".
  - Report counts and intervals per family instead of bare rates.
  - Note that the default suite is 3 distinct tasks, one case_seed each, and that its 3 repeats vary only model sampling.

G2-7. **Check evaluator identity when comparing two single reports.** [S] File: `benchmark.py:284`
- Two version-3 reports graded by different evaluator revisions, under different truth and manifest hashes, compare as `compatible: true, comparison_kind: repeat`.
- Fix: require matching `evaluator_revision` and matching manifest, truth and pin hashes in the case pin, as `compare_suites` already does.

G2-8. **Record the runtime that actually ran.** [S-M] Files: `configurator.py:1910`, `agents.py:207`
- The runtime version is whatever the operator typed, and the model may be left out. Two suites that record only `{runtime: codex}` compare as "unchanged", even if the CLI or the vendor's default model changed.
- Fix:
  - At suite freeze, record the first line of `<command> --version` and the sha256 of the resolved executable.
  - Refuse a suite with an actual agent runtime and no model.
  - List missing dimensions as `unpinned_dimensions`, never as "unchanged".

G2-9. **Recover a trial whose emulator stop was not confirmed.** [S-M] File: `harness.py:686`
- Such a trial can never be graded, and no command records that it was reconciled. One slow Renode exit therefore blocks the 9-trial suite on every resume, even though grading uses a fresh emulator.
- Fix: add `benchmark trial-reconcile <trial_dir>`. It verifies that the process is gone, or records the operator's confirmation, and then lets resume grade the trial.

G2-10. **Depend less on a password file that workers can read.** [M] Files: `evaluator.py:136`, `harness.py:237`, `agents.py:116`
- The harness re-reads the password file during and after the run, so the file must stay on disk.
- Workers run as the same OS user. Codex `workspace-write` does not block reads, and Goose has no sandbox.
- So run records can show what workers were given, not that no worker read the truth.
- Fix: read the password into harness memory once, so the operator can remove or lock the file during the run. Alternatively, run workers as a separate OS user. Record the file's location and permissions as a precondition of the end test.
- Until then, describe the separation as "workers are not given the password", not as isolation.

G2-11. **Separate "equivalent" from "equivalent and usable by a fresh agent".** [S] Files: `suite_reporting.py`, docs/design.md:73 and 88
- The headline counts drivers the evaluator found equivalent even when fresh reuse never ran the package (mission `passed: None`).
- `report.md` never mentions the mission, and design.md still says that fresh use and six gates are required for success.
- Fix: report `equivalent_and_mission` next to the headline, and update design.md to match assistive/1.

G2-12. **Offer the authenticated suite regrade in the CLI.** [S] File: `benchmark.py:134`
- `suite report` accepts only `--output`. The end-test export would therefore carry the qualification "recorded-controller-verdicts" instead of "regraded-encrypted-evidence".
- Fix: add `--evidence TRIAL_KEY=PATH` and `--password-file CASE=PATH`.

**P2: polish**

G2-13. **Count repairs correctly.** [S] suite_reporting.py:276 treats any stage with attempt_count > 1 as repaired, so a resumed reuse stage made a first-attempt success look repaired. Use `progress.repairs == 0`, and add a `repairs_used` distribution, including "exhausted", split by verdict.

G2-14. **Add detail to the per-run report.** [S] reporting.py:192-197 shows only reasons such as "value mismatch". Add passed/total, up to five failing check ids with their reasons, the runtime and model, and the repairs used.

G2-15. **Report Goose cost and tokens.** [S] agents.py:284-290 captures Goose's `cost_usd` and older Goose's `total_tokens`, but `reporting._usage` drops both. Goose suites therefore show tokens as unknown next to Codex, and no suite reports cost. Report both with their coverage, and report tokens and cost per equivalent driver.

## Simplification candidates

- **Delete the dead `suite_attach` RPC.** [S] It is configurator.py:1576-1602 plus the dispatch at :1846, and src, the CLI and MCP have not called it since 032666a. Rewrite its five tests against `suite_start_child` and `suite_resume_child`. This also removes an unused entry point on the owner socket.
- **Delete the orphaned channel from evaluator to repair.** [S] `project_feedback` (behavior.py:120, 46 lines, plus about 101 test lines) is the channel the objective removes, and nothing in src calls it now. Also delete `frozen_final_decision` (evidence.py:372), and move the version 1 `seal_run_evidence` into a test fixture. Deleting the code stops anyone from quietly reconnecting the channel.
- **Shrink the generic self-evidence verdict.** [M]
  - Today about 399 lines (configurator.py:260-1052 plus `_binding_state`) and about 1,555 test lines encode four reachability cases: UNBOUND, UNREACHABLE, UNHEARD and UNLISTENED.
  - Instead, return `false` only when the frozen bytes failed on the device or the replay of recorded exchanges failed.
  - Return `null` whenever the frozen bytes never ran live beyond the identity exchange, and record the gateway's refusal word for word.
  - This removes UNLISTENED, the rule behind the pyserial misgrade, and most of the four cases.
- **Use one fault classifier.** [S-M] Four places decide who owns a fault, and they disagree: `classify_failure`, `_host_fault`, the lambda in `_live_outcome`, and the payload fault in `_binding_state`. Route all of them through `interface_runtime.faults.classify_failure`.
- **Move benchmark orchestration out of configurator.py.** [L]
  - The file grew from 1,398 to 3,398 lines. Of those, 862 lines orchestrate suites, the harness or evaluation, against 167 at the baseline.
  - Move `suite_*`, `record_evaluation`, `attach_device`, `_evaluation_errors` and `_harness_*` into a benchmark_support service registered through a dispatch table. Keep only the client API for the device provider in the generic configurator.
  - Pair this with creating `SuiteStore` lazily (G1-12).
- **Move `cryptography` into a `bench` extra.** [S] Only `benchmark_support/truth.py` uses it. Have that module raise "install generative-driver[bench]" when the import fails.

## Suggested order

1. G1-1: get the installed-wheel run passing, make the test recipe test the worktree, and run CI on Windows and macOS.
2. G1-2, G1-3 and G1-5: fix the executor default, add the version handshake and bring Goose to parity. They are small and protect every later trial. Do G1-4 before the ESP32-S3 smoke test.
3. G2-1 and G2-3: narrow the evaluator identity, and fix how host faults and timeouts are classified. Then build the admission check from G2-2 and the replay executor from G2-4.
4. Freeze the revision. With authorization, the operator recalibrates the three families natively. Then run one replay trial per family.
5. G2-5 to G2-8, so the end-test export explains its results and its comparisons show their uncertainty.
6. Only then ask for authorization to launch the 9-trial model run.

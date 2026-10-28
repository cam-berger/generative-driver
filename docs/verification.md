# Verification

Results describe the pinned revisions below. Scripted tests, replay, native reference qualification, model execution and physical observations establish different things. Current runs use acquire → interpret → probe → ground → emit → fresh reuse; stage completion alone is not equivalence.

## Multiline-reply repair comparison — 2026-10-27

Parameter store changed from **1/3 to 3/3 joint successes**. The core track changed from **7/9 to 9/9**. Joint success requires independent equivalence, successful fresh reuse of the same frozen package, and six accepted handoffs.

| Profile | Baseline joint | New joint | New checks per trial | New pipeline repair cycles |
| --- | ---: | ---: | --- | ---: |
| Zephyr Modbus (upstream) | 3/3 | 3/3 | 315/315 | 1 |
| TQ9-v2 | 3/3 | 3/3 | 9/9 | 0 |
| Sampled sensor | 3/3 | 3/3 | 85/85 | 3 |
| Parameter store | 1/3 | 3/3 | 891/891 | 0 |
| Legacy TQ9 (supplement) | 3/3 | 3/3 | 3/3 recorded-reuse | 1 |

The runnable upstream track is 3/3; the three-case core is 9/9; legacy is 3/3. Administrative totals are **13/15 before and 15/15 after**, with fresh missions 15/15 and 15/15. Legacy shares TQ9 ancestry, so these are five profiles across four measured families, not fifteen independent devices.

The defect was an undersized receive contract: a rejected command can return an error line followed by `DONE`, but generated models allowed one line. The old qualifier counted a framing-limit error as a successful rejection. The fix validates declared rejection/completion bounds in the generation tools, sealed validator and managed gateway, and requires a complete rejection transcript in fault qualification. The managed guard runs before a write is marked uncertain. The raw execution engine and all 45 benchmark-support source files remain byte-identical to baseline.

All three fresh store trials tried one-line bounds, received explicit validator defects, and corrected them within their original interpretation attempts before live probing. Final commit/abort line bounds were 8, 2 and 2. These edits are local agent corrections; each store trial used zero coarse pipeline repair cycles. Store trial 2 left the offline `update` predicted-reply fixture uncovered, and its accepted interpretation records failed/incomplete qualification. Its later live and independent 891-check results passed. The other two store trials completed their supplied offline qualification. Worker claims, handoff acceptance, self-evidence and final grades are distinct.

Validation included ten focused behavior tests with recorded red/green regressions, real loopback TCP tests with LF/CRLF replies, bounded incomplete replies and no write replay, generic deployment repair, and pre-dispatch refusal. The installed suite ran **938 tests: 937 passed, one optional Linux-backend skip**. An initial unrelated cancellation-ordering test failure is retained with the unchanged baseline/fixed repeats, full lifecycle module, two controlled event-order diagnostics and clean full-suite rerun. Fresh qualification ran 42 locally installed Renode reference/control executions across four cases before inference. Positive references passed and required wrong models failed their expected checks; all control-check outcomes match baseline. The deliberately short-frame control still fails the original five rejection checks. The two failed baseline store models are refused by the new validator; the passing baseline model remains admitted.

Implementation `625f55a` was tested against preserved baseline `9df3887`, using the same frozen Codex CLI `0.164.0-alpha.11.1`, gpt-6.1-sol, unspecified CLI reasoning default, dependencies, tools, original scenario/seed 0, trial order and budgets. Each profile received three fresh six-stage `assistive/1` trials, one active child at a time, with a 10,800-second child budget and at most two pipeline repair cycles. Within-worker validation edits are additional iterative work inside that budget. No primary trial was replaced or given a budget extension. The executed and supplied firmware `.bin` artifacts, public missions, contracts, recipes, references and required mutations match baseline. Fresh private build ELFs/reports differ only in audited debug/path provenance; both raw hashes are retained.

| Profile | Baseline median trial min | New median trial min | New trial range min | New fixed-case 95% interval |
| --- | ---: | ---: | --- | --- |
| Zephyr Modbus (upstream) | 9.6 | 7.9 | 7.8–139.2 | 29.2%–100.0% |
| TQ9-v2 | 5.5 | 5.2 | 4.9–6.1 | 29.2%–100.0% |
| Sampled sensor | 7.1 | 7.4 | 7.3–8.0 | 29.2%–100.0% |
| Parameter store | 7.0 | 7.6 | 6.6–7.9 | 29.2%–100.0% |
| Legacy TQ9 (supplement) | 5.2 | 5.9 | 5.7–8.5 | 29.2%–100.0% |

Across all fifteen profile trials, tokens were 23,672,845 → 23,650,505; usage was reported for 98/98 → 100/100 attempts. Managed calls were 551 → 575; pipeline cycles were 4 → 5. Core tokens increased 11,287,310 → 11,545,060 even though main-track tokens decreased 19,940,574 → 18,680,476. Cached input and reasoning output are subsets, not extra totals. These observations do not support a uniform cost improvement.

Unadjusted summed trial time was 104.0 → 236.0 minutes; reported worker time was 96.5 → 114.0 minutes. The first Zephyr trial retained its original **8,352.312-second elapsed** and **1,448.919-second reported worker** times. Mac sleep/wake overlapped a 7,416.209-second public-event gap; that gap is not measured sleep duration and was not subtracted. A temporary idle-sleep guard was then scoped to the conductor. API intervention counts are separate from these external host events; no repair is attributed wholesale to sleep. Timing remains descriptive.

Upstream selection retains 21 slots across seven lineages: three Zephyr passes and 18 blocked slots across six lineages. Runnable success is 3/3; demonstrated selected coverage is 3/21, one of seven lineages. Physical BME280 remains inventory-blocked without a selected binding/reference; no physical operations ran. Scripted setup smoke and reference calibration are excluded from model performance. Legacy uses recorded-reuse grading, with no independent public temperature reference or controlled fresh-final vector claim. This study measures software emulation on macOS, not Windows model performance, electrical faults or physical timing.

The last legacy trial used one automatic owned-emulator recovery: its old owner stopped, the initial state was verified, and fresh reuse used the new session. Its one pipeline repair followed a completed binary device refusal/response-expectation mismatch. This is separate from multiline framing and remains within the original trial.

Each series was regraded under its own preserved installation. All fresh missions were separately rederived from public accepted-reuse observations, package/capability mappings and event chronology. Reuse sessions were distinct and their sole recorded artifact input was the emitted package. State preservation is established at before/after snapshots, not continuously within a call. Workers were not filesystem-read isolated; password separation and hashes are not an OS sandbox or third-party attestation.

Standard upstream comparison: incompatible, reason=`Frozen experiment identities differ`.
Standard core comparison: incompatible, reason=`Frozen experiment identities differ`.
Standard legacy comparison: compatible; changed dimension: `toolchain_revision`.

Refreshed calibration pins make the upstream/core comparisons incompatible; legacy remains compatible under its recorded-reuse policy. Preserve those standard decisions and read the main table as a descriptive system comparison on familiar development cases. Three repetitions and fixed-case binomial intervals do not establish a population success rate, randomized causal improvement or model ranking.

The [verification index](../bench/evidence/multiline-repair-verification.json) records trial/package hashes, independent audits, grading seals, frozen plans and raw metric distributions. The local archive is `20261027-multiline-repair-1707622c` in the Generative Driver experiments directory; baseline `20261027-whole-benchmark-9a965fb8` remains unchanged. All fifteen trial devices and this study’s configurator were stopped; five temporary evaluator key copies and their mappings/options were removed. Original passwords, the user-mounted vault, pre-existing sign-in and private research evidence were preserved. The conductor-scoped idle-sleep assertion was released.

### Frozen method

The pre-inference decision rule was independent equivalence plus successful fresh reuse of the same frozen package after six handoffs. Preserve baseline `9df3887`; qualify and measure implementation `625f55a` without changing firmware, mission, hidden vectors, final rejection witnesses or thresholds. The original plans remain in Git history; their hashes, frozen suite plans, configurations and per-trial evidence remain in the machine-readable indices and evaluator-owned archives.

Both series used the same Codex CLI `0.164.0-alpha.11.1`, `gpt-6.1-sol`, unspecified CLI reasoning default, dependency/native-tool versions, original scenario and evaluator seed 0. Run upstream Zephyr first, then the interleaved three-case core, then the legacy supplement, with three fresh trials per profile and one active child/firmware process globally. Child budget: 10,800 seconds; at most two pipeline repairs; core suite: 108,000 seconds; other suites: 32,400 seconds each. No primary trial replacement, parent-agent coaching or budget extension. Grading-only resume can use unchanged frozen bytes after an environment correction and retains that intervention.

Before inference, independent audits checked design, installed setup, authenticated admission, positive references, required wrong models and matching task inputs. Fresh private family build ELFs and reports differed in temporary debug paths: retain raw digests, require all other inventory files and executed `.bin` bytes to match, and require identical stripped ELFs, load metadata and path-normalized debug-string multisets/build reports. This disclosed provenance check preceded inference. Afterward, separately audit seals, public missions, frozen pins, usage and counts. Cached input/reasoning are subsets; missing counters remain unknown. Preserve stops, unscored outcomes, excluded and blocked slots separately from model failures.

The strict suite comparisons retain changed-pin incompatibility for upstream/core; legacy remains compatible. Do not force compatibility or infer causality from this development-case comparison. Three repetitions of a fixed familiar case do not establish a held-out firmware success rate, a model ranking or physical reliability.

## Preserved whole-benchmark baseline — 2026-10-27

Implementation `9df3887` produced **7/9 core**, **3/3 Zephyr** and **3/3 supplemental legacy** joint successes; fresh missions passed 15/15. Both failed store drivers passed 886/891 checks, failing the five complete-rejection witnesses while associated state checks passed. They allowed one reply line; the passing driver allowed three, and recorded rejections contain two lines. This motivated the generic receive-contract fix; it did not establish unintended state changes.

The baseline retained four automatic repairs, 98/98 usage attempts, 551 managed calls, 23,672,845 reported tokens (20,597,120 cached input, a subset), 104.0 summed trial minutes and 96.5 reported worker minutes. No manual repairs, extensions, host stops, unscored finals or replacement primary trials occurred. Every sealed final regraded under its preserved installation; a separate audit rederived all 15 missions from public observations, package mappings and chronology. See the [baseline index](../bench/evidence/whole-benchmark-performance-verification.json). The comparison above retains every original baseline result.

## Native Modbus pilot — 2026-10-27

The same Zephyr v4.2.0 commit `413b789deb391d3a37d06b463288a5fe765ee57e` runs as host-compiled firmware through the existing registry, provider, six-stage lifecycle and reports. Its application, RTU parser, CRC and state handlers stay unchanged. Substitutions are interrupt-capable native TTY through a host PTY and GPIO emulation. The agent receives the exact final executable (`zephyr.exe`, retained as `firmware.elf`) that executes, including symbols/DWARF. That differs from the ARM raw image and its recovery difficulty; this is not a controlled backend-only performance comparison.

Two clean AArch64 builds produced identical executable SHA-256 `d7b0d96dfbd0c2b42b50339a9abf75752899d775762b5ef37e6c82dd425e927c`. Twenty native/Renode episodes passed **568/568** comparison checks: exact replies plus independently observed complete register/coil state after valid operations, refused writes, malformed traffic, recovery and reset. Four native reference executions passed 315/315 each; all six declared mutants were rejected. These scripted authentic-firmware results are distinct from agent performance.

One `gpt-6.1-sol` trial completed all six stages, package-only fresh reuse and **315/315** independent final checks, confirmed by encrypted-evidence regrade. It used one interpretation repair, eight worker attempts, 489.95 worker seconds and 1,600,910 reported tokens (1,295,616 cached input, a subset). Two prior setup attempts emitted no driver: Bubblewrap namespaces were denied, then the replacement runtime lacked its matching tool-host helper. Their failures and usage remain recorded.

The completed trial was initially unscored because the copied ELF lacked execute permission. Restoring that bit changed no bytes; grading-only resume passed with no repeated stage or changed evaluator. Its 837.42-second elapsed time includes the repair interval. The permanent adapter instead verifies and stages a private executable copy before launch/reset, leaving the non-executable pinned source untouched. A failing-then-passing Linux process regression, fresh 568-check comparison, reference/mutant calibration and admission validated that fix. Historical trial and permanent-fix package identities remain separate. The final installed suite ran 928 tests with three optional skips; the Linux backend suite ran 11 with one non-Linux guard skipped.

The backend is optional Linux execution, including a Linux VM on macOS/Windows. `host-clock-with-process-pauses` pauses the owned process between calls; with `--rt`, simulated time catches up with the continuing host clock on resume. Thinking time is not excluded from clock pacing. Independent register observations use executable symbols and the GPIO offset uses DWARF; neither trusts protocol readback. Physical UART/RS-485, electrical faults, timing equivalence and cross-family behavior remain unqualified. The container's workspace/outside-write smoke check does not establish worker read isolation. Dedicated authentication was signed out after the pilot.

See [native execution instructions](benchmark-case-authoring.md#native-zephyr-execution) and the [pilot index](../bench/evidence/native-modbus-verification.json). Private build attempts, setup failures, the unscored seal and raw evidence remain in the evaluator archive.

## Upstream intake and lifecycle — 2026-10-26–27

The [catalog and gaps](benchmark-corpus.md#catalog-and-execution-gaps) retain seven source lineages and all 21 selected slots. The ARM Zephyr image rebuilt identically twice at `6137dfd5044bf38e791b77e25db0408ba081fc60ccdd2953629e7fb62090ca82`. Independent SRAM/GPIO probes, two valid encodings and six targeted mutants qualify observable behavior rather than candidate syntax. Early reset/vector-table and missing actuation-grant faults were setup evidence, not model failures.

The socket transport investigation retained failed buffering/delay controls and one monitor timeout. The eventual `BufferedSocketTerminal.cs` forwards a received block at one virtual timestamp, without changing bytes, implementing protocol handlers or retrying writes. Three full missions passed without driver delays; a separate monitor timeout remains a host failure. See the [transport record](../bench/evidence/2026-10-26-modbus-transport-experiment.json). That does not establish physical RTU timing or the cause of every timeout. The later 50 ms public scheduling margin is an emulator condition, not a protocol minimum or physical throughput claim.

The original corpus suite completed three Zephyr trials at 315/315 plus fresh reuse; one retained a host recovery and bounded acknowledgment-timeout repair. The ambiguous model-directory/file hash warning did not veto the independently working driver. Demonstrated coverage was 3/21, one of seven lineages. The [intake index](../bench/evidence/2026-10-26-corpus-validation.json) preserves individual trials, failures and interventions.

Lifecycle integration qualified four software cases with 42 reference/control executions and 26 rejected mutants. One installed model trial completed all six stages, fresh reuse and 315/315 final checks; authenticated regrade and the public corpus CLI agreed. The four macOS/Windows × Python 3.11/3.13 jobs passed 915 tests each (Windows: four skips). See the [architecture validation](../bench/evidence/2026-10-27-benchmark-architecture-validation.json) and [accepted registration decision](adr/0001-benchmark-registration-and-selection.md).

That model trial spent a repair after an intentional invalid-address probe despite unchanged independent state and same-driver recovery. A later scripted evaluation of its preserved pre-repair model also passed 315/315; the associated 283.35 worker seconds and 816,527 tokens are not counterfactual savings. An expected-refusal exemption is **not implemented**. Its evidence requirements and synthetic-only prototype scope remain in [follow-up 03](../.scratch/benchmark-lifecycle/issues/03-expected-refusal-repair.md). Qualification scoping and a structured monitor are also [open follow-ups](../.scratch/benchmark-lifecycle/spec.md).

## Analysis, client and protocol evidence

[PR #16](https://github.com/cam-berger/generative-driver/pull/16) consolidated PRs #6–15: acquired USB/FTDI evidence, the sealed firmware-analysis kit, repair tamper checks and joint equivalence/fresh-reuse reporting. The [real Ghidra lane passed on macOS 26 and Windows Server 2025](https://github.com/cam-berger/generative-driver/actions/runs/38845379254) for `e26d865`. It imported a self-authored Cortex-M3 raw image and an Xtensa ELF with Ghidra 12.1.3, JDK 21.0.12.1 and Xtensa GCC 14.2.0, checked exports and killed the timed-out JVM tree. macOS natives were built from release sources; the release ships none for arm64. Earlier local release-only failure, native rebuilds and fixture limitations remain in Git history and CI artifacts. This qualifies those loaders/fixtures and cleanup, not larger firmware, Renode or model performance.

The navigation exporter bounds `references.tsv`, `strings.tsv` and `switches.tsv` at 16 MiB unless configured otherwise. `navigation.json` records found/written rows and truncation; `ghidra_run` verifies hashes and reports truncated tables. Static exports do not prove runtime reachability. Scripted stand-ins establish Python contracts only. See the [Codex acceptance lane](codex-acceptance.md) for authenticated sealed-suite regrading; its CI execution is distinct from the local studies above.

New runs use `assistive/1`: notes accompany handoffs, device differences drive bounded repair, and each run records one final equivalence verdict. These are a **new series** relative to the older gated protocol below. Workers are **not read-isolated** until a real sandbox exists; evaluator separation is password-based. The bench harness holds passwords and truth; the configurator holds no password or recipe. Workers see instrument readings. A **fresh native calibration** for the exact implementation precedes model measurement; an earlier seal regrades from its **preserved checkout**, rather than a later evaluator.

`benchmark run --executor replay` and replay suites have scripted TCP/toy-truth coverage through all six stages, final grading and sealing. Reports label `replay-emulation`; comparisons reject mixing replay and model evidence. Those tests do not establish native replay or model performance.

The dashboard at `0b9fb96` observed one original-image TQ9 model trial (Codex CLI 0.159.2, `gpt-6.1-sol`, medium): six stages, zero repairs, 10.05 minutes, fresh reuse and regraded 9/9. Nineteen sampled browser snapshots agreed with bracketing owner state; stale status and reconnection preserved the completed run. Final source and installed Python 3.11/3.13 suites ran 430 tests each, zero archive skips. Hosted startup exposed reverse DNS blocking; a no-name-service regression validated loopback startup without that dependency. The precise initial hosted cause remains an inference; unchanged cleanup/reconnect failures and retries remain recorded. No scheduler fix or Windows workstation/live Goose observation is claimed. See [dashboard evidence](../bench/evidence/dashboard-verification.json) and [usage](dashboard.md).

An earlier ESP32-S3/BME280/SH1106 physical exploration did not achieve the requested end-to-end generated interface. It exposed acquisition/reset, optional-dependency, device-health and grounding gaps; some were later superseded by the consolidated pipeline. It is historical failure evidence, not an admitted physical benchmark or present-day support claim. Private captures/backups remain operator-owned. No physical operations ran in either 2026-10-27 study.

## Historical gated-series results

These development reruns used familiar cases and medium-reasoning `gpt-6.1-sol`; their older acceptance and evaluator versions remain attached to their original revisions. They are not pooled with `assistive/1`.

| Snapshot | Measured outcome and limits | Preserved evidence |
|---|---|---|
| PR #1 original | 417 software tests; historical scoring retained. | [Original index](../bench/evidence/benchmark-final-verification.json) |
| PR #1 follow-up, 2026-10-03 | Source/installed 3.11/3.13: 420 tests each, zero archive skips. All 32 native grades reproduced (12 reference, 18 mutant, two legacy); stale calibration refused. Windows checkout hash/path/race fixes retain failed jobs and unchanged retries. | [Follow-up index](../bench/evidence/pr1-followup-verification.json) |
| Pipeline repair, 2026-10-05 | 0/3; mappings and refusal classification blocked fresh reuse. | [Pipeline index](../bench/evidence/pipeline-repair-verification.json) |
| Capability repair, 2026-10-05 | 0/3; grounding/repair exhaustion. 432 tests; 30 native grades reproduced and three admissions passed. | [Capability index](../bench/evidence/capability-repair-verification.json) |
| Context repair, `21359b4` | 2/3; TQ9 9/9 and sensor 85/85; store pending update stopped before final. 441 tests; 30 native grades, three admissions; one cleanup-only response did not resume the disposed store. | [Context index](../bench/evidence/context-repair-verification.json) |
| Unchanged store repeat | Separate 1/1, 696/696, zero repairs; visible probes missed pending update. Does not change the prior 2/3 result. | [Repeat index](../bench/evidence/store-repeat-verification.json) |
| Transaction/recovery v4, `f6c055d` | 457 tests; 31 native grades reproduced. Superseded because candidate first-line framing could earn rejection credit. Preserve the monitor collision/retry and proof-harness preflight mistakes. | [V4 index](../bench/evidence/transaction-recovery-v4-verification.json) |
| Transaction/recovery v5 | 461 tests in source/installed 3.11/3.13, zero archive skips; all 32 grades reproduced (12 reference, 20 mutant). Short-frame mutant fails five independent rejection witnesses. Old-process disposal, distinct replacement, initial state and encrypted proof validated recovery without replaying a failed write. Controller durability was a separate deterministic proof. | [V5 index](../bench/evidence/transaction-recovery-verification.json) |
| V5 model trial, `272c1b1` | 2/3 in 35.2 min; TQ9 9/9 after repair/recovery, sensor 85/85 after repair. Store stopped at 364/366 diagnostics after idle-commit rejection and exhausted repairs; final not graded. All 23 attempts reported usage; 127 calls finished. Original 3/3 publication condition unmet; later user-authorized integration preserved 2/3. | [Trial index](../bench/evidence/transaction-recovery-trial-verification.json) |
| PR #5 integration | Combined pipeline/dashboard; no new model/native trial. Source and installed 3.11/3.13: 471 tests each, zero archive skips; replay and three admissions passed. | [Integration index](../bench/evidence/pr5-integration-verification.json) |

Historical dependency versions and package hashes belong to these measured environments, not new project pins. Intermittent reconnect/cancellation ordering failures have unchanged rerun evidence; no scheduler correction is claimed. Physical accuracy, Windows Renode model performance, Windows 11 UI setup, live Goose and worker filesystem read isolation remain unverified.

## Reproduce software checks

Build from a clean revision, with output outside installed resources:

```sh
python -m build
```

Set `GD_TEST_WHEEL` and `GD_TEST_SDIST` to absolute archive paths; run `python -m unittest discover -s tests -v` and `node tests/dashboard-client.test.js` (Node.js 22+). Install the wheel in clean Python 3.11/3.13 environments, extract the source archive elsewhere and run tests there. Remove checkout `PYTHONPATH` and credential/native overrides; retain archive variables. Confirm `generative_driver.__file__` and `interface_runtime.__file__` resolve to the installation.

```sh
python -m generative_driver benchmark cases
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m generative_driver setup --host codex --output "codex setup"
python -m generative_driver setup --host goose --output "goose setup"
```

Use new output directories; run `python -m driver replay` inside `smoke output/relocated package`. These checks use no inference or physical devices. See [qualification/scoring](../bench/groundtruth/README.md), [benchmark operation](../bench/README.md) and [upstream case authoring](benchmark-case-authoring.md).

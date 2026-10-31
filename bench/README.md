# Benchmark

The [native Modbus pilot](../docs/benchmark-case-authoring.md#native-zephyr-execution) uses the same registry, provider, six-stage runner and reports with an explicitly pinned native executable.

Recover a device interface, emit a portable driver, and have a fresh agent use it. Judge outputs/effects, not source-code similarity.

Current `assistive/1` runs follow acquire → interpret → probe → ground → emit → reuse. Stages hand off artifacts/notes; device differences drive repair; only safety, integrity, operator and host faults stop runs. One equivalence verdict follows completion. This is a new series, never compared with earlier gated results, including the 2/3 trial. See [design](../docs/design.md#assistive-protocol-amendment--2026-10-07).

## Profiles

| Case | Purpose |
|---|---|
| `setup-smoke` | Scripted installation/package replay, no inference |
| `tq9` | Legacy Renode temperature/timer/disarm and fresh reuse |
| `tq9-v2` | Calibrated evaluation, device-driven repair and sealed final behavior |
| `sampled-sensor-v1` | Signed/scaled readings, acquisition and sequence counters |
| `parameter-store-v1` | Banks, pending/committed values, generation, commit and abort |
| `zephyr-modbus-v1` | Upstream Zephyr RTU sample: register/coil effects, unrelated-state preservation and recovery |
| `bme280` | Physical FTDI/I2C sensor and independent operator references |

[Installed assets](../src/generative_driver/resources/bench/) pin stable original images, encrypted truth and evaluator versions. A listed or publicly `available` case has not authenticated admission for the installed implementation. The three-family core plans nine default trials and requires fresh native qualification before execution. The [catalog and execution gaps](../docs/benchmark-corpus.md#catalog-and-execution-gaps) describe future admissions separately from [measured results](../docs/verification.md).

The [upstream corpus](../docs/benchmark-corpus.md) adds seven lineages: one upstream case with published native qualification evidence and six blocked lineages. Freeze all lineages for a pilot or select a debug subset; immutable plans retain excluded slots and reasons. Authenticate selected ready cases with `benchmark corpus check`, run them through the existing suite lifecycle and report measured success, selected coverage and full-catalog demonstrated coverage separately. See [case authoring](../docs/benchmark-case-authoring.md) to add or retire cases and the [architecture decision](../docs/adr/0001-benchmark-registration-and-selection.md) for the static registration boundary.

## Install and check

Use Python 3.11+ and one environment. Follow [setup](../docs/setup.md), [Codex](../docs/codex.md) or [Goose](../docs/goose.md).

```sh
python -m pip install -e ".[dev]"
python -m generative_driver doctor
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m unittest discover -s tests -v
```

Use a new output directory. Expect `scripted-replay`, `model_benchmark: false`, `verdict: passed`. No account, hardware, native tools or password is needed. In `relocated package`, run `python -m driver replay`.

## Run an agent benchmark

Install [Renode](https://github.com/renode/renode/releases), [Ghidra](https://github.com/NationalSecurityAgency/ghidra/releases) and compatible Java. Native reference setup uses Renode 1.16.1, Ghidra 12.1.3, Java 21 on macOS arm64; native Windows benchmark qualification remains separate. Authenticate/configure an exact runtime/model and total wall-clock budget:

```sh
generative-driver --home /operator/state/benchmark configure --executor codex --command /absolute/path/to/codex --provider openai --model YOUR_MODEL
generative-driver --home /operator/state/benchmark benchmark cases
generative-driver --home /operator/state/benchmark benchmark run --case tq9-v2 --scenario original --case-seed 0 --executor codex --budget-seconds 10800 --password-file /private/evaluator/tq9-v2.password --renode /absolute/path/to/renode --ghidra-home /absolute/path/to/ghidra --java-home /absolute/path/to/java-home --approve-emulator-tools
```

Replace placeholder paths with your own absolute paths; quote paths containing spaces. Keep the same absolute global `--home` through configure, start, status, resume and report. Use `--executor goose` for configured Goose. Emulator approval covers assigned emulator tools only; MCP uses `scoped_tool_approval: "emulator"`. V2 families use `original`; `case_seed` changes evaluator episodes, not model sampling.

A detached bench harness receives password-file/Renode paths on stdin, authenticates admission, starts the device and passes only its descriptor/hashes to the configurator. The configurator never receives the password or its path. Legacy TQ9 uses its legacy recipe through the same harness.

Save `trial_id` and `run_id`. `--wait` follows device shutdown and post-run grading/sealing. Records live at `<state>-bench/trials/<trial_id>`. Repeating `--request-id` follows the same trial without another device. To finish an unscored/unposted evaluation, or grade a formerly stopped trial whose run later completed:

```sh
generative-driver --home /operator/state/benchmark benchmark trial-resume TRIAL_DIRECTORY --password-file /private/evaluator/tq9-v2.password --renode /absolute/path/to/renode
```

It requires the trial's password and Renode when grading remains; a sealed/drafted final is never graded twice. A stopped emulated run needs a matching healthy device attached by its harness. Only suite harnesses attach replacements today; start a new single trial instead of continuing a stopped one. If emulator stop was unconfirmed, stop it manually from its record; no command records that reconciliation, so the trial stays ungraded.

### Evidence, repair and calibration

Workers receive tasks/units/grants in `TASKS.json`, analysis paths and TCP wiring. Keep Ghidra outputs/caches/logs in the workspace; inferred physical UART settings stay in notes. TCP proves neither baud nor pins.

Probe/ground return instrument readings and candidate outputs, never grades. Readings can expose firmware-internal state, so results differ from interface-only protocols. Model/device differences and configurator validation defects permit at most two interpretation returns, then continue with the best revision. Ground runs the adapted model using mappings, probe records and the live journal. Repair preserves sealed prior candidates/replies/mappings/journals and writes new root outputs; passwords/answers remain excluded.

Use `validate_capabilities.py` with `CAPABILITY_FORMAT.md` before submission. Mapping errors become handoff notes without I/O. Trusted pre-transport refusals leave no uncertain effect; opening/interrupted execution may require reconciliation.

The sampled sensor's probe and ground assignments include `DEVELOPMENT_CONDITIONS.json`: five public reference inputs
at −12, 32, 0, 7.5 and 18.5625 °C, independent of the hidden final vectors. Select a listed `condition_id` on a live
`interface_execute` or `probe_run` call; omit it to retain the current source. The harness changes the source before
the call, without acquiring or resetting. Acquire a sample and compare its returned value with the instrument
reference and advancing sequence. Readings carry the condition's session, epoch and whether an acquisition occurred
since selection; a retained sample is not a measurement of the requested new input. Zero-only observations cannot
establish scale. The catalog is development evidence, not a new acceptance gate or a tool available to fresh reuse.

For the sampled sensor, probe and ground compare mapped outputs with the configurator's paired instrument readings
using the public `TASKS.json` quantities. Temperature allows `1e-9` °C of floating-point noise; sequence is exact.
Only successful live calls of the assigned model, on the recorded session and matching the mapped task arguments,
supply these differences. A batch probe compares its last sample with the after-reading. Retained reads compare
with the retained measurement; a new acquisition must change the measured sequence. Observed value/unit or sampling
differences enter the existing bounded interpretation repair with model/mapping hashes and dispatch evidence.
Worker claims and malformed/missing mappings alone do not trigger this route. Final evaluation is unchanged.

Owned-emulator recovery requires trusted completed model-failure evidence for every unresolved effectful dispatch, verified old-process disposal and independently verified replacement. The provider stores proof; the configurator commits receipt/session/lease with the handoff. Recovery uses a repair only when revising the model; exhausted repair budget alone does not forbid replacement. This applies to failed frozen-driver writes in reuse too. Retain failure evidence; attached sessions, unknown/host/operator faults, cancellation or unverified replacement stay blocked. Never replay the uncertain write.

Store update discards pending edits, changes only the requested committed cell, preserves others, advances generation once and ends idle. Final grading covers idle/same-bank/different-bank pending updates, commit, abort and complete rejection, with full state checks. The diagnostic contract runs only during native calibration. Rejection needs a private complete-response witness matching captured bytes; candidate framing is insufficient, equivalent requests remain valid.

Admission pins the grader identity: the hash of each module the grader imports (grading modules, registered families and `registrations.py`) and the `cryptography` version. A change to that closure voids qualification and blocks comparison with earlier runs; runtime, package template, toolkit, skills and report changes do not. Recorded emulated calibrations predate this protocol and were stale at `52eb018`: obtain fresh native calibration before trials or the nine-trial test via [groundtruth setup](groundtruth/README.md).

Keep password files outside checkout/candidate inputs. Workers are not read-isolated: same-user workers could read control endpoints and act outside recorded calls. Endpoints expose no truth. Answer-key separation is password-based, not a sandbox.

### Inspect and score

```sh
generative-driver --home /operator/state/benchmark status RUN_ID
generative-driver --home /operator/state/benchmark result RUN_ID
generative-driver --home /operator/state/benchmark report RUN_ID --output reports/trial.json
generative-driver --home /operator/state/benchmark benchmark score reports/trial.json --evidence /private/evaluator/run-evidence.enc --password-file /private/evaluator/tq9-v2.password
generative-driver --home /operator/state/benchmark benchmark compare reports/before.json reports/after.json
```

`benchmark-report/3` records the final `equivalent` verdict, and a pass is that verdict: the evaluator's graded equivalence of the frozen driver. The fresh mission is reported beside it and does not change it; corpus success no longer requires it or the six accepted handoffs. Score with the trial's `run-evidence.enc` (`benchmark-run-evidence/2`): verify run/snapshot/case, accepted package and single final, then regrade its records offline. Sidecars seal artifact/note hashes, final/mission checks, public verdict and recovery receipts/journal hash. Pending, stopped or unscored runs score `unscored`.

Preserve accepted artifacts. Regrade earlier evidence only from its preserved checkout; another evaluator implementation is refused. Earlier `/2` reports and evidence `/1` keep gated semantics. Compare refuses different protocols, except suite reports on a shared scoring set, which `suite compare` pairs device by device.

MCP: `driver_benchmark_run` takes `profile`, `executor`, and options `scenario_id`, `case_seed`, `budget_seconds`, `evaluator_password_file`, `renode`, `ghidra_home`, `java_home`, `scoped_tool_approval`. Follow status/events/result with the saved ID. Windows requires a [separately started owner](../docs/setup.md#start-the-configurator).

## Replay a saved candidate

`--executor replay` exercises all stages without a model. A candidate directory contains `replay.json` (`replay-candidate/1`) and named model/reply/capability files from worker outputs, never case sources. Keep it outside installed resources and bench home.

`stages` lists ordered steps. `{"write":"model.json"}` copies without replacement, only in interpret/probe. Other stages use gateway tools. Arguments can reference assignment values with `{"$context":"model_dir"}` or the latest earlier result containing a key with `{"$result":"key"}`.

```sh
generative-driver --home /operator/state/benchmark benchmark run --case tq9-v2 --scenario original --executor replay --replay-candidate /private/candidates/tq9-v2 --password-file /private/evaluator/tq9-v2.password --renode /absolute/path/to/renode --wait
```

The worker reads only its candidate/assignment and uses recorded gateway calls. The configurator copies the candidate at start, hashes it before/after each stage and stops on tampering. Reports record candidate hash, `replay-emulation`, `model_benchmark: false`; score verifies those against the seal and compare refuses model/replay mixtures.

For suites, add `"replay_candidates":{"tq9-v2":"/private/candidates/tq9-v2",...}` for every case and use `--executor replay`. Suite freeze stores each validated candidate by hash; children get independent copies unaffected by later edits. Reports/comparisons retain candidate hashes and replay labels. Replay never measures model performance.

## What fresh reuse must establish

Fresh reuse gets only the package/objective and permitted operating data, calls its public interface and leaves the required state. For V2 families, equivalence comes from sealed final episodes on a fresh emulator after the run; mission is a separate check. Legacy TQ9 and BME280 use `recorded-reuse`, with no evaluator device contact: TQ9 temperature/output/disarm against its sealed goal, BME280 measurement against reference age/uncertainty limits. Host/operator failures before the goal or stale references leave results unscored.

A final grade never triggers repair. Retain uncertainty and reject incompatible saved workflows. Provided emulators run only during live calls, probe included, and are paused between calls. Pinned `time_policy.probe` text still says continuous time; that describes the case, not this provider. This is not real-time control. See Renode [time](https://renode.readthedocs.io/en/latest/advanced/time_framework.html) and [pause/start](https://renode.readthedocs.io/en/latest/basic/control.html).

## Run a repeated suite

The [Codex acceptance gate](../docs/codex-acceptance.md) requires `equivalent_and_mission` for every planned trial; absent mission evidence is not success. Separate equivalence/mission counts remain available.

Default: three families × three repetitions, sequentially; 10,800 seconds per child, 108,000 per suite. Set copied `repetitions` to `1` for three trials. Select settings/budgets before inference.

```sh
generative-driver --home /operator/state/suite configure --executor codex --command /absolute/path/to/codex --model YOUR_MODEL
generative-driver --home /operator/state/suite service start
python -c "from pathlib import Path; from generative_driver.benchmark import case_root; Path('pilot.json').write_text((case_root() / 'suites/development-pilot.json').read_text(encoding='utf-8'), encoding='utf-8')"
```

Create `/operator/private/suite-options.json` outside the checkout and worker directories. Its `home` must match the absolute global `--home`. Password/Renode paths go only to the detached harness on stdin, never suite tables or children:

```json
{
  "home": "/operator/state/suite",
  "renode": "/absolute/path/to/renode",
  "ghidra_home": "/absolute/path/to/ghidra",
  "java_home": "/absolute/path/to/java-home",
  "evaluator_password_files": {
    "tq9-v2": "/private/evaluator/tq9-v2.password",
    "sampled-sensor-v1": "/private/evaluator/sampled-sensor-v1.password",
    "parameter-store-v1": "/private/evaluator/parameter-store-v1.password"
  },
  "scoped_tool_approval": "emulator"
}
```

```sh
generative-driver --home /operator/state/suite benchmark suite start --manifest pilot.json --executor codex --options /operator/private/suite-options.json --request-id pilot-approved-1
generative-driver --home /operator/state/suite benchmark suite status SUITE_ID
generative-driver --home /operator/state/suite benchmark suite events SUITE_ID --after 0
generative-driver --home /operator/state/suite benchmark suite result SUITE_ID --offset 0 --limit 50
generative-driver --home /operator/state/suite benchmark suite report SUITE_ID --output reports/pilot-approved-1 [--audit-path PATH]...
generative-driver --home /operator/state/suite benchmark suite compare reports/before/suite.json reports/pilot-approved-1/suite.json
```

Save `suite_id`; identical request IDs reconnect. The harness provides devices and grades after runs; the configurator settles slots from recorded status/verdict. Unscored/pending grades or stops block the suite. `/3` exports store each trial's `scored` outcome (pass, failure, unknown, fault, flagged, running or not_run) and lead with a pass line such as `7/9 pass, 1 unknown, 2 faults, 12 planned`; `/2` exports keep their old counts (equivalent/planned, not-equivalent, unscored, stopped-by-category and unrun). Rows include scrubbed reason, stopped stage and repairs. Markdown adds frequent failing check IDs; status/events explain stops. `--audit-path PATH` (repeat for each path) reads the worker transcripts in the configurator home: a trial whose worker ran a command naming a forbidden path is flagged and not graded. Results are paginated; preserve every slot. Physical and gated results stay separate.

MCP offers suite start/status/events/result/cancel/resume; CLI exports/compares. Use the same owner for cancel and resume:

```sh
generative-driver --home /operator/state/suite benchmark suite cancel SUITE_ID
generative-driver --home /operator/state/suite benchmark suite resume SUITE_ID --options /operator/private/suite-options.json
```

After correction, the harness reattaches a stopped child or grades an unscored one once. Resume reads the password/Renode handles for the harness. Pre-harness suites are read-only.

Stopped time counts toward both budgets. Extend suite total with `--suite-budget-seconds`/`--budget-reason`, child total with `--child-budget-seconds`/`--child-budget-reason`; neither extends the other. Preserve prior attempts/costs.

## Scorecard and comparison

A scorecard measures a suite afterwards from the records its runs kept; the system under test records nothing for it, so a stored suite can be measured again. Every trial counts in the pass rate, and every trial with a run is measured, whatever its outcome.

```sh
generative-driver --home /operator/state/suite benchmark suite scorecard SUITE_ID --output reports/pilot-scorecard [--audit-path PATH]...
generative-driver --home /operator/state/suite benchmark suite compare reports/before/suite.json reports/after/suite.json [--leak-check reports/fresh/suite.json]
```

`scorecard` makes the export that `suite report` makes in a temporary folder and writes only `scorecard.json` and `scorecard.md` to `--output`. The Markdown opens with the pass line (and, for a replay suite, a second line saying no model ran), then gives the pass line of each group (`family`, and `level` and `framing` where the rows carry them), the repairs and fresh-reuse claims, and for each stage the attempts, wall and worker seconds, tokens, worker actions, managed calls and device use, summed over the trials, with medians for times and tokens. A sum that only some trials could measure shows how many (`n/N`); `unknown` means none could. A suite that ran a scoring set, a fixed sealed set of generated devices that every compared system runs, gets aggregates only and names no trial, case or run; any other suite adds a row of key, outcome and metrics per trial to `scorecard.json`. No scorecard quotes an audit finding.

`compare` pairs two reports of one scoring set device by device, whatever their protocols; `--leak-check` takes the after system's saved report on a fresh batch of devices (see [leak check](../CONTEXT.md)). Other reports compare as whole suites within one protocol.

Limits:

- After a repair, a stage's `wall_seconds` spans the time of the other stages in between.
- A stage skipped without an assignment has `wall_seconds` None.
- Resets without the trial's directory are a lower bound: they come from the configurator's events, plus the device provider's journal where the bench home still holds the directory.
- Worker actions are read only from Codex transcripts.
- Reading the configurator database leaves a `-shm` and an empty `-wal` file beside it in a quiescent home.

## Optional physical trial

Install `.[hardware,docs]`, USB support and the FTDI adapter from [setup](../docs/setup.md). Use explicit URL and vendor wiring. Acquire fetches the official [Bosch datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf).

```sh
generative-driver --home /operator/state/physical benchmark run --case bme280 --executor codex --password-file /private/evaluator/bme280.password --binding-json '{"url":"ftdi://SELECTED_ADAPTER/1"}' --approve-bound-device-tools
```

Ground uses operator observations when supplied. Reuse waits for an independent reference: temperature Celsius, humidity % RH, pressure pascals, uncertainties, `observed_at`, evidence path. Supply via `driver_respond` or `respond RUN_ID --observation reference.json`, then resume promptly. Instruments share a stable environment; stale references leave the trial unscored. Agreement within uncertainty is not absolute calibration.

The harness authenticates before starting, never touches the board and grades recorded reuse after completion, extending its wait with run budgets. If it stopped following first, `benchmark trial-resume TRIAL_DIRECTORY --password-file FILE` grades the completed run once.

Assistive physical scoring uses its `run-evidence.enc`, sealing reference/measurement and evidence hash; the evidence file remains accepted. Earlier physical reports instead use saved `benchmark/reference-reuse-<attempt>.*` with matching digest/time window:

```bash
generative-driver --home /operator/state/physical benchmark score reports/physical.json --evidence /private/evaluator/saved-reference.txt --password-file /private/evaluator/bme280.password
```

## Metrics and comparisons

Reports retain stage attempts/acceptance, final verdict and package/mapping hashes, budgets, runtime/provider/model/settings, toolchain/skill/case/evaluator identities, environment, time, managed calls, usage and interventions. Artifact and notes hashes are sealed in the trial's run evidence; `driver_result` returns the notes. Tool time overlaps worker time; managed counts exclude arbitrary shell calls. Missing usage stays unknown, failed attempts consume resources.

Current assistive run reports record equivalence, and a pass is the evaluator's graded equivalence verdict; the fresh mission is reported beside it and does not change it, so corpus success needs neither it nor the six accepted handoffs. Earlier gated results retain their original acceptance rules and remain a separate series. Preserve failures, exclusions, blocked and unrun slots. Corpus measured success uses only graded passes/failures; selected coverage retains all selected slots and demonstrated coverage retains the entire catalog. Match versions, inputs, modes, seeds, time policies and budgets; label multi-dimension changes as system comparisons. Report repetitions, uncertainty and time/usage coverage. [Verification](../docs/verification.md) records the completed three-trials-per-profile baseline and multiline-repair comparison, with model results separate from software/reference evidence.


## Verification evidence

[docs/verification.md](../docs/verification.md) is the single narrative record for measured plugin results, methods and limits. Frozen JSON indices live in [bench/evidence/](evidence/): baseline and multiline comparison, native Modbus, corpus/lifecycle validation and historical qualification. The source distribution includes these indices. They retain their original bytes, hashes and historical path fields; relocating an index does not revise its experiment. Raw captures, evaluator material, source/build trees and private archives remain outside the repository. Superseded research notes and implementation checklists remain recoverable from Git history.

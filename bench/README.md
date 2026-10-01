# Benchmark

Measure whether a recovered driver produces the required outputs and effects. Candidate code may differ from the reference. A stage report is a claim; accepted artifacts and independent evaluator verdicts are recorded separately.

## Profiles

| Case | Execution | What it establishes |
|---|---|---|
| `setup-smoke` | Recorded replay, scripted | Installation, model validation, probe recording, emission, relocation and standalone package replay. No model performance claim. |
| `tq9` | Actual stage agents + owned firmware in Renode | All seven roles, numeric decoding and independent timer effects, fresh package-only reuse, seeded identity drift, repair/requalification and second fresh reuse. |
| `tq9-v2` | Calibrated native evaluator + independently configured stage agents | Versioned semantic, control and identity scenarios; diagnostic repair, sealed private final checks and independently evidenced maintenance. |
| `bme280` | Optional actual agents + physical sensor | Datasheet recovery, FTDI I2C execution, independent operator reference, portable package, fresh reuse and response to an independently measured ambient change. No injected physical firmware drift. |

Canonical machine assets are installed from [`src/generative_driver/resources/bench`](../src/generative_driver/resources/bench/). This directory holds instructions and selected reports, avoiding duplicate inputs. Case manifests pin firmware, encrypted evidence and evaluator version. No vendor PDF or historical research run is bundled.

## Install and check

Follow [macOS/Windows and runtime setup](../docs/setup.md), or [Codex](../docs/codex.md) / [Goose](../docs/goose.md). Python 3.11+ is required. Run from the same Python environment used for installation:

```sh
python -m pip install -e .
python -m generative_driver doctor
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m unittest discover -s tests -v
```

Choose a new output directory. The smoke report must say `execution: scripted-replay`, `model_benchmark: false`, and `verdict: passed`. It never opens hardware or calls a paid model. Run `python -m driver replay` from the emitted `relocated package` directory to inspect its standalone replay.

## Actual TQ9 trial

Install a native [Renode release](https://github.com/renode/renode/releases), [Ghidra](https://github.com/NationalSecurityAgency/ghidra/releases), and the Java version required by that Ghidra release. This release was calibrated with Renode 1.16.1, Ghidra 12.1.3 and Java 21 on macOS arm64. Native Windows uses its own executable paths; physical and native Windows emulator execution remain separately qualified in the [support matrix](../docs/support.md). Containers are optional; the commands below use native tools.

Authenticate the desired Codex or Goose runtime using its normal setup, then configure it:

```sh
python -m generative_driver --home "benchmark state" configure --executor codex --command "/absolute/path/to/codex" --provider openai --model YOUR_MODEL
python -m generative_driver benchmark run --case tq9 --executor codex --home "benchmark state" --budget-seconds 10800 --password-file "/private/evaluator/tq9.password" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home"
```

On Windows use `python.exe`, the installed `Renode.exe`, the Ghidra directory and Java home directory; quote paths with spaces. Use `--executor goose` after configuring Goose to select that runtime. The evaluator password is obtained separately as described in [groundtruth](groundtruth/README.md). It must be outside the repository, candidate inputs and worker environment. An encrypted bundle without its password cannot run the independent emulator evaluator.

For a noninteractive Codex worker, explicitly authorize the scoped TQ9 emulator tools by adding `--approve-emulator-tools` when you start the trial. The equivalent MCP option is `scoped_tool_approval: "emulator"`. This is limited to the TQ9 emulator profile and its permitted tools; it is not approval for physical hardware. Without that explicit authorization a runtime may stop for a tool approval.

The call returns a durable `run_id`. Closing the UI does not stop the configurator. The same run can be inspected through either UI using `driver_status`, `driver_result`, or these commands:

```sh
python -m generative_driver --home "benchmark state" status RUN_ID
python -m generative_driver --home "benchmark state" result RUN_ID
python -m generative_driver --home "benchmark state" report RUN_ID --output "reports/trial.json"
python -m generative_driver benchmark score "reports/trial.json" --password-file "/private/evaluator/tq9.password"
python -m generative_driver benchmark compare "reports/before.json" "reports/after.json"
```

From Goose or Codex connected to this repository's MCP, call `driver_benchmark_run` with `profile: "tq9"`, `executor`, and `options` containing `budget_seconds`, `evaluator_password_file`, `renode`, `ghidra_home`, `java_home`. The host owns those options; it does not put the password or answer source into worker assignments. UI setup alone does not configure optional analysis tools.

Windows MCP sessions require the [independently started configurator](../docs/setup.md#start-the-configurator). Start it for the same state directory from a standalone PowerShell window before connecting the interface. An MCP benchmark uses its connection's state directory so its status, cancellation and resume tools can manage the returned run; connect a separately configured MCP server to use another directory. The recorded installation smoke profile still needs no background service.

TQ9 interpretation receives the binary and generic sealed analysis kit. Original UART settings are retained and separately scored against source evidence. An explicit adaptation connects the recovered interface to Renode's UART TCP socket; successful TCP execution does not measure actual serial baud or pins. The independent monitor reads output timer state through a separate connection. Grounding proves emulated effects, not a physical actuator. Renode's SysTick frequency follows the firmware reset clock. During fresh package reuse and maintenance, virtual time is paused between calls to exclude agent thinking and evaluator bookkeeping; it runs during each package operation. This is not a real-time control benchmark. Renode documents its [time framework](https://renode.readthedocs.io/en/latest/advanced/time_framework.html) and [pause/start controls](https://renode.readthedocs.io/en/latest/basic/control.html).

The configurator allows two bounded repairs for evidenced model defects. Operator/host faults stop for correction. Maintenance changes only firmware identity in this version, detects the old package's refusal, and requires new interpretation, probing, grounding, emission and fresh reuse. Unknown semantic drift is a future case version.

Ground receives an evidence directory containing the accepted candidate model, live transaction records with decoded units, and independently observed output duty. Permission refusal is the host's rejection before I/O. New captures retain monitor register counts and responses; legacy captures explicitly disclose when only measured duty scalars were retained. Ground receives observations and their provenance, without private reference answers.

### Continuing a stopped trial

Ordinary resume retains the original deadline. The budget is total wall time from the original start, including stopped time. If the user explicitly authorizes more time, record the increased total and its reason:

```sh
python -m generative_driver --home "benchmark state" resume RUN_ID --budget-seconds 21600 --budget-reason "User-approved continuation after pause"
```

This example sets a six-hour total; it does not add six hours. Prior attempts, accepted evidence and the budget extension remain in the same run. See [recovery](../docs/setup.md#recovery) for equivalent UI fields. Reports and comparisons must retain the changed budget and intervention history when interpreting performance.

## Optional physical BME280 trial

Install the `hardware` and `docs` extras and the host USB driver described in [setup](../docs/setup.md). Wire your supported FTDI I2C adapter and BME280 according to their own documentation, select its explicit FTDI URL, and provide independent temperature, humidity and pressure reference instruments. The official [Bosch datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf) is downloaded by acquire and hash checked; a changed vendor document needs a reviewed registry update.

```sh
python -m pip install -e ".[hardware,docs]"
python -m generative_driver benchmark run --case bme280 --executor codex --home "physical state" --password-file "/private/evaluator/bme280.password" --binding-json '{"url":"ftdi://SELECTED_ADAPTER/1"}'
```

PowerShell users can put the same binding in the MCP `options.binding` object to avoid shell quoting. For a noninteractive worker, explicitly authorize this selected device and its configured effects with `--approve-bound-device-tools` (MCP `scoped_tool_approval: "bound-device"`). That approval applies only to the BME280 profile, its supplied binding and assigned tools; it does not permit arbitrary devices. The run requires an explicit adapter; it never chooses the first USB device. The profile grants bounded model register writes required for sensor setup. No physical run is performed by setup smoke.

At ground and maintain the run stops and requests independent reference observations. Create a JSON file with this shape, using actual measurements and timestamp rather than these placeholders:

```json
{
  "stage": "ground",
  "channel": "Independent meters: identifiers and calibration scope",
  "observed_at": 1790000000,
  "reference": {
    "temperature": {"value": 22.0, "absolute_tolerance": 1.0},
    "humidity": {"value": 45.0, "absolute_tolerance": 3.0},
    "pressure": {"value": 101000.0, "absolute_tolerance": 300.0}
  },
  "evidence_path": "/absolute/path/to/reference-photo-or-log"
}
```

Units are Celsius, percent RH and pascals. The reference must be fresh (within ten minutes) and uncertainty bounded by the encrypted case criteria. Keep the sensor and reference instruments in the same stable environment. Submit through `driver_respond` or:

```sh
python -m generative_driver --home "physical state" respond RUN_ID --observation "reference.json"
python -m generative_driver --home "physical state" resume RUN_ID
```

The evaluator takes a fresh sensor sample and compares it with the operator reference. The worker cannot supply its own reference. BME280 case/evaluator version 2 requires a stimulus at maintain: choose a safe ambient change yourself, independently measure it, and submit `stage: "maintain"`. On at least one channel, the new reference must differ from the initial reference by more than the sum of their stated uncertainties. The evaluator then requires a fresh sensor reading to follow the new reference. An unchanged environment cannot establish this gate, and an unchanged constant output fails the changed-reference check. The agent does not induce a physical change. Recorded agreement is within the supplied reference uncertainty; it is not an absolute sensor calibration claim. The optional physical flow is implemented but has no new physical baseline in this release.

## Metrics and comparisons

Reports include runtime/provider/model/settings, toolchain and skills hashes, case/evaluator versions, firmware and recovered-model/package hashes, environment, budget, per-stage attempts and evaluator checks, overall wall time, worker time, tool calls/time, human inputs and reported tokens. Worker tool time is included in worker time; managed evaluator tool time is reported separately. Overall wall time is measured directly, and component durations overlap. Missing usage remains unknown, with measured coverage shown. Retries and failed attempts remain visible.

Managed tool counters cover configurator gateway calls. Native agent shell and analysis calls are included in worker elapsed time but not those counters.

Reports distinguish operator messages/observations from recorded authorization, cancellation, resume and budget events; event counts are not counts of unique people. Original and effective budgets are explicit. Wall time includes stopped intervals; no pause-adjusted active time is inferred. Source, skills and environment fields describe the exporting installation. Trials executed across different installations need execution snapshot history to attribute earlier attempts correctly.

The current report's `seed: 0` is fixed case bookkeeping. No model sampling seed is configured, and repeated agent runs can differ.

Overall success requires every declared acceptance gate. A strong average cannot hide failed grounding, omitted reuse or undetected drift. Unrun, failed, blocked and inapplicable roles remain explicit. Compare the same case, evaluator, seed and execution mode. A changed provider/model and compiler together is labelled `combined-system`; it cannot isolate a model improvement. Replay cannot be compared as an actual agent trial.

For a study, run three fresh trial IDs per configuration with identical budgets and case assets; preserve each report, report success count and median time/usage for completed measurements, and retain failures separately. Each `run` is an explicit inference invocation. The first authorized development baseline is one trial, not a repeated study. Runtime-reported usage is not a bill or an estimated token count.

The [selected Codex development baseline](baselines/codex-tq9-2026-09-28.md) includes the original workflow, firmware drift and repair, stage/overall measurements, failed attempts and source snapshot history.

## Versioned TQ9

`tq9` retains its legacy inputs and scoring. Discover registered cases with `python -m generative_driver benchmark cases`. For `tq9-v2`, use the native run command above with `--case tq9-v2 --scenario semantic --case-seed 0` and its separately supplied evaluator password. Scenarios are `semantic`, `control` and `identity`. The seed rotates independent reset-delimited temperature episodes; it does not control model sampling or remove checks. Stateful effect steps retain their order.

Both CLI and direct configurator admission verify the encrypted native calibration against the current evaluator code, dependencies, images and private inputs. A public `passed` label is insufficient. Changed evaluator code requires calibration again. See [evaluator commands](groundtruth/README.md#v2-calibration).

Diagnostic observations may inform repair. Final checks execute after the submitted package is frozen, stay in encrypted evidence and cannot trigger another repair attempt. Fresh workers must separately complete their visible package-only mission. Maintenance claims (`unchanged`, `drift`, or `unknown`) cite current worker evidence; independent evaluator evidence determines credit. Unknown claims receive no detection credit. Lost native process ownership or interrupted scenario application requires operator reconciliation; no PID-only cleanup or uncertain replay occurs.

Offline v2 scoring requires both explicit paths and does not launch tools or workers:

```sh
python -m generative_driver benchmark score "reports/trial.json" --evidence "/private/evaluator/run-evidence.enc" --password-file "/private/evaluator/tq9-v2.password"
```

The Python adapter accepts `score(report, password_file=None, *, evidence_path=None)` with the same explicit-sidecar requirement. Calibration/reference executions and scripted Controller tests are evaluator checks, not measurements of model performance.

## Additional firmware families

`sampled-sensor-v1` tests fresh acquisition, framed signed readings and sequence counters. `parameter-store-v1` tests committed and pending values, bank isolation, generation, transaction ordering and abort. Both expose semantic-change and unchanged-control scenarios through the shared evaluator and UART-over-TCP binding. Discover their current calibration status with `benchmark cases`; agent preflight rejects pending or stale records before service, device or worker startup.

Qualification uses two independently authored references per family, native binary rebuilds, actual Ghidra import/decompile, native Renode observations and required behavioral mutants. Final episodes execute frozen emitted packages. False-drift rejection requires correct native control observations and a rejected changed maintenance claim. [Private calibration commands](groundtruth/README.md#authored-family-calibration) retain exact stimuli, replies and failed-check details in evaluator evidence.

The available pilot scope is three independently authored families on one emulated STM32 platform, with six paired semantic/control entries. UART-over-TCP does not establish wire baud correctness; independent emulator observations provide no physical grounding. Reference calibration measures evaluator correctness. No model-performance pilot is implied by qualification, and native macOS observations do not qualify native Windows execution.

Historical calibration at `aee512e` passed on macOS arm64: 13 sensor runs, 14 store runs and 32 TQ9-v2 runs. Subsequent evaluator changes make those records stale; current qualification is pending and admission remains closed until the final integration native refresh. See the [calibration record](../docs/verification.md#historical-native-family-calibration--2026-10-01). These results do not replace a separately authorized model pilot.

## Durable benchmark suites

The installed `development-pilot.json` plans 18 fresh trials: three firmware families × two paired semantic/control scenarios × three repetitions, one child at a time. It gives each child 10,800 wall-clock seconds and the suite 216,000 seconds. These are explicit experiment conditions; running the pilot requires current native reference qualification and user authorization for the selected runtime/model, trial count, budgets and assigned emulator tools. Current native calibration remains pending as of 2026-10-01. The commands below describe execution after those gates are met.

Select and configure the runtime in the same home used by CLI and MCP. On Windows, start the owner from a standalone PowerShell window before connecting Goose or Codex:

```sh
python -m generative_driver --home "suite state" configure --executor codex --command "/absolute/path/to/codex" --model YOUR_MODEL
python -m generative_driver --home "suite state" service start
python -m generative_driver benchmark cases
python -c "from pathlib import Path; from generative_driver.benchmark import case_root; Path('pilot.json').write_text((case_root() / 'suites/development-pilot.json').read_text(encoding='utf-8'), encoding='utf-8')"
python -c "from pathlib import Path; print(Path('pilot.json').read_text(encoding='utf-8'))"
```

Use native `.exe` paths on Windows. To select Goose, configure its command, model and provider, then start with `--executor goose`. For a six-trial development run, change only `repetitions` to `1` in the copied manifest before authorization; the three families and paired scenarios remain. `case_seed` identifies the case scenario, separately from model sampling.

Create an operator-owned `suite-options.json` with native tool paths and separately obtained evaluator password file handles:

```json
{
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

Keep these handles outside candidate inputs; the evaluator owns them. Password encryption protects truth at rest and does not provide worker filesystem isolation. Public selection preflight checks registration, execution, scenario/seed, full workflow, one evidence track and calibration status before a possible owner start. The owner checks runtime configuration and authenticated calibration before child execution. A retried request to a reachable owner returns its frozen saved suite before rereading mutable registry assets.

```sh
python -m generative_driver --home "suite state" benchmark suite start --manifest "pilot.json" --executor codex --options "suite-options.json" --request-id "pilot-approved-1"
python -m generative_driver --home "suite state" benchmark suite status SUITE_ID
python -m generative_driver --home "suite state" benchmark suite events SUITE_ID --after 0
python -m generative_driver --home "suite state" benchmark suite result SUITE_ID --offset 0 --limit 50
python -m generative_driver --home "suite state" benchmark suite cancel SUITE_ID
python -m generative_driver --home "suite state" benchmark suite resume SUITE_ID
python -m generative_driver --home "suite state" benchmark suite report SUITE_ID --output "reports/pilot-approved-1"
python -m generative_driver benchmark suite compare "reports/before/suite.json" "reports/pilot-approved-1/suite.json"
```

Save the returned `suite_id`. Retrying the same `request-id` and submitted inputs reconnects to that suite. Status exposes `active_child_id`; result pages expose public trial summaries and frozen experiment metadata. Follow `next_offset` until null; the limit is 1–100. Events use their returned cursor. Closing a CLI or MCP client keeps the owner and child running. Cancellation and host/operator blockers preserve the active slot for explicit resolution; model failures remain measured failed trials.

Both budgets include stopped wall time from their original starts. Correct the blocker, then explicitly authorize any larger total. A suite extension uses `benchmark suite resume SUITE_ID --suite-budget-seconds 259200 --budget-reason "User-approved suite continuation"`. Extend an exhausted child separately with `resume RUN_ID --budget-seconds 21600 --budget-reason "User-approved child continuation"`, then resume the suite. Neither extension resets start time, prior attempts or consumed resources; child overrides become comparison conditions. An uncertain device effect still requires reconciliation.

MCP exposes `driver_benchmark_suite_start(manifest, executor, options, request_id)` and the corresponding `status`, `events`, `result`, `cancel` and `resume` tools. All use the connection's configured owner home; an equivalent `options.home` is stripped, and another resolved home is refused. Windows MCP never starts an owner, including when it disappears after a successful ping. Use CLI for suite export and saved comparison.

Report requires an explicit output directory and a reachable existing owner. It writes `suite.json`, `report.md` and relative allowlisted trial reports; private options, password handles, source/monitor data and raw transcripts are excluded. Incomplete suites produce provisional reports: all planned slots remain in the denominator, failed resources remain visible, and missing usage stays unknown. CLI export uses checked owner records; the Python export API can instead select authenticated regrade with `evidence_files` keyed by trial key and `password_files` keyed by case ID. Saved comparison runs offline without an owner, model process or device connection. Model/toolchain dimensions may differ; changed manifest, pins, evidence scope, time or effective budgets refuse a paired claim. Multiple dimension changes are labelled a combined-system comparison. Physical cases cannot enter these emulated aggregates.

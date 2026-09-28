# Benchmark

Measure whether a recovered driver produces the required outputs and effects. Candidate code may differ from the reference. A stage report is a claim; accepted artifacts and independent evaluator verdicts are recorded separately.

## Profiles

| Case | Execution | What it establishes |
|---|---|---|
| `setup-smoke` | Recorded replay, scripted | Installation, model validation, probe recording, emission, relocation and standalone package replay. No model performance claim. |
| `tq9` | Actual stage agents + owned firmware in Renode | All seven roles, numeric decoding and independent timer effects, fresh package-only reuse, seeded identity drift, repair/requalification and second fresh reuse. |
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

TQ9 interpretation receives the binary and generic sealed analysis kit. Original UART settings are retained and separately scored against source evidence. An explicit adaptation connects the recovered interface to Renode's UART TCP socket; successful TCP execution does not measure actual serial baud or pins. The independent monitor reads output timer state through a separate connection. Grounding proves emulated effects, not a physical actuator. Renode's SysTick frequency follows the firmware reset clock. During fresh package reuse and maintenance, virtual time is paused between calls to exclude agent thinking and evaluator bookkeeping; it runs during each package operation. This is not a real-time control benchmark. Renode documents its [time framework](https://renode.readthedocs.io/en/latest/advanced/time_framework.html) and [pause/start controls](https://renode.readthedocs.io/en/latest/basic/control.html).

The configurator allows two bounded repairs for evidenced model defects. Operator/host faults stop for correction. Maintenance changes only firmware identity in this version, detects the old package's refusal, and requires new interpretation, probing, grounding, emission and fresh reuse. Unknown semantic drift is a future case version.

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

Reports distinguish operator messages/observations from recorded authorization, cancellation, resume and budget events; event counts are not counts of unique people. Original and effective budgets are explicit. Wall time includes stopped intervals; no pause-adjusted active time is inferred. Source, skills and environment fields describe the exporting installation. Trials executed across different installations need execution snapshot history to attribute earlier attempts correctly.

Overall success requires every declared acceptance gate. A strong average cannot hide failed grounding, omitted reuse or undetected drift. Unrun, failed, blocked and inapplicable roles remain explicit. Compare the same case, evaluator, seed and execution mode. A changed provider/model and compiler together is labelled `combined-system`; it cannot isolate a model improvement. Replay cannot be compared as an actual agent trial.

For a study, run three fresh trial IDs per configuration with identical budgets and case assets; preserve each report, report success count and median time/usage for completed measurements, and retain failures separately. Each `run` is an explicit inference invocation. The first authorized development baseline is one trial, not a repeated study. Runtime-reported usage is not a bill or an estimated token count.

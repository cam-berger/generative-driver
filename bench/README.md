# Benchmark

Measure whether an agent can recover a device interface, emit a portable driver, and enable a different agent to use the desired functions. Every agent run ends at **acquire → interpret → probe → ground → emit → reuse**. Candidate code may differ from reference code; outputs, effects and state transitions determine compatibility.

## Profiles

| Case | Execution | What it checks |
|---|---|---|
| `setup-smoke` | Scripted recorded replay | Installation, model validation, probe recording, package emission, relocation and standalone replay. |
| `tq9` | Configured agents against owned firmware in Renode | Numeric decoding, independently observed timer effects and package-only fresh reuse. |
| `tq9-v2` | Configured agents with a calibrated native evaluator | Diagnostic repair, sealed final behavior and a fresh worker operating the frozen package. |
| `sampled-sensor-v1` | Configured agents with a calibrated native evaluator | Fresh acquisition, framed signed readings, sequence counters and fresh reuse. |
| `parameter-store-v1` | Configured agents with a calibrated native evaluator | Pending/committed values, bank isolation, generation, transaction order, abort and fresh reuse. |
| `bme280` | Optional physical sensor and configured agents | Datasheet recovery, explicit FTDI I2C binding, independent reference observations and fresh package use. |

Canonical assets are installed from [`src/generative_driver/resources/bench`](../src/generative_driver/resources/bench/). Each firmware trial uses one stable original image. Manifests pin inputs, encrypted groundtruth and evaluator versions. Source and private answers stay evaluator-owned. See [groundtruth setup](groundtruth/README.md) and [observed verification](../docs/verification.md).

The current research core has three firmware families and nine planned trials. The [benchmark growth plan](../docs/research/2026-10-03-benchmark-growth-plan.md) proposes a 12-lineage pilot, a first comparison on 30 untouched lineages, and a mature corpus of approximately 100 lineages and 200 missions. Its [source review](../docs/research/2026-10-03-benchmark-source-review.md) identifies existing firmware collections to screen; these targets are not yet admitted cases or measured model results.

## Install and check

Follow [macOS/Windows setup](../docs/setup.md), [Codex](../docs/codex.md) or [Goose](../docs/goose.md). Python 3.11+ is required. Use the same Python environment throughout:

```sh
python -m pip install -e ".[dev]"
python -m generative_driver doctor
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m unittest discover -s tests -v
```

Choose a new output directory. Smoke must report `execution: scripted-replay`, `model_benchmark: false` and `verdict: passed`. It uses no agent account, hardware, native analysis tools or evaluator password. From the emitted `relocated package` directory, `python -m driver replay` exercises its standalone replay.

## Run an agent benchmark

Install native [Renode](https://github.com/renode/renode/releases), [Ghidra](https://github.com/NationalSecurityAgency/ghidra/releases) and the Java version required by Ghidra. Native reference setup uses Renode 1.16.1, Ghidra 12.1.3 and Java 21 on macOS arm64. Containers are optional. Native Windows uses its installed `.exe` paths and remains a separate qualification target.

Authenticate Codex or Goose through that runtime's normal setup. Configure the exact executable, provider and model, then explicitly select the trial and its total wall-clock budget:

```sh
python -m generative_driver --home "benchmark state" configure --executor codex --command "/absolute/path/to/codex" --provider openai --model YOUR_MODEL
python -m generative_driver benchmark cases
python -m generative_driver benchmark run --case tq9-v2 --scenario original --case-seed 0 --executor codex --home "benchmark state" --budget-seconds 10800 --password-file "/private/evaluator/tq9-v2.password" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --approve-emulator-tools
```

`--approve-emulator-tools` explicitly authorizes the selected emulator's assigned tools for noninteractive workers. The MCP equivalent is `scoped_tool_approval: "emulator"`. This approval does not authorize physical devices. To use Goose, configure and select `--executor goose`. For another firmware family, select its case and evaluator handle. V2-family trials use `original`; `case_seed` varies independent test episodes and is separate from model sampling.

Interpretation receives the supplied analysis-tool paths and emulator wiring. Installed Ghidra/Java may run outside the candidate workspace; projects, caches, logs and exports stay inside it. The executable model uses the supplied TCP byte stream. Inferred or unresolved physical UART settings belong in the notes; TCP success does not verify baud, framing or pins.

Live probe calls return independent diagnostic observations with units alongside candidate outputs. Workers compare those readings and effects, preserve partial capability mappings and use `needs_revision` for model discrepancies. The configurator checks diagnostics and permits at most two returns to interpretation with observed feedback. Missing host tools, operator input or permission remain blockers. Final package grading stays frozen and cannot trigger another repair. An owned emulator can be replaced after every unresolved effectful dispatch has a trusted completed model-failure outcome, the original process is confirmed stopped, and independent observations verify a distinct replacement at its initial state. The configurator records the recovery and uses the existing repair budget. Failed write evidence remains failed; attached sessions, unknown/host/operator faults, cancellation and unverified replacement remain blocked.

Probe inputs include `CAPABILITY_FORMAT.md` and a standalone `validate_capabilities.py`. Run the helper against the candidate model and mapping before submission. Field-specific mapping errors return to interpretation without diagnostic device I/O. A trusted argument or grant refusal before transport opens does not create an uncertain device effect; failed opening or interrupted execution still requires reconciliation.

Interpretation and probing receive `TASKS.json` with requested behaviors, canonical outputs/units and configured effect grants. A repair also receives `REPAIR_CONTEXT.json`, `DEFECTS.json` and sealed copies of this run's earlier candidate, replies, mappings and observed evidence. These supplied diagnostic measurements are authorized repair inputs. The agent revises root outputs while preserving prior evidence; evaluator passwords, plaintext answer keys and hidden final answers remain excluded. Grounding receives the accepted mapping, transaction captures and diagnostic check results, and a specific model discrepancy can use the remaining repair budget.

The parameter-store contract requires update to discard pending edits in either bank, commit only the requested cell, preserve every other committed cell, advance generation once and end without pending state. Diagnostic and final evaluation always cover idle update, pending updates in the same and different banks, stage/commit, stage/abort and complete rejection. Full ending-state observations prevent exploratory probe selection from omitting that coverage.

Admission authenticates calibration against current evaluator code, dependencies, inputs and images before a trial starts. A stale or pending case cannot launch a worker or native device. Obtain passwords separately and keep their files outside candidate inputs and the checkout. Encryption and pinned hashes provide evaluator separation and provenance; they do not enforce a worker filesystem sandbox.

Save the returned `run_id`. The background configurator continues when the interface disconnects:

```sh
python -m generative_driver --home "benchmark state" status RUN_ID
python -m generative_driver --home "benchmark state" result RUN_ID
python -m generative_driver --home "benchmark state" report RUN_ID --output "reports/trial.json"
python -m generative_driver benchmark score "reports/trial.json" --evidence "/private/evaluator/run-evidence.enc" --password-file "/private/evaluator/tq9-v2.password"
python -m generative_driver benchmark compare "reports/before.json" "reports/after.json"
```

V2 offline scoring requires the explicit encrypted evidence sidecar; it does not launch a worker or native tool. Preserve accepted artifacts and their hashes when archiving a trial. Legacy `tq9` uses its case-specific score without the v2 sidecar argument.

From connected Goose/Codex MCP, use `driver_benchmark_run` with `profile`, `executor` and `options` containing `scenario_id: "original"`, `case_seed`, `budget_seconds`, `evaluator_password_file`, `renode`, `ghidra_home`, `java_home` and `scoped_tool_approval`. Inspect through `driver_status`, `driver_events` and `driver_result` using the saved ID. On Windows, first start the configurator for the same state directory in a standalone PowerShell window; see [service setup](../docs/setup.md#start-the-configurator).

## What fresh reuse must establish

The reuse worker receives the emitted package and its objective without the discovery conversation or workspace. It must call the package's published interface, perform the desired device operations and leave the device in the declared final state. Its own completion claim is checked against observed calls and effects. For the calibrated V2 families, the evaluator then runs independent sealed final episodes against the frozen package; both the mission and final behavior must pass. Legacy TQ9 uses its recorded temperature, output and disarm checks. Physical BME280 compares the fresh measurement with an operator reference within supplied uncertainty.

Diagnostic observations may inform up to two bounded model repairs before final grading. Host/operator faults stop for correction. Failed frozen final checks are terminal. Interrupted writes remain uncertain until explicitly reconciled. Unsupported saved workflow state is rejected; old measurements are never relabelled as current results.

UART-over-TCP does not establish physical baud or pin correctness. Independent emulator observations establish emulated effects. Virtual time runs during operations and pauses between package calls, so agent thinking time does not advance device state. This is not a real-time control benchmark. Renode documents its [time framework](https://renode.readthedocs.io/en/latest/advanced/time_framework.html) and [pause/start controls](https://renode.readthedocs.io/en/latest/basic/control.html).

## Run a repeated suite

The installed `development-pilot.json` plans **nine fresh trials: three firmware families × three repetitions**, one child at a time. Each child has 10,800 wall-clock seconds; the suite has 108,000 seconds. Review these conditions and select the authenticated runtime/model before invoking inference. For a three-trial development run, change the copied manifest's `repetitions` to `1`.

```sh
python -m generative_driver --home "suite state" configure --executor codex --command "/absolute/path/to/codex" --model YOUR_MODEL
python -m generative_driver --home "suite state" service start
python -c "from pathlib import Path; from generative_driver.benchmark import case_root; Path('pilot.json').write_text((case_root() / 'suites/development-pilot.json').read_text(encoding='utf-8'), encoding='utf-8')"
```

Create an operator-owned `suite-options.json`:

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

```sh
python -m generative_driver --home "suite state" benchmark suite start --manifest "pilot.json" --executor codex --options "suite-options.json" --request-id "pilot-approved-1"
python -m generative_driver --home "suite state" benchmark suite status SUITE_ID
python -m generative_driver --home "suite state" benchmark suite events SUITE_ID --after 0
python -m generative_driver --home "suite state" benchmark suite result SUITE_ID --offset 0 --limit 50
python -m generative_driver --home "suite state" benchmark suite report SUITE_ID --output "reports/pilot-approved-1"
python -m generative_driver benchmark suite compare "reports/before/suite.json" "reports/pilot-approved-1/suite.json"
```

Save `suite_id`; a repeated request ID with the same inputs reconnects to the frozen suite. Results are paginated and preserve every planned slot. MCP provides `driver_benchmark_suite_start` and corresponding status/events/result/cancel/resume tools through the same owner. Use CLI to export reports and compare saved results offline. Physical cases remain separate from emulated suite aggregates.

To stop, use `benchmark suite cancel SUITE_ID`; after resolving the cause, use `benchmark suite resume SUITE_ID`. Both child and suite budgets include stopped wall time. A larger total requires an explicit recorded budget extension; prior attempts and consumed resources remain visible. Extend child and suite budgets separately using their resume commands and a `--budget-reason`.

## Optional physical trial

Install `.[hardware,docs]`, the host USB driver and supported FTDI adapter described in [setup](../docs/setup.md). Select its explicit FTDI URL and wire the sensor according to vendor documentation. Acquire fetches the pinned official [Bosch datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bme280-ds002.pdf).

```sh
python -m generative_driver benchmark run --case bme280 --executor codex --home "physical state" --password-file "/private/evaluator/bme280.password" --binding-json '{"url":"ftdi://SELECTED_ADAPTER/1"}' --approve-bound-device-tools
```

At ground, provide fresh independent temperature, humidity and pressure observations with uncertainty bounds and an evidence path through `driver_respond` or `respond RUN_ID --observation reference.json`. Use Celsius, percent RH and pascals, and keep the reference instruments in the same stable environment. The reference is operator-owned; the candidate worker cannot provide its own answer key. Agreement is bounded by the supplied reference uncertainty. Fresh reuse requires a current independent reference and checks the emitted package against the selected sensor; it is the final stage. No physical performance result is implied by installation or scripted tests.

## Metrics and comparisons

Reports retain six-stage attempts, accepted handoffs, independent verdicts, original/effective budgets, runtime/provider/model/settings, toolchain/skill/case/evaluator identities, environment, model/package hashes, time, managed tool calls, reported usage and interventions. Worker tool time overlaps worker elapsed time; overall wall time is measured directly. Missing usage stays unknown; failed attempts consume measured resources. Managed tool counters do not count arbitrary agent shell calls.

Overall success requires every declared stage, the fresh mission and final behavior. Preserve failed, blocked and unrun trials. Compare matching case/evaluator versions, stable inputs, execution mode, seeds, time policies and budgets. Multiple changed model/runtime/toolchain dimensions are labelled a combined-system comparison. Replay and native reference qualification are separate from model performance.

Run repeated fresh IDs per configuration, retain individual outcomes, report success counts and uncertainty, and summarize time/usage with coverage. Current local qualification and its limits are recorded in [verification](../docs/verification.md). This repository's software and reference checks do not imply that the nine-trial model study has run.

Exported physical reports omit private absolute paths. Preserve the saved `benchmark/reference-reuse-<attempt>.*` file with the report in evaluator-owned storage. Supply that file explicitly when scoring from any directory; its bytes must match the report's pinned digest, and the recorded measurement must remain within the reference time window. The handle changes only file resolution, never reference values or acceptance criteria.

```bash
python -m generative_driver benchmark score "reports/physical.json" --evidence "/private/evaluator/saved-reference.txt" --password-file "/private/evaluator/bme280.password"
```

# Upstream firmware corpus

The software-first pilot tracks seven upstream firmware lineages. Its published inventory contains one upstream case with native qualification evidence, `zephyr-modbus-v1`, and six blocked lineages. A lineage is a source ancestry and mission; an executable case is a registered, versioned image and evaluator. Public availability is only a metadata check. Authenticate qualification against the installed code before running agents.

The three synthetic development cases (`tq9-v2`, `sampled-sensor-v1`, `parameter-store-v1`) remain in their separate suite. They require fresh native qualification for the exact evaluator/runtime implementation before new trials. Source intake, evaluator qualification and measured model performance are separate evidence. See the [intake record](research/2026-10-26-corpus-intake.md), [architecture decision](research/2026-10-26-benchmark-architecture.md) and [case authoring guide](benchmark-case-authoring.md).

## Freeze a selection

Use an installed `generative-driver`. All paths below are operator-chosen placeholders; replace them with absolute paths and create their report/private parent directories first. On Windows, use absolute Windows paths and follow the [owner startup instructions](setup.md#start-the-configurator).

```sh
generative-driver --home /operator/state/corpus configure --executor codex --command /absolute/path/to/codex --provider openai --model YOUR_MODEL
generative-driver --home /operator/state/corpus benchmark corpus list
generative-driver --home /operator/state/corpus benchmark corpus plan --repetitions 3 --output /operator/reports/corpus-plan.json
```

This is the all-selected pilot: three slots for each of seven lineages, including all blocked lineages. Only selected, publicly ready cases enter the existing suite runner. To debug one lineage, freeze a separate plan:

```sh
generative-driver --home /operator/state/corpus benchmark corpus plan --repetitions 1 --include zephyr-modbus --output /operator/reports/zephyr-debug-plan.json
```

`--include LINEAGE` can repeat and selects only those lineage IDs. `--exclude LINEAGE REASON` can repeat and records an explicit exclusion, for example `--exclude grbl-motion "No qualified backend for this pilot"`. A lineage cannot be both explicitly included and excluded. With no `--include`, every lineage is selected except explicit exclusions. Neither option deletes catalog entries or coverage slots: unselected slots say `excluded` and retain a reason; selected gaps say `blocked` and retain their admission blockers.

`benchmark-corpus-plan/2` freezes the entire catalog, selection, availability, case pins and repetition count. The runnable suite's manifest ID binds it to that complete context. Freeze a new plan after changing selection, catalog, assets or qualification. Do not edit a frozen plan or use a later exclusion to remove a failed run from its original report. Legacy `/1` plans remain readable with their original all-selected meaning.

## Authenticate, execute and reconnect

Create `/operator/private/corpus-options.json` outside the checkout and worker directories. This is valid JSON; replace the paths with your own native installations and separately supplied evaluator password file:

```json
{
  "home": "/operator/state/corpus",
  "renode": "/absolute/path/to/renode",
  "ghidra_home": "/absolute/path/to/ghidra",
  "java_home": "/absolute/path/to/jdk",
  "evaluator_password_files": {
    "zephyr-modbus-v1": "/operator/private/zephyr-modbus-v1.password"
  },
  "scoped_tool_approval": "emulator"
}
```

`home` must equal the absolute `--home` used to configure and control the owner. Configure Goose with `--executor goose` and its executable instead if desired. Password handles go only to the operator check and detached bench harness; neither passwords nor their paths go to the configurator or assignments. Keep decrypted bundles, reference logs, upstream source/build trees and credentials outside worker inputs and enforce host access controls. Hashes establish integrity; workers are not filesystem-isolated by this implementation.

```sh
generative-driver --home /operator/state/corpus benchmark corpus check --plan /operator/reports/corpus-plan.json --options /operator/private/corpus-options.json
generative-driver --home /operator/state/corpus service start
generative-driver --home /operator/state/corpus benchmark corpus start --plan /operator/reports/corpus-plan.json --executor codex --options /operator/private/corpus-options.json --request-id corpus-pilot-1
generative-driver --home /operator/state/corpus benchmark suite status SUITE_ID
generative-driver --home /operator/state/corpus benchmark suite events SUITE_ID --after 0
generative-driver --home /operator/state/corpus benchmark suite result SUITE_ID --offset 0 --limit 50
```

`corpus check` authenticates the frozen selection's current ready cases through the same `require_calibration` admission used by the harness. It launches no owner, worker, native process or device and reports no password handles or private paths. A successful check establishes authenticated evaluator qualification only: it does not validate runtime authentication, installed native tools or model performance. Blocked and excluded entries remain visible; a selection with no ready cases cannot pass. Requalify stale evidence and freeze a new plan before checking again.

Save `suite_id` from `corpus start`; the returned suite is the normal suite lifecycle. The harness starts children sequentially through acquire → interpret → probe → ground → emit → fresh reuse. Repeating the request ID reconnects to the same suite. After correcting a stopped suite's operator or host fault:

```sh
generative-driver --home /operator/state/corpus benchmark suite resume SUITE_ID --options /operator/private/corpus-options.json
```

Use the [existing suite budget and recovery rules](../bench/README.md#run-a-repeated-suite); resume does not erase attempts, failures or elapsed time. No corpus-specific status or resume command is needed.

## Report without losing coverage

```sh
generative-driver --home /operator/state/corpus benchmark corpus report --plan /operator/reports/corpus-plan.json --suite-id SUITE_ID --output /operator/reports/corpus-report.json
```

`--suite-id` exports through the existing suite reporter. With the output path above, it preserves the raw suite export in `/operator/reports/corpus-report-suite/` beside the corpus report. Without `--output`, it uses a temporary suite export and prints the corpus report. To choose the suite export directory explicitly, use these commands instead:

```sh
generative-driver --home /operator/state/corpus benchmark suite report SUITE_ID --output /operator/reports/corpus-suite
generative-driver --home /operator/state/corpus benchmark corpus report --plan /operator/reports/corpus-plan.json --suite-report /operator/reports/corpus-suite/suite.json --output /operator/reports/corpus-report.json
```

Supply either `--suite-id` or `--suite-report`. Omit both for an intake-only report with no measured model rate. A suite must match the frozen manifest, complete case pins and actual-agent execution before its outcomes count. Reports retain every catalog slot, including excluded slots and their reasons.

| `benchmark-corpus-report/2` field | Meaning |
|---|---|
| `measured_success` | Passed / (passed + failed). A pass requires equivalence, a successful fresh mission and all six accepted stage handoffs. A valid final negative/no-driver grade is a failure; an equivalent driver with failed fresh mission counts as failed only with explicit model attribution. Missing evidence and host/operator/unknown mission failures are unscored. |
| `selected_coverage` | Passed / all selected slots, including selected blocked, stopped and unrun slots. |
| `demonstrated_coverage` | Passed / all catalog slots, including excluded slots. |
| `counts`, `slots` | Separate excluded, blocked, not run, running, stopped, unqualified, unscored, passed and failed outcomes, with selection/blocker/stop reasons and `fresh_mission_fault` / `fresh_mission_reason` when available. |
| `areas`, `lineages`, `origin_groups` | Coverage and measured results at each level. Repetitions are not new devices; related origins do not cross splits. |

A host stop is not a graded model failure. A debug selection's measured rate cannot establish seven-area coverage. Saved owner reports provide provenance, not independent cryptographic authentication; use the existing encrypted-evidence regrade path when authentication is required.

## Investigate a qualification failure

Keep each attempt in its own output directory. Read its private `calibration-record.json` for the failed reference, mutant or host operation; a pending record cannot admit a case. Positive references must pass before their mutant results can establish qualification.

Native session directories contain `renode.log` and, when a monitor request fails, `monitor-errors.jsonl`. A response-timeout entry retains the command, greeting and response separately, with the raw bytes preserved through JSON escaping. This lets the operator distinguish missing command completion from unrelated output. These records remain evaluator-private; do not copy them into issues, worker inputs or public reports.

Use the existing single-emulator execution profile when qualifying and measuring cases. Preserve any attempt made under additional load. A subsequent control run does not erase an earlier failure or prove its cause. A monitor or runtime fault requires environment investigation; it supplies no evidence that a generated driver's functional behavior is wrong.

## Rebuild and requalify Zephyr

Install Zephyr's Python build requirements, CMake, Ninja, devicetree compiler and Arm GNU Toolchain 14.2.Rel1. Clone into an operator source directory outside the checkout and workers:

```sh
git clone https://github.com/zephyrproject-rtos/zephyr /operator/source/zephyr
git -C /operator/source/zephyr checkout 413b789deb391d3a37d06b463288a5fe765ee57e
```

From `/operator/source`, run `west init -l zephyr`, `west update cmsis cmsis_6 hal_stm32` and `python -m pip install -r zephyr/scripts/requirements-base.txt`. Then:

```sh
generative-driver --home /operator/state/corpus benchmark corpus build --case zephyr-modbus-v1 --source /operator/source/zephyr --compiler /absolute/path/to/arm-none-eabi-gcc --west /absolute/path/to/west --output /operator/private/empty-zephyr-build
generative-driver --home /operator/state/corpus benchmark truth calibrate --case zephyr-modbus-v1 --password-file /operator/private/zephyr-modbus-v1.password --renode /absolute/path/to/renode --ghidra-home /absolute/path/to/ghidra --java-home /absolute/path/to/jdk --output /operator/private/empty-zephyr-qualification
```

Build verifies clean pinned source/module commits, builds twice with the shipped configuration, compares image hashes and records compiler/configuration provenance. It does not fetch sources or modify protocol handlers. Qualification authenticates the selected encrypted inventory and its pinned image; a separate rebuild alone does not replace that image. It runs native Ghidra analysis, two valid references in diagnostic and final phases, and six functional mutants. Final phases execute emitted packages. A failed positive reference stops qualification before mutants. Image, mission, evaluator/runtime closure or reference-inventory changes require new qualification.

Successful calibration writes `case.json` and `zephyr-modbus-v1.enc` to the private output directory. It does not activate them. Copy a complete public asset tree to an operator location, preserving `cases`, `groundtruth`, `missions`, `corpora`, `platforms`, `licenses` and other shipped layout. This Python example creates a new tree and replaces the reviewed pair together:

```python
from pathlib import Path
import shutil
from generative_driver.benchmark import case_root

assets = Path("/operator/assets/bench")
qualified = Path("/operator/private/empty-zephyr-qualification")
shutil.copytree(case_root(), assets)  # Destination must not exist.
shutil.copyfile(qualified / "case.json", assets / "cases/zephyr-modbus-v1/case.json")
shutil.copyfile(qualified / "zephyr-modbus-v1.enc", assets / "groundtruth/zephyr-modbus-v1.enc")
```

Preserve the matching pinned `firmware.bin` and public mission; leave plaintext qualification logs, source and passwords private. Set `GENERATIVE_DRIVER_BENCH_ROOT` to `/operator/assets/bench` in the operator shell **before** starting the owner and running plan/check/start. Use `export GENERATIVE_DRIVER_BENCH_ROOT=/operator/assets/bench` in a POSIX shell, or `$env:GENERATIVE_DRIVER_BENCH_ROOT = 'C:\operator\assets\bench'` in PowerShell with your selected absolute path. Restart an already-running owner with that environment. The variable selects the tree; it does not copy or qualify assets. Freeze and authenticate a new plan against the activated tree.

The native recipe's MIT `platforms/BufferedSocketTerminal.cs` forwards each socket block to UART at one virtual timestamp. It contains no protocol logic or write retries. UART-over-TCP establishes neither RS-485 electrical behavior nor physical baud/turnaround timing. Coil switching requires the emulator's `actuate` grant as well as register `write`. The supplied device runs during live calls and pauses between them, including probe and ground; thinking time does not advance the device. The inherited `time_policy.probe` label is not continuous execution between calls.

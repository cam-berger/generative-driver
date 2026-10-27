# Upstream firmware corpus

The software-first pilot selects seven upstream firmware lineages and preserves every admission gap. It adds Zephyr Modbus as the first executable upstream case. The three synthetic development cases remain available in their existing suite. Source intake, native evaluator qualification and actual model performance are separate results; see the [intake evidence](research/2026-10-26-corpus-intake.md).

## Plan, run and report

Use an installed `generative-driver` and a configured Codex or Goose runtime. The existing native tool requirements (Renode, Ghidra and Java) still apply.

```sh
generative-driver benchmark corpus list
generative-driver benchmark corpus plan --repetitions 3 --output corpus-plan.json
generative-driver benchmark corpus start --plan corpus-plan.json --executor codex --options operator-options.json
generative-driver benchmark suite status SUITE_ID
generative-driver benchmark suite report SUITE_ID --output suite-report
generative-driver benchmark corpus report --plan corpus-plan.json --suite-report suite-report/suite.json --output corpus-report.json
```

`operator-options.json` belongs outside the checkout and worker directories. Use the existing suite options: `home`, `evaluator_password_files` keyed by case ID, `renode`, `ghidra_home`, `java_home`, and `scoped_tool_approval: "emulator"`. Password handles go to the bench harness, never to the configurator or worker assignments. Keep the handles, decrypted bundles, upstream source/build trees and reference logs inaccessible to benchmark workers. The implementation's artifact hashes establish integrity, not filesystem isolation.

For a nondefault configurator, set `GENERATIVE_DRIVER_HOME` to the same absolute directory as `home` in the options file. Configure the Codex or Goose runtime in that home before starting the suite.

The corpus runner refuses a changed case pin or availability inventory: freeze a new plan after rebuilding or qualifying a case. Use `corpus report --plan corpus-plan.json` without a suite for an intake-only report. It reports no measured model rate. A saved suite must match the plan's manifest, case pins and actual-agent execution before its outcomes count.

Report fields:

- `measured_success`: equivalence and fresh mission success for the same completed trial, with all six accepted stages. Only graded outcomes enter this rate.
- `demonstrated_coverage`: successful trials divided by **all** planned slots. Unavailable and unrun cases stay visible here.
- `counts`: blocked, not run, stopped, running, unscored, unqualified, passed and failed, with reasons per slot.
- `areas`, `lineages`, `origin_groups`: coverage and measured results at each level. Repetitions are not new devices; related lineages do not cross splits.

A stopped host or missing evaluator result is not a model failure. A source-pinned catalog entry is not an executable device. Saved owner reports are trusted records; use the existing encrypted-evidence regrade path when independent report authentication is needed.

## Rebuild the Zephyr image

Install Zephyr's Python build requirements, CMake, Ninja and devicetree compiler, plus Arm GNU Toolchain 14.2.Rel1. Clone sources into an operator directory outside candidates:

```sh
git clone https://github.com/zephyrproject-rtos/zephyr zephyr
git -C zephyr checkout 413b789deb391d3a37d06b463288a5fe765ee57e
west init -l zephyr
west update cmsis cmsis_6 hal_stm32
python -m pip install -r zephyr/scripts/requirements-base.txt
generative-driver benchmark corpus build --case zephyr-modbus-v1 \
  --source /absolute/path/to/zephyr \
  --compiler /absolute/path/to/arm-none-eabi-gcc \
  --west /absolute/path/to/west \
  --output /absolute/path/to/empty-operator-build
```

The command verifies the clean source/module commits, builds twice using the shipped overlay/configuration, compares binary hashes, and writes compiler identity, build commands and configuration hashes. It does not fetch source, modify protocol handlers or run a model. A dirty or mismatched source tree is refused before compilation. An alternate asset tree can be selected with `GENERATIVE_DRIVER_BENCH_ROOT`, which must be an existing absolute directory with the same `cases`, `groundtruth`, `missions` and `corpora` layout.

To requalify the distributed encrypted inventory against installed evaluator/runtime code:

```sh
generative-driver benchmark truth calibrate --case zephyr-modbus-v1 \
  --password-file /operator/private/zephyr-modbus-v1.password \
  --renode /absolute/path/to/renode \
  --ghidra-home /absolute/path/to/ghidra \
  --java-home /absolute/path/to/jdk \
  --output /operator/private/empty-qualification
```

This runs native Ghidra analysis, two valid references in diagnostic and final phases, and six functional mutants. Final phases execute emitted packages. A failed positive reference stops qualification before mutants. Successful qualification writes a replacement `case.json` and encrypted bundle for the operator's asset tree. Changing the image, mission, evaluator, runtime or reference inventory requires qualification again. Do not copy plaintext logs, models or passwords into package resources.

The native recipe includes `platforms/BufferedSocketTerminal.cs` (MIT), which forwards each received socket block to the UART at one virtual timestamp. Its source is also pinned inside the encrypted recipe. This avoids scheduling each input byte separately; it adds no protocol logic and never retries writes. The bridge is a software transport and does not establish physical baud, byte timing or RS-485 turnaround behavior. Coil switching has an explicit emulator `actuate` grant, in addition to register `write` permission.

The supplied device advances during each live call and pauses between calls, including probe and ground calls. Agent thinking time does not advance device time. The case's inherited `time_policy.probe` label is not a promise of continuous execution between calls; assignments state the actual policy explicitly.

## Add another lineage

Record upstream ancestry, immutable source/license evidence, target architecture/system, public mission, functional requirements and blockers in the catalog first. Add a public mission file rather than branching stage prompts by case name. Executable adapters remain explicitly registered code; catalog data and encrypted inventories cannot dynamically load Python modules.

Admission needs a reproducible image, a working transport/backend, an independent observer, distinct valid references, disjoint diagnostic/final episodes, and mutants that fail the targeted functional requirements. Keep the case blocked until all of those exist. Run the existing six-stage agent pipeline after admission, including a fresh package-only reuse worker. Software qualification leaves physical transport, timing and plant behavior open until separately measured.

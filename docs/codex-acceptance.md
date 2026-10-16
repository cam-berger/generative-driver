# Codex firmware acceptance

The project succeeds when a fresh Codex worker can recover an interface from firmware evidence, emit an executable driver, and hand that driver to another fresh worker that completes the requested device mission. A worker's report, a valid model file and a successful replay are insufficient. Independent functional grading and successful fresh reuse must hold for the same trial.

The suite export reports `equivalent_and_mission` alongside `equivalence` and `fresh_mission`. Its denominator includes every planned trial. Unknown or failed mission evidence contributes no success; an equivalent driver with failed reuse remains equivalent but fails the joint criterion. Saved comparisons recount the joint metric from trial evidence, including older assistive exports that lack the field. Replay retains its own label and cannot be compared with model runs.

## Scripted regression lane

Run the [installed-package checks](verification.md#reproduce-software-checks). `test_acquisition_pipeline.py` exercises actual configurator dispatch, acquisition and sealed interpretation inputs with scripted Codex-format workers and synthetic USB/I2C backends. It verifies that USB discovery opens no serial port, observed files survive acquisition, and the blind firmware worker receives the image, analysis helper, exporter and input hashes. `test_assistive_reports.py` verifies joint success, missing reuse evidence, independently regraded exports and comparisons. These are software contracts, not measurements of a model's reverse-engineering capability.

## Native model acceptance lane

Use the [repeated-suite instructions](../bench/README.md#run-a-repeated-suite) with `--executor codex`, a verified OpenAI Codex CLI, an explicit model and the three-family `development-pilot.json` manifest. That starts nine fresh trials. Candidate workers receive firmware and generic tooling; the evaluator alone holds source-derived truth and passwords. Fresh reuse receives the emitted package and its mission. Save every failed, stopped and unscored trial. Prior gated-protocol measurements cannot qualify an `assistive/1` run.

Before starting, the password holder must perform [native reference calibration](../bench/groundtruth/README.md) for the installed evaluator. This integration changes its identity; existing calibration is insufficient. Same-user filesystem isolation remains unverified, so this is password-based answer-key separation, not a demonstrated sandbox.

After the suite finishes, create a private JSON file outside candidate workspaces and the checkout:

```json
{
  "suite_id": "SAVED_SUITE_ID",
  "home": "/absolute/path/to/suite-state",
  "evidence_files": {
    "entry-000-repeat-000": "/private/trial-0/run-evidence.enc"
  },
  "password_files": {
    "tq9-v2": "/private/evaluator/tq9-v2.password",
    "sampled-sensor-v1": "/private/evaluator/sampled-sensor-v1.password",
    "parameter-store-v1": "/private/evaluator/parameter-store-v1.password"
  }
}
```

Populate `evidence_files` with every graded trial's key and sealed evidence path from the harness records; the abbreviated example above is not a complete nine-trial configuration. With the same installed environment and existing configurator, set `GD_NATIVE_CODEX_SUITE` to that JSON file's absolute path and run:

```sh
python tests/native_codex_pipeline.py -v
```

The gate regrades the sealed evidence through the benchmark API, requires Codex with a recorded model, at least three families and three repetitions, and requires equivalence plus successful fresh reuse in every planned trial. Missing configuration fails explicitly. The gate launches no new inference. A pass establishes those recorded trials only; broader reliability needs held-out firmware and repeated measurements. This acceptance lane has not been run for the integrated branch.

REA's obligation ledger proposal is retained in [the design specification](superpowers/specs/2026-10-15-obligation-ledger-verdict.md). Its tools are not integrated by this change; an eventual adapter should contribute attributable evidence without receiving evaluator truth or deciding its own grade.

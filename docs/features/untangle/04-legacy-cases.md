# 04. legacy-cases

## Intent

Two benchmark cases are older than the rest: the old `tq9` and the physical `bme280`. Each has stage code of its own, with its tasks and checks written into that code. The cases that came after them share one path, and only that path can be turned into "a device and a mission a client supplies", which is what Feature 05 does. On 2026-11-15 the owner decided to drop the two legacy cases, not to carry them along.

This Feature drops them. The two cases are no longer registered, so nothing can run them. The service loses the parts that exist only for them. No code that stays imports their code or reads their files. Their code and resources go on the deletion list as ready to delete, each marked as a dropped case that is not carried to the benchmark's repository. The owner asked for that mark.

Their saved records go with them. A saved report, a sealed trial or a stored run of `tq9` or `bme280` can no longer be read, regraded or resumed, because the registration it needs is gone. The owner decided on the same day to move past old records.

Tests of a case that no longer exists cannot pass, so they are removed. Many tests are not about the old tq9 at all: they use it as a convenient stand-in case, or they test code that every current case shares. Those keep their names and their assertions and run on a current case.

Behaviour changes only for the two dropped cases. Every current case runs as before. No grading code is edited, and no current case's grader identity changes.

This Feature also carries the renumbering. It was added before the old Feature 04, so the Features after it moved up by one, and the files that name them by number are brought up to date here.

## Behaviours

In these Behaviours, "the dropped cases" are `tq9` and `bme280`. "The current cases" are the six registered cases whose stage adapter is `emulator-v2`: `tq9-v2`, `tq9-v3`, `sampled-sensor-v1`, `parameter-store-v1`, `zephyr-modbus-v1` and `generated`. "The dropped cases' modules" are `cases.py`, `physical.py`, `recorded_grading.py` and `tq9_legacy.py` in `src/generative_driver/benchmark_support/`. "The dropped cases' resources" are `cases/tq9/`, `cases/bme280/`, `groundtruth/tq9.enc` and `groundtruth/bme280.enc` in `src/generative_driver/resources/bench/`. "The eight items" are those four modules and four resources. "Code that stays" is every Python file under `src/` and `tests/` other than the dropped cases' modules. "The service" is the configurator. "The stand-in modules" are `tests/test_suite_store.py`, `tests/test_suite_service.py`, `tests/test_suite_interfaces.py`, `tests/test_benchmark_snapshots.py`, `tests/test_suite_manifest.py` and the helper `tests/suite_fixtures.py`.

- **04-B1** — The dropped cases are no longer registered.
  - `registry.case_ids()` gives `setup-smoke`, `tq9-v2`, `tq9-v3`, `sampled-sensor-v1`, `parameter-store-v1`, `zephyr-modbus-v1` and `generated`, in that order, and nothing else.
  - `registry.pin_case('tq9', None, 0)` and `registry.pin_case('bme280', None, 0)` raise `ValueError` with `Unknown benchmark case: ` and the case's name, as for any name that was never registered.
  - A `start` through the service that names `tq9` or `bme280` as its `case` is refused with that same reason.
  - The stage adapters are the current one and the smoke's: for a registration whose adapter key is `legacy-emulator` or `legacy-physical`, `adapters.adapter_for` raises `ValueError` with `Benchmark adapter is pending: ` and the key.
  - `generative_driver.benchmark` no longer has the name `package_execute`, which only `bme280` used.
- **04-B2** — Grading code loses the old tq9's family and nothing else. `grader_closure()` names every module it names at the base commit except `benchmark_support.tq9_legacy`: 34 modules. The test states the 34 names.
- **04-B3** — No code that stays reaches the dropped cases' modules or their answer files.
  - None imports one of the four modules, in any import statement at any depth.
  - None names one of them, or one of the two answer files, by its file name: no string in it is one of `cases.py`, `physical.py`, `recorded_grading.py`, `tq9_legacy.py`, `tq9.enc` and `bme280.enc`, or ends with `/` and one of them.

  The test file of this Behaviour holds those names and is the one exception.
- **04-B4** — The service loses its parts for the dropped cases.
  - `configurator` no longer has the name `NO_HARNESS_DEVICE`.
  - No tool module imports `package_execute`.
  - `validate_scoped_approval` of `{'scoped_tool_approval': 'emulator'}` raises `ValueError` with `Emulator approval requires a registered benchmark case such as tq9-v2`.
- **04-B5** — The dropped cases are marked for deletion. The deletion list has one row under `Ready to delete` for each of the eight items. The Note of each starts `Dropped legacy case. Not carried to the benchmark's repository.` Feature 01's check reports no problem on the repository, and its removal takes the eight items out of the stripped copy.
- **04-B6** — The stand-in modules run on a current case. No string in them is exactly `tq9` or exactly `bme280`. `tests/suite_fixtures.py` no longer has the names `legacy_manifest`, `legacy_case`, `LEGACY_DEVICE_FIXTURE`, `legacy_device_patches` or `legacy_devices`, and no file under `tests/` uses one of them.
- **04-B7** — The Feature numbers are up to date.
  - Every `Freed by` on the deletion list that named Feature 07, 08 or 09 names 08, 09 or 10. After this Feature the list's `Freed by` values are `08`, `09`, `10` and `stays`, and no other.
  - Feature 01's check takes `02` to `11` as a `Freed by`, and reports `12` as naming no later Feature.
  - The sentence of the deletion list that names the Feature which gives the benchmark its own entry point names Feature 09, and the row that names the Feature which settles what moves names Feature 10.
  - The marked passage of `docs/client-interface.md` names Features 05 and 06 where it named 04 and 05.
- **04-B8** — The deletion list can mark part of a folder that is still used. Feature 01's check reports no problem for a row under `Ready to delete` whose item lies inside a folder that has a row under `Still used by the tool`: the narrower row decides for what it covers, and the removal takes that item out. Every other pair of rows that cover one file is still reported, as Feature 01 gives it: two rows of one table, a `Kept` row with any other, and a `Still used by the tool` row inside a `Ready to delete` folder.

## Interface and seams

- `src/generative_driver/resources/registrations/cases.json` loses the two entries of the dropped cases. No other byte of the file changes.
- No grading code is edited. `tq9_legacy.py` is grading code at the base commit only because the old tq9's registration names it; it leaves the closure with that registration, unedited. Grading code that names the dropped cases in comments or in branches keeps those lines: `evaluator.py`, `evidence.py`, `registry.py`, `registrations.py`, `case_assets.py`, `grader_identity.py`.
- The eight items are not deleted and not edited. They are inert: nothing that stays imports them or reads them by path.
- What changes in the benchmark's folder, in files that are not grading code:
  - `adapters.py`: the loaders `_legacy_emulator` and `_legacy_physical` and their two keys go.
  - `_state` (`cases.py:14-16`) gets a home in `candidate_context.py`. `_write` is taken from `case_assets`, where it is defined as `write_json`. The importers change: `emulated.py:11`, `candidate_context.py:7`, `reference_calibration.py:156` and `evaluator_cli.py:40`.
  - `emulated.py`: the old tq9's parts go: `RECORDED` (`:381`) with its uses at `:255` and `:572-579`, the mapping branch at `:372-375`, and the use of `mission_case` at `:321-324`. With `RECORDED` gone, ground writes `TASKS.json` for every case, as it does for every current case today.
  - `harness.py`: `legacy_recipe`, the tq9 branch of `device_recipe`, and in `conclude` the `recorded-reuse` branch with its import of `recorded_grading` and its `Unscorable` handler.
  - `legacy.py`: the `bme280` block that imports `recorded_grading` (`:42-67`) goes. The rest of the file stays as it is.
  - `benchmark.py`: `package_execute` goes. `truth --case` loses its default `tq9` and becomes required.
- What changes in the service, `configurator.py`. This list is complete: a guard that only a dropped case could trip, and that is not listed here, stays.
  - `NO_HARNESS_DEVICE` (`:67-69`) goes, with the two branches of `_start_new` for a legacy tq9 run with and without a device (`:2289-2294`) and the `note` that carried the constant into the reply. The two refusals after those branches stay for a case that is not on the current path.
  - In `_resume_device`, the return for a legacy run without a device (`:2980-2981`) goes.
  - In `_dispatch`, the branch that calls `benchmark.package_execute` (`:3865-3874`) goes. No run can reach that point any more: `benchmark_package_execute` is assigned only to a run on a provided device.
  - The refusal at `:108` names `tq9-v2`.
  - Docstrings and comments in these lines and at `:72-81` are reworded so that none says a dropped case runs.
- `mcp.py:106-107`: the description of `driver_benchmark_run` no longer says that `tq9` runs on the harness's emulator or that `bme280` runs on the operator's device.
- Existing tests. The rules below decide each test that needs a dropped case. A file whose tests are all removed or moved goes; no file goes by name alone.
  - A test of a dropped case's own behaviour is removed: its stage code in `cases.py` or `physical.py`, its grading from recorded reuse, its registration, the regrade of its saved reports, or a service behaviour only it had, such as a device stage that waits for a harness device.
  - A test that uses a dropped case as a stand-in, or tests code that stays, keeps its name and its assertions and runs on a current case. That covers the tests of the stand-in modules, and tests of shared stage code and of its truth boundary elsewhere, such as `tests/test_package_benchmark.py:15` and `:69`, `tests/test_benchmark_stages.py:11` and `:72`, and `tests/test_legacy_cases.py:870` and `:878`. A test that moves out of a file that goes keeps its name in the file it moves to.
  - A converted test uses the fixtures that already serve current cases: `PendingRunFixture` and `HarnessTrialFixture` in `tests/test_evaluation.py`, `HarnessSuiteFixture` in `tests/test_suite_harness.py`, the spawned-harness fixtures in `tests/test_harness.py`, and `public_case_fixture` in `tests/suite_fixtures.py`. A new helper is allowed where those do not reach, such as for a service started as its own process. An expected value that follows from the case, such as a pin, a calibration status or the case's effects, follows the new case.
  - A stand-in test may be removed, not converted, in only one case: a named existing test on a current case asserts the same behaviour.
  - A test module that imports nothing from the benchmark package today must not start to, so that it keeps running in the stripped copy. A test in such a module that needs a case moves, under its name, into a benchmark test module that has a device fixture. One that needs no case runs without one. `tests/test_configurator.py:3496` and `:3531` are two such tests.
  - A test that only names a dropped case in data, in an expected message or in a list changes that data and keeps its assertion.
  - Four tests read the eight items by path and stop doing so: `tests/test_unlocked_keys.py:621`, `tests/test_assistive_wording.py:53-54` and `tests/test_family_seam.py:787` drop the dropped module from what they read, and the inventory test at `tests/test_benchmark_asset_build.py:354-369` compares the answer files of the registered cases only.
  - Helpers that only the dropped cases used go: in `tests/suite_fixtures.py` the five names of 04-B6, and in `tests/tq9_workflow_fixture.py` `legacy_truth` and `legacy_capabilities`. A helper another test still needs moves to a file that stays, such as `silicon_start`, which `tests/test_unlocked_keys.py:22` takes from `tests/test_legacy_cases.py`.
- New tests:
  - `tests/test_benchmark_legacy_cases_dropped.py` tests 04-B1, 04-B2, 04-B3, 04-B5, 04-B6 and the first, third and fourth points of 04-B7. It is a benchmark item and goes under `Ready to delete`. It reads the rows from the text of the deletion list, by heading.
  - `tests/test_service_without_legacy_cases.py` tests 04-B4. It imports nothing from the benchmark package, so it runs in the stripped copy.
  - The second point of 04-B7 and 04-B8 are tested in `tests/test_deletion_marking.py`, with tests named for them. Its existing test of a wrong `Freed by` takes `12` as the wrong number where it took `11`. Its existing test of two rows that cover one file stays as it is.
- Feature 01's check, `tests/deletion_marking.py`, changes in two places: it takes `02` to `11`, and it follows 04-B8.
- The deletion list also loses the rows of the test files this Feature removes.
- Test names of this Feature carry `04_B1` to `04_B8`. Other Runs already have tests named `test_04_B…`, in `tests/test_service_log.py` and `tests/test_grader_identity.py` among others, and the kit's check cannot tell the Runs apart. So the Reviewer checks that each Behaviour here has its test in the files named above.
- The build report lists every removed test with its reason, every moved test with its new file, and for every stand-in test that was removed and not converted, the test that covers the same behaviour.

## Out of scope

- Deleting the eight items. They go with the deletion that follows the benchmark's move.
- Any edit to grading code, and anything that changes a current case's grader identity or registration entry.
- The grading method `recorded-reuse` in the verdict record's rules and in `_record_evaluation`. No registered case uses it any more; Feature 06 puts the record in the tool's own terms.
- Branches and texts in the benchmark's own modules that name a dropped case and import none of its modules, such as `suites.py:101`, `scorecard.py:393-395`, the rest of `legacy.py`, the messages in `evaluator_cli.py` and the approval flags and messages in `benchmark.py`. They go with the benchmark.
- Documents that name the dropped cases: `bench/README.md`, `bench/groundtruth/README.md`, `docs/design.md`, `docs/verification.md` and the records under `docs/features/` and `docs/superpowers/`.
- The approval scope `bound-device` in the service. It serves a user's own bound device and needs no case.
- The mission note the reuse stage writes during a run. Feature 05 stops it.
- The three `stays` rows of the deletion list and the `Kept` table. Feature 10 puts the published record on the list.
- A new case on real hardware.

## Deviations

- New test helpers take the place of the five legacy fixtures, and the converted stand-in tests use them where the Spec names the fixture classes: `scripted_manifest`, `DEVICE_FIXTURE`, `device_patches`, `harness_devices`, `HARNESS_TOKEN`, `device_descriptor` and `provided_device` in `tests/suite_fixtures.py`, and `case_start` and `attach_device` in `tests/test_benchmark_snapshots.py` — the Spec allows a new helper where the named fixtures do not reach; most stand-in tests drive a detached harness, a separate service, the command line or the MCP server, and the in-process ones use the same helpers so their bodies and assertions stay.
- Converted tests that resume a stopped run first attach another device: three in `tests/test_benchmark_snapshots.py` and the moved emulator-approval test — a setup step the current path needs; the asserted refusals are unchanged.
- Two tests of `tests/test_benchmark_stages.py` are converted, not removed: `test_interpret_receives_neutral_sealed_inputs_without_evaluator_answers` and `test_interpret_repair_preserves_firmware_and_seals_observed_defects` — they are written against the public `prepare_stage` and every assertion holds on `tq9-v2`, which keeps coverage as the Intent asks for tests that are not about the old tq9.
- One mixed test is removed: `CategoryTests.test_an_unqualified_interpretation_is_a_note_and_a_device_stage_waits_for_the_harness_device` in `tests/test_suite_service.py` — its deciding assertions are the wait for a harness device, the Spec's own example of a behaviour only a dropped case had; the shared half is asserted on `tq9-v2` by `tests/test_pipeline_repair.py:54` and `tests/test_benchmark_stages.py:72`.
- Three tests that still name `tq9` stay as they are: `tests/test_suite_reporting.py:244`, `tests/test_benchmark_run.py:25` and `tests/test_benchmark_asset_build.py:317` — each tests a branch or message the Spec puts out of scope, needs no registration and passes unchanged.
- In `tests/test_family_seam.py`, the test of 01-B8 edits a copy of `tq9_v2.py`, the test of 01-B10 registers two rows written out in the test, and the test of 01-B4 uses a toy registration — the tests stop reading the eight items, as the Spec orders, and keep an assertion of the same kind on code that stays.
- `setup-smoke` stands where a test needed a registered case that is not an emulator profile or has no native module, in `bme280`'s place and once in the old tq9's — data that follows from the case; the same refusal or the same branch is exercised.
- Expected values follow the new case, and data rows are dropped where no registered case can stand in: the `bme280` datum in `tests/test_suite_manifest.py`, the `bme280` and seed-1 selections in `tests/test_suite_interfaces.py`, and the `--case bme280` row in `tests/test_replay_executor.py` — the Spec lets values follow the case; no physical case and no suite case that refuses a seed is registered.
- Tests are added for Interface items that have no Behaviour bullet, named under the nearest Behaviour ID, with two self-checks of the scanners — the Spec asks that test names carry `04_B1` to `04_B8`, and they give each code change a failing test first.
- Under 04-B8, two still-used folders around one ready row are still reported — that is two rows of one table, which the Spec says is still reported; without a ready row inside a still-used folder the check works as at the base.
- The deletion list gains a sentence under "How to read it" for 04-B8 and a row for the new benchmark test file, and two `Used by` counts change — the Spec orders the row, the sentence states 04-B8, and the counts match the importers.
- `tests/tq9_workflow_fixture.py` loses `REGISTERS` and the `sysbus ReadDoubleWord` branch of `Silicon` beyond the two helpers the Spec names, and gains `silicon_start` — helpers only the legacy recipe used go, and a helper another test needs moves to a file that stays, both by the Spec's rule.
- Leftovers of the listed removals go, the `REUSE_EVENTS` import in `emulated.py` and `import math` in `legacy.py`, and docstrings are reworded where they described removed code — dead remains of the listed removals; no behaviour.
- The moved tests have new homes: `StartAdmissionTests` in `tests/test_harness.py` and `StageBoundaryTests` in `tests/test_benchmark_stages.py` — benchmark test modules, names kept; the bodies match the base apart from the dropped modules and the attach step.
- Lines this Feature wrote, or pushed past 120 columns by a rename, are wrapped — formatting only.
- The build used a scratch copy of the tree outside the repository, and one read-only shell command filtered `git status` output by the base name of the kit's state file — no effect on the Spec or the scope; nothing under `docs/features/untangle/` was edited by the build.
- `tests/test_service_repair.py` gains a direct import of `benchmark_support.registry` where it had none — it was a benchmark item at the base and never ran in the stripped copy, which is the reason the Spec gives for that rule.

# Spec 10: tests

## Intent

For the owner, who is moving the benchmark to the firmware bench repository. After Feature 09 no tool module imports the benchmark, but the tests are still one folder. In `tests/`, 44 test modules are the tool's and 78 are the benchmark's, and the deletion list keeps the benchmark's code and resources as still used, because of those tests.

This Feature pulls the tests apart. The tool's tests stay in `tests/` and need nothing of the benchmark. The benchmark's tests move to a folder of their own, `firmware-bench/tests/`, and need nothing of the tool's test files. The benchmark's code and resources then become ready to delete, and the removal takes them out before it runs the tool's tests.

The owner decided four points for it on 2026-11-18:

- The old Feature 10 is split in two. This Feature is for the tests. Feature 11 is for the documents.
- The benchmark gets one folder at the root, `firmware-bench/`. This Feature creates it with `firmware-bench/tests/`. Feature 11 adds the documents and Feature 12 the package.
- Only what is relevant moves. The owner's words: "only if they are relevant documents and tests. If they are not, or outdated in any way, delete them". So a test that only holds a passing state of this Run in place is deleted. The owner then asked that the other benchmark test modules be gone through for outdated tests as well, and all sixty were, test by test. 10-B4 says which tests go.
- Of the tests of the tool's own behaviour that run on the benchmark's cases today, the 41 that run on the tool's own fixtures join the tool's tests now. About 85 more need the bench harness's device. They stay with the benchmark's tests. `Out of scope` names what the tool then tests only from there.

One requirement comes from the session that wrote this Run's brief. The owner agreed on 2026-11-14 to pass it on: the removal's test processes must not be able to import a package from outside the copy. 10-B8 closes it for the packages of today. Feature 12 closes it for the package it creates.

Terms used below:

- The **benchmark's code** is `src/generative_driver/benchmark.py` and every module under `src/generative_driver/benchmark_support/`. The **benchmark's resources** are `src/generative_driver/resources/bench/` and `src/generative_driver/resources/registrations/`. A **tool module** is any other module under `src/generative_driver`, and `interface_runtime`.
- The **list** is `docs/benchmark-deletion-list.md`. The **check** is `tests/deletion_marking.py`. The **list's tests** are `tests/test_deletion_marking.py`. The **removal** is the check's `strip`, and the **stripped copy** is what it writes.
- A **tool test file** is a Python file under `tests/` other than the check and the list's tests. A **benchmark test file** is a Python file under `firmware-bench/tests/`.
- The **interface** is `docs/client-interface.md` and `src/generative_driver/client_interface.py`.
- Line numbers are those of commit `a7ad7fd`.

Where something is different after this Feature:

- The benchmark's tests are run from `firmware-bench/tests/`. The tool's tests are run from `tests/`, as before.
- 157 tests no longer exist (10-B4).
- The test command in `STANDARDS.md` and the hosted tests run both folders.
- The stripped copy holds no benchmark code, no benchmark resources and no benchmark test.
- No module under `src/` changes.

## Behaviours

- **10-B1** — The tool's tests use nothing of the benchmark.
  - `tests/test_service.py`: the two worker scripts (`:28`, `:98`) take their model from the tool's `resources/smoke/model.json`. `tests/test_toolkit.py` (`:20`) takes its model and its replay from `test_package.example_model()` and `example_replay()`. Their assertions are unchanged.
  - `tests/test_distribution.py` requires in the wheel what the tool needs: `interface_runtime/engine.py`, `generative_driver/resources/toolchain/tools.json`, the client skill, the four files of the smoke, and no compiled file. It no longer requires the benchmark's smoke case, the two registration files, `docs/verification.md` with its evidence, or the four benchmark helpers. Those requirements move to a benchmark test file, with the helpers at their new paths.
  - `tests/test_checkout.py` keeps its assertion that no byte changes on a Windows checkout. The loop that holds each case's answer file to its hash (`:44-50`) moves to a benchmark test file, which makes the same checkout for the files under `resources/bench/`.
  - `tests/test_tool_releases.py` (`:110`) requires the three pins of the tool's own workflow: Ghidra, the JDK and the Xtensa compiler. That `bench/README.md` pins Renode is asserted by a benchmark test file, which reads that document itself.
  - `test_03_B8_the_software_checks_run_the_smoke` (`tests/test_service_smoke.py:503`) reads `README.md` and no longer `docs/verification.md`: one of the fenced blocks of its install section holds a line that ends `-m generative_driver smoke`.
  - No tool test file names `setup-smoke`, `resources/bench`, `bench/cases`, `resources/registrations`, `bench/evidence` or `docs/verification.md` in a string of its code. The scan reads string constants and leaves out module, class and function docs, comments, and the test file that holds the scan. A path built from parts is not seen by it; the removal shows the rest (10-B8).
  - Four tests that hold their own file free of registered case names read `resources/registrations/cases.json` when it is there (`tests/test_client_calls.py:456`, `tests/test_client_surface.py:393`, `tests/test_supplied_runs.py:1326`, `tests/test_tool_commands.py:580`). They stay as they are. They build that path from parts, so the scan does not name them.

- **10-B2** — Forty-one tests of the tool's own behaviour are tool tests.
  - The table under `Interface and seams` names them, their new files, and what each uses in place of the benchmark's fixture.
  - Each keeps its name and its assertions, but for the changes that table names.
  - All 41 pass with the benchmark's code, resources and tests absent. The removal runs them in the stripped copy.
  - The seven modules they are in have no row in the list.

- **10-B3** — The benchmark's tests take nothing from the tool's test files.
  - No benchmark test file imports a module that lies in `tests/`. The scan reads import statements at any depth. It also reads as code every string constant that parses as Python and holds an import: that is how a test writes the script of a child process.
  - What the benchmark's tests took from the tool's test files is copied into one benchmark helper, `firmware-bench/tests/tool_fixtures.py`. `Interface and seams` lists the names.
  - A benchmark test that starts a Python child gives it the environment of the copy of `package_env` in that helper. That meets line 38 of `STANDARDS.md`, which names the tool's `tests/package_env.py`; Feature 11 settles the line's wording.
  - No file name is in both folders.

- **10-B4** — Tests that are outdated, or that only hold a passing state of this Run in place, are deleted.
  - Three modules are deleted whole. `tests/test_benchmark_runs_unchanged.py`: its two tests hold a benchmark run to what the kit-fixes Run left unchanged, on the tool's own test fixtures. `tests/test_benchmark_stages.py`: four of its tests become tool tests (10-B2), and the other two are a scan of tool sources (`:90`) and a list of names that are gone (`:100`). `tests/test_benchmark_reuse_endpoint.py`: its five tests seal and regrade the evidence of the earlier, gated protocol, which nothing in the code writes any more.
  - In eight modules that this Run wrote or changed, each test goes by what its assertions depend on. A test that runs code and asserts what it does moves. So does a test that holds the benchmark's code to the interface. A test is deleted when all it asserts is a state of the list, the text or a hash of a document or a build file, a scan of the tool's source, or that something an earlier Feature removed is absent. `Interface and seams` lists every deleted test by its line, 101 in all.
  - In the sixty other modules a test is deleted in five cases. It tests the earlier, gated protocol's own machinery, the sealing of its evidence and its regrade, whether that accepts or refuses. It shows a record, a report, an export or a saved state of another earlier format being read. All it asserts is that something removed is absent, or that a dropped name is refused as any unknown name is. It holds the tool's skill text to wording that is stale, or scans the tool's source. Or it repeats another test. `Interface and seams` lists every such test by its line, 47 in all beside the five of `test_benchmark_reuse_endpoint.py`. The list governs: a test that is not on it moves.
  - So these stay, though they touch something old. A test in which current code turns an earlier-format file away stays, since the refusal protects results. A test that holds a rule of today stays where its fixture, or a part of its body, is of the earlier protocol. A test of the tool's own code stays, by the owner's fourth decision. The seven tests of `behavior.project_feedback` and `parameter_store.expected_state` stay, though nothing under `src/` calls those two functions: whether something outside this repository uses them cannot be seen from here.
  - Three tests of the check itself move into the list's tests: `tests/test_benchmark_commands.py:731` and `tests/test_benchmark_surface.py:1258`, `:1263`.
  - A helper, a constant or an import that only deleted tests used goes with them. A moved test keeps what it needs of a fixture or a class it shared with a deleted one.
  - No benchmark test file reads the list or imports the check or the list's tests.

- **10-B5** — The benchmark's tests lie in `firmware-bench/tests/`.
  - Every test module and helper that has a row in the list today moves there under its own name, unless 10-B2 or 10-B4 takes it elsewhere. The check and the list's tests stay in `tests/`. That is 69 test modules and 8 helpers.
  - A moved test keeps its name and its assertions. It finds the repository, the source and the documents from its new place. A child script that imports a helper by its bare name finds it in the new folder.
  - `python -m unittest discover -s firmware-bench/tests` finds and runs them.
  - `MANIFEST.in` puts the Python files of `firmware-bench/tests/` into the source archive, in a marked passage.
  - Five lines of the benchmark's documents that name a moved file or the folder name the new place: `bench/README.md:35`, `bench/groundtruth/README.md:73`, `docs/benchmark-case-authoring.md:30`, `docs/codex-acceptance.md:37`, and `docs/generated-device-interface.md:186`, whose link to `tests/generated_fixtures.py` gets the new text and the new target. No other line of a document changes, but in the list.
  - A doc or a comment in a test file that names a moved or deleted test file follows it: `tests/test_input_paths.py:805-807`, `tests/test_supplied_stages.py:5-6` and `tests/test_service_without_legacy_cases.py:4-5`, and the like in the files that move.

- **10-B6** — The test command and the kit read both folders.
  - `STANDARDS.md` holds the three lines given under `Interface and seams`. The owner puts them in by hand before the Build begins, since only the owner changes that file.
  - The test command runs every `test_*.py` of `tests/` and of `firmware-bench/tests/`, each in its own process, eight at a time, and fails when one fails. It then runs the dashboard test and the removal, as before.
  - `Test files` names both folders, so the kit finds a test of a Behaviour in either.

- **10-B7** — The hosted tests run both folders.
  - The step `Test outside the checkout` of `.github/workflows/test.yml` runs the tool's tests from `tests/`, then the benchmark's tests from `firmware-bench/tests/`, then the tool's smoke. Each is a process of its own with `check=True` and the environment the step builds today.
  - The line that runs the benchmark's tests stands in a marked passage. So the workflow of the stripped copy runs the tool's tests and the tool's smoke only.
  - Nothing else in the workflow changes.

- **10-B8** — The benchmark's code and resources are ready to delete, and the removal takes them out.
  - In the list, `src/generative_driver/benchmark.py`, `src/generative_driver/benchmark_support/`, `src/generative_driver/resources/bench/` and `src/generative_driver/resources/registrations/` stand under `Ready to delete`. So does `firmware-bench/tests/`, as one row.
  - The eight rows of the dropped legacy cases are gone as rows. The notes of `benchmark_support/` and of `resources/bench/` say which of their files belong to a dropped legacy case and are not carried to the benchmark's repository.
  - No row names a file under `tests/` but the check and the list's tests.
  - The stripped copy holds none of the ready items, and every tool test module passes in it.
  - In the stripped copy a Python child cannot import `generative_driver.benchmark` or `generative_driver.benchmark_support`. That holds also where the tree the copy was made from, or an installed wheel, still has them. No mechanism changes for this: the removal already puts the copy's `src` first for its children, and the copy keeps the tool's package. One of the list's tests starts a child in a stripped copy with the environment the removal gives its children and expects both imports to fail. The half about an installed wheel is shown by the hosted run.

- **10-B9** — A boundary test on each side.
  - A tool test fails if a tool module, or a tool test file, imports the benchmark's code. It sees the benchmark under each of its names: `generative_driver.benchmark`, `generative_driver.benchmark_support` and `firmware_bench`. It sees an import statement at any depth, relative or absolute, and a literal name given to `import_module` or `__import__`.
  - `in_benchmark_package` (`tests/test_shared_helpers.py:68`) takes `firmware_bench` as well, so the scans of earlier Features see that name too.
  - The tests that hold the benchmark's code to the interface move with the benchmark's tests and keep passing: what it imports from the tool (`test_08_B12_…`), which methods it sends the service (`test_08_B14_…`) and what it reads in the home (`test_08_B15_…`).
  - The grading-code boundary test, `test_grader_identity.py`, moves and keeps passing.

- **10-B10** — The list and its check follow.
  - The check takes the `Freed by` values `02` to `12`.
  - Under `Still used by the tool` stand only documents: `bench/README.md`, `bench/groundtruth/README.md`, `docs/benchmark-case-authoring.md`, `docs/benchmark-corpus.md` and `.scratch/benchmark-lifecycle/`. Each is freed by `11`, and each reason is true.
  - No reason and no note in any table of the list names a test file at a place it no longer has.
  - Every `Freed by` under `Passages` is `11` or `stays`. The row of `docs/codex-acceptance.md` says that Feature 11 settles what moves. No row and no sentence of the list names Feature 10.
  - Every count under `Passages` is true. The row of `STANDARDS.md` says 4.
  - The check reports no problem on the repository.

## Interface and seams

Where the tests of this Feature are. Tests are named `test_10_B<n>_`. A test that guards something lasting is in the folder it guards. A test that only holds a state of this Run in place is in the list's tests, which the deletion removes last.

- `tests/test_tool_boundary.py`, new, a tool test file: 10-B1 (the scan of the tool test files) and 10-B9. It imports nothing of the benchmark.
- `tests/test_tool_commands.py`: of 10-B7, that the step runs the tool's tests and then the tool's smoke, and that any other command of the step stands in a marked passage. It must pass in the stripped copy as well.
- `firmware-bench/tests/test_boundary.py`, new: 10-B3 and the last point of 10-B4.
- The list's tests: 10-B2 (the seven modules are tool test modules), 10-B5, 10-B6, of 10-B7 what the marked passage runs, 10-B8 and 10-B10.

The 41 tests of 10-B2:

| From | To | Tests | In place of the benchmark's fixture |
|---|---|---|---|
| `tests/test_benchmark_family_runtime.py` | `tests/test_engine_protocols.py` | all 9 | The four names it takes from `benchmark_family_fixtures.py` (`ToyTransport`, `sample_reply`, `toy_sample_model`, `toy_store_model`), with what they need, are copied to the tool helper `tests/toy_protocols.py`. |
| `tests/test_rejection_framing.py` | the same name, a tool test | all 10 | The worker script comes from the tool helper `tests/worker_script.py`. The assertions on the line the tests replace (`:140-142`, `:213`, `:219`) follow that helper's line. |
| `tests/test_reuse_controller.py` | the same name | all 5 | The same helper. |
| `tests/test_service_repair.py` | the same name | 1 | The same helper, with `hold_on_repair`. The run starts on `SuppliedDevice` with `hand_written_firmware`, `MISSION` and a token hash, in place of `suite_fixtures.supplied_start`. |
| `tests/test_owner_generated_integration.py` | the same name | 10 of 11 | Nothing changes. The eleventh (`:128`) grades with the benchmark's evaluator. It moves to `firmware-bench/tests/test_evaluation.py` and keeps its assertions. |
| `tests/test_package_benchmark.py` | `tests/test_emitted_package_calls.py` | both | The package is built from `test_package.example_model()` and `example_replay()` with the toolkit calls `benchmark.run('setup-smoke')` made. The stage functions are called with `hand_written_firmware` and `MISSION`. |
| `tests/test_benchmark_stages.py` | `tests/test_supplied_stage_files.py` | 4 (`:11`, `:31`, `:58`, `:72`) | The stage functions are called with `hand_written_firmware` and `MISSION`; the model of `:72` is `test_package.example_model()`. In `:31` the four assertions that name files and a word of the case `tq9-v2` (`:42-44`, `:49`) go: they mean nothing on a hand-written firmware. |

`tests/worker_script.py` holds `write_scripted_worker(directory, *, hold_on_repair=False)`, a copy of `suite_fixtures.write_scripted_case_worker` (`tests/suite_fixtures.py:164-202`). The script it writes takes the tool's `resources/smoke/model.json` where the original takes the benchmark's smoke model. The original stays in `suite_fixtures.py` for the benchmark's tests that use it.

What `firmware-bench/tests/tool_fixtures.py` holds, each a copy that does what the original does:

- `package_env` (`tests/package_env.py`);
- `owner_ipc` and `service_pid` (`tests/owner_ipc_fixture.py`);
- from `tests/test_configurator.py`: `scripted_stages`, `wait_stopped` with `wait_worker_exit` and `WORKFLOW_TIMEOUT`, `gateway_identity`, `task_of`, `thermometer_model`, and `DeviceRepairTests.writing_thermometer`;
- from `tests/test_supplied_stages.py`: `BINDING`, `TemporaryDirectoryCase` and `hand_written_package`.

A name of this list that no moved test uses once the tests of 10-B4 are gone is not copied. The benchmark's tests import these names from `tool_fixtures`. The modules of the tool's package that they import today, they keep importing.

The tests 10-B4 deletes, by module and line. The letter says what the test's assertions depend on: L the list, D the text or a hash of a document or a build file, S a scan of the tool's source, G something gone, X another reason, given after the lines.

- `tests/test_benchmark_client_interface.py`: L `:77`, `:84`. G `:57`, `:63`.
- `tests/test_benchmark_commands.py`: L `:97`, `:109`, `:119`, `:124`, `:720`, `:726`, `:742`, `:763`, `:775`, `:783`, `:805`, `:818`, `:825`. D `:174`, `:427`, `:626`, `:641`, `:652`, `:659`, `:677`, `:699`, `:754`. G `:403`, `:464`, `:473`.
- `tests/test_benchmark_helper_copies.py`: L `:203`, `:209`, `:213`, `:219`.
- `tests/test_benchmark_legacy_cases_dropped.py`: L `:275`, `:293`, `:308`, `:318`, `:338`. G `:167`, `:220`, `:231`, `:246`, `:257`. X `:201`, the self-test of a scan whose tests go; `:287`, that the eight dropped items still exist.
- `tests/test_benchmark_runs.py`: L `:767`, `:925`, `:930`, `:936`, `:945`, `:951`, `:955`, `:978`. D `:837`, `:844`, `:863`, `:871`, `:876`. G `:563`, `:748`, `:996`, `:1026`. X `:728`, `:732`, `:737`, `:753`, scans of tool test files; `:851`, the self-test of a pattern whose tests go; `:1021`, which asserts what `tests/test_benchmark_supplied_stages.py:251` asserts.
- `tests/test_benchmark_suite_book.py`: L `:1868`, `:1875`, `:1882`, `:1903`, `:1922`. D `:1820`, `:1833`, `:1839`, `:1844`. X `:1802`, the self-test of a pattern whose tests go.
- `tests/test_benchmark_supplied_stages.py`: L `:336`, `:340`, `:347`, `:351`. S `:96`, `:174`. G `:59`, `:65`. X `:70`, the self-test of a scan whose tests go.
- `tests/test_benchmark_surface.py`: L `:1142`, `:1148`, `:1154`, `:1163`, `:1170`, `:1177`, `:1199`. S `:232`. G `:227`, `:905`. X `:270`, `:320`, `:1116`, which pin how the benchmark's own source is written; `:1213`, which runs two modules in one process for a fault the deleted tests had.

Every other test of these eight modules moves. `tests/test_assistive_wording.py` and `tests/test_tq9_replacement.py` move whole. The first holds documents to their wording, and Feature 11 settles it with the documents.

The tests 10-B4 deletes in the other modules, by module and line. P is the gated protocol's own machinery or another earlier format still being read, G something gone or a dropped name refused as any unknown one, D the stale wording of the skill text, S a scan of the tool's source, N a test that repeats another.

- `tests/test_benchmark_asset_build.py`: G `:372`.
- `tests/test_benchmark_mcp_reuse.py`: P `:22`, `:31`. N `:8`, `:11`, `:14`, which repeat `:17`.
- `tests/test_benchmark_scenarios.py`: G `:20`.
- `tests/test_benchmark_v2_evidence.py`: P `:97`, `:110`, `:121`, `:132`, `:138`, `:147`, `:153`, `:161`, `:168`, `:182`, `:199`, `:224`, `:393`.
- `tests/test_case_registry.py`: G `:126`, `:131`.
- `tests/test_corpus.py`: P `:279`, `:293`.
- `tests/test_device_recovery.py`: G `:54`. S `:39`. That scan also held `device_recovery.py` to a closed list of imports. It goes knowingly: 10-B9 holds what matters of it, that no tool module imports the benchmark.
- `tests/test_harness.py`: S `:179`.
- `tests/test_pairing.py`: D `:887`.
- `tests/test_reporting.py`: P `:25`, `:62`, `:215`.
- `tests/test_scorecard.py`: P `:1655`.
- `tests/test_slot_policy.py`: P `:385`.
- `tests/test_suite_reporting.py`: P `:54`, `:105`, `:119`, `:163`, `:217`, `:234`, `:246`, `:370`, `:426`, `:469`, `:485`, `:515`.
- `tests/test_suite_store.py`: P `:42`.
- `tests/test_benchmark_surface.py`, one of the eight modules above: P `:1096`, which asserts what `tests/test_suite_reporting.py:217` and `:246` assert.

Six tests that touch an earlier format are not on the list, because each also holds a rule of today that no other test holds: `tests/test_assistive_reports.py:472` and `:986`, `tests/test_corpus_preflight.py:215`, `tests/test_family_seam.py:506`, `tests/test_suite_reporting.py:133` and `tests/test_unlocked_keys.py:1000`. They move unchanged.

The earlier formats these tests read are mainly the gated protocol's run evidence and its report (`benchmark-run-evidence/1`, `benchmark-report/2`), the suite reports `/1` and `/2`, the corpus plan and report `/1`, registration rows such as the dropped legacy cases had, a scorecard saved before a later field, and a suite stored before later columns or before each case had its own grader identity. The code that reads them stays, since no module under `src/` changes. The report names every function and class of the benchmark's code that these deletions leave without a test: one that a deleted test named and that no remaining benchmark test file names. The session that runs this Run records them as noticed, for the firmware bench repository.

Fixtures that deleted tests shared with remaining ones. Three rules cover them.

- The test class `SealedEvidenceTests` (`tests/test_benchmark_v2_evidence.py:96`) goes with its tests. A remaining test or helper that took the evidence from that class takes it from its base class `SealedEvidenceFixture` (`:44`): `tests/test_assistive_reports.py:476` and `export_fixture` (`tests/test_suite_reporting.py:296`).
- `owner_fixture` (`tests/test_suite_reporting.py:198`) goes, since every test that used it is on the lists.
- Nothing else inside a helper or a method that stays is trimmed. A parameter, a branch or a mode name that only deleted tests used stays as it is, such as the parameters of `export_fixture` and the mode names at `tests/test_benchmark_tq9_v2.py:1267`.

Every other test of the sixty modules moves: it keeps its name and its assertions.

The three lines of `STANDARDS.md` for the owner. Line 12 ends with the new place of the test it names:

    `firmware-bench/tests/test_grader_identity.py` checks the boundary.

Line 29:

    Test command: `ls tests/test_*.py firmware-bench/tests/test_*.py 2>/dev/null | PYTHONPATH="$PWD/src" xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\n" "$out"; exit $s' && node tests/dashboard-client.test.js && GD_TEST_REMOVAL=1 PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -v -s tests -p test_deletion_marking.py`

Line 31:

    Test files: `tests/test_*.py`, `firmware-bench/tests/test_*.py`

The command works before and after the move: where `firmware-bench/tests/` is absent it runs the modules of `tests/` alone. The session that runs this Run checks the three lines and commits them before the Build begins. The list's count for `STANDARDS.md` is then off by two until the Build brings it in line (10-B10).

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise. The report lists every existing test that changed, moved, was copied or went, with the rule that covers it, and names every assertion that went. A change no rule covers is a Deviation.

- A test this Spec moves changes only what the move needs: its imports, its paths, and the names of the fixtures this Spec replaces.
- A tool test that holds the hosted step word for word (`tests/test_tool_commands.py:337-355`, `:400`) holds the new step, in a way that passes in the stripped copy as well. Its assertion that no line of the workflow says `bench` holds for the lines outside a marked passage. The hash of the workflow's text before the step stays as it is, since that text does not change.
- A test of the list's tests that says where a row stands follows 10-B8 and 10-B10: `tests/test_deletion_marking.py:738` holds `bench/README.md` under `Still used by the tool` and `benchmark_support/` under `Ready to delete`.
- `test_01_B7_the_hosted_test_step_sets_the_variable` (`tests/test_deletion_marking.py:667`) holds that the step sets the variable before it runs the tests of either folder.
- Two of the list's tests follow the `Freed by` values of 10-B10. `test_04_B7_takes_02_to_11_as_a_freed_by_and_reports_12_as_no_later_feature` (`tests/test_deletion_marking.py:191`) takes `02` to `12` and reports `13`. The test at `:171` gives its row the value `13` where it gave `12`.
- A test renamed under these rules is renamed only where its name states a value that changed.
- The check changes in one place: the `Freed by` values it takes (`tests/deletion_marking.py:28`). The row of `firmware-bench/tests/` covers the benchmark's test files, and tool test modules are found as before.

The scans. Each scan this Feature adds has a test of its own on planted sources, which shows that it sees each form it is meant to see. The scan of 10-B9 passes on the tree from the start, so its RED is taken on planted sources.

The archives. What 10-B1 says of `tests/test_distribution.py`, and the `MANIFEST.in` point of 10-B5, are shown only where `GD_TEST_WHEEL` and `GD_TEST_SDIST` are set. The implementer builds the wheel and the source archive from a copy of the tree, outside the clone, and runs the two distribution tests with both variables set. The hosted run shows the same.

The hosted tests. Windows does not run on this machine, and faults there show only in a hosted run. So the session that runs this Run commits the state of the Build to the Run's branch before the Feature is Done and pushes it, again if a run still fails, and gives the implementer and the Reviewer each run's result. The Feature is not Done before the workflow `Installed package` has passed on all four of its jobs for the tree the Reviewer passed. This is a rule and not a numbered Behaviour, because no test on one machine can show it.

A hosted failure. A fault in a test or a test helper is fixed in this Feature and reported as a Deviation. That covers a benchmark test that passed only because a tool test ran before it in the same process. A fault in product code is not fixed here: the session that runs this Run puts it to the owner.

The test command took 434 to 440 s at the end of Feature 09, and the kit stops it at 540 s. The removal now also runs the seven modules of 10-B2. If the whole command comes near the limit, say so in the report and trim nothing.

## Out of scope

- Product code. No module under `src/` changes. If a moved test shows a fault there, the report says so.
- The tests of the tool's own behaviour that need the bench harness's device. They stay with the benchmark's tests, by the owner's decision of 2026-11-18. After this Feature the tool tests the following only from `firmware-bench/tests/`: the instrument comparison on a live device (`test_quantity_repair.py`); device recovery, a call on another binding, and a restart before a recovery is committed (`test_device_recovery.py`); calls that take turns on a device, and the scripted whole runs through repair, recovery and fresh reuse (`test_benchmark_tq9_v2.py`, `test_benchmark_mcp_reuse.py`); operating conditions through the gateway (`test_development_conditions.py`); the device client's deadline and refusals (`test_harness.py`); `record_evaluation` on a real run (`test_evaluation.py`); a replay through all six stages (`test_replay_executor.py`); and stage notes and repair context (`test_pipeline_repair.py`, `test_public_missions.py`).
- The benchmark's code that reads earlier formats, and functions of it that nothing calls. They stay. Whether to remove them is left to the firmware bench repository.
- What the benchmark's tests import from the tool's package. They build the service from `configurator.Controller` and use private names of the tool. The interface lists none of them.
- Benchmark text in documents that stay, the wording `test_assistive_wording.py` requires, and the stale wording of the skill text itself. Feature 11. The test that held the skill text to that wording, `test_pairing.py:887`, is deleted here (10-B4).
- `.github/scripts/tool_releases.py` and `tool-releases.yml`, which still watch `bench/README.md`. Feature 11.
- The benchmark's own package, build file and the name `firmware_bench`. The dropped legacy cases stay in the benchmark's code and resources. Feature 12.
- The scans and pins that the tool's own test modules of earlier Features hold, other than those this Spec names.
- `real-ghidra.yml`, `CONTEXT.md`, `AGENTS.md`, approved Specs and the records of earlier Runs.

## Deviations

- Three new benchmark test files take names the implementer chose: `firmware-bench/tests/test_benchmark_distribution.py`, `test_benchmark_checkout.py` and `test_reference_tools.py` — the Spec says "a benchmark test file" three times and names none, and no file name may be in both folders.
- Tests named for 10-B1, and one for 10-B5, stand in those three files, outside the files the Spec gives for the two Behaviours — 10-B1 itself sends the wheel requirements, the answer-file loop and the Renode pin to benchmark test files, a test lies in the folder it guards, and the 10-B5 test shows the `MANIFEST.in` point on a real archive, which the Spec's paragraph on the archives expects.
- The moved answer-file loop gained `assertTrue(checked)` — it only stops the loop from passing where no manifest names an answer file, and asserts nothing new of the benchmark.
- Module docs were added to the three renamed tool modules and rewritten in the eight modules of this Run that stay with the benchmark — Standards ask every module for a doc, a doc that says its file reads the deletion list would be untrue after 10-B4, and each rewritten doc names exactly the Behaviours whose tests remain.
- `tests/worker_script.py` writes `scripted_stage_worker.py`, where its original wrote `scripted_suite_worker.py` — nothing reads the name, and "suite" is a benchmark word the tool's helper should not carry.
- In `test_interpret_receives_neutral_sealed_inputs_without_evaluator_answers` the local `names` went with its three assertions — it served only the assertions the Spec removes.
- `emitted_example` in `tests/test_emitted_package_calls.py` makes the four toolkit calls of the benchmark's smoke run, but not its replay child, its score or its report, and writes one-line JSON — the Spec asks for the toolkit calls, and the two tests read only the model, the replay and the package.
- `prepare_stage` and `observe_stage` on the hand-written firmware are defined once, in `tests/test_supplied_stage_files.py`, and `tests/test_emitted_package_calls.py` imports them — the Spec gives both modules the same replacement, and tool test modules already share helpers this way.
- The eleventh test of `tests/test_owner_generated_integration.py` is in the class `FreshReuseVerdictTests` of `firmware-bench/tests/test_evaluation.py`, and its import of `contract` and `EXPECTED` went — the Spec names the file and keeps the assertions, and the dropped import named the module the test now lives in.
- Of the three tests of the check that moved into the list's tests, one joined the class `CommandTests` and two, with their helper `at_a_time`, are the new class `FailedModulesTests`; they name `MODULE_SECONDS` and `failed_modules` directly — 10-B4 names no class, and the bodies are otherwise unchanged.
- `firmware-bench/tests/test_assistive_reports.py:477` lends the fixture the test's own `addCleanup` before it calls `setUp`, and the test's own cleanup line went — the base class is no test case and its `setUp` calls `addCleanup`; the fixture stays untrimmed, and the temporary folder is still removed when the test ends.
- `in_benchmark_package` takes `firmware_bench` only as the first part of a dotted name — 10-B9 names it as a package, and a package name is the first part of a dotted name.
- `removal_environment(copy)` was taken out of `failed_modules` in the list's tests, so that the new 10-B8 test gives its child the same environment — 10-B8 asks for the environment the removal gives its children, and the function returns what `failed_modules` built before.
- `tests/test_tool_commands.py` gained the helper `unmarked` with a test of its own, and one 09-B9 test now reports lines without their numbers — the Spec asks the 09-B9 tests to hold the step without its marked passages and every added scan to have a test on planted sources; the numbers were in a failure message only.
- `firmware-bench/tests/test_boundary.py` skips its two tests of the tool's folder where `tests/` is not beside it — the folder is going to a repository of its own; here both tests run.
- `tool_fixtures.package_env` has a doc its original lacks, and `writing_thermometer` is a function there, not a static method of a test class — Standards ask for the doc, and every copy equals its original as a syntax tree, docs aside.
- In the deletion list the sentence above `Still used by the tool` is reworded for documents, the reasons of the rows of `STANDARDS.md` and `docs/codex-acceptance.md` are extended (the count of the second rose from 3 to 4, since its line 37 now says `bench`), and the notes of the five new ready rows are the implementer's wording — 10-B10 needs the list true after this Feature and leaves the wording open.
- 10-B4 has no test that pins which tests went — the Spec gives 10-B4 a test for its last point only; the Reviewer checked the deletions against the two lists and they are exact.
- `ToyTransport` stays in `firmware-bench/tests/benchmark_family_fixtures.py` though no benchmark test uses it now — the Spec says nothing inside a helper that stays is trimmed, and its user became a tool test and was not deleted.
- The list's tests of 10-B5 were written after the move, and their RED is taken on a copy of the tree of `23e271c` — the RED shows each test failing without the move, for the Behaviour's reason.
- `test_10_B6_the_test_command_runs_each_module_of_both_folders_in_its_own_process_and_fails_when_one_fails` is skipped on Windows — the test command is a line of `sh`, and the skip departs from nothing the Spec says.
- The Feature was marked Done though the hosted workflow had not passed on all four of its jobs. Run `44376939839` for `6847bb6` passed on macOS with Python 3.11 and 3.13 and on Windows with Python 3.13, and failed on Windows with Python 3.11 in one test of 1770, `test_07_B9_a_book_starts_finds_and_seals_a_child_of_the_real_service_through_its_public_calls` — the owner's decision of 2026-11-18, in these words: "It passed with exception of windows 11 (I dont care) it passes as far as Im concerned." The failure reads as a race in that test's own wait, which this Feature did not change. It is recorded in `noticed.md` and not fixed here.

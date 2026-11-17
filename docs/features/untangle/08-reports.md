# Spec 08: reports

## Intent

For the owner, who is moving the benchmark to a repository of its own. Three things still tie the two sides together after Feature 07.

The first is the run report. `src/generative_driver/reporting.py` is a tool module, but what it writes is the benchmark's report: the formats `benchmark-report/1`, `/2` and `/3`, built from the case, the snapshot and the evaluator's records. It imports the benchmark at seven places, every test of it is a benchmark test, and its one caller in the tool is the command `generative-driver report`. The owner decided on 2026-11-17 that the report is the benchmark's and that the tool gets no report of its own. So the module moves to the benchmark as it is. The command keeps working and reaches the module there, until Feature 09 gives the command to the benchmark.

The second is the service. It imports the benchmark at three places. Two of them leave here: the summary it adds to `result` for the benchmark's reports, and the bench home it names when it checks a replay candidate. The third, the launch of the bench harness for an MCP client on Windows (`benchmark_spawn`), stays until Feature 09, as the Specs of Features 06 and 07 say.

The third is the benchmark's side. The benchmark imports tool names the interface does not list, private ones among them, and it works in the tool's home: it reads the database and the run folders, and it keeps files there. The owner decided on 2026-11-17 that this is settled in this Feature, and that what the benchmark reads and keeps in the tool's home is written down in the interface and not moved. So the interface, at version 4, lists every tool name a client may import and names the records on disk a client on the same machine may read. The benchmark's imports are cut down to that list. Its code that reads and keeps files stays as it is.

Terms used below:

- The **interface** is the document `docs/client-interface.md` and the module `src/generative_driver/client_interface.py`.
- The **report module** is `src/generative_driver/reporting.py` at the base commit, and `src/generative_driver/benchmark_support/run_reporting.py` after this Feature.
- The **benchmark's code** is `src/generative_driver/benchmark.py` and every module under `src/generative_driver/benchmark_support/`. A **tool module** is any other module under `src/generative_driver`, and `interface_runtime`.
- The **home** is the folder the service keeps its records in, and the **bench home** is the folder `<home>-bench` beside it (`trial_store.bench_home`).
- A **supplied run** and a **scripted run** are those of the Specs for Features 06 and 07.
- Line numbers are those of commit `ecd08a3`.

Where something behaves differently after this Feature:

- `import generative_driver.reporting` fails. The report's names are in the benchmark's module.
- `result` has no `benchmark_summary`. The service built it from three keys of a run's state file, `accepted_gates`, `evaluations` and `final_evaluation`, which no code writes since the gated protocol left. Only `suite_reporting._recorded_success` reads it, when it judges a report of that earlier protocol. Such a report is then no recorded success. The Breakdown lets records of before Feature 06 go.
- A `start` with the executor `replay` that a client sends straight to the service is no longer refused because its candidate lies in the bench home: the service does not know that folder. The benchmark refuses a candidate in the bench home it works in, as before and with the same text. One case changes: a trial launched with another bench home (`options.bench_home`) had its candidate held out of `<home>-bench` too, by the service, and that second check is gone.
- A run that is blocked because its cleanup failed has the reason `Cleanup failed; operator reconciliation required`. It said `Benchmark cleanup failed; operator reconciliation required`.
- `replay.candidate_config` takes the configuration alone. Its parameter `bench` is gone.
- Where the bench harness raised the tool's `IntegrityError`, it raises the benchmark's own class of the same name, a `ValueError` with the same texts. `harness.conclude` counts that class alone as an integrity stop. Nothing the harness calls while it grades raises the tool's class.

## Behaviours

- **08-B1** — The run report's code is the benchmark's.
  - `src/generative_driver/benchmark_support/run_reporting.py` holds `summarize`, `markdown`, `report_run`, `_usage`, `_interventions`, `_public`, `_awaiting`, `_why` and `_tree_hash`, each with the behaviour it had in the report module.
  - `src/generative_driver/reporting.py` is gone, and no tool module but `cli.py` imports the report module.
  - For the same run, `report_run` writes the same `report.json` and the same Markdown file beside it as before, in each of the formats `benchmark-report/1`, `benchmark-report/2` and `benchmark-report/3`.
  - For a run without a snapshot, `toolchain_revision` and `skills_revision` hash the same folders as before: `src/generative_driver` and `interface_runtime` for the first, `src/generative_driver/resources/toolchain/skills` for the second.
  - A suite's report, a scorecard and a suite's answers that use these names hold what they held.
- **08-B2** — `generative-driver report` gives what it gave.
  - `generative-driver report RUN_ID` prints the report and writes `report.json` and `report.md` in the run's folder, and with `--output PATH` it writes there, as before.
  - `cli.py` imports `report_run` from the benchmark's module, inside the branch of the `report` command.
- **08-B3** — The grader's list of modules that are not grading code follows the move.
  - `grader_identity.SYSTEM_MODULES` names `benchmark_support.run_reporting` and `benchmark_support.process_lock`, and no longer `reporting`.
  - Grading code imports neither of the two new modules, and the test that guards the grading boundary passes.
- **08-B4** — `result` has no `benchmark_summary`.
  - `result` of a supplied run has no key `benchmark_summary`, also when the run's state file holds `accepted_gates`, `evaluations` or `final_evaluation`.
  - Every other key of `result` is as before.
  - `suite_reporting._recorded_success` is unchanged. Given a `result` with a summary it judges as before, and given one without it answers False where it read the summary. The benchmark's tests show this.
- **08-B5** — The service does not know the bench home.
  - `replay.candidate_config(config)` takes the configuration alone and makes every check it made but the one on the bench home, with the same texts in the same order.
  - A `start` with the executor `replay` is checked by the service as before but for that one refusal.
  - Neither `replay.py` nor `configurator.py` holds the text `bench home`, and `configurator.py` names `bench_home` only in the branch of `benchmark_spawn`.
- **08-B6** — The benchmark itself refuses a replay candidate that lies in the bench home.
  - `trial_store.replay_candidate(candidate, bench)` gives what `replay.candidate_config({'candidate': candidate})` gives. Before that, it refuses a candidate that is non-empty text, is an absolute path and lies in the folder `bench`, with `A replay candidate never lies in the bench home, which holds evaluator records: <candidate>`.
  - It tells that a path lies in `bench` by file identity, as before: another spelling of the path, such as other letter case on a file system that ignores case, is refused too.
  - A candidate that is no text or is empty is refused in the tool's words, as before.
  - `harness.launch` and `suite_book.suite_candidates` use it, and `suites.start_suite` through the second. So `benchmark run` with the replay executor, `benchmark suite start` of a replay suite and the suite call `suite_start` refuse such a candidate as before: before a harness starts, and before anything is stored.
- **08-B7** — The service imports the benchmark only to launch the bench harness.
  - The imports of benchmark code in `configurator.py` are exactly `harness._spawn` and `trial_store.bench_home`, `solo_trial_dir` and `suite_dir`, all inside the branch of `benchmark_spawn` in `Controller.call`.
  - `benchmark_spawn` takes, checks and answers what it did.
  - The tool modules that import the benchmark are exactly `cli.py`, `mcp.py` and `configurator.py`.
- **08-B8** — The tool's status text for a failed cleanup is in the tool's terms.
  - A run whose release raised, and that has no final verdict yet, is blocked with the reason `Cleanup failed; operator reconciliation required` and the outcome category `host`, and its effect is marked uncertain, as before but for the text.
  - No tool module holds the text `Benchmark cleanup failed`.
- **08-B9** — The interface is version 4, and its module lists what a client may import.
  - `client_interface.VERSION` is 4, and the document's first line is `# Client interface, version 4`.
  - `client_interface.IMPORTS` is a mapping from a module's name to the tuple of names a client may take from it:
    - `generative_driver.client_interface`: `CALLS`, `IMPORTS`, `PROTOCOL`, `VERDICT_FIELDS`, `VERSION`, `device_errors`, `firmware_errors`, `frozen_driver`, `mission_errors`, `verdict_errors`;
    - `generative_driver.client`: `call`, `default_home`;
    - `generative_driver.integrity`: `digest`;
    - `generative_driver.setup`: `SUPPORTED_EXECUTORS`, `resolve_executor`;
    - `generative_driver.replay`: `candidate_config`;
    - `generative_driver.toolkit`: `call_tool`, `resources_root`;
    - `interface_runtime.engine`: `validate_model`;
    - `interface_runtime`: no name. The package may be imported for where it is installed.
  - `client_interface.__all__` gains `IMPORTS` and is otherwise unchanged. `CALLS`, `PROTOCOL` and `VERDICT_FIELDS` are unchanged.
  - Every module `IMPORTS` names can be imported, and every name it lists is a name of that module.
- **08-B10** — The document's section `## What a client imports` says the same as `IMPORTS`.
  - It lists exactly the modules and names of `IMPORTS`.
  - It says of each addition what it is, in the tool's terms:
    - `SUPPORTED_EXECUTORS` are the executors a `start` takes. `resolve_executor(executor)` gives the executor a `start` runs with, the default for none, and refuses one the tool does not have, as `start` does.
    - `candidate_config(config)` checks the saved candidate of a replay, given as `{"candidate": <directory>}`, and gives the `executor_config` of a `start` with the executor `replay`: the candidate's absolute path and the hash of its files, as `start` checks them.
    - `call_tool(name, arguments)` calls one of the tool's developer tools in this process, the ones `generative-driver tool` calls. `resources_root()` is the folder of the tool's installed assets.
    - `interface_runtime` is the runtime a driver package runs on. `validate_model` of `interface_runtime.engine` checks a device model without opening a transport.
  - It says that any listed module may be imported for where it is installed.
  - It keeps the sentence `Nothing else in the tool is part of this interface.`
- **08-B11** — The document has a section `## Records on disk`, between `## Stop or seal a run` and `## What a client imports`, and each thing it says is true.
  - The home is the folder `default_home()` names when a client gives none. A client on the same machine may read what this section names, without asking the service.
  - `config.json` in the home holds the operator's settings. Its `executors` object holds, by the name of an executor, the settings a run has when its `start` gives no `executor_config`. The file may be absent.
  - `runs.sqlite3` in the home is the SQLite database of the service's records, and a client opens it read-only. The section names these tables and columns: `runs` with `id` and `request_id`; `events` with `seq`, `run_id`, `kind`, `payload` and `created`; `reports` with `run_id`, `stage`, `payload` and `created`; `handoffs` with `run_id`, `payload` and `created`, read in the order they were written, which is SQLite's own `rowid`; `verdicts` with `id` and `payload`, where the final verdict of a run has the id of the run followed by `:final`; and `progress` with `run_id` and `payload`. A `payload` is JSON text. The rows are what `events` and `result` are made from.
  - `runs/<run id>/` in the home is the run's folder, and a client may read every file in it. `supplied/package-events.jsonl` there holds one JSON object on a line for each call a worker made on the driver package, with `actor`, `stage`, `operation`, `revision`, `attempt_id`, `assignment_id`, `package_sha256`, `parameters` and `result`; with `mcp` when the call came through a server of the package; and with `observation` and `before_observation` when the device offers readings. Every file named `_agent_events.jsonl` under the run's folder is the transcript of one worker. An interpret worker works in a temporary workspace outside the run's folder, which its `stage.assigned` event names as `workspace`.
  - The service makes these folders directly in a run's folder: `supplied`, `work`, `accepted`, `checks`, `preparation`, `tool-calls`, `snapshots` and `replay-candidate`. A client may add files and folders of its own directly in a run's folder under other names, and the service never changes or removes them.
  - A client may keep saved candidates for the replay executor in the folder `replay-candidates` in the home. The service never changes or removes that folder, and reads in it only a candidate a `start` names.
  - Once a run has stopped, with no worker of it still stopping, and is sealed, the client may remove the files of the run's folder and those temporary workspaces. `status`, `events` and `result` of the run answer as before without them.
  - No line of the document says `bench` or `suite`.
- **08-B12** — The benchmark's code imports from the tool only what `IMPORTS` lists.
  - Every import of a tool module in the benchmark's code names a module of `IMPORTS` and takes only names listed for it. Where the code imports a listed module itself, it reads from it only listed names and `__file__`.
  - What goes: every import from `configurator` (`IntegrityError`, `PROTOCOL` and `digest`, at `harness.py:396`, `physical.py:209` and `suite_reporting.py:602`); `integrity.IntegrityError` (`harness.py:327`); `setup.home_path` (`authoring.py:11`); `replay.RUNTIME` (`suite_book.py:76`); the three lock helpers of `client` (08-B13); every import of `reporting`, which is the benchmark's own module now; and the report module's own import of `equivalence_label` from `configurator` (`reporting.py:179`).
  - In their place: `PROTOCOL` comes from `client_interface`, `digest` from the tool's `integrity`, `IntegrityError` from the benchmark's own `integrity`, the home's path from `default_home`, the runtime of a stored candidate from the configuration `candidate_config` gave, and `equivalence_label` from the benchmark's `labels`, which words a verdict as the tool's does.
  - What the code does with these names is unchanged: the same refusals with the same texts, the same records and the same hashes. One thing differs: `harness.conclude` counts only the benchmark's own `IntegrityError` as an integrity stop, where it also counted the tool's.
- **08-B13** — The bench harness tells whether a harness runs with lock helpers of its own.
  - `src/generative_driver/benchmark_support/process_lock.py` holds `hold_process_lock`, `process_lock_held` and `wait_for_exit`: copies of the tool's `client.hold_process_lock`, `client.process_lock_held` and `client._wait_for_exit`, with the behaviour of each on macOS, Linux and Windows.
  - The bench harness and the suite book use them where they used the tool's (`harness.py:635`, `:1482`, `:1664`; `suite_book.py:373`, `:379`).
  - Each copy gives the tool's answer: a lock the copy holds is one the tool's function sees held, and the other way round; both say the same of a lock nobody holds and of a file that is missing; and both say the same of a process that has ended and of one that runs.
- **08-B14** — The benchmark's code sends the service only calls the interface lists, and the launch.
  - Every method the benchmark's code sends to the service is one of `client_interface.CALLS`, but for `benchmark_spawn`.
  - `benchmark_spawn` is sent from `harness._spawn_via_owner` alone.
  - Every request the benchmark's code sends during a scripted trial and a scripted suite is a method of `CALLS`. A worker's own requests to the service, such as `tool`, are not the benchmark's.
- **08-B15** — The benchmark's code reads in the home only what `## Records on disk` names.
  - It opens `runs.sqlite3` at three places, `mcp_reuse.owner_evidence`, `scorecard._records` and `trial_store._is_run_of_suite`, read-only, and every statement there names only tables and columns the section names.
  - It reads `config.json` at two places, `harness.settled_executor` and the suite book's settling of a suite's executor, and takes only `executors` from it.
  - It names no other file of the home outside `runs/` and `replay-candidates/`: not `service.json`, a lock file or a log of the service.
  - These places are unchanged by this Feature but for their imports.
- **08-B16** — The deletion list is true after the move.
  - The row of `src/generative_driver/reporting.py` is gone from `Tool modules that import the benchmark`, and the row of `src/generative_driver/configurator.py` there names Feature 09. No row of the list names Feature 08 any more.
  - The two rows under `Still used by the tool` that count those modules, for `benchmark.py` and for `benchmark_support/`, say three.
  - The benchmark's test module of this Feature is under `Ready to delete`, and the tool's test module of this Feature is not on the list.
  - Every count in the list is true, its check reports no problem, and the removal test passes.

## Interface and seams

Tests. The tool's tests of this Feature are in the new file `tests/test_client_surface.py`. It imports nothing of the benchmark and names no registered case, and `tool_test_modules` names it. The benchmark's tests of this Feature are in the new file `tests/test_benchmark_surface.py`. Tests are named `test_08_B<n>_`. Other Runs left tests named `test_08_B…` in `tests/test_output_measure.py`, `tests/test_generated_family.py`, `tests/test_scorecard.py`, `tests/test_slot_policy_flags.py` and `tests/test_trial_sealing.py`; they are not this Feature's.

Which side tests what. In the tool's file: 08-B4 but for its last point, 08-B5, 08-B7, 08-B8, 08-B9, 08-B10 and 08-B11. The last point of 08-B4 is about a benchmark function, and the benchmark's tests show it. A statement of `## Records on disk` is shown true on a scripted run of the tool's own, on `tests/supplied_device.py`: the tables and columns exist in the database, a line of `package-events.jsonl` has the keys named, the folders directly in the run's folder are among the eight named, a file and a folder a client added there under other names are unchanged when the run has ended, and after the run has stopped and is sealed the three calls answer the same with the run's folder emptied. If the source shows that the service makes another folder directly in a run's folder, the document names it too, and the report says so as a Deviation. In the benchmark's file: 08-B1, 08-B2, 08-B3, 08-B6, 08-B12, 08-B13, 08-B14, 08-B15 and 08-B16. 08-B12, 08-B14 and 08-B15 are shown by reading the benchmark's source as a syntax tree, and 08-B14 also by a scripted trial and a scripted suite against a service that records the methods it receives.

The move. The report module moves as it is: its names, its texts and its three formats are unchanged, and nothing in it is fixed or tidied. Its imports of the benchmark become imports within the benchmark: `STAGES` and `case_root` from `case_assets`, `tree_hash` from the benchmark's `integrity`, still under the name `_tree_hash`, and the rest by their own modules. It takes `PROTOCOL` from `client_interface`, `call` and `default_home` from `client`, and `equivalence_label` from the benchmark's `labels`. Where it found the tool's folders from its own place in the tree, it finds the same folders from its new place. `suite_reporting.py` and `suite_book.py` import its names from it.

Grading code. The only edit of grading code is the tuple `SYSTEM_MODULES` in `grader_identity.py`. That changes every grader identity once more, as the Breakdown says of Features 06 and 07.

The dropped legacy modules. `cases.py` and `physical.py` are ready to delete and nothing imports them. They are held to 08-B12 by the smallest edit of their import lines, and nothing else in them changes.

The launch. `benchmark_spawn`, `harness._spawn_via_owner` and the three MCP tools that use them are unchanged. They are the one call and the one import that stay outside the interface until Feature 09.

`trial_store.replay_candidate`. The check on the bench home needs the tool's rule of file identity (`replay._inside`). The benchmark has its own copy of that rule; it does not import the private name.

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise.

- A test that imports `generative_driver.reporting`, or patches a name of it, uses `generative_driver.benchmark_support.run_reporting`. That is 13 test files, all of them benchmark items.
- A test that pins version 3 of the interface, its `__all__`, or the headings of the document follows the new values, and is renamed where its name states a value that changed.
- A test that pins a row, a count or a `Freed by` value of the deletion list follows 08-B16, and is renamed where its name states a value that changed: `test_04_B7_every_freed_by_names_feature_08_09_or_10_or_stays` in `tests/test_benchmark_legacy_cases_dropped.py`, `test_06_B24_every_count_of_files_in_a_rows_reason_is_true` in `tests/test_benchmark_runs.py`, and `test_07_B17_the_service_is_listed_as_a_tool_module_that_still_imports_the_benchmark` in `tests/test_benchmark_suite_book.py`.
- Three tests make a patched grading function raise the tool's `IntegrityError` and assert an integrity stop: `test_06_B11_a_grade_unscored_for_integrity_stands_with_no_regrade_or_rerun` in `tests/test_slot_policy.py`, and `test_06_B11_a_grade_unscored_for_integrity_is_a_failure_in_the_report_and_its_saved_form` and `test_06_B1_a_development_suite_is_not_held_to_the_scoring_rule_for_integrity` in `tests/test_slot_policy_flags.py`. They raise the benchmark's own `integrity.IntegrityError` instead.
- The two tests of the benchmark imports that remain in the service, `test_07_B5_the_benchmark_imports_that_remain_in_the_service_are_exactly_the_named_ones` in `tests/test_client_calls.py` and `test_06_B8_the_benchmark_imports_that_remain_are_the_named_ones` in `tests/test_supplied_runs.py`, keep their names and name the imports of 08-B7.
- `test_06_B22_result_builds_the_summary_of_a_supplied_run_from_supplied_state` in `tests/test_supplied_runs.py` and `test_owner_v2_summary_projects_only_gate_identity_and_final_counts` in `tests/test_suite_service.py` are removed: the summary they test is gone. The tests of `tests/test_suite_reporting.py` that put a summary into a `result` they stand in for are unchanged.
- In `tests/test_replay_executor.py`, the assertion that `Controller.start` refuses a candidate in the bench home (`test_a_replay_starts_only_from_a_checked_candidate_on_an_emulated_case`, `:314-315`) is made of `harness.launch` instead. The other assertions of that test and the tests through the command line are unchanged.
- A test that says which tool names the suite book takes (`tests/test_benchmark_suite_book.py:823`) follows 08-B12.
- A test that patches a lock helper of `generative_driver.client` for the bench harness (`tests/test_suite_harness.py:1172-1177`) patches the benchmark's copy. A test that only holds or reads a harness's lock with the tool's function may keep doing so: the copies and the tool's functions agree.

The Test command took about 430 s at the base, and the kit stops it at 540 s. The new tests are small. If the whole command comes near the limit, say so in the report and trim nothing.

## Out of scope

- `benchmark_spawn`, and what the benchmark's MCP tools do on Windows without it. Feature 09.
- The command line and the MCP entry points, the move of the `report` command to the benchmark's own command, and the documents that show the command (`bench/README.md:99`, `docs/benchmark-case-authoring.md:208`). Feature 09.
- Moving what the benchmark keeps in the tool's home to the bench home, and reading the tool's records through calls. The owner decided on 2026-11-17 that it is written down instead: the snapshot under a run's folder, the stored replay candidates, the report files, the direct reads of the database, and the removal of a sealed run's files stay as they are.
- How the benchmark names the revision of the tool's code. It hashes the installed folders, and Feature 11 settles where those are once there are two packages.
- Other words of the benchmark in the tool's texts, names and comments: the `result` key `evaluator_verdicts`, `start a new trial` (`configurator.py:1416`), `emulated STM32 benchmark` (`setup.py:374`), the refusals of a `start` that names a case, and comments. Only the one status text of 08-B8 changes.
- The copies of `STAGES` in the tool and in the benchmark.
- Fixes to the reports, to `_recorded_success` and to anything else that moves or stays.
- Tests. They are not held to `IMPORTS`; Feature 10 splits them.
- `CONTEXT.md` and the decision records under `docs/adr/`.

## Deviations

# Spec 09: commands

## Intent

For the owner, who is moving the benchmark to the firmware bench repository. Generative-driver is the toolkit and the workflow, and firmware bench evaluates it from outside. After Feature 08 the tool still imports the benchmark in three modules, and each is a way in for the benchmark's commands.

The first is the command line. `generative-driver benchmark …` hands its words to the benchmark's parser, and `generative-driver report` calls the benchmark's report. The benchmark has no command of its own: its parser can be reached only through the tool's.

The second is the MCP server. Seven of the tools of `generative-driver-mcp` are the benchmark's: they start a trial, and start, read, cancel and resume a suite.

The third is the service. On Windows, an MCP client's child processes end when the client closes. So the benchmark's MCP tools ask the service to start the bench harness for them there, with the call `benchmark_spawn`. It is the service's last import of the benchmark.

This Feature gives the benchmark its own two commands and takes all three ways out of the tool. The owner decided three points for it on 2026-11-18:

- The benchmark's commands are called `firmware-bench` and `firmware-bench-mcp` already now, so that documents and tests change once. Feature 11 renames the package.
- The benchmark gets no background process of its own to start a harness on Windows. Its MCP tools that start or resume refuse there and name the command for a terminal.
- The tool gets no command that posts a verdict. The way to accept one verdict from an outside grader is the service's call `record_evaluation`, which the interface lists, and it stays as it is.

The Feature has a first part that is not about commands. On 2026-11-18 `main` was merged into this Run at `fcd078c`. It removed plans and Specs of earlier Runs, and the deletion list still names them, so seven tests fail on the merge. 09-B1 brings the list in line.

Terms used below:

- The **interface** is the document `docs/client-interface.md` and the module `src/generative_driver/client_interface.py`.
- The **benchmark's code** is `src/generative_driver/benchmark.py` and every module under `src/generative_driver/benchmark_support/`. A **tool module** is any other module under `src/generative_driver`, and `interface_runtime`.
- The **benchmark's parser** is the command-line parser in `benchmark.main`.
- The **seven tools** are `driver_benchmark_run`, `driver_benchmark_suite_start`, `driver_benchmark_suite_status`, `driver_benchmark_suite_events`, `driver_benchmark_suite_result`, `driver_benchmark_suite_cancel` and `driver_benchmark_suite_resume`.
- An **agent case** is a registered case whose `execution` starts with `actual-agent-`. The smoke case `setup-smoke` is not one: it replays a recording in the caller's process.
- The **home** is the folder the service keeps its records in.
- Line numbers are those of commit `fcd078c`.

Where something behaves differently after this Feature:

- `generative-driver benchmark …` and `generative-driver report …` end as any command the tool does not know. `firmware-bench …` and `firmware-bench report …` take their place.
- The benchmark's help and usage texts say `firmware-bench`. They said `gd benchmark`.
- `firmware-bench report` of a run that cannot be read prints `{"ok": false, "error": …}` and ends with status 1, as the benchmark's other commands answer an error. `generative-driver report` ended with a traceback.
- `generative-driver-mcp` no longer offers the seven tools. `firmware-bench-mcp` offers them. The tool's plugin registers only the tool's server, as before, so a client that wants the seven tools registers the benchmark's server beside it.
- On Windows, `driver_benchmark_run` of an agent case, `driver_benchmark_suite_start` and `driver_benchmark_suite_resume` refuse, also for a call that repeats a request id. From a terminal the same work starts as before.
- The service answers `benchmark_spawn` as it answers any method it does not know.
- `benchmark.run`, `harness.launch`, `harness.launch_suite`, `suites.start_suite` and `suites.resume_suite` have no parameter `via_owner`.
- The hosted tests no longer run the benchmark's smoke case as a command, and `README.md` checks an installation with the tool's own smoke.
- Every grader identity changes once more (09-B10), as the Breakdown says of Features 06 to 08.

## Behaviours

- **09-B1** — The deletion list names only what the merge left.
  - Seven rows that name files `main` removed are gone. Under `Ready to delete`: `docs/features/bench-harness/`, `docs/superpowers/plans/2026-10-28-tq9-replacement.md`, `docs/superpowers/plans/2026-10-30-grader-identity.md`, `docs/superpowers/plans/2026-10-30-scorecard.md` and `docs/superpowers/plans/2026-11-01-generated-grader.md`. Under `Kept`: `docs/superpowers/plans/2026-10-28-driver-contract-mcp.md` and `docs/superpowers/specs/2026-10-28-driver-contract-mcp-design.md`.
  - No other row changes for the merge. The file the merge added, `.scratch/benchmark-review/bench-harness-noticed.md`, is covered by the row of `.scratch/benchmark-review/`.
  - Every row of the list names a file or folder that exists.
- **09-B2** — The benchmark has a command of its own, `firmware-bench`.
  - `pyproject.toml` declares the command `firmware-bench` as `generative_driver.benchmark:main`, and `python -m generative_driver.benchmark` runs the same function.
  - `firmware-bench WORDS` does what `generative-driver benchmark WORDS` did, for every command of the benchmark's parser: the same printed output, the same exit status and the same files.
  - `firmware-bench --home H WORDS` does what `generative-driver --home H benchmark WORDS` did. `GENERATIVE_DRIVER_HOME` is H for the process, so every command that finds the home itself works in H. `--home=H` does the same, which the tool's handover did not take.
  - `run` keeps its own `--home`. `firmware-bench --home A run --home B …` does what `generative-driver --home A benchmark run --home B …` did: `GENERATIVE_DRIVER_HOME` is A, and the trial is launched with `options.home` B.
  - The parser's program name is `firmware-bench`, so help, usage and error texts say that.
  - With no command, or one the parser does not know, it ends with the parser's usage error and status 2, as before.
- **09-B3** — The run report's command is `firmware-bench report`.
  - `firmware-bench report RUN_ID` prints the report and writes `report.json` and `report.md` in the run's folder, and with `--output PATH` it writes there, as `generative-driver report` did. With `--home H` before `report` it reads the run in H.
  - It ends with status 0 whenever it wrote the report, whatever the report says.
  - For a run that cannot be read it prints `{"ok": false, "error": …}` with the report's own refusal text and ends with status 1.
- **09-B4** — The benchmark has an MCP server of its own, `firmware-bench-mcp`.
  - `src/generative_driver/benchmark_support/mcp_server.py` holds a server named `firmware-bench` that speaks MCP over stdio. `pyproject.toml` declares the command `firmware-bench-mcp` as that module's `main`, and `python -m generative_driver.benchmark_support.mcp_server` runs the same function.
  - It offers exactly the seven tools. Each has the name, the parameters, the input schema and the description it had on the tool's server, but for the descriptions 09-B5 changes.
  - On macOS and Linux each tool answers what it answered on the tool's server.
  - It finds the home as the tool's server does, from `GENERATIVE_DRIVER_HOME` or the default. On macOS and Linux, `driver_benchmark_run` of an agent case still refuses an `options.home` that is another home, with the same text.
- **09-B5** — On Windows the benchmark's MCP tools refuse to start what they cannot keep running.
  - `driver_benchmark_run` of an agent case answers `{"ok": false, "reason": "On Windows a bench harness does not outlive the MCP client that starts it. Run firmware-bench run in a terminal instead."}`.
  - `driver_benchmark_suite_start` answers the same with `firmware-bench suite start`, and `driver_benchmark_suite_resume` with `firmware-bench suite resume`.
  - Each refuses before it reads its other arguments. Nothing is asked of the service, no harness is started, and no file or folder is made. One check comes first in `driver_benchmark_run`: a profile that names no registered case is refused as before.
  - So on Windows the home is not checked, and a call that repeats a `request_id` is refused too, though it would start nothing. The second call for a suite that waits for its vault to close is made from a terminal there, with `firmware-bench suite start --request-id`.
  - `driver_benchmark_run` of `setup-smoke` runs as before.
  - The four tools that read or cancel a suite answer as before. As before, they do not start a service that is not running.
  - A tool tells that it is on Windows by `platform.system() == 'Windows'`, as before.
  - The description of each of the three tools says in one sentence that it refuses on Windows and names the command. In `driver_benchmark_suite_start` that sentence takes the place of `Windows requires an independently started configurator.`
- **09-B6** — The bench harness is started in one way.
  - `harness._spawn_via_owner` and `harness._launch_payload` are gone, and with them the launch channel `bench-launch-channel/1`. The harness's own `main` takes the request it reads from its input as it is.
  - `benchmark.run`, `harness.launch`, `harness.launch_suite`, `suites.start_suite` and `suites.resume_suite` have no parameter `via_owner`. Every launch goes through `harness._spawn`, which is unchanged.
  - From a terminal on Windows, `firmware-bench run`, `firmware-bench suite start` and `firmware-bench suite resume` start the harness as `generative-driver benchmark …` did there.
  - Every method the benchmark's code sends to the service is one of `client_interface.CALLS`, with no exception, and the benchmark's code holds no text `benchmark_spawn`.
- **09-B7** — The tool's command line offers nothing of the benchmark.
  - `generative-driver benchmark …` and `generative-driver report …`, with or without `--home H` before them, end with the parser's error for a command it does not know and status 2.
  - `generative-driver --help` names neither.
  - The commands of `generative-driver` are exactly `doctor`, `configure`, `setup`, `service`, `smoke`, `dashboard`, `run`, `tools`, `tool`, `status`, `events`, `result`, `cancel`, `resume` and `respond`, each with the arguments and the behaviour it had.
- **09-B8** — The tool's MCP server offers nothing of the benchmark.
  - `generative-driver-mcp` offers exactly `driver_doctor`, `driver_start`, `driver_status`, `driver_events`, `driver_cancel`, `driver_result`, `driver_resume` and `driver_respond`, each with the parameters, the input schema, the description and the behaviour it had.
- **09-B9** — No tool module imports the benchmark, and the service does not launch the bench harness.
  - A `benchmark_spawn` sent to the service is refused as any method the service does not have: with `Unknown method: benchmark_spawn` when the request names a run the service knows, and with `Unknown run_id` otherwise. No process is started. The order in which the service looks at a request is unchanged.
  - No tool module imports the benchmark's code: no import statement, at the top of a module or inside a function, names `generative_driver.benchmark` or `generative_driver.benchmark_support`, as an absolute or a relative import, and no tool module gives either name as a literal text to `import_module` or `__import__`.
  - `configurator.py` holds neither the text `benchmark_spawn` nor the name `bench_home`.
  - The interface is unchanged: version 4, the same `CALLS`, `IMPORTS` and document. `record_evaluation` is one of `CALLS`.
- **09-B10** — The grader's list of modules that are not grading code names the new module.
  - `grader_identity.SYSTEM_MODULES` names `benchmark_support.mcp_server`.
  - Grading code does not import it, and the test that guards the grading boundary passes.
- **09-B11** — The hosted tests and `README.md` use the tool's own smoke.
  - The step `Test outside the checkout` of `.github/workflows/test.yml` runs the tests and then `generative_driver smoke`, each with `check=True`. It no longer runs `benchmark run --case setup-smoke`. No other line of the workflow changes, and no line of it says `bench`.
  - In `README.md`, each of the three install blocks shows the tool's smoke where it showed the benchmark's smoke case: `.venv/bin/python -m generative_driver smoke`, `.\.venv\Scripts\python.exe -m generative_driver smoke` and `uv run generative-driver smoke`.
  - The sentence after the blocks reads `The smoke runs one whole run through a service of its own, with a scripted worker and a stand-in device. It needs no AI account or hardware and measures no model performance.` It took the place of the two sentences that started `Use a new output directory each time.`
  - The benchmark's smoke case still runs in the tests, through `python -m generative_driver.benchmark run --case setup-smoke`.
- **09-B12** — The documents show the commands that exist.
  - In `bench/README.md`, `bench/groundtruth/README.md`, `docs/benchmark-case-authoring.md`, `docs/benchmark-corpus.md` and `docs/verification.md`, every line that shows a command line of the benchmark shows it with `firmware-bench`:
    - `generative-driver benchmark WORDS` and `python -m generative_driver benchmark WORDS` become `firmware-bench WORDS`;
    - `generative-driver --home H benchmark WORDS` becomes `firmware-bench --home H WORDS`;
    - `generative-driver --home H report WORDS` becomes `firmware-bench --home H report WORDS`, and `gd report <run>` becomes `firmware-bench report <run>`;
    - in prose, a code span that starts with `benchmark` and a command of the benchmark's parser, such as `benchmark suite resume`, starts with `firmware-bench` instead. That is 13 spans on 11 lines: `bench/README.md:25` and `:222`; `docs/benchmark-case-authoring.md:105`, `:153`, `:166`, `:168`, `:202`, `:204`, `:205` and `:206`; and `docs/verification.md:112`.
  - A line that shows a command of the tool, such as `generative-driver --home H configure …`, `service start`, `status RUN_ID` or `result RUN_ID`, is unchanged.
  - `bench/README.md` says, where it describes the MCP tools (`:108`, `:170`): that `firmware-bench-mcp` serves the seven tools; that a client registers it beside the tool's server, with the same `GENERATIVE_DRIVER_HOME`; that on Windows the three tools of 09-B5 refuse, so a trial or a suite is started and resumed from a terminal there; and that the tools that read or cancel need a separately started service on Windows, with the link the last sentence of `:108` has today.
  - Outside `docs/features/`, `docs/adr/`, `docs/research/` and `.scratch/`, no document shows `generative_driver benchmark`, `gd report`, or `generative-driver` with `benchmark` or `report` as its command, which is its next word after an optional `--home H`.
  - Nothing else in these documents changes.
- **09-B13** — The deletion list is true after this Feature.
  - `Tool modules that import the benchmark` has no row. The table keeps its heading and its column names, and the sentence above it reads `No tool module imports the benchmark package.`
  - `Commands` has no row, and its table stays in the same way. The sentence above it reads ``The tool offers no benchmark command. The benchmark's own commands are `firmware-bench` and `firmware-bench-mcp`.`` The check of the list still reports a benchmark command the tool offers as a problem.
  - Under `Passages`, the row of `.github/workflows/test.yml` and the row of `README.md` that was freed by 09 are gone. The other two rows of `README.md` are true.
  - The two lines of `pyproject.toml` that declare the benchmark's commands sit in one marked passage, so `pyproject.toml` has no row, and the stripped copy's `pyproject.toml` declares only the tool's two commands.
  - No row of the list names Feature 09.
  - Under `Still used by the tool`, each reason is true: the rows of `benchmark.py` and `benchmark_support/` name the still-used tests and no tool module or command, and the row of `src/generative_driver/resources/bench/` names neither the workflow nor a test file that no longer uses the smoke case.
  - The row of `tests/test_mcp.py` there is gone. It is a tool test after this Feature, and `tool_test_modules` names it.
  - The benchmark's test module of this Feature is under `Ready to delete`. The tool's test module of this Feature is not on the list.
  - Every count in the list is true, its check reports no problem, and the removal test passes. The seven tests that failed on the merge pass: the check-of-the-list tests of Features 01, 04, 05, 06, 07 and 08, and `test_08_B17_the_two_modules_that_read_the_stripped_copy_pass_in_one_process_the_legacy_module_first`.
- **09-B14** — The repository's tests pass the way the hosted tests run them.
  - The workflow `Installed package` passes on all four of its jobs for the commit whose tree is the Build the Reviewer passes. A later change to source or tests needs a new hosted run.

## Interface and seams

Tests. The tool's tests of this Feature are in the new file `tests/test_tool_commands.py`. It imports nothing of the benchmark and names no registered case, and `tool_test_modules` names it. The benchmark's tests of this Feature are in the new file `tests/test_benchmark_commands.py`. Tests are named `test_09_B<n>_`. No other Run left a test of that name.

Which side tests what. In the tool's file: 09-B7, 09-B8, 09-B9, and of 09-B11 the workflow and `README.md`. In the benchmark's file: 09-B1 to 09-B6, 09-B10, 09-B12, 09-B13, and of 09-B11 that the smoke case runs through the benchmark's command. 09-B14 is shown by a hosted run.

How the commands are run in tests. A test runs the benchmark's command as `python -m generative_driver.benchmark …` or calls `benchmark.main([...])`, and it starts the benchmark's MCP server as `python -m generative_driver.benchmark_support.mcp_server`. No test needs `firmware-bench` on the search path. The two declarations of 09-B2 and 09-B4 are read from `pyproject.toml`. A child process gets the environment of `tests/package_env.py`, as Standards say. A test that gives `--home` to `benchmark.main` in its own process puts `GENERATIVE_DRIVER_HOME` back as it was when it ends: the hosted tests run every module in one process.

How 09-B2 and 09-B4 are shown. "Does what it did" is shown by the existing tests of the benchmark's command and of the seven tools, which keep their assertions and use the new entry points (see Existing tests). New tests show what is new: the program name, `--home` before the command, `--home` twice, the status 2, the `report` command, the list of tools the benchmark's server offers over stdio, and each refusal of 09-B5. That each tool has the parameters, the input schema and the description it had is shown against what the tool's server gave at the base commit, recorded from a scratch copy of the base and kept in the test. On macOS and Linux, at least one test for each of the three tools of 09-B5 still shows it starting or resuming; those tests are skipped on a Windows host.

How 09-B5 is shown. A test sets `platform.system` to answer `Windows` in the server's process, as the existing tests do, and shows for each of the three tools: the refusal text; that a service that records what it receives got nothing; that no harness was started; and that the home and the folder beside it hold no new file. The hosted run shows the same tests on Windows itself.

The benchmark's command line. `benchmark.main` gains the option `--home` before the command and the command `report`, and its program name changes. Nothing else in the parser changes: every command keeps its arguments, its texts and its exit status. The option before the command and `run`'s own `--home` are two options and do not share a destination. `benchmark.py` gains a `__main__` block.

The benchmark's MCP server. The seven tools and the helper `_suite_call` move from `mcp.py` to `benchmark_support/mcp_server.py` as they are, with their descriptions, but for 09-B5 and the removal of `via_owner`. The module imports from the tool only what `client_interface.IMPORTS` lists (`client.call` and `client.default_home`), as 08-B12 asks of all the benchmark's code.

The tool's side. `cli.py` loses the block that hands over to the benchmark (`:12-19`), the commands `benchmark` (`:43`) and `report` (`:50-52`) and the branch of `report` (`:87-91`). `mcp.py` loses the seven tools and `_suite_call` (`:104-222`). `configurator.py` loses the branch of `benchmark_spawn` (`:1245-1262`). An import that nothing uses afterwards goes with them, here and in the benchmark's code. Nothing else in these three modules changes. The tool gets no text that points to the benchmark's commands.

What stays as it is in the benchmark. The parameters `autostart` of `benchmark.run`, `harness.launch`, `harness.launch_suite`, `suites.start_suite` and `suites.resume_suite` are unchanged, and so is the check in `benchmark.run` that a service must already run when `autostart` is false.

Grading code. The only edit of grading code is the tuple `SYSTEM_MODULES` in `grader_identity.py`.

The plugin. `plugins/generative-driver/`, the skill text in both its copies and `setup.py` are unchanged. The benchmark gets no plugin and no skill of its own here.

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise. The report lists every existing test that changed, moved or went, with the rule that covers it. A change no rule covers is a Deviation.

- A test that runs `generative_driver benchmark …` as a child, or calls `cli.main` with `benchmark` or `report`, runs the benchmark's command instead, with `--home H` after the program. That covers the tests of `tests/test_corpus.py`, `tests/test_corpus_workflow.py`, `tests/test_suite_interfaces.py` and `ReportCommandTests` in `tests/test_benchmark_surface.py`. The two such tests of `tests/test_cli.py`, `test_benchmark_smoke_runs_via_installed_cli_and_reports_replay` and `test_benchmark_executor_options_refuse_unsupported_choices`, move to the benchmark's file of this Feature, because `tests/test_cli.py` is a tool test.
- A test that reaches one of the seven tools through `generative_driver.mcp`, in its own process or over stdio, reaches it through `generative_driver.benchmark_support.mcp_server`. The tests of `tests/test_mcp.py` that call `driver_benchmark_run` move to the benchmark's file of this Feature. `test_executor_schemas_list_only_supported_runtimes` is split: the row of `driver_start` stays, and the rows of the two benchmark tools move. After that, `tests/test_mcp.py` imports nothing of the benchmark and names none of the seven tools.
- A test that sets the platform to Windows and expects one of the three tools of 09-B5 to reach the service, or the service's launch, expects the refusal of 09-B5 instead. What such a test asserted that does not depend on Windows, such as the home check, reconnection, pagination, cancel and resume, it asserts with the platform as it is, or after starting through the command. It is renamed where its name states what changed. The tests known to be of this kind:
  - `test_registered_emulator_fixture_obeys_home_and_windows_owner_boundaries` and `test_windows_agent_benchmarks_require_an_owner_but_replay_does_not` (`tests/test_mcp.py:48`, `:221`);
  - `test_suite_reconnects_with_running_child_and_paginates_every_slot` and `test_windows_suite_does_not_create_an_owner` (`tests/test_suite_interfaces.py:350`, `:425`).
- A test that does not set the platform, and needs one of the three tools of 09-B5 to start or resume, would be refused on a Windows host, where the hosted tests also run. Where the start or the resume is not the test's subject, the test starts and resumes through the command, and keeps reading and cancelling through the tools. Where the tool's own start or resume is its subject, such as the home check or a repeated request id, the test is skipped on a Windows host, with a reason that names 09-B5. The tests known to be of this kind: `test_benchmark_home_must_match_the_mcp_configurator` (`tests/test_mcp.py:110`), and `test_cancel_and_resume_use_the_same_durable_owner_and_child` and `test_home_selection_resolves_and_pagination_rejects_invalid_numbers` (`tests/test_suite_interfaces.py:259`, `:301`). The same holds for what the rule above moves out of a test that set the platform to Windows.
- A test whose whole subject is the launch through the service is removed: `test_windows_start_refuses_when_owner_disappears_after_ping` (`tests/test_suite_interfaces.py:218`); `test_owner_launch_hands_credentials_directly_to_harness` and `test_owner_launch_refuses_other_directories_and_non_channel_payloads` (`tests/test_harness.py:563`, `:597`); and the two tests of `tests/test_client_surface.py` named `test_08_B7_benchmark_spawn_…` (`:438`, `:452`).
- `test_refused_owner_launch_keeps_a_claimed_trial_from_hanging_on_retry` (`tests/test_harness.py:613`) keeps its subject with the direct launch made to fail: `harness._spawn` raises `OSError`. It asserts that both attempts report the failure and that the second starts no harness, and it is renamed `test_a_failed_launch_keeps_a_claimed_trial_from_hanging_on_retry`.
- A test that says which tool modules import the benchmark, which benchmark names the service imports, or where the service names `bench_home`, follows 09-B9: there are none. That covers `test_07_B5_the_benchmark_imports_that_remain_in_the_service_are_exactly_the_named_ones` (`tests/test_client_calls.py`), `test_06_B8_the_benchmark_imports_that_remain_are_the_named_ones` (`tests/test_supplied_runs.py`), and the tests named `test_08_B5_…` and `test_08_B7_…` of `tests/test_client_surface.py`, whose module doc follows too.
- A test that says the benchmark sends the service one method outside `CALLS`, or that one function sends it, follows 09-B6: `test_08_B14_every_method_the_benchmarks_code_sends_the_service_is_a_listed_call_but_for_the_launch` and `test_08_B14_the_launch_is_sent_from_one_function_alone` (`tests/test_benchmark_surface.py`). A helper or a guard that patches `_spawn_via_owner` patches `_spawn` alone (`tests/test_benchmark_surface.py:353-358`, `tests/test_replay_executor.py:579-590`).
- A test that says which module imports the report module, or where `cli.py` imports it, follows 09-B3: the benchmark's command line imports it, and no tool module does (`test_08_B1_no_tool_module_but_the_command_line_imports_the_report_module` and the `test_08_B2_…` test of `cli.py`'s import, `tests/test_benchmark_surface.py`).
- A test that pins a row, a count, a sentence or a `Freed by` value of the deletion list follows 09-B1 and 09-B13. That covers tests of `tests/test_benchmark_legacy_cases_dropped.py`, `tests/test_benchmark_runs.py`, `tests/test_benchmark_suite_book.py` and `tests/test_benchmark_surface.py`.
- A test that pins a line of a document or of the workflow that this Feature changes follows the new line.
- Any test renamed under these rules is renamed only where its name states a value that changed.
- `tests/deletion_marking.py` is unchanged. Its reader takes a table with no row (`:81-95`), and its check of the commands (`:292-313`) already reports a benchmark command the tool offers that `Commands` does not name.

The hosted tests. Windows does not run on this machine, and Feature 08 showed that faults there show only in a hosted run. So the session that runs this Run commits the state of the Build to the Run's branch before the Feature is Done and pushes it, again if a run still fails, and gives the implementer and the Reviewer each run's result. The Feature is not Done before a hosted run has passed on all four jobs for the tree the Reviewer passed.

A hosted failure. One rule covers every failure of a hosted run. A fault in a test or a test helper is fixed in this Feature and reported as a Deviation. A fault in product code this Feature changed is a bug of the Build against its Behaviour, and is fixed. Any other fault in product code is not fixed here: the session that runs this Run puts it to the owner.

The Test command took about 436 s at the end of Feature 08, and the kit stops it at 540 s. `tests/test_mcp.py` becomes a tool test, so the removal step runs it in the stripped copy as well. If the whole command comes near the limit, say so in the report and trim nothing.

## Out of scope

- A background process of the benchmark's own that starts a harness on Windows, and a command or an MCP tool of the tool's that posts a verdict. The owner decided against both on 2026-11-18.
- The names of the seven tools and the name of the package. Feature 11 renames the package.
- A plugin or a skill for the benchmark's MCP server.
- Benchmark text in files that stay, other than the command lines of 09-B11 and 09-B12. Until Feature 10 the tool's skill text, in both copies, and `docs/codex.md:71` still name `driver_benchmark_run`, and `docs/support.md:24` still speaks of the benchmark's tests. Feature 10 marks or moves such text, with the requirement the owner passed on for it.
- The words `an emulated case's` in two help texts of the tool (`cli.py:66`, `mcp.py:84`). The Breakdown has no exception for them.
- The parameters `autostart`, and fixes to any command of the benchmark.
- Texts in the benchmark's code that name a command as `benchmark suite …` or `benchmark trial-resume`, in hints and help (`suite_book.py:339`, `:893`; `harness.py:886`, `:1107`; `trial_store.py:1022`; `benchmark.py:131`, `:383`, `:385`). Tests pin three of them. They stay as they are.
- The split of tests and documents into two folders, and the boundary test that goes with it. Feature 10.
- What pull request 39 changed in the service, and its test. They are taken as merged.
- Records that name the paths `main` removed: the Specs of Features 01 and 04 and one line of `noticed.md`. Approved documents and records are not edited.
- The workflows `real-ghidra.yml` and `tool-releases.yml`.
- `CONTEXT.md`, `STANDARDS.md`, `AGENTS.md` and the decision records under `docs/adr/`.

## Deviations

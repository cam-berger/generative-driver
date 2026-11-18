# Spec 09: commands

## Intent

For the owner, who is moving the benchmark to the firmware bench repository. Generative-driver is the toolkit and the workflow, and firmware bench evaluates it from outside. After Feature 08 the tool still imports the benchmark in three modules, and each is a way in for the benchmark's commands.

The first is the command line. `generative-driver benchmark …` hands its words to the benchmark's parser, and `generative-driver report` calls the benchmark's report. The benchmark has no command of its own: its parser can be reached only through the tool's.

The second is the MCP server. Seven of the tools of `generative-driver-mcp` are the benchmark's: they start a trial, and start, read, cancel and resume a suite.

The third is the service. On Windows, an MCP client's child processes end when the client closes. So the benchmark's MCP tools ask the service to start the bench harness for them there, with the call `benchmark_spawn`. It is the service's last import of the benchmark.

The owner said on 2026-11-18 that the benchmark's side need not keep what is there today and may be shaped like a typical benchmark. A typical benchmark is run from a command line. So this Feature gives the benchmark one command of its own and removes the other two ways. The owner decided three points for it that day:

- The benchmark's command is called `firmware-bench` already now, so that documents and tests change once. Feature 11 renames the package.
- The benchmark is driven from a command line only. Its seven MCP tools are removed, not moved, and it gets no MCP server. With them goes the only caller of `benchmark_spawn`, so no special case for Windows remains: a terminal starts the bench harness there as it does on macOS.
- The tool gets no command that posts a verdict. The way to accept one verdict from an outside grader is the service's call `record_evaluation`, which the interface lists, and it stays as it is.

The names of the benchmark's commands stay as they are. `cases`, `run`, `score`, `report`, `compare` and `suite` already follow the pattern of a typical benchmark, and a deeper rework is left to the firmware bench repository.

The Feature has a first part that is not about commands. On 2026-11-18 `main` was merged into this Run at `fcd078c`. It removed plans and Specs of earlier Runs, and the deletion list still names them, so seven tests fail on the merge. 09-B1 brings the list in line.

Terms used below:

- The **interface** is the document `docs/client-interface.md` and the module `src/generative_driver/client_interface.py`.
- The **benchmark's code** is `src/generative_driver/benchmark.py` and every module under `src/generative_driver/benchmark_support/`. A **tool module** is any other module under `src/generative_driver`, and `interface_runtime`.
- The **benchmark's parser** is the command-line parser in `benchmark.main`.
- The **seven tools** are `driver_benchmark_run`, `driver_benchmark_suite_start`, `driver_benchmark_suite_status`, `driver_benchmark_suite_events`, `driver_benchmark_suite_result`, `driver_benchmark_suite_cancel` and `driver_benchmark_suite_resume`.
- The **home** is the folder the service keeps its records in.
- Line numbers are those of commit `fcd078c`.

Where something behaves differently after this Feature:

- `generative-driver benchmark …` and `generative-driver report …` end as any command the tool does not know. `firmware-bench …` and `firmware-bench report …` take their place.
- The benchmark's help and usage texts say `firmware-bench`. They said `gd benchmark`.
- `firmware-bench report` of a run that cannot be read prints `{"ok": false, "error": …}` and ends with status 1, as the benchmark's other commands answer an error. `generative-driver report` ended with a traceback.
- The seven tools exist nowhere. A chat client can no longer start a trial, or start, follow, cancel or resume a suite. An operator does that with `firmware-bench` in a terminal, on macOS and on Windows. A chat client can still follow a trial's run with the tool's own `driver_status`, `driver_events` and `driver_result`.
- The service answers `benchmark_spawn` as it answers any method it does not know.
- `benchmark.run`, `harness.launch`, `harness.launch_suite`, `suites.start_suite` and `suites.resume_suite` have no parameter `via_owner`.
- The hosted tests no longer run the benchmark's smoke case as a command, and `README.md` checks an installation with the tool's own smoke.

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
- **09-B4** — The benchmark has no MCP tools.
  - No Python module under `src/` holds the name of any of the seven tools, and no module of the benchmark's code imports the `mcp` package.
  - `pyproject.toml` declares three commands and no other: `generative-driver`, `generative-driver-mcp` and `firmware-bench`.
  - What the seven tools did, the command still does from a terminal, on macOS and on Windows: `firmware-bench run`, and `firmware-bench suite start`, `status`, `events`, `result`, `cancel` and `resume`.
- **09-B5** — The bench harness is started in one way.
  - `harness._spawn_via_owner` and `harness._launch_payload` are gone, and with them the launch channel `bench-launch-channel/1`. The harness's own `main` takes the request it reads from its input as it is.
  - `benchmark.run`, `harness.launch`, `harness.launch_suite`, `suites.start_suite` and `suites.resume_suite` have no parameter `via_owner`. Every launch goes through `harness._spawn`, which is unchanged.
  - Every method the benchmark's code sends to the service is one of `client_interface.CALLS`, with no exception, and the benchmark's code holds no text `benchmark_spawn`.
- **09-B6** — The tool's command line offers nothing of the benchmark.
  - `generative-driver benchmark …` and `generative-driver report …`, with or without `--home H` before them, end with the parser's error for a command it does not know and status 2.
  - `generative-driver --help` names neither.
  - The commands of `generative-driver` are exactly `doctor`, `configure`, `setup`, `service`, `smoke`, `dashboard`, `run`, `tools`, `tool`, `status`, `events`, `result`, `cancel`, `resume` and `respond`, each with the arguments and the behaviour it had.
- **09-B7** — The tool's MCP server offers nothing of the benchmark.
  - `generative-driver-mcp` offers exactly `driver_doctor`, `driver_start`, `driver_status`, `driver_events`, `driver_cancel`, `driver_result`, `driver_resume` and `driver_respond`, each with the parameters, the input schema, the description and the behaviour it had.
- **09-B8** — No tool module imports the benchmark, and the service does not launch the bench harness.
  - A `benchmark_spawn` sent to the service is refused as any method the service does not have: with `Unknown method: benchmark_spawn` when the request names a run the service knows, and with `Unknown run_id` otherwise. No process is started. The order in which the service looks at a request is unchanged.
  - No tool module imports the benchmark's code: no import statement, at the top of a module or inside a function, names `generative_driver.benchmark` or `generative_driver.benchmark_support`, as an absolute or a relative import, and no tool module gives either name as a literal text to `import_module` or `__import__`.
  - `configurator.py` holds neither the text `benchmark_spawn` nor the name `bench_home`.
  - The interface is unchanged: version 4, the same `CALLS`, `IMPORTS` and document. `record_evaluation` is one of `CALLS`.
- **09-B9** — The hosted tests and `README.md` use the tool's own smoke.
  - The step `Test outside the checkout` of `.github/workflows/test.yml` runs the tests and then `generative_driver smoke`, each with `check=True`. It no longer runs `benchmark run --case setup-smoke`. No other line of the workflow changes, and no line of it says `bench`.
  - In `README.md`, each of the three install blocks shows the tool's smoke where it showed the benchmark's smoke case: `.venv/bin/python -m generative_driver smoke`, `.\.venv\Scripts\python.exe -m generative_driver smoke` and `uv run generative-driver smoke`.
  - The sentence after the blocks reads `The smoke runs one whole run through a service of its own, with a scripted worker and a stand-in device. It needs no AI account or hardware and measures no model performance.` It took the place of the two sentences that started `Use a new output directory each time.`
  - The benchmark's smoke case still runs in the tests, through `python -m generative_driver.benchmark run --case setup-smoke`.
- **09-B10** — The benchmark's documents show the command that exists, and no MCP tool.
  - In `bench/README.md`, `bench/groundtruth/README.md`, `docs/benchmark-case-authoring.md`, `docs/benchmark-corpus.md`, `docs/generated-device-interface.md` and `docs/verification.md`, every line that shows a command line of the benchmark shows it with `firmware-bench`:
    - `generative-driver benchmark WORDS` and `python -m generative_driver benchmark WORDS` become `firmware-bench WORDS`;
    - `generative-driver --home H benchmark WORDS` becomes `firmware-bench --home H WORDS`;
    - `generative-driver --home H report WORDS` becomes `firmware-bench --home H report WORDS`, and `gd report <run>` becomes `firmware-bench report <run>`;
    - in prose, a code span that starts with `benchmark` and a command of the benchmark's parser, such as `benchmark suite resume`, starts with `firmware-bench` instead. That is 16 spans on 14 lines: `bench/README.md:25` and `:222`; `docs/benchmark-case-authoring.md:105`, `:153`, `:166`, `:168`, `:202`, `:204`, `:205` and `:206`; `docs/generated-device-interface.md:21`, `:23` and `:146`; and `docs/verification.md:112`.
  - A line that shows a command of the tool, such as `generative-driver --home H configure …`, `service start`, `status RUN_ID` or `result RUN_ID`, is unchanged.
  - Three passages of `bench/README.md` go, and nothing is put in their place: the sentence ``MCP uses `scoped_tool_approval: "emulator"`.`` (`:50`); the paragraph that starts ``MCP: `driver_benchmark_run` takes`` (`:108`); and the sentence `MCP offers suite start/status/events/result/cancel/resume; CLI exports/compares.` (`:170`). After that no line of `bench/README.md` holds the word `MCP` or the name of one of the seven tools. `driver_respond` (`:220`) and `driver_result` (`:232`) are the tool's and stay.
  - Outside `docs/features/`, `docs/adr/`, `docs/research/` and `.scratch/`, no document shows `generative_driver benchmark`, `gd report`, or `generative-driver` with `benchmark` or `report` as its command, which is its next word after an optional `--home H`.
  - Nothing else in these documents changes.
- **09-B11** — The deletion list is true after this Feature.
  - `Tool modules that import the benchmark` has no row. The table keeps its heading and its column names, and the sentence above it reads `No tool module imports the benchmark package.`
  - `Commands` has no row, and its table stays in the same way. The sentence above it reads ``The tool offers no benchmark command. The benchmark's own command is `firmware-bench`.`` The check of the list still reports a benchmark command the tool offers as a problem.
  - Under `Passages`, the row of `.github/workflows/test.yml` and the row of `README.md` that was freed by 09 are gone. The other two rows of `README.md` are true.
  - The line of `pyproject.toml` that declares the benchmark's command sits in a marked passage, so `pyproject.toml` has no row, and the stripped copy's `pyproject.toml` declares only the tool's two commands.
  - No row of the list names Feature 09.
  - Under `Still used by the tool`, each reason is true: the rows of `benchmark.py` and `benchmark_support/` name the still-used tests and no tool module or command, and the row of `src/generative_driver/resources/bench/` names neither the workflow nor a test file that no longer uses the smoke case.
  - The row of `tests/test_mcp.py` there is gone. It is a tool test after this Feature, and `tool_test_modules` names it.
  - The benchmark's test module of this Feature is under `Ready to delete`. The tool's test module of this Feature is not on the list.
  - Every count in the list is true, its check reports no problem, and the removal test passes. The seven tests that failed on the merge pass: the check-of-the-list tests of Features 01, 04, 05, 06, 07 and 08, and `test_08_B17_the_two_modules_that_read_the_stripped_copy_pass_in_one_process_the_legacy_module_first`.
- **09-B12** — The repository's tests pass the way the hosted tests run them.
  - The workflow `Installed package` passes on all four of its jobs for the commit whose tree is the Build the Reviewer passes. A later change to source or tests needs a new hosted run.

## Interface and seams

Tests. The tool's tests of this Feature are in the new file `tests/test_tool_commands.py`. It imports nothing of the benchmark and names no registered case, and `tool_test_modules` names it. The benchmark's tests of this Feature are in the new file `tests/test_benchmark_commands.py`. Tests are named `test_09_B<n>_`. No other Run left a test of that name.

Which side tests what. In the tool's file: 09-B6, 09-B7, 09-B8, and of 09-B9 the workflow and `README.md`. In the benchmark's file: 09-B1 to 09-B5, 09-B10, 09-B11, and of 09-B9 that the smoke case runs through the benchmark's command. 09-B12 is shown by a hosted run.

How the commands are run in tests. A test runs the benchmark's command as `python -m generative_driver.benchmark …` or calls `benchmark.main([...])`. No test needs `firmware-bench` on the search path. The declarations of 09-B2 and 09-B4 are read from `pyproject.toml`. A child process gets the environment of `tests/package_env.py`, as Standards say. A test that gives `--home` to `benchmark.main` in its own process puts `GENERATIVE_DRIVER_HOME` back as it was when it ends: the hosted tests run every module in one process.

How 09-B2 is shown. "Does what it did" is shown by the existing tests of the benchmark's command, which keep their assertions and use the new entry point (see Existing tests). New tests show what is new: the program name, `--home` before the command, `--home` twice, the status 2 and the `report` command.

How 09-B4 is shown. Its first two points are read from the source and from `pyproject.toml`. Its third point is shown by the tests that reached the benchmark through a tool and now reach it through the command (see Existing tests).

The benchmark's command line. `benchmark.main` gains the option `--home` before the command and the command `report`, and its program name changes. Nothing else in the parser changes: every command keeps its name, its arguments, its texts and its exit status. The option before the command and `run`'s own `--home` are two options and do not share a destination. `benchmark.py` gains a `__main__` block.

The tool's side. `cli.py` loses the block that hands over to the benchmark (`:12-19`), the commands `benchmark` (`:43`) and `report` (`:50-52`) and the branch of `report` (`:87-91`). `mcp.py` loses the seven tools and `_suite_call` (`:104-222`). `configurator.py` loses the branch of `benchmark_spawn` (`:1245-1263`). An import that nothing uses afterwards goes with them, here and in the benchmark's code. Nothing else in these three modules changes. The tool gets no text that points to the benchmark's command.

What the seven tools did beside calling the benchmark. Three things were theirs alone and go with them: the check that `options.home` is the home of the MCP connection, the ping of the service before a start on Windows, and the texts of their descriptions. Nothing of them moves into the benchmark's code.

What stays as it is in the benchmark. The parameters `autostart` of `benchmark.run`, `harness.launch`, `harness.launch_suite`, `suites.start_suite`, `suites.resume_suite` and `suite_book.suite_call` are unchanged, and so is the check in `benchmark.run` that a service must already run when `autostart` is false. No command passes `autostart` to `benchmark.run`, `start_suite` or `resume_suite` after this Feature; a caller in Python still can.

Grading code. It is not edited. `grader_identity.SYSTEM_MODULES` is unchanged, since no module is added.

The plugin. `plugins/generative-driver/`, the skill text in both its copies and `setup.py` are unchanged.

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise. The report lists every existing test that changed, moved or went, with the rule that covers it, and names every assertion that went. A change no rule covers is a Deviation.

- A test that runs `generative_driver benchmark …` as a child, or calls `cli.main` with `benchmark` or `report`, runs the benchmark's command instead, with `--home H` after the program. That covers the tests of `tests/test_corpus.py`, `tests/test_corpus_workflow.py`, `tests/test_suite_interfaces.py` and `ReportCommandTests` in `tests/test_benchmark_surface.py`. The two such tests of `tests/test_cli.py`, `test_benchmark_smoke_runs_via_installed_cli_and_reports_replay` and `test_benchmark_executor_options_refuse_unsupported_choices`, move to the benchmark's file of this Feature, because `tests/test_cli.py` is a tool test.
- A test that reaches the benchmark through one of the seven tools, where the tool itself is not what it tests, reaches it through the command instead and keeps its other assertions: it starts, reads, cancels and resumes with `firmware-bench run` and `firmware-bench suite …`. It no longer sets the platform to Windows. It is renamed where its name states what changed. The tests known to be of this kind, all in `tests/test_suite_interfaces.py`: `test_cancel_and_resume_use_the_same_durable_owner_and_child` (`:259`), `test_suite_reconnects_with_running_child_and_paginates_every_slot` (`:350`) and `test_home_selection_resolves_and_pagination_rejects_invalid_numbers` (`:301`). Of the last, these stay, through the command with an options file: the three homes that are refused, one suite under two spellings of the home with one request id, the three numbers the store refuses, and that the other home is not made. Its three arguments of the value `True` and what it reads of the SDK's diagnostics go: a command line has only text there.
- A test whose subject is a tool itself, or the launch through the service, is removed. An assertion of such a test that calls a benchmark function directly, such as `benchmark.run` with `autostart` false, stays, in the benchmark's file of this Feature. What such a test showed of the smoke case, that it runs without a service and leaves no `service.json` in the home, the moved smoke test asserts. The tests known to be of this kind:
  - in `tests/test_mcp.py`: `test_registered_emulator_fixture_obeys_home_and_windows_owner_boundaries` (`:48`), `test_unknown_benchmark_profile_fails_before_owner_creation` (`:93`), `test_benchmark_home_must_match_the_mcp_configurator` (`:110`), `test_windows_agent_benchmarks_require_an_owner_but_replay_does_not` (`:221`) and `test_user_can_run_installation_replay_from_the_mcp_interface` (`:395`);
  - in `tests/test_suite_interfaces.py`: `test_windows_start_refuses_when_owner_disappears_after_ping` (`:218`) and `test_windows_suite_does_not_create_an_owner` (`:425`);
  - in `tests/test_harness.py`: `test_owner_launch_hands_credentials_directly_to_harness` (`:563`), `test_owner_launch_refuses_other_directories_and_non_channel_payloads` (`:597`) and `test_refused_owner_launch_keeps_a_claimed_trial_from_hanging_on_retry` (`:613`). `test_a_claimed_request_whose_harness_never_started_reports_that_failure` (`:637`) already holds the direct launch that fails;
  - in `tests/test_client_surface.py`: the two tests named `test_08_B7_benchmark_spawn_…` (`:438`, `:452`);
  - `test_04_B1_the_benchmark_tool_no_longer_says_that_a_dropped_case_runs` (`tests/test_benchmark_legacy_cases_dropped.py:191`), which reads a tool's description.
- `test_executor_schemas_list_only_supported_runtimes` (`tests/test_mcp.py:475`) keeps the row of `driver_start` and loses the rows of the two benchmark tools. `test_07_B14_the_cancel_command_and_the_mcp_tools_return_what_they_did_from_a_book` (`tests/test_benchmark_suite_book.py:1678`) keeps what it shows of the command, loses what it shows of the tools, and is renamed. After this Feature, `tests/test_mcp.py` imports nothing of the benchmark and names none of the seven tools.
- A test that says which tool modules import the benchmark, which benchmark names the service imports, or where the service names `bench_home`, follows 09-B8: there are none. That covers `test_07_B5_the_benchmark_imports_that_remain_in_the_service_are_exactly_the_named_ones` (`tests/test_client_calls.py`), `test_06_B8_the_benchmark_imports_that_remain_are_the_named_ones` (`tests/test_supplied_runs.py`), and the tests named `test_08_B5_…` and `test_08_B7_…` of `tests/test_client_surface.py`, whose module doc follows too.
- A test that says the benchmark sends the service one method outside `CALLS`, or that one function sends it, follows 09-B5: `test_08_B14_every_method_the_benchmarks_code_sends_the_service_is_a_listed_call_but_for_the_launch` and `test_08_B14_the_launch_is_sent_from_one_function_alone` (`tests/test_benchmark_surface.py`). A helper or a guard that patches `_spawn_via_owner` patches `_spawn` alone (`tests/test_benchmark_surface.py:353-358`, `tests/test_replay_executor.py:579-590`).
- A test that says which module imports the report module, or where `cli.py` imports it, follows 09-B3: the benchmark's command line imports it, and no tool module does (`test_08_B1_no_tool_module_but_the_command_line_imports_the_report_module` and the `test_08_B2_…` test of `cli.py`'s import, `tests/test_benchmark_surface.py`).
- A test that pins a row, a count, a sentence or a `Freed by` value of the deletion list follows 09-B1 and 09-B11. That covers tests of `tests/test_benchmark_legacy_cases_dropped.py`, `tests/test_benchmark_runs.py`, `tests/test_benchmark_suite_book.py` and `tests/test_benchmark_surface.py`.
- A test that pins a line of a document or of the workflow that this Feature changes follows the new line.
- Any test renamed under these rules is renamed only where its name states a value that changed.
- `tests/deletion_marking.py` is unchanged. Its reader takes a table with no row (`:81-95`), and its check of the commands (`:292-313`) already reports a benchmark command the tool offers that `Commands` does not name.

The hosted tests. Windows does not run on this machine, and Feature 08 showed that faults there show only in a hosted run. So the session that runs this Run commits the state of the Build to the Run's branch before the Feature is Done and pushes it, again if a run still fails, and gives the implementer and the Reviewer each run's result. The Feature is not Done before a hosted run has passed on all four jobs for the tree the Reviewer passed.

A hosted failure. One rule covers every failure of a hosted run. A fault in a test or a test helper is fixed in this Feature and reported as a Deviation. A fault in product code this Feature changed is a bug of the Build against its Behaviour, and is fixed. Any other fault in product code is not fixed here: the session that runs this Run puts it to the owner.

The Test command took about 436 s at the end of Feature 08, and the kit stops it at 540 s. `tests/test_mcp.py` becomes a tool test, so the removal step runs it in the stripped copy as well. If the whole command comes near the limit, say so in the report and trim nothing.

## Out of scope

- An MCP server, a plugin or a skill for the benchmark, and a command or an MCP tool of the tool's that posts a verdict. The owner decided against them on 2026-11-18.
- New names or a new grouping for the benchmark's commands. They stay as they are, and a deeper rework is left to the firmware bench repository.
- The name of the package. Feature 11 renames it.
- Benchmark text in files that stay, other than the command lines of 09-B9. Until Feature 10 the tool's skill text, in both copies, and `docs/codex.md:71` still name `driver_benchmark_run`, and `docs/dashboard.md:7` names `driver_benchmark_suite_status`, though neither tool exists any more; `docs/support.md:24` still speaks of the benchmark's tests; and `docs/design.md:107` and `:112` still say `benchmark trial-resume`. Feature 10 removes or moves such text, with the requirement the owner passed on for it.
- The words `an emulated case's` in two help texts of the tool (`cli.py:66`, `mcp.py:84`). The Breakdown has no exception for them.
- The parameters `autostart`, and fixes to any command of the benchmark.
- Texts in the benchmark's code that name a command as `benchmark suite …` or `benchmark trial-resume`, in hints and help (`suite_book.py:339`, `:893`; `harness.py:886`, `:1107`; `trial_store.py:1022`; `benchmark.py:131`, `:383`, `:385`). Tests pin three of them. They stay as they are.
- The split of tests and documents into two folders, and the boundary test that goes with it. Feature 10.
- What pull request 39 changed in the service, and its test. They are taken as merged.
- Records that name the paths `main` removed: the Specs of Features 01 and 04 and one line of `noticed.md`. Approved documents and records are not edited.
- The workflows `real-ghidra.yml` and `tool-releases.yml`.
- `CONTEXT.md`, `STANDARDS.md`, `AGENTS.md` and the decision records under `docs/adr/`.

## Deviations

- The row of `tests/test_benchmark_commands.py` went onto the deletion list already at 09-B1 — the list's check reports a new benchmark test file as soon as it exists, and 09-B11 asks for the same row.
- The `--home` before the command has the destination `configurator_home`, the metavar `HOME` and a help text of its own, and `report` carries the two help texts the tool's `report` had — the Spec fixes none of these words, and nothing else in the parser changed.
- The module doc of `src/generative_driver/benchmark.py` gained two lines that name the command and Feature 09 — Standards ask a module doc to cite its decision.
- Three tests pin a SHA-256: the six documents of 09-B10, the workflow's text before the changed step (09-B9) and `docs/client-interface.md` (09-B8) — this is how "nothing else changes" is tested where no base commit is at hand; the diff confirms the pinned texts, and a later Feature that edits these files must update or drop the pins.
- New tests stand in for `harness._spawn` and the wait that follows it (`_follow`, `_follow_suite`), and one for `run_trial` and `run_suite`, so that no harness starts — the Spec's own rule for existing tests names `_spawn` as the place a guard patches, and Feature 08's accepted tests use the same means.
- "The behaviour it had" of 09-B7 is shown by calling the tool functions in the test process with a stand-in for the service call, and the schemas are compared by types, choices and defaults, not as whole JSON — `mcp.py` only lost lines, so this is enough; seven functions are called, since `driver_doctor` sends the service nothing.
- The moved smoke test shares one child run with the new 09-B9 test — its assertions are unchanged, plus the one the Spec adds.
- In `tests/test_suite_interfaces.py` the class `SuiteMcpTests` is now `SuiteCommandTests`, the helper `server_env` went, `invoke_cli` takes a `cwd`, the wait for the worker's gate is 30 s, not 10 s, and the request id reads `command-once` — all follow from reaching the suite through the command, and no assertion changed.
- One 09-B6 test has a cleanup that stops a service if a refused command started one — it only acts when the test fails.
- The `benchmark.run(…, autostart=False)` assertions of the removed `test_windows_agent_benchmarks_require_an_owner_but_replay_does_not` are the new `test_09_B5_a_run_that_may_start_no_service_is_refused_where_none_runs` — the Spec says an assertion that calls a benchmark function stays in the benchmark's file of this Feature.
- Test-only leftovers went with what used them: `import hashlib` in `tests/test_mcp.py`, the import of `cli` in `tests/test_benchmark_surface.py`, and the launch constants and `Launched` in `tests/test_client_surface.py` — nothing else used them, and that module's doc follows 09-B8 as the Spec says.
- In the deletion list the reason of `benchmark.py` and `benchmark_support/` reads `The still-used tests`, and the reason of `resources/bench/` no longer names `tests/test_cli.py` or the workflow — 09-B11 fixes what a reason must and must not name, not its wording, and both are true.
- `test_02_B6_launch_suite_reports_the_reason_and_the_same_request_starts_afresh_once_fixed` (`tests/test_unlocked_keys.py`) now waits for the harnesses it started before its folder is removed — the error on Windows 3.11 in hosted run 44220609846 was a test fault; its sibling tests wait in the same way, no product code changed, and the Spec's rule for a hosted failure covers it.
- `test_09_B2_python_m_generative_driver_benchmark_runs_the_same_function` compares two new processes, not the test process with a child — it shows the same point and depends on less.
- Two tests named for 09-B12 were added to `tests/test_tool_commands.py`, though the Spec says a hosted run shows 09-B12 — the kit's check needs a test for every Behaviour; they pin which four jobs the workflow has and say that they do not show the tests passing there; the hosted run stays the evidence, and the reviewer judged this a fair reading and no matter for the owner.
- Small edits to existing tests that the implementer's table did not list: the module doc and folder prefixes of `tests/test_suite_interfaces.py`; `LaunchTests` in `tests/test_client_surface.py` lost its `setUp` and its `ServiceCase` base; class docs in `tests/test_benchmark_surface.py`; added guards such as `assertGreater(len(tool_modules()), 80)` — the reviewer found them in the diff, and none changes an assertion.

# Spec 07: suites

## Intent

For the owner, who is moving the benchmark to a repository of its own. Today the service keeps every suite. It stores the frozen manifest and the slots in three tables of its own database, serves 15 `suite_*` calls, decides each slot, and starts each child through a private form of `start`. That is about 730 lines of `configurator.py` and most of its imports of benchmark code. The bench harness already drives every step through those calls.

After this Feature the bench harness keeps a suite's bookkeeping itself. The service's suite code moves, as it is, into one benchmark module, the book, which keeps its store in the bench home and asks the service only through public calls. The harness, the benchmark's suite commands and the benchmark's reports ask the book. The service has no suite call, no suite table and no private start any more.

A client needs four things from the tool to do this, and the owner decided on 2026-11-16 that the tool offers them to every client. `status` finds a run by the request id the client gave it. `result` gives back the request a run was started with. A client can mark a run sealed. On a supplied run a given `executor_config` is the whole configuration. The interface goes to version 3. None of the four names the benchmark.

Terms used below:

- A **suite call** is one of the 15 methods the service serves at `2d95998`: `suite_start`, `suite_resume`, `suite_reserve`, `suite_attach`, `suite_start_child`, `suite_resume_child`, `suite_settle`, `suite_no_run`, `suite_flag`, `suite_sealed`, `suite_detach`, `suite_cancel`, `suite_status`, `suite_events` and `suite_result`, each with the params, the answer and the refusal texts it has there.
- The **book** is the new module `src/generative_driver/benchmark_support/suite_book.py`. It serves the suite calls.
- The **store** is `SuiteStore` of `benchmark_support/suite_store.py`, with its three tables `suites`, `suite_trials` and `suite_events`.
- The **harness keys** are what the bench harness adds to a child's request: `device`, `firmware`, `mission`, `evaluation_token_sha256` and `client_record` (`suites.HARNESS_KEYS`).
- A **supplied run**, the **hand-written device**, the **hand-written firmware** and the **hand-written mission** are those of the Spec for Feature 06.
- Line numbers are those of commit `2d95998`.

Where a suite behaves differently after this Feature, beyond the four additions:

- A suite the service stored before this Feature is not carried over. The book's store starts empty, and such a suite is neither read nor resumed. The Breakdown says so.
- The handling of a child whose frozen final evaluation failed under the earlier, gated protocol leaves with the move (`_suite_resume`, `configurator.py:2086-2092`). No code sets that state any more, so only a run recorded before Feature 06 can have it, and the Breakdown lets such runs go.
- A restart of the service blocks a suite at the first suite call after it, not at the moment the service starts. What is recorded is the same: the status, the text and the event (07-B12).
- A run started by a plain `start` under a slot's request id is that slot's child when its request, without the harness keys, is the slot's, and its client record names the slot's case and pin. This is what suites did before Feature 06. That Feature refused such a run only because the public `start` did not record the request, and now it does.

What stays for later Features: `benchmark_spawn`, the command line and the MCP entry points, until Feature 09; `reporting.py`, the private names of it that suite code uses, and `result`'s `benchmark_summary`, until Feature 08.

## Behaviours

- **07-B1** — `status` finds a run by the request id the client gave at `start`.
  - `status` with `request_id` and no `run_id` answers exactly as `status` with the `run_id` of the run that was started with that request id.
  - For a request id that started no run it is refused with `Unknown request_id`.
  - With both, the `run_id` is used and the `request_id` is not read.
  - Every other call needs a `run_id` as before.
- **07-B2** — `result` gives back the request the run was started with.
  - `result` has `request`: the params of the run's `start` as the service received them, `client_record` included, as JSON values.
  - A run that an earlier version stored without its request has no `request` in its `result`.
  - `status` has no `request`.
  - `start` refuses a password, a password file, an emulator executable or a set root as before, so `request` holds none.
- **07-B3** — A client can mark a run sealed.
  - `seal` with `run_id` answers `{'ok': True, 'run_id': <id>, 'sealed': True}`. The same call again gives the same answer.
  - After it, `status` and `result` of the run hold `'sealed': True`, and `resume` and `attach_device` are refused with `A sealed run does not resume or take a device: <run_id>`, as they are today for a run a suite sealed.
  - It changes nothing else of the run: not its status, its events, its verdict or its files.
  - `seal` with an unknown `run_id` is refused with `Unknown run_id`.
- **07-B4** — On a supplied run, a given `executor_config` is the whole configuration.
  - A supplied run started with `executor_config` runs with exactly that object, with `runtime` set to its executor. Nothing of `executors` in the home's `config.json` is laid under it.
  - A supplied run started without `executor_config` runs with what the operator configured, as today.
  - A run without a supplied device is as today: the request's `executor_config` is laid over what the operator configured.
  - The replay executor's check and copy of its candidate are unchanged.
- **07-B5** — The service has no suite.
  - A suite call sent to the service is refused as any method the service does not have: `Unknown run_id` without a valid `run_id`, `Unknown method: <name>` with one.
  - In a new home the service's database has no table `suites`, `suite_trials` or `suite_events`. In a home that has them, the service neither reads nor changes them.
  - `configurator.py` has none of these names: `suite_start`, `find_request`, `_replay_suite_config`, `_harness_identity`, `_harness_alive`, `_sealing`, `_attached_to`, `_assistive`, `OLD_SUITE`, `TAKEN`, `_slot`, `_child_evaluation`, `_child_mismatch`, `_child_ended`, `_cancel_live`, `_started_from`, `_resumed_on`, `_budgeted`, `_first_attempt`, `_reserved_for_scoring`, `_verify_suite`, and no function, method or module-level name that contains `suite`.
  - `Controller.start` takes the params alone. No start of the service takes an intent to store or a flag for a complete configuration.
  - The benchmark imports that remain in `configurator.py` are exactly these: `trial_store.bench_home` in `_replay_config`; `harness._spawn` and `trial_store.bench_home`, `solo_trial_dir` and `suite_dir` in the `benchmark_spawn` branch of `call`; and `evidence.public_v2_report` in the `result` branch of `call`.
  - `run_status.py` has no `without`, and `replay.py` has no `suite_candidates` and no `stored_candidate`. Only suite code called them, so they live with the book.
  - The tool's tests for this Feature import nothing of the benchmark and name no registered case, and `tool_test_modules` names their module.
- **07-B6** — The interface document and module say version 3.
  - `client_interface.VERSION` is `3`, and the document's first line is `# Client interface, version 3`.
  - `client_interface.CALLS` gains `cancel` and `seal` and loses nothing. `__all__` is unchanged.
  - The start table says of `executor_config`: on a supplied run it is the whole configuration, and without it the run has what the operator configured. It says of `request_id` that `status` finds the run by it.
  - The document says that `ping` answers with `pid`, the process id of the service, by which a client sees that another service process answers.
  - The section on reading records says that `status` takes a `run_id` or a `request_id`, gives the refusal `Unknown request_id`, and says that `result` has `request`.
  - A new section, `## Stop or seal a run`, gives `cancel` and `seal` with their params, the answer of `seal`, and the refusal a sealed run gives to `resume` and `attach_device`.
  - No line of the document says `bench` or `suite`.
- **07-B7** — The store lies in the bench home.
  - The book keeps the store in the file `suites.sqlite3` directly under the bench home, and `trial_store` gives that path, as it gives every other path of the bench home.
  - The file and its three tables are made when a book first needs them. The file is readable and writable by its owner alone, as `suite.json` is.
  - The tables and their columns are those `SuiteStore` makes today, and `suites` gains one column for the process id of 07-B12.
  - A suite that runs to its end leaves no suite row, suite table or suite file in the service's home, apart from the stored replay candidates of 07-B15.
- **07-B8** — The book serves the suite calls as the service did.
  - Each suite call, sent to the book with the params it had, gives the answer it gave and refuses with the texts it had. The differences are only those this Spec names: 07-B9 to 07-B12 and the differences listed under Intent.
  - The token rules are unchanged: a call that needed the harness's token needs it, and the book compares its SHA-256 with the one the suite holds.
  - A suite whose harness is attached but no longer runs is blocked and its harness detached at the next call for it, with the text of today. A book in the harness's own process finds its own harness alive.
  - The book takes a suite's lock wherever the service's suite code took it, and the lock holds across processes: two processes that ask for the same suite at once are served one after the other, and a slot is never reserved twice. `suite_start` finds an earlier suite of the same request under a lock of the whole store.
  - A suite reply that passes a child's status on has none of `deadline`, `repairs`, `worker` and `log`, as today.
  - A suite frozen before the harness drove suites is still read-only, with the text of `OLD_SUITE`.
- **07-B9** — The book asks the service only through public calls.
  - It sends only `ping`, `start`, `status`, `result`, `events`, `cancel`, `resume` and `seal`, and it imports nothing from `generative_driver.configurator`. A book driven alone against a callable that records its requests shows this for every suite call.
  - It starts a child with `start`: the slot's request without `case`, `case_pin` and `case_options`, with `ghidra_home` and `java_home` taken out of `case_options`, as `_suite_child_start` built it (`configurator.py:1756-1766`).
  - It finds a run by a slot's request id with `status` (07-B1), where the service's suite code used `find_request` and `_started_from`.
  - It marks a run sealed with `seal` (07-B3), where `_suite_sealed` wrote the run's row.
  - It takes a child's budget and client record from `result`, its final verdict from `status`, and its unscored evaluations, its device events and its resumes from `events`.
  - Whether a request may start a service is as it was for the suite call it serves. In the harness, requests sent while the book serves `suite_start`, `suite_resume`, `suite_start_child` or `suite_resume_child` may start one, and others may not. In a command or a reader, the book's requests may start one where that place's suite call could: `benchmark suite status`, `events`, `result` and `cancel` and the four MCP tools could, and the report, the scorecard, `store` and the check before a resume could not.
- **07-B10** — Refusals and lost answers pass through the book unchanged.
  - A request the service refuses ends the suite call as that refusal: with the service's `error` and `reason`.
  - A request that gets no answer ends the suite call with the client's reply for that, which has no `error`. The harness tells the two apart as it does today, and sends again only what it sends again today.
  - A refusal of the book's own reaches the harness, a command and a reader as `{'ok': False, 'error': 'ValueError', 'reason': <text>}`, the form the service gave it. `SuiteBook.call` itself raises `ValueError` with that text, as `Controller.call` did.
  - A `suite_start_child` whose `start` got no answer, sent again, finds the child that start made and attaches it. Nothing is started twice.
- **07-B11** — A child is the slot's own only with the frozen request.
  - Before a child starts, the request the harness hands in is compared with the frozen slot as today (`suites.child_identity_differs`), and a difference blocks the suite with today's text.
  - A run is the slot's child when its recorded request (`result`'s `request`), without the harness keys, equals the slot's child start of 07-B9, and its `client_record` names the slot's `case` and `case_pin`. Otherwise the difference is `Frozen suite child identity mismatch: trial <key>'s child request differs from the frozen slot in <names>`, where a client record that differs gives the names `case` or `case_pin`. For a run with no recorded request it is `Frozen suite child identity mismatch: trial <key> has no submitted request`.
  - The check runs wherever `_child_mismatch` ran: at a reservation that finds a child, and in `suite_attach`, `suite_resume_child`, `suite_settle`, `suite_no_run`, `suite_flag`, `suite_cancel` and the check before a resume. A mismatch blocks the suite and cancels the child as today.
  - `suite_start_child` looks for the slot's child first, with `status` by the slot's request id and the run's recorded request, and answers a repeated start from what it finds, whatever the suite did since, as today. It sends `start` only when no run has the slot's request id.
- **07-B12** — A suite does not go on past a restart of the service.
  - `suite_start` and `suite_resume` record the process id that the service's `ping` answers with.
  - Every suite call for a suite asks `ping` first, once. When another process id answers and the suite is queued or running, the suite is blocked with `Configurator restarted; explicit resume required` and the event `suite.recovered`, once. The call then goes on as it does for a blocked suite: a reservation gives no slot, and `suite_start_child` and `suite_resume_child` refuse with the texts they have for a blocked suite.
  - When no service answers and the call may start none, the call fails as the suite call itself failed before.
  - A child that the restarted service stopped is settled or resumed by the rules of today, after `suite_resume`.
- **07-B13** — The bench harness asks the book.
  - Every suite call the harness sent to the service goes to a book in the harness's own process: those of `run_suite`, `_suite_trial`, `_start_child`, `_SlotAudit`, `_held_for_resume`, `_refuse_resume_with_no_password` and `_refuse_beside_readable`.
  - During a scripted suite the service receives no method whose name starts with `suite_`.
  - The harness's requests for a run (`ping`, `status`, `result`, `attach_device`, `record_evaluation`) are unchanged. `start` for a suite's child is sent by the book.
  - A scripted suite of two `tq9-v2` trials completes. Each child's `result` holds its `client_record` and its `request`, and the request has no `case`, `case_pin` or `case_options`.
  - A child's `executor_config` is the frozen one whatever the home's `config.json` holds by then, now by 07-B4.
- **07-B14** — The benchmark's commands and readers ask the book.
  - `benchmark suite status`, `events`, `result` and `cancel`, and the MCP tools `driver_benchmark_suite_status`, `_events`, `_result` and `_cancel`, print and return what they did, from a book in their own process.
  - `suites._refuse_live_set_suite` and `refuse_unsettled_scoring`, `trial_store`'s sweep, seal and `store`, and `suite_reporting._export_suite` read the suite from the book and the runs from the service, as each read them from the service before.
  - A report, a scorecard and a comparison of a scripted suite hold what they held. None holds a run's `request`.
- **07-B15** — A replay suite stores its candidates as today.
  - `suite_start` of a replay suite checks one saved candidate for each case and stores each by its hash under `replay-candidates` in the service's home, and every child of that case replays the stored copy.
  - `suite_candidates` and `stored_candidate` are the benchmark's functions now, with the behaviour they had in `replay.py`.
- **07-B16** — The documents say who keeps a suite.
  - `docs/design.md` says under **Suites** that the bench harness freezes and stores a suite in its own store under the bench home, settles each slot from the child's status and recorded verdict, which it reads from the configurator, and starts a child with a plain `start` on a supplied device. No sentence there says that the configurator stores, starts, settles or checks a suite.
  - `bench/README.md` says that the harness settles slots, where line 168 says the configurator does.
  - `docs/benchmark-case-authoring.md` says that the harness asks its own store whether a suite can resume, where line 167 says it asks the configurator.
  - No document under `docs/` outside `docs/features/` and `docs/adr/`, and neither README, says that the configurator stores a suite or serves a suite call.
- **07-B17** — The deletion list is true after this Feature.
  - The benchmark's test module of this Feature is under `Ready to delete`. The tool's test module of this Feature is not on the list.
  - Every count in a row's reason and every count under `Passages` is true.
  - The check reports no problem, and the removal test passes.

## Interface and seams

Test homes. The tool's tests are in the new `tests/test_client_calls.py`, on the service with the hand-written device, firmware and mission of `tests/supplied_device.py`. The benchmark's tests are in the new `tests/test_benchmark_suite_book.py`. This Feature's tests are named `test_07_B<n>_`. Another Run has tests with such names in `tests/test_trial_sealing.py`, `tests/test_slot_policy_flags.py`, `tests/test_input_paths.py` and `tests/test_benchmark_runs_unchanged.py`; they are not this Feature's.

The book.

- `suite_book.SuiteBook(home, configurator)` takes the service's home and the callable that sends a request to the service. `call(method, params)` serves a suite call.
- `suite_book.suite_call(method, params, home=None, autostart=...)` is what a command or a reader calls, with the `autostart` that place gave its suite call before. It opens a book on the client of that home and gives refusals back in the service's form. It looks `client.call` up when it is called, so a test that replaces `client.call` still reaches it.
- The harness's one callable for requests (`_configurator`, `harness.py:555-570`) sends a suite call to a book and every other method to the service. Tests that stand between the harness and its requests by method name keep working on it.
- The book is the service's suite code moved: the module-level helpers at `configurator.py:136-147` and `:237-275`, and the methods at `:1529-2214`. Each use of a service internal is replaced as the Behaviours on public calls and on identity say. The locks, the order of steps and the texts stay.
- The lock of a suite is a lock file under the bench home. The Build chooses the primitive. It must work on macOS and on Windows, and `_exclusive` (`harness.py:1255`) is one that does.
- `SuiteStore.recover`, which blocked every unfinished suite when a service started, gives way to the block of one suite in the Behaviour on restarts. Its test follows.
- The book takes from the tool what the service's suite code took, and no more: `client.process_lock_held` and `client._wait_for_exit`; `reporting._usage`, `_public` and `_interventions`; `setup.resolve_executor` and `SUPPORTED_EXECUTORS`; `client_interface.PROTOCOL`; and, for the moved replay functions, `replay.candidate_config`, `replay.RUNTIME` and `integrity.digest`. It names the terminal statuses itself. Features 08 and 10 settle which of these the interface lists.
- `grader_identity.SYSTEM_MODULES` gains `benchmark_support.suite_book`, so that the boundary test sees the book. This edit of grading code changes every grader identity, as the Breakdown says of every Feature from 06 on. No other grading code changes.

The service.

- `call` loses the `suite_*` branch (`:1315-1316`). `__init__` loses the suite locks and the store (`:1149`, `:1231-1233`). `status` gains the lookup by `request_id`, `result` gains `request`, and `seal` is a new branch beside `cancel`. The column `runs.sealed` stays.
- `_start_new` (`:2269`) lays nothing under the `executor_config` of a supplied run that gives one.
- `benchmark_spawn` is not touched.

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise, and the report lists every test that changed, moved or went, with its rule.

- A test that sent a suite call to the service sends it to the book of the same home, through one shared helper in `tests/suite_fixtures.py`.
- A test that used `controller._suites`, or ran SQL on the suite tables, uses the book's store and its database.
- A test that patched or read a suite method of `Controller` (`_harness_alive`, `_suite_call`, `_suite_resume`, `_started_from`) uses the book's counterpart. A test that patched `Controller._start` to refuse a child patches the service's `start`.
- A test that stood in for the service's suite answers by replacing `generative_driver.client.call` replaces `suite_book.suite_call` for the suite methods as well, with the same stand-in and the same assertions.
- The shared in-process helper answers `ping` with a `pid` that differs for each service object, so that a second `Controller` on the same home is a restart.
- A test of 06-B19 in `tests/test_benchmark_runs.py` keeps its name where the name is still true, and asks the book. Where it pinned the service's private start, it asserts the same of the public calls: the child's recorded request is the plain start's params, and the same start again finds the child.
- The scans of 06-B8 and 06-B13 in `tests/test_supplied_runs.py` lose their exceptions for suite code, and the allowed imports are those this Spec leaves in `configurator.py`.
- `test_04_B5_a_suite_reply_built_from_a_childs_status_stays_as_it_was` moves with its name from `tests/test_run_status.py` to `tests/test_benchmark_suite_book.py` and asks the book.
- `test_recovered_terminal_final_reconciles_same_child_and_settles_without_resume` (`tests/test_suite_service.py`) is removed with the branch it covered.
- A test that showed a suite blocked by the service's restart shows it at the first suite call after the restart.
- The tests of Feature 06 on a child started outside its suite (`tests/test_suite_harness.py`) follow this Spec's identity check. A plain start whose request differs from the slot's, or whose client record names another case or pin, is refused as before. A plain start with the slot's request, case and pin is attached; `test_a_child_started_outside_a_cancelled_suite_is_cancelled_on_the_next_reservation` asserts the attach with `cancelled` again, as before Feature 06.

Documents. `docs/client-interface.md` follows the Behaviour on version 3. `docs/design.md:109`, `bench/README.md:168` and `docs/benchmark-case-authoring.md:167` follow the Behaviour on documents.

The Test command takes about 440 s of the kit's 540 s at `2d95998`. If this Feature brings it near the limit, the Build says so and trims no test on its own.

## Out of scope

- `benchmark_spawn`, the benchmark's command line and the MCP tools as entry points. Feature 09. The four MCP tools that read or cancel a suite import one more benchmark function until then.
- `reporting.py`, its private names that the book imports, and `benchmark_summary` in `result`. Feature 08.
- The slot policy, the vault gate, sealing, the manifest and the experiment record. They move unchanged.
- Fixes to suites, such as the order in which a harness detaches and writes `suite.json` (`harness.py:1800-1803`). The book keeps what suites do today.
- Reading or importing a suite the service stored before this Feature, and dropping the old tables from an existing database.
- `suite_attach`, which no benchmark code sends. It moves with the rest.
- Who may read `result`'s `request`. It holds the paths of the device's endpoint file and of the firmware, which the service already holds and gives to the same local clients in other answers.
- `CONTEXT.md` and the decision records under `docs/adr/`. Only the owner edits the first, and the second are records.
- The replay executor's rule that a candidate lies outside the bench home.

## Deviations

- Three tests in `tests/test_client_interface.py` and two in `tests/test_supplied_runs.py`, both renamed, changed though no rule names them: they pinned version 2, the old `CALLS` and six headings — 07-B6 changes the version, the calls and the headings these tests pinned, and the two renames keep the names true.
- `test_idempotent_start_can_be_reconnected_without_relaunching` in `tests/test_configurator.py` changed one assertion: the unknown start key may now appear in `result['request']`, and the test asserts `request == params` — 07-B2 gives the start's params back, and the unknown key is still held out of every other key of `result`.
- `docs/client-interface.md` says "five things" in its introduction and lists `cancel` and `seal`, its last line no longer says the operator cancels outside the interface, and the module's docstring follows — 07-B6 makes `cancel` and `seal` calls of the interface.
- `status` with no `run_id` and a `request_id` that is not text is refused `Unknown request_id` — such an id started no run, which is 07-B1's refusal.
- When a run already holds the slot's request id but came from another start than the one handed in, the book refuses `request_id was already used with different inputs` and sends no `start`, and `suite_start_child` does one lookup — 07-B11 sends `start` only when no run has the id, and this is the text the service's own start gave at the same step before, so 07-B8's "texts it had" holds.
- `suite_start` no longer refuses `Configurator is stopping` — the book asks only through public calls and none shows a stopping service; a suite frozen in that window has its first `start` refused by the service in those words, or is blocked at the restart by 07-B12.
- A restart is seen only when the suite recorded a process id, `ping` answered one, and the two differ — 07-B12 says "another process id answers", and an unknown id is not another; the real `ping` always has `pid`, and `suite_start` and `suite_resume` always record it.
- `answered` and `suite_call` give `error` as the class name of whatever the book raises, not only `ValueError` — the daemon gave the same for the service, and the harness still ends a start only on `ValueError`.
- `SuiteStore.settle` lost `terminal_final` and the event `terminal_child_settled` — the Intent lets the earlier protocol's failed final go with the move, and nothing calls them.
- RED for 07-B7 to 07-B17 was taken with the benchmark test module against a skeleton book, and by mutations of the final source on scratch copies, after the code; the book, the harness's switch and the removal from the service changed together — the Intent moves the service's suite code as it is, so the evidence is the check, and it stands: the Reviewer broke 13 more behaviours on scratch copies and each sampled test failed for its reason, except one test that was then rewritten.
- The lock is one lock file for each suite, `<bench home>/suite-locks/<suite id>.lock`, and `store.lock` for the whole store, taken with the harness's `_take` and `_let_go`, polled every 10 ms, re-entrant for a thread and never unlinked; the store's database is in WAL mode with a busy timeout. A lock that cannot be made, opened or taken is held: the suite call is refused — the Spec leaves the primitive to the Build, and after the fix round no path goes on without a lock it asked for.
- `tests/owner_ipc_fixture.py` changed beyond the rule for the process id: it answers `ping` as the daemon does (`pid`, `code`), gives a refusal with its `error`, writes the code into `service.json`, and has `service_pid(controller)` — every suite call now asks `ping` first through `client.call`, whose `ping` needs the code identity.
- The "one shared helper" in `tests/suite_fixtures.py` is a small set: `suite_call`, with `service`, `book`, `owner_call`, `client_call` and `standing_in` — the Spec's own rules need the store and a stand-in besides the call, and all are in the one file it names.
- The book has public names beyond `SuiteBook` and `suite_call`: `SuiteBook.store`, `SuiteBook.find_request`, `answered`, `suite_candidates`, `stored_candidate` and `OLD_SUITE` — the Spec or the rules for existing tests need them, and the list matches the module.
- `src/generative_driver/mcp.py` has a private helper `_suite_call(method, **params)` for its four suite tools — it is inside the four MCP tools the Spec leaves until Feature 09, with the autostart `_call` gave.
- `trial_store.suite_lock_path(home, suite_id=None)` is new beside `suite_store_path` — the lock is a file under the bench home, and `trial_store` gives that home's paths.
- `SuiteStore.recover(suite_id)` blocks one queued or running suite and returns whether it did, and `create` and `attach_harness` take `service_pid` — the one-suite block the Spec asks for.
- Docstrings and comments were reworded, with no code change, in `harness.py`, `suites.py`, `trial_store.py` and `suite_store.py` — they said the configurator does what the book now does.
- `Controller._start` is folded into `start(params)`, which calls `_start_new(params)`, and one comment in `status` is reworded — 07-B5: start takes the params alone.
- The password guard in `tests/test_harness.py` scans the bench home as well as the service's home — it follows the store, and is stricter.
- The 07-B5 scan also checks attribute names for `suite` — stricter than 07-B5.
- The restart block is taken under the suite's lock — it is a write on a suite, and it makes "once" hold across processes.
- `replay.py` lost its unused `import tempfile` and `run_status.py` lost `_ADDED` — 07-B5 removed their only users.
- The Suites paragraph of `docs/design.md` gained two sentences: the configurator holds nothing of a suite, and the restart rule — 07-B16's content and 07-B12's rule.
- In `docs/benchmark-deletion-list.md` the count in the row of `tests/suite_fixtures.py` went from 25 to 32, beside the new row — 07-B17 wants every count true.
- A configurator callable that raises ends the suite call as `{'ok': False, 'reason': '<Type>: <text>'}`, and a reply that is no object as `{'ok': False, 'reason': 'Unreadable configurator reply'}`, both without `error` — the same two replies the harness's `_post` gave; they read as a lost answer, so nothing is sent again blind.
- `suite_call` takes `client.call` and `client.default_home` from the tool, beyond the Spec's list of what the book takes — the Spec's own `suite_call(..., home=None)` opens a book on the client of that home and looks `client.call` up.
- Two existing tests changed assertions beyond a rule: `test_a_suite_resume_requests_the_resume_of_a_child_again_after_a_dropped_connection` (what is sent again is the book's first request, `ping`) and `test_a_replay_suite_replays_each_case_candidate_and_is_never_compared_with_a_model_suite` (it reads `result['request']`) — both read what no longer exists; the first still shows the client's resend after a dropped connection and adds that a call that starts nothing resends nothing, and the second asserts more.
- The book's refusal when a lock cannot be made, opened or taken is `Another suite call may be running: its lock cannot be taken (<error class>: <error text>): <lock path>` — the Spec gives no words; the text follows `_exclusive` in `harness.py` and the Standards' form.
- `TERMINAL` and `TAKEN` of the book are `_TERMINAL` and `_TAKEN` — nothing outside the module reads them, and `OLD_SUITE` stays public for its one reader.
- A suite call refused for its lock has still sent the one `ping` — 07-B12 has every suite call ask `ping` first; `ping` changes nothing in the service, and the test shows that nothing else is sent and no row changes.
- `_FileLock.__exit__` lost its check that the descriptor is not None — a failed lock never counts as held, so `__exit__` runs only with a descriptor.

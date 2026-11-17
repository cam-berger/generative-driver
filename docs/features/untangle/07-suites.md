# Spec 07: suites

## Intent

For the owner, who is moving the benchmark to a repository of its own. Today the service keeps every suite. It stores the frozen manifest and the slots in three tables of its own database, serves 15 `suite_*` calls, decides each slot, and starts each child through a private form of `start`. That is about 730 lines of `configurator.py` and most of its imports of benchmark code. The bench harness already drives every step through those calls.

After this Feature the bench harness keeps a suite's bookkeeping itself. The service's suite code moves, as it is, into one benchmark module, the book, which keeps its store in the bench home and asks the service only through public calls. The harness, the benchmark's suite commands and the benchmark's reports ask the book. The service has no suite call, no suite table and no private start any more.

A client needs four things from the tool to do this, and the owner decided on 2026-11-16 that the tool offers them to every client. `status` finds a run by the request id the client gave it. `result` gives back the request a run was started with. A client can mark a run sealed. On a supplied run a given `executor_config` is the whole configuration. The interface goes to version 3. None of the four names the benchmark.

Terms used below:

- A **suite call** is one of the 15 methods the service serves at `2d95998`: `suite_start`, `suite_resume`, `suite_reserve`, `suite_attach`, `suite_start_child`, `suite_resume_child`, `suite_settle`, `suite_no_run`, `suite_flag`, `suite_sealed`, `suite_detach`, `suite_cancel`, `suite_status`, `suite_events` and `suite_result`, each with the params, the answer and the refusal texts it has there.
- The **book** is the new module `src/generative_driver/benchmark_support/suite_book.py`. It serves the suite calls.
- The **store** is `SuiteStore` of `benchmark_support/suite_store.py`, with its three tables `suites`, `suite_trials` and `suite_events`.
- A **supplied run**, the **hand-written device**, the **hand-written firmware** and the **hand-written mission** are those of the Spec for Feature 06.
- Line numbers are those of commit `2d95998`.

What changes in behaviour, beyond the four additions:

- A suite the service stored before this Feature is not carried over. The book's store starts empty, and such a suite is neither read nor resumed. The owner's decision on old records covers this, and the Breakdown says so.
- The handling of a child whose frozen final evaluation failed under the earlier, gated protocol leaves with the move (`_suite_resume`, `configurator.py:2086-2092`). No stage produces such a run any more, and runs recorded before are not carried.
- A restart of the service is seen by a suite at its harness's next reservation, not at the moment the service starts (07-B11).
- A run started by a plain `start` under a slot's request id, with the same inputs and the same client record as the slot's child, is that child. The Spec for Feature 06 refused it, because only the service's private start recorded the request; the public `start` records it now.

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
  - The section on reading records says that `status` takes a `run_id` or a `request_id`, gives the refusal `Unknown request_id`, and says that `result` has `request`.
  - A new section, `## Stop or seal a run`, gives `cancel` and `seal` with their params, the answer of `seal`, and the refusal a sealed run gives to `resume` and `attach_device`.
  - No line of the document says `bench` or `suite`.
- **07-B7** — The store lies in the bench home.
  - The book keeps the store in the file `suites.sqlite3` directly under the bench home, and `trial_store` gives that path, as it gives every other path of the bench home.
  - The file and its three tables are made when a book first needs them. The tables and their columns are those `SuiteStore` makes today.
  - A suite that runs to its end leaves no suite row, suite table or suite file in the service's home, apart from the stored replay candidates of 07-B14.
- **07-B8** — The book serves the suite calls as the service did.
  - Each suite call, sent to the book with the params it had, gives the answer it gave and refuses with the texts it had. The differences are only those this Spec names: 07-B9, 07-B10, 07-B11 and the changes listed under Intent.
  - The token rules are unchanged: a call that needed the harness's token needs it, and the book compares its SHA-256 with the one the suite holds.
  - A suite whose harness is attached but no longer runs is blocked and its harness detached at the next call for it, with the text of today. A book in the harness's own process finds its own harness alive.
  - The book takes a suite's lock wherever the service's suite code took it, and the lock holds across processes: two processes that ask for the same suite at once are served one after the other, and a slot is never reserved twice. `suite_start` finds an earlier suite of the same request under a lock of the whole store.
  - A suite reply that passes a child's status on has none of `deadline`, `repairs`, `worker` and `log`, as today.
  - A suite frozen before the harness drove suites is still read-only, with the text of `OLD_SUITE`.
- **07-B9** — The book asks the service only through public calls.
  - It sends only `ping`, `start`, `status`, `result`, `events`, `cancel`, `resume` and `seal`. It opens no file of the service's home except to store replay candidates (07-B14) and to read `executors` of `config.json`, which the harness's own reader does since Feature 06.
  - It imports nothing from `generative_driver.configurator`.
  - It starts a child with `start`: the slot's request without `case`, `case_pin` and `case_options`, with `ghidra_home` and `java_home` taken out of `case_options`, as `_suite_child_start` built it (`configurator.py:1756-1766`).
  - It finds a run by a slot's request id with `status` (07-B1), where the service's suite code used `find_request` and `_started_from`.
  - It marks a run sealed with `seal` (07-B3), where `_suite_sealed` wrote the run's row.
  - It takes a child's budget and client record from `result`, its final verdict from `status`, and its unscored evaluations and device events from `events`.
  - Its `start` and `resume` requests may start the service, as `suite_start_child` and `suite_resume_child` could. Its other requests do not.
- **07-B10** — A child is the slot's own only with the frozen request.
  - Before a child starts, the request the harness hands in is compared with the frozen slot as today (`suites.child_identity_differs`), and a difference blocks the suite with today's text.
  - A run is the slot's child when its recorded request (`result`'s `request`), without what the harness adds, equals the slot's child start of 07-B9, and its `client_record` names the slot's `case` and `case_pin`. Otherwise the difference is `Frozen suite child identity mismatch: trial <key>'s child request differs from the frozen slot in <names>`, and for a run with no recorded request `Frozen suite child identity mismatch: trial <key> has no submitted request`.
  - The check runs wherever `_child_mismatch` ran: at a reservation that finds a child, and in `suite_attach`, `suite_resume_child`, `suite_settle`, `suite_no_run`, `suite_flag`, `suite_cancel` and the check before a resume. A mismatch blocks the suite and cancels the child as today.
  - A `start` repeated for a slot whose child exists finds that child by the service's own rule for a repeated `request_id`, and the book attaches it. A repeated `request_id` with other inputs is refused by the service with `request_id was already used with different inputs`.
- **07-B11** — A suite does not go on past a restart of the service.
  - `suite_start` and `suite_resume` record the process id that the service's `ping` answers with.
  - `suite_reserve` asks `ping` first, without starting a service. When another process id answers, the suite is blocked with `Configurator restarted; explicit resume required` and the event `suite.recovered`, once, and the reservation gives no slot.
  - When no service answers, the call fails as the suite call itself failed before.
  - A child that the restarted service stopped is settled or resumed by the rules of today, after `suite_resume`.
- **07-B12** — The bench harness asks the book.
  - Every suite call the harness sent to the service goes to a book in the harness's own process: those of `run_suite`, `_suite_trial`, `_start_child`, `_SlotAudit`, `_held_for_resume`, `_refuse_resume_with_no_password` and `_refuse_beside_readable`.
  - During a scripted suite the service receives no method whose name starts with `suite_`.
  - The harness's requests for a run (`ping`, `status`, `result`, `attach_device`, `record_evaluation`) are unchanged. `start` for a suite's child is sent by the book.
  - A scripted suite of two `tq9-v2` trials completes. Each child's `result` holds its `client_record` and its `request`, and the request has no `case`, `case_pin` or `case_options`.
  - A child's `executor_config` is the frozen one whatever the home's `config.json` holds by then, now by 07-B4.
- **07-B13** — The benchmark's commands and readers ask the book.
  - `benchmark suite status`, `events`, `result` and `cancel`, and the MCP tools `driver_benchmark_suite_status`, `_events`, `_result` and `_cancel`, print and return what they did, from a book in their own process.
  - `suites._refuse_live_set_suite` and `refuse_unsettled_scoring`, `trial_store`'s sweep, seal and `store`, and `suite_reporting._export_suite` read the suite from the book and the runs from the service, as each read them from the service before.
  - A report, a scorecard and a comparison of a scripted suite hold what they held.
- **07-B14** — A replay suite stores its candidates as today.
  - `suite_start` of a replay suite checks one saved candidate for each case and stores each by its hash under `replay-candidates` in the service's home, and every child of that case replays the stored copy.
  - `suite_candidates` and `stored_candidate` are the benchmark's functions now, with the behaviour they had in `replay.py`.
- **07-B15** — The documents say who keeps a suite.
  - `docs/design.md` says under **Suites** that the bench harness freezes and stores a suite in its own store under the bench home, settles each slot from the child's status and recorded verdict, which it reads from the configurator, and starts a child with a plain `start` on a supplied device. No sentence there says that the configurator stores, starts, settles or checks a suite.
  - `bench/README.md` says that the harness settles slots, where line 168 says the configurator does.
  - `docs/benchmark-case-authoring.md` says that the harness asks its own store whether a suite can resume, where line 167 says it asks the configurator.
  - No document under `docs/` outside `docs/features/` and `docs/adr/`, and neither README, says that the configurator stores a suite or serves a suite call.
- **07-B16** — The deletion list is true after this Feature.
  - The benchmark's test module of this Feature is under `Ready to delete`. The tool's test module of this Feature is not on the list.
  - Every count in a row's reason and every count under `Passages` is true.
  - The check reports no problem, and the removal test passes.

## Interface and seams

Test homes. The tool's tests are in the new `tests/test_client_calls.py`, on the service with the hand-written device, firmware and mission of `tests/supplied_device.py`. The benchmark's tests are in the new `tests/test_benchmark_suite_book.py`. This Feature's tests are named `test_07_B<n>_`. Another Run has tests with such names in `tests/test_trial_sealing.py`, `tests/test_slot_policy_flags.py`, `tests/test_input_paths.py` and `tests/test_benchmark_runs_unchanged.py`; they are not this Feature's.

The book.

- `suite_book.SuiteBook(home, configurator)` takes the service's home and the callable that sends a request to the service. `call(method, params)` serves a suite call.
- `suite_book.suite_call(method, params, home=None)` is what a command or a reader calls. It opens a book on the default client of that home.
- The harness's one callable for requests (`_configurator`, `harness.py:555-570`) sends a suite call to a book and every other method to the service. Tests that stand between the harness and its requests by method name keep working on it.
- The book is the service's suite code moved: the module-level helpers at `configurator.py:136-147` and `:237-275`, and the methods at `:1529-2214`. Each use of a service internal is replaced as 07-B9 says. The locks, the order of steps and the texts stay.
- The lock of a suite is a lock file under the bench home. The Build chooses the primitive. It must work on macOS and on Windows, and `_exclusive` (`harness.py:1255`) is one that does.
- The book takes from the tool what the service's suite code took, and no more: `client.process_lock_held` and `client._wait_for_exit`, `reporting._usage`, `_public` and `_interventions`, `setup.resolve_executor` and `SUPPORTED_EXECUTORS`, and `client_interface.PROTOCOL`. It names the terminal statuses itself. Features 08 and 10 settle which of these the interface lists.

The service.

- `call` loses the `suite_*` branch (`:1315-1316`). `__init__` loses the suite locks and the store (`:1149`, `:1231-1233`). `status` gains the lookup by `request_id`, `result` gains `request`, and `seal` is a new branch beside `cancel`. The column `runs.sealed` stays.
- `_start_new` (`:2269`) lays nothing under the `executor_config` of a supplied run that gives one.
- `benchmark_spawn` is not touched.

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise, and the report lists every test that changed, moved or went, with its rule.

- A test that sent a suite call to the service sends it to the book of the same home, through one shared helper in `tests/suite_fixtures.py`.
- A test that used `controller._suites`, or ran SQL on the suite tables, uses the book's store and its database.
- A test that patched or read a suite method of `Controller` (`_harness_alive`, `_suite_call`, `_suite_resume`, `_started_from`) uses the book's counterpart. A test that patched `Controller._start` to refuse a child patches the service's `start`.
- A test of 06-B19 in `tests/test_benchmark_runs.py` keeps its name where the name is still true, and asks the book. Where it pinned the service's private start, it asserts the same of the public calls: the child's recorded request is the plain start's params, and the same start again finds the child.
- The scans of 06-B8 and 06-B13 in `tests/test_supplied_runs.py` lose their exceptions for suite code, and the allowed imports are those of 07-B5.
- `test_04_B5_a_suite_reply_built_from_a_childs_status_stays_as_it_was` moves with its name from `tests/test_run_status.py` to `tests/test_benchmark_suite_book.py` and asks the book.
- `test_recovered_terminal_final_reconciles_same_child_and_settles_without_resume` (`tests/test_suite_service.py`) is removed with the branch it covered.
- A test that showed a suite blocked by the service's restart shows it at the next reservation (07-B11).
- A test of Feature 06 that a plain start cannot be attached as a slot's child (`tests/test_suite_harness.py`) follows 07-B10: a plain start with other inputs, or with another client record, is refused as before.

Documents. `docs/client-interface.md` follows 07-B6. `docs/design.md:109`, `bench/README.md:168` and `docs/benchmark-case-authoring.md:167` follow 07-B15.

## Out of scope

- `benchmark_spawn`, the benchmark's command line and the MCP tools as entry points. Feature 09. The MCP tools of 07-B13 import one more benchmark function until then.
- `reporting.py`, its private names that the book imports, and `benchmark_summary` in `result`. Feature 08.
- The slot policy, the vault gate, sealing, the manifest and the experiment record. They move unchanged.
- Fixes to suites, such as the order in which a harness detaches and writes `suite.json` (`harness.py:1800-1803`). The book keeps what suites do today.
- Reading or importing a suite the service stored before this Feature, and dropping the old tables from an existing database.
- `suite_attach`, which no benchmark code sends. It moves with the rest.
- `CONTEXT.md` and the decision records under `docs/adr/`. Only the owner edits the first, and the second are records.
- The replay executor's rule that a candidate lies outside the bench home.

## Deviations

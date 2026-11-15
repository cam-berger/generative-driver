# 02. shared-helpers

## Intent

The tool hashes the artifacts and handoffs of a run, stops a run when a hash no longer matches, and words a run's final verdict. Today it imports the code for this from the benchmark's folder, where grading code uses the same files. This Feature gives the tool its own, in the tool's package and described by what the tool does with it. The tool's modules then use the tool's own.

The tool's modules import three more names from those files. The tool gets no copy of them.

- `artifact_name` reads the execution setup of a benchmark case. It is called only where the service checks a device that the bench harness provides.
- `execution_label` says how a benchmark run executed. For a run with no case it changes nothing.
- `tree_hash` is called only by the report module, which stamps a report with the revision of the installed code. That report is built as a benchmark report, also for a run with no case. Feature 07 settles what of it is the tool's.

Until the later Features take that code out or settle it, the service and the report module keep these three imports.

Grading stays in the benchmark's folder. Grading code keeps its files byte for byte, so no grader identity changes. The grader checks again the hashes the tool recorded, so its helpers must agree with the tool's. That is the benchmark's concern: one benchmark test holds the grader's helpers to the tool's answers, and it goes where the benchmark goes.

Nothing a user, a run record or a report shows changes. One thing changes underneath: there are now two `IntegrityError` classes with the same name, the tool's and the grader's. Every stop that is recorded as an integrity stop today is still recorded as one.

## Behaviours

In these Behaviours, "the tool's helpers" are `generative_driver.integrity` and `generative_driver.verdict_labels`. "The grader's helpers" are `integrity.py` and `labels.py` in `src/generative_driver/benchmark_support/`. "The benchmark package" is `generative_driver.benchmark` and everything under `generative_driver.benchmark_support`. A "tool module" is a Python file under `src/interface_runtime/`, or under `src/generative_driver/` outside `benchmark.py` and `benchmark_support/`. A module "offers" the names it lists in `__all__`: each of the tool's helpers declares `__all__` with exactly the names its Behaviour gives, and defines no other function, class or constant without a leading underscore. Names it imports from the standard library do not count. Two helpers give "the same answer" when they return equal values, or raise exceptions of the same type with the same message.

"The fixed tree" is a directory that holds the file `a.txt` with the bytes `abc`, the file `sub/b.bin` with the two bytes `00 01`, and the empty folder `empty`.

- **02-B1** — `generative_driver.integrity` offers `digest` and `IntegrityError`, and nothing else. The hashes the tool records do not change:
  - `digest` of a file that holds the bytes `abc` is `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad`;
  - `digest` of the fixed tree is `1dac59c765a528370eaa52da6b3e4dfc8127e8a08a9acd81e7cfd7d2ee3184ab`;
  - `digest` of an empty directory is `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`;
  - `digest` raises `ValueError` with `Artifact symlinks are not admissible` for a symlink, at the path or under a directory, and with `Artifact does not exist: ` and the path for a missing one;
  - `IntegrityError` is a subclass of `ValueError`.

  A symlink case is skipped, with its reason, where the platform cannot make symlinks.
- **02-B2** — `generative_driver.verdict_labels` offers `equivalence_label` and `verdict_reason`, and nothing else. `equivalence_label` gives `equivalent` for `{'equivalent': True}`, `not equivalent` for `{'equivalent': False}`, `unknown` for `{'status': 'unknown'}`, and `unscored` for `{}` and for `None`. `verdict_reason` gives `Final verdict: equivalent` for an equivalent record, and `Final verdict: not equivalent: r1; r2; r3; 2 more` for a record that is not equivalent and has five differences with the reasons `r1` to `r5`.
- **02-B3** — The grader's helpers agree with the tool's. For every input below, the grader's helper and the tool's helper of the same name give the same answer.
  - `digest`: an empty file; a file of 3 MiB; the fixed tree; a file that is a symlink and a directory that holds one; a path that does not exist.
  - `equivalence_label` and `verdict_reason`: `{}`; `{'status': 'unknown'}`; an equivalent record; a record that is not equivalent with no, one, three and five reasons; a `diff` that holds an entry that is not a dict and an entry whose reason is not a string.

  The grader's `labels.RUNTIME` equals `replay.RUNTIME`. The two `IntegrityError` classes have the same name and are both subclasses of `ValueError`, and they are two classes. A symlink case is skipped, with its reason, where the platform cannot make symlinks.
- **02-B4** — The tool's modules use the tool's own:
  - no tool module imports `digest`, `IntegrityError`, `equivalence_label`, `verdict_reason` or `RUNTIME` from the benchmark package, in any import statement at any depth;
  - `replay` and `scripted_worker` import nothing from the benchmark package;
  - the replay executor names its own runtime: `replay.RUNTIME` is `replay`, `replay.candidate_config` of a valid saved candidate returns a configuration whose `runtime` is `replay`, and `replay` no longer has the name `execution_label`;
  - the names other code takes from the service are the tool's objects: `configurator.digest`, `configurator.IntegrityError` and `configurator.equivalence_label`.
- **02-B5** — Grading stays inside the benchmark's folder. Every module that `grader_closure()` of `benchmark_support.grader_identity` names is `benchmark_support` or under it, so grading code imports neither of the tool's helpers. The same check finds a breach: in a copy of the package where a grading module imports the tool's `integrity`, `grader_closure` of that copy names `integrity`.
- **02-B6** — The service still stops for integrity as it does today. When the notes of an accepted handoff change while a run is stopped, `resume` raises the tool's `IntegrityError` with `Stale handoff`, the run's status is `blocked`, and its `outcome_category` is `integrity`.
- **02-B7** — The bench harness still records a grader's integrity stop as one. Through `harness.conclude`, a trial whose accepted package bytes changed before grading is recorded unscored with a reason that starts `integrity: `. A seal that finds accepted artifact bytes changed leaves an unscored record whose reason starts `integrity: `.
- **02-B8** — The deletion list follows:
  - `src/generative_driver/replay.py` and `src/generative_driver/scripted_worker.py` leave `Tool modules that import the benchmark`;
  - `tests/test_benchmark_helper_copies.py` is under `Ready to delete`;
  - a test module that now imports nothing from the benchmark package, directly or through the test files it imports, leaves `Still used by the tool` and is among the tool's test modules that Feature 01 runs in the stripped copy. `tests/test_service_log.py` is one; Build finds the others, if any, by their imports;
  - `tests/test_shared_helpers.py` and the tool's two helpers are not on the list.

  The tests of 02-B8 assert only these changes: the rows that left and joined, and that `tool_test_modules` names `tests/test_service_log.py` and `tests/test_shared_helpers.py` and does not name `tests/test_benchmark_helper_copies.py`. Feature 01's own tests prove the rest in the Test command: its check reports no problem on the repository, and its removal and its run of the tool's tests in the copy pass. No second run of the copy is added.

## Interface and seams

- The tool's helpers are two new modules, one for each responsibility:
  - `src/generative_driver/integrity.py`: `digest`, `IntegrityError`;
  - `src/generative_driver/verdict_labels.py`: `equivalence_label`, `verdict_reason`.

  Each takes the code for its names as it stands in the grader's file, so that no recorded hash and no reported word changes. Each has its own module doc and its own docstrings. They say what the tool does with the names, in the tool's words. A verdict there is the tool's own or one posted to the run. The docs name neither the benchmark nor its grader.

  The Breakdown calls this one home: it is the tool's package, where each name now has exactly one place.
- `RUNTIME = 'replay'` is defined in `src/generative_driver/replay.py`, the module of the replay executor.
- The import lines that change in tool modules:
  - `configurator.py:26` and `:27` take their names from the tool's helpers;
  - `scripted_worker.py:57` takes `digest` from the tool's helper;
  - `replay.py:23` goes, with `RUNTIME` defined in its place;
  - `reporting.py:114` took `execution_label` through `replay`. It now takes it from the grader's `labels`, where it is defined. The report built there is the benchmark's.

  The comments at `configurator.py:25` and `replay.py:22` say why the names came from the benchmark's folder. Each is rewritten or removed with its import, so that no comment gives a reason that no longer holds. Nothing else changes in those modules.
- The import lines that stay: `configurator.py:284` and `:3076` (`artifact_name`, where the service checks a device the bench harness provides against a case pin) and `reporting.py:9` (`tree_hash`, for the report's `toolchain_revision` and `skills_revision`). `configurator.py` and `reporting.py` keep their rows on the deletion list.
- The `Used by` cells at `docs/benchmark-deletion-list.md:56` and `:57` count six tool modules that import the benchmark. After this Feature they say four. No test reads those cells.
- Two files of the benchmark's folder change, and neither is grading code:
  - `harness.py`: `harness.conclude` handles both classes in its two integrity handlers, around grading and around sealing. Grading code raises the grader's class there, and code that is not grading code takes the tool's class from `configurator`;
  - `suite_reporting.py:346` took `execution_label` through `replay`. It now takes it from the grader's `labels`.
- The stage code in the benchmark's folder that takes `IntegrityError` and `digest` from `configurator` is not edited: `emulated.py`, `physical.py` and `instrument_evidence.py`. Through the same import it now gets the tool's objects, which is what the service tests for.
- The reports keep their execution label. Existing tests prove it through the report commands and keep passing unchanged: `tests/test_replay_executor.py:394`, `:407` and `:467` expect `replay-emulation` in the report of a replayed run and of a replay suite.
- `tests/test_shared_helpers.py` tests 02-B1, 02-B2, 02-B4, and 02-B6 through the service's public calls. It imports nothing from the benchmark package, so it runs in the stripped copy.
- `tests/test_benchmark_helper_copies.py` tests 02-B3, 02-B5, 02-B7 through `harness.conclude`, and 02-B8. For 02-B8 it reads the rows from the text of the deletion list, by heading, and takes the three claims about test modules from `tool_test_modules` of `tests/deletion_marking.py`. It is a benchmark item.
- Existing tests change only where the two classes make them fail. A test that expects the tool to raise the class it took from the grader's helper takes the tool's instead, as `tests/test_service_log.py:189` does. A test that expects grading code to raise the class it took from `configurator` takes the grader's. No assertion about a record, a reason, a category or a status changes.
- A traceback names a class by its module. So a traceback in the service log reads `generative_driver.integrity.IntegrityError` where it read `generative_driver.benchmark_support.integrity.IntegrityError`. The first line of a log entry, the JSON replies and every recorded reason use the bare name `IntegrityError`, and do not change.

## Out of scope

- Any edit to grading code: the modules `grader_closure()` names, the grader's helpers among them. Their module docs still say that the configurator uses them; that is reported as noticed, not fixed.
- Anything that changes a grader identity, and anything under `src/generative_driver/resources/bench/` or `resources/registrations/`.
- A copy in the tool of `artifact_name`, `execution_label` or `tree_hash`.
- Whether the tool keeps a report of its own runs, and with it a hash of its installed code. That is Feature 07's question.
- Moving or removing the benchmark code that still sits in tool modules: the service's checks of a provided device against a case pin, the benchmark's part of `reporting.py`, and the suite functions and the `bench` argument in `replay.py`. That is the work of Features 04 to 08.
- Folding the tool's other hash functions into the new module: `interface_runtime.package.digest`, `_toolchain.firmware._hash`, `_toolchain.core.sha256` and `sha_tree`, and `client._sources_digest`. They differ in what they refuse and in what they return.
- Changing the imports of any other module in the benchmark's folder.
- Renaming or removing a name that a tool module passes on, such as `configurator.digest`. The one exception is `replay.execution_label`, which goes.
- Changing a message, a reason, a category or a status.
- A check of class by name in the tool, or any other way to make the two classes count as one.

## Deviations

- `verdict_labels.verdict_reason`: the list comprehension is wrapped onto two lines, where the grader's file has one line of 126 columns — layout only, to meet the 120-column rule; 02-B2 and 02-B3 hold every reported word, as the Intent requires.
- Comments: `configurator.py:25` is rewritten, not removed, and `RUNTIME = 'replay'` in `replay.py` carries a new one-line comment — the Spec allows "rewritten or removed", and neither comment gives a reason that no longer holds.
- `harness.conclude`: both handlers catch one tuple, `integrity_stop`, of both classes, built from one added import of the grader's class — the Spec says it "handles both classes" and leaves the how open; it sits in the benchmark's harness, not the tool.
- Guard assertions beyond the Behaviours' words: 02-B3 also asserts whether each answer is a return or a raise and that neither `IntegrityError` class subclasses the other; 02-B4 asserts which modules the scan covered; 02-B5 asserts the closure names `benchmark_support.evaluator`, `.integrity` and `.labels`; 02-B8 asserts each table's header row was read — each only keeps a test from passing vacuously; the 02-B8 header guard does not re-prove Feature 01, which is what "assert only these changes" protects.
- Fixtures: 02-B7 reuses `HarnessTrialFixture`, `reference`, `PASS` and `TOKEN` from `tests/test_evaluation.py`, and 02-B8 reads rows with `table`, `REPO`, `LIST` and `require_working_tree` from `tests/test_deletion_marking.py` — the Spec names no fixtures; that file is a benchmark item, and the tool's test file imports none of them.
- Deletion list: the Note of the new `Ready to delete` row reads "Holds the grader's helpers to the tool's answers. No file that stays imports it." — the Spec gives no wording, and the row says what the Intent says the test is for.
- 02-B8 "the others, if any": none. `tests/benchmark_family_fixtures.py` and `tests/test_benchmark_family_runtime.py` import nothing from the benchmark package but keep their rows — the Spec's "now" covers modules this Feature frees; those two are unchanged from base, need a row for their benchmark names, and are reported under Noticed.
- Order of work: 02-B6 and 02-B7 were built before 02-B4 — order only, so each test failed for its own reason; the result is the same.
- RED evidence for tests that passed on their first run: three of the four 02-B3 tests, both 02-B5 tests and one 02-B8 test — the Intent keeps grading code byte for byte, so no code belongs to these guards; each has a failing excerpt for its own reason, and 02-B5's failure is reproduced by its second test on every run.

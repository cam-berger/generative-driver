# 03. client-interface

## Intent

A client that brings its own device and mission can ask four things of the tool: start a run on a device it supplies, replace that device, post one final verdict, and read the run's records and its frozen driver. The service does all four today. But nothing says so in the tool's own words. The calls are described only as what the bench harness does. The rules for a posted verdict are a private function of the service, and the benchmark imports it from there.

This Feature writes the interface down as the tool's own: one short document and one module, with a version. The module becomes the home of what a client needs in code: the calls it may make, the rules of the two records it sends, and the way to find the frozen driver among a run's handoffs. The document says the same in plain words. It speaks of a client, a supplied device and a posted verdict, and names the benchmark only in one marked passage.

No behaviour changes. Every call, record, field, message and refusal stays as it is. Two things follow, and the document says both:

- The service accepts a supplied device only for a run that names a registered case, and it takes the run's inputs and mission from that case. Features 04 and 05 lift this limit, add the inputs and the mission a client supplies as data, and remove the marked passage.
- In this Feature the records keep the fields and values the service enforces today, among them ones that benchmark runs shaped: the grading methods `fresh-final` and `recorded-reuse`, `capabilities_sha256`, and the descriptor's kind `provided-emulator`. The document describes each in the tool's words and says that they are due to change. Feature 05 puts the records in the tool's own terms.

## Behaviours

In these Behaviours, "the interface module" is `generative_driver.client_interface`. "The document" is `docs/client-interface.md`. "The service" is the configurator as a client reaches it, through `generative_driver.client.call`. "The benchmark package" is `generative_driver.benchmark` and everything under `generative_driver.benchmark_support`. A module "offers" the names it lists in `__all__`: the interface module declares `__all__` with exactly the names 03-B1 gives, and defines no other function, class or constant without a leading underscore. Names it imports from the standard library do not count. A "marked passage" is the text between the two marker lines of Feature 01, `<!-- legacy-benchmark:begin -->` and `<!-- legacy-benchmark:end -->`, with those lines.

"The good record" is this verdict record:

```python
{'schema': 'equivalence/1', 'status': 'graded', 'equivalent': True, 'basis': 'evaluator', 'method': 'fresh-final',
 'checks': [{'id': 'c1', 'passed': True}], 'diff': [], 'passed': 1, 'total': 1, 'mission': None,
 'frozen_artifact_sha256': 'a' * 64, 'capabilities_sha256': None, 'evidence_sha256': 'b' * 64,
 'protocol': 'assistive/1'}
```

"The fields message" is `an equivalence/1 record has exactly the fields basis, capabilities_sha256, checks, diff, equivalent, evidence_sha256, frozen_artifact_sha256, method, mission, passed, protocol, schema, status, total`.

- **03-B1** — The interface module offers `VERSION`, `PROTOCOL`, `CALLS`, `VERDICT_FIELDS`, `verdict_errors`, `device_errors` and `frozen_driver`, and nothing else.
  - `VERSION` is the integer `1`.
  - `PROTOCOL` is `assistive/1`.
  - `CALLS` is the tuple `('ping', 'start', 'resume', 'status', 'events', 'result', 'attach_device', 'record_evaluation')`.
  - `VERDICT_FIELDS` is the frozen set of the fourteen field names in the fields message.
  - The service knows every call. Sent for an existing run, no name in `CALLS` is answered with a reason that starts `Unknown method`. Sent for the same run, a name that is not a method, such as `nonsense`, is answered `Unknown method: nonsense`.
- **03-B2** — `verdict_errors(record)` gives the faults of a verdict record as a list of sentences, empty for a well-formed record.
  - The good record gives `[]`.
  - The good record with one more field, and a value that is not a dict, each give the fields message alone.
  - The good record with `basis` set to `self-evidence` gives `['schema equivalence/1, basis evaluator, method fresh-final or recorded-reuse and protocol assistive/1 are required']`.
  - The good record with its check failed and `passed` set to `0` gives `['an equivalent driver passed every check, with no difference']`.
  - The good record with `evidence_sha256` set to `None` gives `['a grade names the evidence it was sealed in']`.
  - A record with status `unknown`, `equivalent` `None`, the one check undecided (`passed` is `None`), `passed` `0`, and a `diff` that names that check with a reason gives `[]`.
  - A record with status `unscored`, `equivalent` `None`, no checks, `passed` and `total` `0`, `evidence_sha256` `None`, and a `diff` with one named reason gives `[]`.

  The service refuses by the same rules. For a run started with `evaluation_token_sha256`, `record_evaluation` with the run's token and the good record with one more field is refused with the reason `Invalid evaluation record: ` followed by the fields message.
- **03-B3** — `device_errors(device)` gives the faults of a device descriptor, empty for a well-formed one.
  - A descriptor with `schema` `device-descriptor/1`, `kind` `provided-emulator`, a `control` with a non-empty `endpoint_file`, a non-empty `session_id`, a non-empty `binding` and a string `image_sha256` gives `[]`.
  - A value that is not a dict, a descriptor with another `schema`, and one with another `kind` each give `['not a device-descriptor/1 of a provided emulator']`.
  - A descriptor with the right `schema` and `kind` and nothing else gives `['no control endpoint file', 'no session_id', 'no binding', 'no image_sha256']`.

  The service applies these rules: `device_errors` is the same function object as `generative_driver.device_client.descriptor_errors`.
- **03-B4** — `frozen_driver(handoffs)` finds the driver that a posted verdict must name in `frozen_artifact_sha256`. It takes a run's accepted handoffs in the order `result` gives them, and returns `{'path': ..., 'sha256': ...}` of the first artifact of the latest `emit` handoff that has an artifact, or `None`.
  - No handoffs, and handoffs whose only `emit` handoff has no artifact, give `None`.
  - Two `emit` handoffs with artifacts give the first artifact of the later one, also when that handoff has a second artifact.
  - An `emit` handoff with an artifact followed by an `emit` handoff with none gives the earlier one's artifact.
  - A `probe` or `reuse` handoff with artifacts, before or after, changes nothing.
- **03-B5** — The document states the interface in the tool's words. Each point holds in the repository and in the stripped copy of Feature 01, where the marked passage is gone.
  - Its first line is `# Client interface, version 1`, and the number is `VERSION`.
  - Its second-level headings are, in this order: `Start a run on a supplied device`, `Replace a device`, `Post one final verdict`, `Read the records and the frozen driver`, `What a client imports`, `Limits today`.
  - Outside any marked passage it names in backticks: every call of `CALLS`; every name in `VERDICT_FIELDS`; the descriptor's parts `schema`, `kind`, `control`, `endpoint_file`, `session_id`, `binding` and `image_sha256`; and the record names `device-descriptor/1`, `equivalence/1` and `assistive/1`.
  - No line outside a marked passage says `bench`, in any case.
  - Under `Limits today`, outside any marked passage, it says that the service accepts a supplied device only for a run that names a registered case.
- **03-B6** — The benchmark takes the verdict rules from the interface. No module of the benchmark package imports or calls `_evaluation_errors`, and the service no longer has that name. `benchmark_support/harness.py` and `benchmark_support/suite_reporting.py` import `verdict_errors` from the interface module.
- **03-B7** — The deletion marking follows:
  - the document has exactly one marked passage, under its heading `Limits today`;
  - `tests/test_benchmark_client_interface.py` is under `Ready to delete`;
  - the interface module, the document and `tests/test_client_interface.py` are not on the list;
  - `tool_test_modules` names `tests/test_client_interface.py` and does not name `tests/test_benchmark_client_interface.py`.

  Feature 01's own tests prove the rest in the Test command: its check reports no problem on the repository, and its removal and its run of the tool's tests in the copy pass.

## Interface and seams

- The interface module is new: `src/generative_driver/client_interface.py`. It imports the standard library and `device_client` only. It never imports the service, because the service imports it.
- The verdict record's rules move into it from `configurator.py:686-749`. The code and every sentence it returns stay as they are. The field set becomes `VERDICT_FIELDS`, the two grading methods become a private constant, and `_evaluation_errors` becomes `verdict_errors`. The comments at `configurator.py:686-688` and `:693-694` name the bench harness and its cases; they are reworded in the tool's words as they move.
- `PROTOCOL` is defined in the interface module. `configurator.PROTOCOL` stays, as the same value, for the code that takes it from there.
- The service uses the interface module: `_record_evaluation` calls `verdict_errors`, and chooses the driver a posted verdict must name with `frozen_driver` where `configurator.py:3138-3139` chooses it today. `EVALUATION`, `METHODS` and `_evaluation_errors` leave `configurator.py`.
- The module's doc and docstrings are in the tool's words: a client, a supplied device, a posted verdict. They name neither the benchmark nor its harness.
- In the benchmark's folder four lines change, in two files that are not grading code. `harness.py:286` keeps `IntegrityError` from `configurator` and takes `verdict_errors` from the interface module, and its call at `:337` uses that name. `suite_reporting.py:192` takes `PROTOCOL` and `verdict_errors` from the interface module, and its call at `:200` uses that name. Neither file imports the function under its old name. Nothing else changes in the benchmark's folder.
- The document, `docs/client-interface.md`, is short. Under each of its first four headings it says what the client sends, what it gets back and when the tool refuses. It is written from the service's code as it stands, and says nothing the service does not do:
  - `Start a run on a supplied device`: `start` with `goal`, `device`, `effects`, `budget_seconds`, `executor`, `executor_config`, `request_id` and `evaluation_token_sha256`. The descriptor's parts, with `image_sha256` as the hash of the image the run is started on. That device operations go over the descriptor's `binding`. That the tool asks the control endpoint for six things only (`status`, `observe`, `select_condition`, `set_running`, `replace`, `release`), and that the client owns the device and starts and stops it. That the token hash later proves who may replace the device and post the verdict. That a repeated `request_id` finds the same run. That the run ends `completed` and waits for the client's verdict. `resume` and `ping`.
  - `Replace a device`: `attach_device` with `run_id`, `token` and `device`; its checks; that it does not resume the run.
  - `Post one final verdict`: `record_evaluation` with `run_id`, `token` and `equivalence`; the record's fields and the rules `verdict_errors` applies; that it is accepted once, for a completed run with nothing uncertain, about the driver `frozen_driver` gives; that an `unscored` record leaves the run waiting.
  - `Read the records and the frozen driver`: `status`, `events` and `result`. That for a run that waits for a posted verdict, `frozen_driver(result['accepted_handoffs'])` gives the path and the hash of the driver the verdict must name. That the client reads the files at that path on the same machine, and that the hash is `generative_driver.integrity.digest` of the path.
  - `What a client imports`: the interface module, `call` and `default_home` of `generative_driver.client`, and `digest` of `generative_driver.integrity`. Nothing else in the tool is part of this interface. One sentence says that an uncertain effect is cleared, and a run cancelled, by the operator through the command line or the MCP server, outside this interface.
  - `Limits today`: two sentences outside the marked passage, and then the marked passage. The first is the one 03-B5 gives. The second says that the verdict record's `method` and `capabilities_sha256` and the descriptor's `kind` are due to change in a later version of this interface. Neither sentence says `bench`. The passage names what the service takes from a registered benchmark case today: the start keys `case`, `case_options` and `admission`, the inputs and the mission, the pinned image that `image_sha256` must equal, the effects the case requires, and the grading method that fits the case. It says that Features 04 and 05 of this Run replace the case with inputs and a mission the client supplies.
- What the control endpoint's file holds and what each of its six answers must be is not in the document yet. Feature 04 moves the code that uses them behind the interface, and its Spec adds them.
- `README.md` gets one row in its table of documents: `Supply your own device and mission`, linking to the document. The row does not say `bench`.
- `tests/test_client_interface.py` tests 03-B1 to 03-B5. It imports nothing from the benchmark package and no test file that the removal takes away, so it runs in the stripped copy. It holds the two marker lines as its own text. For 03-B1 and 03-B2 it starts a run with no case and a runtime that cannot start, as `tests/test_reporting.py:38-60` does, and reaches the service through `client.call`.
- `tests/test_benchmark_client_interface.py` tests 03-B6 and 03-B7. It is a benchmark item. It reads the rows from the text of the deletion list, by heading.
- Existing tests change only where the move makes them fail: `tests/test_evaluation.py` imports the old name at `:1333`, `:1407` and `:1471` and calls it at seven places. Those take `verdict_errors` from the interface module. No assertion changes.
- Test names in this Feature's two test files carry `03_B1` to `03_B7`. Other Runs already have tests named `test_03_B…`, in `tests/test_service_smoke.py`, `tests/test_generated_trials.py` and `tests/test_distribution.py`, and the kit's check cannot tell the Runs apart. So the Reviewer checks that each Behaviour here has its test in this Feature's own files.

## Out of scope

- Making the service accept a supplied device without a registered case. That is Features 04 and 05.
- The inputs and the mission a client supplies as data, and the control endpoint's protocol. Feature 04's Spec fixes them and adds them to the document.
- Any change to a call, a record, a field name, a value, a message or a refusal. Messages that name the bench harness stay as they are.
- Putting the records in the tool's own terms. That is Feature 05.
- A new call, and a way to fetch the frozen driver's bytes through the service. The client reads the files.
- Giving the interface version over the wire, such as a field in the reply to `ping`.
- The service's other calls: `cancel`, `respond`, `tool` and the device calls belong to the tool's command line and MCP server; the suite calls and `benchmark_spawn` are the work of Features 06 and 08.
- The other rule by which a run with no case names its own frozen package in its own verdict (`configurator.py:1127-1130`). It stays in the service.
- The benchmark's other imports of tool names, private ones among them, and the files it reads in a run's folder without asking the service. Features 04 to 08 remove them or add them to the interface, and Feature 09 adds the test that holds the boundary.
- Any edit to grading code, and anything that changes a grader identity.

## Deviations

- Six lines inserted and five removed in the benchmark's folder, where the Spec names four: `harness.py:286`, `:287`, `:338` and `:339`, and `suite_reporting.py:192` and `:200` — same two files and the same two names, neither in the grader closure; the extra lines are the second half of one import and the re-indented continuation of one call, so no behaviour and no scope changes.
- Five lines over 120 columns are kept in the moved `verdict_errors` — the Spec says the code stays as it is, which is what makes "no behaviour changes" checkable.
- Comments are reworded as they moved, and `verdict_errors` has a new docstring — the Spec orders both; no code or returned sentence changed.
- `_record_evaluation` reads `frozen = (frozen_driver(accepted) or {}).get('sha256')` — the Spec says to use `frozen_driver` there; the value is the same as before.
- The document holds sentences the Spec's list does not name, among them what `call` itself answers with no `error`, what `record_evaluation` answers, the 300-character limit of the `mission` texts, the defaults of `effects`, `budget_seconds` and `executor`, the forms of `binding`, when the service sends `release`, and a five-line example — each says what the client sends, gets back or is refused, and each is true of the code; none adds scope.
- The README row has no test of its own — no Behaviour names it; Feature 01's check and the removal's link check hold it.
- The 03-B6 test also asserts that `EVALUATION` and `METHODS` left the service — the Spec says so under Interface and seams.
- The 03-B1 constants test ties its own two literals together — harmless beside real assertions on the module.
- `tests/test_benchmark_client_interface.py` imports helpers from `tests/test_deletion_marking.py` and `tests/deletion_marking.py` and reads benchmark modules as source — it is a benchmark item, all three are removed together, and it reads the list by heading as the Spec says.
- Order of work: one 03-B1 test stayed red until 03-B4, and the service kept a temporary alias `_evaluation_errors = verdict_errors` between 03-B2 and 03-B6 — the alias is not in the final tree.
- The service-facing parts of 03-B1 and 03-B2 were red only for the missing interface names — it follows from "no behaviour changes"; the missing names are the new part of each Behaviour.

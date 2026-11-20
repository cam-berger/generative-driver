# 02. driver-document

## Intent

Today the tool states a driver's operations three times. The worker writes the interface model, `model.json`, in one of four versions (`interface-model/3` to `/6`). The driver contract, `contract.json`, holds what each operation means. The declarative protocol, `protocol.json`, holds the wire steps a second time. Five places check that contract and protocol compile back to the model. Only a generic run asks its worker for the contract, with a second guide (`CONTRACT.md`) and a second tool (`contract.py`), and its emit stage then merges what the worker wrote with what the tool derives. A run on a supplied device never asks, so its package describes every operation with a placeholder such as "Execute public operation measure."

This Feature makes the interface model the one statement of a driver, in one format, `interface-model/7` ([ADR 0010](../../adr/0010-a-package-is-data-in-one-format.md), decision 3). Each operation holds its meaning beside its wire steps. The driver contract and the declarative protocol become views that the runtime computes from the model. Workers get one document, and every run asks its worker for meanings. A meaning is optional: where one is missing, the driver says so wherever it is shown.

Six things change for a run or a user.

- A model with any other tag is refused, and the refusal names the tag. Every package emitted before this Feature holds such a model, so the tool refuses those packages too. A run or a managed session that began before the change, over such a model, is refused when it continues.
- A meaning is part of the model. Changing one changes the model's hash, so a probe record taken with the earlier bytes no longer belongs to it. Today a contract can change while the model stays.
- A meaning that is written wrongly, such as a blank description, makes the model invalid, as a wrong wire step does. The worker's validator reports it, the gateway refuses a device call with that model, and it costs a repair. Today a bad contract entry costs only its own text.
- A range of values that the model states for an output is checked wherever the model runs. A reading outside it fails a probe sample and drives a repair. Today only a package checks it.
- A precondition is still applied only by a package. So a wrong one is first noticed when the package runs, which can be as late as fresh reuse. The model document tells workers to write one only where the evidence shows that the device refuses the operation otherwise.
- The worker of a run on a supplied device gets a longer model document than today and is asked for meanings. What that does to benchmark results only the benchmark can show. This Feature starts no model run.

A client that calls `validate_model` with a model of an earlier tag gets a refusal.

One rule decides what the lists below miss: code, text or a test that exists only because a driver is stated in more than one file, or in more than one version, goes.

## The format

An interface model is a JSON object with the tag `interface-model/7` in `schema` and, as before, `device`, `channel`, `identity`, `operations` and `provenance`. The file a worker writes is still `model.json`, and the tools still take it as `model_dir`.

**Wire rules.** Every rule about the channel, parameters, steps, decoders and provenance is the one the runtime applies to `interface-model/6` before this Feature. None is added, changed or removed. So every wire feature of the four earlier versions can be used in one model.

**Meanings.** These fields are new, and each is optional.

| Where | Field | Rule |
|---|---|---|
| operation, parameter, output | `description` | Text that is not blank, at most 8192 characters. What the device does or reports, in a user's words. |
| operation, parameter, output | `uncertainties` | A list of at most 64 entries. Each is exactly `code`, `message` and `evidence_refs`: code and message are non-blank text of at most 8192 characters, and `evidence_refs` is a list of at most 128 non-blank texts of at most 2048 characters. |
| parameter | `unit` | Text. An output's decoder has `unit` already, with the same rule. |
| output | `value` | What the decoded value may be. `type` is required: `integer` or `number` for a decoder that gives a number (`integer`, `float32`, `ascii_number`, `kv_number`), `string` for every other. With a number, `minimum`, `maximum` and `enum` may follow; with a string, `enum`, `minLength` and `maxLength`. Bounds are finite and ordered, an `enum` is a list that is not empty, and lengths are integers from 0 to 65536. No other keyword is allowed. |
| operation | `preconditions` | A list of at most 16 entries. Each is exactly `id`, `description`, `observation`, `operator` and `expected`. The `id` is unique in the operation, the description is not blank, and the operator is `eq`. The `observation` is exactly `operation`, `parameters` and `output`: a read operation of the same model that has no precondition of its own, arguments that operation accepts, and one of its outputs. `expected` is a value that output may have. |

Two codes of an uncertainty have a meaning to the tool. `unit_not_stated` on a number without a unit says that the evidence states none. `not_exercised` on an operation says that interpretation did not run it.

These are the rules the driver contract has today, moved into the model. One thing a contract can state today is dropped: `contentEncoding` on an output, which no code of the tool writes. A `bytes` parameter still shows as a hex string.

## Behaviours

- **02-B1** — One format holds every wire feature. `validate_model` accepts a model tagged `interface-model/7` that uses, together, a `lines` exchange, a `frame` exchange with a transmitted `check`, a `bytes` parameter sent in the `raw` format and an integer decoder with a `mask`. The runtime executes each operation of that model in replay.
- **02-B2** — Any other tag is refused by name. For a `model.json` whose schema is `interface-model/3`, `/4`, `/5` or `/6`:
  - `validate_model` returns not ok with one defect at `schema` that names the tag found and `interface-model/7`.
  - The tools `model_validate`, `model_qualify`, `interface_describe`, `interface_execute`, `probe_run`, `probe_diff` and `emit_package` each return not ok with exit 1 and that reason. What Spec 01 says such a refusal leaves untouched (its Behaviour B1) holds here, and `interface_execute` says `transport_open_attempted` false.
  - A package whose `driver/model.json` has such a tag is refused, with the tag named, by `emit_check`, by `emit_test_live` and by `ManagedDriver.open`. The package check looks at the model's tag before it compares anything else. No file of the package runs, no transport opens, and `emit_test_live` writes no live record.
  - The gateway rule of Spec 01 (its Behaviour B7) holds for these tags: the worker gateway refuses the device call itself.
- **02-B3** — Meanings are checked with the model. `validate_model` accepts every field of "The format". It refuses a model that breaks one of the rules there, with one defect that names the operation and the field. A field the format does not name is refused as an unknown field, as today.
- **02-B4** — The driver contract is a view. `contract_view(model)` gives, for a model that validates, the driver contract in the shape `driver-contract/1` has today, and changes nothing in the model. For this operation

  ```json
  "measure": {
    "effect": "read",
    "description": "Read the temperature of the probe.",
    "parameters": {"channel": {"type": "integer", "minimum": 0, "maximum": 3}},
    "steps": ["…"],
    "outputs": {"temperature": {"from": "reply", "kind": "ascii_number", "prefix": "T:", "unit": "Cel",
      "description": "Temperature of the probe.",
      "value": {"type": "number", "minimum": -40, "maximum": 125}}}
  }
  ```

  the view's operation is

  ```json
  {
    "effect": "read",
    "description": "Read the temperature of the probe.",
    "uncertainties": [],
    "parameters": {"channel": {
      "schema": {"type": "integer", "minimum": 0, "maximum": 3},
      "description": "Meaning not documented.",
      "unit": null,
      "uncertainties": [{"code": "semantics_unknown",
        "message": "Parameter channel meaning and operating assumptions are not documented.",
        "evidence_refs": []}]}},
    "outputs": {"temperature": {
      "schema": {"type": "number", "minimum": -40, "maximum": 125},
      "description": "Temperature of the probe.",
      "unit": "Cel",
      "uncertainties": []}}
  }
  ```

  - A missing `description` becomes `Meaning not documented.` and adds the `semantics_unknown` uncertainty shown, whose message starts with `Operation NAME`, `Parameter NAME` or `Output NAME`. It is added after the uncertainties the worker wrote, and not when one of them has that code already.
  - A missing `unit` becomes null. An output without `value` gets `{"type": "number"}` when its decoder gives a number and `{"type": "string"}` otherwise. A parameter's `schema` is what the model's parameter rule gives today, a `bytes` parameter as a hex string.
  - `preconditions` appear in the view's operation as written, and only when the model has them.
  - `protocol_view(model)` gives the wire part in the shape `driver-protocol/1` has today: `runtime_schema` `interface-model/7`, the channel, and for each operation its steps and its outputs without `unit`, `description`, `uncertainties` and `value`.
- **02-B5** — The public rules come from the model. No `contract.json` is read to decide any of them.
  - Wherever a model runs, which is `interface_execute`, `probe_run` and every use of a package: a decoded output outside its `value` makes the result not ok with the code `output_schema_violation` and the fault `model`, and the reading is kept under `invalid_outputs`. At probe this fails the sample, and `probe_diff` gives it as a defect of the model.
  - In a package, which is a managed session, `emit_test_live` and a generated tool call: each precondition is observed fresh before the requested command. When the observed value differs, the result has the code `precondition_failed` and the requested command is not sent. When the observation fails, the code is `precondition_unavailable`.
  - In a package, the result's `warnings` hold every uncertainty of the operation, its parameters and its outputs, each with its scope, the `semantics_unknown` ones of 02-B4 among them.
  - The replay of `emit_check` stays a replay of the wire, as the probe that wrote the record was. It applies `value` and no precondition.
- **02-B6** — Emit shows what the worker wrote. `emit_package`, given a model with meanings and its probe record, writes a package in which:
  - `driver/model.json` is the model, byte for byte;
  - `driver/contract.json` is `contract_view` of it and `driver/protocol.json` is `protocol_view` of it;
  - each tool of `mcp-tools.json` shows the operation's description, each unit, each uncertainty as a warning and each precondition;
  - `describe` of a managed session gives the same contract.

  A `contract.json` or `protocol.json` beside `model.json` in the model directory is not read: the package is the same with and without them. The package check refuses a package whose `driver/contract.json` or `driver/protocol.json` differs from its view, and names the file. It checks no tag of those two files.
- **02-B7** — A missing meaning is marked. For a model with no `description` anywhere, each tool of the package says `Meaning not documented.` for the operation, each parameter and each output, and each result carries the matching `semantics_unknown` warnings. A number with no unit reads `Unit: unknown.`, and `Unit: not stated by the device.` when an uncertainty of that output has the code `unit_not_stated`.
- **02-B8** — Workers get one document, and every run asks for meanings. The interpret workspace of a generic run, of a device-first run and of a run on a supplied device, and the workspace `interpret_run` prepares for `interpret-selfdescribing`, each hold the same `INTERFACE_MODEL.md`, byte for byte the tool's one model document.
  - None holds `CONTRACT.md` or `contract.py`.
  - No file, prompt or tool description a worker gets names `interface-model/3`, `/4`, `/5` or `/6`, speaks of schemas 3 to 6, or names `contract.json`, `CONTRACT.md` or `contract.py`. "Interface and seams" lists these texts.
  - The document names `interface-model/7` and none of the four earlier tags. It describes every field of "The format", and it tells the worker to narrow a `value` or write a precondition only where the evidence fixes it. Every complete model it shows validates.
  - `BRIEF.md` of both front ends asks the worker to write, in `model.json`, a description of each operation, parameter and output, the unit of each number, and an uncertainty where the evidence leaves a meaning open.
- **02-B9** — The worker's validator says what is not documented. `python3 validate.py .` in an interpret workspace, and the tool `model_validate`, give an `advice` list beside the result. It has one entry for each operation, parameter and output without a description, such as `operations.measure.outputs.temperature has no description`, and one for each output whose decoder gives a number and that has neither a unit nor a `unit_not_stated` uncertainty, such as `operations.measure.outputs.temperature has no unit and no unit_not_stated uncertainty`. For a model that does not validate the list is empty. Advice changes neither `ok` nor the exit status. `validate.py` writes the list into `validation/result.json` with the result; `model_validate` writes no file, as today.
- **02-B10** — Task mode writes meanings into the model.
  - A handler's fragment holds `operation`, `model`, `provenance` and `replies`. The meanings are fields of `model`, which is the operation as it stands in `model.json`. A `contract` a worker still writes there is ignored.
  - A handler writes no precondition, because a precondition spans operations and a handler sees one. `HANDLER.md` tells it to name a needed one in `NOTES.md`. The assembly takes each fragment's operation without `preconditions`, and the cross-check, which sees the whole model, writes them. A repair takes the re-run operation the same way and keeps the preconditions the previous final model has for it.
  - The inventory names no format. The configurator writes `interface-model/7` into `MODEL_FRAME.json` and into the assembled model, and ignores a `schema` a worker still writes in `inventory.json`.
  - The assembly writes `model.json` and `replies.json`, and the assembled model holds each kept fragment's meanings. A repair puts each re-run operation, with its meanings, in place of the previous one. The cross-check workspace holds no `contract.json`.
- **02-B11** — Device-first interpretation and supplementary documents name the model. The device-first worker is told to give each write or actuate operation it declares an uncertainty with the code `not_exercised` on that operation in `model.json`, and to write `model.json`, `replies.json` and `NOTES.md`. A worker with supplementary documents is told to record a disagreement in `NOTES.md` and as an uncertainty of the operation in `model.json`.
- **02-B12** — The smoke runs on the one format. The stand-in model is tagged `interface-model/7` and describes `measure` and its temperature, with a `unit_not_stated` uncertainty. The scripted worker hands on the model alone. The tool that fresh reuse is served shows that description and `Unit: not stated by the device.`
- **02-B13** — A run on a supplied device keeps the meanings. The model wired to the supplied device's binding differs from the interpreted model only in its channel, so the package's tools show the meanings the worker wrote. A `contract.json` or `protocol.json` that an interpret handoff carries is not read, checked or handed on, and does not stop the probe.
- **02-B14** — The emit notes of a generic run list what is not documented. The configurator's part of the notes has `undocumented`: the path of each operation, parameter and output without a description, such as `operations.measure.outputs.temperature`, in the model the emit worker is given. The configurator computes it when it prepares the stage. The key is left out when the list is empty. The notes have no `driver_contract`.

## Interface and seams

The public interfaces are the runtime (`interface_runtime.engine.validate_model`, `interface_runtime.contracts`, `interface_runtime.driver.PackageSession`), the tool facade (`generative_driver.toolkit.call_tool` and `list_tools`), the managed session (`generative_driver.sdk.ManagedDriver` and the generated tools over MCP) and the configurator, whose workspaces and notes a controller shows.

- `interface_runtime.contracts` gives `contract_view(model)` and `protocol_view(model)`. What the SDK uses there to check its own tools stays. `validate_model` keeps its result: `ok`, `defects` and `checks_run`, which names `interface-model/7`.
- The engine checks an output's `value` when it decodes the output, so every path that runs a model applies it in one place.
- Everything that reads a driver reads the model and computes the view: `PackageSession`, `ManagedDriver.contract`, the configurator's device access (open, execute, a worker's tool call, `describe`) and the package's own entry module. Nothing reads `driver/contract.json` or `driver/protocol.json` to decide what an operation means or does.
- Until Feature 04 changes the package, its files and its manifest stay as they are. `driver/contract.json` and `driver/protocol.json` are still written and still hashed in the manifest; they are copies of the views. A result's `warnings_ref` names `driver/model.json`.
- The one model document is `src/generative_driver/resources/docs/INTERFACE_MODEL.md`. It holds what `INTERFACE_MODEL_V6.md` says of the wire and what `CONTRACT.md` says of meanings, units (UCUM codes such as `Cel` and `%`), uncertainties, narrowed values and preconditions, with one worked model that has meanings. Feature 01 freed the name.
- The texts of 02-B8 are: `skills/interpret-interface/SKILL.md` and `skills/interpret-selfdescribing/SKILL.md`, which also lose what they say of a conversion module; `INVENTORY.md`, `HANDLER.md` and `CROSS_CHECK.md`; `DEVICE_FIRST.md` and the device-first prompt in `device_first.py`; the instruction on supplementary documents; `TASKS.json` and `REPAIR_CONTEXT.json`; in `tools.json` the descriptions of `interface_describe`, `interface_execute`, `model_validate`, `probe_run`, `probe_diff` and `emit_package`; and the refusal of `probe_run` that speaks of a "schema 3 probe". The description of `model_validate` names `advice`, and keeps what it says of `interface-model/2-experimental`.
- Tests of 02-B1 to 02-B4 attach at the runtime, beside `tests/test_package_engine.py`, `tests/test_engine_protocols.py` and `tests/test_driver_contract.py`. Tests of 02-B2, 02-B6, 02-B7 and 02-B9 attach at `call_tool`. Tests of 02-B5 attach at `call_tool` for a model directory, and at `PackageSession`, a managed session and a generated tool for a package, as `tests/test_driver_contract.py`, `tests/test_device_sessions.py` and `tests/test_device_mcp.py` do. Tests of 02-B8, 02-B10, 02-B11, 02-B13 and 02-B14 attach at the controller, at the prepared workspace and at `list_tools`, as `tests/test_configurator.py`, `tests/test_input_paths.py`, `tests/test_interpret_tasks.py` and `tests/test_supplied_stage_files.py` do. The test of 02-B12 is the smoke's, in `tests/test_smoke_contract.py`.
- Every model a test builds is tagged `interface-model/7`: `example_model`, `controlled_model`, `make_model`, the toy protocols, the task fixtures and the literal models, the scripted ones in `tests/test_configurator.py` among them. A retagged model must meet the rules of `interface-model/6`, which ask a USB exchange for its endpoints. A test that needs a refused tag writes it out. A test that needs a meaning, a narrowed value or a precondition states it in the model and emits the package from that model; it does not rewrite a package's files.
- Tests of earlier Runs are already named `test_02_B1_…` and so on, so the kit's check by name cannot tell them from this Feature's. The implementer's report names each Behaviour's own tests, and the Reviewer checks them by those names.
- Tests use scripted workers. None starts a model run.

## Deleted

Files:

- `src/generative_driver/resources/docs/INTERFACE_MODEL_V3.md`, `INTERFACE_MODEL_V4.md`, `INTERFACE_MODEL_V5.md` and `INTERFACE_MODEL_V6.md`.
- `src/generative_driver/resources/docs/DRIVER_CONTRACT.md`. The link to it in `docs/extending-drivers-and-mcp.md` then points to the one model document.
- `src/generative_driver/resources/toolchain/contract/`, which is `CONTRACT.md` and `contract.py`.
- `src/generative_driver/driver_contract.py`: `TASK`, `REPAIR`, `interpret_inputs`, `emit_inputs` and the merge.
- `tests/fixtures/earlier-package/`. Its model has an earlier tag.
- `tests/test_contract_authoring.py`, all 19 tests named `test_05_B6_…`, `test_05_B7_…` and `test_05_B8_…`. The workflow they test is gone.

Code:

- `interface_runtime/contracts.py`: `from_model`, `compile_contract`, `describes` and what only they use. The precondition rules move into the model's validation.
- `interface_runtime/engine.py`: the list of four tags, every version gate and the unused fourth argument of `_tx_bound`, with the argument `interface_runtime/evidence.py` passes for it.
- `_toolchain/interpret.py`: the table that gives each front end a versioned document, with its fallbacks.
- `_toolchain/interface.py`: the branch of `emit_package` that reads an authored contract and protocol.
- `supplied/stages.py`: `_contract_pair` with its four calls, and `contract.json` and `protocol.json` among the files it keeps and hands on.
- `interpret_tasks.py`: the `contract` of a fragment; `contract.json` in the assembly, the repair and the cross-check; `CONTRACT.md` and `contract.py` in the kit of a task; the inventory's `schema`.
- `scripted_worker.py`: the step that writes `contract.json`.
- `configurator.py` and `device_first.py`: every use of `driver_contract`, and the `driver_contract` key of the notes.

Tests:

- `tests/test_driver_package.py`: `test_05_B4_an_earlier_package_verifies_under_the_renderer_it_names`, `test_05_B5_emit_check_replays_the_earlier_package_on_the_installed_runtime`, `test_05_B5_a_managed_session_over_the_earlier_package_measures_the_stand_in_device` and `test_05_B5_fresh_reuse_is_served_the_earlier_catalog_unchanged_and_a_generated_tool_runs`.
- `tests/test_driver_package.py`: `test_05_B4_a_package_naming_a_format_the_kit_does_not_know_is_refused_naming_it` stays, on a package the test emits, with its cases of the renderer, the catalog format and the package format. It loses the three cases that rewrite `driver/contract.json` or `driver/protocol.json`: those files are copies now, and 02-B6 covers a copy that differs. A model tag the tool does not know is a case of 02-B2 on `driver/model.json`.
- `tests/test_driver_contract.py`: `test_protocol_cannot_override_an_unknown_public_unit`, `test_protocol_cannot_redeclare_public_parameters` and `test_emission_rejects_an_incomplete_authored_contract_pair`. They test the pair of files. The module's other tests stay and state their rules in the model.
- `tests/test_owner_generated_integration.py`: `test_authored_contract_pair_survives_benchmark_adaptation_and_rejects_projection_mismatch`. The test of 02-B13 takes its place.

## Out of scope

- The package: its files, its manifest, the tag `interface-package/2`, its bundled runtime, `runtime_current`, `REFERENCE.md` and catalog renderers 2 and 3. Feature 04 changes them. Until then a package's own command line still runs its bundled copy. The one part of it this Feature changes is the entry module, which reads the model as every other reader does.
- The words and names of the generated tools, and the module that renders them. Feature 03.
- Preconditions at probe and ground. `probe_run` and `interface_execute` of a model directory check them as part of the model but do not observe them before a command. A package does.
- `interface-model/2-experimental`, the `firmware_*` tools and `interpret-firmware-binary`.
- The replies formats `interface-replies/1` and `/2`, and the qualifier.
- A description of the device as a whole, and runs that drive several controllers.
- The notes of a run on a supplied device.
- The client interface and its version. `validate_model` stays its one runtime import.
- The documents under `docs/`, except the one link named above. Feature 05.
- The acquire side, the run folder and the benchmark's repository.

## Deviations

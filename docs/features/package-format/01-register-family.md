# 01. register-family

## Intent

The tool has two families of driver model. Every run uses the interface model, which is data that the installed runtime executes. No run asks for the register model (schema `interface-model/1`): only a developer who calls `interpret_run` by hand with one of three skills gets a workspace for one. Its model carries a `convert.py` that a worker wrote, its package carries that code with a runtime of its own, and about 25 branches in five modules choose between the two families. A worker that ignores its brief can still hand one in, and the configurator's own check then runs that `convert.py`. [ADR 0010](../../adr/0010-a-package-is-data-in-one-format.md) decides to delete the family.

This Feature deletes the register family and nothing else. After it, the tool treats a register model or a register package like any model or package of a format it does not know: it refuses it, names what it found, and loads, imports and runs no file of it. A run on the interface family behaves as before, with two visible differences. The ground worker no longer gets `probe_observe`, which reads only register models. And a device call with a model of a format the runtime does not know is refused by the gateway, where today the gateway lets it through for the tool to refuse.

One rule decides whatever the lists below miss, and whatever has moved once this Run is based on the `dispatch-outcome` Run: code, text or a test that exists only for a register model, a register package or a register probe result goes. Anything the interface family or the acquire stage uses stays.

## Behaviours

- **01-B1** — `model_validate`, `probe_run`, `probe_diff` and `emit_package`, given a model directory whose `model.json` has the schema `interface-model/1`, each return not ok with exit 1 and a reason that names `interface-model/1`. None of them imports or runs `convert.py` or any other file of the directory.
  - `model_validate` writes no file into the directory.
  - `probe_run` says `transport_open_attempted` false and writes no probe record.
  - `probe_diff` and `emit_package` refuse before they read the probe, and `emit_package` creates no package folder.
- **01-B2** — `emit_check` and `emit_test_live`, given a folder whose manifest names no package format, as a register package's does, each return not ok with exit 1 and a reason that says the package format is not one the tool knows. They import and run no file of the folder. `emit_test_live` says `transport_open_attempted` false and writes no live record. This holds with and without `model_dir`.
- **01-B3** — `interpret_run` with the front end `interpret-datasheet`, `interpret-firmware` or `interpret-blackbox` is refused: not ok, exit 1, and the reason names the front end given and lists the front ends that exist. It writes no workspace, seal or attempt record.
- **01-B4** — The tool list, as `list_tools()` and as the command `generative-driver tools` give it, holds neither `probe_observe` nor `model_provenance_audit`, and `call_tool` refuses each as an unknown tool. The tools that remain show none of the parameters only the register family took: `wiring` of `interpret_run` and of `emit_package`; `bus_url` and `period_override_ms` of `probe_run`; `random_files` and `seed` of `model_validate`; `seeds`, `seed` and `tol_rel` of `emit_check`.
- **01-B5** — A generic run's ground worker is assigned exactly `probe_run`, `interface_execute` and `probe_diff`, and the objective it is given names no other tool. The notes of its handoff hold no `supplied_capture_observations` check.
- **01-B6** — `acquire_classify` names no front end that no longer exists. Its result for an ELF image, for a datasheet and for a bus scan has no `front_end`; every other field of those results is as before, and a raw image still names `interpret-firmware-binary`.
- **01-B7** — Through the worker gateway, a device call whose model has a schema the runtime does not know, `interface-model/1` among them, is refused by the gateway itself before it starts the tool: the reason names the schema found, the device is not contacted and no effect is left uncertain, whatever the run's grants. This holds for `probe_run` and `interface_execute` of a model directory and for `emit_test_live` of a package.

## Interface and seams

The public interfaces are the tool facade, `generative_driver.toolkit.call_tool` and `list_tools`, with the command line over it, and the configurator's worker gateway (`tools` and `tool` of a controller).

- Tests of 01-B1 to 01-B4 and 01-B6 attach at `call_tool` and `list_tools`, beside their neighbours in `tests/test_package.py`, `tests/test_toolkit.py` and `tests/test_acquisition_pipeline.py`. Tests of 01-B5 and 01-B7 attach at the controller, as `tests/test_configurator.py` does.
- A test shows that no file of a folder ran by putting a `convert.py`, and for a package a `tests/test_live.py`, there that writes a marker file when imported or run, and finding no marker afterwards.
- A parameter this Feature removes, when a caller still passes it, is ignored, as every parameter a tool does not know is today.
- Tests of earlier Runs are already named `test_01_B1_…` and so on, so the kit's check by name cannot tell them from this Feature's. The implementer's report names each Behaviour's own tests, and the Reviewer checks them by those names.

Tool descriptions are part of what a worker reads. In `resources/toolchain/tools.json` the descriptions of `interpret_collect`, `model_validate`, `probe_run`, `emit_package`, `emit_check`, `emit_test_live` and `firmware_collect` lose every sentence about schema 1, a register model, a register package or its regression suite, and say what the tool does for an interface model. `emit_package` describes the interface package as it is today; Feature 04 changes that package.

## Deleted

Files, all under `src/generative_driver/resources/`:

- `toolchain/template/driver/` (`__init__.py`, `__main__.py`, `device.py`): the register package's runtime and command line.
- `toolchain/template/register_validate.py`: the `validate.py` of a register workspace.
- `tools/model_exec.py`: the schema-1 runner.
- `tools/pyftdi_shim/` (three files): the stand-in for `pyftdi` that serves a register file.
- `tools/provenance_audit.py`: the script behind `model_provenance_audit`.
- `docs/INTERFACE_MODEL.md`: the worker document for schema 1. A workspace still gets the interface model's document under this name.
- `toolchain/skills/interpret-datasheet/`, `toolchain/skills/interpret-firmware/`, `toolchain/skills/interpret-blackbox/`.

Tools, each with its entry in `tools.json`, its function, its name in `toolkit.py` and its name in the configurator's stage lists:

- `probe_observe`: compares a register model with a captured bus record.
- `model_provenance_audit`: looks for a model's register addresses on the datasheet pages it cites. No run assigns it.

Code in modules that stay:

- `_toolchain/emit.py`: `_ops_table`, `_reference`, `_evidence`, `_tests`, `_shim_run`, `_register_integrity`, `_register_snapshot`, `_check_register_snapshot`, `TEMPLATE`, `SHIM` and the register path of `emit_package`, `emit_check` and `emit_test_live`. The three tools then hand every call to `_toolchain/interface.py`. `HOST_FAULTS` stays.
- `_toolchain/interface.py`: `is_interface_package`, whose only callers are those paths.
- `_toolchain/probe.py`: the register path of `probe_run` and of `probe_diff`, `_spi_cmd` and `probe_observe`.
- `_toolchain/interpret.py`: the schema-1 validator inside `model_validate`, `model_provenance_audit`, `STDLIB_OK`, the three skill names in `FRONT_ENDS`, the register kit that `interpret_run` copies into a workspace, `WIRING.md` in `RESERVED`, and the schema-1 clause of `_output_present`.
- `_toolchain/runner.py`: everything except `FtdiI2c` and `hx`, which `acquire_bus_scan` and `acquire_register_sweep` use. What `FtdiI2c` took from `model_exec.py` is written into this module, and its module doc says what is left.
- `toolkit.py`: `wiring` and `bus` in `_PATH_ARGUMENTS`.
- `configurator.py`, each a clause that only a register model or a register tool result reaches: the schema-1 classification in `operation_effects`; the rule of `_refuse_invalid_model` that lets a model through when the runtime does not know its schema, which 01-B7 replaces; the exit-2 clause of `_answered`; the exit-2 and exit-3 clause of `_device_diff` with its identity message; `SEEDED` and every reading of `seeds_run`, in the emit check and in `_emit_check_failure`; the live-suite branch of `_live_outcome`; the `interface-model/1` test in `_frozen_package`; the live suite counted as a function in `_fresh_reuse`; `identity_ok` in `_emit_inputs`; `bus` in `EVIDENCE_ARGUMENTS`; the reading of `probe_observe` results that writes the `supplied_capture_observations` check; and the clause about `probe_observe` in `GROUND_OBJECTIVE`.
- A comment or doc that gives the register family as the reason for a rule that stays is reworded to the reason that remains. This covers the module doc of `tool_process.py`: a tool can still wait on a device or on an analyzer it starts.

Tests whose subject is the register family, deleted:

- `tests/test_package.py`: `test_register_validation_preserves_supplied_model_bytes_and_inventory`, `test_register_workspace_and_package_validate_outside_installation`, `test_a_register_live_test_refuses_the_operators_binding_or_grants_as_its_contract_says`, `test_a_register_converter_that_fails_to_load_fails_the_check_and_is_no_host_fault`.
- `tests/test_configurator.py`: `test_probe_and_ground_workers_cannot_validate_an_assigned_model_in_place`, `test_the_configurator_validates_a_copy_so_a_validator_that_writes_beside_the_model_alters_no_input`, `test_a_package_checked_only_against_its_model_has_no_replay_and_is_not_equivalent`, with the fixtures that then have no user (`REGISTER`, `CONVERT`, the `seeded` mode of `finishing`).
- `tests/test_configurator.py`: `test_an_effectful_register_probe_the_register_runtime_cannot_load_leaves_no_uncertain_effect` becomes a test of 01-B7.
- `tests/test_tool_process.py`: `test_04_B9_a_conversion_module_that_loops_costs_a_failed_check_and_the_run_carries_on` and `test_04_B9_a_conversion_module_that_exits_the_process_costs_a_failed_check_not_the_service`. After this Feature no check of the configurator runs code a worker wrote, so a check can no longer loop or end the process through it. What the configurator does with a check that did not finish stays tested, with a limit that has already passed: a failed `executable_model_contract` check with the reason, the run carries on, and the probe stage says that validation did not run.

Tests that used a register model only as a vehicle keep the behaviour they test:

- `tests/test_tool_process.py`, `DeadlineTests` and `AbandonedTests`: deadlines and abandoned calls of tools that run in a child process. Their vehicle is a register model whose `convert.py` starts a child and never returns. The new vehicle runs no worker code: an interface model with an I2C channel on the `ftdi://` binding the tests already use, and a stand-in for `pyftdi` that the test puts on the tool process's import path, which the runtime loads at use time. The stand-in starts the child, records the process ids and hangs, exits or raises as each test needs. `probe_run` of a write operation serves the tests about an effect; a read operation or `interface_execute` serves the others.
- `tests/test_configurator.py` `test_ground_compares_a_handed_on_record_only_when_the_assigned_model_wrote_it`: the rule it tests is the configurator's and stays. It runs on an interface model, with stand-ins for `probe_run` and `probe_diff`.
- `tests/test_toolkit.py` `test_packaged_text_assets_work_under_a_non_utf8_system_locale` shows the same with an `interpret-interface` workspace and an interface package.
- In `tests/test_configurator.py` and `tests/test_package.py`, a test that mixes both families loses its register cases and keeps the rest. `test_generic_ground_runs_a_worker_that_tests_the_model_on_the_device` and `test_differences_the_configurator_never_saw_the_device_show_on_the_assigned_model_never_revise` no longer expect or call `probe_observe`.

## Out of scope

- Schema 2 (`interface-model/2-experimental`), the tools `firmware_prepare`, `firmware_collect` and `firmware_validate`, and the skill `interpret-firmware-binary`. No run reaches them either, but they are not the register family.
- The acquire side. `acquire_bus_scan`, `acquire_register_sweep`, `bus_prepare`, the parameter `allow_register_sweep` and the rungs and classes of `acquire_classify` stay as they are, although nothing interprets a bus scan or a register sweep after this Feature. Only the `front_end` of 01-B6 changes.
- The skills `interpret-selfdescribing` and `find-datasheet`, and the tools `usage_record` and `usage_summary`.
- The rule of `_probe_records` that the last `probe_run` to write a record path counts. It is written for any tool; its test keeps its behaviour and loses the word register.
- The interpret list of `STAGE_TOOLS`, which no run uses: it loses the name of the deleted tool and is otherwise left.
- Who gets `model_validate`. Probe and ground workers still do not.
- The copy the configurator makes of each assigned model before it validates it. It stays, and its comment gives the reason that remains: the configurator validates the bytes it has just hashed.
- The interface family's package: its format, its copy of the runtime and `runtime_current`. Feature 04 changes them.
- The documents under `docs/`. Feature 05 rewrites them; this Feature only repairs a link that would otherwise break.
- No new tool, and no code in `src/` that exists only for a test.

## Deviations

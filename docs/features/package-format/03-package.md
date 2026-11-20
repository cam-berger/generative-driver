# 03. package

## Intent

A package today holds 22 files for a device with three operations: Python code, a copy of the tool's runtime, the driver in three files, a tools file, a reference that tells the reader to run `python -m driver`, a requirements file and two manifest files. The tool itself does not run that code. A user who wants an agent to use the driver has to open a session with one call and start a server with another, and the tools the agent then sees are called `device_measure` on a server called `generative-driver-device`, whatever the device is.

This Feature builds the rest of [ADR 0010](../../adr/0010-a-package-is-data-in-one-format.md): decisions 1, 2, 4 and 6, and the documents that describe them.

- A package is five files of data with three visible parts: the driver, the MCP side and the documentation. It holds no code. It is shaped to hold several drivers, one for each controller; runs that drive several controllers are not built.
- Every use of a package runs the installed runtime over its data, on a supplied device too.
- One command serves a package to an agent. It opens the managed session, serves the tools and closes the session. The server and its tools are named after the device.
- One module renders the tools, with the same words on every path.
- The guides, the design and the decision records describe what is built, and what they show can be run.

What changes for a run or a user:

- A package of an earlier format is refused, and the refusal names the format. Feature 02 already refuses every earlier driver; this Feature refuses the earlier container as well.
- A package's own command line is gone, and with it `python -m driver sequence`, which ran several operations over one connection. Every call on a package opens the connection for that call, as a managed session does today. Using a driver on another machine needs the tool installed there.
- What an agent is served is rendered from the driver when the package is served. `mcp.json` and `README.md` in the package show the same content as it was when the package was made, and the emit stage accepts a package only when both equal what the tool renders, so a worker cannot edit them.
- The final verdict of a generic run judges fresh reuse by its generated tool calls alone. Real runs already work this way. The older rule, a live test with `emit_test_live`, applied only to a package of format 1, and about 1,700 lines of tests still run on such a package.
- How the benchmark runs a frozen driver once a package holds no code only the benchmark can show. The client interface still gives it the package's folder and hash, and its document says what the folder holds.

One rule decides what the lists below miss: code, text or a test that exists only because a package carries code, a copy of the runtime or an earlier format goes.

## The package

```text
<package>/
  README.md                        the documentation
  manifest.json                    the format, the name, the version, the drivers and a hash of every file
  mcp.json                         the MCP side: the tools, and an entry for an MCP client
  drivers/<driver>.json            the driver of one controller: an interface model
  evidence/<driver>.probe.json     the probe record that driver wrote
```

**Names.** A driver is named after its device: `device.name` of its model in lower case, with every run of characters other than the letters `a` to `z`, the digits and `_` turned into one `_`, and `_` trimmed from both ends. A name that is then empty is `device`; one that starts with a digit gets `device_` in front; one longer than 48 characters is cut to 48 and trimmed again. `SAMP1` gives `samp1`, `stand-in device` gives `stand_in_device`, `9-axis IMU` gives `device_9_axis_imu`, a null name gives `device`. A tool is `<driver>_<operation>`. The server has the driver's name.

**`manifest.json`** has exactly `schema` (`interface-package/3`), `name`, `version`, `drivers` and `files`. `drivers` is a list with one entry for each driver: `name`, `model` (the path of its driver file), `evidence` (the path of its probe record) and `evidence_kind` (`live` or `replay`, as the probe record says). `files` maps each of the other four files to its SHA-256. `name` is the driver's name unless `emit_package` is given one. The hash of `manifest.json` itself is the package's pin; no file in the package holds it. A package holds exactly one driver in this Feature. The layout is what is shaped for several: the list, the two folders and a file name for each driver.

**`mcp.json`** has exactly `schema` (`driver-mcp/2`), `server`, `tools`, `operations` and `client`. `tools` is what the renderer gives for the drivers. `operations` maps each tool name to its `driver` and `operation`. `client` is the entry an MCP client needs, with the two things only the operator knows left as the words `PACKAGE` and `BINDING`: `{"mcpServers": {"<server>": {"command": "generative-driver", "args": ["serve", "PACKAGE", "--binding", "BINDING"]}}}`.

**`README.md`** is rendered from the drivers and the evidence. It holds, in this order:

1. the device's name;
2. a table of the operations with each one's effect, its meaning, its parameters and outputs with their units, and its warnings and preconditions;
3. how to serve the package: the `serve` command line with the form of the binding for this channel, the flag each effect of this driver needs, and the optional install the connection needs (`generative-driver[hardware]` for a serial, USB, I2C or SPI channel, none for TCP);
4. what was tested: each operation as tested, failed or untested, the number of samples, and the kind of evidence;
5. what the evidence does not show: replay shows consistency, not physical correctness, and no accuracy or full coverage is implied.

## Behaviours

The package:

- **03-B1** — `emit_package` writes a package of data. Given a model and the probe record it wrote, the new folder holds exactly the five files of "The package" and nothing else: no Python file, no copy of the runtime, no `requirements.txt`, no `manifest.sha256`. The driver file is the model byte for byte and the evidence file is the probe record byte for byte. The tool's result has `package_dir`, `manifest_sha256` and `evidence_kind`, as today.
- **03-B2** — The package check accepts what `emit_package` wrote and refuses everything else, naming the cause. `emit_check`, `emit_test_live`, `device_open` and `serve` each refuse:
  - a manifest whose `schema` is `interface-package/1` or `interface-package/2`, with a reason that names the format found and `interface-package/3`, and a manifest that names no format;
  - a file whose bytes differ from its hash in the manifest, a listed file that is missing and a file the manifest does not list, each named; and a link in place of a file or folder;
  - a driver that is not a valid `interface-model/7` model, with the reason Feature 02 gives;
  - a manifest that lists no driver, and one that lists more than one, with a reason that says a package of several drivers is not built yet.

  The check reads data only. Nothing of the folder is imported or run, also when someone puts a `driver/__main__.py` there: the file makes the package refused, and a marker it would write stays unwritten.
- **03-B3** — The emit stage accepts only a package that says what the tool would say. `emit_check` passes a package only when `README.md` and `mcp.json` each equal what the tool renders from the package's drivers and evidence, and the probe record replays on the installed runtime. A `README.md` or `mcp.json` that was edited, with the manifest made again to match, is refused, and the file is named. With a `model_dir`, a package whose driver is not that model's bytes is refused, as today. The result has no `runtime_current` and no `replay_reason`. The emit stage of a generic run and of a run on a supplied device accept a package on `ok`, `integrity_ok` and a passed replay.
- **03-B4** — Every use of a package runs the installed runtime. A managed session, a generated tool call in fresh reuse and `emit_test_live` each execute an operation with the installed runtime over the package's data, in a child process that is started one way for all three. The child can import what the tool itself can import, an optional hardware package among them. What Feature 02 says a package applies (values, preconditions, warnings) holds unchanged. A managed session keeps a checked copy of the package, as today, so a package changed after `device_open` does not change the session.
- **03-B5** — Fresh reuse gets the generated tools and nothing else, in a generic run and on a supplied device. The tool `package_execute`, which ran a package's own command line, is gone: no stage assigns it and the gateway refuses it as unknown. No stage assigns `emit_test_live`, `interface_execute`, `interface_describe` or `emit_check` to fresh reuse. The worker's objective names the generated tools and the package's `README.md`. A generated tool call on a supplied device is recorded as before, under the name `package_execute` in `supplied/package-events.jsonl` with its `mcp` proof.
- **03-B6** — The final verdict judges fresh reuse by its generated tool calls. The check `fresh_reuse_live_operations` counts a call as a run of the frozen package when it is a generated tool call that the configurator dispatched for that package: its `mcp` proof names the package, the manifest, the assignment, the dispatch and the tool. The rule is otherwise today's:
  - it passes when every live operation succeeded and at least one ran a function of the package beyond its identity operation;
  - it fails when the driver failed on the device, or when no such call ran;
  - it is unsettled when a call was inconclusive or was dispatched without a recorded result, when the operator's grants permit no function, or when the configurator could open no connection to the device.

  Two reasons change their words, because no live test tool is part of the rule: `fresh reuse never ran a tool of the frozen package`, and `fresh reuse ran no function of the frozen package beyond its identity operation; identity alone is not evidence of function, nor is an operation that only waits or repeats identity's requests`. No other call counts as a run of the frozen package: in the check's `operations`, `emitted_package` is true for such a generated call alone.
- **03-B7** — Results, sessions and assignments name the model, not a contract file. A `driver-result/1` keeps `model_sha256`, the hash of the driver file, and has no `contract_sha256`. The replies of `device_open`, `device_status` and `device_describe`, the `device_mcp` entry of a reuse assignment and the `mcp` proof of a generated call have `manifest_sha256` and no `contract_sha256`. The proof's `catalog_sha256` is the hash of the tools as they were rendered for the assignment.

The MCP side:

- **03-B8** — The server and its tools are named after the device. For a model whose device is `SAMP1` with the operations `identify`, `measure` and `read`, `mcp.json` names the server `samp1` and the tools `samp1_identify`, `samp1_measure` and `samp1_read`; the reuse worker of a run is served the same tools by a server that announces itself as `samp1`; and `serve` announces the server `samp1`. The names follow the rule of "The package" for the cases named there.
- **03-B9** — One module renders the tools, with the same words on every path. For the operation `measure` that Spec 02 shows in its Behaviour B4, the tool `samp1_measure` reads

  ```text
  Read the temperature of the probe.
  ```

  its parameter `channel` reads `Meaning not documented.` and its output `temperature` reads

  ```text
  Temperature of the probe.
  Unit: Cel.
  ```

  - Where the model gives no description the text is `Meaning not documented.`, and no warning line repeats it. Every uncertainty the model states is one line, `Warning: <message>`. Every precondition is one line, `Precondition <id>: <description>`.
  - An output that is a number always gets a unit line: the unit, `not stated by the device` when an uncertainty of it has the code `unit_not_stated`, else `unknown`. A parameter, and an output that is text, gets a unit line only when the model states a unit.
  - The same text is in `mcp.json`, in what `serve` lists, in what a reuse worker is served, in `catalog` of `device_describe` and in the child descriptions of an application of the SDK.
  - A result's `warnings` are as Feature 02 left them, the `semantics_unknown` ones among them.

The command:

- **03-B10** — One command serves a package. `generative-driver serve PACKAGE --binding JSON` checks the package, opens a managed session through the service, starting the service when none runs, and serves the package's tools over MCP on its standard input and output. It writes the session's id to standard error. A tool call runs through that session and returns the `driver-result/1`. Reads need no flag. A call of a write, an actuate or a resetting operation is refused with a result that says which grant is missing, and nothing is sent to the device, unless the command was started with `--allow-effect write`, `--allow-effect actuate` or `--allow-effect reset`. The flag may be repeated, once for each effect. A call the client cancels is cancelled in the session, so a write still waiting is never sent. A call repeated on the same server with the same `_request_id` returns the saved result and sends nothing again.
- **03-B11** — `serve` closes its session when it ends.
  - When the client closes the command's standard input, `serve` waits for a call that is still running, then closes the session: it is `closed` and its binding is free for another session.
  - On macOS and Linux, when the command gets SIGINT or SIGTERM, it tries the close once and exits. On Windows only the closed standard input is claimed.
  - When the close is refused, because an invocation is still active or an effect is uncertain, `serve` writes the reason and the session's id to standard error, exits with a non-zero status and leaves the session as it is for the operator to resolve. It never repeats a call and never closes by force.
- **03-B12** — `serve` refuses before it serves. For a package the check refuses, a binding that is not JSON or does not fit the driver's channel, a binding another session or run holds, or an effect it does not know, it writes the reason to standard error and exits with a non-zero status. No transport is opened and no session is left behind.
- **03-B13** — There is one way to serve a package. `python -m generative_driver.device_mcp --package …` is refused as an unknown option. The same module still serves a reuse worker through `--run` and `--assignment`.
- **03-B14** — The SDK works on the new package. `ManagedDriver.open`, `describe`, `invoke`, `status` and `close` work on a package of format 3, `ManagedDriver.contract` is the driver contract view, and the shipped application `measurement_average` runs as before.

The documents:

- **03-B15** — The package documents itself. `README.md` of an emitted package holds what "The package" lists. For the stand-in model of the smoke it holds the device's name, the row of `measure` with its meaning and the unit line `not stated by the device`, the line `generative-driver serve PACKAGE --binding '{"host": "<host name or address>", "port": <int>}'`, the tested and the untested operations, and the kind of evidence. It names no `python -m driver` and no file the package does not hold.
- **03-B16** — The guides show what is built, and what they show can be run.
  - Every line of a code block in `README.md`, `docs/setup.md`, `docs/extending-drivers-and-mcp.md`, `docs/support.md` and `docs/codex.md` that runs the command is accepted by the command's own parser. The command is written there as `generative-driver …`, `uv run generative-driver …`, `python -m generative_driver …` or `python -m generative_driver.cli …`, with or without a path before `python`.
  - The listing of a package's files in `docs/extending-drivers-and-mcp.md` equals the files of a package `emit_package` writes.
  - None of these strings is in `README.md`, `CONTEXT.md`, `docs/setup.md`, `docs/support.md`, `docs/codex.md`, `docs/dashboard.md`, `docs/extending-drivers-and-mcp.md` or `docs/client-interface.md`: `python -m driver`, `REFERENCE.md`, `mcp-tools.json`, `contract.json`, `protocol.json`, `manifest.sha256`, `interface-package/1`, `interface-package/2`, `interface-model/3`, `interface-model/4`, `interface-model/5`, `interface-model/6`, `runtime_current`, `package_execute`, `device_mcp --package`, `register model` and `Model 1`. The decision records, `docs/features/`, `docs/research/` and `docs/agents/` are not searched.
  - `docs/design.md` keeps its dated sections as history. It gets a section dated 2026-11-19 that says what a package holds, how it runs and how it is served, and the section of 2026-10-28 says at its head that the new section replaces its packaging. Outside the dated sections the design describes no register model.
  - What a worker document shows can be used as it stands: the fragment in `resources/interpret_tasks/HANDLER.md` validates when its operation is put into a model. Feature 02 left it with an empty description, which the validator refuses.
  - No text a worker gets (a prompt, a skill, a tool description) names `REFERENCE.md`, a package's command line, `package_execute`, `runtime_current`, or a file a package no longer holds. The description of `emit_package`, `emit_check` and `emit_test_live` in the tools table says what each does now.
  - `README.md` and `docs/setup.md` show the install with `uv` first.
  - `docs/setup.md` says how an operator frees a session that a killed `serve` left open, where the session's id is found, and how the saved result of a call is read after a lost reply.
  - ADR 0010 no longer says "Not built yet". ADR 0002 and ADR 0008 each say, under their status, what ADR 0010 replaced.
- **03-B17** — The client interface says what a client now reads. `docs/client-interface.md` and `client_interface.VERSION` are version 5. The page says that the frozen driver is a folder of data in the format `interface-package/3`, names its five files and says that it holds no code. It says that the tool runs a frozen driver with the command `generative-driver serve`, started as a process, and that a managed session and the developer tools lie outside this interface, as it says of clearing an uncertain effect today. It says that every line of `supplied/package-events.jsonl` has `mcp`. No call, field or import of the interface changes.

## Interface and seams

The public interfaces are the command line (`generative-driver serve`, and the other commands for 03-B16), the tool facade (`generative_driver.toolkit.call_tool` and `list_tools`), MCP over standard input and output, the managed session (`generative_driver.sdk.ManagedDriver` and the `device_*` methods of the service), the configurator (its stages, gateway and records) and the runtime (`interface_runtime.package.verify_package`, `interface_runtime.catalog`).

- `interface_runtime.package` reads a package: it checks it and gives the manifest and each driver's model. `interface_runtime.catalog` renders the names, the tools and `mcp.json` from the drivers, and one module of the tool renders `README.md`. Every reader of a package goes through these, the configurator's own reads among them (`_frozen_package`, `_generated_reuse`, `worker_tool`, the scripted worker).
- `interface_runtime.driver.PackageSession` and `execute_package` stay the runtime's seam for one operation of the package's one driver. The child and the tests of a package's values, preconditions and warnings use them.
- The child of 03-B4 is a module of the installed runtime. The tool starts it with its own interpreter and gives it its own import path, the package's folder, the operation, the parameters, the binding and the grants; it prints one `driver-result/1`. It is no documented command. "Started one way" is shown by what can be observed: on each of the three paths a stand-in module on the import path of the process that starts the child is imported by the child.
- `DeviceAccess.invoke_package` keeps its name and stays the one place where a managed session and a generated call start the child. Tests that script the result of a generated call patch it there, as `tests/test_owner_generated_integration.py` does today; a call dispatched without a recorded result is shown that way.
- The command is a subcommand of `generative_driver.cli`, as the others are. It uses the service's `device_open`, `device_execute`, `device_cancel_invocation`, `device_status` and `device_close`; it adds no method to the service. It tells a close refused for an active invocation from one refused for an uncertain effect by `device_status`, not by the words of the refusal. Its texts use the terms of `CONTEXT.md`: an uncertain effect, which the operator resolves.
- `emit_check` replays the probe record in process on the installed runtime, once. The second replay in a child process existed to run the package's own code and goes.
- Tests of 03-B1 to 03-B4, 03-B9 and 03-B15 attach at `call_tool` and at the runtime, beside `tests/test_driver_package.py`, `tests/test_package.py` and `tests/test_driver_contract.py`. Tests of 03-B5 and 03-B7 attach at the controller and at a run on a supplied device, as `tests/test_supplied_runs.py`, `tests/test_owner_generated_integration.py` and `tests/test_device_sessions.py` do. Tests of 03-B6 are `FinalVerdictTests` and `RealToolVerdictTests` of `tests/test_configurator.py`: they script fresh reuse with generated tool calls on a package of format 3, where today they script `emit_test_live` on a package of format 1. Tests of 03-B8 and 03-B10 to 03-B13 start the command as an MCP child over standard input and output, as `tests/test_device_mcp.py` does, against a scripted device on a local socket. Tests of 03-B14 are those of `tests/test_application_mcp.py` and `tests/test_tool_authoring.py`. Tests of 03-B16 and 03-B17 read the documents and attach at the command's parser, at `call_tool` and at `tests/test_client_interface.py`.
- Tests of earlier Runs are already named `test_03_B1_…` and so on, so the kit's check by name cannot tell them from this Feature's. The implementer's report names each Behaviour's own tests, and the Reviewer checks them by those names.
- Tests use scripted workers and scripted devices. None starts a model run or touches a physical device.

**Build in parts.** The code is built in this order, because each part reads what the one before it writes: the package (03-B1 to 03-B5 and 03-B7), the MCP side (03-B8, 03-B9), the command (03-B10 to 03-B14), and last the verdict (03-B6). The verdict comes last because the dispatch-outcome Run changes the same functions and tests. The documents of 03-B16 and 03-B17 share no file with the code, except the tools table and the worker texts. They may be written by a second builder at the same time, from this Spec, and joined before the check.

**Places the dispatch-outcome Run also changes.** `Controller._dispatch` and the verdict's reuse check in `configurator.py`, `DeviceAccess.worker_tool` and the session's open and describe code in `device_access.py`, `docs/extending-drivers-and-mcp.md`, `docs/setup.md` and `CONTEXT.md`. This Feature changes there only what reads a package's files or names a removed thing, and the verdict's reuse check as 03-B6 says.

- The builder works on this branch as it stands and merges nothing. Joining the two Runs is no part of the Build. Whoever runs this Run merges the other Run's finished work in a commit of its own, which is no part of this Feature's diff: before the verdict part begins if the other Run is finished by then, and otherwise after this Feature.
- `_fresh_reuse` keeps one place that asks how a call ended (`outcome_of` today). `_live_outcome`, `_toolchain_fault`, `_host_fault`, `_before_contact`, `_declared_refusal` and `_answered` are not moved, renamed or rewritten: the other Run deletes them.
- If the other Run is merged before the tool `package_execute` is removed, the sentence that names that tool in `src/generative_driver/dispatch_outcome.py` and its rows in `tests/test_dispatch_outcome.py` go with it.

## Deleted

Files:

- `src/generative_driver/resources/toolchain/template/interface_driver/`, the entry module and command line a package carried, with its `describe`, `replay`, `execute` and `sequence` commands.
- `src/generative_driver/supplied/package_runner.py`, which ran a package's own command line.
- The second mode of `src/generative_driver/device_mcp.py`: `--package`, `--session` and the server name `generative-driver-device`.

Code:

- `_toolchain/interface.py`: the copies of the template and of the runtime in `emit_package`; `REFERENCE.md`, `requirements.txt` and `manifest.sha256`; `TEMPLATE`, `BOOT`, `_trusted_python`, `_runnable`, `_snapshot`, `_check_snapshot`, `DATA`, `DOCUMENTS`, `BUNDLED`, `REPLAYED_ELSEWHERE`; the child replay of `emit_check`; the checks of bundled Python in `_copy`; `binding_reference` and `coverage_reference`, whose content the README's renderer gives.
- `_toolchain/emit.py`: `subprocess.TimeoutExpired` among `HOST_FAULTS`, and the words on a replay process and a driver entry.
- `interface_runtime/driver.py`: the reads of `driver/model.json` and of the manifest's `contract_sha256`, and the words on package sequences.
- `interface_runtime/package.py`: the list of package formats, `inspect_package`, `runtime_current`, the checks of the contract, protocol and catalog files, and the rule that skips `__pycache__`.
- `interface_runtime/contracts.py`: `protocol_view`, which only wrote `driver/protocol.json`.
- `interface_runtime/catalog.py`: the renderer versions, `renderer_version` and the default namespace `device`.
- `configurator.py`: the tool `package_execute` (`PACKAGE_EXECUTE`) and its branches in the gateway; the `runtime_current` conditions of the emit gate and of `_emit_check_failure`; `contract_sha256` in `_generated_reuse`; the test for `interface-package/2` there, since every package now has generated tools; the four tools of the reuse stage in `STAGE_TOOLS`; in `_frozen_package` and `_fresh_reuse`, `driver_sha256`, `generated_mcp` and the branches that count `emit_test_live` or `interface_execute`.
- `supplied/stages.py`: `package_call`; `package_execute` in the reuse stage's tools and objective; the `runtime_current` condition of the emit gate.
- `device_access.py`, `device_recovery.py` and `sdk.py`: every read of `driver/contract.json`, `driver/protocol.json` and `mcp-tools.json`, the tests for `interface-package/2`, and `contract_sha256`; `'package_execute'` in `LIVE_TOOLS`, since no event carries that name any more.
- `supplied/__init__.py`: the words that name `package_runner`.

Tests:

- Tests of a package's own command line and of its bundled copy: in `tests/test_driver_package.py` the tests named `test_05_B1_…`, `test_05_B2_…` and `test_05_B3_…` that alter or compare the bundled runtime, and in `tests/test_package.py`, `tests/test_driver_contract.py`, `tests/test_toolkit.py`, `tests/test_emitted_package_calls.py` and `tests/test_supplied_stages.py` the tests that import or run a package's `driver` module. Where such a test also shows a Behaviour that stays, it keeps that part on the installed runtime.
- `tests/test_supplied_runs.py`: the tests of `PackageToolTests` that reach `package_execute` by patching `_generated_reuse`.
- `tests/test_configurator.py`: the helper `driver_package`, which writes a folder tagged `interface-package/1`, and in `FinalVerdictTests` and `RealToolVerdictTests` each case that shows only how a summary of `emit_test_live` or a run of the package's `driver` folder through `interface_execute` is read. The implementer's report names each case that went.
- `tests/test_configurator.py`, further: a test that reaches `emit_test_live` through the gateway is scripted with a generated call where it shows a rule that stays, and goes otherwise.
- `tests/test_device_mcp.py`: `test_caller_known_mcp_invocation_reconnects_without_another_write`, `test_generated_server_reconnects_to_the_managed_package_session` and `test_unconfigured_execution_is_a_structured_tool_error` go with the mode they test. The other tests of the file run on `serve`.
- `tests/test_driver_contract.py`: the test of `protocol_view`.
- Every other test that builds, reads or names a package in the earlier layout (`tests/test_supplied_stages.py`, `tests/test_emitted_package_calls.py`, `tests/test_tool_process.py`, `tests/test_owner_generated_integration.py`, `tests/test_smoke_contract.py`, `tests/test_service_smoke.py` among them) uses a package of format 3, or goes under the rule of the Intent.
- Tests that name a tool `device_…` name it after the device.

## Out of scope

- The driver format. Feature 02 built it, and this Feature changes no rule of it. A driver has no description of its device as a whole, so the README starts with the device's name.
- Runs that drive several controllers. The package can hold their drivers; no stage writes more than one.
- More evidence in a package than the one probe record of one operation that `emit_package` takes today.
- One connection for several calls. The package's `sequence` command gave it; no path of the tool does, today or after this Feature.
- Serving a session that is already open. The deleted mode did it with `--session`; `serve` opens its own session each time. After a lost reply from a server that is gone, the saved result is read through the SDK, and `docs/setup.md` says so.
- A command that lists or closes managed sessions. A `serve` that is killed leaves its session open, and the operator frees it through the SDK, as 03-B16 has `docs/setup.md` say.
- `emit_test_live` as a tool. It stays a developer tool and runs on the installed runtime (03-B4). No stage assigns it after this Feature, so what the gateway does for a worker that calls it has no caller: its branches in `Controller._dispatch` and in the helpers `_model_channel` and `operation_effects`, which read `driver/model.json` of a package. That code stays untouched, because the dispatch-outcome Run rewrites that function, and `noticed.md` records it for a clean-up after both Runs.
- The run folder, the acquire side and the benchmark's repository.
- The hosted checks, which install with `pip`.

## Deviations

- The package check is stricter than the Spec's list. The manifest holds exactly `schema`, `name`, `version`, `drivers` and `files`, and a driver entry exactly `name`, `model`, `evidence` and `evidence_kind`. The files are `drivers/<name>.json` and `evidence/<name>.probe.json`, the driver's name must be the one the naming rule gives, and an extra folder or an entry that is neither file nor folder is refused. — This is what "accepts what `emit_package` wrote and refuses everything else" means.
- `verify_package` also returns `manifest_sha256` and `drivers`, and a helper `the_driver` gives the one driver. — The seam says every reader gets the manifest and the model through `interface_runtime.package`.
- The check reads the driver's format tag before it compares any hash. — It keeps Feature 02's refusal by name on format 3.
- The rule that `README.md` and `mcp.json` equal what the tool renders, and the agreement of `evidence_kind`, live in `emit_check` and not in the shared check. `emit_test_live`, `device_open` and `serve` do not compare the rendered files. — 03-B3 puts that rule on `emit_check`; 03-B2 lists only hashes and inventory for the four users.
- `emit_check` still works on a checked copy in a temporary folder (`checked_copy`). — The Spec deletes the checks of bundled Python in `_copy`, not the copy, and the host-fault rule needs it.
- The refusals read `package names no format; this kit reads interface-package/3`, `package format is not one this kit knows: '<found>'; it reads interface-package/3`, `package manifest lists no driver`, `package manifest lists N drivers; a package of several drivers is not built yet`, `package may not contain links: <path>` and `package file inventory/hash mismatch; missing: …; unexpected: …; changed: …`. — Each names what the Spec asks.
- `emit_package` refuses a `name` or `version` the check refuses, before it reads the model or the probe and before it makes a folder. The defect's `where` is the argument and its `what` is `<argument> must be a text of 1 to <256 or 64> characters`. The rule is one new public function, `interface_runtime.package.text_defect`, which `verify_package` and `emit_package` both call. — 03-B2 says the check accepts what `emit_package` wrote, and one shared rule keeps the two from drifting apart.
- An absent or empty `name` still gives the package its driver's name. — Unchanged behaviour; the naming rule keeps that name to 48 characters, so the check always accepts it.
- The input schema of `emit_package` in `tools.json` still gives `name` and `version` as strings without a length. — 03-B16 asks only that each description say what the tool does now; the limits stay in the runtime, and the refusal names them.
- The child of 03-B4 is started as `[sys.executable, '-I', '-B', '-c', <boot>]` in a temporary working folder and reads one JSON request from standard input. — The Spec says what the child is given, not how.
- The child of a managed session no longer runs with `-S`, so it imports site packages. — 03-B4: the child can import what the tool itself can import.
- `device_open`, `device_execute` and `ManagedDriver` have words of their own for a package the check refuses, and a session checks its kept copy before each dispatch. — It serves 03-B4's rule that a package changed after `device_open` does not change the session.
- The result-shape checks of `supplied/package_runner.py` went with the file. — The Spec deletes the file, and nothing else called them.
- `catalog_sha256` is the SHA-256 of the rendered tools as JSON with sorted keys, not the hash of the file `mcp.json`. — The literal wording of 03-B7.
- The gateway's `tools` and `mcp_tools` replies for a reuse assignment gained `server`. — 03-B8 needs the reuse server to announce the driver's name.
- The objective of fresh reuse is the stage's objective plus "Operate the device only with the generated tools a, b, c. README.md in the package says what each operation means, what it takes and gives, and what was tested." — The Spec gives no wording; the objective names the tools and `README.md` as 03-B5 asks.
- `Controller._dispatch` lost its `package_execute` branches. — They are named in "Deleted"; the `emit_test_live` branches, `_model_channel` and `operation_effects` are untouched, as "Out of scope" asks.
- `AssistiveHandoffTests.test_generic_model_reaches_probe_and_emit_and_notes_travel_except_to_reuse` now hands the emit check a really emitted package. — Its old fixture used the earlier layout.
- An output that is neither a number nor text (bytes, hex, a flag) follows the rule of text for unit lines. — The Spec is silent, and only number outputs always get a unit line.
- "No warning line repeats it" drops only the marking the contract view adds for a missing description. Every uncertainty the model states stays one `Warning:` line. — The literal rule of 03-B9.
- A package's `README.md` has the headings `# <device name>`, `## Operations`, `## Serve`, `## Tested` and `## What the evidence does not show`. — The Spec lists the content and its order, not headings.
- `serve` lists the tools the session describes, those of its kept checked copy, not a second read of the folder. — One renderer, the same bytes.
- `serve` refuses an ungranted call itself, before the session, with a failed `driver-result/1` (`error.code` `effect`, `error.fault` `operator`) that names the flag. On a channel that resets when opened every call, a read too, needs `--allow-effect reset`. — 03-B10 names a resetting operation, and on such a channel every call opens the connection and resets the device.
- `serve` writes `generative-driver serve: session <id> is open`, and on a refused close `… stays open: an invocation is still active` or `… an effect is uncertain, which the operator resolves`. It exits 0 when the session is closed, 1 when it refused to serve or left its session, and 2 from the parser for an unknown effect. — 03-B12 asks only for a non-zero status and the reason on standard error.
- On closed standard input the MCP library cancels the calls in flight. A call still waiting in the session's queue is cancelled and never sent; a call already dispatched runs to its end and is waited for. — The call that is still running is waited for, and a queued call that is never sent errs toward safety.
- On a signal the handler ignores further signals, makes the one close attempt and ends the process with `os._exit`. — "Tries the close once and exits"; the library's read of standard input cannot be interrupted another way.
- An explicit wait for running calls after the server ended has no failing test of its own. — Harmless; it guards the bounded join of asyncio's worker threads.
- In `serve`, everything after the session opens runs inside the one `try`/`finally` that makes the close attempt: the line with the session id first, then the signal handlers, then the description. When the client is gone during a call, `serve` still waits for the call and tries the close once, then raises the error with status 1. — This is what 03-B11 and 03-B12 ask.
- When the service will not describe the session, describes another package or answers without tools, `serve` writes `generative-driver serve: the service did not describe the session: <reason>` and exits 1 after the close attempt, also when the close succeeds. — The Spec gives a non-zero status when the command refuses to serve, and it has not served.
- The test of that failure hosts the service in the test's own process and replaces its `device_access.describe` with a stand-in, while the command runs as the unmodified MCP child. One of its cases, an answer without tools, was written before its test; the builder took the rule out, watched the case fail and put it back. — The stand-in sits at the command's boundary, the service, the test checks what the command did, and the failing output is for the finding's reason.
- `python -m generative_driver.device_mcp --package …` is refused by the parser as an unrecognised argument, with status 2. — 03-B13.
- `serve` passes the client's `_request_id` through as the session's request id. — The saved-result rule of 03-B10.
- The words searched in worker texts are the builder's choice: `REFERENCE.md`, `package reference`, `python -m driver`, the package's own command line, `package_execute`, `runtime_current`, `mcp-tools.json`, `protocol.json`, `manifest.sha256`, `driver/model.json`, `driver/__main__` and `contract.json`. So are the three descriptions in the tools table. — The list covers what 03-B16 names.
- `_frozen_package` reads a package only through `verify_package`, `the_driver` and `render_mcp`, and gives one new fact, `tools` (tool name to operation). For a package the check refuses it gives None for tools, identity, effects and functions together. — The seam names `_frozen_package` among the readers.
- When read strictly, `_frozen_package` first reads every file, so a host's read error still raises. — It keeps the rule that a host failure reading the frozen package stops the run for the host.
- A generated call dispatched without a recorded result counts as a call that may have run a function only when its tool is a function of the frozen package and the dispatch recorded the frozen package's digest; the verdict is then unsettled. An unrecorded dispatch of the identity tool alone is "not equivalent", and in the check's `operations` such a dispatch has `emitted_package` false. — The rule already made the verdict unsettled only once something was exercised; a generated tool names its operation, so "identity alone is not evidence" decides it.
- A model failure of a call that is no run of the frozen package keeps its words. — The rule is unchanged.
- The docstrings of `_reset_ungranted`, `_ungranted` and `_self_evidence` say "the runtime" where they said "its runtime". — It follows from 03-B4; no code changed.
- `test_07_B8_a_run_with_a_binding_and_no_firmware_ends_with_an_emitted_package_and_a_self_evidence_verdict` reads the driver through `the_driver(verify_package(...))`. — `driver/model.json` no longer exists.
- The test helper `thermometer_package` builds a real package of format 3 with `probe_run` (a replay) and `emit_package`. — Required by "tests of 03-B6 … on a package of format 3".
- `test_the_configurator_reads_a_model_as_the_runtime_does` keeps a hand-made `driver/model.json` for `_model_channel` and `operation_effects`. — That gateway code stays untouched, as "Out of scope" asks.
- Two tests go beyond a one-to-one conversion: `test_03_B6_a_host_failure_before_the_child_starts_leaves_the_call_unrecorded_and_the_verdict_unscored`, and the case "before the call" of the test of a swapped package. — They show the rules of 03-B6 on the generated route.
- Three test edits came after the code, and their failing output is from copies of the base commit's verdict functions outside the worktree. — The failure is shown against the old rule.
- The guides' parser test wraps `ArgumentParser.parse_args` inside `cli.main` to stop after the parse, and it requires `serve` lines with `--binding` and `--allow-effect` in the guides. — It reaches the command's own parser, runs no command, and cannot pass on guides that omit the command.
- The fragment in `HANDLER.md` became a whole operation with steps, an output and a unit. — 03-B16 asks that it validate, and an operation without steps failed too.
- `docs/client-interface.md` says the interface names `call_tool` and nothing of which developer tools there are. — `call_tool` stays an import, and 03-B17 says no import changes.
- `mcp` moved into the list of keys every line of `supplied/package-events.jsonl` has, `test_08_B11_a_line_of_package_events_has_the_keys_it_names` changed with it, and `test_03_B1` of the client interface pins version 5. — 03-B17.
- `docs/setup.md` has uv first, a new section "Serve a package" and the values of `invocation_status`. — 03-B16; `pending`, `interrupted` and `unknown` match the code.
- `docs/extending-drivers-and-mcp.md` has two new sections, and its passages that described removed things were rewritten. — 03-B16 needs the listing and the command, and the rewrites remove deleted features.
- "Use `null` for an unknown unit" became "leave `unit` out". — What a guide shows can be run, and the validator refuses null.
- The guides say that `--package` is refused, without the literal string `device_mcp --package`. — 03-B16 forbids that string.
- `docs/design.md` has a pointer at its head, and its new section states the rule of 03-B6 and what it replaces. — It keeps the dated sections as history, as 03-B16 asks.
- The notes in ADR 0002 and ADR 0008 are paragraphs under the status line. — "Under their status".
- `README.md` has new wording and a short section "Use a driver"; pip stays after uv. — 03-B16: uv first, and a command that runs.
- `docs/support.md` has rows relabelled, and I2C and SPI added to the sentence that a read label is the author's claim. — The register-model check it named was deleted in Feature 01.
- "Managed session" is defined on first use in each guide; `CONTEXT.md` is unchanged. — It follows the rule of `STANDARDS.md` to define a term on first use.

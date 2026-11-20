# Extend a driver or build an application MCP

Use the public driver contract as the source of tool meanings, schemas and warnings. The declarative protocol supplies wire behavior; the shared runtime executes it. The configurator owns device bindings, effect grants, dispatch records and uncertain writes. [CONTEXT.md](../CONTEXT.md) defines these terms.

Choose one path:

| Need | Change | Completion criterion |
|---|---|---|
| Another device operation using supported exchanges/decoders | The interface model | Validated model, independently checked behavior, emitted package and generated MCP call |
| An exchange/decoder the runtime cannot express | Shared runtime capability | Typed bounded configuration, regression tests, schema/reference update and portable release |
| A workflow or calculation using existing operations | Application MCP using `generative_driver.sdk` | Registered tool, valid results and tests of child failure/cancellation |

Device-specific Python plugins, dynamic imports and expressions are outside the driver format. Python orchestration belongs in an application tool. A workflow requiring an uninterrupted device lease belongs in one public driver operation; several application calls do not form a transaction.

## Public operation

The [model document](../src/generative_driver/resources/docs/INTERFACE_MODEL.md) describes the interface model, `interface-model/7`: the one document that states a driver. Each operation holds its meaning beside its wire steps. The driver contract is the public part of that model and the declarative protocol its wire part. The tool computes both as views and stores neither as a file.

Start with a `model.json` that validates and independently checked probe evidence. Write each meaning into the model, beside the wire steps. A meaning is optional: where one is missing, the tool reads `Meaning not documented.` and each result carries a warning. A meaning is part of the model, so changing one changes the model's hash, and a probe record taken with the earlier bytes no longer belongs to it. Changing wire behavior requires new probe/ground evidence. Every run asks its interpret worker for the meanings, and `emit_package` copies the model into the package byte for byte.

Give every operation and field a concrete description. Record known units; leave `unit` out for an unknown unit. When the evidence states no unit, add a `unit_not_stated` uncertainty that says where the evidence was searched; the catalog then reads `Unit: not stated by the device.` rather than `Unit: unknown.` Add scoped uncertainties as `{"code":"meaning_unverified","message":"Readable caveat.","evidence_refs":["NOTES.md#evidence"]}`. A citation records provenance; it is not an independent verdict. Unknown meaning remains exposed with `Warning:` text in MCP discovery and scoped warnings in results.

Known preconditions belong in the operation, in the model. An operation takes a bounded list of equality rules over fresh, declared, unguarded read observations; all rules must hold. Example on a write operation:

```json
"preconditions": [{
  "id": "armed_required",
  "description": "Device must be armed before changing level.",
  "observation": {"operation": "read_armed", "parameters": {}, "output": "armed"},
  "operator": "eq", "expected": 1
}]
```

Checking this rule and executing the operation hold one managed lease. Guard observations retain their own operation/field caveats in both the observation and the outer result. A rejection reports the rule, expected/observed value and `requested_command_dispatched: false`. Observation communication is retained separately. No tool argument overrides a precondition; changing it means editing and revalidating the driver. A transport-close fault remains a structured host error with the completed transaction evidence.

Validate with the installed toolkit, supplying JSON argument files:

```text
python -m generative_driver.cli tool model_validate --arguments validate.json
python -m generative_driver.cli tool emit_package --arguments emit.json
python -m generative_driver.cli tool emit_check --arguments check.json
```

Use absolute paths in those files. `emit.json` supplies `run_dir`, `model_dir` and the successful `probe` path. `check.json` supplies `run_dir` and `package_dir`. Run live checks only on the operator-selected binding and grants. Replay establishes software consistency with recorded transactions, not independent physical truth.

## The package

`emit_package` writes a package: five files of data in the format `interface-package/3`, and no code.

```text
<package>/
  README.md                        the documentation
  manifest.json                    the format, the name, the version, the drivers and a hash of every other file
  mcp.json                         the MCP side: the tools, and an entry for an MCP client
  drivers/<driver>.json            the driver of one controller: the interface model, byte for byte
  evidence/<driver>.probe.json     the probe record that driver wrote
```

A driver is named after its device: the device's name in lower case, with every run of characters other than `a` to `z`, the digits and `_` turned into one `_`, and `_` trimmed from both ends. `SAMP1` gives `samp1` and `stand-in device` gives `stand_in_device`. A name that would be empty is `device`, one that would start with a digit gets `device_` in front, and one longer than 48 characters is cut to 48. A tool is `<driver>_<operation>`, such as `samp1_measure`, and the MCP server has the driver's name. A package holds one driver. The layout can hold one for each controller of a device; runs that drive several controllers are not built.

The tool renders `README.md` and `mcp.json` from the driver and the evidence. `README.md` holds the device's name, a table of the operations, how to serve the package, what was tested and what the evidence does not show. `mcp.json` holds the tools and the entry an MCP client needs, with the words `PACKAGE` and `BINDING` where the operator fills in the folder and the binding. `emit_check` passes a package only when both files equal what the tool renders and the probe record replays on the installed runtime. A file that was edited is refused by name, also when the manifest was made again to match. The hash of `manifest.json` is the package's pin.

## Serve a package

One command serves a package to an agent over MCP:

```text
generative-driver serve PACKAGE --binding '{"host": "127.0.0.1", "port": 12345}'
```

`PACKAGE` is the package's folder. The binding is JSON and must fit the driver's channel. The one above is an example; the package's `README.md` gives the form for its channel. A serial, USB, I2C or SPI channel needs the optional install `generative-driver[hardware]`, and a TCP channel needs none. Put `--home DIRECTORY` before `serve` for another state directory.

The command checks the package and opens a managed session through the configurator, starting the configurator when none runs. In a managed session the configurator holds a device for one package, outside a run: it claims the binding, keeps a checked copy of the package and records every call. The command then serves the package's tools on its standard input and output, and writes the session's id to standard error. An MCP client starts the command as a process, with the entry from `mcp.json`.

The server announces itself by the driver's name, such as `samp1`. Each tool has typed input and output schemas and shows the model's descriptions, units, warnings and preconditions. A tool call runs through the session and returns the `driver-result/1`. A call the client cancels is cancelled in the session, so a write that is still waiting is never sent.

Reads need no flag. A write, an actuate or a resetting operation is refused, with a result that names the missing grant and with nothing sent to the device, unless the command was started with that effect:

```text
generative-driver serve PACKAGE --binding '{"host": "127.0.0.1", "port": 12345}' --allow-effect write --allow-effect actuate
```

`--allow-effect` takes `write`, `actuate` or `reset`. Repeat it once for each effect.

The command refuses before it serves, for a package the check refuses, a binding that is not JSON or does not fit the channel, a binding another session or run holds, or an effect it does not know. It writes the reason to standard error, exits with a non-zero status and leaves no session behind.

The command closes its session when it ends. When the client closes the command's standard input, the command waits for a call that is still running and then closes the session, which frees the binding. On macOS and Linux, SIGINT or SIGTERM makes it try the close once and exit. On Windows the close is claimed only for a closed standard input. The close is refused while an invocation is still active or an effect is uncertain. The command then writes the reason and the session's id to standard error, exits with a non-zero status and leaves the session as it is for the operator to resolve. It never repeats a call and never closes by force. [Setup](setup.md#serve-a-package) says how the operator frees such a session, and one that a killed command left open.

This command is the one way to serve a package. The module `generative_driver.device_mcp` refuses a `--package` option as unknown; the configurator still starts that module to serve the reuse worker of a run.

## Managed use and generated MCP

An operator opens one session outside installed resources:

```python
from pathlib import Path
from generative_driver.sdk import ManagedDriver

device = ManagedDriver.open(
    Path("package").resolve(), home=Path("state").resolve(),
    binding={"host": "127.0.0.1", "port": 12345}, effects=("read",))
print(device.session_id)
print(device.invoke("measure", request_id="operator-reading-1"))
```

The endpoint above is illustrative; supply the actual binding. Opening validates and snapshots the accepted package and claims exclusive access. A channel declared `resets_on_open` additionally requires the explicit `reset` grant, including for read-only operations. That grant is all such a read needs: a reset is not a write, so a read over that channel leaves no uncertain effect. MCP arguments cannot select a binding or widen grants. Reconnect with `ManagedDriver(package, session_id, home=state)`. `describe()` inspects the contract/catalog; `status(request_id)` reads the saved result; `cancel()` prevents subsequent dispatch; `close()` releases a settled session. Closing refuses active or uncertain effects. Retain unresolved sessions for operator reconciliation. `generative-driver serve` does not serve a session that is already open: it opens its own each time.

An invocation ID denotes one dispatch, including its guard observations. Reusing the same ID and arguments reads its saved result; changing arguments under that ID is rejected. A lost response returns `invocation_outcome_unknown`; inspect `status(request_id)` before any further action. A write or an actuation leaves an uncertain effect when its requested command may have been sent and its execution did not succeed, and when its call ended without a result. A call that succeeded, or whose result shows the requested command was never sent, leaves none, also when it was cancelled while it ran. An uncertain effect remains across owner restarts; while it remains, reads go on and every write and actuation is refused. Never use a new ID as an automatic retry.

Generated and application tools reserve `_request_id` for invocation context; it is required for write/actuate tools and optional for reads. Choose it before calling, for example `{"level":37,"_request_id":"operator-change-1"}`. The Adapter removes it from device/handler parameters and echoes it in the result. After a lost MCP reply, repeat the call on the same server with that same ID: it returns the saved result and sends nothing again. When the server is gone, read the saved result through the SDK, as [setup](setup.md#serve-a-package) shows. Application contexts derive stable child IDs from the parent ID and call order.

If the owner restarts while an invocation is in flight, it blocks the session because an earlier child may still be running. A blocked session refuses every invocation, and `cancel()` leaves it blocked. The restart leaves the uncertain effect as it was: the session is uncertain only when it was before the restart. A blocked session with no uncertain effect keeps its binding until `close()`; after the close the binding can be opened again, and no statement is needed. A blocked session with an uncertain effect needs the operator's statement: stop prior execution independently and inspect the device before calling `device.reconcile(observation, effect_resolution="confirmed_safe")`. The observation requires a `source` and a `message` that are text and not blank, and `prior_execution_stopped: true`; [setup](setup.md#recovery) gives the statement field by field. Reconciliation records that attestation and preserves failed/unknown outcomes; it performs no device command, replays nothing and changes no precondition. It cannot resurrect a closed session.

## Application tool

Copy [the runnable averaging example](../src/generative_driver/examples/measurement_average.py). It imports only `Application`, `ManagedDriver` and `ToolSpec` from the supported `generative_driver.sdk` facade. Run it with the same session:

```text
python -m generative_driver.examples.measurement_average --package PACKAGE --home STATE --session SESSION_ID --operation measure --field temperature
```

Its `sensor_average` tool accepts `count` from 1 through 16. Three 21.5 degC samples return `{"mean":21.5,"count":3,"unit":"degC"}`. The result retains each child call, its dispatch/evidence references and caveats. An unknown source unit remains `null`; averaging makes no calibration claim.

For another application:

1. Construct `Application(name, drivers={"role": existing_managed_client})`.
2. Declare a `ToolSpec` with a unique ASCII name, description, `parameters`, `outputs`, `effect`, `uncertainties` and `dependencies={"role":["operation"]}`. Parameter/output definitions use the contract's `schema`, `description`, `unit`, `uncertainties` shape. Tool schemas are inline JSON Schema 2020-12; references are rejected. Driver values use the narrower format-reference subset.
3. Register `application.register(spec, handler)`. The synchronous handler receives `(context, arguments)` and returns its output object. Use `context.invoke(role, operation, parameters)` for every device call. Do not open transports or configure permissions inside a handler.
4. Use `context.check_cancelled()` between lengthy calculations. `context.invoke` checks cancellation before dispatch and stops automatically on a failed child. Keep partial results explicitly if designing alternative semantics; do not turn a child failure into success silently.
5. Exercise `application.invoke(name, arguments, cancel=threading.Event())`, then `application.serve()` through an actual MCP client. Generated and application tools share the same serving/validation Adapter.

Registration refuses an effect declaration weaker than a declared dependency. The owner still enforces actual grants. Errors retain the original child's `error`, including precondition ID/description/expected/observed values, and the complete partial `calls` trace. Cancellation reaches the current owner invocation as well as preventing the next call. A queued call receives a durable cancellation and cannot dispatch when the lease becomes free; other session intents remain available. Once dispatched, cancellation waits for the original recorded outcome and does not undo the command. `ManagedDriver.invoke(..., cancel=threading.Event())` uses the same rule. An application's MCP disconnection does not own or close the device session.

## Shared runtime capability

Add reusable behavior in `src/interface_runtime`, never a per-device executable file. The one driver format, `interface-model/7`, names every step and decoder the engine knows; step/decoder IDs are validated allowlists. See the [model document](../src/generative_driver/resources/docs/INTERFACE_MODEL.md) for supported fields and bounds.

Add a behavior test that the current public CLI/package path fails. Implement typed configuration validation, execution bounds and an independent transaction fixture in the shared runtime. Update the model document and the validator; reject unknown capabilities before I/O. Add optional dependencies at the point of use, exercise replay, then qualify native macOS and Windows separately.

## Compatibility

The tool runs its installed runtime over a package's data in every use: a managed session, a generated MCP call, `emit_test_live` and the `emit_check` replay. A package holds no code, and nothing in its folder is imported or run. Every file must match the manifest and any pin. A package has no command line of its own, and every call opens the connection for that call. Using a driver on another machine needs the tool installed there.

There is one package format, `interface-package/3`, and one driver format, `interface-model/7`. The tool refuses a package or a model of any other format and names the format it found. A driver emitted in an earlier format is made again by a new run. [ADR 0010](adr/0010-a-package-is-data-in-one-format.md) records the decision.

## Conformance evidence

Test literal outputs against an independent fixture, not the implementation's own prediction. Required cases are valid arguments/results, invalid arguments/results, missing grants, false/unavailable preconditions, scoped caveats, child failure after partial progress, cancellation, package mutation, duplicate/lost replies and owner recovery. Run actual stdio discovery/calls in an installed environment outside the checkout. The repository tests `test_driver_contract.py`, `test_device_sessions.py`, `test_device_mcp.py`, `test_application_mcp.py` and `test_tool_authoring.py` demonstrate these seams.

Label scripted fixtures, replay, native reference calibration, actual model runs and physical evidence separately. Package hashes establish integrity and provenance, not a filesystem or network sandbox.

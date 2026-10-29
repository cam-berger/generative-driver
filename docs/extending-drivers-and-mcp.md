# Extend a driver or build an application MCP

Use the public driver contract as the source of tool meanings, schemas and warnings. The declarative protocol supplies wire behavior; the shared runtime executes it. The configurator owns device bindings, effect grants, dispatch records and uncertain writes. [CONTEXT.md](../CONTEXT.md) defines these terms.

Choose one path:

| Need | Change | Completion criterion |
|---|---|---|
| Another device operation using supported exchanges/decoders | Contract and protocol data | Validated pair, independently checked behavior, emitted package and generated MCP call |
| An exchange/decoder the runtime cannot express | Versioned shared runtime capability | Typed bounded configuration, regression tests, schema/reference update and portable release |
| A workflow or calculation using existing operations | Application MCP using `generative_driver.sdk` | Registered tool, valid results and tests of child failure/cancellation |

Device-specific Python plugins, dynamic imports and expressions are outside the driver format. Python orchestration belongs in an application tool. A workflow requiring an uninterrupted device lease belongs in one public driver operation; several application calls do not form a transaction.

## Public operation

The [format reference](../src/generative_driver/resources/docs/DRIVER_CONTRACT.md) describes `driver-contract/1`, `driver-protocol/1` and `interface-package/2`. Public parameters, effects and units have one authority in the contract. The protocol references their names and supplies the steps and decoders. `driver/model.json` is a checked executable projection.

Start with a supported executable `model.json` and independently checked probe evidence. `emit_package` derives a conservative contract/protocol pair. Missing meanings receive explicit operation, parameter or output warnings. To provide supported meanings, author `contract.json` and `protocol.json` beside `model.json` before emission. Supply both authored files together; a lone document is rejected. Their compiled projection must match the probed model. Changing wire behavior requires new probe/ground evidence.

Give every operation and field a concrete description. Record known units; use `null` for an unknown unit. Add scoped uncertainties as `{"code":"meaning_unverified","message":"Readable caveat.","evidence_refs":["NOTES.md#evidence"]}`. A citation records provenance; it is not an independent verdict. Unknown meaning remains exposed with `Warning:` text in MCP discovery and scoped warnings in results.

Known preconditions belong in the contract. Version 1 currently supports a bounded list of equality rules over fresh, declared, unguarded read observations; all rules must hold. Example on a write operation:

```json
"preconditions": [{
  "id": "armed_required",
  "description": "Device must be armed before changing level.",
  "observation": {"operation": "read_armed", "parameters": {}, "output": "armed"},
  "operator": "eq", "expected": 1
}]
```

Checking this rule and executing the operation hold one managed lease. Guard observations retain their own operation/field caveats in both the observation and the outer result. A rejection reports the rule, expected/observed value and `requested_command_dispatched: false`. Observation communication is retained separately. No tool argument overrides a precondition; changing it means editing and revalidating the driver. Direct package `session()` and `sequence()` calls use the same contract checks over one connection; they cannot bypass a guard. A transport-close fault remains a structured host error with the completed transaction evidence.

Validate with the installed toolkit, supplying JSON argument files:

```text
python -m generative_driver.cli tool model_validate --arguments validate.json
python -m generative_driver.cli tool emit_package --arguments emit.json
python -m generative_driver.cli tool emit_check --arguments check.json
```

Use absolute paths in those files. `emit.json` supplies `run_dir`, `model_dir` and the successful `probe` path. `check.json` supplies `run_dir` and `package_dir`. Run live checks only on the operator-selected binding and grants. Replay establishes software consistency with recorded transactions, not independent physical truth.

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

The endpoint above is illustrative; supply the actual binding. Opening validates and snapshots the accepted package and claims exclusive access. A channel declared `resets_on_open` additionally requires the explicit `reset` grant, including for read-only operations. Such reads are tracked as effectful by the owner because opening can reset the device; an unacknowledged opening blocks another resetting read and session release until reconciliation. MCP arguments cannot select a binding or widen grants. Reconnect with `ManagedDriver(package, session_id, home=state)`. `describe()` inspects the contract/catalog; `status(request_id)` reads the saved result; `cancel()` prevents subsequent dispatch; `close()` releases a settled session. Closing refuses active or uncertain effects. Retain unresolved sessions for operator reconciliation.

Serve the frozen generated catalog:

```text
python -m generative_driver.device_mcp --package PACKAGE --home STATE --session SESSION_ID
```

Connect through an MCP client over stdio. Stdout is the protocol stream. Each public operation has a stable `device_` tool name, typed input/output schemas and the contract's explanations. Discovery without `--session` is supported; execution then returns `managed_session_required`. Live use needs the installed core and its MCP dependency. Offline package inspection/replay uses the bundled standard-library runtime.

An invocation ID denotes one dispatch, including its guard observations. Reusing the same ID and arguments reads its saved result; changing arguments under that ID is rejected. A lost response returns `invocation_outcome_unknown`; inspect `status(request_id)` before any further action. An interrupted or unacknowledged write remains uncertain across owner restarts and blocks another effectful operation. Never use a new ID as an automatic retry.

Generated and application tools reserve `_request_id` for invocation context; it is required for write/actuate tools and optional for reads. Choose it before calling, for example `{"level":37,"_request_id":"operator-change-1"}`. The Adapter removes it from device/handler parameters and echoes it in the result. After a lost MCP reply, reconnect and use that same ID to retrieve the saved invocation. Application contexts derive stable child IDs from the parent ID and call order.

If the owner restarts with an unfinished invocation, it blocks the session because an earlier child may still be running. An operator must independently stop prior execution and inspect the device before calling `device.reconcile(observation, effect_resolution="confirmed_safe")`. The observation requires `source`, `message` and `prior_execution_stopped: true`. Reconciliation records that attestation and preserves failed/unknown outcomes; it performs no device command, replays nothing and changes no precondition. It cannot resurrect a closed session.

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

Registration refuses an effect declaration weaker than a declared dependency. The owner still enforces actual grants. Errors retain the original child's `error`, including precondition ID/description/expected/observed values, and the complete partial `calls` trace. Cancellation reaches the current owner invocation as well as preventing the next call. A queued call receives a durable cancellation and cannot dispatch when the lease becomes free; other session intents remain available. Once dispatched, cancellation waits for the original recorded outcome and does not undo the command. `ManagedDriver.invoke(..., cancel=threading.Event())` uses the same rule. MCP disconnection does not own or close the device session.

## Shared runtime capability

Add reusable behavior in `src/interface_runtime`, never a per-device executable file. The explicit `driver-protocol/1.runtime_schema` identifies a versioned engine capability vocabulary (`interface-model/3` through `/6` today); step/decoder IDs are validated allowlists. See the packaged interface-model references for supported fields and bounds.

Add a behavior test that the current public CLI/package path fails. Implement typed configuration validation, execution bounds and an independent transaction fixture in the shared runtime. Update its versioned schema reference and capability vocabulary; reject unknown capabilities before I/O. Add optional dependencies at the point of use, exercise relocation/stdlib replay, then qualify native macOS and Windows separately. Existing schema versions and archived trial meanings must remain intact.

## Conformance evidence

Test literal outputs against an independent fixture, not the implementation's own prediction. Required cases are valid arguments/results, invalid arguments/results, missing grants, false/unavailable preconditions, scoped caveats, child failure after partial progress, cancellation, package mutation, duplicate/lost replies and owner recovery. Run actual stdio discovery/calls in an installed environment outside the checkout. The repository tests `test_driver_contract.py`, `test_device_sessions.py`, `test_device_mcp.py`, `test_application_mcp.py` and `test_tool_authoring.py` demonstrate these seams.

Label scripted fixtures, replay, native reference calibration, actual model runs and physical evidence separately. New contract/MCP benchmarks must exercise generated tools before independent frozen final grading; a generic executor call alone is insufficient. Passwords and plaintext answers remain evaluator-owned. Package hashes establish integrity and provenance, not a filesystem or network sandbox.

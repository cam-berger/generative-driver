# Installation and runtime setup

[Install first](../README.md). Below, `python` means your installation’s executable: `.venv/bin/python` on macOS or `.venv\Scripts\python.exe` on Windows. Quote paths containing spaces. No activation is required. With uv, use `uv run` in place of `python` from the checkout, for example `uv run python -m generative_driver doctor`; clients that need an absolute interpreter path take the same `.venv` executable.

For an older Windows checkout created before `.gitattributes`, use a fresh clone: converted line endings change bytes that the tool hashes.

## Configure Codex

Install and authenticate the Codex CLI. [Verify the OpenAI CLI](codex.md#verify-the-openai-cli) before selecting a command named `codex`.

```sh
python -m generative_driver configure --executor codex --command "/absolute/path/to/codex" --model YOUR_MODEL
```

Model runs default to the configured Codex executor. Use the installed `.exe` path on Windows. Pass wrapper arguments with repeated `--argument` flags. Credentials stay with Codex. Select the model explicitly and optionally set `--reasoning-effort`.

Codex workers inherit standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` settings, including lowercase forms, so provider connections work through the operator's proxy.

State defaults to `~/Library/Application Support/GenerativeDriver` on macOS and `%LOCALAPPDATA%\GenerativeDriver` on Windows. Use a leading `--home DIRECTORY` or `GENERATIVE_DRIVER_HOME` for another location. Configure all clients against the same location.

## Start the configurator

On Windows, open a standalone PowerShell window separately from Codex and run these commands.

```powershell
python -m generative_driver service start
python -m generative_driver service status
```

Windows clients require this independent owner because closing a client can terminate its child processes. macOS clients can start it automatically. Generated integrations pin the state directory; manual MCP connections must set matching `GENERATIVE_DRIVER_HOME`.

Restart after upgrades or installation edits too: `service status` reports `"compatible": false` when client/owner code differs; run `service stop`, then `service start`. Restart after reboot or sign-out. Stop explicitly with `python -m generative_driver service stop`; closing a chat does not stop it. Keep the computer awake during runs.

## Add optional hardware tools

From the checkout, with uv, name every extra you want in one command, because `uv sync` removes extras it is not given:

```sh
uv sync --extra dev --extra hardware --extra docs
uv run generative-driver doctor
```

Without uv:

```sh
python -m pip install -e ".[hardware,docs]"
python -m generative_driver doctor
```

USB drivers, permissions, Ghidra and Java are separate installations. Follow vendor instructions and check [supported paths](support.md). Optional analysis tools may run in [Podman](https://podman.io/) containers, which `doctor` reports; native core operation does not require containers or provide container USB forwarding.

Supply the objective, exact serial/USB/FTDI binding and allowed effects. Reads are the default; discovery does not authorize a device. For approved noninteractive tools, set `scoped_tool_approval` to `"bound-device"`. The configurator enforces assigned tools, binding and effects without changing global Codex settings. Resume with `--approve-bound-device-tools` when authorization is already established. The value `"emulator"` and `--approve-emulator-tools` apply only to a run on a supplied device, which a client of the [client interface](client-interface.md) starts.

UART can bind by USB identity from doctor’s `serial_ports`: `{"vendor_id":4292,"product_id":60000,"serial_number":"..."}`. Resolve it each run; missing/ambiguous matches are operator faults. Grants are `read`, `write`, `actuate`, `reset`. Acquisition listens for 500 ms; unsolicited output marks reset-on-open. Such a binding needs `reset` before connecting; ESP flash resets only with that grant.

Firmware runs accept optional absolute `ghidra_home`/`java_home` in run JSON or `driver_start`. The interpreter gets the sealed analysis kit and `ANALYSIS_TOOLS.json`; workers infer architecture/mapping. Missing optional analysis is reported.

## Read STM32 firmware through a debug probe

A run can read the flash of an STM32 through an ST-LINK debug probe. The read starts `STM32_Programmer_CLI`, the command-line program of STM32CubeProgrammer, which is a separate installation from ST. Save its command once:

```sh
python -m generative_driver configure-program --program stm32-programmer --command "/absolute/path/to/STM32_Programmer_CLI"
```

Use the `.exe` path on Windows. A path that is not absolute is refused, and so is a `.bat` or `.cmd` file. Pass arguments that stand before the tool's own with repeated `--argument` flags. `doctor` reports the program under `dependencies.stm32_programmer` and never starts it. A run keeps the command it started with.

Declare the probe in the run's binding, alone or beside the device's serial, USB or FTDI binding:

```json
{"binding": {"debug_probe": {"kind": "stlink", "serial_number": "..."}}}
```

Give `serial_number` when more than one probe is attached; otherwise leave it out. The acquire worker then has `acquire_stm32_flash`. It reads from `0x08000000` the flash size the program reports and hands the image to interpretation. It only reads: it never writes or erases.

- Without the `reset` grant the tool neither resets nor halts the chip. With it the chip is reset to connect and again after the read.
- A read that the configurator stops under the `reset` grant (a cancel, a shutdown, a spent budget or the limit of a tool call) can leave the chip halted until it is reset.
- One run with a declared probe runs at a time. A second is refused until the first has ended.
- A read-protected chip is not read. The run goes on without an image.
- On many STM32 families, attaching a probe to a read-protected chip stops its application until the next power cycle. This has not been checked on a chip.
- A run on a supplied device takes no probe.

## Recovery

Save the run ID. Reconnect with `driver_status` or `driver_result`; provide independent observations with `driver_respond`. Cancel explicitly. Resume after resolving the reported cause and checking uncertain physical effects.

An uncertain effect is a write or an actuation that may have been sent to the device and whose outcome the configurator does not know. While one remains, reads go on and every call that would change the device is refused. Stop the earlier execution yourself, check the device, then resolve the effect with one statement:

| Field | Value |
|---|---|
| `effect_resolution` | `confirmed_safe` |
| `observation` | An object with the three fields below. |
| `source` | Who or what observed the device: text that is not blank. |
| `message` | What was observed: text that is not blank. |
| `prior_execution_stopped` | `true`: the earlier execution has stopped. |

The command line and `driver_respond` send the observation alone, so put `effect_resolution` inside it. An `observation.json` for `respond`:

```json
{"effect_resolution": "confirmed_safe", "source": "operator at the bench", "message": "The earlier worker has stopped and the output reads off.", "prior_execution_stopped": true}
```

```sh
python -m generative_driver respond RUN_ID --observation observation.json
```

A statement that lacks a field is refused and changes nothing. A `respond` without `effect_resolution` is an operator message and resolves nothing. A managed session takes the same statement through `device.reconcile(observation, effect_resolution="confirmed_safe")`, as the [extension guide](extending-drivers-and-mcp.md#managed-use-and-generated-mcp) shows.

After a restart of the configurator, a run that was queued or running is blocked until you resume it. It is uncertain only when it holds a pending effect: the stored mark of a write or an actuation whose outcome is not known. A run granted `write` or `actuate` with no pending write or actuation resumes without a statement.

Budgets count wall time from the original start, including stopped time. With explicit approval, extend the total:

```sh
python -m generative_driver resume RUN_ID --budget-seconds 21600 --budget-reason "User-approved continuation after pause"
```

This sets six hours total, not six additional hours. The maximum is seven days; repeating a total adds nothing. MCP uses `budget_seconds` and `budget_reason`. Extensions retain start time, attempts and evidence and record a `run.budget_extended` event.

## Serve a package

A run emits a package: five files of data that the installed tool runs. One command serves a package's tools to an agent over MCP:

```sh
generative-driver serve PACKAGE --binding '{"host": "127.0.0.1", "port": 12345}'
```

The binding above is an example. The package's `README.md` gives the form of the binding for its channel, and its `mcp.json` holds the entry for an MCP client. Reads need no flag. Add `--allow-effect write`, `--allow-effect actuate` or `--allow-effect reset` for each other effect the agent may use. From a checkout, write `uv run generative-driver` or `python -m generative_driver` in place of `generative-driver`. The [extension guide](extending-drivers-and-mcp.md#serve-a-package) describes the command.

The command opens a managed session, in which the configurator holds the device for this package, and writes the session's id to standard error. It closes the session when it ends. In two cases the session stays open and keeps the binding:

- The command was killed, so it never tried the close.
- The close was refused, because a call was still active or an effect is uncertain. The command then writes the reason and the session's id to standard error and exits with a non-zero status.

The session's id is in the command's standard error: at the start, and again with the reason when the close is refused. Where that output goes is up to the MCP client that started the command. Free the session through the SDK, with the package's folder and that id:

```python
from generative_driver.sdk import ManagedDriver

session = ManagedDriver("PACKAGE", "SESSION_ID")
print(session.status("REQUEST_ID"))
print(session.close())
```

Pass `home="DIRECTORY"` to `ManagedDriver` when the command ran with another state directory. The configurator must be running; start it with `service start` when it is not.

`status` with the `_request_id` of a call gives that call's saved result as `result`. After a lost reply, read the result this way and send nothing again. When `result` is null, `invocation_status` says why: `pending` while the call still runs, `interrupted` when it never finished, `unknown` when the session never got that id.

`close` frees the binding. It is refused while a call is still active or an effect is uncertain. The operator then checks the device and resolves the uncertain effect, as the [extension guide](extending-drivers-and-mcp.md#managed-use-and-generated-mcp) says, and closes again. Nothing here repeats a call.

## Developer command line

| Action | Command after `python -m generative_driver` |
|---|---|
| List tools | `tools` |
| Direct tool call | `tool NAME --arguments arguments.json` |
| Start managed run | `run --spec run.json` |
| Inspect | `status RUN_ID`, `events RUN_ID --after CURSOR`, `result RUN_ID` |
| Control | `cancel RUN_ID`, `resume RUN_ID` |
| Supply observation | `respond RUN_ID --observation observation.json` |

The run JSON contains `goal`, `executor`, `inputs`, `binding`, `effects` and `budget_seconds`; a stable `request_id` makes retries idempotent. Put `--home DIRECTORY` before the command. Direct tool calls bypass managed ownership and stage progression.

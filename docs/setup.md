# Installation and runtime setup

[Install first](../README.md). Below, `python` means your installation’s executable: `.venv/bin/python` on macOS or `.venv\Scripts\python.exe` on Windows. Quote paths containing spaces. No activation is required.

For an older Windows checkout created before `.gitattributes`, use a fresh clone: converted line endings can invalidate benchmark hashes.

## Select an agent runtime

Install and authenticate Codex or Goose separately. [Verify the OpenAI CLI](codex.md#verify-the-openai-cli) before selecting a command named `codex`.

```sh
python -m generative_driver configure --executor codex --command "/absolute/path/to/codex" --model YOUR_MODEL
python -m generative_driver configure --executor goose --command "/absolute/path/to/goose" --model YOUR_MODEL --provider YOUR_PROVIDER
```

Only configured runtimes may launch. Without an executor selection, use the single configured runtime, or Codex if both exist. Use the installed `.exe` path on Windows. Wrapper arguments use repeated `--argument`; commands are stored as argument arrays. Credentials stay with the runtime. Select the model explicitly; Codex also accepts supported `--reasoning-effort` values. Goose reasoning settings stay in its provider setup.

Configured runtimes inherit standard `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` settings, including lowercase forms, so provider connections work through the operator's proxy. Other environment filtering still applies.

State defaults to `~/Library/Application Support/GenerativeDriver` on macOS and `%LOCALAPPDATA%\GenerativeDriver` on Windows. Use a leading `--home DIRECTORY` or `GENERATIVE_DRIVER_HOME` for another location. Configure all clients against the same location.

## Start the configurator

On Windows, run this in a standalone PowerShell window opened separately from Codex or Goose:

```powershell
python -m generative_driver service start
python -m generative_driver service status
```

Windows clients require this independent owner because closing a client can terminate its child processes. macOS clients can start it automatically. Generated integrations pin the state directory; manual MCP connections must set matching `GENERATIVE_DRIVER_HOME`.

Restart after upgrades or installation edits too: `service status` reports `"compatible": false` when client/owner code differs; run `service stop`, then `service start`. Restart after reboot or sign-out. Stop explicitly with `python -m generative_driver service stop`; closing a chat does not stop it. Keep the computer awake during runs.

## Add optional hardware tools

From the checkout:

```sh
python -m pip install -e ".[hardware,docs]"
python -m generative_driver doctor
```

USB drivers, permissions, Ghidra, Java and Renode are separate installations. Follow vendor instructions and check [supported paths](support.md). Containers may host optional tools; native core operation does not require Docker or provide container USB forwarding.

Supply the objective, exact serial/USB/FTDI binding and allowed effects. Reads are the default; discovery does not authorize a device. For approved noninteractive tools, set `scoped_tool_approval` to `"bound-device"` or `"emulator"`. The configurator enforces assigned tools, binding and effects without changing global Codex settings. Resume with `--approve-bound-device-tools` or `--approve-emulator-tools` when authorization is already established.

UART can bind by USB identity from doctor’s `serial_ports`: `{"vendor_id":4292,"product_id":60000,"serial_number":"..."}`. Resolve it each run; missing/ambiguous matches are operator faults. Grants are `read`, `write`, `actuate`, `reset`. Acquisition listens for 500 ms; unsolicited output marks reset-on-open. Such a binding needs `reset` before connecting; ESP flash resets only with that grant.

Firmware runs accept optional absolute `ghidra_home`/`java_home` in run JSON or `driver_start`. The interpreter gets the sealed analysis kit and `ANALYSIS_TOOLS.json`; workers infer architecture/mapping. Missing optional analysis is reported.

## Recovery

Save the run ID. Reconnect with `driver_status` or `driver_result`; provide independent observations with `driver_respond`. Cancel explicitly. Resume after resolving the reported cause and checking uncertain physical effects.

Budgets count wall time from the original start, including stopped time. With explicit approval, extend the total:

```sh
python -m generative_driver resume RUN_ID --budget-seconds 21600 --budget-reason "User-approved continuation after pause"
```

This sets six hours total, not six additional hours. The maximum is seven days; repeating a total adds nothing. MCP uses `budget_seconds` and `budget_reason`. Extensions retain start time, attempts and evidence and record a `run.budget_extended` event.

A detached bench harness drives suites; resume relaunches it and retains the current child. Suites use the same owner. Save `suite_id` and use suite status/events/result/cancel/resume. Suite and child budgets are separate; extend each explicitly. Keep CLI `--home` and MCP `GENERATIVE_DRIVER_HOME` consistent; conflicting `options.home` is refused. See [suite commands](../bench/README.md#run-a-repeated-suite).

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

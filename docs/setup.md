# Installation and runtime setup

Install the base package using the [README](../README.md). Use the same environment's Python in every command below (`.venv/bin/python` on macOS, `.venv\Scripts\python.exe` on Windows). Paths containing spaces remain one quoted argument. No shell activation is required.

## Select an agent runtime

Install and authenticate Codex or Goose through its own supported setup. Then record its executable and model:

```sh
python -m generative_driver configure --executor codex --command "/absolute/path/to/codex" --model YOUR_MODEL
python -m generative_driver configure --executor goose --command "/absolute/path/to/goose" --model YOUR_MODEL --provider YOUR_PROVIDER
```

On Windows supply the installed `.exe` path. A runtime command is stored as an argument array, not a shell command. Additional wrapper arguments use repeated `--argument` options. Configuration contains runtime selection; authentication remains with the runtime. Configure the model explicitly so comparisons record the intended selection. Codex also accepts `--reasoning-effort` using a value supported by the selected model; Goose reasoning controls remain in its provider setup.

Run state defaults to the native user data directory: `~/Library/Application Support/GenerativeDriver` on macOS and `%LOCALAPPDATA%\GenerativeDriver` on Windows. Set `GENERATIVE_DRIVER_HOME` or the CLI's leading `--home` option for a separate installation. Configure and connect both interfaces to the same directory to share runs.

## Start the configurator

On Windows, open a standalone PowerShell window separately from Goose or Codex and start the owner before connecting either interface:

```powershell
python -m generative_driver service start
python -m generative_driver service status
```

Use your installation environment's Python executable. `start` returns the background owner's process ID; `status` inspects it without starting another process. If you selected a custom state directory, use the same leading `--home DIRECTORY` for `configure`, `service start` and `setup`. Generated plugins and recipes pin that directory in their MCP environment. For a manually registered MCP connection, set `GENERATIVE_DRIVER_HOME` in its server environment to that same directory.

Windows MCP hosts can terminate their entire child-process tree when a session closes. Windows MCP connections therefore require an existing independent owner and return setup instructions if it is absent. They do not create a background owner inside that process tree. macOS clients can start the owner automatically; these explicit commands work there too.

Start the service again after a reboot or sign-out. After work finishes, stop it explicitly with `python -m generative_driver service stop`. Closing a Goose/Codex conversation does not send that shutdown command.

## Add optional hardware tools

```sh
python -m pip install -e ".[hardware,docs]"
python -m generative_driver doctor
```

Optional Python packages support serial ports, USB, FTDI bridges, ESP acquisition and document extraction. Native USB drivers, device permissions, Ghidra, Java and Renode remain separate installations. Consult the selected backend's vendor documentation before changing a USB driver. Hardware support depends on the device, adapter and firmware; see [support](support.md).

Supply a concrete operator binding and effect grants with the objective. Use an explicit serial endpoint, USB identity or FTDI adapter URL; automatic discovery alone does not establish which device is authorized. Read access is the default. Generated packages preserve operation effects and validate their declared interface before execution.

For noninteractive Codex workers, explicitly approve the assigned runtime tools for that run. A device run uses `scoped_tool_approval: "bound-device"` alongside its selected `binding` and `effects`; the TQ9 benchmark uses `"emulator"`. The configurator permits only the assigned tool names and enforces the saved binding and effects. Without approval, the runtime can stop before invoking a tool. Resume an already selected device run with `resume RUN_ID --approve-bound-device-tools`, or an emulator run with `--approve-emulator-tools`. The equivalent `driver_resume` input is `scoped_tool_approval`. These settings do not alter the developer's global Codex configuration.

## Recovery

Save the run ID returned by `driver_start`. Read `driver_status` or `driver_result` from either UI to reconnect. Closing an MCP connection does not cancel a run. Cancellation is an explicit action. Resume rechecks saved evidence; uncertain physical operations require an observation before further action.

Keep the machine awake during long runs. Background persistence survives a client disconnect, not a powered-off computer. The configurator records interrupted runs for checked recovery after a crash.

The budget measures wall-clock seconds from the original start, including stopped time. Ordinary resume retains that deadline. After the user explicitly approves more time, resume with an increased total and its reason:

```sh
python -m generative_driver resume RUN_ID --budget-seconds 21600 --budget-reason "User-approved continuation after pause"
```

This example sets a six-hour total measured from the original start. `driver_resume` accepts the equivalent `budget_seconds` and `budget_reason` fields from Goose or Codex. The total can increase up to seven days; repeating the same total adds no further time. A durable `run.budget_extended` event records the previous and new totals/deadlines, added seconds and reason. Start time, prior attempts and accepted evidence remain intact, and recovery checks still apply.

## Developer command line

`python -m generative_driver tools` lists the installed tool catalog. Use `tool NAME --arguments arguments.json` to execute one tool with explicit paths and bindings. These direct developer calls are separate from a managed workflow; use the configurator when ownership and checked stage progression are required.

Start a managed run with `run --spec run.json`. The JSON contains `goal`, `executor`, `inputs`, `binding`, `effects`, and `budget_seconds`; `request_id` makes a retried start idempotent. Follow it with `status RUN_ID`, `events RUN_ID --after CURSOR`, `result RUN_ID`, `cancel RUN_ID`, or `resume RUN_ID`. Use `respond RUN_ID --observation observation.json` for a requested independently sourced observation. `--home DIRECTORY` precedes the command when selecting a separate state directory.

## Optional containers

The core, runtime and configurator execute natively. Containers may host analysis tools or an emulator as long as the selected adapter exposes its required files and connection endpoint. Container-specific USB forwarding is outside the native core contract. The initial benchmark uses native tools and does not require Docker.

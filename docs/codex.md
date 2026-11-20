# Use from Codex

Install the package and [configure its worker runtime](setup.md). The chat and worker may use different models. On Windows, [start the configurator](setup.md#start-the-configurator) in standalone PowerShell first; a terminal inside Codex shares the client’s lifetime.

## Verify the OpenAI CLI

```sh
codex --version
```

Output must start with `codex-cli`. Comic-library, archive or Django output belongs to an unrelated application; stop it with Ctrl+C. If names collide, use the verified OpenAI executable’s absolute path for registration and worker configuration. PowerShell invokes quoted paths with `&`.

Some macOS installations bundle it here; check the actual file before use:

```sh
CODEX_CLI="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
"$CODEX_CLI" --version
```

## Generate the plugin

Use the Python environment containing Generative Driver:

```sh
python -m generative_driver setup --host codex --output ./codex-install
codex plugin marketplace add ./codex-install
```

For the bundled executable, use `"$CODEX_CLI" plugin marketplace add ./codex-install`. Restart Codex, open Plugins Directory, install **Generative Driver**, then start a new chat. See [official plugin instructions](https://developers.openai.com/plugins/build/plugins).

Generated assets pin Python and the state directory. Supply a leading `--home DIRECTORY` for a custom location. The plugin's version is its build: the package version, `+`, and 12 hex digits that change whenever a generated file changes. The checked-in plugin instead expects `generative-driver-mcp` on PATH.

## Update the plugin

After upgrading the package or moving the Python environment, run the same `setup` command again. It replaces the plugin it generated in that folder, so the registered marketplace holds the new build; it refuses a folder holding a plugin or marketplace file it did not generate. Then reinstall or refresh **Generative Driver** in Codex and start a new chat.

A driver in the current package format, `interface-package/3`, keeps working after an upgrade, in managed sessions and in reuse: a package is data, the installed tool runs it, and the tools an agent is served are rendered from the driver when the package is served. The tool refuses a package of an earlier format and names the format; a new run makes that driver again.

`driver_doctor` called from Codex reports `plugin.state`: `current`, or `stale` with the command to run again. `python -m generative_driver doctor` from a terminal checks only the plugin `setup` last generated: `stale` when it is out of date, `unchecked` otherwise.

## MCP-only setup

```sh
codex mcp add generative-driver -- /absolute/path/to/python -m generative_driver.mcp
```

Use the Windows Python path on Windows. App configuration uses that executable plus separate `-m` and `generative_driver.mcp` arguments. Set `GENERATIVE_DRIVER_HOME` for a custom state directory. Copy the generated `skills/generative-driver` folder into the project’s `.agents/skills/`. See [official MCP setup](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Check the service path

After `python -m generative_driver doctor`, and before a first run:

```sh
python -m generative_driver smoke
```

It runs one whole six-stage run through a background service of its own, in a new temporary folder, with a scripted worker in place of Codex and a stand-in device served on a localhost port. It needs no model, Codex sign-in or hardware, and leaves your state directory, configuration and runs alone. `"ok": true` shows that the service, its worker launch and the tool gateways work on this machine; it shows nothing about a model, a firmware image or a real device.

A failure prints `"ok": false` with `failed_stage`, `reason` and `logs`, and keeps the folder named by `home` so the logs can be read:

- No `failed_stage`, and the service or the run could not be started: read the service log, `logs.service`. The local socket or named pipe, the Python installation or the temporary folder is at fault.
- A `failed_stage`: read that stage's worker output, `logs.worker_stderr` and `logs.worker_events`. The worker launch, the stage's tool gateway or its tools broke there, such as the sealed interpretation workspace or the generated tool catalog at reuse.
- `service.stopped` is `false`: the service stopped answering during the run, or did not stop within 30 seconds of the smoke's request and was terminated. Read `logs.service`; workers or gateways it started may still run.

## First conversation

> Use Generative Driver to check my installation, help me select a supported device, and start a run. Save its run ID and show the accepted evidence when it finishes.

The configurator launches workers and retains progress after disconnect. Plugin installation does not install optional native tools. The plugin format and stdio connection have automated checks; UI labels may vary.

Workers are not read-isolated: a worker running as the same user can read files outside its workspace.

## Firmware analysis and interpret tasks

Name `ghidra_home` and `java_home` in `driver_start`, and the configurator analyzes the acquired firmware once, before the first interpret worker, with the kit's Ghidra helper. It does so for an ELF of 32-bit ARM, AArch64 or Xtensa, and for a raw Cortex-M image whose vector table is at its first byte. Every interpret worker of the run, repairs included, reads that analysis and its index, `ANALYSIS_INDEX.json`, instead of running Ghidra again. When the analysis cannot be made, interpretation goes on as before, and the interpret stage's notes say why under `configurator.interpretation.kit_analysis`.

Interpretation runs as one worker unless the run starts with `interpretation` set to `tasks`:

```json
{"goal": "...", "ghidra_home": "/absolute/path/to/ghidra", "java_home": "/absolute/path/to/jdk", "interpretation": "tasks"}
```

A task-mode attempt is an inventory of the firmware's commands, one fresh worker per command, and a cross-check of the model the configurator assembles from their pieces. A repair re-runs only the commands the device contradicted. The mode needs the analysis; without it, or when an inventory or the identity command's piece is unusable, the attempt runs the single worker and its notes say so. While a task runs, `driver_status` names it in `worker.task`.

Task mode is unmeasured: whether it costs fewer tokens or makes better drivers than the single worker is a hypothesis until model runs compare them. Each task's token use is recorded for that comparison, in its `task.finished` event and in a `reports` row of its own under the stage's assignment. A run on a supplied device gets no kit analysis and no task mode.

## Documents and runs without firmware

Pass a manual, protocol notes or a datasheet with a run as a named input of `driver_start`:

```json
{"goal": "...", "inputs": {"firmware.bin": "/absolute/path/firmware.bin", "manual.pdf": "/absolute/path/manual.pdf"}}
```

The acquire worker imports each PDF, `.txt` or `.md` input with `acquire_document`, which splits it into page text. A PDF needs pypdf: install `generative-driver[docs]`. Every interpret worker reads the text of the run's documents, with the datasheet pages and protocol specifications the acquire stage fetched, under `documents/` with `DOCUMENTS.json`, up to 2 MiB in all. Documents are evidence of meanings, units and framing; where one disagrees with the firmware or the device, the worker follows the firmware or the device.

A run that acquires no firmware image, or several, and names a device `binding` interprets device first: one worker writes the model from the device's replies to operations it declares `read`, with the listen evidence and the documents. The configurator refuses it any other operation, whatever the run's grants; probe and ground then test the model under those grants, and the run goes on as usual. The interpret stage's notes give the mode `device` and list each device call. A run with neither one image nor a binding skips interpretation and ends without a driver. A run on a supplied device gets neither documents nor device-first interpretation.

## When a run fails

`driver_status`, or `python -m generative_driver status RUN_ID`, names the service's log as `log`: `daemon.log` in the state directory. The traceback behind a failed run is there, in an entry that names the run and its stage. Older entries may be in `daemon.log.1` beside it: a log over 10 MB is set aside under that name when the service starts. The same status gives `deadline`, `repairs` and `worker`, which tell a slow stage from a stuck one.

A worker's tool call has 570 seconds. The service stops a call that takes longer, or that still runs when the run is cancelled or out of budget, and records it as abandoned; an effect it might have had stays uncertain until you confirm it. A run on a supplied device is the exception: its tool calls still have no limit.

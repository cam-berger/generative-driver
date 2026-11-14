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

> Use Generative Driver to check my installation, help me select a benchmark or supported device, and start a run. Save its run ID and show the accepted evidence when it finishes.

The configurator launches workers and retains progress after disconnect. [Benchmark instructions](../bench/README.md) cover replay, calibrated agent trials and private evaluator inputs. Plugin installation does not install optional native tools. The plugin format and stdio connection have automated checks; UI labels may vary.

For emulated benchmarks, `driver_benchmark_run` sends the evaluator password file path to a bench harness. The configurator never receives the password or its path. The harness provides the device and grades once after the run; workers see instrument readings, not grades. Current `assistive/1` results form a new series. Workers are not read-isolated; answer-key separation is password-based, not a sandbox. Fresh native calibration is required because the recorded calibration predates this protocol. See [trial commands and scoring](../bench/README.md#run-an-agent-benchmark).

## When a run fails

`driver_status`, or `python -m generative_driver status RUN_ID`, names the service's log as `log`: `daemon.log` in the state directory. The traceback behind a failed run is there, in an entry that names the run and its stage. Older entries may be in `daemon.log.1` beside it: a log over 10 MB is set aside under that name when the service starts. The same status gives `deadline`, `repairs` and `worker`, which tell a slow stage from a stuck one.

A worker's tool call has 570 seconds. The service stops a call that takes longer, or that still runs when the run is cancelled or out of budget, and records it as abandoned; an effect it might have had stays uncertain until you confirm it. Benchmark runs are the exception: their tool calls still have no limit.

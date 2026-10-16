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

Generated assets pin Python and the state directory. Supply a leading `--home DIRECTORY` for a custom location; regenerate into a new directory after moving the Python environment. The checked-in plugin instead expects `generative-driver-mcp` on PATH.

## MCP-only setup

```sh
codex mcp add generative-driver -- /absolute/path/to/python -m generative_driver.mcp
```

Use the Windows Python path on Windows. App configuration uses that executable plus separate `-m` and `generative_driver.mcp` arguments. Set `GENERATIVE_DRIVER_HOME` for a custom state directory. Copy the generated `skills/generative-driver` folder into the project’s `.agents/skills/`. See [official MCP setup](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## First conversation

> Use Generative Driver to check my installation, help me select a benchmark or supported device, and start a run. Save its run ID and show the accepted evidence when it finishes.

The configurator launches workers and retains progress after disconnect. [Benchmark instructions](../bench/README.md) cover replay, calibrated agent trials and private evaluator inputs. Plugin installation does not install optional native tools. The plugin format and stdio connection have automated checks; UI labels may vary.

For emulated benchmarks, `driver_benchmark_run` sends the evaluator password file path to a bench harness. The configurator never receives the password or its path. The harness provides the device and grades once after the run; workers see instrument readings, not grades. Current `assistive/1` results form a new series. Workers are not read-isolated; answer-key separation is password-based, not a sandbox. Fresh native calibration is required because the recorded calibration predates this protocol. See [trial commands and scoring](../bench/README.md#run-an-agent-benchmark).

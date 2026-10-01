# Use from Codex

Install the Python package and [configure a worker runtime](setup.md). The developer-facing Codex and the worker runtime may use different models; record the worker model explicitly.

On Windows, [start the independent configurator](setup.md#start-the-configurator) from a standalone PowerShell window before connecting the plugin or MCP server. A terminal inside Codex is still owned by Codex's process lifetime.

## Verify the OpenAI CLI

Before running the registration commands below, check which program your terminal selects:

```sh
codex --version
```

The output must begin with `codex-cli`. An unrelated Python application is also named Codex; comic-library, archive or Django server output identifies that application. Press Ctrl+C if it starts a server. If the name resolves incorrectly, use the absolute path to your OpenAI CLI for every `codex` command below and for the worker runtime's `--command`. Verify that executable with `--version` first. In PowerShell, invoke a quoted executable path with `&`.

### macOS bundled executable, when present

Some desktop installations include the CLI at the following path. This installation-specific example verifies it before registering the marketplace; use your installed OpenAI CLI's actual path if this file is absent:

```sh
CODEX_CLI="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
"$CODEX_CLI" --version
```

After generating `codex-install` below, register it with `"$CODEX_CLI" plugin marketplace add ./codex-install` when using this bundled executable.

## Generate the plugin

Use the Python executable from the environment where you installed Generative Driver:

```sh
python -m generative_driver setup --host codex --output ./codex-install
```

This creates `codex-install/plugins/generative-driver` and a local marketplace, with an MCP connection pinned to that Python executable and state directory, plus the workflow skill. Use a leading `--home DIRECTORY` to select the same custom directory as your configurator. Keep the environment at its existing path, or regenerate into a new directory after moving it.

Register the generated marketplace using the OpenAI CLI verified above:

```sh
codex plugin marketplace add ./codex-install
```

Restart the desktop app, open its Plugins Directory, select **Generative Driver**, and install the plugin. Start a new conversation to pick up its tools and skill. The [official local plugin instructions](https://developers.openai.com/plugins/build/plugins) cover marketplace registration and refresh. The checked-in `plugins/generative-driver` variant uses `generative-driver-mcp` on PATH; generated assets avoid depending on the desktop application's PATH.

## MCP-only setup

For Codex CLI or its code extension, register the installation directly:

```sh
codex mcp add generative-driver -- /absolute/path/to/python -m generative_driver.mcp
```

Use the Windows Python executable path on Windows. In the app, an equivalent local MCP server uses that executable as its command and `-m`, `generative_driver.mcp` as separate arguments. For a custom state directory, set `GENERATIVE_DRIVER_HOME` in that server's environment; the generated plugin's `.mcp.json` shows the complete settings. See [official MCP setup](https://learn.chatgpt.com/docs/extend/mcp?surface=cli). Copy `codex-install/plugins/generative-driver/skills/generative-driver` into your project's `.agents/skills/` if using MCP without the plugin.

## First conversation

> Use Generative Driver to check my installation. Help me select a benchmark or attach a supported device. Start the workflow with my configured runtime, save its run ID, and show the accepted evidence when it finishes.

The front-end calls the configurator's tools. The configurator launches fresh workers and scopes their tool access. Reopening Codex and asking for the saved run ID retrieves the same progress. Use the [benchmark guide](../bench/README.md) for the initial replay check and paid agent baseline.

The plugin format is validated and the stdio connection is integration-tested. UI menu labels may vary by Codex version. A plugin installation alone does not install the optional native hardware or analysis dependencies.

For TQ9 v2, select the registered `tq9-v2` case and an explicit scenario/seed through the configurator. The evaluator must supply a current native calibration and keep its password outside worker assignments. Workers receive diagnostic feedback; frozen final checks remain encrypted and final failures are terminal. The [benchmark guide](../bench/README.md#versioned-tq9) covers scenario selection and explicit-sidecar offline scoring.

# Use from Codex

Install the Python package and [configure a worker runtime](setup.md). The developer-facing Codex and the worker runtime may use different models; record the worker model explicitly.

## Generate the plugin

From your installation environment:

```sh
python -m generative_driver setup --host codex --output ./codex-install
```

This creates `codex-install/plugins/generative-driver` and a local marketplace, with an MCP connection pinned to that Python executable and the workflow skill. Keep the environment at its existing path, or regenerate into a new directory after moving it.

Register the generated marketplace:

```sh
codex plugin marketplace add ./codex-install
```

Restart the desktop app, open its Plugins Directory, select **Generative Driver**, and install the plugin. Start a new conversation to pick up its tools and skill. The [official local plugin instructions](https://developers.openai.com/plugins/build/plugins) cover marketplace registration and refresh. The checked-in `plugins/generative-driver` variant uses `generative-driver-mcp` on PATH; generated assets avoid depending on the desktop application's PATH.

## MCP-only setup

For Codex CLI or its code extension, register the installation directly:

```sh
codex mcp add generative-driver -- /absolute/path/to/python -m generative_driver.mcp
```

Use the Windows Python executable path on Windows. In the app, an equivalent local MCP server uses that executable as its command and `-m`, `generative_driver.mcp` as separate arguments. See [official MCP setup](https://learn.chatgpt.com/docs/extend/mcp?surface=cli). Copy `codex-install/plugins/generative-driver/skills/generative-driver` into your project's `.agents/skills/` if using MCP without the plugin.

## First conversation

> Use Generative Driver to check my installation. Help me select a benchmark or attach a supported device. Start the workflow with my configured runtime, save its run ID, and show the accepted evidence when it finishes.

The front-end calls the configurator's tools. The configurator launches fresh workers and scopes their tool access. Reopening Codex and asking for the saved run ID retrieves the same progress. Use the [benchmark guide](../bench/README.md) for the initial replay check and paid agent baseline.

The plugin format is validated and the stdio connection is integration-tested. UI menu labels may vary by Codex version. A plugin installation alone does not install the optional native hardware or analysis dependencies.

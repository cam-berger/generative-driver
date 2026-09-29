# Use from Goose

Install the Python package and [configure the worker runtime](setup.md). Goose can be the front-end while Codex executes workers, or Goose can fill both roles.

On Windows, [start the independent configurator](setup.md#start-the-configurator) from a standalone PowerShell window before starting the recipe or extension. Its process lifetime must be independent of Goose.

## Generate and import the recipe

```sh
python -m generative_driver setup --host goose --output ./goose-install
```

In Goose Desktop open **Recipes → Import Recipe → Recipe File**, choose `goose-install/generative-driver.json`, and import it. Start a session with that recipe. It includes the workflow instructions and a stdio extension pinned to the installed Python executable and state directory. Use a leading `--home DIRECTORY` when generating the recipe to select the same custom directory as your configurator. These steps follow [Goose's recipe import documentation](https://goose-docs.ai/docs/guides/recipes/storing-recipes/).

From Goose CLI:

```sh
goose run --recipe ./goose-install/generative-driver.json
```

The checked-in `plugins/goose/generative-driver.json` expects `generative-driver-mcp` on PATH. Prefer generated assets when the desktop application cannot find your shell's Python environment.

## Add to an existing session

Open **Extensions → Add custom extension** and add a local stdio extension. Use the generated recipe's `cmd`, `args` and `envs` values and a 120-second tool timeout. The `GENERATIVE_DRIVER_HOME` environment entry selects the matching configurator. Follow [Goose's custom extension instructions](https://goose-docs.ai/docs/getting-started/using-extensions/). The configurator's start/status calls return promptly; the independent background worker owns long-running analysis.

Ask:

> Use Generative Driver to check my setup, help me select the inputs and device connection, and start a run. Keep the run ID so I can reconnect later.

For installation replay and the real-agent examples, follow [the benchmark guide](../bench/README.md). A recipe grants access to the configurator; it does not establish hardware availability or independent groundtruth. The configured Goose adapter's live behavior must be checked against your installed Goose version; automated contract tests use a scripted external process and are not a model benchmark.

# Use from Goose

Install the package and [configure a worker runtime](setup.md). Goose may be the client, worker runtime or both. On Windows, [start the independent configurator](setup.md#start-the-configurator) from standalone PowerShell first.

## Generate and import the recipe

```sh
python -m generative_driver setup --host goose --output ./goose-install
```

In Goose Desktop, choose **Recipes → Import Recipe → Recipe File**, select `goose-install/generative-driver.json`, then start a session. In Goose CLI:

```sh
goose run --recipe ./goose-install/generative-driver.json
```

The recipe pins Python and the state directory. Use a leading `--home DIRECTORY` for a custom location. The checked-in recipe expects `generative-driver-mcp` on PATH. See [Goose’s import guide](https://goose-docs.ai/docs/guides/recipes/storing-recipes/).

## Add to an existing session

Choose **Extensions → Add custom extension**. Use the recipe’s `cmd`, `args` and `envs`, with a 120-second timeout. `GENERATIVE_DRIVER_HOME` must match the configurator. See [extension setup](https://goose-docs.ai/docs/getting-started/using-extensions/).

> Use Generative Driver to check my setup, select inputs and a device connection, and start a run. Keep the run ID so I can reconnect.

The background owner runs long jobs. [Benchmarks](../bench/README.md) cover replay and calibrated agent trials. Recipe installation alone establishes neither hardware readiness nor live Goose behavior; automated adapter tests use scripted workers.

For emulated benchmarks, `driver_benchmark_run` sends the evaluator password file path to a bench harness. The configurator never receives the password or its path. The harness provides the device and grades once after the run; workers see instrument readings, not grades. Current `assistive/1` results form a new series. Workers are not read-isolated; answer-key separation is password-based, not a sandbox. Fresh native calibration is required because the recorded calibration predates this protocol. See [trial commands and scoring](../bench/README.md#run-an-agent-benchmark).

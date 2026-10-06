# Run dashboard

The dashboard is a local observer of one configurator run. It shows the six stages, run status, elapsed time, model repairs, recent run events and current Codex or Goose activity. It refreshes every second. It uses the installed Python package and your browser; no web service, container or additional runtime is required.

## Open it

Use the same Python environment and state home that started the run. Take `RUN_ID` from the run-start result in Codex, Goose or the CLI. For a benchmark suite, use `active_child_id` from `driver_benchmark_suite_status`; the dashboard follows that one child run:

```sh
python -m generative_driver dashboard RUN_ID
```

The command prints a local URL and opens your default browser. Keep that terminal open while monitoring. You can paste the URL into the Codex browser pane or another local browser. For an explicitly selected state directory:

```sh
python -m generative_driver --home "path to state" dashboard RUN_ID
```

Use `--no-open` to print the URL without opening a browser, or `--port 8765` to choose a local port. The default selects an available port. The URL is private to that dashboard instance and listens only on this computer.

If the configurator is stopped, start it from an operator terminal using the same home:

```sh
python -m generative_driver --home "path to state" service start
```

The page reports disconnection and reconnects when the service becomes reachable. Press Ctrl+C to stop the dashboard. The run continues in its configurator when the dashboard or browser closes.

## Read the status

An accepted stage has a checked handoff from the configurator. A finished worker shows “checking” until its handoff is accepted. A repair returns progress to the current revision; superseded stages cease to count as accepted. Blockers and unresolved device effects remain visible.

The run log retains the latest 200 configurator events. Uncheck “Follow” to read earlier entries without automatic scrolling. Agent activity shows the latest 12 progress messages and command/tool status entries from the current worker. Full command arguments, outputs and evaluator payloads stay in their original run artifacts.

The dashboard observes status and logs. Use the existing Codex, Goose or CLI run tools when you need to respond, resume or cancel.

# Installed CLI and MCP TDD record

The four seams were approved before implementation. This record covers the installed CLI/MCP seam.

1. Doctor from an unrelated directory with spaces and an empty executable search path: red (`No module named generative_driver`), then package metadata, console entrypoint and non-invasive dependency reporting. Optional tools are reported, not imported; checking does not create user state.

2. Stdio MCP initialization and doctor: red (MCP module missing), green with real MCP SDK transport.
3. Explicit runtime configuration: red (configure command missing), green preserving separate command arrays and model selections.
4. Generate host setup assets: red (setup command missing), green with native absolute interpreter and separate arguments; shared workflow skill included.
5. MCP disconnect/reconnect: red (driver_start unknown), green with detached service and fresh client retrieving the retained blocked result. No inference call was made.

6. Installed benchmark CLI routing: red (command absent), green through the benchmark facade's replay profile.
7. Host marketplace generation: red (catalog absent), green with a relative plugin path that resolves inside the generated distribution.
8. CLI start/result/cancel reconnect: red (run command absent), green across independent command processes.
9. Developer firmware import: red (tool command absent), green with the published SHA-256 test vector for `abc` and byte-preserving import.
10. MCP installation replay: red (tool absent), green through the real stdio connection and relocated package check.
11. Distribution archive: red (wheel included local compiled Python cache files), green after excluding bytecode from wheel and source manifests.
12. Doctor with a configured runtime outside PATH: red (available false), green using the explicit configured executable.
13. Codex reasoning setting: red (flag absent), green persisting the explicit runtime setting. Goose does not claim support for this Codex-specific option.
14. Scoped worker MCP session: red (legacy SDK decorator unavailable in installed MCP 2), green with constructor handlers; the actual protocol test imports the assigned bytes and refuses a subsequent call after cancellation.
15. Explicit emulator approval through CLI/MCP: red (flag absent and MCP ignored the unsupported field), green forwarding the typed approval scope to the controller. Both public interfaces refuse this scope for a generic device run.
16. Explicit bound-device approval: red (CLI flag absent and MCP rejected/ignored the new field), green forwarding the scope on start/resume and refusing it without a selected binding.
17. Explicit resume budget: CLI red (flags unrecognized), MCP red (additional fields ignored and total unchanged), then green through both real interfaces. The new total is measured from the original start, requires a reason and cannot decrease. Prior reports and start time remain intact; one durable event records the increase.

Release tests run again after integration against a freshly built, separately installed wheel; in-progress wheel snapshots are not final release verification.

## Benchmark report seam

1. Aggregate measured telemetry: red (reporting module absent), green with literal wall/tool durations, preserved missing usage and explicit unrun stages.
2. Completion cannot substitute for accepted evidence: red (orphan passing evaluator statuses produced a pass), green requiring every accepted workflow gate and independent verdict.
3. Export a blocked detached run: red (report_run absent), green producing JSON/Markdown with unknown inference and no local home path.
4. Carry the configured run budget into the report: red (missing top-level controller metadata mapping), green preserving the public result metadata.
5. Preserve independently observed values for offline scoring: red (observations missing), green with a whitelist of scoring evidence that excludes evaluator credentials and private contracts.
6. Distinguish managed worker and evaluator tool time: red (actor breakdown absent), green preserving separate measured durations and the directly measured overall wall time.

## Explicit service ownership

The native Windows MCP lifecycle requires an independently started configurator. A public CLI regression first failed because `service` was not a recognized command. Added `service start`, `service status` and `service stop`: the test now starts an owner in an external process, reconnects to the same PID, waits for shutdown, and verifies that status never starts an absent owner. The installed-wheel and native CI gates verify this alongside the workflow commands.

## Generated connection state directory

The public setup regression first showed that `--home "selected state" setup` produced a connection whose actual stdio MCP doctor reported a different inherited state directory. Setup now resolves the selected home and writes `GENERATIVE_DRIVER_HOME` into Codex's connection `env` and Goose's stdio extension `envs`. Both generated connections are launched through the real MCP transport, and their doctor results match the selected directory. The default setup test also verifies that the current native/default home is retained. All 12 CLI tests pass on native macOS Python 3.13. Goose's `envs` string map is verified against its [extension schema and environment resolver](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/extension.rs); this test does not claim a Goose Desktop UI run.

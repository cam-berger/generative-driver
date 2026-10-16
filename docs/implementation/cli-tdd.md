# CLI and MCP test record

Historical failing-then-passing checks at the [approved public seams](../design.md#public-test-seams). Release verification uses freshly built, separately installed archives; intermediate wheels are not final evidence.

| Boundary | Failure observed → accepted behavior |
|---|---|
| Doctor/install | Missing package → installed entrypoint works outside checkout, including spaces/empty PATH; reports optional tools without imports or state creation |
| Real stdio MCP | Missing module/legacy SDK decorator → SDK transport and constructor handlers work; scoped calls fail after cancellation |
| Runtime settings | Missing configuration/flags → command arrays, model and Codex reasoning persist; explicit executables work outside PATH |
| Host setup | Missing assets/catalog → native interpreter/arguments, shared skill and relative marketplace plugin resolve |
| Durable runs | Missing CLI/MCP routes → independent clients start, inspect and cancel the same detached run; disconnect retains state |
| Acquisition/replay | Missing routes → byte-preserving import with known `abc` SHA-256 and CLI/MCP smoke plus relocated replay |
| Archives | Compiled caches included → wheel/sdist exclude bytecode |
| Tool approvals | Missing/ignored scopes → emulator-only and bound-device scopes forwarded and rejected for incompatible/unbound runs |
| Budget extension | Missing/ignored fields → larger total from original start, explicit reason, preserved history and one durable event; no decrease |
| Reports | Missing telemetry/export → wall/tool/actor durations, explicit missing usage/unrun stages, budgets and safe offline observations |
| Acceptance | Orphan passing evaluator status → every accepted gate and independent verdict required |

## Service ownership

Windows MCP needs an independently started configurator. The initially absent `service` command was added with start/status/stop. A real external-process test verifies same-PID reconnect, shutdown and status without implicit startup.

## Generated state directory

Setup initially ignored selected `--home` in generated connections. It now writes the resolved directory to Codex `env` and Goose `envs` as `GENERATIVE_DRIVER_HOME`. Real stdio tests verify both selected and default directories through doctor. All 12 CLI tests passed on macOS Python 3.13 for that snapshot. Goose's string map follows its [extension schema/resolver](https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/extension.rs); this is not Desktop UI qualification.

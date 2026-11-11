# Breakdown: fix what the 2026-11-11 review found in the kit

For someone who installs the kit with Codex and points it at a device. Setup tells the truth about what is installed, a first run fails early and legibly, an emitted driver says what its operations mean and keeps working after the kit is upgraded, and interpretation works in small checked steps, including when there is no firmware image.

Out: the benchmark, which moves to its own repository and whose review notes are kept apart; a split of `configurator.py` as its own Feature, which waits for that move, so each Feature here puts new behaviour in a module of its own; new decoders and transports; any model run, which measures these changes and is the human's to launch.

01. doctor-identity — `doctor` says whether the configured worker runtime is really the Codex CLI and which optional Python extras load
02. plugin-freshness — the generated Codex plugin records what it was built from, and `doctor` says when the installed copy is older than the kit
03. service-smoke — a smoke with no model or hardware runs all six stages through the background service and a scripted worker
04. service-log — the service keeps a log with tracebacks, picks fault categories by type instead of by wording, and status shows the deadline, the repairs used and the worker's last sign of life
05. bounded-calls — worker-written conversion code runs in a child process, and every gateway tool call has a deadline
06. runtime-compat — a package runs on the installed runtime over its own data and records the runtime that built it, so a kit upgrade no longer strands earlier drivers
07. driver-contract-guide — interpret workers are taught to write the driver contract, a unit the firmware does not state is recorded as not stated, and both reach the MCP catalog
08. export-once — the configurator exports the firmware analysis once per run, with an index, and every interpret task and repair reuses it
09. interpret-tasks — interpretation runs as an inventory, one task per command handler, an assembly and a cross-check, each a fresh worker with small inputs
10. supporting-documents — documents acquired beside the firmware reach the interpreter as evidence
11. device-first-path — a run with no firmware lets a read-only device stage write the model and hand it on

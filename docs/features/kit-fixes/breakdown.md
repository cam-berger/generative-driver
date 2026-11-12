# Breakdown: fix what the 2026-11-11 review found in the kit

For someone who installs the kit with Codex and points it at a device. Setup tells the truth about what is installed, a first run fails early and legibly, an emitted driver says what its operations mean and keeps working after the kit is upgraded, and interpretation works in small checked steps, including when there is no firmware image.

Out: the benchmark, which moves to its own repository and whose review notes are kept apart. No Feature edits `benchmark.py`, `benchmark_support/`, the bench harness or their tests; where a change would need to, benchmark runs keep today's behaviour and the difference goes in the benchmark notes. That leaves out choosing fault categories by type instead of by wording, because nearly every word match classifies a benchmark error. Also out: a split of `configurator.py` as its own Feature, which waits for that move, so each Feature here puts new behaviour in a module of its own; new decoders and transports; any model run, which measures these changes and is the human's to launch.

On 2026-11-12 the human asked for fewer, larger Features, so the eight that followed 03 became the four below.

01. doctor-identity — `doctor` says whether the configured worker runtime is really the Codex CLI and which optional Python extras load
02. plugin-freshness — the generated Codex plugin records what it was built from, and `doctor` says when the installed copy is older than the kit
03. service-smoke — a smoke with no model or hardware runs all six stages through the background service and a scripted worker
04. service-diagnostics — the service logs every error it catches with its traceback, status shows the deadline, the repairs used and the worker's last sign of life, and every gateway tool call has a deadline, with worker-written conversion code run in a child process
05. driver-package — a package runs on the installed runtime over its own data, so a kit upgrade no longer strands earlier drivers, and interpret workers are taught to write the driver contract, with a unit the firmware does not state recorded as not stated through to the MCP catalog
06. interpret-tasks — the configurator exports the firmware analysis once per run, and interpretation runs as an inventory, one task per command handler, an assembly and a cross-check, each a fresh worker with small inputs
07. input-paths — documents acquired beside the firmware reach the interpreter as evidence, and a run with no firmware lets a read-only device stage write the model and hand it on

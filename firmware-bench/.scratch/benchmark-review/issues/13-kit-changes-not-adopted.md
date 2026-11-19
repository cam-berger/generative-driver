# Kit changes the benchmark has not adopted

Status: needs-triage

Checked: read, during the `kit-fixes` Run (`docs/features/kit-fixes/`). Line numbers are those of `5def462`.

On 2026-11-12 the `kit-fixes` Run stopped editing benchmark code and its tests, because the benchmark is moving. Each kit change below therefore leaves benchmark runs on the earlier behaviour, or changes what they see without the benchmark having been adjusted.

## Stop categories still follow wording

Left out of the Run entirely. A run's stop category, which decides whether a scoring trial is a failure or a fault, depends on the words of an error message in these places:

- `configurator.py:419`, `:1178-1185`: any `ValueError` whose message matches `stale|changed|altered|hash` becomes an integrity stop; any other is a host fault. "differs" is not in the pattern, so `emulated.py:131`, `:142` come out as host.
- `configurator.py:3219-3221`, `emulated.py:446-447`, `physical.py:259-260`: the interpret seal check matches `seal|changed|differ` in a tool's reason.
- `configurator.py:803-823`: a tool's host fault is read by parsing a class name out of its error text.
- `outcomes.py:32-38` reads an `integrity:` reason prefix; `harness.py:575`, `:792` test `error == 'ValueError'` in a refused start's reply; `emulated_actions.py:200-203` compares a message to choose an error code.

Two things constrain the fix. The grader's identity hashes its import closure, so marking a mismatch at its raise site inside grading code (`registry.py`, `snapshots.py`, `integrity.py`, `evaluator.py`) voids every admission and qualification. And the harness reads the class name of a refused start, so an error raised only at start must keep its plain `ValueError` name. A run reaches the grading code's checks at two call sites, `_verify_snapshot` and the adapters' `case_inputs`; marking there needs no grading edit.

## Benchmark runs have no deadline on tool calls

Feature 04 runs every kit tool of a generic run in a child process that the service stops after 570 seconds, at the budget, on cancel and at shutdown. That covers a worker's gateway calls and the configurator's own checks. A run with a case, and the tools the benchmark provides, keep the inline path:

- a gateway call or a check that hangs still holds the run, so the budget cannot stop it and the service cannot shut down;
- a register model's conversion module (`resources/tools/model_exec.py:51-64`), which the physical case's models use, still executes inside the service process.

Adopting it means letting case runs through `Controller._limits`, and running the adapters' `observe_stage` tool calls through the same process runner.

## What benchmark workers and readers now see

- A dispatched tool call that raises inside the service is recorded as a new event kind, `tool.abandoned`, with no `tool.finished`. Through the service, the worker's reply is `{"ok": false, "abandoned": "raised", "reason": ...}` in place of `{"ok": false, "error": <type>, "reason": <message>}`. The scorecard ignores unknown event kinds and still reads a started call with no finish as unknown.
- A run's `status` adds `deadline`, `repairs`, `worker` and `log`. Suite replies are unchanged.
- Shutdown no longer waits for a generic run's accepted tool call; it stops it. Draining for inline calls is untested.

## Driver packages (Feature 05)

These entries name functions rather than lines.

- **Benchmark interpret workers are not taught the contract.** A generic run's interpret workspace now holds `CONTRACT.md` and `contract.py`, its `TASKS.json` and repair instructions tell the worker to write `contract.json`, and emit merges that file with the model (`driver_contract.py`). Benchmark interpret workspaces get none of this, so benchmark packages keep the contract the kit imports from the model: generic descriptions, a `semantics_unknown` caveat on every operation, and the model's units. A benchmark worker that writes `contract.json` without `protocol.json` is still refused by `emit_package`.
- **`emit_check` now replays a package whose bundled copy differs.** It used to stop at integrity, with `replay_status` `not_run`. It now replays on the installed runtime and reports `passed` or `failed`, with a `replay_reason` that says so; `runtime_current` stays false. The benchmark's emit gate (`emulated.py`, stage `emit`) still requires `runtime_current`, so its verdicts are unchanged. Its rejection reason comes from the shared `configurator._emit_check_failure`, though, and for that case it now reads "the package carries a runtime other than this kit's" instead of "runtime is not current, so replay did not run".
- **Renderer `3` changes new catalogs and the toolchain revision.** New packages' `mcp-tools.json` names renderer version `3`. For benchmark packages the rendered text is unchanged, because their contracts carry no `unit_not_stated` caveat. The catalog's bytes and hashes change all the same, and with them the manifest's. The runtime and toolchain changes also change the toolchain revision, which is a comparison dimension. Earlier packages keep renderer `2` and still verify.

## Firmware analysis and interpret tasks (Feature 06)

These entries name functions rather than lines.

- **Benchmark runs get neither the kit analysis nor the task mode.** A generic run that names `ghidra_home` and `java_home` now has the configurator analyze the acquired image once (`kit_analysis.prepare`), and its interpret workers get the export and its index, `ANALYSIS_INDEX.json`, as sealed inputs. A generic run started with `interpretation: tasks` interprets as an inventory, a handler task per command and a cross-check (`interpret_tasks.run`). A run with a case returns from `Controller._prepare` before either, so its interpret workers still get the analysis kit and run Ghidra themselves, and every repair runs it again. `interpretation` is accepted at start and ignored there.
- **Adopting them** means giving the adapters' interpret preparation (`prepare_stage`) the analysis inputs and their `TASKS.json` entry, and their `observe_stage` a collection that reads a task-mode attempt's output. A benchmark comparison of task mode with the single worker would also need each task's `reports` row counted under its stage attempt: `reporting.summarize` counts every row of a stage as an attempt.
- **What benchmark readers now see.** Nothing in a case run changes. A generic run's `status.worker` may add `task`, and its events add `kit_analysis.started`, `kit_analysis.finished`, `task.started` and `task.finished`.

## Input paths (Feature 07)

These entries name functions rather than lines.

- **Benchmark runs get neither documents nor device-first interpretation.** A generic run's interpret workers now get the text of every supplementary document the acquire handoff carries, under `documents/` with `DOCUMENTS.json` (`supplementary_documents.sealed_inputs`). A generic run with no single image and a device binding interprets device first (`device_first`). A run with a case returns from `Controller._prepare` before either, so the physical case's interpreter still reads only its datasheet and `pages`, and a case run that acquires no image skips interpretation as before. Its acquire workers' tools are unchanged: `acquire_document` is listed only for generic runs.
- **A benchmark acquire worker given `acquire_protocol_spec` would now hand its files on.** The tool now returns the fetched source files and their sections directory under `artifacts`, so the gateway records them and the acquire handoff carries them. No case gives its acquire worker this tool today.
- **What benchmark readers now see.** Nothing in a case run changes. A generic run's interpret handoff notes may add the mode `device` and its `device_calls`.

## Direction

Decide these in the new repository, with the run-side call sites and `SYSTEM_MODULES` as the boundary. The deadline matters most before long unattended suites: one hung tool call there holds a slot until the suite's budget.

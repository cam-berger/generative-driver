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

## Direction

Decide these in the new repository, with the run-side call sites and `SYSTEM_MODULES` as the boundary. The deadline matters most before long unattended suites: one hung tool call there holds a slot until the suite's budget.

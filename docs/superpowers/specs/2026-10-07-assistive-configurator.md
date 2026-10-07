# Assistive configurator: objective

## Purpose

Generative Driver reverse-engineers device firmware into a driver. It succeeds when the driver is **functionally equivalent** to the original firmware: for the same inputs, it produces the same outputs and the same device effects. Equivalence is about function, never text: the driver must be able to do what the firmware's interface does, and it may be written however the agent likes. The benchmark measures that property. It is not the product.

The configurator helps reach that driver. It:
- runs stages and tools;
- holds device access and effect grants;
- records hashed evidence;
- hands each stage what it needs, plus notes on what is still unresolved.

It never knows the truth. It never stops a run because the work so far looks wrong.

## What is wrong today

- **Quality judgements stop runs.**
  - `_check` refuses any report that has a check which is not `pass`, which includes a check reported honestly as `not_run`. This stopped device-connection run `run-0bddf9e2448f46c7` after acquire had already produced `app.elf`.
  - A worker status other than `completed` ends the run as `blocked`.
  - Generic ground is hard-blocked in `_prepare`.
  - When the repair budget runs out, the run is blocked instead of moving on.
- **Truth reaches the process.**
  - In benchmark runs, probe and reuse are judged by the evaluator, which holds the answer key, through `check_stage`.
  - Its graded feedback (`project_feedback(records, grade)`) is then fed into repair.
  - So the evaluator, not the device, steers the candidate.
- **Results confirm the cost.** The 2/3 three-family trial lost store when its repair budget ran out mid-probe. That store run never reached a final driver to grade.

## Target behaviour

1. **Every stage hands off.** A stage ends with its artifacts, their hashes, and a `notes` record. The record holds:
   - the worker's own checks, including failed and not-run ones;
   - the configurator's checks, which do not depend on truth;
   - open questions.

   The next stage always starts. A stage that produced nothing usable passes on an empty handoff and its notes.
2. **The configurator's checks use only run evidence.**
   - Do the hashes match?
   - Does the model validate?
   - Does the device do what the model predicts under probe?
   - Does the package execute?

   Disagreements become notes and drive repair. They never stop the run.
3. **Repair is driven by the device.**
   - When probe or ground observations contradict the model, the configurator sends the difference between what the device did and what the model predicted back to interpret.
   - Repair is bounded by budget.
   - When the budget runs out, the run continues to emit with the best revision so far.
4. **Generic ground runs.** A worker tests the model's meanings against the device, using operator observations where they are given.
5. **One final verdict, kept apart from the process.**
   - After emit, the driver is frozen and fresh reuse runs it against the device.
   - Then the verdict is recorded as `equivalence: {equivalent, checks, diff}`. The run ends `completed` whether the verdict is equivalent or not.
   - **Benchmark runs:** the evaluator grades the frozen package against the encrypted truth once, at this point.
   - **Generic runs:** the verdict compares driver outputs with firmware outputs on the same inputs, and is labelled as self-evidence rather than an independent grade.

**Only these stop a run:**
- **Safety:**
  - effect grants and the `reset` rule;
  - uncertain writes, which are never retried silently;
  - binding conflicts.
- **Integrity:** a hash mismatch, or a sealed input that has changed.
- **Operator:**
  - cancellation;
  - wall-clock budget;
  - a missing executor or binding.

## Clean change path

Each slice is one failing test, then its implementation, through the configurator/worker seam unless noted.

1. **Outcome model.** One `_outcome(stage, report, checked)` function returns `advance`, `revise` or `stop(reason ∈ safety|integrity|operator)`. It replaces the scattered `self._state(run_id, 'blocked', …)` calls in `_run`.
   - Test: scripted workers that report `needs_revision`, `blocked` or a `not_run` check still advance.
2. **Notes handoff.** `_accept` stores the `notes` and hashes every handoff, including partial ones. `_prepare` passes earlier notes forward.
3. **Truth-free checks for every run.**
   - Benchmark adapters keep their role as the device provider: emulator start, reset, recovery and cleanup.
   - Benchmark adapters stop judging probe and reuse. `check_stage` leaves the stage loop.
4. **Device-driven repair.** Model-versus-device differences trigger the `revise` route. Exhausting the budget leads to `advance`.
5. **Generic ground worker.** This replaces the hard block in `_prepare`.
6. **Final verdict.**
   - A `finalize` step after reuse records `equivalence`.
   - The evaluator is called once, from the benchmark run/score seam.
   - Suite reports count equivalent drivers.
7. **Surface and docs.**
   - The dashboard, status output and reports show notes and the verdict.
   - Amend `docs/design.md`, which currently describes a checked stage progression.
   - Rewrite the tests that assert blocking so they assert advance plus notes.

Earlier results keep their protocol label. Numbers produced under the new protocol are a new series and are not compared directly with the 2/3 result.

## End test: three-family run

- **What runs:** the default suite on the registered emulated families TQ9 v2, sampled-sensor and parameter-store, with 3 repetitions each (9 trials), the qualified evaluator, and an actual model executor.
- **Prerequisites:**
  - Renode, Ghidra and Java are installed;
  - a fresh evaluator qualification, because the evaluator's role changes;
  - explicit authorization before launch, because this is an actual model run.

**The test passes when:**
- all 9 trials reach a frozen, graded driver;
- no trial stops for any reason outside the stop list above;
- run records show that no stage, worker or configurator check received the truth or evaluator feedback;
- each trial records an equivalence verdict with its diff.

**What it measures:** the number of equivalent drivers out of 9. This is the number to improve. It may start below the earlier rate, because repair no longer sees graded feedback.

**What stays a smoke test:** the ESP32-S3 device runs as a physical smoke test of the same path. It is recorded as a physical observation, not a score.

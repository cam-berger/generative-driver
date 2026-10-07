# Assistive Configurator Implementation Plan

> **For agentic workers:** execute task by task in order. Each task is one or more failing tests, then the implementation, then the full suite. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stages never stop a run on quality. The configurator records evidence, hands every stage's artifacts and notes forward, repairs from device-versus-model differences, and keeps truth out of the stage loop. Each run ends with one functional-equivalence verdict.

**Architecture:**
- One pure `_outcome()` decides `advance`, `revise` or `stop` after every stage.
- `_accept` stores partial handoffs together with a hashed `notes.json`.
- A `_finalize` step after reuse records `equivalence` once.
- Benchmark adapters keep two roles only:
  - device provider: emulator start, recovery and cleanup;
  - final grader: the new `benchmark.finalize`.

  Their per-stage checks become truth-free.

**Tech stack:** Python 3.11+, sqlite3, unittest (pytest is not installed). Run tests with the main venv from the worktree root:
`env -u GD_TEST_WHEEL -u GD_TEST_SDIST PYTHONPATH="$PWD/src" /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest discover -s tests`
Single module: `... -m unittest tests.test_configurator -q`. Baseline at `5c842b2`: 518 tests, OK, 3 skips (archive gates).

**Spec:** `docs/superpowers/specs/2026-10-07-assistive-configurator.md`

**Code map:** `/private/tmp/claude-501/-Users-cberg-Research-generative-driver--claude-worktrees-goose-new-device-trial-5c6f78/7370e2b2-589f-4122-9562-5ac716afd378/scratchpad/understand.json`. It holds JSON maps with file:line sites and the affected tests per area:
- `maps[0]` configurator loop;
- `maps[1]` generic tests;
- `maps[2]` benchmark stage truth;
- `maps[3]` final evaluation;
- `maps[4]` benchmark tests;
- `critic`: corrections to the other maps.

Read the entries for your task.

## Global constraints

- **Equivalence is functional, never textual.** Compare outputs and effects for the same inputs. Never compare candidate source with reference source.
- **Only these stop a run:**
  - `safety`: effect grants, the reset rule, uncertain writes, binding conflicts;
  - `integrity`: a recorded or observed hash that changed, a changed sealed input, a stale or duplicate handoff;
  - `operator`: cancellation, wall-clock budget, a configured runtime that cannot start, a missing binding;
  - `host`: the runtime crashed with a nonzero exit, or an unexpected configurator exception. Both are resumable.

  Nothing else stops a run.
- **Worker report content is never a stop.** That covers status `needs_revision`, `blocked` or `failed`, failed or `not_run` checks, missing artifacts, a missing or invalid report from a runtime that exited 0, and a worker-claimed hash that disagrees with the bytes. Each becomes a note. A mismatched claimed artifact is dropped, and the drop is noted.
- **Truth stays out of the stage loop.**
  - No stage preparation, stage check, repair input, note, handoff check, progress record or event payload may contain evaluator grades, expected values, pass/fail against truth, or check ids taken from truth contracts.
  - The evaluator runs exactly once per run, in `finalize`, after reuse.
  - Instrument observations of the emulated device (monitor readings of device state) are device evidence. They may be shown to workers, labelled `instrument_observation`, and are never graded during the run.
- **Never retry an uncertain device write silently.** Keep every existing uncertain-write, binding and reset guard and its tests.
- **Fresh reuse** receives only the emitted package, the objective and permitted operating information. It never receives notes.
- **Labels.** New runs record `progress.protocol = "assistive/1"`. Runs from the earlier protocol keep their meaning. Resuming an earlier-protocol run is refused with an operator reason. Reports never mix protocols.
- **Portability.** Core code runs on macOS and Windows: no Unix-only imports, no shell-dependent launch.
- **Commits.** One commit per task, message ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push.
- **Never open or print** anything under `bench/groundtruth`, `*.enc` files, password files or truth plaintext.

## Rulings (recorded decisions)

1. **Hash mismatches.**
   - A worker-claimed hash mismatch is a note.
   - A mismatch against a hash the configurator recorded (accepted handoffs, observed tool artifacts, sealed inputs, notes.json) is an integrity stop.
   - *Cost if wrong:* a typo stops a run, or a tampered artifact goes unnoticed. The second is prevented because only observed or recorded hashes are trusted.
2. **Runtime outcomes.**
   - `agents.execute` 'blocked / Cannot start configured runtime' → operator stop.
   - 'failed / exited with an error' → host stop.
   - 'failed / no structured report' or 'schema mismatch' → advance with notes.
3. **Instrument observations stay worker-visible and ungraded.** Diagnostic stimulus and grading by the evaluator are removed from probe.
4. **Emulator recipe.** The device provider gets a recipe-only accessor. The evaluator contracts stay inside `finalize` and `score`.
   - The truth envelope still holds the recipe, so the configurator process decrypts it to start the emulator.
   - Splitting the envelope needs a rekey, an evaluator version bump and fresh calibration. That is follow-up work.
   - Docs must state this honestly.
5. **Legacy cases (`tq9`, `bme280`)** follow the same adapter interface: truth-free stage checks, grading in `finalize`. Where a legacy stage depended on evaluator-only state, the stage hands off what the worker produced, plus notes.
6. **Unscored final.** A host or operator fault during finalize records `equivalence.equivalent = null` (`unscored`) and stops as `host`/`operator` with `next_stage = "finalize"`. Resume grades once.
7. **Generic final verdict is self-evidence.** `equivalent` is true when:
   - the emitted package passed `emit_check` replay of the recorded device exchanges;
   - and every fresh-reuse live operation succeeded.

   It is labelled `"basis": "self-evidence"`. Benchmark runs use `"basis": "evaluator"`.

## Review focus

1. **Crash between accepting a partial handoff and routing a revision.** Resume must continue at interpret with the bumped revision, never at the next stage.
2. **A worker that reports `blocked` on acquire with no artifacts.** Interpret receives an empty handoff with notes. Interpret preparation must not raise. With no binary it hands off empty with a note, and the run continues to finalize with "not equivalent: no driver emitted".
3. **A run whose host stopped after the verdict.** It must not be graded twice, and cleanup or sealing failure must not erase the verdict.
4. **Old-protocol runs.** Resume is refused. Their feedback is never replayed.
5. **Evaluator wrapper.** An exception inside `_evaluator_operation` must not leave the run uncertain unless a device effect really was dispatched.

---

## Part A: configurator (generic path)

### Task A1: outcome model and stop categories

**Files:** `src/generative_driver/configurator.py` (`_run`, `_state` category whitelist, new `_outcome`), `tests/test_configurator.py`, plus the other test files named in `understand.json` `maps[1].tests_affected` that assert quality blocking.

**Behaviour:**
- `Outcome` is a frozen dataclass: `kind ∈ {advance, revise, stop}`, `reason`, `category ∈ {safety, integrity, operator, host}`, `diff`.
- `_outcome(stage, result, checked, *, uncertain, repairs, max_repairs)` is pure and returns:
  - stop for a runtime-cannot-start, runtime-crash, budget or cancel cause;
  - stop for `checked['stop']` (`{category, reason}`);
  - stop for uncertainty (safety);
  - revise when `checked['device_diff']` exists, the stage is probe or ground, and repairs < max;
  - advance otherwise. When a diff exists but the budget is spent, the reason is "Repair budget exhausted; continuing with best revision".
- `_run` uses `_outcome` for every post-worker decision. The report-status gate (configurator.py around 841-844) is removed. Existing `_check` 'ok False' results are converted:
  - integrity reasons (artifact changed after the tool returned, outside the workspace) → `stop` integrity;
  - everything else → notes, advance.
- `_state` accepts categories `safety` and `integrity`. The post-check uncertainty stop is labelled `safety`.
- An exception from `_prepare` or `_check` that is a `ValueError` about changed bytes or stale handoffs → integrity stop. Other exceptions remain failed/host.

**Tests (write first):**
- A scripted worker that reports `needs_revision` on acquire with one observed acquisition artifact advances to interpret.
- A scripted worker that reports `completed` with a `not_run` check advances.
- A runtime that cannot start stops as `operator` and is resumable.
- A runtime with a nonzero exit stops as `host` and is resumable.
- An observed acquisition artifact modified after the tool returned stops as `integrity`.
- An uncertain write still blocks as `safety` and is never retried. Keep the existing tests.
- Unit tests for `_outcome` over each branch.
- Rewrite every test that asserted quality blocking so it asserts advance plus notes. Keep tests that assert safety, integrity or operator stops.

- [ ] Failing tests → implement → full suite → commit "Decide every stage outcome in one place; only safety, integrity, operator and host stop".

### Task A2: notes handoff, partial handoffs, atomic revise

**Files:** `configurator.py` (`_accept`, `_route`, `_prepare` input selection, `_workflow_progress`, `start`), tests.

**Behaviour:**
- `_accept(run_id, assignment, checked, run_dir, workspace, progress, notes)`:
  - accepts an empty or partial artifact list;
  - writes `notes.json` into the accepted directory and records `handoff.notes = {path, sha256}` plus an inline copy;
  - in the same transaction, persists the new progress and, for a revise, the `run.revision` event and supersede. This replaces the separate `_route` call on that path.
  - The duplicate rule (stage, revision) stays. A revise bumps the revision in that same transaction.
- Notes schema `stage-notes/1`:
  - `worker`: runtime status/reason, report status, summary, checks, unresolved;
  - `configurator`: checks, dropped artifacts with reason, tool refusals, device_diff;
  - `open_questions`;
  - `outcome`: kind and reason.
- `_prepare`:
  - forwards `context.prior_notes` (the inline notes of earlier handoffs at the current revision) to every stage except reuse;
  - picks inputs per stage from the latest handoff that has the needed artifacts, not `accepted[-1]`. In particular the generic model reaches ground and emit:
    - probe needs the interpret model;
    - ground needs the model plus probe records;
    - emit needs the model plus a successful probe if one exists.
  - A stage whose required input is absent still runs its worker with a note "no <input> available" and whatever exists. It does not raise.
  - Exception: reuse with no package skips its worker, hands off empty with a note, and the run proceeds to finalize.
- New runs: progress gets `protocol: "assistive/1"`. `_workflow_progress` accepts it. Resume of a run without the protocol key → refused, operator reason "Run was created under the earlier gated protocol; start a new run".
- Resume re-verifies notes.json hashes (integrity).

**Tests:**
- A partial acquire handoff: notes.json is hashed and listed in `result()`.
- Simulate a crash after accepting a revise. Restart the controller and resume: the run continues at interpret with revision+1.
- Tampering with an accepted notes.json → resume stops as integrity.
- The generic model is passed from interpret to emit.
- Reuse with no package → skipped with a note.
- An old-protocol resume is refused.

- [ ] Failing tests → implement → full suite → commit.

### Task A3: generic ground worker and device-driven repair

**Files:** `configurator.py` (`STAGE_TOOLS['ground']`, `_prepare` ground and interpret, `_check` probe and ground), `src/generative_driver/_toolchain/probe.py` only if a helper is needed, tests.

**Behaviour:**
- Generic ground no longer blocks. It runs a worker with `probe_run`, `interface_execute`, `probe_diff`, `model_validate` and `probe_observe`, under the existing serial and effect gates. The objective: test the model's meanings (units, scaling, effects) against the device, using operator observations where given.
- Generic probe and ground checks compute `device_diff` from observed `probe_diff` results and from failed observed `probe_run` operations of the current assignment. These are configurator-observed tool events, not worker claims. A non-empty diff → `checked['device_diff']`.
- Generic interpret on a revise:
  - passes `defects=` (the diff) to `interpret_run`;
  - passes the previous model directory as an extra named input;
  - keeps the sealed workspace.
- Generic interpret with no usable binary: hands off empty with a note. No stop.
- Generic interpret collection or validation failure → notes. The model is handed off if it exists, with validation notes.

**Tests:**
- Generic ground with a scripted worker calling `probe_run` hands off with notes. It is never blocked.
- A probe whose observed `probe_diff` has defects routes to interpret with the diff as defects, and the next interpret workspace contains the diff.
- With the repair budget exhausted, the run advances to emit with the best revision and a note.
- No binary → interpret hands off empty and the run reaches finalize.

- [ ] Failing tests → implement → full suite → commit.

### Task A4: finalize, once-only verdict, surface

**Files:** `configurator.py` (`_finalize`, recovery at init, resume guard, `_has_terminal_final_failure`, the finally block), `dashboard.py`, `reporting.py`, `suite_reporting.py` (the generic parts only; benchmark counting is in B3), the MCP/CLI result passthrough if needed, `docs/design.md` amendment, tests.

**Behaviour:**
- After the stage queue empties, `progress.next_stage = "finalize"` is persisted, then `_finalize(run_id)` runs:
  - **Generic:** self-evidence verdict (Ruling 7).
  - **Benchmark:** calls `benchmark.finalize`, added in B2. Until B2 lands, benchmark runs keep the current final path. B2 switches them.
- The verdict is recorded in `verdicts` with id `f"{run_id}:final"` and payload:
  ```
  {stage: "finalize", equivalence: {equivalent: bool|None, basis, checks, diff}, protocol}
  ```
- The run becomes `completed`, with reason "Final verdict: equivalent", "not equivalent" or "unscored". `result()` exposes `equivalence`.
- Once-only guarantees:
  - controller init recovery marks a run with a final verdict as completed;
  - resume never re-grades;
  - a sealing or cleanup failure after the verdict records an event and keeps both the verdict and the completed status, unless an effect is uncertain, which still blocks as safety.
- `_has_terminal_final_failure` recognises only old-protocol shapes.
- The dashboard and status show the latest notes summary per stage and the equivalence verdict. Reports count `equivalent`.
- `docs/design.md`: append an amendment dated 2026-10-07 recording the assistive protocol and how it supersedes the "checked stage progression / all six gates" language. Keep the historical sections intact.

**Tests:**
- A generic run with replay passed and successful reuse → completed, equivalent true, basis self-evidence.
- Reuse failed → completed, equivalent false.
- Restart after the verdict → not regraded.
- Cleanup failure after the verdict → the verdict is kept.
- The dashboard projection includes the equivalence and notes.

- [ ] Failing tests → implement → full suite → commit.

## Part B: benchmark adapters

### Task B1: truth-free v2 emulated stage checks and preparation

**Files:** `benchmark_support/emulated.py`, `ground.py`, `candidate_context.py`, `behavior.py` (only stop exporting `project_feedback` to the loop), `emulated_recovery.py`, `benchmark.py` (`check_stage` → renamed or kept as truth-free `observe_stage`; stop writing truth `stage_verdicts`), `configurator.py` (`_check` for cases, the `_evaluator_operation` wrapper, `_tool` uncertain marking for pre-I/O refusals), tests (`maps[4]`).

**Behaviour:**
- **Probe:**
  - `_diagnose` no longer runs.
  - Checks are structural: capabilities present, valid JSON, `validate_capabilities` against the model, model hash unchanged. Failures are notes; a changed model is an integrity stop.
  - `device_diff` comes only from the worker's own observed tool results in this assignment: failed `interface_execute`/`probe_run` results and `probe_diff` defects.
  - The handoff contains the worker's successful `probe_run` outputs (from configurator-observed tool events), `capabilities.json` and the adapted model.
- **Ground:**
  - preparation provides the model, the worker probe records and the worker's live observation journal, with no graded projections;
  - tools: `interface_execute`, `probe_run`, `probe_diff`, `model_validate` over the emulator binding;
  - the check is structural; the diff is the same as for probe.
- **Emit:**
  - uses the latest worker probe if one succeeded; otherwise emit runs with the model only and a note;
  - an `emit_check` failure is a note;
  - the package hash is recorded in state as today.
- **Reuse:**
  - preparation is unchanged except for wording;
  - the check records mission events only: no `_final`, no truth mission predicate;
  - no package → skip with a note.
- **Interpret:**
  - qualification stays as a truth-free self-consistency note, never a stop;
  - repair sources contain only the device diff and the previous candidate's own files. No graded feedback, and no `remember` of evaluator probes or graded feedback.
- **Recovery:** `emulated_recovery.reconcile` keeps emulator replacement and the uncertainty discipline. It returns a device diff instead of committing a route itself. Its feedback carries no graded data.
- **`_evaluator_operation`:**
  - wrap `invoke()` in try/finally so that a raise before any device effect clears the pending row;
  - an emulator start in probe preparation is labelled actor `device_provider`, name `emulator_start`, not `benchmark_evaluate`.
- **Pre-I/O refusals** (digest or binding mismatch) inside adapter dispatch do not leave the run uncertain.
- **Worker-facing text:** update `STAGE_BOUNDARY` (`agents.py`), the interpret repair prompt (`_toolchain/interpret.py` around 313-318), the `benchmark_package_execute` description, the v2 probe and reuse objectives, and the `REPAIR_CONTEXT` evidence_policy. Drop any promise of evaluator diagnostics. Name instrument observations as device evidence.

**Tests:**
- A v2 probe check with a scripted worker produces no evaluator call (assert `_diagnose` and `truth_for_case` are not invoked during the stage loop, via mock).
- The probe handoff includes worker probes.
- Ground receives no file containing `passed`/`expected` keys from a grade.
- Repair DEFECTS.json contains only the device diff.
- An `_evaluator_operation` raise clears uncertainty.
- Rewrite the tests in `maps[4]` that asserted diagnostic routing or blocking.

- [ ] Failing tests → implement → full suite → commit.

### Task B2: single final evaluation through `benchmark.finalize`

**Files:** `benchmark.py` (new `finalize(case_id, run_dir, accepted, options)`), `emulated.py`/`emulated_evidence.py` (adapter `finalize`: mission acceptance over the evaluator-only package event journal, then `_final`; sealing), `registry.py` adapter interface, `configurator.py` (`_finalize` benchmark branch under `_evaluator_operation`, before sealing and cleanup), tests.

**Behaviour:**
- After reuse, the configurator calls `benchmark.finalize` once. It returns `equivalence {equivalent, basis: "evaluator", checks: [{id, passed}], diff: sanitized summary}`.
- The raw records and diff stay in the encrypted draft and the sealed evidence.
- The public summary contains check ids and pass state only, and only after the run's stages are over.
- `equivalent` requires the frozen final behaviour to pass. The fresh-mission result is recorded as a separate check.
- A missing package → `equivalent: false`, reason "no driver emitted". It is never a stop.
- A host fault → `null`/unscored per Ruling 6.
- Sealing (`emulated_evidence.finalize`) includes handoffs, notes hashes and the single final evaluation.
- The run completes regardless of the verdict.
- Old terminal-final-failure handling remains only for old-protocol records.

**Tests:**
- A known-good scripted package → equivalent true.
- A deliberately wrong package → equivalent false with failing check ids.
- `finalize` is called exactly once even across restart.
- No evaluator call occurs before reuse finishes (mock assertion).
- Use the existing scripted family fixtures. Tests needing Renode, Ghidra or Java keep their skip guards.

- [ ] Failing tests → implement → full suite → commit.

### Task B3: score, reports and suites under assistive/1

**Files:** `benchmark_support/evidence.py` (`regrade_v2`, `_gate_results`), `suite_reporting.py` (`_report_success`, `_recorded_success`, counts), `reporting.py`, `suites.py`/`suite_store.py` (settle and child_disposition categories), `benchmark.py` compare, tests (`test_suite_*`, `test_reporting`, `test_benchmark_score`, `test_benchmark_v2_evidence`).

**Behaviour:**
- For `assistive/1` evidence, regrade checks only the single final evaluation plus integrity: the final artifact equals the emitted package, and the hashes match.
- Success = `equivalent: true`.
- Suite summaries report equivalent drivers out of trials, unscored and stopped trials separately, and stop categories.
- Old-protocol reports keep their original gate logic and labels.
- `compare` refuses to compare across protocols.
- Suite child settlement understands `safety`/`integrity` categories and completed-but-not-equivalent runs.

**Tests:**
- A suite of three runs (equivalent, not equivalent, unscored) reports 1/3 equivalent, with the unscored run listed separately.
- Old-protocol fixtures score as before.
- A cross-protocol compare is refused.

- [ ] Failing tests → implement → full suite → commit.

### Task B4: legacy adapters, device-provider accessor, docs

**Files:** `cases.py`, `legacy.py`, `physical.py`, `ground.py` (legacy parts), `emulated.py` (`_session` uses a recipe-only accessor), `docs/verification.md` (a short note, without new numbers), `docs/implementation/benchmark-handoff.md` (a short note), tests.

**Behaviour:**
- Legacy `tq9` and `bme280`: stage checks become truth-free. Grading moves to the adapter `finalize`. Emit and reuse use worker-produced probe and capabilities state rather than evaluator-only state, and hand off with notes when that state is missing.
- A recipe-only accessor `device_recipe(case_id, run_dir, options)` returns only the emulator recipe. `_session` and recovery use it. Contracts are loaded only in `finalize` and `score`.
- Docs state:
  - the protocol change;
  - that the envelope split is follow-up work;
  - that worker workspaces are not read-isolated: Goose has no sandbox and Codex restricts writes only, so "no truth handed to stages" is about what is handed over, not what is reachable;
  - the new numbers series.

**Tests:**
- Legacy scripted runs reach finalize with a verdict.
- `_session` never receives contracts (mock).
- The full suite passes.

- [ ] Failing tests → implement → full suite → commit.

## End test (after all tasks; needs explicit operator authorization)

The default three-family suite (TQ9 v2, sampled-sensor, parameter-store; 3 × 3 = 9 trials):
- an actual model executor;
- fresh evaluator qualification, because the evaluator tree hash changes;
- Renode, Ghidra and Java installed.

**Pass:**
- all 9 trials reach a recorded equivalence verdict;
- no stop outside the stop list;
- no evaluator call before reuse in any run record.

**Measure:** equivalent drivers out of 9.

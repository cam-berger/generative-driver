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
3. **Device readings are device evidence.** Instrument (monitor) readings of the emulated device stay visible to workers, as any real device's readings would be, and are never graded during the run. Diagnostic stimulus and grading by the evaluator are removed from probe. (User, 2026-10-07: "wouldn't a normal device be visible?")
4. **The configurator takes no part in truth or emulator decryption** (user direction, 2026-10-07). The configurator is the main coordinating agent. The benchmark harness plays the operator: it decrypts what it needs, starts, owns and recovers the emulator, and hands the configurator a device binding plus a narrow device-control handle that exposes no truth. A separate evaluator grades the frozen driver after the run. The run spec carries no password. Part B is being re-planned around this; until then, Part A must not add any new truth or password use to the configurator. Workers reading files outside their workspace is accepted until a real sandbox exists; docs must not claim isolation.
5. **Legacy cases (`tq9`, `bme280`)** follow the same adapter interface: truth-free stage checks, grading in `finalize`. Where a legacy stage depended on evaluator-only state, the stage hands off what the worker produced, plus notes.
6. **Unscored final.** For generic runs, a host or operator fault while computing self-evidence stops as `host`/`operator` with `next_stage = "finalize"`, and resume computes it once. For benchmark runs, an unscored grade is a harness state (an event); it never stops the configurator run.
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
  - **Benchmark (`spec.case`):** do not call any evaluator. Leave the existing benchmark final path untouched until Part B. Part B (B5) replaces it: the run completes with `progress.equivalence = "pending"`, and the bench harness posts the verdict later through `record_evaluation`. Design `_finalize` so a pending branch can be added without restructuring.
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

## Part B: benchmark adapters and the bench harness

Design source: `/private/tmp/claude-501/-Users-cberg-Research-generative-driver--claude-worktrees-goose-new-device-trial-5c6f78/7370e2b2-589f-4122-9562-5ac716afd378/scratchpad/provider.json`.
- `designs[1]` is the clean design: roles, interfaces, lifecycle, recovery, grading, sealing, suites and old protocol, with file:line sites at `52eb018`.
- `maps[0..2]` map emulator ownership, harness entry points and evaluator identity.

Find sites by function name, because line numbers drift as Part A changes.

**Simplification of the source design (ruling).** The bench operator, device provider and evaluator are one harness process per trial or suite, not three. Reasons:
- The harness plays the operator, and the operator may hold the password.
- Splitting the provider out of the password holder is a later hardening step.

The evaluator runs inside the harness, after the configurator run is terminal, on a fresh emulator it owns.

**Process roles:**
- **Configurator daemon.** Coordinates stages and workers, and owns grants, uncertainty, leases, notes and handoffs.
  - It holds a device descriptor: binding, control endpoint file, image sha and instrument units.
  - It holds the sha256 of an evaluation token.
  - It never holds a password or password path, the recipe, contracts, drafts, or a grade before its run ends.
  - It imports nothing that decrypts truth.
- **Bench harness** (`benchmark_support/harness.py`, `python -m generative_driver.benchmark_support.harness`). One detached process, launched like `client.py`:
  - an argv list, with `start_new_session` on POSIX or `DETACHED_PROCESS` on Windows;
  - password file paths arrive on stdin and are never written to disk.

  It does the following, in order:
  1. admits the case (`require_calibration`);
  2. projects the recipe;
  3. starts and owns the emulator, and serves a control endpoint (`multiprocessing.connection` with an authkey, AF_UNIX/AF_PIPE as in `daemon.py`; endpoint file 0600);
  4. starts the configurator run with the descriptor;
  5. waits as a reconnecting client;
  6. confirms the emulator has stopped;
  7. grades once on a fresh emulator (via `reference_execute`);
  8. seals the evidence;
  9. posts the verdict with `record_evaluation`.

  Its state lives under a bench home that is a sibling of the configurator home, never inside it.

### Task B1: truth-free v2 stage observation; no evaluator in the stage loop

The device stays in-process for this task. It moves out in B3.

**Files:** `benchmark_support/emulated.py`, `ground.py`, `candidate_context.py`, `emulated_recovery.py`, `benchmark.py` (replace `check_stage` with `observe_stage`, returning Part A's `checked` contract: `artifacts`, `checks` as notes, `device_diff`, optional `stop`), `configurator.py` (case `_check` path and `_adapter_verdict` interim), `agents.py` `STAGE_BOUNDARY`, `_toolchain/interpret.py` repair prompt, tests.

**Behaviour:**
- **Probe:**
  - `_diagnose` is never called;
  - structural notes: capabilities presence, valid JSON and `validate_capabilities`;
  - a changed adapted model → `IntegrityError`;
  - the handoff contains the worker's observed probe artifacts (from `tool.finished` artifacts of this assignment), `capabilities.json`, the adapted model and the live-observation journal;
  - `device_diff` uses Part A's helper over this assignment's failed observed runtime results and `probe_diff` defects.
- **Ground:**
  - preparation provides the model, the worker probes and the instrument journal, with no graded projections;
  - tools: `interface_execute`, `probe_run`, `probe_diff` and `model_validate` over the emulator binding.
- **Emit:** uses the latest worker `interface-probe/1`; if there is none, it uses the model only, with a note.
- **Reuse:** the mission predicate becomes a note only, with no `_final`.
- **Interpret:**
  - qualification and validation are notes;
  - repair sources hold only the device diff plus the previous candidate's own files (no graded feedback, no evaluator probes).
- `benchmark.check_stage` no longer writes truth `stage_verdicts`.
- `reconcile` returns a device diff and no longer commits a route by itself. Part A2's atomic accept-and-revise commits it. Its emulator replacement and uncertainty discipline stay.
- Worker-facing wording drops every promise of evaluator diagnostics and names instrument readings as device evidence:
  - `STAGE_BOUNDARY`;
  - the interpret repair prompt;
  - the `benchmark_package_execute` description;
  - the v2 probe and reuse objectives;
  - the `REPAIR_CONTEXT` `evidence_policy`;
  - `bench/README.md:49`.

**Tests:**
- Mocks prove that neither `_diagnose`, `_final` nor `truth_for_case` is called from any stage preparation or observation except emulator start (which B3 removes).
- The probe handoff includes the worker probes.
- Ground inputs contain no grade fields.
- Repair `DEFECTS.json` holds only the device diff.
- Rewrite the tests in `understand.json` `maps[4]` that asserted diagnostic routing or blocking. Keep the safety assertions verbatim.

- [ ] Failing tests → implement → full suite → commit.

### Task B2: instruments and an in-process device provider

**Files:** new `benchmark_support/instruments.py` (`family_for` and `public_observation` moved from `emulated.py`), new `benchmark_support/device_provider.py` (class `DeviceProvider(case_id, image, recipe, renode, private_dir)`), tests.

**Behaviour:**
- `DeviceProvider` is constructed from the recipe only. It never takes a password or a truth object.
- Methods:
  - `start() -> descriptor`;
  - `status()`;
  - `observe()`, returning `instrument-observation/1`: projected values and units only, with no monitor commands, addresses or raw reads;
  - `window()`, a context manager for the paused-between-calls policy, taken from `run_call`;
  - `replace(expected_session_id, evidence_ids, dispatch_ids)`, ported from `replace_owned` without `_truth`/`_private`, returning an `emulator-recovery/2` receipt;
  - `release(session_id)`;
  - `stop() -> {stopped, confirmed}`.
- Recovery proof bodies go to a device journal file in the provider's private dir.
- `emulated._session`, `worker_tool`, `package_execute`, `recover_emulator` and `cleanup` delegate to a `DeviceProvider` built by `device_recipe()`. `device_recipe()` is `_truth()` reduced to returning only the recipe, and is still in-process in this task.

**Tests:**
- A provider built from a fake recipe and fake session starts, observes (no raw monitor fields), replaces (distinct session, baseline compared) and stops.
- `observe()` output has no keys beyond the schema.
- Existing emulator-recovery tests pass through the provider.

- [ ] Failing tests → implement → full suite → commit.

### Task B3: harness process, control endpoint, configurator device client

**Files:** new `benchmark_support/harness.py`, new `src/generative_driver/device_client.py` (package level, no truth imports), `configurator.py` (start validation, `_tool` live-tool path, `_prepare` binding source, finally block), `benchmark.py` `run`, `mcp.py` `driver_benchmark_run`, `cli.py`, tests.

**Behaviour:**
- **The harness:**
  - is launched by `benchmark.run` for actual-agent v2 cases;
  - receives password file paths on stdin;
  - runs `require_calibration`;
  - builds a `DeviceProvider` from `device_recipe()`;
  - serves the control endpoint with a control authkey. Its methods are `status`, `observe`, `set_running`, `replace` and `release`. `stop` and `shutdown` are owner-only.
  - creates an evaluation token, keeps the token in memory and sends only its sha256 to the daemon;
  - calls daemon `start` with `device` (descriptor `device-descriptor/1`: binding, session_id, image_sha256, instrument units, control endpoint file), `admission` (`calibration_evidence_sha256`) and `evaluation_token_sha256`.
- **`benchmark.run`:**
  - returns `{trial_id, run_id}`;
  - `--wait` follows the trial to its verdict.
- **The daemon `start`:**
  - refuses `evaluator_password_file`, any `password*` key and `renode` in `case_options` or `case.options`;
  - for v2 actual-agent cases, requires a device descriptor whose `image_sha256` equals the pinned `firmware.bin` hash;
  - compares the admission sha with the public pin's `calibration.evidence_sha256`, replacing its own `require_calibration` call;
  - claims the descriptor binding;
  - `progress.device = {session_id, binding, receipts: []}`.
- **`device_client.open_device(descriptor)`** returns a client with `status`, `observe`, `window` and `replace`, and raises a typed `DeviceUnavailable`.
- **`_tool`, for every live tool on a provided device:**
  1. runs all refusable checks first (package path and digest, binding, grants);
  2. marks the dispatch effectful;
  3. calls `observe()` for the before reading;
  4. runs the call inside `with device.window()`;
  5. calls `observe()` for the after reading;
  6. records `before_observation`/`observation` tagged `source: "instrument"`.
- A provider unreachable before dispatch → a refused-before-open result: no uncertainty, an operator stop "device provider unavailable".
- The configurator's finally block no longer seals or stops the emulator. It calls `release()`. A release failure is an event, and the run stays completed unless an effect is uncertain.

**Tests:**
- Spawn the harness with a fake provider (a fake recipe and session factory injected by environment or argument for tests only) and a scripted executor. The run completes, and runs.sqlite3 contains no password path.
- The daemon refuses a password key in `case_options`.
- An image sha mismatch → integrity refusal.
- A provider killed before dispatch → operator stop with no uncertainty.
- Live tool results carry instrument observations.

- [ ] Failing tests → implement → full suite → commit.

### Task B4: recovery through the device handle; the configurator holds no evaluator code

**Files:** new `src/generative_driver/device_recovery.py` (`unresolved_outcomes`, `diagnostic_faults` and `reconcile`, moved from `emulated_recovery.py`), `configurator.py` (delete `_evaluator_operation`, the v2 probe-prepare wrapper, the check wrapper, the `terminal_final` branch for new runs, sealing, the `evaluator_workspace` branch; keep `_managed_probe` only if B7 keeps it for bme280), `emulated.py` (delete `_OWNERS`, `_truth`, `_session`, `cleanup`, `package_execute`, `worker_tool` and `recover_emulator` once nothing calls them), tests.

**Behaviour:**
- `reconcile` is gated on `spec.device.kind == "provided-emulator"`. It calls `device.replace(...)`.
- In one transaction it claims the new binding, updates `progress.device`, accepts the partial handoff, routes revision+1 to interpret, deletes `pending_effects` and clears `uncertain`.
- `DeviceUnavailable` or a failed replace → `emulator.recovery_failed`, uncertainty retained, safety stop. The failed write is never re-executed.
- Daemon restart or resume checks `device.status()`. A session mismatch with `progress.device` is an operator stop with reason "Device session changed; attach a verified device". A new `attach_device({run_id, token, device})` RPC is accepted only while the run is stopped and not uncertain.
- A static check test: `configurator.py` and `device_*.py` import nothing from `benchmark_support.emulator`, `truth`, `calibration`, `emulated_evidence` or `reference_calibration`.

**Tests:**
- Port the uncertain-write recovery tests verbatim in their safety assertions:
  - the pending dispatch ids;
  - the failed write executed exactly once;
  - the binding re-claimed atomically.
- A crash between replace and commit → stays uncertain on restart.
- The import-boundary test passes.

- [ ] Failing tests → implement → full suite → commit.

### Task B5: post-run grading in the harness; `record_evaluation`; equivalence surface

**Files:** new `benchmark_support/evaluator.py` (`reference_execute` moved from `emulated.py`; `_private`/draft and sealing moved from `emulated_evidence.py`), `harness.py` (finish sequence), `configurator.py` (`record_evaluation` RPC; A4's benchmark branch), `evidence.py` (`_validate_payload_v2`, `benchmark-run-evidence/2`), tests.

**Behaviour:**
- **After the run is terminal and the device stop is confirmed,** the harness grades once:
  1. verifies the snapshot and pin;
  2. copies the emitted package from the accepted handoff and re-hashes it;
  3. takes `capabilities.json` from the probe handoff at the emit revision, or the latest probe, and re-hashes it;
  4. unlocks truth;
  5. runs the final phase via `reference_execute` on a fresh emulator;
  6. runs `validate_records`.

  `fresh_worker_mission` is computed from the recorded package events as a secondary check, and `equivalent` = the final verdict passed.
- **Special outcomes:**
  - no package → status `no-driver`, equivalent false;
  - no capabilities → equivalent false, reason "no capability mapping";
  - an evaluator host fault → `unscored` (an event only), retried by an explicit `harness resume`;
  - a final already in the draft for (run_id, frozen sha) → the summary is rebuilt and never regraded.
- **`record_evaluation({run_id, token, equivalence})` on the daemon:**
  - accepts only a completed, not-uncertain run with `progress.equivalence == "pending"`;
  - requires sha256(token) to equal the spec's;
  - requires `frozen_artifact_sha256` to equal the latest accepted emit artifact, or null when there was no emit;
  - writes a graded record once (verdicts id `f"{run_id}:final"`). An identical repeat is idempotent; a different record is refused.
- **Public output:**
  - `equivalence/1` holds check ids with passed flags and a diff `[{id, reason}]`;
  - expected values, tolerances, witnesses and raw records stay sealed.
- **Sealing:** `benchmark-run-evidence/2` under the trial dir, holding:
  - protocol, case pin and snapshot;
  - the accepted artifacts, including notes.json hashes;
  - the final evaluation;
  - the provider receipt and journal hashes;
  - the mission check.

  Stopped trials get a seal of the handoffs only.

**Tests:**
- A known-good scripted package → equivalent true.
- A wrong package → false, with failing ids.
- A forged token → refused.
- A repeat → idempotent.
- A harness crash then resume → no regrade.
- No evaluator call before the run is terminal (mock).

- [ ] Failing tests → implement → full suite → commit.

### Task B6: suites driven by the harness

**Files:** `benchmark_support/suites.py`, `suite_store.py`, `configurator.py` (delete `_run_suite`/`_spawn_suite`; `suite_start` freezes and stores only; add `suite_reserve`/`suite_attach`/`suite_settle` with a suite token), `harness.py` (suite loop), `benchmark.py` suite commands, tests.

**Behaviour:**
- `freeze_suite` accepts only `ghidra_home`, `java_home` and `scoped_tool_approval`. Password files and renode go on the harness's stdin. It adds `scheduler: "harness/1"` and `harness_token_sha256`.
- The harness loops over the trials: reserve → provide device → start child → attach → wait → stop → grade → `record_evaluation` → settle.
- The daemon reads child status and the equivalence record itself, so the harness cannot inject an outcome.
- `child_disposition` advances only a completed child with a graded or no-driver record. It stops on unscored results and on safety, integrity, operator or host stops.
- Old frozen suites (with password files or no scheduler) are read-only, and their resume is refused with an operator reason.
- `comparison_identity` gains `protocol`. Summaries count equivalent out of planned.

**Tests:**
- A three-child suite with a fake provider and scripted executors settles 1 equivalent / 1 not / 1 unscored, with the unscored child listed.
- The suite tables contain no password.
- An old suite's resume is refused.

- [ ] Failing tests → implement → full suite → commit.

### Task B7: score, reports and compare under assistive/1

**Files:** `evidence.py` (regrade `benchmark-run-evidence/2`), `suite_reporting.py`, `reporting.py`, `benchmark.py` compare, tests.

**Behaviour:**
- New reports are `benchmark-report/3` with `protocol: "assistive/1"`. Success = `equivalent: true`.
- Regrade checks:
  - the single final;
  - the final artifact equals the accepted emit;
  - the snapshot identity.
- Old reports (`benchmark-report/2`, `benchmark-run-evidence/1`) keep their gate logic.
- `compare` refuses to compare across protocols.

**Tests:**
- Old fixtures score as before.
- A new fixture regrades.
- A cross-protocol compare is refused.

- [ ] Failing tests → implement → full suite → commit.

### Task B8: legacy tq9 and bme280 under the same split

**Files:** `cases.py`, `legacy.py`, `physical.py`, `ground.py` (legacy), `harness.py`/`evaluator.py` (legacy grading method `recorded-reuse`), tests.

**Behaviour:**
- **Legacy tq9:**
  - stage observation is truth-free;
  - the device is provided by the harness (a Renode session from the legacy recipe);
  - it is graded post-run from the recorded reuse measurements against the truth reuse goal (`method: "recorded-reuse"`).
- **bme280:**
  - keeps the operator's physical binding, with no descriptor;
  - its stage checks become truth-free; the evaluator `_managed_probe` I/O during stages is removed;
  - after the run, the harness grades the recorded reuse readings against the operator reference and the truth tolerances. The harness never touches the board.

**Tests:** legacy scripted runs reach a recorded verdict; the import-boundary test still passes.

- [ ] Failing tests → implement → full suite → commit.

### Task B9: skills, docs and wording

**Files:**
- `docs/design.md`: extend A4's amendment with the harness/device split;
- `docs/verification.md` and `docs/implementation/benchmark-handoff.md`: short notes, without new numbers;
- `bench/README.md`;
- the skill text in all synced copies (keep them identical; the sync test guards this);
- `docs/goose.md` and `docs/codex.md` where they describe benchmark runs.

**Behaviour:** the docs state:
- the protocol change (`assistive/1`, a new numbers series);
- the harness holds the password; the configurator holds none;
- device readings are visible to workers;
- workers are not read-isolated until a real sandbox exists;
- a fresh native calibration is required before the end test, because the current calibration is already stale at `52eb018`;
- historical sealed evidence needs a preserved checkout to regrade.

**Tests:** the distribution and skill-sync tests pass, and the full suite passes.

- [ ] Implement → full suite → commit.

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

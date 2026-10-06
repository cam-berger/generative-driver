# Transaction coverage and checked emulator recovery

> For agentic workers: execute with superpowers:subagent-driven-development and test-driven-development, one behavior slice at a time.

Goal: make the known pending-transaction defect observable on every store evaluation and let verified disposal of an owned emulator unlock bounded model repair.

Architecture: the accepted six-stage workflow and four public test seams in ../design.md govern this change. Case semantics and independent scenario evidence belong to the benchmark; candidate operation composition belongs to interpretation; process ownership, uncertainty and revision routing belong to the configurator. Goose and Codex use the same installed interface.

Spec: the user approved the three proposals and their integration in chat, then instructed implementation and verification. This document records that approved amendment and its execution slices.

Tech stack: Python 3.11+, existing CLI/MCP, declarative interface runtime, native Renode and encrypted evaluator assets.

## Global Constraints

- All runs have six stages: acquire, interpret, probe, ground, emit, reuse.
- One stage is active per run. Worker reports, accepted handoffs and evaluator verdicts are distinct.
- Functional compatibility means observable outputs and effects; candidate source-code equality is not a grading criterion.
- Only the evaluator receives groundtruth passwords. Candidate workers receive neither password nor plaintext answer keys.
- Core Python runs on native macOS and Windows 11+. Optional native analysis tools stay optional.
- Never silently retry an uncertain device write. Model faults alone route to interpretation; host and operator faults retain their classifications.
- Diagnostic repair is bounded before frozen final grading; final evaluation failure is terminal.
- Preserve frozen prior experiments and reports. Work on the existing fix/pipeline-discovery-repair branch; publish no private research artifacts.
- Implementation and deterministic/native verification are authorized. This work does not launch paid model trials or access physical devices.

## Review focus

1. Pending edits in the same or another bank must not leak into a committed update (Task 1).
2. A timeout, malformed reply or missing terminator cannot satisfy an expected rejection (Task 1).
3. A forged recovery result, lost ownership, process-stop failure or unverifiable new state must leave dispatch blocked (Task 2).
4. Cancellation, stale assignment credentials, duplicate recovery and controller restart must not repeat the failed write or consume an unrecorded repair (Task 2).
5. A release containing changed grading rules must refuse stale qualification and retain reproducible encrypted evidence (Task 3).

### Task 1: Required transaction behavior and scenario coverage

Files: src/generative_driver/benchmark_support/candidate_context.py, parameter_store.py, emulated_actions.py, behavior.py, calibration.py; relevant tests/test_parameter_store_family.py, test_benchmark_behavior.py, test_benchmark_family_calibration.py, benchmark_family_fixtures.py; bench/README.md and bench/groundtruth/README.md only as needed. Keep controller recovery out of this task. Evaluator authoring artifacts must stay in a fresh private directory; retain exact reproduction instructions for Task 3.

Interfaces: retain task_contract(), family contract()/build_plan()/observations(), execute_plan(), validate_records(), capability binding and public run/score/compare. Add only minimal scenario/check metadata or observations needed for mandatory state coverage and complete rejection validation. The runtime executes candidate model steps; it does not receive case-specific repair code.

- [ ] Write a failing public evaluator/runtime regression: the idle-only update driver currently passes ordinary sequences but must fail stage→update in the same bank and another bank. Use hand-checked starting and ending values; observe all committed cells, pending state and generation.
- [ ] Run the focused test and retain the expected failing output.
- [ ] Make the public store update contract explicit: discard any pending uncommitted edits, commit the requested value, preserve all other committed cells, and finish with no pending transaction. Define bounds/preconditions consistently with existing firmware evidence. The agent must derive the command sequence itself.
- [ ] Extend independent diagnostic/final episodes through the existing private phase mechanism: idle update, pending same-bank update, pending different-bank update, stage/commit, stage/abort and rejection behavior. Use separate final values/sequences and require named scenario coverage. Keep expected values and oracle code evaluator-owned. Test a correct composed reference and the idle-only mutant.
- [ ] Add a failing rejection regression proving that an incomplete frame/timeout cannot count as a correctly rejected command; implement the smallest evidence distinction and state-invariant check required. Preserve host failure attribution and current effect uncertainty guards.
- [ ] Run affected tests, then the full source suite once. Record RED/GREEN commands and results, source-versus-installed skips, private authoring changes still required and files touched. Commit only public task files, self-review and report.

### Task 2: Trusted emulator reconciliation through the existing repair route

Files: src/generative_driver/configurator.py; src/generative_driver/benchmark.py; src/generative_driver/benchmark_support/emulated.py, native.py, candidate_context.py and a focused internal recovery module if needed to keep the already-large controller readable; tests/test_service_repair.py or a focused tests/test_emulator_recovery.py, test_service.py, test_configurator.py. Consume Task 1's normal diagnostic outcome/feedback without teaching the controller transaction semantics.

Interfaces: add a narrow internal recovery hook reached through the benchmark adapter. The configurator initiates it only after the relevant diagnostic worker/evaluator operation has stopped and only for owned emulation before final grading. The hook establishes trusted process disposal, a distinct new session and independently verified initial state; it returns a bounded receipt linked to preserved evidence. It is not an agent tool and cannot be supplied by candidate output.

- [ ] Write and run a failing public controller/service regression: a model discrepancy after device I/O leaves uncertainty, then owned emulator replacement enables the existing bounded interpret revision, followed by checked stages.
- [ ] Preserve failed raw exchanges, observations and candidate artifacts before recovery, including when the controller currently stops before check_stage(). Carry only permitted diagnostic evidence into the sealed repair history. Classify the actual failure using runtime evidence; worker prose alone cannot prove a model fault or safe effects.
- [ ] Implement verified quiescence/disposal/replacement at the adapter seam. Require retained owned process handles, successful stop confirmation, new session identity and independent startup observations. Clear the live dispatch block only after trusted verification, recording a durable recovery event and the original unresolved attempt. Do not convert the old failed command into success.
- [ ] Route confirmed model discrepancies through the existing revision counter, max_revisions, elapsed budget, _route(), immutable accepted history and normal revalidation. A host/operator failure or unverified reset stays blocked. Recovery failure must clean up any newly started owned session and preserve uncertainty.
- [ ] Add and run targeted regressions for cancellation during recovery, stale worker tokens, exhausted repair budget, restart/repeated recovery, lost process ownership, failed stop/reset verification, physical binding and terminal final grading. Ensure failed writes are never replayed automatically.
- [ ] Expose recovery count/evidence via existing events/result reporting, labelled as emulator recovery and distinct from human intervention. Run focused tests and full source suite once, record RED/GREEN evidence, commit and self-review.

### Task 3: Release assets, installed verification and native qualification

Files: packaged benchmark case/groundtruth/evaluator resources only through existing authoring/release tools; docs/design.md, docs/verification.md, docs/implementation/benchmark-handoff.md and a new transaction-recovery-verification.json. Use a fresh private verification directory, leaving all historical experiments untouched.

Interfaces: consume the committed Task 1 and 2 runtime/evaluator changes. Rebuild/pin affected evaluator assets using existing build/calibrate/admission tools; preserve firmware source/image bytes unless a concrete defect requires a separately recorded change. Independent references must meet the new task semantics; add the idle-only transaction mutant to native qualification with expected failing checks derived before the run.

- [ ] Refresh private store scenario contracts and reference submissions, including complete error framing and mandatory pending-state episodes. Include named coverage in saved grade/report output. Bump the affected public case/evaluator identities and regenerate commitments; never patch encrypted release bytes manually.
- [ ] Run native reference and mutant qualification for every family whose shared evaluator identity changed. Verify every saved grade reproduces and authenticated admission succeeds. Preserve failed qualification attempts and their causes.
- [ ] Build wheel/sdist, install clean Python 3.11 and 3.13 environments and run the full tests from outside the checkout with GD_TEST_WHEEL configured, plus installed setup smoke and archive gates. macOS is locally executable; Windows checks use the existing CI matrix and must be reported as unexecuted unless actually run.
- [ ] Exercise trusted recovery against a real owned native emulator with controlled runtime failure, retaining process lifecycle and independent state evidence. Label this deterministic/native verification, not a model result.
- [ ] Record versions/hashes, counts, TDD evidence, source/installed/native outcomes and any real limitations in the verification index. Update accepted design and handoff with current status and next separately frozen model trial (gpt-6.1-sol, medium).
- [ ] Run public-artifact privacy checks, documentation checks and git diff --check. Commit verification/documentation only after evidence exists; report final status and unresolved limitations. No push, PR or paid model run in this task.

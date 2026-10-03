# Fresh Reuse Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** End every driver workflow at independently qualified fresh reuse.

**Architecture:** Keep the existing configurator and built-in adapters. Reduce their shared workflow, stable case contracts and public scoring to the same six stages; independently qualify the changed assets before admission.

**Tech Stack:** Python 3.11+, unittest, MCP, cryptography, native Renode/Ghidra/Arm GCC.

**Spec:** `docs/superpowers/specs/2026-10-03-reuse-endpoint.md`

## Global Constraints

- Native macOS and Windows 11+ core; no new required dependencies.
- Stages are exactly acquire, interpret, probe, ground, emit, reuse.
- Final acceptance requires a fresh worker using the emitted package and independently graded device behavior.
- Private evaluator material never enters candidate assignments or public plaintext.
- Keep historical measurements recoverable without relabelling them as current observations.
- No model inference, physical operations, merge or push belongs to this refactor.

## Review Focus

- Saved progress naming an unsupported stage must stop explicitly without dispatch or silent completion (Task 1).
- A failed fresh mission or frozen final grade must prevent overall success (Tasks 1–2).
- Earlier interpretation/probe repairs must remain bounded and preserve consumed attempts (Tasks 1–2).
- Old evaluator records and unknown scenarios must fail admission before native/device/worker startup (Tasks 2–3).
- Public packages and current documentation must present one consistent workflow and retain no obsolete instructions or private plaintext (Tasks 3–4).

### Task 1: Shared workflow endpoint

**Files:** `configurator.py`, `benchmark.py`, `reporting.py`, `mcp.py`, `setup.py`, and focused controller/service/report tests under `tests/` (all source paths beneath `src/generative_driver/`).

**Interfaces:** Produce the six-element `STAGES` contract, six-stage progress/assignment grants, completion after reuse and safe incompatible-state handling. Adapter and evidence internals are consumed unchanged until Task 2.

- [x] Write and run a RED controller test using the existing public worker fixture. Assert that the requested stages equal `('acquire','interpret','probe','ground','emit','reuse')`, completion follows accepted reuse, and the reuse assignment receives only the emitted package. Add explicit unsupported saved-progress and failed-reuse regressions.
- [x] Reduce both stage inventories and tool grants; remove the extra routing/cycle limit branch and progress/limit fields; complete with `Fresh reuse accepted`. Restrict ground and device authorization to their retained stage roles. Update smoke stage inventory, run objective, report stage/required-gate handling, client metadata and schema projections directly touched by this contract.
- [x] Run focused controller/service/report/CLI tests; update fixtures that expected an additional assignment, keeping actual repair, cancellation and unknown-fault assertions. Run the full suite once and report remaining Task 2-dependent failures by name.
- [x] Commit only Task 1 paths and write RED/GREEN/full-suite evidence to the task report.

### Task 2: Stable case execution and independent grading

**Files:** `benchmark_support/{cases,physical,legacy,registry,emulated,emulated_evidence,tq9_v2,sampled_sensor,parameter_store,evidence,suite_reporting,suites,scenarios}.py` and their focused tests. Remove obsolete scenario-only code once no caller needs it.

**Interfaces:** Keep adapter call signatures. Registry pins use `original`; emulated behavior exposes only diagnostic/final phases. Reports and sealed offline grading use six acceptance gates and final fresh reuse. A failed final remains terminal.

- [x] Write/run RED tests that a six-gate sealed evidence fixture regrades successfully without any extra stage record, that failed fresh reuse/final behavior fails, and that unknown scenarios are rejected before startup. Pin public report/aggregate absence of obsolete cycle/detection fields and preserve honest failure/time/usage accounting.
- [x] Remove firmware switching, later-cycle execution, special assessments and scenario journals. Simplify legacy/physical adapters to one discovery-to-reuse pass. Make v2 adapters use one stable original image, reset-delimited diagnostic/final plans and package-only mission evidence. Keep pre-final model repairs and host/operator/unknown faults distinct.
- [x] Update registry/suite default selection to `original`; remove obsolete scoring fields and simplify offline overall policy to six gates plus final behavior. Reject incompatible manifests/pins instead of inferring missing grades. Update or remove retired-feature tests while preserving independent package mission, mutation, uncertainty and scoring coverage.
- [x] Run focused changed modules and full suite, report asset/calibration-dependent failures for Task 3, then commit owned paths and write evidence.

### Task 3: Case assets and native evaluator qualification

**Files:** `benchmark_support/{calibration,reference_calibration,asset_build,evaluator_cli,authoring}.py`, matching calibration/build tests, `resources/bench/cases`, `resources/bench/groundtruth`, and `resources/bench/suites/development-pilot.json`.

**Interfaces:** Qualify two distinct references per original family with diagnostic/final behavior and meaningful mutants. Authenticated encrypted evidence, public manifests and current evaluator identity must agree. The suite expands three families times three repetitions to nine distinct slots.

- [x] Write/run RED inventory/admission tests for one original image, two retained phases, meaningful model/capability mutants, exact new versions and nine-trial suite expansion. Assert stale record refusal and complete executed input/evidence coverage.
- [x] Simplify calibration recipes/validators to the stable case inventory, preserving source/input/tool/analysis hashes, two references and mutant rejection evidence. Migrate evaluator-owned input inventories privately; delete unused binary variants and supplementary resources. Reseal cleaned truth with evaluator passwords and update manifest pins and versions.
- [x] Freeze evaluator code, run actual native reference/mutant calibration and retain raw private evidence. Publish only safe hashes/counts and encrypted qualification assets. Confirm all current scenarios admit, all saved reference grades regrade identically and old calibration fails current identity checks.
- [x] Run focused tests and full source suite; commit only matching current source/assets/tests and write evidence.

### Task 4: Documentation, distribution and final integration

**Files:** `README.md`, `CONTEXT.md`, `docs/{design,setup,codex,goose,support,verification}.md`, `bench/{README.md,groundtruth/README.md}`, current client skills, applicable plugin metadata and public implementation records.

**Interfaces:** A single six-stage description across installed CLI/MCP/skills and source documentation, with current verification scope and complete reproducible benchmark commands.

- [x] Update current instructions, benchmark metrics, default suite and final acceptance explanation. Replace obsolete public plans/verification/baseline documents with concise current records; preserve old observations through Git instead of editing measurements. Update the agent-facing domain and skill text using the same glossary.
- [ ] Build wheel and sdist; run complete source and installed-wheel tests on available Python 3.11/3.13 environments, replay, case admission/discovery, offline score/compare and distribution/document audits. Check archived bytes contain no private material or obsolete public instructions. Do not run model trials or physical devices.
- [ ] Record actual outcomes and limitations, perform one whole-branch independent review, address load-bearing findings with covering tests, and commit the coherent final result. Retain review/TDD evidence privately and leave the feature branch ready for user integration.

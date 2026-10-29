# TQ9 replacement evaluator implementation plan

Historical implementation plan for the preserved r2 release. Current-main integration retains its driver semantics through the newer `assistive/1` harness; use the [supported extension guide](../../extending-drivers-and-mcp.md) for current interfaces and the [verification record](../../../bench/evidence/2026-10-29-driver-contract-mcp-verification.json) for measured scope.

> **For agentic workers:** Use superpowers:executing-plans for core integration and test-driven-development for each behavior change. Independent evaluator authors/reviewers work only in protected benchmark directories.

**Goal:** Independently author and qualify a new password-protected TQ9 evaluator, then complete a fresh six-stage generated-MCP run while preserving the locked predecessor and every failed attempt.

**Architecture:** Register `tq9-v3` with case/evaluator version `1` and reuse the bounded TQ9 family engine. Author new private oracle inputs from preserved firmware/sensor sources, retain the same firmware only after hash-matching rebuild, and freeze two reference drivers, separate diagnostic/final vectors and nine expected mutant failures before measurement. Install changed core bytes in a separate permanent runtime; leave `tq9-v2`/version `5` and existing qualified runtimes unchanged.

**Tech stack:** Existing Python/MCP runtime, strict schema-2 case registry, native Arm GCC/Ghidra/Java/Renode, encrypted evaluator evidence.

**Spec:** The user's explicit replacement/evaluation request, [accepted design](../../design.md) and [driver-contract/MCP design](../specs/2026-10-28-driver-contract-mcp-design.md). Execution is already authorized; this plan records implementation choices without another approval gate.

## Global constraints

- Keep source research, old encrypted bundles, old code installations, previous reports and failed attempts unchanged.
- The evaluator alone handles plaintext answers and password values; passwords are generated directly into protected permanent files and never printed.
- New code is test-first through public seams. Native emulation, scripted fixtures, replay and actual model work retain distinct labels.
- No native qualification before independent design audit and frozen private inputs. No model run before complete authenticated native qualification.
- Two bounded diagnostic repairs; frozen final failure is terminal. Preserve uncertain writes and use only existing owned-emulator recovery rules.

## Frozen decision rule

This is a development conformance result, not a statistical success-rate estimate. Admission requires exact source/image provenance, disjoint diagnostic/final stimuli, both distinct references passing both phases, all nine predeclared mutants failing their exact intended semantic checks, current implementation/dependency identity, and a valid encrypted release. Host faults, missing observations and unexpected mutant failures fail qualification.

End-to-end acceptance requires all six gates, a fresh agent using the frozen generated catalog, complete owner-journal matching of actual MCP calls, independent final behavior acceptance and matching authenticated offline regrade. Otherwise report the exact terminal stage/cause and retain its evidence.

## Review focus

1. A new ID must not relax historical version or calibration checks.
2. TQ9 aliases must share task and independent observation semantics, including temperature reference and before-observation policy.
3. Correct-looking references must not define their own oracle; expectations come from source/physical stimulus and independent monitor channels.
4. Mutations and expected failure IDs must be frozen before native results; wrong units/sign/byte order/CRC/state/effects must be observable.
5. The new runtime must not invalidate access to previously qualified cases in the old runtime.

### Task 1: Independent authoring and audit

**Files:** protected `evaluator/authoring/tq9-v3-authoring/` and `tq9-v3.password` under the evaluator-owned benchmark home.

- [x] Record intent, source hashes, oracle independence, disjoint phases and exact reference/mutant inventory.
- [x] Write behavior tests before private builder logic; retain RED/GREEN results and notices.
- [x] Audit with separate measurement and integration perspectives; resolve blockers and freeze the private input manifest before native execution.

### Task 2: Registered replacement profile

**Files:** `benchmark_support/registry.py`, `candidate_context.py`, `emulated.py`, `reference_calibration.py`; public registry/workflow tests.

- [x] Observe a failing public case-selection test for `tq9-v3`/version `1`; register it without changing the predecessor.
- [x] Observe failing TQ9 task/observation/calibration routing behaviors; centralize closed family classification and pass each regression.
- [x] Exercise an actual-MCP controlled workflow for the new profile and retain historical workflow regressions.

### Task 3: Isolated release and native admission

**Files:** new release/runtime, case-bundle and qualification directories under the evaluator-owned benchmark home.

- [x] Build/install outside the checkout; run all 521 installed regressions including actual-MCP fixtures and release-archive gates; verify exact source/runtime identities. Preserve the first 520-test runtime and unused preliminary build.
- [x] Rebuild and analyze the source-pinned image, then run both references in both phases and all nine mutants through native Renode. Repeat all 13 grades after the generic worker-instruction clarification.
- [x] Independently audit measured qualification and 74 raw reset/readbacks, preserve every failed qualification attempt, and publish a new immutable manifest/encrypted bundle only when all frozen gates pass.

### Task 4: Actual-agent generated MCP reuse

**Files:** new durable owner home and output/archive directories under the evaluator-owned benchmark home.

- [x] Run Codex `gpt-6.1-sol/high`, original scenario, seed 0, 10,800-second budget, with explicit emulator-only approval.
- [x] Retain all diagnostic repairs and terminal attempts. Fresh model attempt 2 passed all six stages, four generated MCP calls and 12/12 frozen final checks with zero repairs/recoveries; blocked attempt 1 and all its revisions remain archived.
- [x] Export report, encrypted sidecar and unchanged reusable package; verify complete MCP call order/pins and offline scoring after relocation. Retain the corrected relocation-verifier assertion as a separate harness failure.
- [x] Verify old locked-bundle hashes unchanged; update the permanent runbook and case/runtime availability accurately. Independent final audit regraded 12/12, verified all 23 accepted artifact hashes and four MCP calls across six fresh Codex threads. Corrected the independently discovered runbook bundle-selection issue and verified public case selection before/after without inference or devices.

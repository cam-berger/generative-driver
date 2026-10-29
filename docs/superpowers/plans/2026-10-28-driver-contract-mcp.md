# Driver Contract and MCP Implementation Plan

Historical implementation plan for the preserved r2 release. Current-main integration retains its driver semantics through the newer `assistive/1` harness; use the [supported extension guide](../../extending-drivers-and-mcp.md) for current interfaces and the [verification record](../../../bench/evidence/2026-10-29-driver-contract-mcp-verification.json) for measured scope.

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task, with superpowers:test-driven-development at every behavior change. Steps use checkbox syntax for tracking.

**Goal:** Emit standardized declarative drivers and reusable device MCP servers, provide a consistent Python application-tool Interface, and finish one real-agent benchmark through generated MCP reuse.

**Architecture:** Deepen three Modules: contract execution, managed device access, and tool authoring. Reuse the existing bounded engine, configurator ownership and independent benchmark observation Adapters. Device and application MCP tools share one registration/result Adapter.

**Tech stack:** Python 3.11+, installed MCP 2.x SDK, SQLite, native IPC, existing declarative engine and native benchmark tools.

**Spec:** [Driver contract/MCP design](../specs/2026-10-28-driver-contract-mcp-design.md).

## Global constraints

- Native macOS and Windows 11+; optional dependencies load at use time. Offline inspection/replay remains portable.
- Preserve existing uncommitted work, historical packages/trials, source research artifacts and evaluator credential separation.
- One failing public behavior test, implementation and observed passing result at a time. Use literal expected outputs and independent device fixtures.
- The configurator owns access, permissions, dispatch identity and uncertain effects. A transport failure never silently resends a write.
- New device logic is declarative with registered shared runtime capabilities. Application orchestration may use the supported Python SDK.
- Emit and reuse stay within the existing six stages. A worker claim or internal generic executor does not establish generated MCP reuse.
- Work on the current development branch with its existing changes. No commits, branch resets or unrelated cleanup are required by this task.

## Review focus

1. A disconnected client after a completed write must read the saved invocation result, not send the write again (Task 4).
2. A guard observation can open a device yet reject the requested write; its result must distinguish these effects (Tasks 2 and 4).
3. A changed contract beside an unchanged model/catalog must fail before dispatch (Tasks 1 and 3).
4. A higher-level tool must retain child caveats, partial progress and the original precondition explanation (Task 5).
5. A generic or forged reuse event must not satisfy a new MCP-profile benchmark gate (Task 6).

## File responsibilities and shared Interfaces

- `interface_runtime/contracts.py`: validate/compile the authoritative public/protocol pair; project supported historical executable models conservatively; derive public schemas/caveats and operation metadata.
- `interface_runtime/driver.py`: execute contract operations using the bounded engine, evaluate guards from fresh observations and validate public results.
- `generative_driver/device_access.py`: shared owner dispatch journal and package-use sessions; retain assignment/evaluator identities separately.
- `generative_driver/device_mcp.py`: deterministic catalog and common MCP serving Adapter.
- `generative_driver/sdk.py`: supported client, invocation context and application-tool authoring Interface.
- Existing package emission/checking: freeze and verify the authoritative pair, derived model/catalog and reusable runtime.
- Existing worker gateway/benchmark: serve generated names before worker startup and preserve independent observations and authenticated gate evidence.
- `docs/extending-drivers-and-mcp.md` and a packaged application example: exact supported extension workflow and tested commands.

### Task 1: Standardized portable package

**Files:** create `src/interface_runtime/contracts.py`; modify package emission, integrity checks and generic driver template; test `tests/test_driver_contract.py`.

**Interface:** contract `from_model(model, metadata=None)` produces public/protocol documents; `compile_contract(contract, protocol)` returns a validated executable model. Metadata is authoritative in the contract; the model is a checked projection. Importing a historical model invents no meanings: missing descriptions/units receive scoped uncertainty.

- [x] Write `test_emitted_contract_drives_relocated_package`: emit through the public toolkit, relocate, inspect separate documents, replay and assert the independent temperature fixture yields 21.5 degC. Observe missing contract behavior fail.
- [x] Implement `driver-contract/1`, `driver-protocol/1` and a distinct new package manifest, preserving the historical manifest validator.
- [x] Pass the focused RED→GREEN regressions for public metadata authority, malformed caveats and authored explanations; retain package projection/catalog integrity and unsupported-capability checks.
- [x] Verify existing package tests and offline relocation using standard-library-only Python.

### Task 2: Contract execution and explicit preconditions

**Files:** create `src/interface_runtime/driver.py`; extend contract rules; update generic driver Interface; test `tests/test_driver_contract.py`.

**Interface:** `execute_package(root, operation, parameters=None, *, binding=None, allow_effects=(), replay=None)` returns a versioned public result preserving runtime/evidence details. Registered read observations provide guard values; output validation belongs here.

- [x] Write a scripted-device test in which an unmet arm condition returns its rule/expected/observed values and sends no requested output command. Watch it fail, implement, observe pass.
- [x] Add RED→GREEN regressions for false/unavailable observations, invalid public results, unknown-unit warnings, mistyped guard expectations and unsupported public schema constructs. Document the implemented equality/AND predicate subset.
- [x] Ensure guards and the command have one owner lease; no guard automatically performs state-changing preparation.
- [x] Verify original engine/package tests continue to pass.

### Task 3: Generated tool catalog and MCP Adapter

**Files:** create `src/generative_driver/device_mcp.py`; update emission/entry points; test `tests/test_device_mcp.py`.

**Interface:** `tool_catalog(contract)` produces deterministic MCP tool specs; a shared serving Adapter takes specs and an invocation callable, emits structured success/errors and readable text. The emitted catalog is the catalog actually served.

- [x] Write an actual stdio discovery test for one namespaced tool per public operation, with typed arguments and scoped warnings. Observe failure before implementing catalog/serving support.
- [x] Verify typed success/error schemas, duplicate tool identity refusal, unknown-tool protocol errors and package/catalog integrity through the shared Adapter and package checks.
- [x] Keep the existing generation-management MCP distinct and its tests green.

### Task 4: Managed package sessions and durable dispatch

**Files:** create `src/generative_driver/device_access.py`; factor configurator dispatch/ownership; extend client/daemon public methods; test `tests/test_device_sessions.py` plus service tests.

**Interface:** operator-controlled open/close and public describe/execute/status/cancel/reconnect operations. Sessions pin the package/binding/grants independently of worker assignments. Invocation IDs identify one execution, and saved results can be queried after disconnect. A reuse Adapter joins the existing run's lease.

- [x] Write a public owner test that executes a finalized package and refuses a competing session on its device. Observe RED, implement session ownership through the existing owner, observe GREEN.
- [x] Factor the stage/evaluator/session dispatch journal behind one Module while existing tests protect behavior.
- [x] Add one RED→GREEN cycle each for request deduplication, reconnect after lost response, cancellation, uncertain effect blocking, guard-only refusal reconciliation, package mutation, lease release and recovery.
- [x] Prevent mutating managed submissions from using the client's automatic resend path; include explicit bounded daemon capacity/timeouts.

### Task 5: Application SDK and working extension guide

**Files:** create `src/generative_driver/sdk.py`, a packaged measurement-average example and `docs/extending-drivers-and-mcp.md`; test `tests/test_application_mcp.py`.

**Interface:** public managed client, tool specification, invocation context and registration/serving helpers. Both generated and application tools use the common MCP Adapter and result conventions.

- [x] Write a test that runs the application template through actual MCP, takes independently known readings and reports the literal expected mean, count, unit and child evidence. Watch RED, implement, watch GREEN.
- [x] Add RED→GREEN tests for propagated caveats, precondition failures, partial trace, cancellation and missing grants.
- [x] Document three branches: public operation, shared runtime capability, application MCP. Execute the exact example/validation commands in tests and link the guide from setup/contributing.

### Task 6: Authentic generated MCP fresh reuse

**Files:** update worker gateway, assignment preparation, benchmark package observation and authenticated scoring/profile identity; test `tests/test_benchmark_mcp_reuse.py`.

**Interface:** new runs using a new-format package expose its generated names before agent launch. Invocations retain tool/catalog/package/session/assignment/attempt/dispatch identities in owner-written events. Existing independent observation and frozen final grading remain evaluator-owned.

- [x] Write a controlled six-stage integration test whose fresh worker can use only generated tools and whose independently checked mission succeeds. Watch it fail, implement the gateway and observed call routing, observe pass.
- [x] Add RED→GREEN tests refusing generic-only/forged MCP evidence, stale assignments and altered package pins.
- [x] Identify the strengthened MCP acceptance profile explicitly in frozen metadata/reports/scoring; historical sealed trial meanings remain unchanged.
- [x] Keep the full workflow and package/evaluator regression suites green.

### Task 7: Release verification and real benchmark

**Files:** extension/setup/benchmark docs and the public verification record; benchmark outputs outside installed resources.

- [x] Run `.venv/bin/python -m unittest discover -s tests -v`, fix every observed failure with a reproducing RED→GREEN cycle and report any external limitations by name.
- [x] Build/install the wheel into a clean environment outside the checkout; run installed tests and scripted smoke, inspect distributable assets and exercise the application template.
- [x] Obtain a fresh whole-change review; fix material findings with RED→GREEN tests.
- [x] Requalify the native reference/evaluator against current code as admission requires. Use the configured real runtime/model for development trials; keep password file handles evaluator-only.
- [x] Run all six actual-agent stages, require generated MCP reuse and independent final acceptance, export report/evidence and verify offline scoring. Retain failed attempts and usage coverage. Do not describe scripted/replay/native-reference checks as model performance.

Final evidence: [preserved development and suite verification](../../../bench/evidence/2026-10-29-driver-contract-mcp-verification.json). The third development trial passed with 22 genuine generated MCP calls and 891/891 frozen final checks; the first two blocked trials are retained. Installed release: 516 tests, no skips. Native Windows qualification remains unperformed.

## Execution record

[TDD ledger](../../../bench/evidence/2026-10-29-driver-contract-mcp-verification.json) records observed RED/GREEN commands, baseline, rulings and final benchmark evidence. The approved design and this task's explicit implementation request authorize continuous execution; no additional plan approval is needed.

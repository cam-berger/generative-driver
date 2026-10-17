---
name: generative-driver
description: Generate a device interface from firmware, reconnect to an existing driver workflow, or run a Generative Driver benchmark through the configurator.
---

# Generate a device interface

1. Run `driver_doctor`; resolve profile dependencies and configure authenticated Codex/Goose. Credentials stay with that runtime.
2. Establish objective, inputs, binding and effects. Reads are default; writes/actuation require task authorization. Grant `reset` only for devices that restart on port open. Smoke uses no inference. Real benchmarks send a password file path in options to a bench harness, which provides the device and grades once after the run; configurator/workers never receive the path/password. Keep answers out of candidate context.
3. Start once with `driver_start` or `driver_benchmark_run`, stable request ID; save `run_id`. Configurator owns workers/access/handoffs. When authorized, use `scoped_tool_approval: "emulator"` for any emulated case or `"bound-device"` for the selected binding/effects, inside benchmark options or directly on generic starts. Preserve prior authorization.
4. Follow status/events and stage notes, including failed/not-run checks and open questions. Worker reports never stop runs; only safety, integrity, operator and host faults do. Use `driver_respond` for requested observations, naming the observer. Never relay grades, evaluator output or answers through `driver_respond`. Cancel when asked; resolve causes before resuming. A stopped emulator needs a harness-attached device. Only a suite's harness attaches one; a stopped single trial is not continued, so start a new trial.
5. Retrieve `driver_result`: report stages/notes, `equivalence` with basis, evidence and limits separately. Benchmarks complete with verdict pending until the bench harness posts the grade; pending is not failure. `benchmark trial-resume` completes an evaluation that was unscored or not posted. Generic verdicts are `self-evidence`, not independent grades. Label replay, emulation and physical results.

Six stages: acquire → interpret → probe → ground → emit → reuse. Pass notes forward except fresh reuse, which gets only package/objective and permitted operating information. Ground tests meanings against the device. Device differences drive bounded repair; after budget exhaustion continue with the best revision. Record one verdict, then `completed` regardless of equivalence. A package's structure does not prove its behavior.

`assistive/1` results are a new series, never compared with gated results. Workers see instrument readings as device evidence and are not read-isolated; answer-key separation is password-based, not a sandbox. Interrupted writes stay uncertain until checked; never silently retry them.

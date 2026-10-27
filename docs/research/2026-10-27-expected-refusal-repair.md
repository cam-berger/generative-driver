# Expected refusals and repair effort

The architecture validation found a repair that did not follow a functional defect in the triggering call. Probe deliberately read address 8, which the public Zephyr mission requires the driver to refuse. The captured call failed with a model-owned timeout, preserved every independently observed register and coil, and verified identity. The immediately following valid read used the same model bytes and returned the value in the independent observation. The configurator nevertheless consumed one repair because it treats a failed live execution as a contradiction without recognizing the public negative-test intent.

This finding comes from captured tool calls and accepted configurator notes, not the worker's claim that its test passed. Raw-file and model-directory hashes have different scopes; the two calls' raw runtime model hashes match. The driver subsequently passed fresh reuse and all 315 final checks, authenticated by encrypted-evidence regrade. That final result does not establish every behavior of the earlier revision.

A separate post-hoc control emitted the preserved pre-repair model and mapping, then ran the qualified final evaluator on a fresh emulator. It also passed 315/315. The executed raw model hash exactly matches the model used by the original probe; the mapping matches its accepted artifact. This is scripted execution of a saved candidate, with no new inference or fresh-reuse agent. It strengthens the finding for this tested driver contract without changing the original trial, its repair count or its verdict.

The resulting second interpretation and probe attempts used 283.35 worker seconds and 816,527 reported tokens, including cached input. Those are observed costs associated with this repair, not a measured counterfactual saving or billing estimate. See the [validation record](../implementation/2026-10-27-benchmark-architecture-validation.json).

## Proposed change

Keep runtime errors and final grading unchanged. Add a separate expected-refusal receipt, bound prospectively to a structured public task/input selector, the captured capability mapping, immutable assigned model, dispatch, assignment and device session. The current mission expresses address 8's negative expectation in prose inside `read_register`; its input bounds alone cannot distinguish the expectation.

Recognize only a read-only negative call whose trusted journal proves live contact, verified identity, complete unchanged instrument state, no unresolved effect, and an immediately following valid read by the same driver and session whose value and unit match the independent observer. Require an unambiguous task-to-operation binding. Do not accept a worker's success label, infer intent from an error string, or manufacture recovery with an automatic retry or reset.

Such a receipt would exclude only that call's direct failure entry from repair input. Retain its raw failure and observations, other model/mapping/value discrepancies, and all safety, integrity, host and operator stops. Missing intent, changed state, ambiguous bindings, absent recovery or an unknown failure keeps current routing. Extend beyond this read-only case only with separate evidence.

## Evidence and implementation status

An outside-checkout synthetic proof calls the existing `_device_diff`, `_outcome` and `bind_task` functions. Its 16 scenarios reproduce the unnecessary revision and exercise the proposed guard against missing intent or observations, failed recovery, positive-input failures, other defects, resets, ambiguous bindings and stop conditions. It also demonstrates why differently scoped model hashes must not be compared directly.

This is a proposal, not an implemented exemption or an agent validation of that exemption. The prototype and full findings remain outside the repository for review. Implementing it requires public mission/receipt design, behavioral regressions through the gateway, targeted incorrect drivers, current native qualification and another end-to-end agent run. Final functional grading must remain independent.

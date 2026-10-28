# Benchmark registration and selection

Status: accepted in [PR #19](https://github.com/cam-berger/generative-driver/pull/19).

## Context

Adding a case required repeated identity and family lists. A new family could inherit TQ9 reuse logic, and loading one observer imported unrelated families. Filtering a catalog removed coverage slots, while public availability could be mistaken for authenticated qualification. These made the add/select/run/report workflow difficult to follow.

The 2026-10-26 design review compared pinned primary implementations: [SWE-bench task contracts](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/task/repo.py), [Harbor content locks](https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/models/job/lock.py) and [τ-bench outcome grading](https://github.com/sierra-research/tau-bench/blob/59a200c6d575d595120f1cb70fea53cef0632f6b/tau_bench/envs/base.py). These inform explicit contracts, immutable selection and separate agent/verifier evidence; they are design inputs, not firmware measurements. Keep the existing configurator and firmware evaluator.

## Decision

Use one code-owned `CaseRegistration` declaration for a case's identity and existing adapters. Load its native family lazily. Catalogs, manifests and encrypted truth remain data and cannot select executable imports. Unknown missions stay unscored; manifest families must match their trusted registration.

Preserve the full catalog in a versioned plan. Include/exclude decisions carry reasons, remain independent of admission blockers and contribute to the existing suite's comparison identity. Execute selected ready cases through the existing suite harness and six-stage configurator. Retain known case identities when retiring a case from future selection.

Use the existing authenticated calibration check in an operator-only preflight. A public availability label is not admission. The check launches no agents or devices and does not claim runtime authentication or tool availability.

Bind native monitor completion and observation values to the submitted command's echo and its following prompt. Decode Telnet controls before matching application text; preserve the raw response bytes in private evidence. A greeting or unrelated reply is not a receipt. Commands are dispatched once, including effectful controls.

Report measured success over graded trials, selected coverage over selected planned slots and demonstrated coverage over the entire catalog. A false fresh mission contributes a measured failure only with trusted model attribution; host, operator and missing attribution stay unscored. An independently validated negative final grade or no-driver record still counts as failure.

## Consequences

Case authors have one registration declaration and explicit family/resource/qualification responsibilities. A new transport or backend still requires an actual implementation and independent observations. The [authoring guide](../benchmark-case-authoring.md) names those files and the promotion/activation steps.

Legacy plans remain readable; new plans bind the complete catalog and selection into their suite identity. Changed evaluator code requires native requalification. Historical evidence retains its original code and case snapshots. The upstream pilot still covers one executable lineage and six blocked targets; this decision does not expand physical coverage.

Version 1 plan reconstruction remains explicit for historical compatibility. Its suite construction overlaps version 2; a future scheduling change must preserve the old serialization while updating the current plan. The compatibility regressions cover both forms.


## Deferred work

Qualification currently hashes the entire benchmark-support closure; [issue 01](../../.scratch/benchmark-lifecycle/issues/01-scope-qualification-closure.md) asks whether a complete per-case closure can avoid invalidating unrelated admissions. [Issue 02](../../.scratch/benchmark-lifecycle/issues/02-structured-native-monitor.md) retains the structured-monitor prototype and its confinement/single-dispatch prerequisites. [Issue 03](../../.scratch/benchmark-lifecycle/issues/03-expected-refusal-repair.md) records a narrowly witnessed expected-refusal proposal. None is implemented by this decision.

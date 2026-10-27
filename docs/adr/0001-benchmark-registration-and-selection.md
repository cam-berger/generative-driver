# Benchmark registration and selection

Status: proposed in [PR #19](https://github.com/cam-berger/generative-driver/pull/19).

## Context

Adding a case required repeated identity and family lists. A new family could inherit TQ9 reuse logic, and loading one observer imported unrelated families. Filtering a catalog removed coverage slots, while public availability could be mistaken for authenticated qualification. These made the add/select/run/report workflow difficult to follow.

The [primary-source comparison](../research/2026-10-26-benchmark-architecture.md) supports explicit task contracts, final functional outcomes, immutable selection and separate infrastructure evidence. It does not justify replacing the existing configurator or firmware evaluator.

## Decision

Use one code-owned `CaseRegistration` declaration for a case's identity and existing adapters. Load its native family lazily. Catalogs, manifests and encrypted truth remain data and cannot select executable imports. Unknown missions stay unscored; manifest families must match their trusted registration.

Preserve the full catalog in a versioned plan. Include/exclude decisions carry reasons, remain independent of admission blockers and contribute to the existing suite's comparison identity. Execute selected ready cases through the existing suite harness and six-stage configurator. Retain known case identities when retiring a case from future selection.

Use the existing authenticated calibration check in an operator-only preflight. A public availability label is not admission. The check launches no agents or devices and does not claim runtime authentication or tool availability.

Report measured success over graded trials, selected coverage over selected planned slots and demonstrated coverage over the entire catalog. A false fresh mission contributes a measured failure only with trusted model attribution; host, operator and missing attribution stay unscored. An independently validated negative final grade or no-driver record still counts as failure.

## Consequences

Case authors have one registration declaration and explicit family/resource/qualification responsibilities. A new transport or backend still requires an actual implementation and independent observations. The [authoring guide](../benchmark-case-authoring.md) names those files and the promotion/activation steps.

Legacy plans remain readable; new plans bind the complete catalog and selection into their suite identity. Changed evaluator code requires native requalification. Historical evidence retains its original code and case snapshots. The upstream pilot still covers one executable lineage and six blocked targets; this decision does not expand physical coverage.

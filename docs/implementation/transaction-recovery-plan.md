# Transaction coverage and emulator recovery

Completed amendment approved in chat for `fix/pipeline-discovery-repair`. It makes pending-transaction errors mandatory to test and allows bounded repair after verified replacement of an owned emulator. Implementation used subagent-driven development and failing behavior tests, one slice at a time. See [design](../design.md) for the six stages and four seams.

## Required behavior

Store update discards pending edits in either bank, changes only the requested committed cell, preserves other committed cells, advances generation once and ends idle. Diagnostic and final phases require idle update, pending same-bank update, pending different-bank update, stage/commit, stage/abort and rejection, with complete ending-state observations. The interpreter derives command composition; the runtime receives no case-specific repair code.

Rejection requires an independently authenticated complete-response witness. Timeout, malformed replies, missing terminators and candidate-selected framing cannot earn rejection credit. Preserve fault attribution and uncertain effects.

Recovery is an internal adapter operation, never a candidate-provided tool/result. Before final grading, require trusted completed model-failure evidence for every unresolved effectful dispatch, stopped workers/operations, retained process ownership, verified old-process disposal, a distinct replacement and independent initial-state observations. Record proof, original dispatch IDs and a durable recovery event; retain failed exchanges/artifacts and historical uncertainty. Consume existing revision/time budgets and revalidate normal handoffs. Never replay the uncertain write or turn its failure into success.

Host/operator/unknown faults, physical/attached sessions, cancellation, lost ownership, unverifiable reset, exhausted budget and final grading stay blocked. Stale credentials, restart and duplicate recovery must not dispatch or consume repairs twice. Passwords, private contracts and authoring assets remain evaluator-side. Preserve prior experiments; implementation/reference verification did not authorize model or physical trials.

## Completed slices

| Done | Slice | Checks |
|---|---|---|
| [x] | Transaction contract and evaluator episodes | Hand-checked values; same/other-bank pending updates; full state/generation; correct composition passes, idle-only mutant fails; incomplete rejection refused |
| [x] | Configurator/adapter recovery | Real public service route through repair and six gates; trusted lifecycle receipt; leases, immutable history, cancellation/restart/stale/duplicate cases; preserved failure evidence |
| [x] | Versioned assets and qualification | Private inventories rebuilt/resealed; current evaluator/input pins; stale admission refused; references and predeclared mutant failures reproduced |
| [x] | Release checks | Source and installed archives, smoke/replay, native adapter lifecycle and separate deterministic controller proof; documented limits |

Version 4's [frozen record](transaction-recovery-v4-verification.json) retains its review gap: first-line framing could earn rejection credit. Version 5 adds the independent witness and short-frame mutant. Its [verification](transaction-recovery-verification.json) records 461 tests per source/installed environment, 32 native sessions, unchanged firmware and preserved failed attempts. [The later model trial](../research/2026-10-06-transaction-recovery-results.md) is separate evidence and completed 2/3.

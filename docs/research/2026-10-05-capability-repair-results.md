# Capability repair experiment — 2026-10-05

Result: 0/3 fresh-reuse completions. TQ9 passed diagnostics but stopped at ground; sensor/store exhausted repairs. Exact source/evaluator pins and artifacts are in the [index](../implementation/capability-repair-verification.json).

All three trials used Codex CLI 0.159.2, `gpt-6.1-sol`, medium reasoning, native macOS emulation, original firmware, case seed 0, sequential execution, three-hour budgets and at most two repairs. Success requires six gates, fresh package-only reuse and independent final behavior.

| Family | Current accepted gates | Repairs | Diagnostic checks by revision | Minutes | Reported tokens |
|---|---:|---:|---|---:|---:|
| TQ9 | 3/6 | 1 | 6/9 → 9/9 | 19.6 | 1,716,616 |
| Sampled sensor | 2/6 | 2 | 11/17 → 2/17 → 12/17 | 18.6 | 1,907,847 |
| Parameter store | 2/6 | 2 | Mapping rejected → 156/174 → mapping rejected | 23.8 | 2,327,254 |

TQ9 ground requested revision but the route/evidence was missing despite remaining budget; a raw-label mismatch did not invalidate its 9/9 diagnostics. Sensor's last repair discarded authorized feedback under blind-workspace instructions; scaling, units and effects also remained defective. Store lacked stage/commit/abort task coverage and ignored available repair context.

Twenty workers reported 5,951,717 tokens (5,109,248 cached), with complete usage coverage. Five repairs added ten assignments. Suite creation through outcome took 62.1 minutes including preparation/stops, excluding preflight/later checks. No human input, resume, extension or candidate edit occurred. All 86 managed operations finished; no uncertain effects or stopping workers remained.

## Verification and checkpoint

| Local macOS environment | Tests | Seconds | Skips | Result |
|---|---:|---:|---:|---|
| Source, Python 3.13.12 | 432 | 72.909 | 3 archive gates | Passed |
| Installed wheel, Python 3.11.15 | 432 | 70.508 | 0 | Passed |
| Installed wheel, Python 3.13.12 | 432 | 78.080 | 0 | Passed |

Thirty native grades reproduced and three admissions passed. All 126 final wheel entries matched tested bytes; the final source archive differed by one removed documentation sentence. Replay/distribution checks passed.

The checkpoint called for explicit permission to use supplied diagnostics, sealed same-run model/capability/transaction context, task/effect semantics and ground-to-interpret routing within the remaining budget. These changes were not yet implemented; the user requested a break. The later [context experiment](2026-10-05-context-repair-results.md) is a separate record. See the [handoff](../implementation/benchmark-handoff.md).

These are outcome-aware engineering reruns on three familiar authored cases, not a causal comparison, model ranking or estimate of general reliability. Visible traces do not establish worker filesystem isolation; emulation does not establish physical behavior. Cached input tokens are part of total usage, not additional tokens.

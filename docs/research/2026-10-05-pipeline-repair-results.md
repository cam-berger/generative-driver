# Pipeline repair experiment — 2026-10-05

Result: 0/3 complete workflows before and after repair. The rerun improved tool use but exposed mapping and failure-classification defects. Source `9929e3053135257cd8f9940bd2cb5a4709397d49`; exact evaluator/case pins and logs are in the [index](../implementation/pipeline-repair-verification.json).

All three trials used Codex CLI 0.159.2, `gpt-6.1-sol`, medium reasoning, native macOS emulation, original firmware, case seed 0, sequential execution, three-hour budgets and at most two repairs. Success requires six gates, fresh package-only reuse and independent final behavior.

## Earlier run

| Family | Accepted stages | Model repairs | Elapsed minutes |
|---|---:|---:|---:|
| TQ9 | 2/6 | 0 | 9.1 |
| Sampled sensor | 1/6 | 2 | 14.3 |
| Parameter store | 1/6 | 2 | 15.9 |

All seven interpret attempts refused supplied analysis tools. Eleven workers reported 2,406,167 tokens (1,927,424 cached); elapsed time was about 41 minutes including a manual gap.

## Repaired run

| Family | Outcome | Repairs | Diagnostic checks, by revision | Minutes | Reported tokens |
|---|---|---:|---|---:|---:|
| TQ9 | Model blocked | 2 | 6/9, 6/9, 6/9 | 26.9 | 2,595,164 |
| Sampled sensor | Model blocked | 2 | 1/17, 1/17, 1/17 | 20.2 | 1,970,648 |
| Parameter store | Unknown harness stop | 0 | Not reached | 8.9 | 805,005 |

All seven interpreters used tools. Many diagnostic failures were malformed mappings rejected before device I/O; they are not equivalent to protocol failures. Store's argument refusal occurred before transport but was misclassified as an uncertain effect. One cleanup-only response reconciled the disposed emulator; the run was not resumed.

Seventeen workers reported 5,370,817 tokens (4,593,152 cached). Four repairs added eight assignments. Outcomes took 56.1 minutes; cleanup extended the span to 62.4. All 98 managed operations finished and execution identities stayed unchanged.

## Verification and follow-up

| Local macOS environment | Tests | Seconds | Skips | Result |
|---|---:|---:|---:|---|
| Source, Python 3.13.12 | 426 | 69.125 | 3 archive gates | Passed |
| Installed wheel, Python 3.11.15 | 426 | 64.839 | 0 | Passed |
| Installed wheel, Python 3.13.12 | 426 | 70.756 | 0 | Passed |

Thirty native sessions (12 reference, 18 mutant) reproduced grades; three authenticated admissions passed. Software/reference checks do not establish model success. Follow-up work added explicit capability format/pre-I/O validation, refusal classification and atomic cleanup status. The [capability experiment](2026-10-05-capability-repair-results.md) retains its own qualification and outcomes; its later 432-test result does not replace this table.

These are outcome-aware engineering reruns on three familiar authored cases, not a causal comparison, model ranking or estimate of general reliability. Visible traces do not establish worker filesystem isolation; emulation does not establish physical behavior. Cached input tokens are part of total usage, not additional tokens.

# Multiline error framing: three-trial comparison

Parameter store changed from **1/3 to 3/3 joint successes**. The core track changed from **7/9 to 9/9**. Joint success requires independent equivalence, successful fresh reuse of the same frozen package, and six accepted handoffs.

| Profile | Baseline joint | New joint | New checks per trial | New pipeline repair cycles |
| --- | ---: | ---: | --- | ---: |
| Zephyr Modbus (upstream) | 3/3 | 3/3 | 315/315 | 1 |
| TQ9-v2 | 3/3 | 3/3 | 9/9 | 0 |
| Sampled sensor | 3/3 | 3/3 | 85/85 | 3 |
| Parameter store | 1/3 | 3/3 | 891/891 | 0 |
| Legacy TQ9 (supplement) | 3/3 | 3/3 | 3/3 recorded-reuse | 1 |

The runnable upstream track is 3/3; the three-case core is 9/9; legacy is 3/3. Administrative totals are **13/15 before and 15/15 after**, with fresh missions 15/15 and 15/15. Legacy shares TQ9 ancestry, so these are five profiles across four measured families, not fifteen independent devices.

The defect was an undersized receive contract: a rejected command can return an error line followed by `DONE`, but generated models allowed one line. The old qualifier counted a framing-limit error as a successful rejection. The fix validates declared rejection/completion bounds in the generation tools, sealed validator and managed gateway, and requires a complete rejection transcript in fault qualification. The managed guard runs before a write is marked uncertain. The raw execution engine and all 45 benchmark-support source files remain byte-identical to baseline.

All three fresh store trials tried one-line bounds, received explicit validator defects, and corrected them within their original interpretation attempts before live probing. Final commit/abort line bounds were 8, 2 and 2. These edits are local agent corrections; each store trial used zero coarse pipeline repair cycles. Store trial 2 left the offline `update` predicted-reply fixture uncovered, and its accepted interpretation records failed/incomplete qualification. Its later live and independent 891-check results passed. The other two store trials completed their supplied offline qualification. Worker claims, handoff acceptance, self-evidence and final grades are distinct.

Validation included ten focused behavior tests with recorded red/green regressions, real loopback TCP tests with LF/CRLF replies, bounded incomplete replies and no write replay, generic deployment repair, and pre-dispatch refusal. The installed suite ran **938 tests: 937 passed, one optional Linux-backend skip**. An initial unrelated cancellation-ordering test failure is retained with the unchanged baseline/fixed repeats, full lifecycle module, two controlled event-order diagnostics and clean full-suite rerun. Fresh qualification ran 42 locally installed Renode reference/control executions across four cases before inference. Positive references passed and required wrong models failed their expected checks; all control-check outcomes match baseline. The deliberately short-frame control still fails the original five rejection checks. The two failed baseline store models are refused by the new validator; the passing baseline model remains admitted.

Implementation `625f55a` was tested against preserved baseline `9df3887`, using the same frozen Codex CLI `0.164.0-alpha.11.1`, gpt-6.1-sol, unspecified CLI reasoning default, dependencies, tools, original scenario/seed 0, trial order and budgets. Each profile received three fresh six-stage `assistive/1` trials, one active child at a time, with a 10,800-second child budget and at most two pipeline repair cycles. Within-worker validation edits are additional iterative work inside that budget. No primary trial was replaced or given a budget extension. The executed and supplied firmware `.bin` artifacts, public missions, contracts, recipes, references and required mutations match baseline. Fresh private build ELFs/reports differ only in audited debug/path provenance; both raw hashes are retained. See the [frozen comparison plan](2026-10-27-multiline-repair-comparison-plan.md).

| Profile | Baseline median trial min | New median trial min | New trial range min | New fixed-case 95% interval |
| --- | ---: | ---: | --- | --- |
| Zephyr Modbus (upstream) | 9.6 | 7.9 | 7.8–139.2 | 29.2%–100.0% |
| TQ9-v2 | 5.5 | 5.2 | 4.9–6.1 | 29.2%–100.0% |
| Sampled sensor | 7.1 | 7.4 | 7.3–8.0 | 29.2%–100.0% |
| Parameter store | 7.0 | 7.6 | 6.6–7.9 | 29.2%–100.0% |
| Legacy TQ9 (supplement) | 5.2 | 5.9 | 5.7–8.5 | 29.2%–100.0% |

Across all fifteen profile trials, tokens were 23,672,845 → 23,650,505; usage was reported for 98/98 → 100/100 attempts. Managed calls were 551 → 575; pipeline cycles were 4 → 5. Core tokens increased 11,287,310 → 11,545,060 even though main-track tokens decreased 19,940,574 → 18,680,476. Cached input and reasoning output are subsets, not extra totals. These observations do not support a uniform cost improvement.

Unadjusted summed trial time was 104.0 → 236.0 minutes; reported worker time was 96.5 → 114.0 minutes. The first Zephyr trial retained its original **8,352.312-second elapsed** and **1,448.919-second reported worker** times. Mac sleep/wake overlapped a 7,416.209-second public-event gap; that gap is not measured sleep duration and was not subtracted. A temporary idle-sleep guard was then scoped to the conductor. API intervention counts are separate from these external host events; no repair is attributed wholesale to sleep. Timing remains descriptive.

Upstream selection retains 21 slots across seven lineages: three Zephyr passes and 18 blocked slots across six lineages. Runnable success is 3/3; demonstrated selected coverage is 3/21, one of seven lineages. Physical BME280 remains inventory-blocked without a selected binding/reference; no physical operations ran. Scripted setup smoke and reference calibration are excluded from model performance. Legacy uses recorded-reuse grading, with no independent public temperature reference or controlled fresh-final vector claim. This study measures software emulation on macOS, not Windows model performance, electrical faults or physical timing.

The last legacy trial used one automatic owned-emulator recovery: its old owner stopped, the initial state was verified, and fresh reuse used the new session. Its one pipeline repair followed a completed binary device refusal/response-expectation mismatch. This is separate from multiline framing and remains within the original trial.

Each series was regraded under its own preserved installation. All fresh missions were separately rederived from public accepted-reuse observations, package/capability mappings and event chronology. Reuse sessions were distinct and their sole recorded artifact input was the emitted package. State preservation is established at before/after snapshots, not continuously within a call. Workers were not filesystem-read isolated; password separation and hashes are not an OS sandbox or third-party attestation.

Standard upstream comparison: incompatible, reason=`Frozen experiment identities differ`.
Standard core comparison: incompatible, reason=`Frozen experiment identities differ`.
Standard legacy comparison: compatible; changed dimension: `toolchain_revision`.

Refreshed calibration pins make the upstream/core comparisons incompatible; legacy remains compatible under its recorded-reuse policy. Preserve those standard decisions and read the main table as a descriptive system comparison on familiar development cases. Three repetitions and fixed-case binomial intervals do not establish a population success rate, randomized causal improvement or model ranking.

The [verification index](../implementation/multiline-repair-verification.json) records trial/package hashes, independent audits, grading seals, frozen plans and raw metric distributions. The local archive is `20261027-multiline-repair-1707622c` in the Generative Driver experiments directory; baseline `20261027-whole-benchmark-9a965fb8` remains unchanged. All fifteen trial devices and this study’s configurator were stopped; five temporary evaluator key copies and their mappings/options were removed. Original passwords, the user-mounted vault, pre-existing sign-in and private research evidence were preserved. The conductor-scoped idle-sleep assertion was released.

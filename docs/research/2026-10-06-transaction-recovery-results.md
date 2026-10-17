# Transaction coverage and recovery trial — 2026-10-06

Result: 2/3 complete; the original 3/3 publication condition was unmet. The user later authorized publishing all fixes/results in [PR #5](https://github.com/cam-berger/generative-driver/pull/5); the outcome remains 2/3. Source `272c1b1028cd11f8307d55ce12c1c60e0646ce2e`, installed Python 3.13.12, Codex CLI 0.159.2, `gpt-6.1-sol`, medium. Version 5 adds [transaction/rejection checks](../verification.md#transaction-coverage-and-emulator-recovery--2026-10-06).

One sequential trial per original family, seed 0, three-hour child budgets, two repairs and nine-hour queue deadline. Success required six gates, fresh reuse and sealed regrading. No failed trial was replaced; no prior-run candidate/advice, extra authorization or extension was supplied. Normal same-run repair feedback remained available.

| Family | Current accepted stages | Diagnostic history | Final behavior | Model repairs | Emulator recoveries | Workflow minutes | Reported tokens |
|---|---:|---|---|---:|---:|---:|---:|
| TQ9 | 6/6 | 9/9 after recovery | 9/9 passed | 1 | 1 | 10.2 | 2,087,841 |
| Sampled sensor | 6/6 | 14/17 → 17/17 | 85/85 passed | 1 | 0 | 10.8 | 1,622,834 |
| Parameter store | 2/6 | Mapping refused; then 364/366 | Not reached | 2 | 0 | 14.2 | 2,299,681 |

TQ9 exercised automatic owned-emulator recovery and completed after one repair. Sensor repaired its decoder. Store spent one repair on mapping, then failed two independent rejection checks (364/366); pending-update and ending-state checks passed. Its second revision intentionally committed while idle, received a complete negative reply and hit the runtime expectation/uncertainty guard with no repair budget. That final candidate was not graded; this does not prove a defect in its valid operations.

| Stage | TQ9 seconds | Sensor seconds | Store seconds |
|---|---:|---:|---:|
| Acquire | 25.3 | 28.7 | 29.4 |
| Interpret | 235.0 | 281.8 | 453.5 |
| Probe | 151.9 | 124.4 | 338.0 |
| Ground | 76.9 | 88.4 | Not reached |
| Emit | 31.4 | 33.7 | Not reached |
| Reuse | 57.2 | 51.3 | Not reached |

Stage totals include all attempts; tool time overlaps workers and cannot be added to workflow latency. Managed counts exclude arbitrary shell calls. Twenty-three workers reported 6,010,356 tokens (5,066,624 cached), complete coverage. Total outcome time: 35.2 minutes. All 127 managed operations finished; zero human inputs/rescues/extensions. Postflight identities/settings/artifacts stayed unchanged; the service stopped and final emulators were absent. Store uncertainty remains.

The exact installation reused [461-test/32-native qualification](../implementation/transaction-recovery-verification.json) after identity checks, plus fresh smoke/admission. Recorded and sealed regrades agree; independent reviews support the negative primary result. The [trial index](../implementation/transaction-recovery-trial-verification.json) preserves metrics, hashes and limits.

These familiar-case engineering trials do not estimate general reliability or physical performance. Version 3 scores have different coverage and are not directly comparable. Visible traces lack full stdin/argv capture and do not establish filesystem isolation. Next: reproduce expected-negative-probe handling through the existing interfaces without bypassing uncertainty guards or bounded repair.

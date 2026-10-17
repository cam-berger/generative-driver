# Context repair experiment — 2026-10-05

Result: 2/3 complete workflows at `21359b466f24d58cdb9a0bfb838a64c58de713b0`. Implementation `556d5e0` fixed repair instructions/context, ground routing/evidence and invocation-fault attribution. Exact identities and stage usage are in the [index](../implementation/context-repair-verification.json).

All three trials used Codex CLI 0.159.2, `gpt-6.1-sol`, medium reasoning, native macOS emulation, original firmware, case seed 0, sequential execution, three-hour budgets and at most two repairs. Success requires six gates, fresh package-only reuse and independent final behavior.

| Family | Current accepted gates | Repairs | Diagnostic checks by revision | Final checks | Minutes | Reported tokens |
|---|---:|---:|---|---|---:|---:|
| TQ9 | 6/6 | 0 | 9/9 | 9/9 | 9.6 | 1,193,315 |
| Sampled sensor | 6/6 | 1 | 14/17 → 17/17 | 85/85 | 11.3 | 1,740,158 |
| Parameter store | 2/6 | 1 | 174/174; revised probe blocked before grading | Not reached | 10.9 | 1,861,857 |

Sensor used measured feedback to repair its decoder. Store's first candidate passed diagnostics; ground requested a framing repair. The revision fixed framing, then update during a pending transaction triggered the uncertainty guard. One repair remained, but uncertainty blocked dispatch. Its accepted probe was still revision 0; final evaluation was never reached.

One cleanup-only response reconciled the disposed store emulator without resuming the run; this is not evidence that the failed operation had no effects. Recorded and sealed regrades agree.

| Cumulative worker seconds, all attempts | Acquire | Interpret | Probe | Ground | Emit | Reuse |
|---|---:|---:|---:|---:|---:|---:|
| TQ9 | 35.8 | 176.0 | 95.3 | 127.4 | 47.7 | 60.1 |
| Sampled sensor | 44.0 | 217.5 | 154.8 | 123.7 | 39.9 | 62.1 |
| Parameter store | 32.7 | 271.9 | 253.2 | 82.6 | — | — |

Stage totals include repeated workers. Tool time may overlap worker time; neither sums to workflow latency. Twenty workers reported 4,795,330 tokens (3,967,744 cached), complete usage coverage. Two repairs added four assignments. Outcomes took 1,909 seconds (31.8 minutes), excluding preflight/cleanup. All 99 managed operations finished; postflight identities/settings stayed unchanged, no uncertainty/stopping workers remained after cleanup, and the private service stopped.

## Verification and interpretation

| Local macOS environment | Tests | Seconds | Skips | Result |
|---|---:|---:|---:|---|
| Source, Python 3.13.12 | 441 | 84.782 | 3 archive gates | Passed |
| Installed wheel, Python 3.11.15 | 441 | 81.807 | 0 | Passed |
| Installed wheel, Python 3.13.12 | 441 | 89.967 | 0 | Passed |

All 120 tracked runtime/resource files matched wheel/installations. Thirty native sessions (12 reference, 18 mutant) reproduced grades; three admissions passed with unchanged authored inputs. Distribution, replay and independent implementation/boundary reviews passed.

The prior 0/3 and this 2/3 support further pipeline work but do not isolate which combined fix helped. Ground acceptance is procedural. Next work targeted store state normalization and trusted emulator reconciliation while retaining the physical uncertainty guard. See the [later handoff](../implementation/benchmark-handoff.md).

These are outcome-aware engineering reruns on three familiar authored cases, not a causal comparison, model ranking or estimate of general reliability. Visible traces do not establish worker filesystem isolation; emulation does not establish physical behavior. Cached input tokens are part of total usage, not additional tokens.

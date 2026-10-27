# Benchmark performance — 2026-10-27

The core suite produced equivalent drivers with successful fresh reuse in **7/9** trials. Zephyr Modbus passed **3/3** runnable upstream trials. Legacy TQ9 is a separate supplement: 3/3 graded joint successes (three planned). Parameter-store varied across repetitions; successful public missions alone did not establish equivalence.

Implementation `9df3887`, the [frozen plan](2026-10-27-benchmark-performance-plan.md), and `assistive/1` were held fixed. Actual Codex workers used `gpt-6.1-sol` through CLI `0.164.0-alpha.11.1` on macOS arm64. Reasoning effort was unspecified in the CLI configuration; desktop preferences do not establish it. Each child had 10,800 seconds, scenario `original`, evaluator seed 0, and at most two pipeline repairs. Trials ran sequentially against ARM firmware in Renode; the native-ELF pilot is separate.

| Profile | Joint success | Fresh mission | Wall minutes: median (range) | Pipeline repairs, total | Exact 95% interval |
|---|---:|---:|---:|---:|---|
| `zephyr-modbus-v1` | 3/3 | 3/3 | 9.6 (7.6–12.9) | 1 | 29.24%–100.00% |
| `tq9-v2` | 3/3 | 3/3 | 5.5 (5.0–5.5) | 0 | 29.24%–100.00% |
| `sampled-sensor-v1` | 3/3 | 3/3 | 7.1 (7.0–7.1) | 3 | 29.24%–100.00% |
| `parameter-store-v1` | 1/3 | 3/3 | 7.0 (6.8–7.2) | 0 | 0.84%–90.57% |
| `tq9` (legacy supplement) | 3/3 | 3/3 | 5.2 (5.0–5.4) | 0 | 29.24%–100.00% |

Joint success requires independent equivalence and a successful package-only fresh mission for the same frozen driver, with all six accepted handoffs. Every completed trial handed off all six stages; handoff acceptance alone is not a behavioral grade. Intervals are two-sided Clopper–Pearson intervals, conditional on comparable independent fresh trials of the fixed case. Three repetitions leave substantial uncertainty; heterogeneous cases and blocked slots have no pooled binomial interval.

The two failed store trials each passed 886/891 checks and the fresh mission. Their five failed checks were `final/rejection/end/operation_rejected` and the four `final/cell-N/no-transaction/operation_rejected` checks. The third store trial passed 891/891. A read-only comparison found both failed packages bound commit/abort replies to one line; the passing package permits three. Saved device evidence contains a two-line rejection. This supports a receive-limit explanation for incomplete rejection evidence, without reconstructing each sealed failing call or establishing a controlled intervention. Associated state checks passed; these results do not demonstrate unintended state changes.

There were no manual trial repairs, extensions, host stops, unscored finals or replacement primary trials in the completed results. Failures remained final; automatic bounded repairs are included in the time and usage below.

Resource totals below sum graded trials: three per completed profile.

| Profile | Managed calls, total | Tokens, sum of trials | Cached input, total (subset) | Worker minutes, total |
|---|---:|---:|---:|---:|
| `zephyr-modbus-v1` | 232 | 8,653,264 | 7,860,736 | 28.3 |
| `tq9-v2` | 66 | 3,597,923 | 3,045,504 | 15.2 |
| `sampled-sensor-v1` | 94 | 3,875,397 | 3,210,880 | 19.9 |
| `parameter-store-v1` | 95 | 3,813,990 | 3,271,424 | 17.7 |
| `tq9` | 64 | 3,732,271 | 3,208,576 | 15.5 |

Across all fifteen profile trials, recorded trial wall time sums to 104.0 minutes; workers took 96.5 minutes and made 551 managed calls. Reported tokens total 23,672,845, including 20,597,120 cached-input tokens. Interpretation used 38.6% of worker time, and probing 22.0%. Completed workers reported usage for 98/98 attempts. Total tokens mean input plus output; cached input and reasoning output are subsets. Missing usage remains unknown. These are worker-reported counters, not a billing estimate; setup and audit-agent usage is excluded. Trial wall time includes generation, live calls and final evaluation, excluding earlier installation/calibration. Worker and managed-tool durations overlap and must not be added. The full analysis retains per-stage time, calls and tokens.

Upstream selection retains **21 slots across seven lineages**: three Zephyr passes and **18 blocked slots across six lineages**. Measured upstream success is 3/3; selected/full-catalog demonstrated coverage is 3/21 (14.3%), representing one of seven lineages. GRBL, Tasmota, OpenIMU, FX2 capture, EEZ PSU and ODrive remain blocked by execution, build, runtime, oracle or qualification gaps. Blocked slots are not model failures. Physical BME280 remains blocked without a selected binding and independent reference; no physical operations ran. Setup smoke is scripted and excluded.

The core covers three synthetic families; Zephyr adds one upstream lineage. Legacy TQ9 shares TQ9 ancestry and uses recorded-reuse grading, so it neither adds a fifth independent family nor replaces the core result. The calibrated profiles use fresh-final grading. Four current calibrated cases passed fresh positive-reference and known-wrong-mutant qualification before inference; legacy authentication followed its existing admission policy.

These are familiar development cases, with instrument-visible workers and firmware paused between live calls. Workers were not read-isolated from the host filesystem: evaluator passwords were kept out of configurator/worker inputs, but hashes and encrypted truth are not an operating-system sandbox. There is no held-out device population, baseline comparison, Windows model-performance result, physical timing/electrical qualification or general firmware success-rate claim.

Offline regrading recomputed sealed final behavior checks under the preserved evaluator. Fresh-mission results are the original recorded judgments authenticated in the seals; score carries them forward rather than recomputing reuse events. A separate read-only audit rederived all fifteen missions from public instrument observations for accepted reuse assignments and the frozen packages. Standard suite exports and the corpus report were cross-checked against individual runs, frozen pins, snapshots and usage. See the [verification index](../implementation/whole-benchmark-performance-verification.json). The local evaluator-owned archive is `20261027-whole-benchmark-9a965fb8` under the Generative Driver experiments directory; it retains the wheel, runtime, frozen plans, packages, sealed evidence, public reports and analysis scripts. Passwords and plaintext evaluator material are not committed. All fifteen trial devices and the isolated configurator were stopped; temporary evaluator-key copies were removed, preserving original passwords and host sign-in.

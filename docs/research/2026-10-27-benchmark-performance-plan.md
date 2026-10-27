# Whole-benchmark performance study

Question: how often does the current Generative Driver implementation produce a
functionally correct driver that a fresh agent can use on the available benchmark?

## Freeze before inference

Use implementation `9df3887`, wheel SHA-256
`040f91e6d6f6da205566897040676aaedbcff9df9fc09446e694952a64274042`,
Codex `gpt-6.1-sol`, the configured runtime defaults, and `assistive/1`.
Record effective CLI/tool/dependency versions. Do not change code, prompts,
grading, case selection or budgets after measurement starts.

The operator selected three fresh trials per case. Select nine core slots and
21 upstream slots before checking eligibility: missing keys and backends block
selected slots without shrinking their denominators. Use `original`, case seed 0,
10,800 seconds per child, 108,000 seconds for the core suite, and the corpus
runner's generated 32,400-second budget for its three currently ready trials.
The three-trial legacy supplement also has a 32,400-second suite budget.
Case seed controls evaluator episodes, not model sampling. One active child and
one native firmware process at a time; no parallel model trials.

| Track | Selection and handling |
|---|---|
| Core development | `tq9-v2`, `sampled-sensor-v1`, `parameter-store-v1`; three distinct synthetic families. |
| Upstream corpus | All seven catalog lineages remain selected; `zephyr-modbus-v1` is the only currently executable one. Six backend/build/oracle gaps stay blocked in coverage. |
| Legacy TQ9 | Three separate supplemental slots; unavailable evaluator keys block them. This profile does not add another independent family. |
| BME280 | Record blocked without a selected physical binding and independent operator reference; launch no physical operations. |
| Setup smoke | Scripted installation/replay prerequisite, excluded from model-performance counts. |

Run upstream first, then core, then the supplemental legacy profile. Resolve all
required qualification gates before each track starts. Run the standard Renode/ARM execution for ready software cases on the authenticated
macOS host. Core and upstream evidence tracks use separate existing suites and
reports. The earlier native-ELF pilot remains a separate engineering result;
different binaries and debug information preclude pooling it as another device
or a controlled execution-backend comparison.

## Gates

1. Inventory every registered profile and catalog lineage. Retain unavailable
   keys, hardware and backends as explicit gaps, rather than silently dropping them.
2. Verify the installed wheel and worker distribution, analysis tools, runtime
   authentication and sandbox/tool-host startup. Run the scripted setup smoke.
3. Freshly qualify each selected calibrated case against this exact evaluator:
   both positive references pass, and every required known-wrong mutant is
   rejected on its declared behavior checks. Stage authenticated manifests and
   envelopes together in an external asset tree; keep plaintext sources and
   evaluator password handles out of configurator/worker inputs.
4. Freeze suite/corpus plans and verify authenticated admission before inference.
   A failed gate blocks its dependent trials; it is not a model failure.
5. Audit this design independently for scope/denominators and execution integrity.

## Decision rule and reporting

Primary success, exported as `equivalent_and_mission`, is independent equivalence **and** successful fresh reuse for
the same frozen driver after all six handoffs. Stage completion, an emitted file
or replay success alone cannot pass. Keep final failures and no-driver outcomes;
report host/operator/safety stops, unscored and unrun slots separately. Repair is
the existing bounded pipeline behavior, never parent-agent coaching. Do not
rerun a graded failure to obtain a pass. Preserve any environment repair/resume
and its original evidence; never silently retry an uncertain write.
No primary trial gets a budget extension or extra model attempt outside its
normal bounded pipeline repairs. Grading-only resume may use the unchanged
frozen package after an environment correction. Any exception is a separately
labelled intervention and cannot silently replace a primary result.

Report per-case and per-track joint successes/planned trials, equivalence and
mission separately, repair counts, stage/total time, managed tool calls, token
usage and missing-usage coverage. Cached input/reasoning counts are subsets.
Offline-regrade every sealed final under the preserved implementation. Corpus
reports retain measured success, selected coverage and full-catalog demonstrated
coverage with all blocked slots. Audit exported totals against individual trials.

Freeze the literal executor configuration and CLI hash. Workers ignore user
configuration, so an unspecified CLI reasoning setting stays explicitly
unspecified; desktop preferences do not establish it. Instrument readings and
bounded repairs are available under `assistive/1`; provided firmware pauses
between live calls, despite older continuous-probe labels in pinned manifests.

For repeated cases, show per-case 95% exact binomial intervals, descriptive and
conditional on comparable independent fresh trials (3/3 is approximately
29%–100%). Do not pool heterogeneous cases or blocked slots into a binomial
interval. These repetitions measure this fixed development benchmark, not new devices.
The cases are familiar development cases; there is no held-out population or
baseline comparison. No causal improvement, model ranking, physical accuracy or
general firmware success rate follows from this study.

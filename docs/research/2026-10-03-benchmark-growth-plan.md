# Benchmark growth plan

Proposed 2026-10-03; inspected `318058f1515d9a912773b656839298eb5883d929`, reconciled with [PR #1](https://github.com/cam-berger/generative-driver/pull/1) at `24d8861fab72a401135a4a71f61e54d73b5ba204`. AI-assisted planning with independent coverage/statistical reviews. No external build, admitted case or new model measurement; this is neither preregistration nor execution authorization.

Question: how reliably and efficiently can an agent recover an unfamiliar supplied MCU image into a driver a fresh agent can use? Initially acquisition means importing a binary; reading chips is a separate study. Preserve the [six-stage endpoint](../superpowers/specs/2026-10-03-reuse-endpoint.md).

## Starting point

Assessment: useful evaluation machinery, narrow device coverage. The [registry](../../src/generative_driver/benchmark_support/registry.py) has six profiles, but only three research firmware families. The [default suite](../../src/generative_driver/resources/bench/suites/development-pilot.json) schedules three originals × three repetitions, not nine devices. Earlier variants were not independent families.

| Starter family | Useful coverage retained at fresh reuse | Important boundary |
|---|---|---|
| TQ9 controller | Numeric decoding, enable/set/disarm, permitted effects, independent PWM observation | One emulated controller; no physical actuator or real-time control claim |
| Sampled sensor | Fresh acquisition, signed/scaled values, framed responses, sequence counters | Limited framing and timing diversity |
| Parameter store | Pending/committed values, transaction ordering, bank isolation, abort, generation | Volatile state; does not establish persistence across power loss |

All three use emulated STM32/UART-over-TCP; they do not establish silicon timing or broad hardware compatibility. BME280 is a separate physical/datasheet track. [PR #1's 30 family and two legacy native sessions](https://github.com/cam-berger/generative-driver/blob/24d8861fab72a401135a4a71f61e54d73b5ba204/docs/implementation/benchmark-final-verification.json) qualified evaluators, not models. Contracts live in [TQ9](../../src/generative_driver/benchmark_support/tq9_v2.py), [sensor](../../src/generative_driver/benchmark_support/sampled_sensor.py), [store](../../src/generative_driver/benchmark_support/parameter_store.py) and [worker assignments](../../src/generative_driver/benchmark_support/emulated.py).

Publish a coverage ledger: each behavior needs a passing reference, meaningful failing mutant, disjoint diagnostic/final inputs and fresh-worker mission. Add 30–50 separate stage diagnostics; they never count as completed workflows.

## Sources and population

The [source review](2026-10-03-benchmark-source-review.md) pins P2IM/dataset-builder, Fuzzware/Hoedur, FirmReBugger, Zephyr, EmbedBench/EmbedEval and closed-loop agent sources. Reuse eligible firmware, provenance and evaluation ideas after license review and ancestry deduplication. Fuzzing/bug oracles do not establish driver semantics. Preserve original/modified identities, especially removed CRC checks or changed timeouts. Firmware-writing tasks need an external interface and new qualification; added interfaces are disclosed transformations.

Use `origin group → lineage → mission → repetition → stage observations`. Group shared challenge-bearing parsers/state machines/application ancestry; shared SDK alone need not merge unrelated apps. Builds, ports and versions stay in one lineage. A mission pairs a frozen image with an objective/contract; repetition starts fresh discovery and reuse. Private episodes are checks within a mission.

Eligible population: source-traceable, reproducible MCU applications with runtime-representable interfaces and independently qualified oracles. Report intake/rejection denominators and conditional scope. Unsupported cases stay visible.

Mature targets:

- Six behavior strata: units/measurement, fresh/streamed acquisition, bounded actuation, transactions/persistence, framing/bulk transfer, sessions/errors/reset. At least ten origin groups per stratum; overlap allowed.
- At least two instruction sets, three OS/bare-metal contexts and three protocol styles. Prove a second architecture beyond Cortex-M; another same-architecture board does not count.
- At least 60/100 lineages from upstream apps without new benchmark commands; no source collection over 40%. Report shared stacks.
- Two substantively different missions per lineage where supported; changed input values alone do not count.
- Separate physical transfer subset of 6–10 matching lineages, several boards and independent instruments after emulator admission. BME280 remains separate.

## Milestones

Targets assume origin-group deduplication; independent group counts may be lower. Two missions is a planning convention, never a quota for duplicate tasks.

| Milestone | Admitted lineages | Distinct mission cases | Allocation and purpose |
|---|---:|---:|---|
| Current starter | 3 | 3 original-image mission cases | Development and evaluator checks; suite schedules 9 repeated trials |
| Representative pilot | 12 | About 24 | Proposed 8 development / 4 validation; measure feasibility, cost, variance and discriminatory value |
| First comparative corpus | 42 | About 84 | The 12 pilot lineages plus 30 untouched test lineages; 60 test mission cases |
| Mature release | 100 | About 200 | Proposed 20 development / 20 validation / 60 test; 120 test mission cases |
| Precision extension | About 140 | About 280 | Retain 40 development/validation and obtain about 100 untouched test lineages when needed |

At three repetitions: 72 pilot slots/configuration, 180 first-comparison test slots, 360 mature test slots. Every slot starts a full fresh workflow; sharing discovery defines another experiment. Promotion requires admission, diversity and measurement gates. These extend the [historical roadmap](https://github.com/cam-berger/generative-driver/blob/7d3344c0e3e8f477dfcf5e6ea1ca00ca7e4f089c/docs/superpowers/specs/2026-09-30-benchmark-expansion.md#11-deliberate-later-increments).

## Study design

Primary endpoint: autonomous success within fixed budget, all six gates, fresh package-only mission and sealed final behavior. Repairs consume budget/history; report first-attempt success separately. Final failure is terminal.

Average repetitions/missions within lineage, then within origin group; weight groups equally. Freeze weighting. Retain planned-slot success as a separate operational measure with its denominator.

Compare the staged workflow with one discovery agent retaining context through emit, followed by separate fresh reuse. Match model, evidence, tools, task and total budget. A thin adapter records the same artifacts/evidence at functional checkpoints without adding interpretation, feedback or answers. Qualify it with correct/incorrect scripted candidates first. This measures orchestration plus context structure.

Ablations: fixed probe policy versus agent-selected probes, and bounded repair versus one submission. Preserve diagnostic information type, host gates, grants, budgets and final tests. A no-probe arm would need another endpoint and is outside this study. Changing runtime and model together is a system comparison.

Freeze one primary contrast and practical threshold using development data before test access; 15 percentage points is a proposal. Other comparisons are secondary or multiplicity-adjusted. Pair missions/environments, randomize/interleave blocks and record versions/timestamps. Start with three repetitions; choose three or five from pilot variation. Favor additional independent groups over repeats of stable cases.

Use paired 95% intervals by resampling whole origin groups with both methods and nested rows intact. Also report fixed-corpus repetition variation. Few-group results remain descriptive. For stages, report unconditional achievement and success conditional on reaching the stage, with both denominators.

Retain every scheduled eligible slot. Unresolved host/operator/unknown outcomes are not autonomous successes; preserve attribution, show valid-execution results secondarily and bound uncertainty by treating unresolved results as all-pass/all-fail. Preregister bounded infrastructure retries; retain original attempts. Report success, time, resources, interventions and usage coverage together.

## Precision and power

Illustration only: independent binary outcomes at 50% have Wilson 95% halfwidth `1.96 / (2 * sqrt(n + 1.96²))` ([NIST](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)).

| Independent binary cases | Approximate halfwidth |
|---:|---:|
| 12 | ±24.6 percentage points |
| 30 | ±16.8 percentage points |
| 60 | ±12.3 percentage points |
| 100 | ±9.6 percentage points |

These are not intervals for nested mission scores. A rough paired-group approximation is `n ≈ 7.85 * SD(D)² / delta²` for two-sided 5% significance/80% power. Proposed delta 0.15 and unmeasured SD 0.25–0.35 suggest 22–43 groups. Simulate pilot nesting, missingness and allocation before freezing sample size. [Paired binary power depends on discordance](https://www.stata.com/manuals/pss-2powerpairedproportions.pdf).

Prespecify interpretation: interval above the practical threshold supports material improvement; above zero but crossing the threshold leaves practical size unresolved; crossing zero is inconclusive; below zero supports degradation. Equivalence needs its own margin. Rejecting zero differs from exceeding a practical threshold; 80% power for a lower bound above 15 points requires a planning effect greater than 15 points. Never add samples because test results look favorable.

## Admission and holdout

Require source/commit, asset-specific license, origin group, build/patch hashes, deterministic reset, bounded objective, transport/effects, independent observations and reproducible reference execution. Upstream tests support, but do not automatically complete, the oracle.

Use two independent host references where feasible; otherwise document alternative evidence/limits. Match mutants to claimed behavior: units/sign, stale reads, missing effects, transaction order, bank leakage, CRC/length, reset/persistence. Benign alternative implementations must pass. Version disjoint diagnostic/final vectors; source similarity/style never determines functional correctness.

Split origin groups before tuning; all relatives share a split. Existing cases are development data. Retire a test cohort once its results inform changes; replacements may require more than 100 total assets. Public source may be in pretraining; holdout does not prove no contamination.

Blind-study claims require platform checks that workers cannot read evaluator files, use observer channels or fetch held-out sources. Native account/process boundaries or optional isolation may enforce this; encryption/hashes alone do not. Provide versioned researcher scoring/reproduction access and retire/publish assets where rights permit.

## Work packages

| Work package | Concrete output and code seam | Exit evidence |
|---|---|---|
| 0. Reuse endpoint | Preserve the implemented [endpoint plan](../superpowers/plans/2026-10-03-reuse-endpoint.md) as the regression baseline | Six-stage source/installed checks, fresh mission/final grading, matching native calibration and nine-slot suite expansion |
| 1. Corpus intake | Screen an initial 40 candidates; add a provenance/coverage ledger with accepted/rejected/pending reasons | Asset rights, ancestry deduplication and a ranked admission backlog; report actual yield |
| 2. Three external proofs | Attempt P2IM Gateway, P2IM CNC and Zephyr Modbus server, replacing only for documented admission failure | Reproduce each upstream behavior, qualify independent effects, demonstrate a fresh recovered package |
| 3. Case/mission adapters | Generalize `registry.py`, `emulated.py`, `authoring.py`, `calibration.py` from these concrete imports | Data-driven missions and supported build/platform adapters; preserve the trusted adapter allowlist and native core |
| 4. Representative pilot | Admit 9 external lineages alongside the current 3; add separate stage diagnostics | Coverage gaps closed or disclosed; model pilot estimates costs, missingness, floor/ceiling and group-level variance |
| 5. Study runner/reporting | Extend `suites.py`, suite storage and `suite_reporting.py` with mission identity, grouped metadata, frozen run order and uncertainty | Resume preserves the exact schedule; saved evidence independently reproduces denominators, pairings and intervals |
| 6. First comparison | Freeze settings and 30 untouched origin groups, then run the prespecified comparison | Complete result ledger, effect intervals, failure analysis, independent regrade and reproduction by another operator |
| 7. Mature corpus | Grow toward 100 lineages and a separate physical transfer subset | Diverse admitted population, fresh holdout, native host qualification and release snapshot |

These are future implementation seams, not callable APIs. Registry/missions are hardcoded; suite entries lack mission identity, order is deterministic and reports lack intervals. Version these contracts; do not overload evaluator `case_seed` as a model seed.

Current scheduling allows one active child and a seven-day suite budget. At three hours each, 360 slots allow 1,080 hours/configuration. Use frozen disjoint shards and a common study index; resume/retry must not duplicate slots. Parallel hosts need separate owned resources and recorded environments. Forecast cost from pilot durations.

Before confirmation, freeze population/intake/splits/contracts; primary contrast/threshold; models/settings/permissions; repetitions/order/budgets/retries; weighting/intervals/missingness/multiplicity/stopping; evaluator/build identity and scoring access. Cross-platform execution, imported oracles, protocol breadth, asset yield and costs remain unmeasured. Admit three external applications first; observed exclusions/costs decide the next batch.

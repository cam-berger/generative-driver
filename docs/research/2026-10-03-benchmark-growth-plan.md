# Growing Generative Driver into a research benchmark

Date: 2026-10-03. Status: proposed research and implementation roadmap. Baseline inventory inspected at `318058f1515d9a912773b656839298eb5883d929`, then reconciled with [PR #1](https://github.com/cam-berger/generative-driver/pull/1) at `24d8861fab72a401135a4a71f61e54d73b5ba204`. This roadmap contains recommendations and source inspection, with no new model-performance measurements. Prepared with AI-assisted research and independent coverage/statistical design reviews.

## Recommendation

Keep the small benchmark as a development and evaluator-calibration suite. Grow first to 12 distinct firmware lineages, then reserve 30 additional lineages for a first comparative study. Target 100 admitted lineages and approximately 200 mission cases for the larger release, subject to intake yield, pilot variance and execution cost. Count independent implementations separately from missions and repetitions.

The endpoint implemented in PR #1 follows the [fresh reuse specification](../superpowers/specs/2026-10-03-reuse-endpoint.md): acquire → interpret → probe → ground → emit → reuse, on a stable firmware image. Keep this endpoint and its qualification as the foundation for expansion.

Research question: **Given an unfamiliar, supplied microcontroller firmware image and a device objective, how reliably and efficiently can a configured agent system recover a behaviorally correct interface that a fresh agent can use?** Initially, “acquire” means importing the supplied binary. Reading firmware from an attached chip requires a separate acquisition study.

## 1. Does the small benchmark represent the intended task?

Assessment: it represents the evaluation process well, but samples a narrow device population. The [registry](../../src/generative_driver/benchmark_support/registry.py) contains six profiles, including installation replay, legacy TQ9 and the optional physical sensor. The research core comprises three firmware families. The earlier seven scenarios were variants within these families, and its 18-slot pilot did not represent 18 devices. PR #1 replaces that pilot with [three original-image cases × three repetitions = nine planned trials](../../src/generative_driver/resources/bench/suites/development-pilot.json). Those remain three firmware families, with model-performance measurements pending.

| Starter family | Useful coverage retained at fresh reuse | Important boundary |
|---|---|---|
| TQ9 controller | Numeric decoding, enable/set/disarm, permitted effects, independent PWM observation | One emulated controller; no physical actuator or real-time control claim |
| Sampled sensor | Fresh acquisition, signed/scaled values, framed responses, sequence counters | Limited framing and timing diversity |
| Parameter store | Pending/committed values, transaction ordering, bank isolation, abort, generation | Volatile state; does not establish persistence across power loss |

Sources: [TQ9](../../src/generative_driver/benchmark_support/tq9_v2.py), [sensor](../../src/generative_driver/benchmark_support/sampled_sensor.py), [store](../../src/generative_driver/benchmark_support/parameter_store.py), [worker assignments](../../src/generative_driver/benchmark_support/emulated.py).

All three use one emulated STM32 platform and UART-over-TCP. Supplied binaries, emulator observations and functional host packages form a coherent initial scope. They cannot establish silicon timing, diverse architectures or general device compatibility. BME280 is a separate datasheet/physical track. PR #1 records 30 native family reference/mutant sessions and two legacy sessions; these qualify evaluators and are not model-performance trials ([pinned verification record](https://github.com/cam-berger/generative-driver/blob/24d8861fab72a401135a4a71f61e54d73b5ba204/docs/implementation/benchmark-final-verification.json)).

Before expansion, publish a coverage ledger and preserve these examples as development data. For each required behavior, record a positive reference, a meaningful wrong implementation that fails, disjoint diagnostic/final inputs, and a fresh-worker mission. Add 30–50 standardized stage diagnostics as a separate suite to expose interpretation, probing and reuse failures hidden by upstream failure. Stage diagnostics never count as completed workflows.

## 2. Build on existing work

The [source review](2026-10-03-benchmark-source-review.md) records inspected sources, revisions and reuse boundaries. These are candidate inputs and methods, not already admitted cases.

| Source | Proposed use | Admission work |
|---|---|---|
| [P2IM real firmware](https://github.com/RiS3-Lab/p2im-real_firmware) | External applications: begin screening CNC, Gateway, PLC and Console | Resolve upstream licenses, remove or account for research instrumentation, reproduce observable behavior |
| [Fuzzware](https://github.com/fuzzware-fuzzer/fuzzware-experiments) and [Hoedur](https://github.com/fuzzware-fuzzer/hoedur-experiments) | Firmware/build provenance, platform configurations and further targets | Deduplicate shared P2IM/uEmu ancestry; establish functional oracles independently of fuzzing models |
| [FirmReBugger](https://github.com/FirmReBugger/FirmReBugger) | Target inventory and explicit oracle methodology | Audit modifications and asset permissions; bug detection is a different outcome from driver correctness |
| [Zephyr samples and Twister](https://docs.zephyrproject.org/latest/develop/twister/index.html) | Reproducible applications, host interactions and test fixtures | Extract bounded device missions, independent observations and frozen binaries |
| [EmbedBench](https://github.com/icip-cas/EmbedAgent) and [EmbedEval](https://github.com/Ecro/embedeval) | Coverage categories, task construction and evaluator checks | Any converted task becomes an explicitly derived firmware-recovery case with new behavioral qualification |
| [Closed-loop embedded-agent benchmark](https://github.com/jgcarrasco/closed_loop_evaluation_agents_embedded) | Plant observations and separation of visible feedback from final grading | Inspect permissions and adapt only where a recoverable external interface exists |

A compiled firmware-writing exercise is not automatically a driver-recovery task. If an application has no externally controllable interface, either exclude it from the primary corpus or label an added interface as a benchmark-authored transformation. Hashes, renamings, builds, target ports and mutations remain members of their original lineage.

Do not run imported fuzzing environments as the behavioral truth by default. For example, FirmReBugger's published modification table includes removal of a PLC CRC check and changes to timeout handling. Those changes matter to the interface we aim to recover. Preserve original and patched identities and qualify their effects before inclusion.

## 3. Units, population and coverage

Use this hierarchy:

`origin group → firmware lineage → mission case → agent repetition → stage observations`

An origin group joins implementations sharing the challenge-bearing parser, state machine or application ancestry. Unrelated applications sharing an SDK need not collapse into one group. A lineage contains its builds, versions and ports. A mission case pairs one frozen image with an objective and observable acceptance contract. A repetition starts independent discovery and fresh reuse contexts. Private test episodes are checks inside a mission.

Primary population: source-traceable microcontroller applications that can be reproducibly executed, expose an interface representable by the package runtime, and have independently qualified behavioral oracles. Describe results as conditional on that eligible corpus. Publish the intake denominator and rejection reasons; successful rehosting alone is insufficient.

Proposed coverage targets for the mature corpus:

- Six behavior strata: measurement/units; fresh or streamed acquisition; bounded actuation; transactions/persistence; framing/bulk transfer; sessions/errors/reset recovery. Give every stratum at least ten origin groups; groups may cover multiple strata.
- At least two instruction-set architectures, three OS/bare-metal contexts and three protocol styles. A second board with the same CPU architecture does not satisfy architecture diversity. Start with Cortex-M and prove one additional architecture through an admission pilot.
- At least 60 of 100 lineages from upstream applications with no new benchmark-authored command interface. Cap each upstream application collection at 40% to limit source concentration; report shared-stack groups separately. These are acquisition goals, not evidence that eligible assets already exist.
- Two substantively different missions per lineage where supported: for example acquire a fresh calibrated reading, then configure acquisition and validate its response. Equivalent input values do not create a second mission.
- A separate physical transfer subset of 6–10 matching firmware lineages across several boards, after emulator admission. Device access and independent instruments must support the same mission. The existing BME280 profile remains a different evidence track.

Use original upstream behavior as the default. More complex variable-length messages, asynchronous operations and new bindings enter only after the runtime can express their contracts. Unsupported cases stay visible in intake statistics rather than being quietly replaced by easier ones.

## 4. Growth milestones

All counts below are targets. “Two missions” is a planning convention, not a reason to invent duplicate tasks. Counts assume lineages survive origin-group deduplication; otherwise the independent group count is smaller.

| Milestone | Admitted lineages | Distinct mission cases | Allocation and purpose |
|---|---:|---:|---|
| Current starter | 3 | 3 original-image mission cases | Development and evaluator checks; suite schedules 9 repeated trials |
| Representative pilot | 12 | About 24 | Proposed 8 development / 4 validation; measure feasibility, cost, variance and discriminatory value |
| First comparative corpus | 42 | About 84 | The 12 pilot lineages plus 30 untouched test lineages; 60 test mission cases |
| Mature release | 100 | About 200 | Proposed 20 development / 20 validation / 60 test; 120 test mission cases |
| Precision extension | About 140 | About 280 | Retain 40 development/validation and obtain about 100 untouched test lineages when needed |

Repetitions are additional runs, never additional mission cases. With three repetitions, the pilot is 72 slots per configuration; the first comparison has 180 test slots; the mature release has 360 test slots. Default costing assumes a fresh complete workflow for every slot. Reusing discovery artifacts across missions would define another experiment and needs explicit reporting.

These targets extend the [historical expansion specification](https://github.com/cam-berger/generative-driver/blob/7d3344c0e3e8f477dfcf5e6ea1ca00ca7e4f089c/docs/superpowers/specs/2026-09-30-benchmark-expansion.md#11-deliberate-later-increments) while respecting the later fresh-reuse endpoint. Promotion requires the milestone's admission, diversity and measurement gates, rather than a row-count threshold alone.

## 5. Scientific design

Primary endpoint: autonomous success within a fixed budget, requiring all six stages, the fresh agent's package-only mission and sealed final behavior checks. Bounded pre-final repairs count in the budget and history. Report first-attempt success separately. A failed final check remains terminal.

For configuration A, average the binary episode outcomes over repetitions and missions within each lineage, then within origin group, then give each origin group equal weight. Freeze this weighting before trials. Retain the current planned-slot success fraction as an operational metric and publish its denominator alongside the new scientific aggregate.

Primary comparison: the complete staged workflow versus one discovery agent retaining context across acquire through emit, followed by a separate fresh reuse agent. Match the model, binary evidence, available tools, task and total budget. A thin comparison adapter records the same models, transaction/grounding evidence and emitted package at the same functional checkpoints; it must add no interpretation, corrective feedback or reference answers. Both arms can meet the six acceptance gates without imposing our worker decomposition on the simpler arm. Qualify the adapter using correct and incorrect scripted candidates before inference. This contrast measures the combined effect of orchestration and context structure.

Component ablations compare a fixed, declared probe policy with agent-selected probes, and bounded repair with one submission. The fixed-probe arm receives the same type of diagnostic observations and passes the same host checks; it changes who selects probes, rather than removing a required gate. Match tool/effect permissions and total budgets, and retain identical sealed final tests. A true no-probe arm would need a separately defined common endpoint and is outside this first study. Changing model and runtime together is a system comparison.

Freeze one primary contrast and a minimum meaningful difference. A proposed starting target is a 15 percentage-point absolute improvement; select the final target and sample size using development data before accessing test outcomes. Broader model comparisons, stage outcomes and subgroup results are secondary or use a stated multiplicity adjustment.

Pair configurations on every mission and environment. Randomize and interleave execution blocks, record model/runtime versions and service timestamps, and preserve resource budgets. Start with three fresh repetitions during the pilot; choose three or five for the main study from observed within-lineage variation. More lineages generally address diversity better than repeated attempts on an already stable case.

Report paired 95% intervals by resampling complete origin groups with both methods and their nested rows kept together. Also report fixed-corpus variation across agent repetitions. Few-group results remain descriptive; a hierarchical model cannot manufacture independent examples. Use per-stage unconditional achievement and conditional success among runs reaching the stage, with both denominators.

Every scheduled eligible slot stays in the result. Unresolved host/operator/unknown outcomes are not autonomous successes and retain their actual attribution. Show valid-execution results secondarily and sensitivity bounds that treat unresolved outcomes as all successes or all failures. Preregister a bounded infrastructure-retry rule that preserves original attempts. Report success jointly with time, resource consumption, interventions and usage completeness; successful-run latency alone is insufficient.

### How much precision does size buy?

For intuition only, at a 50% success rate the 95% Wilson halfwidth for independent binary cases is `1.96 / (2 * sqrt(n + 1.96²))` ([NIST](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)).

| Independent binary cases | Approximate halfwidth |
|---:|---:|
| 12 | ±24.6 percentage points |
| 30 | ±16.8 percentage points |
| 60 | ±12.3 percentage points |
| 100 | ±9.6 percentage points |

These are not intervals for our nested mission scores. They show why 200 mission rows do not guarantee precise rankings. For paired origin-group differences D, a rough planning approximation is `n ≈ 7.85 * SD(D)² / delta²` for two-sided 5% significance and 80% power. At delta=0.15 and SD(D)=0.25–0.35, this suggests roughly 22–43 independent groups; neither variance value has been measured here. Validate sizing by simulation preserving pilot nesting, missingness and allocation before freezing the test cohort. Paired binary calculations depend on discordance ([Stata documentation](https://www.stata.com/manuals/pss-2powerpairedproportions.pdf)).

Use a prespecified interpretation: an interval entirely above the meaningful-difference threshold supports a material improvement; above zero but overlapping the threshold supports improvement with unresolved practical size; overlapping zero is inconclusive; entirely below zero supports degradation. Equivalence requires its own prespecified margin and interval criterion. Power to reject zero is different from power to exceed the practical threshold; simulate the criterion actually used. Establishing a lower confidence bound above 15 points requires a planning alternative above 15 points; a true effect exactly at that boundary cannot supply 80% power for that claim. No sequential additions based on favorable test results.

## 6. Admission, truth and holdout policy

Every admitted case needs an upstream source/commit and asset-specific license record, origin-group assignment, original/build/patch hashes, deterministic reset, bounded objective, transport and effect contract, independent observations, and reproducible reference execution. Store upstream tests as supporting evidence; they do not automatically become a complete oracle.

Qualify each oracle with two independently implemented host references where feasible, otherwise record the alternative independent evidence and its limitation. Run defects matched to the claimed behavior: wrong signedness/units, stale reads, missing effects, bad transaction order, cross-bank leakage, ignored CRC/length, and incorrect reset/persistence semantics where applicable. A benign alternative implementation must pass. Disjoint diagnostic and final vectors are versioned. Candidate source similarity and style are never the functional criterion.

Assign origin groups to development, validation and test before prompt/tool tuning. Keep all relatives in one split. Existing cases are development material. Test status is relative to a frozen study: once case-level results inform changes, retire that cohort from the next confirmatory study and recruit replacements. This can require more than the nominal total of 100 assets. Public upstream source may have appeared in model pretraining; grouped holdout does not establish absence of training contamination.

For a blind-study claim, verify on each execution platform that worker credentials cannot read evaluator files, invoke observer channels or fetch held-out source through unrestricted network access. Use native account/process boundaries or optional isolated workers; containers remain optional. Password encryption and hashes continue to protect stored evidence and identity, but do not establish this execution boundary. Publish a researcher access route for scoring/reproduction; publish retired evaluation assets where permissions allow, or provide a reproducible evaluator service with versioned artifacts.

## 7. Work packages and exit gates

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

These are implementation-planning seams, not new callable APIs. Current registration and family missions are hardcoded; copying a manifest is insufficient. Current suite entries have no mission field, execution order is deterministic, and reporting has no statistical intervals. Version those contracts rather than overloading `case_seed`. Case seeds vary device checks; they are not model sampling seeds.

The scheduler currently permits one active child and limits suite budgets to seven days. At existing three-hour child ceilings, 360 slots have a 1,080-hour upper bound per configuration. Plan frozen, disjoint suite shards with a common study index and offline aggregation. Each shard remains owned by its configurator; retries/resume cannot duplicate slots. Parallel hosts need separately owned resources and recorded environments. Use measured pilot durations for actual time/cost forecasts, not the single intervention-heavy legacy run.

## 8. Decisions to freeze before a confirmatory run

Record the population, intake flow, origin groups/splits, accepted cases and executable contracts; one primary comparison and practical effect threshold; models/runtimes/settings; evidence/tool/network permissions; repetitions, budgets, blocking/order and retry rules; weighting, confidence intervals, missingness, multiplicity and stopping rules; evaluator/build identities; and scoring access policy. The freeze is a later experiment artifact. This roadmap is not a preregistration or execution authorization.

The source scan was targeted and primary-source based, not exhaustive. No external firmware was built or admitted during planning. Cross-platform execution, imported-oracle quality, representable protocol breadth, 100-lineage availability and run costs remain unmeasured. The immediate decision gate is successful admission of three external applications; their observed costs and exclusions determine the next acquisition batch.

Planning review: independent coverage and statistical audits checked the unit hierarchy, scientific endpoint, intake policy and counts. Their comparison-arm finding was addressed by preserving functional checkpoints and using fixed-probe and repair-budget ablations. Arithmetic and local document references are checked separately from future experimental validation.

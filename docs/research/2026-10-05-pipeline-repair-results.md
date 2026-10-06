# Pipeline repair experiment — 2026-10-05

## Question and frozen setup

Can the existing pipeline discover a functional driver and let a fresh agent operate it on each of the three authored firmware families? This engineering rerun follows an inspected failed experiment. Every stage uses `gpt-6.1-sol` at medium reasoning through Codex CLI 0.159.2 on native macOS. There is one original-image workflow per family, seed 0, a three-hour budget per workflow, sequential execution, and at most two controller-managed model repairs before final evaluation. The six stages are acquire, interpret, probe, ground, emit, reuse.

The frozen implementation is `9929e3053135257cd8f9940bd2cb5a4709397d49`. Its evaluator identity is `054d88f14c127bbb3759131569500f864d5c8aad2a67755a670f317867b52ac2`. Case and evaluator versions remain 3/3. The candidate firmware images, authored behavioral contracts, scenarios and seeds are unchanged from the earlier experiment. Generated build and calibration records have new commitments; refreshed authenticated qualifications match the new installed evaluator. Raw evidence and credentials remain evaluator-owned.

## Pipeline changes

Interpretation instructions permit the supplied installed analysis executables and runtime libraries outside the task workspace. Analysis outputs remain inside it. The worker receives the known TCP emulator channel before interpretation; physical UART settings remain explicitly unverified. Real UART validation still requires actual connection settings.

Managed probing returns independent before/after measurements. Diagnostic failures identify observed discrepancies and affected checks without disclosing answer keys, tolerances or held-out final vectors. Missing, malformed or incomplete capability mappings become candidate-model feedback and can trigger bounded reinterpretation. Filesystem permission and host-tool failures retain their separate failure category. Final evaluation freezes the candidate and remains terminal.

## Earlier experiment

The earlier same-model, medium-reasoning experiment completed 0/3 fresh-reuse workflows. TQ9 accepted acquisition and interpretation, then stopped at probing with unresolved units and capability mapping. The sensor and parameter-store families accepted acquisition, then exhausted two interpretation repairs on missing physical UART settings despite running through a TCP emulator. All seven interpretation workers declined the supplied analysis tools because of conflicting workspace instructions. None reached independent diagnostic or final evaluation.

| Family | Accepted stages | Model repairs | Elapsed minutes |
|---|---:|---:|---:|
| TQ9 | 2/6 | 0 | 9.1 |
| Sampled sensor | 1/6 | 2 | 14.3 |
| Parameter store | 1/6 | 2 | 15.9 |

The earlier elapsed suite time was 41.0 minutes, including a recorded manual dispatch gap. Its 11 workers reported 2,406,167 tokens, including 1,927,424 cached input tokens; cached tokens are a subset of the total. Original failures and the separate dispatch record are preserved.

## Rerun results

The frozen rerun completed 0/3 qualified reuse workflows. Recorded and independently regraded suite outcomes agree. Each family's current revision accepted acquisition and interpretation; none reached ground, emit, reuse or final evaluation.

| Family | Outcome | Repairs | Diagnostic checks, by revision | Minutes | Reported tokens |
|---|---|---:|---|---:|---:|
| TQ9 | Model blocked | 2 | 6/9, 6/9, 6/9 | 26.9 | 2,595,164 |
| Sampled sensor | Model blocked | 2 | 1/17, 1/17, 1/17 | 20.2 | 1,970,648 |
| Parameter store | Unknown harness stop | 0 | Not reached | 8.9 | 805,005 |

All seven interpretation workers used the analysis tools. TQ9 repaired its temperature units, but subsequent mappings used an unsupported copy field and omitted output units. Direct duty probes worked; malformed mappings failed before device execution. Sensor mappings in all three revisions used unsupported structures. Separate candidate defects included raw temperature scaling and an effect grant. Repeated totals therefore conceal different failure causes and do not represent independent protocol failures.

The store worker identified unit and composite-operation discrepancies and requested revision. An argument-name refusal occurred before transport opened, but the controller classified the failed write call as an uncertain device action and stopped before diagnostic checking or repair. That trial retains its original unknown attribution. After the worker and emulator had stopped, one recorded cleanup-only confirmation cleared uncertainty. There was no resume, candidate change or further inference.

The 17 workers reported 5,370,817 total tokens, including 4,593,152 cached input tokens, with 17/17 usage coverage. Cached input is a subset of total usage. Four repairs added eight assignments. Suite creation through the last blocked outcome took 56.1 minutes; through the later cleanup-only confirmation it took 62.4 minutes. Preflight time is excluded. All 98 managed operations have matching finishes; frozen runtime, executable and evaluator identities remained unchanged. Independent execution review found no observed boundary bypass; this is an audit result, not proof of sandbox enforcement.

## Follow-up corrections

The failures exposed additional mechanical gaps. The follow-up supplies the exact capability format and a standalone candidate-only validator, validates mappings before diagnostic I/O, and carries field-specific binding errors into repair feedback. It distinguishes a trusted refusal before transport opens from failed or interrupted transport activity. Cleanup status now reads the durable outcome and worker ownership under one lock, preventing a completed/stopped combination from concealing a cleanup failure. These changes require a newly qualified build and separately identified model trial; they are not retroactively applied to the results above.

## Software and evaluator verification

The separately qualified [capability repair experiment](2026-10-05-capability-repair-results.md) now records the follow-up trial. Its outcomes and 432-test verification belong to that later frozen implementation; the table below retains this experiment's original checks.

| Local macOS environment | Tests | Seconds | Skips | Result |
|---|---:|---:|---:|---|
| Source, Python 3.13.12 | 426 | 69.125 | 3 archive gates | Passed |
| Installed wheel, Python 3.11.15 | 426 | 64.839 | 0 | Passed |
| Installed wheel, Python 3.13.12 | 426 | 70.756 | 0 | Passed |

Installed suites ran from the extracted source archive outside the checkout, with installed imports verified and wheel/source-archive gates enabled. Setup replay passed and is excluded from model outcomes. Thirty native calibration sessions comprise 12 correct-reference sessions and 18 required behavioral mutants. References passed, mutants were rejected by their required checks, and all 30 saved grades independently reproduced. All three authenticated admissions passed against the matching installed evaluator.

## Interpretation limits

This is a combined pipeline repair on familiar authored emulated cases, with one trial per family. It does not isolate an individual fix, compare model families, estimate population reliability or demonstrate physical-device performance. There were no physical-device operations. Candidate filesystem reads and direct-device isolation remain unverified; password and hash separation do not establish an enforced sandbox. Accepted controller gates are procedural evidence; the ground gate does not independently validate the agent's written reasoning. A missing final evaluation means final behavior was not reached, rather than failed executed final checks.

Use [the benchmark guide](../../bench/README.md) and [groundtruth instructions](../../bench/groundtruth/README.md) to set up and reproduce a separately identified run. Further repetitions and firmware lineages should follow successful closure of this feasibility check.

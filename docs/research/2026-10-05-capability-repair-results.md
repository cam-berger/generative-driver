# Capability repair experiment — 2026-10-05

## Frozen setup

This engineering rerun follows the [earlier pipeline repair](2026-10-05-pipeline-repair-results.md). It uses `gpt-6.1-sol`, medium reasoning, Codex CLI 0.159.2 and native macOS emulation: one original-image trial per authored family, seed 0, sequential execution, three hours per workflow and at most two model repairs. Acceptance requires acquire, interpret, probe, ground, emit and fresh reuse, including independent final behavior. There were no physical-device operations.

The frozen source is `041c0ebc831df0bb1839d5918601413020bed65a`; the qualified installed evaluator identity is `719de1e7232ebe7484cd34d1adf1d2b158c11bfc6a99e684f70778ff272de2eb`. Case/evaluator versions remain 3/3. Firmware and authored behavioral contracts, scenarios and seeds are unchanged; generated build/calibration commitments and authenticated qualifications were refreshed before inference. Runtime, tools, evaluator and budgets remained unchanged during the run.

The combined changes supply an exact capability format and candidate-only validator, validate mappings before diagnostic I/O, forward bounded mapping errors, distinguish a refusal before transport opens from uncertain device activity, and take cleanup status under one lock. They do not isolate the contribution of an individual fix.

## Results

Qualified autonomous completion was 0/3. Recorded and independently regraded success outcomes agree for all three trials. All final behavioral evaluations were not reached; a partial report's default failed verdict with zero checks is not an executed final failure.

| Family | Current accepted gates | Repairs | Diagnostic checks by revision | Minutes | Reported tokens |
|---|---:|---:|---|---:|---:|
| TQ9 | 3/6 | 1 | 6/9 → 9/9 | 19.6 | 1,716,616 |
| Sampled sensor | 2/6 | 2 | 11/17 → 2/17 → 12/17 | 18.6 | 1,907,847 |
| Parameter store | 2/6 | 2 | Mapping rejected → 156/174 → mapping rejected | 23.8 | 2,327,254 |

TQ9 accepted acquisition, interpretation and probing. Its ground worker requested revision, but the ground checker returned a model failure without a repair route or feedback, stopping with one repair still available. The ground handoff also omitted accepted capability and raw transaction evidence needed for the assessment. Differing labels for raw duty outputs and independently observed effects do not establish a functional duty failure: those raw outputs were outside the mapped canonical outputs, and the diagnostic suite passed 9/9. Grounding and final reuse remain unproved.

The sensor exhausted two repairs. Candidate gaps included missing measurement outputs and temperature conversion/units; an intermediate effect classification also prevented acquisition under the assigned grant. Repair preparation supplied genuine independent measurements, but retained generic blind-interpretation instructions. The final repair explicitly read and discarded that feedback as inadmissible evaluator evidence. The repair handoff also lost earlier useful observations and the actual refusal cause. This is a demonstrated instruction/context conflict alongside genuine candidate defects.

The store initially omitted separate stage, commit and abort tasks. Its first repair passed mapping validation and 156/174 diagnostic checks, but mapped stage to an operation that also cancelled the change; staged-write behavior and read units failed. Its final mapping regressed to compound operations and was rejected before diagnostic execution. Neither repair transcript visibly consumed the supplied defect feedback, and preparation omitted the public task contract and prior candidate/probe artifacts. These revisions are not three independent behavioral evaluations.

Twenty workers reported 5,951,717 total tokens, including 5,109,248 cached input tokens, with 20/20 usage coverage. Cached input is a subset of total usage. Five repairs added ten assignments. Suite creation through the last workflow update took 62.1 minutes, including preparation and stopped intervals; preflight and later verification are excluded. There were no human inputs, resumptions, budget extensions or candidate edits. All 86 managed operations finished, with no remaining uncertain effects or stopping workers. The [verification index](../implementation/capability-repair-verification.json) retains selected measurements and evidence hashes; raw candidate artifacts, credentials and exact archives remain evaluator-owned.

## Software and evaluator checks

| Local macOS environment | Tests | Seconds | Skips | Result |
|---|---:|---:|---:|---|
| Source, Python 3.13.12 | 432 | 72.909 | 3 archive gates | Passed |
| Installed wheel, Python 3.11.15 | 432 | 70.508 | 0 | Passed |
| Installed wheel, Python 3.13.12 | 432 | 78.080 | 0 | Passed |

Installed tests ran from extracted source archives outside the checkout with installed imports verified. The final wheel's 126 entries are byte-identical to the installed-tested wheel; its source archive differs only by one documentation sentence removal. Final archive gates, setup replay and relocated standalone replay passed. Thirty native sessions comprised 12 correct-reference sessions and 18 required behavioral mutants: references passed, mutants were rejected, and all saved grades independently reproduced. All three authenticated admissions passed. These checks qualify software/evaluator execution, not model success.

## Checkpoint and limits

The run is saved as a failure, with mixed candidate and framework causes. Stored `model` categories do not establish intrinsic model incapacity. This outcome-aware rerun on familiar authored cases cannot estimate general reliability, compare models or demonstrate physical performance. Password/hash separation does not enforce filesystem or device isolation. Controller gates are procedural; the ground gate does not independently validate written reasoning.

The next repair must explicitly permit supplied diagnostic observations, preserve safe same-run model/capability/transaction context and task/effect semantics, and route a ground revision through the remaining bounded repair budget. Preserve grant enforcement and hidden final answers. Begin with failing seam tests, then installed verification and matching qualification before another separately frozen trial. Keep medium reasoning. These remaining changes are not implemented at this checkpoint.

The user requested a break after this run. Stop after verification, documentation and commit; launch no further experiments. Resume from the [handoff](../implementation/benchmark-handoff.md). Use the [benchmark guide](../../bench/README.md) for setup and separately identified runs.

# Benchmark expansion specification

Date: 2026-09-30. Branch: `feat/bench`. Design baseline: `e7cbba3`.

Status: proposed specification following the user's acceptance of the expansion sequence. This change contains documentation; implementation and inference have not started.

## 1. Outcome and sequence

Measure whether an agent can recover an unfamiliar firmware interface, demonstrate correct observable behavior, emit a portable package, support fresh-agent use, and maintain that package after a change. Functional compatibility is the criterion. A correct implementation can have different code, operation names, and internal structure from the reference.

Deliver three independently reviewable increments:

1. Register and pin cases; add stronger TQ9 behavioral evaluation, sealed final tests, and semantic/control maintenance scenarios while preserving v1.
2. Add two independently implemented firmware families and calibrate their evaluators without model inference.
3. Add durable suite execution and comparable reports, then run a separately selected six-entry pilot through Codex or Goose.

Read the implementation plans in order: [foundation and TQ9 v2](../plans/2026-09-30-benchmark-01-foundation.md), [new firmware families](../plans/2026-09-30-benchmark-02-families.md), then [durable suites and reports](../plans/2026-09-30-benchmark-03-suites.md). They share the contracts in this specification.

## 2. Global constraints

- Core Python is `>=3.11`; native macOS and Windows 11+ remain supported.
- Original code is Apache-2.0; preserve third-party notices and per-asset license provenance.
- Add no required Python dependencies beyond the existing `mcp>=2.2,<3` and `cryptography>=43`.
- Containers and native analysis tools remain optional; installation smoke never requires inference, hardware, Renode, Ghidra, or an evaluator password.
- The existing background configurator owns runs, workers, device leases, cancellation, and recovery; Goose, Codex, CLI, and suite clients do not become additional owners.
- Run one suite child at a time and one stage at a time per child; never retry an uncertain device effect automatically.
- Preserve TQ9 v1 manifests, binaries, encrypted truth, and the recorded September 28 baseline byte-for-byte; `tq9` continues to mean v1.
- Candidate workers receive no evaluator password, reference source, private expectations, final-test vectors, or maintenance-scenario answer.
- Hashes establish identity and integrity; encryption protects evaluator evidence at rest. Neither establishes operating-system isolation.
- Compare observable outputs, effects, state transitions, and refusals; never require candidate source equality or a preferred code idiom.
- Keep recorded replay, scripted contract fixtures, reference calibration, actual-agent emulation, and physical observations distinguishable.
- Missing usage and unrun stages remain unknown or unrun; retries, failures, interventions, and consumed resources remain visible.
- Existing `benchmark-report/1` and saved v1 scoring remain supported; new records use explicit versioned schemas.
- This branch's planning deliverable does not authorize paid model trials, physical operations, or modification of the frozen baseline.

## 3. Current integration points

| Existing file | Current responsibility and constraint | Integration |
|---|---|---|
| `src/generative_driver/benchmark.py` | Public run/score/compare, CLI, case dispatch; currently enumerates three profiles | Preserve callable entry points; dispatch through registry; add suite subcommands |
| `benchmark_support/cases.py` | TQ9 v1 preparation, qualification, fixed probe goals, identity drift | Retain legacy behavior; new cases use a separate adapter |
| `benchmark_support/physical.py` | BME280 physical flow and operator references | Preserve; register without broadening emulator approval |
| `benchmark_support/emulator.py` | Renode lifecycle, UART socket, independent PWM monitor | Preserve legacy session; introduce new session adapter with explicit stimuli/observation/reset methods |
| `benchmark_support/evaluate.py` | Fixed TQ9 capability execution and UART adaptation | Preserve fixed evaluator; bounded generic bindings and evidence records live in new modules |
| `benchmark_support/ground.py` | Projects accepted observations into worker grounding evidence | Add v2 projection with units/provenance; exclude final-test expectations |
| `configurator.py` | SQLite state, stage routing, scoped grants, one maintenance cycle | Pin case at start, generalize registered emulator approval, dispatch suite methods before run lookup |
| `reporting.py` | Stage metrics; currently looks up the exporter installation's manifest | New reports use run-pinned identity and executed snapshots; retain legacy export interpretation |
| `mcp.py` / `cli.py` / `client.py` | Client access and Windows independent-owner boundary | Route suite calls through the same owner; new profiles receive the same home validation as legacy real-agent runs |
| `benchmark_support/evaluator_cli.py` / `truth.py` | Evaluator-only unlock/rekey/rebuild and authenticated encryption | Reuse envelope; add versioned evidence/build recipes, calibration, and sealed run-evidence export |

Paths prefixed `benchmark_support/` above are beneath `src/generative_driver/`. Installed assets live in `src/generative_driver/resources/bench/`; `bench/` remains documentation and selected reports.

## 4. Case identity, registry, and executed snapshots

Use a static Python registry, not third-party module discovery. A manifest's adapter key selects only a built-in allowlist. Registry loading is read-only and cannot import optional tools, decrypt truth, or start a device.

Public interfaces in `benchmark_support/registry.py`:

```python
@dataclass(frozen=True)
class CaseDefinition:
    id: str
    family: str
    version: str
    evaluator_version: str
    execution: str
    evidence_track: str
    adapter_key: str
    approval_scope: str | None
    default_effects: tuple[str, ...]
    root: Path
    manifest: dict
    manifest_sha256: str

def case_ids() -> tuple[str, ...]: ...
def resolve_case(case_id: str) -> CaseDefinition: ...
def adapter_for(case_id: str): ...
def pin_case(case_id: str, scenario_id: str | None, case_seed: int) -> dict: ...
```

The frozen dataclass is an API convenience; callers receive defensive copies of nested manifests. IDs are explicit (`tq9`, `tq9-v2`, `sampled-sensor-v1`, `parameter-store-v1`), never a moving `latest` alias. Normalize legacy descriptors in memory without rewriting their manifests. TQ9 v1 retains its identity maintenance scenario and receives calibration status `legacy-not-required`; this records the older evaluation policy and cannot qualify a v2 asset. `setup-smoke` has no evaluator credentials.

Reject unknown/duplicate IDs, non-finite/bool seeds, invalid schema/adapter keys, malformed hashes, absolute paths, traversal, symlinks escaping resources, ID mismatch, and changed bytes. Verify the complete case before launching any child. Version 2 manifests contain `family`, `evidence_track`, `scenarios`, `required_stages`, public asset hashes, ciphertext hash, adapter key, execution/time policy, approval scope, effect defaults, provenance and limitations.

`pin_case` returns a JSON object with schema `benchmark-case-pin/1`, complete public manifest, its hash, case ID/version/evaluator version, family, scenario ID, case seed, image hashes, truth hash, execution, evidence track, scope, and time policy. These v2 cases use `execution: actual-agent-emulation`, `evidence_track: firmware`, and `scope: full-workflow`; the three fields distinguish execution method, input evidence, and evaluated scope. `case_seed` selects device/test variation; it is never described as a model sampling seed.

Before the first assignment, snapshot public input bytes, manifest, toolchain/skills hashes, runtime configuration and environment under `runs/<run_id>/benchmark/`. Persist the pin in the run spec as `case_pin`. A repeated start with the same request ID compares the pinned request, not newly resolved assets. Resume verifies snapshot hashes and the evaluator implementation hash; it blocks on mismatch instead of silently changing the case. Preserve per-attempt executed identity separately from exporter identity. Password contents never enter a pin or public event.

Retain existing `prepare_stage`, `check_stage`, `package_execute`, `cleanup`, and `score` signatures. Registry adapters implement those same functions. New adapter implementations receive the pin through evaluator-owned options; worker projections use an allowlist. Generic non-benchmark `driver_start` remains supported.

## 5. Behavioral contracts and evidence

Introduce `benchmark-capabilities/2` for canonical tasks. A capability names a candidate operation, constant parameters, affine canonical-input bindings, and output names/units. A binding with `kind: "copy"` supports bounded scalar/enum parameters; numeric affine bindings default to `kind: "affine"`. It contains no Python, expression evaluator, loops, or shell commands.

```json
{
  "schema": "benchmark-capabilities/2",
  "tasks": {
    "set_output": {
      "operation": "set_percent",
      "constants": {},
      "inputs": {
        "fraction": {"parameter": "percent", "scale": 100, "offset": 0}
      },
      "outputs": {}
    }
  }
}
```

Each family declares canonical task input domains and units. Validate finite numeric coefficients, exact input coverage, candidate parameter existence/type/range, no duplicate parameter destinations or constant/input collisions, and explicit output units. Reject bool as a number and implicit rounding. Integer parameters require exact integral results; a case requiring integer setpoints chooses exactly representable inputs. Fixed parameters cannot satisfy a varying-input task. Affine input bindings do not repair candidate output decoding.

`behavior.py` owns `bind_task(capabilities, task, inputs, model) -> dict`, `validate_records(contract, records) -> dict`, and `project_feedback(records) -> dict`. The first returns `operation` and `parameters`; the second recomputes correctness from independently captured data. The contract declares an exact nonempty set of unique `episode/step/check` IDs. Missing, duplicate, extra, wrong-revision, non-finite or incorrectly typed observations cannot pass. Each result records expected versus observed privately, and exports only the allowed public summary.

The case adapter resets to a declared initial state, applies evaluator-selected stimuli, executes candidate operations through the existing runtime/package boundary and observes independent device effects. Capture raw replies, monitor responses, units, timestamps, stimulus commitment, firmware/model/package hashes, stage/assignment/revision, and simulation-time policy. A failure to observe a value is a host/model failure according to evidence, not a fabricated zero.

Two evaluation sets have different purposes:

- Diagnostic episodes run during probe/ground and can return task names, measured outputs, and bounded defect descriptions. They support up to the existing two model repairs.
- Final episodes run only after an accepted package is frozen. Freeze before both initial reuse scoring and repaired reuse scoring. Final vectors and expected answers are evaluator-only; final failures end that trial without another model repair. Never expose hidden records through `ground.prepare`, worker tools, MCP results, or later prompts.

Use at least three varying sensor stimuli and three output/input targets in each applicable phase, with boundary and signed-value coverage appropriate to the device. The numeric vectors for actual cases live inside encrypted truth; public unit fixtures use independent toy values.

## 6. Maintenance scenarios

Each child run selects exactly one scenario. The configurator's one-maintenance-cycle limit remains sufficient.

| Scenario | Device change | Required outcome |
|---|---|---|
| `identity` | Device identity changes | Package refuses incompatibility; worker reports evidenced drift; rebuild, requalification and fresh reuse pass |
| `semantic` | Observable command/decoding/state behavior changes while identification string stays the same | Worker detects independently evidenced contradiction; one repair cycle restores correct behavior |
| `control` | Firmware and protocol stay unchanged; evaluator changes valid environmental/input values | Existing package continues correctly; no drift claim or maintenance repair |

Only TQ9 v2 must initially provide all three. New families provide `semantic` and `control`. The maintain prompt is identical across scenarios and asks whether the package still satisfies its task, with evidence. It never announces that firmware changed. Host failures, missing tools, timeouts without corroboration, and an unavailable monitor do not establish semantic drift.

Persist an action journal in evaluator state before scenario application: `not_started -> applying -> applied -> observed`. Commit an application nonce and intended image/stimulus hash before I/O. If recovery finds `applying`, block for reconciliation; never inject a second update automatically. UI disconnection has no effect on this journal. Recovery after process loss retains the current controller's explicit-resume policy.

Keep separate fields: `drift_claimed`, `drift_observed`, `false_alarm`, `repair_completed`, `requalified`, and `fresh_reuse_passed`. A correct evaluator detecting drift by itself does not earn worker detection credit. The fresh worker completes a new visible package-only mission; hidden evaluator package calls are recorded separately and cannot count as fresh-worker activity. After a repair, maintenance checks resolution without applying the scenario again. A control false alarm fails the maintenance gate. The worker supplies `maintenance.json` as a hashed report artifact with schema `benchmark-maintenance-claim/1`, `claim` (`unchanged`, `drift`, `unknown`), and `evidence_ids` belonging to its current assignment. Use existing report status `needs_revision` for a drift claim and `completed` for unchanged; keep `agents.REPORT_SCHEMA` unchanged. Validate the artifact inside the workspace and bind each cited event to the active assignment before grading. Raw detection rates include only evaluable scenarios; overall success still counts all planned trial slots.

## 7. New firmware families and six-entry pilot

Start with owned, independently authored Cortex-M firmware running on the already used STM32/Renode platform. Share startup/linker and transport plumbing where appropriate, while implementing distinct protocol/application logic. This increment tests three firmware families on one emulated platform; it does not claim three architectures, native I2C/SPI host transport, physical UART timing, or physical hardware validation.

| Case | Task | Independent evidence | Semantic variation |
|---|---|---|---|
| `tq9-v2` | Recover sensor readings and controlled PWM output | Evaluator-set sensor stimulus plus separate PWM register observation | Decoder scaling changes with stable identification; retain optional identity case |
| `sampled-sensor-v1` | Configure acquisition and recover a signed sample plus sequence/freshness information | Evaluator-controlled input peripheral and acquisition counter, read over a separate monitor connection | Packed numeric representation/scale changes while task units and identification remain stable |
| `parameter-store-v1` | Read a bank, stage bounded updates, commit or abort, then verify state across connections | Separate monitor observation of committed/pending emulated memory and commit generation; no flash-persistence claim | Readback scale changes with stable identification |

The runtime's bounded interface-model/6 grammar is the feasibility boundary: finite fixed exchanges, bounded framing, supported checksums, signed/linear/mask decoding. Do not extend the runtime language just to admit a benchmark case. Avoid unbounded streaming, escaping, arbitrary callback code or unsupported nonlinear compensation.

The initial suite has **six case-scenario entries = three independently implemented families × (`semantic`, `control`)**. These are paired scenarios, not six independent devices. Repetitions and compiler variants do not increase the independent-family count. Report family, case, scenario, revision, variant and repeat separately.

Firmware source, build recipe, reference models, oracle vectors, and stimulus recipes belong in evaluator authoring storage outside the repository/candidate workspaces; release encrypted bundles, pinned binaries and provenance. Keep public toy fixtures for CI distinct from scored firmware. A calibrated case must have two different correct capability/model implementations and mutants for constant output, wrong scaling/signedness, stale data, incorrect state order, and unsupported/false drift as applicable. A mutation is useful only if it changes behavior and a specific required check rejects it.

Rebuild with the TQ9 toolchain pin (Arm GNU Toolchain 14.2.Rel1 / GCC 14.2.1), recording flags and adjacent objcopy version. A compiler change creates new asset identity. Native reference calibration uses explicit Renode/Ghidra/Java paths and captures versions. Calibration records commit to source/image/contract hashes, not to the ciphertext that embeds those records, avoiding a hash cycle. A v2 manifest contains `calibration: {status: pending}` until actual reference execution passes. Only `status: passed` with valid evidence admits a v2 agent run; discovery can list pending definitions. A definition whose assets have not been built cannot return a valid case pin.

## 8. Suite ownership and public interfaces

Create `benchmark_support/suites.py` for validation, stable trial expansion and pure transition decisions; `benchmark_support/suite_store.py` for SQLite storage using the controller's connection lifecycle; `benchmark_support/suite_reporting.py` for aggregation. The controller owns the suite scheduler thread and calls its existing child `start/status/cancel/resume` logic. A suite thread cannot operate a device or start agents directly.

`benchmark-suite/1` contains ID/version, ordered entries `{case, scenario, case_seed}`, repetitions, per-child wall budget, total suite wall budget, and maximum active children fixed to `1`. The selected executor configuration, case pins, tool/skill/environment hashes, grants, and suite manifest hash are frozen when the suite starts. The suite includes only one execution mode and evidence track; physical, replay, and stage-local work cannot enter this emulated aggregate.

Controller methods: `suite_start`, `suite_status`, `suite_events`, `suite_result`, `suite_cancel`, `suite_resume`. Parameters use `suite_id`; events use `after`; results use `offset`/`limit` with maximum `100`. Handle these methods before `_row(run_id)`. CLI: `benchmark cases`, `benchmark run --case ID --scenario ID --case-seed N`, and `benchmark suite start|status|events|result|cancel|resume|report|compare`. The new scenario/seed flags are rejected for profiles that do not declare them; legacy calls retain their defaults. MCP tools: `driver_benchmark_suite_start/status/events/result/cancel/resume`; saved report/compare remain CLI operations for this increment.

The start request contains `manifest` (object), `executor`, `options`, and optional `request_id`. Options carry selected paths, evaluator password-file handles keyed by case ID, and explicit scoped emulator approval; those handles remain evaluator-only. The response returns `{ok, suite_id, duplicate}`. `suite_status` returns lifecycle status, active child ID, planned/completed counts, reason, and `stopping`; it never returns secrets. The CLI may read a manifest file, while MCP passes the object. Both pin identical normalized bytes.

Create `suites`, `suite_trials`, and `suite_events` tables with transactional uniqueness on `(suite_id, ordinal)` and child request IDs. Persist a trial's deterministic request ID before child dispatch, then reconcile via the child's existing request-id deduplication after interruptions. Freeze `executor_config` so configuration edits do not defeat deduplication. Never hold the suite database write transaction while starting or cancelling a child. Multiple user requests cannot launch the same slot twice.

Suite states: `queued`, `running`, `blocked`, `completed`, `cancelled`, `failed`. Child model failure is a completed measurement and the suite proceeds after cleanup; host/operator blockage or uncertain effects blocks the suite. An evaluator rejection currently represented as a blocked run must carry a recorded outcome category before the suite may classify it as a model failure. Never infer fault category by parsing human-readable reason strings. Resume is explicit and acts on the existing child when one exists. Cancel persists intent first, cancels the active child, drains it, and prevents subsequent dispatch. Daemon restart blocks suites with explicit recovery events; UI closure leaves them running. Suite and child budgets include stopped intervals; extensions require explicit reasons and are reported as changed conditions.

Windows MCP uses the independently started configurator for the same home. Registry-based real-agent detection replaces both existing hardcoded MCP case checks. Unsupported profiles fail before owner startup or inference.

## 9. Evidence, reports and comparisons

New cases produce `benchmark-report/2`; suite summaries use `benchmark-suite-report/1`. Include executed case/scenario pins, attempted/accepted stage records, first-attempt outcomes, bounded repairs, neutral maintenance results, final-check counts, all required gates, resource/usage coverage and intervention history. The public report includes measurements and status, not reference vectors, password paths, monitor access details or raw private source. Every public export uses a schema allowlist, not only key-name redaction.

Store the full new-case evaluator record as an authenticated encrypted sidecar with a public ciphertext hash. `benchmark score REPORT --evidence SIDECAR --password-file FILE` regrades that record without launching a device, service, or model. The sidecar includes case pin, exact expected check inventories, measured records, frozen artifact hashes and stage/maintenance evidence. Its `evaluations` array preserves every evaluation with phase, revision, artifact hash, contract and records; initial and repaired packages are different submissions. Regrade required gates against the applicable revision while retaining earlier failures. Verify report/sidecar/pin agreement. An installed evaluator version/hash mismatch is an error. A hash-verified local report is not third-party attestation; the evaluator operator remains trusted. Legacy `score(report, password_file=None)` behavior remains available; add keyword-only `evidence_path=None`.

Primary suite metric is successful complete trials / all planned slots. Publish planned, started, finished, failed, blocked, cancelled and not-run counts; incomplete suites are explicitly provisional. Equal-weight family summaries prevent a large number of related variants dominating the result. Also report success by stage/scenario, first-attempt and repaired results, false alarms, repair time, complete-workflow time, and observed usage coverage. A stage that was never reached remains unrun. Never add overlapping worker/tool/wall durations.

Report repeat dispersion, success counts and denominators. For the six-entry development pilot, use three fresh child runs per entry by default, with a one-repeat development override. Do not present naive confidence intervals treating paired scenarios/variants as independent devices; the pilot is descriptive. Broader studies can add prespecified family-level uncertainty after enough independent families exist. Preserve costs only when supplied by authoritative runtime billing telemetry; tokens are not a bill.

Suite comparison requires the same suite content, entries, case/evaluator/image/truth pins, scenarios, case seeds, repetitions, execution mode, evidence track and budget policy. Changes to model, runtime, skills or toolchain are explicit dimensions; multi-dimension changes are `combined-system`. A changed dependency/asset blocks a paired claim unless reported as a different experiment. Always display success beside latency, retain resources consumed by failed trials, and keep unknown usage unknown.

## 10. Testing, calibration and delivery gates

1. Unit/contract gates: registered-case validation, public bindings, adversarial evidence records, maintenance/control decisions, cancellation/recovery/deduplication, report redaction and pure saved regrading. Use `unittest` and temporary storage; scripted fixtures are labelled.
2. Installed-wheel gate: build/install into isolated Python 3.11 and 3.13 environments, execute outside the checkout including paths with spaces, run installation smoke and registry/suite tests on existing macOS/Windows CI. No new mandatory optional tools.
3. Native reference gate: exact pinned binaries rebuilt and hash checked; reference candidates and behavioral mutants run in Renode; persist calibration records and limitations per case. A fake socket contract test cannot satisfy this gate.
4. Inference gate: after review and explicit selection of model/runtime/budgets, launch fresh trials; capture frozen executed versions and include failures. The old development baseline is historical evidence and remains untouched.

Use bounded waits on durable events in lifecycle tests. Do not repeat the fixed-sleep/aggregate-deadline assumptions repaired in `e7cbba3`. Full legacy tests remain a release gate; focused TDD runs accompany each slice.

## 11. Deliberate later increments

The initial implementation plans deliver the six-entry development pilot. Expansion to 12–20 independent cases, 30–50 stage-local diagnostics, native I2C/SPI/USB coverage, additional architectures, and two or three physical setups requires calibrated new assets and explicit execution budgets. Stage-local runs must receive standardized accepted inputs and report a different execution scope; they cannot count as full workflows.

Public development, validation and held-out sets will be split by implementation lineage. The initial three-family pilot is development data. A claim of enforced blind/held-out isolation requires a tested OS/VM/account boundary that denies worker access to evaluator files and monitor channels on the relevant platform. Until that gate exists, reports state `password-separated; filesystem isolation unverified`. Do not silently introduce required containers to claim this property.

External corpus admission requires an upstream commit, license/notice, reproducible binary or documented build limitation, patch log, observable contract and independent evaluator. No external benchmark becomes a dependency of installation smoke. Candidate sources identified in the September 30 survey:

- [EmbedBench](https://github.com/icip-cas/EmbedAgent): device scenarios and simulator checks.
- [EmbedEval](https://github.com/Ecro/embedeval): coverage taxonomy and evaluator mutation ideas; evaluate behavior rather than regex conventions.
- [Closed-loop embedded-agent benchmark](https://github.com/jgcarrasco/closed_loop_evaluation_agents_embedded): independent plant behavior and feedback regimes.
- [P2IM](https://github.com/RiS3-Lab/p2im) and [Fuzzware experiments](https://github.com/fuzzware-fuzzer/fuzzware-experiments): candidate firmware corpora with per-asset provenance review.
- [SRE-Bench evaluation](https://sre-bench.lol/evaluate): optional externally hosted reverse-engineering comparison; private programs are not a local redistributable corpus.

These sources inform design; no third-party firmware is imported by this planning change.

## 12. Implementation coverage and planning review

| Specification requirement | Owning plan and tasks |
|---|---|
| Registry, legacy compatibility, native host boundaries, executed identity | Plan 01, Tasks 1–2 |
| Canonical inputs, exact evidence inventories, bounded native stimuli | Plan 01, Tasks 3–4 |
| Neutral maintenance, control false alarms, interrupted effects | Plan 01, Task 5 |
| Frozen submissions, private final checks, encrypted offline regrading | Plan 01, Tasks 6–7 |
| Existing runtime feasibility and independent family oracles | Plan 02, Tasks 1–3 |
| Private authoring, reproducible assets, two-reference/mutant calibration | Plan 01, Tasks 4/7; Plan 02, Tasks 4–5 |
| Durable suite slots, budget/cancel/recovery and fault categories | Plan 03, Tasks 1–3 |
| Planned denominators, family weighting, saved comparisons and public clients | Plan 03, Tasks 4–5 |
| Installation and platform compatibility gates | All three plans' completion gates |
| Larger corpus, stage-local diagnostics, physical cases and enforced holdouts | Deliberately deferred under section 11 |

The primary agent reviewed specification coverage, shared names/types, placeholder content and every plan's five Review Focus items. The plan set's Python snippets parse, its relative document links resolve and its global constraints match this specification. Seven public toy protocol examples from Plan 02 were executed against the existing interface runtime and passed; this checks the proposed examples, not the unimplemented native families. No new benchmark implementation, native calibration, model trial or physical-device test was performed in this planning change.

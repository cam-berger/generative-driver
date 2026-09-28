# Generative Driver: accepted design

This brief records the public repository requested on 28 September 2026. It incorporates the accepted decisions Q1–Q10. Implementation will follow the agreed public test seams using one failing behavior test and its implementation at a time.

## Agreed outcome

Developers can install the toolchain, skills and workflow through documented Goose or Codex setup, connect supported hardware, state an objective and inspect the resulting package and evidence. The core runs natively on macOS and Windows 11 or later. Optional analysis and benchmark dependencies may use containers.

Goose and Codex provide the developer-facing interface. A local background configurator owns the workflow, controls the orchestrating agent and stage agents, manages their configured runtime connections and device access, and retains progress independently of the UI. The first release launches configured agent runtimes rather than implementing a provider API agent loop. Codex and Goose execution adapters share the same stage contract; the runtime, provider and model identities are recorded separately.

The configurator retains the existing seven roles: acquire, interpret, probe, ground, emit, reuse and maintain. One stage is active per run. Agents make stage decisions and report their work; deterministic checks control whether reports become accepted handoffs. This distribution adds the durable lifecycle controller missing from the source research toolchain.

## Architecture

```mermaid
flowchart TD
    U[Goose, Codex or CLI] --> M[Configurator interface]
    M --> C[Background run controller]
    C --> A[Orchestrating and stage agents]
    A --> T[Scoped toolchain tools]
    T --> D[Selected device or emulator]
    C --> E[Accepted handoffs and evidence]
    E --> P[Portable device package]
    B[Benchmark runner] --> M
    E --> V[Independent evaluator]
    G[Groundtruth evidence] --> V
```

The installed package provides the configurator, tools, portable runtime, model contracts and generic skills. Run state and outputs live in user-selected storage outside the installed package. A small CLI and MCP interface support setup checks, configuration, starting a run, reading status and evidence, supplying a requested observation, cancellation and checked recovery. Calls return a durable run identifier; reconnecting observes the same run.

The configurator records assignments, worker reports, checked handoffs and routing separately. Accepted artifacts are immutable and referenced by hashes. A worker exit code or a claim of completion cannot substitute for required check results. Model defects can return to interpret; host and operator faults route to the relevant correction without teaching the interpreter incorrect device facts.

Device bindings belong to the operator. Managed workers share exclusive device ownership through the configurator. Operations retain explicit effect grants and bounded execution. Cancellation prevents further dispatch, records the result of any outstanding operation and preserves uncertainty after interrupted writes. Recovery never silently repeats an uncertain physical action.

Blind interpret receives only its prepared description and generic kit. Fresh reuse receives only the emitted package, objective and permitted operating information. Each execution records the restrictions actually enforced. The developer-facing chat's history, benchmark answer keys and previous candidate outputs are excluded from these worker inputs.

Maintain is triggered after an accepted package exists. The initial benchmark deliberately changes the firmware revision, requires detection of incompatibility, routes repair and requalification, and checks fresh reuse of the revised package. A harder change to physical meaning with unchanged identity is a later benchmark variant.

## Public repository

The repository is `generative-driver`, with Python package `src/generative_driver/`. Original code is licensed under Apache-2.0. Existing reusable code is extracted selectively from the current working tree, including relevant uncommitted improvements. The source research repository and its frozen records are preserved.

```text
README.md                 purpose, supported paths and first successful run
src/generative_driver/       configurator, tools, runtime, packaged generic resources
plugins/                  Codex plugin and Goose installation assets
skills/                   canonical generic workflow and stage instructions
docs/                     architecture, macOS/Windows and UI setup, support matrix
tests/                    behavior tests at the agreed public seams
bench/README.md           prerequisites, run/score/compare instructions and metrics
bench/cases/              versioned benchmark inputs and execution manifests
bench/groundtruth/        independent evidence, reference vectors and evaluator keys
bench/evaluators/         scoring outside candidate access
bench/baselines/          selected publishable reports with provenance
.github/workflows/        macOS and Windows installation and behavior checks
```

The installed agent resources contain generic tools and skills. The benchmark's device sources, answers, scorers and historical outputs remain evaluator-side assets. Public evidence is selected for each worked example and retains provenance and limitations. Device recordings, vendor assets and historical Git contents are not copied wholesale. Third-party notices accompany any included third-party material.

Codex receives plugin metadata, MCP wiring and skills. Goose receives a recipe and skills with its documented MCP setup. Both connect to the same configurator. Setup documentation walks from the relevant UI through runtime selection, optional dependency checks, installation smoke, and the first supported hardware or benchmark run. A plugin installation does not by itself establish that optional native drivers or analysis tools are installed.

## Benchmark profiles

| Profile | Execution | Evidence and purpose |
|---|---|---|
| Installation check | Scripted fixture and recorded replay, no paid inference or hardware | Exercises installed CLI/MCP, workflow records, package emission and independent package replay. Labelled as setup smoke, excluded from model-performance scores. |
| Emulated STM32 controller | Actual configured agents against the owned TQ9 firmware in Renode | Main performance benchmark. Independent emulator observations score values and effects. Adds fresh-agent reuse and deliberate firmware drift to the existing stage evidence. |
| Physical BME280 sensor | Optional real sensor and supported adapter, with operator-supplied stimulus | Exercises actual transport and fresh reuse against physical observations. Its score remains distinguishable from emulation and replay. |

The existing TQ9 experiment shows why protocol checks and semantics need separate scores: its framing qualification passed while several decoders were wrong. Its package exercise did not establish fresh-agent reuse or maintenance. Those are new benchmark requirements, not historical successes; the independent benchmark evidence records that limitation.

Each groundtruth case includes source/build provenance, pinned input hashes, hand-auditable vectors, an independent oracle or primary documentation, observations from the reference channel, expected stage outcomes and known incorrect outputs that the evaluator must reject. The BME280 evidence establishes response to stimulus within its measured scope; it does not establish absolute sensor calibration.

A benchmark can execute completely and report that the model failed its task. Reports distinguish execution status, accepted workflow state and evaluator verdict. Overall task success requires every declared acceptance gate; averaging stage scores cannot erase a failed required gate. Blocked, failed, unrun and inapplicable stages remain explicit.

Record case and evaluator versions, toolchain revision, agent runtime/version, provider/model/settings, skill revision, attempt limits, environment, generated model/package hashes, stage scores, false capability claims, elapsed time, tool calls, reported usage, retries and human input. Missing usage remains unknown. Comparing different agent runtimes is a comparison of complete systems. A comparison that changes several variables labels that fact.

Provide a one-run development mode and a repeat mode for baselines. Repeated outcomes remain visible; summaries include success counts and median time/usage. The first repeat default is three fresh runs. Inference execution and its configured limits are explicit, separate from installation smoke.

## Approved TDD seams

These four interfaces were confirmed before new tests were written. Existing useful behavior tests are retained and adapted through these interfaces.

| Seam | Observable behavior |
|---|---|
| Installed CLI and MCP | A built package installs in a clean environment, works outside its checkout including paths with spaces, exposes the configurator, completes setup smoke and reports unavailable optional dependencies. |
| Configurator and worker execution | Real runtime adapters receive the correct stage context/tools. Assignments advance only after checked reports. Duplicate starts, stale results and altered inputs cannot advance twice. UI disconnect/reconnect, cancellation and process recovery preserve one consistent run and fresh reuse. |
| Device runtime and emitted package | Bindings and effect grants are checked before I/O. Managed operations have exclusive access. A relocated package describes, replays and executes through its public interface; tampering is rejected; uncertain writes are not retried automatically. |
| Benchmark run, score and compare | Independent known-good and deliberately wrong submissions receive expected verdicts. All seven roles are observable in the agent benchmark. Replay is labelled, missing data stays missing, incompatible comparisons are identified, and seeded drift is detected and repaired through requalification. |

Tests exercise the actual installed CLI, stdio MCP protocol, process boundaries and package interface where those are the seam. External devices, provider execution and clocks may have controlled substitutes for deterministic contract tests. Substitute workers and replay never count as actual model benchmark results. Native macOS and Windows runners validate the portable release; optional backend and physical validation are reported separately.

## Final decisions

The design and four public test seams were confirmed. Groundtruth uses password-encrypted evaluator storage, with artifact and input hashes. The evaluator keeps the password outside candidate prompts, environments and workspaces. Hashes establish integrity and provenance; this is password-based separation, not an operating-system sandbox.

Scores measure functional equivalence: the same supported inputs must produce the expected outputs and effects within the declared tolerances. Candidate code need not resemble reference code.

The initial validation is one real Codex benchmark on the development machine, bounded to three hours, with up to two interpretation repairs. Its failures and unavailable measurements remain in the report. Repeated studies and physical runs are separate selections.

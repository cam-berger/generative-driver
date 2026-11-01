# Learning loop (parked tabletop)

Status: needs-triage

Parked on 2026-10-30 so work can focus on the benchmark, scorecard and device generator. Nothing here is accepted or implemented; the [accepted design](../../docs/design.md) still governs. Codex is the primary runtime for this work; Goose is out of scope. Evidence base: [firmware learning practice](../../docs/research/2026-10-30-firmware-learning-practice.md).

## Decisions the user made

- Success is functional equivalence with the original firmware; failure is a departure from the original function.
- Unknown is neither. Record unknowns now; external verification or a new loop comes later, once enough unknown data exists.
- A device refusal is an answer, not a failure.
- The reference model always grades.
- Retrying is acceptable while failures stay caught. Working reading: repeat a single command only where a repeat is harmless or refused, and let the next state comparison expose a repeated effect. An uncertain write is still never silently retried.
- Firmware in, driver out: the orchestration follows established engineering practice as one scientific loop, generalized and agent-driven.

## Tabletop shape

**Common structure.** Every benchmark and corpus case is a reactive device. A command gets data, a refusal or silence, and may change hidden state; monitors see effects; an emulated case can be reset. Formally this is a Mealy machine (a state machine whose outputs depend on state and input) behind a mapper. Interface styles differ in three parts only:

- **Mapper:** transport, framing and encoding, such as text lines over UART, Modbus frames with CRC, USB control transfers or register reads and writes.
- **Alphabet:** commands, parameters and the value representatives worth testing.
- **Observers:** how effects and the environment are seen or driven, such as a register monitor, a sensor stimulus or elapsed time.

The firmware's shared landmark is its dispatcher, the switch or handler table every command passes through. It yields the alphabet; each handler's branches yield test targets: thresholds, repeat counters, error branches, length limits and timeouts. In the [ESM study](../../docs/research/2026-10-30-firmware-learning-practice.md#applied-studies), one difference needed an input repeated eight times and only the code revealed it.

**Self-construction boundary.** Agents write the mapper, alphabet, observers, hypothesis and test targets in fixed formats. The loop, instruments and grader stay fixed so runs stay comparable. Mappers that pass join a library for the next interface style.

**Stages as loop steps.**

| Stage | Step |
|---|---|
| acquire | Pin inputs with hashes; name the nearest known relative (chip family, vendor SDK, existing driver). |
| interpret | Triage the image, find the dispatcher, write the hypothesis (commands, answers including refusals and silence, state effects), mapper and test targets. |
| probe | Read-only contact; establish reset and determinism; run sequences from reset, recording answers and state per step with a query cache; search for counterexamples. A difference that repeats returns to interpret. |
| ground | Tie meanings (units, scales, effects) to independent references; vary concrete values. |
| emit | Freeze the package with its recorded traces and test suite. This is self-evidence only. |
| reuse | A fresh agent gets only the package and objective; the reference grades every step; the verdict carries the bound. |

**Division of labour.** Deterministic configurator instruments reset and verify, repeat queries to check determinism, run a sequence from reset recording answers and state per step, diff a record against the hypothesis to return the shortest differing prefix, and generate a bounded suite. Agents interpret, explain and revise, ground and reuse.

**The bound.** A verdict records the alphabet, value representatives, depth k, test count and length, target coverage and budget used. A depth-k W-method suite (a standard conformance-test generator for state machines) finds any difference over the tested inputs if the device has at most k more states than the hypothesis ([TLS study](../../docs/research/2026-10-30-firmware-learning-practice.md#applied-studies)), given a reliable reset and repeatable answers. Reset and determinism checks are therefore preconditions, not options.

## What it would simplify

The parameter store shows the cost of treating a refusal as a failed operation. A `FAIL=` reply fails the runtime call, which forces:

- nine-condition rejection inference in [emulated_actions.py](../../src/generative_driver/benchmark_support/emulated_actions.py) (`operation_rejected`);
- byte-level rejection witnesses (`_check_rejection_evidence` in `behavior.py`);
- a required rejection scenario among six reset-delimited scenarios, 891 checks per trial (`validate_phase` in `parameter_store.py`);
- a hand-written reuse progress tracker (`reuse_passed`);
- multiline-bound repairs and the [expected-refusal follow-up](../benchmark-lifecycle/issues/03-expected-refusal-repair.md).

The evaluator's reference state machine (`expected_state`, about 30 lines) could grade every step directly.

## Open for review

1. History: how much earlier behavior must keep working after consolidation, versus living only in git.
2. Who runs the loop: a configurator-owned loop with agent steps (recommended, since it keeps device ownership, durability and comparability) or one host-agent session.
3. Hypothesis format: executable code or a declarative transition table; parameter value classes; timing and unsolicited output; devices without reset (homing sequences, power cycling).
4. A revision budget replacing the fixed two repairs.
5. Whether the package ships the learned model, so applications can simulate the device and the driver can detect deviations.
6. Mapper-library format and admission.
7. Nondeterminism: repeat count and how an unknown is reported.

Related: branch `claude/angry-morse-e784f5` (pushed, no PR) makes the gateway execute a configurator-owned copy of the model, documents the remaining limit and pins the `emit_test_live` manifest.

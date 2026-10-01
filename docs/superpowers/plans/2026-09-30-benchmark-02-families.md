# Two Native Firmware Families Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independently authored sampled-sensor and transactional parameter-store firmware families, with executable behavioral oracles and native reference calibration before either becomes eligible for agent trials.

**Architecture:** Consume the registry, bounded capability bindings, shared emulated stage engine and `NativeSession` supplied by plan 01. Each family owns its task contract and observation projection; private authoring assets produce pinned binaries and encrypted evidence bundles. Qualification first proves that the existing interface-model/6 runtime can express the proposed operations, then proves actual compiled firmware behavior in native Renode.

**Tech Stack:** Python 3.11+ and `unittest`, the existing interface runtime and encryption envelope, owned Apache-2.0 Cortex-M C firmware, Renode, Arm GNU Toolchain 14.2.Rel1 / GCC 14.2.1, Ghidra and Java through explicit executable paths.

**Spec:** [Benchmark expansion specification](../specs/2026-09-30-benchmark-expansion.md). Read it and [plan 01](2026-09-30-benchmark-01-foundation.md) before implementing this plan. Continue with [plan 03](2026-09-30-benchmark-03-suites.md). This file is planning only; no feature implementation, model run, private-evidence read, firmware build or native calibration has been performed by writing it.

## Global Constraints

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

## Review Focus

1. Negative samples, changed numeric representation and stale sequence numbers must be distinguished from a correct changing reading; Tasks 1 and 2 exercise signed decoding, wrong-scale and stale-counter mutants.
2. Closing a package connection between staging and committing must preserve the device's pending transaction, while abort and denied writes must preserve committed memory; Tasks 1 and 3 exercise those boundaries.
3. A missing monitor field or malformed independent observation must fail as unavailable evidence, never become a plausible zero; Tasks 2 and 3 exercise missing, bool-valued and malformed fields.
4. Windows paths with spaces, compiler-version changes and authoring output inside the checkout must not produce an apparently qualified release; Task 4 exercises direct argv construction, pin mismatch and forbidden output locations.
5. Scripted sockets, one successful reference or a calibration record for different bytes must not unlock actual-agent execution; Task 5 exercises execution provenance, two-reference/mutant coverage and asset-hash mismatch.

---

## File responsibilities and shared interfaces

Create these public implementation files:

| File | Responsibility |
|---|---|
| `src/generative_driver/benchmark_support/family_support.py` | Defensive private contract/action copies and typed independent observation extraction |
| `src/generative_driver/benchmark_support/sampled_sensor.py` | Sampled-sensor canonical observations and private phase selection |
| `src/generative_driver/benchmark_support/parameter_store.py` | Store canonical observations and independent matrix transition oracle |
| `src/generative_driver/benchmark_support/asset_build.py` | Native compiler/objcopy pinning, deterministic builds, private inventory and release assembly |
| `src/generative_driver/benchmark_support/calibration.py` | Reference and behavioral-mutant calibration through the shared engine; qualification record validation |
| `tests/benchmark_family_fixtures.py` | Public toy interface-model/6 models, replies and private-shape test data with unrelated identities/values |
| `tests/test_benchmark_family_runtime.py` | Existing runtime feasibility, without a real emulator |
| `tests/test_sampled_sensor_family.py` | Independent observation and contract behavior |
| `tests/test_parameter_store_family.py` | Transaction oracle and independent state checks |
| `tests/test_benchmark_asset_build.py` | Reproducible-build and release-boundary contracts |
| `tests/test_benchmark_family_calibration.py` | Qualification gating and provenance contracts |
| `tests/native_benchmark_families.py` | Explicitly selected native reference gate; excluded from default `test*.py` discovery |

Modify `registry.py`, `emulated.py` and `benchmark.py` only at their plan-01 extension points: static family dispatch, new manifest admission and reference-calibration entry points. Do not alter `cases.py`, `evaluate.py`, `emulator.py`, the legacy TQ9 resources, or historical baseline files. Update `bench/README.md`, `bench/groundtruth/README.md` and `docs/verification.md` with the new distinctions and exact calibration status.

Plan 01 supplies the following interfaces; this plan consumes them:

```text
resolve_case(case_id: str) -> CaseDefinition
pin_case(case_id: str, scenario_id: str | None, case_seed: int) -> dict
bind_task(capabilities: dict, task: str, inputs: dict, model: dict) -> dict
validate_records(contract: dict, records: list[dict]) -> dict
project_feedback(records: list[dict]) -> dict
```

`bind_task` returns `operation` and `parameters`. Its numeric input bindings are affine; enum inputs use `kind: "copy"`. Family oracles never adjust candidate output decoders to make an incorrect model pass.

The shared native adapter in `benchmark_support/native.py` is owned by plan 01:

```text
NativeSession.start(*, renode: Path, image: Path, recipe: dict,
                    timeout_seconds: float = 60.0) -> NativeSession
NativeSession.attach(info: dict, recipe: dict) -> NativeSession
session.reset(initial: dict[str, int]) -> dict
session.stimulate(values: dict[str, int]) -> dict
session.observe() -> dict
session.set_running(running: bool) -> None
session.stop() -> None
```

`session.info` is persisted only in evaluator state; `session.binding` is the worker-facing host/port binding. The recipe contains evaluator-only `source_files`, setup, reset commands, bounded integer stimulus bindings and named memory observations. Observation shape is `{"values": {name: integer_or_integer_list}, "reads": list_of_raw_reads, "time": float, "physical": false}`. Reset/stimulate validate every key and value before sending any command. Missing values and monitor failure raise; no observation defaults to zero.

Each family exports exactly:

```text
contract(pin: dict, truth: dict, phase: str) -> dict
build_plan(pin: dict, truth: dict, phase: str) -> list[dict]
observations(raw: dict, canonical_task_result: dict, inputs: dict) -> dict
```

`truth` is the evaluator's private per-call context. The engine injects `artifact_sha256` for the frozen model/package into that context without modifying the archived bundle. A contract has schema `benchmark-behavior/1`, `artifact_sha256`, and an exact check inventory. Every check carries `id` (`episode/step/check`), `revision`, `kind` (`number`, `boolean`, `text`), `expected`, `unit`, `channel` (`independent-monitor`, `runtime-transcript`), and `absolute_tolerance` for numbers. The record carries the same identity fields plus `value`; raw evidence references are retained separately. The engine assembles records using action `checks` and the final component of each check ID as an observation key.

Action shapes consumed by the shared engine are:

```python
{"episode": "toy", "step": "reset", "kind": "reset", "values": {"cell_0": 0}}
{"episode": "toy", "step": "input", "kind": "stimulate", "values": {"source_q4": -80}}
{"episode": "toy", "step": "sample", "kind": "call", "task": "sample",
 "inputs": {}, "grants": ["write"]}
{"episode": "toy", "step": "measure", "kind": "observe",
 "checks": ["toy/measure/temperature", "toy/measure/reference_temperature"]}
```

These numbers and identities are public toy data, not scored vectors. The engine owns phase selection, current result, simulator running/paused policy, action journaling, sealed final tests, effect gates and evidence export. Families do not start runtimes or own configurator threads.

## Task 1: Prove both protocols fit the existing bounded runtime

**Files:** Create `tests/benchmark_family_fixtures.py` and `tests/test_benchmark_family_runtime.py`. Read `resources/docs/INTERFACE_MODEL_V6.md` and `src/interface_runtime/engine.py`; do not change runtime semantics.

**Interfaces:** Consume `interface_runtime.engine.execute/validate_model`. Produce `toy_sample_model() -> dict`, `toy_store_model() -> dict`, `ToyTransport(script)` with `factory(channel,binding)`, and `sample_reply(raw_temperature, sequence) -> bytes`. These are scripted fixtures, never native calibration.

- [ ] **Step 1: Write the failing feasibility tests.** The test file imports the new fixture helpers and uses the real engine:

```python
import unittest
from interface_runtime.engine import execute, validate_model
from benchmark_family_fixtures import (
    ToyTransport, sample_reply, toy_sample_model, toy_store_model)

class FamilyRuntimeTests(unittest.TestCase):
    def test_negative_framed_sample_and_sequence(self):
        model = toy_sample_model()
        self.assertTrue(validate_model(model)["ok"])
        transport = ToyTransport([
            ("identify", b"TOY-SAMPLE\n"),
            ("acquire", b"OK\n"),
            ("sample", sample_reply(-80, 3)),
        ])
        result = execute(model, "sample", binding={}, allow_effects=["write"],
                         transport_factory=transport.factory)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["outputs"], {"temperature": -5.0, "sequence": 3})
        self.assertEqual(result["units"]["temperature"], "degC")
        self.assertTrue(transport.closed)

    def test_corrupt_frame_fails_before_decoding(self):
        reply = bytearray(sample_reply(-80, 3)); reply[-1] ^= 1
        transport = ToyTransport([
            ("identify", b"TOY-SAMPLE\n"), ("acquire", b"OK\n"),
            ("sample", bytes(reply)),
        ])
        result = execute(toy_sample_model(), "sample", binding={},
                         allow_effects=["write"], transport_factory=transport.factory)
        self.assertFalse(result["ok"])
        self.assertEqual(result["outputs"], {})

    def test_store_fixed_transaction_preserves_step_order(self):
        model = toy_store_model()
        transport = ToyTransport([
            ("identify", b"FAMILY:TOY-STORE\nREADY\n"),
            ("begin", b"READY\n"), ("put", b"READY\n"),
            ("commit", b"READY\n"),
        ])
        result = execute(model, "update", {"bank": "B", "slot": 1, "value": -7},
                         binding={}, allow_effects=["write"],
                         transport_factory=transport.factory)
        self.assertTrue(result["ok"], result)
        self.assertEqual(transport.sent[1:], [b"BEGIN B\n", b"PUT 1 -7\n", b"COMMIT\n"])

    def test_error_before_completion_refuses_commit(self):
        transport = ToyTransport([
            ("identify", b"FAMILY:TOY-STORE\nREADY\n"),
            ("begin", b"READY\n"), ("put", b"ERR:range\nREADY\n"),
        ])
        result = execute(toy_store_model(), "update",
                         {"bank": "A", "slot": 0, "value": 1}, binding={},
                         allow_effects=["write"], transport_factory=transport.factory)
        self.assertFalse(result["ok"])
        self.assertNotIn(b"COMMIT\n", transport.sent)

    def test_denied_write_never_opens_transport(self):
        transport = ToyTransport([])
        result = execute(toy_store_model(), "update",
                         {"bank": "A", "slot": 0, "value": 1}, binding={},
                         transport_factory=transport.factory)
        self.assertFalse(result["ok"])
        self.assertFalse(transport.opened)
```

- [ ] **Step 2: Run red.** `python -m unittest discover -s tests -p test_benchmark_family_runtime.py -v` must fail because the fixture module is absent.

- [ ] **Step 3: Add the toy models and transport.** Build each complete model with device, channel `tcp`, identity, operations and a provenance row per operation. Use unrelated toy wire constants; the scored firmware will have separately authored identities and command tables. The critical framed response and model decoder are:

```python
import binascii

def sample_reply(raw_temperature, sequence):
    payload = raw_temperature.to_bytes(2, "little", signed=True)
    payload += sequence.to_bytes(4, "little")
    body = bytes([len(payload), 0x91]) + payload
    return b"\x7a\x35" + body + binascii.crc_hqx(body, 0xffff).to_bytes(2, "big")

FRAME = {"mode": "frame", "sync_hex": "7a35",
         "length": {"offset": 2, "size": 1, "byteorder": "little", "add": 6},
         "max_bytes": 32,
         "check": {"algorithm": "crc16_ccitt_false", "from": 2, "byteorder": "big"}}
SAMPLE_OUTPUTS = {
    "temperature": {"from": "sample", "kind": "integer", "offset": 4,
        "size": 2, "byteorder": "little", "signed": True, "scale": 0.0625, "unit": "degC"},
    "sequence": {"from": "sample", "kind": "integer", "offset": 6,
        "size": 4, "byteorder": "little", "signed": False, "unit": "count"},
}

class ToyTransport:
    def __init__(self, script):
        self.script = list(script); self.sent = []; self.opened = False; self.closed = False
    def factory(self, channel, binding):
        self.opened = True
        return self
    def exchange(self, tx, rx, timeout_ms):
        self.sent.append(tx)
        _, reply = self.script.pop(0)
        return reply
    def close(self):
        self.closed = True
```

Add the complete toy model builders to that fixture module:

```python
from copy import deepcopy

def _model(name, operations):
    return {"schema": "interface-model/6", "device": {"name": name},
            "channel": {"type": "tcp"}, "identity": {"operation": "identify"},
            "operations": operations,
            "provenance": [{"item": "operations." + key, "source": "independent public toy fixture"}
                           for key in operations]}

def _until(command, expected, capture):
    return {"op": "exchange", "tx": [{"text": command}],
            "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 64},
            "timeout_ms": 1000, "capture": capture,
            "expect": {"equals_hex": expected.hex()}}

def toy_sample_model():
    return _model("TOY-SAMPLE", {
        "identify": {"effect": "read", "parameters": {},
            "steps": [_until("ID\n", b"TOY-SAMPLE\n", "id")], "outputs": {}},
        "sample": {"effect": "write", "parameters": {},
            "steps": [_until("ACQUIRE\n", b"OK\n", "ack"),
                {"op": "exchange", "tx": [{"text": "SAMPLE\n"}], "rx": deepcopy(FRAME),
                 "timeout_ms": 1000, "capture": "sample",
                 "expect": {"prefix_hex": "7a350691"}}],
            "outputs": deepcopy(SAMPLE_OUTPUTS)},
    })

def _line(tx, capture, identity=False):
    expect = {"final_line_equals": "READY", "reject_line_prefix": ["ERR:"]}
    if identity: expect["contains_line"] = "FAMILY:TOY-STORE"
    return {"op": "exchange", "tx": tx, "capture": capture, "timeout_ms": 1000,
            "rx": {"mode": "lines", "max_bytes": 128, "max_lines": 4,
                   "end": {"line_equals": "READY"}}, "expect": expect}

def toy_store_model():
    bank = {"type": "string", "enum": ["A", "B"]}
    slot = {"type": "integer", "minimum": 0, "maximum": 1}
    value = {"type": "integer", "minimum": -9, "maximum": 9}
    begin = _line([{"text": "BEGIN "}, {"arg": "bank", "format": "ascii"}, {"text": "\n"}], "begin")
    put = _line([{"text": "PUT "}, {"arg": "slot", "format": "ascii"}, {"text": " "},
                 {"arg": "value", "format": "ascii"}, {"text": "\n"}], "put")
    commit = _line([{"text": "COMMIT\n"}], "commit")
    operations = {
        "identify": {"effect": "read", "parameters": {},
                     "steps": [_line([{"text": "ID\n"}], "identity", True)], "outputs": {}},
        "update": {"effect": "write", "parameters": {"bank": bank, "slot": slot, "value": value},
                   "steps": [begin, put, commit], "outputs": {}},
        "stage": {"effect": "write", "parameters": {"bank": bank, "slot": slot, "value": value},
                  "steps": [deepcopy(begin), deepcopy(put)], "outputs": {}},
        "commit": {"effect": "write", "parameters": {}, "steps": [deepcopy(commit)], "outputs": {}},
        "abort": {"effect": "write", "parameters": {},
                  "steps": [_line([{"text": "ABORT\n"}], "abort")], "outputs": {}},
        "read": {"effect": "read", "parameters": {"bank": bank, "slot": slot},
                 "steps": [_line([{"text": "GET "}, {"arg": "bank", "format": "ascii"},
                    {"text": " "}, {"arg": "slot", "format": "ascii"}, {"text": "\n"}], "cell")],
                 "outputs": {"value": {"from": "cell", "kind": "kv_number", "key": "VALUE:",
                                       "unit": "configuration-unit"}}},
    }
    return _model("TOY-STORE", operations)
```

The actual sampled family will use bounded framed requests as well. Add this independent request check to `FamilyRuntimeTests` before authoring its binary. `crc16_ccitt_false` is defined in the current `src/interface_runtime/checks.py` catalog with polynomial `0x1021`, initial value `0xffff` and no reflection/xorout; the toy oracle uses the standard-library implementation independently.

```python
    def test_request_crc_covers_varying_argument(self):
        import binascii
        for limit in (-7, 6):
            model = toy_sample_model()
            model["operations"]["sample"]["parameters"] = {
                "limit": {"type": "integer", "minimum": -9, "maximum": 9}}
            model["operations"]["sample"]["steps"][1]["tx"] = [
                {"hex": "357a"}, {"hex": "0231"}, {"arg": "limit", "format": "i16le"},
                {"check": "crc16_ccitt_false", "over_parts": [1, 3], "byteorder": "big"}]
            transport = ToyTransport([("identify", b"TOY-SAMPLE\n"), ("acquire", b"OK\n"),
                                      ("sample", sample_reply(-80, 3))])
            result = execute(model, "sample", {"limit": limit}, binding={},
                             allow_effects=["write"], transport_factory=transport.factory)
            self.assertTrue(result["ok"], result)
            body = b"\x02\x31" + limit.to_bytes(2, "little", signed=True)
            expected = b"\x35\x7a" + body + binascii.crc_hqx(body, 0xffff).to_bytes(2, "big")
            self.assertEqual(transport.sent[-1], expected)
```

The toy store identity and every command use `lines`, maximum 128 bytes, exact completion line `READY`, maximum four lines, positive completion expectation and `reject_line_prefix: ["ERR:"]`. `update` has parameters `bank` string enum `A|B`, `slot` integer 0…1, `value` integer −9…9; its TX parts are literal text plus ASCII arguments. Give each step a distinct capture. Add `stage`, `commit`, `abort` and `read` operations to exercise a transaction across separate `execute` calls. `read` returns `kv_number` key `VALUE:` with unit `configuration-unit`. Bindings select only the transport; model fields must contain no evaluator state.

- [ ] **Step 4: Add the connection boundary assertion and run green.** Add this toy stateful transport and test. It uses the public two-cell model, not the scored store's four-cell protocol:

```python
class ToyStoreDevice:
    def __init__(self):
        self.committed = {"A": [1, 2], "B": [3, 4]}
        self.pending = None; self.bank = None; self.opens = 0
    def factory(self, channel, binding):
        self.opens += 1
        return self
    def exchange(self, tx, rx, timeout_ms):
        words = tx.decode("ascii").split()
        if words[0] == "ID": return b"FAMILY:TOY-STORE\nREADY\n"
        if words[0] == "BEGIN":
            self.bank = words[1]; self.pending = list(self.committed[self.bank])
        elif words[0] == "PUT": self.pending[int(words[1])] = int(words[2])
        elif words[0] == "COMMIT":
            self.committed[self.bank] = list(self.pending); self.pending = None
        elif words[0] == "ABORT": self.pending = None
        elif words[0] == "GET":
            return ("VALUE:" + str(self.committed[words[1]][int(words[2])]) + "\nREADY\n").encode()
        return b"READY\n"
    def close(self):
        return None

class ScriptedStoreConnectionTests(unittest.TestCase):
    def test_commit_and_abort_across_separate_execute_connections(self):
        device = ToyStoreDevice(); model = toy_store_model()
        def call(operation, parameters=None):
            result = execute(model, operation, parameters, binding={}, allow_effects=["write"],
                             transport_factory=device.factory)
            self.assertTrue(result["ok"], result)
            return result
        call("stage", {"bank": "B", "slot": 1, "value": -7})
        self.assertEqual(device.committed["B"], [3, 4])
        call("abort")
        self.assertEqual(call("read", {"bank": "B", "slot": 1})["outputs"]["value"], 4)
        call("stage", {"bank": "B", "slot": 1, "value": -7})
        call("commit")
        self.assertEqual(call("read", {"bank": "B", "slot": 1})["outputs"]["value"], -7)
        self.assertEqual(device.committed["A"], [1, 2])
        self.assertEqual(device.opens, 6)
```

Run the Task 1 command and the existing `test_package_engine.py` tests. Stop if V6 cannot express the operation; simplify firmware requirements rather than changing the runtime language.

- [ ] **Step 5: Commit the fixture qualification slice.** `git add tests/benchmark_family_fixtures.py tests/test_benchmark_family_runtime.py` then `git commit -m "test: qualify new benchmark family protocols through bounded runtime"`.

## Task 2: Add the sampled-sensor contract and independent observation oracle

**Files:** Create `family_support.py`, `sampled_sensor.py`, `tests/test_sampled_sensor_family.py`; extend the toy fixture module. Paths are those in the responsibility table.

**Interfaces:** Consume `validate_records`, shared action/contract shapes and `NativeSession.observe()`. Produce the three family functions and `family_support.private_phase(pin,truth,phase) -> dict`, `integer(values,name) -> int`, `integer_vector(values,name,length) -> list[int]` and `signed16(value: int) -> int`. Native monitor word reads are unsigned; only the family oracle converts declared 16-bit signed fields. Counters remain unsigned.

- [ ] **Step 1: Write the red tests.** Add `toy_sensor_phase()` that returns a `benchmark-behavior/1` contract for the public toy identity `toy-sampled-oracle`; use artifact hash `"a" * 64`, revision 0, private toy expected −5.0 degrees, counter 3 and sensor input −80 sixteenths. Its exact required check names are `temperature`, `reference_temperature`, `sequence`, `monitor_sequence` and `operation_ok`.

```python
import copy
import unittest
from generative_driver.benchmark_support import sampled_sensor
from generative_driver.benchmark_support.behavior import validate_records
from benchmark_family_fixtures import toy_sensor_phase, records_for

class SampledSensorOracleTests(unittest.TestCase):
    def setUp(self):
        self.pin, self.truth = toy_sensor_phase()
        self.raw = {"values": {"latched_q4": 65456, "acquisition_counter": 3},
                    "reads": [{"response": "toy monitor bytes"}], "time": 1.0, "physical": False}
        self.result = {"ok": True, "outputs": {"temperature": -5.0, "sequence": 3},
                       "units": {"temperature": "degC", "sequence": "count"}, "transcript": []}

    def verdict(self, result):
        contract = sampled_sensor.contract(self.pin, self.truth, "diagnostic")
        observed = sampled_sensor.observations(self.raw, result, {})
        return validate_records(contract, records_for(contract, observed))["verdict"]

    def test_correct_independent_sample_passes(self):
        self.assertEqual(self.verdict(self.result), "passed")

    def test_wrong_scale_unsigned_and_stale_counter_fail(self):
        for key, value in (("temperature", -2.5), ("temperature", 4091.0), ("sequence", 2)):
            with self.subTest(key=key, value=value):
                result = copy.deepcopy(self.result); result["outputs"][key] = value
                self.assertEqual(self.verdict(result), "failed")

    def test_missing_or_boolean_monitor_value_is_unavailable(self):
        for values in ({"acquisition_counter": 3}, {"latched_q4": True, "acquisition_counter": 3}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                sampled_sensor.observations({"values": values}, self.result, {})

    def test_contract_and_plan_are_defensive_copies(self):
        first = sampled_sensor.contract(self.pin, self.truth, "diagnostic")
        first["checks"].clear()
        self.assertEqual(len(sampled_sensor.contract(self.pin, self.truth, "diagnostic")["checks"]), 5)
        plan = sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")
        plan[0]["values"]["source_q4"] = 123
        self.assertEqual(sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")[0]["values"]["source_q4"], -80)
```

`records_for(contract, observed)` copies `id`, `revision`, `unit`, `channel` from each check, adds contract `artifact_sha256`, and sets `value = observed[check["id"].rsplit("/",1)[1]]`. It does not copy `expected` into a record. This fixture helper must be shared by Tasks 2 and 3.

```python
def records_for(contract, observed):
    return [{**{key: check[key] for key in ("id", "revision", "unit", "channel")},
             "artifact_sha256": contract["artifact_sha256"],
             "value": observed[check["id"].rsplit("/", 1)[1]]}
            for check in contract["checks"]]

def toy_sensor_phase():
    rows = [("temperature", -5.0, "degC", "runtime-transcript"),
            ("reference_temperature", -5.0, "degC", "independent-monitor"),
            ("sequence", 3, "count", "runtime-transcript"),
            ("monitor_sequence", 3, "count", "independent-monitor"),
            ("operation_ok", True, "boolean", "runtime-transcript")]
    checks = []
    for name, expected, unit, channel in rows:
        check = {"id": "toy/measure/" + name, "revision": 0,
                 "kind": "boolean" if type(expected) is bool else "number",
                 "expected": expected, "unit": unit, "channel": channel}
        if check["kind"] == "number": check["absolute_tolerance"] = 0
        checks.append(check)
    contract = {"schema": "benchmark-behavior/1", "artifact_sha256": "a" * 64, "checks": checks}
    pin = {"id": "toy-sampled-oracle", "family": "sampled-sensor"}
    truth = {"family": "sampled-sensor", "artifact_sha256": "a" * 64,
             "phases": {"diagnostic": {"contract": contract, "actions": [
                 {"episode": "toy", "step": "input", "kind": "stimulate", "values": {"source_q4": -80}}]}}}
    return pin, truth
```

- [ ] **Step 2: Run red.** `python -m unittest discover -s tests -p test_sampled_sensor_family.py -v` fails because the family module is missing.

- [ ] **Step 3: Implement the pure projection and phase lookup.**

```python
# family_support.py
from copy import deepcopy

def integer(values, name):
    value = values[name] if name in values else None
    if type(value) is not int:
        raise ValueError("Unavailable integer observation: " + name)
    return value

def signed16(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("Unavailable unsigned 16-bit register")
    return value - 65536 if value & 32768 else value

def integer_vector(values, name, length):
    value = values.get(name)
    if not isinstance(value, list) or len(value) != length or any(type(x) is not int for x in value):
        raise ValueError("Unavailable integer vector: " + name)
    return list(value)

def private_phase(pin, truth, phase):
    if truth["family"] != pin["family"]:
        raise ValueError("Family evidence mismatch")
    result = deepcopy(truth["phases"][phase])
    result["contract"]["artifact_sha256"] = truth["artifact_sha256"]
    return result

# sampled_sensor.py
from .family_support import integer, private_phase, signed16

def contract(pin, truth, phase):
    return private_phase(pin, truth, phase)["contract"]

def build_plan(pin, truth, phase):
    return private_phase(pin, truth, phase)["actions"]

def observations(raw, canonical_task_result, inputs):
    values = raw["values"]
    reference = signed16(integer(values, "latched_q4")) / 16
    sequence = integer(values, "acquisition_counter")
    outputs = canonical_task_result.get("outputs", {})
    return {"temperature": outputs.get("temperature"),
            "reference_temperature": reference,
            "sequence": outputs.get("sequence"), "monitor_sequence": sequence,
            "operation_ok": canonical_task_result.get("ok") is True}
```

Preserve candidate output units in the engine's records; a value with the wrong unit cannot pass just because its number matches. The current projection returns canonical output names established by capability bindings. Missing candidate output becomes a failed typed record. Missing independent data raises an observation error classified as a host failure.

The sampled application latches an injected signed source value and advances its acquisition counter only on an acquisition command. Identity/readback must not advance it. Each diagnostic and final phase includes at least three changing inputs, including a negative value, and three bounded acquisition/configuration targets. The control scenario changes inputs while preserving firmware bytes. The semantic image changes the wire representation scale with identical advertised identity; the independent source and counter retain their physical meaning.

- [ ] **Step 4: Run green and exercise constant-output rejection.** Add this method to the test class, then run the Task 2 command. Verify no family function opens files, imports Renode or reads a password.

```python
    def test_three_changing_stimuli_reject_constant_reading(self):
        failed = 0
        for sequence, q4 in enumerate((-80, 112, 304), start=3):
            for check in self.truth["phases"]["diagnostic"]["contract"]["checks"]:
                name = check["id"].rsplit("/", 1)[1]
                if name in ("temperature", "reference_temperature"): check["expected"] = q4 / 16
                if name in ("sequence", "monitor_sequence"): check["expected"] = sequence
            self.raw["values"] = {"latched_q4": q4 & 65535, "acquisition_counter": sequence}
            good = copy.deepcopy(self.result)
            good["outputs"] = {"temperature": q4 / 16, "sequence": sequence}
            self.assertEqual(self.verdict(good), "passed")
            failed += self.verdict(self.result) == "failed"
        self.assertEqual(failed, 2)
```

- [ ] **Step 5: Commit.** `git add src/generative_driver/benchmark_support/family_support.py src/generative_driver/benchmark_support/sampled_sensor.py tests/benchmark_family_fixtures.py tests/test_sampled_sensor_family.py` then `git commit -m "feat: add independent sampled sensor benchmark oracle"`.

## Task 3: Add the volatile parameter-store transaction oracle

**Files:** Create `parameter_store.py`, `tests/test_parameter_store_family.py`; extend the toy fixture module.

**Interfaces:** Consume Task 2 private phase/vector helpers and shared behavior checks. Produce the three family functions and `expected_state(initial: list[int], actions: list[dict]) -> dict` for evaluator-side authoring/calibration. The pure oracle is independently written Python; it must not execute or import firmware command-parser code.

- [ ] **Step 1: Write the red tests.**

```python
import unittest
from generative_driver.benchmark_support import parameter_store

class ParameterStoreOracleTests(unittest.TestCase):
    def test_commit_changes_one_cell_and_generation_once(self):
        initial = [4, 5, 6, 7, 8, 9, 10, 11]
        result = parameter_store.expected_state(initial, [
            {"kind": "begin", "bank": "B"},
            {"kind": "put", "slot": 2, "value": -3},
            {"kind": "commit"},
        ])
        self.assertEqual(result["committed"], [4, 5, 6, 7, 8, 9, -3, 11])
        self.assertEqual(result["generation"], 1)
        self.assertFalse(result["pending_active"])
        self.assertEqual(initial, [4, 5, 6, 7, 8, 9, 10, 11])

    def test_abort_leaves_committed_state_and_generation_unchanged(self):
        initial = list(range(8))
        result = parameter_store.expected_state(initial, [
            {"kind": "begin", "bank": "A"}, {"kind": "put", "slot": 0, "value": -9},
            {"kind": "abort"},
        ])
        self.assertEqual(result["committed"], initial)
        self.assertEqual(result["generation"], 0)

    def test_illegal_order_and_invalid_numbers_are_rejected(self):
        for actions in ([{"kind": "commit"}], [{"kind": "put", "slot": 0, "value": 1}],
                        [{"kind": "begin", "bank": "A"}, {"kind": "put", "slot": True, "value": 1}]):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                parameter_store.expected_state([0] * 8, actions)

    def test_missing_or_short_monitor_matrix_is_unavailable(self):
        for values in ({}, {"committed": [0] * 7, "pending": [0] * 8,
                           "generation": 0, "pending_active": 0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parameter_store.observations({"values": values}, {"ok": True}, {})
```

- [ ] **Step 2: Run red.** `python -m unittest discover -s tests -p test_parameter_store_family.py -v` fails on the absent module.

- [ ] **Step 3: Implement the independent transition rule and projection.**

```python
from .family_support import integer, integer_vector, private_phase, signed16

def contract(pin, truth, phase):
    return private_phase(pin, truth, phase)["contract"]

def build_plan(pin, truth, phase):
    return private_phase(pin, truth, phase)["actions"]

def expected_state(initial, actions):
    committed = integer_vector({"initial": initial}, "initial", 8)
    if any(not -32768 <= value <= 32767 for value in committed):
        raise ValueError("Initial value outside int16")
    pending, bank, generation = list(committed), None, 0
    for action in actions:
        kind = action["kind"]
        if kind == "begin":
            if bank is not None or action.get("bank") not in ("A", "B"):
                raise ValueError("Invalid begin")
            bank = action["bank"]; pending = list(committed)
        elif kind == "put":
            slot, value = action.get("slot"), action.get("value")
            if bank is None or type(slot) is not int or not 0 <= slot < 4:
                raise ValueError("Invalid transaction slot")
            if type(value) is not int or not -32768 <= value <= 32767:
                raise ValueError("Invalid transaction value")
            pending[(4 if bank == "B" else 0) + slot] = value
        elif kind in ("commit", "abort"):
            if bank is None:
                raise ValueError("No pending transaction")
            if kind == "commit":
                committed = list(pending); generation += 1
            pending = list(committed); bank = None
        else:
            raise ValueError("Unknown transition")
    return {"committed": committed, "pending": pending,
            "pending_active": bank is not None, "generation": generation}

def observations(raw, canonical_task_result, inputs):
    values = raw["values"]
    committed = [signed16(value) for value in integer_vector(values, "committed", 8)]
    pending = [signed16(value) for value in integer_vector(values, "pending", 8)]
    generation = integer(values, "generation")
    active = integer(values, "pending_active")
    if active not in (0, 1) or generation < 0:
        raise ValueError("Invalid transaction monitor state")
    result = {"generation": generation, "pending_active": active == 1,
              "operation_ok": canonical_task_result.get("ok") is True,
              "value": canonical_task_result.get("outputs", {}).get("value")}
    for index in range(8):
        label = ("A" if index < 4 else "B") + "_" + str(index % 4)
        result["cell_" + label] = committed[index]
        result["pending_" + label] = pending[index]
    return result
```

The committed matrix persists across host connections; MCU reset initializes it from the case's reset recipe. No flash driver, power-cycle persistence or durable-write claim is made. Firmware owns pending state; a transport `close()` must not call `ABORT`. The scored semantic revision multiplies readback numerics by a fixed decimal factor while leaving the logical stored state and identity stable; the correct new decoder changes, but the oracle still compares the same logical units.

- [ ] **Step 4: Run green with behavioral mutants.** Add the following test, plus an unchanged-control false-alarm trial through the shared maintenance tests in plan 01. A family cannot override the engine's neutral maintenance decision. Run the Task 3 command plus Task 1 connection tests.

```python
    def test_observable_transaction_mutants_fail_required_checks(self):
        import copy
        from generative_driver.benchmark_support.behavior import validate_records
        from benchmark_family_fixtures import records_for
        raw = {"values": {"committed": list(range(8)), "pending": list(range(8)),
                          "generation": 0, "pending_active": 0}}
        result = {"ok": True, "outputs": {"value": 2}}
        correct = parameter_store.observations(raw, result, {})
        names = ("cell_A_2", "cell_B_2", "generation", "value")
        contract = {"schema": "benchmark-behavior/1", "artifact_sha256": "a" * 64,
                    "checks": [{"id": "toy/state/" + name, "revision": 0, "kind": "number",
                        "unit": "count" if name == "generation" else "configuration-unit",
                        "channel": "runtime-transcript" if name == "value" else "independent-monitor",
                        "expected": correct[name], "absolute_tolerance": 0} for name in names]}
        self.assertEqual(validate_records(contract, records_for(contract, correct))["verdict"], "passed")
        for name, mutation in (("cell_A_2", -3), ("cell_B_2", -3), ("generation", 1), ("value", 20)):
            observed = copy.deepcopy(correct); observed[name] = mutation
            records = records_for(contract, observed)
            self.assertEqual(validate_records(contract, records)["verdict"], "failed")
            mismatches = [record["id"] for record, check in zip(records, contract["checks"])
                          if record["value"] != check["expected"]]
            self.assertEqual(mismatches, ["toy/state/" + name])
```

- [ ] **Step 5: Commit.** `git add src/generative_driver/benchmark_support/parameter_store.py tests/benchmark_family_fixtures.py tests/test_parameter_store_family.py` then `git commit -m "feat: grade parameter store transactions through independent state"`.

## Task 4: Author private firmware and reproducible release assets

**Files:** Create `asset_build.py`, `tests/test_benchmark_asset_build.py`. Author scored source only under an explicit operator-selected directory outside the checkout, installed packages, run/candidate roots and baseline directory. Create release directories `resources/bench/cases/sampled-sensor-v1/`, `resources/bench/cases/parameter-store-v1/`, and corresponding encrypted truth files only after build checks pass. Do not add unencrypted authoring files to Git.

**Interfaces:** Consume plan-01 `authoring.build_case(authoring_dir: Path, compiler: Path, output_dir: Path) -> dict`; it owns actual compiler/objcopy subprocess execution. Produce `build_argv(compiler, source_dir, elf, flags) -> list[str]`, `require_toolchain(version_line,objcopy_line) -> None`, `require_private_root(root, forbidden_roots) -> Path`, `build_family(authoring_root, compiler, output_dir) -> dict`, and `assemble_release(authoring_root, build_report, password_file, output_dir) -> dict`. Reuse `truth.seal/unlock`; never log the password or unlocked bundle.

- [ ] **Step 1: Write failing boundary tests.**

```python
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark_support.asset_build import (
    build_argv, require_private_root, require_toolchain)

class FamilyBuildTests(unittest.TestCase):
    def test_native_paths_with_spaces_are_individual_argv_entries(self):
        compiler = Path("C:/Program Files/Arm/bin/arm-none-eabi-gcc.exe")
        source = Path("C:/Evaluator Files/toy-case")
        elf = source / "build" / "firmware.elf"
        argv = build_argv(compiler, source, elf, ["-Os", "-mcpu=cortex-m4", "-mthumb"])
        self.assertEqual(argv[0], str(compiler))
        self.assertIn(str(source / "application.c"), argv)
        self.assertEqual(argv[-2:], ["-o", str(elf)])
        self.assertFalse(any(part in ("sh", "bash", "cmd", "-c") for part in argv))

    def test_compiler_change_is_not_the_same_asset_build(self):
        with self.assertRaises(ValueError):
            require_toolchain("arm-none-eabi-gcc 13.3.1", "GNU objcopy 2.43.1")

    def test_authoring_under_checkout_or_symlink_target_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); checkout = root / "repo"; checkout.mkdir()
            with self.assertRaises(ValueError):
                require_private_root(checkout / "private", [checkout])
            private = root / "private"
            self.assertEqual(require_private_root(private, [checkout]), private.resolve())
```

The same `resolve()`-based check must reject a symlink resolving beneath a forbidden root. Add that case where the platform permits symlink creation; Windows lacking that privilege still exercises resolved descendant checks. No test should need administrator privileges.

- [ ] **Step 2: Run red.** `python -m unittest discover -s tests -p test_benchmark_asset_build.py -v` fails on the missing module.

- [ ] **Step 3: Implement native invocation and build identity checks.**

```python
from pathlib import Path

def require_private_root(root, forbidden_roots):
    candidate = Path(root).expanduser().resolve()
    if any(candidate == Path(base).resolve() or candidate.is_relative_to(Path(base).resolve())
           for base in forbidden_roots):
        raise ValueError("Evaluator authoring must be outside repository and candidate roots")
    return candidate

def require_toolchain(version_line, objcopy_line):
    if "14.2.Rel1" not in version_line or "14.2.1" not in version_line:
        raise ValueError("Expected Arm GNU Toolchain 14.2.Rel1 / GCC 14.2.1")
    if not objcopy_line.startswith("GNU objcopy "):
        raise ValueError("Expected adjacent GNU objcopy")

def build_argv(compiler, source_dir, elf, flags):
    source_dir = Path(source_dir)
    return [str(compiler), *flags,
            "-ffile-prefix-map=" + str(source_dir.resolve()) + "=.",
            "-fdebug-prefix-map=" + str(source_dir.resolve()) + "=.",
            "-Wl,--build-id=none", "-T", str(source_dir / "linker.ld"),
            str(source_dir / "startup.c"), str(source_dir / "board_uart.c"),
            str(source_dir / "application.c"), "-lgcc", "-o", str(elf)]
```

`build_family` writes the validated ordered-argv `build.json` consumed by `authoring.build_case`, then delegates process execution to that function. It does not create a second subprocess runner. It requires actual absolute files for the compiler and its adjacent `arm-none-eabi-objcopy` (preserving `.exe`). Capture both `--version` lines. Run all subprocesses with argument arrays, `shell=False`, explicit working directory and bounded timeout. Reject missing/nonzero subprocess output; write the diagnostic privately. Pin the entire objcopy version line in the new family's build manifest and reject changes on rebuild. Set no compiler date/time macros. Use deterministic source bytes, flags, linker script and declared variant defines. Rebuild each image twice into two new directories and require identical binary hashes; keep ELF/debug symbols only in private authoring evidence. Never replace a pinned image on a mismatch.

The private authoring inventory for **each** family is exactly:

```text
AUTHORING.json
LICENSE
source/startup.c
source/board_uart.c
source/application.c
source/linker.ld
build.py
build-recipe.json
native/platform.repl
native/device.resc
native/observation-map.json
reference-a/model.json
reference-a/capabilities.json
reference-a/replies.json
reference-b/model.json
reference-b/capabilities.json
reference-b/replies.json
diagnostic/episodes.json
final/episodes.json
maintenance/semantic.json
maintenance/control.json
oracle/vectors.json
mutants/manifest.json
build/build-report.json
build/firmware.elf
build/firmware-drift.elf
build/firmware.bin
build/firmware-drift.bin
calibration/calibration.json
```

The sampled family additionally owns `native/SampleSource.cs`; the store uses regular emulator memory and needs no new peripheral source. Each mutant entry names its source reference, a bounded JSON change to its model/capability map, expected failed required check IDs and phase. No dynamic Python expression or callback is stored in these mutation definitions. The release assembler rejects absent inventory files, unlisted files, path traversal, source symlinks and unexpected plaintext under release output.

Owned C implementation requirements and critical independent transitions:

```c
#include <stdint.h>
#include <stdbool.h>

/* sampled application: private names/opcodes live in its command table */
static volatile int16_t latched_q4;
static volatile uint32_t acquisition_counter;
static void acquire_sample(int16_t source_q4) {
    latched_q4 = source_q4;
    acquisition_counter += 1u;
}
static int16_t wire_sample(int16_t multiplier) {
    return (int16_t)((int32_t)latched_q4 * multiplier);
}

/* parameter store application: no host-connection close hook mutates these */
static volatile int16_t committed[8], pending[8];
static volatile uint32_t commit_generation;
static volatile unsigned pending_active;
static bool commit_update(void) {
    if (!pending_active) return false;
    for (unsigned index = 0; index < 8; ++index) committed[index] = pending[index];
    ++commit_generation;
    pending_active = 0;
    return true;
}
static bool abort_update(void) {
    if (!pending_active) return false;
    for (unsigned index = 0; index < 8; ++index) pending[index] = committed[index];
    pending_active = 0;
    return true;
}
```

These transition functions are complete; their callers are the separately authored board and protocol code. The sampler passes its validated source-register value and declared variant multiplier. The store parser maps a false transition to its declared no-transaction error and always emits the bounded completion terminator. Supply actual UART register definitions/startup/linker from independently authored minimal board support after checking the existing board's public memory map. Do not copy private TQ9 source. The sampler's C parser handles complete bounded frames, validates CRC before mutation and emits one bounded response; its baseline/drift defines change numeric wire scaling only. Counter and source input remain evaluator-observable. The store parser enforces bank enum, four slots, int16 bounds, single active transaction, explicit commit/abort and multiline error termination. Give each family an independently authored parser/application, rather than renaming a shared command implementation. Same-platform startup/UART code may be shared with clear provenance.

For store readback, multiply in int32 so int16 boundary cells do not overflow the drifted decimal representation. For sampler scaling, restrict the declared source range so both wire representations fit signed int16. Never silently wrap either value. Emit the acquisition counter as unsigned 32-bit; reset before test sequences can wrap. These are functional assumptions included in the private oracle inventory.

- [ ] **Step 4: Run green and assemble a pending release.** Run the Task 4 test command. In a separately selected authoring session, invoke `python -m generative_driver.benchmark_support.asset_build` with explicit `--authoring-root`, `--compiler`, `--password-file`, `--output` paths. The module's `argparse` interface maps those fields directly to the build/assemble functions. It prints public hashes/status only. Unit tests invoke these functions against toy files and a scripted compiler process, labelled `scripted-contract-fixture`; only actual tool execution records `reference-build`.

Public installed release contents are only:

```text
resources/bench/cases/sampled-sensor-v1/case.json
resources/bench/cases/sampled-sensor-v1/firmware.bin
resources/bench/cases/sampled-sensor-v1/firmware-drift.bin
resources/bench/cases/parameter-store-v1/case.json
resources/bench/cases/parameter-store-v1/firmware.bin
resources/bench/cases/parameter-store-v1/firmware-drift.bin
resources/bench/groundtruth/sampled-sensor-v1.enc
resources/bench/groundtruth/parameter-store-v1.enc
```

The manifest uses `benchmark-case/2`; public fields are exact ID/family/version/evaluator version, adapter key, `actual-agent-emulation` execution, `firmware` evidence track, `full-workflow` scope, `semantic` and `control` scenario descriptors, required stages, both image hashes, ciphertext path/hash, effect defaults, emulator approval scope, provenance/license/build-tool version, time policy, limitations and `calibration` with `status: "pending"` and source/contract input hashes. Use family values `sampled-sensor` and `parameter-store` independently of versioned case IDs. It contains no seed vectors, source, symbol addresses, authoring/password paths or reference model. All private inventory files become a versioned payload within the existing authenticated envelope. Hash the canonical inventory entries and preserve them on unlock/rebuild; encryption randomness means ciphertext itself is not a reproducible-build target.

- [ ] **Step 5: Commit code/tests and only assembled public assets.** Use explicit paths from the public inventory in `git add`; never add the authoring root or use a broad add from it. Commit message: `feat: build pinned native family assets from private authoring bundles`. If real authoring/build has not run, commit the builder/tests only and keep registry discovery entries pending without invented binary hashes.

## Task 5: Require native reference calibration before registry admission

**Files:** Create `calibration.py`, `tests/test_benchmark_family_calibration.py`, `tests/native_benchmark_families.py`; modify new-family manifest admission in `registry.py`, static dispatch in `emulated.py`, and the three documentation files named above.

**Interfaces:** Consume plan-01 `authoring.calibrate(case_id: str, options: dict) -> dict`; it owns measured native reference/mutant execution through `emulated.py`. Produce `validate_calibration(manifest: dict, record: dict) -> dict`, `calibrate(case_id: str, *, authoring_root: Path, renode: Path, ghidra_home: Path, java_home: Path, output_dir: Path) -> dict`, and a module CLI with those exact keyword names as hyphenated flags. Consume the shared emulated engine's reference execution seam and normal runtime/package boundaries. References use no agent runtime and get execution label `reference-calibration`.

- [ ] **Step 1: Write the red admission tests.**

```python
import copy
import unittest
from generative_driver.benchmark_support.calibration import validate_calibration

class FamilyCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"id": "toy-case", "family": "sampled-sensor", "evaluator_version": "2",
                         "images": {"firmware.bin": "a" * 64, "firmware-drift.bin": "b" * 64},
                         "calibration": {"input_hashes": {"source": "c" * 64, "contract": "d" * 64}}}
        self.record = {"schema": "benchmark-calibration/1", "case": "toy-case",
            "execution": "reference-calibration", "backend": "native-renode",
            "evaluator_version": "2", "images": dict(self.manifest["images"]),
            "input_hashes": dict(self.manifest["calibration"]["input_hashes"]),
            "native_process_observed": True,
            "references": [{"id": "a", "passed": True}, {"id": "b", "passed": True}],
            "required_mutants": ["constant", "scale", "signedness", "stale", "false-drift"],
            "mutants": [{"id": name, "rejected": True, "failed_checks": ["toy/check/" + name]}
                        for name in ("constant", "scale", "signedness", "stale", "false-drift")],
            "scenarios": {"semantic": {"passed": True}, "control": {"passed": True}},
            "tools": {"renode": "observed-version", "ghidra": "observed-version",
                      "java": "observed-version"}}

    def test_complete_native_calibration_passes(self):
        self.assertTrue(validate_calibration(self.manifest, self.record)["ok"])

    def test_scripted_fixture_cannot_qualify(self):
        self.record["backend"] = "python-socket-fixture"
        self.assertFalse(validate_calibration(self.manifest, self.record)["ok"])

    def test_missing_reference_mutant_or_changed_image_is_refused(self):
        for defect in ("reference", "mutant", "image"):
            record = copy.deepcopy(self.record)
            if defect == "reference": record["references"].pop()
            if defect == "mutant": record["mutants"].pop()
            if defect == "image": record["images"]["firmware.bin"] = "d" * 64
            with self.subTest(defect=defect):
                self.assertFalse(validate_calibration(self.manifest, record)["ok"])
```

- [ ] **Step 2: Run red.** `python -m unittest discover -s tests -p test_benchmark_family_calibration.py -v` fails on the missing calibration module.

- [ ] **Step 3: Implement strict calibration identity and coverage.**

```python
REQUIRED_MUTANTS = {
    "sampled-sensor": {"constant", "scale", "signedness", "stale", "false-drift"},
    "parameter-store": {"constant", "scale", "order", "wrong-bank", "abort", "false-drift"},
}

def validate_calibration(manifest, record):
    failures = []
    expected = {"schema": "benchmark-calibration/1", "case": manifest["id"],
                "execution": "reference-calibration", "backend": "native-renode",
                "evaluator_version": manifest["evaluator_version"],
                "images": manifest["images"], "input_hashes": manifest["calibration"]["input_hashes"]}
    for key, value in expected.items():
        if record.get(key) != value: failures.append("identity:" + key)
    if record.get("native_process_observed") is not True:
        failures.append("native process unobserved")
    references = record.get("references", [])
    if len(references) != 2 or len({row.get("id") for row in references}) != 2:
        failures.append("two distinct references required")
    if any(row.get("passed") is not True for row in references):
        failures.append("reference failed")
    required = record.get("required_mutants", [])
    family_inventory = REQUIRED_MUTANTS.get(manifest.get("family"))
    if family_inventory is None or set(required) != family_inventory:
        failures.append("family mutant inventory mismatch")
    mutants = record.get("mutants", [])
    ids = [row.get("id") for row in mutants]
    if not required or len(required) != len(set(required)) or sorted(ids) != sorted(required):
        failures.append("mutant inventory mismatch")
    if any(row.get("rejected") is not True or not row.get("failed_checks") for row in mutants):
        failures.append("behavioral mutant survived")
    for scenario in ("semantic", "control"):
        if record.get("scenarios", {}).get(scenario, {}).get("passed") is not True:
            failures.append("scenario:" + scenario)
    if set(record.get("tools", {})) != {"renode", "ghidra", "java"}:
        failures.append("tool identity unavailable")
    return {"ok": not failures, "failures": failures}
```

The hardcoded family mutant inventory prevents deleting the same mutant from both the required and observed lists. Add the test below before declaring the admission slice green:

```python
    def test_deleting_mutant_from_both_lists_cannot_weaken_admission(self):
        self.record["required_mutants"].remove("signedness")
        self.record["mutants"] = [row for row in self.record["mutants"] if row["id"] != "signedness"]
        self.assertFalse(validate_calibration(self.manifest, self.record)["ok"])
```
 Pin the evaluator implementation hash and executed package/runtime/toolchain hashes to the manifest's calibration reference; do not accept self-reported booleans from candidate workers as native evidence.

`calibration.calibrate` is a family admission wrapper: it validates authoring inventory and absolute tool paths, then calls `authoring.calibrate(case_id, options)` with `authoring_root`, `renode`, `ghidra_home`, `java_home`, and `output_dir`. It returns `{**record, **validate_calibration(manifest, record)}` for the measured record returned by that shared runner, preserving `execution` and native provenance alongside `ok`/`failures`. The shared runner checks all explicit tool paths before launching anything, records their version output privately, verifies rebuilt binary hashes, runs both independently authored correct models through diagnostic and frozen final episodes, executes semantic and unchanged-control maintenance, and executes every behavioral mutant. Reference A and B have distinct operation/parameter names, legal guard compositions and task maps; author them independently from firmware evidence, not by cloning the same model and changing its name. The calibration record commits to canonical source/build/reference inventory and contract hashes, image hashes and evaluator/tool identities. It must not contain the hash of the ciphertext that will embed the record. Seal the completed private record only after calibration, then pin the resulting ciphertext and public calibration summary in the release manifest. Re-keying does not change measured firmware behavior but creates a new truth/manifest identity for run comparisons. The public admission summary contains identities, counts, outcomes and limitations; exact stimuli, replies and expected failed-check details stay in the encrypted calibration evidence. Ghidra analysis is an actual bounded import/decompile of each pinned binary using the same helper as interpretation; it records success and artifacts without invoking a model.

Use `NativeSession.start` for the actual images. Retain PID/executable/image and raw observation provenance privately; always stop sessions in `finally`. The shared stage engine runs the existing model/package interfaces. The adapter's `physical` flag remains false. A scripted transport unit test can validate orchestration, but cannot generate an admissible native record. A candidate package that changes after final freeze fails before a reference episode proceeds.

- [ ] **Step 4: Add the explicitly selected native gate and run unit green.** The native gate fails when configuration is missing; it does not silently skip and produce a green calibration claim:

```python
import os
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark_support.calibration import calibrate

class NativeFamilyCalibrationTests(unittest.TestCase):
    def test_both_authored_families(self):
        required = ("GD_FAMILY_AUTHORING", "GD_NATIVE_RENODE", "GD_NATIVE_GHIDRA_HOME", "GD_NATIVE_JAVA_HOME")
        paths = {}
        for name in required:
            self.assertIn(name, os.environ, "Native gate requires explicit " + name)
            paths[name] = Path(os.environ[name]).resolve()
            self.assertTrue(paths[name].exists(), name)
        for case_id in ("sampled-sensor-v1", "parameter-store-v1"):
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as temporary:
                result = calibrate(case_id,
                    authoring_root=paths["GD_FAMILY_AUTHORING"] / case_id,
                    renode=paths["GD_NATIVE_RENODE"],
                    ghidra_home=paths["GD_NATIVE_GHIDRA_HOME"],
                    java_home=paths["GD_NATIVE_JAVA_HOME"], output_dir=Path(temporary))
                self.assertTrue(result["ok"], result.get("failures"))
                self.assertEqual(result["execution"], "reference-calibration")
```

Run `python -m unittest discover -s tests -p test_benchmark_family_calibration.py -v`. Run default unit discovery to verify optional tools are not needed. In the selected native authoring environment, run `python -m unittest discover -s tests -p native_benchmark_families.py -v` only after setting all four variables to actual absolute paths. This native command does not start Codex/Goose, make API requests or touch physical devices.

- [ ] **Step 5: Register qualified assets, document and commit.** Static dispatch maps `sampled-sensor-v1` to `sampled_sensor` and `parameter-store-v1` to `parameter_store`; both use shared `emulated.py`. Discovery may list pending profiles. Agent-run preflight must reject every status other than `"passed"`, or stale calibration before service/device/worker startup. Set `calibration.status` to `"passed"` only after its native evidence passes; the new public calibration digest becomes part of its immutable manifest identity. Never edit ciphertext after pinning it without generating a new manifest hash and rerunning identity validation.

Document native invocation, required tool versions, private authoring boundary, failure diagnostics and the distinction between contract tests and measured calibration in `bench/README.md` and `bench/groundtruth/README.md`. Record which OS actually ran calibration; existing Windows Python CI is not proof of native Renode/Ghidra qualification. The pilot reports three independently authored families on one emulated STM32 platform, six paired scenario entries, UART-over-TCP and no physical grounding. Commit message: `feat: gate new firmware families on native reference calibration`.

## Verification and handoff

- [ ] Check all five Review Focus items have the named executable tests and no required oracle record can be omitted silently.
- [ ] Run focused tests after each slice; run full legacy unit tests once before integration. Build/install the wheel, execute outside the checkout and run installation smoke on the existing macOS/Windows Python 3.11/3.13 matrix. No native tool or password may become a smoke dependency.
- [ ] Verify `tq9` manifests/binaries/encrypted evidence and September 28 baseline files are byte-identical to `e7cbba3`.
- [ ] Audit public manifests, unit fixtures, wheel contents and release summaries for private source, expectations, final vectors, monitor paths and credentials. Public toy fixtures must retain unrelated identities and values.
- [ ] Deliver the two qualification summaries with observed native environment and any pending status. Suite pilot execution belongs to plan 03 and requires separately selected executor/model/budgets; do not run inference to finish this plan.

The architecture and protocol choices in this document are proposed work. Native firmware and calibration remain unverified until the explicitly selected reference gate executes and records its result.

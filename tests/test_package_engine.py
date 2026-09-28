"""Independent wire fixtures for the shared, bounded transaction engine."""
import copy
import sys
from pathlib import Path

import unittest
from unittest.mock import patch


def parametrize(names, cases):
    def decorate(function):
        function.cases = [(case,) if "," not in names else case for case in cases]
        return function
    return decorate


from interface_runtime.engine import execute, validate_model


def make_model():
    return {
        "schema": "interface-model/3", "device": {"name": "Demo"},
        "channel": {"type": "uart", "baudrate": 115200},
        "identity": {"operation": "identify"},
        "operations": {
            "identify": {"effect": "read", "parameters": {}, "steps": [{
                "op": "exchange", "tx": [{"text": "ID?\n"}],
                "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 128},
                "timeout_ms": 1000, "capture": "id", "expect": {"contains_text": "DEMO-42"}
            }], "outputs": {"identity": {"from": "id", "kind": "utf8", "strip": True}}},
            "measure": {"effect": "read", "parameters": {}, "steps": [{
                "op": "exchange", "tx": [{"hex": "540a"}],
                "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 128},
                "timeout_ms": 1000, "capture": "reply"
            }], "outputs": {"temperature": {"from": "reply", "kind": "ascii_number", "prefix": "T:", "unit": "degC"}}},
            "set_level": {"effect": "write", "parameters": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                "steps": [{"op": "exchange", "tx": [{"text": "SET "}, {"arg": "level", "format": "ascii"}, {"hex": "0a"}],
                    "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 64}, "timeout_ms": 1000,
                    "capture": "ack", "expect": {"equals_hex": "4f4b0a"}}], "outputs": {}}
        },
        "provenance": [{"item": "operations." + name, "source": "decomp.c: function+offset"}
                       for name in ("identify", "measure", "set_level")]
    }


class Boundary:
    def __init__(self, script):
        self.script = list(script)
        self.opened = False
        self.closed = False
        self.seen = []

    def factory(self, channel, binding):
        self.opened = True
        return self

    def exchange(self, tx, rx, timeout_ms):
        self.seen.append(tx)
        expected, response = self.script.pop(0)
        assert tx == expected
        if isinstance(response, Exception):
            raise response
        return response

    def control(self, setup, tx, rx, timeout_ms):
        self.setup = setup
        return self.exchange(tx, rx, timeout_ms)

    def close(self):
        self.closed = True


def run(model, boundary, op="measure", params=None, **kwargs):
    return execute(model, op, params, binding={}, transport_factory=boundary.factory, **kwargs)


def test_literal_temperature_and_transcript(model):
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"T\n", b"T:21.5\n")])
    assert validate_model(model)["ok"]
    result = run(model, boundary)
    assert result["ok"], result
    assert result["schema"] == "interface-result/1"
    assert result["identity_verified"] is True
    assert result["outputs"] == {"temperature": 21.5}
    assert result["units"] == {"temperature": "degC"}
    assert [(s["operation"], s["step"], s["tx_hex"], s["rx_hex"]) for s in result["transcript"]] == [
        ("identify", 0, "49443f0a", "44454d4f2d34320a"), ("measure", 0, "540a", "543a32312e350a")]
    assert boundary.closed


def test_independent_binary_fixture(model):
    op = model["operations"]["measure"]
    op["parameters"] = {"value": {"type": "integer", "minimum": 0, "maximum": 65535}}
    op["steps"][0].update(tx=[{"arg": "value", "format": "u16le"}], rx={"mode": "exact", "length": 6})
    op["outputs"] = {
        "signed": {"from": "reply", "kind": "integer", "offset": 0, "size": 2, "byteorder": "little", "signed": True},
        "float": {"from": "reply", "kind": "float32", "offset": 2, "byteorder": "little"}}
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"\x02\x01", b"\xfe\xff\x00\x00\xc0\x3f")])
    result = run(model, boundary, params={"value": 258})
    assert result["ok"], result
    assert result["outputs"] == {"signed": -2, "float": 1.5}


@parametrize("path,value", [
    (("schema",), "interface-model/2-experimental"),
    (("surprise",), True), (("provenance",), []),
    (("device", "path"), "/tmp/candidate.py"),
    (("channel", "mystery"), True), (("identity", "operation"), "missing"),
    (("operations", "identify", "effect"), "write"),
    (("operations", "identify", "steps", 0, "expect"), {"contains_text": ""}),
    (("operations", "measure", "effect"), "unknown"),
    (("operations", "measure", "steps", 0, "timeout_ms"), 60001),
    (("operations", "measure", "steps", 0, "timeout_ms"), True),
    (("operations", "measure", "steps", 0, "rx", "max_bytes"), 65537),
    (("operations", "measure", "steps", 0, "tx"), [{"text": "x" * 65537}]),
    (("operations", "measure", "outputs", "temperature", "kind"), "python"),
    (("operations", "set_level", "parameters", "level", "minimum"), float("nan")),
    (("operations", "set_level", "steps", 0, "tx", 1, "format"), "custom"),
    (("operations", "set_level", "parameters", "level", "minimum"), True),
])
def test_whole_model_refused_before_open(model, path, value):
    target = model
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    boundary = Boundary([])
    validation = validate_model(model)
    assert not validation["ok"]
    assert validation["defects"]
    result = run(model, boundary)
    assert not result["ok"]
    assert not boundary.opened


def test_invalid_late_step_refused_before_open(model):
    model["operations"]["set_level"]["steps"].append({"op": "exec", "code": "pass"})
    boundary = Boundary([])
    assert not run(model, boundary)["ok"]
    assert not boundary.opened


@parametrize("params", [{"level": True}, {"level": {"value": 1}}, {"level": 3, "extra": 2}, {}, {"level": float("inf")}, {"level": 101}])
def test_arguments_refused_before_open(model, params):
    boundary = Boundary([])
    assert not run(model, boundary, "set_level", params, allow_effects=("write",))["ok"]
    assert not boundary.opened


def test_effect_requires_explicit_grant(model):
    boundary = Boundary([])
    assert not run(model, boundary, "set_level", {"level": 42})["ok"]
    assert not boundary.opened
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"SET 42\n", b"OK\n")])
    assert run(model, boundary, "set_level", {"level": 42}, allow_effects=("write",))["ok"]
    assert boundary.closed


@parametrize("response", [b"OTHER\n", OSError("disconnect")])
def test_identity_failure_prevents_operation_and_closes(model, response):
    boundary = Boundary([(b"ID?\n", response)])
    result = run(model, boundary)
    assert not result["ok"]
    assert not result["identity_verified"]
    assert boundary.seen == [b"ID?\n"]
    assert result["transcript"][0]["status"] == ("attempted" if isinstance(response, Exception) else "completed")
    assert boundary.closed


@parametrize("response", [b"T:nan\n", b"X:21.5\n", b"T:21.5", OSError("timeout")])
def test_response_failures_close_without_retry(model, response):
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"T\n", response)])
    result = run(model, boundary)
    assert not result["ok"]
    assert result["identity_verified"]
    assert boundary.closed
    assert len(boundary.seen) == 2
    assert result["transcript"][-1]["status"] == ("attempted" if isinstance(response, Exception) or response == b"T:21.5" else "completed")


def test_identity_requested_only_runs_once(model):
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n")])
    result = run(model, boundary, "identify")
    assert result["ok"]
    assert result["outputs"] == {"identity": "DEMO-42"}
    assert len(result["transcript"]) == 1


@parametrize("path,value", [
    (("identity", "extra"), True),
    (("operations", "identify", "parameters"), {"x": {"type": "integer", "minimum": 0, "maximum": 1}}),
    (("operations", "measure", "outputs", "temperature", "regex"), ".*"),
    (("operations", "measure", "outputs", "temperature", "from"), "missing"),
    (("operations", "measure", "steps", 0, "expect"), {"equals_hex": "00", "prefix_hex": "00"}),
    (("operations", "measure", "steps", 0, "rx"), {"mode": "exact", "length": -1}),
    (("operations", "measure", "steps", 0, "rx"), {"mode": "up_to", "max_bytes": 0}),
    (("operations", "measure", "steps", 0, "rx"), {"mode": "until", "delimiter_hex": "0a", "max_bytes": 1, "length": 1}),
    (("operations", "measure", "steps", 0, "tx"), [{"hex": "0 x0"}]),
    (("operations", "measure", "steps", 0, "timeout_ms"), 0),
    (("operations", "set_level", "parameters", "level", "maximum"), -1),
    (("operations", "set_level", "parameters", "level"), {"type": "string", "enum": ["ON\n"]}),
    (("operations", "set_level", "parameters", "level"), {"type": "string", "enum": ["ON"], "minimum": 0}),
    (("operations", "set_level", "parameters", "level"), {"type": "number", "minimum": 0}),
    (("operations", "set_level", "steps", 0, "tx", 1, "format"), "u8"),
])
def test_more_strict_refusals(model, path, value):
    # u8 is a valid format but the amended domain below is not representable.
    if value == "u8":
        model["operations"]["set_level"]["parameters"]["level"]["maximum"] = 256
    test_whole_model_refused_before_open(model, path, value)


def test_total_number_ascii_bounds_validated_before_open(model):
    op = model["operations"]["measure"]
    op["parameters"] = {"x": {"type": "number", "minimum": 0, "maximum": 1e308}}
    op["steps"][0]["tx"] = [{"arg": "x", "format": "ascii"}] * 250
    boundary = Boundary([])
    assert not validate_model(model)["ok"]
    assert not run(model, boundary, params={"x": 10**307})["ok"]
    assert not boundary.opened


def test_capture_overwrite_and_duration_limits(model):
    for steps in ([copy.deepcopy(model["operations"]["measure"]["steps"][0])] * 2,
                  [{"op": "delay", "ms": 1000}] * 121,
                  [{"op": "delay", "ms": 0}] * 129):
        candidate = copy.deepcopy(model)
        candidate["operations"]["measure"]["steps"] = steps
        candidate["operations"]["measure"]["outputs"] = {}
        boundary = Boundary([])
        assert not run(candidate, boundary)["ok"]
        assert not boundary.opened


def test_usb_control_fixture(model):
    model["channel"] = {"type": "usb"}
    identity = model["operations"]["identify"]["steps"][0]
    identity.update(op="usb_control", setup={"request_type": 192, "request": 1, "value": 0, "index": 0},
                    tx=[], rx={"mode": "exact", "length": 8})
    op = model["operations"]["measure"]
    op["parameters"] = {"index": {"type": "integer", "minimum": 0, "maximum": 65535}}
    op["steps"][0].update(op="usb_control", setup={"request_type": 192, "request": 2, "value": 7, "index": {"arg": "index"}},
                          tx=[], rx={"mode": "exact", "length": 4})
    op["outputs"] = {"raw": {"from": "reply", "kind": "hex"}}
    model["operations"].pop("set_level")
    boundary = Boundary([(b"", b"DEMO-42\n"), (b"", b"\x00\x01\x02\x03")])
    result = run(model, boundary, params={"index": 258})
    assert result["ok"], result
    assert result["outputs"] == {"raw": "00010203"}
    assert result["transcript"][1]["setup"] == {"request_type": 192, "request": 2, "value": 7, "index": 258}
    bad = copy.deepcopy(model)
    bad["operations"]["measure"]["steps"][0]["setup"]["request_type"] = 64
    boundary = Boundary([])
    assert not run(bad, boundary, params={"index": 258})["ok"]
    assert not boundary.opened


def test_finite_transformed_values_and_short_decoder(model):
    op = model["operations"]["measure"]
    op["steps"][0]["rx"] = {"mode": "up_to", "max_bytes": 4}
    op["outputs"] = {"value": {"from": "reply", "kind": "float32", "offset": 0, "byteorder": "little"}}
    for response in (b"\x00\x00\x80\x7f", b"\x00"):
        boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"T\n", response)])
        assert not run(model, boundary)["ok"]
        assert boundary.closed
    op["outputs"]["value"]["offset"] = 1
    assert not validate_model(model)["ok"]


def test_string_enum_and_delay(model):
    op = model["operations"]["measure"]
    op["parameters"] = {"mode": {"type": "string", "enum": ["LOW", "HIGH"]}}
    op["steps"].insert(0, {"op": "delay", "ms": 0})
    op["steps"][1]["tx"] = [{"arg": "mode", "format": "ascii"}]
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"HIGH", b"T:21.5\n")])
    assert run(model, boundary, params={"mode": "HIGH"})["ok"]
    boundary = Boundary([])
    assert not run(model, boundary, params={"mode": "UNLISTED"})["ok"]
    assert not boundary.opened


def test_non_json_and_oversized_models_refused(model):
    for candidate in (None, [], {**model, "device": {"name": "a" * 1048576}}, {**model, "device": {"name": object()}}):
        assert not validate_model(candidate)["ok"]
    circular = {}; circular["self"] = circular
    assert not validate_model(circular)["ok"]


def test_open_and_close_failures_are_results(model):
    def bad_factory(channel, binding):
        raise OSError("open failed")
    assert not execute(model, "measure", transport_factory=bad_factory)["ok"]
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"T\n", b"T:21.5\n")])
    def bad_close():
        raise OSError("close failed")
    boundary.close = bad_close
    result = run(model, boundary)
    assert result["error"]["code"] == "close_error"
    assert result["outputs"] == {}
    assert result["units"] == {}


@parametrize("direction", ["in", "out"])
def test_usb_control_uint16_length_refused_before_open(model, direction):
    model["channel"] = {"type": "usb"}
    model["operations"].pop("set_level")
    for operation in model["operations"].values():
        step = operation["steps"][0]
        step.update(op="usb_control", setup={"request_type": 192, "request": 1, "value": 0, "index": 0},
                    tx=[], rx={"mode": "exact", "length": 8})
    step = model["operations"]["measure"]["steps"][0]
    if direction == "in":
        step["rx"]["length"] = 65536
    else:
        step.update(tx=[{"text": "x" * 65536}], rx={"mode": "exact", "length": 0})
        step["setup"]["request_type"] = 64
    boundary = Boundary([])
    assert not validate_model(model)["ok"]
    assert not run(model, boundary)["ok"]
    assert not boundary.opened


def test_injected_delay_preserves_declared_transcript_without_sleep(model):
    model["operations"]["identify"]["steps"].insert(0, {"op": "delay", "ms": 25})
    model["operations"]["measure"]["steps"].insert(0, {"op": "delay", "ms": 1000})
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"T\n", b"T:21.5\n")])
    delays = []
    with patch("interface_runtime.engine.time.sleep", side_effect=AssertionError("injected delay must not sleep")):
        result = run(model, boundary, delay_fn=delays.append)
    assert result["ok"], result
    assert delays == [0.025, 1.0]
    assert [(step["operation"], step["ms"], step["status"]) for step in result["transcript"] if step["op"] == "delay"] == [
        ("identify", 25, "completed"), ("measure", 1000, "completed")]
    assert result["outputs"] == {"temperature": 21.5}


def test_default_delay_uses_sleep_with_seconds(model):
    model["operations"]["measure"]["steps"].insert(0, {"op": "delay", "ms": 250})
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (b"T\n", b"T:21.5\n")])
    with patch("interface_runtime.engine.time.sleep") as sleep:
        result = run(model, boundary)
    assert result["ok"], result
    sleep.assert_called_once_with(0.25)


@parametrize("delay_fn", [False, 0, "skip_delays", {}])
def test_invalid_delay_function_refused_before_open(model, delay_fn):
    boundary = Boundary([])
    result = run(model, boundary, delay_fn=delay_fn)
    assert not result["ok"]
    assert result["error"]["code"] == "validation_error"
    assert not boundary.opened


def test_delay_callback_failure_closes_and_stops_operation(model):
    model["operations"]["measure"]["steps"].insert(0, {"op": "delay", "ms": 25})
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n")])
    def broken_delay(seconds):
        raise RuntimeError("delay failure")
    result = run(model, boundary, delay_fn=broken_delay)
    assert not result["ok"]
    assert boundary.closed
    assert boundary.seen == [b"ID?\n"]
    assert result["outputs"] == {}
    assert result["transcript"][-1]["status"] == "attempted"


def test_model_cannot_select_delay_implementation(model):
    model["delay_fn"] = "skip_delays"
    boundary = Boundary([])
    assert not run(model, boundary)["ok"]
    assert not boundary.opened


class EngineTests(unittest.TestCase):
    pass


def _test_method(function):
    def method(self):
        for args in getattr(function, "cases", [()]):
            with self.subTest(args=args):
                function(make_model(), *args)
    return method


for _name, _function in list(globals().items()):
    if _name.startswith("test_"):
        setattr(EngineTests, _name, _test_method(_function))

if __name__ == "__main__":
    unittest.main()

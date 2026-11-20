"""The runtime on two toy wire protocols: a framed sample with a CRC, and a store whose writes take several exchanges.

The protocols are those of `tests/toy_protocols.py`.
"""
import binascii
import unittest

from interface_runtime.engine import execute, validate_model
from toy_protocols import (
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

    def test_request_crc_covers_varying_argument(self):
        for limit in (-7, 6):
            model = toy_sample_model()
            model["operations"]["sample"]["parameters"] = {
                "limit": {"type": "integer", "minimum": -9, "maximum": 9}}
            model["operations"]["sample"]["steps"][1]["tx"] = [
                {"hex": "357a"}, {"hex": "0231"}, {"arg": "limit", "format": "i16le"},
                {"check": "crc16_ccitt_false", "over_parts": [1, 3], "byteorder": "big"}]
            transport = ToyTransport([
                ("identify", b"TOY-SAMPLE\n"), ("acquire", b"OK\n"),
                ("sample", sample_reply(-80, 3)),
            ])
            result = execute(model, "sample", {"limit": limit}, binding={},
                             allow_effects=["write"], transport_factory=transport.factory)
            self.assertTrue(result["ok"], result)
            body = b"\x02\x31" + limit.to_bytes(2, "little", signed=True)
            expected = b"\x35\x7a" + body + binascii.crc_hqx(body, 0xffff).to_bytes(2, "big")
            self.assertEqual(transport.sent[-1], expected)


class ToyStoreDevice:
    def __init__(self):
        self.committed = {"A": [1, 2], "B": [3, 4]}
        self.pending = None; self.bank = None; self.staged = False; self.opens = 0
    def factory(self, channel, binding):
        self.opens += 1
        return self
    def exchange(self, tx, rx, timeout_ms):
        try:
            words = tx.decode("ascii").split()
            if not words:
                raise ValueError("empty command")
            if words[0] == "ID" and len(words) == 1:
                return b"FAMILY:TOY-STORE\nREADY\n"
            if words[0] == "BEGIN" and len(words) == 2 and words[1] in self.committed:
                if self.pending is not None:
                    raise ValueError("transaction already pending")
                self.bank = words[1]; self.pending = list(self.committed[self.bank])
                self.staged = False
            elif words[0] == "PUT" and len(words) == 3:
                if self.pending is None:
                    raise ValueError("PUT without BEGIN")
                slot, value = int(words[1]), int(words[2])
                if slot not in (0, 1) or not -9 <= value <= 9:
                    return b"ERR:range\nREADY\n"
                self.pending[slot] = value
                self.staged = True
            elif words[0] == "COMMIT" and len(words) == 1:
                if self.pending is None or self.bank is None or not self.staged:
                    raise ValueError("COMMIT without staged PUT")
                self.committed[self.bank] = list(self.pending)
                self.pending = None; self.bank = None; self.staged = False
            elif words[0] == "ABORT" and len(words) == 1:
                if self.pending is None:
                    raise ValueError("ABORT without BEGIN")
                self.pending = None; self.bank = None; self.staged = False
            elif words[0] == "GET" and len(words) == 3:
                if self.pending is not None:
                    raise ValueError("GET during pending transaction")
                bank, slot = words[1], int(words[2])
                if bank not in self.committed or slot not in (0, 1):
                    return b"ERR:range\nREADY\n"
                return ("VALUE:" + str(self.committed[bank][slot]) + "\nREADY\n").encode()
            else:
                raise ValueError("malformed command")
        except (UnicodeDecodeError, ValueError, IndexError) as exc:
            raise AssertionError(f"invalid store command {tx!r}: {exc}") from exc
        return b"READY\n"
    def close(self):
        return None


class ScriptedStoreConnectionTests(unittest.TestCase):
    def test_toy_device_refuses_put_without_begin(self):
        device = ToyStoreDevice()
        with self.assertRaises(AssertionError):
            device.exchange(b"PUT 0 1\n", {}, 1000)

    def test_toy_device_refuses_commit_without_staged_put(self):
        device = ToyStoreDevice()
        device.exchange(b"BEGIN A\n", {}, 1000)
        with self.assertRaises(AssertionError):
            device.exchange(b"COMMIT\n", {}, 1000)

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


def one_format_model():
    """One model that uses, together, what the four earlier formats kept apart: a `lines` exchange, a `frame` exchange
    with a transmitted `check`, a `bytes` parameter sent `raw` and an integer decoder with a `mask`."""
    lines = {"mode": "lines", "max_bytes": 64, "end": {"line_equals": "READY"}}
    guard = {"final_line_equals": "READY", "reject_line_prefix": ["ERR:"]}
    frame = {"mode": "frame", "sync_hex": "7a35", "length": {"offset": 2, "size": 1, "byteorder": "little", "add": 6},
             "max_bytes": 32, "check": {"algorithm": "crc16_ccitt_false", "from": 2, "byteorder": "big"}}
    operations = {
        "identify": {"effect": "read", "parameters": {}, "outputs": {}, "steps": [{
            "op": "exchange", "tx": [{"text": "ID\n"}], "rx": lines, "timeout_ms": 1000, "capture": "identity",
            "expect": {**guard, "contains_line": "FAMILY:TOY-ALL"}}]},
        "status": {"effect": "read", "parameters": {}, "steps": [{
            "op": "exchange", "rx": frame, "timeout_ms": 1000, "capture": "status",
            "tx": [{"hex": "357a"}, {"hex": "0231"},
                   {"check": "crc16_ccitt_false", "over_parts": [1, 2], "byteorder": "big"}],
            "expect": {"prefix_hex": "7a350291"}}],
            "outputs": {"mode": {"from": "status", "kind": "integer", "offset": 4, "size": 2, "byteorder": "little",
                                 "signed": False, "mask": 7}}},
        "label": {"effect": "write", "parameters": {"text": {"type": "bytes", "min_length": 1, "max_length": 8}},
                  "outputs": {}, "steps": [{
                      "op": "exchange", "tx": [{"text": "LABEL "}, {"arg": "text", "format": "raw"}, {"text": "\n"}],
                      "rx": lines, "timeout_ms": 1000, "expect": guard}]},
    }
    return {"schema": "interface-model/7", "device": {"name": "TOY-ALL"}, "channel": {"type": "tcp"},
            "identity": {"operation": "identify"}, "operations": operations,
            "provenance": [{"item": "operations." + name, "source": "independent public toy fixture"}
                           for name in operations]}


class OneFormatTests(unittest.TestCase):
    def test_02_B1_one_format_holds_every_wire_feature_and_each_operation_replays(self):
        from interface_runtime.evidence import run_samples
        model = one_format_model()
        checked = validate_model(model)
        self.assertEqual(checked, {"ok": True, "defects": [], "checks_run": ["interface-model/7"]})
        operations = model["operations"]
        lines, frame = operations["identify"]["steps"][0]["rx"], operations["status"]["steps"][0]["rx"]

        def exchange(tx, rx, reply):
            return {"tx_hex": tx.hex(), "rx_hex": reply.hex(), "rx": rx, "timeout_ms": 1000}
        identity = exchange(b"ID\n", lines, b"FAMILY:TOY-ALL\nREADY\n")
        request = b"\x02\x31"
        body = b"\x02\x91\x8b\x00"
        status = exchange(b"\x35\x7a" + request + binascii.crc_hqx(request, 0xffff).to_bytes(2, "big"), frame,
                          b"\x7a\x35" + body + binascii.crc_hqx(body, 0xffff).to_bytes(2, "big"))
        label = exchange(b"LABEL ALPHA-3\n", lines, b"READY\n")
        cases = {"identify": (None, [identity], {}),
                 "status": (None, [identity, status], {"mode": 3}),
                 "label": ({"text": b"ALPHA-3".hex()}, [identity, label], {})}
        for operation, (parameters, transactions, outputs) in cases.items():
            with self.subTest(operation=operation):
                played = run_samples(model, operation, parameters, allow_effects=["write"],
                                     replay={"schema": "interface-replay/1", "transactions": transactions})
                self.assertTrue(played["ok"], played)
                self.assertEqual(played["evidence_kind"], "replay")
                self.assertEqual(played["results"][0]["outputs"], outputs)
                self.assertEqual(played["transactions"], transactions)


if __name__ == "__main__":
    unittest.main()

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

    def test_01_B1_tx_checksum_input_exceeding_bound_is_rejected_at_validation(self):
        model = one_format_model()
        # 4096-byte payload + 20 1-byte checksums over it: encoded tx is 4116 bytes (<= 65536),
        # but total checksum input is 81920 bytes (> 65536).
        model["operations"]["status"]["steps"][0]["tx"] = [
            {"hex": "ab" * 4096},
            *({"check": "crc8", "over_parts": [0, 1], "byteorder": "big"} for _ in range(20)),
        ]
        checked = validate_model(model)
        self.assertFalse(checked["ok"], checked)
        self.assertTrue(any("checksum input" in d["what"] for d in checked["defects"]), checked["defects"])

    def test_01_B2_unpadded_frame_excludes_check_and_end_bytes_from_non_hex_decoders(self):
        from interface_runtime import checks
        model = one_format_model()
        # Unpadded frame with type byte 0x81, 1-byte length at offset 2, 1-byte crc8 check, 1-byte end marker.
        # With empty body (len=0), bytes are: aa 81 00 <crc> 55 -> header ends at 3, check at 3, end at 4.
        frame = {"mode": "frame", "sync_hex": "aa",
                 "length": {"offset": 2, "size": 1, "byteorder": "big", "add": 5},
                 "max_bytes": 16,
                 "check": {"algorithm": "crc8", "from": 0, "byteorder": "big"},
                 "end_hex": "55"}
        model["operations"]["status"]["steps"][0] = {
            "op": "exchange", "tx": [{"hex": "01"}], "rx": frame, "timeout_ms": 1000, "capture": "reply",
            "expect": {"prefix_hex": "aa81"}}
        model["operations"]["status"]["outputs"] = {
            "value": {"from": "reply", "kind": "integer", "offset": 3, "size": 1, "byteorder": "big", "signed": False}}
        self.assertTrue(validate_model(model)["ok"], validate_model(model))
        header = bytes.fromhex("aa8100")
        short_frame = header + checks.encode("crc8", header, "big") + b"\x55"
        def serve():
            replies = iter([b"FAMILY:TOY-ALL\nREADY\n", short_frame])
            class Handle:
                def exchange(self, tx, rx, timeout_ms):
                    return next(replies)
                def close(self):
                    return None
            return lambda channel, binding: Handle()
        result = execute(model, "status", binding={}, transport_factory=serve())
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["fault"], "model", result)
        # Raw hex decoder still receives the full verified frame including check and end bytes.
        model["operations"]["status"]["outputs"] = {"raw": {"from": "reply", "kind": "hex"}}
        raw_result = execute(model, "status", binding={}, transport_factory=serve())
        self.assertTrue(raw_result["ok"], raw_result)
        self.assertEqual(raw_result["outputs"]["raw"], short_frame.hex())

    def test_01_B3_step_refused_before_transport_call_is_not_marked_dispatched_or_uncertain(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.dispatch_outcome import read
        from interface_runtime import checks
        from interface_runtime.driver import execute_package
        from interface_runtime.evidence import run_samples
        from generative_driver._toolchain import interface as iface_tool
        frame = {"mode": "frame", "sync_hex": "aa",
                 "length": {"offset": 2, "size": 1, "byteorder": "big", "add": 3},
                 "max_bytes": 16, "max_reads": 4}
        id_reply = b"\xaa\x01\x00"
        model = {
            "schema": "interface-model/7", "device": {"name": "TOY-USB"},
            "channel": {"type": "usb", "interface": 0, "in_endpoint": 0x81, "out_endpoint": 0x01},
            "identity": {"operation": "identify"},
            "operations": {
                "identify": {"effect": "read", "parameters": {}, "outputs": {}, "steps": [{
                    "op": "exchange", "tx": [{"hex": "aa01"}], "rx": frame, "timeout_ms": 1000,
                    "expect": {"prefix_hex": "aa01"}}]},
                "write_short": {
                    "effect": "write",
                    "parameters": {"payload": {"type": "bytes", "min_length": 1, "max_length": 8}},
                    "outputs": {},
                    "steps": [{
                        "op": "exchange", "tx": [{"arg": "payload", "format": "raw"}],
                        "rx": frame, "timeout_ms": 1000, "expect": {"prefix_hex": "aa01"},
                        "match": [{"tx_offset": 0, "rx_offset": 0, "size": 2}],
                    }],
                },
            },
            "provenance": [{"item": "operations.identify", "source": "test"},
                           {"item": "operations.write_short", "source": "test"}],
        }
        self.assertTrue(validate_model(model)["ok"], validate_model(model))
        sent = []
        class Handle:
            def exchange(self, tx, rx, timeout_ms):
                sent.append(tx)
                return id_reply
            def close(self):
                return None
        result = execute(model, "write_short", {"payload": "aa"}, binding={}, allow_effects=("write",),
                         transport_factory=lambda channel, binding: Handle())
        self.assertFalse(result["ok"], result)
        self.assertEqual(sent, [bytes.fromhex("aa01")], "write step must never reach transport")
        self.assertIs(result["transcript"][-1].get("dispatched"), False, result["transcript"][-1])
        reading = read("interface_execute", result)
        self.assertFalse(reading.executions[0].sent, reading)
        self.assertFalse(reading.leaves_uncertain, reading)
        # Same through PackageSession / execute_package: requested_command_dispatched must be False.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "model"; model_dir.mkdir()
            (model_dir / "model.json").write_text(json.dumps(model), encoding="utf-8")
            replay = {"schema": "interface-replay/1", "transactions": [
                {"tx_hex": "aa01", "rx_hex": id_reply.hex(), "rx": frame, "timeout_ms": 1000}]}
            probe_out = root / "probe.json"
            sample = run_samples(model, "identify", replay=replay)
            self.assertTrue(sample["ok"], sample)
            from interface_runtime.package import digest
            probe_out.write_text(json.dumps({"schema": "interface-probe/1",
                                             "model_sha256": digest(model_dir / "model.json"),
                                             "operation": "identify", "parameters": {}, "allow_effects": [],
                                             **sample}), encoding="utf-8")
            emitted = iface_tool.emit_package(str(root / "run"), str(model_dir), str(probe_out))
            self.assertTrue(emitted["ok"], emitted)
            sent.clear()
            with patch("interface_runtime.transports.open_transport", lambda channel, binding: Handle()):
                pkg_res = execute_package(emitted["package_dir"], "write_short", {"payload": "aa"},
                                          binding={}, allow_effects=("write",))
        self.assertFalse(pkg_res["ok"], pkg_res)
        self.assertEqual(sent, [bytes.fromhex("aa01")])
        self.assertIs(pkg_res["requested_command_dispatched"], False, pkg_res)
        pkg_reading = read("toy_usb_write_short", pkg_res, generated=True)
        self.assertFalse(pkg_reading.leaves_uncertain, pkg_reading)

    def test_01_B4_resetting_uart_close_error_records_close_error_and_keeps_transactions(self):
        from unittest.mock import patch
        from interface_runtime.evidence import run_samples
        model = one_format_model()
        model["channel"] = {"type": "uart", "baudrate": 115200, "resets_on_open": True}
        lines = model["operations"]["identify"]["steps"][0]["rx"]
        class ClosingFailHandle:
            def exchange(self, tx, rx, timeout_ms):
                return b"FAMILY:TOY-ALL\nREADY\n"
            def close(self):
                raise OSError("serial port vanished on close")
        with patch("interface_runtime.evidence.open_transport", lambda channel, binding: ClosingFailHandle()):
            outcome = run_samples(model, "identify", n=2, binding={"port": "/dev/fake"}, allow_effects=("reset",))
        self.assertFalse(outcome["ok"], outcome)
        self.assertEqual(len(outcome["transactions"]), 2, outcome["transactions"])
        self.assertTrue(outcome["results"][0]["ok"], outcome["results"][0])
        self.assertFalse(outcome["results"][-1]["ok"], outcome["results"][-1])
        self.assertEqual(outcome["results"][-1]["error"]["code"], "close_error", outcome["results"][-1])
        self.assertEqual(outcome["results"][-1]["error"]["fault"], "host", outcome["results"][-1])


if __name__ == "__main__":
    unittest.main()

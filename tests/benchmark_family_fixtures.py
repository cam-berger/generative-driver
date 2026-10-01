"""Independent scripted wire fixtures for the benchmark family protocols."""
import binascii
from copy import deepcopy
import re


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
        if check["kind"] == "number":
            check["absolute_tolerance"] = 0
        checks.append(check)
    contract = {"schema": "benchmark-behavior/1", "artifact_sha256": "a" * 64, "checks": checks}
    pin = {"id": "toy-sampled-oracle", "family": "sampled-sensor"}
    truth = {"family": "sampled-sensor", "artifact_sha256": "a" * 64,
             "phases": {"diagnostic": {"contract": contract, "actions": [
                 {"episode": "toy", "step": "input", "kind": "stimulate", "values": {"source_q4": -80}}]}}}
    return pin, truth


def sample_reply(raw_temperature, sequence):
    payload = raw_temperature.to_bytes(2, "little", signed=True)
    payload += sequence.to_bytes(4, "little")
    body = bytes([len(payload), 0x91]) + payload
    return b"\x7a\x35" + body + binascii.crc_hqx(body, 0xffff).to_bytes(2, "big")


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
    if identity:
        expect["contains_line"] = "FAMILY:TOY-STORE"
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


def toy_store_model_four_cell():
    """Four-cell variant for the transaction oracle; the two-cell model stays fixed."""
    model = toy_store_model()
    for operation in ("update", "stage", "read"):
        model["operations"][operation]["parameters"]["slot"]["maximum"] = 3
    return model


class FourCellStoreDevice:
    """Public volatile store with independent monitor snapshots across connections."""
    def __init__(self, initial):
        self.committed = list(initial)
        self.pending = list(initial)
        self.bank = None
        self.generation = 0
        self.opens = 0

    def factory(self, channel, binding):
        if channel != {"type": "tcp"} or not isinstance(binding, dict):
            raise AssertionError("unexpected toy binding")
        self.opens += 1
        return self

    def exchange(self, tx, rx, timeout_ms):
        if tx == b"ID\n":
            return b"FAMILY:TOY-STORE\nREADY\n"
        begin = re.fullmatch(rb"BEGIN ([AB])\n", tx)
        put = re.fullmatch(rb"PUT ([0-3]) (-?[0-9]+)\n", tx)
        get = re.fullmatch(rb"GET ([AB]) ([0-3])\n", tx)
        if begin:
            if self.bank is not None:
                return b"ERR:order\nREADY\n"
            self.bank = begin.group(1).decode()
            self.pending = list(self.committed)
        elif put:
            value = int(put.group(2))
            if self.bank is None:
                return b"ERR:order\nREADY\n"
            if not -9 <= value <= 9:
                return b"ERR:range\nREADY\n"
            offset = 4 if self.bank == "B" else 0
            self.pending[offset + int(put.group(1))] = value
        elif tx == b"COMMIT\n":
            if self.bank is None:
                return b"ERR:order\nREADY\n"
            self.committed = list(self.pending)
            self.generation += 1
            self.bank = None
        elif tx == b"ABORT\n":
            if self.bank is None:
                return b"ERR:order\nREADY\n"
            self.pending = list(self.committed)
            self.bank = None
        elif get:
            if self.bank is not None:
                return b"ERR:order\nREADY\n"
            offset = 4 if get.group(1) == b"B" else 0
            return ("VALUE:" + str(self.committed[offset + int(get.group(2))]) + "\nREADY\n").encode()
        else:
            return b"ERR:syntax\nREADY\n"
        return b"READY\n"

    def observe(self):
        return {"values": {"committed": [value & 65535 for value in self.committed],
                           "pending": [value & 65535 for value in self.pending],
                           "generation": self.generation,
                           "pending_active": int(self.bank is not None)},
                "reads": [], "time": 0.0, "physical": False}

    def close(self):
        pass


class ToyTransport:
    """Scripted replies guarded by command grammar, ordering and optional request CRC."""
    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.opened = False
        self.closed = False
        self._state = "new"
        self._store_pending = False
        self._store_put = False

    def factory(self, channel, binding):
        if channel != {"type": "tcp"} or not isinstance(binding, dict):
            raise AssertionError("unexpected toy transport binding")
        self.opened = True
        return self

    def _validate_command(self, label, tx):
        if label == "identify":
            if tx != b"ID\n" or self._state != "new":
                raise AssertionError(f"invalid identity command/order: {tx!r} in {self._state}")
            self._state = "identified"
        elif label == "acquire":
            if tx != b"ACQUIRE\n" or self._state != "identified":
                raise AssertionError(f"invalid acquire command/order: {tx!r} in {self._state}")
            self._state = "acquired"
        elif label == "sample":
            if self._state != "acquired":
                raise AssertionError(f"sample before acquire: {tx!r}")
            if tx == b"SAMPLE\n":
                self._state = "sampled"
                return
            if len(tx) < 6 or tx[:2] != b"\x35\x7a" or tx[2:4] != b"\x02\x31":
                raise AssertionError(f"malformed sample request: {tx!r}")
            body, check = tx[2:-2], tx[-2:]
            expected = binascii.crc_hqx(body, 0xffff).to_bytes(2, "big")
            if len(body) != 4 or check != expected:
                raise AssertionError(f"sample request CRC/length mismatch: {tx!r}")
            self._state = "sampled"
        elif label == "begin":
            match = re.fullmatch(rb"BEGIN ([AB])\n", tx)
            if self._state not in ("identified", "store-id") or self._store_pending or not match:
                raise AssertionError(f"invalid BEGIN command/order: {tx!r}")
            self._store_pending = True
            self._store_put = False
            self._state = "store-pending"
        elif label == "put":
            match = re.fullmatch(rb"PUT ([01]) (-?[0-9]+)\n", tx)
            if self._state != "store-pending" or not self._store_pending or not match:
                raise AssertionError(f"invalid PUT command/order: {tx!r}")
            value = int(match.group(2))
            if not -9 <= value <= 9:
                raise AssertionError(f"out-of-range PUT: {tx!r}")
            self._store_put = True
        elif label == "commit":
            if tx != b"COMMIT\n" or not self._store_pending or not self._store_put:
                raise AssertionError(f"invalid COMMIT command/order: {tx!r}")
            self._store_pending = False
            self._state = "identified"
        elif label == "abort":
            if tx != b"ABORT\n" or not self._store_pending:
                raise AssertionError(f"invalid ABORT command/order: {tx!r}")
            self._store_pending = False
            self._state = "identified"
        elif label == "read":
            if not re.fullmatch(rb"GET [AB] [01]\n", tx) or self._store_pending:
                raise AssertionError(f"invalid GET command/order: {tx!r}")
        else:
            raise AssertionError(f"unknown scripted protocol step: {label!r}")

    def exchange(self, tx, rx, timeout_ms):
        if not self.script:
            raise AssertionError(f"unexpected extra command: {tx!r}")
        label, reply = self.script.pop(0)
        self._validate_command(label, tx)
        self.sent.append(tx)
        if label == "put" and reply.startswith(b"ERR:"):
            self._store_put = False
        if label == "identify" and reply == b"FAMILY:TOY-STORE\nREADY\n":
            self._state = "store-id"
        return reply

    def close(self):
        self.closed = True

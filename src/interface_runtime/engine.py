"""Bounded declarative interface-model/3, /4, /5 and /6 validation and transaction execution.

Only the trusted binding selects a host resource. Effects are caller grants to
model declarations, not a proof that a byte sequence is physically harmless.
"""
import json
import math
import struct
import time

from . import checks
from .faults import Fault, classify_failure


class ModelError(ValueError):
    def __init__(self, where, what, fault=Fault.MODEL):
        super().__init__(what)
        self.where = where
        self.fault = fault.value


def _require(condition, where, what, fault=Fault.MODEL):
    if not condition:
        raise ModelError(where, what, fault)


def _fields(value, required, optional, where):
    _require(type(value) is dict, where, "must be an object")
    _require(set(required) <= value.keys(), where, "missing required fields")
    _require(value.keys() <= set(required) | set(optional), where, "unknown fields")


def _name(value):
    return (type(value) is str and 1 <= len(value) <= 64 and value.isascii()
            and value[0].isalpha() and all(c.isalnum() or c == "_" for c in value))


def _integer(value, lo, hi, where):
    _require(type(value) is int and lo <= value <= hi, where, f"must be integer in [{lo}, {hi}]")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _hex(value, where, nonempty=False):
    _require(type(value) is str and len(value) % 2 == 0
             and all(c in "0123456789abcdefABCDEF" for c in value), where, "invalid hex literal")
    _require(not nonempty or bool(value), where, "hex literal must be nonempty")
    return bytes.fromhex(value)


def _json_model(model):
    def walk(value):
        if type(value) is dict:
            _require(all(type(k) is str for k in value), "model", "object keys must be strings")
            for item in value.values():
                walk(item)
        elif type(value) is list:
            for item in value:
                walk(item)
        else:
            _require(value is None or type(value) in (str, int, float, bool), "model", "must contain only JSON values")
    walk(model)
    raw = json.dumps(model, allow_nan=False, separators=(",", ":"))
    _require(len(raw.encode("utf-8")) <= 1048576, "model", "serialized model exceeds 1 MiB")
    return raw


SCHEMAS = ("interface-model/3", "interface-model/4", "interface-model/5", "interface-model/6")

_FORMATS = {
    "u8": ("B", 0, 255), "u16le": ("<H", 0, 65535), "u16be": (">H", 0, 65535),
    "u32le": ("<I", 0, 4294967295), "u32be": (">I", 0, 4294967295),
    "i16le": ("<h", -32768, 32767), "i16be": (">h", -32768, 32767),
    "i32le": ("<i", -2147483648, 2147483647), "i32be": (">i", -2147483648, 2147483647),
    "f32le": ("<f", -3.4028234663852886e38, 3.4028234663852886e38),
    "f32be": (">f", -3.4028234663852886e38, 3.4028234663852886e38),
}
# Schema 6: a bytes argument inserted verbatim, or its byte count as a fixed-width field.
_LENGTH_FORMATS = {"length_u8": ("B", 255), "length_u16le": ("<H", 65535), "length_u16be": (">H", 65535)}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _parameter(spec, where, schema6=False):
    _fields(spec, ("type",), ("minimum", "maximum", "enum", "min_length", "max_length"), where)
    kind = spec["type"]
    if kind in ("integer", "number"):
        _fields(spec, ("type", "minimum", "maximum"), (), where)
        lo, hi = spec["minimum"], spec["maximum"]
        _require(_finite(lo) and _finite(hi) and lo <= hi, where, "finite ordered numeric bounds required")
        if kind == "integer":
            _require(type(lo) is int and type(hi) is int, where, "integer bounds required")
    elif kind == "string":
        _fields(spec, ("type", "enum"), (), where)
        values = spec["enum"]
        _require(type(values) is list and bool(values), where, "nonempty string enum required")
        _require(all(type(v) is str and all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in v) for v in values), where, "enum strings cannot contain control characters")
        _require(len(set(values)) == len(values), where, "duplicate enum value")
    elif kind == "bytes":
        # A bounded byte string the caller supplies as hex, for payloads whose content and length vary.
        _require(schema6, where, "bytes parameters require interface-model/6")
        _fields(spec, ("type", "min_length", "max_length"), (), where)
        _integer(spec["min_length"], 0, 4096, where)
        _integer(spec["max_length"], 1, 4096, where)
        _require(spec["min_length"] <= spec["max_length"], where, "min_length exceeds max_length")
    else:
        raise ModelError(where, "unsupported parameter type")


def _tx_bound(parts, parameters, where, _former_flag=None, *, level=6):
    """Validate a tx list and return its largest encoded length.

    `level` is the model's schema number; validation passes it so that a part the declared schema lacks
    is refused. Sizing callers run after validation and may omit it. The fourth positional argument, a
    former schema-5 flag, is accepted for those callers and ignored.
    """
    _require(type(parts) is list, where, "tx must be a list")
    total = 0
    for i, part in enumerate(parts):
        loc = f"{where}.{i}"
        _fields(part, (), ("hex", "text", "arg", "format", "check", "over_parts", "byteorder"), loc)
        if "check" in part:
            # A checksum over the encoded bytes of earlier parts of this same tx list.
            _require(level >= 5, loc, "tx check parts require interface-model/5")
            _fields(part, ("check", "over_parts", "byteorder"), (), loc)
            span = part["over_parts"]
            _require(type(span) is list and len(span) == 2 and all(type(v) is int for v in span)
                     and 0 <= span[0] < span[1] <= i, loc, "over_parts must be [first, end) of earlier parts")
            _require(part["byteorder"] in ("big", "little"), loc, "check byteorder must be big or little")
            try:
                _, _, size = checks.resolve(part["check"])
            except checks.CheckError as exc:
                raise ModelError(loc, str(exc)) from exc
            total += size
        elif set(part) == {"hex"}:
            total += len(_hex(part["hex"], loc))
        elif set(part) == {"text"}:
            _require(type(part["text"]) is str, loc, "text must be a string")
            total += len(part["text"].encode("utf-8"))
        elif set(part) == {"arg", "format"}:
            _require(type(part["arg"]) is str and part["arg"] in parameters, loc, "unknown parameter")
            p, fmt = parameters[part["arg"]], part["format"]
            _require(type(fmt) is str and (fmt in ("ascii", "raw") or fmt in _FORMATS or fmt in _LENGTH_FORMATS),
                     loc, "unknown argument format")
            if fmt == "raw" or fmt in _LENGTH_FORMATS:
                _require(level >= 6, loc, "raw and length argument formats require interface-model/6")
                _require(p["type"] == "bytes", loc, "raw and length formats require a bytes parameter")
                if fmt == "raw":
                    total += p["max_length"]
                else:
                    _require(p["max_length"] <= _LENGTH_FORMATS[fmt][1], loc, "max_length exceeds the length field")
                    total += struct.calcsize(_LENGTH_FORMATS[fmt][0])
            elif fmt == "ascii":
                _require(p["type"] != "bytes", loc, "bytes parameters encode only as raw or a length format")
                if p["type"] == "string":
                    _require(all(v.isascii() for v in p["enum"]), loc, "ascii arguments require ASCII enum values")
                    total += max(len(v) for v in p["enum"])
                else:
                    # Number domains also admit Python integers: 10**307 renders
                    # as 308 digits, even when its bound is written as 1e308.
                    total += max(len(str(p["minimum"])), len(str(p["maximum"])),
                                 len(str(int(p["minimum"]))), len(str(int(p["maximum"]))),
                                 24 if p["type"] == "number" else 1)
            else:
                fmt_spec, lo, hi = _FORMATS[fmt]
                required_types = ("integer", "number") if fmt.startswith("f") else ("integer",)
                _require(p["type"] in required_types, loc, "parameter type incompatible with format")
                _require(lo <= p["minimum"] <= p["maximum"] <= hi, loc, "parameter bounds exceed encoding")
                total += struct.calcsize(fmt_spec)
        else:
            raise ModelError(loc, "tx part must be hex, text, arg/format, or check")
    _require(total <= 65536, where, "encoded tx exceeds 65536 bytes")
    return total


def _text(value, where, what="must be nonempty printable ASCII text"):
    _require(type(value) is str and bool(value) and value.isascii()
             and all(32 <= ord(c) < 127 for c in value), where, what)
    return value


def _end(end, where):
    _fields(end, (), ("line_equals", "line_token", "line_prefix"), where)
    _require(len(end) == 1, where, "exactly one completion-line form required")
    key, value = next(iter(end.items()))
    _text(value, where)
    if key == "line_token":
        _require(value.split() == [value], where, "line_token must be a single token")


def _rx(rx, channel_type, control, where, schema4=False, schema5=False, schema6=False):
    _fields(rx, ("mode",), ("length", "max_bytes", "delimiter_hex", "end", "newline", "max_lines",
                            "sync_hex", "check", "end_hex", "max_reads"), where)
    mode = rx["mode"]
    from .transports import capabilities
    allowed = ("exact", "up_to") if control else capabilities()[channel_type]["exchange_modes"]
    _require(mode in allowed, where, "unsupported receive mode for channel")
    if mode == "frame":
        # Length-prefixed binary frame: sync bytes, a length field, optional check and end bytes.
        _require(schema5, where, "frame receive mode requires interface-model/5")
        _require(channel_type != "usb" or schema6, where,
                 "USB frame receive requires interface-model/6")
        try:
            spec = checks.frame_spec(rx)
        except checks.CheckError as exc:
            raise ModelError(where, str(exc)) from exc
        _require(schema6 or not ({"body_mask", "padding"} & rx["length"].keys()), where,
                 "composite frame lengths require interface-model/6")
        _require(channel_type == "usb" or "max_reads" not in rx, where,
                 "max_reads applies only to USB frames")
        _require(channel_type != "usb" or "max_reads" in rx, where,
                 "USB frame receive requires max_reads")
        return spec["max"]
    if mode == "lines":
        # Completion-aware line protocol: read whole lines until one satisfies `end`, consume
        # exactly through that line, and leave later bytes for the next exchange.
        _require(schema4, where, "lines receive mode requires interface-model/4")
        _fields(rx, ("mode", "max_bytes", "end"), ("newline", "max_lines"), where)
        _integer(rx["max_bytes"], 2, 65536, where)
        _end(rx["end"], where)
        _require(rx.get("newline", "auto") in ("auto", "lf", "crlf"), where, "newline must be auto, lf or crlf")
        _integer(rx.get("max_lines", 4096), 1, 4096, where)
        return rx["max_bytes"]
    if mode == "exact":
        _fields(rx, ("mode", "length"), (), where)
        _integer(rx["length"], 0, 65536, where)
        return rx["length"]
    _fields(rx, ("mode", "max_bytes") + (("delimiter_hex",) if mode == "until" else ()), (), where)
    _integer(rx["max_bytes"], 1, 65536, where)
    if mode == "until":
        delimiter = _hex(rx["delimiter_hex"], where, True)
        _require(len(delimiter) <= rx["max_bytes"], where, "delimiter exceeds receive bound")
    return rx["max_bytes"]


_BYTE_FORMS = ("equals_hex", "prefix_hex", "contains_text")
_REJECT_FORMS = ("reject_line_prefix", "reject_prefix_hex")
_LINE_FORMS = ("contains_line", "line_prefix_present", "first_line_prefix", "final_line_equals", "final_line_token")


def _expect(expect, bound, where, schema4=False, mode=None, rx=None, schema5=False):
    _fields(expect, (), _BYTE_FORMS + _LINE_FORMS + _REJECT_FORMS, where)
    positives = [k for k in expect if k not in _REJECT_FORMS]
    if not schema4:
        _require(len(expect) == 1 and len(positives) == 1 and positives[0] in _BYTE_FORMS, where,
                 "exactly one expectation form required")
    else:
        _require(positives, where, "at least one positive expectation form required")
        _require(mode in ("lines", "until") or not any(k in _LINE_FORMS for k in positives), where,
                 "line expectations require a lines or until receive mode")
    for key in positives:
        value = expect[key]
        if key in ("equals_hex", "prefix_hex"):
            literal = _hex(value, where, True)
        elif key == "contains_text":
            _require(type(value) is str and bool(value), where, "nonempty expectation required")
            literal = value.encode("utf-8")
        else:
            literal = _text(value, where).encode("ascii")
            if key == "final_line_token":
                _require(value.split() == [value], where, "final_line_token must be a single token")
        _require(len(literal) <= bound, where, "expectation exceeds receive bound")
    if "reject_line_prefix" in expect:
        _require(schema4, where, "reject_line_prefix requires interface-model/4")
        rejects = expect["reject_line_prefix"]
        _require(type(rejects) is list and 1 <= len(rejects) <= 16, where, "reject_line_prefix needs 1-16 entries")
        for item in rejects:
            _text(item, where)
        _require(len(set(rejects)) == len(rejects), where, "duplicate reject_line_prefix entry")
    if schema4 and mode == "lines":
        _require(expect.get("reject_line_prefix"), where,
                 "lines receive mode requires reject_line_prefix naming the device's error lines")
    if "reject_prefix_hex" in expect:
        _require(schema5, where, "reject_prefix_hex requires interface-model/5")
        rejects = expect["reject_prefix_hex"]
        _require(type(rejects) is list and 1 <= len(rejects) <= 16, where, "reject_prefix_hex needs 1-16 entries")
        literals = [_hex(item, where, True) for item in rejects]
        _require(len(set(literals)) == len(literals), where, "duplicate reject_prefix_hex entry")
    if mode == "frame":
        # A frame's sync bytes say nothing about which reply arrived; the positive guard must pin a
        # byte beyond them, such as a response code, so that an error frame cannot pass as success.
        _require(not any(k in _LINE_FORMS for k in positives), where, "line expectations do not apply to frames")
        sync = bytes.fromhex(rx["sync_hex"])
        pinned = [bytes.fromhex(expect[k]) for k in ("equals_hex", "prefix_hex") if k in expect]
        _require(any(len(p) > len(sync) and p.startswith(sync) for p in pinned), where,
                 "frame receive requires equals_hex or prefix_hex that starts with the sync bytes and pins at least one more byte")


# Standard requests that change the device's address, configuration or alternate setting: the host owns them.
_HOST_OWNED_REQUESTS = frozenset({5, 9, 11})


def _setup(setup, parameters, tx_bound, rx_bound, where, effect="read"):
    _require(tx_bound <= 65535 and rx_bound <= 65535, where, "USB control length exceeds uint16 wLength")
    _fields(setup, ("request_type", "request", "value", "index"), (), where)
    bounds = {}
    for key, value in setup.items():
        maximum = 255 if key in ("request_type", "request") else 65535
        if type(value) is dict:
            _fields(value, ("arg",), (), where)
            _require(type(value["arg"]) is str and value["arg"] in parameters, where, "unknown setup parameter")
            p = parameters[value["arg"]]
            _require(p["type"] == "integer", where, "setup parameter must be integer")
            _require(0 <= p["minimum"] <= p["maximum"] <= maximum, where, "setup parameter exceeds field bounds")
            bounds[key] = (p["minimum"], p["maximum"])
        else:
            _integer(value, 0, maximum, where)
            bounds[key] = (value, value)
    low, high = bounds["request_type"]
    _require((low >= 128 and tx_bound == 0) or (high < 128 and rx_bound == 0), where, "control direction conflicts with tx/rx bounds")
    # A host-to-device request changes device state, and a read runs with no grant (the identity always does).
    _require(effect != "read" or low >= 128, where, "a control request that can be host-to-device needs a write or actuate effect")
    standard = any((kind & 0x60) == 0 for kind in range(low, high + 1))
    requests = range(bounds["request"][0], bounds["request"][1] + 1)
    _require(not (standard and _HOST_OWNED_REQUESTS.intersection(requests)), where,
             "SET_ADDRESS, SET_CONFIGURATION and SET_INTERFACE belong to the host and cannot be modelled")


def _decoder(decoder, captures, where, schema4=False, schema6=False):
    _fields(decoder, ("from", "kind"), ("unit", "strip", "prefix", "offset", "size", "byteorder", "signed", "scale", "offset_value", "key", "line_prefix",
                                        "mask", "shift", "max_length", "encoding", "length_offset", "length_size", "includes_terminator"), where)
    _require(type(decoder["from"]) is str and decoder["from"] in captures, where, "unknown source capture")
    kind = decoder["kind"]
    options = {"hex": (), "utf8": ("strip",), "ascii_number": ("prefix",),
               "integer": ("offset", "size", "byteorder", "signed", "scale", "offset_value", "mask", "shift"),
               "float32": ("offset", "byteorder", "scale", "offset_value"),
               "kv_number": ("scale", "offset_value"), "kv_text": (), "line_text": ("strip",),
               "cstring": ("max_length", "encoding"), "lpstring": ("encoding",)}
    _require(type(kind) is str and kind in options, where, "unknown decoder")
    _require(schema4 or kind not in ("kv_number", "kv_text", "line_text"), where, "decoder requires interface-model/4")
    _require(schema6 or kind not in ("cstring", "lpstring"), where, "decoder requires interface-model/6")
    required = {"integer": ("offset", "size", "byteorder", "signed"), "float32": ("offset", "byteorder"),
                "kv_number": ("key",), "kv_text": ("key",), "line_text": ("line_prefix",), "cstring": ("offset",),
                "lpstring": ("length_offset", "length_size", "byteorder", "offset", "includes_terminator")}.get(kind, ())
    _fields(decoder, ("from", "kind") + required, ("unit",) + options[kind], where)
    _require(schema6 or not {"mask", "shift"} & decoder.keys(), where, "mask and shift require interface-model/6")
    if "key" in decoder:
        _text(decoder["key"], where)
        _require(decoder["key"].split() == [decoder["key"]], where, "key must not contain whitespace")
    if "line_prefix" in decoder:
        _text(decoder["line_prefix"], where)
    if "unit" in decoder:
        _require(type(decoder["unit"]) is str, where, "unit must be string")
    if "strip" in decoder:
        _require(type(decoder["strip"]) is bool, where, "strip must be boolean")
    if "prefix" in decoder:
        _require(type(decoder["prefix"]) is str and decoder["prefix"].isascii(), where, "prefix must be literal ASCII text")
    if kind in ("integer", "float32"):
        _integer(decoder["offset"], 0, 65536, where)
        size = decoder["size"] if kind == "integer" else 4
        _require(type(size) is int and size in (1, 2, 4, 8), where, "invalid decoder size")
        _require(decoder["byteorder"] in ("little", "big"), where, "invalid byteorder")
        _require(decoder["offset"] + size <= captures[decoder["from"]], where, "decoder exceeds source bounds")
        if kind == "integer":
            _require(type(decoder["signed"]) is bool, where, "signed must be boolean")
            if "mask" in decoder:
                _integer(decoder["mask"], 1, (1 << 64) - 1, where)
            if "shift" in decoder:
                _integer(decoder["shift"], 0, 8 * size - 1, where)
        for key in ("scale", "offset_value"):
            if key in decoder:
                _require(_finite(decoder[key]), where, "numeric transform must be finite")
    bound = captures[decoder["from"]]
    if kind == "cstring":
        _integer(decoder["offset"], 0, 65535, where)
        _require(decoder["offset"] < bound, where, "decoder exceeds source bounds")
        if "max_length" in decoder:
            _integer(decoder["max_length"], 0, 65535, where)
    if kind == "lpstring":
        _integer(decoder["length_offset"], 0, 65535, where)
        _require(type(decoder["length_size"]) is int and decoder["length_size"] in (1, 2), where, "length_size must be 1 or 2")
        _require(decoder["byteorder"] in ("little", "big"), where, "invalid byteorder")
        _require(decoder["length_offset"] + decoder["length_size"] <= bound, where, "decoder exceeds source bounds")
        _integer(decoder["offset"], 0, 65536, where)
        _require(decoder["offset"] <= bound, where, "decoder exceeds source bounds")
        _require(type(decoder["includes_terminator"]) is bool, where, "includes_terminator must be boolean")
    if "encoding" in decoder:
        _require(decoder["encoding"] in ("ascii", "utf-8"), where, "encoding must be ascii or utf-8")


def _validate(model):
    _json_model(model)
    _fields(model, ("schema", "device", "channel", "identity", "operations", "provenance"), (), "model")
    _require(model["schema"] in SCHEMAS, "schema", "unsupported model schema")
    level = int(model["schema"].rsplit("/", 1)[1])
    schema4, schema5, schema6 = level >= 4, level >= 5, level >= 6
    _fields(model["device"], ("name",), (), "device")
    _require(model["device"]["name"] is None or type(model["device"]["name"]) is str, "device.name", "must be string or null")
    from .transports import validate_channel
    try:
        validate_channel(model["channel"])
    except (ValueError, OSError, TypeError) as exc:
        raise ModelError("channel", str(exc)) from exc
    _fields(model["identity"], ("operation",), (), "identity")
    operations = model["operations"]
    _require(type(operations) is dict and 1 <= len(operations) <= 64, "operations", "requires 1–64 operations")
    for name, operation in operations.items():
        loc = "operations." + name
        _require(_name(name), loc, "invalid operation name")
        _fields(operation, ("effect", "parameters", "steps", "outputs"), (), loc)
        _require(operation["effect"] in ("read", "write", "actuate"), loc, "unknown effect")
        parameters = operation["parameters"]
        _require(type(parameters) is dict and len(parameters) <= 32, loc, "at most 32 parameters")
        for pname, spec in parameters.items():
            _require(_name(pname), loc, "invalid parameter name")
            _parameter(spec, loc + ".parameters." + pname, schema6)
        steps, captures, duration = operation["steps"], {}, 0
        _require(type(steps) is list and 1 <= len(steps) <= 128, loc, "requires 1–128 steps")
        for i, step in enumerate(steps):
            sloc = loc + f".steps.{i}"
            _fields(step, ("op",), ("ms", "tx", "rx", "timeout_ms", "capture", "expect", "setup", "match"), sloc)
            if step["op"] == "delay":
                _fields(step, ("op", "ms"), (), sloc)
                _integer(step["ms"], 0, 1000, sloc)
                duration += step["ms"]
                continue
            _require(step["op"] in ("exchange", "usb_control"), sloc, "unknown step")
            control = step["op"] == "usb_control"
            _fields(step, ("op", "tx", "rx", "timeout_ms") + (("setup",) if control else ()), ("capture", "expect", "match"), sloc)
            _require(not control or model["channel"]["type"] == "usb", sloc, "control requires USB channel")
            _integer(step["timeout_ms"], 1, 60000, sloc)
            duration += step["timeout_ms"]
            tx_bound = _tx_bound(step["tx"], parameters, sloc + ".tx", level=level)
            rx_bound = _rx(step["rx"], model["channel"]["type"], control, sloc + ".rx", schema4, schema5, schema6)
            if "match" in step:
                _require(schema6 and model["channel"]["type"] == "usb" and not control
                         and step["rx"]["mode"] == "frame", sloc,
                         "request/reply matching requires a schema-6 USB frame exchange")
                rules = step["match"]
                _require(type(rules) is list and 1 <= len(rules) <= 8, sloc + ".match",
                         "match requires 1–8 byte ranges")
                fixed_header = checks.frame_spec(step["rx"])["header"]
                for rule_index, rule in enumerate(rules):
                    rloc = sloc + f".match.{rule_index}"
                    _fields(rule, ("tx_offset", "rx_offset", "size"), (), rloc)
                    _integer(rule["tx_offset"], 0, 65535, rloc)
                    _integer(rule["rx_offset"], 0, 65535, rloc)
                    _integer(rule["size"], 1, 64, rloc)
                    _require(rule["tx_offset"] + rule["size"] <= tx_bound
                             and rule["rx_offset"] + rule["size"] <= fixed_header, rloc,
                             "match range exceeds the request or fixed response header")
            if schema6 and model["channel"]["type"] == "usb" and not control:
                # Refused here rather than by the transport, which could only refuse once opened.
                _require(tx_bound == 0 or "out_endpoint" in model["channel"], sloc, "a USB exchange that transmits requires channel out_endpoint")
                _require(rx_bound == 0 or "in_endpoint" in model["channel"], sloc, "a USB exchange that receives requires channel in_endpoint")
            if control:
                _setup(step["setup"], parameters, tx_bound, rx_bound, sloc + ".setup", operation["effect"])
            _require(step["rx"]["mode"] != "lines" or "expect" in step, sloc,
                     "lines receive mode requires an expectation with reject_line_prefix")
            _require(step["rx"]["mode"] != "frame" or "expect" in step, sloc,
                     "frame receive mode requires an expectation pinning the reply beyond its sync bytes")
            if "expect" in step:
                _expect(step["expect"], rx_bound, sloc + ".expect", schema4, step["rx"]["mode"], step["rx"], schema5)
            if "capture" in step:
                capture = step["capture"]
                _require(_name(capture) and capture not in captures, sloc, "invalid or overwritten capture")
                captures[capture] = rx_bound
        _require(duration <= 120000, loc, "operation duration exceeds 120000 ms")
        outputs = operation["outputs"]
        _require(type(outputs) is dict, loc, "outputs must be object")
        for oname, decoder in outputs.items():
            _require(_name(oname), loc, "invalid output name")
            _decoder(decoder, captures, loc + ".outputs." + oname, schema4, schema6)
    identity = model["identity"]["operation"]
    _require(type(identity) is str and identity in operations, "identity", "identity references unknown operation")
    identity_op = operations[identity]
    _require(identity_op["effect"] == "read" and not identity_op["parameters"], "identity", "identity must be read-only and parameterless")
    _require(any("expect" in step for step in identity_op["steps"]), "identity", "identity requires nonempty response expectation")
    provenance = model["provenance"]
    _require(type(provenance) is list, "provenance", "must be a list")
    covered = set()
    for entry in provenance:
        _fields(entry, ("item", "source"), (), "provenance")
        _require(type(entry["item"]) is str and type(entry["source"]) is str and bool(entry["source"].strip()), "provenance", "item and nonempty source required")
        covered.add(entry["item"])
    _require(all("operations." + name in covered for name in operations), "provenance", "every operation needs provenance")


def validate_model(model):
    """Return a serializable validation report; never opens a transport."""
    try:
        _validate(model)
    except (ModelError, ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError) as exc:
        return {"ok": False, "defects": [{"where": getattr(exc, "where", "model"), "what": str(exc)}],
                "checks_run": [model["schema"] if type(model) is dict and model.get("schema") in SCHEMAS else "interface-model/3"]}
    return {"ok": True, "defects": [], "checks_run": [model["schema"]]}


def _arguments(specs, parameters):
    _require(type(parameters) is dict and parameters.keys() == specs.keys(), "parameters", "arguments must match parameter names exactly", Fault.OPERATOR)
    for name, spec in specs.items():
        value = parameters[name]
        if spec["type"] == "string":
            valid = type(value) is str and value in spec["enum"]
        elif spec["type"] == "bytes":
            valid = (type(value) is str and len(value) % 2 == 0 and set(value) <= _HEX_DIGITS
                     and spec["min_length"] <= len(value) // 2 <= spec["max_length"])
        else:
            valid = _finite(value) and (spec["type"] != "integer" or type(value) is int) and spec["minimum"] <= value <= spec["maximum"]
        _require(valid, "parameters." + name, "argument violates parameter type or bounds", Fault.OPERATOR)


def _encode(parts, parameters):
    chunks = []
    for part in parts:
        if "check" in part:
            first, end = part["over_parts"]
            chunks.append(checks.encode(part["check"], b"".join(chunks[first:end]), part["byteorder"]))
        elif "hex" in part:
            chunks.append(bytes.fromhex(part["hex"]))
        elif "text" in part:
            chunks.append(part["text"].encode("utf-8"))
        else:
            value, fmt = parameters[part["arg"]], part["format"]
            if fmt == "raw":
                chunks.append(bytes.fromhex(value))
            elif fmt in _LENGTH_FORMATS:
                chunks.append(struct.pack(_LENGTH_FORMATS[fmt][0], len(value) // 2))
            else:
                chunks.append(str(value).encode("ascii") if fmt == "ascii" else struct.pack(_FORMATS[fmt][0], value))
    encoded = b"".join(chunks)
    _require(len(encoded) <= 65536, "tx", "encoded tx exceeds limit")
    return encoded


def _lines(data):
    """Decode a captured line reply into lines without terminators. Tolerant decoding: bytes that
    are not UTF-8 become U+FFFD and simply fail to match any expectation."""
    text = data.decode("utf-8", "replace")
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [part[:-1] if part.endswith("\r") else part for part in parts]


def _token(line):
    words = line.split(None, 1)
    return words[0] if words else ""


def _end_matches(line, end):
    key, value = next(iter(end.items()))
    if key == "line_equals":
        return line == value
    if key == "line_token":
        return _token(line) == value
    return line.startswith(value)


def _response(data, rx):
    _require(type(data) is bytes, "rx", "transport response must be bytes")
    if rx["mode"] == "frame":
        try:
            checks.verify_frame(data, checks.frame_spec(rx))
        except checks.FrameError as exc:
            raise ModelError("rx", str(exc)) from exc
        return
    if rx["mode"] == "lines":
        _require(data.endswith(b"\n") and len(data) <= rx["max_bytes"], "rx", "line reply must end at a newline within bound")
        newline = rx.get("newline", "auto")
        raw = data.split(b"\n")[:-1]
        if newline == "crlf":
            _require(all(part.endswith(b"\r") for part in raw), "rx", "reply lines must end with CRLF")
        if newline == "lf":
            _require(not any(part.endswith(b"\r") for part in raw), "rx", "reply lines must end with LF only")
        lines = _lines(data)
        _require(1 <= len(lines) <= rx.get("max_lines", 4096), "rx", "line count exceeds max_lines")
        matches = [_end_matches(line, rx["end"]) for line in lines]
        _require(matches[-1] and not any(matches[:-1]), "rx", "reply must end at the first completion line")
        return
    if rx["mode"] == "exact":
        _require(len(data) == rx["length"], "rx", "exact response length mismatch")
    else:
        _require(len(data) <= rx["max_bytes"], "rx", "response exceeds bound")
        if rx["mode"] == "until":
            delimiter = bytes.fromhex(rx["delimiter_hex"])
            _require(data.endswith(delimiter) and data.find(delimiter) == len(data) - len(delimiter), "rx", "response must end at first delimiter")


def _matches(data, expect):
    """Every positive form must hold and no rejected line prefix may appear."""
    lines = None
    for key, value in expect.items():
        if key in _REJECT_FORMS:
            continue
        if key == "equals_hex":
            ok = data == bytes.fromhex(value)
        elif key == "prefix_hex":
            ok = data.startswith(bytes.fromhex(value))
        elif key == "contains_text":
            ok = value.encode("utf-8") in data
        else:
            lines = _lines(data) if lines is None else lines
            if key == "contains_line":
                ok = value in lines
            elif key == "line_prefix_present":
                ok = any(line.startswith(value) for line in lines)
            elif key == "first_line_prefix":
                ok = bool(lines) and lines[0].startswith(value)
            elif key == "final_line_equals":
                ok = bool(lines) and lines[-1] == value
            else:
                ok = bool(lines) and _token(lines[-1]) == value
        if not ok:
            return False
    for prefix in expect.get("reject_line_prefix", ()):
        lines = _lines(data) if lines is None else lines
        if any(line.startswith(prefix) for line in lines):
            return False
    for prefix in expect.get("reject_prefix_hex", ()):
        if data.startswith(bytes.fromhex(prefix)):
            return False
    return True


def _number_prefix(text):
    run = ""
    for c in text:
        if c in "+-0123456789.eE":
            run += c
        else:
            break
    _require(bool(run) and any(ch.isdigit() for ch in run), "output", "invalid ASCII number")
    try:
        return float(run)
    except ValueError:
        raise ModelError("output", "invalid ASCII number")


def _string(raw, decoder):
    encoding = decoder.get("encoding", "ascii")
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        raise ModelError("output", f"string is not valid {encoding}") from None


def _decode(decoder, captures):
    data, kind = captures[decoder["from"]], decoder["kind"]
    if kind == "hex":
        return data.hex()
    if kind == "cstring":
        start = decoder["offset"]
        window = data[start:] if "max_length" not in decoder else data[start:start + decoder["max_length"] + 1]
        end = window.find(b"\x00")
        _require(end >= 0, "output", "no NUL terminator within the string bounds")
        return _string(window[:end], decoder)
    if kind == "lpstring":
        at, size, start = decoder["length_offset"], decoder["length_size"], decoder["offset"]
        field = data[at:at + size]
        _require(len(field) == size, "output", "response too short for decoder")
        count = int.from_bytes(field, decoder["byteorder"])
        raw = data[start:start + count]
        _require(start <= len(data) and len(raw) == count, "output", "response too short for counted string")
        if decoder["includes_terminator"] and count:
            _require(raw.endswith(b"\x00"), "output", "counted string does not end with its terminator")
            raw = raw[:-1]
        return _string(raw, decoder)
    if kind == "utf8":
        text = data.decode("utf-8")
        return text.strip() if decoder.get("strip", False) else text
    if kind in ("kv_number", "kv_text"):
        key = decoder["key"]
        tokens = [t for t in data.decode("utf-8", "replace").split() if t.startswith(key)]
        _require(bool(tokens), "output", "key not present in reply")
        rest = tokens[0][len(key):]
        if kind == "kv_text":
            _require(bool(rest), "output", "key has no value")
            return rest
        result = _number_prefix(rest) * decoder.get("scale", 1) + decoder.get("offset_value", 0)
        _require(_finite(result), "output", "non-finite numeric output")
        return result
    if kind == "line_text":
        prefix = decoder["line_prefix"]
        found = [line for line in _lines(data) if line.startswith(prefix)]
        _require(bool(found), "output", "no line with the requested prefix")
        rest = found[0][len(prefix):]
        return rest.strip() if decoder.get("strip", True) else rest
    if kind == "ascii_number":
        text = data.decode("ascii")
        prefix = decoder.get("prefix", "")
        _require(text.startswith(prefix), "output", "numeric prefix mismatch")
        number_text = text[len(prefix):].strip()
        _require(bool(number_text) and all(c in "+-0123456789.eE" for c in number_text), "output", "invalid ASCII number")
        try:
            result = float(number_text)
        except ValueError:
            raise ModelError('output', 'invalid ASCII number') from None
    else:
        start = decoder["offset"]
        size = decoder["size"] if kind == "integer" else 4
        raw = data[start:start + size]
        _require(len(raw) == size, "output", "response too short for decoder")
        result = (int.from_bytes(raw, decoder["byteorder"], signed=decoder["signed"]) if kind == "integer"
                  else struct.unpack("<f" if decoder["byteorder"] == "little" else ">f", raw)[0])
        if "shift" in decoder:
            result >>= decoder["shift"]
        if "mask" in decoder:
            result &= decoder["mask"]
        result = result * decoder.get("scale", 1) + decoder.get("offset_value", 0)
    _require(_finite(result), "output", "non-finite numeric output")
    return result


def execute(model, operation, parameters=None, *, binding=None, allow_effects=(), transport_factory=None, delay_fn=None):
    """Validate everything, verify identity, execute once, and always close.

    Failures are returned as ``ok: false`` and ``error: {code, message}``.
    Identity verification means the observed/replayed response matched the model;
    it is not a claim of fresh physical hardware verification.
    A trusted ``delay_fn(seconds)`` may replace sleep for replay; models cannot
    select it, and declared delays remain unchanged in the transcript.
    An adapter exposing a positive ``drained_bytes`` adds the optional key
    ``transport_notes: {drained_bytes}``. Failed results also report whether
    transport opening was attempted; even a failed open can have device effects.
    """
    result = {"schema": "interface-result/1", "operation": operation, "ok": False,
              "outputs": {}, "units": {}, "identity_verified": False, "transcript": []}
    transport = None
    transport_open_attempted = False
    try:
        # Snapshot declarative inputs before validation to avoid model mutation
        # by a caller or injected transport during an active transaction.
        model = json.loads(_json_model(model))
        validation = validate_model(model)
        _require(validation["ok"], "model", "; ".join(d["where"] + ": " + d["what"] for d in validation["defects"]))
        _require(type(operation) is str and operation in model["operations"], "operation", "unknown operation", Fault.OPERATOR)
        _require(type(allow_effects) in (tuple, list, set, frozenset) and all(v in ("read", "write", "actuate") for v in allow_effects), "allow_effects", "invalid effect grants", Fault.OPERATOR)
        selected = model["operations"][operation]
        _require(selected["effect"] == "read" or selected["effect"] in allow_effects, "effect", "operation requires explicit effect grant", Fault.OPERATOR)
        parameters = {} if parameters is None else parameters
        _arguments(selected["parameters"], parameters)
        parameters = dict(parameters)
        _require(binding is None or type(binding) is dict, "binding", "binding must be object", Fault.OPERATOR)
        _require(delay_fn is None or callable(delay_fn), "delay_fn", "delay_fn must be callable or None", Fault.OPERATOR)
        delay = time.sleep if delay_fn is None else delay_fn
        if transport_factory is None:
            from .transports import open_transport
            transport_factory = open_transport
        transport_open_attempted = True
        transport = transport_factory(model["channel"], {} if binding is None else dict(binding))
        # Bytes an adapter discarded on open (stale replies to an abandoned transaction) are reported,
        # never silently absorbed; absent means nothing was discarded or the adapter does not drain.
        drained = getattr(transport, "drained_bytes", 0)
        if type(drained) is int and drained > 0:
            result["transport_notes"] = {"drained_bytes": drained}

        def run(name, arguments):
            op = model["operations"][name]
            captures = {}
            decoder_captures = {}
            for index, step in enumerate(op["steps"]):
                entry = {"operation": name, "step": index, "op": step["op"], "tx_hex": "", "rx_hex": "", "status": "attempted"}
                result["transcript"].append(entry)
                if step["op"] == "delay":
                    entry["ms"] = step["ms"]
                    delay(step["ms"] / 1000)
                    entry["status"] = "completed"
                    continue
                tx = _encode(step["tx"], arguments)
                entry["tx_hex"] = tx.hex()
                for rule in step.get("match", ()):
                    _require(len(tx) >= rule["tx_offset"] + rule["size"], "match",
                             "encoded request is shorter than its match range")
                if step["op"] == "usb_control":
                    setup = {key: arguments[value["arg"]] if type(value) is dict else value for key, value in step["setup"].items()}
                    entry["setup"] = setup
                    data = transport.control(setup, tx, step["rx"], step["timeout_ms"])
                else:
                    data = transport.exchange(tx, step["rx"], step["timeout_ms"])
                if type(data) is bytes:
                    entry["rx_hex"] = data.hex()
                _response(data, step["rx"])
                entry["status"] = "completed"
                for rule in step.get("match", ()):
                    start, stop = rule["tx_offset"], rule["tx_offset"] + rule["size"]
                    other = rule["rx_offset"]
                    _require(data[other:other + rule["size"]] == tx[start:stop],
                             "match", "request/reply match failed")
                if "expect" in step:
                    _require(_matches(data, step["expect"]), "expect", "response expectation mismatch")
                if "capture" in step:
                    captures[step["capture"]] = data
                    if step["rx"]["mode"] == "frame" and step["rx"]["length"].get("padding") is not None:
                        spec = checks.frame_spec(step["rx"])
                        data = data[:checks.frame_data_end(data, spec)]
                    decoder_captures[step["capture"]] = data
            return {name: _decode(decoder, captures if decoder["kind"] == "hex" else decoder_captures)
                    for name, decoder in op["outputs"].items()}

        identity = model["identity"]["operation"]
        identity_outputs = run(identity, {})
        result["identity_verified"] = True
        result["outputs"] = identity_outputs if operation == identity else run(operation, parameters)
        result["units"] = {name: decoder["unit"] for name, decoder in selected["outputs"].items() if "unit" in decoder}
        result["ok"] = True
    except Exception as exc:
        result["error"] = {"code": getattr(exc, "code", "validation_error" if transport is None else "execution_error"), "message": str(exc)}
        declared = getattr(exc, 'fault', None)
        if declared is not None:
            result['error']['fault'] = declared
        elif hasattr(exc, 'code'):
            result['error']['fault'] = classify_failure(result)
        else:
            result['error']['fault'] = Fault.MODEL.value if isinstance(exc, UnicodeError) else Fault.HOST.value
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                if result["ok"]:
                    result["ok"] = False
                    result["error"] = {"code": "close_error", "message": str(exc), "fault": Fault.HOST.value}
    if not result["ok"]:
        result["transport_open_attempted"] = transport_open_attempted
        result["outputs"] = {}
        result["units"] = {}
    return result

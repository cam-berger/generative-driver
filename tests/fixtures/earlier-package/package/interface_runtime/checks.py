"""Declarative integrity checks for binary frames: parameterised CRCs and simple sums.

A check is data, never code. The CRC form is the conventional parameter set (width, poly, init, refin,
refout, xorout) that catalogues of CRC algorithms use; a handful of catalogue names are accepted as
shorthand for their parameter sets, each pinned below by the catalogue's own check value over the
ASCII bytes "123456789". Sums and XORs are the other forms firmware commonly uses. Nothing here knows
any device; a model names the algorithm its firmware evidence supports.
"""

CHECK_INPUT = b"123456789"

# Catalogue parameter sets with their published check values over CHECK_INPUT.
PRESETS = {
    "crc8":             {"width": 8,  "poly": 0x07,       "init": 0x00,       "refin": False, "refout": False, "xorout": 0x00,       "check": 0xF4},
    "crc8_maxim":       {"width": 8,  "poly": 0x31,       "init": 0x00,       "refin": True,  "refout": True,  "xorout": 0x00,       "check": 0xA1},
    "crc16_ccitt_false":{"width": 16, "poly": 0x1021,     "init": 0xFFFF,     "refin": False, "refout": False, "xorout": 0x0000,     "check": 0x29B1},
    "crc16_xmodem":     {"width": 16, "poly": 0x1021,     "init": 0x0000,     "refin": False, "refout": False, "xorout": 0x0000,     "check": 0x31C3},
    "crc16_kermit":     {"width": 16, "poly": 0x1021,     "init": 0x0000,     "refin": True,  "refout": True,  "xorout": 0x0000,     "check": 0x2189},
    "crc16_modbus":     {"width": 16, "poly": 0x8005,     "init": 0xFFFF,     "refin": True,  "refout": True,  "xorout": 0x0000,     "check": 0x4B37},
    "crc16_arc":        {"width": 16, "poly": 0x8005,     "init": 0x0000,     "refin": True,  "refout": True,  "xorout": 0x0000,     "check": 0xBB3D},
    "crc32":            {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0xFFFFFFFF, "check": 0xCBF43926},
    "crc32_mpeg2":      {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF, "refin": False, "refout": False, "xorout": 0x00000000, "check": 0x0376E6E7},
}
SIMPLE = {"sum8": 8, "sum16": 16, "xor8": 8, "twos_complement_sum8": 8}
CRC_FIELDS = ("width", "poly", "init", "refin", "refout", "xorout")


class CheckError(ValueError):
    pass


def _reflect(value, width):
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def resolve(algorithm):
    """Validate an algorithm declaration and return (kind, parameters, width_in_bytes)."""
    if isinstance(algorithm, str):
        if algorithm in PRESETS:
            params = {k: PRESETS[algorithm][k] for k in CRC_FIELDS}
            return "crc", params, params["width"] // 8
        if algorithm in SIMPLE:
            return algorithm, {}, SIMPLE[algorithm] // 8
        raise CheckError(f"unknown check algorithm {algorithm!r}")
    if not isinstance(algorithm, dict) or set(algorithm) != set(CRC_FIELDS):
        raise CheckError("an explicit CRC needs exactly width, poly, init, refin, refout and xorout")
    width = algorithm["width"]
    if type(width) is not int or width not in (8, 16, 32):
        raise CheckError("CRC width must be 8, 16 or 32")
    for key in ("poly", "init", "xorout"):
        value = algorithm[key]
        if type(value) is not int or not 0 <= value < (1 << width):
            raise CheckError(f"CRC {key} must be an integer within the width")
    if algorithm["poly"] == 0:
        raise CheckError("CRC poly must be nonzero")
    for key in ("refin", "refout"):
        if type(algorithm[key]) is not bool:
            raise CheckError(f"CRC {key} must be boolean")
    return "crc", dict(algorithm), width // 8


def compute(algorithm, data):
    """Checksum of `data` as an unsigned integer under the declared algorithm."""
    kind, params, size = resolve(algorithm)
    data = bytes(data)
    if kind == "sum8":
        return sum(data) & 0xFF
    if kind == "twos_complement_sum8":
        return (-sum(data)) & 0xFF
    if kind == "sum16":
        return sum(data) & 0xFFFF
    if kind == "xor8":
        out = 0
        for b in data:
            out ^= b
        return out
    width, poly = params["width"], params["poly"]
    top, mask = 1 << (width - 1), (1 << width) - 1
    crc = params["init"]
    for byte in data:
        if params["refin"]:
            byte = _reflect(byte, 8)
        crc ^= byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & mask if crc & top else (crc << 1) & mask
    if params["refout"]:
        crc = _reflect(crc, width)
    return (crc ^ params["xorout"]) & mask


def encode(algorithm, data, byteorder):
    if byteorder not in ("big", "little"):
        raise CheckError("check byteorder must be big or little")
    _, _, size = resolve(algorithm)
    return compute(algorithm, data).to_bytes(size, byteorder)


def self_test():
    """Every preset reproduces its catalogue check value; raises CheckError otherwise."""
    for name, spec in PRESETS.items():
        if compute(name, CHECK_INPUT) != spec["check"]:
            raise CheckError(f"preset {name} does not reproduce its catalogue check value")
    return True


# ---------------------------------------------------------------- length-prefixed frames

FRAME_FIELDS = ("mode", "sync_hex", "length", "max_bytes", "max_reads", "check", "end_hex")
LENGTH_FIELDS = ("offset", "size", "byteorder", "add")
PADDING_FIELDS = ("shift", "mask", "max_bytes")
FRAME_CHECK_FIELDS = ("algorithm", "from", "byteorder")


class FrameError(CheckError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _hexbytes(value, name, nonempty=True, limit=None):
    if not isinstance(value, str) or len(value) % 2 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise CheckError(f"{name} must be hexadecimal bytes")
    raw = bytes.fromhex(value)
    if nonempty and not raw:
        raise CheckError(f"{name} must be nonempty")
    if limit is not None and len(raw) > limit:
        raise CheckError(f"{name} is longer than {limit} bytes")
    return raw


def frame_spec(rx):
    """Validate a `frame` receive declaration; return a normalised spec. Raises CheckError."""
    if not isinstance(rx, dict) or rx.get("mode") != "frame":
        raise CheckError("not a frame receive")
    if set(rx) - set(FRAME_FIELDS) or not {"mode", "sync_hex", "length", "max_bytes"} <= set(rx):
        raise CheckError("frame receive needs mode, sync_hex, length and max_bytes, and allows check and end_hex")
    sync = _hexbytes(rx["sync_hex"], "sync_hex", limit=8)
    length = rx["length"]
    if not isinstance(length, dict) or not set(LENGTH_FIELDS) <= set(length) or set(length) - set(LENGTH_FIELDS) - {"body_mask", "padding"}:
        raise CheckError("length needs offset, size, byteorder and add, with optional body_mask and padding")
    offset, size, add = length["offset"], length["size"], length["add"]
    if type(offset) is not int or not len(sync) <= offset <= 64:
        raise CheckError("length offset must follow the sync bytes and be at most 64")
    if type(size) is not int or size not in (1, 2, 4):
        raise CheckError("length size must be 1, 2 or 4")
    if length["byteorder"] not in ("big", "little"):
        raise CheckError("length byteorder must be big or little")
    if type(add) is not int or not -65536 <= add <= 65536:
        raise CheckError("length add must be an integer in -65536..65536")
    field_max = (1 << (8 * size)) - 1
    body_mask = length.get("body_mask", field_max)
    if type(body_mask) is not int or not 0 < body_mask <= field_max or body_mask & (body_mask + 1):
        raise CheckError("body_mask must be contiguous low bits within the length field")
    padding = length.get("padding")
    if padding is not None:
        if not isinstance(padding, dict) or set(padding) != set(PADDING_FIELDS):
            raise CheckError("padding needs exactly shift, mask and max_bytes")
        shift, mask, pad_max = (padding[k] for k in PADDING_FIELDS)
        if (type(shift) is not int or type(mask) is not int or type(pad_max) is not int
                or not 0 <= shift < 8 * size or not 0 < mask <= field_max
                or mask & (mask + 1) or mask << shift > field_max
                or body_mask & (mask << shift) or not 0 <= pad_max <= mask):
            raise CheckError("padding mask, shift or bound is invalid or overlaps body_mask")
    if "max_reads" in rx and (type(rx["max_reads"]) is not int or not 1 <= rx["max_reads"] <= 4096):
        raise CheckError("max_reads must be an integer in 1..4096")
    maximum = rx["max_bytes"]
    if type(maximum) is not int or not 2 <= maximum <= 65536:
        raise CheckError("max_bytes must be an integer in 2..65536")
    end = _hexbytes(rx["end_hex"], "end_hex", limit=8) if "end_hex" in rx else b""
    check = None
    if "check" in rx:
        check = rx["check"]
        if not isinstance(check, dict) or set(check) != set(FRAME_CHECK_FIELDS):
            raise CheckError("frame check needs exactly algorithm, from and byteorder")
        _, _, check_size = resolve(check["algorithm"])
        if check["byteorder"] not in ("big", "little"):
            raise CheckError("check byteorder must be big or little")
        if type(check["from"]) is not int or not 0 <= check["from"] <= 64:
            raise CheckError("check from must be an integer offset 0..64")
    header = offset + size
    minimum = max(header, (check["from"] if check else 0)) + (check_size if check else 0) + len(end)
    if minimum > maximum:
        raise CheckError("the smallest frame this declaration admits exceeds max_bytes")
    return {"sync": sync, "offset": offset, "size": size, "byteorder": length["byteorder"], "add": add,
            "body_mask": body_mask, "padding": padding,
            "max": maximum, "end": end, "check": check, "check_size": check_size if check else 0,
            "header": header, "minimum": minimum}


def frame_total(buffer, spec):
    """Total frame length once the header is buffered, else None. Raises FrameError on bad bytes."""
    sync = spec["sync"]
    have = bytes(buffer[:len(sync)])
    if have != sync[:len(have)]:
        raise FrameError("framing", "reply does not begin with the declared sync bytes")
    if len(buffer) < spec["header"]:
        return None
    field = int.from_bytes(bytes(buffer[spec["offset"]:spec["header"]]), spec["byteorder"])
    padding = spec["padding"]
    pad_bits = (padding["mask"] << padding["shift"]) if padding else 0
    if field & ~(spec["body_mask"] | pad_bits):
        raise FrameError("framing", "length field contains undeclared bits")
    pad_count = ((field >> padding["shift"]) & padding["mask"]) if padding else 0
    if padding and pad_count > padding["max_bytes"]:
        raise FrameError("limit", "declared padding exceeds its bound")
    total = (field & spec["body_mask"]) + pad_count + spec["add"]
    if total > spec["max"]:
        raise FrameError("limit", f"declared frame length {total} exceeds max_bytes {spec['max']}")
    if total < spec["minimum"]:
        raise FrameError("framing", f"declared frame length {total} is shorter than the frame's fixed parts")
    if total - pad_count < spec["header"] + spec["check_size"] + len(spec["end"]):
        raise FrameError("framing", "declared padding overlaps the frame's fixed parts")
    return total


def verify_frame(frame, spec):
    """Check a complete frame: sync, length, trailer and integrity. Raises FrameError."""
    frame = bytes(frame)
    total = frame_total(frame, spec)
    if total is None or total != len(frame):
        raise FrameError("framing", "frame length disagrees with its length field")
    end = spec["end"]
    if end and not frame.endswith(end):
        raise FrameError("framing", "frame does not end with the declared end bytes")
    check = spec["check"]
    if check:
        stop = len(frame) - len(end) - spec["check_size"]
        if stop < check["from"]:
            raise FrameError("framing", "frame too short for its check")
        expected = encode(check["algorithm"], frame[check["from"]:stop], check["byteorder"])
        if frame[stop:stop + spec["check_size"]] != expected:
            raise FrameError("integrity", "frame check value does not match its contents")
    return frame


def frame_data_end(frame, spec):
    """End of the header/body prefix of an already verified frame.

    Padding precedes the optional integrity check and end bytes. A semantic
    decoder must not use any of those trailing bytes to fill a missing value.
    Raw frame captures retain them for inspection.
    """
    field = int.from_bytes(frame[spec["offset"]:spec["header"]], spec["byteorder"])
    return (field & spec["body_mask"]) + spec["add"] - spec["check_size"] - len(spec["end"])


def build_frame(spec, body, padding=b""):
    """Wrap `body` (the bytes after the length field, before the check) as a valid frame: used by the
    qualifier to construct synthetic well-formed replies, never by the executor."""
    header_gap = spec["offset"] - len(spec["sync"])
    prefix = spec["sync"] + bytes(header_gap)
    padding = bytes(padding)
    if padding and (not spec["padding"] or len(padding) > spec["padding"]["max_bytes"]):
        raise CheckError("padding exceeds the frame declaration")
    total = spec["header"] + len(body) + len(padding) + spec["check_size"] + len(spec["end"])
    body_count = total - spec["add"] - len(padding)
    if body_count & ~spec["body_mask"]:
        raise CheckError("body length exceeds the frame field")
    field = body_count | ((len(padding) << spec["padding"]["shift"]) if spec["padding"] else 0)
    if field < 0 or field >= 1 << (8 * spec["size"]):
        raise CheckError("body does not fit the length field")
    frame = bytearray(prefix + field.to_bytes(spec["size"], spec["byteorder"]) + body + padding)
    if spec["check"]:
        frame += encode(spec["check"]["algorithm"], frame[spec["check"]["from"]:], spec["check"]["byteorder"])
    frame += spec["end"]
    return bytes(frame)

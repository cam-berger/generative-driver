"""Device: a packaging of the bundled interface model (model.json + convert.py).

Everything the driver puts on the bus comes from model.json: the address candidates, the init, state,
identify and measure operations with their registers and bytes in the model's order, the extra named
operations, and the raw read/write primitives gated by the model's safety section. Conversion is the bundled
convert.py called through the model's `conversion.entry`. Nothing here knows the device beyond what the
model says; the bus execution mirrors the reference runner (tools/model_exec.py) operation for operation.
"""
import importlib.util
import re
import json
import os
import time
from dataclasses import dataclass

PACKAGE_NAME = "@@NAME@@"
PACKAGE_VERSION = "@@VERSION@@"
DEFAULT_URL = None
HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "model.json")
RESERVED_OP_NAMES = ("init", "identify", "measure")
# Address discovery probes with the write address (START, address+W, ACK?, STOP), as i2cdetect's quick write.
# The reference runner probes with the read address; on this bench that left the slave driving SDA after the
# STOP whenever the byte under its register pointer began with a 0 bit, and the next session's first write
# then failed with NACK in 3 of 10 trials (0 of 10 with the write probe). See ../NOTES.md.
PROBE_WITH_WRITE = True


def hx(x):
    """'0x76' or '118' -> 118."""
    s = str(x).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def reg_hex(r):
    return "0x%02x" % int(r)


# ------------------------------------------------------------------ errors (exit codes as model_exec's)


class DriverError(Exception):
    exit_code = 1


class RefusedError(DriverError):
    """A read or write the model's safety section does not cover. Raised before anything touches the bus."""
    exit_code = 1

    def __init__(self, reason, detail):
        super().__init__("%s: %s" % (reason, detail))
        self.reason, self.detail = reason, detail


class IdentityError(DriverError):
    exit_code = 2


class NackError(DriverError):
    exit_code = 3


class BusError(DriverError):
    exit_code = 4


# ------------------------------------------------------------------ model loading


def load_model(path=MODEL_PATH):
    """Returns (model dict, conversion module, conversion entry point)."""
    with open(path, encoding="utf-8") as f:
        model = json.load(f)
    if model.get("schema") != "interface-model/1":
        raise DriverError("unknown model schema %r" % model.get("schema"))
    conv = model.get("conversion") or {}
    mod_path = os.path.join(os.path.dirname(os.path.abspath(path)), conv.get("module", "convert.py"))
    spec = importlib.util.spec_from_file_location("driver_model_convert", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return model, mod, getattr(mod, conv.get("entry", "convert"))


# ------------------------------------------------------------------ safety


def _parse_ranges(items):
    regs = set()
    for it in items:
        a, _, b = str(it).partition("-")
        lo = hx(a)
        hi = hx(b) if b else lo
        regs.update(range(lo, hi + 1))
    return regs


class Safety:
    """The model's safety section turned into decisions, entirely from the model.

    safe_reads and known_safe_writes are structured in schema 1 and are parsed as written. The `never`
    entries are prose, so each one is read for the registers it names: a range like `0x88-0xA1`, a single
    register, an exception like `any value other than 0xB6`, and a qualifier like `bit 0 set`. A clause
    that names no register (for example 'any reserved register') cannot become a rule and is kept as prose;
    a write to a register the model never mentions is refused as no_evidence and quotes it. Nothing here is
    specific to a device: a model with no `never` entries simply gets no rules.
    """

    RANGE = re.compile(r"0x([0-9A-Fa-f]{1,2})\s*(?:-|--|to|\u2013)\s*0x([0-9A-Fa-f]{1,2})")
    REG = re.compile(r"0x([0-9A-Fa-f]{1,2})")
    EXCEPT = re.compile(r"(?:other than|except|apart from|but)\s+0x([0-9A-Fa-f]{1,2})", re.I)
    BIT = re.compile(r"bit\s+(\d+)\s+(set|clear)", re.I)

    @classmethod
    def _rule(cls, text):
        """One prose clause -> (predicate, why) or None when it names no register."""
        ranges = [(int(a, 16), int(b, 16)) for a, b in cls.RANGE.findall(text)]
        covered = {t for pair in cls.RANGE.findall(text) for t in pair}
        singles = [int(h, 16) for h in cls.REG.findall(text) if h not in covered]
        exc = cls.EXCEPT.search(text)
        exc_val = int(exc.group(1), 16) if exc else None
        if exc_val is not None and exc_val in singles:
            singles.remove(exc_val)
        bit = cls.BIT.search(text)
        why = text.strip()
        if ranges:
            def pred(r, b, _rs=tuple(ranges)):
                return any(lo <= r <= hi for lo, hi in _rs)
            return pred, why
        if singles:
            regs = set(singles)
            if bit:
                n, sense = int(bit.group(1)), bit.group(2).lower()
                def pred(r, b, _rg=regs, _n=n, _s=sense):
                    return r in _rg and (bool(b & (1 << _n)) if _s == "set" else not b & (1 << _n))
                return pred, why
            if exc_val is not None:
                def pred(r, b, _rg=regs, _v=exc_val):
                    return r in _rg and b != _v
                return pred, why
            def pred(r, b, _rg=regs):
                return r in _rg
            return pred, why
        return None

    def __init__(self, model):
        s = model.get("safety") or {}
        self.safe_reads = _parse_ranges(s.get("safe_reads", []))
        self.known_safe_writes = set()
        for e in s.get("known_safe_writes", []):
            r, _, v = str(e).partition("=")
            self.known_safe_writes.add((hx(r), hx(v)))
        self.never_text = [str(x) for x in s.get("never", [])]
        self.rules, self.prose_only = [], []
        for text in self.never_text:
            made = self._rule(text)
            (self.rules.append(made) if made else self.prose_only.append(text))
        self.reserved_clause = next((t for t in self.prose_only), None)
        # Every register the model mentions anywhere: evidence exists that the register is real.
        self.mentioned = set(self.safe_reads)
        for steps in (model.get("operations") or {}).values():
            for st in steps:
                if "reg" in st:
                    n = int(st.get("len", len(st.get("bytes", [1]))))
                    self.mentioned.update(range(hx(st["reg"]), hx(st["reg"]) + max(n, 1)))
        for st in model.get("state", []):
            self.mentioned.update(range(hx(st["reg"]), hx(st["reg"]) + int(st.get("len", 1))))

    def read_verdict(self, reg, n):
        outside = sorted(set(range(reg, reg + n)) - self.safe_reads)
        if not outside:
            return "safe", "inside model safety.safe_reads"
        return "no_evidence", "outside model safety.safe_reads: " + ",".join(reg_hex(r) for r in outside)

    def write_verdict(self, reg, byte):
        for pred, why in self.rules:
            if pred(reg, byte):
                return "never", why
        if (reg, byte) in self.known_safe_writes:
            return "known_safe", "listed in model safety.known_safe_writes"
        detail = "no model evidence for %s=0x%02x" % (reg_hex(reg), byte)
        if reg not in self.mentioned and self.reserved_clause:
            detail += "; the model never mentions this register and its safety.never says '%s'" % self.reserved_clause
        return "no_evidence", detail


# ------------------------------------------------------------------ bus backend (as tools/model_exec.py)


class FtdiI2cBackend:
    """FT232H as I2C master through pyftdi. Same calls as the reference runner."""

    def __init__(self, url=DEFAULT_URL, speed_hz=100000):
        if not isinstance(url, str) or not url.startswith("ftdi://"):
            raise DriverError("Select an explicit ftdi:// adapter URL")
        from pyftdi.i2c import I2cController
        self.i2c = I2cController()
        self.i2c.configure(url, frequency=speed_hz)
        self.ports = {}

    def port(self, addr):
        if addr not in self.ports:
            self.ports[addr] = self.i2c.get_port(addr)
        return self.ports[addr]

    def probe(self, addr):
        return self.i2c.poll(addr, write=PROBE_WITH_WRITE)

    def reg_read(self, addr, reg, n):
        return bytes(self.port(addr).read_from(reg, n))

    def reg_write(self, addr, reg, data):
        self.port(addr).write_to(reg, bytes(data))

    def raw_write(self, addr, data):
        self.port(addr).write(bytes(data))

    def raw_read(self, addr, n):
        return bytes(self.port(addr).read(n))

    def close(self):
        self.i2c.terminate()


# ------------------------------------------------------------------ the device


@dataclass
class Sample:
    values: dict      # output name -> float, exactly as the model's conversion returns them
    units: dict       # output name -> unit string from the model
    raw: bytes        # the bytes of the measure read tagged 'as': 'raw' (empty if the model has none)
    skipped: dict     # output name -> True if the model's conversion module flags the channel as skipped
    valid: bool       # identity matched and no channel skipped
    t_wall: float


class Device:
    """One session with the device, in the model's order: init, state reads, identify, then measures.

    Device(url=None, speed_hz=None, init=True, strict=True, backend=None, model_path=MODEL_PATH)
      url       explicitly selected pyftdi URL of the adapter
      init      run operations.init on open (soft reset and configuration as the model lists them)
      strict    raise IdentityError on open if the identity byte is not one the model expects
      backend   an object with probe/reg_read/reg_write/raw_write/raw_read/close (for tests without hardware)
    """

    def __init__(self, url=None, speed_hz=None, init=True, strict=True, backend=None, model_path=MODEL_PATH,
                 allow_write=False):
        self.model, self.convert_module, self.convert = load_model(model_path)
        self.safety = Safety(self.model)
        ch = self.model["channel"]
        if ch.get("type") != "i2c":
            raise DriverError("channel type %r not supported" % ch.get("type"))
        if backend is None and (not isinstance(url, str) or not url.startswith("ftdi://")):
            raise DriverError("Select an explicit ftdi:// adapter URL before opening a device")
        self.url = url
        self.allow_write = allow_write is True
        self.speed_hz = int(speed_hz or ch.get("speed_hz", 100000))
        self.run_init, self.strict = bool(init), bool(strict)
        self._backend, self._own_backend = backend, backend is None
        self.address = None
        self.identity = None
        self.identity_ok = None
        self.init_done = False
        self.inputs = {}     # start register (lower-case hex) -> bytes of the latest read from it
        self.named = {}      # 'as' tag -> bytes
        self.state = {}      # state entry name -> bytes
        self._first_measure_after = 0.0
        self._last_measure_at = None

    # -- session ------------------------------------------------------------------------------------

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def open(self):
        ops = self.model.get("operations") or {}
        startup = (ops.get("init", []) if self.run_init else []) + self.model.get("state", []) + ops.get("identify", [])
        self._require_steps(startup)
        if self._backend is None:
            try:
                self._backend = FtdiI2cBackend(self.url, self.speed_hz)
            except Exception as e:  # no FTDI, USB permission, wrong URL
                raise BusError("%s: %s" % (type(e).__name__, e)) from e
        ops = self.model.get("operations") or {}
        self.address = self._choose_address()
        if self.run_init:
            self._run_ops(ops.get("init", []))
            self.init_done = True
        for st in self.model.get("state", []):
            self._run_ops([st])
            self.state[st.get("name", reg_hex(hx(st["reg"])))] = self.inputs["0x%02x" % hx(st["reg"])]
        self._run_ops(ops.get("identify", []))
        ident = self.model.get("identity") or {}
        idb = self.named.get(ident.get("from", "id"))
        self.identity = idb[0] if idb else None
        expect = {hx(e) for e in ident.get("expect", [])}
        self.identity_ok = idb is not None and (not expect or self.identity in expect)
        if self.run_init:
            self._first_measure_after = time.time() + self.model.get("measure_wait_ms", 0) / 1000.0
        if self.strict and not self.identity_ok:
            raise IdentityError("identity %s not in expected %s" % (
                reg_hex(self.identity) if self.identity is not None else "none",
                ",".join(reg_hex(e) for e in sorted(expect))))
        return self

    def close(self):
        if self._backend is not None and self._own_backend:
            try:
                self._backend.close()
            except Exception:
                pass
        self._backend = None

    # -- primitives ---------------------------------------------------------------------------------

    @property
    def ops(self):
        """Names of the model's extra operations (everything under operations except init/identify/measure)."""
        return [n for n in (self.model.get("operations") or {}) if n not in RESERVED_OP_NAMES]

    @property
    def identity_expected(self):
        return [hx(e) for e in (self.model.get("identity") or {}).get("expect", [])]

    def info(self):
        conv = self.model.get("conversion") or {}
        return {
            "package": PACKAGE_NAME, "version": PACKAGE_VERSION, "schema": self.model.get("schema"),
            "address": reg_hex(self.address) if self.address is not None else None,
            "identity": reg_hex(self.identity) if self.identity is not None else None,
            "identity_expected": [reg_hex(e) for e in self.identity_expected],
            "identity_ok": bool(self.identity_ok), "init_done": self.init_done,
            "outputs": dict(conv.get("outputs", {})), "ops": self.ops,
            "device_name": (self.model.get("device") or {}).get("name"),
            "device_confidence": (self.model.get("device") or {}).get("confidence"),
        }

    def read(self, reg, n=1, force=False):
        """Raw register read. Refused outside the model's safe_reads unless force."""
        reg, n = int(reg), int(n)
        if not (0 <= reg <= 0xFF and 1 <= n and reg + n <= 0x100):
            raise DriverError("read %s n=%d out of the 8-bit register space" % (reg_hex(reg), n))
        verdict, detail = self.safety.read_verdict(reg, n)
        if verdict != "safe" and not force:
            raise RefusedError(verdict, detail)
        data = self._bus(self._backend.reg_read, self.address, reg, n)
        self.inputs["0x%02x" % reg] = data
        return data

    def write(self, reg, byte, force=False):
        """Single-byte register write. Returns the evidence class: 'known_safe' or 'forced'.
        'never' writes are refused even with force; 'no_evidence' writes need force."""
        if not self.allow_write:
            raise RefusedError("grant", "Explicit write grant required")
        reg, byte = int(reg), int(byte)
        if not (0 <= reg <= 0xFF and 0 <= byte <= 0xFF):
            raise DriverError("write %s=%s out of range" % (reg, byte))
        verdict, detail = self.safety.write_verdict(reg, byte)
        if verdict == "never" or (verdict == "no_evidence" and not force):
            raise RefusedError(verdict, detail)
        self._bus(self._backend.reg_write, self.address, reg, bytes([byte]))
        return verdict if verdict == "known_safe" else "forced"

    def measure(self):
        """One measurement: the model's measure operations, converted by the model's conversion.
        Paced as the model says: measure_wait_ms after init for the first sample, measure_period_ms between
        consecutive samples of this session."""
        period = self.model.get("measure_period_ms", 0) / 1000.0
        due = self._first_measure_after if self._last_measure_at is None else self._last_measure_at + period
        now = time.time()
        if due > now:
            time.sleep(due - now)
        self._run_ops((self.model.get("operations") or {}).get("measure", []))
        self._last_measure_at = time.time()
        inputs = {k: bytes(v) for k, v in self.inputs.items()}
        vals = {k: float(v) for k, v in self.convert(inputs).items()}
        units = dict((self.model.get("conversion") or {}).get("outputs", {}))
        skipped = {}
        if hasattr(self.convert_module, "skipped"):
            skipped = {k: bool(v) for k, v in self.convert_module.skipped(inputs).items()}
        valid = bool(self.identity_ok) and not any(skipped.values())
        return Sample(vals, units, bytes(self.named.get("raw", b"")), skipped, valid, self._last_measure_at)

    def op(self, name):
        """Run one of the model's extra named operations. Returns {'as' tag: bytes} for its reads."""
        ops = self.model.get("operations") or {}
        if name in RESERVED_OP_NAMES or name not in ops:
            raise DriverError("unknown operation %r; the model offers %s" % (name, ",".join(self.ops) or "none"))
        before = dict(self.named)
        self._run_ops(ops[name])
        return {k: v for k, v in self.named.items() if before.get(k) is not v}

    # -- internals ----------------------------------------------------------------------------------

    def _bus(self, fn, *args):
        try:
            return fn(*args)
        except DriverError:
            raise
        except Exception as e:  # pyftdi: I2cNackError, I2cIOError, FtdiError, usb.core.USBError
            name = type(e).__name__
            if "nack" in name.lower() or "NACK" in str(e).upper():
                raise NackError("%s at %s: %s" % (name, reg_hex(self.address or 0), e)) from e
            raise BusError("%s: %s" % (name, e)) from e

    def _choose_address(self):
        ch = self.model["channel"]
        cands = [hx(ch["address_7bit"])] + [hx(a) for a in ch.get("address_alternatives", [])]
        for a in cands:
            if self._bus(self._backend.probe, a):
                return a
        raise NackError("no acknowledge at %s" % ",".join(reg_hex(a) for a in cands))

    def _require_steps(self, steps):
        if not self.allow_write and any(step.get("op") in ("reg_write", "raw_write") for step in steps):
            raise RefusedError("grant", "Explicit write grant required before executing model writes")

    def _run_ops(self, steps):
        """Same semantics as run_ops in the reference runner."""
        self._require_steps(steps)
        addr = self.address
        for s in steps:
            op = s["op"]
            if op == "reg_write":
                self._bus(self._backend.reg_write, addr, hx(s["reg"]), bytes(hx(b) for b in s["bytes"]))
            elif op == "reg_read":
                data = self._bus(self._backend.reg_read, addr, hx(s["reg"]), int(s["len"]))
                self.inputs["0x%02x" % hx(s["reg"])] = data
                if s.get("as"):
                    self.named[s["as"]] = data
            elif op == "raw_write":
                self._bus(self._backend.raw_write, addr, bytes(hx(b) for b in s["bytes"]))
            elif op == "raw_read":
                data = self._bus(self._backend.raw_read, addr, int(s["len"]))
                self.inputs["raw"] = data
                if s.get("as"):
                    self.named[s["as"]] = data
            elif op == "delay_ms":
                time.sleep(int(s["ms"]) / 1000.0)
            elif op == "probe":
                self._bus(self._backend.probe, addr)
            else:
                raise DriverError("unknown op %r in model" % op)

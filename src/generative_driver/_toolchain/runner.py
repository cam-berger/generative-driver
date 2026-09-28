"""Executing an interface model, on the live bus or on a register file.

Built on tools/model_exec.py, which is frozen, with one behaviour changed: address discovery polls in the
WRITE direction. Both emit agents of run real3 found independently that a read-direction poll leaves a
register device mid-transaction, so the next transfer can fail or return the wrong bytes. The finding is
applied here rather than by editing a frozen file.
"""
import importlib.util, json, os, random, sys

from . import core

sys.path.insert(0, os.path.join(core.BENCH, "tools"))
import model_exec as me  # noqa: E402

hx = me.hx
RegisterFileBackend = me.RegisterFileBackend
run_model = me.run_model
run_ops = me.run_ops


def load_model(model_dir):
    return me.load_model(model_dir)


class FtdiI2c(me.FtdiI2cBackend):
    def probe(self, addr):
        return self.i2c.poll(addr, write=True)   # write-direction: leaves no transaction open


def open_bus(model_or_speed, url=None):
    if isinstance(model_or_speed, dict) and model_or_speed.get("channel", {}).get("type") != "i2c":
        raise ValueError("schema-1 live runner supports I2C only; schema-1 SPI requires capture observation "
                         "or an explicit schema-3 model with an SPI binding")
    speed = model_or_speed if isinstance(model_or_speed, int) else \
        model_or_speed["channel"].get("speed_hz", 100000)
    return FtdiI2c(url, speed)


def for_register_file(model):
    """A copy of the model that the frozen runner can execute against a register file.

    The runner discovers an I2C address before it does anything else. A channel with no addresses, such as
    spi, has none to discover, so scoring a model of one needs a synthetic address that the register-file
    backend acknowledges. This is a detail of executing off the bus, not a change to the model: nothing in
    the copy reaches the wire.
    """
    if (model.get("channel") or {}).get("type") == "i2c":
        return model, hx(model["channel"]["address_7bit"])
    m = json.loads(json.dumps(model))
    m.setdefault("channel", {})["address_7bit"] = "0x00"
    m["channel"].pop("address_alternatives", None)
    return m, 0x00


def read_ranges(model):
    """Every register the model reads, as a set. The package's footprint is compared against this."""
    regs = set()
    ops = model["operations"]
    steps = list(ops.get("init", [])) + [dict(s) for s in model.get("state", [])] + \
        list(ops.get("identify", [])) + list(ops.get("measure", []))
    for s in steps:
        if s.get("op") == "reg_read":
            regs |= set(range(hx(s["reg"]), hx(s["reg"]) + int(s["len"])))
    return regs


def write_set(model, convert):
    """Every (register, bytes) the model writes, found by running it once against a blank register file."""
    m, addr = for_register_file(model)
    be = RegisterFileBackend(bytes(256), {addr})
    try:
        run_model(m, convert, be, n=1, period_override=0)
    except Exception:
        pass
    return {(r, bytes(d)) for r, d in be.writes if r is not None}


def random_register_file(model, rng):
    """A register file with random bytes in every range the model reads. No ground truth, no device
    knowledge: the seeds come out of the model itself, so this works for any register device."""
    regs = bytearray(rng.getrandbits(8) for _ in range(256))
    ident = model.get("identity") or {}
    if ident.get("expect"):
        for s in model["operations"].get("identify", []):
            if s.get("op") == "reg_read":
                regs[hx(s["reg"])] = hx(ident["expect"][0])
    return bytes(regs)


def model_values(model, convert, regs):
    """What the model itself says this register file means. Returns None if its conversion cannot cope
    with these bytes, which is allowed: random bytes are not always in a device's domain."""
    m, addr = for_register_file(model)
    be = RegisterFileBackend(regs, {addr})
    try:
        out = run_model(m, convert, be, n=1, period_override=0)
    except Exception:
        return None, be
    vals = out["samples"][0]["values"]
    if any(v != v or v in (float("inf"), float("-inf")) for v in vals.values()):
        return None, be
    return vals, be

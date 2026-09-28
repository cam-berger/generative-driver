#!/usr/bin/env python3
"""Execute an interface model (docs/INTERFACE_MODEL.md, schema 1) against a backend.

Backends: the live I2C bus through the FT232H (pyftdi), or a synthetic register file (for scoring without
hardware). The same code runs the model in both cases, so a model that scores also runs, and vice versa.

    python model_exec.py <model_dir> --url SELECTED_ADAPTER --allow-write [--n 3] [--json]

Prints one JSON object per measurement: the converted outputs, the raw bytes, the identity byte, timestamps.
Exit 0 if the identity check passed, 2 if it did not, 3 if the device did not acknowledge (NACK), 4 on any
other bus or USB error, which is a hardware fault and makes the stage INCONCLUSIVE rather than failed."""
import importlib.util, json, os, sys, time

hx = lambda x: int(str(x), 16) if str(x).lower().startswith("0x") else int(x)


class RegisterFileBackend:
    """A device that is nothing but a 256-byte register file. Used by scorers."""
    def __init__(self, regs, ack_addresses):
        self.regs, self.ack = bytearray(regs), set(ack_addresses); self.writes = []
    def probe(self, addr): return addr in self.ack
    def reg_read(self, addr, reg, n):
        if addr not in self.ack: raise IOError("NACK")
        return bytes(self.regs[reg:reg + n])
    def reg_write(self, addr, reg, data):
        if addr not in self.ack: raise IOError("NACK")
        self.writes.append((reg, bytes(data)))
        for i, b in enumerate(data): self.regs[(reg + i) & 0xFF] = b
    def raw_write(self, addr, data): self.writes.append((None, bytes(data)))
    def raw_read(self, addr, n): return bytes(self.regs[:n])
    def close(self): pass


class FtdiI2cBackend:
    def __init__(self, url=None, speed_hz=100000):
        if not isinstance(url, str) or not url.startswith("ftdi://"):
            raise ValueError("Select an explicit ftdi:// adapter URL")
        from pyftdi.i2c import I2cController
        self.i2c = I2cController(); self.i2c.configure(url, frequency=speed_hz); self.ports = {}
    def port(self, addr):
        if addr not in self.ports: self.ports[addr] = self.i2c.get_port(addr)
        return self.ports[addr]
    def probe(self, addr): return self.i2c.poll(addr, write=False)
    def reg_read(self, addr, reg, n): return bytes(self.port(addr).read_from(reg, n))
    def reg_write(self, addr, reg, data): self.port(addr).write_to(reg, bytes(data))
    def raw_write(self, addr, data): self.port(addr).write(bytes(data))
    def raw_read(self, addr, n): return bytes(self.port(addr).read(n))
    def close(self): self.i2c.terminate()


def load_model(model_dir):
    with open(os.path.join(model_dir, "model.json"), encoding="utf-8") as stream:
        model = json.load(stream)
    assert model.get("schema") == "interface-model/1", "unknown schema"
    conv = model.get("conversion") or {}
    source_path = os.path.join(model_dir, conv.get("module", "convert.py"))
    spec = importlib.util.spec_from_file_location("model_convert", source_path)
    mod = importlib.util.module_from_spec(spec)
    # Supplied models are pinned inputs. Execute their source bytes without
    # reading or creating __pycache__ inside that evidence directory.
    with open(source_path, "rb") as stream:
        source = stream.read()
    exec(compile(source, source_path, "exec"), mod.__dict__)
    return model, getattr(mod, conv.get("entry", "convert"))


def run_ops(backend, addr, steps, inputs, named):
    """Execute a list of operations; byte strings read go into inputs (by start register) and named (by 'as')."""
    for s in steps:
        op = s["op"]
        if op == "reg_write": backend.reg_write(addr, hx(s["reg"]), bytes(hx(b) for b in s["bytes"]))
        elif op == "reg_read":
            data = backend.reg_read(addr, hx(s["reg"]), int(s["len"])); inputs[f"0x{hx(s['reg']):02x}"] = data
            if s.get("as"): named[s["as"]] = data
        elif op == "raw_write": backend.raw_write(addr, bytes(hx(b) for b in s["bytes"]))
        elif op == "raw_read":
            data = backend.raw_read(addr, int(s["len"])); inputs["raw"] = data
            if s.get("as"): named[s["as"]] = data
        elif op == "delay_ms": time.sleep(int(s["ms"]) / 1000.0)
        elif op == "probe": backend.probe(addr)
        else: raise ValueError(f"unknown op {op}")


def choose_address(backend, channel):
    cands = [hx(channel["address_7bit"])] + [hx(a) for a in channel.get("address_alternatives", [])]
    for a in cands:
        if backend.probe(a): return a
    return cands[0]


def run_model(model, convert, backend, n=3, period_override=None):
    ch = model["channel"]; addr = choose_address(backend, ch)
    inputs, named, out = {}, {}, {"address": f"0x{addr:02x}", "samples": []}
    ops = model["operations"]
    run_ops(backend, addr, ops.get("init", []), inputs, named)
    for st in model.get("state", []):
        run_ops(backend, addr, [dict(st, as_=None)], inputs, named)
    run_ops(backend, addr, ops.get("identify", []), inputs, named)
    ident = model.get("identity") or {}
    idb = named.get(ident.get("from", "id"))
    out["identity"] = f"0x{idb[0]:02x}" if idb else None
    out["identity_ok"] = bool(idb) and (not ident.get("expect") or idb[0] in {hx(e) for e in ident["expect"]})
    period = (period_override if period_override is not None else model.get("measure_period_ms", 500)) / 1000.0
    time.sleep(model.get("measure_wait_ms", 0) / 1000.0)
    for i in range(n):
        run_ops(backend, addr, ops.get("measure", []), inputs, named)
        vals = convert({k: bytes(v) for k, v in inputs.items()})
        out["samples"].append({"t_wall": time.time(), "values": {k: float(v) for k, v in vals.items()},
                               "raw": " ".join(f"{b:02x}" for b in named.get("raw", b""))})
        if i < n - 1: time.sleep(period)
    return out


def main():
    a = sys.argv[1:]
    if not a: print(__doc__); return 2
    model_dir = a[0]; n = 3; url = None; as_json = "--json" in a
    if "--n" in a: n = int(a[a.index("--n") + 1])
    if "--url" in a: url = a[a.index("--url") + 1]
    if not isinstance(url, str) or not url.startswith("ftdi://"):
        print(json.dumps({"error": "Explicit --url binding required"})); return 4
    if "--allow-write" not in a:
        print(json.dumps({"error": "Legacy model runner requires explicit --allow-write"})); return 4
    model, convert = load_model(model_dir)
    backend = FtdiI2cBackend(url, model["channel"].get("speed_hz", 100000))
    try:
        try:
            out = run_model(model, convert, backend, n=n)
        except IOError as e:
            nack = "NACK" in str(e).upper() or type(e).__name__ == "I2cNackError"
            print(json.dumps({"error": f"{'nack' if nack else 'bus'}: {e}"})); return 3 if nack else 4
    finally:
        backend.close()
    if as_json: print(json.dumps(out))
    else:
        print(f"address {out['address']} identity {out['identity']} {'ok' if out['identity_ok'] else 'UNEXPECTED'}")
        for s in out["samples"]: print(" ".join(f"{k}={v:.3f}" for k, v in s["values"].items()), "| raw", s["raw"])
    return 0 if out["identity_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())

"""Stage 3. Execute the model on the real device and say what disagreed with it.

The probe is the first time the chain touches the device. It runs the model exactly as written; it never
repairs it. Anything that does not match goes back to the interpret stage as a defect list.
"""
import json, os

from . import core, runner


@core.tool("probe_run")
def probe_run(run_dir, model_dir, n=3, bus_url=None, period_override_ms=None,
              operation=None, parameters=None, binding=None, allow_effects=None, replay=None, **_):
    """Run the model on the bus: address discovery, init, state, identity, n measurements.

    Exit 0 ok, 2 identity unexpected, 3 the device did not acknowledge, 4 a bus or USB fault, which is a
    hardware problem and makes the stage inconclusive rather than failed."""
    run = core.resolve_run(run_dir)
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    from . import interface
    loaded, _sha = interface.load(md)
    schema = loaded.get("schema")
    if schema in interface.SCHEMAS:
        return interface.probe_run(run_dir, md, n=n, operation=operation, parameters=parameters,
            binding=binding, allow_effects=allow_effects, replay=replay)
    if binding is not None:
        if not isinstance(binding, dict) or set(binding) != {"url"}:
            return {"ok": False, "fault": "operator", "reason": "Register binding must contain only url", "_exit": 4}
        if bus_url is not None and bus_url != binding["url"]:
            return {"ok": False, "fault": "operator", "reason": "binding.url and bus_url disagree", "_exit": 4}
        bus_url = binding["url"]
    if not isinstance(bus_url, str) or not bus_url.startswith("ftdi://"):
        return {"ok": False, "fault": "operator", "reason": "Select an explicit ftdi:// binding.url or bus_url", "_exit": 4}
    operations = loaded.get("operations", {})
    steps = sum((operations.get(key, []) for key in ("init", "identify", "measure")), []) + loaded.get("state", [])
    if any(step.get("op") in ("reg_write", "raw_write") for step in steps) and "write" not in (allow_effects or []):
        return {"ok": False, "fault": "operator", "reason": "Explicit write effect grant required", "_exit": 4}
    model, convert = runner.load_model(md)
    try:
        be = runner.open_bus(model, bus_url)
    except (ImportError, OSError) as exc:
        return {"ok": False, "fault": "host", "reason": str(exc), "_exit": 4}
    try:
        out = runner.run_model(model, convert, be, n=int(n), period_override=period_override_ms)
    except IOError as e:
        nack = "NACK" in str(e).upper() or type(e).__name__ == "I2cNackError"
        return {"ok": False, "error": f"{'nack' if nack else 'bus'}: {e}", "_exit": 3 if nack else 4}
    finally:
        be.close()
    out["evidence_kind"] = "live"
    os.makedirs(os.path.join(run, "probe"), exist_ok=True)
    p = os.path.join(run, "probe", "probe.json")
    json.dump(out, open(p, "w", encoding="utf-8"), indent=1)
    return {"ok": bool(out["identity_ok"]), "address": out["address"], "identity": out["identity"],
            "identity_ok": out["identity_ok"], "samples": [s["values"] for s in out["samples"]],
            "probe": core.label(p), "_exit": 0 if out["identity_ok"] else 2,
            "_files": {core.label(p): core.sha256(p)}}


@core.tool("probe_diff")
def probe_diff(model_dir, probe, **_):
    """What the bus said that the model did not predict. Returns the defect list an interpret retry gets."""
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    pp = probe if os.path.isabs(probe) else os.path.join(core.BENCH, probe)
    from . import interface
    model, _sha = interface.load(md)
    if model.get("schema") in interface.SCHEMAS:
        return interface.probe_diff(md, pp)
    pr = json.load(open(pp, encoding="utf-8"))
    d = []
    ident = model.get("identity") or {}
    if not pr.get("identity_ok"):
        d.append({"where": "identity", "what": f"device answered {pr.get('identity')}, model expects "
                  f"{ident.get('expect')}", "evidence": pp})
    primary = f"0x{runner.hx(model['channel']['address_7bit']):02x}"
    if pr.get("address") != primary:
        d.append({"where": "channel.address_7bit", "what": f"the device is at {pr.get('address')}, "
                  f"the model's primary address is {primary}", "evidence": pp})
    samples = pr.get("samples") or []
    raws = {s.get("raw") for s in samples}
    if len(samples) > 1 and len(raws) == 1:
        d.append({"where": "operations.measure", "what": "every sample returned identical raw bytes: the "
                  "read may not be triggering a new conversion", "evidence": sorted(raws)[0]})
    for k in ((model.get("conversion") or {}).get("outputs") or {}):
        vals = [s["values"].get(k) for s in samples if k in s.get("values", {})]
        if not vals:
            d.append({"where": f"conversion.outputs.{k}", "what": "declared but not returned by convert"})
        elif any(v != v or abs(v) == float("inf") for v in vals):
            d.append({"where": f"conversion.outputs.{k}", "what": f"not a finite number on the bus: {vals}"})
    return {"ok": not d, "defects": d, "_exit": 0 if not d else 1}


def _spi_cmd(fr, reg, kind):
    """The command byte a model's framing says an access to `reg` produces."""
    key = {"read": "read_prefix", "write": "write_prefix", "burst": "burst_prefix"}[kind]
    if key not in fr:
        key = "read_prefix" if kind == "burst" else key
    if key not in fr:
        return None
    p = runner.hx(fr[key])
    m = runner.hx(fr.get("address_mask", "0xFF"))
    s = int(fr.get("address_shift", 0))
    return (p | ((reg & m) << s)) & 0xFF


@core.tool("probe_observe")
def probe_observe(run_dir, model_dir, bus, **_):
    """The probe stage where the toolchain cannot drive the bus, only watch it.

    Instead of executing the model on the device, check it against a decoded capture of the device's own
    traffic: every operation the model lists must appear on the wire, and the identity byte the device
    returned must be one the model accepts. Each hypothesis comes back confirmed, refuted or not observed,
    and 'not observed' is not a failure: a capture is a window, and an operation outside it says nothing.
    """
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    bp = bus if os.path.isabs(bus) else os.path.join(core.BENCH, bus)
    model = json.load(open(os.path.join(md, "model.json"), encoding="utf-8"))
    cap = json.load(open(bp, encoding="utf-8"))
    tx = [{"t_ms": t["t_ms"], "mosi": [int(x, 16) for x in t["mosi"].split()],
           "miso": [int(x, 16) for x in t["miso"].split()]} for t in cap["transactions"]]
    ch = model.get("channel") or {}
    fr = ch.get("framing") or {}
    ops = model.get("operations") or {}
    lines, confirmed, refuted, unseen = [], 0, 0, 0

    def verdict(name, hypothesis_seen, detail):
        nonlocal confirmed, refuted, unseen
        lines.append(f"{hypothesis_seen:<14} {name}  {detail}")
        if hypothesis_seen == "confirmed":
            confirmed += 1
        elif hypothesis_seen == "refuted":
            refuted += 1
        else:
            unseen += 1

    if ch.get("type") != "spi":
        return {"supported": False, "reason": f"probe_observe covers spi captures, model is {ch.get('type')!r}",
                "_exit": 1}

    # identity
    ident = model.get("identity") or {}
    for s in ops.get("identify", []):
        if s.get("op") != "reg_read":
            continue
        reg = runner.hx(s["reg"])
        want = _spi_cmd(fr, reg, "read")
        hits = [t for t in tx if t["mosi"] and t["mosi"][0] == want]
        if not hits:
            verdict(f"identify reads 0x{reg:02x}", "not observed",
                    f"no frame began with {want if want is None else hex(want)}")
        else:
            got = hits[0]["miso"][1] if len(hits[0]["miso"]) > 1 else None
            accept = {runner.hx(e) for e in ident.get("expect", [])}
            ok = got is not None and got in accept
            verdict(f"identify reads 0x{reg:02x}", "confirmed" if ok else "refuted",
                    f"frame {hex(want)} answered {hex(got) if got is not None else None}, model accepts "
                    f"{sorted(hex(a) for a in accept)}")

    # init writes
    for s in ops.get("init", []):
        if s.get("op") != "reg_write":
            continue
        reg, data = runner.hx(s["reg"]), [runner.hx(b) for b in s["bytes"]]
        want = _spi_cmd(fr, reg, "write")
        hits = [t for t in tx if t["mosi"][:1] == [want] and t["mosi"][1:1 + len(data)] == data]
        near = [t for t in tx if t["mosi"][:1] == [want]]
        if hits:
            verdict(f"init writes 0x{reg:02x}", "confirmed",
                    f"frame {hex(want)} {' '.join(f'{b:02x}' for b in data)} seen {len(hits)}x")
        elif near:
            verdict(f"init writes 0x{reg:02x}", "refuted",
                    f"frame {hex(want)} carried {' '.join(f'{b:02x}' for b in near[0]['mosi'][1:])}, "
                    f"model says {' '.join(f'{b:02x}' for b in data)}")
        else:
            verdict(f"init writes 0x{reg:02x}", "not observed", f"no frame began with {hex(want)}")

    # state and measure reads
    def check_read(label, reg, n, collect=None, strict=False):
        """strict marks a measurement: the capture spans many periods, so a measurement command that never
        appears is a refutation rather than a silence."""
        want = _spi_cmd(fr, reg, "burst" if n > 1 else "read")
        hits = [t for t in tx if t["mosi"] and t["mosi"][0] == want]
        if not hits:
            verdict(label, "refuted" if strict else "not observed",
                    f"no frame began with {hex(want) if want is not None else None}"
                    + (f"; the capture holds {len(tx)} frames, so a measurement should have appeared"
                       if strict else ""))
            return
        length = len(hits[0]["mosi"]) - 1
        ok = length >= n
        verdict(label, "confirmed" if ok else "refuted",
                f"frame {hex(want)} returned {length} byte(s), model reads {n}")
        if collect is not None:
            collect.extend(t["t_ms"] for t in hits)

    for st in model.get("state", []):
        if st.get("op", "reg_read") == "reg_read":
            check_read(f"state read 0x{runner.hx(st['reg']):02x}", runner.hx(st["reg"]), int(st.get("len", 1)))

    times = []
    for s in ops.get("measure", []):
        if s.get("op") == "reg_read":
            check_read(f"measure reads 0x{runner.hx(s['reg']):02x}", runner.hx(s["reg"]), int(s["len"]),
                       times, strict=True)

    period_seen = None
    if len(times) > 2:
        gaps = [b - a for a, b in zip(sorted(times), sorted(times)[1:])]
        gaps = sorted(gaps)[len(gaps) // 2:] or gaps
        period_seen = round(sum(gaps) / len(gaps), 1)
        claim = model.get("measure_period_ms")
        if claim:
            ok = abs(float(claim) - period_seen) <= max(20.0, 0.1 * period_seen)
            verdict("measurement period", "confirmed" if ok else "refuted",
                    f"bus shows {period_seen} ms, model says {claim} ms")
        else:
            verdict("measurement period", "not observed", f"bus shows {period_seen} ms, model states none")

    return {"confirmed": confirmed, "refuted": refuted, "not_observed": unseen,
            "period_ms_seen": period_seen, "transactions": len(tx), "lines": lines,
            "_exit": 0 if refuted == 0 else 1}

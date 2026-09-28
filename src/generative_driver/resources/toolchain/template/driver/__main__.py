"""CLI: python -m driver [--url URL] [--no-init] [--json] [--force] <command> ...   (run from package/)

  info                         address, identity byte, package version, the model's outputs and extra ops
  read <reg> [n] [--force]     raw register read; refused outside the model's safe reads unless --force
  write <reg> <byte> [--force] single-byte write; known-safe writes run, 'never' writes are always refused,
                               anything else is refused unless --force
  measure [--n N]              every quantity the model supports, converted exactly as the model converts
  op <name>                    one of the model's extra named operations (see info: ops=...)

One line per reply: 'OK key=value ...' or 'ERR code=<code> key=value ...'; --json gives the same reply as one
JSON object per line. Exit codes: 0 ok, 1 refused or usage, 2 identity mismatch, 3 no acknowledge, 4 bus or
USB error. Every invocation is one session in the model's order: init (unless --no-init), state reads,
identify, then the command.
"""
import argparse
import json
import sys

from .device import Device, DriverError, IdentityError, RefusedError, DEFAULT_URL, hx, reg_hex


def _emit(as_json, st, line_kv, json_kv=None):
    """line_kv: ordered dict of tokens for the one-line form; json_kv: the structured form (defaults to line_kv)."""
    if as_json:
        d = {"status": st}
        d.update(json_kv if json_kv is not None else line_kv)
        print(json.dumps(d))
    else:
        print(st + "".join(" %s=%s" % (k, v) for k, v in line_kv.items()))
    sys.stdout.flush()


def _lst(xs):
    xs = list(xs)
    return ",".join(str(x) for x in xs) if xs else "-"


def _hexbytes(b):
    return "".join("%02x" % x for x in b) if b else "-"


def _parser():
    p = argparse.ArgumentParser(prog="python -m driver", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="Explicit operator-selected FTDI adapter URL")
    p.add_argument("--allow-write", action="store_true", help="Grant the model's register writes for this invocation")
    p.add_argument("--no-init", action="store_true", help="skip the model's init operations for this session")
    p.add_argument("--json", action="store_true", help="one JSON object per reply instead of key=value tokens")
    p.add_argument("--force", action="store_true", help="read/write: proceed without model evidence")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    r = sub.add_parser("read")
    r.add_argument("reg")
    r.add_argument("n", nargs="?", default="1")
    r.add_argument("--force", dest="cmd_force", action="store_true", default=False)
    w = sub.add_parser("write")
    w.add_argument("reg")
    w.add_argument("byte")
    w.add_argument("--force", dest="cmd_force", action="store_true", default=False)
    m = sub.add_parser("measure")
    m.add_argument("--n", type=int, default=1, help="number of samples, paced at the model's period")
    o = sub.add_parser("op")
    o.add_argument("name")
    return p


def main(argv=None):
    a = _parser().parse_args(argv)
    force = bool(a.force or getattr(a, "cmd_force", False))
    as_json = a.json
    # info always reports the identity it saw; read/write with --force may proceed past a mismatch.
    strict = a.cmd != "info" and not (a.cmd in ("read", "write") and force)
    dev = Device(url=a.url, init=not a.no_init, strict=strict, allow_write=a.allow_write)
    try:
        try:
            dev.open()
            if a.cmd == "info":
                i = dev.info()
                kv = dict(package=i["package"], version=i["version"], address=i["address"], identity=i["identity"],
                          identity_ok=int(i["identity_ok"]), expected=_lst(i["identity_expected"]),
                          init_done=int(i["init_done"]), schema=i["schema"],
                          outputs=_lst("%s:%s" % (k, v) for k, v in i["outputs"].items()), ops=_lst(i["ops"]),
                          device_confidence=i["device_confidence"])
                if i["identity_ok"]:
                    _emit(as_json, "OK", kv, i)
                    return 0
                _emit(as_json, "ERR", dict(code="identity_mismatch", **kv), dict(code="identity_mismatch", **i))
                return IdentityError.exit_code
            if a.cmd == "read":
                reg, n = hx(a.reg), int(a.n)
                data = dev.read(reg, n, force=force)
                ev = "safe" if dev.safety.read_verdict(reg, n)[0] == "safe" else "forced"
                _emit(as_json, "OK", dict(reg=reg_hex(reg), n=n, data=_hexbytes(data), evidence=ev))
                return 0
            if a.cmd == "write":
                reg, byte = hx(a.reg), hx(a.byte)
                ev = dev.write(reg, byte, force=force)
                _emit(as_json, "OK", dict(reg=reg_hex(reg), byte="0x%02x" % byte, evidence=ev))
                return 0
            if a.cmd == "measure":
                for _ in range(max(1, a.n)):
                    s = dev.measure()
                    skipped = [k for k, v in s.skipped.items() if v]
                    line = dict(s.values)
                    line.update(units=_lst("%s:%s" % (k, v) for k, v in s.units.items()), valid=int(s.valid),
                                skipped=_lst(skipped), raw=_hexbytes(s.raw), t_wall="%.3f" % s.t_wall)
                    js = dict(values=s.values, units=s.units, valid=s.valid, skipped=skipped,
                              raw=_hexbytes(s.raw), t_wall=s.t_wall)
                    _emit(as_json, "OK", line, js)
                return 0
            if a.cmd == "op":
                reads = {k: _hexbytes(v) for k, v in dev.op(a.name).items()}
                _emit(as_json, "OK", dict(op=a.name, **reads), dict(op=a.name, reads=reads))
                return 0
        finally:
            dev.close()
    except RefusedError as e:
        _emit(as_json, "ERR", dict(code="refused", reason=e.reason, detail=json.dumps(e.detail)),
              dict(code="refused", reason=e.reason, detail=e.detail))
        return e.exit_code
    except IdentityError as e:
        _emit(as_json, "ERR", dict(code="identity_mismatch", detail=json.dumps(str(e))),
              dict(code="identity_mismatch", detail=str(e)))
        return e.exit_code
    except DriverError as e:
        code = {3: "nack", 4: "bus"}.get(e.exit_code, "error")
        _emit(as_json, "ERR", dict(code=code, detail=json.dumps(str(e))), dict(code=code, detail=str(e)))
        return e.exit_code
    return 1


if __name__ == "__main__":
    sys.exit(main())

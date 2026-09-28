"""Run directories, the event log, and small shared helpers.

Every tool in this toolchain records one event. An event says who acted, what tool, with what arguments,
what came back, how long it took, and the sha256 of every file it read or wrote. The log is the product:
a run is interpretable if a reader who was not there can replay what happened from `events.jsonl` alone.
"""
import datetime, hashlib, json, os, subprocess, time, sys, sqlite3
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

BENCH = str(Path(__file__).resolve().parents[1] / "resources")
VENV_PY = sys.executable


def label(path):
    """Artifact references cross directories and Windows volumes unchanged."""
    return str(Path(path).resolve())


@contextmanager
def file_lock(path):
    """Cross-process ownership for an append-only evidence file on either host OS.

    SQLite's native locks release when a crashed owner exits. Keep the sidecar
    file permanently; deleting lock files can create two concurrent owners.
    """
    connection = sqlite3.connect(str(path) + ".lock.sqlite3", timeout=30)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        yield
    finally:
        connection.rollback()
        connection.close()


_CALLER = ContextVar("toolchain_caller", default=None)


@contextmanager
def event_context(actor, client):
    """Attribute nested events to a remote caller without changing local callers."""
    token = _CALLER.set((actor, dict(client)))
    try:
        yield
    finally:
        _CALLER.reset(token)


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (IsADirectoryError, FileNotFoundError, PermissionError):
        return None


def sha_tree(root, limit=None):
    """Hash the full inventory. An explicit caller limit fails instead of silently truncating."""
    out = {}
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git", "work", ".venv"))
        for n in sorted(names):
            if n == ".DS_Store":
                continue
            p = os.path.join(dirpath, n)
            out[Path(p).relative_to(root).as_posix()] = sha256(p)
            if limit is not None and len(out) > limit:
                raise ValueError("file inventory exceeds explicit limit")
    return out


def resolve_run(run_dir):
    """Every caller chooses absolute storage outside the installed resources."""
    if not isinstance(run_dir, (str, os.PathLike)) or not Path(run_dir).is_absolute():
        raise ValueError("run_dir must be an absolute path")
    path = Path(run_dir).resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("run_dir must be outside installed resources")
    return str(path)


def log_path(run):
    return os.path.join(run, "events.jsonl")


def log_event(run, tool, args=None, result=None, exit_code=0, t_wall_s=None, actor="orchestrator",
              files=None, notes=None, stage=None):
    """Append one event. Returns the event so a tool can hand it straight back."""
    os.makedirs(run, exist_ok=True)
    p = log_path(run)
    seq = 1
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            seq = sum(1 for _ in fh) + 1
    ev = {"seq": seq, "ts": now(), "actor": actor, "tool": tool, "args": args or {},
          "exit": exit_code, "t_wall_s": None if t_wall_s is None else round(t_wall_s, 2),
          "result": result if result is not None else {}, "files": files or {}, "notes": notes or [],
          "stage": stage or tool_stage(tool)}
    caller = _CALLER.get()
    if caller is not None:
        ev["actor"], ev["client"] = caller[0], dict(caller[1])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")
    return ev


def tool_stage(name):
    """Host-owned tool labels; inference attribution is recorded separately."""
    prefix = name.split('_', 1)[0]
    return {'acquire': 'acquire', 'interpret': 'interpret', 'model': 'interpret',
            'firmware': 'interpret', 'image': 'interpret', 'ghidra': 'interpret', 'probe': 'probe', 'emit': 'emit',
            'bus': 'setup', 'hardware': 'hardware', 'interface': 'probe',
            'usage': 'accounting'}.get(prefix, 'unattributed')


def tool(name):
    """Decorator: time the call, log it, and return a JSON-serialisable dict with the event's sequence
    number. An exception is an event too, with exit 1 and the message, so nothing fails silently."""
    def deco(fn):
        @wraps(fn)
        def wrapper(**kw):
            run = resolve_run(kw.get("run_dir") or kw.get("run") or "scratch")
            t0 = time.monotonic()
            try:
                out = fn(**kw) or {}
                exit_code = int(out.pop("_exit", 0))
                files = out.pop("_files", {})
                notes = out.pop("_notes", [])
                ev = log_event(run, name, kw, _summary(out), exit_code, time.monotonic() - t0,
                               files=files, notes=notes)
                out["_event"] = ev["seq"]
                out["_exit"] = exit_code
                return out
            except Exception as e:  # a tool that throws is still an event
                ev = log_event(run, name, kw, {"error": f"{type(e).__name__}: {e}"}, 1, time.monotonic() - t0)
                return {"_event": ev["seq"], "_exit": 1, "error": f"{type(e).__name__}: {e}"}
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _summary(out, cap=2000):
    """Keep the log readable: long values are truncated in the event, never in the return."""
    s = json.dumps(out, default=str)
    if len(s) <= cap:
        return out
    return {"truncated": True, "keys": sorted(out.keys()), "head": s[:cap]}


def sh(cmd, cwd=None, env=None, timeout=900):
    """Run a command, capture everything, never raise on a non-zero exit."""
    r = subprocess.run(cmd, cwd=cwd or BENCH, env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def render_log(run):
    """The event log as a readable timeline."""
    p = log_path(run)
    if not os.path.exists(p):
        return "no events"
    lines = ["# Run log", "", f"`{label(run)}`", ""]
    for raw in open(p, encoding="utf-8"):
        e = json.loads(raw)
        head = f"{e['seq']:>3}  {e['ts'][11:19]}  {e['actor']:<16} {e['tool']}"
        if e.get("t_wall_s") is not None:
            head += f"  ({e['t_wall_s']}s)"
        if e["exit"]:
            head += f"  EXIT {e['exit']}"
        lines.append(head)
        for k, v in (e.get("result") or {}).items():
            v = json.dumps(v, default=str)
            lines.append(f"       {k}: {v[:300]}")
        for n in e.get("notes", []):
            lines.append(f"       note: {n}")
        for f, h in list((e.get("files") or {}).items())[:12]:
            lines.append(f"       file: {f}  {str(h)[:16]}")
        lines.append("")
    return "\n".join(lines)

"""Stage 2. Turn whatever the acquire stage found into an interface model.

Each pipeline stage has an assigned agent; this module supplies the interpret stage's tools.
The interpret agent receives a separate workspace with supplied inputs hashed first and the hashes
kept on the host. The configured runtime controls its tools; the default seal records input integrity
and instructions to stay within supplied evidence. It does not establish OS filesystem or network
isolation. The agent writes the model for host collection and validation.
"""
import collections, gzip, hashlib, json, os, re, shlex, shutil, tempfile
from pathlib import Path

from . import core, runner

SKILLS = os.path.join(core.BENCH, "toolchain", "skills")
REPOSITORY = os.path.dirname(core.BENCH)
STDLIB_OK = set(
    "math struct sys json time collections itertools functools dataclasses typing decimal fractions "
    "array binascii statistics enum abc copy re os".split())

# Front ends that write an executable interface model get the shared runtime as their kit; the others write
# a schema-1 register model checked by the register validator.
EXECUTABLE = {"interpret-interface", "interpret-selfdescribing"}
FRONT_ENDS = EXECUTABLE | {"interpret-datasheet", "interpret-firmware", "interpret-blackbox"}
# Schema document per executable front end, newest first; the workspace copy keeps the neutral name.
# interpret-interface stays on schema 5: its brief names schemas 4 and 5 and nothing newer.
MODEL_DOCS = {"interpret-interface": ("INTERFACE_MODEL_V5.md", "INTERFACE_MODEL_V4.md", "INTERFACE_MODEL_V3.md"),
              "interpret-selfdescribing": ("INTERFACE_MODEL_V6.md",)}
# Directories a workspace may not lie in: a Claude Code session scratch tree, whose path every agent of every session
# is told to use and which can hold the parent's own working files (answer keys included), and the Claude
# configuration tree, which holds memory files and other sessions' transcripts.
SCRATCH_TREES = tuple(str(Path.home() / name) for name in (".claude", ".codex", ".config/goose"))
RESERVED = {"BRIEF.md", "INTERFACE_MODEL.md", "INPUT_HASHES.json", "validate.py", "qualify.py",
            "image_map_check.py", "WIRING.md", "defects.json", "_toolkit"}
# defects.json is the control file the retry loop writes and the schema-1 validator rewrites; a change to it
# is reported, never counted as tampering.
CONTROL = {"defects.json"}
SEAL_SCHEMA = "interpret-input-seal/1"
SEAL_KINDS = {
    "instruction": "sealed by instruction, not by tool restriction: the agent's tools, shell and file access "
                   "were not limited, so the host seal shows only that the supplied inputs are byte-unchanged, "
                   "not what the agent read, ran or reached elsewhere",
    "enforced": "sealed by a boundary the harness enforced (tool restriction or sandbox): the host seal shows "
                "that the supplied inputs are byte-unchanged; the boundary itself is the harness's claim and is "
                "not checked here"}
AGENT_SEAL = ("sealed by the Claude CLI tool allow-list: the agent had file tools and the listed commands only, "
              "and file tools can still read any path; no OS sandbox")
DEVICE_TERMS = ("usb", "ioreg", "system_profiler", "/dev/", "serial", "pyusb", "pyocd", "openocd", "probe-rs",
                "esptool", "pyftdi")
BENIGN_DEVICES = re.compile(r"/dev/(?:null|stdin|stdout|stderr|tty|fd/\d+)\b")
NETWORK_COMMAND = re.compile(r"https?://|\b(?:curl|wget|ssh|scp|sftp|telnet|ncat|nmap|ping|ftp)\b|"
                             r"\bpip3? install\b|\bgit (?:clone|fetch|pull)\b|\bnpm install\b|\bbrew\b")
PATH_TOKEN = re.compile(r"(?<![\w.:/~$@%+\\-])((?:~|\$HOME|\$\{HOME\})?/[^\s'\"`;|&<>(){}\[\],]+)")
INJECTED_TEXT = re.compile(r"Contents of (\S+?\.md) \(")
# Text the agent wrote or searched for is data, not a location it touched.
DATA_FIELDS = {"Write": {"content"}, "Edit": {"old_string", "new_string"}, "MultiEdit": {"edits"},
               "NotebookEdit": {"new_source"}, "Grep": {"pattern"}}


def _engine():
    from . import interface  # noqa: F401  (puts the shared runtime on sys.path)
    from interface_runtime import engine, evidence
    return engine, evidence


def _output_present(workspace):
    try:
        model = json.loads(Path(workspace, "model.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(model, dict) and (model.get("schema") in _engine()[0].SCHEMAS or
        (model.get("schema") == "interface-model/1" and Path(workspace, "convert.py").is_file()))


def _attempt_no(attempt):
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return attempt


def _canon(path):
    """Resolve symlinks and the macOS /private aliases, so /tmp/x and /private/tmp/x compare equal."""
    p = os.path.realpath(os.path.expanduser(str(path)))
    for alias in ("/private/tmp", "/private/var", "/private/etc"):
        if p == alias or p.startswith(alias + "/"):
            return p[len("/private"):]
    return p


def _under(path, root):
    path, root = _canon(path), _canon(root)
    return Path(path).is_relative_to(Path(root))


def _label(path):
    """A path as the event log names it: relative to bench/ inside it, canonical and absolute outside."""
    return os.path.relpath(_canon(path), _canon(core.BENCH)) if _under(path, core.BENCH) else _canon(path)


def _tree(root):
    """Hash every regular file. A symlink or special file is refused: collect would copy what it points at,
    or block reading it."""
    out = {}
    for dirpath, dirnames, names in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for n in dirnames + names:
            p = os.path.join(dirpath, n)
            if os.path.islink(p) or not (os.path.isdir(p) or os.path.isfile(p)):
                raise ValueError(f"not a regular file or directory in the workspace: {os.path.relpath(p, root)}")
        for n in sorted(names):
            if n != ".DS_Store":
                out[Path(dirpath, n).relative_to(root).as_posix()] = core.sha256(os.path.join(dirpath, n))
    return out


def _input_name(name):
    parts = name.split("/") if isinstance(name, str) else []
    if (not parts or "\\" in name or "\x00" in name or name.startswith("/")
            or any(p in ("", ".", "..") for p in parts)):
        return f"input name {name!r} must be a relative path inside the workspace"
    if name in RESERVED or parts[0] in RESERVED:
        return f"input name {name!r} is reserved for the workspace kit"
    return None


def _agent(ws, prompt, allowed, timeout=7200, model=None):
    """One sealed Claude Code session in `ws`. Returns the transcript events and a cost summary."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "default", "--allowedTools", *allowed,
           "--disallowedTools", "WebFetch", "WebSearch", "Task", "NotebookEdit"]
    if model:
        cmd[3:3] = ["--model", model]
    rc, out, err = core.sh(cmd, cwd=ws, timeout=timeout)
    events, cost = [], {}
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        events.append(ev)
        if ev.get("type") == "result":
            cost = {"wall_s": round(ev.get("duration_ms", 0) / 1000.0, 1),
                    "turns": ev.get("num_turns"), "usd": ev.get("total_cost_usd"),
                    "tokens_in": (ev.get("usage") or {}).get("input_tokens"),
                    "tokens_out": (ev.get("usage") or {}).get("output_tokens"),
                    "is_error": ev.get("is_error")}
    return rc, events, cost, err


def _tool_uses(events):
    """(event, tool name, input) for every tool_use block, in transcript order."""
    for ev in events:
        message = ev.get("message") if isinstance(ev, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        for blk in content if isinstance(content, list) else []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                inp = blk.get("input")
                yield ev, str(blk.get("name")), inp if isinstance(inp, dict) else {}


def _actions(events):
    """The agent's actions, as a list a person can read: which tool, on what."""
    acts = []
    for _ev, name, inp in _tool_uses(events):
        what = inp.get("file_path") or inp.get("pattern") or inp.get("command") or inp.get("url") or ""
        extra = f" pages={inp['pages']}" if inp.get("pages") else ""
        acts.append(f"{name}: {str(what)[:90]}{extra}")
    return acts


@core.tool("model_validate")
def model_validate(model_dir, random_files=20, seed=0, **_):
    """Structural check of an interface model. No ground truth and no device knowledge: the model is asked
    only to be well formed, to declare its evidence, and for its conversion to survive bytes the model
    itself says the device can return."""
    import random
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    d = []
    mp = os.path.join(md, "model.json")
    if not os.path.exists(mp):
        return {"ok": False, "defects": [{"where": "model.json", "what": "missing"}], "_exit": 1}
    try:
        from .interface import load
        model, _sha = load(md)
    except Exception as e:
        return {"ok": False, "defects": [{"where": "model.json", "what": f"not valid JSON: {e}"}], "_exit": 1}
    if isinstance(model, dict) and model.get("schema") in _engine()[0].SCHEMAS:
        from .interface import validate_model
        result = validate_model(model)
        return {**result, "_exit": 0 if result["ok"] else 1}
    if not isinstance(model, dict):
        return {"ok": False, "defects": [{"where":"model.json", "what":"model must be an object"}], "_exit":1}
    if model.get("schema") == "interface-model/2-experimental":
        from .firmware import firmware_validate
        return firmware_validate(run_dir=_.get("run_dir", "scratch"), model_dir=md)
    if model.get("schema") != "interface-model/1":
        return {"ok": False, "defects": [{"where": "schema", "what":
            f"unsupported schema {model.get('schema')!r}; select a supported model validator"}], "_exit": 1}
    ch = model.get("channel") or {}
    if ch.get("type") not in ("i2c", "spi"):
        d.append({"where": "channel.type", "what": f"schema 1 has no channel type {ch.get('type')!r}"})
    if ch.get("type") == "i2c":
        try:
            runner.hx(ch.get("address_7bit"))
        except Exception:
            d.append({"where": "channel.address_7bit", "what": "not a number"})
    elif ch.get("type") == "spi":
        fr = ch.get("framing") or {}
        if not fr:
            d.append({"where": "channel.framing", "what": "an spi channel must say how a register access "
                                                          "becomes bytes on the wire"})
        else:
            for k in ("read_prefix", "write_prefix", "address_mask"):
                if k not in fr:
                    d.append({"where": f"channel.framing.{k}", "what": "missing"})
    ops = model.get("operations") or {}
    if not ops.get("measure"):
        d.append({"where": "operations.measure", "what": "empty: the model reads nothing"})
    ident = model.get("identity") or {}
    if not ident.get("expect"):
        d.append({"where": "identity.expect", "what": "empty: nothing would confirm the part"})
    if not (model.get("conversion") or {}).get("outputs"):
        d.append({"where": "conversion.outputs", "what": "no outputs declared"})
    for key in ("init", "identify", "measure"):
        if ops.get(key) and not any(key in str(p.get("item", "")) for p in model.get("provenance", [])):
            d.append({"where": f"provenance[{key}]", "what": "no provenance entry mentions this operation"})
    if "safety" not in model:
        d.append({"where": "safety", "what": "missing"})
    cp = os.path.join(md, (model.get("conversion") or {}).get("module", "convert.py"))
    if not os.path.exists(cp):
        d.append({"where": "convert.py", "what": "missing"})
        return {"ok": False, "defects": d, "_exit": 1}
    src = Path(cp).read_text(encoding="utf-8")
    for line in src.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            mod = s.split()[1].split(".")[0]
            if mod not in STDLIB_OK:
                d.append({"where": "convert.py", "what": f"imports {mod!r}, which is not the standard library"})
    try:
        model, convert = runner.load_model(md)
    except Exception as e:
        d.append({"where": "convert.py", "what": f"will not import: {type(e).__name__}: {e}"})
        return {"ok": False, "defects": d, "_exit": 1}
    declared = set((model.get("conversion") or {}).get("outputs", {}).keys())
    raised, produced = [], set()
    rng = random.Random(seed)
    trials = [bytes(256)] + [runner.random_register_file(model, rng) for _ in range(int(random_files))]
    for i, regs in enumerate(trials):
        m_exec, addr = runner.for_register_file(model)
        be = runner.RegisterFileBackend(regs, {addr})
        try:
            out = runner.run_model(m_exec, convert, be, n=1, period_override=0)
            produced |= set(out["samples"][0]["values"].keys())
        except Exception as e:
            raised.append(f"{'all-zero' if i == 0 else f'random#{i}'}: {type(e).__name__}: {e}")
    if raised:
        d.append({"where": "convert.py", "what": "raises on bytes the device can return",
                  "evidence": raised[:4]})
    if produced and produced != declared:
        d.append({"where": "conversion.outputs",
                  "what": f"declared {sorted(declared)} but convert returned {sorted(produced)}"})
    dp = os.path.join(md, "defects.json")
    if d:
        with open(dp, "w", encoding="utf-8") as stream:
            json.dump(d, stream, indent=1)
    elif os.path.exists(dp):
        os.remove(dp)
    return {"ok": not d, "defects": d, "checks_run": 8 + len(trials),
            "_exit": 0 if not d else 1, "_files": {core.label(mp): core.sha256(mp),
                                                   core.label(cp): core.sha256(cp)}}


@core.tool("model_provenance_audit")
def model_provenance_audit(model_dir, artifact, kind="datasheet", **_):
    """Reported, not a gate: does every citation point at something that is actually in the artifact?"""
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    art = artifact if os.path.isabs(artifact) else os.path.join(core.BENCH, artifact)
    if kind != "datasheet":
        return {"supported": False, "reason": f"no provenance backend for {kind!r} yet", "_exit": 0}
    rc, out, err = core.sh([core.VENV_PY, os.path.join(core.BENCH, "tools", "provenance_audit.py"), md, art])
    tail = [l for l in out.splitlines() if l.strip()][-3:]
    return {"supported": True, "report": tail, "raw_exit": rc}


def _refusal(front_end, mode):
    if (not isinstance(front_end, str) or not front_end or front_end.startswith(".")
            or any(s in front_end for s in ("/", "\\", os.sep, "\x00"))):
        return f"front_end must be a skill name, not a path: {front_end!r}"
    if front_end not in FRONT_ENDS:
        return f"unknown front_end {front_end!r}; one of {sorted(FRONT_ENDS)}"
    if mode not in ("agent", "prepare"):
        return f"mode must be 'agent' or 'prepare', not {mode!r}"
    return None


def _prompt(ws, mode):
    """The agent's instructions. Generic by construction: the workspace path is the only variable in them."""
    where = f"Read {os.path.join(ws, 'BRIEF.md')}"
    check = ("Set the process working directory to this workspace and use the configured Python 3.11+ "
             "interpreter to run `validate.py .`; fix every reported defect. If BRIEF.md asks for "
             "replies.json, write it from your own analysis and run `qualify.py . replies.json` with "
             "the same interpreter and working directory until every operation with replies qualifies.")
    return (f"Keep task evidence and generated artifacts inside {ws}. Installed Python/runtime libraries and "
            f"explicitly supplied analysis executables, including Ghidra and Java named in ANALYSIS_TOOLS.json, "
            f"may be read and executed outside this directory. Use the supplied helper and keep its project, "
            f"cache, logs and exports inside this workspace. Do not read personal instructions, memory files, "
            f"other repositories, other runs or evaluator-owned files outside the supplied inputs, or search outside this workspace for task "
            f"evidence. Do not enumerate, open or query any USB, serial or network device or service. "
            f"If BINDING_CONTEXT.json is supplied, use its runtime_channel and record unverified physical "
            f"settings in NOTES.md; a supplied byte-stream binding does not establish baud or pins. "
            f"Read TASKS.json when supplied for requested behaviors, canonical outputs/units and effect grants. "
            f"If REPAIR_CONTEXT.json is supplied, this is a repair: read it and DEFECTS.json first, then use "
            f"the listed prior candidate, capabilities, replies and observed transactions. These supplied "
            f"diagnostic measurements are authorized evidence, including independent monitor readings; "
            f"use them to correct discrepancies instead of discarding them as hidden evaluator data. "
            f"Start from the latest candidate and change what the evidence contradicts. Preserve sealed "
            f"prior files and write the corrected model/replies at the workspace root. Do not read passwords, "
            f"plaintext oracle files or hidden final answers. If only defects.json is supplied, read it "
            f"as the requested repair feedback. "
            f"{where} and do exactly what it says: it tells you to produce an "
            f"interface model from the description supplied. You have no access to the device and must not try "
            f"to reach it, and you must not search the web. {check} An operation the qualifier reports "
            f"uncovered is recorded in NOTES.md, not retried. Report in one paragraph what you produced and what "
            f"the description left ambiguous.")


@core.tool("interpret_run")
def interpret_run(run_dir, front_end, inputs, wiring=None, attempt=1, defects=None,
                  budget_min=120, model=None, mode="agent", workspace_root=None, **_):
    """Seal a workspace, run the front-end's skill in it as its own agent, and return the model it wrote.

    `front_end` is interpret-interface or interpret-selfdescribing, which write an executable interface model
    and get the shared runtime with validate.py and qualify.py, or interpret-datasheet, interpret-firmware or
    interpret-blackbox, which write a schema-1 register model. `inputs` maps a relative name in the workspace
    to a path under bench/ (a directory is copied whole). On a retry, pass `defects` and they are written into
    the workspace as defects.json for the agent to work from.

    The workspace is runs/<run>/interpret/attempt<N>, or <workspace_root>/attempt<N> when workspace_root is
    given: an absolute path outside the repository that does not carry the run name, so the prompt shows
    neither. Every input is hashed into INPUT_HASHES.json and into a host seal the agent is not shown,
    runs/<run>/interpret/attempt<N>.seal.json; the prepare event logs the same hashes. An attempt is sealed
    once: a prepared attempt or a non-empty workspace is refused.

    mode 'agent' runs the sealed agent itself with the Claude CLI and collects what it wrote. mode 'prepare'
    only seals the workspace and hands back the workspace path and the prompt, for a caller whose own harness
    spawns the agent; the caller then calls interpret_collect. Use 'prepare' when the CLI cannot authenticate.
    """
    refused = _refusal(front_end, mode)
    attempt = _attempt_no(attempt)
    run = core.resolve_run(run_dir)
    host = Path(run, "interpret")
    seal_path, pointer, record = (host / f"attempt{attempt}.seal.json", host / f"attempt{attempt}.json",
                                  host / f"attempt{attempt}")
    if not refused and any(p.exists() or p.is_symlink() for p in (seal_path, pointer)):
        refused = f"attempt {attempt} is already prepared; choose a fresh attempt instead of resealing"
    ws = record
    if not refused and workspace_root:
        root = os.path.expanduser(str(workspace_root))
        name = os.path.basename(os.path.normpath(run)).lower()
        if not os.path.isabs(root):
            refused = "workspace_root must be an absolute path"
        elif _under(root, REPOSITORY):
            refused = "workspace_root must lie outside the repository"
        elif any(_under(root, tree) for tree in SCRATCH_TREES):
            refused = ("workspace_root must not lie in a Claude Code scratch or configuration tree: agents are told to "
                       "use the session scratchpad, and it can hold the caller's own files")
        elif len(name) >= 3 and (name in os.path.realpath(root).lower() or name in root.lower()):
            refused = "workspace_root must not carry the run name: the prompt shows the workspace path"
        elif record.exists() or record.is_symlink():
            refused = f"the run already holds interpret/attempt{attempt}"
        ws = Path(os.path.realpath(root), f"attempt{attempt}")
    if not refused and (ws.is_symlink() or (ws.exists() and (not ws.is_dir() or any(ws.iterdir())))):
        refused = f"workspace {ws} is not empty; prepare a fresh attempt instead"
    skill = Path(SKILLS, str(front_end), "SKILL.md")
    if not refused and not skill.is_file():
        refused = f"no skill {front_end!r}"
    model_doc = "INTERFACE_MODEL.md"
    if not refused and front_end in EXECUTABLE:
        model_doc = next((n for n in MODEL_DOCS[front_end] if Path(core.BENCH, "docs", n).is_file()), None)
        refused = None if model_doc else f"schema document docs/{MODEL_DOCS[front_end][0]} is missing"
    sources = {}
    for name, src in (inputs or {}).items():
        refused = refused or _input_name(name)
        s = src if os.path.isabs(str(src)) else os.path.join(core.BENCH, str(src))
        refused = refused or (None if os.path.exists(s) else f"input {name!r} does not exist: {src}")
        sources[name] = s
    if refused:
        return {"ok": False, "reason": refused, "_exit": 1}
    ws.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill, ws / "BRIEF.md")
    shutil.copyfile(Path(core.BENCH, "docs", model_doc), ws / "INTERFACE_MODEL.md")
    if wiring:
        shutil.copyfile(wiring if os.path.isabs(wiring) else os.path.join(core.BENCH, wiring), ws / "WIRING.md")
    for name, s in sources.items():
        dst = ws / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.isdir(s):
            shutil.copytree(s, dst, dirs_exist_ok=True)
        else:
            shutil.copyfile(s, dst)
    if defects:
        (ws / "defects.json").write_text(json.dumps(defects, indent=1), encoding="utf-8")
    toolchain = Path(core.BENCH, "toolchain")
    if front_end in EXECUTABLE:
        shutil.copyfile(toolchain / "template/interface_validate.py", ws / "validate.py")
        shutil.copyfile(toolchain / "template/interface_qualify.py", ws / "qualify.py")
        checker = Path(core.BENCH, "tools/workspace/image_map_check.py")
        if "image.bin" in sources and checker.is_file():  # standard-library mapping checker for a raw image
            shutil.copyfile(checker, ws / "image_map_check.py")
        from .interface import RUNTIME
        shutil.copytree(RUNTIME, ws / "_toolkit/interface_runtime",
            dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
    else:
        validator = toolchain / "template/register_validate.py"
        shutil.copyfile(validator, ws / "validate.py")
        # A register candidate may validate in an isolated Python process using
        # only its sealed kit, without reaching back into the host installation.
        package = ws / "_toolkit/generative_driver"
        private = package / "_toolchain"
        private.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name in ("__init__", "core", "runner", "interpret", "interface", "firmware"):
            shutil.copyfile(Path(__file__).parent / (name + ".py"), private / (name + ".py"))
        helpers = package / "resources/tools"
        helpers.mkdir(parents=True)
        shutil.copyfile(Path(core.BENCH, "tools/model_exec.py"), helpers / "model_exec.py")
        from .interface import RUNTIME
        shutil.copytree(RUNTIME, ws / "_toolkit/interface_runtime",
            ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"))
    sealed = _tree(ws)
    (ws / "INPUT_HASHES.json").write_text(json.dumps(sealed, indent=1), encoding="utf-8")
    prompt = _prompt(ws, mode)
    seal = {"schema": SEAL_SCHEMA, "front_end": front_end, "attempt": attempt, "mode": mode,
            "workspace": str(ws), "outside_run": bool(workspace_root), "schema_document": model_doc,
            "files": sealed, "input_hashes_sha256": core.sha256(ws / "INPUT_HASHES.json"),
            "prompt": prompt, "sealed_at": core.now()}
    host.mkdir(parents=True, exist_ok=True)
    with open(seal_path, "x", encoding="utf-8") as fh:
        json.dump(seal, fh, indent=1)
    if workspace_root:
        with open(pointer, "x", encoding="utf-8") as fh:
            json.dump({"schema": "interpret-attempt/1", "workspace": str(ws), "seal": seal_path.name,
                       "outputs": record.name}, fh, indent=1)
    files = {f"{_label(ws)}/{k}": v for k, v in sealed.items()}
    files[_label(ws / "INPUT_HASHES.json")] = seal["input_hashes_sha256"]
    files[_label(seal_path)] = core.sha256(seal_path)
    base = {"workspace": str(ws), "model_dir": core.label(record), "front_end": front_end,
            "attempt": attempt, "seal": str(seal_path), "seal_sha256": files[_label(seal_path)],
            "schema_document": model_doc}
    if mode == "prepare":
        return {"ok": True, "mode": "prepare", **base, "prompt_for_agent": prompt, "sealed_inputs": sorted(sealed),
                "next": "keep seal_sha256 outside the agent's reach, spawn one agent with prompt_for_agent, then call "
                        "interpret_collect with this run_dir, attempt and expected_seal_sha256", "_files": files}
    allowed = ["Read", "Write", "Edit", "Glob", "Grep", "Bash(python3 validate.py:*)",
               "Bash(python3 qualify.py:*)", "Bash(python3 image_map_check.py:*)"]
    rc, events, cost, err = _agent(ws, prompt, allowed, timeout=int(budget_min) * 60, model=model)
    kept = host / f"attempt{attempt}.transcript.json"  # beside the seal, so it is not counted as agent output
    kept.write_text(json.dumps(events), encoding="utf-8")
    files[_label(kept)] = core.sha256(kept)
    acts = _actions(events)
    for a in acts:
        core.log_event(run, "agent_action", {"attempt": attempt}, {"action": a}, actor=f"{front_end}#{attempt}")
    try:
        delivered = _deliver(run, attempt, seal)
    except (OSError, ValueError) as exc:
        delivered = {"ok": False, "reason": str(exc)}
    files.update(delivered.pop("_files", {}))
    ok = delivered.pop("ok")
    audit = _audit(events, ws, prompt)
    note = _isolation_note(AGENT_SEAL, audit)
    return {**base, "ok": ok, **delivered, "agent": cost, "actions": len(acts), "sealed_inputs": len(sealed),
            "seal_kind": "tool_allowlist", "isolation_note": note, "transcript_audit": audit,
            "_exit": 0 if ok else 1,
            "_notes": [note] + ([] if ok else [f"agent exit {rc}: {(err or '')[-200:]}"]), "_files": files}


def _read_seal(raw):
    """Parse a host seal from the bytes that are also hashed, so the seal checked is the seal used."""
    seal = json.loads(raw)
    if not (isinstance(seal, dict) and seal.get("schema") == SEAL_SCHEMA and isinstance(seal.get("files"), dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in seal["files"].items())
            and isinstance(seal.get("workspace"), str) and os.path.isabs(seal["workspace"])
            and isinstance(seal.get("input_hashes_sha256"), str)):
        raise ValueError("host seal is malformed")
    return seal


def _logged_seal(run, key):
    """The seal's hash as its prepare event logged it: the earliest successful interpret_run event that names the
    seal, so a line appended later cannot replace it. None when no such event exists. The log is a plain file the
    host writes; a caller-held expected_seal_sha256 is the stronger check."""
    try:
        with open(core.log_path(run), encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not (isinstance(ev, dict) and ev.get("tool") == "interpret_run" and ev.get("exit") == 0):
                    continue
                files = ev.get("files")
                if isinstance(files, dict) and isinstance(files.get(key), str):
                    return files[key]
    except OSError:
        return None
    return None


def _freeze(ws, record, now, keep):
    """Copy the agent's outputs into the run, once. Collecting the same outputs again is a no-op; different
    outputs are refused, because the first delivery stands."""
    expected = {k: now[k] for k in keep}
    if record.exists() or record.is_symlink():
        if record.is_symlink() or _tree(record) != expected:
            raise ValueError(f"{record.name} was already collected with different outputs; the first delivery stands")
        return
    staging = Path(tempfile.mkdtemp(prefix=f".{record.name}-", dir=record.parent))
    try:
        for k in keep:
            (staging / k).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws / k, staging / k)
        if _tree(staging) != expected or _tree(ws) != now:
            raise ValueError("workspace changed during collection; outputs were not frozen")
        staging.rename(record)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _validation(md):
    """Structural validation of an executable model, reported beside ok rather than folded into it."""
    engine, evidence = _engine()
    path = Path(md, "model.json")
    if not path.is_file():
        return {"schema": None, "validated": None, "defects": []}
    try:
        model = evidence.read_json(path, 1048576)
    except (OSError, ValueError) as exc:
        return {"schema": None, "validated": False, "defects": [{"where": "model.json", "what": str(exc)}]}
    schema = model.get("schema") if isinstance(model, dict) else None
    if schema not in engine.SCHEMAS:
        return {"schema": schema, "validated": None, "defects": [],
                "validation_note": "not an executable interface model; check it with model_validate"}
    result = engine.validate_model(model)
    return {"schema": schema, "validated": result["ok"], "defects": result["defects"]}


def _qualification(md):
    """Qualification re-run on the host, offline, over the frozen model.json and replies.json with the toolchain's
    own runtime, so the status cannot come from a file the agent wrote. The agent's own qualification/result.json
    is reported beside it as a claim, with whether the two agree."""
    engine, evidence = _engine()
    from interface_runtime.qualify import QualifyError, qualify
    claimed = _agent_qualification(md)
    model_path, replies_path = Path(md, "model.json"), Path(md, "replies.json")
    if not model_path.is_file() or not replies_path.is_file():
        return {"status": "absent", "source": "host", "reason": "no model.json and replies.json to qualify",
                "agent_reported": claimed}
    out = {"source": "host"}
    try:
        model, out["model_sha256"] = evidence.read_json_hashed(model_path, 1048576)
        replies, out["replies_sha256"] = evidence.read_json_hashed(replies_path, 4194304)
        if not (isinstance(model, dict) and model.get("schema") in engine.SCHEMAS):
            return {"status": "absent", "source": "host", "reason": "not an executable interface model",
                    "agent_reported": claimed}
        report = qualify(model, replies)
    except (OSError, ValueError, TypeError, KeyError, QualifyError) as exc:
        out.update(status="not_qualified", error=str(exc)[:500], qualified=[], not_qualified=[], uncovered=[])
    else:
        out.update(status="qualified" if report["all_supplied_qualify"] else
                   "not_qualified" if report["not_qualified"] else "uncovered",
                   qualified=report["qualified"], not_qualified=report["not_qualified"], uncovered=report["uncovered"])
    out["agent_reported"] = claimed
    out["agent_agrees"] = claimed.get("status") == out["status"]
    return out


def _agent_qualification(md):
    """The agent's own qualification, as qualify.py left it in qualification/result.json: a claim, never the
    status. Stale when the model or the replies changed after it ran, by hash when it recorded them."""
    path = Path(md, "qualification", "result.json")
    if not path.is_file():
        return {"status": "absent"}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return {"status": "not_qualified", "error": f"unreadable result.json: {exc}"}
    lists = {k: sorted(str(n) for n in report.get(k) or []) for k in ("qualified", "not_qualified", "uncovered")}
    status = ("not_qualified" if report.get("error") or lists["not_qualified"] else
              "qualified" if report.get("all_supplied_qualify") is True else "uncovered")
    newer = {n for n in ("model.json", "replies.json")
             if Path(md, n).is_file() and Path(md, n).stat().st_mtime_ns > path.stat().st_mtime_ns}
    for name in ("model", "replies"):
        recorded, current = report.get(f"{name}_sha256"), Path(md, f"{name}.json")
        if recorded and (not current.is_file() or recorded != core.sha256(current)):
            newer.add(f"{name}.json")
    out = {"status": status, **lists, "stale": bool(newer)}
    if newer:
        out["changed_after_qualification"] = sorted(newer)
    if report.get("error"):
        out["error"] = str(report["error"])[:500]
    return out


def _deliver(run, attempt, seal):
    """Verify the workspace against the host seal and freeze what the agent wrote into the run.

    ok means the seal held, the outputs were frozen and a model with a supported schema is present; whether
    it validates or qualifies is reported beside ok, not folded into it."""
    ws, record = Path(seal["workspace"]), Path(run, "interpret", f"attempt{attempt}")
    if not ws.is_dir():
        return {"ok": False, "reason": f"sealed workspace is missing: {ws}"}
    now, sealed = _tree(ws), seal["files"]
    tampered = sorted(k for k, v in sealed.items() if k not in CONTROL and now.get(k) != v)
    if core.sha256(ws / "INPUT_HASHES.json") != seal["input_hashes_sha256"]:
        tampered.append("INPUT_HASHES.json")
    control = sorted(k for k in CONTROL if now.get(k) != sealed.get(k))
    wrote = sorted(k for k in now if k not in sealed and k != "INPUT_HASHES.json")
    out = {"workspace": str(ws), "model_dir": core.label(record), "wrote": wrote,
           "sealed_inputs_unchanged": not tampered, "tampered": tampered, "control_changed": control,
           "frozen_copy": False}
    if tampered:
        return {**out, "ok": False, "reason": f"sealed inputs changed during the run: {tampered}"}
    keep = sorted(k for k in wrote + control if k in now)
    if _canon(ws) != _canon(record):
        _freeze(ws, record, now, keep)
        out["frozen_copy"] = True
    out.update(_validation(record))
    out["qualification"] = _qualification(record)
    out["ok"] = _output_present(record)
    if not out["ok"]:
        out["reason"] = "no model.json with a supported schema"
    out["_files"] = {_label(record / k): now[k] for k in keep}
    return out


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _expand(token):
    token = token.rstrip(".,:;!?")
    for prefix in ("${HOME}", "$HOME", "~"):
        if token.startswith(prefix + "/"):
            return os.path.expanduser("~") + token[len(prefix):]
    return token


def _user_text(ev):
    """The text of a user turn, or None for a turn that carries tool results."""
    message = ev.get("message") if ev.get("type") == "user" else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _injected(events):
    """Files the harness put into the agent's context before it acted: instructions (CLAUDE.md, memory),
    attached files and skills, and any 'Contents of <file>' reminder."""
    found, seen = [], set()

    def add(path, kind):
        if isinstance(path, str) and path and path not in seen:
            seen.add(path)
            found.append({"path": path, "kind": kind})
    for ev in events:
        att = ev.get("attachment")
        if isinstance(att, dict):
            kind = str(att.get("type"))
            for f in att.get("files") or [] if kind == "instructions" or "memory" in kind.lower() else []:
                if isinstance(f, dict):
                    add(f.get("path"), f"{kind}:{f.get('type')}" if f.get("type") else kind)
            if kind in ("file", "directory", "pdf_reference", "compact_file_reference") or "memory" in kind.lower():
                for key in ("path", "filename", "filePath"):
                    add(att.get(key), kind)
            for s in att.get("skills") or [] if kind == "invoked_skills" else []:
                if isinstance(s, dict):
                    add(s.get("path"), "skill")
            for text in _strings([att.get("rendered"), att.get("systemPrompt")]):
                for path in INJECTED_TEXT.findall(text):
                    add(path, "reminder")
        text = _user_text(ev)
        for path in INJECTED_TEXT.findall(text or ""):
            add(path, "reminder")
    return found


def _audit(events, ws, prompt=None):
    """What a transcript shows the agent asked its tools to do. Every tool input is searched in full; text the
    agent wrote or searched for is data, not a location. This shows what was asked for, not what the tools could
    have reached."""
    calls, outside, network, device, shell_outside, delegated, seen = (
        collections.Counter(), [], [], [], [], [], set())
    for ev, name, inp in _tool_uses(events):
        calls[name] += 1
        cwd = ev.get("cwd") if isinstance(ev.get("cwd"), str) else None
        texts = list(_strings({k: v for k, v in inp.items() if k not in DATA_FIELDS.get(name, ())}))
        paths = sorted({_expand(m) for t in texts for m in PATH_TOKEN.findall(t)} - {""})
        inside = [p for p in paths if _under(p, ws)]
        for p in paths:
            if p in inside or BENIGN_DEVICES.fullmatch(p):
                continue
            if (name, p) not in seen:
                seen.add((name, p))
                outside.append({"tool": name, "path": p})
            if name != "Bash" and _canon(p).startswith("/dev/"):  # a shell command is judged whole below
                device.append({"tool": name, "input": p, "terms": ["/dev/"]})
        pattern = str(inp.get("pattern", ""))
        if (name in ("Grep", "Glob") and not inp.get("path") and cwd and not _under(cwd, ws)
                and not (name == "Glob" and pattern.startswith(("/", "~")))):
            outside.append({"tool": name, "path": cwd, "why": "no path given: it searched the harness's working directory"})
        if name == "Bash":
            command = str(inp.get("command", ""))
            terms = [t for t in DEVICE_TERMS if t in BENIGN_DEVICES.sub("", command.lower())]
            if terms:
                device.append({"tool": name, "input": command[:2000], "terms": terms})
            if NETWORK_COMMAND.search(command):
                network.append({"tool": name, "input": command[:2000]})
            if not inside and cwd and not _under(cwd, ws):
                shell_outside.append({"command": command[:2000], "cwd": cwd})
        if name in ("WebFetch", "WebSearch") or name.startswith("mcp__"):
            network.append({"tool": name, "input": json.dumps(inp, default=str)[:300]})
        if name in ("Task", "Agent"):
            delegated.append({"tool": name, "input": json.dumps(inp, default=str)[:300]})
    first = next((t for t in map(_user_text, events) if t is not None), None)
    delivered = extra = None
    if prompt and first is not None:
        p, u = " ".join(prompt.split()), " ".join(first.split())
        delivered = p in u
        extra = " ".join(u.replace(p, " ", 1).split())[:1000] if delivered else None
    return {"tool_calls": dict(sorted(calls.items())), "outside_workspace_paths": outside,
            "shell_outside_workspace": shell_outside, "network_tools_used": network,
            "device_access_suspects": device, "delegated": delegated, "injected_context": _injected(events),
            "harness_cwd": sorted({ev["cwd"] for ev in events if isinstance(ev.get("cwd"), str)}),
            "prompt_delivered": delivered, "prompt_extra": extra or None}


def _read_transcript(transcript, ws, prompt):
    """Parse a JSONL transcript, gzip-compressed when it ends in .gz. An audit that parsed nothing is an error, and
    one that skipped lines says so: zero findings from lines never read are not a clean audit."""
    path = Path(os.path.expanduser(str(transcript)))
    path = path if path.is_absolute() else Path(core.BENCH, path)
    events, unparsed, lines = [], 0, 0
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                lines += 1
                try:
                    ev = json.loads(line)
                except ValueError:
                    ev = None
                if isinstance(ev, dict):
                    events.append(ev)
                else:
                    unparsed += 1
    except (OSError, EOFError) as exc:
        return {"transcript": str(path), "error": f"{type(exc).__name__}: {exc}"}, []
    head = {"transcript": str(path), "sha256": core.sha256(path), "events": len(events), "lines": lines,
            "unparsed_lines": unparsed, "complete": bool(events) and not unparsed}
    if not events:
        return {**head, "error": f"no transcript event could be parsed from {lines} nonblank lines; nothing was audited"}, []
    return {**head, **_audit(events, ws, prompt)}, events


def _isolation_note(base, audit):
    if audit is None:
        return base + "; no transcript was audited, so what the agent did outside the workspace is unknown"
    if audit.get("error"):
        return base + f"; the transcript could not be read ({audit['error']})"
    counts = (f"{sum(audit['tool_calls'].values())} tool calls, {len(audit['outside_workspace_paths'])} paths "
              f"outside the workspace, {len(audit['shell_outside_workspace'])} shell commands run outside it, "
              f"{len(audit['network_tools_used'])} network uses, {len(audit['device_access_suspects'])} "
              f"device-access suspects, {len(audit['delegated'])} delegations")
    injected = [i["path"] for i in audit["injected_context"]]
    return (base + "; transcript audited: " + counts
            + (f"; {audit['unparsed_lines']} of {audit['lines']} transcript lines could not be parsed, so the audit is "
               f"incomplete" if audit.get("unparsed_lines") else "")
            + (f"; context the harness injected: {', '.join(injected)}" if injected else "")
            + ("; the prepared prompt does not appear verbatim in the transcript"
               if audit.get("prompt_delivered") is False else "")
            + (f"; the first message carried {len(audit['prompt_extra'])} characters beyond the prepared prompt"
               if audit.get("prompt_extra") else "")
            + ". The audit shows what the agent asked its tools for, not what they could reach")


@core.tool("interpret_collect")
def interpret_collect(run_dir, attempt=1, agent_summary=None, seal_kind="instruction", transcript=None,
                      expected_seal_sha256=None, **_):
    """Take delivery of a model an externally spawned agent wrote into a prepared workspace.

    Verifies the workspace against the host seal written at prepare time, not against the workspace's own
    INPUT_HASHES.json alone, which the agent could rewrite, and the host seal against `expected_seal_sha256`,
    the seal_sha256 that prepare returned and the caller kept. Without that pin the seal is checked against the
    earliest successful prepare event that logged it, and refused when no such event exists; the log is a plain
    file, so the pin is the stronger check. When the workspace lies outside the run, copies what the agent wrote
    into runs/<run>/interpret/attempt<N>/ once. The model's structural validation and a qualification the host
    re-runs offline over the frozen model and replies are reported beside ok, with the agent's own qualification
    result as a claim: ok means the seal held, the outputs were frozen and a model is present, not that the
    model is right.

    `seal_kind` says how the agent was bounded: 'instruction' (a spawned agent told to stay inside) or
    'enforced' (a harness that restricted its tools or sandboxed it). `transcript` is the agent's Claude Code
    transcript (JSONL); when given, every tool call becomes an agent_action event and the calls are audited for
    paths outside the workspace, network use, device access and injected context. Nothing here can prove what
    an instruction-sealed agent did not do.
    """
    if seal_kind not in SEAL_KINDS:
        return {"ok": False, "reason": f"seal_kind must be one of {sorted(SEAL_KINDS)}", "_exit": 1}
    attempt = _attempt_no(attempt)
    run = core.resolve_run(run_dir)
    seal_path = Path(run, "interpret", f"attempt{attempt}.seal.json")
    base = {"attempt": attempt, "seal_kind": seal_kind}
    if not seal_path.is_file() or seal_path.is_symlink():
        return {**base, "ok": False, "reason": f"attempt not prepared: no host seal interpret/{seal_path.name}",
                "isolation_note": _isolation_note(SEAL_KINDS[seal_kind], None), "_exit": 1}
    raw = seal_path.read_bytes()
    try:
        seal = _read_seal(raw)
    except ValueError as exc:
        return {**base, "ok": False, "reason": f"host seal unreadable: {exc}", "_exit": 1}
    actual, logged = hashlib.sha256(raw).hexdigest(), _logged_seal(run, _label(seal_path))
    if expected_seal_sha256 is not None and expected_seal_sha256 != actual:
        return {**base, "ok": False, "reason": "host seal differs from the expected_seal_sha256 prepare returned",
                "_exit": 1}
    if expected_seal_sha256 is None and logged is None:
        return {**base, "ok": False, "reason": "no successful prepare event logged this host seal; pass the "
                "seal_sha256 prepare returned as expected_seal_sha256", "_exit": 1}
    if expected_seal_sha256 is None and logged != actual:
        return {**base, "ok": False, "reason": "host seal differs from the hash its prepare event logged",
                "_exit": 1}
    base["seal_pinned"] = expected_seal_sha256 is not None
    files, audit = {}, None
    if transcript:
        audit, events = _read_transcript(transcript, seal["workspace"], seal.get("prompt"))
        if audit.get("sha256"):
            files[_label(audit["transcript"])] = audit["sha256"]
        for a in _actions(events):
            core.log_event(run, "agent_action", {"attempt": attempt}, {"action": a},
                           actor=f"{seal.get('front_end', 'interpret')}#{attempt}")
    try:
        delivered = _deliver(run, attempt, seal)
    except (OSError, ValueError) as exc:
        delivered = {"ok": False, "reason": str(exc)}
    files.update(delivered.pop("_files", {}))
    ok = delivered.pop("ok")
    note = _isolation_note(SEAL_KINDS[seal_kind], audit)
    if agent_summary:
        core.log_event(run, "agent_report", {"attempt": attempt}, {"summary": str(agent_summary)[:1500]},
                       actor=f"interpret#{attempt}")
    return {**base, "ok": ok, **delivered, "seal_logged": logged == actual, "isolation_note": note,
            **({"transcript_audit": audit} if audit is not None else {}),
            "_exit": 0 if ok else 1,
            "_notes": [note] + ([delivered["reason"]] if delivered.get("reason") else []), "_files": files}

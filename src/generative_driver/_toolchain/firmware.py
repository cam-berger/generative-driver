"""Prepare and collect an instruction-isolated, binary-only interpretation handoff.

The host seal detects changed supplied inputs; it cannot establish what an agent
read elsewhere or prove that a recovered interface is semantically correct.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from . import core


SKILL = Path(core.BENCH) / "toolchain/skills/interpret-firmware-binary"
REQUIRED_OUTPUTS = ["model.json", "NOTES.md", "PROBES.json"]
EFFECT_CLASSES = {"read_only", "state_change", "physical_action", "destructive", "unknown"}
BASE_INPUTS = {"image.bin", "BRIEF.md", "OUTPUT_SCHEMA.json", "ExportDecomp.java", "TOOLING.md"}
SCOPE = "Structural contract and local evidence-file integrity only; no semantic or live-device verification."
ISOLATION = "Instruction-level isolation, not an OS sandbox; hashes cannot disprove external access."


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON: {value}")))


def _path(value, label, base=None, absolute=False):
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError(f"{label} must be a nonempty path")
    raw = os.fspath(value)
    if "\x00" in raw or ".." in Path(raw).parts:
        raise ValueError(f"unsafe {label} path")
    path = Path(raw)
    if absolute and not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    if not path.is_absolute():
        path = Path(base or core.BENCH) / path
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    return path.resolve()


def _run(value):
    # Validate before core.tool can create an event log at an unchecked path.
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError("run_dir must be a nonempty path")
    _path(value, "run_dir")
    return _path(core.resolve_run(os.fspath(value)), "run_dir")


def _attempt(run, attempt):
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    directory = run / "firmware" / f"attempt{attempt}"
    if (run / "firmware").is_symlink() or directory.is_symlink():
        raise ValueError("unsafe attempt directory")
    return directory


def _relative_file(root, name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise ValueError("evidence/input path must be a relative file path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError(f"unsafe relative path: {name!r}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink not allowed: {name!r}")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"missing or unsafe file: {name!r}")
    return current


def _tree(root):
    """Hash every regular file, without the display-oriented core.sha_tree cap."""
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise ValueError("symlink directory in workspace")
        for name in files:
            relative = (Path(directory) / name).relative_to(root).as_posix()
            result[relative] = _hash(_relative_file(root, relative))
    return dict(sorted(result.items()))


def _failure(exc):
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "_exit": 1}


def firmware_prepare(run_dir, artifact, expected_sha256, attempt=1, ghidra_path=None, java_home=None):
    """Prepare a fresh binary-only workspace; return a neutral prompt for a separate agent.

    No interpreter is launched. Optional installed tool directories must be
    absolute. Original artifact names and paths remain in host-only provenance.
    """
    try:
        run = _run(run_dir)
        destination = _attempt(run, attempt)
        if destination.exists():
            raise ValueError("attempt already exists; choose a fresh attempt instead of resealing")
        source = _path(artifact, "artifact")
        if not source.is_file():
            raise ValueError("artifact must be an existing regular file")
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("expected_sha256 must be 64 hexadecimal characters")
        digest = expected_sha256.lower()
        if _hash(source) != digest:
            raise ValueError("artifact SHA-256 does not match expected_sha256")
        installed = {}
        for label, value in (("ghidra_path", ghidra_path), ("java_home", java_home)):
            if value is not None:
                path = _path(value, label, absolute=True)
                if not path.is_dir():
                    raise ValueError(f"{label} must name an installed tool directory")
                installed[label] = str(path)
        for name in ("SKILL.md", "OUTPUT_SCHEMA.json", "ExportDecomp.java", "TOOLING.md"):
            if not (SKILL / name).is_file():
                raise ValueError(f"missing generic skill asset: {name}")
    except (OSError, TypeError, ValueError) as exc:
        return _failure(exc)
    result = _prepare(run_dir=str(run), artifact=str(source), expected_sha256=digest,
                      attempt=attempt, installed=installed)
    result.setdefault("ok", False)
    return result


@core.tool("firmware_prepare")
def _prepare(run_dir, artifact, expected_sha256, attempt, installed):
    host = _attempt(Path(run_dir), attempt)
    host.mkdir(parents=True, exist_ok=False)
    ws = Path(tempfile.mkdtemp(prefix="firmware-interpret-")).resolve()
    # Keep an incomplete attempt on failure: it must never be silently reused.
    _write_json(host / "provenance.json", {
        "schema": "firmware-handoff-provenance/1", "artifact": artifact,
        "expected_sha256": expected_sha256, "workspace": str(ws), "attempt": attempt,
        "installed_tools": installed, "isolation": ISOLATION,
        "live_image_equivalence": "unverified by this handoff",
    })
    shutil.copyfile(artifact, ws / "image.bin")
    if _hash(ws / "image.bin") != expected_sha256:
        raise ValueError("artifact changed while copying; preparation failed")
    for source, target in (("SKILL.md", "BRIEF.md"), ("OUTPUT_SCHEMA.json", "OUTPUT_SCHEMA.json"),
                           ("ExportDecomp.java", "ExportDecomp.java"), ("TOOLING.md", "TOOLING.md")):
        shutil.copyfile(SKILL / source, ws / target)
    # Standard-library helpers the tooling guide refers to; sealed with the rest of the inputs.
    for helper in ("image_map_check.py", "ghidra_run.py"):
        source = Path(core.BENCH, "tools", "workspace", helper)
        if source.is_file():
            shutil.copyfile(source, ws / helper)
    if installed:
        with (ws / "TOOLING.md").open("a", encoding="utf-8") as stream:
            stream.write("\nAvailable generic installed tools (tools and their local documentation only):\n")
            for name, path in sorted(installed.items()):
                stream.write(f"- {name}: {json.dumps(path)}\n")
    _write_json(ws / "INPUT_HASHES.json", _tree(ws))
    sealed = _tree(ws)
    seal_path = host / "input-seal.json"
    _write_json(seal_path, {"schema": "firmware-input-seal/1", "workspace": str(ws), "files": sealed})
    prompt = (
        "Start in the supplied neutral workspace. Read BRIEF.md and OUTPUT_SCHEMA.json. "
        "Infer an externally usable interface from image.bin and generic installed local tools only. "
        "Do not read parent directories, other workspaces, conversations, repositories, or source; "
        "do not use network resources or access hardware. Follow TOOLING.md before using Ghidra. "
        "Preserve every supplied input and keep all analysis, exports, caches and logs here. "
        "Write model.json, NOTES.md and PROBES.json with concrete binary evidence and explicit uncertainties. "
        "Do not execute proposed probes. Isolation is by instruction, not an OS sandbox."
    )
    return {"ok": True, "workspace": str(ws), "prompt_for_agent": prompt,
            "neutral_inputs": sorted(sealed), "sealed_inputs": sorted(sealed),
            "required_outputs": list(REQUIRED_OUTPUTS), "seal_path": str(seal_path),
            "provenance_path": str(host / "provenance.json"), "attempt": attempt,
            "isolation": ISOLATION, "_files": {str(seal_path): _hash(seal_path)}}


def _location(evidence):
    for key in ("address", "file_offset", "offset"):
        value = evidence.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return True
        if isinstance(value, str) and re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", value):
            return True
    return False


def _validate_model(ws):
    defects, evidence_files = [], set()
    def defect(where, what):
        defects.append({"where": where, "what": what})
    try:
        model = _read_json(_relative_file(ws, "model.json"))
    except (ValueError, OSError) as exc:
        return None, [{"where": "model.json", "what": str(exc)}], set()
    if not isinstance(model, dict):
        return None, [{"where": "model.json", "what": "must be an object"}], set()
    if model.get("schema") != "interface-model/2-experimental":
        defect("schema", "expected interface-model/2-experimental")
    for key in ("device", "channel", "limits", "safety", "provenance"):
        if not isinstance(model.get(key), dict):
            defect(key, "must be an object; unknown facts may be stated explicitly")
    uncertainties = model.get("uncertainties")
    if not isinstance(uncertainties, list) or any(not isinstance(item, str) or not item.strip() for item in uncertainties):
        defect("uncertainties", "must be a list of nonempty strings")
    operations = model.get("operations")
    if not isinstance(operations, list) or not operations:
        defect("operations", "must be a nonempty operation list")
        operations = []
    ids = set()
    for index, operation in enumerate(operations):
        where = f"operations[{index}]"
        if not isinstance(operation, dict):
            defect(where, "must be an object")
            continue
        operation_id = operation.get("id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            defect(where + ".id", "must be a nonempty string")
        elif operation_id in ids:
            defect(where + ".id", "operation IDs must be unique")
        else:
            ids.add(operation_id)
        effect = operation.get("effect_class")
        if not isinstance(effect, str) or effect not in EFFECT_CLASSES:
            defect(where + ".effect_class", "invalid effect class")
        for key in ("request", "response_interpretation"):
            if not isinstance(operation.get(key), dict) or not operation[key]:
                defect(where + "." + key, "must be a nonempty object")
        evidence = operation.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            defect(where + ".evidence", "must cite concrete binary evidence")
            continue
        for number, item in enumerate(evidence):
            entry = f"{where}.evidence[{number}]"
            if not isinstance(item, dict) or not _location(item):
                defect(entry, "requires a concrete address or file offset and an export")
                continue
            try:
                export = _relative_file(ws, item.get("export"))
                if export.stat().st_size == 0:
                    raise ValueError("evidence export is empty")
                evidence_files.add(item["export"])
            except (OSError, ValueError) as exc:
                defect(entry + ".export", str(exc))
    # Validate supplementary explicitly named exports too, wherever present.
    def walk(value, where):
        if isinstance(value, dict):
            for key, item in value.items():
                location = f"{where}.{key}"
                if key == "export":
                    try:
                        _relative_file(ws, item)
                        evidence_files.add(item)
                    except (OSError, ValueError) as exc:
                        defect(location, str(exc))
                else:
                    walk(item, location)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{where}[{index}]")
    walk(model, "model")
    return model, defects, evidence_files


def firmware_validate(run_dir, model_dir):
    """Check the experimental operation-list structure and concrete local evidence.

    This checks no executable conversion module and authorizes no live operation.
    """
    try:
        run = _run(run_dir)
        ws = _path(model_dir, "model_dir")
        if not ws.is_dir():
            raise ValueError("model_dir must be an existing directory")
    except (OSError, TypeError, ValueError) as exc:
        return _failure(exc)
    result = _validate(run_dir=str(run), model_dir=str(ws))
    result.setdefault("ok", False)
    return result


@core.tool("firmware_validate")
def _validate(run_dir, model_dir):
    model, defects, evidence = _validate_model(Path(model_dir))
    return {"ok": not defects, "defects": defects, "model_dir": model_dir,
            "schema": model.get("schema") if model else None,
            "operations": len(model.get("operations", [])) if model and isinstance(model.get("operations"), list) else 0,
            "evidence_files": sorted(evidence), "validation_scope": SCOPE,
            "execution_authorized": False, "_exit": 1 if defects else 0}


def _sealed_workspace(host):
    seal = _read_json(_relative_file(host, "input-seal.json"))
    if not isinstance(seal, dict) or seal.get("schema") != "firmware-input-seal/1":
        raise ValueError("missing or invalid input seal")
    ws = _path(seal.get("workspace"), "sealed workspace", absolute=True)
    if not ws.is_dir():
        raise ValueError("sealed workspace is missing")
    files = seal.get("files")
    if not isinstance(files, dict) or not BASE_INPUTS.union({"INPUT_HASHES.json"}).issubset(files):
        raise ValueError("input seal has an incomplete file list")
    for name, digest in files.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid input digest: {name}")
        if _hash(_relative_file(ws, name)) != digest:
            raise ValueError(f"sealed input changed: {name}")
    manifest = _read_json(ws / "INPUT_HASHES.json")
    if manifest != {name: digest for name, digest in files.items() if name != "INPUT_HASHES.json"}:
        raise ValueError("input manifest and host seal do not contain the exact same input list")
    return ws, files


def firmware_collect(run_dir, attempt=1):
    """Verify the full input seal, validate outputs, and freeze a fresh first pass."""
    try:
        run = _run(run_dir)
        host = _attempt(run, attempt)
        if not host.is_dir():
            raise ValueError("prepared attempt and input seal are missing")
        if (host / "first-pass").exists() or (host / "first-pass").is_symlink():
            raise ValueError("first-pass already exists; it must not be overwritten")
    except (OSError, TypeError, ValueError) as exc:
        return _failure(exc)
    result = _collect(run_dir=str(run), attempt=attempt)
    result.setdefault("ok", False)
    return result


@core.tool("firmware_collect")
def _collect(run_dir, attempt):
    host = _attempt(Path(run_dir), attempt)
    ws, sealed = _sealed_workspace(host)
    for name in REQUIRED_OUTPUTS:
        _relative_file(ws, name)
    # Capture before parsing, so the snapshot must contain the bytes validated.
    before = _tree(ws)
    model, defects, evidence = _validate_model(ws)
    probes = _read_json(ws / "PROBES.json")
    if not isinstance(probes, list):
        defects.append({"where": "PROBES.json", "what": "must be an ordered list; use [] when none justified"})
    else:
        operations = model.get("operations", []) if model else []
        by_id = {op.get("id"): op for op in operations if isinstance(op, dict) and isinstance(op.get("id"), str)} if isinstance(operations, list) else {}
        for index, probe in enumerate(probes):
            operation = by_id.get(probe.get("operation_id")) if isinstance(probe, dict) and isinstance(probe.get("operation_id"), str) else None
            if not operation or operation.get("effect_class") != "read_only" or probe.get("request") != operation.get("request"):
                defects.append({"where": f"PROBES.json[{index}]", "what": "must reference a read_only operation with its exact modeled request"})
    if defects:
        return {"ok": False, "defects": defects, "validation_scope": SCOPE, "_exit": 1}
    staging = Path(tempfile.mkdtemp(prefix=".first-pass-", dir=host))
    try:
        for name in before:
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_relative_file(ws, name), destination)
        if _tree(staging) != before or _tree(ws) != before:
            raise ValueError("workspace changed during collection; first pass was not frozen")
        # Recheck sealed files after copying, including INPUT_HASHES.json itself.
        if any(before.get(name) != digest for name, digest in sealed.items()):
            raise ValueError("sealed input changed during collection")
        first_pass = host / "first-pass"
        if first_pass.exists() or first_pass.is_symlink():
            raise ValueError("first-pass already exists")
        staging.rename(first_pass)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    manifest_path = host / "first-pass-hashes.json"
    _write_json(manifest_path, {"schema": "firmware-first-pass/1", "files": before,
        "input_seal_sha256": _hash(host / "input-seal.json"), "validation_scope": SCOPE,
        "isolation": ISOLATION, "execution_authorized": False})
    return {"ok": True, "workspace": str(ws), "first_pass": str(first_pass),
            "model_dir": str(first_pass), "model_path": str(first_pass / "model.json"),
            "manifest_path": str(manifest_path), "operations": len(model["operations"]),
            "uncertainties": model["uncertainties"], "evidence_files": sorted(evidence),
            "sealed_inputs_checked": len(sealed), "frozen_files": len(before),
            "validation_scope": SCOPE, "isolation": ISOLATION, "execution_authorized": False,
            "_files": {str(manifest_path): _hash(manifest_path)}}

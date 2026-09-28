#!/usr/bin/env python3
"""Run Ghidra's headless analyzer on one raw image and say, from evidence, whether it worked.

Standard library only, so this file can be copied on its own into an offline worker. The toolchain tool
`ghidra_run` in bench/toolchain/tc/ghidra.py loads this module by path and adds the event log, so the two
entry points share one implementation rather than two copies that drift.

Why this exists. In an earlier offline trial the candidate agents lost work to four avoidable
faults recorded in that trial's runtime audit: explicit loader selectors that Ghidra rejected
("Invalid loader name specified"), a shell wrapper that masked the failure with `|| true` and then
reported exit 0 beside an empty export directory, output previews (`... | head`) under `pipefail` that
turned a completed analysis into exit 141, and a Log4j hostname stack trace on stderr that had to be told
apart from real errors on every call. This helper runs the analyzer with no shell and no pipeline, keeps
its true exit status, classifies the log markers, reports the hostname diagnostic in a field of its own,
and verifies the exporter's files before it says ok. The verdict `ok` is true only when the exit status is
0, the log says "Import succeeded", and the exports verify.

Procedural facts and where each was read, in the local Ghidra 12.1.3 installation:

  support/analyzeHeadless (lines 22-25 and 43): the launcher builds a JVM argument list and appends the
  environment variables GHIDRA_JAVA_OPTIONS and GHIDRA_HEADLESS_JAVA_OPTIONS to it, then hands the list
  to support/launch.sh as one string.

  support/launch.sh: the usage text says the vmarg list is a pass-thru of "-D" options in which "Spaces
  are not supported", and the foreground branch expands the list unquoted before `-cp ... ghidra.Ghidra`,
  so a workspace path may not contain whitespace. The "Identify java command" block takes `java` from PATH
  before it consults JAVA_HOME, so this helper puts <java_home>/bin first on PATH. LaunchSupport.jar runs
  in a separate JVM before those options apply.

  support/launch.properties (the commented blocks near the end): the settings directory is chosen by the
  property application.settingsdir, then XDG_CONFIG_HOME, then a platform default; the cache directory by
  application.cachedir, then XDG_CACHE_HOME; the temporary directory by application.tempdir, then
  java.io.tmpdir. Overridden values must be absolute paths.

  support/analyzeHeadlessREADME.md, Usage: -import, -overwrite (an existing project file with the same
  name is replaced instead of skipped), -processor <languageID> (exact case, from the .ldefs files),
  -noanalysis, -scriptPath, -preScript, -postScript <ScriptName.ext> [args], -log, -scriptlog,
  -loader <name> and -loader-<argument> <value>. The -loader section lists `-loader BinaryLoader` with
  `-loader-baseAddr` and notes that full Java package loader paths are no longer recognised; footnote 1
  says the base address is `[space:]offset` with a hexadecimal offset and no leading 0x. This helper
  sends that documented form; on the local installation the loader logged "Successfully applied" for
  the offset spelled with and without a 0x prefix, and a full run then listed its first function at the mapped base.

Measured on the local installation, 2026-09-18: `-loader BinaryLoader` and no `-loader` both import a
raw image as "Raw Binary" with exit 0; `ghidra.app.util.opinion.BinaryLoader`, `Binary Loader` and
`Raw Binary` each end in exit 1 with `Invalid loader name specified` on stderr before any import.

The exporter (ExportDecomp.java in the interpret-firmware-binary skill) writes functions.tsv with the
header address/name/decompilation_complete, one function_<address>.c per function, and disassembly.txt.
"""
import argparse
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time

POSTSCRIPT = "ExportDecomp.java"
PROJECT_NAME = "analysis"
WORKSPACE_DIRS = ("settings", "cache", "tmp", "home", "projects", "logs")

# Which `-loader` spellings the installed headless analyzer accepts, measured on the local installation
# by importing a synthetic Thumb image with -noanalysis (bench/toolchain/tests/test_ghidra.py repeats the
# accepted case). The display label Ghidra prints afterwards ("Using Loader: Raw Binary") and the Java
# class name are not selectors. The README's -loader section documents the accepted spelling.
LOADER_SELECTORS = {
    "ghidra": "12.1.3_PUBLIC",
    "probed": "2026-09-18",
    "accepted": ["BinaryLoader"],
    "rejected": ["ghidra.app.util.opinion.BinaryLoader", "Binary Loader", "Raw Binary"],
    "omitted": "with no -loader Ghidra selects Raw Binary for a raw image and applies -loader-baseAddr",
    "documented_in": "support/analyzeHeadlessREADME.md, section '-loader <desired loader name>'",
}

MARKERS = {
    "import_succeeded": "Import succeeded",
    "analysis_succeeded": "Analysis succeeded",
    "post_analysis_succeeded": "Post-analysis succeeded",
    "save_succeeded": "Save succeeded",
    "script_error": "SCRIPT ERROR",
    "invalid_loader": "Invalid loader name",
    "import_failed": "Import failed",
}
HOSTNAME_START = "Could not determine local host name"
HOSTNAME_EXCEPTION = "java.net.UnknownHostException"
JAVA_VERSION_RE = re.compile(r"^(openjdk|java) version |^(OpenJDK|Java\(TM\)|Java HotSpot)")
REPORT_CAP = 200
STDERR_CAP = 200


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _abs(path):
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def parse_base_address(value):
    """An int, or a hexadecimal string with or without 0x. Strings are always hexadecimal, because that
    is the form the README documents for -loader-baseAddr."""
    if isinstance(value, bool):
        raise ValueError("base_address must be an integer or a hexadecimal string")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        if not re.fullmatch(r"[0-9a-f]+", text or ""):
            raise ValueError(f"base_address is not hexadecimal: {value!r}")
        number = int(text, 16)
    if number < 0 or number > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"base_address out of range: {value!r}")
    return number


def format_base_address(number):
    """README footnote 1: hexadecimal offset, no leading 0x."""
    return "%08x" % number


def build_command(ghidra_path, workspace, image, base_address, processor, loader, prescripts,
                  script_path, export_dir, noanalysis, project_name=PROJECT_NAME):
    """The analyzeHeadless argument vector, every path absolute. No shell is involved anywhere."""
    logs = os.path.join(workspace, "logs")
    argv = [os.path.join(ghidra_path, "support", "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless"),
            os.path.join(workspace, "projects"), project_name,
            "-import", image, "-overwrite",
            "-processor", processor]
    if loader:
        argv += ["-loader", loader]
    argv += ["-loader-baseAddr", format_base_address(base_address)]
    if noanalysis:
        argv.append("-noanalysis")
    if script_path:
        argv += ["-scriptPath", script_path]
    for entry in prescripts or []:
        parts = [entry] if isinstance(entry, str) else list(entry)
        argv += ["-preScript", *[str(p) for p in parts]]
    if script_path:
        argv += ["-postScript", POSTSCRIPT, export_dir]
    argv += ["-log", os.path.join(logs, "application.log"),
             "-scriptlog", os.path.join(logs, "script.log")]
    return argv


def build_env(workspace, java_home, base_env=None):
    """Workspace-local settings, cache, temporary directory and user.home through the mechanism the
    launcher documents (GHIDRA_HEADLESS_JAVA_OPTIONS), with the XDG fallbacks pointed at the same places
    so the pre-launch LaunchSupport JVM, which does not receive those options, agrees. Inherited JVM
    option variables are dropped so the run is defined by this helper alone; the result records that."""
    env = dict(os.environ if base_env is None else base_env)
    dropped = [k for k in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
                           "GHIDRA_JAVA_OPTIONS", "GHIDRA_HEADLESS_JAVA_OPTIONS") if env.pop(k, None) is not None]
    dirs = {name: os.path.join(workspace, name) for name in WORKSPACE_DIRS}
    env["JAVA_HOME"] = java_home
    env["PATH"] = os.path.join(java_home, "bin") + os.pathsep + env.get("PATH", "")
    env["GHIDRA_HEADLESS_JAVA_OPTIONS"] = " ".join([
        "-Duser.home=" + dirs["home"],
        "-Djava.io.tmpdir=" + dirs["tmp"],
        "-Dapplication.tempdir=" + dirs["tmp"],
        "-Dapplication.settingsdir=" + dirs["settings"],
        "-Dapplication.cachedir=" + dirs["cache"],
    ])
    env["XDG_CONFIG_HOME"] = dirs["settings"]
    env["XDG_CACHE_HOME"] = dirs["cache"]
    return env, dirs, dropped


def classify_markers(text):
    return {key: (needle in text) for key, needle in MARKERS.items()}


def report_lines(texts):
    """Every distinct REPORT line, in order of first appearance, with the log decoration trimmed."""
    seen, out = set(), []
    for text in texts:
        for line in text.splitlines():
            if "REPORT:" not in line:
                continue
            body = line[line.index("REPORT:"):].strip()
            body = re.sub(r"\s*\(HeadlessAnalyzer\)\s*$", "", body)
            if body not in seen:
                seen.add(body)
                out.append(body)
    return out[:REPORT_CAP], len(out)


def _first(pattern, texts):
    for text in texts:
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def split_stderr(text):
    """Three bins: the JVM's -showversion banner, the allowlisted Log4j hostname block, and everything
    else. Only the hostname block is treated as a known nonfatal diagnostic; the rest is returned as is."""
    version, hostname, other = [], [], []
    in_block = False
    for line in text.splitlines():
        if HOSTNAME_START in line:
            in_block = True
            hostname.append(line)
            continue
        if in_block and (HOSTNAME_EXCEPTION in line or line.startswith("\tat ")
                         or line.startswith("\t... ") or line.startswith("Caused by:")):
            hostname.append(line)
            continue
        in_block = False
        if not line.strip():
            continue
        if JAVA_VERSION_RE.match(line):
            version.append(line)
        else:
            other.append(line)
    return version, hostname, other


def verify_exports(export_dir, expected):
    """What ExportDecomp.java should have left behind, checked on disk rather than inferred from a marker."""
    tsv = os.path.join(export_dir, "functions.tsv")
    asm = os.path.join(export_dir, "disassembly.txt")
    out = {"dir": export_dir, "expected": expected, "functions_tsv": tsv, "disassembly_txt": asm,
           "functions": 0, "decompiled": 0, "not_decompiled": 0, "header_ok": False,
           "disassembly_bytes": 0, "disassembly_lines": 0, "c_files": 0,
           "address_range": None, "problems": [], "verified": False}
    if not expected:
        out["problems"].append("no post-script was run, so nothing was exported")
        return out
    if not os.path.isdir(export_dir):
        out["problems"].append("export directory missing")
        return out
    out["c_files"] = sum(1 for n in os.listdir(export_dir) if n.startswith("function_") and n.endswith(".c"))
    if os.path.isfile(tsv):
        with open(tsv, encoding="utf-8", errors="replace") as fh:
            lines = [l.rstrip("\r\n") for l in fh if l.strip()]
        out["header_ok"] = bool(lines) and lines[0].split("\t") == ["address", "name", "decompilation_complete"]
        if not out["header_ok"]:
            out["problems"].append("functions.tsv header is not address/name/decompilation_complete")
        rows = [l.split("\t") for l in lines[1:]]
        out["functions"] = len(rows)
        out["decompiled"] = sum(1 for r in rows if len(r) >= 3 and r[2].strip().lower() == "true")
        out["not_decompiled"] = out["functions"] - out["decompiled"]
        if rows:
            out["address_range"] = [rows[0][0], rows[-1][0]]
        if out["functions"] < 1:
            out["problems"].append("functions.tsv has no data rows")
        elif out["decompiled"] < 1:
            out["problems"].append("no function has decompilation_complete true")
    else:
        out["problems"].append("functions.tsv missing")
    if os.path.isfile(asm):
        out["disassembly_bytes"] = os.path.getsize(asm)
        if out["disassembly_bytes"] == 0:
            out["problems"].append("disassembly.txt is empty")
        else:
            with open(asm, "rb") as fh:
                out["disassembly_lines"] = sum(1 for _ in fh)
    else:
        out["problems"].append("disassembly.txt missing")
    out["verified"] = not out["problems"]
    return out


def preflight(workspace, image, ghidra_path, java_home, export_dir, script_path, prescripts, timeout_s):
    problems = []
    if re.search(r"\s", workspace):
        problems.append("workspace path contains whitespace, which the Ghidra launcher does not support in JVM options")
    if not os.path.isfile(image):
        problems.append(f"image is not a file: {image}")
    analyze = os.path.join(ghidra_path, "support", "analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
    if not os.path.isfile(analyze) or (os.name != "nt" and not os.access(analyze, os.X_OK)):
        problems.append(f"analyzeHeadless not executable: {analyze}")
    java = os.path.join(java_home, "bin", "java.exe" if os.name == "nt" else "java")
    if not os.path.isfile(java) or (os.name != "nt" and not os.access(java, os.X_OK)):
        problems.append(f"java not executable: {java}")
    if script_path:
        if not os.path.isfile(os.path.join(script_path, POSTSCRIPT)):
            problems.append(f"{POSTSCRIPT} not found in script_path {script_path}")
        for entry in prescripts or []:
            name = entry if isinstance(entry, str) else entry[0]
            if not os.path.isfile(os.path.join(script_path, name)):
                problems.append(f"pre-script not found in script_path: {name}")
    if os.path.exists(export_dir) and (not os.path.isdir(export_dir) or os.listdir(export_dir)):
        problems.append(f"export_dir must be absent or an empty directory: {export_dir}")
    try:
        if float(timeout_s) <= 0:
            problems.append("timeout_s must be positive")
    except (TypeError, ValueError):
        problems.append("timeout_s must be a number")
    return problems


def run(workspace, image, base_address, processor, ghidra_path, java_home, export_dir,
        loader="BinaryLoader", prescripts=None, script_path=None, timeout_s=1800, noanalysis=False,
        project_name=PROJECT_NAME):
    """Import `image` at `base_address` with `processor`, analyse unless `noanalysis`, run
    ExportDecomp.java from `script_path` into `export_dir`, and return one dict. `loader` None omits
    -loader so Ghidra chooses. `script_path` None runs no scripts, so nothing is exported and `ok` stays
    false; the import verdict is then in `import_ok`."""
    workspace, image, ghidra_path, java_home, export_dir = (_abs(p) for p in
        (workspace, image, ghidra_path, java_home, export_dir))
    script_path = _abs(script_path) if script_path else None
    result = {"ok": False, "import_ok": False, "workspace": workspace, "image": image,
              "export_dir": export_dir, "loader_requested": loader, "processor": processor,
              "noanalysis": bool(noanalysis), "reasons": [], "_exit": 1}
    try:
        base = parse_base_address(base_address)
    except ValueError as e:
        result["reasons"].append(str(e))
        return result
    result["base_address"] = "0x%08x" % base
    problems = preflight(workspace, image, ghidra_path, java_home, export_dir, script_path, prescripts, timeout_s)
    if problems:
        result["reasons"] = problems
        return result

    for name in WORKSPACE_DIRS:
        os.makedirs(os.path.join(workspace, name), exist_ok=True)
    os.makedirs(export_dir, exist_ok=True)
    logs = os.path.join(workspace, "logs")
    argv = build_command(ghidra_path, workspace, image, base, processor, loader, prescripts,
                         script_path, export_dir, noanalysis, project_name)
    # The upstream Windows entry point is a batch launcher. It expands its arguments
    # again, so refuse command metacharacters instead of constructing a shell string.
    if os.name == "nt" and any(re.search(r'[&|<>^%!"\r\n]', value) for value in [*argv, workspace, java_home]):
        result["reasons"] = ["Windows Ghidra launcher arguments cannot contain shell metacharacters"]
        return result
    env, dirs, dropped = build_env(workspace, java_home)
    stdout_path = os.path.join(logs, "analyzeHeadless.stdout")
    stderr_path = os.path.join(logs, "analyzeHeadless.stderr")
    log_path = os.path.join(logs, "application.log")
    for p in (stdout_path, stderr_path, log_path, os.path.join(logs, "script.log")):
        if os.path.exists(p):
            os.remove(p)
    record = {"argv": argv, "cwd": workspace, "started": now(), "timeout_s": timeout_s,
              "env": {k: env[k] for k in ("JAVA_HOME", "GHIDRA_HEADLESS_JAVA_OPTIONS",
                                          "XDG_CONFIG_HOME", "XDG_CACHE_HOME")},
              "path_head": env["PATH"].split(os.pathsep)[0], "env_dropped": dropped,
              "shell": False, "process_policy": "windows-process-group" if os.name == "nt" else "posix-session"}
    with open(os.path.join(logs, "command.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    result.update({"argv": argv, "command_record": os.path.join(logs, "command.json"),
                   "stdout_path": stdout_path, "stderr_path": stderr_path, "log_path": log_path,
                   "env_dropped": dropped, "jvm_options": env["GHIDRA_HEADLESS_JAVA_OPTIONS"]})

    t0 = time.monotonic()
    timed_out = False
    with open(stdout_path, "wb") as out_fh, open(stderr_path, "wb") as err_fh:
        process_options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                           else {"start_new_session": True})
        proc = subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                stdout=out_fh, stderr=err_fh, shell=False, **process_options)
        try:
            proc.wait(timeout=float(timeout_s))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "nt":
                    taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
                    try:
                        subprocess.run([taskkill, "/PID", str(proc.pid), "/T", "/F"],
                                       capture_output=True, timeout=10, check=False, shell=False)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)
    wall = time.monotonic() - t0
    exit_code = proc.returncode
    result.update({"exit_code": exit_code, "timed_out": timed_out, "t_wall_s": round(wall, 2)})

    def read(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""
    stdout, stderr, log = read(stdout_path), read(stderr_path), read(log_path)
    texts = [stdout, stderr, log]
    markers = classify_markers("\n".join(texts))
    reports, report_count = report_lines(texts)
    version, hostname, other = split_stderr(stderr)
    log_file = _first(r"Using log file: (.+?)\s*\(LoggingInitialization\)", [stdout, log])
    result.update({
        "markers": markers,
        "report_lines": reports, "report_count": report_count,
        "loader_used": _first(r"Using Loader: (.+?)\s*\(ProgramLoader\)", texts),
        "language_used": _first(r"Using Language/Compiler: (.+?)\s*\(ProgramLoader\)", texts),
        "base_address_applied": _first(r'Successfully applied "-loader-baseAddr" to "Base Address" \((.+?)\)', texts),
        "settings_log_file": log_file,
        "settings_in_workspace": bool(log_file) and log_file.startswith(workspace + os.sep),
        "java_version": version,
        "hostname_diagnostic": {"present": bool(hostname), "lines": len(hostname), "head": hostname[:2],
                                "note": "Log4j could not resolve the local host name; nonfatal on its own"},
        "stderr_other": other[:STDERR_CAP], "stderr_other_count": len(other),
        "stdout_bytes": len(stdout.encode("utf-8", "replace")),
    })
    result["exports"] = verify_exports(export_dir, expected=bool(script_path))
    result["import_ok"] = exit_code == 0 and not timed_out and markers["import_succeeded"]

    reasons = []
    if timed_out:
        reasons.append(f"timed out after {timeout_s} s and was killed")
    if exit_code != 0:
        reasons.append(f"analyzeHeadless exit status {exit_code}")
    if not markers["import_succeeded"]:
        reasons.append("log has no 'Import succeeded'")
    for key in ("invalid_loader", "import_failed", "script_error"):
        if markers[key]:
            reasons.append(f"log has '{MARKERS[key]}'")
    reasons += result["exports"]["problems"]
    result["reasons"] = reasons
    result["ok"] = exit_code == 0 and not timed_out and markers["import_succeeded"] and result["exports"]["verified"]
    result["_exit"] = 0 if result["ok"] else 1
    with open(os.path.join(logs, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    result["result_path"] = os.path.join(logs, "result.json")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workspace", required=True, help="absolute, whitespace-free directory the run may write")
    ap.add_argument("--image", required=True, help="raw image to import")
    ap.add_argument("--base-address", required=True, help="hexadecimal load address, with or without 0x")
    ap.add_argument("--processor", required=True, help="Ghidra language id, for example ARM:LE:32:Cortex")
    ap.add_argument("--ghidra-path", required=True, help="Ghidra installation directory")
    ap.add_argument("--java-home", required=True, help="JDK directory whose bin/java runs Ghidra")
    ap.add_argument("--export-dir", required=True, help="absent or empty directory for the exporter")
    ap.add_argument("--loader", default="BinaryLoader",
                    help="-loader selector; pass an empty string to let Ghidra choose")
    ap.add_argument("--prescript", action="append", default=[], help="pre-script name in script-path; repeatable")
    ap.add_argument("--script-path", default=None,
                    help="directory holding ExportDecomp.java; omitted means import only, nothing exported")
    ap.add_argument("--timeout-s", type=float, default=1800)
    ap.add_argument("--noanalysis", action="store_true")
    ap.add_argument("--project-name", default=PROJECT_NAME)
    args = ap.parse_args(argv)
    result = run(workspace=args.workspace, image=args.image, base_address=args.base_address,
                 processor=args.processor, ghidra_path=args.ghidra_path, java_home=args.java_home,
                 export_dir=args.export_dir, loader=args.loader or None, prescripts=args.prescript,
                 script_path=args.script_path, timeout_s=args.timeout_s, noanalysis=args.noanalysis,
                 project_name=args.project_name)
    json.dump(result, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return int(result["_exit"])


if __name__ == "__main__":
    sys.exit(main())

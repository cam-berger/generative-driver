"""Explicit native gate for headless Ghidra. No configuration means failure, never a skipped qualification.

Set GD_NATIVE_GHIDRA_HOME and GD_NATIVE_JAVA_HOME to a Ghidra installation and a JDK it supports. The image
is the hand-assembled Cortex-M3 program of native_ghidra_exports.py, which derives from no benchmark source,
answer key or device firmware. Each run goes through the public command
`python -m generative_driver tool ghidra_run --arguments FILE`. With GD_NATIVE_GHIDRA_EVIDENCE set, each run's
result, logs and exports are kept there.

    python -m unittest discover -s tests -p native_ghidra.py -v
"""
import getpass
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from native_ghidra_exports import BANNER, BASE, CLASSIFY, DISPATCH, GREET, LOOKUP, RESET, cortex_m_image
from package_env import package_env

REQUIRED = ("GD_NATIVE_GHIDRA_HOME", "GD_NATIVE_JAVA_HOME")
# The image has no symbols; these are the functions its reset handler reaches by direct calls.
FIXTURE = {"processor": "ARM:LE:32:Cortex", "base_address": "0x%08x" % BASE,
           "functions": [{"name": name, "address": "0x%08x" % address} for name, address in (
               ("reset", RESET), ("dispatch", DISPATCH), ("classify", CLASSIFY), ("greet", GREET),
               ("lookup", LOOKUP), ("banner", BANNER))]}
SUCCEEDED = ("import_succeeded", "analysis_succeeded", "post_analysis_succeeded", "save_succeeded")
FAILED = ("script_error", "invalid_loader", "import_failed")
# Generous for a cold JVM on a hosted runner: the hold script must be running when the timeout ends the run.
HOLD_TIMEOUT_S = 90
HOLD_MESSAGE = "HoldAnalyzer: waiting for the caller timeout"
HOLD_SCRIPT = """// Keeps the headless analyzer busy until the caller's timeout ends the run.
import ghidra.app.script.GhidraScript;

public class HoldAnalyzer extends GhidraScript {
    @Override
    public void run() throws Exception {
        println("%s");
        Thread.sleep(600000);
    }
}
""" % HOLD_MESSAGE


def export_problems(result, manifest):
    """Every reason a ghidra_run result does not show the image imported as specified and each listed
    function recovered from it, decompiled and disassembled. An empty list is the lane's pass."""
    problems = []
    if result.get("ok") is not True:
        problems.append(f"ghidra_run ok is {result.get('ok')!r}: {result.get('reasons') or result.get('error')}")
    markers = result.get("markers") or {}
    problems += [f"log lacks the {key} marker" for key in SUCCEEDED if markers.get(key) is not True]
    problems += [f"log has the {key} marker" for key in FAILED if markers.get(key) is not False]
    if result.get("loader_used") != "Raw Binary":
        problems.append(f"loader is {result.get('loader_used')!r}, not Raw Binary")
    if not str(result.get("language_used")).startswith(manifest["processor"] + ":"):
        problems.append(f"language is {result.get('language_used')!r}, not {manifest['processor']}")
    base = "%08x" % int(manifest["base_address"], 16)
    if not str(result.get("base_address_applied")).endswith(f'"{base}"'):
        problems.append(f"base address applied is {result.get('base_address_applied')!r}, not {base}")
    if result.get("settings_in_workspace") is not True:
        problems.append("Ghidra's log is outside the workspace")
    exports = Path(result.get("export_dir") or ".")
    try:
        rows = (exports / "functions.tsv").read_text(encoding="utf-8").splitlines()[1:]
        listing = (exports / "disassembly.txt").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return problems + [f"export unreadable: {exc}"]
    complete = {fields[0]: fields[2] == "true" for fields in (row.split("\t") for row in rows) if len(fields) >= 3}
    listed = {line.split("\t", 1)[0] for line in listing}
    for function in manifest["functions"]:
        address, name = "%08x" % int(function["address"], 16), function["name"]
        if address not in complete:
            problems.append(f"{name} at {address} is not in functions.tsv")
            continue
        source = exports / f"function_{address}.c"
        text = source.read_text(encoding="utf-8") if source.is_file() else ""
        if not complete[address] or not text or "DECOMPILATION FAILED" in text:
            problems.append(f"{name} at {address} has no completed decompilation")
        if address not in listed:
            problems.append(f"{name} at {address} has no instruction in disassembly.txt")
    return problems


def ghidra_user_dirs():
    """Ghidra's per-user settings, cache and temporary locations when nothing overrides them, from the
    rules in its support/launch.properties. A run configured by ghidra_run must leave all of them alone."""
    user, home, temp = getpass.getuser(), Path.home(), Path(tempfile.gettempdir())
    if os.name == "nt":
        bases = (Path(os.environ["APPDATA"]), Path(os.environ["LOCALAPPDATA"]), temp)
    elif sys.platform == "darwin":
        bases = (home / "Library", Path("/var/tmp"), temp)
    else:
        bases = (home / ".config", Path("/var/tmp"), temp)
    return [base / name for base in bases for name in ("ghidra", user + "-ghidra")]


def footprint(path):
    if not path.exists():
        return None
    return sorted((str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns) for p in (path, *path.rglob("*")))


def processes_mentioning(path):
    """Command lines of running processes that name `path`; a finished or killed analyzer leaves none."""
    if os.name == "nt":
        shell = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0",
                     "powershell.exe")
        command = [str(shell), "-NoProfile", "-NonInteractive", "-Command",
                   "Get-CimInstance Win32_Process | ForEach-Object { [string]$_.ProcessId + ' ' + $_.CommandLine }"]
    else:
        command = ["/bin/ps", "-axww", "-o", "pid=,command="]
    listing = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=120, check=True).stdout
    fold = str.casefold if os.name == "nt" else str
    return [line.strip() for line in listing.splitlines() if fold(str(path)) in fold(line)]


class NativeGhidraTests(unittest.TestCase):
    def setUp(self):
        for name in REQUIRED:
            # Name the variable only: a failure message must not print the rest of the environment.
            self.assertTrue(os.environ.get(name), "Native gate requires explicit " + name)
            self.assertTrue(Path(os.environ[name]).is_absolute() and Path(os.environ[name]).is_dir(), name)

    def ghidra_run(self, label, root, **overrides):
        image = root / "image.bin"
        image.write_bytes(cortex_m_image())
        arguments = {"run_dir": str(root / "run"), "workspace": str(root / "analysis"), "image": str(image),
                     "base_address": FIXTURE["base_address"], "processor": FIXTURE["processor"],
                     "ghidra_path": os.environ["GD_NATIVE_GHIDRA_HOME"], "java_home": os.environ["GD_NATIVE_JAVA_HOME"],
                     "export_dir": str(root / "exports"), "prescripts": ["SeedCortexM.java"], "timeout_s": 900,
                     **overrides}
        (root / "arguments.json").write_text(json.dumps(arguments, indent=1), encoding="utf-8")
        completed = subprocess.run([sys.executable, "-m", "generative_driver", "tool", "ghidra_run", "--arguments",
                                    str(root / "arguments.json")], cwd=root, env={**os.environ, **package_env()},
                                   capture_output=True, text=True, timeout=1200)
        (root / "result.json").write_text(completed.stdout, encoding="utf-8")
        (root / "cli.stderr").write_text(completed.stderr, encoding="utf-8")
        evidence = os.environ.get("GD_NATIVE_GHIDRA_EVIDENCE")
        if evidence:
            kept = Path(evidence) / label
            for name in ("arguments.json", "result.json", "cli.stderr", "run", "exports", "scripts", "analysis/logs"):
                if (root / name).is_dir():
                    shutil.copytree(root / name, kept / name, dirs_exist_ok=True)
                elif (root / name).is_file():
                    kept.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(root / name, kept / name)
            properties = Path(os.environ["GD_NATIVE_GHIDRA_HOME"], "Ghidra", "application.properties")
            host = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version,
                    "ghidra": [line for line in properties.read_text(encoding="utf-8").splitlines()
                               if line.startswith(("application.version=", "application.build.date="))],
                    "runner_image": [os.environ.get("ImageOS"), os.environ.get("ImageVersion")],
                    "image_sha256": hashlib.sha256(cortex_m_image()).hexdigest()}
            (kept / "host.json").write_text(json.dumps(host, indent=1), encoding="utf-8")
        try:
            return completed, json.loads(completed.stdout)
        except ValueError:
            self.fail(f"ghidra_run printed no result (exit {completed.returncode}): {completed.stderr[-2000:]}")

    def test_public_tool_imports_the_fixture_and_exports_every_function(self):
        before = {path: footprint(path) for path in ghidra_user_dirs()}
        with tempfile.TemporaryDirectory(prefix="gd-ghidra-", ignore_cleanup_errors=True) as temp:
            root = Path(temp).resolve()
            completed, result = self.ghidra_run("import", root)
            problems = export_problems(result, FIXTURE)
            if (root / "image.bin").read_bytes() != cortex_m_image():
                problems.append("the analyzed image changed")
            problems += [f"Ghidra wrote outside the workspace: {path}" for path, seen in before.items()
                         if footprint(path) != seen]
            problems += [f"left running: {line}" for line in processes_mentioning(root)]
            self.assertEqual(problems, [])
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])

    def test_timeout_ends_the_analyzer_process_tree(self):
        from generative_driver.toolkit import resources_root
        with tempfile.TemporaryDirectory(prefix="gd-ghidra-", ignore_cleanup_errors=True) as temp:
            root = Path(temp).resolve()
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy(resources_root() / "toolchain/skills/interpret-firmware-binary/ExportDecomp.java", scripts)
            (scripts / "HoldAnalyzer.java").write_text(HOLD_SCRIPT, encoding="utf-8")
            completed, result = self.ghidra_run("timeout", root, script_path=str(scripts),
                                                prescripts=["HoldAnalyzer.java"], timeout_s=HOLD_TIMEOUT_S)
            self.assertEqual(completed.returncode, 1, completed.stderr[-2000:])
            self.assertTrue(result.get("timed_out"), result.get("reasons"))
            self.assertFalse(result["ok"])
            logs = root / "analysis" / "logs"
            output = "".join((logs / name).read_text(encoding="utf-8", errors="replace")
                             for name in ("analyzeHeadless.stdout", "script.log") if (logs / name).is_file())
            self.assertIn(HOLD_MESSAGE, output, "the analyzer was not yet running the hold script")
            self.assertEqual(processes_mentioning(root), [])


if __name__ == "__main__":
    unittest.main()

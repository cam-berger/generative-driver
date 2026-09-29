"""Behavior through the installed toolchain facade; no hardware or inference."""
import unittest
import hashlib
import json
import shutil
import tempfile
import os
import subprocess
import sys
from pathlib import Path


class ToolkitTests(unittest.TestCase):
    def test_advertised_probe_diff_argument_compares_saved_replay_evidence(self):
        from generative_driver.toolkit import call_tool, list_tools, resources_root
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model = root / "model"
            model.mkdir()
            fixture = resources_root() / "bench/cases/setup-smoke"
            shutil.copy2(fixture / "model.json", model / "model.json")
            shutil.copy2(fixture / "replay.json", root / "replay.json")
            arguments = {"run_dir": str(root / "run"), "model_dir": str(model)}
            observed = call_tool("probe_run", {**arguments, "operation": "measure", "n": 1,
                                               "replay": str(root / "replay.json")})
            self.assertTrue(observed.get("ok"), observed)
            self.assertEqual(observed["results"][0]["outputs"]["temperature"], 21.5)
            contract = next(tool for tool in list_tools() if tool["name"] == "probe_diff")
            self.assertIn("probe_json", contract["inputSchema"]["required"])
            for field in ("probe_json", "probe"):
                compared = call_tool("probe_diff", {**arguments, field: observed["probe"]})
                self.assertTrue(compared.get("ok"), compared)
                self.assertEqual(compared["defects"], [])
            refused = call_tool("probe_diff", {**arguments, "probe_json": observed["probe"],
                                               "probe": str(root / "other.json")})
            self.assertIn("same evidence", refused.get("error", ""), refused)
            relative_run = root / "relative-run"
            refused = call_tool("probe_diff", {**arguments, "run_dir": str(relative_run),
                                               "probe_json": "relative-probe.json"})
            self.assertIn("absolute", refused.get("error", ""), refused)
            self.assertFalse(relative_run.exists())

    def test_packaged_text_assets_work_under_a_non_utf8_system_locale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            script = """import locale,json,sys
locale.setlocale(locale.LC_CTYPE, 'C')
from test_package import PackageTests
PackageTests().test_register_workspace_and_package_validate_outside_installation()
print(json.dumps({'_exit': 0}))
"""
            env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(__import__("generative_driver").__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)))}
            result = subprocess.run([sys.executable, "-X", "utf8=0", "-c", script,
                str(root / "run"), str(root / "neutral")], cwd=temp, env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output.get("_exit"), 0, output)

    def test_optional_flash_tool_uses_the_installed_python_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "module-call.json"
            (root / "esptool.py").write_text(
                'import json,sys\nfrom pathlib import Path\n'
                f'Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")\n'
                'raise SystemExit(1)\n', encoding="utf-8")
            (root / "espefuse.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(Path(__import__("generative_driver").__file__).resolve().parents[1])))}
            program = "from generative_driver.toolkit import call_tool; import json,sys; print(json.dumps(call_tool('acquire_flash',{'run_dir':sys.argv[1],'backend':'esp32','port':'fixture-port'})))"
            result = subprocess.run([sys.executable, "-c", program, str(root / "run")], env=env,
                                    cwd=temp, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file(), result.stdout)
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")),
                ["-p", "fixture-port", "--before", "no-reset", "--after", "no-reset", "chip-id"])
            self.assertFalse(json.loads(result.stdout)["available"])

    def test_bus_discovery_never_selects_a_default_adapter(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            for name in ("acquire_bus_scan", "acquire_classify"):
                result = call_tool(name, {"run_dir": str(Path(temp).resolve() / name)})
                self.assertEqual(result["_exit"], 4, result)
                self.assertEqual(result.get("fault"), "operator", result)
                self.assertIn("bus_url", result.get("reason", ""), result)

    def test_relative_artifact_paths_are_rejected_before_creating_run_storage(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp).resolve() / "run"
            result = call_tool("model_validate", {"run_dir": str(run), "model_dir": "relative-model"})
            self.assertEqual(result["_exit"], 1, result)
            self.assertIn("absolute", result.get("error", ""), result)
            self.assertFalse(run.exists())

    def test_bus_preparation_requires_an_explicit_supported_controller(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            result = call_tool("bus_prepare", {"run_dir": str(Path(temp).resolve()),
                "backend": "unknown", "ports": ["not-a-device"]})
            self.assertEqual(result["_exit"], 1, result)
            self.assertEqual(result.get("controllers"), {}, result)
            self.assertIn("esp32", result.get("reason", ""), result)

    def test_uninformative_firmware_does_not_claim_a_load_map(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            image = root / "zeros.bin"; image.write_bytes(bytes(1024))
            result = call_tool("image_map_check", {"run_dir": str(root / "run"), "image": str(image)})
            self.assertEqual(result.get("vector_candidates"), [], result)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["_exit"], 1, result)
            self.assertTrue(Path(result["report_path"]).is_absolute())

    def test_analyzer_records_native_launch_policy_and_kills_a_timed_out_launcher(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory(prefix="gdghidra-") as temp:
            root = Path(temp).resolve()
            support = root / "ghidra/support"; support.mkdir(parents=True)
            java = root / "jdk/bin"; java.mkdir(parents=True)
            image = root / "image.bin"; image.write_bytes(bytes(64))
            launcher_script = root / "launcher.py"
            launcher_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            if os.name == "nt":
                launcher = support / "analyzeHeadless.bat"
                launcher.write_text(f'@"{sys.executable}" "{launcher_script}" %*\n', encoding="utf-8")
                (java / "java.exe").write_bytes(b"placeholder")
            else:
                launcher = support / "analyzeHeadless"
                launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{launcher_script}" "$@"\n', encoding="utf-8")
                launcher.chmod(0o700)
                (java / "java").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                (java / "java").chmod(0o700)
            result = call_tool("ghidra_run", {"run_dir": str(root / "run"), "workspace": str(root / "analysis"),
                "image": str(image), "base_address": "08000000", "processor": "ARM:LE:32:Cortex",
                "ghidra_path": str(root / "ghidra"), "java_home": str(root / "jdk"),
                "export_dir": str(root / "exports"), "timeout_s": 0.25})
            self.assertTrue(result.get("timed_out"), result)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["argv"][0], str(launcher))
            record = json.loads(Path(result["command_record"]).read_text(encoding="utf-8"))
            self.assertEqual(record.get("process_policy"),
                "windows-process-group" if os.name == "nt" else "posix-session", record)
            self.assertEqual(record.get("shell"), False)
            self.assertLess(result["t_wall_s"], 10)

    def test_missing_optional_analyzer_is_an_explicit_host_diagnostic(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            image = root / "image.bin"; image.write_bytes(bytes(range(64)))
            result = call_tool("ghidra_run", {"run_dir": str(root / "run"), "workspace": str(root / "analysis"),
                "image": str(image), "base_address": "0x08000000", "processor": "ARM:LE:32:Cortex",
                "ghidra_path": str(root / "missing-ghidra"), "java_home": str(root / "missing-java"),
                "export_dir": str(root / "exports")})
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["_exit"], 1, result)
            self.assertTrue(any("analyzeHeadless" in item for item in result.get("reasons", [])), result)
            self.assertFalse((root / "exports").exists())

    def test_concurrent_usage_delivery_is_counted_once_without_unix_dependencies(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            run = str(Path(temp).resolve() / "run")
            args = {"run_dir": run, "stage": "interpret", "source": "contract-test", "model": "fixture",
                    "record_id": "one-delivery", "usage": {"mode": "delta", "session_id": "session-1",
                        "input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3, "reasoning_tokens": 1}}
            script = '''import importlib.abc,sys,json,os
class AbsentHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('fcntl','serial','usb','pyftdi','mcp'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, AbsentHardware())
if hasattr(os,'getuid'): del os.getuid
from generative_driver.toolkit import call_tool
print(json.dumps(call_tool('usage_record',json.loads(sys.argv[1]))))
'''
            env = {**os.environ, "PYTHONPATH": str(Path(__import__("generative_driver").__file__).resolve().parents[1])}
            children = [subprocess.Popen([sys.executable, "-c", script, json.dumps(args)],
                cwd=temp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
            completed = [(child, *child.communicate(timeout=30)) for child in children]
            for child, stdout, stderr in completed:
                self.assertEqual(child.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout)["_exit"], 0, stdout)
            summary = call_tool("usage_summary", {"run_dir": run})
            self.assertEqual(summary["_exit"], 0, summary)
            self.assertEqual(summary["tokens"]["total_tokens"], 13)

    def test_firmware_import_prepares_neutral_inputs_and_freezes_evidence(self):
        from generative_driver.toolkit import call_tool

        with tempfile.TemporaryDirectory(prefix="toolkit input ") as temp:
            root = Path(temp).resolve()
            run = root / "private objective"
            source = root / "vendor-release.bin"
            source.write_bytes(bytes(range(64)))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            def call(name, **arguments):
                result = call_tool(name, {"run_dir": str(run), **arguments})
                self.assertEqual(result.get("_exit", 0), 0, result)
                return result
            acquired = call("acquire_firmware_artifact", source_path=str(source),
                            origin="provided_binary", expected_sha256=digest)
            prepared = call("firmware_prepare", artifact=acquired["artifact"], expected_sha256=digest)
            workspace = Path(prepared["workspace"])
            self.addCleanup(shutil.rmtree, workspace, True)
            self.assertFalse(workspace.is_relative_to(root))
            self.assertEqual((workspace / "image.bin").read_bytes(), bytes(range(64)))
            self.assertNotIn("vendor-release", prepared["prompt_for_agent"])
            (workspace / "exports").mkdir()
            (workspace / "exports/handler.txt").write_text("00000010: accepts 51; emits 52\n")
            (workspace / "model.json").write_text(json.dumps({
                "schema": "interface-model/2-experimental", "device": {"identity": "unknown"},
                "channel": {"type": "unknown"}, "operations": [{"id": "status", "effect_class": "read_only",
                    "request": {"bytes_hex": "51"}, "response_interpretation": {"meaning": "status byte"},
                    "evidence": [{"file_offset": "0x10", "export": "exports/handler.txt"}]}],
                "limits": {}, "safety": {}, "provenance": {"source": "image.bin"},
                "uncertainties": ["Physical meaning is unproved."]}))
            (workspace / "NOTES.md").write_text("Synthetic fixture, no live observations.\n")
            (workspace / "PROBES.json").write_text("[]")
            collected = call("firmware_collect")
            self.assertTrue(collected["ok"], collected)
            self.assertEqual((Path(collected["first_pass"]) / "exports/handler.txt").read_text(),
                             "00000010: accepts 51; emits 52\n")
            self.assertFalse(call_tool("firmware_collect", {"run_dir": str(run)})["ok"])

    def test_installed_catalog_exposes_generic_tools_and_resources(self):
        from generative_driver.toolkit import list_tools, resources_root

        catalog = {item["name"]: item for item in list_tools()}
        self.assertTrue({"acquire_firmware_artifact", "interpret_run", "model_validate",
                         "probe_run", "emit_package", "emit_check"} <= catalog.keys())
        self.assertFalse(any(name.startswith("hardware_") for name in catalog))
        for entry in catalog.values():
            self.assertTrue(entry["description"])
            self.assertIn("run_dir", entry["inputSchema"]["required"])
        self.assertIsInstance(resources_root(), Path)
        self.assertTrue(resources_root().is_absolute())


if __name__ == "__main__":
    unittest.main()

"""Public emitted-package behavior against an independent invented wire trace."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


def example_model():
    def step(text, capture, expected=None):
        result = {"op": "exchange", "tx": [{"text": text}],
                  "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 128},
                  "timeout_ms": 500, "capture": capture}
        if expected:
            result["expect"] = {"equals_hex": expected}
        return result
    return {"schema": "interface-model/3", "device": {"name": "fictional demonstration"},
            "channel": {"type": "uart", "baudrate": 115200}, "identity": {"operation": "identify"},
            "operations": {
                "identify": {"effect": "read", "parameters": {}, "steps": [step("ID?\n", "id", "44454d4f2d34320a")], "outputs": {}},
                "measure": {"effect": "read", "parameters": {}, "steps": [step("T\n", "reply")],
                            "outputs": {"temperature": {"from": "reply", "kind": "ascii_number", "prefix": "T:", "unit": "degC"}}}},
            "provenance": [{"item": "operations." + name, "source": "independent synthetic fixture"}
                           for name in ("identify", "measure")]}


def example_replay():
    return {"schema": "interface-replay/1", "transactions": [
        {"tx_hex": "49443f0a", "rx_hex": "44454d4f2d34320a", "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 128}, "timeout_ms": 500},
        {"tx_hex": "540a", "rx_hex": "543a32312e350a", "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 128}, "timeout_ms": 500}]}


class PackageTests(unittest.TestCase):
    def test_register_workspace_and_package_validate_outside_installation(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory(prefix="register case ") as temp:
            root = Path(temp).resolve()
            def call(tool_name, **arguments):
                result = call_tool(tool_name, {"run_dir": str(root / "trial"), **arguments})
                self.assertEqual(result.get("_exit", 0), 0, result)
                return result
            prepared = call("interpret_run", front_end="interpret-blackbox", inputs={},
                            workspace_root=str(root / "neutral"))
            workspace = Path(prepared["workspace"])
            model = {"schema": "interface-model/1", "device": {"name": "invented register sensor"},
                "channel": {"type": "i2c", "address_7bit": "0x20", "speed_hz": 100000},
                "identity": {"from": "id", "expect": ["0x42"]},
                "operations": {"init": [{"op": "reg_write", "reg": "0x01", "bytes": ["0x02"]}],
                    "identify": [{"op": "reg_read", "reg": "0x00", "len": 1, "as": "id"}],
                    "measure": [{"op": "reg_read", "reg": "0x10", "len": 1, "as": "raw"}]},
                "conversion": {"module": "convert.py", "entry": "convert", "outputs": {"value": {"unit": "count"}}},
                "safety": {"safe_reads": ["0x00", "0x10"], "known_safe_writes": [], "never": []},
                "provenance": [{"item": "operations." + key, "source": "invented register contract"}
                               for key in ("init", "identify", "measure")]}
            (workspace / "model.json").write_text(json.dumps(model))
            (workspace / "convert.py").write_text('def convert(inputs):\n    return {"value": inputs["0x10"][0] / 2}\n')
            validated = subprocess.run([sys.executable, "-I", "-S", "validate.py", "."], cwd=workspace,
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            self.assertTrue(call("model_validate", model_dir=str(workspace))["ok"])
            for binding in (None, {"url": "ftdi://selected/1"}):
                denied_probe = call_tool("probe_run", {"run_dir": str(root / "denied"),
                    "model_dir": str(workspace), "binding": binding})
                self.assertEqual(denied_probe["_exit"], 4, denied_probe)
                self.assertEqual(denied_probe.get("fault"), "operator", denied_probe)
            probe = root / "register-probe.json"
            probe.write_text(json.dumps({"address": "0x20", "identity": "0x42", "identity_ok": True,
                "samples": [{"t_wall": 0, "values": {"value": 17.5}, "raw": "23"}], "evidence_kind": "replay"}))
            emitted = call("emit_package", model_dir=str(workspace), probe=str(probe), name="Ω register")
            relocated = root / "moved register package"; shutil.move(emitted["package_dir"], relocated)
            checked = call("emit_check", package_dir=str(relocated), model_dir=str(workspace), seeds=3)
            self.assertTrue(checked["ok"], checked)
            self.assertEqual(checked["values_mismatched"], 0)
            self.assertEqual(checked["footprint_mismatched"], 0)
            refused = subprocess.run([sys.executable, "-m", "driver", "measure"], cwd=relocated,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn("--url", refused.stderr)
            denied = subprocess.run([sys.executable, "-m", "driver", "--url", "ftdi://selected/1", "--json", "measure"],
                                    cwd=relocated, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("grant", denied.stdout, denied.stdout + denied.stderr)
            self.assertNotIn("pyftdi", denied.stdout)
            for binding in (None, {"url": "ftdi://selected/1"}):
                refused_live = call_tool("emit_test_live", {"run_dir": str(root / "live-refused"),
                    "package_dir": str(relocated), "binding": binding})
                self.assertEqual(refused_live["_exit"], 4, refused_live)
                self.assertEqual(refused_live.get("fault"), "operator", refused_live)

            marker = root / "must-not-run"
            with (relocated / "driver/device.py").open("a", encoding="utf-8") as stream:
                stream.write(f"\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n")
            rejected = call_tool("emit_check", {"run_dir": str(root / "check-tampered"),
                "package_dir": str(relocated), "model_dir": str(workspace), "seeds": 1})
            self.assertFalse(marker.exists(), "A changed package must be rejected before import")
            self.assertFalse(rejected["ok"], rejected)
            self.assertFalse(rejected.get("integrity_ok", True), rejected)

    def test_emitted_package_relocates_and_replays_without_source_checkout(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory(prefix="package with spaces ") as temp:
            root = Path(temp).resolve()
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(json.dumps(example_model()))
            replay = root / "replay.json"; replay.write_text(json.dumps(example_replay()))
            def call(tool_name, **arguments):
                result = call_tool(tool_name, {"run_dir": str(root / "run"), **arguments})
                self.assertEqual(result.get("_exit", 0), 0, result)
                return result
            self.assertTrue(call("model_validate", model_dir=str(model))["ok"])
            probe = call("probe_run", model_dir=str(model), operation="measure", n=1, replay=str(replay))
            self.assertTrue(Path(probe["probe"]).is_absolute())
            # MCP clients use the catalogue's probe_json spelling; direct Python
            # callers may also use the original probe argument.
            emitted = call("emit_package", model_dir=str(model), probe_json=probe["probe"])
            relocated = root / "relocated package"
            shutil.move(emitted["package_dir"], relocated)
            checked = call("emit_check", package_dir=str(relocated),
                           expected_manifest_sha256=emitted["manifest_sha256"])
            self.assertEqual(checked["replay_status"], "passed")
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(relocated), "replay"], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["results"][0]["outputs"], {"temperature": 21.5})


if __name__ == "__main__":
    unittest.main()

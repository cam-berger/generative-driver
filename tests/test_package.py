"""Public emitted-package behavior against an independent invented wire trace."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from line_device import line_device


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
    def test_emitted_package_runs_a_sequence_over_one_connection(self):
        import contextlib, importlib, io
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model = root / "model"; model.mkdir()
            definition = example_model()
            definition["channel"]["resets_on_open"] = True
            (model / "model.json").write_text(json.dumps(definition))
            replay = root / "replay.json"; replay.write_text(json.dumps(example_replay()))
            def call(tool_name, **arguments):
                result = call_tool(tool_name, {"run_dir": str(root / "run"), **arguments})
                self.assertEqual(result.get("_exit", 0), 0, result)
                return result
            # A resetting model's probe carries the single-session marker and still checks and emits.
            probe = call("probe_run", model_dir=str(model), operation="measure", n=1, replay=str(replay),
                         allow_effects=["reset"])
            self.assertEqual(json.loads(Path(probe["probe"]).read_text())["session"], "single")
            emitted = call("emit_package", model_dir=str(model), probe_json=probe["probe"])
            sys.path.insert(0, emitted["package_dir"])
            try:
                driver = importlib.import_module("driver")
                transports = sys.modules["interface_runtime.transports"]
                opens = []
                class Fake:
                    def exchange(self, tx, rx, timeout_ms):
                        return self.replies.pop(0)
                    def close(self):
                        pass
                def fresh():
                    fake = Fake()
                    fake.replies = [bytes.fromhex(t["rx_hex"]) for t in example_replay()["transactions"]] * 2
                    return fake
                fake = fresh()
                def factory(channel, binding):
                    opens.append(1); return fake
                steps = [{"operation": "measure"}, {"operation": "measure"}]
                with patch.object(transports, "open_transport", factory):
                    refused = driver.sequence(steps, binding={})
                    self.assertFalse(refused["ok"], refused)
                    self.assertEqual(opens, [])
                    result = driver.sequence(steps, binding={}, allow_effects=("reset",))
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(len(result["results"]), 2)
                    self.assertEqual(len(opens), 1)
                    # CLI: argument parsing and the reset grant, in process.
                    fake = fresh(); opens.clear()
                    cli = importlib.import_module("driver.__main__")
                    out = io.StringIO()
                    argv = ["driver", "sequence", "--steps", json.dumps(steps), "--binding", "{}",
                            "--allow-effect", "reset"]
                    with patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
                        code = cli.main()
                    self.assertEqual(code, 0, out.getvalue())
                    self.assertEqual(len(json.loads(out.getvalue())["results"]), 2)
                    self.assertEqual(len(opens), 1)
            finally:
                sys.path.remove(emitted["package_dir"])
                for name in [n for n in sys.modules if n == "driver" or n.startswith("driver.")]:
                    sys.modules.pop(name, None)

    def test_register_validation_preserves_supplied_model_bytes_and_inventory(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model_dir = root / "model"
            model_dir.mkdir()
            model = {"schema": "interface-model/1", "channel": {"type": "i2c", "address_7bit": "0x20"},
                "identity": {"from": "id", "expect": ["0x42"]},
                "operations": {"identify": [{"op": "reg_read", "reg": "0x00", "len": 1, "as": "id"}],
                    "measure": [{"op": "reg_read", "reg": "0x10", "len": 1, "as": "raw"}]},
                "conversion": {"module": "convert.py", "outputs": {"value": "count"}}, "safety": {},
                "provenance": [{"item": "operations.identify"}, {"item": "operations.measure"}]}
            supplied = {"model.json": json.dumps(model).encode(),
                        "convert.py": b'def convert(inputs):\n    return {"value": inputs["0x10"][0]}\n'}
            for name, content in supplied.items():
                (model_dir / name).write_bytes(content)
            checked = call_tool("model_validate", {"run_dir": str(root / "run"),
                "model_dir": str(model_dir), "random_files": 0})
            self.assertTrue(checked["ok"], checked)
            self.assertEqual({p.relative_to(model_dir).as_posix(): p.read_bytes()
                              for p in model_dir.rglob("*") if p.is_file()}, supplied)

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
                # Refused before the suite ran: nothing reached the device.
                self.assertIs(refused_live.get("transport_open_attempted"), False, refused_live)

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

    def test_live_test_summary_says_whether_the_package_tried_to_open_the_device(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(json.dumps(example_model()))
            replay = root / "replay.json"; replay.write_text(json.dumps(example_replay()))
            def call(tool_name, **arguments):
                result = call_tool(tool_name, {"run_dir": str(root / "run"), **arguments})
                self.assertEqual(result.get("_exit", 0), 0, result)
                return result
            probe = call("probe_run", model_dir=str(model), operation="measure", n=1, replay=str(replay))
            emitted = call("emit_package", model_dir=str(model), probe_json=probe["probe"])
            # A parameter the operation does not take: the package's own runtime refuses it before it opens the
            # transport. The port names no device, so nothing is reachable either way.
            live = call_tool("emit_test_live", {"run_dir": str(root / "run"), "package_dir": emitted["package_dir"],
                                                "binding": {"port": str(root / "no-such-serial-port")},
                                                "operations": ["measure"], "parameters": {"measure": {"unexpected": 1}}})
            self.assertEqual((live["_exit"], live["verdict"]), (4, "inconclusive"), live)
            [entry] = live["results"]
            self.assertEqual((entry["fault"], entry["transport_open_attempted"]), ("operator", False), entry)


def tcp_model():
    """The example model on a TCP channel, so a localhost line device can answer it."""
    return {**example_model(), "channel": {"type": "tcp"}}


DEVICE = {b"ID?": b"DEMO-42\n", b"T": b"T:21.5\n"}


class ToolFaultTests(unittest.TestCase):
    """A tool declares each refusal it makes before any device call, and a host fault never discards what the device
    did. The device is a fictional line protocol on a localhost socket; no hardware is involved."""

    def emitted(self, root, binding):
        from generative_driver.toolkit import call_tool
        model = root / "model"; model.mkdir()
        (model / "model.json").write_text(json.dumps(tcp_model()))
        probe = call_tool("probe_run", {"run_dir": str(root / "run"), "model_dir": str(model), "operation": "measure",
                                        "n": 1, "binding": binding})
        self.assertEqual((probe["_exit"], probe["evidence_kind"]), (0, "live"), probe)
        emitted = call_tool("emit_package", {"run_dir": str(root / "run"), "model_dir": str(model), "probe_json": probe["probe"]})
        self.assertEqual(emitted["_exit"], 0, emitted)
        return model, emitted

    def test_a_live_test_keeps_what_the_device_did_when_the_host_cannot_record_it(self):
        from generative_driver.toolkit import call_tool
        replies = dict(DEVICE)
        with tempfile.TemporaryDirectory() as temp, line_device(replies) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            for answer, ok, verdict in ((b"T:21.5\n", True, "inconclusive"), (b"ERR\n", False, "fail")):
                with self.subTest(verdict=verdict):
                    replies[b"T"] = answer
                    run = root / ("blocked-" + verdict); run.mkdir()
                    # The host cannot write the live record: a file stands where its folder belongs.
                    (run / "emit").write_text("not a folder")
                    live = call_tool("emit_test_live", {"run_dir": str(run), "package_dir": emitted["package_dir"],
                                                        "binding": binding, "operations": ["measure"]})
                    self.assertEqual((live["_exit"], live["ok"], live["fault"], live["verdict"], live["live_record"]),
                                     (4, False, "host", verdict, None), live)
                    self.assertIn("live record", live["error"])
                    self.assertEqual(live["manifest_sha256"], emitted["manifest_sha256"])
                    [entry] = live["results"]
                    self.assertEqual((entry["operation"], entry["ok"]), ("measure", ok), entry)
                    if not ok:
                        # The driver failed on the device; that stays in the result.
                        self.assertEqual((entry["fault"], entry["transport_open_attempted"]), ("model", True), entry)

    def test_a_live_test_declares_every_refusal_before_its_first_package_call(self):
        from generative_driver.toolkit import call_tool
        seen = []
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE), seen) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            tampered = root / "tampered"; shutil.copytree(emitted["package_dir"], tampered)
            (tampered / "REFERENCE.md").write_text("changed after emission")
            del seen[:]
            run = root / "refused"
            for name, arguments in (
                    ("parameters for an operation that is not tested", {"operations": ["measure"], "parameters": {"identify": {}}}),
                    ("an operation the model does not have", {"operations": ["erase"]}),
                    ("a binding the channel refuses", {"binding": {"port": str(root / "no-such-serial-port")}}),
                    ("an effect grant that is not one", {"allow_effects": ["everything"]}),
                    ("a package changed after emission", {"package_dir": str(tampered)})):
                with self.subTest(refusal=name):
                    live = call_tool("emit_test_live", {"run_dir": str(run), "package_dir": emitted["package_dir"],
                                                        "binding": binding, **arguments})
                    self.assertEqual((live["_exit"], live["ok"], live["transport_open_attempted"]), (1, False, False), live)
                    self.assertNotIn("results", live)
            # Nothing reached the device and no live record was written.
            self.assertEqual(seen, [])
            self.assertFalse((run / "emit").exists())

    def test_emit_check_reports_a_host_fault_as_one_and_a_broken_package_as_a_refusal(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE)) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            package = emitted["package_dir"]
            # The host has no usable temporary directory: the check could not run, which says nothing about the package.
            with patch.object(tempfile, "tempdir", str(root / "missing temporary directory")):
                checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": package})
            self.assertEqual((checked["_exit"], checked["ok"], checked["fault"], checked["replay_status"]),
                             (4, False, "host", "not_run"), checked)
            self.assertTrue(checked["error"].startswith("FileNotFoundError: "), checked)
            # A package without its driver model is the package's defect.
            broken = root / "broken"; shutil.copytree(package, broken)
            (broken / "driver" / "model.json").unlink()
            refused = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": str(broken)})
            self.assertEqual((refused["_exit"], refused["ok"]), (1, False), refused)
            self.assertNotIn("fault", refused)
            # The same package checks normally on a working host, from the device exchanges it recorded.
            passed = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": package})
            self.assertEqual((passed["_exit"], passed["replay_status"], passed["source_evidence_kind"]), (0, "passed", "live"), passed)

    def test_interface_execute_declares_a_model_the_runtime_refuses_before_opening_the_device(self):
        from generative_driver.toolkit import call_tool
        seen = []
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE), seen) as binding:
            root = Path(temp).resolve()
            model = root / "model"; model.mkdir()
            invalid = tcp_model(); invalid["operations"]["measure"]["steps"] = []
            (model / "model.json").write_text(json.dumps(invalid))
            refused = call_tool("interface_execute", {"run_dir": str(root / "run"), "model_dir": str(model),
                                                      "operation": "measure", "binding": binding})
            self.assertEqual((refused["_exit"], refused["ok"], refused["transport_open_attempted"]), (1, False, False), refused)
            self.assertTrue(refused["error"].startswith("ValueError: invalid model: "), refused)
            self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()

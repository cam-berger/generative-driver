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

    def test_a_register_live_test_refuses_the_operators_binding_or_grants_as_its_contract_says(self):
        """A register package's live suite refuses a binding that is no FTDI url, or a write without its grant, before
        the bus opens: fault operator with exit 4, not exit 1 as an interface package's refusals are."""
        from generative_driver.toolkit import call_tool, list_tools
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            package = root / "register-package"; (package / "driver").mkdir(parents=True)
            (package / "manifest.json").write_text(json.dumps({"name": "register"}))
            (package / "driver" / "model.json").write_text(json.dumps({"schema": "interface-model/1",
                                                                        "operations": {"init": [{"op": "reg_write"}]}}))
            for name, binding in (("a binding that is no FTDI url", {"host": "127.0.0.1", "port": 9}),
                                  ("a write without its grant", {"url": "ftdi://ftdi:232h/1"})):
                with self.subTest(refusal=name):
                    live = call_tool("emit_test_live", {"run_dir": str(root / "run"), "package_dir": str(package), "binding": binding})
                    self.assertEqual((live["_exit"], live["ok"], live["fault"], live["transport_open_attempted"]),
                                     (4, False, "operator", False), live)
        description = " ".join(next(t for t in list_tools() if t["name"] == "emit_test_live")["description"].split())
        self.assertIn("every such refusal returns transport_open_attempted false, with exit 1, except a register package's "
                      "binding or grant refusal, which is fault operator with exit 4.", description)

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

    def test_interface_execute_declares_every_refusal_it_makes_before_it_opens_the_device(self):
        """The model's bytes, the operation and the evidence bound are all checked before the transport opens; each
        refusal says so, with the tool's own reason, and the device sees nothing."""
        from generative_driver.toolkit import call_tool
        seen = []
        exchange = {"op": "exchange", "tx": [{"text": "T\n"}], "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 65536},
                    "timeout_ms": 500}
        # A valid model whose operation could record more than the evidence reader accepts.
        bounded = tcp_model()
        bounded["operations"]["dump"] = {"effect": "read", "parameters": {},
                                         "steps": [dict(exchange, capture="c%d" % i) for i in range(100)],
                                         "outputs": {"x%d" % i: {"from": "c%d" % i, "kind": "hex"} for i in range(100)}}
        bounded["provenance"].append({"item": "operations.dump", "source": "independent synthetic fixture"})
        text = json.dumps(tcp_model())
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE), seen) as binding:
            root = Path(temp).resolve()
            for name, content, operation, says in (
                    ("a duplicate JSON key", text.replace('"device": {', '"device": {"name": "twice", ', 1), "measure",
                     "ValueError: duplicate JSON key: name"),
                    ("a non-finite constant", text.replace('"timeout_ms": 500', '"timeout_ms": NaN', 1), "measure",
                     "ValueError: non-finite JSON constant: NaN"),
                    ("nesting deeper than the parser allows", text[:-1] + ', "x": ' + "[" * 100000 + "]" * 100000 + "}",
                     "measure", "RecursionError: "),
                    ("no model.json", None, "measure", "ValueError: model_dir holds no model.json"),
                    ("an operation the model does not have", text, "erase", "ValueError: unknown operation"),
                    ("an evidence bound the model exceeds", json.dumps(bounded), "dump",
                     "ValueError: requested probe exceeds evidence bound")):
                with self.subTest(refusal=name):
                    model = root / name.replace(" ", "-"); model.mkdir()
                    if content is not None:
                        (model / "model.json").write_text(content)
                    refused = call_tool("interface_execute", {"run_dir": str(root / "run"), "model_dir": str(model),
                                                              "operation": operation, "binding": binding})
                    self.assertEqual((refused["_exit"], refused["ok"], refused["transport_open_attempted"]), (1, False, False), refused)
                    self.assertTrue(refused["error"].startswith(says), refused)
                    self.assertNotIn("transactions", refused)
            self.assertEqual(seen, [])
            # The same tool reaches the device with a model it accepts.
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(text)
            executed = call_tool("interface_execute", {"run_dir": str(root / "run"), "model_dir": str(model),
                                                       "operation": "measure", "binding": binding})
            self.assertEqual((executed["_exit"], executed["ok"], executed["outputs"]), (0, True, {"temperature": 21.5}), executed)
            self.assertNotEqual(seen, [])

    def test_probe_run_declares_every_refusal_it_makes_before_it_opens_the_device(self):
        """probe_run reads the model's bytes and checks the model, the operation, the sample count, the evidence bound
        and a replay fixture before any transport opens; each refusal says so, and the device sees nothing. So does a
        register probe's refusal of the operator's binding or grants."""
        from interface_runtime import evidence
        from generative_driver.toolkit import call_tool
        seen, opened, real_open = [], [], evidence.open_transport
        def recording_open(channel, binding):
            opened.append(binding)
            return real_open(channel, binding)
        exchange = {"op": "exchange", "tx": [{"text": "T\n"}], "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 65536},
                    "timeout_ms": 500}
        # A valid model whose one-step dump fits the evidence reader once but not a hundred times.
        bounded = tcp_model()
        bounded["operations"]["dump"] = {"effect": "read", "parameters": {}, "steps": [dict(exchange, capture="c")],
                                         "outputs": {"x": {"from": "c", "kind": "hex"}}}
        bounded["provenance"].append({"item": "operations.dump", "source": "independent synthetic fixture"})
        invalid = tcp_model()
        invalid["operations"]["measure"]["steps"] = []
        text = json.dumps(tcp_model())
        register = json.dumps({"schema": "interface-model/1", "operations": {"init": [{"op": "reg_write"}]}})
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE), seen) as binding, \
                patch("interface_runtime.evidence.open_transport", recording_open):
            root = Path(temp).resolve()
            for name, content, arguments, exit_code, says in (
                    ("a duplicate JSON key", text.replace('"device": {', '"device": {"name": "twice", ', 1), {}, 1,
                     "ValueError: duplicate JSON key: name"),
                    ("a non-finite constant", text.replace('"timeout_ms": 500', '"timeout_ms": NaN', 1), {}, 1,
                     "ValueError: non-finite JSON constant: NaN"),
                    ("nesting deeper than the parser allows", text[:-1] + ', "x": ' + "[" * 100000 + "]" * 100000 + "}",
                     {}, 1, "RecursionError: "),
                    ("no model.json", None, {}, 1, "ValueError: model_dir holds no model.json"),
                    ("a model that is no JSON object", "[]", {}, 1, "ValueError: model.json is not a JSON object"),
                    ("an invalid model", json.dumps(invalid), {}, 1, "ValueError: invalid model: "),
                    ("no operation", text, {"operation": None}, 1, "ValueError: schema 3 probe requires an explicit operation"),
                    ("an operation the model does not have", text, {"operation": "erase"}, 1, "ValueError: unknown operation"),
                    ("too many samples", text, {"n": 101}, 1, "ValueError: sample count must be integer 1..100"),
                    ("no samples", text, {"n": 0}, 1, "ValueError: sample count must be integer 1..100"),
                    ("an evidence bound the model exceeds", json.dumps(bounded), {"operation": "dump", "n": 100}, 1,
                     "ValueError: requested probe exceeds evidence bound"),
                    ("a replay fixture that is not there", text, {"replay": str(root / "missing-replay.json")}, 1, "FileNotFoundError: "),
                    ("a register binding that is no FTDI url", register, {}, 4, None),
                    ("a register write without its grant", register, {"binding": {"url": "ftdi://ftdi:232h/1"}}, 4, None)):
                with self.subTest(refusal=name):
                    model = root / name.replace(" ", "-"); model.mkdir()
                    if content is not None:
                        (model / "model.json").write_text(content)
                    refused = call_tool("probe_run", {"run_dir": str(root / "run"), "model_dir": str(model),
                                                      **{"operation": "measure", "n": 1, "binding": binding, **arguments}})
                    self.assertEqual((refused["_exit"], refused["ok"], refused["transport_open_attempted"]),
                                     (exit_code, False, False), refused)
                    if says is None:
                        self.assertEqual(refused["fault"], "operator", refused)
                    else:
                        self.assertTrue(refused["error"].startswith(says), refused)
                    self.assertNotIn("results", refused)
                    self.assertNotIn("probe", refused)
            self.assertEqual((seen, opened), ([], []))
            # The same tool reaches the device with a model it accepts.
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(text)
            probed = call_tool("probe_run", {"run_dir": str(root / "run"), "model_dir": str(model), "operation": "measure",
                                             "n": 1, "binding": binding})
            self.assertEqual((probed["_exit"], probed["ok"], probed["evidence_kind"]), (0, True, "live"), probed)
            self.assertNotEqual(seen, [])
            # The evidence bound counts the samples asked for: one sample of the dump fits it and reaches the device.
            dumped = call_tool("probe_run", {"run_dir": str(root / "run"), "model_dir": str(root / "an-evidence-bound-the-model-exceeds"),
                                             "operation": "dump", "n": 1, "binding": binding})
            self.assertEqual((dumped["_exit"], dumped["ok"]), (0, True), dumped)
            self.assertEqual(len(opened), 2)

    @staticmethod
    def reseal(package):
        """Rewrite a package's manifest and checksum over its current files, as a worker editing its package could."""
        from interface_runtime.package import digest, package_files
        manifest = json.loads((package / "manifest.json").read_text())
        manifest.update(files=package_files(package), probe_sha256=digest(package / "probe.json"),
                        model_sha256=digest(package / "driver" / "model.json"))
        (package / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (package / "manifest.sha256").write_text(digest(package / "manifest.json") + "\n")

    def test_emit_check_refuses_a_hand_edited_package_and_never_calls_it_a_host_fault(self):
        """Content a worker wrote is the package's, however malformed: emit_check refuses it (exit 1) and never
        reports it as a host fault, which would stop the run for the host."""
        import os
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE)) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            record = (Path(emitted["package_dir"]) / "probe.json").read_text()
            # Each refusal is declared with its defects, except where the toolchain's error names what failed.
            for name, path, content, refusal in (
                    ("a probe record that is a JSON array", "probe.json", "[1, 2]", "defects"),
                    ("a probe record nested deeper than the parser allows", "probe.json",
                     record.rstrip()[:-1] + ', "x": ' + "[" * 100000 + "]" * 100000 + "}", "defects"),
                    ("a driver model nested deeper than the parser allows", "driver/model.json",
                     '{"x": ' + "[" * 100000 + "]" * 100000 + "}", "defects"),
                    ("a manifest nested deeper than the parser allows", "manifest.json",
                     '{"x": ' + "[" * 100000 + "]" * 100000 + "}", "error"),
                    ("a driver model the host may not read", "driver/model.json", None, "defects"),
                    # Searching a folder needs its execute permission, which Python 3.12 and earlier report as an error.
                    ("a driver folder the host may not search", "driver", None, "defects")):
                with self.subTest(package=name):
                    package = root / name.replace(" ", "-"); shutil.copytree(emitted["package_dir"], package)
                    folder = (package / path).is_dir()
                    mode = 0o755 if folder else 0o644
                    if content is not None:
                        (package / path).write_text(content)
                        if path != "manifest.json":
                            self.reseal(package)
                    else:
                        (package / path).chmod(0)
                        if os.access(package / path, os.X_OK if folder else os.R_OK):
                            (package / path).chmod(mode)
                            self.skipTest("this host reads files without read permission")
                    try:
                        checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": str(package)})
                    finally:
                        (package / path).chmod(mode)
                    self.assertEqual((checked["_exit"], checked.get("ok", False)), (1, False), checked)
                    self.assertTrue(checked.get(refusal), checked)
                    self.assertNotIn("fault", checked)
                    self.assertNotEqual(checked.get("replay_status"), "passed", checked)

    def test_a_snapshot_copy_the_host_cannot_read_back_is_a_host_fault_never_a_package_defect(self):
        """Both tools copy a package that passed its integrity check into a temporary snapshot and verify the copy. A
        read of that copy the host refuses (an antivirus or indexer lock, an I/O error) is the host's failure around
        the check: emit_check reports a host fault, and emit_test_live raises it undeclared, so it reads as
        inconclusive, never as a refusal of the package."""
        import interface_runtime.package as runtime_package
        from generative_driver.toolkit import call_tool
        from generative_driver.configurator import _host_fault, _live_outcome, digest
        seen = []
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE), seen) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            package = Path(emitted["package_dir"])
            real_digest = runtime_package.digest
            def locked(path):
                if Path(path).name == "engine.py" and "package" in Path(path).parts and not Path(path).is_relative_to(package):
                    raise PermissionError(13, "The process cannot access the file because it is being used by another process", str(path))
                return real_digest(path)
            del seen[:]
            with patch.object(runtime_package, "digest", locked):
                checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": str(package)})
                live = call_tool("emit_test_live", {"run_dir": str(root / "reuse"), "package_dir": str(package),
                                                    "binding": binding, "operations": ["measure"]})
            self.assertEqual((checked["_exit"], checked["ok"], checked["fault"], checked["replay_status"]),
                             (4, False, "host", "not_run"), checked)
            self.assertTrue(checked["error"].startswith("PermissionError: "), checked)
            self.assertTrue(_host_fault(checked))
            self.assertNotIn("transport_open_attempted", live)
            self.assertTrue(live["error"].startswith("PermissionError: "), live)
            event = {"name": "emit_test_live", "connected": True, "ok": False, "result": live, "dispatch_id": "7",
                     "argument_sha256": {"package_dir": digest(package)}}
            self.assertEqual(_live_outcome(event), ("inconclusive", "host fault (PermissionError)"))
            self.assertEqual(seen, [])
            # The same package on a host that reads its copy back.
            passed = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": str(package)})
            self.assertEqual((passed["_exit"], passed["replay_status"]), (0, "passed"), passed)
            ran = call_tool("emit_test_live", {"run_dir": str(root / "reuse"), "package_dir": str(package),
                                               "binding": binding, "operations": ["measure"]})
            self.assertEqual((ran["_exit"], ran["ok"]), (0, True), ran)

    def test_a_replay_process_the_host_ended_is_a_host_fault_never_a_differing_replay(self):
        """emit_check replays the verified snapshot in a separate process of the trusted runtime, after the same replay
        passed in the check's own process. A replay process that printed no result (killed by the host, out of memory,
        crashed at start) or exited with an error after printing the same result failed around the check: a host
        fault. Only a replay process that printed a different result says the package's replay differs."""
        import subprocess as subprocess_module
        from generative_driver._toolchain import interface
        from generative_driver.toolkit import call_tool
        from generative_driver.configurator import _host_fault
        real_run = subprocess_module.run
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE)) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            package = emitted["package_dir"]
            same = real_run([sys.executable, "-I", "-B", "-c", interface.BOOT, package, "replay"], capture_output=True,
                            text=True, timeout=120).stdout
            self.assertEqual(json.loads(same)["ok"], True, same)
            for name, returncode, stdout, host in (
                    ("a process the host killed", -9, "", True),
                    ("a process that exited cleanly without a result", 0, "", True),
                    ("a process that printed half a result", 0, same[: len(same) // 2], True),
                    ("a process that printed the same result and then failed", 1, same, True),
                    ("a process that printed a different result", 1, json.dumps({"ok": False, "defects": ["differs"]}), False)):
                with self.subTest(replay=name):
                    def replay(command, **kwargs):
                        if "replay" in command:
                            return subprocess_module.CompletedProcess(command, returncode, stdout, "")
                        return real_run(command, **kwargs)
                    with patch.object(subprocess_module, "run", replay):
                        checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": package})
                    self.assertFalse(checked["ok"], checked)
                    if host:
                        self.assertEqual((checked["_exit"], checked["fault"], checked["replay_status"]), (4, "host", "not_run"), checked)
                        self.assertTrue(_host_fault(checked))
                    else:
                        self.assertEqual((checked["_exit"], checked["replay_status"]), (1, "failed"), checked)
                        self.assertNotIn("fault", checked)
                        self.assertFalse(_host_fault(checked))
                        self.assertEqual(checked["defects"], [{"where": "package replay", "what": "standalone replay differs"}])

    def test_a_register_converter_that_fails_to_load_fails_the_check_and_is_no_host_fault(self):
        """A register package's seeded check imports the candidate's converter and runs it on register files. An error
        that converter raises, an ImportError or OSError included, is the package's: the check fails (exit 1), naming
        driver/convert.py when it will not load. It is never reported as a host fault."""
        from generative_driver.toolkit import call_tool
        model = {"schema": "interface-model/1", "device": {"name": "invented register sensor"},
                 "channel": {"type": "i2c", "address_7bit": "0x20", "speed_hz": 100000},
                 "identity": {"from": "id", "expect": ["0x42"]},
                 "operations": {"identify": [{"op": "reg_read", "reg": "0x00", "len": 1, "as": "id"}],
                                "measure": [{"op": "reg_read", "reg": "0x10", "len": 1, "as": "raw"}]},
                 "conversion": {"module": "convert.py", "entry": "convert", "outputs": {"value": {"unit": "count"}}},
                 "safety": {"safe_reads": ["0x00", "0x10"], "known_safe_writes": [], "never": []},
                 "provenance": [{"item": "operations." + key, "source": "invented register contract"} for key in ("identify", "measure")]}
        probe = {"address": "0x20", "identity": "0x42", "identity_ok": True,
                 "samples": [{"t_wall": 0, "values": {"value": 1}, "raw": "23"}], "evidence_kind": "replay"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "probe.json").write_text(json.dumps(probe))
            for name, converter, defects in (
                    ("a converter that imports a missing module",
                     "import module_the_worker_forgot\ndef convert(inputs):\n    return {'value': 1}\n", ["driver/convert.py"]),
                    ("a converter that opens a file that is not there",
                     "open('no-such-calibration.bin', 'rb')\ndef convert(inputs):\n    return {'value': 1}\n", ["driver/convert.py"]),
                    # Bytes a conversion cannot cope with are outside the model's domain: no seed runs, and the check fails.
                    ("a converter that raises an OSError on the bytes",
                     "def convert(inputs):\n    raise OSError('calibration table unreadable')\n", None),
                    ("a converter that works", "def convert(inputs):\n    return {'value': inputs['0x10'][0] / 2}\n", [])):
                with self.subTest(converter=name):
                    workspace = root / name.replace(" ", "-"); workspace.mkdir()
                    (workspace / "model.json").write_text(json.dumps(model))
                    (workspace / "convert.py").write_text(converter)
                    emitted = call_tool("emit_package", {"run_dir": str(root / "run"), "model_dir": str(workspace),
                                                         "probe": str(root / "probe.json")})
                    self.assertEqual(emitted["_exit"], 0, emitted)
                    checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": emitted["package_dir"],
                                                       "model_dir": str(workspace), "seeds": 2})
                    self.assertNotIn("fault", checked)
                    if defects == []:
                        self.assertEqual((checked["_exit"], checked["ok"]), (0, True), checked)
                    else:
                        self.assertEqual((checked["_exit"], checked["ok"]), (1, False), checked)
                    if defects:
                        self.assertEqual([d["where"] for d in checked["defects"]], defects, checked)
                    elif defects is None:
                        self.assertEqual(checked["seeds_run"], 0, checked)

    def test_emit_check_keeps_a_passed_replay_when_its_temporary_folder_cannot_be_removed(self):
        """On Windows an antivirus or indexer can hold a file open briefly, so removing a temporary folder fails after
        the check finished. That cleanup failure neither discards a passed replay nor reads as a host fault."""
        import errno, os
        from generative_driver.toolkit import call_tool
        real_rmtree = shutil.rmtree
        def held_open(path, *args, onexc=None, onerror=None, **kwargs):
            real_rmtree(path, ignore_errors=True)
            try:
                raise OSError(errno.EBUSY, "file in use by another process", str(path))
            except OSError as error:
                if onexc is not None:
                    onexc(os.rmdir, str(path), error)
                elif onerror is not None:
                    onerror(os.rmdir, str(path), sys.exc_info())
                elif not kwargs.get("ignore_errors"):
                    raise
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE)) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            with patch.object(shutil, "rmtree", held_open):
                checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": emitted["package_dir"]})
            self.assertEqual((checked["_exit"], checked["ok"], checked["replay_status"]), (0, True, "passed"), checked)


if __name__ == "__main__":
    unittest.main()

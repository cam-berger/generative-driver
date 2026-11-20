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
        and a replay fixture before any transport opens; each refusal says so, and the device sees nothing."""
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
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE), seen) as binding, \
                patch("interface_runtime.evidence.open_transport", recording_open):
            root = Path(temp).resolve()
            for name, content, arguments, says in (
                    ("a duplicate JSON key", text.replace('"device": {', '"device": {"name": "twice", ', 1), {},
                     "ValueError: duplicate JSON key: name"),
                    ("a non-finite constant", text.replace('"timeout_ms": 500', '"timeout_ms": NaN', 1), {},
                     "ValueError: non-finite JSON constant: NaN"),
                    ("nesting deeper than the parser allows", text[:-1] + ', "x": ' + "[" * 100000 + "]" * 100000 + "}",
                     {}, "RecursionError: "),
                    ("no model.json", None, {}, "ValueError: model_dir holds no model.json"),
                    ("a model that is no JSON object", "[]", {}, "ValueError: model.json is not a JSON object"),
                    ("an invalid model", json.dumps(invalid), {}, "ValueError: invalid model: "),
                    ("no operation", text, {"operation": None}, "ValueError: schema 3 probe requires an explicit operation"),
                    ("an operation the model does not have", text, {"operation": "erase"}, "ValueError: unknown operation"),
                    ("too many samples", text, {"n": 101}, "ValueError: sample count must be integer 1..100"),
                    ("no samples", text, {"n": 0}, "ValueError: sample count must be integer 1..100"),
                    ("an evidence bound the model exceeds", json.dumps(bounded), {"operation": "dump", "n": 100},
                     "ValueError: requested probe exceeds evidence bound"),
                    ("a replay fixture that is not there", text, {"replay": str(root / "missing-replay.json")}, "FileNotFoundError: ")):
                with self.subTest(refusal=name):
                    model = root / name.replace(" ", "-"); model.mkdir()
                    if content is not None:
                        (model / "model.json").write_text(content)
                    refused = call_tool("probe_run", {"run_dir": str(root / "run"), "model_dir": str(model),
                                                      **{"operation": "measure", "n": 1, "binding": binding, **arguments}})
                    self.assertEqual((refused["_exit"], refused["ok"], refused["transport_open_attempted"]),
                                     (1, False, False), refused)
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
        the check: emit_check reports a host fault, and emit_test_live declares a host fault before any call, so the
        gateway reconciles an effect it marked and the verdict reads it as inconclusive, never as a refusal of the
        package."""
        import interface_runtime.package as runtime_package
        from generative_driver.toolkit import call_tool
        from generative_driver.configurator import _declared_refusal, _host_fault, _live_outcome, digest
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
            self.assertEqual((live["_exit"], live["ok"], live["fault"], live["transport_open_attempted"]),
                             (4, False, "host", False), live)
            self.assertNotIn("results", live)
            self.assertTrue(live["error"].startswith("PermissionError: "), live)
            self.assertFalse(_declared_refusal(live))
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
        crashed at start), exited with an error after printing the same result, or could not read the snapshot (the
        trusted package's package_error) failed around the check: a host fault. Only a replay process that printed a
        different result says the package's replay differs."""
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
                    ("a process that could not read the snapshot", 1, json.dumps(
                        {"ok": False, "error": {"code": "package_error", "message": "[Errno 13] Permission denied: 'probe.json'"}}), True),
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

    def test_a_replay_process_the_host_keeps_from_the_snapshot_is_a_host_fault_never_a_differing_replay(self):
        """The replay process reads the verified snapshot whose replay just passed in the check's own process. When the
        host keeps it from a file of that snapshot (a lock taken after the check read the copy back), the trusted package
        prints its package_error and exits 1: a failure around the check, which says nothing about the package."""
        import subprocess as subprocess_module
        from generative_driver.toolkit import call_tool
        from generative_driver.configurator import _host_fault
        real_run = subprocess_module.run
        with tempfile.TemporaryDirectory() as temp, line_device(dict(DEVICE)) as binding:
            root = Path(temp).resolve()
            _, emitted = self.emitted(root, binding)
            locked = []
            def replay(command, **kwargs):
                if "replay" not in command:
                    return real_run(command, **kwargs)
                probe = Path(command[-2]) / "probe.json"
                mode = probe.stat().st_mode
                probe.chmod(0)
                try:
                    try:
                        probe.read_bytes()
                    except PermissionError:
                        locked.append(probe)
                    return real_run(command, **kwargs)
                finally:
                    probe.chmod(mode)
            with patch.object(subprocess_module, "run", replay):
                checked = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": emitted["package_dir"]})
            if not locked:
                self.skipTest("this host reads a file whose permissions forbid reading it")
            self.assertEqual((checked["_exit"], checked["ok"], checked["fault"], checked["replay_status"]),
                             (4, False, "host", "not_run"), checked)
            self.assertIn("Permission denied", checked["error"])
            self.assertTrue(_host_fault(checked))
            passed = call_tool("emit_check", {"run_dir": str(root / "checks"), "package_dir": emitted["package_dir"]})
            self.assertEqual((passed["_exit"], passed["replay_status"]), (0, "passed"), passed)

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


REGISTER_MODEL = {"schema": "interface-model/1", "device": {"name": "invented register sensor"},
                  "channel": {"type": "i2c", "address_7bit": "0x20", "speed_hz": 100000},
                  "identity": {"from": "id", "expect": ["0x42"]},
                  "operations": {"identify": [{"op": "reg_read", "reg": "0x00", "len": 1, "as": "id"}],
                                 "measure": [{"op": "reg_read", "reg": "0x10", "len": 1, "as": "raw"}]},
                  "conversion": {"module": "convert.py", "entry": "convert", "outputs": {"value": {"unit": "count"}}},
                  "safety": {"safe_reads": ["0x00", "0x10"], "known_safe_writes": [], "never": []},
                  "provenance": [{"item": "operations." + key, "source": "invented register description"}
                                 for key in ("identify", "measure")]}


def marking(marker):
    """Python source that writes the file `marker` when it is imported or run."""
    return (f"with open({str(marker)!r}, 'w') as out:\n    out.write('ran')\n"
            "def convert(inputs):\n    return {'value': inputs['0x10'][0] / 2}\n")


def said(result):
    """The reason a tool gave for a refusal: its error, its reason and what each of its defects says."""
    defects = result.get("defects") if isinstance(result.get("defects"), list) else []
    parts = (result.get("error"), result.get("reason"), *(defect.get("what") for defect in defects))
    return " ".join(str(part) for part in parts if part)


def contents(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def register_package(folder, marker):
    """A folder laid out as the deleted emit stage laid out a register package: its manifest names no package format,
    and its conversion module and its live suite write the file `marker` when imported or run."""
    from interface_runtime.package import digest, package_files
    (folder / "driver").mkdir(parents=True)
    (folder / "tests").mkdir()
    (folder / "driver" / "model.json").write_text(json.dumps(REGISTER_MODEL))
    (folder / "driver" / "convert.py").write_text(marking(marker))
    (folder / "tests" / "test_live.py").write_text(marking(marker))
    manifest = {"name": "register", "version": "1.0.0",
                "kind": "device package generated from interface-model/1 by the driver compiler's emit stage",
                "files": package_files(folder)}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (folder / "manifest.sha256").write_text(digest(folder / "manifest.json") + "\n")
    return folder


class RegisterFamilyTests(unittest.TestCase):
    """The register model family (schema interface-model/1, with a conversion module a worker wrote) is deleted. The
    tool refuses a model or a package of it as it refuses any format it does not know, and runs no file of it."""

    def test_01_B1_a_register_model_is_refused_by_its_schema_and_no_file_of_it_runs(self):
        from generative_driver.toolkit import call_tool
        opened = []
        def bus(*arguments, **keywords):
            opened.append(arguments)
            raise AssertionError("a bus was opened")
        with tempfile.TemporaryDirectory() as temp, patch("generative_driver._toolchain.runner.FtdiI2c", bus), \
                patch("interface_runtime.evidence.open_transport", bus):
            root = Path(temp).resolve()
            marker = root / "convert-ran"
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(json.dumps(REGISTER_MODEL))
            (model / "convert.py").write_text(marking(marker))
            supplied = contents(model)
            # No tool reads the probe record: none exists.
            probe = str(root / "no-such-probe.json")
            for tool, arguments in (("model_validate", {}),
                                    ("probe_run", {"binding": {"url": "ftdi://ftdi:232h/1"}}),
                                    ("probe_diff", {"probe": probe}),
                                    ("emit_package", {"probe": probe})):
                with self.subTest(tool=tool):
                    run = root / ("run-" + tool)
                    refused = call_tool(tool, {"run_dir": str(run), "model_dir": str(model), **arguments})
                    self.assertEqual((refused.get("ok"), refused["_exit"]), (False, 1), refused)
                    self.assertIn("interface-model/1", said(refused), refused)
                    self.assertFalse(marker.exists(), "a file of the register model was imported or run")
                    self.assertEqual(contents(model), supplied)
                    if tool == "probe_run":
                        self.assertIs(refused.get("transport_open_attempted"), False, refused)
                    # Neither a probe record nor a package folder was written.
                    self.assertFalse((run / "probe").exists())
                    self.assertFalse((run / "emit").exists())
            self.assertEqual(opened, [])

    def test_01_B2_a_register_package_is_refused_by_its_format_and_no_file_of_it_runs(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            marker = root / "package-ran"
            package = register_package(root / "package", marker)
            model = root / "model"; model.mkdir()
            for name in ("model.json", "convert.py"):
                shutil.copyfile(package / "driver" / name, model / name)
            supplied = contents(package)
            for tool, arguments in (("emit_check", {}),
                                    ("emit_test_live", {"binding": {"url": "ftdi://ftdi:232h/1"}})):
                for model_dir in ({}, {"model_dir": str(model)}):
                    with self.subTest(tool=tool, with_model_dir=bool(model_dir)):
                        run = root / ("run-" + tool + ("-with-model" if model_dir else ""))
                        refused = call_tool(tool, {"run_dir": str(run), "package_dir": str(package), **arguments,
                                                   **model_dir})
                        self.assertEqual((refused.get("ok"), refused["_exit"]), (False, 1), refused)
                        self.assertIn("package format is not one this kit knows", said(refused), refused)
                        self.assertFalse(marker.exists(), "a file of the register package was imported or run")
                        self.assertEqual(contents(package), supplied)
                        if tool == "emit_test_live":
                            self.assertIs(refused.get("transport_open_attempted"), False, refused)
                        # No live record was written.
                        self.assertFalse((run / "emit").exists())

    def test_01_B3_interpret_run_refuses_a_deleted_front_end(self):
        import re
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            listed = set()
            for front_end in ("interpret-datasheet", "interpret-firmware", "interpret-blackbox"):
                run, neutral = root / ("run-" + front_end), root / ("neutral-" + front_end)
                for where, arguments in (("in the run", {}), ("outside the run", {"workspace_root": str(neutral)})):
                    with self.subTest(front_end=front_end, workspace=where):
                        refused = call_tool("interpret_run", {"run_dir": str(run), "front_end": front_end,
                                                              "inputs": {}, **arguments})
                        self.assertEqual((refused.get("ok"), refused["_exit"]), (False, 1), refused)
                        named, _, existing = refused["reason"].partition("; one of ")
                        self.assertIn(repr(front_end), named, refused)
                        listed.update(re.findall(r"'([^']+)'", existing))
                        # No workspace, seal or attempt record was written.
                        self.assertFalse((run / "interpret").exists())
                        self.assertFalse(neutral.exists())
            # The front ends the refusal lists are the ones that exist: each prepares a workspace.
            self.assertEqual(listed, {"interpret-interface", "interpret-selfdescribing"})
            for front_end in sorted(listed):
                prepared = call_tool("interpret_run", {"run_dir": str(root / ("run-" + front_end)),
                                                       "front_end": front_end, "inputs": {}})
                self.assertEqual((prepared.get("ok"), prepared["_exit"]), (True, 0), prepared)


if __name__ == "__main__":
    unittest.main()

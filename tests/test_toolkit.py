"""Behavior through the installed toolchain facade; no hardware or inference."""
import unittest
import hashlib
import json
import tempfile
import os
import subprocess
import sys
from pathlib import Path


class ToolkitTests(unittest.TestCase):
    def test_advertised_probe_diff_argument_compares_saved_replay_evidence(self):
        from generative_driver.toolkit import call_tool, list_tools
        from test_package import example_model, example_replay
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model = root / "model"
            model.mkdir()
            (model / "model.json").write_text(json.dumps(example_model()), encoding="utf-8")
            (root / "replay.json").write_text(json.dumps(example_replay()), encoding="utf-8")
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
            # An interpret-interface workspace and an interface package, whose name is not ASCII: every packaged text
            # asset is read and written under a locale that cannot encode it. The run folder has a name that no path
            # of a temporary folder holds: the tool refuses a workspace whose path carries the run's name.
            script = """import locale,json,subprocess,sys
locale.setlocale(locale.LC_CTYPE, 'C')
from pathlib import Path
from generative_driver.toolkit import call_tool
from test_package import example_model, example_replay
def call(tool, **arguments):
    result = call_tool(tool, {'run_dir': sys.argv[1], **arguments})
    assert result.get('_exit', 0) == 0, result
    return result
prepared = call('interpret_run', front_end='interpret-interface', inputs={}, workspace_root=sys.argv[2])
workspace = Path(prepared['workspace'])
(workspace / 'model.json').write_text(json.dumps(example_model()), encoding='utf-8')
validated = subprocess.run([sys.executable, '-I', '-S', 'validate.py', '.'], cwd=workspace, capture_output=True,
                           text=True, timeout=30)
assert validated.returncode == 0, validated.stdout + validated.stderr
assert call('model_validate', model_dir=str(workspace))['ok']
replay = Path(sys.argv[2]) / 'replay.json'
replay.write_text(json.dumps(example_replay()), encoding='utf-8')
probed = call('probe_run', model_dir=str(workspace), operation='measure', n=1, replay=str(replay))
emitted = call('emit_package', model_dir=str(workspace), probe=probed['probe'], name='\u03a9 thermometer')
manifest = json.loads((Path(emitted['package_dir']) / 'manifest.json').read_text(encoding='utf-8'))
assert manifest['name'] == '\u03a9 thermometer', manifest
assert call('emit_check', package_dir=emitted['package_dir'])['ok']
print(json.dumps({'_exit': 0}))
"""
            env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(__import__("generative_driver").__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)))}
            result = subprocess.run([sys.executable, "-X", "utf8=0", "-c", script,
                str(root / "gd-run-0"), str(root / "neutral")], cwd=temp, env=env,
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

    @staticmethod
    def _esp_flash_image(entries):
        import struct
        image = bytearray(b"\xff" * 0x30000)
        for i, (kind, sub, offset, size, label) in enumerate(entries):
            image[0x8000 + 32 * i:0x8000 + 32 * (i + 1)] = (
                b"\xaa\x50" + struct.pack("<BBII", kind, sub, offset, size) + label.ljust(16, b"\0") + b"\0" * 4)
            image[offset] = 0xE9
            image[offset + 1:offset + size] = bytes([i + 1]) * (size - 1)
        return bytes(image)

    def _esp_fake_sh(self, commands, image, app_seen=None, fuses=(0, "SPI_BOOT_CRYPT_CNT = Disable\nSECURE_BOOT_EN = False\n"), convert_ok=True):
        def fake_sh(cmd, cwd=None, env=None, timeout=900):
            commands.append(cmd)
            if "chip-id" in cmd:
                return 0, "Chip is ESP32-S3 (QFN56)\nMAC: 80:45:6b:2d:2f:6c\n", ""
            if "summary" in cmd:
                return fuses[0], fuses[1], "fuse stderr"
            if "read-flash" in cmd:
                Path(cmd[-1]).write_bytes(image)
                return 0, "", ""
            if any(str(c).endswith("esp_image_to_elf.py") for c in cmd):
                if app_seen is not None:
                    app_seen.append(Path(cmd[-2]).read_bytes())
                if not convert_ok:
                    return 1, "", "conversion failed"
                Path(cmd[-1]).write_bytes(b"\x7fELF")
                return 0, "", ""
            return 0, "", ""
        return fake_sh

    def test_esp_flash_uses_auto_reset_only_with_grant_and_names_interpret_input(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        commands = []
        image = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        with tempfile.TemporaryDirectory() as temp, patch.object(core, "sh", self._esp_fake_sh(commands, image)):
            granted = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32", "allow_reset": True})
            self.assertTrue(granted["available"], granted)
            self.assertTrue(granted["interpret_input"].endswith("interpret-input.elf"))
            self.assertTrue(Path(granted["interpret_input"]).is_file())
            self.assertIn("SECURE_BOOT_EN", (Path(temp) / "acquire" / "efuse.txt").read_text(encoding="utf-8"))
            self.assertTrue(any(p.endswith("efuse.txt") for p in granted["artifacts"]), granted["artifacts"])
            esp = [c for c in commands if "-p" in c]
            self.assertTrue(all("default-reset" in c for c in esp))
            self.assertIn("hard-reset", esp[-1])
            commands.clear()
            call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32"})
            esp = [c for c in commands if "-p" in c]
            self.assertTrue(all("no-reset" in c and "default-reset" not in c for c in esp))
            self.assertFalse(any("hard-reset" in c for c in commands))
            dump = next(c for c in esp if "read-flash" in c)
            self.assertEqual(dump[dump.index("-b") + 1], "115200")

    def test_esp_flash_carves_the_factory_app_partition_from_the_table(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        image = self._esp_flash_image([(1, 0x02, 0x9000, 0x100, b"nvs"), (0, 0x10, 0x20000, 0x80, b"ota_0"),
                                       (0, 0x00, 0x10000, 0x100, b"factory")])
        seen = []
        with tempfile.TemporaryDirectory() as temp, patch.object(core, "sh", self._esp_fake_sh([], image, seen)):
            result = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32"})
            self.assertTrue(result["available"], result)
            self.assertEqual(seen, [image[0x10000:0x10100]])
            self.assertEqual((Path(temp) / "acquire" / "app.bin").read_bytes(), image[0x10000:0x10100])

    def test_esp_flash_returns_the_chip_to_the_application_on_every_failed_exit_with_a_grant(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        good = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        cases = {"fuse failure": (good, (1, "")), "locked": (good, (0, "SECURE_BOOT_EN = True\n")),
                 "no partition table": (b"\xff" * 0x20000, (0, "SECURE_BOOT_EN = False\n"))}
        for name, (image, fuses) in cases.items():
            for grant in (True, False):
                with self.subTest(name, grant=grant):
                    commands = []
                    with tempfile.TemporaryDirectory() as temp, \
                            patch.object(core, "sh", self._esp_fake_sh(commands, image, fuses=fuses)):
                        result = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake",
                                                             "backend": "esp32", "allow_reset": grant})
                        self.assertFalse(result["available"], result)
                    if grant:
                        self.assertIn("hard-reset", commands[-1])
                        if name == "no partition table":  # the dump itself already ended with the hard reset
                            self.assertIn("read-flash", commands[-1])
                            self.assertEqual(sum("hard-reset" in c for c in commands), 1)
                        else:
                            self.assertIn("chip-id", commands[-1])
                            self.assertEqual(commands[-1][commands[-1].index("--before") + 1], "default-reset")
                            self.assertIs(result["returned_to_application"], True)
                    else:
                        self.assertFalse(any("hard-reset" in c for c in commands))
                        self.assertNotIn("returned_to_application", result)

    def test_esp_flash_leave_failure_never_masks_the_original_result(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        good = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        for how in ("exit", "raise"):
            with self.subTest(how):
                commands = []
                inner = self._esp_fake_sh(commands, good, fuses=(1, ""))
                def fake(cmd, **kw):
                    if "hard-reset" in cmd and "chip-id" in cmd:
                        commands.append(cmd)
                        if how == "raise":
                            raise OSError("port vanished")
                        return 1, "", "no sync"
                    return inner(cmd, **kw)
                with tempfile.TemporaryDirectory() as temp, patch.object(core, "sh", fake):
                    result = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake",
                                                         "backend": "esp32", "allow_reset": True})
                self.assertIn("Fuse read failed", result["reason"], result)
                self.assertIs(result["returned_to_application"], False)

    @staticmethod
    def _raw_table_image(entries, size=0x30000):
        """Dump with a partition table at 0x8000; entries are (type, subtype, offset, size), taken as given."""
        import struct
        image = bytearray(b"\xff" * size)
        for i, (kind, sub, offset, length) in enumerate(entries):
            at = 0x8000 + 32 * i
            image[at:at + 32] = b"\xaa\x50" + struct.pack("<BBII", kind, sub, offset, length) + b"x".ljust(16, b"\0") + b"\0" * 4
        for kind, sub, offset, length in entries:
            if kind == 0 and 0 < length and offset + length <= size:
                image[offset:offset + length] = bytes([sub + 1]) * length
        return bytes(image)

    def test_esp_flash_carving_bounds_and_selection(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        cases = {
            "entry beyond the dump": (self._raw_table_image([(0, 0x00, 0x2FF00, 0x1000)]), None),
            "dump shorter than the table": (b"\xff" * 0x8010, None),
            "zero-size entry skipped": (self._raw_table_image([(0, 0x00, 0x10000, 0), (0, 0x10, 0x20000, 0x40)]), (0x20000, 0x40)),
            "only an ota entry": (self._raw_table_image([(1, 0x02, 0x9000, 0x100), (0, 0x10, 0x20000, 0x80)]), (0x20000, 0x80)),
            "bad factory, valid ota_0": (self._raw_table_image([(0, 0x00, 0x2FF00, 0x1000), (0, 0x10, 0x20000, 0x80)]), (0x20000, 0x80)),
        }
        for name, (image, want) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                seen = []
                with patch.object(core, "sh", self._esp_fake_sh([], image, seen)):
                    result = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32"})
                acquire = Path(temp) / "acquire"
                if want is None:
                    self.assertFalse(result["available"], result)
                    self.assertEqual(result["_exit"], 1)
                    self.assertEqual((acquire / "flash.bin").read_bytes(), image)
                    self.assertEqual(result["raw_flash"], str((acquire / "flash.bin").resolve()))
                    self.assertEqual(seen, [])
                else:
                    self.assertTrue(result["available"], result)
                    self.assertEqual(seen, [image[want[0]:want[0] + want[1]]])

    def test_esp_flash_resets_even_when_a_local_write_fails_after_contact(self):
        import builtins
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        real_open = builtins.open
        def failing_open(path, *args, **kwargs):
            if str(path).endswith("efuse.txt"):
                raise OSError("disk full")
            return real_open(path, *args, **kwargs)
        commands = []
        image = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        with tempfile.TemporaryDirectory() as temp, patch.object(core, "sh", self._esp_fake_sh(commands, image)), \
                patch("builtins.open", failing_open):
            result = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32", "allow_reset": True})
        self.assertFalse(result.get("available"), result)
        self.assertIn("hard-reset", commands[-1])
        self.assertIn("chip-id", commands[-1])

    def test_esp_flash_binary_fallback_is_named_as_a_binary_not_an_elf(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        image = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(core, "sh", self._esp_fake_sh([], image, convert_ok=False)):
                flash = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32"})
                self.assertTrue(flash["available"], flash)
                self.assertTrue(flash["interpret_input"].endswith("interpret-input.bin"))
                self.assertEqual(Path(flash["interpret_input"]).read_bytes(), image[0x10000:0x10100])
            with tempfile.TemporaryDirectory() as other, patch.object(core, "sh", self._esp_fake_sh([], image, convert_ok=False)):
                result = call_tool("acquire_classify", {"run_dir": other, "port": "/dev/cu.fake", "flash_backend": "esp32"})
                self.assertEqual(result["device_class"], 1, result)
                self.assertTrue(result["interpret_input"].endswith("interpret-input.bin"))
                self.assertNotIn("elf", result["inputs_for_interpret"])
                self.assertEqual(result["inputs_for_interpret"], {"image.bin": result["interpret_input"]})
                self.assertEqual(result["front_end"], "interpret-interface")

    def test_classify_forwards_the_grant_and_reports_the_interpret_input(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        commands = []
        image = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        with tempfile.TemporaryDirectory() as temp, patch.object(core, "sh", self._esp_fake_sh(commands, image)):
            result = call_tool("acquire_classify", {"run_dir": temp, "port": "/dev/cu.fake",
                                                    "flash_backend": "esp32", "allow_reset": True})
            self.assertEqual(result["device_class"], 1, result)
            self.assertTrue(result["available"])
            self.assertTrue(result["interpret_input"].endswith("interpret-input.elf"))
            self.assertTrue(result["artifacts"])
            self.assertTrue(any("default-reset" in c for c in commands))

    def test_01_B6_classification_names_no_front_end_for_an_elf_image_a_datasheet_or_a_bus_scan(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import acquire, core

        class Bus:
            def __init__(self, url, speed):
                pass

            def probe(self, address):
                return address == 0x42

            def close(self):
                pass

        def datasheet(run_dir, part_hint=None):
            # What acquire_datasheet returns for a part it resolved; the real tool fetches the vendor's file.
            return {"available": True, "artifact": "acquire/datasheet.pdf", "pages_dir": "acquire/pages",
                    "revision": "1.24"}

        image = self._esp_flash_image([(0, 0x00, 0x10000, 0x100, b"factory")])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            controller = {"port": "/dev/cu.fake", "flash_backend": "esp32"}
            with patch.object(core, "sh", self._esp_fake_sh([], image)):
                elf = call_tool("acquire_classify", {"run_dir": str(root / "elf"), **controller})
            with patch.object(core, "sh", self._esp_fake_sh([], image, convert_ok=False)):
                raw = call_tool("acquire_classify", {"run_dir": str(root / "raw"), **controller})
            with patch.object(acquire, "acquire_datasheet", datasheet):
                documented = call_tool("acquire_classify", {"run_dir": str(root / "datasheet"), "part_hint": "bme280"})
            with patch("generative_driver._toolchain.runner.FtdiI2c", Bus):
                scanned = call_tool("acquire_classify", {"run_dir": str(root / "scan"),
                                                         "bus_url": "ftdi://ftdi:232h/1"})
            scan = json.loads((root / "scan" / "acquire" / "scan.json").read_text(encoding="utf-8"))
            self.assertEqual(scan["acks"], ["0x42"])
        fields = {"available", "device_class", "artifact", "artifacts", "rungs", "inputs_for_interpret", "_event",
                  "_exit"}
        for description, result, device_class, more in (
                ("elf image", elf, 1, {"interpret_input"}), ("datasheet", documented, 2, set()),
                ("bus scan", scanned, 4, set())):
            with self.subTest(description=description):
                self.assertNotIn("front_end", result)
                self.assertEqual(set(result), fields | more, result)
                self.assertEqual((result["available"], result["device_class"], result["_exit"]),
                                 (True, device_class, 0))
        self.assertTrue(elf["interpret_input"].endswith("interpret-input.elf"))
        self.assertEqual(elf["inputs_for_interpret"], {"elf": elf["artifact"]})
        self.assertIn(elf["artifact"], elf["artifacts"])
        self.assertEqual(documented["inputs_for_interpret"],
                         {"datasheet.pdf": "acquire/datasheet.pdf", "pages": "acquire/pages"})
        self.assertEqual((documented["artifact"], documented["artifacts"]),
                         ("acquire/datasheet.pdf", ["acquire/datasheet.pdf", "acquire/pages"]))
        self.assertEqual(scanned["inputs_for_interpret"], {"scan": scanned["artifact"]})
        self.assertEqual(scanned["artifacts"], [scanned["artifact"]])
        self.assertEqual([(rung["name"], rung["tried"]) for rung in scanned["rungs"]],
                         [("flash", False), ("datasheet", True), ("selfdescription", True), ("blackbox", True)])
        # A raw image names the front end a run gives it.
        self.assertEqual(raw["front_end"], "interpret-interface")
        self.assertEqual(set(raw), fields | {"interpret_input", "front_end"}, raw)

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

    def test_serial_listen_records_capture_without_transmitting(self):
        import sys, types
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        writes = []
        class Port:
            def __init__(self): self.chunks = [b"ESP-ROM:esp32s3\r\n", b"waiting for download\r\n"]
            def open(self): pass
            def reset_input_buffer(self): pass
            def read(self, size=1): return self.chunks.pop(0) if self.chunks else b""
            def write(self, data): writes.append(data)
            def close(self): pass
        module = types.ModuleType("serial"); module.Serial = Port
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"serial": module}):
            result = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.3})
            self.assertTrue(result["available"], result)
            self.assertTrue(result["unsolicited_output"])
            self.assertEqual(writes, [])
            capture = json.loads(Path(result["artifact"]).read_text())
            self.assertIn("waiting for download", capture["text"])
            self.assertEqual(Path(result["artifacts"][0]).read_bytes(), b"ESP-ROM:esp32s3\r\nwaiting for download\r\n")
            refused = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 31})
            self.assertFalse(refused["available"])

    def test_serial_listens_never_overwrite_and_never_look_like_an_image(self):
        import sys, types
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        class Quiet:
            def open(self): pass
            def read(self, size=1): return b""
            def close(self): pass
        module = types.ModuleType("serial"); module.Serial = Quiet
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"serial": module}):
            first = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.1})
            before = {p: core.sha256(p) for p in first["artifacts"]}
            second = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.1})
            paths = first["artifacts"] + second["artifacts"]
            self.assertEqual(len(set(paths)), 4, paths)
            self.assertEqual(before, {p: core.sha256(p) for p in first["artifacts"]})
            self.assertTrue(all(Path(p).is_file() for p in paths))
            self.assertFalse(any(Path(p).suffix.lower() in (".bin", ".elf") for p in paths), paths)

    def test_serial_listen_without_pyserial_names_the_extra_as_a_host_fault(self):
        import sys
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"serial": None}):
            result = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.1})
        self.assertFalse(result["available"], result)
        self.assertEqual(result["fault"], "host")
        self.assertIn("generative-driver[hardware]", result["reason"])

    def test_coverage_scope_does_not_claim_a_connection_per_call(self):
        from interface_runtime.evidence import COVERAGE_SCOPE
        self.assertNotIn("separate connection", COVERAGE_SCOPE)
        self.assertIn("resets_on_open", COVERAGE_SCOPE)

    def test_serial_listen_on_a_quiet_device_is_evidence_without_reset(self):
        import sys, types
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        class Quiet:
            def open(self): pass
            def read(self, size=1): return b""
            def close(self): pass
        module = types.ModuleType("serial"); module.Serial = Quiet
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"serial": module}):
            result = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.2})
        self.assertTrue(result["available"])
        self.assertFalse(result["unsolicited_output"])
        self.assertEqual((result["bytes"], result["first_byte_ms"]), (0, None))

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

    def test_01_B4_the_tool_list_holds_no_register_tool_and_none_of_its_parameters(self):
        from generative_driver.toolkit import call_tool, list_tools
        from package_env import package_env
        from test_package import example_model
        command = subprocess.run([sys.executable, "-m", "generative_driver", "tools"], capture_output=True, text=True,
                                 env={**os.environ, **package_env()}, timeout=60)
        self.assertEqual(command.returncode, 0, command.stderr)
        removed = {"interpret_run": {"wiring"}, "emit_package": {"wiring"},
                   "probe_run": {"bus_url", "period_override_ms"}, "model_validate": {"random_files", "seed"},
                   "emit_check": {"seeds", "seed", "tol_rel"}}
        for source, listed in (("list_tools()", list_tools()), ("generative-driver tools", json.loads(command.stdout))):
            with self.subTest(source=source):
                tools = {tool["name"]: tool for tool in listed}
                self.assertEqual({"probe_observe", "model_provenance_audit"} & tools.keys(), set())
                for name, parameters in removed.items():
                    self.assertEqual(parameters & tools[name]["inputSchema"]["properties"].keys(), set(), name)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name in ("probe_observe", "model_provenance_audit"):
                with self.subTest(tool=name):
                    refused = call_tool(name, {"run_dir": str(root / "run"), "model_dir": str(root / "model")})
                    self.assertEqual((refused.get("ok"), refused["_exit"], refused.get("error")),
                                     (False, 1, "Unknown tool: " + name), refused)
            # A caller that still passes a removed parameter is not refused for it: the tool ignores it.
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(json.dumps(example_model()), encoding="utf-8")
            checked = call_tool("model_validate", {"run_dir": str(root / "run"), "model_dir": str(model),
                                                   "random_files": 3, "seed": 7})
            self.assertEqual((checked.get("ok"), checked["_exit"]), (True, 0), checked)

    def test_01_B1_the_tool_list_holds_no_tool_of_the_static_path_and_each_is_an_unknown_tool(self):
        from generative_driver.toolkit import call_tool, list_tools
        from package_env import package_env
        static = ("firmware_prepare", "firmware_collect", "firmware_validate")
        command = subprocess.run([sys.executable, "-m", "generative_driver", "tools"], capture_output=True, text=True,
                                 env={**os.environ, **package_env()}, timeout=60)
        self.assertEqual(command.returncode, 0, command.stderr)
        for source, listed in (("list_tools()", list_tools()), ("generative-driver tools", json.loads(command.stdout))):
            with self.subTest(source=source):
                self.assertEqual({tool["name"] for tool in listed} & set(static), set())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for name in static:
                with self.subTest(tool=name):
                    refused = call_tool(name, {"run_dir": str(root / "run"), "model_dir": str(root / "model")})
                    self.assertEqual(refused, {"ok": False, "_exit": 1, "error": "Unknown tool: " + name})
            self.assertFalse((root / "run").exists())

    def test_01_B2_model_validate_refuses_a_static_description_by_its_format_and_writes_nothing_beside_it(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model = root / "model"
            (model / "exports").mkdir(parents=True)
            # A static description that the validator of its format accepted: evidence at a file offset of an export.
            (model / "exports/handler.txt").write_text("00000010: accepts 51; emits 52\n", encoding="utf-8")
            (model / "model.json").write_text(json.dumps({
                "schema": "interface-model/2-experimental", "device": {"identity": "unknown"},
                "channel": {"type": "unknown"}, "operations": [{"id": "status", "effect_class": "read_only",
                    "request": {"bytes_hex": "51"}, "response_interpretation": {"meaning": "status byte"},
                    "evidence": [{"file_offset": "0x10", "export": "exports/handler.txt"}]}],
                "limits": {}, "safety": {}, "provenance": {"source": "image.bin"},
                "uncertainties": ["Physical meaning is unproved."]}), encoding="utf-8")
            supplied = {path.relative_to(model).as_posix(): path.read_bytes()
                        for path in sorted(model.rglob("*")) if path.is_file()}
            refused = call_tool("model_validate", {"run_dir": str(root / "run"), "model_dir": str(model)})
            self.assertEqual((refused.get("ok"), refused["_exit"], refused.get("advice")), (False, 1, []), refused)
            [defect] = refused["defects"]
            self.assertEqual(defect["where"], "schema", refused)
            self.assertIn("interface-model/2-experimental", defect["what"])
            self.assertIn("interface-model/7", defect["what"])
            self.assertEqual({path.relative_to(model).as_posix(): path.read_bytes()
                              for path in sorted(model.rglob("*")) if path.is_file()}, supplied)

    def test_01_B3_an_imported_image_names_interpret_interface_and_interpret_run_prepares_it(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            run = root / "run"
            source = root / "provided.bin"
            source.write_bytes(bytes(range(64)))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            acquired = call_tool("acquire_firmware_artifact", {"run_dir": str(run), "source_path": str(source),
                                                               "origin": "provided_binary", "expected_sha256": digest})
            bundle = run / "acquire" / "firmware_artifact"
            self.assertEqual(acquired, {
                "available": True, "rung": "firmware_artifact", "artifact": str(bundle / "image.bin"),
                "provenance": str(bundle / "provenance.json"), "sha256": digest, "size": 64,
                "front_end": "interpret-interface", "next_tool": "interpret_run",
                "inputs_for_interpret": {"image.bin": str(bundle / "image.bin")}, "_event": 1, "_exit": 0})
            prepared = call_tool("interpret_run", {"run_dir": str(run), "front_end": acquired["front_end"],
                                                   "inputs": acquired["inputs_for_interpret"]})
            self.assertEqual((prepared.get("ok"), prepared["_exit"]), (True, 0), prepared)
            self.assertEqual((Path(prepared["workspace"]) / "image.bin").read_bytes(), bytes(range(64)))

    def test_01_B4_no_stage_names_a_tool_the_tool_list_does_not_hold(self):
        from generative_driver.configurator import STAGE_TOOLS
        from generative_driver.toolkit import list_tools
        listed = {tool["name"] for tool in list_tools()}
        unlisted = {stage: [name for name in tools if name not in listed] for stage, tools in STAGE_TOOLS.items()}
        self.assertEqual(unlisted, {stage: [] for stage in STAGE_TOOLS})
        self.assertEqual(STAGE_TOOLS["interpret"], ["model_validate", "ghidra_run", "image_map_check"])

    def test_01_B6_no_text_of_the_installed_resources_and_no_tool_description_names_the_static_path(self):
        from generative_driver.toolkit import list_tools, resources_root
        from worker_texts import STATIC, texts_of
        texts = texts_of(resources_root())
        self.assertTrue({"toolchain/tools.json", "docs/INTERFACE_MODEL.md", "tools/workspace/ghidra_run.py",
                         "tools/workspace/ExportDecomp.java"} <= set(texts), sorted(texts)[:20])
        texts.update({"description of " + tool["name"]: tool["description"] for tool in list_tools()})
        # A text is read as one line, so words that a line break divides are found.
        named = sorted((name, words) for name, text in texts.items()
                       for words in STATIC.findall(" ".join(text.split())))
        self.assertEqual(named, [])


if __name__ == "__main__":
    unittest.main()

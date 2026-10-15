"""ghidra_run's export verification through the installed toolchain facade, against a scripted analyzer.

A small Python program stands in for analyzeHeadless and writes the export files each case names, so these are
contract tests of the verification and say nothing about Ghidra. native_ghidra_exports.py runs the real exporter.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LAUNCHER = '''import json, pathlib, sys
spec = pathlib.Path(sys.argv[1])
argv = sys.argv[2:]
spec.with_name("argv.json").write_text(json.dumps(argv), encoding="utf-8")
export = pathlib.Path(argv[argv.index("ExportDecomp.java") + 1])
export.mkdir(parents=True, exist_ok=True)
for name, text in json.loads(spec.read_text(encoding="utf-8")).items():
    (export / name).write_bytes(text.encode("utf-8"))
print("INFO  Import succeeded")
'''

CLASSIC = {
    "functions.tsv": "address\tname\tdecompilation_complete\n08000040\tFUN_08000040\ttrue\n",
    "function_08000040.c": "// entry address: 08000040\nvoid FUN_08000040(void)\n{\n  return;\n}\n",
    "disassembly.txt": "08000040\tbx lr\n",
}
REFERENCES = ("from_address\tfrom_function\tto_address\tto_function\ttype\n"
              "08000042\t08000040\t08000058\t08000058\tUNCONDITIONAL_CALL\n")
STRINGS = ("address\tencoding\tlength\tclipped\tfunctions\trefs_outside_functions\tvalue\n"
           "080000d0\tUS-ASCII\t18\tfalse\t0800005c,0800009c\t\thello from fixture\n")
ONE_ROW = {"rows": 1, "rows_found": 1, "truncated": False}


def manifest(max_bytes=16777216, **tables):
    """navigation.json for one-row tables, with `tables` (references_tsv=...) replacing their counts."""
    counts = {"references.tsv": ONE_ROW, "strings.tsv": ONE_ROW}
    counts.update({name.replace("_", "."): value for name, value in tables.items()})
    return json.dumps({"schema": "ghidra-navigation/1", "max_bytes": max_bytes, "tables": counts})


def export(references=REFERENCES, strings=STRINGS, navigation=None):
    return {**CLASSIC, "references.tsv": references, "strings.tsv": strings,
            "navigation.json": navigation if navigation is not None else manifest()}


def exporter_arguments(root):
    """The arguments the scripted analyzer received for ExportDecomp.java."""
    argv = json.loads((root / "argv.json").read_text(encoding="utf-8"))
    return argv[argv.index("ExportDecomp.java") + 1:argv.index("-log")]


class GhidraExportVerificationTests(unittest.TestCase):
    def scripted_ghidra(self, files):
        """A directory holding a scripted analyzer that writes `files`, a JDK stand-in and an image."""
        root = Path(tempfile.mkdtemp(prefix="gdexports-")).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        support = root / "ghidra/support"; support.mkdir(parents=True)
        java = root / "jdk/bin"; java.mkdir(parents=True)
        spec = root / "spec.json"
        spec.write_text(json.dumps(files), encoding="utf-8")
        launcher = root / "launcher.py"
        launcher.write_text(LAUNCHER, encoding="utf-8")
        if os.name == "nt":
            (support / "analyzeHeadless.bat").write_text(
                f'@"{sys.executable}" "{launcher}" "{spec}" %*\n', encoding="utf-8")
            (java / "java.exe").write_bytes(b"placeholder")
        else:
            script = support / "analyzeHeadless"
            script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{launcher}" "{spec}" "$@"\n', encoding="utf-8")
            script.chmod(0o700)
            (java / "java").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (java / "java").chmod(0o700)
        (root / "image.bin").write_bytes(bytes(64))
        return root

    def analyze(self, files, **arguments):
        """Run the ghidra_run tool against a scripted analyzer that writes `files`; return the result and root."""
        from generative_driver.toolkit import call_tool
        root = self.scripted_ghidra(files)
        result = call_tool("ghidra_run", {"run_dir": str(root / "run"), "workspace": str(root / "analysis"),
            "image": str(root / "image.bin"), "base_address": "08000000", "processor": "ARM:LE:32:Cortex",
            "ghidra_path": str(root / "ghidra"), "java_home": str(root / "jdk"),
            "export_dir": str(root / "exports"), "timeout_s": 60, **arguments})
        return result, root

    def test_a_truncated_reference_table_verifies_is_reported_and_is_hashed(self):
        files = export(navigation=manifest(references_tsv={"rows": 1, "rows_found": 3, "truncated": True}))
        result, root = self.analyze(files)
        self.assertTrue(result["ok"], result.get("reasons"))
        table = result["exports"]["navigation"]["tables"]["references.tsv"]
        self.assertEqual((table["rows"], table["rows_found"], table["truncated"]), (1, 3, True))
        self.assertEqual(table["bytes"], len(REFERENCES.encode("utf-8")))
        self.assertEqual(result["exports"]["truncated"], ["references.tsv"])
        events = [json.loads(line) for line in (root / "run/events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["files"].get(str(root / "exports/references.tsv")),
                         hashlib.sha256(REFERENCES.encode("utf-8")).hexdigest())

    def test_a_missing_or_inconsistent_navigation_table_fails_verification(self):
        header = REFERENCES.split("\n", 1)[0] + "\n"
        long_row = "08000042\t08000040\t08000058\t08000058\t" + "X" * 1100 + "\n"
        cases = {
            "table missing": ({k: v for k, v in export().items() if k != "references.tsv"}, "references.tsv"),
            "manifest missing": ({k: v for k, v in export().items() if k != "navigation.json"}, "navigation.json"),
            "wrong header": (export(references="source\tsource_function\ttarget\ttarget_function\tkind\n"
                                               + REFERENCES.split("\n", 1)[1]), "references.tsv"),
            "stray field": (export(references=header + "08000042\t08000040\t08000058\t08000058\tDATA\textra\n"),
                            "references.tsv"),
            "row count": (export(navigation=manifest(references_tsv={"rows": 2, "rows_found": 2,
                                                                     "truncated": False})), "references.tsv"),
            "unreported truncation": (export(navigation=manifest(references_tsv={"rows": 1, "rows_found": 3,
                                                                                 "truncated": False})),
                                      "references.tsv"),
            "over the bound": (export(references=header + long_row, navigation=manifest(
                1024, references_tsv={"rows": 1, "rows_found": 1, "truncated": False})), "references.tsv"),
            "strings table missing": ({k: v for k, v in export().items() if k != "strings.tsv"}, "strings.tsv"),
            "raw tab in a string value": (export(strings=STRINGS.replace("hello from", "hello\tfrom")),
                                          "strings.tsv"),
        }
        for name, (files, culprit) in cases.items():
            with self.subTest(name):
                result, _ = self.analyze(files)
                self.assertFalse(result["ok"], result.get("exports"))
                self.assertTrue(any(culprit in reason for reason in result["reasons"]), result["reasons"])

    def test_a_requested_table_bound_reaches_the_exporter_and_must_be_the_one_it_records(self):
        recorded = lambda bound: export(navigation=manifest(bound, references_tsv={"rows": 1, "rows_found": 1,
                                                                                    "truncated": False}))
        result, root = self.analyze(recorded(16777216))
        self.assertTrue(result["ok"], result.get("reasons"))
        self.assertEqual(exporter_arguments(root), [str(root / "exports")])
        result, root = self.analyze(recorded(4096), navigation_max_bytes=4096)
        self.assertTrue(result["ok"], result.get("reasons"))
        self.assertEqual(exporter_arguments(root), [str(root / "exports"), "4096"])
        self.assertEqual(result["exports"]["navigation"]["max_bytes"], 4096)
        result, _ = self.analyze(recorded(16777216), navigation_max_bytes=4096)
        self.assertFalse(result["ok"])
        self.assertTrue(any("navigation.json" in reason for reason in result["reasons"]), result["reasons"])
        for bound in (1023, (1 << 30) + 1, True, "4096"):
            with self.subTest(bound=bound):
                result, root = self.analyze(recorded(4096), navigation_max_bytes=bound)
                self.assertFalse(result["ok"])
                self.assertTrue(any("navigation_max_bytes" in reason for reason in result["reasons"]), result)
                self.assertFalse((root / "argv.json").exists())

    def test_the_workspace_helper_passes_a_requested_bound_from_its_command_line(self):
        from generative_driver.toolkit import resources_root
        root = self.scripted_ghidra(export(navigation=manifest(4096, references_tsv={"rows": 1, "rows_found": 1,
                                                                                     "truncated": False})))
        completed = subprocess.run([sys.executable, str(resources_root() / "tools/workspace/ghidra_run.py"),
            "--workspace", str(root / "analysis"), "--image", str(root / "image.bin"), "--base-address", "08000000",
            "--processor", "ARM:LE:32:Cortex", "--ghidra-path", str(root / "ghidra"), "--java-home", str(root / "jdk"),
            "--export-dir", str(root / "exports"),
            "--script-path", str(resources_root() / "toolchain/skills/interpret-firmware-binary"),
            "--navigation-max-bytes", "4096"], capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(exporter_arguments(root), [str(root / "exports"), "4096"])
        self.assertEqual(json.loads(completed.stdout)["exports"]["navigation"]["max_bytes"], 4096)


if __name__ == "__main__":
    unittest.main()

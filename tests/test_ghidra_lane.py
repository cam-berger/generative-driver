"""The real-Ghidra lane's verdict, checked without Ghidra: an export that misses the fixture must fail."""
import tempfile
import unittest
from pathlib import Path

from native_ghidra import export_problems

MANIFEST = {"base_address": "0x08000000", "processor": "ARM:LE:32:Cortex",
            "functions": [{"name": "reset_handler", "address": "0x08000008"},
                          {"name": "classify", "address": "0x08000084"}]}


def exported(root, rows):
    """A ghidra_run result whose ExportDecomp.java output lists `rows` as (address, complete)."""
    exports = root / "exports"
    exports.mkdir()
    (exports / "functions.tsv").write_text("address\tname\tdecompilation_complete\n" + "".join(
        f"{address}\tFUN_{address}\t{str(complete).lower()}\n" for address, complete in rows), encoding="utf-8")
    for address, complete in rows:
        body = "uint FUN_%s(void)\n{\n  return 0;\n}\n" % address if complete else "// DECOMPILATION FAILED: timeout\n"
        (exports / f"function_{address}.c").write_text(f"// entry address: {address}\n{body}", encoding="utf-8")
    (exports / "disassembly.txt").write_text("".join(f"{address}\tbx lr\n" for address, _ in rows), encoding="utf-8")
    markers = {"import_succeeded": True, "analysis_succeeded": True, "post_analysis_succeeded": True,
               "save_succeeded": True, "script_error": False, "invalid_loader": False, "import_failed": False}
    return {"ok": True, "reasons": [], "markers": markers, "loader_used": "Raw Binary",
            "language_used": "ARM:LE:32:Cortex:default", "base_address": "0x08000000",
            "base_address_applied": 'old: "null", new: "08000000"', "settings_in_workspace": True,
            "export_dir": str(exports), "exports": {"dir": str(exports), "verified": True}}


class GhidraLaneVerdictTests(unittest.TestCase):
    def test_export_of_every_fixture_function_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = exported(Path(temp), [("08000008", True), ("08000030", True), ("08000084", True)])
            self.assertEqual(export_problems(result, MANIFEST), [])

    def test_export_missing_a_fixture_function_names_it(self):
        with tempfile.TemporaryDirectory() as temp:
            result = exported(Path(temp), [("08000008", True), ("08000030", True)])
            problems = export_problems(result, MANIFEST)
            self.assertTrue(any("classify" in p and "08000084" in p for p in problems), problems)

    def test_function_without_completed_decompilation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            result = exported(Path(temp), [("08000008", True), ("08000084", False)])
            problems = export_problems(result, MANIFEST)
            self.assertTrue(any("classify" in p and "decompil" in p for p in problems), problems)

    def test_import_through_another_loader_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            result = exported(Path(temp), [("08000008", True), ("08000084", True)])
            result["loader_used"] = "Executable and Linking Format (ELF)"
            self.assertTrue(any("Raw Binary" in p for p in export_problems(result, MANIFEST)))


if __name__ == "__main__":
    unittest.main()

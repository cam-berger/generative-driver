"""Explicit native gate for ExportDecomp.java: real headless Ghidra on a hand-assembled Cortex-M3 image.

Select it with `python -m unittest discover -s tests -p native_ghidra_exports.py` and set GD_NATIVE_GHIDRA_HOME and
GD_NATIVE_JAVA_HOME. No configuration means failure, never a skip. The image is built here from the listing below; it
derives from no benchmark source, answer key or device firmware.
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

BASE = 0x08000000
RESET, DISPATCH, CLASSIFY, GREET, LOOKUP, BANNER = (0x08000040, 0x0800005C, 0x08000080, 0x0800009C, 0x080000A4,
                                                    0x080000B0)
NAMES, HELLO = 0x080000C0, 0x080000D0
COMMANDS = {0x080000E4: "read-status", 0x080000F0: "write-config", 0x08000100: "reset-device",
            0x08000110: "identify-unit"}
ESCAPED, LONG = 0x08000120, 0x08000138
LONG_TEXT = "status register value " * 230   # 5060 characters, more than the exporter shows of one string


def cortex_m_image():
    """A Cortex-M3 image assembled by hand; each comment gives the Thumb instruction its halfwords encode."""
    code = bytearray()

    def at(address):
        assert BASE + len(code) == address, hex(BASE + len(code))

    def half(*values):
        for value in values:
            code.extend(value.to_bytes(2, "little"))

    def word(value):
        code.extend(value.to_bytes(4, "little"))

    def bl(target):
        offset = target - (BASE + len(code) + 4)
        assert 0 <= offset < 1 << 22
        half(0xF000 | offset >> 12, 0xF800 | (offset >> 1) & 0x7FF)

    def cstring(text):
        code.extend(text.encode("ascii") + b"\0")
        code.extend(bytes(-len(code) % 4))

    word(0x20001000)                     # initial stack pointer
    word(RESET | 1)                      # reset vector, Thumb
    code.extend(bytes(0x38))             # the other vectors are unused
    at(RESET)
    half(0x2002); bl(DISPATCH)           # movs r0,#2; bl dispatch
    half(0x2001); bl(CLASSIFY)           # movs r0,#1; bl classify
    bl(GREET)                            # bl greet
    half(0x2003); bl(LOOKUP)             # movs r0,#3; bl lookup
    bl(BANNER)                           # bl banner
    half(0xE7FE)                         # b .
    at(DISPATCH)                         # a switch whose bounds check leads to its own default block
    half(0x2803, 0xD80B)                 # cmp r0,#3; bhi 0x08000078
    half(0xE8DF, 0xF000)                 # tbb [pc,r0]
    code.extend(bytes([2, 4, 6, 8]))     # cases 0..3 -> 0x08000068, 6c, 70, 74
    half(0x4804, 0x4770)                 # case 0: ldr r0,[pc,#0x10] (hello); bx lr
    half(0x2011, 0x4770)                 # case 1: movs r0,#0x11; bx lr
    half(0x2022, 0x4770)                 # case 2: movs r0,#0x22; bx lr
    half(0x2033, 0x4770)                 # case 3: movs r0,#0x33; bx lr
    half(0x2000, 0x4770)                 # default: movs r0,#0; bx lr
    word(HELLO)                          # literal pool
    at(CLASSIFY)                         # a switch whose bounds check joins case 2
    half(0x2803, 0xD809)                 # cmp r0,#3; bhi 0x08000098
    half(0xE8DF, 0xF000)                 # tbb [pc,r0]
    code.extend(bytes([2, 4, 8, 6]))     # cases 0..3 -> 0x0800008c, 90, 98, 94
    half(0x200A, 0x4770)                 # case 0: movs r0,#0xa; bx lr
    half(0x200B, 0x4770)                 # case 1: movs r0,#0xb; bx lr
    half(0x200C, 0x4770)                 # case 3: movs r0,#0xc; bx lr
    half(0x2000, 0x4770)                 # case 2 and default: movs r0,#0; bx lr
    at(GREET)
    half(0x4800, 0x4770)                 # ldr r0,[pc,#0] (hello); bx lr
    word(HELLO)
    at(LOOKUP)
    half(0x4901)                         # ldr r1,[pc,#4] (names)
    half(0xF851, 0x0020)                 # ldr.w r0,[r1,r0,lsl #2]
    half(0x4770)                         # bx lr
    word(NAMES)
    at(BANNER)
    half(0x4801)                         # ldr r0,[pc,#4] (escaped)
    half(0x4902)                         # ldr r1,[pc,#8] (long)
    half(0x4770, 0x0000)                 # bx lr; padding
    word(ESCAPED)
    word(LONG)
    at(NAMES)                            # a table of pointers to command names
    for address in COMMANDS:
        word(address)
    at(HELLO)
    cstring("hello from fixture")
    for address, text in COMMANDS.items():
        at(address)
        cstring(text)
    at(ESCAPED)
    cstring("tab\there\r\nback\\slash")
    at(LONG)
    cstring(LONG_TEXT)
    return bytes(code)


def analyze(**arguments):
    from generative_driver.toolkit import call_tool
    for name in ("GD_NATIVE_GHIDRA_HOME", "GD_NATIVE_JAVA_HOME"):
        assert name in os.environ, "Native gate requires explicit " + name
    root = Path(tempfile.mkdtemp(prefix="gd-ghidra-exports-")).resolve()
    image = root / "image.bin"
    image.write_bytes(cortex_m_image())
    result = call_tool("ghidra_run", {"run_dir": str(root / "run"), "workspace": str(root / "analysis"),
        "image": str(image), "base_address": "08000000", "processor": "ARM:LE:32:Cortex",
        "ghidra_path": os.environ["GD_NATIVE_GHIDRA_HOME"], "java_home": os.environ["GD_NATIVE_JAVA_HOME"],
        "export_dir": str(root / "exports"), "prescripts": ["SeedCortexM.java"], "timeout_s": 300, **arguments})
    return result, root


def rows(root, name):
    lines = (root / "exports" / name).read_text(encoding="utf-8").split("\n")
    return [tuple(line.split("\t")) for line in lines[1:-1]]


class NativeGhidraExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result, cls.root = analyze()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, True)

    def test_the_reference_table_names_both_ends_and_their_functions(self):
        self.assertTrue(self.result["ok"], self.result.get("reasons"))
        references = rows(self.root, "references.tsv")
        for row in (("08000042", "08000040", "0800005c", "0800005c", "UNCONDITIONAL_CALL"),
                    ("08000052", "08000040", "080000a4", "080000a4", "UNCONDITIONAL_CALL"),
                    ("08000068", "0800005c", "0800007c", "", "READ"),
                    ("080000c0", "", "080000e4", "", "DATA")):
            self.assertIn(row, references)
        self.assertEqual(self.result["exports"]["truncated"], [])

    def test_the_strings_table_gives_each_string_its_users_and_an_escaped_bounded_value(self):
        self.assertTrue(self.result["ok"], self.result.get("reasons"))
        self.assertEqual(rows(self.root, "strings.tsv"), [
            ("080000d0", "US-ASCII", "18", "false", "0800005c,0800009c", "", "hello from fixture"),
            ("080000e4", "US-ASCII", "11", "false", "", "080000c0", "read-status"),
            ("080000f0", "US-ASCII", "12", "false", "", "080000c4", "write-config"),
            ("08000100", "US-ASCII", "12", "false", "", "080000c8", "reset-device"),
            ("08000110", "US-ASCII", "13", "false", "", "080000cc", "identify-unit"),
            ("08000120", "US-ASCII", "20", "false", "080000b0", "", r"tab\there\r\nback\\slash"),
            ("08000138", "US-ASCII", "5060", "true", "", "", LONG_TEXT[:4096])])

    def test_a_small_bound_keeps_the_leading_rows_of_each_table_and_reports_the_rest(self):
        result, root = analyze(navigation_max_bytes=1024)
        self.addCleanup(shutil.rmtree, root, True)
        self.assertTrue(result["ok"], result.get("reasons"))
        self.assertEqual(result["exports"]["truncated"], ["references.tsv", "strings.tsv"])
        for name, table in result["exports"]["navigation"]["tables"].items():
            with self.subTest(name):
                complete, kept = rows(self.root, name), rows(root, name)
                self.assertLessEqual((root / "exports" / name).stat().st_size, 1024)
                self.assertEqual(kept, complete[:len(kept)])
                self.assertEqual((table["rows"], table["rows_found"]), (len(kept), len(complete)))
                self.assertEqual(table["truncated"], len(kept) < len(complete))


if __name__ == "__main__":
    unittest.main()

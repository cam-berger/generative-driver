"""The service has no part left for the two dropped benchmark cases, the old `tq9` and the physical `bme280`.

Feature 04 of the untangle Run (docs/features/untangle/04-legacy-cases.md), 04-B4. This file imports nothing from the
benchmark package, so it stays when the benchmark is deleted and runs in the stripped copy. The benchmark's side of
the Feature is tested in `firmware-bench/tests/test_benchmark_legacy_cases_dropped.py`.
"""
import ast
from pathlib import Path
import unittest

import generative_driver
from generative_driver import configurator

SOURCE = Path(generative_driver.__file__).resolve().parent
# The benchmark's own modules, which are not the tool's.
BENCHMARK = ('benchmark.py', 'benchmark_support')
GONE = 'package_execute'


def tool_modules():
    """The Python modules of the tool: every module of the package that is not one of the benchmark's own."""
    return sorted(path for path in SOURCE.rglob('*.py') if path.relative_to(SOURCE).parts[0] not in BENCHMARK)


def imports(source, name):
    """Whether an import statement of the Python `source`, at any depth, takes `name`: as the name it imports from a
    module, or as the last part of a module it imports."""
    return any(alias.name.split('.')[-1] == name for node in ast.walk(ast.parse(source))
               if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names)


class ServiceWithoutLegacyCasesTests(unittest.TestCase):
    def test_04_B4_the_service_no_longer_has_the_note_of_a_run_without_a_harness_device(self):
        self.assertFalse(hasattr(configurator, 'NO_HARNESS_DEVICE'), 'configurator still has NO_HARNESS_DEVICE')

    def test_04_B4_the_import_check_sees_each_form_and_not_the_device_tool_of_every_case(self):
        for line in ('from .benchmark import package_execute', 'from .benchmark import package_call, package_execute',
                     'def dispatch():\n    from .benchmark import package_execute as run',
                     'import generative_driver.benchmark.package_execute'):
            with self.subTest(line=line):
                self.assertTrue(imports(line, GONE))
        for line in ('from .benchmark import package_call', "NAME = 'benchmark_package_execute'",
                     'from .worker_tools import benchmark_package_execute', 'package_execute = None'):
            with self.subTest(line=line):
                self.assertFalse(imports(line, GONE))

    def test_04_B4_no_tool_module_imports_package_execute(self):
        modules = tool_modules()
        names = [path.relative_to(SOURCE).as_posix() for path in modules]
        for name in ('configurator.py', 'device_access.py', 'mcp.py', 'cli.py', '_toolchain/interpret.py'):
            self.assertIn(name, names)
        self.assertFalse([name for name in names if name.split('/')[0] in BENCHMARK])
        found = [name for name, path in zip(names, modules) if imports(path.read_bytes(), GONE)]
        self.assertEqual(found, [])

    def test_04_B4_emulator_approval_without_a_device_names_no_case(self):
        # The refusal named a current case until the service stopped knowing cases (06-B12 of the untangle Run).
        with self.assertRaisesRegex(ValueError, '^Emulator approval requires a supplied device$'):
            configurator.validate_scoped_approval({'scoped_tool_approval': 'emulator'})


if __name__ == '__main__':
    unittest.main()

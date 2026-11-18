"""The tool's side of the boundary between the tool and the benchmark.

Feature 10 of the untangle Run (docs/features/untangle/10-tests.md), 10-B1 and 10-B9: no tool module and no tool test
file imports the benchmark's code, under any of its names, and no tool test file names a path of the benchmark's
resources or of its published record. A tool test file is a Python file under `tests/` other than the check of the
deletion list and its tests, which the deletion removes last. This module imports nothing of the benchmark, so it
also runs where the benchmark is gone.

The sources are read as syntax trees. Nothing is started.
"""
import ast
import tempfile
import unittest
from pathlib import Path

from test_shared_helpers import benchmark_imports, in_benchmark_package, tool_modules

TESTS = Path(__file__).resolve().parent
LIST_FILES = ('deletion_marking.py', 'test_deletion_marking.py')
# What names the benchmark's smoke case, its resources, its registrations and its published record in a path.
BENCHMARK_PATHS = ('setup-smoke', 'resources/bench', 'bench/cases', 'resources/registrations', 'bench/evidence',
                   'docs/verification.md')


def tool_test_files():
    """The tool test files, sorted: every Python file under `tests/` but the check of the deletion list and its
    tests."""
    return [path for path in sorted(TESTS.rglob('*.py')) if path.relative_to(TESTS).as_posix() not in LIST_FILES]


def named_paths(source):
    """The benchmark paths the Python `source` names in a string of its code, as sorted pairs of line and path. A
    doc of the module, of a class or of a function is no code, and a comment is no string. A path the code builds
    from parts is not seen."""
    tree = ast.parse(source)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(first.value)
    texts = [node for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docs]
    return sorted({(node.lineno, path) for node in texts for path in BENCHMARK_PATHS if path in node.value})


def benchmark_reaches(source, package):
    """The lines of the Python `source` that reach the benchmark's code, sorted. `source` is a module of the package
    `package`, or a test file where `package` is empty. A line reaches it with an import statement at any depth,
    absolute or relative, or with a call of `import_module` or `__import__` that is given the name as a literal
    text. The benchmark's code is whatever `in_benchmark_package` takes."""
    def resolved(dots, name):
        parts = package.split('.') if package else []
        base = parts[:len(parts) - dots + 1] if dots else []
        return '.'.join([*base, *([name] if name else [])])

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [node.lineno for alias in node.names if in_benchmark_package(alias.name)]
        elif isinstance(node, ast.ImportFrom):
            module = resolved(node.level, node.module)
            found += [node.lineno for alias in node.names
                      if in_benchmark_package(module) or in_benchmark_package(f'{module}.{alias.name}')]
        elif isinstance(node, ast.Call) and ast.unparse(node.func).split('.')[-1] in ('import_module', '__import__'):
            for argument in node.args[:1]:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    name = argument.value.lstrip('.')
                    if in_benchmark_package(resolved(len(argument.value) - len(name), name)):
                        found.append(node.lineno)
    return sorted(set(found))


PACKAGE = 'generative_driver'
# Each way a source could reach the benchmark's code under one of its three names, for a module of a package or for
# a test file, with whether the scan must name it.
REACHES = (
    ('import generative_driver.benchmark', PACKAGE, True),
    ('import json, generative_driver.benchmark_support.harness as harness', PACKAGE, True),
    ('from generative_driver import benchmark', 'interface_runtime', True),
    ('from generative_driver.benchmark_support.suites import start_suite', '', True),
    ('from .benchmark import run', PACKAGE, True),
    ('from . import benchmark_support', PACKAGE, True),
    ('from .. import benchmark', PACKAGE + '.supplied', True),
    ('from ..benchmark_support.labels import equivalence_label', PACKAGE + '.supplied', True),
    ('class Case:\n    def run(self):\n        from generative_driver.benchmark import run', '', True),
    ("importlib.import_module('generative_driver.benchmark')", '', True),
    ("def late():\n    return import_module('generative_driver.benchmark_support.suites')", PACKAGE, True),
    ("__import__('generative_driver.benchmark', fromlist=['run'])", '', True),
    ("import_module('.benchmark_support', __package__)", PACKAGE, True),
    ("import_module('..benchmark', __package__)", PACKAGE + '.supplied', True),
    ('import firmware_bench', PACKAGE, True),
    ('import firmware_bench.harness as harness', '', True),
    ('from firmware_bench import harness', '', True),
    ('def call():\n    from firmware_bench.suites import start_suite', PACKAGE + '.supplied', True),
    ("importlib.import_module('firmware_bench.registry')", PACKAGE, True),
    ("__import__('firmware_bench')", '', True),
    # What stays: the tool's own modules, names that only begin alike, a name made at run time, and a text that
    # is given to no import.
    ('from .client import call', PACKAGE, False),
    ('from . import client_interface', PACKAGE, False),
    ('from .benchmarks import run', PACKAGE, False),
    ('import firmware_benches', '', False),
    ('from generative_driver._toolchain import bench', '', False),
    ("importlib.import_module(f'generative_driver._toolchain.{name}')", PACKAGE, False),
    ("__import__('generative_driver.agents', fromlist=['REPORT_SCHEMA'])", PACKAGE, False),
    ("named = 'generative_driver.benchmark'", '', False),
    ("named = 'firmware_bench'", '', False),
)

# Sources that name a benchmark path, each with the paths the scan must name, and sources it must leave alone.
NAMED = (
    ("fixture = resources_root() / 'bench/cases/setup-smoke'", ['bench/cases', 'setup-smoke']),
    ('root = checkout / "src/generative_driver/resources/bench"', ['resources/bench']),
    ("names = ('generative_driver/resources/registrations/cases.json',)", ['resources/registrations']),
    ("def test_it(self):\n    link = '../bench/evidence/result.json'", ['bench/evidence']),
    ("class Tests:\n    DOCUMENT = ROOT / 'docs/verification.md'", ['docs/verification.md']),
    ("script.write_text('''import shutil\nshutil.copy2(root/'bench/cases/setup-smoke/model.json','model.json')\n''')",
     ['bench/cases', 'setup-smoke']),
    ("text = f'{root}/resources/bench/{name}'", ['resources/bench']),
    ('"""The module doc names resources/bench and docs/verification.md."""', []),
    ('class Tests:\n    """A class doc names bench/evidence."""', []),
    ('def helper():\n    """A function doc names setup-smoke."""', []),
    ('async def helper():\n    """A function doc names bench/cases."""', []),
    ('VALUE = 1  # A comment names resources/registrations.', []),
    ("table = package / 'resources' / 'registrations' / 'cases.json'", []),
    ("smoke = resources_root() / 'smoke/model.json'", []),
    ("text = 'docs/client-interface.md, the bench of a desk and the evidence of a run'", []),
)


class NoBenchmarkPathTests(unittest.TestCase):
    """The tool's tests name nothing of the benchmark's resources or of its published record (10-B1)."""

    def test_10_B1_no_tool_test_file_names_a_benchmark_path_in_a_string_of_its_code(self):
        files = [path for path in tool_test_files() if path != Path(__file__).resolve()]
        self.assertGreater(len(files), 50)
        named = [f'{path.relative_to(TESTS).as_posix()}:{line} names {name}' for path in files
                 for line, name in named_paths(path.read_text(encoding='utf-8'))]
        self.assertEqual(len(named), 0, '\n'.join(named))

    def test_10_B1_the_scan_reads_the_strings_of_the_code_and_leaves_out_docs_and_comments(self):
        for source, expected in NAMED:
            with self.subTest(source=source):
                self.assertEqual(sorted({name for _, name in named_paths(source)}), expected)
        # It gives the line of the string, also inside a function.
        self.assertEqual(named_paths("import json\n\n\ndef read():\n    return open('docs/verification.md')\n"),
                         [(5, 'docs/verification.md')])


class NoImportTests(unittest.TestCase):
    """No tool module and no tool test file imports the benchmark's code, under any of its names (10-B9)."""

    def test_10_B9_no_tool_module_and_no_tool_test_file_imports_the_benchmarks_code(self):
        modules, files = tool_modules(), tool_test_files()
        self.assertGreater(len(modules), 80)
        self.assertGreater(len(files), 50)
        self.assertIn(Path(__file__).resolve(), files)
        sources = [*modules, *((path, '') for path in files)]
        reaching = [f'{path.name}:{line}' for path, package in sources
                    for line in benchmark_reaches(path.read_text(encoding='utf-8'), package)]
        self.assertEqual(len(reaching), 0, '\n'.join(reaching))

    def test_10_B9_the_scan_sees_each_name_of_the_benchmark_in_each_form_of_an_import(self):
        for source, package, reached in REACHES:
            with self.subTest(source=source, package=package):
                self.assertEqual(bool(benchmark_reaches(source, package)), reached)
        # It gives the line of the import, also at depth.
        self.assertEqual(benchmark_reaches('import json\n\n\ndef late():\n    import firmware_bench\n', ''), [5])

    def test_10_B9_the_helper_of_the_earlier_scans_takes_firmware_bench_as_well(self):
        for name in ('firmware_bench', 'firmware_bench.harness', 'generative_driver.benchmark',
                     'generative_driver.benchmark_support.suites'):
            self.assertTrue(in_benchmark_package(name), name)
        for name in ('firmware_benches', 'generative_driver.firmware_bench', 'generative_driver.benchmarks',
                     'generative_driver', 'interface_runtime.engine'):
            self.assertFalse(in_benchmark_package(name), name)
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / 'module.py'
            module.write_text('import firmware_bench.harness\nfrom firmware_bench import suites\n'
                              'def late():\n    from firmware_bench.registry import resolve_case\n', encoding='utf-8')
            self.assertEqual(benchmark_imports(module, 'generative_driver'), [
                ('firmware_bench.harness', None), ('firmware_bench', 'suites'),
                ('firmware_bench.registry', 'resolve_case')])


if __name__ == '__main__':
    unittest.main()

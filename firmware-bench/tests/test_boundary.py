"""The benchmark's side of the boundary between the benchmark's tests and the tool's.

Feature 10 of the untangle Run (docs/features/untangle/10-tests.md), 10-B3. The benchmark's tests take nothing from
the tool's test files: what they used of them is copied into `tool_fixtures.py` of this folder, and no file name is
in both test folders. A benchmark test file is a Python file of this folder. The two tests on the tool's deletion
list went with that list (Feature 12 of this Run, docs/features/untangle/12-firmware-bench.md).

The sources are read as syntax trees. One Python child is started, with the environment the copy of `package_env`
gives.
"""
import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch

import firmware_bench
import generative_driver

TESTS = Path(__file__).resolve().parent
TOOL_TESTS = TESTS.parents[1] / 'tests'


def benchmark_test_files():
    """The benchmark test files, sorted: every Python file of this folder."""
    return sorted(TESTS.glob('*.py'))


def script(text):
    """The syntax tree of `text` where it is Python, as a test writes the script of a child process, or None."""
    for source in (text, textwrap.dedent(text)):
        try:
            return ast.parse(source)
        except (SyntaxError, ValueError):
            continue
    return None


def imported(source):
    """The modules the Python `source` imports, as sorted pairs of line and the first part of the dotted name: in
    import statements at any depth, and in every string of the source that is itself Python, to any depth. A
    relative import names no module of another folder, so it is left out."""
    found = set()

    def read(tree, above):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update((above + node.lineno, alias.name.split('.')[0]) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                found.add((above + node.lineno, node.module.split('.')[0]))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and 'import' in node.value:
                inner = script(node.value)
                if inner is not None:
                    read(inner, above + node.lineno - 1)
    read(ast.parse(source), 0)
    return sorted(found)


def takes(source, modules):
    """The lines of the Python `source` that import one of `modules`, sorted, each with the module."""
    return [(line, name) for line, name in imported(source) if name in modules]


TOOL = {'test_configurator', 'package_env', 'owner_ipc_fixture'}
# Sources that take a module of the tool's test folder, each with the modules the scan must name, and sources it
# must leave alone.
IMPORTS = (
    ('from test_configurator import scripted_stages, wait_stopped', ['test_configurator']),
    ('import json, test_configurator', ['test_configurator']),
    ('import package_env as environment', ['package_env']),
    ('class Case:\n    def run(self):\n        from owner_ipc_fixture import owner_ipc', ['owner_ipc_fixture']),
    ('def late():\n    import test_configurator.helpers', ['test_configurator']),
    ("WORKER = '''\nimport sys\nsys.path.insert(0, TESTS)\nfrom package_env import package_env\n'''", ['package_env']),
    ("child = [sys.executable, '-c', 'import test_configurator; test_configurator.main()']", ['test_configurator']),
    ('def child():\n    return """\n        import sys\n        from owner_ipc_fixture import owner_ipc\n    """',
     ['owner_ipc_fixture']),
    ("OUTER = '''\nINNER = \"\"\"\nimport package_env\n\"\"\"\n'''", ['package_env']),
    # What stays: the folder's own helpers, the tool's package, a relative import, and a text that is no Python.
    ('from tool_fixtures import package_env, scripted_stages', []),
    ('from suite_fixtures import supplied_start', []),
    ('from generative_driver.configurator import Controller', []),
    ('from . import test_configurator', []),
    ("reason = 'Tests that import test_configurator fail where the tool is gone'", []),
    ("name = 'package_env'", []),
)

class ToolTestFileTests(unittest.TestCase):
    """The benchmark's tests take nothing from the tool's test files (10-B3)."""

    def tool_modules(self):
        """The names of the modules of the tool's test folder. The test is skipped where that folder is not beside
        this one, as in a repository of the benchmark alone."""
        names = {path.stem for path in TOOL_TESTS.glob('*.py')}
        if not names:
            self.skipTest(f'The tool\'s test folder is not beside this one: {TOOL_TESTS}')
        return names

    def test_10_B3_no_benchmark_test_file_imports_a_module_of_the_tools_test_folder(self):
        tool = self.tool_modules()
        files = [path for path in benchmark_test_files() if path != Path(__file__).resolve()]
        self.assertGreater(len(files), 70)
        taken = [f'{path.name}:{line} imports {name}' for path in files
                 for line, name in takes(path.read_text(encoding='utf-8'), tool)]
        self.assertEqual(len(taken), 0, '\n'.join(taken))

    def test_10_B3_no_file_name_is_in_both_test_folders(self):
        tool = {path.name for path in TOOL_TESTS.glob('*.py')} if self.tool_modules() else set()
        both = sorted(tool & {path.name for path in benchmark_test_files()})
        self.assertEqual(len(both), 0, ', '.join(both))

    def test_10_B3_the_scan_sees_an_import_at_any_depth_and_in_the_script_of_a_child(self):
        for source, expected in IMPORTS:
            with self.subTest(source=source):
                self.assertEqual(sorted({name for _, name in takes(source, TOOL)}), expected)
        # It gives the line of the import, also inside the script of a child.
        self.assertEqual(takes('import json\n\n\ndef late():\n    import package_env\n', TOOL), [(5, 'package_env')])
        self.assertEqual(takes("import json\nWORKER = '''\nimport sys\nimport package_env\n'''\n", TOOL),
                         [(4, 'package_env')])

    def test_10_B3_a_python_child_gets_the_environment_of_the_copy_of_package_env(self):
        self.assertTrue((TESTS / 'tool_fixtures.py').is_file(), 'firmware-bench/tests/tool_fixtures.py is missing')
        from tool_fixtures import package_env
        # Both package folders (12-B4 of Feature 12 of this Run): the folder that holds the benchmark's package and
        # the folder that holds the tool's, once where they are the same folder, as with both installed.
        folders = (str(Path(firmware_bench.__file__).resolve().parents[1]),
                   str(Path(generative_driver.__file__).resolve().parents[1]))
        parents = os.pathsep.join(dict.fromkeys(folders))
        with patch.dict(os.environ, {'PYTHONPATH': 'inherited'}):
            before = dict(os.environ)
            self.assertEqual(package_env(GD_EXAMPLE='1'),
                             {'PYTHONPATH': parents + os.pathsep + 'inherited', 'GD_EXAMPLE': '1'})
            self.assertEqual(dict(os.environ), before)
        with patch.dict(os.environ):
            os.environ.pop('PYTHONPATH', None)
            self.assertEqual(package_env(), {'PYTHONPATH': parents})
            # A child that inherits no `PYTHONPATH`, as an MCP stdio child, still imports this process's packages.
            script = ('import firmware_bench, generative_driver\n'
                      'print(firmware_bench.__file__)\nprint(generative_driver.__file__)')
            child = subprocess.run([sys.executable, '-c', script], env={**os.environ, **package_env()},
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual([Path(line).resolve() for line in child.stdout.splitlines()],
                         [Path(firmware_bench.__file__).resolve(), Path(generative_driver.__file__).resolve()])
        with patch('tool_fixtures.PACKAGE_ROOTS', ('one folder', 'one folder')), patch.dict(os.environ):
            os.environ.pop('PYTHONPATH', None)
            self.assertEqual(package_env(), {'PYTHONPATH': 'one folder'})


if __name__ == '__main__':
    unittest.main()

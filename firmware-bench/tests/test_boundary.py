"""The benchmark's side of the boundary between the benchmark's tests and the tool's.

Feature 10 of the untangle Run (docs/features/untangle/10-tests.md), 10-B3 and the last point of 10-B4. The
benchmark's tests take nothing from the tool's test files: what they used of them is copied into `tool_fixtures.py`
of this folder, and no file name is in both test folders. No benchmark test file reads the deletion list or imports
its check or its tests, which stay with the tool until the deletion removes them. A benchmark test file is a Python
file of this folder.

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

import generative_driver

TESTS = Path(__file__).resolve().parent
TOOL_TESTS = TESTS.parents[1] / 'tests'
# The check of the deletion list, its tests, and the name of the list as a path holds it.
LIST_MODULES, LIST_NAME = ('deletion_marking', 'test_deletion_marking'), 'benchmark-deletion-list'


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


def code_strings(source):
    """The strings of the code of the Python `source`, as pairs of line and text. A doc of the module, of a class or
    of a function is no code."""
    tree = ast.parse(source)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(first.value)
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docs]


def takes(source, modules):
    """The lines of the Python `source` that import one of `modules`, sorted, each with the module."""
    return [(line, name) for line, name in imported(source) if name in modules]


def reads_the_list(source):
    """The lines of the Python `source` whose code holds the name of the deletion list in a string, sorted."""
    return sorted({line for line, text in code_strings(source) if LIST_NAME in text})


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
# Sources that read the deletion list or take its check, each with whether the scan must name it.
LIST_READS = (
    ("LIST = REPO / 'docs' / 'benchmark-deletion-list.md'", True),
    ("text = (REPO / 'docs/benchmark-deletion-list.md').read_text(encoding='utf-8')", True),
    ("def table(heading):\n    return open(f'{root}/docs/benchmark-deletion-list.md').read()", True),
    ('"""This module reads docs/benchmark-deletion-list.md."""', False),
    ('class Tests:\n    """It holds docs/benchmark-deletion-list.md to the tree."""', False),
    ('LIST = 1  # docs/benchmark-deletion-list.md', False),
    ("DOCUMENT = REPO / 'docs' / 'client-interface.md'", False),
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
        parent = str(Path(generative_driver.__file__).resolve().parents[1])
        with patch.dict(os.environ, {'PYTHONPATH': 'inherited'}):
            before = dict(os.environ)
            self.assertEqual(package_env(GD_EXAMPLE='1'),
                             {'PYTHONPATH': parent + os.pathsep + 'inherited', 'GD_EXAMPLE': '1'})
            self.assertEqual(dict(os.environ), before)
        with patch.dict(os.environ):
            os.environ.pop('PYTHONPATH', None)
            self.assertEqual(package_env(), {'PYTHONPATH': parent})
            # A child that inherits no `PYTHONPATH`, as an MCP stdio child, still imports this process's package.
            script = 'import generative_driver; print(generative_driver.__file__)'
            child = subprocess.run([sys.executable, '-c', script], env={**os.environ, **package_env()},
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(Path(child.stdout.strip()).resolve(), Path(generative_driver.__file__).resolve())


class DeletionListTests(unittest.TestCase):
    """No benchmark test file reads the deletion list or imports its check or its tests (10-B4)."""

    def test_10_B4_no_benchmark_test_file_reads_the_deletion_list_or_imports_its_check_or_its_tests(self):
        files = [path for path in benchmark_test_files() if path != Path(__file__).resolve()]
        self.assertGreater(len(files), 70)
        found = []
        for path in files:
            source = path.read_text(encoding='utf-8')
            found += [f'{path.name}:{line} imports {name}' for line, name in takes(source, LIST_MODULES)]
            found += [f'{path.name}:{line} names the deletion list' for line in reads_the_list(source)]
        self.assertEqual(len(found), 0, '\n'.join(found))

    def test_10_B4_the_scan_sees_the_name_of_the_list_in_code_and_the_check_in_an_import(self):
        for source, read in LIST_READS:
            with self.subTest(source=source):
                self.assertEqual(bool(reads_the_list(source)), read)
        for source in ('from deletion_marking import problems, strip', 'import test_deletion_marking',
                       'def late():\n    from test_deletion_marking import REPO, table',
                       "command = [sys.executable, '-c', 'import deletion_marking']"):
            with self.subTest(source=source):
                self.assertTrue(takes(source, LIST_MODULES))
        self.assertEqual(takes('from generative_driver import client', LIST_MODULES), [])


if __name__ == '__main__':
    unittest.main()

"""The benchmark takes the verdict rules from the client interface, and the deletion marking follows the interface.

Feature 03 of the untangle Run (docs/features/untangle/03-client-interface.md), 03-B6 and 03-B7. This file is a
benchmark item: it reads the benchmark package and the deletion list, and the deletion removes it with them. The
interface's own tests are in `tests/test_client_interface.py`, which stays.
"""
import ast
import re
import unittest

from deletion_marking import tool_test_modules
from test_deletion_marking import REPO, TABLES, require_working_tree, table

SOURCE = REPO / 'src' / 'generative_driver'
DOCUMENT = REPO / 'docs' / 'client-interface.md'
BEGIN, END = '<!-- legacy-benchmark:begin -->', '<!-- legacy-benchmark:end -->'
# What Feature 03 adds that stays when the benchmark is deleted.
STAYS = ('src/generative_driver/client_interface.py', 'docs/client-interface.md', 'tests/test_client_interface.py')
OLD_NAME = '_evaluation_errors'


def benchmark_modules():
    """The Python files of the benchmark package: `benchmark.py` and every module under `benchmark_support/`."""
    return sorted([SOURCE / 'benchmark.py', *(SOURCE / 'benchmark_support').rglob('*.py')])


def names(path, name):
    """Whether the module at `path` imports `name` or refers to it, as a name or as an attribute."""
    return any(isinstance(node, ast.alias) and node.name == name or isinstance(node, ast.Name) and node.id == name
               or isinstance(node, ast.Attribute) and node.attr == name
               for node in ast.walk(ast.parse(path.read_bytes())))


def imported(path, package):
    """What the module at `path` takes with `from ... import`, at any depth: pairs of the dotted name of a module
    and a name taken from it. A relative import starts from `package`, the dotted name of the module's package."""
    found = set()
    for node in ast.walk(ast.parse(path.read_bytes())):
        if isinstance(node, ast.ImportFrom):
            parts = package.split('.')
            base = parts[:len(parts) - node.level + 1] if node.level else []
            module = '.'.join([*base, *([node.module] if node.module else [])])
            found.update((module, alias.name) for alias in node.names)
    return found


def listed(heading):
    """The items of the table under `heading` of the deletion list: the first cell of each row, without backticks."""
    rows = [line.split('|')[1].strip().strip('`') for line in table(heading).splitlines() if line.startswith('|')]
    return rows[2:]  # after the column names and the rule under them


def covers(item, path):
    """Whether the item of the deletion list covers the file at `path`: an item that ends in `/` covers every file
    under it."""
    return path.startswith(item) if item.endswith('/') else path == item


class VerdictRuleTests(unittest.TestCase):
    def test_03_B6_no_module_of_the_benchmark_package_imports_or_calls_the_old_name(self):
        modules = benchmark_modules()
        for name in ('benchmark.py', 'harness.py', 'suite_reporting.py'):
            self.assertIn(name, [path.name for path in modules])
        self.assertEqual([str(path.relative_to(SOURCE)) for path in modules if names(path, OLD_NAME)], [])

    def test_03_B6_the_service_no_longer_has_the_old_names(self):
        from generative_driver import configurator
        for name in (OLD_NAME, 'EVALUATION', 'METHODS'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(configurator, name), f'configurator still has the name {name}')

    def test_03_B6_the_harness_and_the_suite_reporting_take_verdict_errors_from_the_interface_module(self):
        for name in ('harness.py', 'suite_reporting.py'):
            with self.subTest(module=name):
                taken = imported(SOURCE / 'benchmark_support' / name, 'generative_driver.benchmark_support')
                self.assertIn(('generative_driver.client_interface', 'verdict_errors'), taken)


class DeletionMarkingTests(unittest.TestCase):
    def test_03_B7_the_document_has_exactly_one_marked_passage_under_limits_today(self):
        lines = [line.strip() for line in DOCUMENT.read_text(encoding='utf-8').splitlines()]
        self.assertEqual((lines.count(BEGIN), lines.count(END)), (1, 1),
                         'The document has one begin and one end marker')
        begin, end = lines.index(BEGIN), lines.index(END)
        self.assertLess(begin, end)
        heading = re.compile(r'#+ ')
        self.assertEqual([line for line in lines[:begin] if heading.match(line)][-1], '## Limits today')
        self.assertEqual([line for line in lines[begin:end] if heading.match(line)], [])

    def test_03_B7_this_file_is_ready_to_delete_and_what_stays_is_not_on_the_list(self):
        self.assertIn('tests/test_benchmark_client_interface.py', listed('Ready to delete'))
        for heading, _ in TABLES:
            for path in STAYS:
                with self.subTest(table=heading, path=path):
                    self.assertEqual([item for item in listed(heading) if covers(item, path)], [])

    def test_03_B7_the_tool_test_modules_name_the_interface_tests_and_not_this_file(self):
        require_working_tree(self)
        modules = tool_test_modules(REPO)
        self.assertIn('tests/test_client_interface.py', modules)
        self.assertNotIn('tests/test_benchmark_client_interface.py', modules)


if __name__ == '__main__':
    unittest.main()

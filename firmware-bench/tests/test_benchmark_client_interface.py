"""The benchmark takes the verdict rules from the client interface.

Feature 03 of the untangle Run (docs/features/untangle/03-client-interface.md), 03-B6. This file is a benchmark
item: it reads the benchmark package. The interface's own tests are in `tests/test_client_interface.py`, which stays
with the tool.
"""
import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / 'src' / 'generative_driver'


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


class VerdictRuleTests(unittest.TestCase):
    def test_03_B6_the_harness_and_the_suite_reporting_take_verdict_errors_from_the_interface_module(self):
        for name in ('harness.py', 'suite_reporting.py'):
            with self.subTest(module=name):
                taken = imported(SOURCE / 'benchmark_support' / name, 'generative_driver.benchmark_support')
                self.assertIn(('generative_driver.client_interface', 'verdict_errors'), taken)


if __name__ == '__main__':
    unittest.main()

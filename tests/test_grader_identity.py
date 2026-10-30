"""The grader's import closure and the identity computed from it."""
import hashlib
import importlib.metadata
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import generative_driver
from generative_driver.benchmark_support.grader_identity import grader_closure, grader_identity
from generative_driver.benchmark_support.snapshots import canonical_digest


class GraderClosureTests(unittest.TestCase):
    def copy_package(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'generative_driver'
        shutil.copytree(Path(generative_driver.__file__).parent, root, ignore=shutil.ignore_patterns('__pycache__'))
        return root

    def add_modules(self, root, *names):
        """Empty modules that exist only in this copy, so a test names what it imports itself."""
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('', encoding='utf-8')

    def import_from_grading(self, root, source):
        """Import `source` from a function body in behavior.py, a closure module; the copy never runs it."""
        path = root / 'benchmark_support' / 'behavior.py'
        body = textwrap.indent(textwrap.dedent(source), '    ')
        path.write_text(path.read_text(encoding='utf-8') + '\n\ndef _imports_added_by_a_test():\n' + body + '\n', encoding='utf-8')

    def test_closure_follows_imports_inside_functions(self):
        self.assertIn('benchmark_support.behavior', grader_closure())  # final_record imports it in its body

    def test_closure_includes_registered_family_modules(self):
        self.assertLessEqual({'benchmark_support.parameter_store', 'benchmark_support.sampled_sensor',
                              'benchmark_support.tq9_v2', 'benchmark_support.zephyr_modbus'}, set(grader_closure()))

    def test_grading_change_changes_identity(self):
        root = self.copy_package(); before = grader_identity(root)['sha256']
        path = root / 'benchmark_support' / 'behavior.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        self.assertNotEqual(grader_identity(root)['sha256'], before)

    def test_closure_resolves_relative_and_absolute_imports(self):
        root = self.copy_package()
        self.add_modules(root, 'benchmark_support/sibling.py', 'toplevel.py', 'absolute.py',
                         'subpackage/__init__.py', 'subpackage/leaf.py')
        self.import_from_grading(root, '''
            from . import sibling
            from .. import toplevel
            from ..subpackage import leaf
            import generative_driver.absolute
            from ... import above_the_package
            import json
            import cryptography
            from json import decoder
        ''')
        closure = set(grader_closure(root))
        self.assertLessEqual({'benchmark_support.sibling', 'toplevel', 'subpackage', 'subpackage.leaf', 'absolute'}, closure)
        self.assertFalse({'json', 'json.decoder', 'cryptography', 'above_the_package'} & closure)

    def test_importing_a_module_counts_its_parent_package(self):
        root = self.copy_package()
        self.add_modules(root, 'outer/__init__.py', 'outer/inner.py')
        self.import_from_grading(root, 'import generative_driver.outer.inner')
        self.assertLessEqual({'outer', 'outer.inner'}, set(grader_closure(root)))
        before = grader_identity(root)['sha256']
        (root / 'outer' / '__init__.py').write_text('changed = True\n', encoding='utf-8')
        self.assertNotEqual(grader_identity(root)['sha256'], before)

    def test_interface_runtime_is_named_in_the_closure_but_not_hashed(self):
        root = self.copy_package()
        self.import_from_grading(root, '''
            import interface_runtime
            import interface_runtime.engine
            from interface_runtime.package import digest
        ''')
        self.assertLessEqual({'interface_runtime', 'interface_runtime.engine', 'interface_runtime.package'},
                             set(grader_closure(root)))
        self.assertEqual([name for name in grader_identity(root)['modules'] if name.startswith('interface_runtime')], [])

    def test_identity_pins_module_hashes_and_the_cryptography_version(self):
        identity = grader_identity()
        behavior = Path(generative_driver.__file__).parent / 'benchmark_support' / 'behavior.py'
        self.assertEqual(set(identity), {'schema', 'modules', 'dependencies', 'sha256'})
        self.assertEqual(identity['schema'], 'benchmark-grader-identity/1')
        self.assertEqual(identity['modules']['benchmark_support.behavior'], hashlib.sha256(behavior.read_bytes()).hexdigest())
        self.assertEqual(identity['dependencies'], {'cryptography': importlib.metadata.version('cryptography')})
        self.assertEqual(identity['sha256'], canonical_digest({k: v for k, v in identity.items() if k != 'sha256'}))

    def test_change_outside_the_closure_keeps_identity(self):
        root = self.copy_package(); before = grader_identity(root)['sha256']
        self.assertEqual(before, grader_identity()['sha256'])  # a copy of the installed package is the same grader
        for name in ('dashboard.py', 'daemon.py'):
            path = root / name; path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        self.assertEqual(grader_identity(root)['sha256'], before)

    def test_missing_family_module_is_an_error_not_a_gap(self):
        root = self.copy_package()
        (root / 'benchmark_support' / 'zephyr_modbus.py').unlink()
        with self.assertRaisesRegex(ValueError, 'benchmark_support.zephyr_modbus'):
            grader_closure(root)

    def test_closure_excludes_shared_system_modules(self):
        shared = ('configurator', 'benchmark', 'reporting', 'replay', 'client', 'setup', 'benchmark_support.cases',
                  'benchmark_support.emulated', 'benchmark_support.legacy', 'benchmark_support.authoring')
        root = self.copy_package()
        # physical (legacy recorded grading) and reference_calibration (the evaluator identity) are still in the closure,
        # and their own imports hold the shared modules in. They leave it when recorded grading moves out of the
        # evaluator and admission checks the grader identity, so this checks the rest of the closure without them.
        for name in ('physical', 'reference_calibration'):
            (root / 'benchmark_support' / (name + '.py')).write_text('', encoding='utf-8')
        self.assertEqual([m for m in grader_closure(root) if m in shared], [])


if __name__ == '__main__':
    unittest.main()

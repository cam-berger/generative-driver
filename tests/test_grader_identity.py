"""The grader's import closure and the identity computed from it."""
import base64
import hashlib
import importlib.metadata
import json
import shutil
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import generative_driver
from generative_driver.benchmark_support import registrations, sampled_sensor
from generative_driver.benchmark_support.calibration import hydrate_truth, inventory, validate_calibration
from generative_driver.benchmark_support.evaluator import case_truth
from generative_driver.benchmark_support.grader_identity import (ENTRY_MODULES, SYSTEM_MODULES, grader_closure,
                                                                 grader_identity)
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

    def test_the_shared_core_imports_no_family(self):
        families = {'benchmark_support.' + c.native_module for c in registrations._CASES if c.native_module}
        self.assertEqual(families & set(grader_closure(families=())), set())

    def test_a_tuple_of_families_seeds_only_the_native_modules_it_names(self):
        core, closure = set(grader_closure(families=())), set(grader_closure(families=('sampled_sensor',)))
        self.assertLessEqual(set(ENTRY_MODULES), core)
        self.assertLessEqual(core | {'benchmark_support.sampled_sensor', 'benchmark_support.family_support'}, closure)
        self.assertNotIn('benchmark_support.zephyr_modbus', closure)

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

    def beyond_the_boundary(self, closure):
        """The modules of `closure` that grading code must never reach: the system under test and the reporting code."""
        return [m for m in closure if any(m == s or m.startswith(s + '.') for s in SYSTEM_MODULES)]

    def test_closure_excludes_system_modules(self):
        self.assertEqual(self.beyond_the_boundary(grader_closure()), [])

    def test_a_grading_module_that_imports_reporting_code_crosses_the_boundary(self):
        # Catches reporting code (the outcomes, the audit, the pairing, the scorecard, the suite report and the corpus) that a
        # grading module comes to import without the boundary test failing: every change to it would then change the grader's
        # identity and void a qualification, though it grades nothing.
        for name in ('outcomes', 'audit', 'pairing', 'scorecard', 'suite_reporting', 'corpus'):
            with self.subTest(name):
                root = self.copy_package()
                self.import_from_grading(root, 'from . import ' + name)
                closure = grader_closure(root)
                self.assertIn('benchmark_support.' + name, closure)
                self.assertIn('benchmark_support.' + name, self.beyond_the_boundary(closure))

    def test_system_change_keeps_identity(self):
        root = self.copy_package(); before = grader_identity(root)['sha256']
        for name in ('configurator.py', 'toolkit.py', 'benchmark.py', 'benchmark_support/emulated.py',
                     'benchmark_support/harness.py', 'benchmark_support/reference_calibration.py'):
            path = root / name; path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        self.assertEqual(grader_identity(root)['sha256'], before)


class FamilyRoutingTests(unittest.TestCase):
    """The shared core reaches a family's code through the registrations, by name, so that it imports none."""

    def family_truth(self, family):
        """A family-truth payload whose scored phases only have complete reset/call/observe coverage, so that nothing
        but the family's own phase rules can refuse them."""
        phase = {'contract': {'checks': [{'id': 'toy/ok'}]},
                 'actions': [{'kind': 'reset'}, {'kind': 'call'}, {'kind': 'observe', 'checks': ['toy/ok']}]}
        files = {name: b'{}' for name in inventory(family)}
        recipe = {'source_file_paths': {'device.resc': 'native/device.resc'}}
        files['native/observation-map.json'] = json.dumps(recipe).encode()
        files['diagnostic/episodes.json'] = files['final/episodes.json'] = json.dumps(phase).encode()
        entries = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'base64': base64.b64encode(data).decode()}
                   for name, data in files.items()}
        return {'schema': 'benchmark-family-truth/1', 'case_id': family + '-v1', 'family': family,
                'inventory_state': 'pending', 'inventory': entries,
                'inventory_sha256': canonical_digest({name: entry['sha256'] for name, entry in entries.items()})}

    def register(self, case_id, **changes):
        """Register a case like tq9-v2's with `changes` beside the real ones, until the test ends."""
        case = replace(registrations.registration_for('tq9-v2'), id=case_id, **changes)
        known = MappingProxyType({**registrations._REGISTRATIONS, case_id: case})
        patcher = patch.object(registrations, '_REGISTRATIONS', known)
        patcher.start()
        self.addCleanup(patcher.stop)

    def truth_of(self, case_id, unlocked):
        """`case_truth` of `case_id` when its sealed truth unlocks to `unlocked`, with no vault, case files or run."""
        with patch('generative_driver.benchmark_support.case_assets.case_inputs', return_value=(Path('inputs'), {})), \
                patch('generative_driver.benchmark_support.case_assets.case_root', return_value=Path('root')), \
                patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value=unlocked):
            return case_truth(case_id, Path('run'), {})

    def test_a_family_resolves_to_the_one_native_module_its_cases_name(self):
        # tq9-v2 and tq9-v3 both name tq9_v2; the legacy tq9 case names none.
        for family, module in (('tq9', 'tq9_v2'), ('sampled-sensor', 'sampled_sensor'),
                               ('parameter-store', 'parameter_store'), ('zephyr-modbus', 'zephyr_modbus')):
            with self.subTest(family):
                self.assertEqual(registrations.family_module(family).__name__,
                                 'generative_driver.benchmark_support.' + module)

    def test_a_family_without_exactly_one_native_module_is_refused(self):
        for family in ('bme280', 'setup-smoke', 'unregistered-family'):
            with self.subTest(family), self.assertRaisesRegex(ValueError, 'No native module registered'):
                registrations.family_module(family)
        elsewhere = replace(registrations.registration_for('tq9-v2'), id='tq9-elsewhere', native_module='sampled_sensor')
        with patch.object(registrations, '_CASES', registrations._CASES + (elsewhere,)), \
                self.assertRaisesRegex(ValueError, 'Several native modules.*sampled_sensor, tq9_v2'):
            registrations.family_module('tq9')

    def test_each_family_declares_the_mutants_its_qualification_requires(self):
        required = {
            'tq9': {'wrong-scale', 'constant-output', 'wrong-state', 'unsigned-temperature', 'wrong-temperature-scale',
                    'wrong-temperature-unit', 'wrong-temperature-byteorder', 'wrong-crc', 'wrong-duty-effect'},
            'sampled-sensor': {'constant', 'scale', 'signedness', 'stale'},
            'parameter-store': {'constant', 'scale', 'order', 'wrong-bank', 'abort', 'idle-only-update',
                                'short-rejection-frame'},
            'zephyr-modbus': {'constant-read', 'wrong-byteorder', 'wrong-register', 'wrong-coil', 'omit-write',
                              'wrong-request-crc'}}
        for family, mutants in required.items():
            with self.subTest(family):
                declared = registrations.family_module(family).REQUIRED_MUTANTS
                self.assertIsInstance(declared, frozenset)
                self.assertEqual(declared, mutants)

    def test_qualification_requires_the_mutants_the_family_declares(self):
        manifest = {'id': 'probe-v1', 'family': 'sampled-sensor', 'evaluator_version': '1',
                    'images': {'firmware.bin': 'a' * 64}, 'calibration': {'input_hashes': {}}}

        def refuses_the_inventory(declared, listed):
            """Whether a record that lists `listed` mutants fails the family's mutant inventory when the family declares
            `declared`. The record stops short of its runs, which ends the validation right after that inventory."""
            with patch.object(sampled_sensor, 'REQUIRED_MUTANTS', frozenset(declared)):
                record = {'input_hashes': {}, 'required_mutants': listed}
                return 'family mutant inventory' in validate_calibration(manifest, record)['failures']

        self.assertFalse(refuses_the_inventory({'alpha', 'beta'}, ['alpha', 'beta']))
        self.assertTrue(refuses_the_inventory({'alpha', 'beta'}, ['alpha']))
        self.assertTrue(refuses_the_inventory({'alpha', 'beta'}, ['alpha', 'beta', 'beta']))

    def test_hydration_has_the_family_validate_its_own_phases(self):
        hydrated = hydrate_truth(self.family_truth('sampled-sensor'))  # only the generic coverage rules apply to it
        self.assertEqual(hydrated['phases']['final']['actions'][0]['kind'], 'reset')
        with self.assertRaisesRegex(ValueError, 'Incomplete parameter-store scenarios'):
            hydrate_truth(self.family_truth('parameter-store'))

    def test_only_the_family_kind_cases_have_an_authoring_inventory(self):
        for family in ('sampled-sensor', 'parameter-store'):
            self.assertIn('AUTHORING.json', inventory(family))
        for family in ('tq9', 'zephyr-modbus', 'bme280', 'unregistered-family'):
            with self.subTest(family), self.assertRaisesRegex(ValueError, 'Unsupported family'):
                inventory(family)

    def test_a_case_is_hydrated_as_its_registered_truth_schema_says_whatever_its_id(self):
        self.register('another-sensor-v1', truth_schema='benchmark-family-truth/1')
        self.register('another-upstream-v1', truth_schema='benchmark-upstream-truth/1')
        unlocked = {'schema': 'unlocked'}
        with patch('generative_driver.benchmark_support.calibration.hydrate_truth', return_value='family') as family, \
                patch('generative_driver.benchmark_support.upstream.hydrate_truth', return_value='upstream') as upstream:
            self.assertEqual(self.truth_of('another-sensor-v1', unlocked), 'family')
            self.assertEqual(self.truth_of('another-upstream-v1', unlocked), 'upstream')
        family.assert_called_once_with(unlocked)
        upstream.assert_called_once_with(unlocked)

    def test_a_truth_of_a_schema_the_core_does_not_hydrate_comes_back_as_unlocked(self):
        self.register('generated-fixture-v1', truth_schema='generated-answer-key/1')
        unlocked = {'schema': 'generated-answer-key/1', 'references': {}}
        with patch('generative_driver.benchmark_support.calibration.hydrate_truth', side_effect=AssertionError('hydrated')), \
                patch('generative_driver.benchmark_support.upstream.hydrate_truth', side_effect=AssertionError('hydrated')):
            self.assertIs(self.truth_of('generated-fixture-v1', unlocked), unlocked)
            self.assertIs(self.truth_of('tq9-v2', unlocked), unlocked)  # a registration with no truth schema


if __name__ == '__main__':
    unittest.main()

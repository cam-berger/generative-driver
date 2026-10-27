"""Trusted case registration through benchmark selection and execution boundaries."""
import tempfile
import unittest
import builtins
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch


class CaseRegistrationTests(unittest.TestCase):
    def test_counterfeit_family_is_refused_before_a_benchmark_harness_starts(self):
        from generative_driver.benchmark import run
        from suite_fixtures import public_case_fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public_case_fixture(root, family='zephyr-modbus')
            with patch('generative_driver.benchmark.case_root', return_value=root), \
                    patch('generative_driver.benchmark_support.harness.launch') as launch:
                with self.assertRaisesRegex(ValueError, 'family'):
                    run('tq9-v2')
                launch.assert_not_called()

    def test_sensor_observation_does_not_import_an_unused_family(self):
        from generative_driver.benchmark_support.instruments import observation
        original_import = builtins.__import__
        def without_modbus(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith('zephyr_modbus') or 'zephyr_modbus' in (fromlist or ()):
                raise ImportError('Unused Modbus adapter was removed')
            return original_import(name, globals, locals, fromlist, level)
        with patch('builtins.__import__', side_effect=without_modbus):
            observed = observation('sampled-sensor-v1',
                {'values': {'latched_q4': 400, 'acquisition_counter': 2}}, 'sensor-session')
        self.assertEqual(observed['values'], {'reference_temperature': 25, 'monitor_sequence': 2})
        self.assertEqual(observed['units'], {'reference_temperature': 'degC', 'monitor_sequence': 'count'})

    def test_unknown_reuse_case_cannot_inherit_the_tq9_mission(self):
        from generative_driver.benchmark_support.emulated import _mission
        capabilities = {'tasks': {'temperature': {'operation': 'read',
            'outputs': {'temperature': {'output': 'reading'}}}}}
        events = [{'operation': 'read', 'result': {'ok': True, 'outputs': {'reading': 25}},
                   'observation': {'duty': duty}} for duty in (370, 0)]
        self.assertTrue(_mission('tq9-v2', events, capabilities)['passed'])
        mission = _mission('future-unregistered-case', events, capabilities)
        self.assertIsNone(mission['passed'])
        self.assertIn('Unknown benchmark case', mission['reason'])

    def test_case_inventory_labels_public_availability_without_claiming_admission(self):
        from generative_driver.benchmark import main
        from suite_fixtures import public_case_fixture
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public_case_fixture(root, 'zephyr-modbus-v1')
            output = io.StringIO()
            with patch('generative_driver.benchmark.case_root', return_value=root), \
                    patch('generative_driver.benchmark_support.emulator.truth_for_case',
                          side_effect=AssertionError('Discovery must not unlock evaluator truth')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(main(['cases']), 0)
        rows = json.loads(output.getvalue())
        case = next(row for row in rows if row['id'] == 'zephyr-modbus-v1')
        self.assertEqual(case['status'], 'available')
        for row in rows:
            self.assertEqual(row.get('availability_basis'), 'public-metadata')
            self.assertEqual(row.get('authenticated_admission'), 'not-checked')

    def test_invalid_inventory_is_refused_before_loading_a_native_family(self):
        from generative_driver.benchmark_support.upstream import hydrate_truth
        original_import = builtins.__import__
        def without_modbus(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith('zephyr_modbus') or 'zephyr_modbus' in (fromlist or ()):
                raise ImportError('Unused Modbus adapter was removed')
            return original_import(name, globals, locals, fromlist, level)
        with patch('builtins.__import__', side_effect=without_modbus):
            with self.assertRaisesRegex(ValueError, 'Invalid upstream truth identity'):
                hydrate_truth({})

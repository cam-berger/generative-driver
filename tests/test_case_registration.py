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
                    patch('generative_driver.benchmark_support.case_assets.case_root', return_value=root), \
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
                    patch('generative_driver.benchmark_support.case_assets.case_root', return_value=root), \
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


class RegistrationTableTests(unittest.TestCase):
    """Registrations are code-owned data: one table installed beside the package code, refused when malformed."""

    def write(self, text):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'cases.json'
        path.write_text(text, encoding='utf-8')
        return path

    def test_the_table_holds_every_registered_case(self):
        # Catches a registration lost or changed when the table moved from code into data, tq9-v3 (no resources) included.
        from generative_driver.benchmark_support.registrations import CaseRegistration, _CASES
        self.assertEqual(_CASES, (
            CaseRegistration('setup-smoke', 'setup-smoke', '1', 'smoke', None),
            CaseRegistration('tq9', 'tq9', '2', 'legacy-emulator', 'benchmark-case/1', mission_case='tq9-v2'),
            CaseRegistration('bme280', 'bme280', '3', 'legacy-physical', 'benchmark-case/1'),
            CaseRegistration('tq9-v2', 'tq9', '5', 'emulator-v2', 'benchmark-case/2',
                             native_module='tq9_v2', calibration_kind='reference'),
            CaseRegistration('tq9-v3', 'tq9', '1', 'emulator-v2', 'benchmark-case/2',
                             native_module='tq9_v2', calibration_kind='reference'),
            CaseRegistration('sampled-sensor-v1', 'sampled-sensor', '5', 'emulator-v2', 'benchmark-case/2',
                             native_module='sampled_sensor', truth_schema='benchmark-family-truth/1',
                             calibration_kind='family'),
            CaseRegistration('parameter-store-v1', 'parameter-store', '5', 'emulator-v2', 'benchmark-case/2',
                             native_module='parameter_store', truth_schema='benchmark-family-truth/1',
                             calibration_kind='family'),
            CaseRegistration('zephyr-modbus-v1', 'zephyr-modbus', '1', 'emulator-v2', 'benchmark-case/2',
                             native_module='zephyr_modbus', truth_schema='benchmark-upstream-truth/1',
                             build_kind='zephyr-modbus', calibration_kind='upstream')))

    def test_a_duplicate_key_an_unknown_or_missing_field_or_a_duplicate_id_is_refused(self):
        from dataclasses import asdict
        from generative_driver.benchmark_support.registrations import _CASES, read_cases
        entry = asdict(_CASES[0])
        def table(cases, schema='benchmark-case-registrations/1'):
            return json.dumps({'schema': schema, 'cases': cases})
        self.assertEqual(read_cases(self.write(table([entry]))), _CASES[:1])
        # A JSON reader that keeps the last of two equal keys would take this entry as `other`.
        duplicate_key = table([entry]).replace('"family"', '"id": "other", "family"', 1)
        missing = {name: value for name, value in entry.items() if name != 'calibration_kind'}
        for name, text in (('duplicate key', duplicate_key), ('unknown field', table([{**entry, 'extra': None}])),
                           ('missing field', table([missing])), ('duplicate id', table([entry, entry])),
                           ('unknown schema', table([entry], 'benchmark-case-registrations/2'))):
            with self.subTest(name), self.assertRaises(ValueError):
                read_cases(self.write(text))

    def test_a_case_root_override_never_replaces_the_registrations(self):
        # Catches the table read through case_root(), which GENERATIVE_DRIVER_BENCH_ROOT redirects: a selected asset tree
        # would then choose the native modules that load and the identity each case is admitted under.
        import os
        import subprocess
        import sys
        from dataclasses import asdict
        from package_env import package_env
        from generative_driver.benchmark_support.registrations import _CASES, registered_case_ids
        forged = {'schema': 'benchmark-case-registrations/1', 'cases': [{**asdict(_CASES[0]), 'id': 'forged-v1'}]}
        script = 'from generative_driver.benchmark_support import registrations; print(*registrations.registered_case_ids())'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # A forged table inside the selected tree, and beside it, as the installed table sits beside resources/bench.
            for folder in (root / 'registrations', root / 'bench' / 'registrations'):
                folder.mkdir(parents=True)
                (folder / 'cases.json').write_text(json.dumps(forged), encoding='utf-8')
            listed = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True, timeout=60,
                                    env={**os.environ, **package_env(GENERATIVE_DRIVER_BENCH_ROOT=str(root / 'bench'))})
        self.assertEqual(listed.stdout.split(), list(registered_case_ids()))

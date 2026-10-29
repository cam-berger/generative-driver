"""Replacement identity and TQ9-family behavior through public benchmark seams."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generative_driver.benchmark_support.registry import pin_case, resolve_case, validate_pinned_workflow
from suite_fixtures import public_case_fixture


class Tq9ReplacementTests(unittest.TestCase):
    def test_replacement_retains_the_tq9_instrument_units(self):
        from generative_driver.benchmark_support.instruments import units
        self.assertEqual(units('tq9-v3'), {'duty': 'permille', 'temperature_reference': 'degC'})

    def test_unavailable_native_tools_cannot_qualify_the_replacement(self):
        from generative_driver.benchmark_support.authoring import calibrate
        from generative_driver.benchmark_support.truth import seal
        from tq9_workflow_fixture import capabilities, model
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            resources = root/'resources'
            manifest = public_case_fixture(resources, 'tq9-v3', version='1', evaluator_version='1',
                family='tq9', calibration={'status': 'pending'}, default_effects=['write', 'actuate'])
            truth = {key: {} for key in ('source_hashes', 'contracts', 'recipe', 'references', 'build', 'analysis')}
            truth['references'] = {'reference-a': {'model': model(), 'capabilities': capabilities()}}
            truth['mutations'] = {'required': [], 'mutations': []}
            password = root/'password'
            password.write_text('synthetic replacement fixture')
            manifest['truth']['sha256'] = seal(truth, resources/manifest['truth']['path'], password.read_text())
            (resources/'cases/tq9-v3/case.json').write_text(json.dumps(manifest))
            with patch.dict(os.environ, {'GENERATIVE_DRIVER_CASE_ROOT': str(resources)}):
                result = calibrate('tq9-v3', {'evaluator_password_file': str(password),
                    'output': str(root/'output'), 'compiler': '/absent',
                    'renode': None, 'ghidra_home': None, 'java_home': None})
            self.assertFalse(result['ok'])
            self.assertEqual(result['calibration'], 'pending')
            record = json.loads((root/'output/calibration-record.json').read_text())
            self.assertEqual(record['case'], 'tq9-v3')
            self.assertEqual(record['runs'], [])
            self.assertEqual(record['mutants'], [])
            self.assertIn('Native Renode, Ghidra and Java paths are required', record['failure'])

    def test_replacement_uses_registered_native_adapter_and_public_mission(self):
        from generative_driver.benchmark_support.registrations import native_family_for
        from generative_driver.benchmark_support.missions import load_mission
        self.assertIs(native_family_for('tq9-v3'), native_family_for('tq9-v2'))
        self.assertEqual(load_mission('tq9-v3')['tasks'], load_mission('tq9-v2')['tasks'])
        self.assertEqual(load_mission('tq9-v3')['case_id'], 'tq9-v3')

    def test_replacement_interpreter_gets_the_same_public_tq9_tasks(self):
        from generative_driver.benchmark import prepare_stage
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            public_case_fixture(root, 'tq9-v3', version='1', evaluator_version='1',
                family='tq9', calibration={'status': 'pending'}, default_effects=['write', 'actuate'])
            with patch.dict(os.environ, {'GENERATIVE_DRIVER_CASE_ROOT': str(root)}):
                prepared = prepare_stage('tq9-v3', 'interpret', root/'run', root/'worker',
                                         options={'configured_effects': ['write', 'actuate']})
            tasks = json.loads((Path(prepared['work_dir'])/'TASKS.json').read_text())
            self.assertEqual(set(tasks['tasks']), {'temperature', 'arm', 'set_duty', 'disarm'})
            self.assertEqual(tasks['tasks']['temperature']['outputs']['temperature']['unit'], 'degC')
            self.assertEqual(tasks['tasks']['set_duty']['inputs']['duty']['unit'], 'permille')
            self.assertEqual(tasks['effect_grants'], ['write', 'actuate'])

    def test_replacement_has_its_own_pin_without_changing_the_locked_predecessor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, version in (('tq9-v2', '5'), ('tq9-v3', '1')):
                public_case_fixture(root, name, version=version, evaluator_version=version,
                    family='tq9', calibration={'status': 'pending'}, default_effects=['write', 'actuate'])
            predecessor = root/'cases/tq9-v2/case.json'
            original = predecessor.read_bytes()
            with patch.dict(os.environ, {'GENERATIVE_DRIVER_CASE_ROOT': str(root)}):
                replacement = resolve_case('tq9-v3')
                self.assertEqual((replacement.id, replacement.version, replacement.evaluator_version),
                                 ('tq9-v3', '1', '1'))
                self.assertEqual(replacement.adapter_key, 'emulator-v2')
                pin = pin_case('tq9-v3', 'original', 0)
                validate_pinned_workflow(pin)
                self.assertEqual(pin['case_id'], 'tq9-v3')
                self.assertEqual(resolve_case('tq9-v2').version, '5')
                self.assertNotEqual(pin['manifest_sha256'], pin_case('tq9-v2', 'original', 0)['manifest_sha256'])
            self.assertEqual(predecessor.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

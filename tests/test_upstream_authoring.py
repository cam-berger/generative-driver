import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class UpstreamAuthoringTests(unittest.TestCase):
    def test_qualification_stops_before_mutants_when_a_positive_reference_fails(self):
        from upstream_fixture import payload
        from generative_driver.benchmark_support.upstream_qualification import qualify
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root/'qualification'
            image = root/'firmware.bin'; image.write_bytes(b'synthetic image')
            truth = payload(); truth['images'] = {'firmware.bin': hashlib.sha256(image.read_bytes()).hexdigest()}
            truth['build'] = {'tool_versions': {'compiler': 'fixture', 'objcopy': 'fixture'}}
            manifest = {'id': 'zephyr-modbus-v1', 'family': 'zephyr-modbus',
                        'evaluator_version': '1', 'images': truth['images']}
            renode, ghidra, java = root/'renode', root/'ghidra', root/'java'
            for path in (renode, ghidra/'support/analyzeHeadless', java/'bin/java', java/'bin/java.exe'):
                path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            (ghidra/'Ghidra').mkdir(); (ghidra/'Ghidra/application.properties').write_text('application.version=fixture\n')
            def command(args, **kwargs):
                if '-version' in args:
                    return SimpleNamespace(returncode=0, stderr='fixture java', stdout='')
                (output/'exports').mkdir(); (output/'exports/functions.json').write_text('{}')
                return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'ok': True}))
            def reference(*args, **kwargs):
                destination = kwargs['output_dir']; destination.mkdir(parents=True)
                (destination/'evaluation.json').write_text('{}')
                return {'native_process_observed': False, 'process': {},
                    'contract': truth['phases'][kwargs['phase']]['contract'],
                    'grade': {'verdict': 'failed', 'checks': [{'id': 'toy', 'passed': False}]},
                    'model_sha256': 'a'*64, 'capabilities_sha256': 'b'*64, 'package_sha256': None,
                    'runtime_sha256': 'c'*64, 'artifact_execution': 'model-runtime'}
            with patch('generative_driver.benchmark_support.evaluator.reference_execute', side_effect=reference) as run, \
                    patch('generative_driver.benchmark_support.upstream_qualification.subprocess.run', side_effect=command), \
                    patch('generative_driver.benchmark_support.upstream_qualification.subprocess.check_output', return_value='fixture Renode'):
                record = qualify(truth, manifest, image, output, renode=renode, ghidra_home=ghidra, java_home=java)
            self.assertEqual(record['status'], 'pending')
            self.assertEqual(run.call_count, 1)
            self.assertEqual(record['mutants'], [])
            self.assertIn('Positive reference failed', record['failure'])

    def test_build_refuses_an_unpinned_source_before_launching_a_compiler(self):
        from generative_driver.benchmark_support.upstream_build import verify_source
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            verify_source(Path(temp))

    def test_upstream_mutants_must_fail_the_named_requirement_but_may_expose_related_failures(self):
        from test_benchmark_family_calibration import AdmissionTests
        from generative_driver.benchmark_support.calibration import validate_calibration
        from generative_driver.benchmark_support.zephyr_modbus import REQUIRED_MUTANTS
        manifest, record = AdmissionTests().fixture()
        manifest.update(id='zephyr-modbus-v1', family='zephyr-modbus')
        record.update(case='zephyr-modbus-v1', required_mutants=sorted(REQUIRED_MUTANTS))
        template = record['runs'][-1]
        mutation = record['mutants'][-1]
        record['runs'] = record['runs'][:4]
        record['mutants'] = []
        for name in sorted(REQUIRED_MUTANTS):
            run = copy.deepcopy(template)
            run.update(id=name, check_ids=['target', 'related'], checks=[
                {'id': 'target', 'passed': False, 'reason': 'value mismatch'},
                {'id': 'related', 'passed': False, 'reason': 'value mismatch'}])
            row = copy.deepcopy(mutation)
            row.update(id=name, run=name, expected_failed_checks=['target'], failed_checks=['target', 'related'])
            record['runs'].append(run); record['mutants'].append(row)
        checked = validate_calibration(manifest, record)
        self.assertTrue(checked['ok'], checked)
        record['runs'][-1]['checks'][0]['passed'] = True
        record['mutants'][-1]['failed_checks'] = ['related']
        self.assertFalse(validate_calibration(manifest, record)['ok'])

    def test_external_resources_are_explicit_and_never_written_into_installation(self):
        from generative_driver.benchmark import case_root
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with patch.dict(os.environ, {'GENERATIVE_DRIVER_BENCH_ROOT': str(root)}):
                self.assertEqual(case_root(), root)
            with patch.dict(os.environ, {'GENERATIVE_DRIVER_BENCH_ROOT': 'relative/resources'}):
                with self.assertRaises(ValueError):
                    case_root()

    def test_upstream_truth_derives_commitments_and_refuses_mutant_or_phase_omissions(self):
        from upstream_fixture import payload as make_payload
        from generative_driver.benchmark_support.upstream import hydrate_truth
        from interface_runtime.engine import validate_model
        payload = make_payload()
        hydrated = hydrate_truth(payload)
        self.assertTrue(hydrated['input_hashes'])
        self.assertEqual(set(hydrated['references']), {'a', 'b'})
        for reference in hydrated['references'].values():
            checked = validate_model(reference['model'])
            self.assertTrue(checked['ok'], checked)
        self.assertNotEqual(hydrated['references']['a'], hydrated['references']['b'])
        for mutate in (lambda p: p['mutations']['mutants'].pop(),
                       lambda p: p['phases']['final']['actions'].pop(),
                       lambda p: p.update(input_hashes={'fabricated': 'a'*64}),
                       lambda p: p.update(case_id='sampled-sensor-v1')):
            changed = copy.deepcopy(payload); mutate(changed)
            with self.subTest(change=mutate), self.assertRaises(ValueError):
                hydrate_truth(changed)

    def test_declared_mutants_target_observable_checks_and_change_executed_inputs(self):
        from upstream_fixture import payload as make_payload
        from generative_driver.benchmark_support.calibration import apply_mutation
        payload = make_payload()
        checks = {c['id'] for c in payload['phases']['final']['contract']['checks']}
        for mutant in payload['mutations']['mutants']:
            reference = payload['references'][mutant['reference']]
            changed = apply_mutation(reference['model'], reference['capabilities'], mutant)
            self.assertNotEqual(changed['source_sha256'], changed['mutated_sha256'])
            self.assertTrue(set(mutant['expected_failed_checks']) <= checks)

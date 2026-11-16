import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark import observe_stage, prepare_stage


class StagePreparationTests(unittest.TestCase):
    def test_acquire_hands_on_only_actual_imported_bytes(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('tq9-v2', 'acquire', root / 'run', root / 'worker')
            # A claimed artifact is no import: nothing is handed on, and the missing import is a note, never a stop.
            empty = observe_stage('tq9-v2', 'acquire', root / 'run', root / 'worker',
                                  {'status': 'completed', 'artifacts': [{'path': 'claimed.bin'}]})
            self.assertEqual(empty['artifacts'], [])
            self.assertEqual([(c['name'], c['passed']) for c in empty['checks']], [('imported_firmware_hash', False)])
            for key in ('ok', 'stop', 'route', 'fault'):
                self.assertNotIn(key, empty)
            result = call_tool('acquire_firmware_artifact', {'run_dir': str((root/'worker').resolve()),
                'source_path': prepared['inputs'][0], 'origin': 'provided_binary',
                'expected_sha256': prepared['context']['expected_sha256']})
            accepted = observe_stage('tq9-v2', 'acquire', root / 'run', root / 'worker',
                {'status': 'completed', 'artifacts': [result['artifact']]})
            self.assertEqual([(c['name'], c['passed']) for c in accepted['checks']], [('imported_firmware_hash', True)])
            self.assertIn(str(Path(result['artifact']).resolve()), [str(Path(a).resolve()) for a in accepted['artifacts']])

    def test_interpret_receives_neutral_sealed_inputs_without_evaluator_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root/'evaluator-credential'; credential.write_text('SYNTHETIC PRIVATE CREDENTIAL')
            prepared = prepare_stage('tq9-v2', 'interpret', root / 'run', root / 'worker',
                                     options={'evaluator_password_file': str(credential.resolve())})
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            self.assertFalse(prepared['report_required'])
            workspace = Path(prepared['work_dir'])
            self.assertTrue((workspace / 'image.bin').is_file())
            names = [p.name.lower() for p in workspace.rglob('*')]
            self.assertNotIn('tq9-v2.enc', names)
            self.assertNotIn('main.c', names)
            self.assertNotIn('reference_model.json', names)
            self.assertNotIn('evaluator_password_file', json.dumps(prepared))
            self.assertNotIn(str(credential.resolve()), json.dumps(prepared))
            for supplied in prepared['inputs']:
                self.assertNotIn(b'SYNTHETIC PRIVATE CREDENTIAL', Path(supplied).read_bytes())
            self.assertNotIn('TQ9', prepared['prompt'])
            hashes = json.loads((workspace / 'INPUT_HASHES.json').read_text())
            self.assertEqual(hashes['image.bin'], hashlib.sha256((workspace / 'image.bin').read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()

class RepairPreparationTests(unittest.TestCase):
    def test_interpret_repair_preserves_firmware_and_seals_observed_defects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = prepare_stage('tq9-v2', 'interpret', root/'run', root/'first')
            repaired = prepare_stage('tq9-v2', 'interpret', root/'run', root/'repair',
                options={'revision': 1, 'feedback': {'defects': ['temperature decoded outside observed range']}})
            for prepared in (first, repaired):
                self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            a, b = Path(first['work_dir']), Path(repaired['work_dir'])
            self.assertEqual((a/'image.bin').read_bytes(), (b/'image.bin').read_bytes())
            self.assertIn('temperature', (b/'DEFECTS.json').read_text())
            self.assertIn('DEFECTS.json', json.loads((b/'INPUT_HASHES.json').read_text()))

class QualificationNoteTests(unittest.TestCase):
    def test_a_valid_model_without_predicted_reply_qualification_is_handed_on_with_a_note(self):
        # Catches a qualification gate refusing the candidate: its own qualification is a note, the model behind
        # an intact seal is handed on, and probe tests it against the device.
        from generative_driver.benchmark import case_root
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('tq9-v2', 'interpret', root/'run', root/'worker')
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            __import__('shutil').copy2(case_root()/'cases/setup-smoke/model.json', Path(prepared['work_dir'])/'model.json')
            checked = observe_stage('tq9-v2', 'interpret', root/'run', prepared['work_dir'], {'status':'completed'})
            self.assertEqual([Path(a).name for a in checked['artifacts']], ['model.json'])
            checks = {c['name']: c['passed'] for c in checked['checks']}
            self.assertEqual((checks['sealed_inputs'], checks['predicted_reply_qualification']), (True, False))
            for key in ('ok', 'stop', 'route', 'fault', 'feedback', 'evaluator'):
                self.assertNotIn(key, checked)


class StageBoundaryTests(unittest.TestCase):
    def test_no_stage_adapter_imports_what_decrypts_truth_or_grades(self):
        # Catches a stage preparation or observation of any case reaching the truth or the evaluator: only the bench
        # harness and the evaluator may.
        from test_device_recovery import FORBIDDEN, imported
        from test_harness import SOURCE
        for name in ('ground.py', 'emulated.py', 'candidate_context.py'):
            found = imported(SOURCE / 'benchmark_support' / name)
            with self.subTest(module=name):
                self.assertFalse({n for n in found if FORBIDDEN & set(n.split('.'))}, found)

    def test_the_in_loop_legacy_evaluator_paths_are_gone(self):
        from generative_driver import benchmark, configurator
        from generative_driver.benchmark_support import evaluate, legacy
        from generative_driver.configurator import Controller
        from test_harness import SOURCE
        for owner, names in ((Controller, ('_managed_probe',)), (configurator, ('_adapter_verdict', 'LEGACY_CASES')),
                             (benchmark, ('check_stage', 'evaluation_fault')), (legacy, ('package_execute', 'cleanup')),
                             (evaluate, ('evaluate_model',))):
            for name in names:
                self.assertFalse(hasattr(owner, name), name)
        self.assertNotIn('evaluator_workspace', (SOURCE / 'configurator.py').read_text(encoding='utf-8'))

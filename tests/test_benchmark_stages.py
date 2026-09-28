import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark import check_stage, prepare_stage


class StagePreparationTests(unittest.TestCase):
    def test_acquire_acceptance_requires_actual_imported_bytes(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('tq9', 'acquire', root / 'run', root / 'worker')
            rejected = check_stage('tq9', 'acquire', root / 'run', root / 'worker',
                                   {'status': 'completed', 'artifacts': []})
            self.assertFalse(rejected['ok'])
            result = call_tool('acquire_firmware_artifact', {'run_dir': str((root/'worker').resolve()),
                'source_path': prepared['inputs'][0], 'origin': 'provided_binary',
                'expected_sha256': prepared['context']['expected_sha256']})
            accepted = check_stage('tq9', 'acquire', root / 'run', root / 'worker',
                {'status': 'completed', 'artifacts': [result['artifact']]})
            self.assertTrue(accepted['ok'], accepted)

    def test_interpret_receives_neutral_sealed_inputs_without_evaluator_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('tq9', 'interpret', root / 'run', root / 'worker',
                                     options={'evaluator_password_file': '/private/not-a-candidate-input'})
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            self.assertFalse(prepared['report_required'])
            workspace = Path(prepared['work_dir'])
            self.assertTrue((workspace / 'image.bin').is_file())
            names = [p.name.lower() for p in workspace.rglob('*')]
            self.assertNotIn('tq9.enc', names)
            self.assertNotIn('main.c', names)
            self.assertNotIn('reference_model.json', names)
            self.assertNotIn('password', json.dumps(prepared).lower())
            self.assertNotIn('TQ9', prepared['prompt'])
            hashes = json.loads((workspace / 'INPUT_HASHES.json').read_text())
            self.assertEqual(hashes['image.bin'], hashlib.sha256((workspace / 'image.bin').read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()

class RepairPreparationTests(unittest.TestCase):
    def test_interpret_repair_preserves_firmware_and_seals_observed_defects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = prepare_stage('tq9', 'interpret', root/'run', root/'first')
            repaired = prepare_stage('tq9', 'interpret', root/'run', root/'repair',
                options={'revision': 1, 'feedback': {'defects': ['temperature decoded outside observed range']}})
            for prepared in (first, repaired):
                self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            a, b = Path(first['work_dir']), Path(repaired['work_dir'])
            self.assertEqual((a/'image.bin').read_bytes(), (b/'image.bin').read_bytes())
            self.assertIn('temperature', (b/'DEFECTS.json').read_text())
            self.assertIn('DEFECTS.json', json.loads((b/'INPUT_HASHES.json').read_text()))

class QualificationGateTests(unittest.TestCase):
    def test_valid_model_without_predicted_reply_qualification_is_not_accepted(self):
        from generative_driver.benchmark import case_root
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('tq9', 'interpret', root/'run', root/'worker')
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            __import__('shutil').copy2(case_root()/'cases/setup-smoke/model.json', Path(prepared['work_dir'])/'model.json')
            checked = check_stage('tq9', 'interpret', root/'run', prepared['work_dir'], {'status':'completed'})
            self.assertFalse(checked['ok'], checked)

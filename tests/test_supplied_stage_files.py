"""The files the stage package prepares, seals and hands on at acquire and interpret, on the hand-written firmware.

The stage functions run on the hand-written firmware and mission of `tests/test_supplied_stages.py`.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from generative_driver.supplied import stages
from test_package import example_model
from test_supplied_stages import MISSION, hand_written_firmware


def prepare_stage(stage, run_dir, workspace, accepted=None, options=None):
    """What `stages.prepare_stage` returns for `stage` on the hand-written firmware and mission."""
    return stages.prepare_stage(stage, run_dir, workspace, accepted, options,
                                firmware=hand_written_firmware(Path(run_dir).parent), mission=MISSION)


def observe_stage(stage, run_dir, workspace, report, accepted=None, options=None):
    """What `stages.observe_stage` returns for `stage` on the hand-written firmware and mission."""
    return stages.observe_stage(stage, run_dir, workspace, report, accepted, options,
                                firmware=hand_written_firmware(Path(run_dir).parent), mission=MISSION)


class StagePreparationTests(unittest.TestCase):
    def test_acquire_hands_on_only_actual_imported_bytes(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('acquire', root / 'run', root / 'worker')
            # A claimed artifact is no import: nothing is handed on, and the missing import is a note, never a stop.
            empty = observe_stage('acquire', root / 'run', root / 'worker',
                                  {'status': 'completed', 'artifacts': [{'path': 'claimed.bin'}]})
            self.assertEqual(empty['artifacts'], [])
            self.assertEqual([(c['name'], c['passed']) for c in empty['checks']], [('imported_firmware_hash', False)])
            for key in ('ok', 'stop', 'route', 'fault'):
                self.assertNotIn(key, empty)
            result = call_tool('acquire_firmware_artifact', {'run_dir': str((root/'worker').resolve()),
                'source_path': prepared['inputs'][0], 'origin': 'provided_binary',
                'expected_sha256': prepared['context']['expected_sha256']})
            accepted = observe_stage('acquire', root / 'run', root / 'worker',
                {'status': 'completed', 'artifacts': [result['artifact']]})
            self.assertEqual([(c['name'], c['passed']) for c in accepted['checks']], [('imported_firmware_hash', True)])
            self.assertIn(str(Path(result['artifact']).resolve()), [str(Path(a).resolve()) for a in accepted['artifacts']])

    def test_interpret_receives_neutral_sealed_inputs_without_evaluator_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root/'evaluator-credential'; credential.write_text('SYNTHETIC PRIVATE CREDENTIAL')
            prepared = prepare_stage('interpret', root / 'run', root / 'worker',
                                     options={'evaluator_password_file': str(credential.resolve())})
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            self.assertFalse(prepared['report_required'])
            workspace = Path(prepared['work_dir'])
            self.assertTrue((workspace / 'image.bin').is_file())
            self.assertNotIn('evaluator_password_file', json.dumps(prepared))
            self.assertNotIn(str(credential.resolve()), json.dumps(prepared))
            for supplied in prepared['inputs']:
                self.assertNotIn(b'SYNTHETIC PRIVATE CREDENTIAL', Path(supplied).read_bytes())
            hashes = json.loads((workspace / 'INPUT_HASHES.json').read_text())
            self.assertEqual(hashes['image.bin'], hashlib.sha256((workspace / 'image.bin').read_bytes()).hexdigest())


class OneFormatTests(unittest.TestCase):
    """A run on a supplied device asks its interpret worker for meanings, with the one model document, and hands on the
    model alone (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3)."""

    def test_02_B8_a_run_on_a_supplied_device_gives_its_interpret_worker_the_one_model_document(self):
        from worker_texts import ASK, earlier_names, model_document, texts_of
        self.assertTrue(model_document().is_file(), 'the tool has no one model document')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('interpret', root / 'run', root / 'worker')
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            workspace = Path(prepared['work_dir'])
            self.assertEqual((workspace / 'INTERFACE_MODEL.md').read_bytes(), model_document().read_bytes())
            for name in ('CONTRACT.md', 'contract.py'):
                self.assertFalse((workspace / name).exists(), name)
            self.assertIn(ASK, (workspace / 'BRIEF.md').read_text(encoding='utf-8'))
            self.assertEqual(earlier_names({**texts_of(workspace), 'the prompt': prepared['prompt']}), [])

    def test_02_B13_the_interpret_handoff_carries_no_contract_or_protocol_a_worker_still_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('interpret', root / 'run', root / 'worker')
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            workspace = Path(prepared['work_dir'])
            model = example_model()
            model['operations']['measure']['description'] = 'Read the temperature of the probe.'
            (workspace / 'model.json').write_text(json.dumps(model), encoding='utf-8')
            (workspace / 'NOTES.md').write_text('The reply states degrees Celsius.\n', encoding='utf-8')
            for name in ('contract.json', 'protocol.json'):
                (workspace / name).write_text('{"schema": "written by a worker of an earlier kit"}', encoding='utf-8')
            checked = observe_stage('interpret', root / 'run', workspace, {'status': 'completed'})
            self.assertEqual(sorted(Path(a).name for a in checked['artifacts']), ['NOTES.md', 'model.json'])
            handed = next(Path(a) for a in checked['artifacts'] if Path(a).name == 'model.json')
            self.assertEqual(json.loads(handed.read_text(encoding='utf-8')), model)


if __name__ == '__main__':
    unittest.main()

class RepairPreparationTests(unittest.TestCase):
    def test_interpret_repair_preserves_firmware_and_seals_observed_defects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = prepare_stage('interpret', root/'run', root/'first')
            repaired = prepare_stage('interpret', root/'run', root/'repair',
                options={'revision': 1, 'feedback': {'defects': ['temperature decoded outside observed range']}})
            for prepared in (first, repaired):
                self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            a, b = Path(first['work_dir']), Path(repaired['work_dir'])
            self.assertEqual((a/'image.bin').read_bytes(), (b/'image.bin').read_bytes())
            self.assertIn('temperature', (b/'DEFECTS.json').read_text())
            self.assertIn('DEFECTS.json', json.loads((b/'INPUT_HASHES.json').read_text()))

    def test_02_B10_write_json_replaces_atomically_and_preserves_existing_file_on_write_failure(self):
        import os
        from unittest.mock import patch
        from generative_driver.supplied._files import write_json
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'history.json'
            write_json(target, {'version': 1})
            original = target.read_text(encoding='utf-8')
            # Failed os.replace preserves the existing file and removes temporary files.
            with patch.object(os, 'replace', side_effect=OSError('simulated crash')):
                with self.assertRaises(OSError):
                    write_json(target, {'version': 2})
            self.assertEqual(target.read_text(encoding='utf-8'), original)
            self.assertEqual([p.name for p in target.parent.iterdir()], ['history.json'])
            # Non-serializable JSON preserves the existing file and leaves no temp file.
            with self.assertRaises(ValueError):
                write_json(target, {'bad': float('nan')})
            self.assertEqual(target.read_text(encoding='utf-8'), original)
            self.assertEqual([p.name for p in target.parent.iterdir()], ['history.json'])
            # Normal replace uses os.replace and writes the new content cleanly.
            replaced = []
            real_replace = os.replace
            def track(src, dst):
                replaced.append((Path(src).parent, Path(dst)))
                return real_replace(src, dst)
            with patch.object(os, 'replace', side_effect=track):
                write_json(target, {'version': 3})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'version': 3})
            self.assertEqual(replaced, [(target.parent, target)])

class QualificationNoteTests(unittest.TestCase):
    def test_a_valid_model_without_predicted_reply_qualification_is_handed_on_with_a_note(self):
        # Catches a qualification gate refusing the candidate: its own qualification is a note, the model behind
        # an intact seal is handed on, and probe tests it against the device.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepare_stage('interpret', root/'run', root/'worker')
            self.addCleanup(__import__('shutil').rmtree, Path(prepared['work_dir']).parent, True)
            (Path(prepared['work_dir'])/'model.json').write_text(json.dumps(example_model()), encoding='utf-8')
            checked = observe_stage('interpret', root/'run', prepared['work_dir'], {'status':'completed'})
            self.assertEqual([Path(a).name for a in checked['artifacts']], ['model.json'])
            checks = {c['name']: c['passed'] for c in checked['checks']}
            self.assertEqual((checks['sealed_inputs'], checks['predicted_reply_qualification']), (True, False))
            for key in ('ok', 'stop', 'route', 'fault', 'feedback', 'evaluator'):
                self.assertNotIn(key, checked)

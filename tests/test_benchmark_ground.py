"""Ground workers receive attributable measurements through the public stage seam."""
import hashlib
import json
import copy
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generative_driver.benchmark import prepare_stage


class GroundEvidenceTests(unittest.TestCase):
    def test_current_grounding_keeps_the_model_mapping_worker_probes_and_journal_without_grades(self):
        # Catches grounding an unmapped raw model, discarding observed captures, or projecting any grade.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root/'accepted'; source.mkdir()
            model = source/'1-model.json'; model.write_text('{"operations":{}}')
            model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
            adaptation = source/'2-adaptation.json'; adaptation.write_text('{"kind":"emulator UART-to-TCP wiring"}')
            mapping = source/'0-capabilities.json'
            mapping.write_text(json.dumps({'schema': 'benchmark-capabilities/2', 'tasks': {
                'temperature': {'operation': 'sample', 'constants': {}, 'inputs': {},
                                'outputs': {'temperature': {'output': 'raw', 'unit': 'degC'}}}}}))
            probe = source/'4-interface-1.json'
            probe.write_text(json.dumps({'schema': 'interface-probe/1', 'model_sha256': model_hash,
                'operation': 'sample', 'parameters': {}, 'allow_effects': [], 'ok': True,
                'results': [{'ok': True, 'outputs': {'raw': 21}, 'units': {'raw': 'degC'},
                             'transcript': [{'tx_hex': '01', 'rx_hex': '15'}]}]}))
            stale = source/'5-observations.json'
            stale.write_text(json.dumps({'schema': 'benchmark-observations/2', 'model_sha256': model_hash,
                'diagnostics': [{'checks': [{'id': 'read/temperature', 'passed': True, 'expected': 'PRIVATE ORACLE'}]}]}))
            live = source/'3-live-observations-0.jsonl'
            live.write_text(json.dumps({'operation': 'sample', 'result': {'outputs': {'raw': 21}},
                'observation': {'duty': 0, 'temperature_reference': 21, 'source': 'instrument',
                                'units': {'duty': 'permille', 'temperature_reference': 'degC'}}})+'\n')
            artifacts = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in (mapping, model, adaptation, live, probe, stale)]
            # Ground runs on the device the bench harness provides, as the configurator's record holds it.
            prepared = prepare_stage('tq9-v2', 'ground', root/'run', root/'worker',
                accepted=[{'stage': 'probe', 'artifacts': artifacts}],
                options={'device': {'session_id': 'provided', 'binding': {'host': '127.0.0.1', 'port': 1}}})
            ws = root/'worker'
            evidence = json.loads((ws/'ground-evidence/device-observations.json').read_text())
            self.assertEqual(sorted(prepared['allowed_tools']), ['interface_execute', 'probe_diff', 'probe_run'])
            self.assertEqual(prepared['binding'], {'host': '127.0.0.1', 'port': 1})
            self.assertEqual((ws/'model/model.json').read_bytes(), model.read_bytes())
            self.assertEqual((ws/'model/adaptation.json').read_bytes(), adaptation.read_bytes())
            self.assertEqual(prepared['context']['model_dir'], str(ws/'model'))
            self.assertIn(str(ws/'model'), prepared['inputs'])
            self.assertEqual(evidence['model']['sha256'], model_hash)
            self.assertEqual((ws/evidence['capabilities']['artifact']).read_bytes(), mapping.read_bytes())
            self.assertEqual((ws/evidence['probes'][0]['artifact']).read_bytes(), probe.read_bytes())
            self.assertEqual(evidence['probes'][0]['sha256'], hashlib.sha256(probe.read_bytes()).hexdigest())
            self.assertIn(str(ws/evidence['probes'][0]['artifact']), prepared['inputs'])
            self.assertEqual((ws/evidence['live_observations']['artifact']).read_bytes(), live.read_bytes())
            text = json.dumps(evidence) + ''.join(Path(path).read_text() for path in prepared['inputs'] if Path(path).is_file())
            for forbidden in ('PRIVATE', 'expected', '"passed"', '"checks"', 'diagnostic'):
                self.assertNotIn(forbidden, text)
            self.assertIn('device evidence', prepared['objective'])
            probe.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                prepare_stage('tq9-v2', 'ground', root/'run', root/'changed',
                    accepted=[{'stage': 'probe', 'artifacts': artifacts}])

    def test_ground_without_an_adapted_model_skips_with_a_note(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for accepted in ([], [{'stage': 'probe', 'artifacts': []}]):
                with self.subTest(accepted=accepted), patch(
                        'generative_driver.benchmark_support.device_provider.DeviceProvider.start', side_effect=AssertionError('emulator started')):
                    prepared = prepare_stage('tq9-v2', 'ground', root/'run', root/'worker', accepted=accepted)
                    self.assertIn('no interpreted model available', prepared['skip'])

    def test_legacy_ground_receives_worker_probes_and_device_readings_never_evaluator_observations(self):
        # Catches legacy tq9 grounding from the evaluator's probe observations (its graded measurements and monitor
        # reads), or ground bytes replaced after they were verified. Legacy ground is prepared as an emulated case's.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            accepted = root/'accepted'; accepted.mkdir()
            model_path = accepted/'2-model.json'; model_path.write_text(json.dumps({'operations': {'read': {'outputs': {
                'temperature': {'unit': 'degC', 'kind': 'integer', 'offset': 0, 'size': 2, 'scale': .1}}}}}))
            model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
            probe_path = accepted/'3-interface-1.json'
            probe_path.write_text(json.dumps({'schema': 'interface-probe/1', 'model_sha256': model_hash, 'operation': 'read',
                'parameters': {}, 'allow_effects': [], 'ok': True, 'evidence_kind': 'live', 'results': [{'ok': True,
                'outputs': {'temperature': 21.5}, 'units': {'temperature': 'degC'}, 'transcript': [{'tx_hex': '01', 'rx_hex': 'd700'}]}]}))
            mapping = accepted/'0-capabilities.json'
            mapping.write_text(json.dumps({'temperature': {'operation': 'read', 'parameters': {}, 'output': 'temperature'}}))
            live = accepted/'4-live-observations-0.jsonl'
            live.write_text(json.dumps({'operation': 'read', 'result': {'outputs': {'temperature': 21.5}},
                'observation': {'duty': .4, 'source': 'instrument', 'units': {'duty': 'fraction'}}}) + '\n')
            stale = accepted/'1-observations.json'
            stale.write_text(json.dumps({'observations': {'temperature': 21.5, 'requested_duty': .4}, 'model_sha256': model_hash,
                'score': {'expected': 'PRIVATE ORACLE'}, 'monitor_observations': {'requested_duty': {'compare': 400}}}))
            artifacts = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'kind': 'file'}
                         for p in (stale, model_path, probe_path, mapping, live)]
            device = {'device': {'session_id': 'provided', 'binding': {'host': '127.0.0.1', 'port': 1}}}
            ws = root/'worker'
            prepared = prepare_stage('tq9', 'ground', root/'run', ws, accepted=[{'stage': 'probe', 'artifacts': artifacts}],
                                     options=device)
            evidence = json.loads((ws/'ground-evidence/device-observations.json').read_text())
            self.assertEqual(prepared['binding'], device['device']['binding'])
            self.assertEqual((ws/'model/model.json').read_bytes(), model_path.read_bytes())
            self.assertEqual((ws/evidence['probes'][0]['artifact']).read_bytes(), probe_path.read_bytes())
            self.assertEqual((ws/evidence['capabilities']['artifact']).read_bytes(), mapping.read_bytes())
            self.assertEqual((ws/evidence['live_observations']['artifact']).read_bytes(), live.read_bytes())
            text = json.dumps(evidence) + ''.join(Path(path).read_text() for path in prepared['inputs'] if Path(path).is_file())
            for forbidden in ('PRIVATE', 'expected', 'score', 'monitor_observations', 'requested_duty'):
                self.assertNotIn(forbidden, text)
            # Without the device its bench harness provides, ground waits for the operator.
            self.assertIn('blocked', prepare_stage('tq9', 'ground', root/'run', root/'no-device',
                                                   accepted=[{'stage': 'probe', 'artifacts': artifacts}]))
            # A filesystem change after verification must not replace copied bytes.
            original_read = Path.read_bytes
            before = model_path.read_bytes()
            changed = False
            def concurrent_change(path):
                nonlocal changed
                content = original_read(path)
                if path == model_path and not changed:
                    changed = True
                    model_path.write_text('{}')
                return content
            with patch.object(Path, 'read_bytes', concurrent_change):
                prepare_stage('tq9', 'ground', root/'run', root/'next-worker', accepted=[{'stage': 'probe', 'artifacts': artifacts}],
                              options=device)
            self.assertEqual((root/'next-worker/model/model.json').read_bytes(), before)

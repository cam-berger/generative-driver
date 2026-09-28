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
    def test_evaluation_retains_existing_monitor_reads_and_live_probe_hashes(self):
        from generative_driver.benchmark import case_root
        from generative_driver.benchmark_support.evaluate import evaluate_model
        monitor_commands = []
        timer = {'compare': 0}
        class Connection:
            def __init__(self, monitor): self.monitor, self.buffer = monitor, b''
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def close(self): pass
            def settimeout(self, value): pass
            def sendall(self, data): self.send(data)
            def send(self, data):
                if data.startswith(b'\xff'): return len(data)
                if self.monitor:
                    command = data.decode().strip(); monitor_commands.append(command)
                    value = timer['compare'] if command.endswith('00000001') else 999
                    self.buffer = f'{command}\n0x{value:x}\n(device)'.encode()
                else:
                    if data == b'SET\n': timer['compare'] = 400
                    if data == b'OFF\n': timer['compare'] = 0
                    self.buffer = {b'ID?\n': b'DEMO-42\n', b'T\n': b'T:21.5\n'}.get(data, b'OK\n')
                return len(data)
            def recv(self, size):
                if not self.buffer: raise socket.timeout()
                out, self.buffer = self.buffer[:size], self.buffer[size:]
                return out
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); model_dir = root/'model'; model_dir.mkdir()
            model = json.loads((case_root()/'cases/setup-smoke/model.json').read_text())
            model['channel'] = {'type': 'tcp'}
            for name, command in [('arm', 'ARM\n'), ('set', 'SET\n'), ('off', 'OFF\n')]:
                op = copy.deepcopy(model['operations']['measure'])
                op['effect'], op['outputs'], op['steps'][0]['tx'] = 'actuate', {}, [{'text': command}]
                model['operations'][name] = op
                model['provenance'].append({'item': 'operations.'+name, 'source': 'independent synthetic fixture'})
            (model_dir/'model.json').write_text(json.dumps(model))
            capabilities = {'temperature': {'operation': 'measure', 'output': 'temperature'},
                'arm': {'operation': 'arm'}, 'set_duty': {'operation': 'set'}, 'disarm': {'operation': 'off'}}
            truth = {'monitor': {'pwm_compare': 1, 'pwm_reload': 2}, 'checks': [
                {'id': 'temperature', 'expected': 21.5, 'absolute_tolerance': .1},
                {'id': 'requested_duty', 'expected': .4, 'absolute_tolerance': .001},
                {'id': 'disarmed_duty', 'expected': 0., 'absolute_tolerance': .001},
                {'id': 'no_grant', 'expected': True}]}
            with patch('socket.create_connection', side_effect=lambda address, **kwargs: Connection(address[1] == 5002)):
                evaluated = evaluate_model(model_dir, capabilities,
                    {'monitor_port': 5002, 'binding': {'host': 'fixture', 'port': 5001}, 'image_sha256': 'fixture-image'},
                    truth, root/'evaluation')
            self.assertEqual(evaluated['score']['verdict'], 'passed', evaluated)
            self.assertEqual(evaluated['monitor_observations']['requested_duty']['compare'], 400)
            self.assertEqual(evaluated['monitor_observations']['requested_duty']['reload'], 999)
            self.assertEqual(evaluated['monitor_observations']['disarmed_duty']['compare'], 0)
            self.assertIn('0x190', evaluated['monitor_observations']['requested_duty']['reads'][0]['response'])
            self.assertEqual(len(monitor_commands), 4)
            self.assertIsInstance(evaluated['monitor_observations']['requested_duty']['time'], float)
            for call in evaluated['calls']:
                self.assertEqual(call['probe_sha256'], hashlib.sha256(Path(call['result']['probe']).read_bytes()).hexdigest())

    def test_ground_handoff_exposes_accepted_measurements_and_transactions_without_oracle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            accepted = root/'accepted'; accepted.mkdir()
            model = {'operations': {'read': {'outputs': {'temperature': {
                'unit': 'degC', 'kind': 'integer', 'offset': 0, 'size': 2, 'scale': .1}}}}}
            model_path = accepted/'2-model.json'; model_path.write_text(json.dumps(model))
            model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
            probe = {'schema': 'interface-probe/1', 'model_sha256': model_hash,
                'operation': 'read', 'parameters': {}, 'allow_effects': [], 'ok': True,
                'evidence_kind': 'live', 'results': [{'ok': True, 'outputs': {'temperature': 21.5},
                    'units': {'temperature': 'degC'}, 'transcript': [{'tx_hex': '01', 'rx_hex': 'd700'}]}]}
            probe_path = accepted/'3-interface-1.json'; probe_path.write_text(json.dumps(probe))
            probe_hash = hashlib.sha256(probe_path.read_bytes()).hexdigest()
            observation = {'execution': 'actual-agent-emulation', 'physical': False,
                'model_sha256': model_hash, 'image_sha256': 'fixture-image',
                'observations': {'temperature': 21.5, 'requested_duty': .4, 'disarmed_duty': 0., 'no_grant': True},
                'capabilities': {'temperature': {'operation': 'read', 'output': 'temperature'}},
                'calls': [{'capability': 'temperature', 'grants': [], 'probe_sha256': probe_hash,
                    'result': {'probe': '/obsolete/mutable/probe.json'}}],
                'monitor_observations': {
                    'requested_duty': {'duty': .4, 'compare': 400, 'reload': 999, 'time': 100,
                        'reads': [{'register': 'compare', 'command': 'read compare', 'response': '0x190', 'value': 400},
                                  {'register': 'reload', 'command': 'read reload', 'response': '0x3e7', 'value': 999}]},
                    'disarmed_duty': {'duty': 0., 'compare': 0, 'reload': 999, 'time': 101,
                        'reads': [{'register': 'compare', 'command': 'read compare', 'response': '0x0', 'value': 0},
                                  {'register': 'reload', 'command': 'read reload', 'response': '0x3e7', 'value': 999}]}},
                'score': {'expected': 'PRIVATE ORACLE'}, 'source_files': 'PRIVATE SOURCE'}
            observed_path = accepted/'1-observations.json'; observed_path.write_text(json.dumps(observation))
            artifacts = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'kind': 'file'}
                         for p in (observed_path, model_path, probe_path)]
            run = root/'run'; (run/'benchmark').mkdir(parents=True)
            (run/'benchmark/state.json').write_text(json.dumps({'probe_evaluation': {
                'execution': 'wrong mutable state', 'observations': {'temperature': -999},
                'observation_channel': 'unsupported claim', 'physical': False}}))
            ws = root/'worker'
            prepared = prepare_stage('tq9', 'ground', run, ws,
                                     accepted=[{'stage': 'probe', 'artifacts': artifacts}])
            bundle = ws/'ground-evidence'
            handoff = json.loads((bundle/'independent-observations.json').read_text())
            self.assertEqual(handoff['measurements']['temperature']['unit'], 'degC')
            self.assertEqual(handoff['measurements']['temperature']['value'], 21.5)
            self.assertIn('actual', handoff['measurements']['requested_duty']['meaning'])
            self.assertEqual(handoff['monitor_observations']['requested_duty']['compare'], 400)
            entry = handoff['calls'][0]
            copied = bundle/entry['artifact']
            self.assertEqual(copied.read_bytes(), probe_path.read_bytes())
            self.assertEqual(entry['sha256'], probe_hash)
            self.assertIn(str(copied), prepared['inputs'])
            self.assertNotIn('PRIVATE', json.dumps(handoff))
            self.assertNotIn('score', handoff)
            self.assertNotIn('expected', json.dumps(handoff))
            # Historical accepted captures have duty scalars but no raw monitor
            # records. Preserve those observations with the missing detail explicit.
            observation.pop('monitor_observations')
            observation['calls'][0].pop('probe_sha256')
            observation['calls'][0]['result']['probe'] = '/obsolete/interface-1.json'
            observed_path.write_text(json.dumps(observation))
            artifacts[0]['sha256'] = hashlib.sha256(observed_path.read_bytes()).hexdigest()
            prepare_stage('tq9', 'ground', run, root/'legacy-worker',
                          accepted=[{'stage': 'probe', 'artifacts': artifacts}])
            legacy = json.loads((root/'legacy-worker/ground-evidence/independent-observations.json').read_text())
            self.assertEqual(legacy['measurements']['requested_duty']['value'], .4)
            self.assertEqual(legacy['monitor_observations'], {})
            self.assertTrue(any('not raw register' in note for note in legacy['limitations']))
            self.assertEqual((root/'legacy-worker/ground-evidence/evidence/probe-1.json').read_bytes(), probe_path.read_bytes())
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
                prepare_stage('tq9', 'ground', run, root/'next-worker',
                              accepted=[{'stage': 'probe', 'artifacts': artifacts}])
            self.assertEqual((root/'next-worker/ground-evidence/evidence/model.json').read_bytes(), before)

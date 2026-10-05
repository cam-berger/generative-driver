"""Discovery and repair contracts; synthetic peers, never model performance claims."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from generative_driver.benchmark import prepare_stage, check_stage


class InterpretationBindingTests(unittest.TestCase):
    def test_supplied_emulator_wiring_allows_qualification_without_inventing_uart_baud(self):
        # Catches requiring an unknown physical baud before testing recovered bytes.
        from tq9_workflow_fixture import model, replies
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepared = prepare_stage('sampled-sensor-v1', 'interpret', root/'run', root/'worker')
            ws = Path(prepared['work_dir'])
            self.addCleanup(shutil.rmtree, ws.parent, True)
            self.assertTrue((ws/'BINDING_CONTEXT.json').is_file(), 'Interpretation lacks supplied emulator wiring')
            context = json.loads((ws/'BINDING_CONTEXT.json').read_text())
            self.assertEqual(context['runtime_channel'], {'type': 'tcp'})
            self.assertEqual(context['physical_channel'], {'type': 'uart'})
            self.assertEqual(context['unverified'], ['baudrate', 'bytesize', 'parity', 'stopbits', 'pins'])
            self.assertNotIn('host', context)
            self.assertNotIn('port', context)
            candidate = model()
            candidate['channel'] = context['runtime_channel']
            (ws/'model.json').write_text(json.dumps(candidate))
            (ws/'replies.json').write_text(json.dumps(replies()))
            (ws/'NOTES.md').write_text('Physical UART baud, framing and pins remain unverified. TCP tests bytes only.')
            for command in ([sys.executable, 'validate.py', '.'],
                            [sys.executable, 'qualify.py', '.', 'replies.json']):
                result = subprocess.run(command, cwd=ws, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            checked = check_stage('sampled-sensor-v1', 'interpret', root/'run', ws, None)
            self.assertTrue(checked['ok'], checked)
            self.assertIn('BINDING_CONTEXT.json', json.loads((ws/'INPUT_HASHES.json').read_text()))


class IndependentProbeObservationTests(unittest.TestCase):
    def test_live_probe_returns_measured_reference_to_compare_with_candidate_decoder(self):
        # Catches probing bytes without exposing the independent observation channel.
        from tq9_workflow_fixture import Silicon, model
        from generative_driver.benchmark_support.emulated import worker_tool
        recipe = {'observations': {
            'compare': {'command': 'compare', 'width': 32, 'count': 1},
            'reload': {'command': 'reload', 'width': 32, 'count': 1},
            'temperature': {'kind': 'scalar', 'command': 'temperature', 'unit': 'degC'}}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            peer = Silicon(root/'firmware.bin')
            self.addCleanup(peer.close)
            session = peer.session(recipe, root/'native')
            candidate = model()
            candidate['channel'] = {'type': 'tcp'}
            candidate['operations']['measure']['outputs']['temperature']['scale'] = 10
            model_dir = root/'model'; model_dir.mkdir()
            (model_dir/'model.json').write_text(json.dumps(candidate))
            with patch('generative_driver.benchmark_support.emulated._session', return_value=session):
                result = worker_tool('tq9-v2', root/'run', 'interface_execute', {
                    'model_dir': str(model_dir), 'run_dir': str(root/'tools'), 'operation': 'measure',
                    'binding': session.binding}, {})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['outputs']['temperature'], 200)
            self.assertIn('observation', result, 'Probe hides independently measured reference')
            self.assertEqual(result['observation']['temperature_reference'], 20)
            self.assertEqual(result['observation']['temperature_unit'], 'degC')
            self.assertEqual(result['observation']['duty'], 0)
            self.assertEqual(result['observation']['phase'], 'diagnostic')
            self.assertFalse(peer.running)
            self.assertFalse(peer.paused_requests)
            self.assertNotIn('command', json.dumps(result['observation']))
            self.assertNotIn('expected', json.dumps(result['observation']))


class DiagnosticRepairTests(unittest.TestCase):
    def test_malformed_candidate_capabilities_route_to_model_repair(self):
        # Catches candidate JSON syntax escaping as a host/evaluator failure.
        from generative_driver.benchmark_support.cases import _write
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); ws = root/'worker'; ws.mkdir()
            model_dir = root/'model'; model_dir.mkdir()
            (model_dir/'model.json').write_text('{}')
            _write(root/'run/benchmark/state.json', {'adapted_model_dir': str(model_dir),
                'adaptation': {'adapted_model_sha256': sha256(b'{}').hexdigest()}})
            (ws/'capabilities.json').write_text('{unfinished')
            try:
                checked = check_stage('tq9-v2', 'probe', root/'run', ws,
                    {'status': 'needs_revision', 'unresolved': []})
            except ValueError as error:
                self.fail('Candidate JSON escaped the model-fault boundary: ' + str(error))
            self.assertFalse(checked['ok'])
            self.assertEqual(checked['fault'], 'model')
            self.assertEqual(checked['route'], 'interpret')
            self.assertEqual(checked['feedback']['missing'], ['valid capabilities.json'])
            original_read = Path.read_text
            def unreadable(path, *args, **kwargs):
                if path == ws/'capabilities.json':
                    raise PermissionError('host fixture cannot read file')
                return original_read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', unreadable), self.assertRaises(PermissionError):
                check_stage('tq9-v2', 'probe', root/'run', ws, {'status': 'needs_revision'})

    def test_feedback_pairs_observed_discrepancy_with_check_and_channel(self):
        # Catches stripping the information a repair agent needs to locate a defect.
        from generative_driver.benchmark_support.behavior import project_feedback, validate_records
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a'*64, 'checks': [
            {'id': 'diagnostic/read/temperature', 'revision': 0, 'kind': 'number', 'expected': 20,
             'absolute_tolerance': .1, 'unit': 'degC', 'channel': 'runtime-transcript'},
            {'id': 'diagnostic/read/reference', 'revision': 0, 'kind': 'number', 'expected': 20,
             'absolute_tolerance': .1, 'unit': 'degC', 'channel': 'independent-monitor'}]}
        records = [dict(row, artifact_sha256='a'*64, value=value) for row, value in [
            ({'id': 'diagnostic/read/temperature', 'revision': 0, 'task_id': 'temperature',
              'unit': 'degC', 'channel': 'runtime-transcript'}, 200),
            ({'id': 'diagnostic/read/reference', 'revision': 0, 'task_id': 'reference',
              'unit': 'degC', 'channel': 'independent-monitor'}, 20)]]
        feedback = project_feedback(records, validate_records(contract, records))
        self.assertEqual(feedback['records'][0]['id'], 'diagnostic/read/temperature')
        self.assertEqual(feedback['records'][0]['channel'], 'runtime-transcript')
        self.assertFalse(feedback['records'][0]['passed'])
        self.assertEqual(feedback['records'][0]['reason'], 'value mismatch')
        self.assertEqual(feedback['records'][1]['value'], 20)
        self.assertTrue(feedback['records'][1]['passed'])
        self.assertNotIn('expected', json.dumps(feedback))
        self.assertNotIn('tolerance', json.dumps(feedback))

    def test_unsupported_task_is_model_discrepancy_without_device_io(self):
        # Catches misclassifying an incomplete candidate mapping as a host crash.
        from tq9_workflow_fixture import model
        from generative_driver.benchmark_support.emulated_actions import model_invoker
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'model.json').write_text(json.dumps(model()))
            invoke = model_invoker(root, {'schema': 'benchmark-capabilities/2', 'tasks': {}},
                                   {'host': '127.0.0.1', 'port': 1}, root/'tools', [])
            try:
                result = invoke('temperature', {}, [])
            except ValueError as error:
                self.fail('Candidate mapping escaped the model-fault boundary: ' + str(error))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['fault'], 'model')
            self.assertEqual(result['error']['code'], 'capability_mapping')
            self.assertEqual(result['transcript'], [])
            self.assertFalse((root/'tools').exists())

    def test_incomplete_capability_mapping_requests_bounded_interpretation_repair(self):
        # Catches consuming an evidence gap as a terminal failure before repair.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath('worker').mkdir()
            checked = check_stage('tq9-v2', 'probe', root/'run', root/'worker',
                {'status': 'needs_revision', 'summary': 'Decoder contradicts observations',
                 'unresolved': ['temperature scaling remains unresolved']})
            self.assertFalse(checked['ok'])
            self.assertEqual(checked.get('route'), 'interpret')
            self.assertEqual(checked['fault'], 'model')
            self.assertEqual(checked['feedback']['unresolved'], ['temperature scaling remains unresolved'])
            self.assertEqual(checked['feedback']['missing'], ['capabilities.json'])


if __name__ == '__main__':
    unittest.main()

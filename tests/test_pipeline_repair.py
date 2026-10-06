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
    def test_malformed_interpretation_is_retained_for_bounded_repair(self):
        # Catches intact frozen output being terminal just because collection rejected its schema.
        for raw in ('{unfinished', '{"schema":"unsupported-model/99"}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                first = prepare_stage('tq9-v2', 'interpret', root/'run', root/'initial')
                first_ws = Path(first['work_dir']); self.addCleanup(shutil.rmtree, first_ws.parent, True)
                (first_ws/'model.json').write_text(raw)
                (first_ws/'NOTES.md').write_text('Partial analysis to preserve.')
                failed = check_stage('tq9-v2', 'interpret', root/'run', first_ws, None)
                self.assertEqual(failed['fault'], 'model')
                self.assertEqual(failed['route'], 'interpret')
                self.assertTrue(failed['feedback']['structural_defects'])
                following = prepare_stage('tq9-v2', 'interpret', root/'run', root/'repair',
                    options={'revision': 1, 'feedback': failed['feedback']})
                ws = Path(following['work_dir']); self.addCleanup(shutil.rmtree, ws.parent, True)
                context = json.loads((ws/'REPAIR_CONTEXT.json').read_text())
                prior = context['history'][-1]['files']
                self.assertEqual((ws/prior['model.json']).read_text(), raw)

    def test_repair_does_not_follow_candidate_links_outside_the_workspace(self):
        # Catches turning a candidate symlink into host-copied repair evidence.
        from tq9_workflow_fixture import model
        from generative_driver.benchmark_support.cases import _write
        from generative_driver.configurator import digest
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); ws = root/'worker'; ws.mkdir()
            model_dir = ws/'model'; model_dir.mkdir()
            (model_dir/'model.json').write_text(json.dumps(model()))
            _write(root/'run/benchmark/state.json', {'adapted_model_dir': str(model_dir),
                'adaptation': {'adapted_model_sha256': digest(model_dir/'model.json')}})
            private = root/'outside.txt'; private.write_text('PRIVATE OUTSIDE SENTINEL')
            try:
                (ws/'capabilities.json').symlink_to(private)
            except OSError:
                self.skipTest('Symbolic links unavailable for this test user')
            with self.assertRaisesRegex(ValueError, 'assigned evidence roots'):
                check_stage('tq9-v2', 'probe', root/'run', ws, {'status': 'needs_revision'})
            for saved in (root/'run/benchmark').rglob('*'):
                if saved.is_file():
                    self.assertNotIn(b'PRIVATE OUTSIDE SENTINEL', saved.read_bytes())

    def test_structural_revision_preserves_the_failed_interpretation(self):
        # Catches losing partial analysis when qualification requests another attempt.
        from tq9_workflow_fixture import model
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = prepare_stage('tq9-v2', 'interpret', root/'run', root/'initial')
            first_ws = Path(first['work_dir']); self.addCleanup(shutil.rmtree, first_ws.parent, True)
            candidate = model(); candidate['channel'] = {'type': 'tcp'}
            (first_ws/'model.json').write_text(json.dumps(candidate))
            (first_ws/'NOTES.md').write_text('Partial handler analysis; predicted replies still missing.')
            failed = check_stage('tq9-v2', 'interpret', root/'run', first_ws, None)
            self.assertEqual(failed['route'], 'interpret')
            following = prepare_stage('tq9-v2', 'interpret', root/'run', root/'repair',
                options={'revision': 1, 'feedback': failed['feedback']})
            ws = Path(following['work_dir']); self.addCleanup(shutil.rmtree, ws.parent, True)
            context = json.loads((ws/'REPAIR_CONTEXT.json').read_text())
            self.assertTrue(context['history'], 'Structural repair lost the preceding model and notes')
            prior = context['history'][-1]
            self.assertEqual(prior['stage'], 'interpret')
            self.assertEqual(json.loads((ws/prior['files']['model.json']).read_text()), candidate)
            self.assertIn('replies still missing', (ws/prior['files']['NOTES.md']).read_text())

    def test_repair_receives_immutable_prior_candidate_and_public_task_context(self):
        # Catches starting binary discovery over after losing the failed candidate.
        from tq9_workflow_fixture import model, replies
        from generative_driver.configurator import digest
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); source = root/'run/accepted'; source.mkdir(parents=True)
            (source/'model.json').write_text(json.dumps(model()))
            (source/'replies.json').write_text(json.dumps(replies()))
            (source/'NOTES.md').write_text('Candidate inference; unresolved units need live evidence.')
            accepted = [{'stage': 'interpret', 'artifacts': [
                {'path': str(p), 'sha256': digest(p)} for p in source.iterdir()]}]
            with patch('generative_driver.benchmark_support.emulated._session',
                       return_value=SimpleNamespace(binding={'host': '127.0.0.1', 'port': 1})):
                prepare_stage('tq9-v2', 'probe', root/'run', root/'probe', accepted)
            (root/'probe/capabilities.json').write_text('{"schema":"benchmark-capabilities/2","tasks":{}}')
            failed = check_stage('tq9-v2', 'probe', root/'run', root/'probe',
                {'status': 'needs_revision', 'summary': 'Canonical tasks are missing', 'unresolved': ['temperature']}, accepted)
            prepared = prepare_stage('tq9-v2', 'interpret', root/'run', root/'repair', accepted,
                options={'revision': 1, 'feedback': failed['feedback'], 'configured_effects': ['write', 'actuate']})
            ws = Path(prepared['work_dir']); self.addCleanup(shutil.rmtree, ws.parent, True)
            self.assertTrue((ws/'REPAIR_CONTEXT.json').is_file(), 'Repair has no usable context contract')
            context = json.loads((ws/'REPAIR_CONTEXT.json').read_text())
            self.assertEqual(context['mode'], 'repair')
            self.assertEqual(context['feedback'], 'DEFECTS.json')
            prior = context['history'][-1]['files']
            expected_model = model(); expected_model['channel'] = {'type': 'tcp'}
            self.assertEqual(json.loads((ws/prior['model.json']).read_text()), expected_model)
            self.assertEqual(json.loads((ws/prior['replies.json']).read_text()), replies())
            self.assertEqual(json.loads((ws/prior['capabilities.json']).read_text())['tasks'], {})
            self.assertEqual(json.loads((ws/prior['worker-report.json']).read_text())['summary'], 'Canonical tasks are missing')
            contract = json.loads((ws/context['task_contract']).read_text())
            self.assertEqual(contract['tasks']['temperature']['outputs'], {'temperature': {'unit': 'degC'}})
            self.assertEqual(contract['effect_grants'], ['write', 'actuate'])
            hashes = json.loads((ws/'INPUT_HASHES.json').read_text())
            self.assertEqual(hashes[prior['model.json']], digest(ws/prior['model.json']))
            self.assertIn('REPAIR_CONTEXT.json', hashes)
            self.assertIn('DEFECTS.json', hashes)
            # Repair edits the root output, never the sealed prior artifact.
            (ws/'model.json').write_text('{}')
            self.assertEqual(json.loads((ws/prior['model.json']).read_text()), expected_model)
            self.assertNotIn('evaluator_password', json.dumps(context))
            next((root/'run/benchmark/candidate-history').rglob('model.json')).write_text('{}')
            with self.assertRaisesRegex(ValueError, 'repair evidence hash changed'):
                prepare_stage('tq9-v2', 'interpret', root/'run', root/'changed', accepted,
                    options={'revision': 1, 'feedback': failed['feedback']})

    def test_probe_supplies_executable_capability_validation_before_submission(self):
        from tq9_workflow_fixture import model, capabilities
        from generative_driver.configurator import digest
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'original'; source.mkdir()
            (source/'model.json').write_text(json.dumps(model()))
            handoff = [{'stage': 'interpret', 'artifacts': [
                {'path': str(source/'model.json'), 'sha256': digest(source/'model.json')}]}]
            with patch('generative_driver.benchmark_support.emulated._session',
                       return_value=SimpleNamespace(binding={'host': '127.0.0.1', 'port': 1})):
                prepared = prepare_stage('tq9-v2', 'probe', root/'run', root/'worker', handoff)
            ws = root/'worker'
            helper = ws/'validate_capabilities.py'
            self.assertTrue(helper.is_file(), 'Probe has no pure capability validator')
            self.assertTrue((ws/'CAPABILITY_FORMAT.md').is_file())
            self.assertIn(str(helper.resolve()), prepared['inputs'])
            self.assertIn('validate_capabilities.py', prepared['objective'])
            mapping = capabilities()
            del mapping['tasks']['set_duty']['inputs']['duty']['kind']
            mapping['tasks']['set_duty']['inputs']['duty']['mapping'] = 'copy'
            (ws/'capabilities.json').write_text(json.dumps(mapping))
            command = [sys.executable, str(helper), str(ws/'model'), str(ws/'capabilities.json')]
            bad = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertEqual(json.loads(bad.stdout)['errors'][0]['path'], 'tasks.set_duty.inputs.duty.kind')
            (ws/'capabilities.json').write_text(json.dumps(capabilities()))
            good = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertTrue(json.loads(good.stdout)['ok'])

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
    def test_preflight_refusal_is_distinct_from_attempting_to_open_a_transport(self):
        from tq9_workflow_fixture import model
        from interface_runtime.engine import execute
        from unittest.mock import Mock
        opening = Mock(side_effect=OSError('synthetic opening failure'))
        refused = execute(model(), 'set_duty', {'unexpected': 1}, allow_effects=['actuate'],
                          transport_factory=opening)
        opening.assert_not_called()
        self.assertFalse(refused['ok'])
        self.assertIs(refused['transport_open_attempted'], False)
        uncertain = execute(model(), 'set_duty', {'duty': 5}, allow_effects=['actuate'],
                            transport_factory=opening)
        opening.assert_called_once()
        self.assertFalse(uncertain['ok'])
        self.assertIs(uncertain['transport_open_attempted'], True)

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
            journal = root/'run/benchmark/live-observations-0.jsonl'
            self.assertTrue(journal.is_file(), 'Live comparison is lost before the next repair')
            saved = json.loads(journal.read_text().splitlines()[0])
            self.assertEqual(saved['result']['outputs']['temperature'], 200)
            self.assertEqual(saved['observation']['temperature_reference'], 20)
            self.assertEqual(saved['before_observation']['temperature_reference'], 20)
            self.assertEqual(saved['operation'], 'measure')
            self.assertNotIn('private_dir', json.dumps(saved))


class DiagnosticRepairTests(unittest.TestCase):
    def test_runtime_refusal_survives_diagnostic_projection_as_invocation_failure(self):
        # Catches a missing effect grant being presented only as a decoder mismatch.
        from tq9_workflow_fixture import model, capabilities
        from generative_driver.benchmark_support.emulated_actions import execute_plan, model_invoker
        from generative_driver.benchmark_support.behavior import project_feedback, validate_records
        class Session:
            def set_running(self, value): pass
            def observe(self): return {}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); candidate = model(); candidate['channel'] = {'type': 'tcp'}
            (root/'model.json').write_text(json.dumps(candidate))
            invoke = model_invoker(root, capabilities(), {'host': '127.0.0.1', 'port': 1}, root/'tools', [])
            contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a'*64, 'checks': [
                {'id': 'set/operation_ok', 'revision': 0, 'kind': 'boolean', 'expected': True,
                 'unit': 'boolean', 'channel': 'runtime-transcript'}]}
            records = execute_plan(Session(), invoke, [
                {'kind': 'call', 'task': 'set_duty', 'inputs': {'duty': 500}, 'grants': ['write']},
                {'kind': 'observe', 'checks': ['set/operation_ok']}], contract, root/'raw.json', lambda *args: {})
            feedback = project_feedback(records, validate_records(contract, records))
            self.assertIn('invocation_errors', feedback, 'Runtime refusal cause was erased')
            self.assertEqual(feedback['invocation_errors'][0]['code'], 'effect_grant_required')
            self.assertEqual(feedback['invocation_errors'][0]['task'], 'set_duty')
            self.assertNotIn('500', json.dumps(feedback))
            self.assertNotIn('expected', json.dumps(feedback))

    def test_missing_probe_execution_is_host_failure_without_model_feedback(self):
        # Catches toolkit startup/I/O envelopes being scored as missing outputs.
        from tq9_workflow_fixture import model, capabilities
        from generative_driver.benchmark_support.emulated_actions import model_invoker
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'model.json').write_text(json.dumps(model()))
            with patch('generative_driver.toolkit.call_tool', return_value={
                    'ok': False, '_exit': 1, 'error': 'Cannot create probe evidence directory'}):
                invoke = model_invoker(root, capabilities(), {'host': '127.0.0.1', 'port': 1}, root/'tools', [])
                result = invoke('temperature', {}, [])
            self.assertEqual(result.get('error', {}).get('fault'), 'host')
            self.assertEqual(result['error']['code'], 'probe_execution_unavailable')
            self.assertNotIn('capability_mapping', json.dumps(result))
            self.assertNotIn('transport_open_attempted', result)

    def test_final_candidate_binding_failure_is_a_behavioral_result(self):
        # Catches final inputs outside candidate bounds escaping as a host crash.
        from tq9_workflow_fixture import model, capabilities
        from generative_driver.benchmark_support.emulated_actions import canonical_package_invoker
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary); (package/'driver').mkdir()
            (package/'driver/model.json').write_text(json.dumps(model()))
            invoke = canonical_package_invoker(package, capabilities(), {'host': '127.0.0.1', 'port': 1})
            try:
                result = invoke('set_duty', {'duty': 1001}, ['actuate'])
            except ValueError as error:
                self.fail('Final candidate binding escaped behavioral grading: '+str(error))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['fault'], 'model')
            self.assertEqual(result['error']['code'], 'capability_mapping')
            self.assertFalse(result['transport_open_attempted'])
            self.assertEqual(result['transcript'], [])

    def test_ground_discrepancy_routes_to_interpretation_with_its_claim(self):
        # Catches dropping a requested ground repair while budget is available.
        from generative_driver.benchmark_support.cases import _write
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root/'run/benchmark/state.json', {'diagnostic': {'passed': True}})
            checked = check_stage('tq9-v2', 'ground', root/'run', root/'worker',
                {'status': 'needs_revision', 'summary': 'Decoded temperature contradicts the paired monitor reading',
                 'unresolved': ['The decoder needs a measured scale correction']})
            self.assertFalse(checked['ok'])
            self.assertEqual(checked.get('route'), 'interpret')
            self.assertEqual(checked['fault'], 'model')
            self.assertEqual(checked['feedback']['worker_claim']['unresolved'],
                             ['The decoder needs a measured scale correction'])
            self.assertEqual(checked['reason'], 'Ground evidence requires model revision')

    def test_binding_bounds_errors_reach_repair_without_disclosing_evaluator_inputs(self):
        from tq9_workflow_fixture import model, capabilities
        from generative_driver.benchmark_support.behavior import project_feedback, validate_records
        from generative_driver.benchmark_support.emulated_actions import execute_plan, model_invoker
        class Session:
            def set_running(self, value):
                self.running = value
            def observe(self):
                return {'duty': 0}
        def observations(raw, result, inputs):
            return raw
        observations.monitor_units = {'duty': 'permille'}
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a'*64, 'checks': [
            {'id': 'diagnostic/write/duty', 'revision': 0, 'kind': 'number', 'expected': 700,
             'absolute_tolerance': 0, 'unit': 'permille', 'channel': 'independent-monitor'}]}
        plan = [{'kind': 'call', 'task': 'set_duty', 'inputs': {'duty': 700}, 'grants': ['actuate']},
                {'kind': 'observe', 'checks': ['diagnostic/write/duty']}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = model(); candidate['operations']['set_duty']['parameters']['duty']['maximum'] = 100
            (root/'model.json').write_text(json.dumps(candidate))
            session = Session()
            with patch('generative_driver.toolkit.call_tool') as dispatch:
                invoke = model_invoker(root, capabilities(), {}, root/'tools', [])
                records = execute_plan(session, invoke, plan, contract, root/'evidence.json', observations)
            dispatch.assert_not_called()
            self.assertFalse(session.running)
            feedback = project_feedback(records, validate_records(contract, records))
            error = feedback['capability_errors'][0]
            self.assertEqual(error['task'], 'set_duty')
            self.assertEqual(error['code'], 'capability_mapping')
            self.assertIn('bounds', error['message'])
            for forbidden in ('700', 'expected', 'tolerance', str(root), 'password'):
                self.assertNotIn(forbidden, json.dumps(feedback))

    def test_malformed_mapping_targets_remain_candidate_errors(self):
        from tq9_workflow_fixture import model, capabilities
        from generative_driver.benchmark_support.behavior import validate_capabilities
        mapping = capabilities()
        mapping['tasks']['set_duty']['inputs']['duty']['parameter'] = {'wrong': 'shape'}
        mapping['tasks']['temperature']['outputs']['temperature']['output'] = ['wrong', 'shape']
        checked = validate_capabilities(mapping, model())
        self.assertFalse(checked['ok'])
        self.assertIn('tasks.set_duty.inputs.duty.parameter', {e['path'] for e in checked['errors']})
        self.assertIn('tasks.temperature.outputs.temperature.output', {e['path'] for e in checked['errors']})

    def test_capability_schema_failures_are_actionable_before_any_device_dispatch(self):
        # Reproduces the live repair's wrong copy key and missing unit metadata.
        from tq9_workflow_fixture import model
        from generative_driver.benchmark_support.behavior import bind_task, validate_capabilities
        from generative_driver.benchmark_support.cases import _write
        from hashlib import sha256
        candidate = model()
        candidate['operations']['measure']['outputs']['temperature']['unit'] = 'degC'
        capabilities = {'schema': 'benchmark-capabilities/2', 'tasks': {
            'temperature': {'operation': 'measure', 'constants': {}, 'inputs': {},
                'outputs': {'temperature': {'output': 'temperature'}}},
            'set_duty': {'operation': 'set_duty', 'constants': {},
                'inputs': {'duty': {'parameter': 'duty', 'mapping': 'copy'}}, 'outputs': {}}}}
        checked = validate_capabilities(capabilities, candidate)
        self.assertFalse(checked['ok'])
        self.assertEqual({error['path'] for error in checked['errors']},
            {'tasks.temperature.outputs.temperature.unit', 'tasks.set_duty.inputs.duty.kind'})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); ws = root/'worker'; ws.mkdir()
            model_dir = ws/'model'; model_dir.mkdir()
            raw = json.dumps(candidate).encode()
            (model_dir/'model.json').write_bytes(raw)
            (ws/'capabilities.json').write_text(json.dumps(capabilities))
            _write(root/'run/benchmark/state.json', {'adapted_model_dir': str(model_dir),
                'adaptation': {'adapted_model_sha256': sha256(raw).hexdigest()}})
            with patch('generative_driver.benchmark_support.emulated._diagnose') as dispatch:
                result = check_stage('tq9-v2', 'probe', root/'run', ws, {'status': 'completed'})
            dispatch.assert_not_called()
            self.assertEqual(result['fault'], 'model')
            self.assertEqual(result['route'], 'interpret')
            errors = result['feedback']['capability_errors']
            self.assertTrue(all(error['code'] == 'capability_mapping' for error in errors))
            self.assertIn('kind', json.dumps(errors))
            self.assertIn('unit', json.dumps(errors))
            for forbidden in ('expected', 'tolerance', str(root), 'password'):
                self.assertNotIn(forbidden, json.dumps(errors))
        capabilities['tasks']['temperature']['outputs']['temperature']['unit'] = 'degC'
        capabilities['tasks']['set_duty']['inputs']['duty'] = {'parameter': 'duty', 'kind': 'copy'}
        self.assertTrue(validate_capabilities(capabilities, candidate)['ok'])
        self.assertEqual(bind_task(capabilities, 'set_duty', {'duty': 500}, candidate),
            {'operation': 'set_duty', 'parameters': {'duty': 500}})

    def test_malformed_candidate_capabilities_route_to_model_repair(self):
        # Catches candidate JSON syntax escaping as a host/evaluator failure.
        from generative_driver.benchmark_support.cases import _write
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); ws = root/'worker'; ws.mkdir()
            model_dir = ws/'model'; model_dir.mkdir()
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

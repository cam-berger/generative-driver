"""Public package behavior against independent invented wire evidence.

The interface model is the one statement of a driver (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3): a
test that needs a meaning, a narrowed value or a precondition states it in the model and emits the package from it.
"""
import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_package import example_model, example_replay


def emit_fixture(root, model=None, replay_data=None, probe_operation='measure'):
    from generative_driver.toolkit import call_tool
    source = root / 'model'
    source.mkdir()
    (source / 'model.json').write_text(json.dumps(model or example_model()))
    replay = root / 'replay.json'
    replay.write_text(json.dumps(example_replay() if replay_data is None else replay_data))
    probe = call_tool('probe_run', {'run_dir': str(root / 'run'),
        'model_dir': str(source), 'operation': probe_operation, 'n': 1, 'replay': str(replay)})
    if not probe.get('ok'):
        raise AssertionError(probe)
    emitted = call_tool('emit_package', {'run_dir': str(root / 'run'),
        'model_dir': str(source), 'probe': probe['probe']})
    if not emitted.get('ok'):
        raise AssertionError(emitted)
    return Path(emitted['package_dir']), emitted


def rewrite_document(package, relative, document):
    """Put `document` in the place of one file of a package and rebuild the unsigned checksums, so that a test of a
    changed package reaches the check of what the file says, not the check of its hash. A test that needs a meaning
    states it in the model and emits the package from that model."""
    (package / relative).write_text(json.dumps(document))
    manifest_path = package / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    sha = hashlib.sha256((package / relative).read_bytes()).hexdigest()
    manifest['files'][relative] = sha
    key = 'mcp_catalog' if relative == 'mcp-tools.json' else Path(relative).stem
    manifest[key + '_sha256'] = sha
    manifest_path.write_text(json.dumps(manifest))
    (package / 'manifest.sha256').write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest()+'\n')


def controlled_model():
    model = example_model()
    model['operations']['read_armed'] = {'effect': 'read', 'parameters': {},
        'steps': [{'op': 'exchange', 'tx': [{'text': 'ARM?\n'}],
            'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128},
            'timeout_ms': 500, 'capture': 'state'}],
        'outputs': {'armed': {'from': 'state', 'kind': 'ascii_number', 'prefix': 'ARM:'}}}
    model['operations']['set_level'] = {'effect': 'write',
        'parameters': {'level': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
        'steps': [{'op': 'exchange', 'tx': [{'text': 'LEVEL '},
            {'arg': 'level', 'format': 'ascii'}, {'text': '\n'}],
            'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}], 'outputs': {}}
    model['provenance'] += [{'item': 'operations.' + name, 'source': 'independent invented device'}
                            for name in ('read_armed', 'set_level')]
    return model


def guarded_package(root, model=None):
    package, _ = emit_fixture(root, model or guarded_model())
    return package


def narrowed_package(root, value):
    """A package of the example model whose temperature may only be `value`. Its probe record is of the identity
    alone, since a probe of a reading outside the value fails."""
    model = example_model()
    model['operations']['measure']['outputs']['temperature']['value'] = value
    package, _ = emit_fixture(root, model, probe_operation='identify', replay_data={
        'schema': 'interface-replay/1', 'transactions': [example_replay()['transactions'][0]]})
    (root / 'reading.json').write_text(json.dumps(example_replay()))
    return package


def invoke_package(package, *arguments):
    return subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
        str(package), *arguments], cwd=package.parent, capture_output=True, text=True, timeout=30)


class DriverContractTests(unittest.TestCase):
    def test_public_execution_bounds_output_expansion_before_io(self):
        model = example_model()
        model['operations']['measure']['steps'][0]['rx']['max_bytes'] = 65536
        model['operations']['measure']['outputs'] = {
            'text_'+str(index): {'from': 'reply', 'kind': 'utf8', 'strip': True} for index in range(300)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root, model, probe_operation='identify', replay_data={
                'schema': 'interface-replay/1', 'transactions': [example_replay()['transactions'][0]]})
            replay = root/'small-reply.json'
            trace = example_replay(); trace['transactions'][1]['rx']['max_bytes'] = 65536
            replay.write_text(json.dumps(trace))
            result = json.loads(invoke_package(package, 'execute', 'measure', '--replay', str(replay)).stdout)
            self.assertFalse(result['ok'], 'potential output/evidence expansion must be bounded before any exchange')
            self.assertEqual(result['error']['code'], 'evidence_bound_exceeded')
            self.assertFalse(result['transport_open_attempted'])
            self.assertEqual(result['transactions'], [])
            # The warnings left out of the bounded result are the model's, so the result points there.
            self.assertEqual((result['warnings'], result['warnings_omitted'], result['warnings_ref']),
                             ([], 301, 'driver/model.json'))

    def test_precondition_observation_preserves_its_scoped_caveats(self):
        # Removing guard metadata from execution must fail even when the guarded write has none.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            caveat = {'code': 'armed_semantics_unverified',
                'message': 'The armed flag interpretation has not been independently grounded.',
                'evidence_refs': ['invented guard fixture']}
            model = guarded_model()
            model['operations']['set_level']['description'] = 'Set the output level.'
            model['operations']['set_level']['parameters']['level']['description'] = 'Level of the output.'
            model['operations']['read_armed']['outputs']['armed']['uncertainties'] = [caveat]
            package = guarded_package(root, model)
            replay = root / 'unarmed.json'
            replay.write_text(json.dumps({'schema': 'interface-replay/1', 'transactions': [
                example_replay()['transactions'][0],
                {'tx_hex': '41524d3f0a', 'rx_hex': '41524d3a300a',
                 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}, 'timeout_ms': 500}]}))
            outcome = json.loads(invoke_package(package, 'execute', 'set_level', '--parameters',
                '{"level":37}', '--allow-effect', 'write', '--replay', str(replay)).stdout)
            expected = {**caveat, 'scope': 'operations.read_armed.outputs.armed'}
            self.assertIn(expected, outcome['observations'][0]['result'].get('warnings', []))
            self.assertIn(expected, outcome['warnings'])
            self.assertEqual(outcome['error']['code'], 'precondition_failed')
            self.assertFalse(outcome['requested_command_dispatched'])

    def test_precondition_expected_value_matches_the_observation_schema(self):
        from interface_runtime.engine import validate_model
        model = controlled_model()
        model['operations']['set_level']['preconditions'] = [{
            'id': 'typed_guard', 'description': 'Require a numeric armed reading.',
            'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
            'operator': 'eq', 'expected': True}]
        checked = validate_model(model)
        self.assertEqual([defect['where'] for defect in checked['defects']],
                         ['operations.set_level.preconditions.0.expected'], checked)
        self.assertRegex(checked['defects'][0]['what'], 'type')

    def test_categorically_incompatible_output_schema_is_rejected_before_io(self):
        from interface_runtime.engine import validate_model
        model = example_model()
        model['operations']['measure']['outputs']['temperature']['value'] = {'type': 'boolean'}
        checked = validate_model(model)
        self.assertEqual([defect['where'] for defect in checked['defects']],
                         ['operations.measure.outputs.temperature.value'], checked)
        self.assertRegex(checked['defects'][0]['what'], 'decoder.*type|type.*decoder')

    def test_public_output_constraints_are_enforced(self):
        # Catches silently ignoring declared output bounds or allowed values.
        for constraint in ({'maximum': 20}, {'minimum': 22}, {'enum': [0, 1]}):
            with self.subTest(constraint=constraint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                package = narrowed_package(root, {'type': 'number', **constraint})
                done = invoke_package(package, 'execute', 'measure', '--replay', str(root / 'reading.json'))
                result = json.loads(done.stdout)
                self.assertFalse(result['ok'], '21.5 violates this public output constraint')
                self.assertEqual(result['error']['code'], 'output_schema_violation')

    def test_unsupported_public_schema_construct_is_rejected_at_acceptance(self):
        # Catches publishing constraints that the runtime silently ignores.
        from generative_driver.toolkit import call_tool
        model = example_model()
        temperature = model['operations']['measure']['outputs']['temperature']
        temperature['value'] = {'type': 'number', 'unknownConstraint': True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'model'; source.mkdir()
            (source / 'model.json').write_text(json.dumps(model))
            (root / 'replay.json').write_text(json.dumps(example_replay()))
            checked = call_tool('model_validate', {'run_dir': str(root / 'run'), 'model_dir': str(source)})
            self.assertFalse(checked['ok'], 'unsupported schemas must fail before serving or dispatch')
            self.assertEqual([defect['where'] for defect in checked['defects']],
                             ['operations.measure.outputs.temperature.value'])
            probed = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                             'operation': 'measure', 'n': 1, 'replay': str(root / 'replay.json')})
            self.assertFalse(probed['ok'], probed)

    def test_unavailable_precondition_preserves_the_observation_fault(self):
        # Catches misrouting an observation/decoder fault as an operator refusal.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = guarded_package(root)
            replay = root / 'unavailable.json'
            replay.write_text(json.dumps({'schema': 'interface-replay/1', 'transactions': [
                example_replay()['transactions'][0],
                {'tx_hex': '41524d3f0a', 'rx_hex': '62726f6b656e0a',
                 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}, 'timeout_ms': 500}]}))
            done = invoke_package(package, 'execute', 'set_level', '--parameters', '{"level":37}',
                                  '--allow-effect', 'write', '--replay', str(replay))
            result = json.loads(done.stdout)
            self.assertEqual(result['error']['code'], 'precondition_unavailable')
            self.assertEqual(result['error']['fault'], 'model')
            self.assertFalse(result['requested_command_dispatched'])
            self.assertEqual(result['observations'][0]['result']['error']['code'], 'execution_error')

    def test_nonobject_arguments_are_refused_before_wire_execution(self):
        # Catches coercing an empty list to an empty parameter object.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(package), 'execute', 'measure', '--parameters', '[]',
                '--replay', str(root / 'replay.json')], cwd=root,
                capture_output=True, text=True, timeout=30)
            result = json.loads(done.stdout)
            self.assertFalse(result['ok'], 'arguments must be an object even when there are no parameters')
            self.assertFalse(result['requested_command_dispatched'])
            self.assertEqual(result['transactions'], [])

    def test_decoded_output_must_conform_to_public_value_schema(self):
        # Catches treating the public output schema as documentation only.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = narrowed_package(root, {'type': 'integer'})
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(package), 'execute', 'measure', '--replay', str(root / 'reading.json')],
                cwd=root, capture_output=True, text=True, timeout=30)
            result = json.loads(done.stdout)
            self.assertFalse(result['ok'], '21.5 cannot satisfy an integer output contract')
            self.assertEqual(result['error']['code'], 'output_schema_violation')
            self.assertTrue(result['requested_command_dispatched'])
            self.assertEqual(result['invalid_outputs'], {'temperature': 21.5})

    def test_unmet_precondition_reports_observation_without_dispatching_write(self):
        # Catches treating a guard as prose or sending the requested write before checking it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = guarded_package(root)
            trace = {'schema': 'interface-replay/1', 'transactions': [
                example_replay()['transactions'][0],
                {'tx_hex': '41524d3f0a', 'rx_hex': '41524d3a300a',
                 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}, 'timeout_ms': 500}]}
            replay = root / 'guard.json'; replay.write_text(json.dumps(trace))
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(package), 'execute', 'set_level', '--parameters', '{"level":37}',
                '--allow-effect', 'write', '--replay', str(replay)],
                cwd=root, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(done.returncode, 0)
            result = json.loads(done.stdout)
            self.assertEqual(result['error']['code'], 'precondition_failed')
            self.assertEqual(result['error']['precondition']['id'], 'armed_required')
            self.assertEqual(result['error']['precondition']['expected'], 1)
            self.assertEqual(result['error']['precondition']['observed'], 0)
            self.assertIn('must be armed', result['error']['message'])
            self.assertFalse(result['requested_command_dispatched'])
            self.assertEqual([item['tx_hex'] for item in result['transactions']], ['49443f0a', '41524d3f0a'])

    def test_close_failure_updates_a_bounded_refusal_in_the_same_session(self):
        from unittest.mock import patch
        from interface_runtime.driver import PackageSession
        from interface_runtime.evidence import ReplayFactory
        model = example_model()
        model['operations']['measure']['steps'][0]['rx']['max_bytes'] = 65536
        model['operations']['measure']['outputs'] = {
            'text_' + str(index): {'from': 'reply', 'kind': 'utf8', 'strip': True} for index in range(300)}
        identity = {'schema': 'interface-replay/1', 'transactions': [example_replay()['transactions'][0]]}
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), model,
                replay_data=identity, probe_operation='identify')
            replay = ReplayFactory(identity)
            def opening(channel, binding):
                handle = replay(channel, binding)
                class CloseFails:
                    def exchange(self, *args):
                        return handle.exchange(*args)
                    def close(self):
                        handle.close()
                        raise OSError('scripted close failure')
                return CloseFails()
            with patch('interface_runtime.transports.open_transport', side_effect=opening):
                with PackageSession(package, binding={}) as current:
                    self.assertTrue(current.execute('identify')['ok'])
                    refused = current.execute('measure')
                    self.assertEqual(refused['error']['code'], 'evidence_bound_exceeded')
                self.assertEqual(refused['error']['code'], 'close_error')
                self.assertEqual(refused['error']['fault'], 'host')
                self.assertEqual(refused['prior_error']['code'], 'evidence_bound_exceeded')
                self.assertEqual(refused['transactions'], [], 'The oversized operation must still send nothing')

    def test_close_failure_preserves_the_completed_transaction_evidence(self):
        from unittest.mock import patch
        from interface_runtime.driver import execute_package
        from interface_runtime.evidence import ReplayFactory
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            replay = ReplayFactory(example_replay())
            def opening(channel, binding):
                handle = replay(channel, binding)
                class CloseFails:
                    def exchange(self, *args):
                        return handle.exchange(*args)
                    def close(self):
                        handle.close()
                        raise OSError('scripted close failed after the reading')
                return CloseFails()
            with patch('interface_runtime.transports.open_transport', side_effect=opening):
                try:
                    result = execute_package(package, 'measure', binding={})
                except OSError as error:
                    self.fail('A close failure must retain a structured completed trace: ' + str(error))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'], 'close_error')
            self.assertEqual(result['error']['fault'], 'host')
            self.assertTrue(result['requested_command_dispatched'])
            self.assertEqual([entry['rx_hex'] for entry in result['transactions']],
                             ['44454d4f2d34320a', '543a32312e350a'])
            self.assertTrue(result['identity_verified'])

    def test_session_and_sequence_enforce_the_public_precondition(self):
        # Every package execution entrypoint must apply the model's preconditions; a raw model session bypasses them.
        from test_device_sessions import demonstration_device
        for entrypoint in ('session', 'sequence'):
            with self.subTest(entrypoint=entrypoint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                model = guarded_model(); model['channel'] = {'type': 'tcp'}
                package, _ = emit_fixture(root, model)
                state = {'armed': 0, 'writes': 0}
                with demonstration_device(state) as binding:
                    if entrypoint == 'sequence':
                        done = invoke_package(package, 'sequence', '--steps',
                            '[{"operation":"set_level","parameters":{"level":37}}]',
                            '--binding', json.dumps(binding), '--allow-effect', 'write')
                        result = json.loads(done.stdout)['results'][0]
                    else:
                        program = ('import sys,json; sys.path.insert(0,sys.argv[1]); import driver; '
                            'session=driver.session(binding=json.loads(sys.argv[2]),allow_effects=("write",)); '
                            '\nwith session as current:\n print(json.dumps(current.execute("set_level",{"level":37})))')
                        done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program,
                            str(package), json.dumps(binding)], capture_output=True, text=True, timeout=20)
                        self.assertEqual(done.returncode, 0, done.stderr)
                        result = json.loads(done.stdout)
                self.assertFalse(result['ok'], 'Public session/sequence must not skip the armed rule')
                self.assertEqual(result['error']['code'], 'precondition_failed')
                self.assertEqual(state['writes'], 0)

    def test_emission_preserves_authored_public_explanations(self):
        # Catches replacing driver-authored clarification with guessed/generated prose.
        description = 'Read the demonstration temperature; the fixture declares Celsius.'
        model = example_model()
        model['operations']['measure']['description'] = description
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), model)
            actual = json.loads((package / 'driver/contract.json').read_text())
            self.assertEqual(actual['operations']['measure']['description'], description)
            self.assertEqual(actual['operations']['measure']['uncertainties'], [])

    def test_malformed_public_uncertainty_is_rejected_before_execution(self):
        # Catches accepting unchecked prose where scoped, renderable caveats are required.
        from generative_driver.toolkit import call_tool
        model = example_model()
        model['operations']['measure']['uncertainties'] = 'verified'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'model'; source.mkdir()
            (source / 'model.json').write_text(json.dumps(model))
            (root / 'replay.json').write_text(json.dumps(example_replay()))
            executed = call_tool('interface_execute', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                                       'operation': 'measure', 'replay': str(root / 'replay.json')})
            self.assertFalse(executed['ok'], 'malformed uncertainty must fail before the model runs')
            self.assertEqual((executed['_exit'], executed['transport_open_attempted']), (1, False), executed)
            self.assertIn('operations.measure.uncertainties', executed['error'])

    def test_describe_preserves_operation_and_parameter_uncertainty(self):
        # Catches silently presenting a bounded raw argument as a known physical quantity.
        model = example_model()
        model['operations']['set_level'] = {'effect': 'write',
            'parameters': {'level': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
            'steps': [{'op': 'exchange', 'tx': [{'text': 'LEVEL '},
                {'arg': 'level', 'format': 'ascii'}, {'text': '\n'}],
                'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}], 'outputs': {}}
        model['provenance'].append({'item': 'operations.set_level', 'source': 'invented bounded wire command'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root, model)
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(package), 'describe'], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            public = json.loads(done.stdout).get('contract')
            self.assertIsInstance(public, dict, 'describe must expose the public contract')
            operation = public['operations']['set_level']
            self.assertTrue(operation['uncertainties'])
            self.assertTrue(operation['parameters']['level']['uncertainties'])
            self.assertIsNone(operation['parameters']['level']['unit'])
            self.assertEqual(operation['parameters']['level']['schema'],
                             {'type': 'integer', 'minimum': 0, 'maximum': 100})

    def test_emitted_contract_drives_relocated_package(self):
        # Catches emitting only the combined historical model or losing public units.
        with tempfile.TemporaryDirectory(prefix='driver contract ') as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            moved = root / 'relocated driver'
            shutil.move(package, moved)
            self.assertTrue((moved / 'driver/contract.json').is_file(),
                            'emit must freeze a separate public driver contract')
            contract = json.loads((moved / 'driver/contract.json').read_text())
            protocol = json.loads((moved / 'driver/protocol.json').read_text())
            self.assertEqual(contract['operations']['measure']['outputs']['temperature']['unit'], 'degC')
            self.assertNotIn('steps', contract['operations']['measure'])
            self.assertNotIn('parameters', protocol['operations']['measure'])
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(moved), 'replay'], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertEqual(json.loads(done.stdout)['results'][0]['outputs'], {'temperature': 21.5})


def spec_model():
    """The example model with the operation the Spec of the one driver format shows: a described read with a described,
    narrowed output and a parameter that says nothing of its meaning."""
    model = example_model()
    model['operations']['measure'] = {
        'effect': 'read',
        'description': 'Read the temperature of the probe.',
        'parameters': {'channel': {'type': 'integer', 'minimum': 0, 'maximum': 3}},
        'steps': [{'op': 'exchange', 'tx': [{'text': 'T '}, {'arg': 'channel', 'format': 'ascii'}, {'text': '\n'}],
                   'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}, 'timeout_ms': 500,
                   'capture': 'reply'}],
        'outputs': {'temperature': {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T:', 'unit': 'Cel',
                                    'description': 'Temperature of the probe.',
                                    'value': {'type': 'number', 'minimum': -40, 'maximum': 125}}}}
    return model


def unknown(subject):
    return {'code': 'semantics_unknown', 'message': subject + ' meaning and operating assumptions are not documented.',
            'evidence_refs': []}


class ContractViewTests(unittest.TestCase):
    """The driver contract and the declarative protocol are views the runtime computes from the interface model
    (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3)."""

    def test_02_B4_the_contract_view_of_an_operation_is_the_one_the_spec_shows(self):
        from interface_runtime.contracts import contract_view
        from interface_runtime.engine import validate_model
        model = spec_model()
        self.assertTrue(validate_model(model)['ok'])
        before = json.loads(json.dumps(model))
        view = contract_view(model)
        self.assertEqual(model, before, 'the view changes nothing in the model')
        self.assertEqual(view['operations']['measure'], {
            'effect': 'read',
            'description': 'Read the temperature of the probe.',
            'uncertainties': [],
            'parameters': {'channel': {
                'schema': {'type': 'integer', 'minimum': 0, 'maximum': 3},
                'description': 'Meaning not documented.',
                'unit': None,
                'uncertainties': [unknown('Parameter channel')]}},
            'outputs': {'temperature': {
                'schema': {'type': 'number', 'minimum': -40, 'maximum': 125},
                'description': 'Temperature of the probe.',
                'unit': 'Cel',
                'uncertainties': []}}})
        # The shape driver-contract/1 has: the public part of the model and nothing of its wire.
        self.assertEqual({key: value for key, value in view.items() if key != 'operations'}, {
            'schema': 'driver-contract/1', 'device': model['device'], 'identity': model['identity'],
            'provenance': model['provenance']})
        self.assertEqual(list(view['operations']), ['identify', 'measure'])
        # A view is a copy: changing it leaves the model as it was.
        view['operations']['measure']['outputs']['temperature']['schema']['maximum'] = 0
        view['provenance'].clear()
        self.assertEqual(model, before)

    def test_02_B4_a_missing_description_is_marked_once_after_what_the_worker_wrote(self):
        from interface_runtime.contracts import contract_view
        written = {'code': 'range_unverified', 'message': 'The upper range was not exercised.',
                   'evidence_refs': ['probe 3']}
        already = {'code': 'semantics_unknown', 'message': 'No document names this command.', 'evidence_refs': []}
        model = spec_model()
        measure = model['operations']['measure']
        del measure['description'], measure['outputs']['temperature']['description']
        measure['uncertainties'] = [written]
        measure['outputs']['temperature']['uncertainties'] = [already, written]
        view = contract_view(model)['operations']
        self.assertEqual((view['measure']['description'], view['measure']['uncertainties']),
                         ('Meaning not documented.', [written, unknown('Operation measure')]))
        output = view['measure']['outputs']['temperature']
        self.assertEqual((output['description'], output['uncertainties']),
                         ('Meaning not documented.', [already, written]))
        # An operation that says nothing at all is marked, and so is each of its outputs.
        self.assertEqual((view['identify']['description'], view['identify']['uncertainties']),
                         ('Meaning not documented.', [unknown('Operation identify')]))
        model['operations']['identify']['outputs'] = {'name': {'from': 'id', 'kind': 'utf8'}}
        self.assertEqual(contract_view(model)['operations']['identify']['outputs']['name'], {
            'schema': {'type': 'string'}, 'description': 'Meaning not documented.', 'unit': None,
            'uncertainties': [unknown('Output name')]})
        # A description alone settles the meaning: nothing is added to what the worker wrote.
        measure['description'] = 'Read the temperature of the probe.'
        self.assertEqual(contract_view(model)['operations']['measure']['uncertainties'], [written])

    def test_02_B4_a_missing_unit_is_null_and_a_missing_value_follows_the_decoder(self):
        from interface_runtime.contracts import contract_view
        model = controlled_model()
        binary = {'op': 'exchange', 'tx': [{'hex': '01'}], 'rx': {'mode': 'exact', 'length': 8}, 'timeout_ms': 500,
                  'capture': 'block'}
        decoders = {
            'whole': ({'kind': 'integer', 'offset': 0, 'size': 2, 'byteorder': 'little', 'signed': False}, 'number'),
            'real': ({'kind': 'float32', 'offset': 4, 'byteorder': 'little'}, 'number'),
            'reading': ({'kind': 'ascii_number'}, 'number'),
            'keyed': ({'kind': 'kv_number', 'key': 'V:'}, 'number'),
            'raw': ({'kind': 'hex'}, 'string'), 'text': ({'kind': 'utf8'}, 'string'),
            'word': ({'kind': 'kv_text', 'key': 'V:'}, 'string'),
            'line': ({'kind': 'line_text', 'line_prefix': 'V:'}, 'string'),
            'name': ({'kind': 'cstring', 'offset': 0}, 'string'),
            'label': ({'kind': 'lpstring', 'length_offset': 0, 'length_size': 1, 'byteorder': 'little', 'offset': 1,
                       'includes_terminator': False}, 'string')}
        model['operations']['read_block'] = {'effect': 'read', 'parameters': {}, 'steps': [binary],
                                             'outputs': {name: {'from': 'block', **decoder}
                                                         for name, (decoder, _) in decoders.items()}}
        model['operations']['send_block'] = {
            'effect': 'write', 'parameters': {'payload': {'type': 'bytes', 'min_length': 1, 'max_length': 4},
                                              'mode': {'type': 'string', 'enum': ['A', 'B'], 'unit': '1'}},
            'steps': [{'op': 'exchange',
                       'tx': [{'arg': 'mode', 'format': 'ascii'}, {'arg': 'payload', 'format': 'raw'}],
                       'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}], 'outputs': {}}
        model['provenance'] += [{'item': 'operations.' + name, 'source': 'independent invented device'}
                                for name in ('read_block', 'send_block')]
        view = contract_view(model)['operations']
        self.assertEqual({name: (output['schema'], output['unit'])
                          for name, output in view['read_block']['outputs'].items()},
                         {name: ({'type': kind}, None) for name, (_, kind) in decoders.items()})
        # A parameter's schema is the model's parameter rule, a bytes parameter as a hex string of twice its length.
        self.assertEqual({name: (parameter['schema'], parameter['unit'])
                          for name, parameter in view['send_block']['parameters'].items()}, {
            'payload': ({'type': 'string', 'contentEncoding': 'hex', 'minLength': 2, 'maxLength': 8}, None),
            'mode': ({'type': 'string', 'enum': ['A', 'B']}, '1')})
        self.assertEqual(view['set_level']['parameters']['level']['schema'],
                         {'type': 'integer', 'minimum': 0, 'maximum': 100})
        self.assertEqual(view['measure']['outputs']['temperature']['unit'], 'degC')

    def test_02_B4_preconditions_appear_as_written_and_only_where_the_model_has_them(self):
        from interface_runtime.contracts import contract_view
        model = controlled_model()
        guard = {'id': 'armed_required', 'description': 'Device must be armed before changing level.',
                 'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
                 'operator': 'eq', 'expected': 1}
        model['operations']['set_level']['preconditions'] = [guard]
        view = contract_view(model)['operations']
        self.assertEqual(view['set_level']['preconditions'], [guard])
        self.assertEqual([name for name, operation in view.items() if 'preconditions' in operation], ['set_level'])
        self.assertEqual(list(view['set_level']), ['effect', 'description', 'uncertainties', 'parameters', 'outputs',
                                                   'preconditions'])
        self.assertEqual(list(view['measure']), ['effect', 'description', 'uncertainties', 'parameters', 'outputs'])

    def test_02_B4_the_protocol_view_is_the_wire_part_of_the_model(self):
        from interface_runtime.contracts import protocol_view
        model = spec_model()
        model['operations']['measure']['uncertainties'] = [unknown('Operation measure')]
        model['operations']['measure']['outputs']['temperature']['uncertainties'] = []
        before = json.loads(json.dumps(model))
        view = protocol_view(model)
        self.assertEqual(model, before)
        self.assertEqual(view, {
            'schema': 'driver-protocol/1', 'runtime_schema': 'interface-model/7', 'channel': model['channel'],
            'operations': {
                'identify': {'steps': model['operations']['identify']['steps'], 'outputs': {}},
                'measure': {'steps': model['operations']['measure']['steps'],
                            'outputs': {'temperature': {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T:'}}}}})


GUARD = {'id': 'armed_required', 'description': 'Device must be armed before changing level.',
         'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
         'operator': 'eq', 'expected': 1}
RANGE = {'code': 'range_unverified', 'message': 'Readings above 25 were never observed.',
         'evidence_refs': ['invented probe record']}
STEP = {'code': 'step_unknown', 'message': 'The device may round the level to a step it does not state.',
        'evidence_refs': ['invented level fixture']}
ARMED = {'code': 'armed_semantics_unverified',
         'message': 'The armed flag interpretation has not been independently grounded.',
         'evidence_refs': ['invented guard fixture']}


def guarded_model():
    """The controlled model, whose `set_level` states that the device must report armed first."""
    model = controlled_model()
    model['operations']['set_level']['preconditions'] = [json.loads(json.dumps(GUARD))]
    return model


def described_model():
    """The guarded model with the meanings a worker wrote: descriptions, units, uncertainties and a narrowed value.
    `identify` and the output `armed` say nothing of their meaning."""
    model = guarded_model()
    operations = model['operations']
    operations['measure'].update(description='Read the temperature of the probe.', uncertainties=[dict(RANGE)])
    operations['measure']['outputs']['temperature'].update(
        unit='Cel', description='Temperature of the probe.', value={'type': 'number', 'minimum': -40, 'maximum': 25})
    operations['read_armed']['description'] = 'Report whether the device is armed.'
    operations['read_armed']['outputs']['armed']['uncertainties'] = [dict(ARMED)]
    operations['set_level']['description'] = 'Set the output level.'
    operations['set_level']['parameters']['level'].update(description='Level of the output.', unit='%',
                                                         uncertainties=[dict(STEP)])
    return model


def exchange(command, reply):
    return {'tx_hex': command.hex(), 'rx_hex': reply.hex(),
            'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}, 'timeout_ms': 500}


IDENTITY = exchange(b'ID?\n', b'DEMO-42\n')


def replay_of(*exchanges):
    return {'schema': 'interface-replay/1', 'transactions': [IDENTITY, *exchanges]}


def warned(caveat, scope):
    return {**caveat, 'scope': scope}


class PublicRuleTests(unittest.TestCase):
    """The public rules of a driver come from its interface model: what an output may be wherever the model runs, and
    preconditions and warnings in a package (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3)."""

    def test_02_B5_a_reading_outside_its_value_fails_wherever_a_model_directory_runs(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'model'; source.mkdir()
            (source / 'model.json').write_text(json.dumps(described_model()))
            (root / 'hot.json').write_text(json.dumps(replay_of(exchange(b'T\n', b'T:30.5\n'))))
            arguments = {'run_dir': str(root / 'run'), 'model_dir': str(source), 'operation': 'measure',
                         'replay': str(root / 'hot.json')}
            executed = call_tool('interface_execute', arguments)
            probed = call_tool('probe_run', {**arguments, 'n': 1})
            for name, result, sample in (('interface_execute', executed, executed),
                                         ('probe_run', probed, probed['results'][0])):
                with self.subTest(tool=name):
                    self.assertEqual((result['ok'], result['_exit']), (False, 1), result)
                    self.assertEqual((sample['error']['code'], sample['error']['fault']),
                                     ('output_schema_violation', 'model'), sample)
                    self.assertEqual(sample['invalid_outputs'], {'temperature': 30.5})
                    self.assertEqual(sample['outputs'], {})
            # At probe the reading fails the sample, and the difference is a defect of the model.
            self.assertEqual((probed['error_code'], probed['fault']), ('output_schema_violation', 'model'))
            differences = call_tool('probe_diff', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                                   'probe': probed['probe']})
            self.assertEqual(differences['_exit'], 1, differences)
            self.assertEqual([(defect['code'], defect['fault'], defect['where']) for defect in differences['defects']],
                             [('output_schema_violation', 'model', 'operations.measure.steps.0')])
            # A reading inside the value passes both.
            (root / 'mild.json').write_text(json.dumps(replay_of(exchange(b'T\n', b'T:21.5\n'))))
            mild = call_tool('interface_execute', {**arguments, 'replay': str(root / 'mild.json')})
            self.assertEqual((mild['ok'], mild['outputs']), (True, {'temperature': 21.5}), mild)

    def test_02_B5_a_package_checks_the_value_and_observes_each_precondition_before_the_command(self):
        from interface_runtime.driver import PackageSession
        armed = lambda value: exchange(b'ARM?\n', b'ARM:' + value + b'\n')
        level = {'tx_hex': b'LEVEL 37\n'.hex(), 'rx_hex': '', 'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), described_model())

            def call(operation, parameters, *exchanges):
                with PackageSession(package, allow_effects=('write',), replay=replay_of(*exchanges)) as session:
                    return session.execute(operation, parameters)
            hot = call('measure', None, exchange(b'T\n', b'T:30.5\n'))
            self.assertEqual((hot['ok'], hot['error']['code'], hot['error']['fault']),
                             (False, 'output_schema_violation', 'model'), hot)
            self.assertEqual((hot['invalid_outputs'], hot['outputs']), ({'temperature': 30.5}, {}))
            # The observed value differs: the requested command is not sent.
            unarmed = call('set_level', {'level': 37}, armed(b'0'))
            self.assertEqual(unarmed['error']['code'], 'precondition_failed', unarmed)
            self.assertEqual(unarmed['error']['precondition'], {**{key: GUARD[key] for key in
                             ('id', 'description', 'operator', 'expected')}, 'observed': 0})
            self.assertFalse(unarmed['requested_command_dispatched'])
            self.assertEqual([entry['tx_hex'] for entry in unarmed['transactions']], [b'ID?\n'.hex(), b'ARM?\n'.hex()])
            # The observation fails: the precondition could not be checked, and nothing more is sent.
            broken = call('set_level', {'level': 37}, armed(b'broken'))
            self.assertEqual((broken['error']['code'], broken['error']['fault']), ('precondition_unavailable', 'model'))
            self.assertFalse(broken['requested_command_dispatched'])
            # The observed value is the expected one: the command follows the fresh observation.
            sent = call('set_level', {'level': 37}, armed(b'1'), IDENTITY, level)
            self.assertTrue(sent['ok'], sent)
            self.assertEqual([entry['tx_hex'] for entry in sent['transactions']],
                             [b'ID?\n'.hex(), b'ARM?\n'.hex(), b'ID?\n'.hex(), b'LEVEL 37\n'.hex()])
            self.assertEqual(sent['observations'][0]['precondition_id'], 'armed_required')

    def test_02_B5_a_package_result_warns_of_every_uncertainty_with_its_scope(self):
        from interface_runtime.driver import PackageSession
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), described_model())
            with PackageSession(package, replay=replay_of(exchange(b'T\n', b'T:21.5\n'))) as session:
                measured = session.execute('measure')
            self.assertEqual(measured['warnings'], [warned(RANGE, 'operations.measure')])
            with PackageSession(package, replay=replay_of(exchange(b'ARM?\n', b'ARM:1\n'))) as session:
                read = session.execute('read_armed')
            self.assertEqual(read['warnings'], [
                warned(ARMED, 'operations.read_armed.outputs.armed'),
                warned(unknown('Output armed'), 'operations.read_armed.outputs.armed')])
            with PackageSession(package, replay=replay_of()) as session:
                identified = session.execute('identify')
            self.assertEqual(identified['warnings'], [warned(unknown('Operation identify'), 'operations.identify')])
            # A guarded operation also carries what its observation is uncertain of.
            with PackageSession(package, allow_effects=('write',),
                                replay=replay_of(exchange(b'ARM?\n', b'ARM:0\n'))) as session:
                refused = session.execute('set_level', {'level': 37})
            self.assertEqual(refused['warnings'], [
                warned(STEP, 'operations.set_level.parameters.level'),
                warned(ARMED, 'operations.read_armed.outputs.armed'),
                warned(unknown('Output armed'), 'operations.read_armed.outputs.armed')])

    def test_02_B5_the_replay_of_emit_check_applies_the_value_and_no_precondition(self):
        from generative_driver.toolkit import call_tool
        level = {'tx_hex': b'LEVEL 37\n'.hex(), 'rx_hex': '', 'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}
        writes = {'parameters': {'level': 37}, 'allow_effects': ['write']}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()

            def package_of(name, model, operation, replay, **more):
                source = root / name / 'model'; source.mkdir(parents=True)
                (source / 'model.json').write_text(json.dumps(model))
                (root / name / 'replay.json').write_text(json.dumps(replay))
                run = {'run_dir': str(root / name / 'run'), 'model_dir': str(source)}
                probe = call_tool('probe_run', {**run, 'n': 1, 'operation': operation,
                                                'replay': str(root / name / 'replay.json'), **more})
                self.assertTrue(probe['ok'], probe)
                emitted = call_tool('emit_package', {**run, 'probe': probe['probe']})
                self.assertTrue(emitted.get('ok'), emitted)
                return Path(emitted['package_dir'])

            def check(package):
                return call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})
            # The probe ran `set_level` on a device that was never asked whether it is armed, so its record holds no
            # observation. The check replays that wire, and it passes.
            written = package_of('written', described_model(), 'set_level', replay_of(level), **writes)
            checked = check(written)
            self.assertEqual((checked['ok'], checked['replay_status']), (True, 'passed'), checked)
            # A record of a reading of 21.5, taken with the same model bytes, replays in that package's place.
            measured = package_of('measured', described_model(), 'measure', replay_of(exchange(b'T\n', b'T:21.5\n')))
            record = json.loads((measured / 'probe.json').read_text())
            rewrite_document(written, 'probe.json', record)
            self.assertTrue(check(written)['ok'])
            # Under a model whose value excludes that reading it no longer replays: the check applies the value.
            narrow = described_model()
            narrow['operations']['measure']['outputs']['temperature']['value']['maximum'] = 20
            narrowed = package_of('narrowed', narrow, 'set_level', replay_of(level), **writes)
            record['model_sha256'] = json.loads((narrowed / 'manifest.json').read_text())['model_sha256']
            rewrite_document(narrowed, 'probe.json', record)
            refused = check(narrowed)
            self.assertEqual((refused['ok'], refused['_exit']), (False, 1), refused)
            self.assertIn('replayed outputs/transcripts differ', said(refused))


def said(result):
    defects = result.get('defects') if isinstance(result.get('defects'), list) else []
    return ' '.join(str(part) for part in (result.get('error'), result.get('reason'),
                                           *(defect.get('what') for defect in defects)) if part)


def files_of(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob('*'))
            if path.is_file()}


class EmitTests(unittest.TestCase):
    """Emit shows what the worker wrote in the model: the package's contract and protocol are copies of the views, and
    its tools are rendered from the contract view."""

    def test_02_B6_emit_writes_the_model_byte_for_byte_and_the_two_views(self):
        from generative_driver.toolkit import call_tool
        from interface_runtime.contracts import contract_view, protocol_view
        model = described_model()
        written = (json.dumps(model, indent=3, sort_keys=True) + '\n\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'model'; source.mkdir()
            (source / 'model.json').write_bytes(written)
            (root / 'replay.json').write_text(json.dumps(replay_of(exchange(b'T\n', b'T:21.5\n'))))
            probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                            'operation': 'measure', 'n': 1, 'replay': str(root / 'replay.json')})
            emitted = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                                 'probe': probe['probe']})
            self.assertTrue(emitted.get('ok'), emitted)
            package = Path(emitted['package_dir'])
            self.assertEqual((package / 'driver/model.json').read_bytes(), written)
            self.assertEqual(json.loads((package / 'driver/contract.json').read_text()), contract_view(model))
            self.assertEqual(json.loads((package / 'driver/protocol.json').read_text()), protocol_view(model))
            contract = json.loads((package / 'driver/contract.json').read_text())
            self.assertEqual(contract['operations']['measure']['description'], 'Read the temperature of the probe.')
            self.assertEqual(contract['operations']['set_level']['preconditions'], [GUARD])
            checked = call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})
            self.assertEqual((checked['ok'], checked['replay_status']), (True, 'passed'), checked)

    def test_02_B6_each_tool_shows_the_description_units_uncertainties_and_preconditions(self):
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), described_model())
            catalog = json.loads((package / 'mcp-tools.json').read_text())
            tools = {tool['name']: tool for tool in catalog['tools']}
            self.assertEqual(sorted(tools), ['device_identify', 'device_measure', 'device_read_armed',
                                             'device_set_level'])
            measure = tools['device_measure']
            self.assertEqual(measure['description'].split('\n'),
                             ['Read the temperature of the probe.', 'Warning: ' + RANGE['message']])
            self.assertEqual(measure['outputSchema']['properties']['outputs']['properties']['temperature'], {
                'type': 'number', 'minimum': -40, 'maximum': 25,
                'description': 'Temperature of the probe.\nUnit: Cel.'})
            set_level = tools['device_set_level']
            self.assertEqual(set_level['description'].split('\n'),
                             ['Set the output level.', 'Precondition armed_required: ' + GUARD['description']])
            self.assertEqual(set_level['inputSchema']['properties']['level'], {
                'type': 'integer', 'minimum': 0, 'maximum': 100,
                'description': 'Level of the output.\nUnit: %.\nWarning: ' + STEP['message']})
            armed = tools['device_read_armed']['outputSchema']['properties']['outputs']['properties']['armed']
            self.assertIn('Warning: ' + ARMED['message'], armed['description'].split('\n'))

    def test_02_B6_a_contract_or_a_protocol_beside_the_model_is_not_read(self):
        from generative_driver.toolkit import call_tool
        from interface_runtime.contracts import contract_view, protocol_view
        model = described_model()
        other = contract_view(model)
        other['operations']['measure']['description'] = 'Another text that the model does not hold.'
        beside = {'neither': {}, 'both': {'contract.json': json.dumps(other),
                                           'protocol.json': json.dumps(protocol_view(example_model()))},
                  'a lone contract': {'contract.json': json.dumps(other)},
                  'a lone protocol': {'protocol.json': '{"schema": "driver-protocol/9"}'},
                  'text that is no JSON': {'contract.json': 'not JSON', 'protocol.json': '{'}}
        packages = {}
        with tempfile.TemporaryDirectory() as directory:
            for case, extra in beside.items():
                root = Path(directory).resolve() / case.replace(' ', '-'); root.mkdir()
                source = root / 'model'; source.mkdir()
                (source / 'model.json').write_text(json.dumps(model))
                for name, text in extra.items():
                    (source / name).write_text(text)
                (root / 'replay.json').write_text(json.dumps(replay_of(exchange(b'T\n', b'T:21.5\n'))))
                probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                                'operation': 'measure', 'n': 1, 'replay': str(root / 'replay.json')})
                emitted = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                                     'probe': probe['probe']})
                self.assertTrue(emitted.get('ok'), (case, emitted))
                packages[case] = files_of(Path(emitted['package_dir']))
        for case in beside:
            with self.subTest(beside_the_model=case):
                self.assertEqual(packages[case], packages['neither'])

    def test_02_B6_the_package_check_refuses_a_view_file_that_differs_and_names_it(self):
        from generative_driver.toolkit import call_tool

        def described(contract):
            contract['operations']['measure']['description'] = 'Another text that the model does not hold.'

        def retagged(document):
            document['schema'] = 'driver-format/9'
        cases = (('driver/contract.json', described), ('driver/contract.json', retagged),
                 ('driver/protocol.json',
                  lambda protocol: protocol['operations']['measure']['steps'][0].update(timeout_ms=7)),
                 ('driver/protocol.json', retagged))
        for index, (relative, change) in enumerate(cases):
            with self.subTest(file=relative, case=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                package, _ = emit_fixture(root, described_model())
                document = json.loads((package / relative).read_text())
                change(document)
                rewrite_document(package, relative, document)
                refused = call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})
                self.assertEqual((refused['ok'], refused['_exit']), (False, 1), refused)
                self.assertIn(relative, said(refused), refused)
                self.assertIn('differs', said(refused), refused)
                # No tag of the file is checked: it is refused as a copy that differs, whatever it names.
                self.assertNotIn('format', said(refused), refused)


class UndocumentedTests(unittest.TestCase):
    """A driver says where its model gives no meaning, wherever the driver is shown."""

    def test_02_B7_each_tool_and_each_result_marks_a_model_without_descriptions(self):
        from interface_runtime.driver import PackageSession
        model = controlled_model()
        self.assertNotIn('description', json.dumps(model))

        def marked(subject, *unit):
            return '\n'.join(('Meaning not documented.', *unit, 'Warning: ' + unknown(subject)['message']))
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), model)
            tools = {tool['name']: tool for tool in json.loads((package / 'mcp-tools.json').read_text())['tools']}
            shown = {name: (tool['description'],
                            {key: value['description'] for key, value in tool['inputSchema']['properties'].items()
                             if key != '_request_id'},
                            {key: value['description']
                             for key, value in tool['outputSchema']['properties']['outputs']['properties'].items()})
                     for name, tool in tools.items()}
            self.assertEqual(shown, {
                'device_identify': (marked('Operation identify'), {}, {}),
                'device_measure': (marked('Operation measure'), {},
                                   {'temperature': marked('Output temperature', 'Unit: degC.')}),
                'device_read_armed': (marked('Operation read_armed'), {},
                                      {'armed': marked('Output armed', 'Unit: unknown.')}),
                'device_set_level': (marked('Operation set_level'),
                                     {'level': marked('Parameter level', 'Unit: unknown.')}, {})})
            with PackageSession(package, replay=replay_of(exchange(b'T\n', b'T:21.5\n'))) as session:
                measured = session.execute('measure')
            self.assertEqual(measured['warnings'], [
                warned(unknown('Operation measure'), 'operations.measure'),
                warned(unknown('Output temperature'), 'operations.measure.outputs.temperature')])
            level = {'tx_hex': b'LEVEL 37\n'.hex(), 'rx_hex': '', 'rx': {'mode': 'exact', 'length': 0},
                     'timeout_ms': 500}
            with PackageSession(package, allow_effects=('write',), replay=replay_of(level)) as session:
                sent = session.execute('set_level', {'level': 37})
            self.assertEqual(sent['warnings'], [
                warned(unknown('Operation set_level'), 'operations.set_level'),
                warned(unknown('Parameter level'), 'operations.set_level.parameters.level')])

    def test_02_B7_a_number_without_a_unit_reads_unknown_or_not_stated_by_the_device(self):
        stated = {'code': 'unit_not_stated', 'message': 'The device states no unit for the armed flag.',
                  'evidence_refs': ['invented guard fixture']}
        model = controlled_model()
        model['operations']['read_armed']['outputs']['armed']['uncertainties'] = [stated]
        model['operations']['read_level'] = json.loads(json.dumps(model['operations']['read_armed']))
        del model['operations']['read_level']['outputs']['armed']['uncertainties']
        model['provenance'].append({'item': 'operations.read_level', 'source': 'independent invented device'})
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), model)
            tools = {tool['name']: tool for tool in json.loads((package / 'mcp-tools.json').read_text())['tools']}
            unit = lambda name: tools[name]['outputSchema']['properties']['outputs']['properties']['armed'][
                'description'].split('\n')[1]
            self.assertEqual(unit('device_read_armed'), 'Unit: not stated by the device.')
            self.assertEqual(unit('device_read_level'), 'Unit: unknown.')
            self.assertEqual(
                tools['device_set_level']['inputSchema']['properties']['level']['description'].split('\n')[1],
                'Unit: unknown.')


if __name__ == '__main__':
    unittest.main()

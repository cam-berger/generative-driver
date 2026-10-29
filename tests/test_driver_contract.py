"""Public package behavior against independent invented wire evidence."""
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
    """Rebuild unsigned fixture checksums to exercise semantic checks, not hashes."""
    (package / relative).write_text(json.dumps(document))
    manifest_path = package / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if relative == 'driver/contract.json' and (package / 'mcp-tools.json').is_file():
        # Execution fixtures need a consistent derived catalog. Catalog tampering
        # tests edit that artifact itself and do not take this setup branch.
        from interface_runtime.catalog import render_catalog
        from interface_runtime.contracts import compile_contract
        try:
            compile_contract(document, json.loads((package / 'driver/protocol.json').read_text()))
        except (ValueError, KeyError, TypeError):
            pass
        else:
            contract_sha = hashlib.sha256((package / relative).read_bytes()).hexdigest()
            (package / 'mcp-tools.json').write_text(json.dumps(render_catalog(document, contract_sha)))
            manifest['mcp_catalog_sha256'] = hashlib.sha256((package / 'mcp-tools.json').read_bytes()).hexdigest()
            manifest['files']['mcp-tools.json'] = manifest['mcp_catalog_sha256']
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


def guarded_package(root):
    package, _ = emit_fixture(root, controlled_model())
    contract = json.loads((package / 'driver/contract.json').read_text())
    contract['operations']['set_level']['preconditions'] = [{
        'id': 'armed_required', 'description': 'Device must be armed before changing level.',
        'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
        'operator': 'eq', 'expected': 1}]
    rewrite_document(package, 'driver/contract.json', contract)
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

    def test_precondition_observation_preserves_its_scoped_caveats(self):
        # Removing guard metadata from execution must fail even when the guarded write has none.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = guarded_package(root)
            contract = json.loads((package / 'driver/contract.json').read_text())
            contract['operations']['set_level']['uncertainties'] = []
            contract['operations']['set_level']['parameters']['level']['uncertainties'] = []
            caveat = {'code': 'armed_semantics_unverified',
                'message': 'The armed flag interpretation has not been independently grounded.',
                'evidence_refs': ['invented guard fixture']}
            contract['operations']['read_armed']['outputs']['armed']['uncertainties'] = [caveat]
            rewrite_document(package, 'driver/contract.json', contract)
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
        from interface_runtime.contracts import from_model, compile_contract
        contract, protocol = from_model(controlled_model())
        contract['operations']['set_level']['preconditions'] = [{
            'id': 'typed_guard', 'description': 'Require a numeric armed reading.',
            'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
            'operator': 'eq', 'expected': True}]
        with self.assertRaisesRegex(ValueError, 'precondition.*type'):
            compile_contract(contract, protocol)

    def test_categorically_incompatible_output_schema_is_rejected_before_io(self):
        from interface_runtime.contracts import from_model, compile_contract
        contract, protocol = from_model(example_model())
        contract['operations']['measure']['outputs']['temperature']['schema'] = {'type': 'boolean'}
        with self.assertRaisesRegex(ValueError, 'decoder.*type|type.*decoder'):
            compile_contract(contract, protocol)

    def test_protocol_cannot_override_an_unknown_public_unit(self):
        from interface_runtime.contracts import from_model, compile_contract
        contract, protocol = from_model(example_model())
        contract['operations']['measure']['outputs']['temperature']['unit'] = None
        protocol['operations']['measure']['outputs']['temperature']['unit'] = 'degC'
        with self.assertRaisesRegex(ValueError, 'unit'):
            compile_contract(contract, protocol)

    def test_public_output_constraints_are_enforced(self):
        # Catches silently ignoring declared output bounds or allowed values.
        for constraint in ({'maximum': 20}, {'minimum': 22}, {'enum': [0, 1]}):
            with self.subTest(constraint=constraint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                package, _ = emit_fixture(root)
                contract = json.loads((package / 'driver/contract.json').read_text())
                contract['operations']['measure']['outputs']['temperature']['schema'].update(constraint)
                rewrite_document(package, 'driver/contract.json', contract)
                done = invoke_package(package, 'execute', 'measure', '--replay', str(root / 'replay.json'))
                result = json.loads(done.stdout)
                self.assertFalse(result['ok'], '21.5 violates this public output constraint')
                self.assertEqual(result['error']['code'], 'output_schema_violation')

    def test_unsupported_public_schema_construct_is_rejected_at_acceptance(self):
        # Catches publishing constraints that the runtime silently ignores.
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            contract = json.loads((package / 'driver/contract.json').read_text())
            contract['operations']['measure']['outputs']['temperature']['schema']['unknownConstraint'] = True
            rewrite_document(package, 'driver/contract.json', contract)
            checked = call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})
            self.assertFalse(checked['ok'], 'unsupported schemas must fail before serving or dispatch')

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
            package, _ = emit_fixture(root)
            contract = json.loads((package / 'driver/contract.json').read_text())
            contract['operations']['measure']['outputs']['temperature']['schema'] = {'type': 'integer'}
            rewrite_document(package, 'driver/contract.json', contract)
            done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
                'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("driver",run_name="__main__")',
                str(package), 'execute', 'measure', '--replay', str(root / 'replay.json')],
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
            package, _ = emit_fixture(root, controlled_model())
            contract = json.loads((package / 'driver/contract.json').read_text())
            contract['operations']['set_level']['preconditions'] = [{
                'id': 'armed_required', 'description': 'Device must be armed before changing level.',
                'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
                'operator': 'eq', 'expected': 1}]
            rewrite_document(package, 'driver/contract.json', contract)
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
        # Every package execution entrypoint must use the contract; a raw model session bypasses guards.
        from test_device_sessions import demonstration_device
        for entrypoint in ('session', 'sequence'):
            with self.subTest(entrypoint=entrypoint), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                model = controlled_model(); model['channel'] = {'type': 'tcp'}
                package, _ = emit_fixture(root, model)
                contract = json.loads((package / 'driver/contract.json').read_text())
                contract['operations']['set_level']['preconditions'] = [{
                    'id': 'armed_required', 'description': 'Device must be armed before changing level.',
                    'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
                    'operator': 'eq', 'expected': 1}]
                rewrite_document(package, 'driver/contract.json', contract)
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

    def test_emission_rejects_an_incomplete_authored_contract_pair(self):
        # A lone authored document must not silently borrow a generated counterpart.
        from generative_driver.toolkit import call_tool
        for name in ('contract.json', 'protocol.json'):
            with self.subTest(document=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                package, _ = emit_fixture(root)
                (root / 'model' / name).write_bytes((package / 'driver' / name).read_bytes())
                emitted = call_tool('emit_package', {'run_dir': str(root / 'second'),
                    'model_dir': str(root / 'model'), 'probe': str(package / 'probe.json')})
                self.assertFalse(emitted.get('ok'), 'Authored contract and protocol must be supplied together')

    def test_emission_preserves_authored_public_explanations(self):
        # Catches replacing driver-authored clarification with guessed/generated prose.
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            contract = json.loads((package / 'driver/contract.json').read_text())
            description = 'Read the demonstration temperature; the fixture declares Celsius.'
            contract['operations']['measure']['description'] = description
            contract['operations']['measure']['uncertainties'] = []
            (root / 'model/contract.json').write_text(json.dumps(contract))
            (root / 'model/protocol.json').write_bytes((package / 'driver/protocol.json').read_bytes())
            emitted = call_tool('emit_package', {'run_dir': str(root / 'second'),
                'model_dir': str(root / 'model'), 'probe': str(package / 'probe.json')})
            self.assertTrue(emitted['ok'], emitted)
            actual = json.loads((Path(emitted['package_dir']) / 'driver/contract.json').read_text())
            self.assertEqual(actual['operations']['measure']['description'], description)
            self.assertEqual(actual['operations']['measure']['uncertainties'], [])

    def test_malformed_public_uncertainty_is_rejected_before_execution(self):
        # Catches accepting unchecked prose where scoped, renderable caveats are required.
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            contract = json.loads((package / 'driver/contract.json').read_text())
            contract['operations']['measure']['uncertainties'] = 'verified'
            rewrite_document(package, 'driver/contract.json', contract)
            checked = call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})
            self.assertFalse(checked['ok'], 'malformed uncertainty must fail package acceptance')

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

    def test_protocol_cannot_redeclare_public_parameters(self):
        # Catches an implementation introducing a second parameter authority.
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package, _ = emit_fixture(root)
            protocol = json.loads((package / 'driver/protocol.json').read_text())
            protocol['operations']['measure']['parameters'] = {'value': {'type': 'number'}}
            rewrite_document(package, 'driver/protocol.json', protocol)
            checked = call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})
            self.assertFalse(checked['ok'], 'protocol-local public metadata must be rejected')

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


if __name__ == '__main__':
    unittest.main()

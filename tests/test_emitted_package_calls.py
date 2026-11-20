"""Calls on an emitted package and the emit stage of a supplied run, through the stage package's public functions.

The package is the one the toolkit emits for the example model and replay of `tests/test_package.py`, and the stage
functions run on the hand-written firmware and mission of `tests/test_supplied_stages.py`.
"""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import socketserver
import threading
import copy
from contextlib import ExitStack

from generative_driver.toolkit import call_tool
from test_package import example_model, example_replay
from test_supplied_stage_files import observe_stage, prepare_stage


def emitted_example(output):
    """The example model at `output/model`, its replay at `output/replay.json` and the package the toolkit emits for
    them, validated, probed by replay, moved and checked: `{'package': <its path>}`."""
    output = Path(output)
    model = output / 'model'
    model.mkdir(parents=True)
    (model / 'model.json').write_text(json.dumps(example_model()), encoding='utf-8')
    replay = output / 'replay.json'
    replay.write_text(json.dumps(example_replay()), encoding='utf-8')

    def call(name, **arguments):
        result = call_tool(name, {'run_dir': str(output / 'tools'), **arguments})
        if result.get('_exit', 0) != 0 or result.get('ok') is False:
            raise RuntimeError(f'Example package {name} failed: {result}')
        return result
    call('model_validate', model_dir=str(model))
    probe = call('probe_run', model_dir=str(model), operation='measure', n=1, replay=str(replay))
    emitted = call('emit_package', model_dir=str(model), probe=probe['probe'])
    package = output / 'relocated package'
    shutil.move(emitted['package_dir'], package)
    call('emit_check', package_dir=str(package), expected_manifest_sha256=emitted['manifest_sha256'])
    return {'package': str(package)}


class BenchmarkPackageGateTests(unittest.TestCase):
    def test_package_execute_treats_read_as_implicit_and_preserves_write_and_actuate(self):
        # A package call on the device a case's bench harness provides: the stage-assigned copy, over the assigned
        # binding with the assigned grants. Read is implicit; write and actuate need their grants, and a refused effect
        # reaches no device.
        from generative_driver.supplied.stages import package_call
        from generative_driver.configurator import digest
        received = []
        class DeviceFixture(socketserver.StreamRequestHandler):
            def handle(self):
                for request in self.rfile:
                    received.append(request)
                    self.wfile.write(b'DEMO-42\n' if request == b'ID?\n' else b'T:21.5\n')
                    self.wfile.flush()
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as cleanup:
            server = cleanup.enter_context(socketserver.ThreadingTCPServer(('127.0.0.1', 0), DeviceFixture))
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            cleanup.callback(thread.join)
            cleanup.callback(server.shutdown)
            root = Path(temporary).resolve()
            emitted_example(root/'smoke')
            model_dir = root/'smoke/model'
            model = json.loads((model_dir/'model.json').read_text())
            model['channel'] = {'type': 'tcp'}
            for name in ('write', 'actuate'):
                operation = copy.deepcopy(model['operations']['measure']); operation['effect'] = name
                model['operations'][name] = operation
                model['provenance'].append({'item': 'operations.'+name, 'source': 'synthetic fixture'})
            (model_dir/'model.json').write_text(json.dumps(model))
            probe = call_tool('probe_run', {'run_dir': str(root/'build'), 'model_dir': str(model_dir),
                'operation': 'measure', 'n': 1, 'replay': str(root/'smoke/replay.json')})
            emitted = call_tool('emit_package', {'run_dir': str(root/'build'), 'model_dir': str(model_dir), 'probe': probe['probe']})
            package = Path(emitted['package_dir'])
            binding = {'host': '127.0.0.1', 'port': server.server_address[1]}
            run_dir = root/'run'; (run_dir/'supplied').mkdir(parents=True)
            (run_dir/'supplied/state.json').write_text(json.dumps({
                'package_copies': {'reuse': str(package)}, 'package_attempts': {'reuse': 'fixture-attempt'},
                'package_sha256': digest(package)}))
            def call(operation, grants):
                return package_call(run_dir, 'reuse', {'package_dir': str(package), 'operation': operation,
                                                   'parameters': {}, 'binding': binding, 'allow_effects': grants})()
            for operation in ('measure', 'write', 'actuate'):
                result = call(operation, ['read', 'write', 'actuate'])
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['outputs']['temperature'], 21.5)
            before = len(received)
            refused = call('actuate', ['read'])
            self.assertFalse(refused['ok'])
            self.assertEqual(refused['error']['fault'], 'operator')
            self.assertIn('explicit effect grant', refused['error']['message'])
            self.assertEqual(refused['transcript'], [])
            self.assertEqual(len(received), before)

    def test_emit_uses_accepted_bytes_and_requires_matching_current_executable_package(self):
        from generative_driver.configurator import digest
        # The configurator's record that an emit_package of the assignment built these package bytes, so the package
        # reaches the emit_check gate this test is about.
        built = lambda package: {'emit_builds': {str(package.resolve()): digest(package)}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            smoke = emitted_example(root/'smoke')
            accepted = root/'accepted'; accepted.mkdir()
            model = accepted/'0-model.json'
            probe = accepted/'1-interface-1.json'
            shutil.copy2(root/'smoke/model/model.json', model)
            shutil.copy2(Path(smoke['package'])/'probe.json', probe)
            handoffs = [{'stage':'probe','artifacts':[{'path':str(path),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'kind':'file'} for path in (model, probe)]}]
            ws = root/'worker'
            prepared = prepare_stage('emit',root/'run',ws,accepted=handoffs)
            self.assertEqual((ws/'model/model.json').read_bytes(), model.read_bytes())
            self.assertEqual((ws/'probe.json').read_bytes(), probe.read_bytes())
            package = ws/'package'
            shutil.copytree(smoke['package'], package)
            checked = observe_stage('emit',root/'run',ws,{'status':'completed'},accepted=handoffs,options=built(package))
            self.assertEqual([Path(a).name for a in checked['artifacts']],['package'],checked)
            self.assertEqual([(c['name'],c['passed']) for c in checked['checks']],[('package_integrity_and_replay',True)])
            # A valid, current package for different model bytes (emit_package run on another model) cannot replace
            # the model accepted by the preceding stage, even if its replay passes: emit_check rejects it.
            other = root/'other-model'; other.mkdir()
            changed = json.loads(model.read_text(encoding='utf-8'))
            changed['device']['name'] = 'different source model'
            (other/'model.json').write_text(json.dumps(changed),encoding='utf-8')
            result = call_tool('probe_run',{'run_dir':str(root/'other'),'model_dir':str(other),
                'operation':'measure','n':1,'replay':str(root/'smoke/replay.json')})
            self.assertTrue(result['ok'],result)
            emitted = call_tool('emit_package',{'run_dir':str(root/'other'),'model_dir':str(other),'probe':result['probe']})
            shutil.rmtree(package); shutil.copytree(emitted['package_dir'],package)
            rejected = observe_stage('emit',root/'run',ws,{'status':'completed'},accepted=handoffs,options=built(package))
            self.assertEqual((rejected['artifacts'],rejected['checks'][0]['passed']),([],False),rejected)
            self.assertEqual(len(rejected['checks'][0]['rejected']),1,rejected)
            # Historical runtimes may have valid integrity but cannot satisfy the
            # benchmark's executed-replay gate.
            shutil.rmtree(package); shutil.copytree(smoke['package'],package)
            engine = package/'interface_runtime/engine.py'
            with engine.open('a',encoding='utf-8') as stream: stream.write('\n# historical runtime\n')
            manifest = json.loads((package/'manifest.json').read_text(encoding='utf-8'))
            manifest['files']['interface_runtime/engine.py'] = hashlib.sha256(engine.read_bytes()).hexdigest()
            (package/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            (package/'manifest.sha256').write_text(hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),encoding='utf-8')
            integrity = call_tool('emit_check',{'run_dir':str(root/'historical-check'),'package_dir':str(package)})
            self.assertTrue(integrity['ok'],integrity)
            self.assertFalse(integrity['runtime_current'],integrity)
            rejected = observe_stage('emit',root/'run',ws,{'status':'completed'},accepted=handoffs,options=built(package))
            self.assertEqual((rejected['artifacts'],rejected['checks'][0]['passed']),([],False),rejected)
            self.assertEqual(len(rejected['checks'][0]['rejected']),1,rejected)

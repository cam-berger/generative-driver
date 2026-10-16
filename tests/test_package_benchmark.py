"""Benchmark package handling through the public stage preparation, observation and package interfaces."""
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


class BenchmarkPackageGateTests(unittest.TestCase):
    def test_package_execute_treats_read_as_implicit_and_preserves_write_and_actuate(self):
        # A legacy tq9 package call on the device its bench harness provides: the stage-assigned copy, over the assigned
        # binding with the assigned grants. Read is implicit; write and actuate need their grants, and a refused effect
        # reaches no device.
        from generative_driver.benchmark import run, package_call
        from generative_driver.configurator import digest
        from generative_driver.toolkit import call_tool
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
            run('setup-smoke', root/'smoke')
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
            run_dir = root/'run'; (run_dir/'benchmark').mkdir(parents=True)
            (run_dir/'benchmark/state.json').write_text(json.dumps({
                'package_copies': {'reuse': str(package)}, 'package_attempts': {'reuse': 'fixture-attempt'},
                'package_sha256': digest(package)}))
            def call(operation, grants):
                return package_call('tq9', run_dir, 'reuse', {'package_dir': str(package), 'operation': operation,
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
        from generative_driver.benchmark import run, prepare_stage, observe_stage
        from generative_driver.configurator import digest
        from generative_driver.toolkit import call_tool
        # The configurator's record that an emit_package of the assignment built these package bytes, so the package
        # reaches the emit_check gate this test is about.
        built = lambda package: {'emit_builds': {str(package.resolve()): digest(package)}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            smoke = run('setup-smoke', root/'smoke')
            accepted = root/'accepted'; accepted.mkdir()
            model = accepted/'0-model.json'
            probe = accepted/'1-interface-1.json'
            shutil.copy2(root/'smoke/model/model.json', model)
            shutil.copy2(Path(smoke['package'])/'probe.json', probe)
            handoffs = [{'stage':'probe','artifacts':[{'path':str(path),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'kind':'file'} for path in (model, probe)]}]
            ws = root/'worker'
            prepared = prepare_stage('tq9','emit',root/'run',ws,accepted=handoffs)
            self.assertEqual((ws/'model/model.json').read_bytes(), model.read_bytes())
            self.assertEqual((ws/'probe.json').read_bytes(), probe.read_bytes())
            package = ws/'package'
            shutil.copytree(smoke['package'], package)
            checked = observe_stage('tq9','emit',root/'run',ws,{'status':'completed'},accepted=handoffs,options=built(package))
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
            rejected = observe_stage('tq9','emit',root/'run',ws,{'status':'completed'},accepted=handoffs,options=built(package))
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
            rejected = observe_stage('tq9','emit',root/'run',ws,{'status':'completed'},accepted=handoffs,options=built(package))
            self.assertEqual((rejected['artifacts'],rejected['checks'][0]['passed']),([],False),rejected)
            self.assertEqual(len(rejected['checks'][0]['rejected']),1,rejected)

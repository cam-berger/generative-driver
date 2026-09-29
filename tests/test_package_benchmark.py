"""Benchmark acceptance through public preparation/check and package interfaces."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import socket
import socketserver
import threading
import copy
from contextlib import ExitStack


class BenchmarkPackageGateTests(unittest.TestCase):
    def test_package_execute_treats_read_as_implicit_and_preserves_write_and_actuate(self):
        from generative_driver.benchmark import run, package_execute, case_root
        from generative_driver.benchmark_support.truth import seal
        from generative_driver.toolkit import call_tool
        class DeviceFixture(socketserver.StreamRequestHandler):
            def handle(self):
                for request in self.rfile:
                    self.wfile.write(b'DEMO-42\n' if request == b'ID?\n' else b'T:21.5\n')
                    self.wfile.flush()
        class MonitorFixture:
            def __init__(self): self.data = b''
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, value): pass
            def sendall(self, data):
                if not data.startswith(b'\xff'):
                    self.data = data + (b'0x3e7\n' if b'ReadDoubleWord' in data else b'') + b'(device)'
            def recv(self, size):
                if not self.data: raise socket.timeout()
                data, self.data = self.data[:size], self.data[size:]
                return data
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
                'package_sha256': hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
                'session': {'binding': binding, 'monitor_port': 1}}))
            # Substitute only the filesystem case manifest with a synthetic,
            # properly encrypted evaluator fixture. No real case password is used.
            manifest_path = case_root()/'cases/tq9/case.json'
            manifest = json.loads(manifest_path.read_text())
            truth_path = root/'fixture.enc'; password = root/'fixture.password'; password.write_text('test-only')
            digest = seal({'monitor': {'pwm_compare': 1, 'pwm_reload': 2}}, truth_path, 'test-only')
            manifest['truth'] = {'path': str(truth_path), 'sha256': digest}
            original_read = Path.read_text
            def read_fixture(path, *args, **kwargs):
                return json.dumps(manifest) if path == manifest_path else original_read(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read_fixture), patch('socket.create_connection', side_effect=lambda *args, **kwargs: MonitorFixture()):
                for operation in ('measure', 'write', 'actuate'):
                    result = package_execute('tq9', run_dir, 'reuse', package, operation,
                        binding=binding, allow_effects=['read', 'write', 'actuate'],
                        options={'evaluator_password_file': str(password)})
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['outputs']['temperature'], 21.5)
                refused = package_execute('tq9', run_dir, 'reuse', package, 'actuate',
                    binding=binding, allow_effects=['read'], options={'evaluator_password_file': str(password)})
                self.assertFalse(refused['ok'])
                self.assertEqual(refused['error']['fault'], 'operator')
                self.assertIn('explicit effect grant', refused['error']['message'])
                self.assertEqual(refused['transcript'], [])

    def test_emit_uses_accepted_bytes_and_requires_matching_current_executable_package(self):
        from generative_driver.benchmark import run, prepare_stage, check_stage
        from generative_driver.toolkit import call_tool
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
            checked = check_stage('tq9','emit',root/'run',ws,{'status':'completed'},accepted=handoffs)
            self.assertTrue(checked['ok'],checked)
            # A valid, current package for different model bytes cannot replace the
            # model accepted by the preceding stage, even if its replay passes.
            other = root/'other-model'; other.mkdir()
            changed = json.loads(model.read_text(encoding='utf-8'))
            changed['device']['name'] = 'different source model'
            (other/'model.json').write_text(json.dumps(changed),encoding='utf-8')
            result = call_tool('probe_run',{'run_dir':str(root/'other'),'model_dir':str(other),
                'operation':'measure','n':1,'replay':str(root/'smoke/replay.json')})
            self.assertTrue(result['ok'],result)
            emitted = call_tool('emit_package',{'run_dir':str(root/'other'),'model_dir':str(other),'probe':result['probe']})
            shutil.rmtree(package); shutil.copytree(emitted['package_dir'],package)
            rejected = check_stage('tq9','emit',root/'run',ws,{'status':'completed'},accepted=handoffs)
            self.assertFalse(rejected['ok'],rejected)
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
            rejected = check_stage('tq9','emit',root/'run',ws,{'status':'completed'},accepted=handoffs)
            self.assertFalse(rejected['ok'],rejected)

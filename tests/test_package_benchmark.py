"""Benchmark acceptance through public preparation/check and package interfaces."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest


class BenchmarkPackageGateTests(unittest.TestCase):
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

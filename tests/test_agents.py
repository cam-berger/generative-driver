"""External scripted processes exercise adapter contracts, never model performance."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import time


class AgentAdapterTests(unittest.TestCase):
    def test_budget_stops_descendants_after_the_runtime_leader_exits(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            started=time.monotonic()
            result=execute(StageRequest(stage='interpret',workspace=Path(root)/'work',prompt='',budget_seconds=.2,report_required=False),
                {'runtime':'codex','command':[sys.executable,'-c',"import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)'])"]})
            self.assertIn(result['status'],('blocked','completed'))
            self.assertLess(time.monotonic()-started,2,'A child kept the output pipe open past the run budget')

    def test_current_goose_complete_event_retains_cumulative_usage(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            fake=root/'usage.py'
            fake.write_text('''import json
print(json.dumps({'type':'complete','total_tokens':150,'input_tokens':100,'output_tokens':50,'cache_read_input_tokens':20,'cache_write_input_tokens':5,'cost_usd':0.03}))
''')
            result=execute(StageRequest(stage='interpret',workspace=root/'work',prompt='fixture',report_required=False),
                           {'runtime':'goose','command':[sys.executable,str(fake)]})
            self.assertEqual(result['usage']['input_tokens'],100)
            self.assertEqual(result['usage']['cached_input_tokens'],20)
            self.assertEqual(result['usage']['output_tokens'],50)
            self.assertEqual(result['usage']['total_tokens'],150)
            self.assertEqual(result['reported_cost_usd'],.03)

    def test_goose_recipe_uses_explicit_extensions_and_does_not_invent_cumulative_usage(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            fake = root / 'goose_contract.py'
            fake.write_text('''import json,sys,pathlib
assert '--no-profile' in sys.argv
assert '--no-session' in sys.argv
r=json.loads(pathlib.Path(sys.argv[sys.argv.index('--recipe')+1]).read_text())
assert [e['name'] for e in r['extensions']]==['developer']
assert r['settings']['goose_provider']=='fixture-provider'
assert r['settings']['goose_model']=='fixture-model'
report={'status':'blocked','summary':'contract fixture','artifacts':[],'checks':[],'unresolved':['fixture']}
print(json.dumps({'type':'message','message':{'role':'assistant','content':[{'type':'text','text':json.dumps(report)}]}}))
print(json.dumps({'type':'complete','total_tokens':42}))
''')
            result = execute(StageRequest(stage='reuse',workspace=root/'work',prompt='contract test'),
                {'runtime':'goose','command':[sys.executable,str(fake)],'provider':'fixture-provider','model':'fixture-model'})
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(result['report']['status'],'blocked')
            self.assertIsNone(result['usage'])
            self.assertEqual(result['runtime_usage']['reported_total_tokens'],42)

    def test_structured_but_incomplete_report_is_rejected(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            fake = root / 'incomplete.py'
            fake.write_text('''import json,sys
sys.stdin.read()
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{"status":"completed"}'}}))
''')
            result = execute(StageRequest(stage='emit',workspace=root/'work',prompt='contract test'),
                             {'runtime':'codex','command':[sys.executable,str(fake)]})
            self.assertEqual(result['status'],'failed')
            self.assertIn('schema',result['reason'])

    def test_codex_adapter_returns_structured_report_and_reported_usage(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            fake = root / 'external_agent.py'
            fake.write_text('''import json, sys
prompt = sys.stdin.read()
assert "stage objective" in prompt
assert "--json" in sys.argv
assert "--ignore-user-config" in sys.argv
report = {"status":"blocked","summary":"Independent observation unavailable","artifacts":[],"checks":[],"unresolved":["reference missing"]}
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(report)}}))
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":42,"cached_input_tokens":12,"output_tokens":7,"reasoning_output_tokens":2}}))
''')
            request = StageRequest(stage='ground', workspace=root / 'work', prompt='stage objective', budget_seconds=10)
            result = execute(request, {'runtime':'codex', 'command':[sys.executable, str(fake)]})
            self.assertEqual(result['report']['status'], 'blocked')
            self.assertEqual(result['usage'], {'input_tokens':42,'cached_input_tokens':12,'output_tokens':7,'reasoning_output_tokens':2})
            self.assertEqual(result['runtime'], 'codex')
            self.assertTrue(Path(result['transcript']).is_file())


if __name__ == '__main__':
    unittest.main()

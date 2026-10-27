"""External scripted processes exercise adapter contracts, never model performance."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import time


class AgentAdapterTests(unittest.TestCase):
    def test_configured_runtimes_preserve_standard_proxy_environment_without_extra_configuration(self):
        import os
        from unittest.mock import patch
        from generative_driver.agents import StageRequest, execute
        proxies = {'HTTP_PROXY': 'http://http-proxy.invalid:8080',
            'HTTPS_PROXY': 'http://https-proxy.invalid:8080', 'ALL_PROXY': 'http://all-proxy.invalid:8080',
            'NO_PROXY': 'localhost,127.0.0.1,::1'}
        # Windows environment names ignore case; aliases must carry the same fixture values.
        proxies.update({name.lower(): value for name, value in list(proxies.items())})
        expected = {**proxies, 'SELECTED_RUNTIME_FLAG': 'selected-fixture-value'}
        with tempfile.TemporaryDirectory(prefix='runtime proxy contract ') as temporary:
            root = Path(temporary)
            fake = root/'environment_contract.py'
            fake.write_text('''import os,sys
sys.stdin.read()
expected = ''' + repr(expected) + '''
actual = {name: os.environ.get(name) for name in expected}
assert actual == expected, actual
for name in ('UNRELATED_SESSION_FLAG', 'BENCH_EVALUATOR_TOKEN', 'GROUNDTRUTH_PASSWORD'):
    assert name not in os.environ, name
''', encoding='utf-8')
            environment = {**expected, 'HOME': str(root/'home'), 'USERPROFILE': str(root/'home'),
                'CODEX_HOME': str(root/'codex'),
                'UNRELATED_SESSION_FLAG': 'unrelated-fixture-value',
                'BENCH_EVALUATOR_TOKEN': 'forbidden-fixture-value', 'GROUNDTRUTH_PASSWORD': 'forbidden-fixture-value'}
            if 'SYSTEMROOT' in os.environ:
                environment['SYSTEMROOT'] = os.environ['SYSTEMROOT']
            with patch.dict(os.environ, environment, clear=True):
                for runtime in ('codex', 'goose'):
                    with self.subTest(runtime=runtime):
                        result = execute(StageRequest(stage='interpret', workspace=root/runtime,
                            prompt='scripted environment contract', report_required=False),
                            {'runtime': runtime, 'command': [sys.executable, str(fake)],
                             'env_keys': ['SELECTED_RUNTIME_FLAG', 'BENCH_EVALUATOR_TOKEN', 'GROUNDTRUTH_PASSWORD']})
                        self.assertEqual(result['status'], 'completed', Path(result['stderr']).read_text())

    def test_a_runtime_without_a_configured_command_is_never_looked_up_on_path(self):
        # Whatever has the runtime's name on PATH may be another program: with no configured command the stage stops
        # for the operator and nothing is launched.
        from unittest.mock import patch
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory(prefix='unconfigured runtime ') as directory, \
                patch('subprocess.Popen', side_effect=AssertionError('a runtime was launched')):
            for runtime in ('codex', 'goose'):
                blocked = execute(StageRequest(stage='acquire', workspace=Path(directory)/runtime, prompt='fixture'),
                                  {'runtime': runtime})
                self.assertEqual((blocked['status'], blocked['cause']), ('blocked', 'runtime_unavailable'), blocked)
                self.assertIn('configure', blocked['reason'])

    def test_launcher_failure_is_a_blocker_while_runtime_exit_remains_a_failure(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory(prefix='runtime launch contract ') as directory:
            root = Path(directory)
            launcher = root / 'launcher.py'
            launcher.write_text('''import json,sys
sys.stdin.read()
print(json.dumps({'type':'generative_driver.runtime_start_failed',
                  'error':'Configured executable is unavailable'}), flush=True)
sys.exit(1)
''', encoding='utf-8')
            blocked = execute(StageRequest(stage='acquire', workspace=root/'blocked', prompt='fixture'),
                              {'runtime':'codex', 'command':[sys.executable,str(launcher)]})
            self.assertEqual((blocked['status'], blocked['cause']), ('blocked', 'runtime_unavailable'), blocked)
            self.assertIn('Cannot start configured runtime', blocked['reason'])
            self.assertIn('unavailable', blocked['reason'])
            self.assertIsNone(blocked['usage'])
            self.assertIsNone(blocked['report'])
            failed = execute(StageRequest(stage='acquire', workspace=root/'failed', prompt='fixture'),
                             {'runtime':'codex', 'command':[sys.executable,'-c','raise SystemExit(7)']})
            self.assertEqual((failed['status'], failed['cause']), ('failed', 'exit_error'), failed)
            self.assertEqual(failed['exit_code'], 7)
            missing = execute(StageRequest(stage='acquire', workspace=root/'missing', prompt='fixture'),
                              {'runtime':'codex', 'command':['missing-contract-runtime']})
            self.assertEqual((missing['status'], missing['cause']), ('blocked', 'runtime_unavailable'))
            silent = execute(StageRequest(stage='acquire', workspace=root/'silent', prompt='fixture'),
                             {'runtime':'codex', 'command':[sys.executable,'-c','import sys; sys.stdin.read()']})
            self.assertEqual((silent['status'], silent['cause']), ('failed', 'no_report'))
            self.assertIn('Configured runtime exited', failed['reason'])

    def test_codex_scoped_approval_is_explicit_and_only_for_assigned_tools(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            fake=root/'approval_contract.py'
            fake.write_text('''import json,sys,tomllib
args=sys.argv
assert 'approval_policy="never"' in args
assert args[args.index('--sandbox')+1]=='workspace-write'
assert 'mcp_servers.stage.enabled_tools=["model_validate"]' in args
settings=[a for a in args if a.startswith('mcp_servers.stage.tools=')]
configured=tomllib.loads(settings[0])['mcp_servers']['stage']['tools'] if settings else {}
approved=configured.get('model_validate',{}).get('approval_mode')=='approve'
assert set(configured)<= {'model_validate'}
assert approved == (sys.stdin.read()=='approved')
assert not any('default_tools_approval_mode' in a for a in args)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}}))
''')
            for approved in (False,True):
                result=execute(StageRequest(stage='probe',workspace=root/str(approved),prompt='approved' if approved else 'unapproved scope',
                    report_required=False,allowed_tools=['model_validate'],gateway={'command':sys.executable},approve_scoped_tools=approved),
                    {'runtime':'codex','command':[sys.executable,str(fake)]})
                self.assertEqual(result['status'],'completed',result)

    def test_local_skill_disables_target_instruction_files(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            fake=root/'skill_contract.py'
            fake.write_text('''import json,sys,tomllib
settings=[a for a in sys.argv if a.startswith('skills.config=')]
for item in tomllib.loads(settings[0])['skills']['config']:
 assert item['path'].endswith('/SKILL.md') or item['path'].endswith('\\\\SKILL.md')
 assert item['enabled'] is False
assert any(a.startswith('developer_instructions=') and 'Do not read personal skills' in a for a in sys.argv)
''')
            result=execute(StageRequest(stage='interpret',workspace=root/'work',prompt='exact sealed prompt',report_required=False),
                {'runtime':'codex','command':[sys.executable,str(fake)]})
            self.assertEqual(result['status'],'completed',result)

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
            # Goose 1.53 --no-profile also drops recipe extensions, leaving the
            # worker without its developer tools and stage gateway.
            fake.write_text('''import json,sys,pathlib
assert '--no-profile' not in sys.argv
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
            self.assertEqual((result['status'],result['cause']),('failed','invalid_report'))
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

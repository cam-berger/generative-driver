"""External scripted processes exercise adapter contracts, never model performance."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import time


class AgentAdapterTests(unittest.TestCase):
    def test_codex_preserves_standard_proxy_environment_without_extra_configuration(self):
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
                result = execute(StageRequest(stage='interpret', workspace=root/'codex',
                    prompt='scripted environment contract', report_required=False),
                    {'runtime': 'codex', 'command': [sys.executable, str(fake)],
                     'env_keys': ['SELECTED_RUNTIME_FLAG', 'BENCH_EVALUATOR_TOKEN', 'GROUNDTRUTH_PASSWORD']})
                self.assertEqual(result['status'], 'completed', Path(result['stderr']).read_text())

    def test_a_runtime_without_a_configured_command_is_never_looked_up_on_path(self):
        # Whatever has the runtime's name on PATH may be another program: with no configured command the stage stops
        # for the operator and nothing is launched.
        from unittest.mock import patch
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory(prefix='unconfigured runtime ') as directory, \
                patch('subprocess.Popen', side_effect=AssertionError('a runtime was launched')):
            blocked = execute(StageRequest(stage='acquire', workspace=Path(directory)/'codex', prompt='fixture'),
                              {'runtime': 'codex'})
            self.assertEqual((blocked['status'], blocked['cause']), ('blocked', 'runtime_unavailable'), blocked)
            self.assertIn('configure --executor codex --command <path>', blocked['reason'])

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

    def test_unsupported_worker_is_refused_before_workspace_creation(self):
        from generative_driver.agents import StageRequest, execute
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / 'stage'
            request = StageRequest(stage='interpret', workspace=workspace, prompt='fixture', report_required=False)
            with self.assertRaisesRegex(ValueError, 'Unsupported executor'):
                execute(request, {'runtime': 'unknown-runtime', 'command': [sys.executable, '-c', 'pass']})
            self.assertFalse(workspace.exists())

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

    def test_02_B7_non_object_json_or_null_item_stdout_does_not_crash_supervision_and_terminates_on_callback_error(self):
        from generative_driver.agents import StageRequest, execute
        from interpret_task_fixtures import gone
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            odd = root / 'odd_stdout.py'
            odd.write_text('''import json, sys
sys.stdin.read()
for line in ('123', '[1, 2]', '"hello"', 'null',
             json.dumps({'type': 'item.completed', 'item': None}),
             json.dumps({'type': 'item.completed', 'item': 42}),
             json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 99}})):
    print(line, flush=True)
report = {'status': 'completed', 'summary': 'ok', 'artifacts': [], 'checks': [], 'unresolved': []}
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}}), flush=True)
''', encoding='utf-8')
            result = execute(StageRequest(stage='interpret', workspace=root / 'odd', prompt='odd', budget_seconds=10),
                             {'runtime': 'codex', 'command': [sys.executable, str(odd)]})
            self.assertEqual(result['status'], 'completed', result)
            self.assertEqual(result['report']['summary'], 'ok')
            # If supervision raises after Popen (e.g. in on_event), the worker process must be terminated in finally.
            sleeper = root / 'sleeper.py'
            pid_file = root / 'worker.pid'
            sleeper.write_text('''import json, os, pathlib, sys, time
sys.stdin.read()
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')
print(json.dumps({'type': 'ping'}), flush=True)
time.sleep(30)
''', encoding='utf-8')
            def boom(event):
                raise RuntimeError('supervision boom')
            with self.assertRaisesRegex(RuntimeError, 'supervision boom'):
                execute(StageRequest(stage='interpret', workspace=root / 'boom', prompt='boom',
                                     budget_seconds=10, report_required=False),
                        {'runtime': 'codex', 'command': [sys.executable, str(sleeper), str(pid_file)]},
                        on_event=boom)
            self.assertTrue(gone(int(pid_file.read_text(encoding='utf-8')), seconds=5),
                            'worker process survived exception inside execute()')

    def test_02_B8_unbounded_single_line_without_newlines_hits_output_limit_without_buffering_whole_line(self):
        from unittest.mock import patch
        from generative_driver import agents
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            flood = root / 'flood.py'
            flood.write_text('''import sys, time
sys.stdin.read()
chunk = 'x' * 8192
for _ in range(1024):
    sys.stdout.write(chunk)
    sys.stdout.flush()
time.sleep(30)
''', encoding='utf-8')
            started = time.monotonic()
            with patch.object(agents, 'OUTPUT_LIMIT_BYTES', 32 * 1024), \
                    patch.object(agents, '_CHUNK_BYTES', 4096):
                result = agents.execute(
                    agents.StageRequest(stage='interpret', workspace=root / 'flood', prompt='flood',
                                        budget_seconds=15, report_required=False),
                    {'runtime': 'codex', 'command': [sys.executable, str(flood)]})
            self.assertEqual((result['status'], result['cause']), ('failed', 'output_limit'), result)
            self.assertLess(time.monotonic() - started, 5, 'single line without newline waited instead of hitting limit')

    def test_large_prompt_never_deadlocks_chatty_worker_and_honors_budget(self):
        from generative_driver import agents
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            big_prompt = 'P' * (512 * 1024)
            # Subtest 1: worker floods stdout (> _QUEUE_MAX lines and pipe buffer) before reading a 512 KiB prompt.
            chatty = root / 'chatty.py'
            chatty.write_text('''import json, sys
for i in range(500):
    print(json.dumps({'type': 'chat', 'i': i, 'pad': 'x' * 512}), flush=True)
data = sys.stdin.read()
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': str(len(data))}}), flush=True)
''', encoding='utf-8')
            outcome = {}
            def run_chatty():
                try:
                    outcome['result'] = agents.execute(
                        agents.StageRequest(stage='interpret', workspace=root / 'chatty', prompt=big_prompt,
                                            budget_seconds=15, report_required=False),
                        {'runtime': 'codex', 'command': [sys.executable, str(chatty)]})
                except BaseException as exc:
                    outcome['error'] = exc
            t = threading.Thread(target=run_chatty, daemon=True)
            t.start()
            t.join(5)
            self.assertFalse(t.is_alive(), 'execute() deadlocked writing a large prompt to a chatty worker')
            self.assertNotIn('error', outcome)
            self.assertEqual(outcome['result']['status'], 'completed', outcome['result'])
            self.assertEqual(outcome['result']['final_text'], str(len(big_prompt)))

            # Subtest 2: worker never reads stdin; budget must still fire promptly despite large prompt.
            deaf = root / 'deaf.py'
            deaf.write_text('import time\ntime.sleep(30)\n', encoding='utf-8')
            outcome2 = {}
            started = time.monotonic()
            def run_deaf():
                try:
                    outcome2['result'] = agents.execute(
                        agents.StageRequest(stage='interpret', workspace=root / 'deaf', prompt=big_prompt,
                                            budget_seconds=0.5, report_required=False),
                        {'runtime': 'codex', 'command': [sys.executable, str(deaf)]})
                except BaseException as exc:
                    outcome2['error'] = exc
            t2 = threading.Thread(target=run_deaf, daemon=True)
            t2.start()
            t2.join(4)
            self.assertFalse(t2.is_alive(), 'execute() hung in stdin.write instead of enforcing budget')
            self.assertEqual((outcome2['result']['status'], outcome2['result']['cause']),
                             ('blocked', 'run_budget'), outcome2['result'])
            self.assertLess(time.monotonic() - started, 4)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group teardown after leader exit')
    def test_exited_leader_with_live_posix_descendant_is_terminated_on_callback_error(self):
        from generative_driver import agents
        from interpret_task_fixtures import gone
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            child_pid_file = root / 'child.pid'
            worker = root / 'leader.py'
            worker.write_text(f'''import json, os, subprocess, sys
child_code = """import json, os, pathlib, sys, time
pathlib.Path({str(child_pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8')
time.sleep(0.25)
print(json.dumps({{'type': 'from_child'}}), flush=True)
time.sleep(30)
"""
subprocess.Popen([sys.executable, '-c', child_code], stdin=subprocess.DEVNULL)
os._exit(0)
''', encoding='utf-8')
            outcome = {}
            def boom(event):
                if event.get('type') == 'from_child':
                    raise RuntimeError('callback boom after leader exited')
            def run():
                try:
                    agents.execute(
                        agents.StageRequest(stage='interpret', workspace=root / 'leader', prompt='hi',
                                            budget_seconds=15, report_required=False),
                        {'runtime': 'codex', 'command': [sys.executable, str(worker)]},
                        on_event=boom)
                except BaseException as exc:
                    outcome['error'] = exc
            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(3)
            try:
                self.assertFalse(t.is_alive(), 'execute() hung closing pipe held by descendant after leader exited')
                self.assertIsInstance(outcome.get('error'), RuntimeError, outcome)
                self.assertTrue(gone(int(child_pid_file.read_text(encoding='utf-8')), seconds=3),
                                'descendant process survived callback failure after leader exited')
            finally:
                if child_pid_file.is_file():
                    try:
                        os.kill(int(child_pid_file.read_text(encoding='utf-8')), 9)
                    except OSError:
                        pass


if __name__ == '__main__':
    unittest.main()

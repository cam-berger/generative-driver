"""Dashboard contracts through the same read-only run interfaces as the CLI."""
import importlib
import json
import os
from multiprocessing.connection import Client, Listener
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


class DashboardTests(unittest.TestCase):
    def dashboard(self):
        try:
            return importlib.import_module('generative_driver.dashboard')
        except ImportError as exc:
            self.fail('Run dashboard is unavailable: ' + str(exc))

    def scripted_worker(self, root):
        home, ready, release = root / 'owner', root / 'ready', root / 'release'
        script = root / 'scripted_worker.py'
        script.write_text(f'''import json, pathlib, sys, time
sys.stdin.read()
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'Scripted worker is active'}}}}), flush=True)
pathlib.Path({str(ready)!r}).touch()
until = time.monotonic() + 30
while not pathlib.Path({str(release)!r}).exists() and time.monotonic() < until: time.sleep(.02)
report = {{'status':'completed','summary':'scripted dashboard contract','artifacts':[],'checks':[],'unresolved':[]}}
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':json.dumps(report)}}}}), flush=True)
''', encoding='utf-8')
        return home, ready, release, script

    def test_repair_progress_does_not_count_superseded_gates_as_accepted(self):
        # Counting the previous revision's handoffs would falsely complete probe.
        result = {'run_id': 'run-example', 'status': 'running', 'stage': 'interpret',
                  'created': 10, 'updated': 20, 'uncertain_effect': False,
                  'progress': {'revision': 1, 'repairs': 1},
                  'accepted_handoffs': [
                      {'stage': 'acquire', 'revision': 0},
                      {'stage': 'interpret', 'revision': 0},
                      {'stage': 'probe', 'revision': 0}],
                  'worker_reports': [], 'agent': {'model': 'example-model'},
                  'case_options': {'evaluator_password': 'PRIVATE-DO-NOT-SHOW'}}
        snapshot = self.dashboard().project_run(result, now=30)
        self.assertEqual([(s['name'], s['status']) for s in snapshot['stages']], [
            ('acquire', 'accepted'), ('interpret', 'running'), ('probe', 'pending'),
            ('ground', 'pending'), ('emit', 'pending'), ('reuse', 'pending')])
        self.assertEqual(snapshot['accepted_count'], 1)
        self.assertEqual(snapshot['repairs'], 1)
        self.assertEqual(snapshot['elapsed_seconds'], 20)
        self.assertNotIn('PRIVATE-DO-NOT-SHOW', str(snapshot))

    def test_http_monitor_preserves_a_blocked_run_and_refuses_control_requests(self):
        from generative_driver.client import call
        dashboard = self.dashboard()
        self.assertTrue(hasattr(dashboard, 'create_server'), 'Local dashboard server is missing')
        with tempfile.TemporaryDirectory(prefix='dashboard owner ') as directory:
            home = Path(directory)
            try:
                run = call('start', {'goal': 'Dashboard contract; no model or hardware',
                    'executor_config': {'command': ['missing-dashboard-contract-runtime']}}, home=home)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    original = call('result', run, home=home)
                    if original['status'] == 'blocked' and not original['stopping']:
                        break
                    time.sleep(.01)
                self.assertEqual(original['status'], 'blocked')
                events_before = call('events', run, home=home)
                server = dashboard.create_server(run['run_id'], home=home)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with urllib.request.urlopen(server.url + 'api/run', timeout=5) as response:
                        snapshot = json.load(response)
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(snapshot['run_id'], run['run_id'])
                    self.assertEqual(snapshot['status'], 'blocked')
                    self.assertEqual(snapshot['stages'][0]['status'], 'blocked')
                    self.assertIn('Cannot start', snapshot['reason'])
                    self.assertTrue(any('blocked' in entry['message'] for entry in snapshot['logs']))
                    with self.assertRaises(urllib.error.HTTPError) as absent:
                        urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/run', timeout=5)
                    self.assertEqual(absent.exception.code, 404)
                    request = urllib.request.Request(server.url + 'api/run', data=b'{"method":"resume"}',
                                                     headers={'Content-Type': 'application/json'})
                    with self.assertRaises(urllib.error.HTTPError) as refused:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(refused.exception.code, 405)
                    request = urllib.request.Request(server.url + 'api/run', headers={'Host': 'other.example'})
                    with self.assertRaises(urllib.error.HTTPError) as foreign:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(foreign.exception.code, 403)
                    self.assertEqual(call('events', run, home=home), events_before)
                    self.assertEqual(call('result', run, home=home), original)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(5)
            finally:
                call('shutdown', home=home)

    def test_worker_activity_reads_only_a_bounded_tail_and_omits_command_payloads(self):
        dashboard = self.dashboard()
        self.assertTrue(hasattr(dashboard, 'read_activity'), 'Worker activity reader is missing')
        with tempfile.TemporaryDirectory(prefix='dashboard log ') as directory:
            root = Path(directory).resolve()
            rows = [json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': 'Analyzing the firmware'}}),
                json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution',
                    'command': 'tool --password PRIVATE', 'aggregated_output': 'PRIVATE', 'exit_code': 0}})]
            (root / '_agent_events.jsonl').write_text('x' * 100000 + '\n' + '\n'.join(rows) + '\n{', encoding='utf-8')
            activity = dashboard.read_activity(root)
            self.assertEqual(activity['entries'], ['Analyzing the firmware', 'Shell command finished (exit 0)'])
            self.assertNotIn('PRIVATE', str(activity))
            self.assertLess(activity['age_seconds'], 5)
            self.assertEqual(dashboard.read_activity(root / 'missing')['entries'], [])

    def test_finished_worker_stays_in_checking_until_its_handoff_is_accepted(self):
        result = {'run_id': 'run-checking', 'status': 'running', 'stage': 'probe',
                  'created': 10, 'updated': 20, 'progress': {'revision': 1},
                  'accepted_handoffs': [{'stage': 'acquire', 'revision': 0},
                                        {'stage': 'interpret', 'revision': 1}],
                  'worker_reports': [{'stage': 'probe', 'revision': 1, 'status': 'completed'}]}
        snapshot = self.dashboard().project_run(result, now=30)
        self.assertEqual(snapshot['stages'][2]['status'], 'checking')
        self.assertEqual(snapshot['accepted_count'], 2)

    def test_cli_opens_a_live_page_and_its_exit_leaves_the_run_running(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix='dashboard CLI ') as directory:
            root = Path(directory).resolve()
            home, ready, release, script = self.scripted_worker(root)
            process = None
            try:
                run = call('start', {'goal': 'Scripted dashboard contract; no model or hardware',
                    'executor_config': {'command': [sys.executable, str(script)]}}, home=home)
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists(), 'Scripted worker did not start')
                stdout, stderr = root / 'dashboard.out', root / 'dashboard.err'
                with stdout.open('wb') as out, stderr.open('wb') as err:
                    process = subprocess.Popen([sys.executable, '-m', 'generative_driver', '--home',
                        str(home), 'dashboard', run['run_id'], '--no-open'], cwd=root,
                        stdin=subprocess.DEVNULL, stdout=out, stderr=err)
                    deadline = time.monotonic() + 10
                    while not stdout.stat().st_size and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertIsNone(process.poll(), stderr.read_text(encoding='utf-8'))
                    url = stdout.read_text(encoding='utf-8').strip()
                    self.assertTrue(url.startswith('http://127.0.0.1:'), url)
                    with urllib.request.urlopen(url, timeout=5) as response:
                        self.assertEqual(response.headers.get_content_type(), 'text/html')
                        self.assertGreater(len(response.read()), 100)
                    with urllib.request.urlopen(url + 'app.js', timeout=5) as response:
                        self.assertEqual(response.headers.get_content_type(), 'text/javascript')
                    with urllib.request.urlopen(url + 'api/run', timeout=5) as response:
                        snapshot = json.load(response)
                    self.assertEqual(snapshot['status'], 'running')
                    self.assertIn('Scripted worker is active', snapshot['activity']['entries'])
                    process.terminate()
                    process.wait(timeout=5)
                    self.assertEqual(call('status', run, home=home)['status'], 'running')
                    release.touch()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        final = self.dashboard().RunReader(run['run_id'], home).snapshot()
                        if final.get('status') == 'blocked':
                            break
                        time.sleep(.02)
                    self.assertEqual(final['status'], 'blocked')
                    self.assertEqual(final['stages'][0]['status'], 'blocked')
            finally:
                release.touch()
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                call('shutdown', home=home)

    def test_resumed_worker_is_running_despite_an_older_completed_report(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix='dashboard resume ') as directory:
            home, ready, release, script = self.scripted_worker(Path(directory).resolve())
            release.touch()
            try:
                run = call('start', {'goal': 'Scripted resume contract; no model or hardware',
                    'executor_config': {'command': [sys.executable, str(script)]}}, home=home)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    original = call('result', run, home=home)
                    if original['status'] == 'blocked' and not original['stopping']:
                        break
                    time.sleep(.02)
                self.assertEqual(original['worker_reports'][0]['status'], 'completed')
                ready.unlink()
                release.unlink()
                call('resume', run, home=home)
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists(), 'Resumed worker did not start')
                snapshot = self.dashboard().RunReader(run['run_id'], home).snapshot()
                self.assertEqual(snapshot['status'], 'running')
                self.assertEqual(snapshot['stages'][0]['status'], 'running')
            finally:
                release.touch()
                call('shutdown', home=home)

    def test_goose_assistant_activity_keeps_prompt_and_tool_payloads_private(self):
        with tempfile.TemporaryDirectory(prefix='dashboard goose ') as directory:
            root = Path(directory).resolve()
            rows = [
                {'type': 'message', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'PRIVATE INPUT'}]}},
                {'type': 'message', 'message': {'role': 'assistant', 'content': [
                    {'type': 'text', 'text': 'Inspecting the supplied image'},
                    {'type': 'toolRequest', 'toolCall': {'name': 'analysis', 'arguments': {'secret': 'PRIVATE'}}}]}},
                {'type': 'complete', 'response': 'Preparing the driver package'}]
            (root / '_agent_events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            activity = self.dashboard().read_activity(root)
            self.assertEqual(activity['entries'], ['Inspecting the supplied image', 'Preparing the driver package'])
            self.assertNotIn('PRIVATE', str(activity))

    def test_reconnect_only_read_has_a_reply_deadline_without_retrying_the_owner(self):
        from generative_driver.client import call
        suffix = uuid.uuid4().hex[:12]
        address = (r'\\.\pipe\gd-ui-' + suffix if os.name == 'nt' else
                   str(Path(tempfile.gettempdir()) / ('gd-ui-' + suffix + '.sock')))
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        auth = secrets.token_bytes(32)
        release, entered = threading.Event(), threading.Event()
        with Listener(address, family=family, authkey=auth) as listener, tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            metadata = json.dumps({'address': address, 'family': family, 'authkey': auth.hex()})
            (home / 'service.json').write_text(metadata, encoding='utf-8')
            requests = []
            def slow_owner():
                try:
                    with listener.accept() as connection:
                        requests.append(json.loads(connection.recv_bytes()))
                        entered.set()
                        release.wait(5)
                except (OSError, EOFError):
                    pass
            thread = threading.Thread(target=slow_owner, daemon=True)
            thread.start()
            try:
                started = time.monotonic()
                try:
                    result = call('result', {'run_id': 'run-slow'}, home=home,
                                  autostart=False, read_timeout=.05)
                except TypeError as exc:
                    self.fail('Read-only reply deadline is unavailable: ' + str(exc))
                self.assertFalse(result['ok'])
                self.assertLess(time.monotonic() - started, 1)
                self.assertTrue(entered.is_set())
                self.assertEqual(requests, [{'method': 'result', 'params': {'run_id': 'run-slow'}}])
                self.assertEqual((home / 'service.json').read_text(encoding='utf-8'), metadata)
                with self.assertRaises(ValueError):
                    call('start', home=home, autostart=False, read_timeout=1)
            finally:
                release.set()
                if not entered.is_set():
                    with Client(address, family=family, authkey=auth) as connection:
                        connection.send_bytes(b'{"method":"fixture-cleanup","params":{}}')
                thread.join(5)

    def test_stalled_owner_does_not_queue_additional_monitor_reads(self):
        suffix = uuid.uuid4().hex[:12]
        address = (r'\\.\pipe\gd-ui-' + suffix if os.name == 'nt' else
                   str(Path(tempfile.gettempdir()) / ('gd-ui-' + suffix + '.sock')))
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        auth, release, entered = secrets.token_bytes(32), threading.Event(), threading.Event()
        with Listener(address, family=family, authkey=auth) as listener, tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'service.json').write_text(json.dumps({'address': address, 'family': family,
                                                         'authkey': auth.hex()}), encoding='utf-8')
            requests, snapshots = [], []
            def slow_owner():
                with listener.accept() as connection:
                    requests.append(json.loads(connection.recv_bytes()))
                    entered.set()
                    release.wait(5)
            owner = threading.Thread(target=slow_owner, daemon=True)
            owner.start()
            reader = self.dashboard().RunReader('run-slow', home)
            first = threading.Thread(target=lambda: snapshots.append(reader.snapshot()), daemon=True)
            second = threading.Thread(target=lambda: snapshots.append(reader.snapshot()), daemon=True)
            try:
                first.start()
                self.assertTrue(entered.wait(1))
                second.start()
                second.join(.3)
                self.assertFalse(second.is_alive(), 'A stalled read queues another dashboard request')
                first.join(2.5)
                self.assertFalse(first.is_alive(), 'Dashboard did not apply its short reply deadline')
                self.assertEqual(len(snapshots), 2)
                self.assertTrue(all(not snapshot['ok'] for snapshot in snapshots))
                self.assertEqual(requests, [{'method': 'result', 'params': {'run_id': 'run-slow'}}])
            finally:
                (home / 'service.json').unlink(missing_ok=True)
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
                owner.join(5)

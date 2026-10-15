"""Public process lifecycle contracts; scripted workers, no inference or hardware."""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def process_alive(pid):
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            status = kernel.WaitForSingleObject(handle, 0)
            if status == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            return status == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ServiceLifecycleTests(unittest.TestCase):
    def ended(self, home, **changes):
        """A configurator that ran from `home` and ended, its service.json changed by `changes`: the pid it ran as."""
        import json
        from generative_driver.client import call
        pid = call('ping', {}, home)['pid']
        self.assertTrue(call('shutdown', {}, home)['stopped'])
        record = home / 'service.json'
        record.write_text(json.dumps({**json.loads(record.read_text(encoding='utf-8')), **changes}), encoding='utf-8')
        return pid

    def test_burst_of_clients_reconnects_to_one_owner_without_dropped_connections(self):
        from concurrent.futures import ThreadPoolExecutor
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            started = call('ping', {}, home)
            try:
                with ThreadPoolExecutor(max_workers=32) as clients:
                    replies = list(clients.map(lambda _:call('ping', {}, home, autostart=False), range(32)))
                self.assertTrue(all(reply.get('ok') for reply in replies), replies)
                self.assertEqual({reply['pid'] for reply in replies}, {started['pid']})
            finally:
                call('shutdown', {}, home)

    def test_transient_ipc_errors_never_replace_a_live_owner(self):
        from generative_driver import client
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            started = client.call('ping', {}, home)
            original, failures = client._request, []
            def interrupted(*args, **kwargs):
                if len(failures) < 2:
                    failures.append(True)
                    raise ConnectionRefusedError('transient connection refusal')
                return original(*args, **kwargs)
            try:
                with patch.object(client, '_request', side_effect=interrupted), \
                     patch('subprocess.Popen', side_effect=AssertionError('A live owner must not be replaced')):
                    reconnected = client.call('ping', {}, home)
                self.assertEqual(reconnected['pid'], started['pid'])
                self.assertTrue(process_alive(started['pid']))
            finally:
                client.call('shutdown', {}, home)

    def test_unreachable_live_owner_preserves_its_endpoint(self):
        from generative_driver import client
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            client.call('ping', {}, home)
            endpoint = (home/'service.json').read_bytes()
            try:
                with patch.object(client, '_request', side_effect=ConnectionRefusedError('unreachable')), \
                     patch('subprocess.Popen', side_effect=AssertionError('A live owner must not be replaced')):
                    with self.assertRaisesRegex(TimeoutError, 'still running'):
                        client._start(home, timeout=.01)
                self.assertEqual((home/'service.json').read_bytes(), endpoint)
            finally:
                client.call('shutdown', {}, home)

    def test_lost_tool_reply_is_reported_without_dispatching_the_operation_again(self):
        import json
        from generative_driver import client
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home/'service.json').write_text(json.dumps({'address':'fixture', 'family':'AF_PIPE', 'authkey':'aa'}))
            class Connection:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def send_bytes(self, data): sent.append(json.loads(data))
                def poll(self, timeout): return True
                def recv_bytes(self, limit): raise EOFError('reply lost after dispatch')
            sent = []
            with patch.object(client, 'Client', return_value=Connection()), \
                 patch.object(client, '_start', side_effect=AssertionError('Do not replay an interrupted write')):
                result = client.call('tool', {'name':'interface_execute'}, home)
            self.assertFalse(result['ok'])
            self.assertIn('interrupted', result['reason'])
            self.assertEqual([item['method'] for item in sent], ['tool'])

    def test_unpublished_daemon_exits_when_starting_client_disconnects(self):
        with tempfile.TemporaryDirectory() as temporary:
            child = subprocess.run([sys.executable, '-m', 'generative_driver.daemon',
                '--home', temporary, '--wait-for-startup'], input='',
                capture_output=True, text=True, timeout=5)
            self.assertEqual(child.returncode, 1, child)
            self.assertIn('Startup cancelled before endpoint publication', child.stderr)
            self.assertFalse((Path(temporary) / 'service.json').exists())
            self.assertFalse((Path(temporary) / 'runs.sqlite3').exists())

    def test_service_restarts_with_a_metadata_reader_open(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            first = call('ping', {}, home)
            self.assertTrue(call('shutdown', {}, home)['stopped'])
            try:
                # A normal Windows read handle permits writes but denies rename.
                # Keep the same reader open across the next owner publication.
                with (home / 'service.json').open(encoding='utf-8'):
                    try:
                        second = call('ping', {}, home)
                    except Exception as exc:
                        self.fail(f'{exc}\nFresh fixture daemon log:\n' +
                                  (home / 'daemon.log').read_text(encoding='utf-8'))
                    self.assertNotEqual(first['pid'], second['pid'])
                    self.assertEqual(call('ping', {}, home, autostart=False)['pid'], second['pid'])
            finally:
                call('shutdown', {}, home)

    def test_failed_startup_reports_the_child_exit_without_waiting_for_readiness(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / 'runs.sqlite3').write_bytes(b'invalid database fixture')
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'Configurator exited during startup'):
                call('ping', {}, home)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn('DatabaseError: file is not a database',
                          (home / 'daemon.log').read_text(encoding='utf-8'))

    def test_reconnect_only_client_never_creates_a_service(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'service'
            absent = call('ping', {}, home, autostart=False)
            self.assertFalse(absent['ok'], absent)
            self.assertIn('service start', absent.get('reason', ''), absent)
            self.assertFalse((home / 'service.json').exists())
            try:
                owner = call('ping', {}, home)
                connected = call('ping', {}, home, autostart=False)
                self.assertEqual(connected['pid'], owner['pid'])
            finally:
                call('shutdown', {}, home)

    def test_a_client_never_drives_a_configurator_running_other_code(self):
        import json
        from generative_driver.client import call, code_identity
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'service'
            try:
                owner = call('ping', {}, home)
                self.assertEqual((owner['code'], owner['compatible']), (code_identity(), True), owner)
                # The running configurator was started by another installation, or before this code was edited.
                record = home / 'service.json'
                record.write_text(json.dumps({**json.loads(record.read_text(encoding='utf-8')), 'code': 'other'}),
                                  encoding='utf-8')
                refused = call('start', {'goal': 'Must not reach older code'}, home)
                self.assertFalse(refused['ok'], refused)
                self.assertIn('service stop', refused['reason'])
                self.assertFalse((home / 'runs').exists() and list((home / 'runs').iterdir()))
                pinged = call('ping', {}, home)
                self.assertEqual((pinged['ok'], pinged['pid'], pinged['compatible']), (False, owner['pid'], False), pinged)
                self.assertIn('service stop', pinged['reason'])
                # A worker gateway is the configurator's own machinery, launched from its tree for one assignment it
                # authenticates: a host change during a run must never cut a stage off from its tools.
                gateway = call('tools', {'run_id': 'run-absent', 'assignment_id': 'absent'}, home, autostart=False)
                self.assertNotIn('service stop', gateway.get('reason', ''), gateway)
            finally:
                self.assertTrue(call('shutdown', {}, home)['stopped'])
            # With the incompatible configurator gone, the next call starts one running this code.
            try:
                restarted = call('ping', {}, home)
                self.assertNotEqual(restarted['pid'], owner['pid'])
                self.assertTrue(restarted['compatible'], restarted)
            finally:
                call('shutdown', {}, home)

    def test_a_configurator_never_serves_under_another_installations_record(self):
        import json
        from generative_driver import daemon
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / 'service.json').write_text(json.dumps({'family': 'AF_UNIX', 'address': str(home / 'owner.sock'),
                                                            'authkey': '00' * 32, 'code': 'other'}), encoding='utf-8')
            # Started by a client that loaded other code than this configurator imports: it fails at startup, loudly,
            # instead of serving clients that record a digest it does not run.
            with self.assertRaisesRegex(RuntimeError, 'other generative-driver code'):
                daemon.serve(home)
            self.assertFalse((home / 'runs.sqlite3').exists())

    def test_a_configurator_that_ended_is_started_again_even_when_its_process_id_was_reused(self):
        # Catches the configurator read as running from the bare process id service.json names: once it ended without
        # a shutdown (killed, or its machine restarted), that id may name another running process (as the system
        # reuses ids) or one this user may not signal, and no client could start the configurator again.
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            try:
                # This test's parent process stands in for an unrelated process that took over the ended one's id.
                ended = self.ended(home, pid=os.getppid())
                started = call('ping', {}, home)
                self.assertTrue(started['ok'], started)
                self.assertNotIn(started['pid'], (ended, os.getppid()))
            finally:
                call('shutdown', {}, home)

    def test_a_configurator_holds_its_home_from_before_its_record_names_it(self):
        # Catches a record that names a starting configurator whose lock is not held yet: a client that read it
        # meanwhile would take the configurator for one that ended.
        import json
        from generative_driver import client
        real, published = Path.write_text, []
        def write_text(path, data, *args, **kwargs):
            if path.name == 'service.json':
                published.append((json.loads(data).get('lock'), client.process_lock_held(path.parent / 'service.lock')))
            return real(path, data, *args, **kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            try:
                with patch.object(Path, 'write_text', write_text):
                    self.assertTrue(client.call('ping', {}, home)['ok'])
                self.assertEqual(published, [('service.lock', True)])
            finally:
                client.call('shutdown', {}, home)

    def test_a_configurator_never_starts_from_a_home_another_configurator_runs_from(self):
        # Catches a second configurator started on the home a running one serves (by hand, or by a client that took the
        # running one for ended): its startup recovery blocked the running one's runs as though it had restarted, before
        # it failed to listen where the running one listens, or, on Windows, listened beside it.
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            try:
                run = call('start', {'goal': 'Scripted run another configurator must leave alone', 'executor_config': {
                    'command': [sys.executable, '-c', 'import time; time.sleep(30)']}}, home)
                deadline = time.monotonic() + 5
                while call('status', run, home)['status'] != 'running':
                    self.assertLess(time.monotonic(), deadline, call('result', run, home))
                    time.sleep(.01)
                second = subprocess.run([sys.executable, '-m', 'generative_driver.daemon', '--home', str(home)],
                                        capture_output=True, text=True, timeout=30)
                status = call('status', run, home)
                self.assertEqual((status['status'], status['reason']), ('running', None), status)
                self.assertNotEqual(second.returncode, 0, second)
                self.assertIn(str(home / 'service.lock'), second.stderr)
            finally:
                call('shutdown', {}, home)

    def test_shutdown_drains_an_accepted_tool_before_confirming_exit(self):
        from generative_driver.client import call
        entered, release = threading.Event(), threading.Event()
        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(5)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'scripted source')
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        with tempfile.TemporaryDirectory() as home:
            tool_thread = stop_thread = None
            try:
                run = call('start', {'goal':'Scripted shutdown drain', 'executor_config':{
                    'command':[sys.executable, '-c', 'import time; time.sleep(30)']}}, home)
                deadline = time.monotonic() + 5
                assignments = []
                state = {}
                while time.monotonic() < deadline:
                    assignments = [e['data'] for e in call('events', run, home)['events']
                                   if e['kind'] == 'stage.assigned']
                    state = call('status', run, home)
                    if assignments and (state['status'], state['stage']) == ('running', 'acquire'):
                        break
                    time.sleep(.01)
                self.assertTrue(assignments, call('result', run, home))
                self.assertEqual((state.get('status'), state.get('stage')), ('running', 'acquire'), state)
                replies = {}
                def invoke():
                    replies['tool'] = call('tool', {**run, 'assignment_id':assignments[0]['id'],
                        'name':'acquire_datasheet', 'arguments':{
                            'url':f'http://127.0.0.1:{server.server_port}/fixture',
                            'expected_sha256':'0' * 64}}, home)
                tool_thread = threading.Thread(target=invoke)
                tool_thread.start()
                self.assertTrue(entered.wait(5), replies)
                stopped = threading.Event()
                def shutdown():
                    replies['shutdown'] = call('shutdown', {}, home)
                    stopped.set()
                stop_thread = threading.Thread(target=shutdown)
                stop_thread.start()
                self.assertFalse(stopped.wait(.3), 'shutdown returned with an accepted tool still running')
                release.set()
                stop_thread.join(10)
                tool_thread.join(10)
                self.assertFalse(stop_thread.is_alive())
                self.assertFalse(tool_thread.is_alive())
                self.assertTrue(replies['shutdown'].get('stopped'), replies)
                self.assertIn('_exit', replies['tool'], replies)
            finally:
                release.set()
                if tool_thread:
                    tool_thread.join(10)
                if stop_thread:
                    stop_thread.join(10)
                call('shutdown', {}, home)
                server.shutdown()
                server_thread.join(5)
                server.server_close()

    def test_cancel_settles_an_idle_worker_before_an_immediate_resume(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            ready = home / 'worker.pid'
            program = ('import os,sys,time; from pathlib import Path; '
                       'p=Path(sys.argv[1]); t=p.with_suffix(".tmp"); '
                       't.write_text(str(os.getpid())); t.replace(p); time.sleep(30)')
            try:
                run = call('start', {'goal':'Scripted cancellation lifecycle',
                    'executor_config':{'command':[sys.executable, '-c', program, str(ready)]}}, home)
            except Exception as exc:
                log = home / 'daemon.log'
                self.fail(f'{exc}\nFresh fixture daemon log:\n' +
                          (log.read_text(encoding='utf-8') if log.exists() else '(not created)'))
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), call('result', run, home))
                pid = int(ready.read_text())
                cancelled = call('cancel', run, home)
                self.assertEqual(cancelled['status'], 'cancelled', cancelled)
                self.assertFalse(process_alive(pid), 'cancel returned before its idle worker exited')
                result = call('result', run, home)
                self.assertEqual(len(result['worker_reports']), 1, result)
                resumed = call('resume', run, home)
                self.assertTrue(resumed['ok'], resumed)
                self.assertIn(resumed['status'], ('queued', 'running'))
            finally:
                call('shutdown', {}, home)

    def test_shutdown_returns_only_after_daemon_exit_and_home_is_releasable(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'service'
            started = call('ping', {}, home)
            try:
                stopped = call('shutdown', {}, home)
                self.assertTrue(stopped.get('stopped'), stopped)
                self.assertFalse(process_alive(started['pid']), 'shutdown acknowledged while daemon still lives')
                self.assertTrue(call('shutdown', {}, home).get('stopped'))
                shutil.rmtree(home)
                self.assertFalse(home.exists())
            finally:
                if home.exists():
                    call('shutdown', {}, home)


if __name__ == '__main__':
    unittest.main()

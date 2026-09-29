"""Public process lifecycle contracts; scripted workers, no inference or hardware."""
import ctypes
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
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
                while not assignments and time.monotonic() < deadline:
                    assignments = [e['data'] for e in call('events', run, home)['events']
                                   if e['kind'] == 'stage.assigned']
                    if not assignments:
                        time.sleep(.01)
                self.assertTrue(assignments, call('result', run, home))
                replies = {}
                def invoke():
                    replies['tool'] = call('tool', {**run, 'assignment_id':assignments[0]['id'],
                        'name':'acquire_datasheet', 'arguments':{
                            'url':f'http://127.0.0.1:{server.server_port}/fixture',
                            'expected_sha256':'0' * 64}}, home)
                tool_thread = threading.Thread(target=invoke)
                tool_thread.start()
                self.assertTrue(entered.wait(5))
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
            run = call('start', {'goal':'Scripted cancellation lifecycle',
                'executor_config':{'command':[sys.executable, '-c', program, str(ready)]}}, home)
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

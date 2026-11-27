"""Scripted IPC peer exercising the public client after a lost reply."""
import json
from multiprocessing.connection import Listener
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest


class DeviceClientTests(unittest.TestCase):
    def test_lost_reply_does_not_automatically_resubmit_a_device_invocation(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
            address = (r'\\.\pipe\gd-test-'+secrets.token_hex(8)) if os.name == 'nt' else str(home/'peer.sock')
            key = secrets.token_bytes(32)
            received = []
            stop = threading.Event()
            with Listener(address, family=family, authkey=key) as listener:
                (home/'service.json').write_text(json.dumps({
                    'address': address, 'family': family, 'authkey': key.hex()}))

                def peer():
                    # Stop only after answering a connection, so the cleanup ping always gets a reply.
                    while True:
                        with listener.accept() as connection:
                            request = json.loads(connection.recv_bytes())
                            if request['method'] == 'device_execute':
                                received.append(request)
                                if len(received) == 1:
                                    continue  # Request accepted, response lost.
                            connection.send_bytes(b'{"ok":true}')
                        if stop.is_set():
                            return

                thread = threading.Thread(target=peer, daemon=True)
                thread.start()
                try:
                    result = call('device_execute', {'session_id': 'saved-session',
                        'operation': 'write', 'request_id': 'one-dispatch'}, home)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(result.get('error', {}).get('code'), 'invocation_outcome_unknown')
                    self.assertEqual(result.get('request_id'), 'one-dispatch')
                    self.assertEqual(len(received), 1)
                finally:
                    stop.set()
                    call('ping', home=home, autostart=False)
                    thread.join(5)

    def test_03_B8_daemon_thread_start_failure_rolls_back_slot_and_requests(self):
        import time
        from unittest.mock import patch
        from generative_driver.client import _request, call, code_identity
        from generative_driver import daemon
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
            address = (r'\\.\pipe\gd-slot-' + secrets.token_hex(8)
                       if os.name == 'nt' else str(home / 'owner.sock'))
            key = secrets.token_bytes(32)
            (home / 'service.json').write_text(json.dumps({
                'address': address, 'family': family, 'authkey': key.hex(), 'code': code_identity(),
                'pid': os.getpid()}), encoding='utf-8')
            srv = threading.Thread(target=daemon.serve, args=(home,), daemon=True)
            srv.start()
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if call('ping', home=home, autostart=False).get('ok'):
                    break
                time.sleep(.02)
            try:
                real_start = threading.Thread.start
                failed_once = []
                def fail_one_request(t):
                    if not failed_once and getattr(t, '_target', None) is not None and t._target.__name__ in ('dispatch', 'handle'):
                        failed_once.append(True)
                        raise RuntimeError("can't start new thread")
                    return real_start(t)
                with patch.object(threading.Thread, 'start', fail_one_request):
                    first = call('status', {'run_id': 'no-such-run'}, home=home, autostart=False)
                self.assertFalse(first['ok'])
                self.assertEqual(failed_once, [True])
                # Daemon still answers and shuts down cleanly (no leaked slot or dangling requests entry).
                self.assertTrue(call('ping', home=home, autostart=False).get('ok'))
                shut = _request(home, 'shutdown', {})
                self.assertTrue(shut.get('stopped'), shut)
            finally:
                try:
                    _request(home, 'shutdown', {})
                except (OSError, EOFError, ValueError):
                    pass
                srv.join(5)
                self.assertFalse(srv.is_alive())

    @unittest.skipIf(os.name == 'nt', 'AF_UNIX raw socket handshake stall test')
    def test_03_B9_stalled_handshake_never_blocks_daemon_accept_loop_or_exceeds_client_deadline(self):
        import socket
        import time
        from generative_driver.client import _request, call, code_identity
        from generative_driver import daemon
        # Part 1 (core-5): A raw socket that connects to daemon and stalls in handshake must NOT block accept loop.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            address = str(home / 'owner.sock')
            key = secrets.token_bytes(32)
            (home / 'service.json').write_text(json.dumps({
                'address': address, 'family': 'AF_UNIX', 'authkey': key.hex(), 'code': code_identity(),
                'pid': os.getpid()}), encoding='utf-8')
            srv = threading.Thread(target=daemon.serve, args=(home,), daemon=True)
            srv.start()
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if call('ping', home=home, autostart=False).get('ok'):
                    break
                time.sleep(.02)
            stalled = socket.socket(socket.AF_UNIX)
            try:
                stalled.connect(address)
                started = time.monotonic()
                ping = call('ping', home=home, autostart=False, read_timeout=1.0)
                self.assertTrue(ping.get('ok'), ping)
                self.assertLess(time.monotonic() - started, 1.0, 'stalled handshake blocked daemon accept loop')
                shut = _request(home, 'shutdown', {})
                self.assertTrue(shut.get('stopped'), shut)
            finally:
                stalled.close()
                srv.join(5)
                self.assertFalse(srv.is_alive())

        # Part 2 (device-3): Client connect+handshake+recv bounded under one deadline and never resends.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            address = str(home / 'stall.sock')
            key = secrets.token_bytes(32)
            (home / 'service.json').write_text(json.dumps({
                'address': address, 'family': 'AF_UNIX', 'authkey': key.hex(), 'code': code_identity(),
                'pid': os.getpid()}), encoding='utf-8')
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(address)
            sock.listen(4)
            accepted, done = [], threading.Event()
            def stall_listener():
                sock.settimeout(0.2)
                while not done.is_set():
                    try:
                        c, _ = sock.accept()
                        accepted.append(c)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
            th = threading.Thread(target=stall_listener, daemon=True)
            th.start()
            try:
                started = time.monotonic()
                res = call('result', {'run_id': 'r1'}, home=home, autostart=False, read_timeout=0.2)
                self.assertFalse(res['ok'], res)
                self.assertLess(time.monotonic() - started, 1.0, 'stalled handshake exceeded read_timeout')
                self.assertEqual(len(accepted), 1, 'client resent after handshake timeout')
            finally:
                done.set()
                for c in accepted:
                    c.close()
                sock.close()
                th.join(2)

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

    @unittest.skipIf(os.name == 'nt', 'POSIX socketpair partial-frame deadline test')
    def test_ipc_partial_frame_recv_and_send_enforce_total_deadline(self):
        import socket
        import struct
        import time
        from multiprocessing.connection import Connection
        from generative_driver._ipc import TimedConnection

        # 1. Slow-drip receive: 1 byte every 0.05s for a 200-byte frame must time out at total deadline (~0.25s).
        s1, s2 = socket.socketpair()
        conn = Connection(s1.detach())
        stop = threading.Event()
        def drip_sender():
            try:
                s2.sendall(struct.pack('!i', 200))
                for _ in range(200):
                    if stop.is_set():
                        return
                    s2.sendall(b'a')
                    time.sleep(0.05)
            except OSError:
                pass
        t = threading.Thread(target=drip_sender, daemon=True)
        t.start()
        try:
            started = time.monotonic()
            timed = TimedConnection(conn, started + 0.25, family='AF_UNIX')
            with self.assertRaises(TimeoutError):
                timed.recv_bytes(4096)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.9, f'slow-drip recv exceeded total deadline: {elapsed:.2f}s')
        finally:
            stop.set()
            conn.close()
            s2.close()
            t.join(2)

        # 2. Slow-draining send: 2 MiB payload while peer drains 1 KiB every 0.05s must time out at deadline (~0.25s).
        s1, s2 = socket.socketpair()
        for sock in (s1, s2):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        conn = Connection(s1.detach())
        stop2 = threading.Event()
        def slow_reader():
            s2.settimeout(0.1)
            while not stop2.is_set():
                try:
                    data = s2.recv(1024)
                    if not data:
                        return
                except socket.timeout:
                    continue
                except OSError:
                    return
                time.sleep(0.05)
        t2 = threading.Thread(target=slow_reader, daemon=True)
        t2.start()
        try:
            started = time.monotonic()
            timed = TimedConnection(conn, started + 0.25, family='AF_UNIX')
            with self.assertRaises(TimeoutError):
                timed.send_bytes(b'x' * (2 * 1024 * 1024))
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.9, f'slow-drain send exceeded total deadline: {elapsed:.2f}s')
        finally:
            stop2.set()
            conn.close()
            s2.close()
            t2.join(2)

        # 3. Normal complete frames (small and >16 KiB), maxlength enforcement, and EOF semantics stay intact.
        s1, s2 = socket.socketpair()
        c1, c2 = Connection(s1.detach()), Connection(s2.detach())
        try:
            for payload in (b'', b'hello', b'Z' * 100_000):
                deadline = time.monotonic() + 5
                t_send = threading.Thread(target=lambda p=payload: TimedConnection(c1, deadline, 'AF_UNIX').send_bytes(p),
                                          daemon=True)
                t_send.start()
                received = TimedConnection(c2, deadline, 'AF_UNIX').recv_bytes(200_000)
                t_send.join(5)
                self.assertEqual(received, payload)
            # Oversized frame rejected before reading body.
            t_big = threading.Thread(target=lambda: c1.send_bytes(b'B' * 500), daemon=True)
            t_big.start()
            with self.assertRaises(OSError):
                TimedConnection(c2, time.monotonic() + 5, 'AF_UNIX').recv_bytes(100)
            t_big.join(2)
        finally:
            c1.close()
            c2.close()

        s1, s2 = socket.socketpair()
        c1, c2 = Connection(s1.detach()), Connection(s2.detach())
        try:
            c1.close()
            with self.assertRaises(EOFError):
                TimedConnection(c2, time.monotonic() + 2, 'AF_UNIX').recv_bytes(1024)
        finally:
            c2.close()

    def test_windows_pipe_overlapped_write_and_read_cancel_on_timeout(self):
        import time
        from unittest.mock import patch
        from generative_driver import _ipc

        class FakeWinApi:
            ERROR_IO_PENDING = 997
            ERROR_OPERATION_ABORTED = 995
            ERROR_MORE_DATA = 234
            ERROR_BROKEN_PIPE = 109
            WAIT_OBJECT_0 = 0
            WAIT_TIMEOUT = 258

            def __init__(self, *, wait_result, write_err=997, res_err=995, nwritten=0,
                         read_chunks=(), read_wait_result=0):
                self.wait_result = wait_result
                self.write_err = write_err
                self.res_err = res_err
                self.nwritten = nwritten
                self.read_chunks = list(read_chunks)
                self.read_wait_result = read_wait_result
                self.cancelled = 0
                self.results_collected = 0
                self.wait_timeouts = []

            def _make_ov(self, data=b'', err=0, n=0):
                api = self
                class FakeOv:
                    event = 1234
                    def cancel(self_ov):
                        api.cancelled += 1
                    def GetOverlappedResult(self_ov, wait):
                        assert wait is True
                        api.results_collected += 1
                        return (n, err)
                    def getbuffer(self_ov):
                        return memoryview(data)
                return FakeOv()

            def WriteFile(self, handle, data, overlapped=False):
                assert overlapped is True
                return self._make_ov(data, self.res_err, self.nwritten), self.write_err

            def WaitForMultipleObjects(self, handles, wait_all, timeout_ms):
                self.wait_timeouts.append(timeout_ms)
                if isinstance(self.wait_result, BaseException):
                    raise self.wait_result
                return self.wait_result

            def ReadFile(self, handle, size, overlapped=False):
                assert overlapped is True
                chunk, err = self.read_chunks.pop(0)
                return self._make_ov(chunk, err, len(chunk)), (self.ERROR_IO_PENDING if self.read_wait_result == self.WAIT_TIMEOUT else err)

            def PeekNamedPipe(self, handle):
                nxt = len(self.read_chunks[0][0]) if self.read_chunks else 0
                return (nxt, nxt)

        class FakePipeConn:
            def __init__(self):
                self._handle = 42
                self._readable = True
                self._writable = True
                self._send_ov = None
                self._got_empty_message = False
                self.bad_len = False

            def _check_closed(self):
                if self._handle is None:
                    raise OSError('handle is closed')

            def _check_writable(self):
                if not self._writable:
                    raise OSError('not writable')

            def _check_readable(self):
                if not self._readable:
                    raise OSError('not readable')

            def fileno(self):
                self._check_closed()
                return self._handle

            def _bad_message_length(self):
                self.bad_len = True
                raise OSError('bad message length')

        # Timeout on pending WriteFile cancels overlapped op, collects GetOverlappedResult, clears _send_ov.
        api = FakeWinApi(wait_result=FakeWinApi.WAIT_TIMEOUT, res_err=FakeWinApi.ERROR_OPERATION_ABORTED, nwritten=0)
        conn = FakePipeConn()
        with patch.object(_ipc, '_winapi', api), patch.object(_ipc, '_PipeConnection', FakePipeConn):
            timed = _ipc.TimedConnection(conn, time.monotonic() + 0.05, family='AF_PIPE')
            with self.assertRaises(TimeoutError):
                timed.send_bytes(b'payload')
        self.assertEqual(api.cancelled, 1)
        self.assertEqual(api.results_collected, 1)
        self.assertIsNone(conn._send_ov)

        # Normal pending WriteFile completes before deadline without cancelling.
        api_ok = FakeWinApi(wait_result=FakeWinApi.WAIT_OBJECT_0, res_err=0, nwritten=7)
        conn_ok = FakePipeConn()
        with patch.object(_ipc, '_winapi', api_ok), patch.object(_ipc, '_PipeConnection', FakePipeConn):
            _ipc.TimedConnection(conn_ok, time.monotonic() + 2.0, family='AF_PIPE').send_bytes(b'payload')
        self.assertEqual(api_ok.cancelled, 0)
        self.assertEqual(api_ok.results_collected, 1)
        self.assertIsNone(conn_ok._send_ov)

        # KeyboardInterrupt during WaitForMultipleObjects cancels and collects overlapped result.
        api_int = FakeWinApi(wait_result=KeyboardInterrupt('stop'), res_err=FakeWinApi.ERROR_OPERATION_ABORTED, nwritten=0)
        conn_int = FakePipeConn()
        with patch.object(_ipc, '_winapi', api_int), patch.object(_ipc, '_PipeConnection', FakePipeConn):
            with self.assertRaises(KeyboardInterrupt):
                _ipc.TimedConnection(conn_int, time.monotonic() + 2.0, family='AF_PIPE').send_bytes(b'payload')
        self.assertEqual(api_int.cancelled, 1)
        self.assertEqual(api_int.results_collected, 1)
        self.assertIsNone(conn_int._send_ov)

        # Multi-chunk ERROR_MORE_DATA read succeeds and enforces maxlength.
        api_rd = FakeWinApi(wait_result=FakeWinApi.WAIT_OBJECT_0,
                            read_chunks=[(b'hello ', FakeWinApi.ERROR_MORE_DATA), (b'world', 0)])
        conn_rd = FakePipeConn()
        with patch.object(_ipc, '_winapi', api_rd), patch.object(_ipc, '_PipeConnection', FakePipeConn):
            got = _ipc.TimedConnection(conn_rd, time.monotonic() + 2.0, family='AF_PIPE').recv_bytes(64)
        self.assertEqual(got, b'hello world')

        api_big = FakeWinApi(wait_result=FakeWinApi.WAIT_OBJECT_0,
                             read_chunks=[(b'hello ', FakeWinApi.ERROR_MORE_DATA), (b'world!', 0)])
        conn_big = FakePipeConn()
        with patch.object(_ipc, '_winapi', api_big), patch.object(_ipc, '_PipeConnection', FakePipeConn):
            with self.assertRaises(OSError):
                _ipc.TimedConnection(conn_big, time.monotonic() + 2.0, family='AF_PIPE').recv_bytes(8)
        self.assertTrue(conn_big.bad_len)

    @unittest.skipUnless(os.name == 'nt', 'Windows AF_PIPE real overlapped write timeout test')
    def test_windows_real_pipe_write_times_out_and_cancels_when_peer_stops_reading(self):
        import time
        from generative_driver._ipc import TimedConnection, open_connection
        address = r'\\.\pipe\gd-write-timeout-' + secrets.token_hex(8)
        key = secrets.token_bytes(32)
        accepted = []
        ready = threading.Event()
        release = threading.Event()
        with Listener(address, family='AF_PIPE', authkey=key) as listener:
            def peer():
                conn = listener.accept()
                accepted.append(conn)
                ready.set()
                release.wait(10)
                conn.close()
            t = threading.Thread(target=peer, daemon=True)
            t.start()
            try:
                deadline = time.monotonic() + 5
                with open_connection(address, 'AF_PIPE', key, deadline) as client_conn:
                    self.assertTrue(ready.wait(5))
                    started = time.monotonic()
                    # 512 KiB exceeds Windows named pipe buffer so WriteFile blocks pending without a reader.
                    timed = TimedConnection(client_conn, started + 0.25, family='AF_PIPE')
                    with self.assertRaises(TimeoutError):
                        timed.send_bytes(b'X' * (512 * 1024))
                    elapsed = time.monotonic() - started
                    self.assertLess(elapsed, 1.5, f'Windows pipe write exceeded deadline: {elapsed:.2f}s')
                    self.assertFalse(accepted[0].poll(0.1), 'cancelled pipe write still delivered bytes to peer')
            finally:
                release.set()
                t.join(5)

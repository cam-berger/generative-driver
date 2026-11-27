"""The hand-written device of the tool's tests ends when it is closed, whatever it is doing.

One hosted job once waited for ever in `SuppliedDevice.close()`:
the serving thread had just answered a request, saw that the device was closed and ended without accepting again,
and `close()` waited in the handshake of its wake-up connection, which nobody accepted. The first test forces that
order at the device's own lock.

Temporary folders only; no model, no hardware and no password.
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from generative_driver.device_client import DeviceClient, DeviceUnavailable
from supplied_device import SuppliedDevice


class Gate:
    """The device's lock for one test: it says when the serving thread has reached it, inside a request, and holds
    the thread there until `open` is set."""

    def __init__(self):
        self.reached, self.open = threading.Event(), threading.Event()

    def __enter__(self):
        self.reached.set()
        self.open.wait(30)

    def __exit__(self, *error):
        return False


def waited(condition, seconds=5):
    """Whether `condition()` became true within `seconds`."""
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() >= end:
            return False
        time.sleep(.01)
    return True


class CloseTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)

    def test_12_B10_close_returns_within_ten_seconds_when_the_serving_thread_ends_before_the_wake_up(self):
        device = SuppliedDevice(Path(self.root.name) / 'device')
        client, answers, gate = DeviceClient(device.endpoint_file, timeout=20), [], Gate()
        device._lock = gate
        self.addCleanup(gate.open.set)
        asked = threading.Thread(target=lambda: answers.append(client.status()), daemon=True)
        asked.start()
        self.assertTrue(gate.reached.wait(10), 'the device did not take the request')
        began = time.monotonic()
        closing = threading.Thread(target=device.close, daemon=True)
        closing.start()
        self.assertTrue(waited(lambda: device._closed))
        # `close()` gets time to reach its wake-up. Then the request is let go: the serving thread answers it, sees
        # that the device is closed and ends without accepting again.
        time.sleep(.5)
        gate.open.set()
        asked.join(10)
        closing.join(10)
        self.assertFalse(asked.is_alive())
        self.assertEqual([answer['healthy'] for answer in answers], [True])
        self.assertEqual(device.sent('status'), [{}])
        self.assertFalse(closing.is_alive(), 'close() still waits 10 seconds after the request was answered')
        self.assertLess(time.monotonic() - began, 10)
        self.assertTrue(waited(lambda: not device._thread.is_alive()), 'the serving thread still runs')

    def test_12_B10_a_device_closed_while_idle_answers_nothing_afterwards_and_leaves_no_thread_serving(self):
        device = SuppliedDevice(Path(self.root.name) / 'device')
        self.assertTrue(DeviceClient(device.endpoint_file, timeout=5).status()['healthy'])
        endpoint = device.endpoint_file.read_text(encoding='utf-8')
        began = time.monotonic()
        device.close()
        self.assertLess(time.monotonic() - began, 10)
        self.assertFalse(device.endpoint_file.exists())
        self.assertTrue(waited(lambda: not device._thread.is_alive()), 'the serving thread still runs')
        # A client that kept the endpoint's address and key reaches nothing either.
        kept = Path(self.root.name) / 'kept.json'
        kept.write_text(endpoint, encoding='utf-8')
        with self.assertRaises(DeviceUnavailable):
            DeviceClient(kept, timeout=5).status()
        self.assertEqual(device.sent('status'), [{}])
        device.close()

    def test_03_B4_window_pauses_device_when_set_running_true_reply_is_lost_and_timed_out_call_is_never_sent(self):
        import json
        import os
        import secrets
        import socket
        from multiprocessing.connection import Listener
        # Docstring must not falsely claim nothing reached the device when a reply is lost.
        self.assertNotIn('nothing reached the device', DeviceUnavailable.__doc__ or '')
        # Part 1: lost reply to set_running(True) must issue a best-effort set_running(False) so device is not left running.
        root = Path(self.root.name).resolve()
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        address = (r'\\.\pipe\gd-dev-lost-' + secrets.token_hex(8)
                   if os.name == 'nt' else str(root / 'lost.sock'))
        key = secrets.token_bytes(32)
        endpoint = root / 'lost.json'
        endpoint.write_text(json.dumps({'address': address, 'family': family, 'authkey': key.hex()}), encoding='utf-8')
        seen, running, stop = [], [False], threading.Event()
        with Listener(address, family=family, authkey=key) as listener:
            def peer():
                while True:
                    with listener.accept() as conn:
                        req = json.loads(conn.recv_bytes(1024 * 1024))
                        method, params = req['method'], req.get('params', {})
                        if method == 'set_running':
                            seen.append(params['running'])
                            running[0] = params['running']
                            if len(seen) == 1:
                                continue  # Drop reply after starting the device!
                        conn.send_bytes(json.dumps({'ok': True, 'result': {'running': running[0]}}).encode())
                    if stop.is_set():
                        return
            th = threading.Thread(target=peer, daemon=True)
            th.start()
            try:
                client = DeviceClient(endpoint, timeout=2)
                with self.assertRaises(DeviceUnavailable):
                    with client.window():
                        pass
                self.assertEqual(seen, [True, False], 'window() did not pause device after lost set_running(True) reply')
                self.assertFalse(running[0])
            finally:
                stop.set()
                try:
                    DeviceClient(endpoint, timeout=1).status()
                except DeviceUnavailable:
                    pass
                th.join(5)

        # Part 2: stalled handshake times out within client.timeout AND timed-out request is NEVER sent later.
        if os.name != 'nt':
            stall_addr = str(root / 'stall.sock')
            stall_ep = root / 'stall.json'
            stall_ep.write_text(json.dumps({'address': stall_addr, 'family': 'AF_UNIX', 'authkey': key.hex()}),
                                encoding='utf-8')
            srv = socket.socket(socket.AF_UNIX)
            srv.bind(stall_addr)
            srv.listen(4)
            received = []
            unblock = threading.Event()
            def stall_peer():
                conn, _ = srv.accept()
                try:
                    unblock.wait(5)
                    conn.settimeout(0.2)
                    try:
                        data = conn.recv(4096)
                        if data:
                            received.append(data)
                    except OSError:
                        pass
                finally:
                    conn.close()
            th2 = threading.Thread(target=stall_peer, daemon=True)
            th2.start()
            try:
                started = time.monotonic()
                with self.assertRaises(DeviceUnavailable):
                    DeviceClient(stall_ep, timeout=0.2)._call('set_running', running=True)
                self.assertLess(time.monotonic() - started, 1.0)
                unblock.set()
                th2.join(5)
                self.assertEqual(received, [], 'timed-out request was sent after deadline')
            finally:
                unblock.set()
                srv.close()


if __name__ == '__main__':
    unittest.main()

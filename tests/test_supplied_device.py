"""The hand-written device of the tool's tests ends when it is closed, whatever it is doing.

Feature 12 of the untangle Run, 12-B10. One hosted job of Feature 11 waited for ever in `SuppliedDevice.close()`:
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


if __name__ == '__main__':
    unittest.main()

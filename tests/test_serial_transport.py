"""UART identity resolution and opening, with a fake pyserial so no hardware or OS port is needed."""
import sys
import types
import unittest
from unittest.mock import patch

from interface_runtime.transports import TransportError, open_transport, resolve_serial_port

CHANNEL = {"type": "uart", "baudrate": 115200}


def port(device, vid=0x10C4, pid=0xEA60, serial_number="abc"):
    return types.SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial_number)


class FakeSerial:
    instances = []

    def __init__(self, *args, **kwargs):
        assert not args and not kwargs, "open must be deferred until control lines are released"
        self.events, self.chunks, self.timeout, self.write_timeout = [], [], 0, 0
        FakeSerial.instances.append(self)

    def __setattr__(self, name, value):
        if name in ("dtr", "rts", "port", "baudrate", "bytesize", "parity", "stopbits") and hasattr(self, "events"):
            self.events.append((name, value))
        object.__setattr__(self, name, value)

    def open(self):
        self.events.append(("open", None))

    def reset_input_buffer(self):
        self.events.append(("flush", None))

    @property
    def in_waiting(self):
        return sum(len(c) for c in self.chunks)

    def read(self, size=1):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:]); chunk = chunk[:size]
        return chunk

    def close(self):
        self.events.append(("close", None))


def fake_serial_module():
    module = types.ModuleType("serial")
    module.Serial = FakeSerial
    return module


class SerialTransportTests(unittest.TestCase):
    def test_identity_resolves_current_path(self):
        ports = [port("/dev/cu.other", vid=1, pid=2), port("/dev/cu.usbserial-11140")]
        self.assertEqual(resolve_serial_port({"vendor_id": 0x10C4, "product_id": 0xEA60}, ports), "/dev/cu.usbserial-11140")
        self.assertEqual(resolve_serial_port({"vendor_id": 0x10C4, "product_id": 0xEA60, "serial_number": "abc"}, ports),
                         "/dev/cu.usbserial-11140")

    def test_missing_or_ambiguous_identity_is_operator_fault(self):
        for ports in ([], [port("/dev/a"), port("/dev/b", serial_number="def")]):
            with self.assertRaises(TransportError) as raised:
                resolve_serial_port({"vendor_id": 0x10C4, "product_id": 0xEA60}, ports)
            self.assertEqual((raised.exception.code, raised.exception.fault), ("binding", "operator"))

    def test_open_releases_control_lines_before_opening_and_flushes(self):
        FakeSerial.instances.clear()
        with patch.dict(sys.modules, {"serial": fake_serial_module()}):
            transport = open_transport(CHANNEL, {"port": "/dev/cu.test"})
        events = [name for name, _ in FakeSerial.instances[0].events]
        self.assertLess(events.index("dtr"), events.index("open"))
        self.assertLess(events.index("rts"), events.index("open"))
        self.assertLess(events.index("open"), events.index("flush"))
        transport.close()

    def test_stream_boot_wait_discards_through_boot_line(self):
        FakeSerial.instances.clear()
        with patch.dict(sys.modules, {"serial": fake_serial_module()}):
            transport = open_transport(CHANNEL, {"port": "/dev/cu.test"})
        FakeSerial.instances[0].chunks = [b"ESP-ROM\r\nnoise\n@ boot ok\nREST"]
        self.assertEqual(transport.wait_boot("@ boot", 500), len(b"ESP-ROM\r\nnoise\n@ boot ok\n"))
        self.assertEqual(bytes(transport.buffer), b"REST")
        FakeSerial.instances[0].chunks = [b"no boot line here\n"]
        with self.assertRaises(TransportError) as raised:
            transport.wait_boot("@ boot", 50)
        self.assertEqual(raised.exception.code, "boot_timeout")
        self.assertIn("discarded", str(raised.exception))
        transport.close()


if __name__ == "__main__":
    unittest.main()

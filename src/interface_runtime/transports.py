"""Bounded adapters for explicit, trusted host bindings.

USB binding flags ``allow_set_configuration`` and
``allow_detach_kernel_driver`` default to False. Detachment is limited to the
selected interface and is reversed on close. No alternate setting is changed.
Error codes separate who can fix a failure: ``binding`` is the operator's
selection (malformed fields, no unique device, a permission not granted) and
``channel`` is the model's channel (an interface, endpoint, transfer type or
packet size the device's descriptors do not have).
USB control payload/receive lengths are limited to the 16-bit wLength maximum
of 65535 bytes (other transaction byte limits remain 65536). A USB bulk or
interrupt IN transfer ends on a short packet or a full buffer, so receive
bounds are checked against the IN endpoint's wMaxPacketSize before any I/O;
on open, stale IN packets left by an abandoned transaction are drained and
counted in ``drained_bytes``.
FTDI workers check the handle and deadline immediately before backend entry;
workers delayed until after expiry do not submit a new transaction. Already
submitted hardware work cannot be cancelled, and cleanup waits for its worker.
"""
import errno
import importlib
import math
import socket
import threading
import time

from . import checks
from .faults import Fault

MAX_BYTES = 65536
# Standard requests that change the device's address, configuration or alternate setting: the host owns them.
HOST_OWNED_REQUESTS = frozenset({5, 9, 11})
DRAIN_READS = 8
DRAIN_TIMEOUT_MS = 10


class TransportError(OSError):
    def __init__(self, code, message, fault=None):
        super().__init__(message)
        self.code = code
        self.fault = fault.value if isinstance(fault, Fault) else fault


def _fail(message, code='invalid', fault=None):
    raise TransportError(code, message, fault)


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        _fail(f'{name} must be an integer in {low}..{high}')
    return value


def _fields(value, allowed, required, name):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        _fail(f'{name} has unknown or missing fields')


def _string(value, name):
    if not isinstance(value, str) or not value or '\0' in value:
        _fail(f'{name} must be a nonempty string without NUL')


def capabilities():
    return {kind: {'exchange_modes': modes, 'control': kind == 'usb'} for kind, modes in [
        ('uart', ['exact', 'until', 'up_to', 'lines', 'frame']), ('tcp', ['exact', 'until', 'up_to', 'lines', 'frame']),
        ('i2c', ['exact']), ('spi', ['exact']), ('usb', ['exact', 'up_to', 'frame'])]}


def validate_channel(channel):
    if not isinstance(channel, dict) or not isinstance(channel.get('type'), str):
        _fail('channel requires a type')
    kind = channel['type']
    fields = {'tcp': ([], []), 'uart': (['baudrate','bytesize','parity','stopbits'], ['baudrate']),
              'i2c': (['address_7bit','speed_hz'], ['address_7bit','speed_hz']),
              'spi': (['cs','clock_hz','mode','bit_order'], ['cs','clock_hz','mode','bit_order']),
              'usb': (['interface','in_endpoint','out_endpoint','transfer'], [])}
    if kind not in fields:
        _fail(f'unsupported channel {kind}', 'unsupported')
    allowed, required = fields[kind]
    _fields(channel, ['type'] + allowed, ['type'] + required, 'channel')
    if kind == 'uart':
        _integer(channel['baudrate'], 1, 12000000, 'baudrate')
        _integer(channel.get('bytesize', 8), 5, 8, 'bytesize')
        if channel.get('parity', 'N') not in ('N','E','O','M','S'):
            _fail('unsupported parity')
        stop = channel.get('stopbits', 1)
        if type(stop) not in (int, float) or stop not in (1, 1.5, 2):
            _fail('unsupported stopbits')
    elif kind == 'i2c':
        _integer(channel['address_7bit'], 0, 127, 'address_7bit')
        _integer(channel['speed_hz'], 1, 3400000, 'speed_hz')
    elif kind == 'spi':
        _integer(channel['cs'], 0, 4, 'cs')
        _integer(channel['clock_hz'], 1, 30000000, 'clock_hz')
        _integer(channel['mode'], 0, 3, 'mode')
        if channel['bit_order'] != 'msb_first':
            _fail('only msb_first SPI is supported', 'unsupported')
    elif kind == 'usb':
        _integer(channel.get('interface', 0), 0, 255, 'interface')
        if channel.get('transfer', 'bulk') not in ('bulk', 'interrupt'):
            _fail('unsupported USB transfer', 'unsupported')
        for field, direction in [('in_endpoint', 0x80), ('out_endpoint', 0)]:
            if field in channel:
                ep = _integer(channel[field], 1, 255, field)
                if (ep & 0x80) != direction or ep & 0x70 or not ep & 0x0f:
                    _fail(f'{field} has wrong endpoint direction/address')


def _request(tx, rx, timeout_ms, modes):
    if not isinstance(tx, bytes) or len(tx) > MAX_BYTES:
        _fail('tx must be bytes of length 0..65536')
    _integer(timeout_ms, 1, 60000, 'timeout_ms')
    if not isinstance(rx, dict) or rx.get('mode') not in modes:
        _fail('unsupported receive mode', 'unsupported')
    mode = rx['mode']
    if mode == 'frame':
        try:
            spec = checks.frame_spec(rx)
        except checks.CheckError as exc:
            _fail(str(exc))
        return mode, spec['max'], spec
    if mode == 'exact':
        _fields(rx, ['mode','length'], ['mode','length'], 'rx')
        bound = _integer(rx['length'], 0, MAX_BYTES, 'length')
        delimiter = None
    elif mode == 'lines':
        _fields(rx, ['mode','max_bytes','end','newline','max_lines'], ['mode','max_bytes','end'], 'rx')
        bound = _integer(rx['max_bytes'], 2, MAX_BYTES, 'max_bytes')
        end = rx['end']
        if (not isinstance(end, dict) or len(end) != 1 or next(iter(end)) not in ('line_equals','line_token','line_prefix')):
            _fail('end must hold exactly one of line_equals, line_token, line_prefix')
        value = next(iter(end.values()))
        if not isinstance(value, str) or not value or not value.isascii() or any(not 32 <= ord(c) < 127 for c in value):
            _fail('completion line form must be nonempty printable ASCII')
        if rx.get('newline', 'auto') not in ('auto','lf','crlf'):
            _fail('newline must be auto, lf or crlf')
        _integer(rx.get('max_lines', 4096), 1, 4096, 'max_lines')
        delimiter = None
    else:
        required = ['mode','max_bytes'] + (['delimiter_hex'] if mode == 'until' else [])
        _fields(rx, required, required, 'rx')
        bound = _integer(rx['max_bytes'], 1, MAX_BYTES, 'max_bytes')
        delimiter = None
        if mode == 'until':
            value = rx['delimiter_hex']
            if not isinstance(value, str) or len(value) % 2 or not value or any(c not in '0123456789abcdefABCDEF' for c in value):
                _fail('delimiter_hex must be nonempty hexadecimal bytes')
            delimiter = bytes.fromhex(value)
            if len(delimiter) > bound:
                _fail('delimiter exceeds max_bytes')
    return mode, bound, delimiter


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail('transaction deadline expired', 'timeout')
    return remaining


def _millis(deadline):
    return max(1, math.ceil(_remaining(deadline) * 1000))


def _timed_out(exc):
    """pyusb reports a libusb timeout as USBTimeoutError, an OSError that is not a TimeoutError."""
    return (isinstance(exc, TimeoutError) or getattr(exc, 'errno', None) == errno.ETIMEDOUT
            or any(cls.__name__ == 'USBTimeoutError' for cls in type(exc).__mro__))


def _dependency(name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise TransportError('dependency', f'optional dependency {name} is required') from exc


class _Handle:
    closed = False
    def _check(self):
        if self.closed:
            _fail('transport is closed', 'closed')
    def control(self, setup, tx, rx, timeout_ms):
        _fail('control is supported only by USB', 'unsupported')
    def _error(self, exc):
        self.close()
        if isinstance(exc, TransportError):
            raise exc
        raise TransportError('timeout' if _timed_out(exc) else 'io', str(exc)) from exc


class _Stream(_Handle):
    def __init__(self, handle, kind):
        self.handle, self.kind = handle, kind
        self.buffer = bytearray()
        self.closed = False
    def close(self):
        if not self.closed:
            self.closed = True
            self.handle.close()
    def _write(self, data, deadline):
        offset = 0
        while offset < len(data):
            remaining = _remaining(deadline)
            if self.kind == 'tcp':
                self.handle.settimeout(remaining)
                count = self.handle.send(data[offset:])
            else:
                self.handle.write_timeout = remaining
                count = self.handle.write(data[offset:])
            if not isinstance(count, int) or not 0 < count <= len(data) - offset:
                _fail('write made no progress or returned invalid length', 'io')
            offset += count
        _remaining(deadline)
    def _read(self, size, deadline):
        remaining = _remaining(deadline)
        if self.kind == 'tcp':
            self.handle.settimeout(remaining)
            data = self.handle.recv(size)
            if not data:
                _fail('stream disconnected before response completed', 'disconnect')
        else:
            self.handle.timeout = remaining
            # Read one blocking byte, then only bytes already available.
            data = self.handle.read(1)
            if data and size > 1:
                waiting = min(size - 1, self.handle.in_waiting)
                if waiting:
                    self.handle.timeout = 0
                    data += self.handle.read(waiting)
            if not data:
                _fail('serial response deadline expired', 'timeout')
        _remaining(deadline)
        if len(data) > size:
            _fail('oversized stream read', 'io')
        return data
    def _line_end(self, rx, bound):
        """Byte count through the first complete line satisfying rx['end'], else None.
        Framing violations and bound overruns fail the transaction."""
        end = rx['end']
        key, value = next(iter(end.items()))
        newline = rx.get('newline', 'auto')
        start = 0
        lines = 0
        while True:
            cut = self.buffer.find(b'\n', start)
            if cut < 0:
                if len(self.buffer) >= bound:
                    _fail('completion line not found within max_bytes', 'limit')
                return None
            raw = bytes(self.buffer[start:cut])
            had_cr = raw.endswith(b'\r')
            if newline == 'crlf' and not had_cr or newline == 'lf' and had_cr:
                _fail('reply line framing does not match the declared newline', 'framing')
            line = (raw[:-1] if had_cr else raw).decode('utf-8', 'replace')
            lines += 1
            if lines > rx.get('max_lines', 4096):
                _fail('line count exceeds max_lines', 'limit')
            words = line.split(None, 1)
            token = words[0] if words else ''
            if (key == 'line_equals' and line == value) or (key == 'line_token' and token == value) \
                    or (key == 'line_prefix' and line.startswith(value)):
                if cut + 1 > bound:
                    _fail('completion line exceeds max_bytes', 'limit')
                return cut + 1
            start = cut + 1
    def exchange(self, tx, rx, timeout_ms):
        mode, bound, delimiter = _request(tx, rx, timeout_ms, ('exact','until','up_to','lines','frame'))
        self._check()
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            self._write(tx, deadline)
            if bound == 0:
                return b''
            while True:
                if mode == 'lines':
                    count = self._line_end(rx, bound)
                    if count is not None:
                        break
                elif mode == 'frame':
                    # `delimiter` carries the frame spec here. No hunting: the reply must start with
                    # the sync bytes, and a bad length, trailer or check fails the transaction.
                    total = checks.frame_total(self.buffer, delimiter)
                    if total is not None and len(self.buffer) >= total:
                        count = total
                        checks.verify_frame(self.buffer[:count], delimiter)
                        break
                    if total is not None:
                        size = min(4096, total - len(self.buffer))
                        self.buffer.extend(self._read(size, deadline))
                        continue
                elif mode == 'until':
                    end = self.buffer.find(delimiter)
                    if end >= 0 and end + len(delimiter) <= bound:
                        count = end + len(delimiter)
                        break
                    if len(self.buffer) >= bound:
                        _fail('delimiter not found within max_bytes', 'limit')
                elif mode == 'exact' and len(self.buffer) >= bound:
                    count = bound
                    break
                elif mode == 'up_to' and self.buffer:
                    count = min(bound, len(self.buffer))
                    break
                # Read bounded chunks; preserve any bytes following a delimiter.
                size = min(4096, bound - len(self.buffer)) if mode != 'up_to' else bound
                self.buffer.extend(self._read(size, deadline))
            result = bytes(self.buffer[:count])
            del self.buffer[:count]
            return result
        except checks.FrameError as exc:
            self._error(TransportError(exc.code, str(exc)))
        except Exception as exc:
            self._error(exc)


class _USB(_Handle):
    def __init__(self, channel, binding):
        core, self.util = _dependency('usb.core'), _dependency('usb.util')
        self.device = None
        self.closed = False
        self.claimed = False
        self.detached = False
        self.drained_bytes = 0
        self.buffer = bytearray()
        self.packet_size = {}
        self.interface = channel.get('interface', 0)
        self.in_endpoint = channel.get('in_endpoint')
        self.out_endpoint = channel.get('out_endpoint')
        try:
            devices = list(core.find(find_all=True, idVendor=binding['vendor_id'], idProduct=binding['product_id']))
            if 'serial_number' in binding:
                devices = [dev for dev in devices if dev.serial_number == binding['serial_number']]
            if len(devices) != 1:
                _fail(f'USB binding matched {len(devices)} devices; require exactly one', 'binding', Fault.HOST)
            self.device = devices[0]
            try:
                config = self.device.get_active_configuration()
            except Exception:
                if not binding.get('allow_set_configuration', False):
                    _fail('USB has no active configuration; explicit allow_set_configuration required', 'binding', Fault.OPERATOR)
                self.device.set_configuration()
                config = self.device.get_active_configuration()
            interfaces = [item for item in config if item.bInterfaceNumber == self.interface and item.bAlternateSetting == 0]
            if len(interfaces) != 1:
                _fail(f'USB interface {self.interface} alternate setting 0 is not unique in the active configuration', 'channel')
            endpoints = {ep.bEndpointAddress: ep for ep in interfaces[0]}
            kind = 2 if channel.get('transfer', 'bulk') == 'bulk' else 3
            for address in (self.in_endpoint, self.out_endpoint):
                if address is not None and (address not in endpoints or endpoints[address].bmAttributes & 3 != kind):
                    _fail('USB endpoint descriptor does not match transfer type/address', 'channel')
                if address is not None:
                    # Bits 0-10 are the packet size; bits 11-12 count extra high-speed transactions.
                    self.packet_size[address] = endpoints[address].wMaxPacketSize & 0x7FF
                    if not self.packet_size[address]:
                        _fail('USB endpoint descriptor declares a zero packet size', 'channel')
            try:
                kernel = self.device.is_kernel_driver_active(self.interface)
            except NotImplementedError:
                kernel = False
            if kernel:
                if not binding.get('allow_detach_kernel_driver', False):
                    _fail('USB interface has a kernel driver; explicit detach permission required', 'binding', Fault.OPERATOR)
                self.device.detach_kernel_driver(self.interface)
                self.detached = True
            self.util.claim_interface(self.device, self.interface)
            self.claimed = True
            self.drained_bytes = self._drain()
        except Exception as exc:
            self._error(exc)
    @classmethod
    def _scripted(cls, device, channel):
        """The real adapter over a scripted device object, for the offline qualifier: no pyusb, descriptor
        lookup, claim or drain. Packet sizes are unknown offline, so the whole-packet rule is not applied
        there (size 1); it is enforced against the descriptor when a real device is opened."""
        self = cls.__new__(cls)
        self.util = type('_NoUtil', (), {'dispose_resources': staticmethod(lambda device: None)})
        self.device, self.closed, self.claimed, self.detached, self.drained_bytes = device, False, False, False, 0
        self.buffer = bytearray()
        self.interface = channel.get('interface', 0)
        self.in_endpoint, self.out_endpoint = channel.get('in_endpoint'), channel.get('out_endpoint')
        self.packet_size = {address: 1 for address in (self.in_endpoint, self.out_endpoint) if address is not None}
        return self
    def _drain(self):
        """Discard IN packets left by an earlier, abandoned transaction so that the first exchange reads
        its own reply: at most DRAIN_READS short reads, ending at the first timeout. Returns the count."""
        if self.in_endpoint is None:
            return 0
        drained = 0
        for _ in range(DRAIN_READS):
            try:
                drained += len(self.device.read(self.in_endpoint, self.packet_size[self.in_endpoint], timeout=DRAIN_TIMEOUT_MS))
            except Exception as exc:
                if not _timed_out(exc):
                    raise
                break
        return drained
    def _read_size(self, mode, bound):
        """Bulk and interrupt IN transfers end on a short packet or a full buffer. A buffer that is not a
        whole number of packets overflows when a full packet lands in its tail, so up_to bounds must be
        packet multiples and exact lengths are read in whole packets, then checked for their length."""
        size = self.packet_size[self.in_endpoint]
        if mode == 'up_to':
            if bound % size:
                _fail(f'USB up_to max_bytes {bound} must be a positive multiple of the IN endpoint packet size {size}')
            return bound
        whole = -(-bound // size) * size
        if whole > MAX_BYTES:
            _fail(f'USB exact length {bound} needs {whole} bytes of whole packets, above {MAX_BYTES}')
        return whole
    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.device is None:
            return
        for enabled, action in [(self.claimed, lambda: self.util.release_interface(self.device, self.interface)),
                                (self.detached, lambda: self.device.attach_kernel_driver(self.interface)),
                                (True, lambda: self.util.dispose_resources(self.device))]:
            if enabled:
                try:
                    action()
                except Exception:
                    pass
    def _response(self, data, mode, bound):
        result = bytes(data)
        if len(result) > bound or (mode == 'exact' and len(result) != bound):
            _fail('USB response length does not match receive limit', 'length')
        return result
    def exchange(self, tx, rx, timeout_ms):
        mode, bound, frame_spec = _request(tx, rx, timeout_ms, ('exact','up_to','frame'))
        self._check()
        if tx and self.out_endpoint is None or bound and self.in_endpoint is None:
            _fail('USB exchange requires explicit endpoints', 'channel')
        if mode == 'frame' and 'max_reads' not in rx:
            _fail('USB frame receive requires max_reads')
        if mode != 'frame' and self.buffer:
            _fail('USB buffered frame requires a framed receive before another exchange', 'framing')
        size = (self.packet_size[self.in_endpoint] if mode == 'frame' else
                self._read_size(mode, bound) if bound else 0)
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            if tx:
                count = self.device.write(self.out_endpoint, tx, timeout=_millis(deadline))
                if count != len(tx):
                    _fail('USB short write; delivery uncertain', 'length')
            if mode == 'frame':
                reads = 0
                while True:
                    total = checks.frame_total(self.buffer, frame_spec)
                    if total is not None and len(self.buffer) >= total:
                        result = checks.verify_frame(self.buffer[:total], frame_spec)
                        del self.buffer[:total]
                        break
                    if len(self.buffer) >= bound or reads >= rx['max_reads']:
                        _fail('USB frame did not complete within its byte/read bounds', 'limit')
                    chunk = bytes(self.device.read(self.in_endpoint, size, timeout=_millis(deadline)))
                    reads += 1
                    if len(chunk) > size:
                        _fail('USB frame read exceeds the requested packet size', 'io')
                    # A zero-length packet can terminate a preceding full-packet
                    # transfer. It carries no frame bytes but still consumes a
                    # read from the finite budget and is bounded by the deadline.
                    self.buffer.extend(chunk)
            else:
                result = self._response(self.device.read(self.in_endpoint, size, timeout=_millis(deadline)), mode, bound) if bound else b''
            _remaining(deadline)
            return result
        except checks.FrameError as exc:
            self._error(TransportError(exc.code, str(exc)))
        except Exception as exc:
            self._error(exc)
    def control(self, setup, tx, rx, timeout_ms):
        mode, bound, _ = _request(tx, rx, timeout_ms, ('exact','up_to'))
        if len(tx) > 65535 or bound > 65535:
            _fail('USB control length exceeds 16-bit wLength maximum 65535')
        fields = ['request_type','request','value','index']
        _fields(setup, fields, fields, 'setup')
        for key in fields:
            _integer(setup[key], 0, 255 if key in fields[:2] else 65535, key)
        incoming = bool(setup['request_type'] & 0x80)
        if incoming and tx or not incoming and bound:
            _fail('USB control direction disagrees with tx/rx lengths')
        if (setup['request_type'] & 0x60) == 0 and setup['request'] in HOST_OWNED_REQUESTS:
            _fail('USB standard SET_ADDRESS, SET_CONFIGURATION and SET_INTERFACE belong to the host, never a model')
        self._check()
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            data = self.device.ctrl_transfer(setup['request_type'], setup['request'], setup['value'], setup['index'], bound if incoming else tx, timeout=_millis(deadline))
            _remaining(deadline)
            if incoming:
                return self._response(data, mode, bound)
            if data != len(tx):
                _fail('USB control short write; delivery uncertain', 'length')
            return b''
        except Exception as exc:
            self._error(exc)


class _FTDI(_Handle):
    def __init__(self, channel, binding):
        self.kind = channel['type']
        self.closed = False
        self.controller = None
        self.active = None
        self.lock = threading.Lock()
        try:
            if self.kind == 'i2c':
                self.controller = _dependency('pyftdi.i2c').I2cController()
                self.controller.configure(binding['url'], frequency=channel['speed_hz'])
                self.controller.set_retry_count(1)
                self.port = self.controller.get_port(channel['address_7bit'])
            else:
                self.controller = _dependency('pyftdi.spi').SpiController(cs_count=channel['cs']+1)
                self.controller.configure(binding['url'])
                self.port = self.controller.get_port(cs=channel['cs'], freq=channel['clock_hz'], mode=channel['mode'])
        except Exception as exc:
            self._error(exc)
    def close(self):
        with self.lock:
            self.closed = True
            if self.active is None and self.controller is not None:
                controller, self.controller = self.controller, None
                controller.terminate()
    def exchange(self, tx, rx, timeout_ms):
        _, bound, _ = _request(tx, rx, timeout_ms, ('exact',))
        self._check()
        deadline = time.monotonic() + timeout_ms / 1000
        done = threading.Event()
        results = []
        def transact():
            try:
                kwargs = {'relax':True,'start':True} if self.kind == 'i2c' else {'start':True,'stop':True,'duplex':False}
                # The worker may first run after the caller has timed out.
                # Check here, rather than treating thread creation as submission.
                self._check()
                _remaining(deadline)
                if self.kind == 'i2c' and bound == 0:
                    self.port.write(tx, **kwargs)
                    results.append(b'')
                else:
                    results.append(bytes(self.port.exchange(tx, bound, **kwargs)))
            except Exception as exc:
                results.append(exc)
            finally:
                with self.lock:
                    self.active = None
                    if self.closed and self.controller is not None:
                        controller, self.controller = self.controller, None
                        try:
                            controller.terminate()
                        except Exception:
                            pass
                done.set()
        worker = threading.Thread(target=transact, daemon=True)
        with self.lock:
            self._check()
            if self.active is not None:
                _fail('concurrent transactions are unsupported', 'busy')
            self.active = worker
        worker.start()
        try:
            if not done.wait(_remaining(deadline)):
                _fail('FTDI transaction deadline expired; delivery uncertain', 'timeout')
            _remaining(deadline)
            if isinstance(results[0], Exception):
                raise results[0]
            if len(results[0]) != bound:
                _fail('FTDI exact response length mismatch', 'length')
            return results[0]
        except Exception as exc:
            self._error(exc)


def validate_binding(channel, binding):
    """Check the operator's binding for a channel without opening anything. A malformed binding fails with code
    `binding`: only the operator can fix it, so it never counts against the model."""
    validate_channel(channel)
    fields = {'uart': (['port'], ['port']), 'tcp': (['host','port'], ['host','port']),
              'i2c': (['url'], ['url']), 'spi': (['url'], ['url']),
              'usb': (['vendor_id','product_id','serial_number','allow_set_configuration','allow_detach_kernel_driver'], ['vendor_id','product_id'])}
    try:
        _binding(channel['type'], binding, *fields[channel['type']])
    except TransportError as exc:
        raise TransportError('binding', str(exc), Fault.OPERATOR) from None


def _binding(kind, binding, allowed, required):
    _fields(binding, allowed, required, 'binding')
    if kind == 'tcp':
        _string(binding['host'], 'host')
        _integer(binding['port'], 1, 65535, 'port')
    elif kind == 'uart':
        _string(binding['port'], 'port')
    elif kind in ('i2c','spi'):
        _string(binding['url'], 'url')
        if not binding['url'].startswith('ftdi://'):
            _fail('FTDI binding requires ftdi:// URL')
    else:
        for key in ['vendor_id','product_id']:
            _integer(binding[key], 0, 65535, key)
        if 'serial_number' in binding:
            _string(binding['serial_number'], 'serial_number')
        for key in ['allow_set_configuration','allow_detach_kernel_driver']:
            if key in binding and type(binding[key]) is not bool:
                _fail(f'{key} must be boolean')


def open_transport(channel, binding):
    validate_binding(channel, binding)
    kind = channel['type']
    try:
        if kind == 'tcp':
            return _Stream(socket.create_connection((binding['host'], binding['port']), timeout=5), 'tcp')
        if kind == 'uart':
            serial = _dependency('serial')
            return _Stream(serial.Serial(port=binding['port'], baudrate=channel['baudrate'], bytesize=channel.get('bytesize',8), parity=channel.get('parity','N'), stopbits=channel.get('stopbits',1), timeout=0, write_timeout=0), 'uart')
        if kind == 'usb':
            return _USB(channel, binding)
        return _FTDI(channel, binding)
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError('open', str(exc)) from exc

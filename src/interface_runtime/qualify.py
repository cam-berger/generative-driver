"""Offline qualification of an interface model against predicted or archived replies.

The model's own engine and stream adapter run over a fake serial source that delivers every reply
complete and in fixed-size fragments, under LF and CRLF framing, and then under synthetic faults.
Binary replies (interface-replies/2, for interface-model/5 frame steps) are delivered the same way under
one BINARY framing, with faults built from the model's own frame declaration: a corrupted check, a wrong
sync byte, an impossible length, a truncated frame, an error frame and a well-formed unrelated frame.
On a usb channel every reply, control reads included, is one whole transfer through the real USB adapter
over a scripted device, with faults built from each step's own expectations and receive bound.
An operation qualifies only when every positive case passes with all reply bytes consumed and
identical outputs, and every applicable fault is rejected at the phase where it was injected.
Replies are assumptions supplied by the caller, predicted from analysis or archived from a device.
Qualification proves that the model's framing and guards are robust against those assumptions;
it never proves that a device behaves this way.
"""
import copy
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from .engine import execute, validate_model, _encode, _lines
from . import checks
from .transports import TransportError, _Stream, _USB
from . import transports as transport_module

REPLIES_SCHEMA = 'interface-replies/1'
REPLIES_SCHEMAS = ('interface-replies/1', 'interface-replies/2')
RESULT_SCHEMA = 'interface-qualification/1'
CHUNKS = (0, 1, 2, 7, 16, 32, 64)
FRAMINGS = {'LF': b'\n', 'CRLF': b'\r\n'}
FAULTS = ('unrelated', 'truncated', 'missing_completion', 'error', 'malformed', 'timeout', 'disconnect')
BINARY_FAULTS = ('unrelated', 'truncated', 'corrupt_check', 'bad_sync', 'bad_length', 'error', 'timeout', 'disconnect')
BINARY = {'BINARY': b''}
USB = {'USB': b''}
USB_FAULTS = ('unrelated', 'truncated', 'error', 'oversize', 'timeout')
USB_FRAME_FAULTS = ('unrelated', 'truncated', 'error', 'oversize', 'mismatch', 'bad_padding', 'timeout')
NEGATIVE_CHUNK = 7
STREAM_CHANNELS = ('uart', 'tcp')


class QualifyError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise QualifyError(message)


def _exchange_steps(operation):
    """Every step that transfers bytes and so takes one scripted reply: exchanges and USB control requests."""
    return [step for step in operation['steps'] if step['op'] in ('exchange', 'usb_control')]


def _receive_bound(rx):
    return rx['length'] if rx['mode'] == 'exact' else rx['max_bytes']


def validate_replies(model, replies):
    """Structural check of an interface-replies/1 or /2 fixture against the model it qualifies."""
    _require(type(replies) is dict and replies.get('schema') in REPLIES_SCHEMAS,
             'expected schema ' + ' or '.join(REPLIES_SCHEMAS))
    binary = replies['schema'] == 'interface-replies/2'
    usb = model['channel']['type'] == 'usb'
    _require(set(replies) == {'schema', 'operations'}, 'replies must hold schema and operations only')
    operations = replies['operations']
    _require(type(operations) is dict and operations, 'operations must be a nonempty object')
    for name, entry in operations.items():
        _require(name in model['operations'], f'unknown operation {name!r}')
        _require(type(entry) is dict and 'steps' in entry and set(entry) <= {'steps', 'parameters'},
                 f'replies for {name!r} must hold steps and optional parameters')
        steps = entry['steps']
        exchanges = _exchange_steps(model['operations'][name])
        _require(type(steps) is list and len(steps) == len(exchanges),
                 f'replies for {name!r} need exactly {len(exchanges)} reply lists, one per exchange step')
        for index, (step, lines) in enumerate(zip(exchanges, steps)):
            if usb:
                # A USB reply is one whole transfer, so it is bytes, never lines; a step that receives
                # nothing (a write, or a control request without a data stage) takes the empty reply.
                _require(binary and type(lines) is dict, 'a usb channel step needs a {"hex": ...} reply in interface-replies/2')
                _require(set(lines) == {'hex'} and type(lines['hex']) is str and len(lines['hex']) % 2 == 0
                         and all(c in '0123456789abcdefABCDEF' for c in lines['hex']),
                         'a binary reply is {"hex": "<hexadecimal bytes>"}')
                bound = _receive_bound(step['rx'])
                _require(bool(lines['hex']) == (bound > 0),
                         'a usb step that receives needs a nonempty hex reply; one that receives nothing needs {"hex": ""}')
                _require(len(lines['hex']) // 2 <= bound,
                         f'the reply to {name!r} transfer step {index} is {len(lines["hex"]) // 2} bytes, longer than '
                         f'that step\'s receive bound of {bound}; the transfer could never deliver it')
                continue
            if type(lines) is dict:
                _require(binary, 'hex replies require interface-replies/2')
                _require(set(lines) == {'hex'} and type(lines['hex']) is str and lines['hex']
                         and len(lines['hex']) % 2 == 0 and all(c in '0123456789abcdefABCDEF' for c in lines['hex']),
                         'a binary reply is {"hex": "<nonempty hexadecimal bytes>"}')
                continue
            _require(step['rx']['mode'] != 'frame', 'a frame receive step needs a {"hex": ...} reply')
            _require(type(lines) is list and 1 <= len(lines) <= 4096, 'each reply is a nonempty list of lines')
            for line in lines:
                _require(type(line) is str and line.isascii() and '\n' not in line and '\r' not in line,
                         'reply lines are ASCII text without terminators')
        if 'parameters' in entry:
            _require(type(entry['parameters']) is dict, 'parameters must be an object')


def default_parameters(specs):
    """Minimum bound or first enumerated value for every parameter; explicit fixtures override."""
    out = {}
    for name, spec in specs.items():
        if spec['type'] == 'bytes':
            out[name] = '00' * spec['min_length']
        else:
            out[name] = spec['enum'][0] if spec['type'] == 'string' else spec['minimum']
    return out


def _completion(lines):
    return lines[-1]


def _anchors(step, decoders, capture):
    """Literals the model relies on inside this step's reply: expectation literals and decoder keys."""
    found = []
    expect = step.get('expect', {})
    for key in ('contains_text', 'contains_line', 'line_prefix_present', 'first_line_prefix'):
        if key in expect:
            found.append(expect[key])
    for decoder in decoders.values():
        if decoder['from'] != capture:
            continue
        for key in ('key', 'line_prefix', 'prefix'):
            if key in decoder:
                found.append(decoder[key])
    return [a for a in found if a]


def _mutate(literal):
    """Same-length alteration of a literal so that only a load-bearing check can notice it."""
    if not literal:
        return literal
    last = literal[-1]
    replacement = 'X' if last != 'X' else 'Y'
    if last.isdigit():
        replacement = '7' if last != '7' else '8'
    return literal[:-1] + replacement


def _reject_prefix(step):
    rejects = step.get('expect', {}).get('reject_line_prefix') or []
    return (rejects[0], True) if rejects else ('Error:', False)


def fault_reply(lines, fault, step, decoders, capture):
    """Return (lines, applicable, note) for one fault against one positive reply."""
    completion = _completion(lines)
    if fault == 'unrelated':
        return ['UNRELATED_DEVICE', completion], True, ''
    if fault == 'truncated':
        text = '\n'.join(lines)
        positions = [text.find(a) for a in _anchors(step, decoders, capture)]
        positions = [p for p in positions if p > 0]
        if not positions:
            return lines, False, 'no anchor after the reply start to truncate before'
        cut = max(positions)
        kept = text[:cut].rstrip('\n').split('\n')
        if len(lines) > 1 and kept[-1] != completion:
            kept.append(completion)
        return kept, True, f'truncated before offset {cut}'
    if fault == 'missing_completion':
        if len(lines) > 1:
            return lines[:-1], True, ''
        end = step['rx'].get('end') if step['rx']['mode'] == 'lines' else None
        if end:
            value = next(iter(end.values()))
            remainder = completion[len(value):].lstrip() if completion.startswith(value) else ''
            return ([remainder] if remainder else ['']), True, 'completion token removed from the only line'
        return [''], True, 'the only line emptied'
    if fault == 'error':
        prefix, declared = _reject_prefix(step)
        return [prefix + ' synthetic rejected request', completion], True, ('' if declared else 'model declares no reject_line_prefix; generic Error: used')
    if fault == 'malformed':
        return ['@@@ malformed reply @@@', completion], True, ''
    if fault in ('timeout', 'disconnect'):
        return [], True, ''
    if fault == 'trailing':
        return lines + ['UNCLASSIFIED_TRAILER'], True, 'diagnostic only'
    if fault.startswith('mutate:'):
        literal = fault[len('mutate:'):]
        text = '\n'.join(lines)
        if literal not in text:
            return lines, False, 'literal absent from the positive reply'
        return text.replace(literal, _mutate(literal), 1).split('\n'), True, ''
    raise QualifyError('unknown fault ' + fault)


def _flip(value):
    return value ^ 0xFF if value not in (0x00, 0xFF) else value ^ 0x5A


def _reframe(raw, step):
    """Recompute a frame's check after a content change, so only a content guard can notice it."""
    rx = step['rx']
    if rx['mode'] != 'frame':
        return bytes(raw)
    spec = checks.frame_spec(rx)
    body = bytes(raw[spec['header']:len(raw) - spec['check_size'] - len(spec['end'])])
    return checks.build_frame(spec, body) if spec['check'] else bytes(raw)


def _pinned_hex(step):
    expect = step.get('expect', {})
    return [bytes.fromhex(expect[k]) for k in ('equals_hex', 'prefix_hex') if k in expect]


def binary_fault(raw, fault, step):
    """Return (bytes, applicable, note) for one fault against one positive binary reply."""
    raw = bytes(raw)
    rx = step['rx']
    spec = checks.frame_spec(rx) if rx['mode'] == 'frame' else None
    if fault in ('timeout', 'disconnect'):
        return b'', True, ''
    if fault == 'trailing':
        return raw + b'\x00\x00', True, 'diagnostic only'
    if fault == 'truncated':
        return (raw[:-1], True, 'last byte withheld') if len(raw) > 1 else (raw, False, 'reply too short')
    if fault == 'unrelated':
        at = spec['header'] if spec else 0
        if at >= len(raw) - (spec['check_size'] + len(spec['end']) if spec else 0):
            return raw, False, 'no content byte to change'
        changed = bytearray(raw)
        changed[at] = _flip(changed[at])
        return _reframe(changed, step), True, f'content byte {at} changed, frame kept well formed'
    if spec is None:
        return raw, False, 'fault applies to frame receive steps only'
    if fault == 'corrupt_check':
        if not spec['check']:
            return raw, False, 'frame declares no check'
        changed = bytearray(raw)
        at = len(raw) - len(spec['end']) - 1
        changed[at] = _flip(changed[at])
        return bytes(changed), True, 'last check byte changed'
    if fault == 'bad_sync':
        changed = bytearray(raw)
        changed[0] = _flip(changed[0])
        return bytes(changed), True, 'first sync byte changed'
    if fault == 'bad_length':
        limit = 1 << (8 * spec['size'])
        value = spec['max'] - spec['add'] + 1
        if not 0 <= value < limit:
            value = 0
        changed = bytearray(raw)
        changed[spec['offset']:spec['header']] = value.to_bytes(spec['size'], spec['byteorder'])
        return bytes(changed), True, f'length field set to {value}'
    if fault == 'error':
        rejects = [bytes.fromhex(h) for h in step.get('expect', {}).get('reject_prefix_hex', [])]
        if not rejects:
            return raw, False, 'model declares no reject_prefix_hex error frame'
        prefix = rejects[0]
        for size in range(0, spec['max']):
            body = prefix[spec['header']:] + bytes(max(0, size - len(prefix[spec['header']:])))
            try:
                frame = checks.build_frame(spec, body)
            except checks.CheckError:
                continue
            if frame.startswith(prefix) and len(frame) <= spec['max']:
                return frame, True, 'well-formed frame starting with the model\'s own reject prefix'
        return raw, False, 'no well-formed frame carries the declared reject prefix'
    if fault.startswith('mutate:'):
        literal = bytes.fromhex(fault[len('mutate:'):])
        if not raw.startswith(literal):
            return raw, False, 'pinned bytes absent from the positive reply'
        changed = bytearray(raw)
        changed[len(literal) - 1] = _flip(changed[len(literal) - 1])
        return _reframe(changed, step), True, 'last pinned byte changed, frame kept well formed'
    raise QualifyError('unknown fault ' + fault)


def _relied_extent(raw, step, outputs):
    """End offset of the last reply byte a guard or fixed-offset decoder of this step depends on."""
    ends = []
    expect = step.get('expect', {})
    for key in ('equals_hex', 'prefix_hex'):
        if key in expect:
            ends.append(len(expect[key]) // 2)
    if 'contains_text' in expect:
        literal = expect['contains_text'].encode('utf-8')
        at = raw.find(literal)
        if at >= 0:
            ends.append(at + len(literal))
    for decoder in outputs.values():
        if 'capture' not in step or decoder['from'] != step['capture']:
            continue
        kind = decoder['kind']
        if kind in ('integer', 'float32'):
            ends.append(decoder['offset'] + (decoder['size'] if kind == 'integer' else 4))
        elif kind == 'cstring':
            at = raw.find(b'\x00', decoder['offset'])
            if at >= 0:
                ends.append(at + 1)
        elif kind == 'lpstring':
            start, size = decoder['length_offset'], decoder['length_size']
            ends.append(start + size)
            if len(raw) >= start + size:
                ends.append(decoder['offset'] + int.from_bytes(raw[start:start + size], decoder['byteorder']))
    return min(max(ends, default=0), len(raw))


def usb_fault(raw, fault, step, outputs):
    """Return (bytes, applicable, note) for one fault against one whole-transfer USB reply. Faults are
    built from the step's own expectations and bound, and only those meaningful for the step apply."""
    raw = bytes(raw)
    rx = step['rx']
    bound = _receive_bound(rx)
    if fault == 'timeout':
        return b'', True, ''
    if fault == 'padded':
        return (raw + bytes(max(0, bound - len(raw))) if rx['mode'] == 'up_to' else raw), True, 'diagnostic only'
    if rx['mode'] == 'frame':
        spec = checks.frame_spec(rx)
        if fault == 'truncated':
            return raw[:-1], True, 'final frame byte withheld'
        if fault in ('oversize', 'bad_padding'):
            if fault == 'bad_padding' and not spec['padding']:
                return raw, False, 'frame has no declared padding field'
            changed = bytearray(raw)
            field = int.from_bytes(changed[spec['offset']:spec['header']], spec['byteorder'])
            if fault == 'oversize':
                pad = spec['padding']
                pad_count = ((field >> pad['shift']) & pad['mask']) if pad else 0
                body_count = spec['max'] - spec['add'] - pad_count + 1
                if not 0 <= body_count <= spec['body_mask']:
                    return raw, False, 'length field cannot encode a frame beyond this receive bound'
                field = body_count | (field & ~spec['body_mask'])
            else:
                pad = spec['padding']
                if pad['max_bytes'] == pad['mask']:
                    return raw, False, 'padding bound fills its field'
                field = (field & ~(pad['mask'] << pad['shift'])) | ((pad['max_bytes'] + 1) << pad['shift'])
            changed[spec['offset']:spec['header']] = field.to_bytes(spec['size'], spec['byteorder'])
            return bytes(changed), True, 'declared frame length or padding exceeds its bound'
        if fault == 'mismatch':
            rules = step.get('match') or []
            if not rules:
                return raw, False, 'step has no request/reply match rule'
            rule = rules[0]
            changed = bytearray(raw)
            at = rule['rx_offset'] + rule['size'] - 1
            changed[at] = _flip(changed[at])
            return bytes(changed), True, 'matched reply header byte changed'
    if not bound:
        return raw, False, 'the step receives nothing'
    if fault == 'unrelated':
        if not raw:
            return raw, False, 'no reply byte to change'
        changed = bytearray(raw)
        changed[0] = _flip(changed[0])
        return bytes(changed), True, 'first reply byte changed'
    if fault == 'truncated':
        if rx['mode'] == 'exact':
            return (raw[:-1], True, 'last byte withheld') if raw else (raw, False, 'reply too short')
        extent = _relied_extent(raw, step, outputs)
        if not extent:
            return raw, False, 'no guard or fixed-offset decoder relies on a reply byte'
        return raw[:extent - 1], True, f'cut before byte {extent - 1}, the last one a guard or decoder relies on'
    if fault == 'error':
        rejects = step.get('expect', {}).get('reject_prefix_hex') or []
        if not rejects:
            return raw, False, 'model declares no reject_prefix_hex error reply'
        prefix = bytes.fromhex(rejects[0])
        return prefix + bytes(max(0, len(raw) - len(prefix))), True, "the model's first reject prefix, zero-padded to the reply length"
    if fault == 'oversize':
        return raw + bytes(max(1, bound + 1 - len(raw))), True, f'reply padded to {max(bound, len(raw)) + 1} bytes, past the receive bound'
    if fault.startswith('mutate:'):
        literal = bytes.fromhex(fault[len('mutate:'):])
        at = raw.find(literal)
        if at < 0:
            return raw, False, 'pinned bytes absent from the positive reply'
        changed = bytearray(raw)
        changed[at + len(literal) - 1] = _flip(changed[at + len(literal) - 1])
        return bytes(changed), True, 'last pinned byte changed'
    raise QualifyError('unknown fault ' + fault)


class ScriptedUSB:
    """A pyusb-like device that fragments a scripted exchange reply at its USB read boundary."""

    def __init__(self, owner):
        self.owner = owner
        self.pending = None
        self.pending_frame = False
        self.frame_exhausted = False

    def _take(self, tx):
        owner = self.owner
        owner.attempted_tx.append(bytes(tx).hex())
        if self.pending is not None:
            owner.no_tx_while_pending = False
            raise TransportError('unconsumed_reply', 'new request before the prior reply was consumed')
        if owner.cursor >= len(owner.script):
            raise TransportError('unscripted_request', 'request beyond the scripted replies')
        raw, fault, receives, framed = owner.script[owner.cursor]
        owner.cursor += 1
        if fault is not None and fault != 'padded':
            owner.fault_applied = True
        if fault == 'timeout':
            raise TransportError('timeout', 'simulated USB transfer timeout')
        owner.response_bytes += len(raw)
        return raw, receives, framed

    def _deliver(self, raw):
        self.owner.read_calls += 1
        self.owner.chunks_delivered += 1
        self.owner.read_bytes += len(raw)
        return raw

    def write(self, endpoint, tx, timeout):
        self.frame_exhausted = False
        raw, receives, framed = self._take(tx)
        if receives:
            self.pending = raw
            self.pending_frame = framed
        return len(tx)

    def read(self, endpoint, size, timeout):
        if self.pending is None:
            if self.frame_exhausted:
                raise TransportError('timeout', 'scripted frame ended before completion')
            raw, _, self.pending_frame = self._take(b'')
            self.pending = raw
        amount = min(len(self.pending), size, self.owner.chunk_bytes or size) if self.pending_frame else len(self.pending)
        raw, self.pending = self.pending[:amount], self.pending[amount:] or None
        if self.pending is None and self.pending_frame:
            self.frame_exhausted = True
        return self._deliver(raw)

    def ctrl_transfer(self, request_type, request, value, index, data_or_length, timeout):
        incoming = bool(request_type & 0x80)
        raw, _, _ = self._take(b'' if incoming else bytes(data_or_length))
        return self._deliver(raw) if incoming else len(data_or_length)


class ScriptedUSBTransport(_USB):
    """Supply exchange boundaries and synthetic read sizes without inventing a descriptor.

    The model does not contain endpoint packet sizes. Framed cases use the receive
    bound as capacity and ScriptedUSB splits delivery at the requested chunk size;
    the physical packet-size rule is exercised by transport tests and live opens.
    """
    def exchange(self, tx, rx, timeout_ms):
        self.device.frame_exhausted = False
        if self.in_endpoint is not None:
            self.packet_size[self.in_endpoint] = rx['max_bytes'] if rx['mode'] == 'frame' else 1
        return super().exchange(tx, rx, timeout_ms)


class ScriptedSerial:
    """A pyserial-like source: each write releases the next scripted reply, chunk by chunk."""

    def __init__(self, owner):
        self.owner = owner
        self.queue = deque()
        self.current = bytearray()
        self.stream = None
        self.disconnect = False
        self.timeout = None
        self.write_timeout = None
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.current)

    @property
    def pending(self):
        return bool(self.current or self.queue)

    def write(self, tx):
        owner = self.owner
        owner.attempted_tx.append(bytes(tx).hex())
        if self.pending or (self.stream is not None and self.stream.buffer):
            owner.no_tx_while_pending = False
            raise TransportError('unconsumed_reply', 'new request before the prior reply was consumed')
        if owner.cursor >= len(owner.script):
            raise TransportError('unscripted_request', 'request beyond the scripted replies')
        raw, fault = owner.script[owner.cursor]
        owner.cursor += 1
        if fault in ('timeout', 'disconnect'):
            owner.fault_applied = True
            self.disconnect = fault == 'disconnect'
            return len(tx)
        if fault is not None:
            owner.fault_applied = True
        owner.response_bytes += len(raw)
        size = owner.chunk_bytes or max(1, len(raw))
        self.queue.extend(raw[i:i + size] for i in range(0, len(raw), size))
        return len(tx)

    def read(self, size):
        self.owner.read_calls += 1
        if self.disconnect:
            raise TransportError('disconnect', 'simulated serial disconnect')
        if not self.current and self.queue:
            self.current.extend(self.queue.popleft())
            self.owner.chunks_delivered += 1
        result = bytes(self.current[:size])
        del self.current[:size]
        self.owner.read_bytes += len(result)
        return result

    def close(self):
        self.closed = True


class ScriptedStream:
    """Transport factory: identity replies first, then the operation's, in exchange order."""

    def __init__(self, script, chunk_bytes):
        self.script, self.chunk_bytes = script, chunk_bytes
        self.cursor = 0
        self.fault_applied = False
        self.no_tx_while_pending = True
        self.attempted_tx, self.serials, self.streams, self.devices, self.usb_handles = [], [], [], [], []
        self.read_calls = self.chunks_delivered = self.read_bytes = self.response_bytes = 0

    def __call__(self, channel, binding):
        if channel['type'] == 'usb':
            device = ScriptedUSB(self)
            self.devices.append(device)
            handle = ScriptedUSBTransport._scripted(device, channel)
            self.usb_handles.append(handle)
            return handle
        if channel['type'] not in STREAM_CHANNELS:
            raise TransportError('uncovered_transport', 'qualification covers stream and usb channels only')
        serial = ScriptedSerial(self)
        stream = _Stream(serial, 'uart')
        serial.stream = stream
        self.serials.append(serial)
        self.streams.append(stream)
        return stream

    def state(self):
        return {'script_exhausted': self.cursor == len(self.script),
                'serial_exhausted': all(s.in_waiting == 0 and not s.queue for s in self.serials)
                                    and all(d.pending is None for d in self.devices),
                'stream_buffer_empty': all(not s.buffer for s in self.streams + self.usb_handles),
                'no_tx_while_pending': self.no_tx_while_pending,
                'read_calls': self.read_calls, 'chunks_delivered': self.chunks_delivered,
                'read_bytes': self.read_bytes, 'response_bytes': self.response_bytes,
                'attempted_tx': self.attempted_tx, 'fault_applied': self.fault_applied}


def _script(model, replies, operation, parameters, newline, fault=None, fault_phase=None):
    """(raw bytes, fault) per exchange, identity first; the fault lands on the phase's first exchange.
    On a usb channel each entry also says whether the step receives, and a `padded` diagnostic pads every
    up_to reply to its bound."""
    identity = model['identity']['operation']
    usb = model['channel']['type'] == 'usb'
    phases = [('identity', identity, {})]
    if operation != identity:
        phases.append(('operation', operation, parameters))
    script, expected_tx = [], []
    for phase, name, arguments in phases:
        op = model['operations'][name]
        steps = _exchange_steps(op)
        lists = replies['operations'][name]['steps']
        for index, (step, lines) in enumerate(zip(steps, lists)):
            applied = fault if (phase == fault_phase and index == 0) or fault == 'padded' else None
            if usb:
                tx = _encode(step['tx'], arguments)
                receives = _receive_bound(step['rx']) > 0
                if step['op'] == 'exchange' and not tx and not receives:
                    # No transfer happens, so no reply is taken and no fault can land here.
                    if applied is not None and fault != 'padded':
                        return None, expected_tx
                    continue
                expected_tx.append(tx.hex())
                raw = bytes.fromhex(lines['hex'])
                if applied is not None:
                    raw, applicable, _ = usb_fault(raw, applied, step, op['outputs'])
                    if not applicable:
                        return None, expected_tx
                script.append((raw, applied, receives, step['rx']['mode'] == 'frame'))
                continue
            expected_tx.append(_encode(step['tx'], arguments).hex())
            if type(lines) is dict:
                raw = bytes.fromhex(lines['hex'])
                if applied is not None:
                    raw, applicable, _ = binary_fault(raw, applied, step)
                    if not applicable:
                        return None, expected_tx
                script.append((raw, applied))
                continue
            if applied is not None:
                lines, applicable, _ = fault_reply(lines, applied, step, op['outputs'], step.get('capture'))
                if not applicable:
                    return None, expected_tx
            raw = b'' if applied in ('timeout', 'disconnect') else newline.join(s.encode('ascii') for s in lines) + newline
            script.append((raw, applied))
    return script, expected_tx


def _execute(model, operation, parameters, factory):
    with patch.object(transport_module, 'time', SimpleNamespace(monotonic=lambda: 0.0)):
        return execute(model, operation, parameters, transport_factory=factory, delay_fn=lambda _: None,
                       allow_effects=('write', 'actuate'))


def run_case(model, replies, operation, parameters, framing, chunk_bytes, fault=None, fault_phase=None):
    newline = FRAMINGS.get(framing, BINARY.get(framing))
    script, expected_tx = _script(model, replies, operation, parameters, newline, fault, fault_phase)
    if script is None:
        return {'framing': framing, 'chunk_bytes': chunk_bytes, 'fault': fault, 'fault_phase': fault_phase,
                'applicable': False, 'note': 'fault not applicable to this reply'}
    source = ScriptedStream(script, chunk_bytes)
    result = _execute(model, operation, parameters, source)
    state = source.state()
    identity = model['identity']['operation']
    identity_count = len(_script(model, replies, identity, {}, newline)[1])
    consumed = all(state[k] for k in ('script_exhausted', 'serial_exhausted', 'stream_buffer_empty', 'no_tx_while_pending'))
    sequence_ok = state['attempted_tx'] == expected_tx
    outputs = result['outputs']
    nonempty = (bool(outputs) and any(v not in ('', None) for v in outputs.values())) if model['operations'][operation]['effect'] == 'read' else True
    case = {'framing': framing, 'chunk_bytes': chunk_bytes, 'fault': fault, 'fault_phase': fault_phase,
            'applicable': True, 'engine_ok': result['ok'], 'sequence_ok': sequence_ok, 'consumed': consumed,
            'nonempty_output': nonempty, 'outputs': outputs, 'error': result.get('error'), 'state': state}
    if fault is None:
        case['positive_pass'] = bool(result['ok'] and sequence_ok and consumed and nonempty)
    elif fault == 'padded':
        pass
    elif fault == 'trailing':
        case['leftover_bytes'] = state['response_bytes'] - state['read_bytes'] + sum(len(s.buffer) for s in source.streams)
        case['diagnostic'] = 'accepted with unread trailing bytes' if result['ok'] and not consumed else ('rejected' if not result['ok'] else 'clean')
    else:
        # The fault lands on the phase's first transfer and must stop the run there: nothing further may
        # be sent once a faulted reply has been seen.
        stopped = (state['attempted_tx'] == expected_tx[:1] and not result['identity_verified']
                   if fault_phase == 'identity' else
                   result['identity_verified'] and state['attempted_tx'] == expected_tx[:identity_count + 1])
        case['negative_pass'] = bool(state['fault_applied'] and not result['ok'] and stopped)
    return case


def _binary(model, replies, name):
    return any(type(r) is dict for r in replies['operations'][name]['steps'])


def _pinned_bytes(model, name):
    """Hex literals of the first exchange's positive guard, beyond its sync bytes, for mutation."""
    steps = _exchange_steps(model['operations'][name])
    if not steps:
        return []
    return [p.hex() for p in _pinned_hex(steps[0])]


def _usb_pinned(model, name):
    """Hex of every literal the first transfer's positive guard pins, text included, for mutation."""
    steps = _exchange_steps(model['operations'][name])
    expect = steps[0].get('expect', {}) if steps else {}
    found = [expect[k].lower() for k in ('equals_hex', 'prefix_hex') if k in expect]
    if 'contains_text' in expect:
        found.append(expect['contains_text'].encode('utf-8').hex())
    return list(dict.fromkeys(found))


def _pinned_literals(model, name):
    literals = []
    for step in _exchange_steps(model['operations'][name]):
        expect = step.get('expect', {})
        for key in ('contains_text', 'contains_line', 'line_prefix_present', 'first_line_prefix'):
            if key in expect and expect[key] not in literals:
                literals.append(expect[key])
    return literals


def operation_suite(model, replies, name):
    op = model['operations'][name]
    identity = model['identity']['operation']
    usb = model['channel']['type'] == 'usb'
    if model['channel']['type'] not in STREAM_CHANNELS and not usb:
        return {'status': 'uncovered', 'reason': 'qualification covers uart, tcp and usb channels only', 'variants': {}}
    if name not in replies['operations'] or (name != identity and identity not in replies['operations']):
        return {'status': 'uncovered', 'reason': 'no replies supplied for this operation or its identity', 'variants': {}}
    parameters = replies['operations'][name].get('parameters') or default_parameters(op['parameters'])
    def phase_faults(phase, op_name):
        if usb:
            steps = _exchange_steps(model['operations'][op_name])
            framed = bool(steps and steps[0]['rx']['mode'] == 'frame')
            faults = USB_FRAME_FAULTS if framed else USB_FAULTS
            return [(phase, f) for f in faults] + [(phase, 'mutate:' + h) for h in _usb_pinned(model, op_name)]
        if _binary(model, replies, op_name):
            return [(phase, f) for f in BINARY_FAULTS] + [(phase, 'mutate:' + h) for h in _pinned_bytes(model, op_name)]
        return [(phase, f) for f in FAULTS] + [(phase, 'mutate:' + lit) for lit in _pinned_literals(model, op_name)]
    faults = phase_faults('identity', identity)
    if name != identity:
        faults += phase_faults('operation', name)
    binary_only = all(_binary(model, replies, n) for n in {identity, name})
    variants = {}
    usb_framed = usb and any(s['rx']['mode'] == 'frame' for n in {identity, name}
                             for s in _exchange_steps(model['operations'][n]))
    frame_chunks = (0,)
    negative_chunk = NEGATIVE_CHUNK
    if usb_framed:
        minimum = max((len(bytes.fromhex(reply['hex'])) + step['rx']['max_reads'] - 1) // step['rx']['max_reads']
                      for n in {identity, name} for step, reply in zip(
                          _exchange_steps(model['operations'][n]), replies['operations'][n]['steps'])
                      if step['rx']['mode'] == 'frame')
        frame_chunks = tuple(dict.fromkeys((0, max(1, minimum), max(64, minimum))))
        negative_chunk = max(NEGATIVE_CHUNK, minimum)
    for framing in (USB if usb else BINARY if binary_only else FRAMINGS):
        positives = [run_case(model, replies, name, parameters, framing, size)
                     for size in (frame_chunks if usb else CHUNKS)]
        stable = all(c['outputs'] == positives[0]['outputs'] for c in positives)
        negatives = [run_case(model, replies, name, parameters, framing, negative_chunk, fault, phase) for phase, fault in faults]
        if usb:
            # Diagnostic only: every up_to reply zero-padded to its bound, as firmware that answers in
            # whole packets would send it. Outputs that change under padding depend on reply length.
            diagnostic = run_case(model, replies, name, parameters, framing, 0, 'padded', 'operation')
            diagnostic['diagnostic'] = ('not applicable' if not diagnostic['applicable'] else 'rejected' if not diagnostic['engine_ok'] else
                                        'outputs identical' if diagnostic['outputs'] == positives[0]['outputs'] else 'outputs differ')
        else:
            diagnostic = run_case(model, replies, name, parameters, framing, 0, 'trailing', 'operation' if name != identity else 'identity')
        applicable = [c for c in negatives if c['applicable']]
        qualifies = all(c['positive_pass'] for c in positives) and stable and all(c['negative_pass'] for c in applicable)
        variants[framing] = {'qualifies': qualifies, 'outputs_stable_across_chunks': stable,
                             'positives': positives, 'negatives': negatives,
                             ('padding_diagnostic' if usb else 'trailing_diagnostic'): diagnostic,
                             'guard_gaps': [f"{c['fault_phase']}:{c['fault']}" for c in applicable if not c['negative_pass']]}
    return {'status': 'evaluated', 'effect': op['effect'], 'parameters': parameters,
            'qualifies': all(v['qualifies'] for v in variants.values()), 'variants': variants}


def qualify(model, replies):
    """Full suite. Raises QualifyError for an invalid model or fixture; never opens hardware."""
    checked = validate_model(model)
    _require(checked['ok'], 'model is not structurally valid: ' + json.dumps(checked['defects']))
    model = copy.deepcopy(model)
    validate_replies(model, replies)
    operations = {name: operation_suite(model, replies, name) for name in model['operations']}
    evaluated = {n: o for n, o in operations.items() if o['status'] == 'evaluated'}
    usb = model['channel']['type'] == 'usb'
    return {'schema': RESULT_SCHEMA, 'model_schema': model['schema'],
            'identity_operation': model['identity']['operation'],
            'operations': operations,
            'qualified': sorted(n for n, o in evaluated.items() if o['qualifies']),
            'not_qualified': sorted(n for n, o in evaluated.items() if not o['qualifies']),
            'uncovered': sorted(n for n, o in operations.items() if o['status'] != 'evaluated'),
            'all_supplied_qualify': bool(evaluated) and all(o['qualifies'] for o in evaluated.values()),
            'chunks': sorted({c['chunk_bytes'] for o in evaluated.values() for v in o['variants'].values()
                              for c in v['positives']}) if usb else list(CHUNKS),
            'framings': list(USB if usb else FRAMINGS),
            'scope': ('Assumed replies delivered over scripted USB reads with synthetic faults through the real engine and USB adapter; framed replies are also fragmented within their declared max_reads. The packet-size rule needs the device descriptor and is checked only when a real device is opened. '
                      if usb else 'Assumed reply content delivered complete and fragmented under LF/CRLF with synthetic faults through the real engine and stream adapter. ')
                     + 'Proves framing and guard robustness against the supplied replies only; no device, timing or physical-effect evidence.'}


def summary(report):
    lines = []
    for name, op in report['operations'].items():
        if op['status'] != 'evaluated':
            lines.append(f"{name}: uncovered ({op['reason']})")
            continue
        for framing, variant in op['variants'].items():
            passed = sum(c['positive_pass'] for c in variant['positives'])
            applicable = [c for c in variant['negatives'] if c['applicable']]
            rejected = sum(c['negative_pass'] for c in applicable)
            gaps = ', '.join(variant['guard_gaps']) or 'none'
            lines.append(f"{name} [{framing}]: {'QUALIFIES' if variant['qualifies'] else 'FAILS'}; positives {passed}/{len(variant['positives'])}, "
                         f"faults rejected {rejected}/{len(applicable)}, stable outputs {variant['outputs_stable_across_chunks']}, guard gaps: {gaps}")
    lines.append('all supplied operations qualify: ' + str(report['all_supplied_qualify']))
    return '\n'.join(lines)

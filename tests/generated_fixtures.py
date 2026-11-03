"""A hand-written generated device for tests, never generator output: the firmware stand-in behind local sockets, its
answer key with two reference drivers and the mutants, and a set of two such devices sealed under a synthetic password.

The device speaks text lines over its UART. `MODE?` replies `MODE:<n>`; `MODE <n>` sets the mode and replies `MODE OK`
when 0 <= n <= 3, and replies `MODE ERR` otherwise, so both its replies begin with the command's word; `ID?` replies
`ID:FIXTURE`; `PING` replies `PONG` in mode 3 and is never answered in another mode; any other line replies `ERR`. The
mode lives at 0x20000000, and a reset leaves it at 2. Its public mission states the mode's unit, `1` (dimensionless)."""
import copy
import re
import secrets
import socketserver
import threading
from pathlib import Path

from generative_driver.benchmark_support.generated_sets import seal_device, write_manifest

PASSWORD = 'synthetic fixture credential'
FIXTURE_IMAGE = b'fixture firmware image'
MODE_ADDRESS = 0x20000000
RESET_MODE = 2
MODE_UNIT = '1'
_MASKS = {'ReadByte': 0xff, 'ReadWord': 0xffff, 'ReadDoubleWord': 0xffffffff}


class FixtureDevice:
    """The fixture firmware behind a fault-injecting stand-in for a Renode monitor, each on a local socket. It speaks
    the monitor's protocol but is not a faithful Renode: it answers an error line where Renode reads an unmapped address
    as 0, prints values as unpadded hex, boots the firmware at once on `machine Reset` (so its recipe needs no boot
    interval), and has a stopped-machine mode Renode lacks.

    The monitor answers `start` and `pause` (the emulator runs only between them), `machine Reset` (the mode becomes
    `reset_mode`), `cpu PC`, and a `sysbus ReadByte`, `ReadWord` or `ReadDoubleWord` of the mode's address. It answers
    with an error line a read of any other address, of an address in `unreadable`, or of any address while the mode is
    `unreadable_in_mode`. A UART line sent while the emulator is paused is dropped with its connection. On the UART line
    `dies_on` the machine stops: its UART hangs up on every line from then on, and its monitor still starts and pauses
    the emulator but rejects every other command."""

    def __init__(self, *, unreadable=(), reset_mode=RESET_MODE, unreadable_in_mode=None, dies_on=None):
        self.mode = self.reset_mode = reset_mode
        self.unreadable, self.unreadable_in_mode, self.dies_on = set(unreadable), unreadable_in_mode, dies_on
        self.running = self.dead = self.closed = False
        self.lines, self.commands = [], []
        owner = self

        class UART(socketserver.StreamRequestHandler):
            def handle(self):
                for raw in self.rfile:
                    line = raw.decode(errors='replace').strip()
                    owner.lines.append(line)
                    owner.dead = owner.dead or line == owner.dies_on
                    if owner.dead or not owner.running:
                        return
                    reply = owner.reply(line)
                    if reply is not None:
                        self.wfile.write((reply + '\n').encode())
                        self.wfile.flush()

        class Monitor(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'(device) ')
                self.wfile.flush()
                line = self.rfile.readline().decode(errors='ignore').lstrip('\x00\x01\x03').strip()
                owner.commands.append(line)
                self.wfile.write((line + '\r\n' + owner.monitor(line) + '\r\n(device) ').encode())
                self.wfile.flush()

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.servers = [Server(('127.0.0.1', 0), handler) for handler in (UART, Monitor)]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def reply(self, line):
        """The firmware's reply to one UART line, or None for none."""
        if line == 'MODE?':
            return 'MODE:%d' % self.mode
        if line == 'PING':
            return 'PONG' if self.mode == 3 else None
        if line == 'ID?':
            return 'ID:FIXTURE'
        command, _, argument = line.partition(' ')
        if command == 'MODE' and re.fullmatch(r'-?[0-9]+', argument) and 0 <= int(argument) <= 3:
            self.mode = int(argument)
            return 'MODE OK'
        return 'MODE ERR' if command == 'MODE' else 'ERR'

    def monitor(self, command):
        """The monitor's answer to one command."""
        if command in ('start', 'pause'):
            self.running = command == 'start'
            return ''
        if self.dead:
            return 'Error: the machine stopped'
        if command == 'machine Reset':
            self.mode = self.reset_mode
            return ''
        if command == 'cpu PC':
            return '0x08000100'
        read = re.fullmatch(r'sysbus (ReadByte|ReadWord|ReadDoubleWord) (0x[0-9a-f]{8})', command)
        if read is None:
            return 'Error: unknown command'
        address = int(read.group(2), 16)
        if address != MODE_ADDRESS or address in self.unreadable or self.mode == self.unreadable_in_mode:
            return 'Error: cannot read 0x%08x' % address
        return hex(self.mode & _MASKS[read.group(1)])

    @property
    def binding(self):
        """The binding of the device's UART."""
        return {'host': '127.0.0.1', 'port': self.servers[0].server_address[1]}

    def session(self, recipe, private_dir=None):
        """A NativeSession attached to this device; stopping the session stops the device."""
        from generative_driver.benchmark_support.native import NativeSession
        info = {'binding': self.binding, 'monitor_port': self.servers[1].server_address[1]}
        if private_dir is not None:
            Path(private_dir).mkdir(parents=True, exist_ok=True)
            info['private_dir'] = str(private_dir)
        session = NativeSession.attach(info, recipe)
        session.stop = self.close
        return session

    def close(self):
        if self.closed:
            return
        self.closed = True
        for server in self.servers:
            server.shutdown()
            server.server_close()


def fixture_start(**device):
    """A stand-in for `NativeSession.start` to patch it with: each start starts a new FixtureDevice(**device)."""
    def start(*, recipe, private_dir=None, **_):
        return FixtureDevice(**device).session(recipe, private_dir)
    return start


def _exchange(text, capture, *, expect=None, timeout_ms=500, rx=None):
    step = {'op': 'exchange', 'tx': [{'text': text}] if isinstance(text, str) else text,
            'rx': rx or {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 32}, 'timeout_ms': timeout_ms,
            'capture': capture}
    return {**step, 'expect': expect} if expect else step


def _model(name, identity, operations):
    return {'schema': 'interface-model/6', 'device': {'name': name}, 'channel': {'type': 'tcp'},
            'identity': {'operation': identity}, 'operations': operations,
            'provenance': [{'item': 'operations.' + operation, 'source': 'hand-written generated-device fixture'}
                           for operation in operations]}


def _capabilities(get_mode, set_mode, ping, output):
    """A mapping of the mission's three tasks onto a driver's operations, its mode in the mission's unit."""
    return {'schema': 'benchmark-capabilities/2', 'tasks': {
        'get_mode': {'operation': get_mode, 'constants': {}, 'inputs': {},
                     'outputs': {'mode': {'output': output, 'unit': MODE_UNIT}}},
        'set_mode': {'operation': set_mode, 'constants': {}, 'inputs': {'mode': {'parameter': 'mode', 'kind': 'copy'}},
                     'outputs': {}},
        'ping': {'operation': ping, 'constants': {}, 'inputs': {}, 'outputs': {}}}}


MODE_COMMAND = [{'text': 'MODE '}, {'arg': 'mode', 'format': 'ascii'}, {'text': '\n'}]
ACCEPTED = {'final_line_equals': 'MODE OK', 'reject_line_prefix': ['MODE ERR']}
MODE_LINE = {'first_line_prefix': 'MODE:', 'reject_line_prefix': ['ERR']}

# Reference A sends any mode up to 255, reads the reply until its line feed and reports the device's MODE ERR as a
# refusal.
REFERENCE_A = {'model': _model('FIXTURE-A', 'read_mode', {
    'read_mode': {'effect': 'read', 'parameters': {},
                  'steps': [_exchange('MODE?\n', 'reply', expect={'prefix_hex': b'MODE:'.hex()})],
                  'outputs': {'mode': {'from': 'reply', 'kind': 'kv_number', 'key': 'MODE:', 'unit': MODE_UNIT}}},
    'write_mode': {'effect': 'write', 'parameters': {'mode': {'type': 'integer', 'minimum': 0, 'maximum': 255}},
                   'steps': [_exchange(MODE_COMMAND, 'ack', expect=ACCEPTED)], 'outputs': {}},
    'ping': {'effect': 'read', 'parameters': {}, 'steps': [_exchange('PING\n', 'pong', timeout_ms=300)],
             'outputs': {}}}),
    'capabilities': _capabilities('read_mode', 'write_mode', 'ping', 'mode')}

# Reference B identifies the device by another read, forwards any mode up to 255 as A does but reads the reply as the
# first line that begins with MODE, and decodes the mode another way.
REFERENCE_B = {'model': _model('FIXTURE-B', 'identify', {
    'identify': {'effect': 'read', 'parameters': {},
                 'steps': [_exchange('ID?\n', 'id', expect={'prefix_hex': b'ID:'.hex()})], 'outputs': {}},
    'query': {'effect': 'read', 'parameters': {},
              'steps': [_exchange('MODE?\n', 'line', expect=MODE_LINE)],
              'outputs': {'current': {'from': 'line', 'kind': 'ascii_number', 'prefix': 'MODE:', 'unit': MODE_UNIT}}},
    'select': {'effect': 'write', 'parameters': {'mode': {'type': 'integer', 'minimum': 0, 'maximum': 255}},
               'steps': [_exchange(MODE_COMMAND, 'ack', expect=ACCEPTED,
                                   rx={'mode': 'lines', 'max_bytes': 32, 'end': {'line_token': 'MODE'}})],
               'outputs': {}},
    'listen': {'effect': 'read', 'parameters': {},
               'steps': [_exchange('PING\n', 'pong', timeout_ms=250, rx={'mode': 'up_to', 'max_bytes': 16})],
               'outputs': {}}}),
    'capabilities': _capabilities('query', 'select', 'listen', 'current')}

# A correct driver that bounds the mode to 0..3, so it declines 9 before any contact with the device.
BOUNDED = copy.deepcopy(REFERENCE_A)
BOUNDED['model']['operations']['write_mode']['parameters']['mode']['maximum'] = 3


def _mutant(change):
    """A copy of reference A with `change` applied to it."""
    candidate = copy.deepcopy(REFERENCE_A)
    change(candidate['model']['operations'], candidate['capabilities']['tasks'])
    return candidate


# Reports the device's ERR to an out-of-range mode as a value.
MUTANT_ERR_AS_VALUE = _mutant(lambda operations, tasks: operations['write_mode']['steps'][0].pop('expect'))
WRONG_SCALE = _mutant(lambda operations, tasks: operations['read_mode']['outputs']['mode'].update(scale=2))
CONSTANT_OUTPUT = _mutant(lambda operations, tasks: operations['read_mode']['outputs']['mode'].update(scale=0))
WRONG_STATE = _mutant(lambda operations, tasks: tasks['set_mode']['inputs'].update(
    mode={'parameter': 'mode', 'kind': 'affine', 'scale': 1, 'offset': 1}))


# The fixture's state machine as far as its hidden sequences walk it: each state is the mode it holds, and each
# transition (start, task, answer, end) one task call from a state.
_STATES = {'m1': 1, 'm2': 2, 'm3': 3}
_TRANSITIONS = {'t1': ('m2', 'set_mode', 'refusal', 'm2'), 't2': ('m2', 'get_mode', 'value', 'm2'),
                't3': ('m2', 'set_mode', 'value', 'm1'), 't4': ('m1', 'get_mode', 'value', 'm1'),
                't5': ('m2', 'ping', 'no_reply', 'm2'), 't6': ('m2', 'set_mode', 'value', 'm3'),
                't7': ('m3', 'get_mode', 'value', 'm3'), 't8': ('m3', 'ping', 'value', 'm3')}


def _step(transition, inputs, *, output=None, refusal=None):
    """A hidden step that takes `transition`: its task call, the reference's answer and the mode the device holds
    afterwards, with the mode a value step reports (`output`) and, on every refusal step, the bytes with which the
    device refuses (`refusal`)."""
    _, task, answer, end = _TRANSITIONS[transition]
    step = {'task': task, 'inputs': inputs, 'grants': ['write'] if task == 'set_mode' else [], 'answer': answer,
            'state': {'c1': _STATES[end]}, 'transition': transition}
    if output is not None:
        step['outputs'] = {'mode': {'value': output, 'tolerance': 0, 'unit': MODE_UNIT}}
    if answer == 'refusal':
        step['refusal_rx_hex'] = refusal.hex()
    return step


def fixture_answer_key():
    """The answer key of the fixture device (`generated-answer-key/2`), with a fresh random nonce."""
    return {
        'schema': 'generated-answer-key/2', 'nonce': secrets.token_hex(32),
        'recipe': {'reset_commands': ['machine Reset'],
                   'observations': {'pc': {'command': 'cpu PC', 'width': 32, 'count': 1}}},
        'state': {'c1': {'address': MODE_ADDRESS, 'width': 32}},
        'reset': {'c1': RESET_MODE},
        'machine': {'initial': 'm2', 'states': {name: {'c1': mode} for name, mode in _STATES.items()},
                    'transitions': [{'id': identifier, 'from': start, 'task': task, 'answer': answer, 'to': end}
                                    for identifier, (start, task, answer, end) in _TRANSITIONS.items()]},
        'sequences': [
            {'id': 's1', 'steps': [_step('t1', {'mode': 9}, refusal=b'MODE ERR\n'), _step('t2', {}, output=2),
                                   _step('t3', {'mode': 1}), _step('t4', {}, output=1)]},
            {'id': 's2', 'steps': [_step('t5', {}), _step('t6', {'mode': 3}), _step('t7', {}, output=3),
                                   _step('t8', {})]}],
        'mission': {'state': {'c1': 1}},
        'references': {'a': copy.deepcopy(REFERENCE_A), 'b': copy.deepcopy(REFERENCE_B)},
        'mutants': [
            {'id': 'wrong-scale', **copy.deepcopy(WRONG_SCALE),
             'expected_failed_checks': ['s1/2/out:mode', 's1/4/out:mode', 's2/3/out:mode']},
            {'id': 'constant-output', **copy.deepcopy(CONSTANT_OUTPUT),
             'expected_failed_checks': ['s1/2/out:mode', 's1/4/out:mode', 's2/3/out:mode']},
            {'id': 'wrong-state', **copy.deepcopy(WRONG_STATE),
             'expected_failed_checks': ['s1/3/state:c1', 's1/4/out:mode', 's1/4/state:c1', 's2/2/answer',
                                        's2/2/state:c1', 's2/3/out:mode', 's2/3/state:c1', 's2/4/answer',
                                        's2/4/state:c1']},
            {'id': 'refusal-as-value', **copy.deepcopy(MUTANT_ERR_AS_VALUE),
             'expected_failed_checks': ['s1/1/answer']}]}


FIXTURE_ANSWER_KEY = fixture_answer_key()
# The step that sets an out-of-range mode, which the device refuses with ERR.
STEP = FIXTURE_ANSWER_KEY['sequences'][0]['steps'][0]


def fixture_mission(device_id):
    """The public mission of a fixture device (`benchmark-public-mission/1`): each task's effect and each output's
    unit, and an objective that names the device, so that a test can tell the devices apart."""
    return {'schema': 'benchmark-public-mission/1', 'case_id': 'generated', 'tasks': {
        'get_mode': {'meaning': 'Report the mode the device is in.', 'inputs': {},
                     'outputs': {'mode': {'unit': MODE_UNIT}}},
        'set_mode': {'meaning': 'Put the device in the requested mode.', 'inputs': {'mode': {'type': 'integer'}},
                     'outputs': {}},
        'ping': {'meaning': 'Check that the device listens.', 'inputs': {}, 'outputs': {}}},
        'reuse_objective': 'Put fixture device ' + device_id + ' in mode 1 and report its mode.'}


def write_fixture_set(root):
    """Seal devices 0000d001 and 0000d002 (level 1, text lines), each the fixture device with FIXTURE_ANSWER_KEY, under
    `root`, write the manifest of set fixture-v1 beside them and return the SHA-256 of that manifest, which a test
    registers."""
    devices = [seal_device(root, {'id': device_id, 'level': 1, 'framing': 'text-lines'}, image=FIXTURE_IMAGE,
                           mission=fixture_mission(device_id), answer_key=FIXTURE_ANSWER_KEY, password=PASSWORD)
               for device_id in ('0000d001', '0000d002')]
    return write_manifest(root, 'fixture-v1', devices)

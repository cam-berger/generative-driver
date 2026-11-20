"""The hand-written device of the tool's tests: a device a client supplies.

Its binding is a line device on a local TCP port (`tests/line_device.py`). Its control endpoint answers the six
requests the tool sends, as `docs/client-interface.md` gives them: `status`, `observe`, `select_condition`,
`set_running`, `replace` and `release`. It speaks as the tool's device handle expects: a connection of
`multiprocessing.connection` with the key of the endpoint file, one request per connection, JSON bytes both ways.
It works on macOS and on Windows: a Unix socket in a short temporary folder, or a named pipe.

This is the helper of `tests/test_supplied_runs.py`.
"""
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path

from line_device import line_device

# The SHA-256 of the hand-written firmware, the bytes `supplied firmware`: the device runs it unless told otherwise.
FIRMWARE_SHA256 = hashlib.sha256(b'supplied firmware').hexdigest()
# What the line device answers: its fill level and its status code.
REPLIES = {b'L?': b'L:42\n', b'S?': b'S:7\n'}


class SuppliedDevice:
    """A supplied device under `directory`, whose endpoint file is `directory/control.json`.

    `descriptor` is its `device-descriptor/2`. `requests` lists every control request it got, as (method, params),
    and `lines` every request line its line device got. `units` are the units of its instrument readings: None
    leaves `instrument` out of the descriptor, and `values` are the readings `observe` answers. `conditions` is a
    catalog of development conditions for the descriptor. `healthy` is what `status` says, and a method named in
    `refused` is answered with a refusal. Both may be changed while the device serves. `close()` ends it."""

    def __init__(self, directory, *, replies=None, firmware_sha256=FIRMWARE_SHA256, units=None, values=None,
                 conditions=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.replies = dict(REPLIES if replies is None else replies)
        self.firmware_sha256, self.units, self.conditions = firmware_sha256, units, conditions
        self.values = values or {}
        self.requests, self.lines, self.refused, self.healthy = [], [], set(), True
        self.running, self.condition, self.released = False, None, []
        self._lock, self._closed, self._sessions = threading.Lock(), False, contextlib.ExitStack()
        self.session_id, self.binding = None, None
        self._new_session()
        self._sockets = None if os.name == 'nt' else Path(tempfile.mkdtemp(prefix='gd-s-'))
        if os.name == 'nt':
            self._endpoint = {'family': 'AF_PIPE', 'address': r'\\.\pipe\gd-supplied-' + secrets.token_hex(8)}
        else:
            self._endpoint = {'family': 'AF_UNIX', 'address': str(self._sockets / 'control.sock')}
        self._endpoint['authkey'] = secrets.token_hex(32)
        self._listener = Listener(self._endpoint['address'], family=self._endpoint['family'],
                                  authkey=bytes.fromhex(self._endpoint['authkey']))
        self.endpoint_file = self.directory / 'control.json'
        self.endpoint_file.write_text(json.dumps(self._endpoint), encoding='utf-8')
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _new_session(self):
        """Start another line device on a port of its own, as a replaced device has: a new session and binding."""
        self.binding = self._sessions.enter_context(line_device(self.replies, self.lines))
        self.session_id = 'session-' + secrets.token_hex(4)

    @property
    def descriptor(self):
        return {'schema': 'device-descriptor/2', 'control': {'endpoint_file': str(self.endpoint_file)},
                'session_id': self.session_id, 'binding': dict(self.binding), 'firmware_sha256': self.firmware_sha256,
                **({'instrument': {'units': dict(self.units)}} if self.units is not None else {}),
                **({'development_conditions': self.conditions} if self.conditions else {})}

    def sent(self, method):
        """The params of every request of `method`, in the order they came."""
        return [params for name, params in self.requests if name == method]

    def _answer(self, method, params):
        if method in self.refused:
            raise RuntimeError('The hand-written device refuses ' + method)
        if method == 'status':
            return {'session_id': self.session_id, 'binding': dict(self.binding), 'healthy': self.healthy}
        if method == 'observe':
            return {'session_id': self.session_id, 'values': dict(self.values), 'units': dict(self.units or {}),
                    **({'condition': {'id': self.condition}} if self.condition else {})}
        if method == 'select_condition':
            if params.get('expected_session_id') != self.session_id:
                raise ValueError('Another session is live')
            self.condition = params.get('condition_id')
            return {'condition_id': self.condition}
        if method == 'set_running':
            self.running = params['running']
            return {'running': self.running}
        if method == 'replace':
            old = self.session_id
            if params.get('expected_session_id') != old:
                raise ValueError('Another session is live')
            self._new_session()
            return {'schema': 'emulator-recovery/2', 'old_session_id': old, 'new_session_id': self.session_id,
                    'binding': dict(self.binding), 'old_owner_stopped': True, 'initial_state_verified': True}
        if method == 'release':
            self.released.append(params.get('session_id'))
            return {'released': params.get('session_id')}
        raise ValueError('The hand-written device answers no ' + str(method))

    def _serve(self):
        try:
            while not self._closed:
                try:
                    connection = self._listener.accept()
                except (OSError, EOFError, AuthenticationError):
                    continue
                with connection:
                    if self._closed:
                        return
                    try:
                        if not connection.poll(10):
                            continue
                        request = json.loads(connection.recv_bytes(1024 * 1024))
                        method, params = request.get('method'), request.get('params') or {}
                        with self._lock:
                            self.requests.append((method, params))
                            reply = {'ok': True, 'result': self._answer(method, params)}
                    except Exception as error:
                        reply = {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
                    with contextlib.suppress(OSError, EOFError):
                        connection.send_bytes(json.dumps(reply).encode('utf-8'))
        finally:
            self._listener.close()

    def _wake(self):
        """Connect once, so a serving thread that waits in `accept` sees that the device is closed."""
        with contextlib.suppress(OSError, EOFError, AuthenticationError):
            Client(self._endpoint['address'], family=self._endpoint['family'],
                   authkey=bytes.fromhex(self._endpoint['authkey'])).close()

    def close(self):
        """Withdraw the endpoint and stop the line devices, within ten seconds whatever the serving thread does."""
        if self._closed:
            return
        self._closed = True
        self.endpoint_file.unlink(missing_ok=True)
        # The wake-up connects from a thread of its own (12-B10). A serving thread
        # that has just answered a request ends without accepting again, and a connection nobody accepts waits in
        # its handshake for ever.
        threading.Thread(target=self._wake, daemon=True).start()
        self._thread.join(5)
        if self._thread.is_alive():
            self._listener.close()
        self._sessions.close()
        if self._sockets is not None:
            shutil.rmtree(self._sockets, ignore_errors=True)

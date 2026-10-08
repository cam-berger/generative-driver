"""The configurator's handle on a device the bench harness provides: its status, instrument readings, the
paused-between-calls window, replacement and release, over the harness's private control endpoint.

It holds no password, recipe or truth and imports nothing that decrypts one. A reading is device evidence, as a real
device's reading would be, never a grade. Stop and shutdown belong to the harness (the owner endpoint), never here."""
import contextlib
import json
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from pathlib import Path

DESCRIPTOR = 'device-descriptor/1'


class DeviceUnavailable(RuntimeError):
    """The device provider could not be reached, or refused the request: nothing reached the device through it."""


def reading(observed):
    """The flat reading a worker sees beside a live result: the instrument's values with their units, so labelled."""
    return {**observed['values'], 'units': dict(observed['units']), 'source': 'instrument'}


def descriptor_errors(device):
    """What is wrong with a `device-descriptor/1` of a provided emulator; empty when it can be used."""
    if not isinstance(device, dict) or device.get('schema') != DESCRIPTOR or device.get('kind') != 'provided-emulator':
        return ['not a device-descriptor/1 of a provided emulator']
    control = device.get('control')
    errors = [] if isinstance(control, dict) and isinstance(control.get('endpoint_file'), str) and control['endpoint_file'] \
        else ['no control endpoint file']
    if not isinstance(device.get('session_id'), str) or not device['session_id']:
        errors.append('no session_id')
    if not isinstance(device.get('binding'), dict) or not device['binding']:
        errors.append('no binding')
    if not isinstance(device.get('image_sha256'), str):
        errors.append('no image_sha256')
    return errors


class DeviceClient:
    """Control requests to one provided device. Every failure to reach it, and every refusal, is DeviceUnavailable."""

    def __init__(self, endpoint_file, timeout=60):
        self.endpoint_file, self.timeout = Path(endpoint_file), timeout

    def _call(self, method, timeout=None, **params):
        try:
            endpoint = json.loads(self.endpoint_file.read_text(encoding='utf-8'))
            with Client(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey'])) as conn:
                conn.send_bytes(json.dumps({'method': method, 'params': params}, allow_nan=False).encode('utf-8'))
                if not conn.poll(timeout or self.timeout):
                    raise TimeoutError('no answer before the deadline')
                reply = json.loads(conn.recv_bytes(4 * 1024 * 1024))
        except (OSError, EOFError, ValueError, KeyError, TypeError, AuthenticationError) as error:
            raise DeviceUnavailable('Device provider unavailable: ' + type(error).__name__ + ': ' + str(error)) from None
        if not isinstance(reply, dict) or reply.get('ok') is not True:
            reason = reply.get('reason') if isinstance(reply, dict) else None
            raise DeviceUnavailable('Device provider refused ' + method + ': ' + str(reason))
        return reply.get('result')

    def status(self):
        return self._call('status')

    def observe(self):
        """An `instrument-observation/1`: projected values and units of the live session, nothing raw."""
        return self._call('observe')

    @contextlib.contextmanager
    def window(self):
        """The device runs only inside this window; it is paused again when the call inside it returns or raises."""
        self._call('set_running', running=True)
        try:
            yield
        finally:
            self._call('set_running', running=False)

    def replace(self, expected_session_id, evidence_ids=(), dispatch_ids=(), deadline_seconds=None):
        """Replace the live session after an uncertain write; returns the `emulator-recovery/2` receipt. The proof stays
        in the provider's private journal."""
        wait = None if deadline_seconds is None else max(0.0, float(deadline_seconds))
        return self._call('replace', timeout=(wait or self.timeout) + 30, expected_session_id=expected_session_id,
                          evidence_ids=list(evidence_ids), dispatch_ids=list(dispatch_ids), deadline_seconds=wait)

    def release(self, session_id):
        """Pause the device and give up control of this session; only the harness can stop it."""
        return self._call('release', session_id=session_id)


def open_device(descriptor):
    """The client of a provided device, from its descriptor; a malformed descriptor is a ValueError."""
    errors = descriptor_errors(descriptor)
    if errors:
        raise ValueError('Invalid provided device descriptor: ' + '; '.join(errors))
    return DeviceClient(descriptor['control']['endpoint_file'])

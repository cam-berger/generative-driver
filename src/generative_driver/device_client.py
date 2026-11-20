"""The configurator's handle on a device a client supplies: its status, instrument readings, the
paused-between-calls window, replacement and release, over the client's private control endpoint.

It holds no password, recipe or truth and imports nothing that decrypts one. A reading is device evidence, as a real
device's reading would be, never a grade. Stopping the device belongs to the client that owns it, never here. The
descriptor is `device-descriptor/2`, in the tool's own terms."""
import contextlib
import json
import re
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
from pathlib import Path

DESCRIPTOR = 'device-descriptor/2'
# The parts a descriptor may have. The tool knows no other, so any other is a fault.
PARTS = ('schema', 'control', 'session_id', 'binding', 'firmware_sha256', 'instrument', 'development_conditions')


class DeviceUnavailable(RuntimeError):
    """The device provider could not be reached, or refused the request: nothing reached the device through it."""


def reading(observed):
    """The flat reading a worker sees beside a live result: the instrument's values with their units, so labelled."""
    return {**observed['values'], 'units': dict(observed['units']), 'source': 'instrument',
            'session_id': observed['session_id'],
            **({'condition': observed['condition']} if 'condition' in observed else {})}


def offers_readings(device):
    """Whether a device, as its descriptor describes it, offers workers instrument readings: its `instrument.units` name
    some. A description with no `instrument` offers them, as it always has; a device whose units name none offers
    none."""
    if not isinstance(device, dict) or 'instrument' not in device:
        return True
    return bool((device['instrument'] or {}).get('units'))


def _catalog_ok(catalog):
    """Whether `catalog` is a catalog of development conditions: an object whose `conditions` is a list of at least
    one object, each with an `id` that is text, not empty and unlike the others."""
    conditions = catalog.get('conditions') if isinstance(catalog, dict) else None
    if not isinstance(conditions, list) or not conditions:
        return False
    ids = [condition.get('id') if isinstance(condition, dict) else None for condition in conditions]
    return all(isinstance(name, str) and name for name in ids) and len(set(ids)) == len(ids)


def descriptor_errors(device):
    """The faults of the device descriptor `device` as a list of sentences, empty for a `device-descriptor/2` the
    tool can use. A descriptor of another schema gives one fault, and so does each part the tool does not know."""
    if not isinstance(device, dict) or device.get('schema') != DESCRIPTOR:
        return ['not a device-descriptor/2']
    errors = ['a device-descriptor/2 has no part named ' + str(name) for name in device if name not in PARTS]
    control = device.get('control')
    if (not isinstance(control, dict) or not isinstance(control.get('endpoint_file'), str)
            or not control['endpoint_file']):
        errors.append('no control endpoint file')
    if not isinstance(device.get('session_id'), str) or not device['session_id']:
        errors.append('no session_id')
    if not isinstance(device.get('binding'), dict) or not device['binding']:
        errors.append('no binding')
    image = device.get('firmware_sha256')
    if not isinstance(image, str) or not re.fullmatch('[0-9a-f]{64}', image):
        errors.append('no firmware_sha256')
    if 'instrument' in device:
        instrument = device['instrument']
        if (not isinstance(instrument, dict) or set(instrument) != {'units'}
                or not isinstance(instrument['units'], dict)):
            errors.append('instrument is an object with exactly units')
    if 'development_conditions' in device and not _catalog_ok(device['development_conditions']):
        errors.append('development_conditions holds a list of conditions with distinct ids')
    return errors


class DeviceClient:
    """Control requests to one supplied device. Every failure to reach it, and every refusal, is DeviceUnavailable."""

    def __init__(self, endpoint_file, timeout=60):
        self.endpoint_file, self.timeout = Path(endpoint_file), timeout

    def _exchange(self, method, params, limit):
        endpoint = json.loads(self.endpoint_file.read_text(encoding='utf-8'))
        with Client(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey'])) as conn:
            conn.send_bytes(json.dumps({'method': method, 'params': params}, allow_nan=False).encode('utf-8'))
            if not conn.poll(limit):
                raise TimeoutError('no answer before the deadline')
            return json.loads(conn.recv_bytes(4 * 1024 * 1024))

    def _call(self, method, timeout=None, **params):
        """One request under one deadline: connecting, the authkey handshake, the request and the reply. A provider
        stuck on another request accepts no connection meanwhile, and its caller still gets DeviceUnavailable in time;
        the abandoned attempt ends on a daemon thread when the provider answers or goes away."""
        limit, outcome = timeout or self.timeout, {}
        def exchange():
            try:
                outcome['reply'] = self._exchange(method, params, limit)
            except BaseException as error:
                outcome['error'] = error
        attempt = threading.Thread(target=exchange, name='device-' + method, daemon=True)
        attempt.start()
        attempt.join(limit)
        error = TimeoutError('no answer before the deadline') if attempt.is_alive() else outcome.get('error')
        if isinstance(error, (OSError, EOFError, ValueError, KeyError, TypeError, AuthenticationError)):
            raise DeviceUnavailable('Device provider unavailable: ' + type(error).__name__ + ': ' + str(error)) from None
        if error is not None:
            raise error
        reply = outcome['reply']
        if not isinstance(reply, dict) or reply.get('ok') is not True:
            reason = reply.get('reason') if isinstance(reply, dict) else None
            raise DeviceUnavailable('Device provider refused ' + method + ': ' + str(reason))
        return reply.get('result')

    def status(self):
        return self._call('status')

    def observe(self):
        """An `instrument-observation/1`: projected values and units of the live session, nothing raw."""
        return self._call('observe')

    def select_condition(self, condition_id, expected_session_id):
        """Apply one public development input without acquiring a sample or resetting the device."""
        return self._call('select_condition', condition_id=condition_id, expected_session_id=expected_session_id)

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
        """Pause the device and give up control of this session; only the client that owns it can stop it."""
        return self._call('release', session_id=session_id)


def open_device(descriptor):
    """The handle on a supplied device, from its descriptor; a descriptor with a fault is a ValueError."""
    errors = descriptor_errors(descriptor)
    if errors:
        raise ValueError('Invalid device descriptor: ' + '; '.join(errors))
    return DeviceClient(descriptor['control']['endpoint_file'])

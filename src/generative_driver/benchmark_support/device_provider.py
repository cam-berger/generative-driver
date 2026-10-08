"""The device provider: one emulated device, started, observed, recovered and stopped from its recipe alone.

It holds no password and no truth object and imports nothing that decrypts one. What leaves it is a public descriptor,
instrument readings projected to named quantities with units, and recovery receipts; startup readings and recovery
proofs stay in its private journal. It runs in-process until the bench harness owns it."""
import contextlib
import copy
import hashlib
import json
import math
import threading
import time
import uuid
from pathlib import Path

from . import instruments

DESCRIPTOR = 'device-descriptor/1'
RECEIPT = 'emulator-recovery/2'
# Keys of a decrypted case truth or a credential; a provider is given the device recipe and nothing else.
TRUTH_KEYS = ('recipe', 'contracts', 'phases', 'scenarios', 'images', 'references', 'input_hashes',
              'evaluator_password_file', 'password', 'password_file')


def observed_values(session):
    """The session's startup reading, refused unless every recipe observation was read, with its own response."""
    observation = session.observe()
    values = observation.get('values')
    expected = set(session.recipe.get('observations', {}))
    if not expected or not isinstance(values, dict) or set(values) != expected:
        raise RuntimeError('Incomplete independent startup observations')
    def valid(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                or isinstance(value, list) and bool(value) and all(valid(item) for item in value))
    reads = observation.get('reads')
    if (not isinstance(reads,list) or {r.get('name') for r in reads if isinstance(r,dict)} != expected
            or any(not isinstance(r,dict) or r.get('value') != values.get(r.get('name'))
                   or not isinstance(r.get('response'),str) or not r['response'] for r in reads)):
        raise RuntimeError('Incomplete independent startup observation reads')
    if not all(valid(value) for value in values.values()):
        raise RuntimeError('Malformed independent startup observations')
    return observation


def _sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class DeviceProvider:
    """Owns one emulated device session at a time. `start` is the session factory (NativeSession.start by default),
    called with renode, image and recipe only. Control calls are served one at a time."""

    def __init__(self, case_id, image, recipe, renode, private_dir, *, image_sha256=None, start=None):
        if not isinstance(recipe, dict) or any(key in recipe for key in TRUTH_KEYS):
            raise ValueError('A device provider takes the device recipe only, never truth or a password')
        instruments.family_for(case_id)
        self.case_id, self.image, self.renode = case_id, Path(image), renode
        self.recipe, self.private_dir, self.image_sha256 = copy.deepcopy(recipe), Path(private_dir), image_sha256
        self.provider_id, self._start, self._lock = uuid.uuid4().hex, start, threading.RLock()
        self.session = self.session_id = self.baseline = None
        self.running = self.released = False

    def _launch(self):
        """A new session of the pinned image: its bytes are checked before every start, a replacement's too."""
        actual = hashlib.sha256(self.image.read_bytes()).hexdigest()
        if self.image_sha256 is not None and actual != self.image_sha256:
            raise ValueError('Device image differs from the pinned image hash')
        self.image_sha256 = actual
        start = self._start
        if start is None:
            from .native import NativeSession
            start = NativeSession.start
        session = start(renode=self.renode, image=self.image, recipe=copy.deepcopy(self.recipe))
        session.info['session_id'] = uuid.uuid4().hex
        return session

    def _journal(self, kind, **entry):
        """Append to the private device journal; recovery proofs and startup readings live only here."""
        with (self.private_dir/'journal.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'kind': kind, **entry, 'time': time.time()}, sort_keys=True, allow_nan=False) + '\n')

    def _hold(self, session):
        self.session, self.session_id = session, session.info['session_id'] if session is not None else None

    def _live(self):
        if self.released:
            raise RuntimeError('Device session was released; no further control is served')
        if self.session is None:
            raise RuntimeError('No live device session')
        return self.session

    @property
    def binding(self):
        return dict(self._live().binding)

    def descriptor(self):
        """The public description of the held session: no monitor port, private path, process or reading."""
        session = self.session
        if session is None:
            raise RuntimeError('No live device session')
        return {'schema': DESCRIPTOR, 'kind': 'provided-emulator', 'provider_id': self.provider_id,
                'session_id': self.session_id, 'binding': dict(session.binding),
                'image_sha256': self.image_sha256,
                'instrument': {'units': instruments.units(self.case_id), 'label': 'instrument_observation'},
                'time_policy': 'paused-between-calls'}

    def start(self):
        """Start the device from the pinned image and the recipe, and keep its startup reading as the baseline a
        replacement must equal. A session whose reading fails is stopped, or kept owned when its stop is unconfirmed."""
        with self._lock:
            if self.session is not None:
                return self.descriptor()
            if self.released:
                raise RuntimeError('Device session was released; no further control is served')
            self.private_dir.mkdir(parents=True, exist_ok=True)
            self.private_dir.chmod(0o700)
            self._hold(self._launch())
            try:
                baseline = observed_values(self.session)
                self._journal('started', session_id=self.session_id, process=self.session.info, startup_observation=baseline)
            except BaseException as failure:
                try:
                    self.stop()
                except BaseException as stop_error:
                    raise RuntimeError(str(failure)+'; emulator cleanup unverified: '+str(stop_error)) from failure
                raise
            self.baseline = baseline
            return self.descriptor()

    def status(self):
        with self._lock:
            process = getattr(self.session, 'process', None)
            healthy = bool(self.session is not None and not self.released and (process is None or process.poll() is None))
            return {'provider_id': self.provider_id, 'session_id': self.session_id,
                    'binding': dict(self.session.binding) if self.session is not None else None,
                    'running': self.running, 'healthy': healthy}

    def observe(self):
        """An `instrument-observation/1` of the live session: projected values and units, nothing raw."""
        with self._lock:
            return instruments.observation(self.case_id, self._live().observe(), self.session_id)

    @contextlib.contextmanager
    def window(self):
        """The paused-between-calls time policy: the device runs only while one call is inside this window."""
        with self._lock:
            session = self._live()
            session.set_running(True)
            self.running = True
            try:
                yield
            finally:
                self.running = False
                session.set_running(False)

    def set_running(self, running):
        """One edge of the window for a remote caller, which cannot hold the window across requests."""
        with self._lock:
            session = self._live()
            self.running = bool(running)
            session.set_running(self.running)

    def replace(self, expected_session_id, evidence_ids=(), dispatch_ids=(), cancelled=None):
        """Replace the live session after an uncertain write: the old process's stop is confirmed, a distinct process
        on a new binding starts from the recipe, and its startup reading must equal the baseline. The proof goes to the
        private journal and the receipt carries its hash. A replacement that cannot be stopped stays owned."""
        cancelled = cancelled or (lambda: False)
        with self._lock:
            old = self._live()
            if old.process is None or self.baseline is None:
                raise RuntimeError('Owned emulator process handle and startup evidence are required')
            if not self.session_id or expected_session_id != self.session_id or cancelled():
                raise RuntimeError('Emulator recovery ownership is inactive')
            old_id = self.session_id
            old.stop()
            if old.process.poll() is None:
                raise RuntimeError('Old emulator stop could not be confirmed')
            self._hold(None)
            replacement = None
            try:
                if cancelled():
                    raise RuntimeError('Emulator recovery cancelled')
                replacement = self._launch()
                if (replacement is old or replacement.process is old.process or replacement.process is None
                        or replacement.process.poll() is not None):
                    raise RuntimeError('Replacement is not a distinct live owned emulator')
                if replacement.binding == old.binding:
                    raise RuntimeError('Replacement emulator binding was reused')
                observed = observed_values(replacement)
                if observed['values'] != self.baseline['values'] or cancelled():
                    raise RuntimeError('Replacement initial state is unverified or recovery cancelled')
                receipt = {'schema': RECEIPT, 'old_session_id': old_id, 'new_session_id': replacement.info['session_id'],
                           'binding': dict(replacement.binding), 'old_owner_stopped': True, 'initial_state_verified': True,
                           'observation_sha256': _sha256(observed)}
                proof = {'receipt': dict(receipt), 'old_startup_observation': self.baseline,
                         'new_startup_observation': observed, 'old_process': old.info, 'new_process': replacement.info,
                         'unresolved_evidence_ids': list(evidence_ids), 'unresolved_dispatch_ids': list(dispatch_ids)}
                self._journal('replaced', proof=proof)
                self._hold(replacement)
                return {**receipt, 'proof_sha256': _sha256(proof)}
            except BaseException:
                if replacement is not None:
                    try:
                        replacement.stop()
                    finally:
                        if replacement.process is None or replacement.process.poll() is None:
                            # Retain handles for cleanup even when a failed replacement cannot stop.
                            self._hold(replacement)
                raise

    def release(self, session_id):
        """Pause the device and serve no further control of this session; only stop remains."""
        with self._lock:
            if session_id is None or session_id != self.session_id:
                raise ValueError('Release names a session this provider does not hold')
            self.released = True
            self.running = False
            self.session.set_running(False)

    def stop(self):
        """Stop the session, confirmed by poll. An unconfirmed stop raises and keeps the handle owned; a session with no
        process handle (an attached observer) is released unconfirmed."""
        with self._lock:
            session = self.session
            if session is None:
                return {'stopped': True, 'confirmed': True}
            session.stop()
            if session.process is not None and session.process.poll() is None:
                raise RuntimeError('Owned emulator stop was not confirmed')
            stopped, confirmed = self.session_id, session.process is not None
            self._hold(None)
            self.running = False
            self._journal('stopped', session_id=stopped, confirmed=confirmed)
            return {'stopped': True, 'confirmed': confirmed}

"""The suite book: a suite's bookkeeping, kept by the bench harness and not by the configurator (Feature 07 of the
untangle Run, docs/features/untangle/07-suites.md).

The book freezes a suite, keeps it and its slots in a store of its own under the bench home (`suite_store`), decides
each slot, and serves the suite calls the configurator served before that Feature, with the params, the answers and
the refusal texts they had: it is that code, moved. It runs in the process that asks it, the bench harness's, a
command's or a reader's, and it asks the configurator only through the public calls `ping`, `start`, `status`,
`result`, `events`, `cancel`, `resume` and `seal`. A child is a plain `start` on a supplied device; the book finds a
run by its slot's request id with `status`, reads the request a run was started with from `result`, and marks a run
sealed with `seal`.

Since several processes may ask for one suite, a suite's lock is a lock file under the bench home, and so is the lock
of the whole store, under which a suite is frozen. The configurator no longer blocks its suites when it starts: the
book notes the process id `ping` answers with when a suite is started or resumed, and blocks the suite at the first
call that another process answers.

Nothing the book sends, stores or answers holds a password, a password file, a set's root or an answer: the harness
holds those, and the book is handed the sha256 of the harness's token alone."""
import contextlib
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from . import trial_store
from .suite_store import SuiteStore

# The statuses with which a run or a suite has stopped.
_TERMINAL = ('completed', 'failed', 'cancelled', 'blocked')
OLD_SUITE = ('This suite was frozen before the bench harness drove suites (it holds evaluator password files or no '
             'scheduler) and is read-only: start a new suite')
# A child whose recorded request differs from its frozen slot holds that slot's request_id for good.
_TAKEN = "; the slot's request_id is taken by that child, so start a new suite"
# What a run's `status` gained for whoever watches the run (docs/features/kit-fixes/04-service-diagnostics.md, 04-B5).
# Suite replies stay as they were, so a child's status passes into one without them.
_ADDED = ('deadline', 'repairs', 'worker', 'log')
# The refusal of a `status` asked by a request id that started no run.
_NO_SUCH_REQUEST = 'Unknown request_id'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


class _Passed(Exception):
    """A request to the configurator that it refused or that got no answer: its `reply` ends the suite call as it
    is, so a refusal keeps its `error` and a lost answer has none."""

    def __init__(self, reply):
        super().__init__(reply.get('reason'))
        self.reply = reply


def suite_candidates(cases, candidates, *, bench=None):
    """The checked configuration of each case's candidate of a replay suite (options.replay_candidates): exactly one
    saved candidate per case of its manifest, none of them in the bench home `bench`."""
    if not isinstance(candidates, dict) or set(candidates) != set(cases):
        raise ValueError('A replay suite takes one saved candidate per case: options.replay_candidates '
                         '{case: directory} for ' + ', '.join(sorted(cases)))
    return {case: trial_store.replay_candidate(candidates[case], bench) for case in sorted(cases)}


def stored_candidate(config, store):
    """The candidate of `config`, stored under `store` by its sha256, where a suite keeps the candidates it froze: the
    stored copy's configuration. Every child of the suite replays those bytes, whatever happens to the saved
    candidate."""
    from generative_driver.integrity import digest
    store = Path(store)
    target = store / config['candidate_sha256']
    if not target.is_dir():
        store.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.candidate-', dir=store))
        try:
            shutil.copytree(config['candidate'], temporary / 'copy')
            if digest(temporary / 'copy') != config['candidate_sha256']:
                raise ValueError('The replay candidate changed while it was stored: ' + config['candidate'])
            try:
                os.rename(temporary / 'copy', target)
            except OSError:
                if not target.is_dir():
                    raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    if digest(target) != config['candidate_sha256']:
        raise ValueError('The stored replay candidate changed: ' + str(target))
    # The runtime's name is the one the tool gave when it checked the candidate.
    return {'runtime': config['runtime'], 'candidate': str(target), 'candidate_sha256': config['candidate_sha256']}


def _replay_suite_config(manifest, options, home):
    """A replay suite's configuration: one saved candidate per case of its manifest (options.replay_candidates), all
    checked first, then each stored by its hash in the configurator home, from which every child of that case takes its
    copy."""
    checked = suite_candidates({entry['case'] for entry in manifest['entries']},
                               options.get('replay_candidates') if isinstance(options, dict) else None,
                               bench=trial_store.bench_home(home))
    stored = {case: stored_candidate(config, Path(home) / 'replay-candidates') for case, config in checked.items()}
    return {'runtime': 'replay', 'candidates': {case: {key: config[key] for key in ('candidate', 'candidate_sha256')}
                                                for case, config in stored.items()}}


def _harness_identity(params, refusal):
    """The sha256 of a bench harness's token, the harness's process id and the lock file it holds for as long as it runs
    (where its file system offers one), as the harness hands them over. Without a process to watch, a harness that died
    without letting go of its suite would leave the suite running, or stopping, for good."""
    token, pid, lock = params.get('harness_token_sha256'), params.get('harness_pid'), params.get('harness_lock')
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError(refusal)
    if type(pid) is not int or pid <= 0:
        raise ValueError('harness_pid must be the positive process id of the bench harness')
    if lock is not None and (not isinstance(lock, str) or not Path(lock).is_absolute()):
        raise ValueError('harness_lock must be an absolute path')
    return token, pid, lock


def _sealing(frozen):
    """What tells a suite's harness that it seals concluded trials, and under whose password: a scoring suite's set."""
    return {'scoring_set': frozen['scoring_set']['registration']} if 'scoring_set' in frozen else {}


def _attached_to(suite, token_sha256):
    """Whether the bench harness holding `token_sha256` is the one attached to the suite."""
    held = suite['harness']['token_sha256']
    return bool(suite['harness']['attached'] and isinstance(held, str) and isinstance(token_sha256, str)
                and hmac.compare_digest(held, token_sha256))


def _assistive(suite):
    """Whether every child of the suite is graded after its run, as the assistive protocol grades it: a suite its bench
    harness drives. A suite frozen before the harness names no protocol and counts no equivalent drivers."""
    from .suites import harness_driven
    return harness_driven(suite['frozen'])


def _child_start(request):
    """The `start` of a suite's child, a supplied run, from the request its bench harness hands in: the request
    without the case, its pin and its options, with the analysis tools those options name as keys of the start
    itself. The run records this start as its request."""
    start = {key: value for key, value in request.items() if key not in ('case', 'case_pin', 'case_options')}
    options = request.get('case_options')
    for key in ('ghidra_home', 'java_home'):
        if isinstance(options, dict) and key in options:
            start[key] = options[key]
    return start


def _run_differs(frozen, slot, recorded):
    """Why the run that recorded the request `recorded` (`result`'s `request`) is not the frozen slot's child, or
    None when it is: without what the bench harness adds, the request is the plain start of the slot's own
    `child_request`, and its client record names the slot's case and pin."""
    from .suites import HARNESS_KEYS, child_request
    if not isinstance(recorded, dict):
        return 'Frozen suite child identity mismatch: trial ' + str(slot.get('trial_key')) + ' has no submitted request'
    expected = child_request(frozen, slot)
    start = _child_start(expected)
    submitted = {key: value for key, value in recorded.items() if key not in HARNESS_KEYS}
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    differing = {key for key in set(submitted) | set(start)
                 if canonical(submitted.get(key)) != canonical(start.get(key))}
    record = recorded.get('client_record') if isinstance(recorded.get('client_record'), dict) else {}
    differing |= {key for key in ('case', 'case_pin') if canonical(record.get(key)) != canonical(expected[key])}
    if not differing:
        return None
    return ('Frozen suite child identity mismatch: trial ' + slot['trial_key'] + "'s child request differs from the "
            'frozen slot in ' + ', '.join(sorted(differing)))


def _as_before(reply):
    """A suite's reply as it was before a run's `status` gained fields: one that passes its child's status on drops
    them."""
    if isinstance(reply, dict) and any(key in reply for key in _ADDED):
        return {key: value for key, value in reply.items() if key not in _ADDED}
    return reply


class _FileLock:
    """One lock file: one holder at a time among the threads of this process and among processes. A thread that holds
    it takes it again at once, as it took the configurator's own lock, and the operating system lets go of it when its
    process ends, however it ends. A lock that cannot be made, opened or taken is held: the suite call is refused, and
    nothing of it goes on without the lock."""

    def __init__(self, path):
        self.path, self._mutex, self._depth, self._descriptor = Path(path), threading.RLock(), 0, None

    def __enter__(self):
        from .harness import _take
        self._mutex.acquire()
        if self._depth == 0:
            descriptor = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
                while not _take(descriptor):
                    time.sleep(.01)
            except BaseException as error:
                if descriptor is not None:
                    os.close(descriptor)
                self._mutex.release()
                if isinstance(error, OSError):
                    raise ValueError('Another suite call may be running: its lock cannot be taken ('
                                     + type(error).__name__ + ': ' + (error.strerror or str(error)) + '): '
                                     + str(self.path)) from None
                raise
            self._descriptor = descriptor
        self._depth += 1
        return self

    def __exit__(self, *exc):
        from .harness import _let_go
        self._depth -= 1
        try:
            if self._depth == 0:
                descriptor, self._descriptor = self._descriptor, None
                _let_go(descriptor)
        finally:
            self._mutex.release()
        return False


_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def _locked(path):
    """The lock of the lock file at `path`, one object for each file in this process."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), _FileLock(path))


class SuiteBook:
    """The book of the suites of the configurator whose home is `home`. `configurator(method, params)` sends a
    request to that configurator and returns its reply. `call` serves a suite call; `store` is the book's
    `SuiteStore`."""

    def __init__(self, home, configurator):
        self.home = Path(home).expanduser().resolve()
        self._configurator = configurator
        self._store = None
        # The process id the configurator's `ping` answered with for the suite call this thread serves.
        self._serving = threading.local()

    @contextlib.contextmanager
    def _db(self):
        db = sqlite3.connect(trial_store.suite_store_path(self.home), timeout=20)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=20000')
            with db:
                yield db
        finally:
            db.close()

    @property
    def store(self):
        """The book's `SuiteStore`, in the file `trial_store.suite_store_path` gives. The file and its tables are
        made when a book first needs them, for this user only, as `suite.json` is."""
        if self._store is None:
            path = trial_store.suite_store_path(self.home)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(FileExistsError):
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            self._store = SuiteStore(self._db)
        return self._store

    def call(self, method, params=None):
        """Serve the suite call `method` as the configurator served it. The book's own refusal is a `ValueError`
        with the text it had. A request to the configurator that is refused, or that gets no answer, ends the call
        with that reply as it is. The configurator's `ping` is asked first, once: no suite goes on past a restart
        of the configurator, and a configurator that does not answer fails the call as it did."""
        params = params or {}
        try:
            self._serving.pid = self._ask('ping').get('pid')
            return _as_before(self._suite_call(method, params))
        except _Passed as passed:
            return passed.reply

    @property
    def _answering(self):
        """The process id the configurator's `ping` answered with for the suite call being served, or None."""
        return getattr(self._serving, 'pid', None)

    def _ask(self, method, params=None):
        """The configurator's reply to one public call. Anything but an answer whose `ok` is true ends the suite
        call being served (`_Passed`): a refusal as it came, and a callable that raised as a reply with no `error`."""
        try:
            reply = self._configurator(method, params or {})
        except Exception as error:
            raise _Passed({'ok': False, 'reason': type(error).__name__ + ': ' + str(error)}) from None
        if not isinstance(reply, dict):
            raise _Passed({'ok': False, 'reason': 'Unreadable configurator reply'})
        if reply.get('ok') is not True:
            raise _Passed(reply)
        return reply

    def _status(self, run_id):
        return self._ask('status', {'run_id': run_id})

    def _events(self, run_id):
        """Every event of the run, in order."""
        events, cursor = [], 0
        while True:
            batch = self._ask('events', {'run_id': run_id, 'after': cursor})
            events.extend(batch['events'])
            if batch['cursor'] == cursor or len(batch['events']) < 500:
                return events
            cursor = batch['cursor']

    def _configured_executors(self):
        path = self.home / 'config.json'
        return json.loads(path.read_text(encoding='utf-8')).get('executors', {}) if path.is_file() else {}

    def _store_lock(self):
        """The lock of the whole store, under which a suite is frozen: two starts of one request make one suite."""
        return _locked(trial_store.suite_lock_path(self.home))

    def _suite_lock(self, suite_id):
        if not isinstance(suite_id, str) or not re.fullmatch('[0-9A-Za-z_-]+', suite_id):
            raise ValueError('Unknown suite_id')
        return _locked(trial_store.suite_lock_path(self.home, suite_id))

    def suite_start(self, params):
        """Freeze and store a suite for the bench harness that drives it, attached by the sha256 of its token. Nothing
        is scheduled here: the harness reserves, starts, attaches and settles each slot."""
        from generative_driver.setup import resolve_executor
        from . import tool_install
        from .snapshots import execution_provenance
        from .suites import freeze_suite, normalize_manifest, refuse_secrets
        manifest = normalize_manifest(params.get('manifest'))
        executor = resolve_executor(params.get('executor'))
        options = params.get('options', {})
        refuse_secrets(options)
        token, pid, lock = _harness_identity(
            params, 'A suite is started and driven by its bench harness: `benchmark suite start`')
        pins = params.get('device_pins')
        request = {'manifest': manifest, 'executor': executor, 'options': options,
                   **({'device_pins': pins} if pins is not None else {})}
        with self._store_lock():
            saved = self.store.find_request(params.get('request_id'), request)
            if saved is None:
                config = {**self._configured_executors().get(executor, {}), 'runtime': executor}
                if executor == 'replay':
                    config = _replay_suite_config(manifest, options, self.home)
                provenance = execution_provenance(config, case_ids=[entry['case'] for entry in manifest['entries']],
                                                  **tool_install.folders())
                frozen = freeze_suite(manifest, executor, config, options, provenance, harness_token_sha256=token,
                                      device_pins=pins)
                saved = self.store.create(frozen, params.get('request_id'), harness_pid=pid, harness_lock=lock,
                                          service_pid=self._answering)
                if not saved['duplicate']:
                    self.store.set_state(saved['suite_id'], 'running')
                    return {'ok': True, 'suite_id': saved['suite_id'], 'duplicate': False, 'attached': True,
                            **_sealing(frozen)}
            # A repeated request. Its harness, sending it again after a lost reply, is told it still drives the suite.
            return {'ok': True, 'suite_id': saved['suite_id'], 'duplicate': True,
                    'attached': _attached_to(saved, token), **_sealing(saved['frozen'])}

    @staticmethod
    def _harness_alive(suite):
        """Whether the bench harness attached to the suite is still running: by the lock it holds for as long as it
        runs, which ends with it (a process id may name another process once its harness ended); where that cannot be
        told, by its process id. An attached harness with neither is taken to have ended. The lock is asked through a
        descriptor of its own, so a book in the harness's own process finds its harness alive."""
        harness = suite['harness']
        if not harness['attached']:
            return False
        if harness.get('lock'):
            from .process_lock import process_lock_held
            held = process_lock_held(harness['lock'])
            if held is not None:
                return held
        if type(harness['pid']) is not int or harness['pid'] <= 0:
            return False
        from .process_lock import wait_for_exit
        try:
            return not wait_for_exit(harness['pid'], 0)
        except OSError:
            return True

    def _suite(self, suite_id):
        """The suite, blocked for an explicit resume if it was queued or running and another configurator process
        answers than the one it was started or resumed under, or if its harness ended without letting go of it."""
        from .suites import harness_driven
        suite = self.store.get(suite_id)
        answering, noted = self._answering, suite['service_pid']
        if (suite['status'] in ('queued', 'running') and noted is not None and answering is not None
                and answering != noted):
            with self._suite_lock(suite_id):
                self.store.recover(suite_id)
                suite = self.store.get(suite_id)
        left = lambda suite: (suite['status'] in ('queued', 'running') and suite['harness']['attached']
                              and not self._harness_alive(suite))
        if harness_driven(suite['frozen']) and left(suite):
            with self._suite_lock(suite_id):
                suite = self.store.get(suite_id)
                if left(suite):
                    self.store.set_state(suite_id, 'blocked',
                                         'The bench harness driving the suite ended; resume it through its harness')
                    self.store.detach_harness(suite_id)
                    suite = self.store.get(suite_id)
        return suite

    def _suite_driver(self, suite_id, params):
        """The suite, for the bench harness that drives it: only a suite frozen for a harness, and only with the token
        whose sha256 the suite holds."""
        from .suites import harness_driven
        suite = self._suite(suite_id)
        if not harness_driven(suite['frozen']):
            raise ValueError(OLD_SUITE)
        token, held = params.get('token'), suite['harness']['token_sha256']
        if not isinstance(token, str) or not isinstance(held, str) or \
                not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), held):
            raise ValueError('Only the bench harness driving the suite may schedule it (harness token)')
        return suite

    @staticmethod
    def _slot(suite, ordinal):
        slot = next((t for t in suite['trials'] if type(ordinal) is int and t['ordinal'] == ordinal), None)
        if slot is None:
            raise ValueError('Unknown or stale suite slot')
        return slot

    def find_request(self, request_id):
        """The `status` of the run that was started with `request_id`, or None when no run was: it reconciles an
        already created child without creating or resuming work."""
        try:
            return self._ask('status', {'request_id': request_id})
        except _Passed as passed:
            if passed.reply.get('error') == 'ValueError' and passed.reply.get('reason') == _NO_SUCH_REQUEST:
                return None
            raise

    @staticmethod
    def _unscored(events):
        """The reason of the latest unscored evaluation among a run's `events`, or None when they hold none."""
        latest = next((event for event in reversed(events) if event['kind'] == 'evaluation.unscored'), None)
        if latest is None:
            return None
        reasons = [d.get('reason') for d in latest['data'].get('diff') or [] if isinstance(d, dict)]
        return str(reasons[0]) if reasons else 'unscored'

    def _child_evaluation(self, run_id):
        """A suite child as the configurator holds it: (its `status`, with its final verdict as `equivalence`; the
        reason of its latest unscored evaluation while it still awaits its grade, or None), never the child's own
        say."""
        observed = self._status(run_id)
        return observed, None if observed['equivalence'] else self._unscored(self._events(run_id))

    def _child_mismatch(self, suite, slot, run_id):
        """Why the run is not the frozen slot's child, from the request the run recorded (see `_run_differs`), or None
        when it is."""
        return _run_differs(suite['frozen'], slot, self._ask('result', {'run_id': run_id}).get('request'))

    def _cancel_live(self, run_id):
        """Cancel a suite child that still runs; one already stopped keeps its own stop and reason."""
        if run_id and self._status(run_id)['status'] in ('queued', 'running'):
            self._ask('cancel', {'run_id': run_id})

    def _suite_reserve(self, suite_id, params):
        """The slot the harness works on next: the active one, or the next pending slot, reserved. None once the suite
        is cancelled (a child of its that still runs, however it came to run, is cancelled too), stopped, out of budget
        (it blocks and its active child is cancelled) or complete. A slot whose child exists but was never attached is
        attached here, from the child the book finds by the slot's request, and only if that child's request is the
        frozen slot's own: another blocks the suite and is cancelled."""
        from .suites import child_request
        expired = live = None
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            trial = None
            if suite['cancelled']:
                active = next((t for t in suite['trials'] if t['status'] in ('launching', 'running')), None)
                live = active and (active['run_id']
                                   or (self.find_request(active['child_request_id']) or {}).get('run_id'))
            elif suite['status'] == 'running':
                expired = self._suite_expired(suite)
                if expired is None:
                    trial = self.store.reserve(suite_id)
                    if trial is None:
                        self.store.set_state(suite_id, 'completed')
                    elif trial['run_id'] is None:
                        found = self.find_request(trial['child_request_id'])
                        mismatch = found and self._child_mismatch(suite, trial, found['run_id'])
                        if mismatch:
                            self.store.set_state(suite_id, 'blocked', mismatch + _TAKEN)
                            trial, live = None, found['run_id']
                        elif found:
                            self.store.attach(suite_id, trial['ordinal'], trial['child_request_id'], found['run_id'])
                            trial['run_id'] = found['run_id']
            status = self.store.get(suite_id)['status']
        if expired:
            # Cancellation can wait for a worker; never hold the suite lock meanwhile.
            self._ask('cancel', {'run_id': expired})
        if live:
            self._cancel_live(live)
        if trial is None:
            return {'ok': True, 'suite_id': suite_id, 'status': status, 'slot': None}
        return {'ok': True, 'suite_id': suite_id, 'status': status,
                # Fixed at freeze: the harness never reads the live table of sets for this.
                'scoring': 'scoring_set' in suite['frozen'],
                'slot': {key: trial[key] for key in ('ordinal', 'trial_key', 'case', 'device_id', 'child_request_id',
                                                     'run_id', 'attempt', 'regrade', 'audit') if key in trial},
                'request': child_request(suite['frozen'], trial)}

    def _suite_expired(self, suite):
        """Block a running suite whose wall-clock budget is spent. Returns None while it is not spent; otherwise the
        active child to cancel ('' if there is none), as the suite's budget ends its active child whatever state that
        child is in."""
        if time.time() < suite['created'] + suite['budget_seconds']:
            return None
        active = next((t for t in suite['trials'] if t['status'] in ('launching', 'running')), None)
        found = active and (active['run_id'] or (self.find_request(active['child_request_id']) or {}).get('run_id'))
        self.store.set_state(suite['suite_id'], 'blocked', 'Suite wall-clock budget exhausted')
        return found or ''

    def _suite_attach(self, suite_id, params):
        """Attach the child the harness started for a slot: only the run the book itself finds by that slot's
        request, and only if that run's request is the frozen slot's own (another blocks the suite and is cancelled). A
        child attached to a suite that was cancelled or stopped meanwhile is cancelled. Only a reserved slot takes a
        child: a run found under a pending slot's request would run beside the active one, out of order."""
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            if slot['status'] not in ('launching', 'running'):
                raise ValueError('The suite slot is not reserved')
            found = self.find_request(slot['child_request_id'])
            if not found or found['run_id'] != params.get('run_id'):
                raise ValueError('The run is not the child of this suite slot')
            stopped = suite['cancelled'] or suite['status'] != 'running'
            mismatch = self._child_mismatch(suite, slot, found['run_id'])
            if mismatch:
                mismatch += _TAKEN
                if not stopped:
                    self.store.set_state(suite_id, 'blocked', mismatch)
            else:
                self.store.attach(suite_id, slot['ordinal'], slot['child_request_id'], found['run_id'])
        if mismatch or stopped:
            # Cancellation can wait for a worker; never hold the suite lock meanwhile.
            self._cancel_live(found['run_id'])
        if mismatch:
            raise ValueError(mismatch)
        return {'ok': True, 'suite_id': suite_id, 'run_id': found['run_id'], **({'cancelled': True} if stopped else {})}

    def _suite_start_child(self, suite_id, params):
        """Start a slot's child for its harness and attach it, in one step under the suite lock: only while the suite
        runs, so a suite cancelled or stopped as its harness provided the device starts no child; only within the
        suite's wall-clock budget, so a budget spent meanwhile blocks the suite, as on every reservation, before
        anything starts; and only from the frozen slot's own request plus what the harness adds (see
        `child_identity_differs`). A request that differs blocks the suite before anything starts. The book looks for
        the slot's child first, by the slot's request id: the same start sent again after a lost reply is answered
        with the child it started, whatever the suite did since (cancelled, blocked, out of budget), and starts
        nothing: that child runs on the device of the harness that sent it, which must not stop that device under it.
        `start` is sent only when no run has the slot's request id, so a run another start made under it is refused
        as the configurator refuses a request id used with other inputs."""
        from .suites import child_identity_differs
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            mismatch = child_identity_differs(suite['frozen'], slot, params.get('request'))
            found = None if mismatch else self.find_request(slot['child_request_id'])
            started = self._started_from(slot, params.get('request'), found) if found else None
            if started:
                if slot['run_id'] is None and slot['status'] in ('launching', 'running'):
                    self.store.attach(suite_id, slot['ordinal'], slot['child_request_id'], started)
                return {'ok': True, 'run_id': started, 'duplicate': True, 'suite_id': suite_id}
            if suite['cancelled'] or suite['status'] != 'running':
                raise ValueError('The suite is ' + suite['status'] + '; no child starts for it')
            if slot['status'] not in ('launching', 'running'):
                raise ValueError('The suite slot is not reserved')
            expired = self._suite_expired(suite)
            if expired is None:
                if mismatch:
                    self.store.set_state(suite_id, 'blocked', mismatch)
                    raise ValueError(mismatch)
                if found:
                    raise ValueError('request_id was already used with different inputs')
                started = self._ask('start', _child_start(params['request']))
                self.store.attach(suite_id, slot['ordinal'], slot['child_request_id'], started['run_id'])
                return {**started, 'suite_id': suite_id}
        self._suite_spent(expired)

    def _started_from(self, slot, request, found):
        """The run id of the slot's child `found` (its `status`, by the slot's request id) when that child was started
        from exactly `request`, as the run recorded it (a harness's start sent again: the same device, firmware,
        mission and evaluation token hash), else None. The caller has checked `request` against the frozen slot."""
        if not isinstance(request, dict) or (slot['run_id'] is not None and slot['run_id'] != found['run_id']):
            return None
        recorded = self._ask('result', {'run_id': found['run_id']}).get('request')
        return found['run_id'] if recorded is not None and _json(recorded) == _json(_child_start(request)) else None

    def _suite_spent(self, expired):
        """Refuse a child's start or resume once the suite's budget is spent; the active child `_suite_expired` names,
        however it came to exist, is cancelled outside the suite lock, since cancellation can wait for a worker."""
        if expired:
            self._ask('cancel', {'run_id': expired})
        raise ValueError('Suite wall-clock budget exhausted')

    def _suite_resume_child(self, suite_id, params):
        """Resume a slot's stopped child for its harness, once the harness attached a device anew, in one step under
        the suite lock: only while the suite runs, so a suite cancelled or stopped as its harness provided the device
        resumes nothing; only within the suite's wall-clock budget (a budget spent meanwhile blocks the suite and ends
        the child, as on every reservation); and only the slot's attached child, whose request is the frozen slot's
        own. An approved child budget extension (`budget_seconds`, `budget_reason`) reaches that child's resume. The
        same resume sent again after a lost reply, for the device session the harness attached (`session_id`), finds
        the child already resumed on that device and resumes nothing."""
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            if suite['cancelled'] or suite['status'] != 'running':
                raise ValueError('The suite is ' + suite['status'] + '; its child does not resume')
            if slot['status'] != 'running' or not slot['run_id'] or slot['run_id'] != params.get('run_id'):
                raise ValueError('The run is not the attached child of this suite slot')
            expired = self._suite_expired(suite)
            if expired is None:
                mismatch = self._child_mismatch(suite, slot, slot['run_id'])
                if mismatch:
                    raise ValueError(mismatch + _TAKEN)
                if self._resumed_on(slot['run_id'], params.get('session_id')):
                    # Its harness's resume again, the first reply lost: the child was resumed on the device that harness
                    # attached, whatever it has done since (an operator may have cancelled it), and is not resumed
                    # again.
                    return {**self._status(slot['run_id']), 'duplicate': True}
                return self._ask('resume', {'run_id': slot['run_id'],
                                            **{key: params[key] for key in ('budget_seconds', 'budget_reason')
                                               if params.get(key) is not None}})
        self._suite_spent(expired)

    def _resumed_on(self, run_id, session_id):
        """Whether the run was resumed since the device of `session_id` was attached to it, the device attached last:
        by the run's events."""
        if not isinstance(session_id, str):
            return False
        attached, resumed = None, False
        for event in self._events(run_id):
            if event['kind'] == 'device.attached':
                attached, resumed = event, False
            elif event['kind'] == 'run.resumed':
                resumed = attached is not None
        return attached is not None and attached['data'].get('session_id') == session_id and resumed

    def _suite_settle(self, suite_id, params):
        """Settle a slot from what the book reads of the configurator itself, the child's status and its recorded
        evaluation; the harness names only the slot. On a scoring suite `scoring_disposition` decides, and a child that
        has already ended settles before the suite's budget is checked, so a paid-for grade is not left unsettled. On
        any other suite a child that may not advance blocks a running suite."""
        from .suites import child_disposition, scoring_disposition
        expired = None
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            if not slot['run_id']:
                raise ValueError('Settlement does not match attached child')
            scoring = 'scoring_set' in suite['frozen']
            settled = {'finished': 'advance', 'faulted': 'fault', 'flagged': 'flag'}.get(slot['status'])
            ended = scoring and not settled and self._child_ended(slot['run_id'])
            if not settled and not ended and suite['status'] == 'running' and not suite['cancelled']:
                # The suite's budget is checked before its child's outcome, as on every reservation.
                expired = self._suite_expired(suite)
            if expired is not None:
                decision = 'block'
            elif settled:
                decision = settled
            else:
                # Every child is graded after its run: the book reads its recorded evaluation from the configurator.
                observed, unscored = self._child_evaluation(slot['run_id'])
                observed = {**observed, 'unscored': unscored}
                decision = (scoring_disposition(self._budgeted(observed), slot) if scoring
                            else child_disposition(observed))
                mismatch = self._child_mismatch(suite, slot, slot['run_id']) if decision != 'wait' else None
                if mismatch:
                    decision = 'block'
                if decision in ('advance', 'fault', 'regrade', 'flag'):
                    history = decision in ('fault', 'regrade') and 'attempt' not in slot
                    self.store.settle(suite_id, slot['ordinal'], observed, disposition=decision,
                                      first_attempt=self._first_attempt(slot) if history else None)
                elif decision == 'block' and suite['status'] == 'running' and not suite['cancelled']:
                    stop = observed.get('reason')
                    reason = (mismatch + _TAKEN if mismatch else
                              'Trial ' + slot['trial_key'] + ' is unscored: ' + unscored if unscored else
                              'Trial ' + slot['trial_key'] + ' awaits its evaluation'
                              if observed.get('status') == 'completed' else
                              'Trial ' + slot['trial_key'] + ' stopped' + (': ' + str(stop) if stop else ''))
                    self.store.set_state(suite_id, 'blocked', reason)
            status = self.store.get(suite_id)['status']
        if expired:
            self._ask('cancel', {'run_id': expired})
        return {'ok': True, 'suite_id': suite_id, 'decision': decision, 'status': status}

    def _child_ended(self, run_id):
        """Whether a suite's child has ended, with nothing of it left stopping."""
        observed = self._status(run_id)
        return observed['status'] in _TERMINAL and observed['stopping'] is False

    def _budgeted(self, observed):
        """`observed` with the budget its report calls effective: an approved extension is already in the run's own
        budget, which `result` gives."""
        budget = self._ask('result', {'run_id': observed['run_id']})['budget_seconds']
        return {**observed, 'effective_budget_seconds': budget}

    def _first_attempt(self, slot):
        """A slot's first attempt, as the row keeps it once a rerun replaces it. Its tokens are None unless every worker
        reported them, and the outcome a fault, which is the only first attempt a slot keeps."""
        from .run_reporting import _usage
        run_id, tokens, seconds = slot['run_id'], None, None
        if run_id:
            result = self._ask('result', {'run_id': run_id})
            tokens = _usage(result['worker_reports'])['total_tokens']
            seconds = max(0, result['updated'] - result['created'])
        return {'run_id': run_id, 'request_id': slot['child_request_id'], 'outcome': 'fault', 'tokens': tokens,
                'seconds': seconds}

    @staticmethod
    def _reserved_for_scoring(suite, slot):
        """Refuse a scoring suite's record of a slot unless the suite is one, still runs and has the slot reserved."""
        if 'scoring_set' not in suite['frozen']:
            raise ValueError('Only a scoring suite records this: ' + suite['suite_id'])
        if suite['cancelled'] or suite['status'] != 'running':
            raise ValueError('The suite is ' + suite['status'] + '; it records nothing of a slot')
        if slot['status'] not in ('launching', 'running'):
            raise ValueError('The suite slot is not reserved')

    def _suite_no_run(self, suite_id, params):
        """Record that a scoring suite's reserved slot never got a run, with one of the closed `NO_RUN` words: the
        harness's text stays in its trial, since it may name a private path. A child that exists after all, its start's
        reply lost, is attached rather than faulted: a child whose request is not the frozen slot's blocks the suite and
        is cancelled instead."""
        from .suites import NO_RUN
        live = mismatch = None
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            self._reserved_for_scoring(suite, slot)
            if params.get('reason') not in NO_RUN:
                raise ValueError('Not a reason a slot got no run: ' + str(params.get('reason')))
            found = self.find_request(slot['child_request_id'])
            if found:
                mismatch = self._child_mismatch(suite, slot, found['run_id'])
                if mismatch:
                    mismatch += _TAKEN
                    self.store.set_state(suite_id, 'blocked', mismatch)
                    live = found['run_id']
                else:
                    self.store.attach(suite_id, slot['ordinal'], slot['child_request_id'], found['run_id'])
            else:
                settled = self.store.settle_no_run(suite_id, slot['ordinal'], params['reason'],
                                                   self._first_attempt(slot) if 'attempt' not in slot else None)
        if mismatch:
            # Cancellation can wait for a worker; never hold the suite lock meanwhile.
            self._cancel_live(live)
            raise ValueError(mismatch)
        if found:
            return {'ok': True, 'suite_id': suite_id, 'decision': 'attached', 'run_id': found['run_id']}
        return {'ok': True, 'suite_id': suite_id, 'decision': 'fault' if settled == 'faulted' else 'advance'}

    def _suite_flag(self, suite_id, params):
        """Record what the transcript audit found of a scoring suite's reserved slot, each finding by its transcript's
        name and its command's hash, so no record or call holds the set's root. They are stored first, and the slot
        moves to `flagged` only if its child has ended with nothing uncertain; otherwise `scoring_disposition` flags it
        after reconciliation. A child whose request is not the frozen slot's blocks the suite instead, as on
        settling."""
        from .suites import scoring_disposition
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            self._reserved_for_scoring(suite, slot)
            findings = params.get('findings')
            if not slot['run_id'] or not isinstance(findings, list) or not findings or not all(
                    isinstance(row, dict) and set(row) == {'transcript', 'command_sha256'}
                    and isinstance(row['transcript'], str) and isinstance(row['command_sha256'], str)
                    and re.fullmatch('[0-9a-f]{64}', row['command_sha256']) for row in findings):
                raise ValueError('A flag names a slot with a child and findings of a transcript and a command hash')
            self.store.store_audit(suite_id, slot['ordinal'], findings)
            observed = self._budgeted({**self._status(slot['run_id']), 'unscored': None})
            decision = scoring_disposition(observed, {**slot, 'audit': findings})
            mismatch = self._child_mismatch(suite, slot, slot['run_id']) if decision == 'flag' else None
            if mismatch:
                decision = 'block'
                self.store.set_state(suite_id, 'blocked', mismatch + _TAKEN)
            elif decision == 'flag':
                self.store.settle(suite_id, slot['ordinal'], observed, disposition='flag')
        return {'ok': True, 'suite_id': suite_id, 'decision': decision}

    def _suite_sealed(self, suite_id, params):
        """Mark a run of a scoring suite sealed, with the configurator's `seal`: `status` and `result` show it, and it
        no longer resumes or takes a device. It takes the slot's run or its first attempt's, and it is repeatable,
        since a crash may send it again. It needs the suite's token while a harness is attached; with none it takes
        none: it only restricts a run."""
        with self._suite_lock(suite_id):
            suite = self._suite(suite_id)
            if 'scoring_set' not in suite['frozen']:
                raise ValueError('Only a scoring suite seals its trials: ' + suite_id)
            if self._harness_alive(suite):
                self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            run_id = params.get('run_id')
            if not run_id or run_id not in (slot['run_id'], (slot.get('first_attempt') or {}).get('run_id')):
                raise ValueError('The run is not a trial of this suite slot: ' + str(run_id))
            self._ask('seal', {'run_id': run_id})
        return {'ok': True, 'suite_id': suite_id, 'run_id': run_id}

    def _suite_detach(self, suite_id, params):
        """The harness lets go of the suite; one that stops while the suite still runs leaves it blocked for a
        resume."""
        reason = params.get('reason')
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            if suite['status'] in ('queued', 'running') and not suite['cancelled']:
                self.store.set_state(suite_id, 'blocked', 'The bench harness stopped driving the suite'
                                     + (': ' + reason if isinstance(reason, str) and reason.strip() else ''))
            self.store.detach_harness(suite_id)
        return self._suite_call('suite_status', {'suite_id': suite_id})

    def _suite_cancel(self, suite_id):
        """Cancel the suite and its active child, then wait a while for its harness to let go, unless the child is still
        stopping (an operation in flight)."""
        deadline = time.monotonic() + 10
        with self._suite_lock(suite_id):
            self.store.cancel(suite_id)
            suite = self.store.get(suite_id)
            active = next((t for t in suite['trials'] if t['status'] in ('launching', 'running')), None)
            child = active['run_id'] if active else None
            if active and not child:
                found = self.find_request(active['child_request_id'])
                if found:
                    # Whatever runs under the slot's request is cancelled; only the slot's frozen child is attached.
                    child = found['run_id']
                    if not self._child_mismatch(suite, active, child):
                        self.store.attach(suite_id, active['ordinal'], active['child_request_id'], child)
        if child:
            self._ask('cancel', {'run_id': child})
        while True:
            status = self._suite_call('suite_status', {'suite_id': suite_id})
            if (not status['stopping'] or time.monotonic() >= deadline
                    or (child and self._status(child)['stopping'])):
                return status
            time.sleep(.05)

    def _verify_suite(self, suite):
        from . import tool_install
        from .families import family_for
        from .snapshots import execution_provenance
        frozen = suite['frozen']
        for pin in frozen['entry_pins']:
            if family_for(pin['case_id']).current_pin(pin) != pin:
                raise ValueError('Frozen suite case pin changed')
        cases = [entry['case'] for entry in frozen['manifest']['entries']]
        if execution_provenance(frozen['executor_config'], case_ids=cases,
                                **tool_install.folders()) != frozen['provenance']:
            raise ValueError('Frozen suite implementation or execution environment changed')
        for trial in suite['trials']:
            found = self.find_request(trial['child_request_id'])
            if trial['run_id'] and (not found or trial['run_id'] != found['run_id']):
                raise ValueError('Frozen suite child identity mismatch')
            if found:
                # The child's client record holds the slot's pin. Its bench harness checks the child's snapshot itself.
                mismatch = self._child_mismatch(suite, trial, found['run_id'])
                if mismatch:
                    raise ValueError(mismatch + _TAKEN)

    def _suite_resume(self, suite_id, params):
        """Resume a stopped suite for a newly launched bench harness, which takes over its scheduling with a token of
        its own. A suite frozen before the harness drove suites is read-only. A stopped child is left to the harness,
        which first attaches a device it provides anew and passes an approved child budget extension itself. A scoring
        suite refuses a child budget extension here, the authority, since pairing calls any such override
        incompatible. The suite goes on under the configurator that answers now, whose process id is noted."""
        from generative_driver.setup import SUPPORTED_EXECUTORS
        from .suites import NO_CHILD_EXTENSION, harness_driven
        uncertain = 'Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation'
        with self._suite_lock(suite_id):
            suite = self._suite(suite_id)
            if not harness_driven(suite['frozen']):
                raise ValueError(OLD_SUITE)
            if suite['frozen']['executor'] not in SUPPORTED_EXECUTORS:
                raise ValueError('The saved suite uses an unsupported executor. Start a new suite with Codex.')
            if suite['status'] in ('queued', 'running') and _attached_to(suite, params.get('harness_token_sha256')):
                # Its harness's resume again, the first reply lost: that harness still drives the suite.
                return {**self._suite_call('suite_status', {'suite_id': suite_id}), 'attached': True,
                        **_sealing(suite['frozen'])}
            if suite['status'] not in ('blocked', 'failed', 'cancelled'):
                raise ValueError('Only a stopped incomplete suite can resume')
            if self._suite_call('suite_status', {'suite_id': suite_id})['stopping']:
                raise ValueError('Previous suite work is still stopping')
            if 'scoring_set' in suite['frozen'] and params.get('child_budget_seconds') is not None:
                raise ValueError(NO_CHILD_EXTENSION)
            self._verify_suite(suite)
            active = next((t for t in suite['trials'] if t['status'] in ('launching', 'running')), None)
            found = self.find_request(active['child_request_id']) if active else None
            if found and found['uncertain_effect']:
                # Nothing resumes, and nothing about the suite changes, while its child's effect is unreconciled.
                raise ValueError(uncertain)
            token, pid, lock = _harness_identity(
                params, 'A suite resumes through its bench harness: `benchmark suite resume`')
            if params.get('suite_budget_seconds') is not None:
                self.store.extend_budget(suite_id, params['suite_budget_seconds'], params.get('budget_reason'))
                suite = self.store.get(suite_id)
            if time.time() >= suite['created'] + suite['budget_seconds']:
                raise ValueError('Suite budget exhausted; supply an explicitly approved larger total budget')
            if found:
                self.store.attach(suite_id, active['ordinal'], active['child_request_id'], found['run_id'])
            self.store.attach_harness(suite_id, token, pid, lock, service_pid=self._answering)
            self.store.set_state(suite_id, 'running')
        return {**self._suite_call('suite_status', {'suite_id': suite_id}), 'attached': True,
                **_sealing(suite['frozen'])}

    def _suite_call(self, method, params):
        if method == 'suite_start':
            return self.suite_start(params)
        suite_id = params.get('suite_id')
        suite = self._suite(suite_id)
        if method == 'suite_cancel':
            return self._suite_cancel(suite_id)
        if method == 'suite_resume':
            return self._suite_resume(suite_id, params)
        if method == 'suite_reserve':
            return self._suite_reserve(suite_id, params)
        if method == 'suite_attach':
            return self._suite_attach(suite_id, params)
        if method == 'suite_start_child':
            return self._suite_start_child(suite_id, params)
        if method == 'suite_resume_child':
            return self._suite_resume_child(suite_id, params)
        if method == 'suite_settle':
            return self._suite_settle(suite_id, params)
        if method == 'suite_no_run':
            return self._suite_no_run(suite_id, params)
        if method == 'suite_flag':
            return self._suite_flag(suite_id, params)
        if method == 'suite_sealed':
            return self._suite_sealed(suite_id, params)
        if method == 'suite_detach':
            return self._suite_detach(suite_id, params)
        from .run_reporting import _public
        if method == 'suite_events':
            # Each event says why the suite stopped where it did, with paths scrubbed as reports scrub them.
            page = self.store.events(suite_id, params.get('after', 0))
            for event in page['events']:
                if isinstance(event['data'].get('reason'), str):
                    event['data']['reason'] = _public(event['data']['reason'], self.home)
            return {'ok': True, **page}
        if method not in ('suite_status', 'suite_result'):
            raise ValueError('Unknown suite method: ' + str(method))
        # The slots and the harness's liveness are read together under the suite's lock, which settlement and detach
        # hold, and the harness lets go of its lock file only after it detached: a suite no longer read as stopping
        # carries the slots its harness left, never a count from before its last settlement.
        with self._suite_lock(suite_id):
            suite = self.store.get(suite_id)
            active = next((trial for trial in suite['trials'] if trial['status'] in ('launching', 'running')), None)
            child = self._status(active['run_id']) if active and active['run_id'] else None
            status = {'ok': True, 'suite_id': suite_id, **{key: suite[key] for key in ('status', 'created', 'updated')},
                      # Why the suite stopped (which child, and why it stopped or is unscored), with paths scrubbed.
                      'reason': None if suite['reason'] is None else _public(suite['reason'], self.home),
                      'active_child_id': active['run_id'] if active else None,
                      'planned': len(suite['trials']),
                      'completed': sum(trial['status'] in ('finished', 'flagged') for trial in suite['trials']),
                      # Equivalent drivers among the settled children, out of `planned`; none applies to a suite frozen
                      # before the harness drove suites, whose children were graded inside their runs.
                      'equivalent': sum(trial['status'] == 'finished'
                                        and (trial['result'] or {}).get('equivalent') is True
                                        for trial in suite['trials']) if _assistive(suite) else None,
                      'stopping': (suite['status'] in _TERMINAL and self._harness_alive(suite))
                                  or bool(child and child['stopping'])}
            if method == 'suite_status':
                return status
            page = self.store.trials(suite_id, params.get('offset', 0), params.get('limit', 50))
        return self._suite_result(suite, status, page)

    def _suite_result(self, suite, status, page):
        """A page of the suite's slots as read with its status, each with its child's interventions and, while it is
        not settled, its child's current state."""
        from .run_reporting import _interventions
        from .evidence import public_v2_report
        for trial in page['trials']:
            events = self._events(trial['run_id']) if trial['run_id'] else []
            trial['interventions'] = public_v2_report({'interventions': _interventions(events)[0]})['interventions']
            if trial['run_id'] and trial['status'] != 'finished':
                current = self._status(trial['run_id'])
                unscored = None if current['equivalence'] else self._unscored(events)
                trial['outcome_category'] = current['outcome_category']
                # A slot settled `faulted` or `flagged` keeps saying so: its child's own status is in its report.
                if (current['status'] in ('blocked', 'failed', 'cancelled', 'completed')
                        and trial['status'] not in ('faulted', 'flagged')):
                    trial['status'] = current['status']
                equivalence = current['equivalence']
                if equivalence:
                    trial.update(equivalent=equivalence.get('equivalent'), equivalence_status=equivalence.get('status'))
                elif current['status'] == 'completed':
                    # Not settled: its grade is unscored, or still pending.
                    trial['equivalence_status'] = 'unscored' if unscored else 'pending'
        return {**status, **page, 'experiment': self._suite_experiment(suite)}

    def _suite_experiment(self, suite):
        # Public provenance is an allowlist, never the private execution snapshot.
        from generative_driver.client_interface import PROTOCOL
        from .evidence import public_pin
        frozen = suite['frozen']
        manifest, provenance, config = frozen['manifest'], frozen['provenance'], frozen['executor_config']

        def scalars(value, keys):
            return {key: value[key] for key in keys if key in value and
                    (value[key] is None or type(value[key]) in (str, int, float, bool))}
        dimensions = scalars(config, ('model', 'provider', 'version', 'reasoning_effort', 'max_turns'))
        dimensions.update(runtime=frozen['executor'], **scalars(provenance, ('skills_revision', 'toolchain_revision')))
        if frozen['executor'] == 'replay':
            # The candidates a replay suite replays, by case, as a model suite names its model.
            dimensions['replay_candidates'] = {case: entry['candidate_sha256']
                                               for case, entry in config['candidates'].items()}
        policy = {key: manifest[key] for key in ('child_budget_seconds', 'suite_budget_seconds')}
        snapshots, overrides = [], []
        for trial in suite['trials']:
            child = self._ask('result', {'run_id': trial['run_id']}) if trial['run_id'] else {}
            # The bench harness of the child keeps its snapshot's hash in the child run's client record.
            snapshots.append({'trial_key': trial['trial_key'],
                              'snapshot_sha256': (child.get('client_record') or {}).get('snapshot_sha256')})
            if child and child['budget_seconds'] != policy['child_budget_seconds']:
                overrides.append({'trial_key': trial['trial_key'], 'budget_seconds': child['budget_seconds']})
        return {'schema': 'benchmark-suite-experiment/1',
                # Only a suite its bench harness drives, whose children are graded after their runs, names the
                # assistive protocol.
                'comparison_identity': {
                    **({'protocol': PROTOCOL} if _assistive(suite) else {}),
                    **({'scoring_set': frozen['scoring_set']} if 'scoring_set' in frozen else {}),
                    'manifest': manifest, 'manifest_sha256': frozen['manifest_sha256'],
                    'entry_pins': [public_pin(pin) for pin in frozen['entry_pins']],
                    'trials': frozen['trials'], 'repetitions': manifest['repetitions'],
                    'execution': frozen['entry_pins'][0]['execution'],
                    'evidence_track': frozen['entry_pins'][0]['evidence_track'], 'scope': frozen['scope'],
                    'time_policy': [scalars(pin['time_policy'], ('probe', 'package_calls', 'clock', 'limitation'))
                                    for pin in frozen['entry_pins']],
                    'evaluator_revision': provenance['evaluator_revision'],
                    'budgets': {'original': policy,
                                'effective': {**policy, 'suite_budget_seconds': suite['budget_seconds'],
                                              'child_budget_overrides': overrides}},
                    'intervention_policy': {'scoped_tool_approval': frozen['options'].get('scoped_tool_approval'),
                                            'max_model_repairs': 2}},
                # Each case's own revision, which its trials are checked against. A suite frozen before cases had their
                # own grader identities names one revision, its `evaluator_revision`, for all of them.
                **({'evaluator_revisions': provenance['evaluator_revisions']}
                   if 'evaluator_revisions' in provenance else {}),
                'dimensions': dimensions, 'execution_snapshots': snapshots}


def answered(book, method, params=None):
    """What `book` answers the suite call, for the harness, a command or a reader: what the book raises itself comes
    back in the form the configurator gave a refusal, `{'ok': False, 'error': <class>, 'reason': <text>}`."""
    try:
        return book.call(method, params)
    except Exception as error:
        return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}


def suite_call(method, params=None, home=None, *, autostart=True):
    """What the book of the configurator at `home` answers the suite call, for a command or a reader of the
    benchmark. The book's requests may start a configurator only with `autostart`, which is what that place gave its
    suite call when the configurator served it. `client.call` is looked up when this is called."""
    from generative_driver import client
    directory = Path(home or client.default_home()).expanduser().resolve()

    def configurator(name, request=None):
        return client.call(name, request or {}, home=directory, autostart=autostart)
    return answered(SuiteBook(directory, configurator), method, params)

"""The bench harness: the benchmark's operator, one detached process per emulated trial.

It holds what the configurator never does. The evaluator password file path arrives on its stdin and is never written
to disk; the case truth is unlocked only to project the device recipe; and it owns the device. In order, it:
admits the case (`require_calibration`), starts the emulator through a recipe-only provider and serves the configurator
a narrow control endpoint (status, observe, set_running, replace, release; stop and shutdown are owner-only), starts the
configurator run with the device descriptor, the admission evidence hash and the sha256 of an evaluation token it keeps
in memory, waits as a reconnecting client, stops the device once the run is terminal and seals the handoffs. Its state
lives under a bench home beside the configurator home, never inside it."""
import argparse
import hashlib
import json
import os
import runpy
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from pathlib import Path

TRIAL = 'bench-trial/1'
GOAL = 'Recover, check, package and freshly reuse the benchmark device interface for the desired functions.'
# A trial the harness has finished with; grading after the run arrives with the evaluator.
FINISHED = ('awaiting-evaluation', 'run-stopped', 'stop-unconfirmed', 'failed')
CONTROL = ('status', 'observe', 'set_running', 'replace', 'release')
OWNER = (*CONTROL, 'stop', 'shutdown')
TERMINAL = ('completed', 'failed', 'cancelled', 'blocked')


def bench_home(home=None):
    """The bench home: a sibling of the configurator home, never inside it."""
    from ..client import default_home
    home = Path(home or default_home()).expanduser().resolve()
    return home.parent / (home.name + '-bench')


def _private(path, value):
    """Write JSON readable by this user only, replacing the file in one step."""
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=1, allow_nan=False)
    for attempt in range(50):
        try:
            return os.replace(temporary, path)
        except PermissionError:  # Windows refuses to replace a file a reader holds open; the reader closes it soon.
            if attempt == 49:
                raise
            time.sleep(.05)


def read_trial(trial_dir):
    try:
        return json.loads((Path(trial_dir) / 'trial.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class DeviceServer:
    """Serves one provider on two private endpoints, one request at a time: `control.json` is the configurator's
    capability, `owner.json` the harness's (it alone may stop the device or shut the server down)."""

    def __init__(self, provider, directory):
        self.provider, self.directory = provider, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock, self._closed, self.shutdown = threading.Lock(), False, threading.Event()
        self._sockets = None if os.name == 'nt' else Path(tempfile.mkdtemp(prefix='gd-d-'))
        self._served, self._threads = {}, []
        for name, methods in (('control', CONTROL), ('owner', OWNER)):
            if os.name == 'nt':
                endpoint = {'family': 'AF_PIPE', 'address': r'\\.\pipe\gd-device-' + secrets.token_hex(8) + '-' + name}
            else:
                endpoint = {'family': 'AF_UNIX', 'address': str(self._sockets / (name + '.sock'))}
            endpoint['authkey'] = secrets.token_hex(32)
            listener = Listener(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey']))
            if os.name != 'nt':
                Path(endpoint['address']).chmod(0o600)
            _private(self.directory / (name + '.json'), endpoint)
            self._served[name] = (listener, endpoint, methods)
            thread = threading.Thread(target=self._serve, args=(name,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def endpoint_file(self, name='control'):
        return self.directory / (name + '.json')

    @property
    def descriptor(self):
        """The provider's public descriptor and the control endpoint file: no secret inline."""
        return {**self.provider.descriptor(), 'control': {'endpoint_file': str(self.endpoint_file())}}

    def handle(self, method, params, methods=CONTROL):
        if method not in methods:
            raise ValueError('Method is not served on this endpoint: ' + str(method))
        with self._lock:
            provider = self.provider
            if method == 'status':
                return provider.status()
            if method == 'observe':
                return provider.observe()
            if method == 'set_running':
                if type(params.get('running')) is not bool:
                    raise ValueError('set_running takes a boolean')
                provider.set_running(params['running'])
                return {'running': params['running']}
            if method == 'replace':
                wait = params.get('deadline_seconds')
                end = None if wait is None else time.monotonic() + float(wait)
                return provider.replace(params.get('expected_session_id'), params.get('evidence_ids') or [],
                                        params.get('dispatch_ids') or [],
                                        lambda: self._closed or (end is not None and time.monotonic() >= end))
            if method == 'release':
                provider.release(params.get('session_id'))
                return provider.status()
            result = provider.stop()
            if method == 'shutdown':
                self.shutdown.set()
            return result

    def _serve(self, name):
        listener, _, methods = self._served[name]
        try:
            while not self._closed:
                try:
                    conn = listener.accept()
                except (OSError, EOFError, AuthenticationError):
                    continue
                with conn:
                    if self._closed:
                        return
                    try:
                        if not conn.poll(10):
                            continue
                        request = json.loads(conn.recv_bytes(1024 * 1024))
                        reply = {'ok': True, 'result': self.handle(request.get('method'), request.get('params') or {}, methods)}
                    except Exception as error:
                        reply = {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
                    try:
                        conn.send_bytes(json.dumps(reply, allow_nan=False, default=str).encode('utf-8'))
                    except (OSError, EOFError):
                        pass
        finally:
            listener.close()

    def close(self, timeout=5):
        """Withdraw both endpoints; the provider and its device are untouched (stop them first)."""
        if self._closed:
            return
        self._closed = True
        for name, (_, endpoint, _) in self._served.items():
            self.endpoint_file(name).unlink(missing_ok=True)
            def wake(endpoint=endpoint):
                try:
                    Client(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey'])).close()
                except (OSError, EOFError, AuthenticationError):
                    pass
            threading.Thread(target=wake, daemon=True).start()
        for thread in self._threads:
            thread.join(timeout)
        if self._sockets is not None:
            shutil.rmtree(self._sockets, ignore_errors=True)


def admit(case_id, options, trial_dir, *, password_file, renode=None):
    """Admit the case and build its device provider from the recipe alone: (provider, admission). Only this step unlocks
    the case truth, to take the device recipe; everything else in it is dropped before the provider sees it. The caller
    starts the provider, so that it holds the device even when the start fails."""
    from .device_provider import DeviceProvider
    from .emulated import device_recipe
    from .registry import pin_case, require_calibration, resolve_case
    secret = {'evaluator_password_file': str(password_file)} if password_file else {}
    pin = pin_case(case_id, options.get('scenario_id'), options.get('case_seed', 0))
    if pin['manifest'].get('schema') != 'benchmark-case/2':
        raise ValueError('The bench harness provides emulated benchmark cases only')
    admitted = require_calibration(pin, secret)
    recipe = device_recipe(case_id, trial_dir, secret)
    provider = DeviceProvider(case_id, resolve_case(case_id).root / 'firmware.bin', recipe, renode, Path(trial_dir) / 'device',
                              image_sha256=pin['image_hashes']['firmware.bin'])
    return provider, {'calibration_evidence_sha256': admitted.get('evidence_sha256')}


def seal(case_id, run_dir, accepted, password_file):
    """Seal a terminal run's accepted handoffs with the existing run evidence sealing (until the evaluator seals
    benchmark-run-evidence/2 in the trial directory). Nothing is graded here."""
    from .emulated_evidence import finalize
    finalize(case_id, Path(run_dir), accepted, {'evaluator_password_file': str(password_file)})


def finish(provider, case_id, run_dir, accepted, password_file, status=None):
    """After the run is terminal: stop the device, confirmed, then seal the handoffs. A stop that is not confirmed keeps
    the device owned for manual reconciliation; sealing needs no device and runs either way."""
    done = {}
    try:
        done['device_stop'] = provider.stop()
    except Exception as error:
        done['device_stop'] = {'stopped': False, 'confirmed': False, 'reason': type(error).__name__ + ': ' + str(error)}
    if run_dir is not None and password_file:
        try:
            seal(case_id, run_dir, accepted, password_file)
            done['sealed'] = True
        except Exception as error:
            done.update(sealed=False, sealing_failed=type(error).__name__ + ': ' + str(error))
    done['status'] = ('stop-unconfirmed' if done['device_stop'].get('confirmed') is not True else
                      'awaiting-evaluation' if (status or {}).get('status') == 'completed' else 'run-stopped')
    return done


def _wait(home, run_id, deadline, shutdown):
    """Follow the run as a reconnecting client until it is terminal with nothing left stopping, or the owner shut the
    device down."""
    from ..client import call
    status = {}
    while time.monotonic() < deadline and not shutdown.is_set():
        observed = call('status', {'run_id': run_id}, home=home, autostart=False, read_timeout=30)
        if observed.get('ok'):
            status = observed
            if observed.get('status') in TERMINAL and not observed.get('stopping'):
                return status
        time.sleep(.25)
    return {**status, 'harness_stopped': 'owner shutdown' if shutdown.is_set() else 'deadline'}


def run_trial(trial_dir, request):
    """One trial: admit, provide the device, start the run, wait, stop the device, seal. The password file path and
    the emulator executable stay in this process's memory; trial.json never holds either."""
    from ..client import call
    trial_dir = Path(trial_dir)
    trial_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = dict(request)
    password_file, renode = request.pop('password_file', None), request.pop('renode', None)
    home = Path(request['home'])
    record = {'schema': TRIAL, 'trial_id': request['trial_id'], 'case': request['case'], 'status': 'admitting',
              'run_id': None, 'pid': os.getpid(), 'created': time.time()}
    _private(trial_dir / 'trial.json', record)
    provider = server = None
    try:
        # The configurator must be reachable before any device starts for it.
        reachable = call('ping', home=home, autostart=request.get('autostart', True))
        if not reachable.get('ok'):
            raise ValueError(reachable.get('reason') or 'The configurator is not reachable')
        provider, admission = admit(request['case'], request.get('case_options') or {}, trial_dir,
                                    password_file=password_file, renode=renode)
        provider.start()
        server = DeviceServer(provider, trial_dir / 'endpoint')
        # The token stays in this process; the configurator holds its hash for the evaluation after the run.
        token = secrets.token_hex(32)
        params = {key: request[key] for key in ('goal', 'case', 'executor', 'budget_seconds', 'effects', 'case_options',
                                                'request_id', 'output_dir', 'scoped_tool_approval')
                  if request.get(key) is not None}
        params.update(device=server.descriptor, admission=admission,
                      evaluation_token_sha256=hashlib.sha256(token.encode()).hexdigest())
        started = call('start', params, home=home, autostart=request.get('autostart', True))
        if not started.get('ok'):
            raise ValueError(started.get('reason') or 'The configurator refused the run')
        record.update(status='running', run_id=started['run_id'],
                      device={key: params['device'][key] for key in ('provider_id', 'session_id', 'binding')})
        _private(trial_dir / 'trial.json', record)
        status = _wait(home, started['run_id'], time.monotonic() + float(request.get('budget_seconds', 10800)) + 600,
                       server.shutdown)
        record['run_status'] = {key: status.get(key) for key in ('status', 'outcome_category', 'reason', 'uncertain_effect',
                                                                 'harness_stopped') if key in status}
        result = call('result', {'run_id': started['run_id']}, home=home, autostart=False, read_timeout=60)
        accepted = result.get('accepted_handoffs') if result.get('ok') else []
        done = finish(provider, request['case'], home / 'runs' / started['run_id'], accepted or [], password_file, status)
        provider = None
        record.update(done)
    except Exception as error:
        record.update(status='failed', reason=type(error).__name__ + ': ' + str(error))
    finally:
        if provider is not None:
            try:
                record['device_stop'] = provider.stop()
            except Exception as error:
                # The device stays owned and named here for manual reconciliation; it is never forgotten.
                record.update(status='stop-unconfirmed', device=provider.status(),
                              device_stop={'confirmed': False, 'reason': type(error).__name__ + ': ' + str(error)})
        if server is not None:
            server.close()
        record['finished'] = time.time()
        _private(trial_dir / 'trial.json', record)
    return record


def launch(case, output_dir=None, executor=None, options=None, approval=None, *, effects=(), autostart=True, wait=False,
           timeout=900):
    """Start an emulated trial in a detached harness and return {trial_id, run_id} once the run has started; with
    `wait`, follow the trial until the harness has finished with it. Password file and emulator paths reach the harness
    on its stdin only."""
    from ..client import default_home
    options = dict(options or {})
    home = Path(options.pop('home', None) or default_home()).expanduser().resolve()
    bench = Path(options.pop('bench_home', None) or bench_home(home)).expanduser().resolve()
    if bench == home or bench.is_relative_to(home):
        raise ValueError('The bench home must lie outside the configurator home')
    password_file, renode = options.pop('evaluator_password_file', None), options.pop('renode', None)
    budget, request_id = options.pop('budget_seconds', 10800), options.pop('request_id', None)
    chosen = options.pop('effects', list(effects))
    case_options = {key: options.pop(key) for key in ('scenario_id', 'case_seed', 'ghidra_home', 'java_home') if key in options}
    if options:
        raise ValueError('Unsupported options for an emulated benchmark trial: ' + ', '.join(sorted(options)))
    trial_id = 'trial-' + uuid.uuid4().hex[:16]
    trial_dir = bench / 'trials' / trial_id
    trial_dir.mkdir(parents=True, mode=0o700)
    request = {'trial_id': trial_id, 'home': str(home), 'autostart': autostart, 'goal': GOAL, 'case': case,
               'executor': executor or 'codex', 'budget_seconds': budget, 'effects': chosen, 'case_options': case_options,
               'request_id': request_id, 'output_dir': str(Path(output_dir).resolve()) if output_dir else None,
               'scoped_tool_approval': approval}
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    flags = ({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
             else {'start_new_session': True})
    with (trial_dir / 'harness.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-m', 'generative_driver.benchmark_support.harness',
                                    '--trial-dir', str(trial_dir)], cwd=trial_dir, env=env, stdin=subprocess.PIPE,
                                   stdout=log, stderr=log, **flags)
    threading.Thread(target=process.wait, daemon=True).start()
    try:
        process.stdin.write(json.dumps({**request, 'password_file': password_file, 'renode': renode}).encode('utf-8'))
    finally:
        process.stdin.close()
    deadline = time.monotonic() + timeout
    while True:
        record, exited = read_trial(trial_dir) or {}, process.poll() is not None
        if record.get('run_id') and (not wait or exited or record.get('status') in FINISHED):
            return {'ok': True, 'trial_id': trial_id, 'run_id': record['run_id'], 'trial_dir': str(trial_dir),
                    **({key: record.get(key) for key in ('status', 'run_status', 'device_stop', 'sealed')} if wait else {})}
        if record.get('status') == 'failed' or exited:
            raise ValueError(record.get('reason') or 'The bench harness exited before the run started; see '
                             + str(trial_dir / 'harness.log'))
        if not record.get('run_id') and time.monotonic() > deadline:
            raise TimeoutError('The bench harness did not start the run in time; see ' + str(trial_dir / 'harness.log'))
        time.sleep(.2 if record.get('run_id') else .1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m generative_driver.benchmark_support.harness')
    parser.add_argument('--trial-dir', required=True)
    args = parser.parse_args(argv)
    # Tests only: a fixture file that substitutes the case resources, admission and device session factory.
    fixture = os.environ.get('GD_TEST_HARNESS_FIXTURE')
    if fixture:
        runpy.run_path(fixture, run_name='gd_harness_fixture')
    run_trial(Path(args.trial_dir), json.loads(sys.stdin.buffer.read().decode('utf-8')))


if __name__ == '__main__':
    main()

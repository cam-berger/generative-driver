"""The bench harness: the benchmark's operator, one detached process per trial or per suite.

It holds what the configurator never does. The evaluator password file path arrives on its stdin and is never a field of
a record it writes; the case truth is unlocked before the run to project the device recipe (for a bound device, to
authenticate the password) and to grade after it; and it owns an emulated case's device. In order, it: admits the case
(`require_calibration`); for an emulated case (v2, or legacy tq9 from the legacy recipe) starts the emulator through a
recipe-only provider and serves the configurator a narrow control endpoint (status, observe, set_running, replace,
release; stop and shutdown are owner-only); starts the configurator run with the device descriptor, the admission
evidence hash and the sha256 of a random evaluation token it keeps sealed under the password; waits as a reconnecting
client; stops the device once the run is terminal; grades a completed run's frozen driver once, on a fresh emulator or
from the fresh reuse the run recorded; seals the run evidence and posts the verdict with `record_evaluation`. A bme280
trial runs on the operator's bound device, which the harness never touches: it waits while the operator answers the run
and resumes it, until the run completes, and a trial whose harness stopped waiting first is graded by `resume` once the
run has completed. Its state lives under a bench home beside the configurator home, never inside it.

A suite's harness (`run_suite`) drives the suite the configurator stores: it reserves each slot, runs that slot's trial as
above, and has the configurator settle the slot from what the configurator reads itself. A resumed suite gets a new
harness, which continues a stopped trial on a device it provides anew (`reattach`) or grades an unscored one once."""
import argparse
import contextlib
import errno
import hashlib
import json
import os
import runpy
import secrets
import shutil
import socket
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
# A trial the harness has finished with. `awaiting-evaluation` and `unscored` trials are completed by `resume`.
FINISHED = ('evaluated', 'unscored', 'awaiting-evaluation', 'run-stopped', 'stop-unconfirmed', 'failed')
# What a resume may still complete: the device stopped, confirmed, and the run's verdict not yet recorded.
RESUMABLE = ('evaluating', 'awaiting-evaluation', 'unscored', 'evaluated')
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


def legacy_recipe(truth):
    """The legacy tq9 emulator's recipe, projected from the legacy truth: its sensor model and Renode setup, and the PWM
    timer's compare and reload registers read through the monitor. Nothing else of the truth goes with it."""
    read = lambda address: {'command': 'sysbus ReadDoubleWord 0x{:08x}'.format(int(address)), 'width': 32, 'count': 1}
    return {'source_files': {'sensor.cs': truth['source_files']['BME280Bench.cs'], 'device.resc': truth['renode_setup']},
            'includes': ['sensor.cs'], 'setup': 'device.resc',
            'observations': {'compare': read(truth['monitor']['pwm_compare']), 'reload': read(truth['monitor']['pwm_reload'])}}


def device_recipe(case_id, trial_dir, options):
    """The device definition and nothing else: the harness unlocks the case truth for it with the password file in
    `options`, and everything but the recipe is dropped before a provider sees it."""
    from .evaluator import case_truth
    truth = case_truth(case_id, trial_dir, options)
    return legacy_recipe(truth) if case_id == 'tq9' else truth['recipe']


def admit(case_id, options, trial_dir, *, password_file, renode=None):
    """Admit the case and, for an emulated one, build its device provider from the recipe alone: (provider or None,
    admission). Only this step unlocks the case truth, to take the device recipe; everything else in it is dropped
    before the provider sees it. A v2 case's admission names its calibration evidence; legacy cases have none. A bound
    device (bme280) is the operator's: no provider, and the truth is unlocked only to authenticate the password, so a
    password that could not grade the run refuses it before it starts. The caller starts the provider, so that it holds
    the device even when the start fails."""
    from .device_provider import DeviceProvider
    from .evaluator import case_truth
    from .registry import pin_case, require_calibration, resolve_case
    secret = {'evaluator_password_file': str(password_file)} if password_file else {}
    pin = pin_case(case_id, options.get('scenario_id'), options.get('case_seed', 0))
    if not pin['execution'].startswith('actual-agent-'):
        raise ValueError('The bench harness runs actual-agent benchmark cases only')
    admitted = require_calibration(pin, secret)
    if pin['execution'] != 'actual-agent-emulation':
        case_truth(case_id, trial_dir, secret)
        return None, {}
    recipe = device_recipe(case_id, trial_dir, secret)
    provider = DeviceProvider(case_id, resolve_case(case_id).root / 'firmware.bin', recipe, renode, Path(trial_dir) / 'device',
                              image_sha256=pin['image_hashes']['firmware.bin'])
    return provider, ({'calibration_evidence_sha256': admitted.get('evidence_sha256')}
                      if pin['manifest'].get('schema') == 'benchmark-case/2' else {})


TOKEN = 'evaluation-token.enc'


def evaluation_token(trial_dir, password_file, trial_id, *, create=False):
    """The trial's evaluation token: 32 random bytes the harness creates before the run starts (`create`) and seals under
    the evaluator password in the trial directory, with the truth envelope's key derivation, so that a harness resuming
    the trial with the password unlocks it again. The configurator holds only its sha256, which tells nothing about the
    password, and no plaintext copy is written. A password that does not unlock it is refused."""
    from .truth import seal, unlock
    path = Path(trial_dir) / TOKEN
    if not password_file:
        raise ValueError('The bench harness needs the evaluator password file to seal and unlock the trial\'s evaluation token')
    password = Path(password_file).read_text(encoding='utf-8').strip()
    if not password:
        raise ValueError('A nonempty evaluator password is required')
    if create:
        if path.exists():
            raise ValueError('The trial already holds an evaluation token: ' + str(path))
        token = secrets.token_hex(32)
        seal({'schema': 'bench-evaluation-token/1', 'trial_id': trial_id, 'token': token}, path, password)
        return token
    try:
        sealed = unlock(path, password, hashlib.sha256(path.read_bytes()).hexdigest())
    except FileNotFoundError:
        raise ValueError('The trial holds no evaluation token: ' + str(path)) from None
    except ValueError:
        raise ValueError('The evaluator password does not unlock the trial\'s evaluation token') from None
    sealed = sealed if isinstance(sealed, dict) else {}
    token = sealed.get('token')
    if (sealed.get('schema'), sealed.get('trial_id')) != ('bench-evaluation-token/1', trial_id) or not (
            isinstance(token, str) and len(token) == 64 and all(c in '0123456789abcdef' for c in token)):
        raise ValueError('The evaluation token in ' + str(path) + ' is not this trial\'s')
    return token


def _post(call, method, params):
    """One configurator request; a refusal or an unreachable configurator is a reply with its reason, never a raise."""
    try:
        reply = call(method, params)
    except Exception as error:
        return {'ok': False, 'reason': type(error).__name__ + ': ' + str(error)}
    return reply if isinstance(reply, dict) else {'ok': False, 'reason': 'Unreadable configurator reply'}


def conclude(trial_dir, case_id, run_dir, result, *, password_file, renode=None, token=None, call=None, graded=True):
    """What the harness does with a terminal run whose handoffs it read. A completed run whose verdict is pending is
    graded once (`graded` False when its device stop was not confirmed: then nothing is graded), the run evidence is
    sealed in the trial directory, and the verdict, or an unscored grade, is posted with record_evaluation; any other
    run's handoffs are sealed alone. No grade is posted that was not sealed first; an unscored one carries none. A record
    the configurator's equivalence/1 schema would refuse, or accepted records that no longer match their hashes at the
    seal, are posted unscored: no retry would change them. The plaintext evaluation never outlives this call."""
    from ..configurator import IntegrityError, _evaluation_errors
    from . import evaluator
    if result.get('equivalence') is not None:
        # The configurator already holds this run's verdict: nothing is graded, sealed or posted again. Its seal is the
        # evidence the verdict names, when the trial directory holds exactly that (a harness may have died after the
        # post, before it wrote the seal down).
        done = {'status': 'evaluated', 'equivalence': result['equivalence']}
        named = result['equivalence'].get('evidence_sha256') if isinstance(result['equivalence'], dict) else None
        try:
            held = hashlib.sha256((Path(trial_dir) / evaluator.EVIDENCE).read_bytes()).hexdigest()
        except OSError:
            held = None
        if named and held == named:
            done.update(sealed=True, evidence={'path': evaluator.EVIDENCE, 'sha256': named})
        return done
    pending = result.get('status') == 'completed' and (result.get('progress') or {}).get('equivalence') == 'pending'
    accepted = result.get('accepted_handoffs') or []
    frozen = (evaluator.emitted(accepted) or (None, None))[1]
    method = evaluator.method_for(case_id)
    done, content, equivalence = {}, {}, None
    try:
        if pending and graded:
            try:
                equivalence, content['final'] = evaluator.grade(case_id, trial_dir, run_dir, result,
                                                                password_file=password_file, renode=renode)
                content['mission'] = equivalence.get('mission')
            except IntegrityError as error:
                equivalence, content['error'] = evaluator.unscored('integrity: ' + str(error), frozen, method), str(error)
            except evaluator.Unscorable as error:
                # Recorded evidence that cannot grade the driver for a reason outside it; its message names no truth.
                equivalence, content['error'] = evaluator.unscored(str(error), frozen, method), str(error)
            except Exception as error:
                # The message stays sealed: it may name what only the evaluator holds.
                equivalence = evaluator.unscored('evaluator host fault: ' + type(error).__name__, frozen, method)
                content['error'] = type(error).__name__ + ': ' + str(error)
            errors = _evaluation_errors({**equivalence, 'evidence_sha256': None if equivalence['status'] == 'unscored'
                                         else '0' * 64})
            if errors:
                # Every resume would rebuild the same record and have it refused: it is unscored, and why stays sealed.
                equivalence = evaluator.unscored('evaluator host fault: invalid evaluation record', frozen, method)
                content = {'error': 'Invalid evaluation record: ' + '; '.join(errors)}
            content['equivalence'] = equivalence
        try:
            done.update(sealed=True, evidence=evaluator.seal(trial_dir, run_dir, result, password_file=password_file,
                                                             **content))
        except IntegrityError as error:
            done.update(sealed=False, sealing_failed='integrity: ' + str(error))
            if equivalence is not None and not (equivalence['status'] == 'unscored'
                                                and equivalence['diff'][0]['reason'].startswith('integrity: ')):
                # An accepted record changed after acceptance, or the snapshot no longer verifies: no retry seals it, so
                # the grade is unscored for integrity. An unscored record needs no seal.
                equivalence = evaluator.unscored('integrity: ' + str(error), frozen, method)
        except Exception as error:
            done.update(sealed=False, sealing_failed=type(error).__name__ + ': ' + str(error))
    finally:
        # Whatever ends grading or sealing, an interrupt included, the plaintext copies and evaluation output go.
        shutil.rmtree(Path(trial_dir) / 'private', ignore_errors=True)
    if equivalence is None:
        return done
    equivalence = {**equivalence, 'evidence_sha256': done['evidence']['sha256'] if done['sealed'] else None}
    done['equivalence'] = equivalence
    if not done['sealed'] and equivalence['status'] != 'unscored':
        # A grade is posted only once it is sealed; a resume seals it again from the draft and never grades again.
        done['status'] = 'awaiting-evaluation'
        return done
    posted = _post(call, 'record_evaluation', {'run_id': result['run_id'], 'token': token, 'equivalence': equivalence})
    if posted.get('ok') is True:
        done['status'] = 'unscored' if equivalence['status'] == 'unscored' else 'evaluated'
    else:
        # Graded and sealed, not recorded: a resume posts it again and never grades again.
        done.update(status='unscored' if equivalence['status'] == 'unscored' else 'awaiting-evaluation',
                    evaluation_failed=str(posted.get('reason')))
    return done


def finish(provider, case_id, run_dir, result, password_file, status=None, *, trial_dir=None, renode=None, token=None,
           call=None, persist=None):
    """After the run is terminal: stop the device, confirmed, then conclude the trial (`run_dir` None concludes nothing:
    the harness did not read the run's handoffs). A stop that is not confirmed keeps the device owned and names its live
    session and binding, which a recovery may have replaced since the run started, for manual reconciliation; nothing is
    graded then, and the handoffs are sealed all the same. `persist(update)` records the stop before any grading."""
    done = {}
    try:
        # A bound device is the operator's: the harness holds none, so nothing of it is left to stop.
        done['device_stop'] = provider.stop() if provider is not None else dict(UNHELD)
    except Exception as error:
        done['device_stop'] = {'stopped': False, 'confirmed': False, 'reason': type(error).__name__ + ': ' + str(error)}
        try:
            done['device'] = provider.status()
        except Exception:
            pass
    confirmed = done['device_stop'].get('confirmed') is True
    completed = (status or {}).get('status') == 'completed'
    done['status'] = ('stop-unconfirmed' if not confirmed else 'awaiting-evaluation' if completed else 'run-stopped')
    if run_dir is not None and trial_dir is not None and password_file:
        if confirmed and completed:
            done['status'] = 'evaluating'
        if persist:
            persist(dict(done))
        concluded = conclude(trial_dir, case_id, run_dir, result, password_file=password_file, renode=renode, token=token,
                             call=call, graded=confirmed)
        done.update({key: value for key, value in concluded.items() if key != 'status' or confirmed})
        if confirmed and 'status' not in concluded:
            done['status'] = 'awaiting-evaluation' if completed else 'run-stopped'
    return done


# How long a harness waits through an unreachable configurator before it stops waiting for a run.
UNREACHABLE = 300
# How long past the end of a run's wall-clock budget a harness keeps following the run.
GRACE = 600
# The device stop of a trial whose device the operator owns: the harness held none.
UNHELD = {'confirmed': True, 'held': False}


def _configurator(home, autostart=True):
    """The harness's requests to the configurator at `home`. Only ping and the start or resume of a run, suite or suite
    child may start a configurator, and only these are sent again once the connection drops (each finds what an earlier
    one did); status and result reconnect with a deadline. Once a home that existed is removed, requests fail without
    recreating it."""
    from ..client import call
    home = Path(home)
    starting = ('ping', 'start', 'suite_start', 'suite_resume', 'suite_start_child', 'suite_resume_child')
    existed = home.is_dir()
    def configurator(method, params=None):
        if existed and not home.is_dir():
            return {'ok': False, 'gone': True, 'reason': 'The configurator home was removed: ' + str(home)}
        if method in ('status', 'result'):
            return call(method, params or {}, home=home, autostart=False, read_timeout=30 if method == 'status' else 60)
        return call(method, params or {}, home=home, autostart=autostart if method in starting else False)
    return configurator


def _wait(call, run_id, deadline, shutdown, heartbeat=None, stop=None, until=TERMINAL):
    """Follow the run as a reconnecting client until its status is in `until` (terminal by default) with nothing left
    stopping, the owner shut the device down or the harness was asked to stop (`stop`); a configurator unreachable for
    long ends the wait too. `heartbeat()` runs between reads."""
    status, lost = {}, None
    halted = lambda: shutdown.is_set() or (stop is not None and stop.is_set())
    while time.monotonic() < deadline and not halted():
        observed = _post(call, 'status', {'run_id': run_id})
        if observed.get('ok'):
            status, lost = observed, None
            if observed.get('status') in until and not observed.get('stopping'):
                return status
        else:
            lost = lost or time.monotonic()
            if observed.get('gone') or time.monotonic() - lost > UNREACHABLE:
                return {**status, 'harness_stopped': 'configurator unreachable'}
        if heartbeat:
            heartbeat()
        time.sleep(.25)
    return {**status, 'harness_stopped': 'owner shutdown' if shutdown.is_set() else 'harness stopped' if halted() else 'deadline'}


def _unread(status, result):
    """Why the run's handoffs cannot be sealed, or None. The harness seals only a run it saw finish, with nothing left
    stopping, and whose handoffs it read: a partial or empty seal would read as a good one."""
    if 'harness_stopped' in status:
        return 'run not terminal: the harness stopped waiting (' + status['harness_stopped'] + ')'
    if result.get('ok') is not True:
        return 'run result unavailable: ' + str(result.get('reason'))
    if result.get('status') not in TERMINAL or result.get('stopping'):
        return 'run not terminal: ' + ('stopping' if result.get('stopping') else str(result.get('status')))
    return None


def run_trial(trial_dir, request, *, call=None, launch=None, heartbeat=None, stop=None):
    """One trial: admit, provide the device, start the run, wait, stop the device, grade a completed run once, seal,
    post the verdict. The password file path and the emulator executable stay in this process's memory; trial.json holds
    neither as a field, nor the evaluation token, though the reason a failure records there quotes the error met, which
    may name either path. A suite's harness passes its child's start request (`start` in `request`), its configurator
    requests (`call`), `launch(params)`, which starts and attaches the child through its suite, a `heartbeat()` while
    the run runs and a `stop` event that ends the wait. A suite child's start that got no answer is sent again (see
    `_start_child`), so a lost reply never has the device stopped under the child it started; trial.json records that the
    start was sent (`start_sent`) before it is. The harness holds a lock file in `trial_dir` for as long as it runs, by
    which others tell that it still runs (its process id alone may come to name another process)."""
    from ..client import hold_process_lock
    trial_dir = Path(trial_dir)
    trial_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = dict(request)
    password_file, renode = request.pop('password_file', None), request.pop('renode', None)
    home = Path(request['home'])
    configurator = call or _configurator(home, request.get('autostart', True))
    start = request.get('start')
    # Every start carries a request_id, so a start whose reply was lost and is sent again reconnects to its run.
    request_id = (start or {}).get('request_id') or request.get('request_id') or request['trial_id']
    record = {'schema': TRIAL, 'trial_id': request['trial_id'], 'request_id': request_id, 'case': request['case'],
              'home': str(home), 'status': 'admitting', 'run_id': None, 'pid': os.getpid(), 'created': time.time()}
    # Held before the record names this process, and let go of only once it says the harness finished.
    lock = hold_process_lock(trial_dir / HARNESS_LOCK)
    _private(trial_dir / 'trial.json', record)
    held, server = {'provider': None}, None
    try:
        # The configurator must be reachable before any device starts for it.
        reachable = configurator('ping', {})
        if not reachable.get('ok'):
            raise ValueError(reachable.get('reason') or 'The configurator is not reachable')
        held['provider'], admission = admit(request['case'], (start or request).get('case_options') or {}, trial_dir,
                                            password_file=password_file, renode=renode)
        if held['provider'] is not None:
            held['provider'].start()
            server = DeviceServer(held['provider'], trial_dir / 'endpoint')
        # The configurator holds the token's hash, for the evaluation after the run; the token stays in this process and,
        # for a resume, sealed under the password in the trial directory.
        token = evaluation_token(trial_dir, password_file, request['trial_id'], create=True)
        params = dict(start) if start else {key: request[key] for key in ('goal', 'case', 'executor', 'budget_seconds',
                                                                          'effects', 'case_options', 'output_dir',
                                                                          'scoped_tool_approval', 'binding')
                                           if request.get(key) is not None}
        params.update(request_id=request_id, evaluation_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                      **({'device': server.descriptor} if server else {}), **({'admission': admission} if admission else {}))
        # From here a run may exist that this trial started, whether or not its reply arrives.
        record['start_sent'] = time.time()
        _private(trial_dir / 'trial.json', record)
        started = (_start_child(launch, params, server.shutdown if server else threading.Event(), stop) if launch
                   else configurator('start', params))
        if not started.get('ok'):
            raise ValueError(started.get('reason') or 'The configurator refused the run')
        # A bound device is the operator's: the record names the stop of a device the harness never held from the start,
        # so a harness lost while it follows the run leaves a trial `resume` grades once the run has completed.
        record.update(status='running', run_id=started['run_id'],
                      **({'device': {key: params['device'][key] for key in ('provider_id', 'session_id', 'binding')}}
                         if server else {'device_stop': dict(UNHELD)}))
        _private(trial_dir / 'trial.json', record)
        _follow_run(trial_dir, record, held, server, configurator, budget=params.get('budget_seconds') or 10800,
                    password_file=password_file, renode=renode, token=token, heartbeat=heartbeat, stop=stop)
    except Exception as error:
        record.update(status='failed', reason=type(error).__name__ + ': ' + str(error))
    finally:
        try:
            _release_trial(trial_dir, record, held, server)
        finally:
            if lock is not None:
                lock.close()
    return record


def _start_child(launch, params, shutdown, stop=None):
    """Start a suite's child through its suite (`launch(params)`), sending the start again for as long as the configurator
    gives no answer: a dropped connection or a reply that never came (a client that sends nothing again itself, or one
    whose own resend failed). The configurator answers a start it already did with the child that start started,
    whatever the suite did since, so sending it again starts nothing twice and tells the harness of a child that runs on
    its device. Its answer is a run or its refusal, a ValueError, which ends it; so do a removed configurator home, the
    owner's shutdown of the device, a `stop`, and a configurator unreachable for longer than UNREACHABLE."""
    lost = None
    while True:
        started = _post(lambda _method, request: launch(request), 'suite_start_child', params)
        if started.get('ok') is True or started.get('error') == 'ValueError' or started.get('gone'):
            return started
        lost = lost or time.monotonic()
        if time.monotonic() - lost > UNREACHABLE or shutdown.is_set() or (stop is not None and stop.is_set()):
            return started
        time.sleep(.25)


def _follow_run(trial_dir, record, held, server, configurator, *, budget, password_file, renode, token, heartbeat=None,
                stop=None):
    """See a started run through: wait until it ends, then stop its device, confirmed, and conclude the trial. The device
    `held` holds passes to `finish`, which records its stop before anything is graded. A run on the operator's bound
    device (no `server`) is followed until it completes or is cancelled: the operator answers and resumes it in between,
    and may extend its budget, which extends the wait."""
    home, run_id = Path(record['home']), record['run_id']
    deadline = time.monotonic() + float(budget) + GRACE
    while True:
        status = _wait(configurator, run_id, deadline, server.shutdown if server else threading.Event(), heartbeat, stop,
                       until=TERMINAL if server else ('completed', 'cancelled'))
        if server is not None or status.get('harness_stopped') != 'deadline':
            break
        # The run's own budget, which the operator's resume may have extended, ends the wait: not the one it started with.
        current = _post(configurator, 'result', {'run_id': run_id})
        remaining = (float(current['created']) + float(current['budget_seconds']) + GRACE - time.time()
                     if current.get('ok') is True and isinstance(current.get('created'), (int, float))
                     and isinstance(current.get('budget_seconds'), (int, float)) else 0)
        if remaining <= 0:
            break
        deadline = time.monotonic() + remaining
    record['run_status'] = {key: status.get(key) for key in ('status', 'outcome_category', 'reason', 'uncertain_effect',
                                                             'harness_stopped') if key in status}
    result = configurator('result', {'run_id': run_id})
    unread = _unread(status, result)
    def persist(update):
        # The device stop is recorded before anything is graded; a crash after it leaves a trial a resume completes.
        held['provider'] = None
        record.update(update)
        _private(trial_dir / 'trial.json', record)
    done = finish(held['provider'], record['case'], None if unread else home / 'runs' / run_id, result, password_file,
                  status, trial_dir=trial_dir, renode=renode, token=token, persist=persist, call=configurator)
    held['provider'] = None
    if unread:
        # The device is stopped all the same; nothing is sealed that the harness did not read from a finished run.
        done.update(sealed=False, sealing_failed=unread)
    _settle(record, done)


def _release_trial(trial_dir, record, held, server):
    """Whatever ended the trial: a device still held is stopped, or named for manual reconciliation; never forgotten."""
    if held['provider'] is not None:
        try:
            record['device_stop'] = held['provider'].stop()
        except Exception as error:
            # The device stays owned and named here for manual reconciliation; it is never forgotten.
            record.update(status='stop-unconfirmed', device=held['provider'].status(),
                          device_stop={'confirmed': False, 'reason': type(error).__name__ + ': ' + str(error)})
    if server is not None:
        server.close()
    record['finished'] = time.time()
    _private(trial_dir / 'trial.json', record)


def reattach(trial_dir, *, password_file, renode=None, call=None, heartbeat=None, stop=None, budget_seconds=None,
             budget_reason=None, resume_run=None):
    """Continue a trial whose run stopped before it ended, once the device it ran on was stopped, confirmed: provide a
    device anew from the recipe, attach it to the stopped run with the trial's evaluation token (`attach_device`; the
    configurator refuses a run with anything uncertain), resume the run and see it through as a new trial is. Only the
    password the trial ran with unlocks its token: under any other nothing starts. An approved budget extension
    (`budget_seconds`, `budget_reason`) reaches the run's resume; a suite's harness resumes its child through the suite
    (`resume_run(params)`). From the moment a device may run, the trial record names it with an unconfirmed stop, so a
    harness lost meanwhile leaves a device to reconcile, never the earlier confirmed stop. It holds the trial's
    evaluation lock throughout, which every resume, reattachment or adoption of the trial takes before it asks whether
    the trial's harness still runs: the process id the record names from then on is a diagnostic."""
    trial_dir = Path(trial_dir)
    if read_trial(trial_dir) is None:
        raise ValueError('No trial with a run to continue: ' + str(trial_dir))
    with _exclusive(trial_dir):
        record = read_trial(trial_dir)
        if not isinstance(record, dict) or not record.get('run_id') or not record.get('home'):
            raise ValueError('No trial with a run to continue: ' + str(trial_dir))
        if not _harness_exited(None, record, trial_dir):
            raise ValueError('The trial\'s bench harness is still running')
        if (record.get('device_stop') or {}).get('confirmed') is not True:
            raise ValueError('The trial\'s device stop was not confirmed; reconcile its device before continuing the run')
        if record['device_stop'].get('held') is False:
            # Refused before the record changes, so the trial stays one `resume` grades once the run has completed.
            raise ValueError('A bound-device trial has no device to provide anew: its operator resumes the run, and '
                             'trial-resume grades it once the run has completed')
        token = evaluation_token(trial_dir, password_file, record['trial_id'])
        configurator = call or _configurator(Path(record['home']), False)
        result = _post(configurator, 'result', {'run_id': record['run_id']})
        if result.get('ok') is not True or result.get('status') not in ('blocked', 'failed', 'cancelled') or result.get('stopping'):
            raise ValueError('The trial\'s run is not stopped: ' + str(result.get('status') or result.get('reason')))
        pin = result.get('case_pin') or {}
        record.pop('finished', None)
        record.update(status='attaching', pid=os.getpid(), reattached=time.time())
        _private(trial_dir / 'trial.json', record)
        held, server = {'provider': None}, None
        try:
            held['provider'], _ = admit(record['case'], {'scenario_id': pin.get('scenario_id'), 'case_seed': pin.get('case_seed', 0)},
                                        trial_dir, password_file=password_file, renode=renode)
            if held['provider'] is None:
                raise ValueError('A bound-device trial has no device to provide anew: its operator resumes the run')
            for key in ('device', 'reason', 'run_status', 'sealed', 'sealing_failed', 'evidence', 'equivalence',
                        'evaluation_failed'):
                record.pop(key, None)
            # The earlier stop no longer describes the trial's device: the stop of the one provided now is recorded when
            # it is stopped (confirmed at once if it never started).
            record['device_stop'] = {'confirmed': False, 'reason': 'A device is being provided anew for the run'}
            _private(trial_dir / 'trial.json', record)
            held['provider'].start()
            server = DeviceServer(held['provider'], trial_dir / 'endpoint')
            record['device'] = {key: server.descriptor[key] for key in ('provider_id', 'session_id', 'binding')}
            _private(trial_dir / 'trial.json', record)
            attached = configurator('attach_device', {'run_id': record['run_id'], 'token': token, 'device': server.descriptor})
            if not attached.get('ok'):
                raise ValueError('The configurator refused the device: ' + str(attached.get('reason')))
            params = {'run_id': record['run_id'], **({'budget_seconds': budget_seconds, 'budget_reason': budget_reason}
                                                     if budget_seconds is not None else {})}
            # A suite's resume names the session attached here, so the same resume sent again after a lost reply finds
            # the child already resumed on it.
            resumed = (resume_run({**params, 'session_id': server.descriptor['session_id']}) if resume_run
                       else configurator('resume', params))
            if not resumed.get('ok') and not _resumed_here(configurator, record['run_id']):
                raise ValueError('The configurator did not resume the run: ' + str(resumed.get('reason')))
            record['status'] = 'running'
            _private(trial_dir / 'trial.json', record)
            _follow_run(trial_dir, record, held, server, configurator,
                        budget=budget_seconds or result.get('budget_seconds') or 10800,
                        password_file=password_file, renode=renode, token=token, heartbeat=heartbeat, stop=stop)
        except Exception as error:
            record.update(status='failed', reason=type(error).__name__ + ': ' + str(error))
        finally:
            _release_trial(trial_dir, record, held, server)
    return record


def _resumed_here(configurator, run_id):
    """Whether the run, whose resume got no reply or a refusal, was resumed all the same: a reply lost after the
    configurator resumed the run must not have the harness stop the device under it. The run was stopped when the
    device was attached here, under the trial's lock and with its token; only a resume ends that stop, and it resumes
    the run on the device attached last, this one. A run already stopped again whose worker is still stopping (its
    suite cancelled meanwhile, say) was resumed too, and keeps the device until nothing of it runs."""
    observed = _post(configurator, 'status', {'run_id': run_id})
    return observed.get('ok') is True and (observed.get('status') in ('queued', 'running', 'completed')
                                           or observed.get('stopping') is True)


def resume(trial_dir, *, password_file, renode=None, call=None):
    """Complete the evaluation of a trial whose harness has exited after its device stop was confirmed: one that died
    after the stop, could not grade (unscored, an evaluator host fault) or could not post its verdict. A final the draft
    already holds is rebuilt, never graded again; a verdict the configurator holds is never posted again. Grading needs
    the emulator executable (`renode`); without it a resume that would grade refuses before it unlocks anything. A
    password that does not unlock the trial's evaluation token is refused before anything is graded, sealed or posted.
    A trial on the operator's bound device, of which the harness held nothing, is completed too when its harness
    stopped following the run (its budget's deadline passed, it was stopped, or it died) and the operator has since
    completed or cancelled the run; while the run is still stopped, the operator resumes it first. So is a trial
    recorded `run-stopped` whose run has since completed awaiting its grade (stopped after fresh reuse, by a restart
    say, and resumed): grading uses a fresh emulator, so the confirmed stop of the trial's device is all it needs."""
    from ..client import call as client_call
    from . import evaluator
    trial_dir = Path(trial_dir)
    if read_trial(trial_dir) is None:
        raise ValueError('No trial with a run to evaluate: ' + str(trial_dir))
    with _exclusive(trial_dir):
        # Read under the lock: an evaluation that held it may have just completed the trial.
        record = read_trial(trial_dir)
        if not isinstance(record, dict) or not record.get('run_id') or not record.get('home'):
            raise ValueError('No trial with a run to evaluate: ' + str(trial_dir))
        if not _harness_exited(None, record, trial_dir):
            raise ValueError('The trial\'s bench harness is still running')
        if (record.get('device_stop') or {}).get('confirmed') is not True:
            raise ValueError('The trial\'s device stop was not confirmed; reconcile its device before evaluating')
        # A bound-device trial whose harness stopped following its run: the operator may have completed the run since.
        followed = record['device_stop'].get('held') is False and record.get('status') in ('run-stopped', 'running')
        if record.get('status') not in (*RESUMABLE, 'run-stopped') and not followed:
            raise ValueError('The trial awaits no evaluation: ' + str(record.get('status')))
        # Only the password the trial ran with unlocks its token: under any other, nothing is graded, sealed or posted.
        token = evaluation_token(trial_dir, password_file, record['trial_id'])
        home = Path(record['home'])
        configurator = call or (lambda method, params: client_call(method, params, home=home, autostart=False))
        result = _post(configurator, 'result', {'run_id': record['run_id']})
        unread = _unread({}, result)
        if unread:
            raise ValueError('Cannot evaluate the trial\'s run: ' + unread)
        if followed and result.get('status') not in ('completed', 'cancelled'):
            raise ValueError('The trial\'s run has not completed (' + str(result.get('status')) + '): its operator answers '
                             'and resumes the run on the bound device, and the trial is evaluated once it has completed')
        if (record.get('status') == 'run-stopped' and not followed
                and (result.get('status') != 'completed' or (result.get('progress') or {}).get('equivalence') != 'pending')):
            raise ValueError('The trial awaits no evaluation: run-stopped, and its run (' + str(result.get('status'))
                             + ') does not await its grade')
        if renode is None and evaluator.needs_grading(trial_dir, result, password_file=password_file):
            raise ValueError('The trial\'s frozen driver is not graded yet: grading starts a fresh emulator, so pass its '
                             'executable (--renode)')
        done = conclude(trial_dir, record['case'], home / 'runs' / record['run_id'], result, password_file=password_file,
                        renode=renode, token=token, call=configurator)
        _settle(record, {**done, 'status': done.get('status') or 'run-stopped', 'resumed': time.time()})
        _private(trial_dir / 'trial.json', record)
    return record


def _settle(record, done):
    """Record what an evaluation attempt established: it replaces what an earlier attempt left. A failure is reported
    only by the attempt that met it, and the seal by the attempt that sealed (one that found the verdict already
    recorded keeps the seal that verdict names)."""
    for key in ('sealing_failed', 'evaluation_failed', *(('sealed', 'evidence') if 'sealed' in done else ())):
        record.pop(key, None)
    record.update(done)


LOCK = 'evaluation.lock'
# The lock file a trial's or a suite's harness holds in its directory for as long as it runs (see `_harness_exited`).
HARNESS_LOCK = 'harness.lock'
# The byte of the lock file that the evaluation lock locks on Windows, far past the text the file holds: Windows forbids
# every other open file to read or write a locked byte, and the text names the holder to whoever finds the lock held. On
# POSIX the lock covers the whole file and forbids nothing.
LOCKED_BYTE = 1 << 30


def _lock_text(path):
    """A lock file's content: None when there is none, '' when it cannot be read."""
    try:
        return Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return ''


def _lock_holder(text):
    """The process id a lock names, or None when it names none (a lock that cannot be read or is still being written).
    It is a diagnostic: once the process that took the lock ended, its id may name another."""
    parts = (text or '').split()
    return int(parts[0]) if parts and parts[0].isdigit() and int(parts[0]) > 0 else None


def _put(source, target):
    """Move `source` to `target`, which must not exist (FileExistsError): a file so placed is whole from the moment it
    exists."""
    if os.name == 'nt':
        os.rename(source, target)  # Windows refuses to rename onto an existing file.
        return
    try:
        os.link(source, target)  # Unlike a rename, a link never replaces an existing file.
    except FileExistsError:
        raise
    except OSError:
        # A filesystem without hard links: create the file exclusively, then write it.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(Path(source).read_bytes())
    os.unlink(source)


def _take(descriptor):
    """Take the evaluation lock of an open lock file without waiting: True once taken, False while another open file
    holds it, in another process or in this one. An OSError when the file system can neither take nor test it."""
    if os.name == 'nt':
        import msvcrt
        os.lseek(descriptor, LOCKED_BYTE, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EDEADLOCK):
                return False
            raise
        return True
    import fcntl
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _let_go(descriptor):
    """Let go of the evaluation lock and close its file. Windows unlocks first: a lock left to the close may outlast it."""
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(descriptor, LOCKED_BYTE, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


def _names(path, descriptor):
    """Whether `path` names the file open as `descriptor`."""
    try:
        return os.path.samestat(os.fstat(descriptor), os.stat(path))
    except FileNotFoundError:
        return False


@contextlib.contextmanager
def _exclusive(trial_dir):
    """One evaluation of a trial at a time: each holds an operating-system lock on the trial's lock file for the whole
    evaluation, which the system lets go of when its process ends, however it ends. The file names the process and a
    nonce, for diagnostics only: once that process ended, its id may name another process, or one this user may not
    signal. A lock that cannot be taken or tested is held. An evaluation removes only its own lock."""
    path = Path(trial_dir) / LOCK
    mine = str(os.getpid()) + ' ' + uuid.uuid4().hex
    for _ in range(3):
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
            taken = _take(descriptor)
            # A lock let go of and removed after this open locks a file the path no longer names. Windows removes no
            # open file.
            current = taken and (os.name == 'nt' or _names(path, descriptor))
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise ValueError('Another evaluation of this trial may be running: its lock cannot be taken ('
                             + type(error).__name__ + ': ' + (error.strerror or str(error)) + '): ' + str(path)) from None
        if current:
            break
        os.close(descriptor)
        if not taken:
            holder = _lock_holder(_lock_text(path))
            raise ValueError('Another evaluation of this trial is running (it holds the lock ' + str(path)
                             + (', which names process ' + str(holder) if holder else '') + ')')
    else:
        raise ValueError('Cannot take the evaluation lock of ' + str(trial_dir))
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, mine.encode('utf-8'))
        yield
    finally:
        # Removed while still held, so that no other evaluation holds the file removed; Windows removes no file another
        # has open, so there it is removed once let go of, unless another evaluation opened it meanwhile.
        own = _lock_text(path) == mine
        if own and os.name != 'nt':
            path.unlink(missing_ok=True)
        _let_go(descriptor)
        if own and os.name == 'nt':
            with contextlib.suppress(OSError):
                path.unlink()


def launch(case, output_dir=None, executor=None, options=None, approval=None, *, effects=(), autostart=True, wait=False,
           timeout=900, via_owner=False):
    """Start a trial in a detached harness and return {trial_id, run_id} once the run has started; with `wait`, follow
    the trial until the harness has finished with it. Password file and emulator paths reach the harness on its stdin
    only. A repeated request_id with the same inputs follows the trial it started, with no second harness or device;
    with other inputs it is refused."""
    from ..client import default_home
    options = dict(options or {})
    home = Path(options.pop('home', None) or default_home()).expanduser().resolve()
    bench = Path(options.pop('bench_home', None) or bench_home(home)).expanduser().resolve()
    if bench == home or bench.is_relative_to(home):
        raise ValueError('The bench home must lie outside the configurator home')
    # Relative paths name files from the caller's directory; the harness itself runs in the trial directory.
    password_file, renode = (str(Path(path).expanduser().resolve()) if path else None
                             for path in (options.pop('evaluator_password_file', None), options.pop('renode', None)))
    budget, request_id = options.pop('budget_seconds', 10800), options.pop('request_id', None)
    chosen = options.pop('effects', list(effects))
    # A bound device's binding is the operator's, for the configurator's run; the harness never opens it.
    binding = options.pop('binding', None)
    case_options = {key: options.pop(key) for key in ('scenario_id', 'case_seed', 'ghidra_home', 'java_home') if key in options}
    if options:
        raise ValueError('Unsupported options for a benchmark trial: ' + ', '.join(sorted(options)))
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
        raise ValueError('request_id must be a nonblank string')
    from ..setup import configured_executor
    trial_id = 'trial-' + uuid.uuid4().hex[:16]
    request = {'trial_id': trial_id, 'home': str(home), 'autostart': autostart, 'goal': GOAL, 'case': case,
               'executor': configured_executor(executor, home), 'budget_seconds': budget, 'effects': chosen, 'case_options': case_options,
               'request_id': request_id, 'output_dir': str(Path(output_dir).resolve()) if output_dir else None,
               'scoped_tool_approval': approval, 'binding': binding}
    if request_id is not None:
        held = _claim(bench, request_id, trial_id, {key: request[key] for key in IDENTITY})
        if held is not None:
            return _follow(bench / 'trials' / held, None, wait, timeout, duplicate=True)
    trial_dir = bench / 'trials' / trial_id
    trial_dir.mkdir(parents=True, mode=0o700)
    try:
        payload = {**request, 'password_file': password_file, 'renode': renode}
        process = (_spawn_via_owner(trial_dir, 'trial', payload, home) if via_owner else
                   _spawn(trial_dir, ['--trial-dir', str(trial_dir)], payload))
    except (OSError, ValueError) as error:
        # A claimed request_id then reports this failure instead of waiting for a harness that never ran.
        _private(trial_dir / 'trial.json', {'schema': TRIAL, 'trial_id': trial_id, 'request_id': request_id or trial_id,
                                            'case': case, 'status': 'failed', 'run_id': None, 'finished': time.time(),
                                            'reason': 'The bench harness did not start: ' + str(error)})
        raise
    return _follow(trial_dir, process, wait, timeout, duplicate=False)


def _spawn(directory, arguments, payload):
    """Start a detached harness process in `directory`, like the configurator's own start (an argv list; a new session
    on POSIX, a detached process on Windows), and hand it `payload` on its stdin, never on its command line."""
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    flags = ({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
             else {'start_new_session': True})
    with (Path(directory) / 'harness.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-m', 'generative_driver.benchmark_support.harness', *arguments],
                                   cwd=directory, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, **flags)
    threading.Thread(target=process.wait, daemon=True).start()
    try:
        process.stdin.write(json.dumps(payload).encode('utf-8'))
    finally:
        process.stdin.close()
    return process


def _spawn_via_owner(directory, kind, payload, home):
    """Launch outside a Windows MCP client's kill-on-close job. The independent owner
    receives only launch metadata; credentials travel directly from this client to
    the harness over a one-shot authenticated loopback connection, never on disk.
    """
    from ..client import call
    token = secrets.token_hex(32)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(30)
        channel = {'schema':'bench-launch-channel/1', 'port':listener.getsockname()[1], 'token':token}
        reply = call('benchmark_spawn', {'directory':str(directory), 'kind':kind, 'channel':channel},
                     home, autostart=False)
        if not reply.get('ok'):
            raise ValueError(reply.get('reason') or 'The background owner refused the harness launch')
        with listener.accept()[0] as connection:
            connection.settimeout(30)
            with connection.makefile('rb') as stream:
                if not secrets.compare_digest(stream.readline(66).rstrip(b'\n'), token.encode('ascii')):
                    raise ValueError('The bench harness launch connection did not authenticate')
                connection.sendall(json.dumps(payload).encode('utf-8'))
                connection.shutdown(socket.SHUT_WR)
    return None  # Its process is reaped by the owner that launched it.


def _launch_payload(request):
    """The owner's stdin envelope names a direct handoff, without evaluator secrets."""
    if request.get('schema') != 'bench-launch-channel/1':
        return request
    with socket.create_connection(('127.0.0.1', request['port']), timeout=30) as connection:
        connection.sendall(request['token'].encode('ascii') + b'\n')
        with connection.makefile('rb') as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Bench harness launch payload is too large')
        return json.loads(raw.decode('utf-8'))


# What makes two launches the same request; never a password file or emulator path.
IDENTITY = ('home', 'case', 'executor', 'budget_seconds', 'effects', 'case_options', 'output_dir', 'scoped_tool_approval',
            'binding')
SUITE_IDENTITY = ('home', 'manifest', 'executor', 'options')


def _claim_path(bench, request_id, kind='trial'):
    claims = bench / ('requests' if kind == 'trial' else 'suite-requests')
    claims.mkdir(parents=True, exist_ok=True, mode=0o700)
    return claims / (hashlib.sha256(request_id.encode('utf-8')).hexdigest() + '.json')


def _claim(bench, request_id, held_id, identity, kind='trial'):
    """Claim request_id for a new trial (or suite harness), once, under the bench home. Returns None when this launch
    claimed it, or the trial id (suite directory) that holds it when the inputs are the same; other inputs under it are
    refused."""
    identity = json.loads(json.dumps(identity))
    field = 'trial_id' if kind == 'trial' else 'suite_dir'
    path = _claim_path(bench, request_id, kind)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        held = None
        for _ in range(100):  # A concurrent launch may still be writing its claim.
            try:
                held = json.loads(path.read_text(encoding='utf-8'))
                break
            except (OSError, ValueError):
                time.sleep(.05)
        if not isinstance(held, dict) or held.get('request_id') != request_id:
            raise ValueError('The trial claim of this request_id cannot be read: ' + str(path))
        if held.get('identity') != identity:
            raise ValueError('request_id was already used with different inputs')
        return held[field]
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump({'schema': 'bench-' + kind + '-request/1', 'request_id': request_id, field: held_id,
                   'identity': identity}, stream, indent=1)
    return None


def _harness_exited(process, record, directory=None):
    """Whether the harness of the trial or suite in `directory` has ended: by its process handle when this caller
    started it, otherwise by its record or by the lock it holds there for as long as it runs, which ends with its
    process however that ends; its recorded process id may by then name another process, or one this user may not
    signal. The id is read only where no such lock tells: a harness from before harnesses held one, or a file system
    without such locks. (A harness that continues a suite's trial holds the trial's evaluation lock instead: see
    `reattach`.)"""
    if process is not None:
        return process.poll() is not None
    if record.get('finished') is not None:
        return True
    if type(record.get('pid')) is not int or record['pid'] <= 0:
        return False
    from ..client import _wait_for_exit, process_lock_held
    lock = None if directory is None else Path(directory) / HARNESS_LOCK
    held = process_lock_held(lock) if lock is not None and lock.exists() else None
    if held is not None:
        return not held
    try:
        return _wait_for_exit(record['pid'], 0)
    except OSError:
        return False


def _unconfirmed(trial_dir, record):
    """The failure of a trial that could not confirm its device stopped: the emulator may still run, so name it."""
    device = record.get('device') or {}
    return ('The device stop was not confirmed; the emulator may still be running. Reconcile it from the trial record '
            + str(trial_dir / 'trial.json') + ' (session ' + str(device.get('session_id')) + ', binding '
            + json.dumps(device.get('binding'), sort_keys=True) + '). ' + str(record.get('reason') or ''))


def _follow(trial_dir, process, wait, timeout, *, duplicate):
    """Follow a trial until its run has started or, with `wait`, until the harness has finished with it."""
    deadline = time.monotonic() + timeout
    while True:
        record = read_trial(trial_dir) or {}
        exited = _harness_exited(process, record, trial_dir)
        if record.get('run_id') and (not wait or exited or record.get('status') in FINISHED):
            return {'ok': True, 'trial_id': trial_dir.name, 'run_id': record['run_id'], 'trial_dir': str(trial_dir),
                    'duplicate': duplicate,
                    **({key: record.get(key) for key in ('status', 'run_status', 'device_stop', 'sealed', 'sealing_failed',
                                                         'evidence', 'equivalence', 'evaluation_failed')}
                       if wait else {})}
        if record.get('status') == 'stop-unconfirmed':
            raise ValueError(_unconfirmed(trial_dir, record))
        if record.get('status') == 'failed' or exited:
            raise ValueError(record.get('reason') or 'The bench harness exited before the run started; see '
                             + str(trial_dir / 'harness.log'))
        if not record.get('run_id') and time.monotonic() > deadline:
            raise TimeoutError('The bench harness did not start the run in time; see ' + str(trial_dir / 'harness.log'))
        time.sleep(.2 if record.get('run_id') else .1)


SUITE = 'bench-suite/1'


def read_suite(suite_dir):
    try:
        return json.loads((Path(suite_dir) / 'suite.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _expect(reply, refusal):
    if reply.get('ok') is not True:
        raise ValueError(refusal + ': ' + str(reply.get('reason') or 'no reply'))
    return reply


def _trial_of(suites, run_id, request_id=None):
    """The trial directory, under any suite of this bench home, whose trial ran `run_id`, or None. A trial whose start the
    configurator never answered names no run: failing a trial that names it, the latest trial that sent a start under
    the slot's child request (`request_id`, unique to the slot) and learnt of no run is the one that started it, since
    the configurator starts a slot's child once and no trial is made for a slot whose child exists."""
    latest = None
    for path in sorted(Path(suites).glob('*/trials/*/trial.json')):
        record = read_trial(path.parent)
        if not isinstance(record, dict):
            continue
        if record.get('run_id') == run_id:
            return path.parent
        if (request_id is not None and record.get('request_id') == request_id and record.get('run_id') is None
                and record.get('start_sent') and (latest is None or record['start_sent'] > latest[0])):
            latest = (record['start_sent'], path.parent)
    return latest[1] if latest else None


def _adopt(trial_dir, run_id, completed):
    """Name `run_id` in the trial that started it but never heard so (`_trial_of`), once its harness has ended, so a
    resume can continue the run on a device provided anew, or grade it once if it `completed`. The trial keeps why its
    start failed (`start_failed`). Its device was stopped without the harness seeing the run finish; a run that met the
    stopped device holds an uncertain effect, which the configurator blocks on until the operator reconciles it."""
    record = read_trial(trial_dir)
    if isinstance(record, dict) and record.get('run_id') == run_id:
        return
    with _exclusive(trial_dir):
        record = read_trial(trial_dir)
        if not isinstance(record, dict) or record.get('run_id') == run_id:
            return
        if record.get('run_id') is not None or not record.get('start_sent'):
            raise ValueError('The trial in ' + str(trial_dir) + ' did not start child ' + run_id)
        if not _harness_exited(None, record, trial_dir):
            raise ValueError('The trial\'s bench harness is still running')
        record.update(run_id=run_id, start_failed=record.pop('reason', None), adopted=time.time())
        if completed and (record.get('device_stop') or {}).get('confirmed') is True:
            record['status'] = 'awaiting-evaluation'
        _private(Path(trial_dir) / 'trial.json', record)


def _release_claim(bench, request_id, held):
    """Let go of a suite request_id claim held by `held`, a suite directory whose harness ended without the
    configurator's reply on record (see `_refused`), so a launch with the same inputs starts afresh. The claim is moved
    aside first, and removed only if it is still the one let go of: a claim another launch took meanwhile is put back."""
    path = _claim_path(bench, request_id, 'suite')
    aside = path.with_name('.' + path.name + '.refused.' + uuid.uuid4().hex)
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return  # Another launch let go of it first.
    try:
        moved = json.loads(aside.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        moved = None
    if not isinstance(moved, dict) or moved.get('suite_dir') != held:
        with contextlib.suppress(OSError):
            _put(aside, path)  # Never over a claim made since.
    aside.unlink(missing_ok=True)


def _refused(suite_dir):
    """Whether the suite harness in `suite_dir` ended without recording that the configurator took its suite on: it was
    refused, or it ended (killed, say) before it wrote the reply down, though the configurator may have frozen the suite.
    Either way a launch with the same inputs may start afresh: the configurator finds a suite frozen under the
    request_id and reports it, attached to no new harness, so the operator learns its suite_id and can resume it."""
    record = read_suite(suite_dir)
    return isinstance(record, dict) and record.get('accepted') is False and _harness_exited(None, record)


def run_suite(suite_dir, request, *, call=None, stop=None):
    """One bench harness run of a suite. It starts the suite on the configurator (or resumes a stopped one, given its
    `suite_id`) under a fresh random token, of which the configurator keeps only the sha256, and then works through the
    suite one slot at a time: reserve the slot, run its trial (a child on a device this harness provides, graded once
    after its run), and have the configurator settle the slot from the child's status and recorded evaluation, which it
    reads itself. It stops once the configurator reports the suite complete, stopped or blocked, and lets go of it; a
    harness that stops while the suite runs leaves it blocked for a resume. Password file and emulator paths are never
    fields of suite.json or the suite tables, and no child's run receives them. The reason a failure records in
    suite.json (written for this user only, in the bench home) quotes the error met, which may name either path. The
    configurator learns of a failure only which trial failed and where this record is. An approved child budget
    extension in `request` goes to the stopped child this run continues, and to no other. The harness holds a lock file
    in `suite_dir` for as long as it runs, by which the configurator tells that it still runs (its process id alone may
    come to name another process)."""
    from ..client import hold_process_lock
    suite_dir = Path(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = dict(request)
    passwords, renode = dict(request.pop('password_files', None) or {}), request.pop('renode', None)
    extension = {key: request[key] for key in ('child_budget_seconds', 'child_budget_reason') if request.get(key) is not None}
    home = Path(request['home'])
    configurator = call or _configurator(home, request.get('autostart', True))
    token = secrets.token_hex(32)
    record = {'schema': SUITE, 'home': str(home), 'suite_id': request.get('suite_id'), 'request_id': request.get('request_id'),
              'status': 'starting', 'accepted': False, 'pid': os.getpid(), 'created': time.time(), 'settled': []}
    _private(suite_dir / 'suite.json', record)
    lock = hold_process_lock(suite_dir / 'harness.lock')
    attached, failure, current = False, None, None
    try:
        reachable = configurator('ping', {})
        if not reachable.get('ok'):
            raise ValueError(reachable.get('reason') or 'The configurator is not reachable')
        identity = {'harness_token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'harness_pid': os.getpid(),
                    **({'harness_lock': str((suite_dir / 'harness.lock').resolve())} if lock else {})}
        if record['suite_id']:
            reply = configurator('suite_resume', {'suite_id': record['suite_id'], **identity,
                                                  **{key: request[key] for key in ('suite_budget_seconds', 'budget_reason')
                                                     if request.get(key) is not None}, **extension})
        else:
            # Every start carries a request_id, so a start the client sends again after a lost reply finds its suite.
            reply = configurator('suite_start', {'manifest': request.get('manifest'), 'executor': request.get('executor'),
                                                 'options': request.get('options') or {},
                                                 'request_id': request.get('request_id') or 'harness-' + uuid.uuid4().hex,
                                                 **identity})
        if reply.get('ok') is not True:
            raise ValueError(reply.get('reason') or 'The configurator refused the suite')
        # A duplicate start drives nothing, unless the configurator says this harness is the one attached to it.
        attached = bool(reply.get('attached')) or not reply.get('duplicate')
        record.update(status='running', accepted=True, suite_id=reply['suite_id'], duplicate=bool(reply.get('duplicate')),
                      reply={key: value for key, value in reply.items() if key != 'reason'})
        _private(suite_dir / 'suite.json', record)
        beat = {'at': 0.0}
        def heartbeat():
            # While a child runs, the configurator holds the suite to its budget and its cancellation on reservation.
            if time.monotonic() - beat['at'] >= .2:
                beat['at'] = time.monotonic()
                _post(configurator, 'suite_reserve', {'suite_id': record['suite_id'], 'token': token})
        while attached and not (stop is not None and stop.is_set()):
            reserved = _expect(configurator('suite_reserve', {'suite_id': record['suite_id'], 'token': token}),
                               'The configurator refused the reservation')
            slot = reserved.get('slot')
            if slot is None:
                record['suite_status'] = reserved.get('status')
                break
            current = slot['trial_key']
            scheduling = {'suite_id': record['suite_id'], 'token': token, 'ordinal': slot['ordinal']}
            # The configurator starts, or resumes, the slot's child only while the suite runs, under the suite's lock.
            _suite_trial(suite_dir, slot, reserved.get('request'), configurator, home=home, passwords=passwords,
                         renode=renode, heartbeat=heartbeat, stop=stop, extension=extension if slot['run_id'] else {},
                         launch=lambda params, scheduling=scheduling: configurator('suite_start_child', {**scheduling, 'request': params}),
                         resume_run=lambda params, scheduling=scheduling: configurator('suite_resume_child', {**scheduling, **params}))
            if slot['run_id']:
                extension = {}
            settled = _expect(configurator('suite_settle', {'suite_id': record['suite_id'], 'token': token,
                                                            'ordinal': slot['ordinal']}), 'The configurator refused the settlement')
            current = None
            record['settled'].append({'ordinal': slot['ordinal'], 'decision': settled.get('decision')})
            _private(suite_dir / 'suite.json', record)
            if settled.get('decision') == 'block':
                record['suite_status'] = settled.get('status')
                break
        record['status'] = 'finished'
    except Exception as error:
        failure = str(error) if isinstance(error, ValueError) else type(error).__name__ + ': ' + str(error)
        record.update(status='failed', reason=failure)
    finally:
        if attached:
            # The failure text may name a path handed to this harness alone (a password file, the emulator executable):
            # the configurator is told which trial failed and where this record is, never the text itself.
            public = (('trial ' + current + ' failed' if current else 'the harness failed') + '; see '
                      + str(suite_dir / 'suite.json')) if failure else None
            record['detached'] = _post(configurator, 'suite_detach', {'suite_id': record['suite_id'], 'token': token,
                                                                       **({'reason': public} if public else {})}).get('ok') is True
        record['finished'] = time.time()
        _private(suite_dir / 'suite.json', record)
        if lock:
            lock.close()
    return record


def _suite_trial(suite_dir, slot, child, configurator, *, home, passwords, renode, launch, resume_run, heartbeat, stop,
                 extension=None):
    """Run, or continue, the trial of a reserved slot until its child has ended and, for an emulated case, the frozen
    driver was graded and its verdict posted, or the trial says why not. The child starts through its suite
    (`launch(params)`), which starts and attaches it only while the suite runs. A slot whose child exists continues it:
    one still running is waited for, a completed one awaiting its grade is evaluated again (never a final the
    evaluator's draft holds), and a stopped one with nothing uncertain gets a device anew and resumes through its suite
    (`resume_run(params)`), with the approved child budget `extension` if any. Every child, legacy tq9 included, is
    graded once after its run."""
    case, run_id = slot['case'], slot['run_id']
    extension = extension or {}
    budget = float((child or {}).get('budget_seconds') or 10800)
    def password():
        # Only providing, grading or continuing a device needs the case's password file: a child whose verdict is
        # recorded, or whose effect is uncertain, is left to the configurator's settlement without one.
        if not passwords.get(case):
            raise ValueError('The evaluator password file of ' + case + ' is required to provide and grade its device')
        return passwords[case]
    def trial_of(completed):
        found = _trial_of(suite_dir.parent, run_id, slot['child_request_id'])
        if found is None:
            raise ValueError('The trial of child ' + run_id + ' is not under this bench home; its device and evaluation '
                             'cannot be continued')
        _adopt(found, run_id, completed)
        return found
    if run_id is None:
        trial_dir = suite_dir / 'trials' / (slot['trial_key'] + '-' + uuid.uuid4().hex[:8])
        record = run_trial(trial_dir, {'trial_id': trial_dir.name, 'home': str(home), 'case': case, 'start': child,
                                       'password_file': password(), 'renode': renode},
                           call=configurator, launch=launch, heartbeat=heartbeat, stop=stop)
    else:
        status = _wait(configurator, run_id, time.monotonic() + budget + GRACE, threading.Event(), heartbeat, stop)
        if 'harness_stopped' in status:
            raise ValueError('The bench harness stopped waiting for child ' + run_id + ': ' + status['harness_stopped'])
        if status.get('status') == 'completed':
            if status.get('equivalence') is not None:
                return
            password_file = password()
            record = resume(trial_of(True), password_file=password_file, renode=renode, call=configurator)
        elif status.get('uncertain_effect'):
            return  # The configurator blocks the suite; only the operator reconciles an uncertain effect.
        else:
            password_file = password()
            record = reattach(trial_of(False), password_file=password_file, renode=renode, call=configurator,
                              heartbeat=heartbeat, stop=stop, budget_seconds=extension.get('child_budget_seconds'),
                              budget_reason=extension.get('child_budget_reason'), resume_run=resume_run)
    if record.get('status') in ('failed', 'stop-unconfirmed'):
        raise ValueError('Trial ' + slot['trial_key'] + ' ' + record['status'] + ': ' + str(record.get('reason') or ''))


def launch_suite(request, *, home, password_files=None, renode=None, autostart=True, timeout=900, via_owner=False):
    """Start a detached bench harness for a suite and return once the configurator took the suite on, or refused it.
    `request` holds the manifest, executor, options and request_id of a new suite, or the suite_id (with an approved
    budget extension) of a stopped one to resume. Password file and emulator paths reach the harness on its stdin, never
    its command line or the suite tables. A repeated request_id with the same inputs follows the suite it started, with
    no second harness."""
    home = Path(home).expanduser().resolve()
    bench = bench_home(home)
    if bench == home or bench.is_relative_to(home):
        raise ValueError('The bench home must lie outside the configurator home')
    if password_files is not None and (not isinstance(password_files, dict) or any(
            not isinstance(case, str) or not isinstance(path, str) or not path.strip() for case, path in password_files.items())):
        raise ValueError('Evaluator password files must map case ids to file paths')
    if renode is not None and (not isinstance(renode, str) or not renode.strip()):
        raise ValueError('The emulator executable must be a path')
    if request.get('manifest') is not None and set(password_files or {}) - {e['case'] for e in request['manifest']['entries']}:
        raise ValueError('Evaluator password files must name selected cases')
    # Relative paths name files from the caller's directory; the harness itself runs in the suite directory.
    passwords = {case: str(Path(path).expanduser().resolve()) for case, path in (password_files or {}).items()}
    renode = str(Path(renode).expanduser().resolve()) if renode else None
    request = {**request, 'home': str(home), 'autostart': autostart}
    key = 'suite-run-' + uuid.uuid4().hex[:16]
    request_id = request.get('request_id')
    if request_id is not None and not request.get('suite_id'):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError('request_id must be a nonblank string')
        identity = {name: request.get(name) for name in SUITE_IDENTITY}
        held = _claim(bench, request_id, key, identity, kind='suite')
        if held is not None and _refused(bench / 'suites' / held):
            # The harness that claimed this request_id ended without the configurator's reply on record: refused (none
            # was frozen, so once the cause is fixed the same request starts afresh), or ended before it wrote the reply
            # down (the new harness's start finds the frozen suite and reports it, driving nothing).
            _release_claim(bench, request_id, held)
            held = _claim(bench, request_id, key, identity, kind='suite')
        if held is not None:
            return _follow_suite(bench / 'suites' / held, None, timeout, duplicate=True)
    suite_dir = bench / 'suites' / key
    suite_dir.mkdir(parents=True, mode=0o700)
    try:
        payload = {**request, 'password_files': passwords, 'renode': renode}
        process = (_spawn_via_owner(suite_dir, 'suite', payload, home) if via_owner else
                   _spawn(suite_dir, ['--suite-dir', str(suite_dir)], payload))
    except (OSError, ValueError) as error:
        _private(suite_dir / 'suite.json', {'schema': SUITE, 'status': 'failed', 'accepted': False, 'finished': time.time(),
                                            'reason': 'The bench harness did not start: ' + str(error)})
        raise
    return _follow_suite(suite_dir, process, timeout, duplicate=False)


def _follow_suite(suite_dir, process, timeout, *, duplicate):
    """Follow a suite harness until the configurator took the suite on (its reply), or refused it ({ok: false})."""
    deadline = time.monotonic() + timeout
    while True:
        record = read_suite(suite_dir) or {}
        exited = _harness_exited(process, record)
        if exited:
            record = read_suite(suite_dir) or {}
        if record.get('accepted'):
            return {**(record.get('reply') or {}), 'ok': True, 'suite_id': record['suite_id'],
                    'duplicate': duplicate or bool(record.get('duplicate')), 'suite_dir': str(suite_dir)}
        if exited or record.get('status') in ('failed', 'finished'):
            return {'ok': False, 'suite_dir': str(suite_dir), 'reason': record.get('reason') or
                    'The bench harness exited before the configurator took the suite on; see ' + str(suite_dir / 'harness.log')}
        if time.monotonic() > deadline:
            raise TimeoutError('The bench harness did not start the suite in time; see ' + str(suite_dir / 'harness.log'))
        time.sleep(.1)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m generative_driver.benchmark_support.harness')
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('--trial-dir')
    where.add_argument('--suite-dir', help='Drive a suite; its request, password files and emulator path arrive on stdin')
    parser.add_argument('--resume', action='store_true', help='Complete the evaluation of a finished trial; the password '
                        'file and emulator paths arrive on stdin as JSON')
    args = parser.parse_args(argv)
    # Tests only: a fixture file that substitutes the case resources, admission and device session factory.
    fixture = os.environ.get('GD_TEST_HARNESS_FIXTURE')
    if fixture:
        runpy.run_path(fixture, run_name='gd_harness_fixture')
    request = _launch_payload(json.loads(sys.stdin.buffer.read().decode('utf-8')))
    if args.suite_dir:
        run_suite(Path(args.suite_dir), request)
        return
    if args.resume:
        record = resume(Path(args.trial_dir), password_file=request['password_file'], renode=request.get('renode'))
        print(json.dumps({key: record.get(key) for key in ('trial_id', 'run_id', 'status', 'equivalence', 'evidence',
                                                           'evaluation_failed', 'sealing_failed')}, indent=2))
        return
    run_trial(Path(args.trial_dir), request)


if __name__ == '__main__':
    main()

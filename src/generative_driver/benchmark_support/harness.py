"""The bench harness: the benchmark's operator, one detached process per trial or per suite.

It holds what the configurator never does. The evaluator password file path arrives on its stdin and is never a field of
a record it writes; it reads that file once into an `EvaluatorPassword`, before it first contacts the configurator, and
unlocks a trial's answer key once, at admission, to project the device recipe (for a bound device, to authenticate the
password) and to hand to the grader after the run; and it owns an emulated case's device. In order, it: admits the case
(`admit`); for an emulated case (v2, or legacy tq9 from the legacy recipe) starts the emulator through a
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
harness, which continues a stopped trial on a device it provides anew (`reattach`) or grades an unscored one once. A
suite of a generated set holds the set in memory and starts no trial until the set's folder is out of reach (Feature 05,
vault gate; spec decision 17). A scoring suite moves past a trial that ended badly and audits each ended child's
transcripts for commands naming the set's folder (Feature 06, slot policy; spec decisions 17 and 20)."""
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
FINISHED = ('evaluated', 'unscored', 'awaiting-evaluation', 'run-stopped', 'stop-unconfirmed', 'failed', 'flagged')
# What a resume may still complete: the device stopped, confirmed, and the run's verdict not yet recorded.
RESUMABLE = ('evaluating', 'awaiting-evaluation', 'unscored', 'evaluated')
CONTROL = ('status', 'observe', 'set_running', 'select_condition', 'replace', 'release')
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
            if method == 'select_condition':
                return provider.select_condition(params.get('condition_id'), params.get('expected_session_id'))
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


def device_recipe(case_id, truth):
    """The device definition of the `UnlockedTruth` `truth` and nothing else: everything but the recipe is dropped
    before a provider sees it."""
    return legacy_recipe(truth.payload) if case_id == 'tq9' else truth.payload['recipe']


def admit(case_id, options, trial_dir, *, password, renode=None, device=None, pin=None):
    """Admit the case and, for an emulated one, build its device provider from the recipe alone: (provider or None,
    admission, `UnlockedTruth`). This step decrypts the case truth, once for every kind of case, and the trial grades
    from what it returns; a provider sees only the recipe. A v2 case's admission names its calibration evidence; legacy
    cases have none. A bound device (bme280) is the operator's: no provider, and a password that could not grade the run
    refuses it before it starts. The caller starts the provider, so that it holds the device even when the start
    fails. A device of a generated set (`device`, with its frozen `pin`) is admitted through its set instead."""
    from .device_provider import DeviceProvider
    from .registry import admitted_truth, case_truth, pin_case, resolve_case
    if device is not None:
        from .generated_trials import admit_device
        return admit_device(device, pin, password, trial_dir, renode=renode)
    pin = pin_case(case_id, options.get('scenario_id'), options.get('case_seed', 0))
    if not pin['execution'].startswith('actual-agent-'):
        raise ValueError('The bench harness runs actual-agent benchmark cases only')
    truth, admitted = admitted_truth(pin, password)
    truth = truth or case_truth(case_id, trial_dir, password)
    if pin['execution'] != 'actual-agent-emulation':
        return None, {}, truth
    recipe = device_recipe(case_id, truth)
    from .execution import artifact_name
    artifact = artifact_name(pin['manifest'])
    provider = DeviceProvider(case_id, resolve_case(case_id).root / artifact, recipe, renode, Path(trial_dir) / 'device',
                              image_sha256=pin['image_hashes'][artifact])
    return provider, ({'calibration_evidence_sha256': admitted.get('evidence_sha256')}
                      if pin['manifest'].get('schema') == 'benchmark-case/2' else {}), truth


TOKEN = 'evaluation-token.enc'


def evaluation_token(trial_dir, password, trial_id, *, create=False):
    """The trial's evaluation token: 32 random bytes the harness creates before the run starts (`create`) and seals under
    the evaluator password in the trial directory, with the truth envelope's key derivation, so that a harness resuming
    the trial with the password unlocks it again. The configurator holds only its sha256, which tells nothing about the
    password, and no plaintext copy is written. A password that does not unlock it is refused."""
    path = Path(trial_dir) / TOKEN
    if not password:
        raise ValueError('The bench harness needs the evaluator password file to seal and unlock the trial\'s '
                         'evaluation token')
    if create:
        if path.exists():
            raise ValueError('The trial already holds an evaluation token: ' + str(path))
        token = secrets.token_hex(32)
        password.seal({'schema': 'bench-evaluation-token/1', 'trial_id': trial_id, 'token': token}, path)
        return token
    try:
        sealed = password.unlock(path, hashlib.sha256(path.read_bytes()).hexdigest())
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


def conclude(trial_dir, case_id, run_dir, result, *, password, truth_source, renode=None, token=None, call=None,
             graded=True):
    """What the harness does with a terminal run whose handoffs it read. A completed run whose verdict is pending is
    graded once (`graded` False when its device stop was not confirmed: then nothing is graded) from the answer key
    `truth_source()` returns, the run evidence is sealed in the trial directory, and the verdict, or an unscored grade,
    is posted with record_evaluation; any other run's handoffs are sealed alone. No grade is posted that was not sealed
    first; an unscored one carries none. A record the configurator's equivalence/1 schema would refuse, or accepted
    records that no longer match their hashes at the seal, are posted unscored: no retry would change them. The
    plaintext evaluation never outlives this call."""
    from ..configurator import IntegrityError, _evaluation_errors
    from . import evaluator, recorded_grading
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
    # The protocol the run was under stamps every record built for it; a run that names none is refused by the grade.
    protocol = (result.get('progress') or {}).get('protocol')
    accepted = result.get('accepted_handoffs') or []
    frozen = (evaluator.emitted(accepted) or (None, None))[1]
    method = evaluator.method_for(case_id)
    done, content, equivalence = {}, {}, None
    try:
        if pending and graded:
            try:
                if method == 'recorded-reuse':
                    # Legacy tq9 and bme280: graded from the fresh reuse the run recorded, outside the grader.
                    equivalence, content['final'] = recorded_grading.grade(case_id, trial_dir, run_dir, result,
                                                                          password=password, truth_source=truth_source,
                                                                          protocol=protocol)
                else:
                    equivalence, content['final'] = evaluator.grade(case_id, trial_dir, run_dir, result,
                                                                    password=password, truth_source=truth_source,
                                                                    renode=renode)
                content['mission'] = equivalence.get('mission')
            except IntegrityError as error:
                equivalence, content['error'] = evaluator.unscored('integrity: ' + str(error), frozen, method,
                                                                    protocol=protocol), str(error)
            except recorded_grading.Unscorable as error:
                # Recorded evidence that cannot grade the driver for a reason outside it; its message names no truth.
                equivalence, content['error'] = evaluator.unscored(str(error), frozen, method, protocol=protocol), str(error)
            except Exception as error:
                # The message stays sealed: it may name what only the evaluator holds.
                equivalence = evaluator.unscored('evaluator host fault: ' + type(error).__name__, frozen, method,
                                                 protocol=protocol)
                content['error'] = type(error).__name__ + ': ' + str(error)
            errors = _evaluation_errors({**equivalence, 'evidence_sha256': None if equivalence['status'] == 'unscored'
                                         else '0' * 64})
            if errors:
                # Every resume would rebuild the same record and have it refused: it is unscored, and why stays sealed.
                equivalence = evaluator.unscored('evaluator host fault: invalid evaluation record', frozen, method,
                                                 protocol=protocol)
                content = {'error': 'Invalid evaluation record: ' + '; '.join(errors)}
            content['equivalence'] = equivalence
        try:
            done.update(sealed=True, evidence=evaluator.seal(trial_dir, run_dir, result, password=password, **content))
        except IntegrityError as error:
            done.update(sealed=False, sealing_failed='integrity: ' + str(error))
            if equivalence is not None and not (equivalence['status'] == 'unscored'
                                                and equivalence['diff'][0]['reason'].startswith('integrity: ')):
                # An accepted record changed after acceptance, or the snapshot no longer verifies: no retry seals it, so
                # the grade is unscored for integrity. An unscored record needs no seal.
                equivalence = evaluator.unscored('integrity: ' + str(error), frozen, method, protocol=protocol)
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


def finish(provider, case_id, run_dir, result, password, status=None, *, truth_source, trial_dir=None, renode=None,
           token=None, call=None, persist=None, audit=None):
    """After the run is terminal: stop the device, confirmed, then conclude the trial (`run_dir` None concludes nothing:
    the harness did not read the run's handoffs). A stop that is not confirmed keeps the device owned and names its live
    session and binding, which a recovery may have replaced since the run started, for manual reconciliation; nothing is
    graded then, and the handoffs are sealed all the same. `persist(update)` records the stop before any grading. On a
    scoring suite (`audit`, a `_SlotAudit`) the run's transcripts are read first, with the device still held so that a
    failed flag call leaves `_release_trial` to stop it and say so; a flagged trial's handoffs are sealed as for an
    unconfirmed stop, with no grade."""
    flagged = audit is not None and run_dir is not None and audit.flagged(run_dir)
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
    ended = 'flagged' if flagged else 'awaiting-evaluation' if completed else 'run-stopped'
    done['status'] = 'stop-unconfirmed' if not confirmed else ended
    if run_dir is not None and trial_dir is not None and password:
        if confirmed and completed and not flagged:
            done['status'] = 'evaluating'
        if persist:
            persist(dict(done))
        concluded = conclude(trial_dir, case_id, run_dir, result, password=password, truth_source=truth_source,
                             renode=renode, token=token, call=call, graded=confirmed and not flagged)
        done.update({key: value for key, value in concluded.items() if key != 'status' or confirmed})
        if confirmed and 'status' not in concluded:
            done['status'] = ended
    return done


class _SlotAudit:
    """The transcript audit of a scoring suite's slot (`audit_commands`; spec decision 17): the set's root, in this
    harness's memory alone, and the slot's token and ordinal for the flag call. What the call sends and any reason it
    leads to name each finding by its transcript and its command's hash, so that no record holds the root."""

    def __init__(self, set_root, scheduling, call):
        self._set_root, self._scheduling, self._call = set_root, scheduling, call

    def flagged(self, run_dir):
        """Whether the workers of the ended run in `run_dir` ran a command naming the set's folder, once the
        configurator has stored what was found. A reply that is not ok raises: the trial is left for a resume, which
        audits it again."""
        from .audit import audit_commands
        findings = audit_commands(run_dir, (self._set_root,))
        if not findings:
            return False
        stored = [stored_finding(row) for row in findings]
        _expect(_post(self._call, 'suite_flag', {**self._scheduling, 'findings': stored}),
                'The configurator did not record the flag')
        return True


def stored_finding(finding):
    """An audit finding as a suite stores it: its transcript's name and its command's sha256, not the command."""
    return {'transcript': finding['transcript'],
            'command_sha256': hashlib.sha256(finding['command'].encode('utf-8')).hexdigest()}


# How long a harness waits through an unreachable configurator before it stops waiting for a run.
UNREACHABLE = 300
# How long past the end of a run's wall-clock budget a harness keeps following the run.
GRACE = 600
# How long a suite's harness waits for the set's folder to go out of reach, each time, and how often it looks.
UNMOUNT_WAIT = 3600
UNMOUNT_POLL = 1
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


def run_trial(trial_dir, request, *, password=None, call=None, launch=None, heartbeat=None, stop=None, device=None,
              audit=None):
    """One trial: read the password, admit, provide the device, start the run, wait, stop the device, grade a completed
    run once from the answer key admission unlocked, seal, post the verdict. The password file (`password_file` in
    `request`) is read once, before the configurator is first contacted, unless the caller passes the object as
    `password`; an unreadable or empty file fails the trial with a reason that names the case and the error's class, and
    no path. The password file path and the emulator executable stay in this process's memory; trial.json holds neither
    as a field, nor the evaluation token, though the reason a later failure records there quotes the error met, which
    may name the emulator's path. A suite's harness passes the password it holds, its child's start request (`start` in
    `request`), its configurator requests (`call`), `launch(params)`, which starts and attaches the child through its
    suite, a `heartbeat()` while the run runs, a `stop` event that ends the wait and, for a device of a generated set,
    its `SetDevice` (`device`), which admits the trial through its set from the start request's pin. A suite child's
    start that got no answer is sent again (see `_start_child`), so a lost reply never has the device stopped under the
    child it started; trial.json records that the start was sent (`start_sent`) before it is. The harness holds a lock
    file in `trial_dir` for as long as it runs, by which others tell that it still runs (its process id alone may come
    to name another process). A scoring suite's harness also passes its slot's `audit` (`_SlotAudit`), which `finish`
    runs, and a failure before there is a run names why in closed words (`no_run`) if it was no matter of reaching the
    configurator."""
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
    # The word for why this trial has no run yet, from the moment a failure would be the trial's and not the reach of
    # the configurator; None once there is a run, or if the configurator never answered.
    no_run = None
    try:
        if password is None and password_file:
            password = _read_supplied(request['case'], password_file)
        # The configurator must be reachable before any device starts for it.
        reachable = configurator('ping', {})
        if not reachable.get('ok'):
            raise ValueError(reachable.get('reason') or 'The configurator is not reachable')
        set_device = {'device': device, 'pin': (start or {}).get('case_pin')} if device is not None else {}
        no_run = 'admit-refused'
        held['provider'], admission, truth = admit(request['case'], (start or request).get('case_options') or {},
                                                   trial_dir, password=password, renode=renode, **set_device)
        no_run = 'provider-failed'
        if held['provider'] is not None:
            held['provider'].start()
            server = DeviceServer(held['provider'], trial_dir / 'endpoint')
        # The configurator holds the token's hash, for the evaluation after the run; the token stays in this process and,
        # for a resume, sealed under the password in the trial directory.
        token = evaluation_token(trial_dir, password, request['trial_id'], create=True)
        params = dict(start) if start else {key: request[key] for key in ('goal', 'case', 'executor', 'executor_config',
                                                                          'budget_seconds', 'effects', 'case_options',
                                                                          'output_dir', 'scoped_tool_approval', 'binding')
                                           if request.get(key) is not None}
        params.update(request_id=request_id, evaluation_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                      **({'device': server.descriptor} if server else {}), **({'admission': admission} if admission else {}))
        if device is not None:
            from .generated_trials import PUBLIC_INPUTS
            params['public_inputs'] = str(trial_dir / PUBLIC_INPUTS)
        # From here a run may exist that this trial started, whether or not its reply arrives.
        record['start_sent'] = time.time()
        _private(trial_dir / 'trial.json', record)
        started = (_start_child(launch, params, server.shutdown if server else threading.Event(), stop) if launch
                   else configurator('start', params))
        no_run = None
        if not started.get('ok'):
            no_run = 'start-refused' if started.get('error') == 'ValueError' else None
            raise ValueError(started.get('reason') or 'The configurator refused the run')
        # A bound device is the operator's: the record names the stop of a device the harness never held from the start,
        # so a harness lost while it follows the run leaves a trial `resume` grades once the run has completed.
        record.update(status='running', run_id=started['run_id'],
                      **({'device': {key: params['device'][key] for key in ('provider_id', 'session_id', 'binding')}}
                         if server else {'device_stop': dict(UNHELD)}))
        _private(trial_dir / 'trial.json', record)
        _follow_run(trial_dir, record, held, server, configurator, budget=params.get('budget_seconds') or 10800,
                    password=password, truth_source=lambda: truth, renode=renode, token=token, heartbeat=heartbeat,
                    stop=stop, audit=audit)
    except _UnreadablePassword as error:
        record.update(status='failed', reason=str(error))
    except Exception as error:
        record.update(status='failed', reason=type(error).__name__ + ': ' + str(error),
                      **({'no_run': no_run} if no_run else {}))
    finally:
        try:
            _release_trial(trial_dir, record, held, server)
        finally:
            if lock is not None:
                lock.close()
    return record


class _UnreadablePassword(ValueError):
    """A supplied password file that cannot be read. Its message is written to the trial's or suite's record: it names
    the case and the error's class, never the path or the error's text."""


def _read_supplied(case, path):
    """The password in the file `path` supplied for `case`, or `_UnreadablePassword`."""
    from .truth import read_password
    try:
        return read_password(path)
    except (OSError, ValueError) as error:
        reason = 'The evaluator password file of ' + case + ' cannot be read: ' + type(error).__name__
    raise _UnreadablePassword(reason)


def _supplied_passwords(files):
    """{case: `EvaluatorPassword`} from `files` ({case: path}), each file read once, however many cases share it. A case
    whose path is blank has no entry."""
    read = {}
    for case, path in files.items():
        if path and path not in read:
            read[path] = _read_supplied(case, path)
    return {case: read[path] for case, path in files.items() if path}


def _not_admitted(registration, reasons):
    """The refusal of the set `registration`, listing its `reasons` once each: it names no path, as `_read_supplied`'s
    does."""
    return ValueError('The generated set ' + registration + ' is not admitted: ' + '; '.join(dict.fromkeys(reasons)))


def _held_set(registration, devices, set_root, passwords):
    """The `HeldSet` of `devices` of the set `registration`, or its refusal (`_not_admitted`), for a harness whose every
    file of the set is read here, once."""
    from . import generated_trials
    reasons = generated_trials.fixture_variable_reasons(registration)
    if reasons:
        raise _not_admitted(registration, reasons)
    if not set_root:
        raise _not_admitted(registration, ['no set root was supplied'])
    if registration not in passwords:
        raise _not_admitted(registration, ['no evaluator password file was supplied'])
    try:
        return generated_trials.hold_set(set_root, registration, devices, passwords[registration])
    except OSError as error:
        raise _not_admitted(registration, [type(error).__name__]) from None
    except ValueError as error:
        raise _not_admitted(registration, [str(error)]) from None


def _under_set_root(set_root, home, renode, options):
    """The reasons a suite could never run with the set's folder out of reach: the configurator home `home`, its bench
    home, the emulator executable `renode` or a replay candidate of the suite's `options` lies under `set_root`, both
    sides resolved."""
    root = Path(set_root).resolve()
    candidates = (options or {}).get('replay_candidates') if isinstance(options, dict) else None
    named = (('the configurator home', [home]), ('the bench home', [bench_home(home)]),
             ('the emulator executable', [renode] if renode else []),
             ('a replay candidate', list(candidates.values()) if isinstance(candidates, dict) else []))
    return [what + ' lies under the set root' for what, paths in named
            if any(Path(path).expanduser().resolve().is_relative_to(root) for path in paths)]


def _set_device_pins(manifest, set_root, passwords, *, home, renode, options):
    """(the `HeldSet`, {device id: pin} of every entry of the set manifest `manifest`), once the set `set_root` holds is
    registered, every entry's device admitted and nothing the suite needs lies under its root (`_under_set_root`), for
    a harness that has not yet contacted the configurator. A scoring set must have its entries name every device of its
    set once, with one repetition. Any refusal is `_not_admitted`."""
    from . import generated_sets, generated_trials
    from .suites import normalize_manifest
    normalized = normalize_manifest(manifest)
    registration = normalized['set']
    devices = [entry['device_id'] for entry in normalized['entries']]
    held = _held_set(registration, devices, set_root, passwords)
    listed = {entry['id'] for entry in held.manifest['devices']}
    partial = generated_sets.is_scoring(registration) and (set(devices) != listed or normalized['repetitions'] != 1)
    reasons = (['a scoring suite runs every device of its set once'] if partial else
               [reason for device in devices for reason in generated_sets.admitted_held(held, device)])
    reasons += _under_set_root(set_root, home, renode, options)
    if reasons:
        raise _not_admitted(registration, reasons)
    pins = {device: generated_trials.device_pin(held.manifest, device, held.evidence_sha256) for device in devices}
    return held, pins


def _held_for_resume(configurator, suite_id, set_root, passwords, *, home, renode, extension=None):
    """The `HeldSet` a resume given `set_root` drives its set suite with, or None for a suite of no set. It asks the
    configurator first, and refuses in its words a suite that cannot resume, so no vault stays open for one. Then it
    refuses (`_not_admitted`), with the vault still open, a set not admitted, what lies under its root, and a record or
    set other than the ones each entry's frozen pin names, which `public_pin` shows only by its digest. Budget and
    frozen implementation refusals stay in `suite_resume`, but for a scoring suite's child budget `extension`, which is
    refused here too with the vault still open."""
    from . import generated_sets, generated_trials
    from .suites import NO_CHILD_EXTENSION
    from .snapshots import canonical_digest
    result = _expect(configurator('suite_result', {'suite_id': suite_id, 'limit': 1}),
                     'The configurator did not report the suite')
    identity = (result.get('experiment') or {}).get('comparison_identity') or {}
    manifest = identity.get('manifest') or {}
    if 'set' not in manifest:
        return None
    if result.get('status') not in ('blocked', 'failed', 'cancelled'):
        raise ValueError('Only a stopped incomplete suite can resume')
    if result.get('stopping'):
        raise ValueError('Previous suite work is still stopping')
    child = result.get('active_child_id')
    if child and _expect(configurator('status', {'run_id': child}),
                         'The configurator did not report the suite\'s active child').get('uncertain_effect'):
        raise ValueError('Outstanding effect is uncertain; operator must respond with confirmed_safe after '
                         'reconciliation')
    if extension and 'scoring_set' in identity:
        raise ValueError(NO_CHILD_EXTENSION)
    registration = manifest['set']
    devices = [entry['device_id'] for entry in manifest['entries']]
    held = _held_set(registration, devices, set_root, passwords)
    reasons = [reason for device in devices for reason in generated_sets.admitted_held(held, device)]
    reasons += _under_set_root(set_root, home, renode, None)
    reasons += ['qualification differs from the frozen pin:' + device
                for device, pin in zip(devices, identity['entry_pins'])
                if canonical_digest(generated_trials.device_pin(held.manifest, device, held.evidence_sha256))
                != pin['pin_sha256']]
    if reasons:
        raise _not_admitted(registration, reasons)
    return held


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


def _follow_run(trial_dir, record, held, server, configurator, *, budget, password, truth_source, renode, token,
                heartbeat=None, stop=None, audit=None):
    """See a started run through: wait until it ends, then stop its device, confirmed, and conclude the trial. The device
    `held` holds passes to `finish`, which records its stop before anything is graded. A run on the operator's bound
    device (no `server`) is followed until it completes or is cancelled: the operator answers and resumes it in between,
    and may extend its budget, which extends the wait. A scoring suite's run that ended unread could not be audited (see
    `_SlotAudit`), so it ends here for a resume, rather than settle."""
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
    done = finish(held['provider'], record['case'], None if unread else home / 'runs' / run_id, result, password,
                  status, truth_source=truth_source, trial_dir=trial_dir, renode=renode, token=token, persist=persist,
                  call=configurator, audit=audit)
    held['provider'] = None
    if unread:
        # The device is stopped all the same; nothing is sealed that the harness did not read from a finished run.
        done.update(sealed=False, sealing_failed=unread)
    _settle(record, done)
    if unread and audit is not None and 'harness_stopped' not in status:
        raise ValueError('The ended run could not be read, so it is not audited: ' + unread)


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


def reattach(trial_dir, *, password, renode=None, call=None, heartbeat=None, stop=None, budget_seconds=None,
             budget_reason=None, resume_run=None, device=None):
    """Continue a trial whose run stopped before it ended, once the device it ran on was stopped, confirmed: admit it
    again, which decrypts the answer key its grade uses, provide a device anew from the recipe, attach it to the stopped
    run with the trial's evaluation token (`attach_device`; the configurator refuses a run with anything uncertain),
    resume the run and see it through as a new trial is. Only the password the trial ran with unlocks its token: under
    any other nothing starts. An approved budget extension (`budget_seconds`, `budget_reason`) reaches the run's resume;
    a suite's harness resumes its child through the suite (`resume_run(params)`). From the moment a device may run, the
    trial record names it with an unconfirmed stop, so a harness lost meanwhile leaves a device to reconcile, never the
    earlier confirmed stop. It holds the trial's evaluation lock throughout, which every resume, reattachment or
    adoption of the trial takes before it asks whether the trial's harness still runs: the process id the record names
    from then on is a diagnostic. A device of a generated set (`device`) is admitted again through its set."""
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
        token = evaluation_token(trial_dir, password, record['trial_id'])
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
            options = {'scenario_id': pin.get('scenario_id'), 'case_seed': pin.get('case_seed', 0)}
            set_device = {'device': device, 'pin': result.get('case_pin')} if device is not None else {}
            held['provider'], _, truth = admit(record['case'], options, trial_dir, password=password, renode=renode,
                                               **set_device)
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
                        password=password, truth_source=lambda: truth, renode=renode, token=token, heartbeat=heartbeat,
                        stop=stop)
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


def resume(trial_dir, *, password, renode=None, call=None, device=None, audit=None):
    """Complete the evaluation of a trial whose harness has exited after its device stop was confirmed: one that died
    after the stop, could not grade (unscored, an evaluator host fault) or could not post its verdict. A final the draft
    already holds is rebuilt, never graded again; a verdict the configurator holds is never posted again. The answer key
    is decrypted only if the trial grades, at most once. Grading needs
    the emulator executable (`renode`); without it a resume that would grade refuses before it unlocks anything. A
    password that does not unlock the trial's evaluation token is refused before anything is graded, sealed or posted.
    A trial on the operator's bound device, of which the harness held nothing, is completed too when its harness
    stopped following the run (its budget's deadline passed, it was stopped, or it died) and the operator has since
    completed or cancelled the run; while the run is still stopped, the operator resumes it first. So is a trial
    recorded `run-stopped` whose run has since completed awaiting its grade (stopped after fresh reuse, by a restart
    say, and resumed): grading uses a fresh emulator, so the confirmed stop of the trial's device is all it needs. A
    trial on a device of a generated set grades from the answer key unlocked through its set (`device`), which only its
    suite holds: without it, it is refused here. A scoring suite's completed child (`audit`, its slot's `_SlotAudit`)
    grades whatever the trial recorded of its device or its harness, since grading uses a fresh emulator that a leftover
    one cannot touch, unless the audit flags it first: then its handoffs are sealed with no grade."""
    from ..client import call as client_call
    from . import evaluator
    from .registrations import registered_case_ids, registration_for
    from .registry import case_truth
    trial_dir = Path(trial_dir)
    found = read_trial(trial_dir)
    if found is None:
        raise ValueError('No trial with a run to evaluate: ' + str(trial_dir))
    if (device is None and found.get('case') in registered_case_ids()
            and registration_for(found['case']).calibration_kind == 'set'):
        raise ValueError('A trial on a device of a generated set is resumed through its suite, which holds the set '
                         'root: benchmark suite resume')
    with _exclusive(trial_dir):
        # Read under the lock: an evaluation that held it may have just completed the trial.
        record = read_trial(trial_dir)
        if not isinstance(record, dict) or not record.get('run_id') or not record.get('home'):
            raise ValueError('No trial with a run to evaluate: ' + str(trial_dir))
        if not _harness_exited(None, record, trial_dir):
            raise ValueError('The trial\'s bench harness is still running')
        if audit is None and (record.get('device_stop') or {}).get('confirmed') is not True:
            raise ValueError('The trial\'s device stop was not confirmed; reconcile its device before evaluating')
        # A bound-device trial whose harness stopped following its run: the operator may have completed the run since.
        followed = ((record.get('device_stop') or {}).get('held') is False
                    and record.get('status') in ('run-stopped', 'running'))
        if audit is None and record.get('status') not in (*RESUMABLE, 'run-stopped') and not followed:
            raise ValueError('The trial awaits no evaluation: ' + str(record.get('status')))
        # Only the password the trial ran with unlocks its token: under any other, nothing is graded, sealed or posted.
        token = evaluation_token(trial_dir, password, record['trial_id'])
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
        run_dir = home / 'runs' / record['run_id']
        flagged = audit is not None and audit.flagged(run_dir)
        native = (result.get('case_pin') or {}).get('manifest', {}).get('execution_setup', {}).get('method') == 'native-sim'
        if (renode is None and not native and not flagged
                and evaluator.needs_grading(trial_dir, result, password=password)):
            raise ValueError('The trial\'s frozen driver is not graded yet: grading starts a fresh emulator, so pass its '
                             'executable (--renode)')
        if device is not None:
            from .generated_trials import answer_key
            truth_source = lambda: answer_key(device, password)
        else:
            truth_source = lambda: case_truth(record['case'], run_dir, password)
        done = conclude(trial_dir, record['case'], run_dir, result, password=password, truth_source=truth_source,
                        renode=renode, token=token, call=configurator, graded=not flagged)
        _settle(record, {**done, 'status': 'flagged' if flagged else done.get('status') or 'run-stopped',
                         'resumed': time.time()})
        _private(trial_dir / 'trial.json', record)
    return record


def _seal_flagged(trial_dir, run_dir, result, password):
    """Seal the handoffs of a stopped child the audit flagged on a resume, its trial's harness gone before it could,
    with no grade: as `finish` seals a flagged trial."""
    with _exclusive(trial_dir):
        record = read_trial(trial_dir)
        done = conclude(trial_dir, record['case'], run_dir, result, password=password, truth_source=None, graded=False)
        _settle(record, {**done, 'status': 'flagged', 'resumed': time.time()})
        _private(Path(trial_dir) / 'trial.json', record)


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
    candidate = options.pop('replay_candidate', None)
    if options:
        raise ValueError('Unsupported options for a benchmark trial: ' + ', '.join(sorted(options)))
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
        raise ValueError('request_id must be a nonblank string')
    from ..setup import configured_executor
    executor = configured_executor(executor, home)
    if (executor == 'replay') != (candidate is not None):
        raise ValueError('Only the replay executor takes a saved candidate, and it needs one: replay_candidate')
    trial_id = 'trial-' + uuid.uuid4().hex[:16]
    request = {'trial_id': trial_id, 'home': str(home), 'autostart': autostart, 'goal': GOAL, 'case': case,
               'executor': executor, 'budget_seconds': budget, 'effects': chosen, 'case_options': case_options,
               'request_id': request_id, 'output_dir': str(Path(output_dir).resolve()) if output_dir else None,
               'scoped_tool_approval': approval, 'binding': binding}
    if candidate is not None:
        # The candidate's path and the hash of its files, which the configurator checks again before every stage.
        from ..replay import candidate_config
        request['executor_config'] = candidate_config({'candidate': str(Path(candidate).expanduser().resolve())}, bench=bench)
    if request_id is not None:
        identity = {key: request[key] for key in IDENTITY}
        if 'executor_config' in request:
            identity['executor_config'] = request['executor_config']
        held = _claim(bench, request_id, trial_id, identity)
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


def _harness_exited(process, record, directory):
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
    lock = Path(directory) / HARNESS_LOCK
    held = process_lock_held(lock) if lock.exists() else None
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
    return isinstance(record, dict) and record.get('accepted') is False and _harness_exited(None, record, suite_dir)


class _VaultNotClosed(ValueError):
    """A set's folder still reachable after `UNMOUNT_WAIT`. Its message names the set and no path."""


class _NoWorkerStarted(Exception):
    """A slot's wait for its set's folder ended before the folder went out of reach: no worker started, and the slot is
    not the harness's to settle (`_suite_trial`)."""


def _await_unmount(suite_dir, record, set_root, registration, *, stop=None, poll=None):
    """Whether the folder of the set `registration` is out of reach, after waiting for it if it is not. The first check
    finds out; if the folder is reachable the record says `awaiting-unmount` until it is not, for at most
    `UNMOUNT_WAIT`, after which `_VaultNotClosed` ends the harness. Only the path is watched (see
    `generated_trials.set_unreachable`). A `stop`, or a `poll()` that returns false, ends the wait early: False."""
    from . import generated_trials
    if generated_trials.set_unreachable(set_root):
        return True
    before = record['status']
    record.update(status='awaiting-unmount', set=registration)
    _private(Path(suite_dir) / 'suite.json', record)
    deadline, closed = time.monotonic() + UNMOUNT_WAIT, False
    while not closed and not (stop is not None and stop.is_set()) and (poll is None or poll()):
        if time.monotonic() >= deadline:
            raise _VaultNotClosed('The vault holding set ' + registration + ' was not closed in time')
        time.sleep(UNMOUNT_POLL)
        closed = generated_trials.set_unreachable(set_root)
    record['status'] = before
    _private(Path(suite_dir) / 'suite.json', record)
    return closed


def run_suite(suite_dir, request, *, call=None, stop=None):
    """One bench harness run of a suite. It starts the suite on the configurator (or resumes a stopped one, given its
    `suite_id`) under a fresh random token, of which the configurator keeps only the sha256, and then works through the
    suite one slot at a time: reserve the slot, run its trial (a child on a device this harness provides, graded once
    after its run), and have the configurator settle the slot from the child's status and recorded evaluation, which it
    reads itself. It stops once the configurator reports the suite complete, stopped or blocked, and lets go of it; a
    harness that stops while the suite runs leaves it blocked for a resume. Every supplied password file is read once,
    before the configurator is first contacted, so that deleting the files later changes nothing; an unreadable or empty
    one ends the harness there, with a reason that names the case and the error's class, and no path, and the suite
    never accepted. Password file and emulator paths are never fields of suite.json or the suite tables, and no child's
    run receives them. The reason a later failure records in suite.json (written for this user only, in the bench home)
    quotes the error met, which may name the emulator's path. The configurator learns of a failure only which trial
    failed and where this record is. An approved child budget extension in `request` goes to the stopped child this run
    continues, and to no other. The harness holds a lock file in `suite_dir` for as long as it runs, by which the
    configurator and other launches tell that it still runs (its process id alone may come to name another process). A
    new suite of one generated set also needs its `set_root` and the set's password file: before that first contact the
    harness holds the set in memory, refuses it unless every entry's device is admitted and nothing it needs lies under
    its root (as it refuses an unreadable file), and builds each entry's pin, which the configurator freezes. After a
    `ping` it waits until the folder is out of reach (`_await_unmount`), and then reads no file of the set. A resumed
    one given `set_root` does the same once the configurator says it can resume (`_held_for_resume`); one given none
    does not wait, and a slot that needs its device fails there. It checks the folder again before each worker: a wait
    that ends with no worker started goes back to the top of the loop unsettled, and one that passes its limit ends the
    harness, blaming no trial."""
    from ..client import hold_process_lock
    suite_dir = Path(suite_dir)
    suite_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = dict(request)
    files, renode = dict(request.pop('password_files', None) or {}), request.pop('renode', None)
    set_root = request.pop('set_root', None)
    extension = {key: request[key] for key in ('child_budget_seconds', 'child_budget_reason') if request.get(key) is not None}
    home = Path(request['home'])
    configurator = call or _configurator(home, request.get('autostart', True))
    token = secrets.token_hex(32)
    record = {'schema': SUITE, 'home': str(home), 'suite_id': request.get('suite_id'), 'request_id': request.get('request_id'),
              'status': 'starting', 'accepted': False, 'pid': os.getpid(), 'created': time.time(), 'settled': []}
    # Held before the record names this process, as a trial's harness holds its own.
    lock = hold_process_lock(suite_dir / HARNESS_LOCK)
    _private(suite_dir / 'suite.json', record)
    attached, failure, current = False, None, None
    try:
        passwords = _supplied_passwords(files)
        # Only a new set suite is held here: a resumed one has its pins frozen, and holds its set below, once the
        # configurator says it can resume.
        manifest = None if record['suite_id'] else request.get('manifest')
        held, device_pins = (_set_device_pins(manifest, set_root, passwords, home=home, renode=renode,
                                              options=request.get('options'))
                             if isinstance(manifest, dict) and 'set' in manifest else (None, None))
        reachable = configurator('ping', {})
        if not reachable.get('ok'):
            raise ValueError(reachable.get('reason') or 'The configurator is not reachable')
        if record['suite_id'] and set_root:
            held = _held_for_resume(configurator, record['suite_id'], set_root, passwords, home=home, renode=renode,
                                    extension=extension)
        if held is not None and not _await_unmount(suite_dir, record, set_root, held.registration, stop=stop):
            raise ValueError('The bench harness stopped before the vault holding set ' + held.registration
                             + ' was closed')
        identity = {'harness_token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'harness_pid': os.getpid(),
                    **({'harness_lock': str((suite_dir / HARNESS_LOCK).resolve())} if lock else {})}
        if record['suite_id']:
            reply = configurator('suite_resume', {'suite_id': record['suite_id'], **identity,
                                                  **{key: request[key] for key in ('suite_budget_seconds', 'budget_reason')
                                                     if request.get(key) is not None}, **extension})
        else:
            # Every start carries a request_id, so a start the client sends again after a lost reply finds its suite.
            reply = configurator('suite_start', {'manifest': request.get('manifest'), 'executor': request.get('executor'),
                                                 'options': request.get('options') or {},
                                                 'request_id': request.get('request_id') or 'harness-' + uuid.uuid4().hex,
                                                 **identity, **({'device_pins': device_pins} if device_pins else {})})
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
        def slot_gate(slot):
            # The reservation stands in for a heartbeat while no child runs, and tells of a suite that moved on. A reply
            # that is not ok says nothing of the suite: a brief outage is no reason to stop waiting.
            def still_reserved():
                reply = _post(configurator, 'suite_reserve', {'suite_id': record['suite_id'], 'token': token})
                return reply.get('ok') is not True or reply.get('slot') == slot
            return _await_unmount(suite_dir, record, set_root, held.registration, stop=stop, poll=still_reserved)
        while attached and not (stop is not None and stop.is_set()):
            reserved = _expect(configurator('suite_reserve', {'suite_id': record['suite_id'], 'token': token}),
                               'The configurator refused the reservation')
            slot = reserved.get('slot')
            if slot is None:
                record['suite_status'] = reserved.get('status')
                break
            current = slot['trial_key']
            scheduling = {'suite_id': record['suite_id'], 'token': token, 'ordinal': slot['ordinal']}
            scoring = reserved.get('scoring') is True
            # The configurator starts, or resumes, the slot's child only while the suite runs, under the suite's lock.
            try:
                recorded = _suite_trial(suite_dir, slot, reserved.get('request'), configurator, home=home,
                                        passwords=passwords, renode=renode, held=held, heartbeat=heartbeat, stop=stop,
                                        extension=extension if slot['run_id'] else {},
                                        launch=lambda params, scheduling=scheduling: configurator(
                                            'suite_start_child', {**scheduling, 'request': params}),
                                        resume_run=lambda params, scheduling=scheduling: configurator(
                                            'suite_resume_child', {**scheduling, **params}),
                                        gate=slot_gate if held is not None else None,
                                        scoring=scheduling if scoring else None,
                                        audit=(_SlotAudit(set_root, scheduling, configurator)
                                               if scoring and set_root else None))
            except _NoWorkerStarted:
                current = None
                continue
            except _VaultNotClosed:
                current = None  # No trial failed: the operator left the vault open.
                raise
            if slot['run_id']:
                extension = {}
            if recorded:
                # The configurator has the slot's fault or found its child, or the child is unaudited: no settlement.
                current = None
                record['settled'].append({'ordinal': slot['ordinal'], 'decision': recorded})
                _private(suite_dir / 'suite.json', record)
                continue
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
            # The failure text may name a path handed to this harness alone (the emulator executable): the configurator
            # is told which trial failed and where this record is, never the text itself.
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
                 extension=None, held=None, gate=None, scoring=None, audit=None):
    """Run, or continue, the trial of a reserved slot until its child has ended and, for an emulated case, the frozen
    driver was graded and its verdict posted, or the trial says why not. The child starts through its suite
    (`launch(params)`), which starts and attaches it only while the suite runs. A slot whose child exists continues it:
    one still running is waited for, a completed one awaiting its grade is evaluated again (never a final the
    evaluator's draft holds), and a stopped one with nothing uncertain gets a device anew and resumes through its suite
    (`resume_run(params)`), with the approved child budget `extension` if any. Every child, legacy tq9 included, is
    graded once after its run. A slot of a set suite takes its password by its set, and its `SetDevice` from its frozen
    pin and the set the harness holds (`held`), in the same step. Immediately before a worker can start, on a new child
    or a continued one, `gate(slot)` must find the set's folder out of reach, or `_NoWorkerStarted` ends the slot; a
    grade starts no worker, and is not gated.

    On a scoring suite (`scoring`, the slot's scheduling fields) a trial that never got a run is recorded in place of a
    raise, by its closed word: the configurator's decision is returned, and there is nothing to settle. A stopped child
    never gets a device anew: it settles as it stands, once `audit` (`_SlotAudit`, which needs the set's root) has read
    its transcripts. A completed one is graded whatever became of its device. A slot whose findings are stored is not
    audited or graded again. A trial whose harness stopped waiting returns `unsettled`, as nothing audited its child,
    and a grade that could not be sealed or posted stops the suite for a resume."""
    case, run_id = slot['case'], slot['run_id']
    extension = extension or {}
    budget = float((child or {}).get('budget_seconds') or 10800)
    pinned = (child or {}).get('case_pin') or {}
    keyed = pinned.get('set') or case
    def password():
        # Only providing, grading or continuing a device needs the case's password: a child whose verdict is recorded,
        # or whose effect is uncertain, is left to the configurator's settlement without one.
        if keyed not in passwords:
            raise ValueError('The evaluator password file of ' + keyed + ' is required to provide and grade its device')
        return passwords[keyed]
    def device():
        """Keyword arguments naming the slot's device of a set, for a slot that needs it; none for any other case."""
        if 'set' not in pinned:
            return {}
        if held is None:
            raise ValueError('The set root of ' + keyed + ' is required to provide and grade its device')
        from .generated_trials import SetDevice
        return {'device': SetDevice(held, pinned['device_id'])}
    def out_of_reach():
        if gate is not None and not gate(slot):
            raise _NoWorkerStarted()
    def trial_of(completed):
        found = _trial_of(suite_dir.parent, run_id, slot['child_request_id'])
        if found is None:
            raise ValueError('The trial of child ' + run_id + ' is not under this bench home; its device and evaluation '
                             'cannot be continued')
        _adopt(found, run_id, completed)
        return found
    if run_id is None:
        supplied, named = password(), device()
        out_of_reach()
        trial_dir = suite_dir / 'trials' / (slot['trial_key'] + '-' + uuid.uuid4().hex[:8])
        record = run_trial(trial_dir, {'trial_id': trial_dir.name, 'home': str(home), 'case': case, 'start': child,
                                       'renode': renode},
                           password=supplied, call=configurator, launch=launch, heartbeat=heartbeat, stop=stop,
                           audit=audit, **named)
        if scoring and record.get('status') == 'failed' and record.get('no_run'):
            return _expect(_post(configurator, 'suite_no_run', {**scoring, 'reason': record['no_run']}),
                           'The configurator did not record the fault of ' + slot['trial_key'])['decision']
    else:
        status = _wait(configurator, run_id, time.monotonic() + budget + GRACE, threading.Event(), heartbeat, stop)
        if 'harness_stopped' in status:
            raise ValueError('The bench harness stopped waiting for child ' + run_id + ': ' + status['harness_stopped'])
        if scoring and status.get('uncertain_effect'):
            return None  # Whatever the child's status: the configurator blocks the suite until it is reconciled.
        if status.get('status') == 'completed':
            if status.get('equivalence') is not None or (scoring and slot.get('audit')):
                return None
            supplied, named = password(), device()
            record = resume(trial_of(True), password=supplied, renode=renode, call=configurator, audit=audit, **named)
        elif status.get('uncertain_effect'):
            return None  # The configurator blocks the suite; only the operator reconciles an uncertain effect.
        elif scoring:
            if slot.get('audit'):
                return None
            if audit is None:
                raise ValueError('The set root of ' + keyed + ' is required to audit its stopped trial')
            # Read before the flag: a flagged slot never comes back, so its handoffs are sealed now or never.
            result = _post(configurator, 'result', {'run_id': run_id})
            unread = _unread({}, result)
            if unread:
                raise ValueError('The stopped run could not be read, so it is not audited: ' + unread)
            if audit.flagged(home / 'runs' / run_id):
                found = _trial_of(suite_dir.parent, run_id, slot['child_request_id'])
                if found is not None:
                    _adopt(found, run_id, False)
                    _seal_flagged(found, home / 'runs' / run_id, result, password())
            return None
        else:
            supplied, named = password(), device()
            out_of_reach()
            record = reattach(trial_of(False), password=supplied, renode=renode, call=configurator,
                              heartbeat=heartbeat, stop=stop, budget_seconds=extension.get('child_budget_seconds'),
                              budget_reason=extension.get('child_budget_reason'), resume_run=resume_run, **named)
    if record.get('status') in ('failed', 'stop-unconfirmed'):
        raise ValueError('Trial ' + slot['trial_key'] + ' ' + record['status'] + ': ' + str(record.get('reason') or ''))
    if run_id is None and scoring and 'harness_stopped' in (record.get('run_status') or {}):
        # It stopped waiting, so nothing audited the child, which may have ended since: a resume audits, then settles.
        return 'unsettled'
    if scoring and (record.get('evaluation_failed') or record.get('status') == 'awaiting-evaluation'):
        # The configurator never heard this grade, and an earlier unscored one would read as if this were one too. A
        # resume seals and posts it again and never grades again.
        raise ValueError('Trial ' + slot['trial_key'] + ' could not seal or post its grade: '
                         + str(record.get('evaluation_failed') or record.get('sealing_failed')))
    return None


def launch_suite(request, *, home, password_files=None, renode=None, set_root=None, autostart=True, timeout=900,
                 via_owner=False):
    """Start a detached bench harness for a suite and return once the configurator took the suite on, or refused it.
    `request` holds the manifest, executor, options and request_id of a new suite, or the suite_id (with an approved
    budget extension) of a stopped one to resume. Password file and emulator paths, and a set suite's `set_root`, reach
    the harness on its stdin, never its command line or the suite tables. A repeated request_id with the same inputs
    follows the suite it started, with
    no second harness. A new set suite always has a request_id, minted here if none is given, and this returns as soon
    as its harness waits for the set's folder to go out of reach, as a resume of one does."""
    home = Path(home).expanduser().resolve()
    bench = bench_home(home)
    if bench == home or bench.is_relative_to(home):
        raise ValueError('The bench home must lie outside the configurator home')
    if password_files is not None and (not isinstance(password_files, dict) or any(
            not isinstance(case, str) or not isinstance(path, str) or not path.strip() for case, path in password_files.items())):
        raise ValueError('Evaluator password files must map case ids to file paths')
    if renode is not None and (not isinstance(renode, str) or not renode.strip()):
        raise ValueError('The emulator executable must be a path')
    if set_root is not None and (not isinstance(set_root, str) or not set_root.strip()):
        raise ValueError('The set root must be a path')
    manifest = request.get('manifest')
    # A set suite's password files are keyed by its set, as one password seals the set's every device.
    if manifest is not None and set(password_files or {}) - ({manifest['set']} if 'set' in manifest
                                                             else {e['case'] for e in manifest['entries']}):
        raise ValueError('Evaluator password files must name selected cases')
    # Relative paths name files from the caller's directory; the harness itself runs in the suite directory.
    passwords = {case: str(Path(path).expanduser().resolve()) for case, path in (password_files or {}).items()}
    renode = str(Path(renode).expanduser().resolve()) if renode else None
    set_root = str(Path(set_root).expanduser().resolve()) if set_root else None
    request = {**request, 'home': str(home), 'autostart': autostart}
    key = 'suite-run-' + uuid.uuid4().hex[:16]
    if isinstance(manifest, dict) and 'set' in manifest and request.get('request_id') is None:
        # A set suite's harness may answer before any suite exists: this is what its operator follows it by.
        request['request_id'] = 'set-suite-' + uuid.uuid4().hex
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
        payload = {**request, 'password_files': passwords, 'renode': renode,
                   **({'set_root': set_root} if set_root else {})}
        process = (_spawn_via_owner(suite_dir, 'suite', payload, home) if via_owner else
                   _spawn(suite_dir, ['--suite-dir', str(suite_dir)], payload))
    except (OSError, ValueError) as error:
        _private(suite_dir / 'suite.json', {'schema': SUITE, 'status': 'failed', 'accepted': False, 'finished': time.time(),
                                            'reason': 'The bench harness did not start: ' + str(error)})
        raise
    return _follow_suite(suite_dir, process, timeout, duplicate=False)


def _follow_suite(suite_dir, process, timeout, *, duplicate):
    """Follow a suite harness until the configurator took the suite on (its reply), it was refused ({ok: false}), or the
    harness waits for its set's folder to go out of reach, which is itself a reply: the operator has yet to close the
    vault, so a client must not be held here. A new suite's reply carries the request_id to follow it by; a resume takes
    no claim, so its reply carries the suite_id instead."""
    deadline = time.monotonic() + timeout
    while True:
        record = read_suite(suite_dir) or {}
        exited = _harness_exited(process, record, suite_dir)
        if exited:
            record = read_suite(suite_dir) or {}
        if record.get('accepted'):
            return {**(record.get('reply') or {}), 'ok': True, 'suite_id': record['suite_id'],
                    'duplicate': duplicate or bool(record.get('duplicate')), 'suite_dir': str(suite_dir)}
        if exited or record.get('status') in ('failed', 'finished'):
            return {'ok': False, 'suite_dir': str(suite_dir), 'reason': record.get('reason') or
                    'The bench harness exited before the configurator took the suite on; see ' + str(suite_dir / 'harness.log')}
        if record.get('status') == 'awaiting-unmount':
            named = ({'suite_id': record['suite_id']} if record.get('suite_id') else
                     {'request_id': record.get('request_id'), 'duplicate': duplicate})
            return {'ok': True, 'status': 'awaiting-unmount', 'set': record.get('set'), 'suite_dir': str(suite_dir),
                    **named}
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
        from .truth import read_password
        record = resume(Path(args.trial_dir), password=read_password(request['password_file']),
                        renode=request.get('renode'))
        print(json.dumps({key: record.get(key) for key in ('trial_id', 'run_id', 'status', 'equivalence', 'evidence',
                                                           'evaluation_failed', 'sealing_failed')}, indent=2))
        return
    run_trial(Path(args.trial_dir), request)


if __name__ == '__main__':
    main()

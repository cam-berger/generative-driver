"""`generative-driver smoke`: one whole run through the background service, its worker launch and the tool gateway on
this machine, with no model, no Codex and no hardware (docs/features/kit-fixes/03-service-smoke.md).

It starts its own service with a new temporary folder as its home, configures the scripted worker as the run's Codex
command and binds the stand-in device it serves on 127.0.0.1. It checks the kit's machinery, not a model, a firmware
image or a device."""
import contextlib
import shutil
import tempfile
import threading
import time
from pathlib import Path

from .configurator import STAGES, TERMINAL

# What a passing smoke shows, and what no smoke shows.
_EVIDENCE = ("This run used the kit's scripted worker in place of Codex and a stand-in device served on this machine, "
             "so it shows that the kit's service path works on this machine and nothing about a model, a firmware "
             "image or a real device.")
# What a smoke that did not pass could have shown at most.
_EVIDENCE_AT_MOST = ("This run used the kit's scripted worker in place of Codex and a stand-in device served on this "
                     "machine, so it can show at most whether the kit's service path works on this machine, and "
                     "nothing about a model, a firmware image or a real device.")
_GOAL = 'Smoke check of the service path: identify the stand-in device and read its temperature.'
# The firmware stand-in the acquire stage imports: a few bytes, not a firmware image.
_FIRMWARE = b'generative-driver smoke: firmware stand-in, not a firmware image\n'
BUDGET_SECONDS = 300
# How long after its budget the smoke still waits for a run to end before it cancels the run itself.
GRACE_SECONDS = 60
# How long the smoke waits for a cancelled run's worker to stop.
_CANCEL_SECONDS = 30
# How long the smoke asks its service to shut down before it terminates the service process.
STOP_SECONDS = 30
_POLL_SECONDS = 0.25
# A JSON object of test seams that only the smoke's own tests set; callers outside tests never use it.
_TEST_SEAM = '_GENERATIVE_DRIVER_SMOKE_TEST'


def _seam():
    import json
    import os
    value = os.environ.get(_TEST_SEAM)
    return json.loads(value) if value else {}


def _worker_command(seams):
    """The scripted worker, configured as the run's Codex command, with the test `seams` it takes. A worker launch
    passes no PYTHONPATH, so the command names the folder holding this package, as the replay worker's does."""
    import sys
    package = str(Path(__file__).resolve().parents[1])
    return [sys.executable, '-c', 'import sys; sys.path.insert(0,' + repr(package) + '); '
            'from generative_driver.scripted_worker import main; main()', *seams]


class _ServiceGone(Exception):
    """The service stopped answering: its process no longer holds the lock on its home."""


def _call(method, params, home):
    """The service's reply to `method`. Raises `_ServiceGone` when the service no longer runs to give one."""
    from .client import SERVICE_LOCK, call, process_lock_held
    reply = call(method, params, home, autostart=False)
    if reply.get('ok') is False and process_lock_held(home / SERVICE_LOCK) is False:
        raise _ServiceGone(str(reply.get('reason')))
    return reply


def _follow(home, observed, final=False):
    """Read the run's events since the last read and, when there are new ones or the read is `final`, its attempts so
    far (and, when `final`, its verdict), so that they stay known if the service stops answering later."""
    run_id, cursor, new = observed['run_id'], observed['cursor'], []
    while True:
        page = _call('events', {'run_id': run_id, 'after': cursor}, home)
        if not page.get('events'):
            break
        new += page['events']
        cursor = page['cursor']
    observed['events'] += new
    observed['cursor'] = cursor
    if new or final:
        result = _call('result', {'run_id': run_id}, home)
        observed['attempts'] = _attempts(observed['events'], result.get('worker_reports') or [])
        if final:
            observed['verdict'] = _verdict(result)


def _worker_runs(observed, stage):
    """Whether the worker of the run's latest attempt, in `stage`, has been launched."""
    attempts = observed['attempts']
    return bool(attempts and attempts[-1].stage == stage and attempts[-1].workspace
                and (Path(attempts[-1].workspace) / '_agent_stderr.txt').is_file())


def _wait(home, observed, deadline, seam=None, pid=None):
    """Follow the run until it has ended and its worker has stopped (True), or until the monotonic `deadline` passes
    (False), keeping its last `status` and its attempts so far in `observed`. A test may name a stage in which the smoke
    raises an error of its own (`error_in`), or kills its service `pid` once that stage's worker runs (`kill_in`)."""
    seam = dict(seam or {})
    while True:
        status = observed['status'] = _call('status', {'run_id': observed['run_id']}, home)
        _follow(home, observed)
        if status.get('status') in TERMINAL and not status.get('stopping'):
            return True
        running = status.get('stage') if status.get('status') == 'running' else None
        if running is not None and running == seam.get('error_in'):
            raise RuntimeError('a test of the smoke raised this error while the run was in ' + running)
        if running is not None and running == seam.get('kill_in') and _worker_runs(observed, running):
            seam.pop('kill_in')
            _terminate(home, pid)
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def _worker(record):
    """The scripted worker's report status, or the cause its launch recorded when it gave no valid report."""
    from .agents import validate_report
    if record is None:
        return None
    report = record.get('report')
    return report['status'] if validate_report(report) else record.get('cause') or record.get('status')


class _Attempt:
    """One stage attempt of the run: what the report gives of it (`shown`), its worker's record, its notes and the
    workspace its worker was launched in (None for a skipped stage)."""

    def __init__(self, shown, record, notes, workspace):
        self.shown, self.record, self.notes, self.workspace = shown, record, notes, workspace

    @property
    def stage(self):
        return self.shown['stage']

    def failure(self, repaired):
        """Why the attempt failed a condition of a passing smoke, or None. A probe or ground attempt whose device
        difference caused a repair is the failed one, rather than the stages the repair ran again (`repaired`)."""
        shown, notes = self.shown, self.notes or {}
        outcome = notes.get('outcome') or {}
        if outcome.get('kind') == 'revise':
            return 'its device difference caused a repair: ' + _difference((notes.get('configurator') or {})
                                                                           .get('device_diff'))
        if shown['skipped'] is not None:
            return 'it was skipped: ' + shown['skipped']
        if shown['worker'] != 'completed':
            worker = notes.get('worker') or {}
            record = self.record or {}
            said = worker.get('summary') or record.get('reason') or 'no worker ran'
            return 'its worker gave no completed report (' + str(shown['worker']) + '): ' + str(said)
        if not shown['accepted']:
            return 'its handoff was not accepted'
        if shown['failed_checks']:
            return 'its configurator checks failed: ' + ', '.join(map(str, shown['failed_checks']))
        if shown['runs'] > 1 and not repaired:
            return 'it ran ' + str(shown['runs']) + ' times'
        return None


def _difference(entries):
    """A device difference in a line: each entry's source and what it says."""
    said = []
    for entry in entries or []:
        defects = '; '.join(str(defect.get('what')) for defect in entry.get('defects') or []
                            if isinstance(defect, dict))
        said.append(str(entry.get('source')) + ': ' + str(entry.get('what') or entry.get('code') or defects))
    return ' | '.join(said) or 'none recorded'


def _attempts(events, reports):
    """Each stage attempt of the run, in the order they ran."""
    records = {record.get('assignment_id'): record for record in reports}
    attempts, ended = {}, None
    for event in events:
        kind, data = event['kind'], event['data']
        if kind in ('stage.assigned', 'stage.skipped'):
            key = data['id'] if kind == 'stage.assigned' else data['assignment_id']
            attempts[key] = {'stage': data['stage'], 'accepted': False,
                             'skipped': data.get('reason') if kind == 'stage.skipped' else None,
                             'workspace': data.get('workspace') if kind == 'stage.assigned' else None,
                             'started': event['time'], 'ended': None, 'notes': None}
        elif kind == 'stage.accepted' and data.get('assignment_id') in attempts:
            attempt = attempts[data['assignment_id']]
            attempt.update(accepted=True, ended=event['time'], notes=(data.get('notes') or {}).get('inline'))
        elif kind.startswith('run.') and kind[4:] in TERMINAL:
            ended = event['time']
    listed = list(attempts.items())
    runs = {stage: sum(attempt['stage'] == stage for _, attempt in listed) for stage in STAGES}
    out = []
    for index, (key, attempt) in enumerate(listed):
        following = listed[index + 1][1]['started'] if index + 1 < len(listed) else ended
        end = attempt['ended'] or following or attempt['started']
        configurator = (attempt['notes'] or {}).get('configurator') or {}
        shown = {'stage': attempt['stage'], 'accepted': attempt['accepted'], 'skipped': attempt['skipped'],
                 'worker': _worker(records.get(key)),
                 'failed_checks': [check.get('name') for check in configurator.get('checks') or []
                                   if check.get('passed') is not True],
                 'seconds': round(max(0, end - attempt['started']), 3), 'runs': runs.get(attempt['stage'], 1)}
        out.append(_Attempt(shown, records.get(key), attempt['notes'], attempt['workspace']))
    return out


def _verdict(result):
    equivalence = result.get('equivalence')
    if not isinstance(equivalence, dict):
        return None
    return {'equivalent': equivalence.get('equivalent'), 'basis': equivalence.get('basis'),
            'failed_checks': [check.get('id') for check in equivalence.get('checks') or []
                              if check.get('passed') is not True]}


def _running(status):
    """The stage of a run still queued or running, or None."""
    return status.get('stage') if status and status.get('status') in ('queued', 'running') else None


def _attempt_in(attempts, stage):
    """The run's latest attempt when it is in `stage`, or None."""
    return attempts[-1] if stage and attempts and attempts[-1].stage == stage else None


def _failure(observed, service):
    """The stage a smoke that did not pass failed in, why, and that stage's attempt; (None, None, None) when it
    passed."""
    status, attempts, verdict = observed['status'], observed['attempts'], observed['verdict']
    if observed['error'] is not None:
        return None, observed['error'], None
    if observed['broke'] is not None:
        # Interrupted, or the smoke itself failed: the stage the run was in, whose worker the operator may want to read.
        stage = _running(status)
        return stage, observed['broke'] + (' while the run was in ' + stage if stage else ''), _attempt_in(attempts,
                                                                                                           stage)
    if observed['gone']:
        stage = _running(status)
        return stage, ('The service stopped answering' + (' while the run was in ' + stage if stage else '')
                       + '; workers or gateways it launched may still run'), _attempt_in(attempts, stage)
    if observed['late'] is not None:
        stage, budget, grace = observed['late']
        return stage, ('The run did not end in time: it was still running in ' + str(stage) + ' ' + str(grace)
                       + ' seconds after its budget of ' + str(budget) + ' seconds, so the smoke cancelled it'
                       ), _attempt_in(attempts, stage)
    if status['status'] != 'completed':
        stage = status.get('stage')
        return stage, ('The run ' + str(status['status']) + ' in ' + str(stage) + ' ('
                       + str(status.get('outcome_category')) + '): ' + str(status.get('reason'))
                       ), _attempt_in(attempts, stage)
    repaired = any(((attempt.notes or {}).get('outcome') or {}).get('kind') == 'revise' for attempt in attempts)
    for attempt in attempts:
        why = attempt.failure(repaired)
        if why:
            return attempt.stage, 'The ' + attempt.stage + ' attempt failed: ' + why, attempt
    missing = [stage for stage in STAGES if stage not in [attempt.stage for attempt in attempts]]
    if missing:
        return None, 'The run completed without the stages ' + ', '.join(missing), None
    if verdict is None or verdict['equivalent'] is not True or verdict['basis'] != 'self-evidence':
        return None, ('The run recorded no final verdict' if verdict is None else
                      'The final verdict is not an equivalent self-evidence verdict: equivalent '
                      + str(verdict['equivalent']) + ', basis ' + str(verdict['basis']) + ', failed checks '
                      + (', '.join(map(str, verdict['failed_checks'])) or 'none')), None
    if service['stopped'] is not True:
        return None, ('The service did not stop within ' + str(service['window']) + ' seconds of the smoke\'s '
                      'request, so the smoke terminated it; workers or gateways it started may still run'), None
    return None, None, None


def _logs(home, attempt):
    """The service's log and, when the failed stage launched a worker, that worker's standard error and events."""
    logs = {'service': str(home / 'daemon.log')}
    if attempt is not None and attempt.workspace:
        # The names the worker launch gives these files in the worker's workspace (`agents.execute`).
        for key, name in (('worker_stderr', '_agent_stderr.txt'), ('worker_events', '_agent_events.jsonl')):
            if (Path(attempt.workspace) / name).is_file():
                logs[key] = str(Path(attempt.workspace) / name)
    return logs


def _read(home, observed):
    """Read the run's status, attempts and verdict, as far as the service still answers."""
    observed['status'] = _call('status', {'run_id': observed['run_id']}, home)
    _follow(home, observed, final=True)


def _observe(home, binding, firmware, seam, service, observed):
    """Start the service and the run, follow the run until it ends and read it into `observed`: the run's `status`,
    its `attempts`, its `verdict`, an `error` when the service or the run could not be started, and, when the run had
    not ended `GRACE_SECONDS` after its budget and the smoke cancelled it, the stage it was `late` in with that budget
    and grace. Raises `_ServiceGone` as soon as the service stops answering."""
    from .client import call
    try:
        service['pid'] = call('ping', {}, home, autostart=True)['pid']
    except (OSError, RuntimeError, ValueError) as error:
        observed['error'] = 'The service could not be started: ' + str(error)
        return
    budget, grace = seam.get('budget_seconds', BUDGET_SECONDS), seam.get('grace_seconds', GRACE_SECONDS)
    # A test may give the service a longer budget than the smoke waits for, as a service that does not end the run.
    started = _call('start', {'goal': _GOAL, 'inputs': {'firmware.bin': str(firmware)}, 'binding': binding,
                              'effects': ['read'], 'executor': 'codex',
                              'budget_seconds': seam.get('run_budget_seconds', budget),
                              'executor_config': {'command': _worker_command(seam.get('worker', []))}}, home)
    observed['run_id'] = started.get('run_id')
    if observed['run_id'] is None:
        observed['error'] = 'The run could not be started: ' + str(started.get('reason'))
        return
    if not _wait(home, observed, time.monotonic() + budget + grace, seam, service['pid']):
        observed['late'] = (observed['status'].get('stage'), budget, grace)
        _call('cancel', {'run_id': observed['run_id']}, home)
        _wait(home, observed, time.monotonic() + _CANCEL_SECONDS)
    _read(home, observed)


def _service_runs(home, pid):
    """Whether the service `pid` still runs: whether it holds the lock on its home, which it holds for as long as it
    runs, or, where the file system cannot tell, whether its process has not ended."""
    from .client import SERVICE_LOCK, _wait_for_exit, process_lock_held
    held = process_lock_held(home / SERVICE_LOCK)
    if held is None:
        try:
            return not _wait_for_exit(pid, 0)
        except OSError:
            return True
    return held


def _ask(home, method, seconds):
    """The service's answer to `method`, or None when none came within `seconds`."""
    from .client import call
    answer = []

    def ask():
        try:
            answer.append(call(method, {}, home, autostart=False))
        except Exception as error:
            answer.append({'ok': False, 'reason': str(error)})
    asking = threading.Thread(target=ask, daemon=True)
    asking.start()
    asking.join(seconds)
    return answer[0] if answer else None


def _terminate(home, pid):
    """End the service process `pid` while it still holds its home's lock, so that no other process is hit."""
    import os
    import signal
    import subprocess
    if _service_runs(home, pid) is False:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True, timeout=30)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
    deadline = time.monotonic() + 10
    while _service_runs(home, pid) is not False and time.monotonic() < deadline:
        time.sleep(0.05)


def _stop(home, pid, seconds, request):
    """Whether the smoke saw the service it started end when asked: it is asked to shut down, and asked again while it
    answers that its shutdown is pending; once it answers that it stopped, the smoke waits for its process to end.
    After `seconds` its process is terminated, and it did not stop. A test may send another `request` in place of
    `shutdown`, as to a service that does not stop."""
    import json
    if pid is None:
        try:
            pid = json.loads((home / 'service.json').read_text(encoding='utf-8')).get('pid')
        except (OSError, ValueError, AttributeError):
            pid = None
        if not isinstance(pid, int):
            return True  # No service was started.
    deadline = time.monotonic() + seconds
    stopping = False
    while _service_runs(home, pid) is not False:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate(home, pid)
            return False
        if not stopping:
            answer = _ask(home, request, remaining)
            stopping = answer is not None and answer.get('stopped') is True
        time.sleep(min(0.05 if stopping else _POLL_SECONDS, max(0, deadline - time.monotonic())))
    return True


@contextlib.contextmanager
def _uninterrupted():
    """Ignore Ctrl-C for the length of the block, so that a second one cannot keep the smoke from stopping its
    service."""
    import signal
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # Not the main thread, where no Ctrl-C arrives.
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def smoke():
    """The smoke's report: `ok`, the `run_id`, each stage attempt, the final `verdict`, the `service` it started and
    stopped, the `seconds` it took and the `evidence` it gives. A smoke that did not pass also names the
    `failed_stage`, the `reason`, the `logs` to read and the `home` it keeps; one that passed removes its home.

    The service and the stand-in device are stopped on every exit, Ctrl-C and an error of the smoke's own included."""
    from . import stand_in_device
    started = time.monotonic()
    seam = _seam()
    home = Path(tempfile.mkdtemp(prefix='generative-driver-smoke-')).resolve()
    firmware = home / 'inputs' / 'firmware.bin'
    firmware.parent.mkdir()
    firmware.write_bytes(_FIRMWARE)
    service = {'pid': None, 'stopped': False}
    observed = {'run_id': None, 'status': None, 'events': [], 'cursor': 0, 'attempts': [], 'verdict': None,
                'error': None, 'broke': None, 'gone': False, 'late': None}
    replies = {line.encode(): reply.encode() for line, reply in seam.get('replies', {}).items()}
    with stand_in_device.serving(replies) as binding:
        try:
            _observe(home, binding, firmware, seam, service, observed)
        except _ServiceGone:
            observed['gone'] = True
        except (KeyboardInterrupt, Exception) as error:
            observed['broke'] = ('The smoke was interrupted (Ctrl-C)' if isinstance(error, KeyboardInterrupt) else
                                 'The smoke itself failed: ' + type(error).__name__ + ': ' + str(error))
            if observed['run_id'] is not None:
                try:
                    _read(home, observed)
                except _ServiceGone:
                    observed['gone'] = True
                except (KeyboardInterrupt, Exception):
                    pass  # What was read before stays; stopping the service matters more.
        finally:
            with _uninterrupted():
                window = seam.get('stop_seconds', STOP_SECONDS)
                stopped = _stop(home, service['pid'], window, 'ping' if seam.get('ignore_shutdown') else 'shutdown')
                # A service that stopped answering on its own did not stop when the smoke asked, and the workers and
                # gateways it launched may still run.
                service['stopped'] = stopped and not observed['gone']
    failed_stage, reason, attempt = _failure(observed, {**service, 'window': window})
    report = {'ok': reason is None, 'run_id': observed['run_id'],
              'stages': [{key: value for key, value in attempt.shown.items() if key != 'runs'}
                         for attempt in observed['attempts']],
              'verdict': observed['verdict'], 'service': service, 'seconds': round(time.monotonic() - started, 3),
              'evidence': _EVIDENCE if reason is None else _EVIDENCE_AT_MOST}
    if reason is not None:
        report.update(failed_stage=failed_stage, reason=reason, logs=_logs(home, attempt), home=str(home))
    elif not seam.get('keep'):
        shutil.rmtree(home, ignore_errors=True)
    return report

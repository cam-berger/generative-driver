"""The service's log, `daemon.log` in its home (docs/features/kit-fixes/04-service-diagnostics.md, 04-B1 to 04-B4).

The file takes whatever escapes the service process. This module adds an entry for every exception the service catches
and turns into a reply, a stop or a failure event, and for every exception a kit tool catches while it runs for one of
the service's runs, so the traceback behind a failed run is kept. An entry names what was being done and quotes the
exception. It never holds a request's parameters, a tool's arguments or anything of `service.json`."""
import contextlib
import datetime
import os
import re
import traceback
from contextvars import ContextVar
from pathlib import Path

LOG_NAME = 'daemon.log'
# A log larger than this when a service starts is set aside as `daemon.log.1`, so the file does not grow for ever.
_ROTATE_BYTES = 10 * 1024 * 1024
# The attribute that marks an exception an entry was already written for.
_WRITTEN = '_generative_driver_logged'
# A run id, a method or a tool's name as an entry may hold it. Anything else is a request's own text and is left out.
_NAME = re.compile(r'[A-Za-z0-9_.:-]{1,80}')


def open_for_service(home):
    """The stream a starting service's output goes to: the log in `home`, opened to append. A log larger than
    10 MB is first renamed to `daemon.log.1`, replacing any earlier one, and a new log is begun; a log that
    cannot be renamed (another process holds it open on Windows) is kept and written on."""
    path = Path(home) / LOG_NAME
    try:
        if path.stat().st_size > _ROTATE_BYTES:
            os.replace(path, path.with_name(LOG_NAME + '.1'))
    except OSError:
        pass
    return path.open('ab')


def _named(value):
    return value if isinstance(value, str) and _NAME.fullmatch(value) else None


def _line(kind, fields, raised=None, message=None):
    """An entry's first line: its time, its kind, each field that names something and, when `raised` names an
    exception's type, that type and its `message`, kept on the one line."""
    stamp = datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')
    named = ''.join(' ' + key + '=' + value for key, value in ((key, _named(value)) for key, value in fields) if value)
    said = '' if raised is None else ' ' + str(raised) + ': ' + str(message).replace('\r', '\\r').replace('\n', '\\n')
    return stamp + ' ' + kind + named + said + '\n'


class ServiceLog:
    """The entries one service writes to its log. Writing never raises: a log that cannot be written must not fail the
    request or the run the entry is about."""

    def __init__(self, path):
        self.path = Path(path)

    def _write(self, text):
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
            try:
                # One write per entry, so entries of the service and of its tools' processes do not interleave.
                os.write(descriptor, text.encode('utf-8', 'backslashreplace'))
            finally:
                os.close(descriptor)
        except OSError:
            pass

    def error(self, error, *, run_id=None, stage=None, method=None, step=None, tool=None):
        """Write the full entry for `error`: what was being done (`method`, `step` or `tool`), the run and stage when
        there are any, the exception's type and message, and its traceback. An exception gets one entry, however many
        callers pass it on."""
        if getattr(error, _WRITTEN, False):
            return
        fields = (('run', run_id), ('stage', stage), ('method', method), ('step', step), ('tool', tool))
        self._write(_line('error', fields, type(error).__name__, error) + ''.join(traceback.format_exception(error))
                    + '\n')
        self.written(error)

    def _refusal(self, error, *, method, run_id=None, stage=None, tool=None):
        """Write the one-line entry for a request refused with `error` that left its run as it was: no traceback."""
        if getattr(error, _WRITTEN, False):
            return
        fields = (('run', run_id), ('stage', stage), ('method', method), ('tool', tool))
        self._write(_line('refused', fields, type(error).__name__, error))
        self.written(error)

    def replied(self, error, *, method, run_id=None, stage=None, tool=None):
        """Write the entry for an exception that became a request's reply, unless one was written where it was caught:
        one line for a refusal (a `ValueError`), the full entry for anything else."""
        if isinstance(error, ValueError):
            self._refusal(error, method=method, run_id=run_id, stage=stage, tool=tool)
        else:
            self.error(error, method=method, run_id=run_id, stage=stage, tool=tool)

    def abandoned(self, unfinished, *, run_id=None, stage=None, step=None):
        """Write the one-line entry for a tool the service stopped, or whose process ended without a result
        (`tool_process.Unfinished`): its reason and, when its process ended, what ended it. The service caught no
        exception of its own there; what the tool's process let through has that process's entry."""
        fields = (('run', run_id), ('stage', stage), ('step', step), ('tool', unfinished.tool),
                  ('reason', unfinished.reason))
        self._write(_line('abandoned', fields, unfinished.kind, unfinished.message))
        self.written(unfinished)

    @staticmethod
    def written(error):
        """Mark `error` as having its entry, so that whoever it is passed on to writes no second one."""
        with contextlib.suppress(AttributeError, TypeError):
            setattr(error, _WRITTEN, True)


class _Running:
    """What a thread, or a tool's whole process, does for the service: the log to write to, the run and its stage."""

    def __init__(self, log, run_id, stage):
        self.log, self.run_id, self.stage = log, run_id, stage


_THREAD = ContextVar('generative_driver_running', default=None)
_PROCESS = None


@contextlib.contextmanager
def running(log, run_id, stage=None):
    """Mark what this thread does inside the block as done for the service's run `run_id`, so that an exception a kit
    tool catches there gets its entry (`caught`)."""
    token = _THREAD.set(_Running(log, run_id, stage))
    try:
        yield
    finally:
        _THREAD.reset(token)


def here(log, run_id, stage=None):
    """Mark everything this thread does from now on as done for the service's run `run_id`, for a thread that does
    nothing else. Returns the mark, whose `stage` the caller sets as the run moves on."""
    mark = _Running(log, run_id, stage)
    _THREAD.set(mark)
    return mark


def adopt(path, run_id, stage):
    """Mark this whole process as running one tool for the service's run `run_id`, whose log is at `path`: the child
    process of a tool calls it once, before the tool runs."""
    global _PROCESS
    _PROCESS = _Running(ServiceLog(path), run_id, stage) if path else None


def caught(error, *, tool=None, step=None):
    """Write the entry for an exception caught while this thread or process works for one of the service's runs: by
    the kit tool `tool`, or in the `step` of the run that the caller names. Outside the service it writes nothing to
    any log."""
    mark = _THREAD.get() or _PROCESS
    if mark is not None:
        mark.log.error(error, tool=tool, step=step, run_id=mark.run_id, stage=mark.stage)

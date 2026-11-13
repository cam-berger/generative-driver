"""A kit tool run in a child process of the service, with a deadline (docs/features/kit-fixes/04-service-diagnostics.md,
04-B7 to 04-B9). A kit tool is a tool `toolkit.call_tool` runs, as against a tool the benchmark provides or an
operation of an emitted package.

A tool that runs inside the service has no deadline and holds its run while it runs, and some tools execute code a
worker wrote: a register model's conversion module. The service therefore runs a generic run's kit tools here, in a
process of their own. It stops that process, with every process the tool started, when the call's limits pass, and the
process can loop, crash or exit without taking the service with it. Only the service turns this on, when it builds its
controller (`Controller(home, for_service=True)`)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .agents import _OWN_GROUP, _terminate

# How long the service gives one tool call, counted from when its request arrived. It keeps the service's answer ahead
# of the 600 seconds a Codex worker waits for a tool (`agents._codex_launch`).
LIMIT_SECONDS = 570
_POLL_SECONDS = 0.05
# The child finds this package as the service found it, from a source checkout or an installed wheel, whatever
# PYTHONPATH the service was started with.
_BOOT = ('import sys; sys.path.insert(0, ' + repr(str(Path(__file__).resolve().parents[1])) + '); '
         'from generative_driver.tool_process import main; main()')
# Why the service stopped a call, in the words of the sentence a worker or a stage's notes get.
_STOPPED = {'timeout': 'the configurator stopped it when its limit of {limit:g} seconds passed',
            'budget': "the configurator stopped it when the run's budget ran out",
            'cancelled': 'the configurator stopped it because the run was cancelled',
            'shutdown': 'the configurator stopped it because the service is shutting down'}


# The `kind` of an `Unfinished` whose process ended and returned nothing.
_EXITED = 'ToolProcessExit'


class Unfinished(Exception):
    """A tool call that ended without a result. Its `reason` is `timeout`, `budget`, `cancelled` or `shutdown` when the
    service stopped it, and `raised` otherwise: `kind` and `message` then name the exception the tool let through, or
    (kind `ToolProcessExit`) the exit status of a process that returned nothing. As text it is a sentence naming
    what happened."""

    def __init__(self, tool, reason, kind=None, message=None):
        self.tool, self.reason, self.kind, self.message = tool, reason, kind, message
        if reason in _STOPPED:
            why = _STOPPED[reason].format(limit=LIMIT_SECONDS)
        elif kind == _EXITED:
            why = 'its process ended without a result (' + str(message) + ')'
        else:
            why = 'it raised ' + str(kind) + ': ' + str(message)
        super().__init__(str(tool) + ' did not finish: ' + why)


class Limits:
    """When the service stops one call: at `expires` on the monotonic clock (`LIMIT_SECONDS` after the call arrived),
    when the run's budget ends (`budget_ends`, seconds since the epoch), whichever comes first, and at once when
    `cancelled()` or `closing()` says the run was cancelled or the service is shutting down."""

    def __init__(self, *, expires, budget_ends, cancelled, closing):
        self.expires, self.budget_ends, self.cancelled, self.closing = expires, budget_ends, cancelled, closing

    def passed(self):
        """`timeout` or `budget` once the call's deadline has passed, whichever limit ran out first; else None."""
        limit, budget = self.expires - time.monotonic(), self.budget_ends - time.time()
        if min(limit, budget) > 0:
            return None
        return 'budget' if budget <= limit else 'timeout'

    def stop(self):
        """Why the call must stop now, or None. A shutdown cancels every run, so it is asked first."""
        if self.closing():
            return 'shutdown'
        if self.cancelled():
            return 'cancelled'
        return self.passed()


def run(name, arguments, *, directory, stop, context):
    """The result of the kit tool `name`, run in a child process. `stop()` is asked before it starts and many times a
    second while it runs, and names why the call must stop now (a reason of `Unfinished`), or None; the process is
    then stopped with every process it started, and nothing it did afterwards counts. Raises `Unfinished` when it was
    stopped or ended without a result. The request and the result travel as files in a new folder of `directory`,
    which the caller owns; `context` (`log`, `run_id`, `stage`) lets the tool's own catches reach the service's
    log."""
    reason = stop()
    if reason is not None:
        raise Unfinished(name, reason)  # Its limits had passed already: it is not started.
    Path(directory).mkdir(parents=True, exist_ok=True)
    exchange = Path(tempfile.mkdtemp(dir=directory))
    try:
        (exchange / 'request.json').write_text(json.dumps({'name': name, 'arguments': arguments, **context}),
                                               encoding='utf-8')
        command = [sys.executable, '-c', _BOOT, str(exchange)]
        if os.name == 'nt':
            # A job the wrapper owns ends every process the tool started when the wrapper is stopped.
            command = [sys.executable, str(Path(__file__).with_name('_windows_worker.py')), *command]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, **_OWN_GROUP)
        try:
            while True:
                try:
                    status = process.wait(_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    reason = stop()
                    if reason is not None:
                        raise Unfinished(name, reason) from None
        except BaseException:
            # However the wait ends, the process and what it started never outlive the call.
            _terminate(process)
            raise
        try:
            outcome = json.loads((exchange / 'result.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            ended = 'signal ' + str(-status) if status < 0 else 'exit status ' + str(status)
            raise Unfinished(name, 'raised', _EXITED, ended) from None
        if 'result' not in outcome:
            raised = outcome.get('raised') or {}
            raise Unfinished(name, 'raised', raised.get('type'), raised.get('message'))
        return outcome['result']
    finally:
        shutil.rmtree(exchange, ignore_errors=True)


def main():
    """The child process: run the one tool its request names and leave the result, or the exception the tool let
    through, in its exchange folder."""
    exchange = Path(sys.argv[1])
    request = json.loads((exchange / 'request.json').read_text(encoding='utf-8'))
    from . import service_log
    # Everything this process does is for one call of one run: what the toolkit or the toolchain catches is logged so.
    service_log.adopt(request.get('log'), request.get('run_id'), request.get('stage'))
    try:
        from .toolkit import call_tool
        outcome = json.dumps({'result': call_tool(request['name'], request['arguments'])})
    except BaseException as error:  # An exit a worker's conversion module asks for is named too.
        service_log.caught(error, tool=request['name'])
        outcome = json.dumps({'raised': {'type': type(error).__name__, 'message': str(error)}})
    ready = exchange / 'result.json.part'
    ready.write_text(outcome, encoding='utf-8')
    os.replace(ready, exchange / 'result.json')

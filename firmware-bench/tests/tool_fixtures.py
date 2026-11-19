"""What the benchmark's tests took from the tool's test files, copied here so that this folder runs alone.

10-B3 of docs/features/untangle/10-tests.md: no benchmark test file imports a module of the tool's test folder. Each
name is a copy that does what its original does: the environment of a Python child (`tests/package_env.py`), an
in-process configurator served over IPC (`tests/owner_ipc_fixture.py`), the scripted stages and their helpers
(`tests/test_configurator.py`), and the hand-written binding and package (`tests/test_supplied_stages.py`). The
originals stay with the tool.
"""
from contextlib import contextmanager
import itertools
import json
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest

import firmware_bench
import generative_driver

# The folders that hold the two packages under test, the benchmark's first (12-B4 of Feature 12 of this Run).
PACKAGE_ROOTS = (str(Path(firmware_bench.__file__).resolve().parents[1]),
                 str(Path(generative_driver.__file__).resolve().parents[1]))


def package_folders():
    """Both package folders as a list, once where they are the same folder: what the script of a child puts on its
    search path."""
    return list(dict.fromkeys(PACKAGE_ROOTS))
WORKFLOW_TIMEOUT = 120
BINDING = {'host': '127.0.0.1', 'port': 1}
_PIDS = itertools.count(70001)


def package_env(**values):
    """The environment of a Python child that must import the packages under test: an MCP stdio child inherits only
    a short list of variables, so `PYTHONPATH` is lost and it would run whatever its interpreter finds first. It
    gives both package folders, once where they are the same folder, and then the inherited value."""
    inherited = os.environ.get('PYTHONPATH')
    folders = package_folders() + ([inherited] if inherited else [])
    return {'PYTHONPATH': os.pathsep.join(folders), **values}


def service_pid(controller):
    """The process id an in-process configurator answers `ping` with: one for each `Controller` object, so that a
    second one on the same home reads as a restart of the service, as another daemon process would."""
    if not hasattr(controller, '_test_pid'):
        controller._test_pid = next(_PIDS)
    return controller._test_pid


@contextmanager
def owner_ipc(controller):
    """Serve `controller` over native IPC at its home, as the daemon serves one: `ping` is answered here, with the
    code this process runs and the controller's `service_pid`, and a refusal carries its `error`."""
    from generative_driver.client import code_identity
    with tempfile.TemporaryDirectory(prefix='gd-test-ipc-') as directory:
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        address = r'\\.\pipe\gd-'+secrets.token_hex(8) if os.name == 'nt' else str(Path(directory)/'owner.sock')
        key = secrets.token_bytes(32)
        requests = []
        with Listener(address, family=family, authkey=key) as listener:
            (controller.home/'service.json').write_text(json.dumps({
                'family': family, 'address': address, 'authkey': key.hex(), 'code': code_identity()}))

            def dispatch(connection, request):
                with connection:
                    try:
                        if request['method'] == 'ping':
                            result = {'ok': True, 'pid': service_pid(controller), 'code': code_identity()}
                        else:
                            result = controller.call(request['method'], request.get('params', {}))
                    except Exception as error:
                        result = {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
                    connection.send_bytes(json.dumps(result, allow_nan=False).encode())

            def serve():
                while True:
                    connection = listener.accept()
                    request = json.loads(connection.recv_bytes())
                    if request['method'] == '_fixture_stop':
                        connection.close()
                        return
                    thread = threading.Thread(target=dispatch, args=(connection, request), daemon=True)
                    requests.append(thread)
                    thread.start()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                yield
            finally:
                with Client(address, family=family, authkey=key) as connection:
                    connection.send_bytes(b'{"method":"_fixture_stop"}')
                thread.join(10)
                for request in requests:
                    request.join(10)


def wait_worker_exit(controller, run, seconds=WORKFLOW_TIMEOUT):
    """Wait for a scripted crash's worker, whose durable status can remain running.

    Join the worker rather than repeatedly materializing every handoff while the
    six-stage fixture is still writing them. Read full diagnostics only on failure.
    """
    with controller._lock:
        worker = controller._workers.get(run['run_id'])
    if worker:
        worker[1].join(seconds)
        if worker[1].is_alive():
            raise AssertionError(controller.call('result', run))


def wait_stopped(controller, run, seconds=WORKFLOW_TIMEOUT):
    until = time.monotonic() + seconds
    # A full result expands all stage reports and handoffs. Re-reading it while
    # the same worker writes those files can starve Windows fixture progress.
    wait_worker_exit(controller, run, seconds)
    while time.monotonic() < until:
        status = controller.call('status', run)
        if status['status'] not in ('queued', 'running') and not status['stopping']:
            return controller.call('result', run)
        time.sleep(.05)
    raise AssertionError(controller.call('result', run))


def scripted_stages(controller, *, check, prepare=None, prompts=None, work=None, report=None, runtime=None):
    """Patch one controller so every stage runs an in-process scripted worker; no model, tool or device runs.

    `check` replaces the stage checks; `prepare(original, spec, stage, run_dir, workspace, accepted)` may call
    the real preparation; `work(request)` may call gateway tools first; `report(request)` may return the worker's
    report instead of the default; `runtime(request)` may return the runtime's whole result after the work, such
    as an exit with an error. Each worker writes out.json in its workspace and reports completed.
    """
    from contextlib import ExitStack
    from unittest.mock import patch
    original = controller._prepare
    def fake_prepare(spec, stage, run_dir, workspace, accepted):
        if prepare:
            return prepare(original, spec, stage, run_dir, workspace, accepted)
        return {'inputs': [], 'allowed_tools': []}
    def fake_execute(request, config, cancel):
        if prompts is not None:
            prompts.append((request.stage, request.prompt))
        if work:
            work(request)
        (Path(request.workspace) / 'out.json').write_text(json.dumps({'stage': request.stage}))
        stopped = runtime and runtime(request)
        if stopped:
            return stopped
        return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0, 'usage': None,
                'report': (report and report(request)) or {'status': 'completed', 'summary': 'scripted ' + request.stage,
                           'artifacts': [], 'checks': [], 'unresolved': ['open in ' + request.stage]}}
    stack = ExitStack()
    stack.enter_context(patch.object(controller, '_prepare', side_effect=fake_prepare))
    stack.enter_context(patch.object(controller, '_check', side_effect=check))
    stack.enter_context(patch('generative_driver.configurator.execute', fake_execute))
    return stack


def task_of(prompt):
    """The JSON task after the default prompt's first line."""
    return json.loads(prompt.split('\n', 1)[1])


def gateway_identity(request):
    """The run and assignment a scripted worker's gateway command names."""
    args = request.gateway['args']
    return {'run_id': args[args.index('--run') + 1], 'assignment_id': args[args.index('--assignment') + 1]}


def thermometer_model(name='Scripted thermometer', *, steps=True):
    """A schema-3 line-protocol thermometer on a TCP channel that the real validator accepts; with `steps=False`
    its read has no steps, which the validator and the runtime refuse."""
    def until(command, capture, **extra):
        return {'op': 'exchange', 'tx': [{'text': command}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64},
                'timeout_ms': 1000, 'capture': capture, **extra}
    return {'schema': 'interface-model/3', 'device': {'name': name}, 'channel': {'type': 'tcp'},
            'identity': {'operation': 'identify'},
            'operations': {
                'identify': {'effect': 'read', 'parameters': {}, 'outputs': {},
                             'steps': [until('ID\n', 'id', expect={'contains_text': 'THERMO'})]},
                'read_temperature': {'effect': 'read', 'parameters': {}, 'steps': [until('T\n', 't')] if steps else [],
                                     'outputs': {'temperature_c': {'from': 't', 'kind': 'ascii_number', 'unit': 'degC'}}}},
            'provenance': [{'item': 'operations.' + operation, 'source': 'scripted fixture'}
                           for operation in ('identify', 'read_temperature')]}


def writing_thermometer():
    """The valid thermometer with a write operation, so a run granted write dispatches it as an effect."""
    model = thermometer_model()
    model['operations']['set_rate'] = {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [
        {'op': 'exchange', 'tx': [{'text': 'R\n'}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64},
         'timeout_ms': 1000}]}
    model['provenance'].append({'item': 'operations.set_rate', 'source': 'scripted fixture'})
    return model


def hand_written_package(root, body):
    """A package at `root/package` whose command line is the Python `body`: the path of the package."""
    package = Path(root) / 'package'
    (package / 'driver').mkdir(parents=True)
    (package / 'driver' / '__init__.py').write_text('', encoding='utf-8')
    (package / 'driver' / '__main__.py').write_text(body, encoding='utf-8')
    return package


class TemporaryDirectoryCase(unittest.TestCase):
    def directory(self):
        """A new empty directory, removed when the test ends."""
        temporary = tempfile.TemporaryDirectory(prefix='supplied stages ')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name).resolve()

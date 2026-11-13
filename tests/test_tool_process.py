"""The service runs a generic run's kit tools in a child process with a deadline, records a call that ends without a
result as abandoned, and runs its own checks' tools the same way (docs/features/kit-fixes/04-service-diagnostics.md,
04-B7 to 04-B9).

Scripted workers, temporary homes and a local HTTP server that never answers; no model and no hardware."""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from package_env import PACKAGE_ROOT
from service_fixtures import IDLE_WORKER, entries, entry, serve


def silent_source(test):
    """A local HTTP source that takes a request and does not answer until the test ends: (its URL, the event set once a
    request arrived)."""
    arrived, release = threading.Event(), threading.Event()

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            arrived.set()
            release.wait(60)

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        release.set()
        server.shutdown()
        thread.join(10)
        server.server_close()
    test.addCleanup(stop)
    return 'http://127.0.0.1:' + str(server.server_port) + '/source', arrived


class ServiceCallTests(unittest.TestCase):
    """A background service of the test's own, in a temporary home, stopped when the test ends."""

    def service(self):
        from generative_driver.client import call
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve() / 'home'
        self.addCleanup(call, 'shutdown', {}, home)
        self.assertTrue(call('ping', {}, home)['ok'])
        return lambda method, params=None: call(method, params or {}, home, autostart=False)

    def assigned(self, call):
        """A running generic run whose acquire worker idles, and that worker's assignment."""
        run = call('start', {'goal': 'A run whose worker idles', 'executor_config': {'command': IDLE_WORKER}})
        until = time.monotonic() + 30
        while True:
            found = [event['data'] for event in call('events', run)['events'] if event['kind'] == 'stage.assigned']
            if found and call('status', run)['status'] == 'running':
                return run, found[0]
            self.assertLess(time.monotonic(), until, call('result', run))
            time.sleep(.02)

    def test_04_B7_cancelling_the_run_stops_a_gateway_call_at_once_and_the_run_ends(self):
        call = self.service()
        url, arrived = silent_source(self)
        run, assignment = self.assigned(call)
        replies = {}

        def fetch():
            replies['tool'] = call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_datasheet',
                                            'arguments': {'url': url, 'expected_sha256': '0' * 64}})
        calling = threading.Thread(target=fetch, daemon=True)
        calling.start()
        self.assertTrue(arrived.wait(30), replies)
        call('cancel', run)
        # The source still has not answered: the call ends because the service stopped it.
        calling.join(10)
        self.assertFalse(calling.is_alive(), 'the call still runs 10 seconds after the run was cancelled')
        self.assertEqual((replies['tool']['ok'], replies['tool']['abandoned']), (False, 'cancelled'), replies)
        until = time.monotonic() + 10
        while True:
            status = call('status', run)
            if not status['stopping']:
                break
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)
        self.assertEqual(status['status'], 'cancelled', status)


def blocking_model(directory, pids, *, writes=False):
    """A register model (`interface-model/1`) in `directory` whose conversion module, once a tool loads it, starts a
    child process of its own, records both process ids in the file `pids` and never returns. With `writes` the model
    writes a register, so running it on a device is an effect."""
    directory.mkdir(parents=True)
    steps = [{'op': 'reg_write', 'reg': '0x01', 'bytes': ['0x00']}] if writes else []
    (directory / 'model.json').write_text(json.dumps({
        'schema': 'interface-model/1', 'channel': {'type': 'i2c', 'address_7bit': '0x40'},
        'identity': {'expect': ['0x12']}, 'conversion': {'outputs': {'value': {}}},
        'operations': {'init': steps, 'identify': [{'op': 'reg_read', 'reg': '0x00', 'len': 1}],
                       'measure': [{'op': 'reg_read', 'reg': '0x02', 'len': 2}]}}), encoding='utf-8')
    (directory / 'convert.py').write_text(
        'import os, subprocess, sys, time\n'
        'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])\n'
        'with open(' + repr(str(pids) + '.part') + ', "w") as out:\n'
        '    out.write(str(os.getpid()) + " " + str(child.pid))\n'
        'os.replace(' + repr(str(pids) + '.part') + ', ' + repr(str(pids)) + ')\n'
        'while True:\n'
        '    time.sleep(0.05)\n', encoding='utf-8')
    return directory


def alive(pid):
    """Whether a process with this id still runs."""
    from generative_driver.client import _wait_for_exit
    return not _wait_for_exit(pid, 0)


@contextlib.contextmanager
def saved_rows(home):
    """The rows a home's configurator saves, to write a run there as its rows hold it; kept when the block ends."""
    db = sqlite3.connect(Path(home) / 'runs.sqlite3')
    try:
        with db:
            yield db
    finally:
        db.close()


def benchmark_run(db, workspace):
    """Save a benchmark run at its acquire stage whose worker may call `model_validate`; nothing of it runs. Returns
    what its worker's gateway calls carry."""
    from generative_driver.configurator import PROTOCOL
    workspace.mkdir(parents=True)
    spec = {'goal': 'x', 'case': 'toy', 'executor': 'codex', 'executor_config': {}, 'budget_seconds': 600,
            'effects': ['read']}
    assignment = {'id': 'toy-acquire', 'stage': 'acquire', 'workspace': str(workspace), 'inputs': {},
                  'allowed_tools': ['model_validate'], 'report_required': True, 'binding': None, 'effects': ['read'],
                  'revision': 0}
    db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
               ('run-toy-case', None, json.dumps(spec), 'running', 'acquire', time.time(), time.time()))
    db.execute('INSERT INTO progress VALUES(?,?)', ('run-toy-case', json.dumps(
        {'revision': 0, 'next_stage': 'acquire', 'repairs': 0, 'feedback': None, 'protocol': PROTOCOL})))
    db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
               ('toy-acquire', 'run-toy-case', 'acquire', 'active', json.dumps(assignment), time.time()))
    return {'run_id': 'run-toy-case', 'assignment_id': 'toy-acquire'}


def reuse_run(db, package, workspace):
    """Save a fresh-reuse assignment over the generated catalog of `package`, on a binding nothing listens at; nothing
    of it runs. Returns what its worker's gateway calls carry and the name of the catalog's measuring tool."""
    from generative_driver.configurator import digest
    workspace.mkdir(parents=True)
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    tools = [tool['name'] for tool in json.loads((package / 'mcp-tools.json').read_text(encoding='utf-8'))['tools']]
    binding = {'host': '127.0.0.1', 'port': 9}
    spec = {'goal': 'Measure temperature', 'binding': binding, 'effects': ['read']}
    assignment = {'id': 'reuse-1', 'stage': 'reuse', 'revision': 0, 'workspace': str(workspace),
                  'inputs': {str(package): digest(package)}, 'binding': binding, 'effects': ['read'],
                  'allowed_tools': tools,
                  'device_mcp': {'package_dir': str(package), 'manifest_sha256': digest(package / 'manifest.json'),
                                 'contract_sha256': manifest['contract_sha256'],
                                 'catalog_sha256': digest(package / 'mcp-tools.json')}}
    db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
               ('run-reuse', json.dumps(spec), 'running', 'reuse', time.time(), time.time()))
    db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
               ('reuse-1', 'run-reuse', 'reuse', 'active', json.dumps(assignment), time.time()))
    return {'run_id': 'run-reuse', 'assignment_id': 'reuse-1'}, next(name for name in tools if name.endswith('measure'))


class ChildProcessCase(unittest.TestCase):
    """A configurator in this process built as the service builds one, with child processes on, in a temporary home;
    its workers idle, and the test makes their gateway calls itself."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def controller(self, **options):
        from generative_driver.configurator import Controller
        controller = Controller(self.root / 'home', **{'for_service': True, **options})
        self.addCleanup(controller.close)
        return controller

    def assigned(self, controller, tools=('model_validate', 'probe_run'), **start):
        """A running generic run whose acquire worker idles and may call `tools`, and that worker's assignment."""
        with patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': list(tools)}):
            run = controller.call('start', {'goal': 'A run whose worker idles',
                                            'executor_config': {'command': IDLE_WORKER}, **start})
            until = time.monotonic() + 30
            while True:
                found = self.events(controller, run, 'stage.assigned')
                if found and controller.call('status', run)['status'] == 'running':
                    return run, found[0]
                self.assertLess(time.monotonic(), until, controller.call('result', run))
                time.sleep(.02)

    def events(self, controller, run, kind):
        return [event['data'] for event in controller.call('events', run)['events'] if event['kind'] == kind]

    def dispatch_ids(self, controller, run):
        """The id of each `tool.started` event of the run, as a call's later events name it."""
        return [str(event['id']) for event in controller.call('events', run)['events']
                if event['kind'] == 'tool.started']

    def ended(self, controller, run, seconds=30):
        """The run's status once it has stopped and nothing of it is left stopping."""
        until = time.monotonic() + seconds
        while True:
            status = controller.call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return status
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)

    def blocked_call(self, controller, run, assignment, name='model_validate', **arguments):
        """Start a gateway call of a tool that loads a blocking model, in a thread: (the dict its reply or its error
        goes to, the thread, the ids of the tool's process and of the child that process started), once both run."""
        pids = self.root / (name + '-' + str(time.monotonic_ns()) + '.pids')
        model = blocking_model(Path(assignment['workspace']) / pids.stem, pids, writes=name == 'probe_run')
        ended = {}

        def call():
            try:
                ended['reply'] = controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': name,
                                                          'arguments': {'model_dir': str(model), **arguments}})
            except Exception as error:
                ended['error'] = error
        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        until = time.monotonic() + 30
        while not pids.exists():
            self.assertTrue(thread.is_alive() and time.monotonic() < until, ended)
            time.sleep(.02)
        tool, child = (int(pid) for pid in pids.read_text(encoding='utf-8').split())
        self.addCleanup(self.stop_processes, tool, child)
        self.assertTrue(alive(tool) and alive(child))
        return ended, thread, (tool, child)

    @staticmethod
    def stop_processes(*pids):
        """Whatever a failed test left running is stopped."""
        import signal
        import subprocess
        for pid in pids:
            if alive(pid):
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=30)
                else:
                    os.kill(pid, signal.SIGKILL)

    def gone(self, pids, seconds=10):
        """Whether every one of these processes has ended, waiting a little for the system to take them away."""
        until = time.monotonic() + seconds
        while any(alive(pid) for pid in pids) and time.monotonic() < until:
            time.sleep(.02)
        return not any(alive(pid) for pid in pids)


class DeadlineTests(ChildProcessCase):
    def test_04_B7_a_call_is_stopped_with_every_process_it_started_when_its_limit_passes(self):
        from generative_driver import tool_process
        self.assertEqual(tool_process.LIMIT_SECONDS, 570)
        controller = self.controller()
        run, assignment = self.assigned(controller)
        with patch.object(tool_process, 'LIMIT_SECONDS', 1.5):
            began = time.monotonic()
            ended, thread, pids = self.blocked_call(controller, run, assignment)
            thread.join(20)
        self.assertFalse(thread.is_alive(), 'the call still runs long after its limit passed')
        self.assertGreaterEqual(time.monotonic() - began, 1.5)
        self.assertEqual((ended['reply']['ok'], ended['reply']['abandoned']), (False, 'timeout'), ended)
        self.assertTrue(self.gone(pids), 'the tool\'s process or the child it started still runs')
        controller.call('cancel', run)

    def test_04_B7_a_call_is_stopped_when_the_runs_budget_runs_out_before_its_limit(self):
        controller = self.controller()
        run, assignment = self.assigned(controller, budget_seconds=4)
        deadline = controller.call('status', run)['deadline']
        ended, thread, pids = self.blocked_call(controller, run, assignment)
        thread.join(30)
        self.assertFalse(thread.is_alive(), 'the call still runs long after the run\'s budget ran out')
        self.assertGreaterEqual(time.time(), deadline)
        self.assertEqual((ended['reply']['ok'], ended['reply']['abandoned']), (False, 'budget'), ended)
        self.assertTrue(self.gone(pids), 'the tool\'s process or the child it started still runs')
        # The call no longer holds the run, which ends for its spent budget.
        until = time.monotonic() + 30
        while True:
            status = controller.call('status', run)
            if status['status'] != 'running' and not status['stopping']:
                break
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'operator'), status)

    def test_04_B7_the_service_shutting_down_stops_a_call_at_once(self):
        controller = self.controller()
        run, assignment = self.assigned(controller)
        ended, thread, pids = self.blocked_call(controller, run, assignment)
        began = time.monotonic()
        self.assertTrue(controller.close(timeout=20), 'the run\'s worker did not stop')
        thread.join(20)
        self.assertFalse(thread.is_alive(), 'the call still runs after the configurator closed')
        self.assertLess(time.monotonic() - began, 15)
        self.assertEqual((ended['reply']['ok'], ended['reply']['abandoned']), (False, 'shutdown'), ended)
        self.assertTrue(self.gone(pids), 'the tool\'s process or the child it started still runs')

    def test_04_B7_a_call_whose_deadline_passed_while_it_waited_is_refused_without_being_started(self):
        from generative_driver import tool_process
        controller = self.controller()
        run, assignment = self.assigned(controller, effects=['read', 'write'], binding={'url': 'ftdi://ftdi:232h/1'})
        with patch.object(tool_process, 'LIMIT_SECONDS', 3):
            # The first call holds the run for its 3 seconds.
            first, holding, pids = self.blocked_call(controller, run, assignment)
            with patch.object(tool_process, 'LIMIT_SECONDS', 0.5):
                # The second arrives with half a second, waits behind the first and would write to the device.
                pids_file = self.root / 'second.pids'
                model = blocking_model(Path(assignment['workspace']) / 'second', pids_file, writes=True)
                with self.assertRaisesRegex(ValueError, 'deadline passed before it was dispatched; nothing was '
                                                        'dispatched') as refused:
                    controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'probe_run',
                                             'arguments': {'model_dir': str(model)}})
            holding.join(20)
        self.assertEqual(first['reply']['abandoned'], 'timeout', first)
        self.assertTrue(self.gone(pids))
        # Refused, never started: one dispatch only, the refusal on record and no effect left uncertain.
        self.assertEqual([event['name'] for event in self.events(controller, run, 'tool.started')], ['model_validate'])
        self.assertEqual([(event['name'], event['reason']) for event in self.events(controller, run, 'tool.refused')],
                         [('probe_run', str(refused.exception))])
        self.assertFalse(pids_file.exists())
        self.assertFalse(controller.call('status', run)['uncertain_effect'])
        controller.call('cancel', run)


class AbandonedTests(ChildProcessCase):
    def test_04_B8_a_call_the_service_stopped_is_recorded_as_abandoned_never_as_finished_and_lets_the_run_end(self):
        controller = self.controller()
        run, assignment = self.assigned(controller)
        ended, thread, pids = self.blocked_call(controller, run, assignment)
        cancelled = controller.call('cancel', run)
        self.assertTrue(cancelled['stopping'], cancelled)
        thread.join(20)
        self.assertEqual(ended['reply'], {'ok': False, 'abandoned': 'cancelled', 'reason':
                                          'model_validate did not finish: the configurator stopped it because the run '
                                          'was cancelled'})
        self.assertEqual(self.events(controller, run, 'tool.abandoned'), [
            {'stage': 'acquire', 'assignment_id': assignment['id'], 'name': 'model_validate',
             'dispatch_id': self.dispatch_ids(controller, run)[0], 'reason': 'cancelled', 'actor': 'worker'}])
        self.assertEqual(self.events(controller, run, 'tool.finished'), [])
        self.assertTrue(self.gone(pids))
        # Once its process has ended the call no longer holds the run: nothing is left stopping.
        status = self.ended(controller, run)
        self.assertEqual((status['status'], status['uncertain_effect']), ('cancelled', False), status)
        # The stop has its line in the service's log.
        written = entry(controller.home, 'run=' + run['run_id'], 'tool=model_validate')
        self.assertEqual(len(written.strip().splitlines()), 1, written)
        self.assertIn('stage=acquire', written)
        self.assertIn('reason=cancelled', written)

    def test_04_B8_an_effect_a_stopped_call_might_have_had_stays_uncertain_until_the_operator_confirms_it(self):
        from generative_driver import tool_process
        controller = self.controller()
        run, assignment = self.assigned(controller, effects=['read', 'write'], binding={'url': 'ftdi://ftdi:232h/1'})
        with patch.object(tool_process, 'LIMIT_SECONDS', 1.5):
            # The model writes a register, so the call might have written by the time it is stopped.
            ended, thread, pids = self.blocked_call(controller, run, assignment, 'probe_run')
            thread.join(20)
        self.assertEqual(ended['reply'], {'ok': False, 'abandoned': 'timeout', 'uncertain_effect': True, 'reason':
                                          'probe_run did not finish: the configurator stopped it when its limit of 1.5 '
                                          'seconds passed'})
        self.assertTrue(self.gone(pids))
        abandoned = self.events(controller, run, 'tool.abandoned')
        self.assertEqual([(event['name'], event['reason'], event['dispatch_id']) for event in abandoned],
                         [('probe_run', 'timeout', self.dispatch_ids(controller, run)[0])])
        # Nothing completed the call later, and no second effect is dispatched while the first is uncertain.
        self.assertEqual(self.events(controller, run, 'tool.finished'), [])
        self.assertTrue(controller.call('status', run)['uncertain_effect'])
        second = blocking_model(Path(assignment['workspace']) / 'second', self.root / 'second.pids', writes=True)
        with self.assertRaisesRegex(ValueError, 'Outstanding effect is uncertain'):
            controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'probe_run',
                                     'arguments': {'model_dir': str(second)}})
        self.assertFalse((self.root / 'second.pids').exists())
        controller.call('cancel', run)
        self.assertTrue(self.ended(controller, run)['uncertain_effect'])
        with self.assertRaisesRegex(ValueError, 'Outstanding effect is uncertain'):
            controller.call('resume', run)
        confirmed = controller.call('respond', {**run, 'message': 'The operator checked the device',
                                                'effect_resolution': 'confirmed_safe'})
        self.assertFalse(confirmed['uncertain_effect'])

    def test_04_B8_a_tool_process_that_ends_without_a_result_is_abandoned_with_what_ended_it(self):
        controller = self.controller()
        run, assignment = self.assigned(controller)
        work = Path(assignment['workspace'])
        for label, ending, kind, message, said in (
                ('exits', 'import os\nos._exit(7)\n', 'ToolProcessExit', 'exit status 7',
                 'its process ended without a result (exit status 7)'),
                ('raises', 'raise SystemExit("the conversion module gave up")\n', 'SystemExit',
                 'the conversion module gave up', 'it raised SystemExit: the conversion module gave up')):
            with self.subTest(label=label):
                model = blocking_model(work / label, self.root / (label + '.pids'))
                (model / 'convert.py').write_text(ending, encoding='utf-8')
                reply = controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'model_validate',
                                                 'arguments': {'model_dir': str(model)}})
                self.assertEqual(reply, {'ok': False, 'abandoned': 'raised',
                                         'reason': 'model_validate did not finish: ' + said})
                abandoned = self.events(controller, run, 'tool.abandoned')[-1]
                self.assertEqual((abandoned['reason'], abandoned['type'], abandoned['message']),
                                 ('raised', kind, message))
                self.assertEqual(abandoned['dispatch_id'], self.dispatch_ids(controller, run)[-1])
        self.assertEqual(self.events(controller, run, 'tool.finished'), [])
        # What the tool's process let through has its traceback in the service's log, with the run, stage and tool.
        raised = [written for written in entries(controller.home)
                  if all(needle in written.splitlines()[0] for needle in
                         ('run=' + run['run_id'], 'stage=acquire', 'tool=model_validate', 'SystemExit'))]
        traced = [written for written in raised if 'Traceback (most recent call last):' in written]
        self.assertEqual(len(traced), 1, raised)
        self.assertIn('raise SystemExit("the conversion module gave up")', traced[0])
        # The run is still the service's to run: the same worker's next call is answered.
        self.assertEqual(controller.call('status', run)['status'], 'running')
        controller.call('cancel', run)

    @staticmethod
    def raising(name, arguments):
        """In place of `call_tool`: a tool that raises what neither the toolkit nor the toolchain catches."""
        raise RuntimeError('scripted tool failure')

    def test_04_B8_a_call_that_raises_outside_the_service_is_recorded_as_abandoned_and_still_raises(self):
        from generative_driver.configurator import Controller
        # A configurator a test builds: it runs tools inline, and what a tool raises reaches the test, as it always did.
        controller = Controller(self.root / 'inline-home')
        self.addCleanup(controller.close)
        run, assignment = self.assigned(controller, effects=['read', 'write'], binding={'url': 'ftdi://ftdi:232h/1'})
        request = {**run, 'assignment_id': assignment['id']}
        model = blocking_model(Path(assignment['workspace']) / 'model', self.root / 'unused.pids', writes=True)
        with patch('generative_driver.toolkit.call_tool', self.raising):
            with self.assertRaisesRegex(RuntimeError, 'scripted tool failure'):
                controller.call('tool', {**request, 'name': 'model_validate', 'arguments': {}})
            with self.assertRaisesRegex(RuntimeError, 'scripted tool failure'):
                controller.call('tool', {**request, 'name': 'probe_run', 'arguments': {'model_dir': str(model)}})
        started = self.dispatch_ids(controller, run)
        self.assertEqual(self.events(controller, run, 'tool.abandoned'), [
            {'stage': 'acquire', 'assignment_id': assignment['id'], 'name': name, 'dispatch_id': dispatch,
             'reason': 'raised', 'type': 'RuntimeError', 'message': 'scripted tool failure', 'actor': 'worker'}
            for name, dispatch in zip(('model_validate', 'probe_run'), started)])
        self.assertEqual(self.events(controller, run, 'tool.finished'), [])
        self.assertTrue(controller.call('status', run)['uncertain_effect'])
        # The exception has its full entry in the log.
        written = entry(controller.home, 'run=' + run['run_id'], 'tool=model_validate')
        self.assertIn('stage=acquire', written.splitlines()[0])
        self.assertIn('RuntimeError: scripted tool failure', written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)
        self.assertRegex(written, r'in raising\n')
        controller.call('cancel', run)

    def test_04_B8_the_service_answers_a_call_that_raised_inside_it_with_the_abandoned_reply(self):
        # The service runs in a thread of this test, so that the tool it runs inline can be replaced: a benchmark run's
        # tool, which the service does not move to a child process.
        home = self.root / 'service-home'
        call = serve(self, home)
        with saved_rows(home) as db:
            worker = benchmark_run(db, self.root / 'work')
        with patch('generative_driver.toolkit.call_tool', self.raising):
            reply = call('tool', {**worker, 'name': 'model_validate', 'arguments': {}})
        self.assertEqual(reply, {'ok': False, 'abandoned': 'raised', 'reason':
                                 'model_validate did not finish: it raised RuntimeError: scripted tool failure'})
        events = call('events', {'run_id': worker['run_id']})['events']
        started = [str(event['id']) for event in events if event['kind'] == 'tool.started']
        self.assertEqual([event['data'] for event in events if event['kind'] == 'tool.abandoned'], [
            {'stage': 'acquire', 'assignment_id': worker['assignment_id'], 'name': 'model_validate',
             'dispatch_id': started[0], 'reason': 'raised', 'type': 'RuntimeError', 'message': 'scripted tool failure',
             'actor': 'worker'}])
        self.assertEqual([event for event in events if event['kind'] == 'tool.finished'], [])
        written = entry(home, 'run=' + worker['run_id'], 'tool=model_validate')
        self.assertIn('Traceback (most recent call last):', written)

    def test_04_B8_a_fresh_reuse_call_that_raises_after_it_began_is_abandoned_in_and_outside_the_service(self):
        from generative_driver.configurator import Controller
        from test_driver_contract import emit_fixture
        from test_package import example_model
        model = example_model()
        model['channel'] = {'type': 'tcp'}
        package, _ = emit_fixture(self.root, model)
        failing = patch('generative_driver.device_access.DeviceAccess.invoke_package',
                        side_effect=RuntimeError('scripted package process failure'))
        abandoned = {'stage': 'reuse', 'assignment_id': 'reuse-1', 'reason': 'raised', 'type': 'RuntimeError',
                     'message': 'scripted package process failure', 'actor': 'worker'}
        # Outside the service the exception reaches the caller, with the call on record as abandoned.
        controller = Controller(self.root / 'reuse-home')
        self.addCleanup(controller.close)
        with saved_rows(controller.home) as db:
            worker, name = reuse_run(db, package, controller.home / 'runs/run-reuse/reuse')
        with failing, self.assertRaisesRegex(RuntimeError, 'scripted package process failure'):
            controller.call('mcp_tool', {**worker, 'name': name, 'arguments': {}, 'request_id': 'measure-1'})
        run = {'run_id': worker['run_id']}
        self.assertEqual(self.events(controller, run, 'tool.abandoned'),
                         [{**abandoned, 'name': name, 'dispatch_id': self.dispatch_ids(controller, run)[0]}])
        self.assertEqual(self.events(controller, run, 'tool.finished'), [])
        # The service answers the worker with the abandoned reply.
        home = self.root / 'service-home'
        call = serve(self, home)
        with saved_rows(home) as db:
            worker, name = reuse_run(db, package, home / 'runs/run-reuse/reuse')
        with failing:
            reply = call('mcp_tool', {**worker, 'name': name, 'arguments': {}, 'request_id': 'measure-1'})
        self.assertEqual(reply, {'ok': False, 'abandoned': 'raised', 'reason':
                                 name + ' did not finish: it raised RuntimeError: scripted package process failure'})
        events = call('events', run)['events']
        started = [str(event['id']) for event in events if event['kind'] == 'tool.started']
        self.assertEqual([event['data'] for event in events if event['kind'] == 'tool.abandoned'],
                         [{**abandoned, 'name': name, 'dispatch_id': started[0]}])
        self.assertEqual([event for event in events if event['kind'] == 'tool.finished'], [])

    def test_04_B1_what_a_kit_tool_catches_in_its_child_process_is_logged_with_the_run_stage_and_tool(self):
        controller = self.controller()
        run, assignment = self.assigned(controller, tools=('acquire_firmware_artifact',))
        # The tool is called without what it requires; its own wrapper catches that, in the tool's process.
        reply = controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_firmware_artifact',
                                         'arguments': {}})
        self.assertTrue(reply['error'].startswith('TypeError: acquire_firmware_artifact() missing'), reply)
        self.assertEqual([event['ok'] for event in self.events(controller, run, 'tool.finished')], [False])
        written = entry(controller.home, 'run=' + run['run_id'], 'tool=acquire_firmware_artifact')
        self.assertIn('stage=acquire', written.splitlines()[0])
        self.assertIn(reply['error'], written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)
        self.assertRegex(written, r'in wrapper\n')
        controller.call('cancel', run)


class CheckTests(unittest.TestCase):
    """The service's own checks, in a service that runs in a thread of this test so that the limit can be shortened.
    Workers of this test stand in for Codex; the kit's scripted worker does the acquire stage through its gateway
    where a firmware stand-in must be imported."""

    # Launched in place of Codex with a model folder and a stage name: acquire is the scripted worker's, interpret
    # writes the register model found in the folder, the named stage and every later one wait to be stopped, and a
    # stage between them ends at once.
    MODELLING = ('import io, json, shutil, sys, time\n'
                 'from pathlib import Path\n'
                 'sys.path.insert(0, ' + repr(PACKAGE_ROOT) + ')\n'
                 'stages = ["acquire", "interpret", "probe", "ground", "emit", "reuse"]\n'
                 'prompt = sys.stdin.buffer.read()\n'
                 'try:\n'
                 '    stage = json.loads(prompt.decode("utf-8").partition(chr(10))[2])["stage"]\n'
                 'except (ValueError, KeyError, TypeError):\n'
                 '    stage = "interpret"\n'
                 'if stage == "acquire":\n'
                 '    sys.stdin = io.TextIOWrapper(io.BytesIO(prompt), encoding="utf-8")\n'
                 '    from generative_driver.scripted_worker import main\n'
                 '    main(sys.argv[3:])\n'
                 'elif stage == "interpret":\n'
                 '    workspace = Path(sys.argv[sys.argv.index("-C") + 1])\n'
                 '    for name in ("model.json", "convert.py"):\n'
                 '        shutil.copyfile(Path(sys.argv[1]) / name, workspace / name)\n'
                 'elif stages.index(stage) >= stages.index(sys.argv[2]):\n'
                 '    time.sleep(120)\n')
    # Launched in place of Codex with a file name: every stage ends at once but emit, which waits for the file and
    # reports the package the file names.
    EMITTING = ('import json, sys, time\n'
                'from pathlib import Path\n'
                'try:\n'
                '    stage = json.loads(sys.stdin.read().partition(chr(10))[2])["stage"]\n'
                'except (ValueError, KeyError, TypeError):\n'
                '    stage = None\n'
                'if stage == "emit":\n'
                '    told = Path(sys.argv[1])\n'
                '    while not told.exists():\n'
                '        time.sleep(.02)\n'
                '    package = {"path": told.read_text(encoding="utf-8"), "sha256": "claimed", "kind": "package"}\n'
                '    report = {"status": "completed", "summary": "built", "artifacts": [package], "checks": [],\n'
                '              "unresolved": []}\n'
                '    message = {"type": "agent_message", "text": json.dumps(report)}\n'
                '    print(json.dumps({"type": "item.completed", "item": message}))\n')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'

    def shorten(self, seconds):
        """Give every tool the service runs from now on `seconds`, until the test ends."""
        from generative_driver import tool_process
        limit = patch.object(tool_process, 'LIMIT_SECONDS', seconds)
        limit.start()
        self.addCleanup(limit.stop)

    def follow(self, call, run, reached, seconds=120):
        """The run's events once `reached(events)` holds; the run must not stop before."""
        until = time.monotonic() + seconds
        while True:
            events = call('events', run)['events']
            if reached(events):
                return events
            status = call('status', run)
            self.assertTrue(status['status'] in ('queued', 'running') and time.monotonic() < until,
                            (status, [event['kind'] for event in events]))
            time.sleep(.1)

    @staticmethod
    def assigned(events, stage):
        return next((event['data'] for event in events
                     if event['kind'] == 'stage.assigned' and event['data']['stage'] == stage), None)

    @staticmethod
    def checks(events, stage):
        """The configurator's checks in the notes of the stage's accepted handoff, by name, and the stage's outcome."""
        notes = next(event['data'] for event in events
                     if event['kind'] == 'stage.accepted' and event['data']['stage'] == stage)['notes']['inline']
        return {check['name']: check for check in notes['configurator']['checks']}, notes['outcome']['kind']

    def interpreted(self, convert, waits_in):
        """Run a generic run in which the worker writes a register model whose conversion module is `convert`, up to
        the stage its workers wait in: (the run's events, the service's `call`, the run)."""
        model = blocking_model(self.root / 'written', self.root / 'unused.pids')
        (model / 'convert.py').write_text(convert, encoding='utf-8')
        firmware = self.root / 'firmware.bin'
        firmware.write_bytes(b'a firmware stand-in, not a firmware image\n')
        self.shorten(4)
        call = serve(self, self.home)
        command = [sys.executable, '-c', self.MODELLING, str(model), waits_in]
        run = call('start', {'goal': 'A run whose interpreted model has a bad conversion module',
                             'inputs': {'firmware.bin': str(firmware)}, 'budget_seconds': 300,
                             'executor_config': {'command': command}})
        self.addCleanup(call, 'cancel', run)
        return self.follow(call, run, lambda events: self.assigned(events, waits_in)), call, run

    def test_04_B9_a_conversion_module_that_loops_costs_a_failed_check_and_the_run_carries_on(self):
        events, call, run = self.interpreted('while True:\n    pass\n', waits_in='probe')
        checks, outcome = self.checks(events, 'interpret')
        self.assertEqual((checks['executable_model_contract']['passed'], checks['executable_model_contract']['reason']),
                         (False, 'model_validate did not finish: the configurator stopped it when its limit of 4 '
                                 'seconds passed'))
        # A failed check is a note: the stage advanced and the run went on to probe, with no host stop.
        self.assertEqual(outcome, 'advance')
        status = call('status', run)
        self.assertEqual((status['status'], status['stage'], status['reason']), ('running', 'probe', None), status)
        self.assertIn('executable_model_contract', status['stage_notes'][1]['failed_checks'])
        # The check that was stopped has its line in the service's log.
        written = entry(self.home, 'run=' + run['run_id'], 'stage=interpret', 'tool=model_validate')
        self.assertIn('reason=timeout', written)

    def test_04_B9_a_conversion_module_that_exits_the_process_costs_a_failed_check_not_the_service(self):
        events, call, run = self.interpreted('import os\nos._exit(7)\n', waits_in='ground')
        exited = 'model_validate did not finish: its process ended without a result (exit status 7)'
        checks, outcome = self.checks(events, 'interpret')
        self.assertEqual((checks['executable_model_contract']['passed'], checks['executable_model_contract']['reason']),
                         (False, exited))
        self.assertEqual(outcome, 'advance')
        # The probe stage validates the model it was assigned, with the same module: a failed check there too.
        checks, outcome = self.checks(events, 'probe')
        validated = checks['executable_model_contract']
        self.assertEqual((validated['passed'], [model['reason'] for model in validated['models']]),
                         (False, ['Validation did not run: ' + exited]))
        self.assertEqual(outcome, 'advance')
        # The service, which runs in this very process, is still here and answers.
        status = call('status', run)
        self.assertEqual((status['status'], status['stage'], status['reason']), ('running', 'ground', None), status)

    def test_04_B9_an_emit_check_that_was_stopped_rejects_the_package_and_is_no_host_stop(self):
        call = serve(self, self.home)
        told = self.root / 'package-to-report'
        run = call('start', {'goal': 'A run whose emitted package cannot be checked in time', 'budget_seconds': 300,
                             'executor_config': {'command': [sys.executable, '-c', self.EMITTING, str(told)]}})
        self.addCleanup(call, 'cancel', run)
        emit = self.assigned(self.follow(call, run, lambda events: self.assigned(events, 'emit')), 'emit')
        # The worker gives a package to emit_check through its gateway, so the configurator checks those bytes itself.
        package = Path(emit['workspace']) / 'package'
        package.mkdir()
        (package / 'manifest.json').write_text('{}', encoding='utf-8')
        given = call('tool', {**run, 'assignment_id': emit['id'], 'name': 'emit_check',
                              'arguments': {'package_dir': str(package)}})
        self.assertNotIn('abandoned', given)
        # From here a tool has no time at all: the worker reports the package and the configurator's own emit_check
        # of it is stopped.
        self.shorten(0)
        (self.root / 'told.part').write_text(str(package), encoding='utf-8')
        os.replace(self.root / 'told.part', told)
        events = self.follow(call, run, lambda events: any(
            event['kind'] == 'stage.accepted' and event['data']['stage'] == 'emit' for event in events))
        checks, outcome = self.checks(events, 'emit')
        replayed = checks['package_integrity_and_replay']
        self.assertFalse(replayed['passed'])
        self.assertEqual(replayed['rejected'], [{'package': 'package', 'reason': 'emit_check did not finish: the '
                                                 'configurator stopped it when its limit of 0 seconds passed'}])
        self.assertEqual(outcome, 'advance')
        # No host stop: the run goes on without a package and ends with its verdict.
        until = time.monotonic() + 60
        while True:
            status = call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                break
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.05)
        self.assertEqual((status['status'], status['outcome_category']), ('completed', 'completed'), status)


class InlineTests(ChildProcessCase):
    """Where tools stay in the configurator's own process, so that a test's replacement for `call_tool` takes effect."""

    @staticmethod
    def replacement(name, arguments):
        return {'ok': True, 'ran': name, 'in_process': os.getpid(), '_exit': 0}

    def test_04_B7_a_controller_built_outside_the_service_runs_tools_inline(self):
        from generative_driver.configurator import Controller
        controller = Controller(self.root / 'another-home')
        self.addCleanup(controller.close)
        with patch('generative_driver.toolkit.call_tool', self.replacement):
            run, assignment = self.assigned(controller)
            reply = controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'model_validate',
                                             'arguments': {}})
        self.assertEqual((reply['ran'], reply['in_process']), ('model_validate', os.getpid()))
        self.assertFalse((controller.home / 'runs' / run['run_id'] / 'tool-calls').exists())
        controller.call('cancel', run)

    def test_04_B7_a_benchmark_run_keeps_the_inline_path_where_the_service_runs_tools_in_child_processes(self):
        controller = self.controller()
        with saved_rows(controller.home) as db:
            worker = benchmark_run(db, self.root / 'work')
        with patch('generative_driver.toolkit.call_tool', self.replacement):
            reply = controller.call('tool', {**worker, 'name': 'model_validate', 'arguments': {}})
        self.assertEqual((reply['ran'], reply['in_process']), ('model_validate', os.getpid()))


if __name__ == '__main__':
    unittest.main()

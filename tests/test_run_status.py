"""What a run's `status` adds for whoever watches it: the service's log, the deadline, the repairs used and allowed and
the running worker's last sign of life (docs/features/kit-fixes/04-service-diagnostics.md, 04-B3, 04-B5 and 04-B6).

Scripted workers and temporary homes only; no model and no hardware."""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from package_env import package_env
from service_fixtures import IDLE_WORKER


class StatusCase(unittest.TestCase):
    """A configurator in this process, in a temporary home, closed when the test ends."""

    def controller(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        controller = Controller(self.root / 'home')
        self.addCleanup(controller.close)
        return controller

    def assigned(self, controller, command=IDLE_WORKER, **start):
        """A running generic run whose acquire worker runs `command`, and that worker's assignment."""
        run = controller.call('start', {'goal': 'A run to read the status of', 'executor_config': {'command': command},
                                        **start})
        until = time.monotonic() + 30
        while True:
            found = [event['data'] for event in controller.call('events', run)['events']
                     if event['kind'] == 'stage.assigned']
            if found and controller.call('status', run)['status'] == 'running':
                return run, found[0]
            self.assertLess(time.monotonic(), until, controller.call('result', run))
            time.sleep(.02)

    def stopped(self, controller, run, seconds=30):
        """The run's status once it has stopped and nothing of it is left stopping."""
        until = time.monotonic() + seconds
        while True:
            status = controller.call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return status
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)


class LogPathTests(StatusCase):
    def test_04_B3_status_names_the_service_log_whether_or_not_the_run_has_an_entry_in_it(self):
        controller = self.controller()
        run, assignment = self.assigned(controller)
        log = controller.home / 'daemon.log'
        self.assertFalse(log.exists())
        self.assertEqual(controller.call('status', run)['log'], str(log))
        # Once the run has an entry (its tool call was refused), the status names the same file.
        with self.assertRaises(ValueError):
            controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'probe_run', 'arguments': {}})
        self.assertIn(run['run_id'], log.read_text(encoding='utf-8'))
        controller.call('cancel', run)
        self.assertEqual(self.stopped(controller, run)['log'], str(log))


class DeadlineAndRepairsTests(StatusCase):
    def blocked(self, controller, **start):
        """A run that stopped because its worker runtime cannot be started; it can be resumed."""
        run = controller.call('start', {'goal': 'A run that stops before its first worker',
                                        'executor_config': {'command': ['no-such-worker-runtime']}, **start})
        status = self.stopped(controller, run)
        self.assertEqual(status['status'], 'blocked', status)
        return run, status

    @staticmethod
    def save_progress(controller, run, change):
        """Replace the run's saved progress by `change` of it."""
        with controller._db() as db:
            saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run['run_id'],)).fetchone()[0]
            db.execute('UPDATE progress SET payload=? WHERE run_id=?',
                       (json.dumps(change(json.loads(saved))), run['run_id']))

    def test_04_B5_deadline_is_when_the_budget_runs_out_after_any_extension(self):
        controller = self.controller()
        run, status = self.blocked(controller, budget_seconds=600)
        self.assertEqual(status['deadline'], status['created'] + 600)
        controller.call('resume', {**run, 'budget_seconds': 900, 'budget_reason': 'The operator approved more time'})
        extended = self.stopped(controller, run)
        self.assertEqual(extended['deadline'], status['created'] + 900)

    def test_04_B5_repairs_gives_the_repairs_made_and_the_repair_budget(self):
        controller = self.controller()
        run, status = self.blocked(controller)
        self.assertEqual(status['repairs'], {'used': 0, 'budget': 2})
        limited, status = self.blocked(controller, max_revisions=1)
        self.assertEqual(status['repairs'], {'used': 0, 'budget': 1})
        # The count is the one the run's saved progress records, as after one repair.
        self.save_progress(controller, run, lambda saved: {**saved, 'repairs': 1})
        self.assertEqual(controller.call('status', run)['repairs'], {'used': 1, 'budget': 2})

    def test_04_B5_repairs_is_null_when_the_saved_progress_records_no_repair_count(self):
        controller = self.controller()
        run, _ = self.blocked(controller)
        self.save_progress(controller, run, lambda saved: {key: saved[key] for key in saved if key != 'repairs'})
        status = controller.call('status', run)
        self.assertIsNone(status['repairs'])
        with controller._db() as db:
            db.execute('DELETE FROM progress WHERE run_id=?', (run['run_id'],))
        self.assertIsNone(controller.call('status', run)['repairs'])

    def test_04_B5_a_stopped_run_reads_the_same_twice_and_every_reply_built_from_status_adds_the_fields(self):
        controller = self.controller()
        run, status = self.blocked(controller)
        self.assertIsNone(status['worker'])
        time.sleep(.05)
        self.assertEqual(controller.call('status', run), status)
        added = {key: status[key] for key in ('deadline', 'repairs', 'worker', 'log')}
        result = controller.call('result', run)
        self.assertEqual({key: result[key] for key in added}, added)
        self.assertEqual(controller.call('result', run), result)
        responded = controller.call('respond', {**run, 'message': 'The operator looked at the device'})
        self.assertEqual({key: responded[key] for key in added}, added)
        cancelled = controller.call('cancel', run)
        self.assertEqual({key: cancelled[key] for key in added}, added)
        resumed = controller.call('resume', run)
        self.assertEqual({key: resumed[key] for key in ('deadline', 'repairs', 'log')},
                         {key: added[key] for key in ('deadline', 'repairs', 'log')})
        self.stopped(controller, run)

    def test_04_B5_reading_the_fields_never_fails_for_a_run_saved_by_an_earlier_version(self):
        controller = self.controller()
        # Rows as earlier versions may have left them: a spec without a budget or with one that is no number, a repair
        # budget that is no number, and progress that is absent, of the gated protocol or not an object.
        saved = {'no budget': ({'goal': 'x'}, None),
                 'no number': ({'budget_seconds': 'three hours', 'max_revisions': 'a few'}, json.dumps({'repairs': 1})),
                 'gated progress': ({'budget_seconds': 600}, json.dumps({'maintenance_cycles': 2, 'stage': 'probe'})),
                 'no object': ({'budget_seconds': 600}, json.dumps(['repairs'])),
                 'no json': ({'budget_seconds': 600}, 'not json')}
        with controller._db() as db:
            for index, (spec, progress) in enumerate(saved.values()):
                db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                           ('run-saved-' + str(index), None, json.dumps(spec), 'blocked', 'probe', 1000.0, 2000.0))
                if progress is not None:
                    db.execute('INSERT INTO progress VALUES(?,?)', ('run-saved-' + str(index), progress))
        read = {name: controller.call('status', {'run_id': 'run-saved-' + str(index)})
                for index, name in enumerate(saved)}
        for name, status in read.items():
            with self.subTest(name=name):
                self.assertEqual((status['ok'], status['status'], status['worker']), (True, 'blocked', None))
                self.assertEqual(status['log'], str(controller.home / 'daemon.log'))
        self.assertEqual([read[name]['deadline'] for name in saved], [None, None, 1600.0, 1600.0, 1600.0])
        self.assertEqual([read[name]['repairs'] for name in saved],
                         [None, {'used': 1, 'budget': None}, None, None, None])

    def test_04_B5_a_suite_reply_built_from_a_childs_status_stays_as_it_was(self):
        from generative_driver.configurator import Controller
        controller = self.controller()
        run, status = self.blocked(controller)
        # A suite's reply to its harness that passes its child's status on, as a resumed child's does.
        with unittest.mock.patch.object(Controller, '_suite_call', return_value={**status, 'duplicate': True}):
            reply = controller.call('suite_resume_child', {'suite_id': 'suite-x'})
        self.assertEqual(set(reply), {'ok', 'status', 'stage', 'reason', 'created', 'updated', 'outcome_category',
                                      'run_id', 'uncertain_effect', 'stopping', 'equivalence', 'stage_notes',
                                      'duplicate'})
        self.assertEqual(reply, {key: value for key, value in status.items() if key in reply} | {'duplicate': True})


class WorkerTests(StatusCase):
    # A worker that prints one line each time the test makes the next of its two files, then waits.
    PRINTING = ('import sys,time; from pathlib import Path\n'
                'for name in sys.argv[1:3]:\n'
                '    while not Path(name).exists(): time.sleep(.02)\n'
                '    print("a line of output", flush=True)\n'
                'time.sleep(120)')

    def worker(self, controller, run, kind):
        """The status's `worker` once its last activity is of `kind`."""
        until = time.monotonic() + 30
        while True:
            worker = controller.call('status', run)['worker']
            if worker is not None and worker['last_activity_kind'] == kind:
                return worker
            self.assertLess(time.monotonic(), until, worker)
            time.sleep(.02)

    def test_04_B5_worker_gives_the_running_workers_stage_start_and_last_sign_of_life(self):
        controller = self.controller()
        first, second = self.root / 'first', self.root / 'second'
        run, assignment = self.assigned(controller, [sys.executable, '-c', self.PRINTING, str(first), str(second)])
        with controller._db() as db:
            began = db.execute('SELECT created FROM assignments WHERE id=?', (assignment['id'],)).fetchone()[0]
        # Before the worker prints or calls anything, its last activity is its start.
        self.assertEqual(controller.call('status', run)['worker'], {
            'stage': 'acquire', 'started': began, 'last_activity': began, 'last_activity_kind': 'started'})
        first.write_text('print', encoding='utf-8')
        printed = self.worker(controller, run, 'output')
        self.assertEqual((printed['stage'], printed['started']), ('acquire', began))
        self.assertTrue(began < printed['last_activity'] <= time.time(), printed)
        # One of its gateway calls starts and ends.
        before = time.time()
        controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_firmware_artifact',
                                 'arguments': {}})
        called = controller.call('status', run)['worker']
        self.assertEqual((called['last_activity_kind'], called['started']), ('tool', began))
        self.assertTrue(before <= called['last_activity'] <= time.time(), called)
        # The later of the two counts: a line printed after the call.
        second.write_text('print', encoding='utf-8')
        again = self.worker(controller, run, 'output')
        self.assertGreaterEqual(again['last_activity'], called['last_activity'])
        controller.call('cancel', run)
        self.assertIsNone(self.stopped(controller, run)['worker'])


class ClientTests(unittest.TestCase):
    """The command line and the MCP server, each started as a process of its own against one background service."""

    def setUp(self):
        from generative_driver.client import call
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / 'home'
        self.addCleanup(call, 'shutdown', {}, self.home)
        self.run_id = call('start', {'goal': 'A run that stops before its first worker', 'budget_seconds': 600,
                                     'executor_config': {'command': ['no-such-worker-runtime']}}, self.home)['run_id']
        until = time.monotonic() + 30
        while True:
            self.status = call('status', {'run_id': self.run_id}, self.home, autostart=False)
            if self.status['status'] == 'blocked' and not self.status['stopping']:
                break
            self.assertLess(time.monotonic(), until, self.status)
            time.sleep(.02)
        self.added = {'deadline': self.status['created'] + 600, 'repairs': {'used': 0, 'budget': 2}, 'worker': None,
                      'log': str(self.home / 'daemon.log')}

    def test_04_B6_the_status_command_returns_the_added_fields(self):
        printed = subprocess.run([sys.executable, '-m', 'generative_driver', '--home', str(self.home), 'status',
                                  self.run_id], env={**__import__('os').environ, **package_env()}, capture_output=True,
                                 text=True, timeout=60)
        self.assertEqual(printed.returncode, 0, printed)
        status = json.loads(printed.stdout)
        self.assertEqual({key: status[key] for key in self.added}, self.added)

    def test_04_B6_driver_status_returns_the_added_fields_and_its_description_names_them(self):
        asyncio.run(self.driver_status())

    async def driver_status(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'], cwd=self.home,
                                       env=package_env(GENERATIVE_DRIVER_HOME=str(self.home)))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
                reply = await session.call_tool('driver_status', {'run_id': self.run_id})
        status = json.loads(reply.content[0].text)
        self.assertEqual({key: status[key] for key in self.added}, self.added)
        for name in self.added:
            self.assertIn('`' + name + '`', catalog['driver_status'].description)


if __name__ == '__main__':
    unittest.main()

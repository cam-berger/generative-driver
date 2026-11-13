"""The service's log, `daemon.log` in its home: every exception the service catches and turns into a reply, a stop or a
failure event leaves an entry there, and so does every exception a kit tool catches while it runs for one of the
service's runs (docs/features/kit-fixes/04-service-diagnostics.md, 04-B1 to 04-B4).

Scripted workers and temporary homes only; no model and no hardware."""
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from service_fixtures import IDLE_WORKER, STAMP, entries, entry, serve


class ServiceCase(unittest.TestCase):
    """A background service of the test's own, in a temporary home, stopped when the test ends."""

    def service(self):
        from generative_driver.client import call
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        home = self.root / 'home'
        self.addCleanup(call, 'shutdown', {}, home)
        self.assertTrue(call('ping', {}, home)['ok'])
        return home, lambda method, params=None: call(method, params or {}, home, autostart=False)


class ControllerCase(unittest.TestCase):
    """A configurator in this process, in a temporary home, closed when the test ends."""

    def controller(self, **options):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        controller = Controller(self.root / 'home', **options)
        self.addCleanup(controller.close)
        return controller

    def stopped(self, controller, run, seconds=30):
        """The run's status once it has stopped and nothing of it is left stopping."""
        until = time.monotonic() + seconds
        while True:
            status = controller.call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return status
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)

    def events(self, controller, run_id, kind):
        return [event['data'] for event in controller.call('events', {'run_id': run_id})['events']
                if event['kind'] == kind]

    def assigned(self, controller, **start):
        """A running generic run whose acquire worker idles, and that worker's assignment."""
        run = controller.call('start', {'goal': 'A run whose worker idles', 'executor_config': {'command': IDLE_WORKER},
                                        **start})
        until = time.monotonic() + 30
        while True:
            found = self.events(controller, run['run_id'], 'stage.assigned')
            if found and controller.call('status', run)['status'] == 'running':
                return run, found[0]
            self.assertLess(time.monotonic(), until, controller.call('result', run))
            time.sleep(.02)


class ReplyEntryTests(ServiceCase):
    def test_04_B1_an_exception_that_becomes_a_reply_leaves_its_traceback_in_the_log(self):
        home, call = self.service()
        # A request the service cannot handle: it reads a parameter the request does not hold.
        reply = call('device_execute')
        self.assertEqual(reply, {'ok': False, 'error': 'KeyError', 'reason': "'session_id'"})
        written = entry(home, 'device_execute')
        first, rest = written.split('\n', 1)
        self.assertRegex(first, STAMP)
        self.assertIn('method=device_execute', first)
        self.assertIn("KeyError: 'session_id'", first)
        self.assertIn('Traceback (most recent call last):', rest)
        # The whole traceback, down to the line that raised.
        self.assertRegex(rest, r'File ".*device_access\.py", line \d+, in execute')
        self.assertEqual(rest.strip().splitlines()[-1], "KeyError: 'session_id'")

    def test_04_B1_a_refusal_that_leaves_the_run_as_it_was_gets_one_line_without_a_traceback(self):
        home, call = self.service()
        reply = call('status', {'run_id': 'run-absent'})
        self.assertEqual((reply['ok'], reply['error'], reply['reason']), (False, 'ValueError', 'Unknown run_id'))
        written = entry(home, 'method=status')
        self.assertEqual(len(written.strip().splitlines()), 1, written)
        self.assertIn('run=run-absent', written)
        self.assertIn('ValueError: Unknown run_id', written)
        self.assertNotIn('Traceback', (home / 'daemon.log').read_text(encoding='utf-8'))

    def test_04_B2_no_entry_holds_parameters_inputs_arguments_prompts_reports_or_the_service_record(self):
        home, call = self.service()
        firmware, flag = self.root / 'firmware.bin', self.root / 'go'
        firmware.write_bytes(b'SENTINEL-INPUT-BYTES')
        # A worker that waits for the test, then alters its assigned input and reports, so the run stops on an
        # exception; its prompt holds the goal and its report a summary of its own.
        worker = ('import json,sys,time; from pathlib import Path\n'
                  'task=json.loads(sys.stdin.read().partition(chr(10))[2])\n'
                  'while not Path(sys.argv[1]).exists(): time.sleep(.02)\n'
                  'Path(task["inputs"][0]).write_bytes(b"altered")\n'
                  'report={"status":"completed","summary":"SENTINEL-REPORT","artifacts":[],"checks":[],\n'
                  '        "unresolved":[]}\n'
                  'message={"type":"agent_message","text":json.dumps(report)}\n'
                  'print(json.dumps({"type":"item.completed","item":message}))')
        run = call('start', {'goal': 'SENTINEL-GOAL', 'inputs': {'firmware.bin': str(firmware)},
                             'executor_config': {'command': [sys.executable, '-c', worker, str(flag)]}})
        until = time.monotonic() + 30
        while True:
            assigned = [event['data'] for event in call('events', run)['events'] if event['kind'] == 'stage.assigned']
            if assigned and call('status', run)['status'] == 'running':
                break
            self.assertLess(time.monotonic(), until, call('result', run))
            time.sleep(.02)
        # A tool that fails on an argument, a refused request and a request the service cannot handle.
        failed = call('tool', {**run, 'assignment_id': assigned[0]['id'], 'name': 'acquire_firmware_artifact',
                               'arguments': {'note': 'SENTINEL-ARGUMENT'}})
        self.assertTrue(failed['error'].startswith('TypeError: acquire_firmware_artifact()'), failed)
        self.assertEqual(call('respond', {**run, 'message': 'SENTINEL-MESSAGE'})['reason'],
                         'Respond only while the run is stopped')
        self.assertEqual(call('device_execute', {'operation': 'SENTINEL-OPERATION'})['error'], 'KeyError')
        flag.write_text('go', encoding='utf-8')
        while True:
            status = call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                break
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'integrity'), status)
        log = (home / 'daemon.log').read_text(encoding='utf-8')
        # Each entry names its method, its run and its tool, and quotes the exception's own message.
        self.assertIn(failed['error'], entry(home, 'run=' + run['run_id'], 'tool=acquire_firmware_artifact'))
        self.assertIn('ValueError: Respond only while the run is stopped', entry(home, 'method=respond',
                                                                                 'run=' + run['run_id']))
        self.assertIn("KeyError: 'session_id'", entry(home, 'method=device_execute'))
        self.assertIn(status['reason'], entry(home, 'run=' + run['run_id'], 'step=run'))
        record = json.loads((home / 'service.json').read_text(encoding='utf-8'))
        for held in ('SENTINEL-GOAL', 'SENTINEL-INPUT-BYTES', 'SENTINEL-ARGUMENT', 'SENTINEL-MESSAGE',
                     'SENTINEL-OPERATION', 'SENTINEL-REPORT', record['authkey'], record['address'], record['code']):
            self.assertNotIn(held, log)


class RunEntryTests(ControllerCase):
    def test_04_B1_an_exception_that_stops_a_run_leaves_its_traceback_with_the_run_and_stage(self):
        controller = self.controller()
        # The run's only input does not exist, so preparing its first stage raises.
        run = controller.call('start', {'goal': 'A run that stops while its first stage is prepared',
                                        'inputs': {'firmware.bin': str(self.root / 'absent.bin')},
                                        'executor_config': {'command': IDLE_WORKER}})
        status = self.stopped(controller, run)
        self.assertEqual((status['status'], status['stage']), ('failed', 'acquire'), status)
        self.assertTrue(status['reason'].startswith('ValueError: Artifact does not exist'), status)
        written = entry(controller.home, 'run=' + run['run_id'])
        first, rest = written.split('\n', 1)
        self.assertRegex(first, STAMP)
        self.assertIn('stage=acquire', first)
        self.assertIn('step=run', first)
        self.assertIn(status['reason'], first)
        self.assertIn('Traceback (most recent call last):', rest)
        self.assertRegex(rest, r'File ".*configurator\.py", line \d+, in _prepare')
        self.assertEqual(rest.strip().splitlines()[-1], status['reason'])

    def test_04_B1_a_worker_that_cannot_be_launched_leaves_its_entry_with_the_run_and_stage(self):
        controller = self.controller()
        run = controller.call('start', {'goal': 'A run whose worker runtime does not exist',
                                        'executor_config': {'command': ['no-such-worker-runtime']}})
        status = self.stopped(controller, run)
        self.assertEqual((status['status'], status['stage']), ('blocked', 'acquire'), status)
        self.assertTrue(status['reason'].startswith('Cannot start configured runtime: '), status)
        written = entry(controller.home, 'run=' + run['run_id'], 'step=launch')
        first = written.splitlines()[0]
        self.assertIn('stage=acquire', first)
        # The entry quotes the exception the stop's reason was made from.
        self.assertIn(status['reason'].removeprefix('Cannot start configured runtime: '), first)
        if os.name != 'nt':
            # The service itself caught the exception, so its traceback is there. On Windows the launch's wrapper
            # process caught it and reported only its message.
            self.assertIn('FileNotFoundError', first)
            self.assertIn('Traceback (most recent call last):', written)
            self.assertRegex(written, r'File ".*agents\.py", line \d+, in execute')

    def test_04_B1_a_refusal_that_also_records_a_stop_gets_the_full_entry(self):
        from generative_driver.benchmark_support.integrity import IntegrityError
        controller = self.controller()
        # A worker that ends at once in acquire and fails in every later stage, so the run has an accepted handoff
        # when it stops.
        worker = ('import json,sys; stage=json.loads(sys.stdin.read().partition(chr(10))[2])["stage"]; '
                  'sys.exit(0 if stage == "acquire" else 1)')
        run = controller.call('start', {'goal': 'A run whose accepted notes change while it is stopped',
                                        'executor_config': {'command': [sys.executable, '-c', worker]}})
        stopped = self.stopped(controller, run)
        self.assertEqual((stopped['status'], stopped['stage']), ('failed', 'probe'), stopped)
        notes = Path(controller.call('result', run)['accepted_handoffs'][0]['notes']['path'])
        notes.write_text(notes.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        with self.assertRaisesRegex(IntegrityError, 'Stale handoff'):
            controller.call('resume', run)
        status = controller.call('status', run)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'integrity'), status)
        written = entry(controller.home, 'method=resume', 'run=' + run['run_id'])
        self.assertIn('IntegrityError: ' + status['reason'], written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)
        self.assertRegex(written, r'File ".*configurator\.py", line \d+, in _verify_inputs')

    def test_04_B1_a_resume_refused_for_a_lost_device_records_its_stop_with_one_full_entry(self):
        from generative_driver.configurator import Controller, PROTOCOL
        from generative_driver.device_client import DeviceUnavailable
        controller = self.controller()
        # A stopped run on a device its bench harness provided, as its saved rows hold it; nothing of it runs.
        spec = {'goal': 'x', 'case': 'toy', 'executor': 'codex', 'executor_config': {}, 'budget_seconds': 600,
                'effects': ['read'], 'device': {'kind': 'provided-emulator'},
                '_case_pin': {'execution': 'actual-agent-emulation', 'manifest': {'schema': 'benchmark-case/2'}}}
        with controller._db() as db:
            db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                       ('run-lost-device', None, json.dumps(spec), 'blocked', 'probe', time.time(), time.time()))
            db.execute('INSERT INTO progress VALUES(?,?)', ('run-lost-device', json.dumps(
                {'revision': 0, 'next_stage': 'probe', 'repairs': 0, 'feedback': None, 'protocol': PROTOCOL,
                 'device': {'session_id': 's', 'binding': {'host': '127.0.0.1', 'port': 1}}})))
        controller.close()
        # The service runs in a thread of this test, so that the device's answer can be replaced.
        call = serve(self, controller.home)
        with patch.object(Controller, '_device_ready', side_effect=DeviceUnavailable('The device provider is gone')):
            reply = call('resume', {'run_id': 'run-lost-device'})
        self.assertEqual((reply['ok'], reply['error'], reply['reason']),
                         (False, 'ValueError', 'The device provider is gone; attach a verified device'))
        status = call('status', {'run_id': 'run-lost-device'})
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'operator'), status)
        # One entry, the full one: the refusal the reply carries gets no second line.
        written = entry(controller.home, 'run=run-lost-device')
        self.assertIn('method=resume', written.splitlines()[0])
        self.assertIn('DeviceUnavailable: The device provider is gone', written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)

    def test_04_B1_a_run_stopped_at_restart_for_progress_it_cannot_read_leaves_its_traceback(self):
        from generative_driver.configurator import Controller
        controller = self.controller()
        # A run an earlier version left running, whose saved progress this version does not continue.
        spec = {'goal': 'x', 'executor': 'codex', 'executor_config': {}, 'budget_seconds': 600, 'effects': ['read']}
        with controller._db() as db:
            db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                       ('run-earlier', None, json.dumps(spec), 'running', 'probe', time.time(), time.time()))
            db.execute('INSERT INTO progress VALUES(?,?)', ('run-earlier', json.dumps({'maintenance_cycles': 1})))
        controller.close()
        restarted = Controller(controller.home)
        self.addCleanup(restarted.close)
        status = restarted.call('status', {'run_id': 'run-earlier'})
        self.assertEqual((status['status'], status['reason']),
                         ('blocked', 'Unsupported saved workflow progress; start a new six-stage run'), status)
        written = entry(restarted.home, 'run=run-earlier')
        self.assertIn('step=recover', written.splitlines()[0])
        self.assertIn('ValueError: ' + status['reason'], written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)
        self.assertRegex(written, r'in _workflow_progress\n')


class FailureEventEntryTests(ControllerCase):
    """Each failure event an exception becomes has an entry: the event and the entry name the same failure."""

    def test_04_B1_a_failed_cleanup_leaves_its_traceback(self):
        from generative_driver.configurator import Controller
        controller = self.controller()
        with patch.object(Controller, '_release', side_effect=RuntimeError('scripted release failure')):
            run = controller.call('start', {'goal': 'A run whose release fails',
                                            'executor_config': {'command': ['no-such-worker-runtime']}})
            self.stopped(controller, run)
        failed = self.events(controller, run['run_id'], 'cleanup.failed')
        self.assertEqual([event['reason'] for event in failed], ['RuntimeError: scripted release failure'])
        written = entry(controller.home, 'run=' + run['run_id'], 'step=release')
        self.assertIn('RuntimeError: scripted release failure', written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)

    def test_04_B1_a_failed_device_release_leaves_its_traceback(self):
        from generative_driver.device_client import DeviceUnavailable
        controller = self.controller()

        class Provider:
            def release(self, session_id):
                raise DeviceUnavailable('scripted provider refusal')
        with patch('generative_driver.device_client.open_device', return_value=Provider()):
            failures = controller._release('run-released', {'case': 'toy', 'device': {'kind': 'provided-emulator'}},
                                           self.root / 'run')
        self.assertEqual(failures, [('device.release_failed', 'DeviceUnavailable: scripted provider refusal', None)])
        written = entry(controller.home, 'run=run-released', 'step=release')
        self.assertIn('DeviceUnavailable: scripted provider refusal', written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)
        self.assertRegex(written, r'in release\n')

    def test_04_B1_a_device_that_cannot_be_reached_before_a_call_leaves_its_traceback(self):
        from generative_driver.device_client import DeviceUnavailable
        controller = self.controller()
        run, assignment = self.assigned(controller)

        class Device:
            def observe(self):
                raise DeviceUnavailable('scripted provider silence')
        refused = controller._device_call({'id': run['run_id']}, assignment, 'interface_execute', {}, Device(),
                                          lambda: self.fail('the call was made'))
        self.assertEqual(refused['error']['code'], 'device_unavailable')
        lost = self.events(controller, run['run_id'], 'device.unavailable')
        self.assertEqual([(event['phase'], event['reason']) for event in lost],
                         [('before', 'scripted provider silence')])
        written = entry(controller.home, 'run=' + run['run_id'], 'tool=interface_execute')
        self.assertIn('stage=acquire', written.splitlines()[0])
        self.assertIn('DeviceUnavailable: scripted provider silence', written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)

    def test_04_B1_a_failed_emulator_recovery_leaves_its_traceback(self):
        from generative_driver.device_recovery import reconcile
        controller = self.controller()
        run, assignment = self.assigned(controller)
        run_id = run['run_id']
        # A write the runtime attributed to the model after it opened the device, as the configurator records one.
        call = {'stage': 'acquire', 'assignment_id': assignment['id'], 'name': 'interface_execute', 'effectful': True,
                'actor': 'worker'}
        with controller._db() as db:
            started = controller._event(run_id, 'tool.started', call, db)
            db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)', (started, run_id, assignment['id'], 'worker'))
            db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run_id,))
            controller._event(run_id, 'tool.finished', {**call, 'ok': False, 'dispatch_id': str(started),
                'effects_reconciled': False, 'result': {'schema': 'interface-result/1', 'ok': False,
                    'identity_verified': True, 'transport_open_attempted': True, 'transcript': [{'tx': '00'}],
                    'error': {'fault': 'model', 'code': 'unexpected_reply', 'message': 'scripted'}}}, db)

        class Provider:
            def replace(self, *arguments, **options):
                raise RuntimeError('scripted provider failure')
        spec = {'budget_seconds': 600, 'device': {'kind': 'provided-emulator'}}
        with patch('generative_driver.device_client.open_device', return_value=Provider()):
            checked = reconcile(controller, run_id, spec, assignment, self.root / 'run',
                                {'revision': 0, 'device': {'session_id': 's'}})
        self.assertEqual(checked['stop']['reason'], 'Emulator recovery unverified: scripted provider failure')
        failed = self.events(controller, run_id, 'emulator.recovery_failed')
        self.assertEqual([event['reason'] for event in failed], ['RuntimeError: scripted provider failure'])
        written = entry(controller.home, 'run=' + run_id, 'step=emulator-recovery')
        self.assertIn('stage=acquire', written.splitlines()[0])
        self.assertIn('RuntimeError: scripted provider failure', written.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', written)

    def test_04_B1_a_refused_tool_call_gets_one_line_and_a_refusal_that_is_no_value_error_its_traceback(self):
        controller = self.controller()
        with patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': ['emit_test_live']}):
            run, assignment = self.assigned(controller)
            request = {**run, 'assignment_id': assignment['id']}
            with self.assertRaisesRegex(ValueError, 'Tool is not assigned to this worker'):
                controller.call('tool', {**request, 'name': 'probe_run', 'arguments': {}})
            # The package the worker names holds no manifest, which the gateway reads before it dispatches anything.
            package = Path(assignment['workspace']) / 'package'
            package.mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                controller.call('tool', {**request, 'name': 'emit_test_live',
                                         'arguments': {'package_dir': str(package)}})
        refused = self.events(controller, run['run_id'], 'tool.refused')
        self.assertEqual([event['name'] for event in refused], ['probe_run', 'emit_test_live'])
        line = entry(controller.home, 'run=' + run['run_id'], 'tool=probe_run')
        self.assertEqual(len(line.strip().splitlines()), 1, line)
        self.assertIn('method=tool', line)
        self.assertIn('stage=acquire', line)
        self.assertIn('ValueError: Tool is not assigned to this worker', line)
        full = entry(controller.home, 'run=' + run['run_id'], 'tool=emit_test_live')
        self.assertIn('FileNotFoundError', full.splitlines()[0])
        self.assertIn('Traceback (most recent call last):', full)
        self.assertRegex(full, r'in read_json_hashed\n')


class ToolEntryTests(ControllerCase):
    def test_04_B1_an_exception_a_kit_tool_catches_for_a_run_leaves_its_traceback_with_the_run_stage_and_tool(self):
        controller = self.controller()
        with patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': ['acquire_firmware_artifact',
                                                                                   'emit_package']}):
            run, assignment = self.assigned(controller)
            request = {**run, 'assignment_id': assignment['id']}
            work = Path(assignment['workspace'])
            # The toolchain's own wrapper catches this one: the tool is called without what it requires.
            wrapped = controller.call('tool', {**request, 'name': 'acquire_firmware_artifact', 'arguments': {}})
            # The toolkit catches this one before any tool runs: the two names for one record disagree.
            refused = controller.call('tool', {**request, 'name': 'emit_package', 'arguments': {
                'model_dir': str(work / 'model'), 'probe': str(work / 'a.json'), 'probe_json': str(work / 'b.json')}})
        self.assertTrue(wrapped['error'].startswith('TypeError: acquire_firmware_artifact() missing'), wrapped)
        self.assertEqual(refused['error'], 'probe and probe_json must name the same evidence')
        for tool, said, raised_in in (('acquire_firmware_artifact', wrapped['error'], 'wrapper'),
                                      ('emit_package', 'ValueError: ' + refused['error'], 'call_tool')):
            with self.subTest(tool=tool):
                written = entry(controller.home, 'run=' + run['run_id'], 'tool=' + tool)
                first = written.splitlines()[0]
                self.assertRegex(first, STAMP)
                self.assertIn('stage=acquire', first)
                self.assertIn(said, first)
                self.assertIn('Traceback (most recent call last):', written)
                self.assertRegex(written, r'in ' + raised_in + r'\n')

    def test_04_B1_the_toolkit_and_toolchain_write_nothing_when_they_run_outside_the_service(self):
        from generative_driver.toolkit import call_tool
        controller = self.controller()
        # This thread first runs a tool for a run of the service, which is logged, and then the same tools directly.
        run, assignment = self.assigned(controller)
        controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_firmware_artifact',
                                 'arguments': {}})
        before = entries(controller.home)
        self.assertEqual(len(before), 1, before)
        outside = str(self.root / 'tools')
        wrapped = call_tool('acquire_firmware_artifact', {'run_dir': outside})
        refused = call_tool('emit_package', {'run_dir': outside, 'model_dir': str(self.root / 'model'),
                                             'probe': str(self.root / 'a.json'),
                                             'probe_json': str(self.root / 'b.json')})
        self.assertTrue(wrapped['error'].startswith('TypeError'), wrapped)
        self.assertEqual(refused['error'], 'probe and probe_json must name the same evidence')
        self.assertEqual(entries(controller.home), before)
        self.assertEqual(sorted(path.name for path in self.root.rglob('*.log')), ['daemon.log'])


class RotationTests(unittest.TestCase):
    TEN_MB = 10 * 1024 * 1024

    def home(self, size):
        """A home whose log an earlier service left with `size` bytes, and the service started there."""
        from generative_driver.client import call
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve() / 'home'
        home.mkdir()
        with (home / 'daemon.log').open('wb') as log:
            log.write(b'an earlier service wrote this\n')
            log.truncate(size)
        return home, call

    def test_04_B4_a_log_over_10_MB_is_set_aside_when_a_service_starts(self):
        home, call = self.home(self.TEN_MB + 1)
        (home / 'daemon.log.1').write_bytes(b'a log set aside at an earlier start\n')
        self.addCleanup(call, 'shutdown', {}, home)
        self.assertTrue(call('ping', {}, home)['ok'])
        aside = home / 'daemon.log.1'
        self.assertEqual(aside.stat().st_size, self.TEN_MB + 1)
        with aside.open('rb') as log:
            self.assertEqual(log.readline(), b'an earlier service wrote this\n')
        # The new service writes a new log.
        call('status', {'run_id': 'run-absent'}, home, autostart=False)
        written = entry(home, 'method=status')
        self.assertIn('run=run-absent', written)
        self.assertEqual((home / 'daemon.log').read_text(encoding='utf-8'), written)

    def test_04_B4_a_log_of_10_MB_is_kept_and_written_on(self):
        home, call = self.home(self.TEN_MB)
        self.addCleanup(call, 'shutdown', {}, home)
        self.assertTrue(call('ping', {}, home)['ok'])
        call('status', {'run_id': 'run-absent'}, home, autostart=False)
        self.assertFalse((home / 'daemon.log.1').exists())
        with (home / 'daemon.log').open('rb') as log:
            self.assertEqual(log.readline(), b'an earlier service wrote this\n')
            log.seek(self.TEN_MB)
            self.assertIn(b'run=run-absent', log.read())


if __name__ == '__main__':
    unittest.main()

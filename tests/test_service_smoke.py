"""`generative-driver smoke`: one whole run through the background service, a scripted worker and a stand-in device,
with no model, no Codex and no hardware (docs/features/kit-fixes/03-service-smoke.md).

Each test runs the command as a child process in folders of its own: its system temporary directory, its home
directory and its operator homes are new folders the test removes."""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from package_env import package_env

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
SEAM = '_GENERATIVE_DRIVER_SMOKE_TEST'


def _processes_naming(text):
    """(pid, command line) of each running process whose command line holds `text`."""
    if os.name == 'nt':
        listing = subprocess.run(['powershell', '-NoProfile', '-Command',
                                  'Get-CimInstance Win32_Process | '
                                  'ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }'],
                                 capture_output=True, text=True, timeout=60).stdout
    else:
        listing = subprocess.run(['ps', '-axww', '-o', 'pid=,command='], capture_output=True, text=True,
                                 timeout=60).stdout
    found = []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(' ')
        if text in command and pid.isdigit() and int(pid) != os.getpid():
            found.append((int(pid), command))
    return found


def _kill(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=30)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _alive(pid):
    from generative_driver.client import _wait_for_exit
    return not _wait_for_exit(pid, 0)


class SmokeCase(unittest.TestCase):
    """Runs the smoke as a child process with `package_env()`, its temporary directory, home and default
    configurator home redirected into a folder of this test."""

    def setUp(self):
        # A short root, left unresolved: the service's socket lives under the child's temporary directory, and macOS
        # refuses a socket path over 104 bytes.
        self.root = Path(tempfile.mkdtemp(prefix='gds'))
        self.temp = self.root / 't'
        self.user = self.root / 'u'
        for folder in (self.temp, self.user):
            folder.mkdir()
        self.addCleanup(self._clean)

    def _clean(self):
        # Whatever a failed smoke left behind is stopped before its folder goes.
        for pid, _ in _processes_naming(str(self.root)):
            _kill(pid)
        shutil.rmtree(self.root, ignore_errors=True)

    def environment(self, seam=None, **values):
        env = {key: value for key, value in os.environ.items()
               if key not in ('GENERATIVE_DRIVER_HOME', 'CODEX_HOME', SEAM)}
        env.update(package_env(), TMPDIR=str(self.temp), TEMP=str(self.temp), TMP=str(self.temp),
                   HOME=str(self.user), USERPROFILE=str(self.user), LOCALAPPDATA=str(self.user / 'local'),
                   XDG_STATE_HOME=str(self.user / 'state'))
        if seam is not None:
            env[SEAM] = json.dumps(seam)
        env.update(values)
        return env

    def smoke(self, *arguments, env=None, timeout=600):
        """Exit status and report of one smoke."""
        completed = subprocess.run([sys.executable, '-m', 'generative_driver', *arguments, 'smoke'],
                                   cwd=self.root, env=env or self.environment(), capture_output=True, text=True,
                                   timeout=timeout)
        try:
            report = json.loads(completed.stdout)
        except ValueError:
            self.fail('The smoke printed no JSON report (exit ' + str(completed.returncode) + '):\n'
                      + completed.stdout[-3000:] + '\n' + completed.stderr[-3000:])
        return completed.returncode, report

    def homes(self):
        """The smoke folders left in the child's temporary directory."""
        return sorted(self.temp.glob('generative-driver-smoke-*'))

    @staticmethod
    def _rows(home, query):
        uri = (Path(home) / 'runs.sqlite3').as_uri() + '?mode=ro'
        db = sqlite3.connect(uri, uri=True)
        try:
            return db.execute(query).fetchall()
        finally:
            db.close()

    @classmethod
    def events(cls, home):
        """The run events the kept home holds, oldest first, read without starting a service."""
        return [{'kind': kind, 'data': json.loads(payload)}
                for kind, payload in cls._rows(home, 'SELECT kind, payload FROM events ORDER BY seq')]

    @classmethod
    def records(cls, home, table):
        """The payloads of a table the kept home holds (reports, handoffs), oldest first."""
        rows = cls._rows(home, 'SELECT payload FROM ' + table + ' ORDER BY created')
        return [json.loads(payload) for payload, in rows]


class SmokeTests(SmokeCase):
    def test_03_B1_a_whole_run_passes_every_stage_and_reports_each_attempt(self):
        code, report = self.smoke()
        self.assertEqual(code, 0, report)
        self.assertIs(report['ok'], True, report)
        self.assertRegex(report['run_id'], r'^run-[0-9a-f]{16}$')
        self.assertEqual([attempt['stage'] for attempt in report['stages']], list(STAGES), report)
        for attempt in report['stages']:
            self.assertEqual(set(attempt), {'stage', 'accepted', 'skipped', 'worker', 'failed_checks', 'seconds'})
            self.assertEqual((attempt['accepted'], attempt['skipped'], attempt['worker'], attempt['failed_checks']),
                             (True, None, 'completed', []), attempt)
            self.assertGreaterEqual(attempt['seconds'], 0)
        self.assertEqual(report['verdict'], {'equivalent': True, 'basis': 'self-evidence', 'failed_checks': []})
        self.assertIs(report['service']['stopped'], True, report)
        self.assertGreater(report['seconds'], 0)
        for words in ('scripted worker', 'stand-in device', 'service path works on this machine', 'model',
                      'firmware image', 'real device'):
            self.assertIn(words, report['evidence'])

    def test_03_B1_a_failing_report_does_not_claim_that_the_service_path_works(self):
        code, report = self.smoke(env=self.environment({'replies': {'ID?': 'OTHER-1\n'}}))
        self.assertEqual((code, report['ok']), (1, False), report)
        self.assertNotIn('shows that', report['evidence'])
        for words in ('scripted worker', 'stand-in device', "whether the kit's service path works on this machine",
                      'model', 'firmware image', 'real device'):
            self.assertIn(words, report['evidence'])

    def test_03_B2_goes_through_a_service_of_its_own_the_codex_launch_and_each_stage_gateway(self):
        code, report = self.smoke(env=self.environment({'keep': True}))
        self.assertEqual(code, 0, report)
        # Its own service, started as `service start` starts one, in a new folder under the system temporary directory.
        [home] = self.homes()
        pid = report['service']['pid']
        self.assertEqual(json.loads((home / 'service.json').read_text(encoding='utf-8'))['pid'], pid)
        self.assertTrue((home / 'daemon.log').is_file())
        self.assertNotEqual(pid, os.getpid())
        self.assertIs(report['service']['stopped'], True)
        self.assertFalse(_alive(pid))
        events = self.events(home)
        assignments = [event['data'] for event in events if event['kind'] == 'stage.assigned']
        self.assertEqual([assignment['stage'] for assignment in assignments], list(STAGES))
        for assignment in assignments:
            with self.subTest(stage=assignment['stage']):
                record = Path(assignment['workspace']) / '_scripted_worker.json'
                self.assertTrue(record.is_file(), 'The scripted worker recorded no launch in ' + str(record))
                launch = json.loads(record.read_text(encoding='utf-8'))
                # The arguments the configurator passes Codex, its workspace among them.
                arguments = launch['arguments']
                self.assertEqual(arguments[:3], ['exec', '--json', '--ephemeral'])
                self.assertEqual(arguments[arguments.index('-C') + 1], assignment['workspace'])
                self.assertEqual(arguments[-1], '-')
                named = dict(value.split('=', 1) for flag, value in zip(arguments, arguments[1:])
                             if flag == '-c' and value.startswith('mcp_servers.stage.'))
                called = [event['data']['name'] for event in events if event['kind'] == 'tool.started'
                          and event['data']['assignment_id'] == assignment['id']]
                if not assignment['allowed_tools']:
                    # Interpretation runs sealed, with no gateway and no tools.
                    self.assertEqual((named, launch['gateway'], launch['called_tools'], called), ({}, None, [], []))
                    continue
                # It started the gateway the launch names, listed its tools over MCP and called only enabled ones,
                # each of which the configurator dispatched.
                self.assertEqual(launch['gateway'], {
                    'command': json.loads(named['mcp_servers.stage.command']),
                    'args': json.loads(named['mcp_servers.stage.args'])})
                enabled = json.loads(named['mcp_servers.stage.enabled_tools'])
                self.assertEqual(enabled, assignment['allowed_tools'])
                self.assertEqual(sorted(launch['listed_tools']), sorted(enabled))
                self.assertTrue(called)
                self.assertEqual(launch['called_tools'], called)
                self.assertLessEqual(set(called), set(enabled))
                self.assertIn(assignment['id'], launch['gateway']['args'])
        reuse = assignments[-1]
        self.assertIn(reuse['id'], [event['data']['assignment_id'] for event in events
                                    if event['kind'] == 'mcp.catalog_served'])

    def test_03_B3_each_stage_does_its_work_with_its_own_tools(self):
        from generative_driver.toolkit import resources_root
        code, report = self.smoke(env=self.environment({'keep': True}))
        self.assertEqual(code, 0, report)
        [home] = self.homes()
        events = self.events(home)
        assigned = {event['data']['stage']: event['data'] for event in events if event['kind'] == 'stage.assigned'}
        finished = {stage: [event['data'] for event in events if event['kind'] == 'tool.finished'
                            and event['data']['assignment_id'] == assigned[stage]['id']] for stage in STAGES}
        # acquire imports the small firmware stand-in the smoke wrote.
        [imported] = finished['acquire']
        self.assertEqual((imported['name'], imported['ok']), ('acquire_firmware_artifact', True))
        self.assertEqual([imported['result']['sha256']], list(assigned['acquire']['inputs'].values()))
        self.assertLess(imported['result']['size'], 1024)
        # interpret writes the stand-in model as model.json.
        stand_in = json.loads((resources_root() / 'smoke/model.json').read_text(encoding='utf-8'))
        [interpreted] = [h for h in self.records(home, 'handoffs') if h['stage'] == 'interpret']
        written = Path(interpreted['artifacts'][0]['path']) / 'model.json'
        self.assertEqual(json.loads(written.read_text(encoding='utf-8')), stand_in)
        self.assertEqual(finished['interpret'], [])
        # probe and ground each run the model's operations on the stand-in device.
        for stage in ('probe', 'ground'):
            self.assertEqual([(e['name'], e['ok'], e['connected'], e['result']['evidence_kind'])
                              for e in finished[stage]],
                             [('probe_run', True, True, 'live')] * len(stand_in['operations']), stage)
        # emit builds the package.
        self.assertEqual([(e['name'], e['ok']) for e in finished['emit']], [('emit_package', True)])
        # reuse, given only the package and the objective, calls an operation beyond identity through the package's
        # generated catalog, served by the gateway its launch names.
        [package] = assigned['reuse']['inputs']
        self.assertTrue((Path(package) / 'mcp-tools.json').is_file())
        [called] = finished['reuse']
        self.assertEqual((called['ok'], called['connected'], called['result']['mcp']['schema']),
                         (True, True, 'driver-mcp-call/1'))
        self.assertNotEqual(called['result']['operation'], stand_in['identity']['operation'])
        record = Path(assigned['reuse']['workspace']) / '_scripted_worker.json'
        launch = json.loads(record.read_text(encoding='utf-8'))
        self.assertIn('generative_driver.device_mcp', ' '.join(launch['gateway']['args']))
        # A report at every stage, interpret included.
        self.assertEqual([(r['stage'], r['report']['status']) for r in self.records(home, 'reports')],
                         [(stage, 'completed') for stage in STAGES])

    def test_03_B3_a_failed_step_writes_its_error_and_stops_the_run_in_that_stage(self):
        """The stand-in device gives another identity than the stand-in model expects, so probing fails."""
        code, report = self.smoke(env=self.environment({'replies': {'ID?': 'OTHER-1\n'}}))
        self.assertEqual(code, 1, report)
        [home] = self.homes()
        events = self.events(home)
        stopped = [event['data'] for event in events if event['kind'] in ('run.failed', 'run.blocked', 'run.completed')]
        self.assertEqual([(stop['stage'], stop['outcome_category']) for stop in stopped], [('probe', 'host')], stopped)
        self.assertEqual([event['data']['stage'] for event in events if event['kind'] == 'stage.assigned'],
                         ['acquire', 'interpret', 'probe'])
        worker = self.records(home, 'reports')[-1]
        self.assertEqual((worker['stage'], worker['cause']), ('probe', 'exit_error'))
        self.assertNotEqual(worker['exit_code'], 0)
        stderr = Path(worker['stderr']).read_text(encoding='utf-8')
        [failed] = [event['data'] for event in events if event['kind'] == 'tool.finished' and not event['data']['ok']]
        self.assertEqual(failed['name'], 'probe_run')
        self.assertIn('probe_run', stderr)
        self.assertIn(failed['result']['error_message'], stderr)

    def assert_kept(self, report, stage):
        """The failure report keeps its home and names its logs: the service's, and the failed stage worker's."""
        [home] = self.homes()
        self.assertEqual(Path(report['home']).resolve(), home.resolve())
        self.assertEqual(Path(report['logs']['service']).resolve(), (home / 'daemon.log').resolve())
        self.assertTrue(Path(report['logs']['service']).is_file())
        if stage is None:
            self.assertEqual(set(report['logs']), {'service'})
            return home
        attempt = [a for a in self.events(home) if a['kind'] == 'stage.assigned' and a['data']['stage'] == stage][-1]
        workspace = Path(attempt['data']['workspace'])
        self.assertEqual(Path(report['logs']['worker_stderr']), workspace / '_agent_stderr.txt')
        self.assertEqual(Path(report['logs']['worker_events']), workspace / '_agent_events.jsonl')
        self.assertTrue(all(Path(path).is_file() for path in report['logs'].values()), report['logs'])
        return home

    def test_03_B5_a_run_that_stops_names_its_stage_its_reason_and_its_logs(self):
        code, report = self.smoke(env=self.environment({'replies': {'ID?': 'OTHER-1\n'}}))
        self.assertEqual((code, report['ok']), (1, False), report)
        self.assertEqual(report['failed_stage'], 'probe')
        self.assertIn('Configured runtime exited with an error', report['reason'])
        self.assertIn('host', report['reason'])
        self.assert_kept(report, 'probe')
        self.assertIn('probe_run', Path(report['logs']['worker_stderr']).read_text(encoding='utf-8'))

    def test_03_B5_a_run_that_cannot_start_names_no_stage(self):
        """A budget the configurator refuses: the run is never started."""
        code, report = self.smoke(env=self.environment({'budget_seconds': 0}))
        self.assertEqual((code, report['ok'], report['run_id']), (1, False, None), report)
        self.assertIsNone(report['failed_stage'])
        self.assertIn('budget_seconds must be positive', report['reason'])
        self.assert_kept(report, None)

    def test_03_B5_a_completed_run_names_the_first_attempt_that_failed_a_condition(self):
        """The scripted worker does no work in ground but reports it completed, so only ground's device-test check
        fails; the run still completes, with the probe records handed on."""
        code, report = self.smoke(env=self.environment({'worker': ['--idle', 'ground']}))
        self.assertEqual((code, report['ok']), (1, False), report)
        self.assertEqual([attempt['stage'] for attempt in report['stages']], list(STAGES))
        self.assertEqual(report['failed_stage'], 'ground')
        self.assertIn('observed_device_test', report['reason'])
        self.assert_kept(report, 'ground')

    def test_03_B5_a_service_that_stops_answering_mid_run_is_named_at_once_with_its_stage(self):
        """A test of the smoke kills its service while probe's worker waits with its gateway open."""
        code, report = self.smoke(env=self.environment({'budget_seconds': 30, 'grace_seconds': 2,
                                                        'worker': ['--hang', 'probe'], 'kill_in': 'probe'}),
                                  timeout=180)
        self.assertEqual((code, report['ok'], report['failed_stage']), (1, False, 'probe'), report)
        self.assertIn('stopped answering', report['reason'])
        self.assertIn('may still run', report['reason'])
        # At once, not after the run's budget and grace.
        self.assertLess(report['seconds'], 15, report)
        # The attempts read before it stopped answering stay in the report.
        self.assertEqual([attempt['stage'] for attempt in report['stages']], ['acquire', 'interpret', 'probe'])
        self.assertEqual([(attempt['accepted'], attempt['worker']) for attempt in report['stages'][:2]],
                         [(True, 'completed')] * 2)
        # It ended, but not as the smoke asked, and what it launched may still run.
        self.assertIs(report['service']['stopped'], False)
        self.assertFalse(_alive(report['service']['pid']))
        self.assert_kept(report, 'probe')

    def test_03_B6_gives_its_run_300_seconds_and_the_default_repair_budget(self):
        code, report = self.smoke(env=self.environment({'keep': True}))
        self.assertEqual(code, 0, report)
        [home] = self.homes()
        [spec] = [json.loads(spec) for spec, in self._rows(home, 'SELECT spec FROM runs')]
        self.assertEqual(spec['budget_seconds'], 300)
        self.assertNotIn('max_revisions', spec)

    def test_03_B6_a_run_whose_budget_is_spent_is_stopped_by_the_configurator(self):
        code, report = self.smoke(env=self.environment({'budget_seconds': 4, 'worker': ['--hang', 'probe']}),
                                  timeout=120)
        self.assertEqual((code, report['ok'], report['failed_stage']), (1, False, 'probe'), report)
        self.assertIn('Stage exhausted the remaining run time budget', report['reason'])
        self.assertIn('operator', report['reason'])
        self.assert_kept(report, 'probe')

    def test_03_B6_a_run_not_ended_after_its_budget_and_grace_is_cancelled(self):
        """The service gets a longer budget than the smoke waits for, so the run is still running when the smoke's
        wait ends."""
        code, report = self.smoke(env=self.environment({'budget_seconds': 3, 'grace_seconds': 2,
                                                        'run_budget_seconds': 300, 'worker': ['--hang', 'probe']}),
                                  timeout=120)
        self.assertEqual((code, report['ok'], report['failed_stage']), (1, False, 'probe'), report)
        self.assertIn('did not end in time', report['reason'])
        home = self.assert_kept(report, 'probe')
        self.assertEqual([event['kind'] for event in self.events(home) if event['kind'] in
                          ('run.cancelled', 'run.failed', 'run.blocked', 'run.completed')], ['run.cancelled'])
        self.assertIs(report['service']['stopped'], True)

    def assert_nothing_running(self, report):
        """Neither the service nor any worker or gateway of this smoke is left running."""
        self.assertFalse(_alive(report['service']['pid']))
        deadline = time.monotonic() + 5  # a gateway ends when its worker's pipe closes
        while _processes_naming(str(self.root)) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(_processes_naming(str(self.root)), [])

    def test_03_B7_a_passing_smoke_removes_its_folder_and_leaves_nothing_running(self):
        code, report = self.smoke()
        self.assertEqual(code, 0, report)
        self.assertEqual(self.homes(), [])
        self.assert_nothing_running(report)

    def test_03_B7_a_failing_smoke_keeps_its_folder_and_leaves_nothing_running(self):
        code, report = self.smoke(env=self.environment({'budget_seconds': 4, 'worker': ['--hang', 'ground']}))
        self.assertEqual((code, report['ok'], report['failed_stage']), (1, False, 'ground'), report)
        self.assertIs(report['service']['stopped'], True)
        self.assertEqual([home.resolve() for home in self.homes()], [Path(report['home']).resolve()])
        self.assert_nothing_running(report)

    def test_03_B7_an_error_in_the_smoke_itself_stops_the_service_it_started(self):
        """The smoke raises an error of its own while a stage's worker waits with its gateway open."""
        code, report = self.smoke(env=self.environment({'error_in': 'probe', 'worker': ['--hang', 'probe']}))
        self.assertEqual((code, report['ok'], report['failed_stage']), (1, False, 'probe'), report)
        self.assertIn('The smoke itself failed', report['reason'])
        self.assertIs(report['service']['stopped'], True)
        self.assert_kept(report, 'probe')
        self.assert_nothing_running(report)

    @unittest.skipIf(os.name == 'nt', 'A test cannot send Ctrl-C to one child process alone on Windows')
    def test_03_B7_ctrl_c_stops_the_service_and_reports_the_interruption(self):
        process = subprocess.Popen([sys.executable, '-m', 'generative_driver', 'smoke'], cwd=self.root,
                                   env=self.environment({'worker': ['--hang', 'probe']}), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 60
        while not list(self.temp.glob('generative-driver-smoke-*/runs/*/work/probe/*')) and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(2)  # the probe worker starts and opens its gateway
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=120)
        self.assertNotEqual(process.returncode, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual((report['ok'], report['failed_stage']), (False, 'probe'), report)
        self.assertIn('interrupted', report['reason'])
        self.assertIs(report['service']['stopped'], True)
        self.assert_kept(report, 'probe')
        self.assert_nothing_running(report)

    def test_03_B7_a_service_that_does_not_stop_is_terminated(self):
        """The smoke asks with a ping, which never stops a service, in place of a service that does not stop."""
        code, report = self.smoke(env=self.environment({'ignore_shutdown': True, 'stop_seconds': 2}))
        self.assertEqual((code, report['ok'], report['failed_stage']), (1, False, None), report)
        self.assertIs(report['service']['stopped'], False)
        self.assertIn('did not stop', report['reason'])
        self.assertFalse(_alive(report['service']['pid']))
        self.assert_kept(report, None)

    def test_03_B4_leaves_the_operator_home_codex_and_devices_alone(self):
        """An operator home named by --home, by GENERATIVE_DRIVER_HOME and by default each configure a Codex that
        would leave a mark, and a program named codex on PATH would too: the smoke reads none of them."""
        marks = self.root / 'marks'
        marks.mkdir()
        mark = marks / 'launched'
        recorder = self.root / 'record_launch.py'
        recorder.write_text('import sys\nopen(' + repr(str(mark)) + ', "a").write(" ".join(sys.argv) + "\\n")\n',
                            encoding='utf-8')
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        if os.name == 'nt':
            (bin_dir / 'codex.cmd').write_text('@"' + sys.executable + '" "' + str(recorder) + '" %*\r\n')
        else:
            codex = bin_dir / 'codex'
            codex.write_text('#!/bin/sh\nexec "' + sys.executable + '" "' + str(recorder) + '" "$@"\n')
            codex.chmod(0o755)
        named, inherited = self.root / 'named home', self.root / 'inherited home'
        env = self.environment({'keep': True}, GENERATIVE_DRIVER_HOME=str(inherited),
                               PATH=str(bin_dir) + os.pathsep + os.environ.get('PATH', ''))
        from unittest.mock import patch
        from generative_driver.client import default_home
        with patch.dict(os.environ, {k: v for k, v in env.items() if k != 'GENERATIVE_DRIVER_HOME'}, clear=True), \
                patch.object(Path, 'home', lambda: self.user):
            default = default_home()
        self.assertTrue(default.is_relative_to(self.user), default)
        homes = (named, inherited, default)
        for home in homes:
            home.mkdir(parents=True)
            (home / 'config.json').write_text(json.dumps({'executors': {'codex': {
                'command': [sys.executable, str(recorder), str(home)]}}}), encoding='utf-8')
        before = {home: sorted(path.relative_to(home).as_posix() for path in home.rglob('*')) for home in homes}

        code, report = self.smoke('--home', str(named), env=env)

        self.assertEqual(code, 0, report)
        self.assertIs(report['ok'], True, report)
        self.assertFalse(mark.exists(), mark.read_text() if mark.exists() else '')
        for home in homes:
            self.assertEqual(sorted(path.relative_to(home).as_posix() for path in home.rglob('*')), before[home], home)
            self.assertEqual(json.loads((home / 'config.json').read_text(encoding='utf-8'))['executors']['codex']
                             ['command'][-1], str(home))
        # The stand-in device: TCP on this machine only, at the port the system gave it for this smoke.
        [home] = self.homes()
        bindings = [event['data']['binding'] for event in self.events(home) if event['kind'] == 'stage.assigned']
        self.assertEqual(len(bindings), len(STAGES))
        self.assertEqual({tuple(sorted(binding)) for binding in bindings}, {('host', 'port')})
        self.assertEqual({binding['host'] for binding in bindings}, {'127.0.0.1'})
        self.assertEqual(len({binding['port'] for binding in bindings}), 1)
        self.assertTrue(all(0 < binding['port'] < 65536 for binding in bindings))

    def test_03_B8_the_scripted_worker_finds_the_package_although_its_launch_passes_no_pythonpath(self):
        """The package under test, copied to a folder found only through the smoke's PYTHONPATH, which a worker launch
        drops: as a source checkout or an installed wheel no editable install points at."""
        from package_env import PACKAGE_ROOT
        copy = self.root / 'p'
        for package in ('generative_driver', 'interface_runtime'):
            shutil.copytree(Path(PACKAGE_ROOT) / package, copy / package,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        code, report = self.smoke(env=self.environment({'keep': True}, PYTHONPATH=str(copy)))
        self.assertEqual((code, report['ok']), (0, True), report)
        [home] = self.homes()
        assigned = [event['data'] for event in self.events(home) if event['kind'] == 'stage.assigned']
        self.assertEqual([assignment['stage'] for assignment in assigned], list(STAGES))
        for assignment in assigned:
            with self.subTest(stage=assignment['stage']):
                record = Path(assignment['workspace']) / '_scripted_worker.json'
                launch = json.loads(record.read_text(encoding='utf-8'))
                self.assertEqual(launch['pythonpath'], None)
                self.assertEqual(Path(launch['package']).resolve(),
                                 (copy / 'generative_driver' / '__init__.py').resolve())


class SmokeChecksTests(unittest.TestCase):
    """Where the smoke runs besides a test of its own: the software checks and the CI job for the installed wheel."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_03_B8_the_software_checks_run_the_smoke(self):
        document = (self.ROOT / 'docs' / 'verification.md').read_text(encoding='utf-8')
        section = document.split('## Reproduce software checks', 1)[1].split('\n## ', 1)[0]
        commands = [line.strip() for block in section.split('```')[1::2] for line in block.splitlines()]
        self.assertIn('python -m generative_driver smoke', commands)

    def test_03_B8_the_installed_wheel_job_runs_the_smoke(self):
        workflow = self.ROOT / '.github' / 'workflows' / 'test.yml'
        if not workflow.is_file():
            self.skipTest('The source archive carries no CI workflow')
        text = workflow.read_text(encoding='utf-8')
        step = text.split('- name: Test outside the checkout', 1)[1].split('\n      - name:', 1)[0]
        self.assertIn("[sys.executable, '-m', 'generative_driver', 'smoke']", step)
        self.assertIn('check=True', [line for line in step.splitlines() if "'smoke']" in line][0])


if __name__ == '__main__':
    unittest.main()

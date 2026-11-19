"""Durable suite clients exercise real processes of the benchmark's command line.

A suite is started, read, cancelled and resumed with `firmware-bench suite …` alone: the benchmark has no MCP tool
(Feature 09 of the untangle Run, docs/features/untangle/09-commands.md). The tests that reached a suite through such
a tool reach it through the command.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tool_fixtures import package_env
from suite_fixtures import (shutdown_owner, harness_devices, options, scripted_manifest, wait_suite,
                            write_scripted_case_worker)


def invoke_cli(root, *args, cwd=None):
    """The finished process of `firmware-bench --home <root> suite <args>`. It inherits this test's bench harness
    fixture, which the harness it launches loads to provide the case's devices."""
    return subprocess.run([sys.executable, '-m', 'firmware_bench.benchmark', '--home', str(root),
        'suite', *args], cwd=cwd, env={**os.environ, **package_env()}, capture_output=True, text=True, timeout=30)


class SuiteCliTests(unittest.TestCase):
    def test_pending_registered_fixture_is_refused_before_owner_creation(self):
        from suite_fixtures import client_call as call
        from suite_fixtures import public_case_fixture
        with tempfile.TemporaryDirectory(prefix='suite CLI pending calibration ') as temporary:
            root = Path(temporary)
            resources, home = root/'resources', root/'absent owner'
            public_case_fixture(resources, calibration={'status': 'pending'})
            manifest = scripted_manifest()
            path = root/'manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            code = """import sys
from pathlib import Path
import firmware_bench.benchmark as benchmark
import firmware_bench.benchmark_support.case_assets as case_assets
resources = Path(sys.argv.pop(1))
benchmark.case_root = lambda: resources
case_assets.case_root = lambda: resources
raise SystemExit(benchmark.main())
"""
            try:
                done = subprocess.run([sys.executable, '-c', code, str(resources), '--home', str(home),
                    'suite', 'start', '--manifest', str(path)],
                    env={**os.environ, **package_env()}, capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
                self.assertIn('reference calibration', json.loads(done.stdout)['error'])
                self.assertFalse((home/'service.json').exists())
                self.assertFalse((home/'runs.sqlite3').exists())
            finally:
                if (home/'service.json').exists():
                    shutdown_owner(home)

    def test_saved_comparison_is_offline_and_incompatibility_sets_exit_status(self):
        from test_suite_reporting import comparison_fixture
        with tempfile.TemporaryDirectory(prefix='suite offline compare ') as temporary:
            root = Path(temporary)
            saved, changed = root/'saved.json', root/'changed.json'
            report = comparison_fixture()
            saved.write_text(json.dumps(report), encoding='utf-8')
            home = root/'absent owner'
            done = invoke_cli(home, 'compare', str(saved), str(saved))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertTrue(json.loads(done.stdout)['compatible'])
            report['experiment']['comparison_identity']['budgets']['effective']['suite_budget_seconds'] = 61
            changed.write_text(json.dumps(report), encoding='utf-8')
            refused = invoke_cli(home, 'compare', str(saved), str(changed))
            self.assertEqual(refused.returncode, 1, refused.stderr + refused.stdout)
            self.assertFalse(json.loads(refused.stdout)['compatible'])
            self.assertFalse(home.exists())

    def test_report_requires_output_and_exports_from_the_existing_owner(self):
        from suite_fixtures import client_call as call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI export ') as temporary:
            # The bench home lies beside the configurator home, inside the test directory.
            root = Path(temporary)/'owner'
            absent = Path(temporary)/'absent owner'
            missing = invoke_cli(absent, 'report', 'unknown-suite')
            self.assertEqual(missing.returncode, 2, missing.stderr + missing.stdout)
            self.assertIn('--output', missing.stderr)
            self.assertFalse(absent.exists())
            configure('codex', ['missing-scripted-runtime'], home=root)
            manifest, secrets = root/'manifest.json', root/'options.json'
            manifest.write_text(json.dumps(scripted_manifest()), encoding='utf-8')
            secrets.write_text(json.dumps(options(harness_devices(self, Path(temporary)/'fixture'))), encoding='utf-8')
            try:
                started = invoke_cli(root, 'start', '--manifest', str(manifest), '--options', str(secrets))
                self.assertEqual(started.returncode, 0, started.stderr + started.stdout)
                suite_id = json.loads(started.stdout)['suite_id']
                wait_suite(call, suite_id, root, lambda state: state['status'] == 'blocked')
                owner_pid = call('ping', {}, root, autostart=False)['pid']
                exported = invoke_cli(root, 'report', suite_id, '--output', str(root/'export'))
                self.assertEqual(exported.returncode, 0, exported.stderr + exported.stdout)
                self.assertTrue((root/'export/suite.json').is_file())
                self.assertTrue((root/'export/report.md').is_file())
                report = json.loads((root/'export/suite.json').read_text(encoding='utf-8'))
                self.assertTrue(report['provisional'])
                # Its harness grades every child after its run: the report counts equivalent drivers of the protocol, and
                # leads with the pass rate of the trials graded.
                self.assertEqual((report['schema'], report['protocol']), ('benchmark-suite-report/3', 'assistive/1'))
                self.assertEqual(report['equivalence']['equivalent'], 0)
                self.assertEqual((report['pass_rate']['passes'], report['pass_rate']['rate']), (0, None))
                self.assertEqual(call('ping', {}, root, autostart=False)['pid'], owner_pid)
            finally:
                shutdown_owner(root)
            no_owner = invoke_cli(root, 'report', suite_id, '--output', str(root/'refused'))
            self.assertEqual(no_owner.returncode, 1, no_owner.stderr + no_owner.stdout)
            self.assertFalse((root/'refused').exists())
            self.assertFalse(call('ping', {}, root, autostart=False)['ok'])

    def test_invalid_selection_is_refused_before_owner_creation(self):
        from suite_fixtures import client_call as call
        selections = [
            ('not-registered', 'original', 0), ('setup-smoke', 'original', 0),
            ('tq9-v2', 'not-a-scenario', 0)]
        with tempfile.TemporaryDirectory(prefix='suite CLI preflight ') as temporary:
            root = Path(temporary)
            for index, (case, scenario, seed) in enumerate(selections):
                with self.subTest(case=case, scenario=scenario, seed=seed):
                    manifest = scripted_manifest()
                    manifest['entries'] = [{'case': case, 'scenario': scenario, 'case_seed': seed}]
                    path = root / f'manifest-{index}.json'
                    path.write_text(json.dumps(manifest), encoding='utf-8')
                    home = root / f'home-{index}'
                    try:
                        done = invoke_cli(home, 'start', '--manifest', str(path))
                        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
                        self.assertFalse(json.loads(done.stdout)['ok'])
                        self.assertFalse((home / 'service.json').exists())
                        self.assertFalse((home / 'runs.sqlite3').exists())
                    finally:
                        if (home / 'service.json').exists():
                            shutdown_owner(home)

    def test_duplicate_reconnect_does_not_repin_current_registry_assets(self):
        from suite_fixtures import client_call as call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI duplicate ') as temporary:
            root = Path(temporary)/'owner'
            configure('codex', ['missing-scripted-runtime'], home=root)
            path = root / 'manifest.json'
            path.write_text(json.dumps(scripted_manifest()), encoding='utf-8')
            try:
                first = invoke_cli(root, 'start', '--manifest', str(path), '--request-id', 'saved')
                self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
                suite = json.loads(first.stdout)
                wait_suite(call, suite['suite_id'], root, lambda state: state['status'] == 'blocked')
                code = """from firmware_bench.benchmark_support import registry
def unavailable(*args, **kwargs):
    raise ValueError('Current registry assets unavailable')
registry.pin_case = unavailable
from firmware_bench.benchmark import main
raise SystemExit(main())
"""
                done = subprocess.run([sys.executable, '-c', code, '--home', str(root),
                    'suite', 'start', '--manifest', str(path), '--request-id', 'saved'],
                    env={**os.environ, **package_env()}, capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
                self.assertEqual(json.loads(done.stdout)['suite_id'], suite['suite_id'])
                self.assertTrue(json.loads(done.stdout)['duplicate'])
            finally:
                shutdown_owner(root)

    def test_separate_cli_processes_reconnect_to_the_same_suite(self):
        from suite_fixtures import client_call as call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI ') as temporary:
            root = Path(temporary)/'owner'
            configure('codex', ['missing-scripted-runtime'], home=root)
            manifest, secrets = root/'manifest.json', root/'options.json'
            manifest.write_text(json.dumps(scripted_manifest()), encoding='utf-8')
            secrets.write_text(json.dumps(options(harness_devices(self, Path(temporary)/'PRIVATE_HANDLE'))), encoding='utf-8')
            def invoke(*args):
                done = invoke_cli(root, *args)
                self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
                return json.loads(done.stdout)
            try:
                first = invoke('start', '--manifest', str(manifest), '--executor', 'codex',
                    '--options', str(secrets), '--request-id', 'cli-once')
                self.assertTrue(first['ok'])
                wait_suite(call, first['suite_id'], root, lambda state: state['status'] == 'blocked')
                again = invoke('start', '--manifest', str(manifest), '--options', str(secrets),
                    '--request-id', 'cli-once')
                self.assertEqual(first['suite_id'], again['suite_id'])
                self.assertTrue(again['duplicate'])
                self.assertEqual(invoke('status', first['suite_id'])['status'], 'blocked')
                page = invoke('result', first['suite_id'], '--offset', '0', '--limit', '1')
                self.assertEqual(page['trials'][0]['outcome_category'], 'operator')
                self.assertNotIn('PRIVATE_HANDLE', json.dumps(page))
                self.assertNotIn('evaluator_password', json.dumps(page))
                events = invoke('events', first['suite_id'], '--after', '0')
                self.assertGreater(events['cursor'], 0)
                self.assertEqual(invoke('cancel', first['suite_id'])['status'], 'cancelled')
                resumed = invoke('resume', first['suite_id'], '--suite-budget-seconds', '240',
                    '--budget-reason', 'scripted contract continuation', '--options', str(secrets))
                self.assertEqual(resumed['suite_id'], first['suite_id'])
                wait_suite(call, first['suite_id'], root, lambda state: state['status'] == 'blocked')
                experiment = invoke('result', first['suite_id'])['experiment']
                self.assertEqual(experiment['comparison_identity']['budgets']['effective']['suite_budget_seconds'], 240)
            finally:
                shutdown_owner(root)


class SuiteCommandTests(unittest.TestCase):
    """The tests that reached a suite through the benchmark's MCP tools, which are gone. Each reaches it through
    the command and keeps its other assertions."""

    def started(self, root, manifest, secrets=None):
        """The files a `suite start` names, written under `root`: the manifest and, with `secrets`, the options. And
        `invoke(action, *arguments)`, which gives what the suite command prints once it has ended well."""
        manifest_path, options_path = root/'manifest.json', root/'options.json'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        if secrets is not None:
            options_path.write_text(json.dumps(secrets), encoding='utf-8')

        def invoke(action, *arguments):
            done = invoke_cli(root, action, *arguments)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            result = json.loads(done.stdout)
            self.assertTrue(result['ok'], result)
            return result
        return str(manifest_path), str(options_path), invoke

    def test_cancel_and_resume_use_the_same_durable_owner_and_child(self):
        from suite_fixtures import client_call as call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite command control ') as temporary:
            root = Path(temporary)/'owner'
            secrets = options(harness_devices(self, Path(temporary)/'fixture'))
            configure('codex', ['missing-scripted-runtime'], home=root)
            owner_pid = call('ping', {}, root)['pid']
            manifest, given, invoke = self.started(root, scripted_manifest(2), secrets)
            try:
                suite = invoke('start', '--manifest', manifest, '--options', given)
                stopped = wait_suite(call, suite['suite_id'], root,
                    lambda state: state['status'] == 'blocked' and not state['stopping'])
                child_id = stopped['active_child_id']
                self.assertIsNotNone(child_id)
                cancelled = invoke('cancel', suite['suite_id'])
                self.assertEqual(cancelled['status'], 'cancelled')
                invoke('resume', suite['suite_id'], '--suite-budget-seconds', '240',
                    '--budget-reason', 'explicit scripted test extension', '--options', given)
                stopped = wait_suite(call, suite['suite_id'], root,
                    lambda state: state['status'] == 'blocked' and not state['stopping'])
                self.assertEqual(stopped['active_child_id'], child_id)
                page = invoke('result', suite['suite_id'])
                self.assertEqual([trial['run_id'] for trial in page['trials']], [child_id, None])
                self.assertEqual(page['experiment']['comparison_identity']['budgets']['effective']['suite_budget_seconds'], 240)
                self.assertEqual(call('ping', {}, root, autostart=False)['pid'], owner_pid)
            finally:
                shutdown_owner(root)

    def test_home_selection_resolves_and_pagination_rejects_invalid_numbers(self):
        from suite_fixtures import client_call as call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite command public parameters ') as temporary:
            root, other = Path(temporary)/'owner', Path(temporary)/'other owner'
            configure('codex', ['missing-scripted-runtime'], home=root)
            call('ping', {}, root)
            manifest, given, _ = self.started(root, scripted_manifest())

            def start(home, *arguments):
                """What `suite start` prints with `home` as its `options.home`, given in an options file and run in
                the home's folder, from where a relative home is read."""
                Path(given).write_text(json.dumps({'home': home}), encoding='utf-8')
                done = invoke_cli(root, 'start', '--manifest', manifest, '--options', given, *arguments, cwd=root)
                return json.loads(done.stdout)
            try:
                for home in (str(other), '', True):
                    self.assertFalse(start(home)['ok'])
                suite = start('.', '--request-id', 'same-home')
                self.assertTrue(suite['ok'], suite)
                again = start(str(root.resolve()), '--request-id', 'same-home')
                self.assertEqual(again['suite_id'], suite['suite_id'])
                for action, values in (('result', ('--limit', '101')), ('result', ('--limit', '0')),
                        ('result', ('--offset', '-1'))):
                    with self.subTest(action=action, values=values):
                        done = invoke_cli(root, action, suite['suite_id'], *values)
                        self.assertFalse(json.loads(done.stdout)['ok'], done.stdout + done.stderr)
                self.assertFalse(other.exists())
            finally:
                shutdown_owner(root)

    def test_suite_reconnects_with_running_child_and_paginates_every_slot(self):
        from suite_fixtures import client_call as call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite command reconnect ') as temporary:
            root = Path(temporary)/'owner'
            root.mkdir()
            secrets = options(harness_devices(self, Path(temporary)/'fixture'))
            command = write_scripted_case_worker(root)
            script = Path(command[1])
            source = script.read_text(encoding='utf-8')
            source = source.replace("if prompt.startswith('Execute exactly'):", """if prompt.startswith('Execute exactly'):
    pathlib.Path(GATE_STARTED).write_text('ready')
    while not pathlib.Path(GATE_RELEASE).exists(): time.sleep(.02)""")
            source = source.replace('GATE_STARTED', repr(str(root/'started')))
            source = source.replace('GATE_RELEASE', repr(str(root/'release')))
            script.write_text(source, encoding='utf-8')
            configure('codex', command, home=root)
            owner_pid = call('ping', {}, root)['pid']
            manifest, given, invoke = self.started(root, scripted_manifest(2), secrets)
            start = ('start', '--manifest', manifest, '--request-id', 'command-once', '--options', given)
            try:
                suite = invoke(*start)
                deadline = time.monotonic() + 30
                while not (root/'started').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root/'started').exists(), 'scripted worker did not reach its gate')
                self.assertEqual(call('suite_status', {'suite_id': suite['suite_id']}, root)['status'], 'running')
                # Each command is a process of its own: a later one reconnects to the suite the first started.
                status = invoke('status', suite['suite_id'])
                self.assertEqual(status['status'], 'running')
                again = invoke(*start)
                self.assertTrue(again['duplicate'])
                self.assertEqual(again['suite_id'], suite['suite_id'])
                (root/'release').write_text('continue', encoding='utf-8')
                # The bench harness detaches after the suite completes; until it has let go (not stopping),
                # its suite.harness_detached event can still follow the page read below.
                wait_suite(call, suite['suite_id'], root,
                    lambda state: state['status'] == 'completed' and not state['stopping'])
                events = invoke('events', suite['suite_id'])
                self.assertNotIn('suite.recovered', [event['kind'] for event in events['events']])
                self.assertIn('trial.finished', [event['kind'] for event in events['events']])
                empty = invoke('events', suite['suite_id'], '--after', str(events['cursor']))
                self.assertEqual(empty['events'], [])
                rows, offset = [], 0
                while offset is not None:
                    page = invoke('result', suite['suite_id'], '--offset', str(offset), '--limit', '1')
                    rows.extend(page['trials'])
                    offset = page['next_offset']
                    self.assertNotIn('evaluator_password', json.dumps(page))
                    self.assertNotIn('transcript', json.dumps(page))
                    self.assertEqual(len(page['experiment']['comparison_identity']['trials']), 2)
                self.assertEqual([row['ordinal'] for row in rows], [0, 1])
                self.assertEqual(len({row['run_id'] for row in rows}), 2)
                self.assertEqual(call('ping', {}, root, autostart=False)['pid'], owner_pid)
            finally:
                (root/'release').write_text('cleanup', encoding='utf-8')
                shutdown_owner(root)

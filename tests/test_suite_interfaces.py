"""Durable suite clients exercise real CLI processes and stdio MCP connections."""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from suite_fixtures import legacy_manifest, wait_suite, write_scripted_case_worker


def invoke_cli(root, *args):
    return subprocess.run([sys.executable, '-m', 'generative_driver', '--home', str(root),
        'benchmark', 'suite', *args], capture_output=True, text=True, timeout=30)


class SuiteCliTests(unittest.TestCase):
    def test_pending_registered_fixture_is_refused_before_owner_creation(self):
        from generative_driver.client import call
        from suite_fixtures import public_case_fixture
        with tempfile.TemporaryDirectory(prefix='suite CLI pending calibration ') as temporary:
            root = Path(temporary)
            resources, home = root/'resources', root/'absent owner'
            public_case_fixture(resources, calibration={'status': 'pending'})
            manifest = legacy_manifest()
            manifest['entries'] = [{'case': 'tq9-v2', 'scenario': 'semantic', 'case_seed': 0}]
            path = root/'manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            code = """import sys
from pathlib import Path
import generative_driver.benchmark as benchmark
resources = Path(sys.argv.pop(1))
benchmark.case_root = lambda: resources
from generative_driver.cli import main
raise SystemExit(main())
"""
            try:
                done = subprocess.run([sys.executable, '-c', code, str(resources), '--home', str(home),
                    'benchmark', 'suite', 'start', '--manifest', str(path)],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
                self.assertIn('reference calibration', json.loads(done.stdout)['error'])
                self.assertFalse((home/'service.json').exists())
                self.assertFalse((home/'runs.sqlite3').exists())
            finally:
                if (home/'service.json').exists():
                    call('shutdown', {}, home)

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
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI export ') as temporary:
            root = Path(temporary)
            absent = root/'absent owner'
            missing = invoke_cli(absent, 'report', 'unknown-suite')
            self.assertEqual(missing.returncode, 2, missing.stderr + missing.stdout)
            self.assertIn('--output', missing.stderr)
            self.assertFalse(absent.exists())
            configure('codex', ['missing-scripted-runtime'], home=root)
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps(legacy_manifest()), encoding='utf-8')
            try:
                started = invoke_cli(root, 'start', '--manifest', str(manifest))
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
                self.assertEqual(report['success']['passed'], 0)
                self.assertEqual(call('ping', {}, root, autostart=False)['pid'], owner_pid)
            finally:
                call('shutdown', {}, root)
            no_owner = invoke_cli(root, 'report', suite_id, '--output', str(root/'refused'))
            self.assertEqual(no_owner.returncode, 1, no_owner.stderr + no_owner.stdout)
            self.assertFalse((root/'refused').exists())
            self.assertFalse(call('ping', {}, root, autostart=False)['ok'])

    def test_invalid_selection_is_refused_before_owner_creation(self):
        from generative_driver.client import call
        selections = [
            ('not-registered', 'semantic', 0), ('setup-smoke', 'identity', 0),
            ('bme280', 'identity', 0), ('tq9', 'not-a-scenario', 0),
            ('tq9', 'identity', 1)]
        with tempfile.TemporaryDirectory(prefix='suite CLI preflight ') as temporary:
            root = Path(temporary)
            for index, (case, scenario, seed) in enumerate(selections):
                with self.subTest(case=case, scenario=scenario, seed=seed):
                    manifest = legacy_manifest()
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
                            call('shutdown', {}, home)

    def test_duplicate_reconnect_does_not_repin_current_registry_assets(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI duplicate ') as temporary:
            root = Path(temporary)
            configure('codex', ['missing-scripted-runtime'], home=root)
            path = root / 'manifest.json'
            path.write_text(json.dumps(legacy_manifest()), encoding='utf-8')
            try:
                first = invoke_cli(root, 'start', '--manifest', str(path), '--request-id', 'saved')
                self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
                suite = json.loads(first.stdout)
                wait_suite(call, suite['suite_id'], root, lambda state: state['status'] == 'blocked')
                code = """from generative_driver.benchmark_support import registry
def unavailable(*args, **kwargs):
    raise ValueError('Current registry assets unavailable')
registry.pin_case = unavailable
from generative_driver.cli import main
raise SystemExit(main())
"""
                done = subprocess.run([sys.executable, '-c', code, '--home', str(root), 'benchmark',
                    'suite', 'start', '--manifest', str(path), '--request-id', 'saved'],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
                self.assertEqual(json.loads(done.stdout)['suite_id'], suite['suite_id'])
                self.assertTrue(json.loads(done.stdout)['duplicate'])
            finally:
                call('shutdown', {}, root)

    def test_separate_cli_processes_reconnect_to_the_same_suite(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI ') as temporary:
            root = Path(temporary)
            configure('codex', ['missing-scripted-runtime'], home=root)
            manifest, options = root/'manifest.json', root/'options.json'
            manifest.write_text(json.dumps(legacy_manifest()), encoding='utf-8')
            options.write_text(json.dumps({'evaluator_password_files': {'tq9': 'PRIVATE_HANDLE'}}), encoding='utf-8')
            def invoke(*args):
                done = invoke_cli(root, *args)
                self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
                return json.loads(done.stdout)
            try:
                first = invoke('start', '--manifest', str(manifest), '--executor', 'codex',
                    '--options', str(options), '--request-id', 'cli-once')
                self.assertTrue(first['ok'])
                wait_suite(call, first['suite_id'], root, lambda state: state['status'] == 'blocked')
                again = invoke('start', '--manifest', str(manifest), '--options', str(options),
                    '--request-id', 'cli-once')
                self.assertEqual(first['suite_id'], again['suite_id'])
                self.assertTrue(again['duplicate'])
                self.assertEqual(invoke('status', first['suite_id'])['status'], 'blocked')
                page = invoke('result', first['suite_id'], '--offset', '0', '--limit', '1')
                self.assertEqual(page['trials'][0]['outcome_category'], 'host')
                self.assertNotIn('PRIVATE_HANDLE', json.dumps(page))
                self.assertNotIn('evaluator_password', json.dumps(page))
                events = invoke('events', first['suite_id'], '--after', '0')
                self.assertGreater(events['cursor'], 0)
                self.assertEqual(invoke('cancel', first['suite_id'])['status'], 'cancelled')
                resumed = invoke('resume', first['suite_id'], '--suite-budget-seconds', '240',
                    '--budget-reason', 'scripted contract continuation')
                self.assertEqual(resumed['suite_id'], first['suite_id'])
                wait_suite(call, first['suite_id'], root, lambda state: state['status'] == 'blocked')
                experiment = invoke('result', first['suite_id'])['experiment']
                self.assertEqual(experiment['comparison_identity']['budgets']['effective']['suite_budget_seconds'], 240)
            finally:
                call('shutdown', {}, root)


class SuiteMcpTests(unittest.TestCase):
    def test_windows_start_refuses_when_owner_disappears_after_ping(self):
        asyncio.run(self.owner_disappears())

    async def owner_disappears(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite MCP disappearing owner ') as temporary:
            root = Path(temporary)
            configure('codex', ['missing-scripted-runtime'], home=root)
            call('ping', {}, root)
            # Control only the external connection boundary: stop the real owner
            # after its successful ping, before the real start request forwards.
            code = """import platform
platform.system = lambda: 'Windows'
from generative_driver import client
original = client.call
def disconnect(method, params=None, home=None, **kwargs):
    result = original(method, params, home, **kwargs)
    if method == 'ping' and result.get('ok'):
        original('shutdown', {}, home, autostart=False)
    return result
client.call = disconnect
from generative_driver.mcp import main
main()
"""
            params = StdioServerParameters(command=sys.executable, args=['-c', code],
                env={'GENERATIVE_DRIVER_HOME': str(root)}, cwd=str(root))
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reply = await session.call_tool('driver_benchmark_suite_start', {'manifest': legacy_manifest()})
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused['ok'])
                        self.assertIn('service start', refused['reason'])
                self.assertFalse(call('ping', {}, root, autostart=False)['ok'])
            finally:
                call('shutdown', {}, root)

    def test_cancel_and_resume_use_the_same_durable_owner_and_child(self):
        asyncio.run(self.cancel_and_resume())

    async def cancel_and_resume(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite MCP control ') as temporary:
            root = Path(temporary)
            configure('codex', ['missing-scripted-runtime'], home=root)
            owner_pid = call('ping', {}, root)['pid']
            params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                env={'GENERATIVE_DRIVER_HOME': str(root)}, cwd=str(root))
            async def invoke(session, action, **arguments):
                reply = await session.call_tool('driver_benchmark_suite_' + action, arguments)
                self.assertFalse(reply.is_error, reply)
                result = json.loads(reply.content[0].text)
                self.assertTrue(result['ok'], result)
                return result
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        suite = await invoke(session, 'start', manifest=legacy_manifest(2))
                        stopped = await asyncio.to_thread(wait_suite, call, suite['suite_id'], root,
                            lambda state: state['status'] == 'blocked' and not state['stopping'])
                        child_id = stopped['active_child_id']
                        cancelled = await invoke(session, 'cancel', suite_id=suite['suite_id'])
                        self.assertEqual(cancelled['status'], 'cancelled')
                        await invoke(session, 'resume', suite_id=suite['suite_id'], suite_budget_seconds=240,
                            budget_reason='explicit scripted test extension')
                        stopped = await asyncio.to_thread(wait_suite, call, suite['suite_id'], root,
                            lambda state: state['status'] == 'blocked' and not state['stopping'])
                        self.assertEqual(stopped['active_child_id'], child_id)
                        page = await invoke(session, 'result', suite_id=suite['suite_id'])
                        self.assertEqual([trial['run_id'] for trial in page['trials']], [child_id, None])
                        self.assertEqual(page['experiment']['comparison_identity']['budgets']['effective']['suite_budget_seconds'], 240)
                self.assertEqual(call('ping', {}, root, autostart=False)['pid'], owner_pid)
            finally:
                call('shutdown', {}, root)

    def test_home_selection_resolves_and_pagination_rejects_invalid_numbers(self):
        asyncio.run(self.home_and_pagination())

    async def home_and_pagination(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite MCP public parameters ') as temporary:
            root, other = Path(temporary), Path(temporary)/'other owner'
            configure('codex', ['missing-scripted-runtime'], home=root)
            call('ping', {}, root)
            params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                env={'GENERATIVE_DRIVER_HOME': str(root)}, cwd=str(root))
            expected_error_log = tempfile.TemporaryFile(mode='w+t')
            try:
                async with stdio_client(params, errlog=expected_error_log) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        for home in (str(other), '', True):
                            reply = await session.call_tool('driver_benchmark_suite_start', {
                                'manifest': legacy_manifest(), 'options': {'home': home}})
                            self.assertFalse(json.loads(reply.content[0].text)['ok'])
                        first = await session.call_tool('driver_benchmark_suite_start', {
                            'manifest': legacy_manifest(), 'options': {'home': '.'}, 'request_id': 'same-home'})
                        suite = json.loads(first.content[0].text)
                        self.assertTrue(suite['ok'], suite)
                        again = await session.call_tool('driver_benchmark_suite_start', {
                            'manifest': legacy_manifest(), 'options': {'home': str(root.resolve())},
                            'request_id': 'same-home'})
                        self.assertEqual(json.loads(again.content[0].text)['suite_id'], suite['suite_id'])
                        for action, values in (('result', {'limit': 101}), ('result', {'limit': 0}),
                                ('result', {'offset': -1}), ('result', {'offset': True}),
                                ('result', {'limit': True}), ('events', {'after': True})):
                            with self.subTest(action=action, values=values):
                                reply = await session.call_tool('driver_benchmark_suite_' + action,
                                    {'suite_id': suite['suite_id'], **values})
                                if reply.is_error:
                                    self.assertIn('validation error', reply.content[0].text.lower())
                                else:
                                    self.assertFalse(json.loads(reply.content[0].text)['ok'], reply)
                expected_error_log.seek(0)
                diagnostics = expected_error_log.read()
                self.assertIn('validation error', diagnostics.lower())
                self.assertIn('int_type', diagnostics)
                self.assertFalse(other.exists())
            finally:
                expected_error_log.close()
                call('shutdown', {}, root)

    def test_suite_reconnects_with_running_child_and_paginates_every_slot(self):
        asyncio.run(self.reconnect_running_child())

    async def reconnect_running_child(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite MCP reconnect ') as temporary:
            root = Path(temporary)
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
            params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                env={'GENERATIVE_DRIVER_HOME': str(root)}, cwd=str(root))
            async def invoke(session, action, **arguments):
                reply = await session.call_tool('driver_benchmark_suite_' + action, arguments)
                self.assertFalse(reply.is_error, reply)
                result = json.loads(reply.content[0].text)
                self.assertTrue(result['ok'], result)
                return result
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        suite = await invoke(session, 'start', manifest=legacy_manifest(2), request_id='mcp-once')
                        deadline = time.monotonic() + 10
                        while not (root/'started').exists() and time.monotonic() < deadline:
                            await asyncio.sleep(.02)
                        self.assertTrue((root/'started').exists(), 'scripted worker did not reach its gate')
                self.assertEqual(call('suite_status', {'suite_id': suite['suite_id']}, root)['status'], 'running')
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        status = await invoke(session, 'status', suite_id=suite['suite_id'])
                        self.assertEqual(status['status'], 'running')
                        again = await invoke(session, 'start', manifest=legacy_manifest(2), request_id='mcp-once')
                        self.assertTrue(again['duplicate'])
                        self.assertEqual(again['suite_id'], suite['suite_id'])
                        (root/'release').write_text('continue', encoding='utf-8')
                        await asyncio.to_thread(wait_suite, call, suite['suite_id'], root,
                            lambda state: state['status'] == 'completed')
                        events = await invoke(session, 'events', suite_id=suite['suite_id'])
                        self.assertNotIn('suite.recovered', [event['kind'] for event in events['events']])
                        self.assertIn('trial.finished', [event['kind'] for event in events['events']])
                        empty = await invoke(session, 'events', suite_id=suite['suite_id'], after=events['cursor'])
                        self.assertEqual(empty['events'], [])
                        rows, offset = [], 0
                        while offset is not None:
                            page = await invoke(session, 'result', suite_id=suite['suite_id'], offset=offset, limit=1)
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
                call('shutdown', {}, root)

    def test_windows_suite_does_not_create_an_owner(self):
        asyncio.run(self.absent_owner())

    async def absent_owner(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix='suite MCP Windows policy ') as temporary:
            root = Path(temporary)
            params = StdioServerParameters(command=sys.executable,
                args=['-c', "import platform; platform.system=lambda:'Windows'; from generative_driver.mcp import main; main()"],
                env={'GENERATIVE_DRIVER_HOME': str(root)}, cwd=str(root))
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        response = await session.call_tool('driver_benchmark_suite_start', {
                            'manifest': legacy_manifest(), 'executor': 'codex', 'options': {}})
                        self.assertFalse(response.is_error, response)
                        result = json.loads(response.content[0].text)
                        self.assertFalse(result['ok'])
                        self.assertIn('service start', result['reason'])
                self.assertFalse((root/'service.json').exists())
                self.assertFalse((root/'runs.sqlite3').exists())
            finally:
                if (root/'service.json').exists():
                    call('shutdown', {}, root)

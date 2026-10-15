"""The replay executor drives a benchmark trial through every stage without a language model: a saved candidate's writes
and tool calls, replayed through the stage gateway a real worker gets. Synthetic TCP peers, a scripted candidate and a toy
case only; never Renode, a real password or a model run."""
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

EVIDENCE = 'e' * 64
TIMEOUT = 120
STAGES = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse']


def serve(test, home):
    """A configurator daemon in this process at `home`; worker gateway processes reach it as they reach any configurator."""
    from generative_driver import client, daemon
    home.mkdir(mode=0o700)
    if os.name == 'nt':
        family, address = 'AF_PIPE', r'\\.\pipe\gd-test-' + secrets.token_hex(8)
    else:
        sockets = Path(tempfile.mkdtemp(prefix='gd-t-'))
        test.addCleanup(shutil.rmtree, sockets, True)
        family, address = 'AF_UNIX', str(sockets / 'owner.sock')
    (home / 'service.json').write_text(json.dumps({'family': family, 'address': address, 'authkey': secrets.token_hex(32),
                                                   'pid': os.getpid(), 'code': client.code_identity()}))
    thread = threading.Thread(target=daemon.serve, args=(home,), daemon=True)
    thread.start()

    def stop():
        client._request(home, 'shutdown', {})
        thread.join(20)
    test.addCleanup(stop)
    until = time.monotonic() + 10
    while not client.call('ping', home=home, autostart=False).get('ok'):
        test.assertLess(time.monotonic(), until)
        time.sleep(.05)
    return lambda method, params=None: client.call(method, params or {}, home=home, autostart=False)


class ReplayExecutionTests(unittest.TestCase):
    """The test plays the bench harness: it provides the device and the admission; the configurator holds no truth."""

    def setUp(self):
        from suite_fixtures import public_case_fixture
        from tq9_workflow_fixture import replay_candidate
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        # Built before the case resources are replaced: the fixture model starts from the installed setup-smoke model.
        self.candidate = replay_candidate(self.root / 'candidate')
        public_case_fixture(self.root / 'resources', calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
        for target, value in (('generative_driver.benchmark.case_root', {'return_value': self.root / 'resources'}),
                              ('generative_driver.benchmark_support.registry.require_calibration',
                               {'side_effect': AssertionError('the configurator admitted the case')}),
                              ('generative_driver.benchmark_support.emulator.truth_for_case',
                               {'side_effect': AssertionError('the configurator unlocked the case truth')})):
            guard = patch(target, **value)
            guard.start()
            self.addCleanup(guard.stop)
        self.home = self.root / 'home'
        self.call = serve(self, self.home)

    def start(self, candidate, *, device=None, **changes):
        from test_harness import Device
        self.device = Device(self, self.root / 'resources/cases/tq9-v2/firmware.bin', device or self.root)
        return self.call('start', {
            'goal': 'Execute one registered benchmark trial with checked evidence.', 'case': 'tq9-v2',
            'executor': 'replay', 'executor_config': {'candidate': str(candidate)}, 'effects': ['write', 'actuate'],
            'budget_seconds': TIMEOUT, 'case_options': {'scenario_id': 'original'}, 'device': self.device.server.descriptor,
            'admission': {'calibration_evidence_sha256': EVIDENCE},
            'evaluation_token_sha256': hashlib.sha256(b'harness token').hexdigest(), **changes})

    def finished(self, run_id):
        until = time.monotonic() + TIMEOUT
        while time.monotonic() < until:
            status = self.call('status', {'run_id': run_id})
            if status.get('status') not in ('queued', 'running') and not status.get('stopping'):
                return self.call('result', {'run_id': run_id})
            time.sleep(.1)
        self.fail(self.call('result', {'run_id': run_id}))

    def events(self, run_id):
        events, cursor = [], 0
        while True:
            page = self.call('events', {'run_id': run_id, 'after': cursor})
            events.extend(page['events'])
            if page['cursor'] == cursor or len(page['events']) < 500:
                return events
            cursor = page['cursor']

    def test_a_saved_candidate_runs_every_stage_through_the_stage_gateway_without_a_model(self):
        from generative_driver.benchmark_support.snapshots import read_snapshot
        from generative_driver.configurator import digest
        candidate = self.candidate
        started = self.start(candidate, request_id='replay-trial')
        self.assertTrue(started.get('ok'), started)
        result = self.finished(started['run_id'])
        # Every stage handed off, and the run awaits the one grade its bench harness gives after it.
        self.assertEqual((result['status'], result['reason']), ('completed', 'Frozen driver awaits independent evaluation'),
                         result)
        self.assertEqual(result['progress']['equivalence'], 'pending')
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']], STAGES)
        # Every stage ran the replay runtime: no language model, provider or token usage.
        self.assertEqual([w['stage'] for w in result['worker_reports']], STAGES)
        for worker in result['worker_reports']:
            self.assertEqual((worker['runtime'], worker['model'], worker['provider'], worker['usage'], worker['status']),
                             ('replay', None, None, None, 'completed'), worker)
        self.assertEqual((result['agent']['runtime'], result['agent']['provider']), ('replay', None))
        events = self.events(started['run_id'])
        self.assertEqual([e['data']['executor'] for e in events if e['kind'] == 'run.started'], ['replay'])
        # The configurator observed and recorded every scripted call, in order, and refused none.
        finished = [e['data'] for e in events if e['kind'] == 'tool.finished']
        self.assertEqual([(e['stage'], e['name']) for e in finished],
                         [('acquire', 'acquire_firmware_artifact'), ('probe', 'interface_execute'), ('probe', 'probe_run'),
                          ('emit', 'emit_package'), ('emit', 'emit_check')] + [('reuse', 'benchmark_package_execute')] * 4)
        self.assertTrue(all(e['ok'] and e['actor'] == 'worker' for e in finished), finished)
        self.assertEqual([e for e in events if e['kind'] == 'tool.refused'], [])
        # Nothing reached the device outside the configurator's live-call windows.
        self.assertEqual(self.device.peers[0].paused_requests, [])
        # The interpretation handed off is the saved candidate's model and replies, byte for byte.
        # Accepted artifacts are copies named <index>-<name>.
        interpret = {Path(a['path']).name.split('-', 1)[1]: a['sha256'] for a in result['accepted_handoffs'][1]['artifacts']}
        for name in ('model.json', 'replies.json'):
            self.assertEqual(interpret[name], hashlib.sha256((candidate / name).read_bytes()).hexdigest())
        # The package handed off is the one emit_package built through the gateway, hashed when the tool returned.
        build = next(e for e in finished if e['name'] == 'emit_package')
        package = result['accepted_handoffs'][4]['artifacts']
        self.assertEqual([a['sha256'] for a in package], [build['artifacts'][str(Path(build['result']['package_dir']).resolve())]])
        # Fresh reuse ran the frozen package live on the provided device, with instrument readings around each call.
        live = [e for e in finished if e['name'] == 'benchmark_package_execute']
        self.assertTrue(all(e['connected'] and not e['refused_before_open'] for e in live), live)
        self.assertEqual({(e['result']['before_observation']['source'], e['result']['observation']['source'])
                          for e in live}, {('instrument', 'instrument')})
        mission = next(c for c in result['accepted_handoffs'][5]['checks'] if c['name'] == 'fresh_worker_mission')
        self.assertIs(mission['passed'], True, mission)
        # The run's execution snapshot names the copy it replays, taken into its run directory at start, by the saved
        # candidate's hash.
        run_dir = self.home / 'runs' / started['run_id']
        executed = read_snapshot(run_dir)['executed']['runtime_configuration']
        self.assertEqual(executed, {'runtime': 'replay', 'candidate': str(run_dir / 'replay-candidate'),
                                    'candidate_sha256': digest(candidate)})

    def test_a_run_replays_the_copy_of_the_candidate_it_took_at_start(self):
        from generative_driver.configurator import Controller
        original, saved = Controller._prepare, (self.candidate / 'model.json').read_bytes()

        def prepare(controller, spec, stage, *args, **kwargs):
            if stage == 'interpret':
                # Between acquire's handoff and interpretation, the saved candidate's interpretation is replaced.
                (self.candidate / 'model.json').write_text('{"schema": "another model"}')
            return original(controller, spec, stage, *args, **kwargs)
        with patch.object(Controller, '_prepare', prepare):
            started = self.start(self.candidate)
            result = self.finished(started['run_id'])
        self.assertEqual((result['status'], result['progress']['equivalence']), ('completed', 'pending'), result)
        # Interpretation handed on the bytes the run recorded at start, not the saved candidate as it is now.
        interpret = next(h for h in result['accepted_handoffs'] if h['stage'] == 'interpret')
        model = next(a for a in interpret['artifacts'] if a['path'].endswith('model.json'))
        self.assertEqual(model['sha256'], hashlib.sha256(saved).hexdigest())

    def test_a_change_to_the_runs_copy_of_the_candidate_stops_the_run_for_integrity(self):
        from generative_driver import configurator
        from generative_driver.configurator import Controller
        prepare, execute = Controller._prepare, configurator.execute

        def changing(when):
            def change(spec):
                (Path(spec['executor_config']['candidate']) / 'model.json').write_text('{"schema": "another model"}')
            def prepared(controller, spec, stage, *args, **kwargs):
                if when == 'before interpret' and stage == 'interpret':
                    change(spec)
                return prepare(controller, spec, stage, *args, **kwargs)
            def executed(request, config, *args, **kwargs):
                result = execute(request, config, *args, **kwargs)
                if when == 'during reuse' and request.stage == 'reuse':
                    change({'executor_config': config})
                return result
            return prepared, executed
        for when, stopped, ran, handed in (('before interpret', 'interpret', ['acquire'], ['acquire']),
                                           ('during reuse', 'reuse', STAGES, STAGES[:-1])):
            with self.subTest(when=when):
                prepared, executed = changing(when)
                with patch.object(Controller, '_prepare', prepared), patch.object(configurator, 'execute', executed):
                    started = self.start(self.candidate, device=self.root / when.replace(' ', '-'))
                    result = self.finished(started['run_id'])
                self.assertEqual((result['status'], result['outcome_category'], result['progress']['next_stage']),
                                 ('blocked', 'integrity', stopped), result)
                self.assertIn('replay candidate changed', result['reason'])
                # The stage that saw the change hands nothing on: no worker starts from a changed copy, and one whose
                # copy changed while it ran is never committed.
                self.assertEqual([w['stage'] for w in result['worker_reports']], ran)
                self.assertEqual([h['stage'] for h in result['accepted_handoffs']], handed)

    def test_failed_steps_are_notes_and_a_replay_never_replaces_a_workspace_file_or_calls_an_unlisted_tool(self):
        script = json.loads((self.candidate / 'replay.json').read_text())
        # The candidate also writes a file named like a sealed interpretation input, and its probe calls a tool probe is
        # not assigned and an operation the model lacks.
        (self.candidate / 'TASKS.json').write_text('{"schema": "not the sealed task contract"}')
        script['stages']['interpret'].insert(0, {'write': 'TASKS.json'})
        script['stages']['probe'][:0] = [
            {'call': 'emit_package', 'arguments': {'model_dir': {'$context': 'model_dir'}}},
            {'call': 'interface_execute', 'arguments': {'model_dir': {'$context': 'model_dir'}, 'operation': 'absent'}}]
        (self.candidate / 'replay.json').write_text(json.dumps(script))
        started = self.start(self.candidate)
        result = self.finished(started['run_id'])
        # Report content never stops a run: every stage handed off and the run awaits its grade.
        self.assertEqual((result['status'], result['progress']['equivalence']), ('completed', 'pending'), result)
        workers = {w['stage']: w['report'] for w in result['worker_reports']}
        checks = lambda stage: [(c['name'], c['status']) for c in workers[stage]['checks']]
        self.assertEqual(checks('interpret'), [('replay step 1: write TASKS.json', 'fail'), ('replay step 2: write model.json', 'pass'),
                                               ('replay step 3: write replies.json', 'pass')])
        self.assertEqual(checks('probe')[:2], [('replay step 1: emit_package', 'fail'), ('replay step 2: interface_execute', 'fail')])
        self.assertEqual((workers['interpret']['status'], workers['probe']['status']), ('failed', 'failed'))
        # The sealed task contract stayed the configurator's: interpretation handed on the model behind an intact seal.
        interpret = next(h for h in result['accepted_handoffs'] if h['stage'] == 'interpret')
        self.assertIn({'name': 'sealed_inputs', 'passed': True}, interpret['checks'])
        # The unlisted tool never reached the configurator; the absent operation did, and the configurator refused it
        # before dispatch and recorded the refusal for probe's notes.
        events = self.events(started['run_id'])
        probe = [(e['kind'], e['data']['name']) for e in events if e['kind'] in ('tool.started', 'tool.refused')
                 and e['data']['stage'] == 'probe']
        self.assertEqual(probe, [('tool.refused', 'interface_execute'), ('tool.started', 'interface_execute'),
                                 ('tool.started', 'probe_run')])
        notes = next(h for h in result['accepted_handoffs'] if h['stage'] == 'probe')['notes']['inline']
        self.assertEqual([r['name'] for r in notes['configurator']['tool_refusals']], ['interface_execute'])


class ReplayStartTests(unittest.TestCase):
    """A replay starts only from a checked saved candidate, and only on an emulated benchmark case."""

    def setUp(self):
        from generative_driver.configurator import Controller
        from tq9_workflow_fixture import replay_candidate
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.candidate = replay_candidate(self.root / 'candidate')
        self.controller = Controller(self.root / 'home')
        self.addCleanup(self.controller.close)

    def variant(self, name, stages):
        directory = self.root / name
        shutil.copytree(self.candidate, directory)
        script = json.loads((directory / 'replay.json').read_text())
        (directory / 'replay.json').write_text(json.dumps({**script, 'stages': stages}))
        return str(directory)

    def test_a_replay_starts_only_from_a_checked_candidate_on_an_emulated_case(self):
        from generative_driver.toolkit import resources_root
        good = {'goal': 'g', 'case': 'tq9-v2', 'executor': 'replay', 'executor_config': {'candidate': str(self.candidate)},
                'effects': ['write', 'actuate'], 'case_options': {'scenario_id': 'original'}}
        refused = [
            ({'case': None}, 'emulated benchmark cases only'),
            ({'case': 'bme280'}, 'emulated benchmark cases only'),
            ({'executor_config': {}}, 'needs a saved candidate'),
            # A replay runs the installed replay worker only, never a command a request names.
            ({'executor_config': {'candidate': str(self.candidate), 'command': [sys.executable]}}, 'only its saved candidate'),
            ({'executor_config': {'candidate': str(self.candidate), 'candidate_sha256': '0' * 64}}, 'changed since'),
            ({'executor_config': {'candidate': 'candidate'}}, 'absolute directory'),
            ({'executor_config': {'candidate': str(self.root / 'absent')}}, 'absolute directory'),
            ({'executor_config': {'candidate': str(resources_root() / 'bench/cases/setup-smoke')}}, 'never the installed'),
            ({'executor_config': {'candidate': self.variant('unknown-stage', {'final': []})}}, 'stages name only'),
            ({'executor_config': {'candidate': self.variant('escape', {'interpret': [{'write': '../model.json'}]})}},
             'file of the candidate'),
            ({'executor_config': {'candidate': self.variant('missing', {'interpret': [{'write': 'absent.json'}]})}},
             'file of the candidate'),
            ({'executor_config': {'candidate': self.variant('no-arguments', {'probe': [{'call': 'probe_run'}]})}},
             'replay step is'),
            ({'executor_config': {'candidate': self.variant('placeholder', {'probe': [
                {'call': 'probe_run', 'arguments': {'model_dir': {'$context': ['model_dir']}}}]})}}, 'placeholder'),
            # Only interpretation and probe write their own outputs: every other stage acts through its tools, so a
            # replay never places a package or a handoff behind the gateway.
            ({'executor_config': {'candidate': self.variant('emit-write', {'emit': [{'write': 'model.json'}]})}},
             'interpret and probe'),
            ({'executor_config': {'candidate': self.variant('reuse-write', {'reuse': [{'write': 'capabilities.json'}]})}},
             'interpret and probe'),
            # The bench home beside the configurator home holds evaluator records, never a candidate.
            ({'executor_config': {'candidate': self.variant('home-bench/candidate', {})}}, 'evaluator records')]
        for changes, message in refused:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.controller.start({key: value for key, value in {**good, **changes}.items() if value is not None})
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)

    def test_a_suite_never_freezes_the_replay_executor(self):
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.replay import candidate_config
        from suite_fixtures import pilot_manifest
        # A suite's identity names a model runtime; replays run as single trials, one case each.
        with self.assertRaisesRegex(ValueError, 'benchmark run --executor replay'):
            freeze_suite(pilot_manifest(), 'replay', candidate_config({'candidate': str(self.candidate)}), {}, {},
                         harness_token_sha256='a' * 64)


class ReplayTrialTests(unittest.TestCase):
    """`benchmark run --executor replay` through a real detached bench harness, which admits the case, provides the
    device, grades the frozen driver once after the run and seals the evidence, as it does for a model run."""

    def test_a_replay_trial_is_graded_labelled_replay_and_never_compared_with_a_model_run(self):
        from generative_driver.benchmark import compare, run, score
        from generative_driver.benchmark_support.truth import seal
        from generative_driver.configurator import digest
        from generative_driver.reporting import report_run
        from suite_fixtures import public_case_fixture
        from test_device_provider import RECIPE
        from test_harness import HARNESS_FIXTURE
        from tq9_workflow_fixture import replay_candidate
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        candidate = replay_candidate(root / 'candidate')
        resources, home = root / 'resources', root / 'home'
        manifest = public_case_fixture(resources, calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
        password = root / 'password-handle'
        password.write_text('synthetic fixture credential')
        # The final phase the harness grades the frozen driver with after the run, on a fresh emulator of its own.
        final = {'temperature_vectors': [{'stimulus': v, 'expected': v, 'absolute_tolerance': .01} for v in (-7, 29)],
                 'effect_vectors': [{'task': 'arm', 'inputs': {}}, {'task': 'set_duty', 'inputs': {'duty': 400}},
                                    {'task': 'disarm', 'inputs': {}}]}
        truth = {'private_sentinel': 'EVALUATOR SENTINEL', 'images': manifest['images'],
                 'recipe': {'stimuli': {'temperature': {'minimum': -100, 'maximum': 100, 'command': 'temp {value}'}},
                            **RECIPE, 'reset_commands': ['reset']},
                 'contracts': {'units': {'temperature': 'degC', 'duty': 'permille'},
                               'scenarios': {'original': {'phases': {'final': final}}},
                               'time_policy': {'sample_settle_seconds': 0}}}
        manifest['truth']['sha256'] = seal(truth, resources / manifest['truth']['path'], password.read_text())
        (resources / 'cases/tq9-v2/case.json').write_text(json.dumps(manifest))
        fixture = root / 'harness_fixture.py'
        fixture.write_text(HARNESS_FIXTURE.replace('TESTS', repr(str(Path(__file__).resolve().parent)))
                           .replace('PRIVATE', repr(str(root / 'native'))).replace('RESOURCES', repr(str(resources)))
                           .replace('EVIDENCE', repr(EVIDENCE)))
        for target, value in (('generative_driver.benchmark.case_root', {'return_value': resources}),
                              ('generative_driver.benchmark_support.registry.require_calibration',
                               {'side_effect': AssertionError('the configurator admitted the case')}),
                              ('generative_driver.benchmark_support.emulator.truth_for_case',
                               {'side_effect': AssertionError('the configurator unlocked the case truth')})):
            guard = patch(target, **value)
            guard.start()
            self.addCleanup(guard.stop)
        call = serve(self, home)
        options = {'home': str(home), 'evaluator_password_file': str(password), 'effects': ['write', 'actuate'],
                   'renode': str(root / 'renode-executable-handle'), 'budget_seconds': TIMEOUT, 'scenario_id': 'original',
                   'replay_candidate': str(candidate)}
        with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
            launched = run('tq9-v2', executor='replay', wait=True, options=options)
        trial = Path(launched['trial_dir'])
        log = (trial / 'harness.log').read_text(errors='replace') if (trial / 'harness.log').is_file() else ''
        self.assertEqual(launched['status'], 'evaluated', (launched, log))
        # The harness graded the replayed driver once, as it grades a model's.
        result = call('result', {'run_id': launched['run_id']})
        self.assertEqual((result['equivalence']['equivalent'], result['equivalence']['basis']), (True, 'evaluator'), result)
        # Its report says replay, never a model run, and names the candidate by its hash alone.
        report = report_run(launched['run_id'], home=home, output=root / 'report/report.json', autostart=False)
        self.assertEqual((report['schema'], report['verdict'], report['execution'], report['model_benchmark']),
                         ('benchmark-report/3', 'equivalent', 'replay-emulation', False))
        self.assertEqual(report['executed']['runtime_configuration'], {'runtime': 'replay', 'candidate_sha256': digest(candidate)})
        self.assertIn('Execution: `replay-emulation`', (root / 'report/report.md').read_text())
        # Scoring regrades it from the sealed evidence; the replay label comes from the sealed snapshot, so a replay
        # relabelled as a model run no longer matches it.
        evidence = trial / 'run-evidence.enc'
        self.assertEqual(score(report, str(password), evidence_path=evidence)['verdict'], 'equivalent')
        relabelled = {**report, 'execution': 'actual-agent-emulation', 'model_benchmark': True}
        with self.assertRaisesRegex(ValueError, 'execution identity'):
            score(relabelled, str(password), evidence_path=evidence)
        unlabelled = {key: value for key, value in report.items() if key not in ('execution', 'model_benchmark')}
        regraded = score(unlabelled, str(password), evidence_path=evidence)
        self.assertEqual((regraded['verdict'], regraded['execution'], regraded['model_benchmark']),
                         ('equivalent', 'replay-emulation', False))
        # A replay and a model run are separate series: never compared, while two replays compare as usual.
        model_run = {**relabelled, 'executed': {**report['executed'], 'runtime_configuration': {'runtime': 'codex'}}}
        for before, after in ((report, model_run), (model_run, report)):
            compared = compare(before, after)
            self.assertEqual((compared['compatible'], compared['incompatible_fields']), (False, ['execution']), compared)
            self.assertIn('separate series', compared['reason'])
        self.assertEqual((compare(report, report)['compatible'], compare(report, report)['comparison_kind']), (True, 'repeat'))


# Tests only: a stage gateway that lists probe_run and records the arguments of every call.
RECORDING_GATEWAY = '''
import asyncio, json, sys
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
async def tools(context, params):
    return ListToolsResult(tools=[Tool(name='probe_run', description='Records its arguments', inputSchema={'type': 'object'})])
async def invoke(context, params):
    with open(sys.argv[1], 'a', encoding='utf-8') as stream:
        stream.write(json.dumps({'name': params.name, 'arguments': params.arguments}, ensure_ascii=False) + '\\n')
    return CallToolResult(content=[TextContent(type='text', text=json.dumps({'ok': True}))])
server = Server('recording-stage', on_list_tools=tools, on_call_tool=invoke)
async def run():
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())
asyncio.run(run())
'''


class ReplayWorkerTests(unittest.TestCase):
    def test_the_worker_reads_its_prompt_as_utf8_whatever_the_stdin_encoding(self):
        import subprocess
        from generative_driver.configurator import digest
        from package_env import package_env
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            candidate, workspace = root / 'candidate', root / 'workspace'
            candidate.mkdir()
            workspace.mkdir()
            (candidate / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {'probe': [
                {'call': 'probe_run', 'arguments': {'model_dir': {'$context': 'model_dir'}}}]}}))
            (root / 'gateway.py').write_text(RECORDING_GATEWAY)
            # A context path with non-ASCII characters, as under a Windows profile named Zoë Ålund. The configurator
            # writes the prompt as UTF-8; Windows decodes a pipe with its ANSI code page, for which latin-1 stands in.
            model_dir = str(workspace / 'Zoë Ålund' / 'model')
            prompt = 'Execute exactly the assigned stage.\n' + json.dumps({'stage': 'probe', 'context': {'model_dir': model_dir}},
                                                                          ensure_ascii=False)
            completed = subprocess.run(
                [sys.executable, '-m', 'generative_driver.replay', '--candidate', str(candidate),
                 '--candidate-sha256', digest(candidate), '--stage', 'probe',
                 '--gateway', json.dumps({'command': sys.executable, 'args': [str(root / 'gateway.py'), str(root / 'calls.jsonl')]})],
                input=prompt.encode('utf-8'), cwd=workspace, capture_output=True, timeout=120,
                env={**os.environ, **package_env(PYTHONIOENCODING='latin-1')})
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
            # Split on newlines only: misdecoded text may hold other line separators (U+0085).
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text(encoding='utf-8').split('\n') if line]
            self.assertEqual(calls, [{'name': 'probe_run', 'arguments': {'model_dir': model_dir}}])


class ReplayCommandTests(unittest.TestCase):
    def test_only_the_replay_executor_takes_a_saved_candidate_and_only_for_an_emulated_case(self):
        import contextlib
        import io
        from generative_driver.benchmark import main
        from tq9_workflow_fixture import replay_candidate
        # A refusal that does not happen fails here instead of starting a real bench harness and configurator.
        for name in ('_spawn', '_spawn_via_owner'):
            guard = patch('generative_driver.benchmark_support.harness.' + name,
                          side_effect=AssertionError('a bench harness started'))
            guard.start()
            self.addCleanup(guard.stop)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            candidate = str(replay_candidate(root / 'candidate'))
            for argv, message in ((['--case', 'tq9-v2', '--executor', 'replay'], 'saved candidate'),
                                  (['--case', 'tq9-v2', '--executor', 'codex', '--replay-candidate', candidate],
                                   'replay executor'),
                                  (['--case', 'bme280', '--executor', 'replay', '--replay-candidate', candidate],
                                   'emulated benchmark cases only'),
                                  (['--case', 'setup-smoke', '--executor', 'replay', '--replay-candidate', candidate],
                                   'emulated benchmark cases only')):
                with self.subTest(argv=argv):
                    printed = io.StringIO()
                    with contextlib.redirect_stdout(printed):
                        code = main(['run', '--home', str(root / 'home'), *argv])
                    self.assertEqual(code, 1, printed.getvalue())
                    self.assertIn(message, json.loads(printed.getvalue())['error'])
            # Each was refused before any bench harness, trial or configurator started.
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['candidate'])
            # So is a candidate in the bench home, where the harness keeps evaluator records: no trial starts.
            inside = replay_candidate(root / 'home-bench/candidate')
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                code = main(['run', '--home', str(root / 'home'), '--case', 'tq9-v2', '--executor', 'replay',
                             '--replay-candidate', str(inside)])
            self.assertEqual(code, 1, printed.getvalue())
            self.assertIn('evaluator records', json.loads(printed.getvalue())['error'])
            self.assertEqual(sorted(path.name for path in (root / 'home-bench').iterdir()), ['candidate'])
            # On a case-insensitive file system another spelling names the same bench home, and is refused as it is.
            spelled = root / 'HOME-BENCH' / 'candidate'
            if spelled.is_dir():
                printed = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    code = main(['run', '--home', str(root / 'home'), '--case', 'tq9-v2', '--executor', 'replay',
                                 '--replay-candidate', str(spelled)])
                self.assertEqual(code, 1, printed.getvalue())
                self.assertIn('evaluator records', json.loads(printed.getvalue())['error'])


if __name__ == '__main__':
    unittest.main()

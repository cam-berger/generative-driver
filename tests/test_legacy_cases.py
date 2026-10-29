"""Legacy tq9 and bme280 under the assistive split. Their stages are observed without truth; the bench harness provides
the legacy tq9 emulator from the legacy recipe, leaves the operator's bound bme280 alone, and grades the recorded fresh
reuse once after the run (`recorded-reuse`). Synthetic TCP peers, scripted stages, a toy truth and a synthetic
credential only; never Renode, a real password or a physical device."""
import hashlib
import importlib.util
import inspect
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_configurator import WORKFLOW_TIMEOUT, context_of, gateway_identity, scripted_stages, task_of, wait_stopped
from test_device_provider import MONITOR, keys
from test_device_recovery import FORBIDDEN, imported
from test_harness import SOURCE
from tq9_workflow_fixture import Silicon, legacy_capabilities, legacy_truth, model as tq9_model, replies

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
REUSE_CHECKS = ['fresh_package_temperature', 'fresh_package_requested_output', 'fresh_package_disarm']


def silicon_start(test, peers, root):
    """NativeSession.start for a test: a Silicon peer per session, alive until the peer closes."""
    def start(**kwargs):
        peer = Silicon(kwargs['image']); peers.append(peer); test.addCleanup(peer.close)
        session = peer.session(kwargs['recipe'], Path(kwargs.get('private_dir') or Path(root) / ('native-' + str(len(peers)))))
        class Process:
            def poll(self):
                return 0 if getattr(peer, 'closed', False) else None
        session.process = Process()
        return session
    return start


def home_files(home):
    return [path for path in Path(home).rglob('*') if path.is_file()]


class LegacyDeviceTests(unittest.TestCase):
    def test_the_harness_projects_only_the_device_recipe_of_the_legacy_truth(self):
        # Catches a legacy emulator started from the whole truth, or a provider whose readings carry monitor commands,
        # register addresses or raw reads.
        from generative_driver.benchmark_support import harness
        from generative_driver.benchmark_support.registry import pin_case
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); trial = root / 'trial'; peers = []
            password = root / 'password'; password.write_text('synthetic fixture credential')
            with patch('generative_driver.benchmark_support.evaluator.case_truth', return_value=legacy_truth()), \
                 patch('generative_driver.benchmark_support.native.NativeSession.start', side_effect=silicon_start(self, peers, root)):
                recipe = harness.device_recipe('tq9', trial, {'evaluator_password_file': str(password)})
                self.assertEqual(recipe, {'source_files': {'sensor.cs': '// toy sensor model', 'device.resc': '# toy setup'},
                    'includes': ['sensor.cs'], 'setup': 'device.resc', 'observations': {
                        'compare': {'command': 'sysbus ReadDoubleWord 0x40000034', 'width': 32, 'count': 1},
                        'reload': {'command': 'sysbus ReadDoubleWord 0x4000002c', 'width': 32, 'count': 1}}})
                for secret in ('reuse_goal', 'checks', 'monitor', 'SENTINEL', 'pwm_'):
                    self.assertNotIn(secret, json.dumps(recipe))
                # A provider takes the recipe only: the legacy truth itself is refused.
                from generative_driver.benchmark_support.device_provider import DeviceProvider
                with self.assertRaisesRegex(ValueError, 'recipe only'):
                    DeviceProvider('tq9', root / 'image', legacy_truth(), None, root / 'device')
                provider, admission = harness.admit('tq9', {}, trial, password_file=password, renode=str(root / 'renode'))
                self.assertEqual(admission, {})
                descriptor = provider.start()
                self.assertEqual((descriptor['kind'], descriptor['instrument']['units']), ('provided-emulator', {'duty': 'fraction'}))
                self.assertEqual(descriptor['image_sha256'], pin_case('tq9', None, 0)['image_hashes']['firmware.bin'])
                peers[0].duty = 370
                observed = provider.observe()
                self.assertEqual((observed['schema'], observed['values'], observed['units']),
                                 ('instrument-observation/1', {'duty': .37}, {'duty': 'fraction'}))
                self.assertFalse(keys(observed) & MONITOR)
                # The legacy device is the harness's: its stop is confirmed through the process handle.
                self.assertEqual(provider.stop(), {'stopped': True, 'confirmed': True})
                self.assertTrue(peers[0].closed)


class LegacyTrialTests(unittest.TestCase):
    """A legacy tq9 trial the bench harness runs in this process: it provides the legacy emulator, the configurator runs
    scripted stages on it with the real stage preparations and observations, and the harness grades the recorded fresh
    reuse once after the run."""

    def setUp(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.password = self.root / 'password-handle'
        self.password.write_text('synthetic fixture credential')
        self.home = self.root / 'home'; self.home.mkdir(mode=0o700)
        (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': {'command': ['scripted-runtime']}}}))
        self.peers, self.unlocked = [], []
        def case_truth(case_id, where, options):
            # The harness unlocks the truth to project the recipe and, once the run has ended, to grade.
            caller = {frame.function for frame in inspect.stack()}
            run = self.run_id and self.controller.call('status', {'run_id': self.run_id})
            self.unlocked.append((case_id, 'device_recipe' in caller, 'grade' in caller, run and run['status']))
            return legacy_truth()
        for target, effect in (('generative_driver.benchmark_support.evaluator.case_truth', case_truth),
                               ('generative_driver.benchmark_support.native.NativeSession.start', silicon_start(self, self.peers, self.root)),
                               # Nothing in the configurator or a stage unlocks the case truth.
                               ('generative_driver.benchmark_support.emulator.truth_for_case', AssertionError('truth unlocked in the run'))):
            guard = patch(target, side_effect=effect); guard.start(); self.addCleanup(guard.stop)
        self.run_id = None
        self.controller = Controller(self.home)
        self.addCleanup(lambda: self.controller.close())

    def configurator(self, method, params=None):
        if method == 'ping':
            return {'ok': True}
        if method == 'start':
            reply = self.controller.call(method, params or {})
            self.run_id = reply.get('run_id')
            return reply
        return self.controller.call(method, params or {})

    def tool(self, request, name, arguments):
        return self.controller.call('tool', {**gateway_identity(request), 'name': name, 'arguments': arguments})

    def trial(self, *, duty=370, disarm=True):
        """Run one trial with scripted workers; the reuse worker requests `duty` permille and disarms when `disarm`."""
        from generative_driver.benchmark_support import harness
        real_check, self.objectives = self.controller._check, {}
        def work(request):
            if request.stage == 'interpret':
                (Path(request.workspace) / 'model.json').write_text(json.dumps(tq9_model()))
                (Path(request.workspace) / 'replies.json').write_text(json.dumps(replies()))
                return
            task = task_of(request.prompt)
            self.objectives[request.stage] = task.get('objective')
            if request.stage == 'acquire':
                self.tool(request, 'acquire_firmware_artifact', task['context'])
            elif request.stage == 'probe':
                model_dir = task['context']['model_dir']
                self.assertTrue(self.tool(request, 'interface_execute', {'model_dir': model_dir, 'operation': 'measure', 'parameters': {}})['ok'])
                self.assertTrue(self.tool(request, 'probe_run', {'model_dir': model_dir, 'operation': 'measure', 'parameters': {}, 'n': 1})['ok'])
                (Path(request.workspace) / 'capabilities.json').write_text(json.dumps(legacy_capabilities()))
            elif request.stage == 'emit':
                self.assertTrue(self.tool(request, 'emit_package', task['context'])['ok'])
            elif request.stage == 'reuse':
                # Legacy case grading still uses recorded reuse, but this newly emitted package
                # is format 2: the fresh worker uses its actual assigned generated MCP gateway.
                import asyncio
                from mcp import ClientSession, StdioServerParameters
                from mcp.client.stdio import stdio_client
                from package_env import package_env
                async def reuse():
                    gateway = request.gateway
                    async with stdio_client(StdioServerParameters(command=gateway['command'],
                            args=gateway['args'], env=package_env())) as streams:
                        async with ClientSession(*streams) as client:
                            await client.initialize()
                            self.assertEqual({tool.name for tool in (await client.list_tools()).tools},
                                             set(task['allowed_tools']))
                            for operation, parameters in (('measure', {}), ('arm', {}), ('set_duty', {'duty': duty}),
                                                          *((('disarm', {}),) if disarm else ())):
                                result = await client.call_tool('device_'+operation, {**parameters,
                                    '_request_id': gateway_identity(request)['assignment_id']+':'+operation})
                                self.assertTrue(result.structured_content['ok'], result)
                asyncio.run(reuse())
        request = {'trial_id': 'trial-legacy', 'home': str(self.home), 'goal': 'Legacy scripted contract', 'case': 'tq9',
                   'executor': 'codex', 'budget_seconds': 120, 'effects': ['write', 'actuate'], 'case_options': {},
                   'scoped_tool_approval': 'emulator', 'password_file': str(self.password), 'renode': str(self.root / 'renode')}
        from owner_ipc_fixture import owner_ipc
        with owner_ipc(self.controller), \
                scripted_stages(self.controller, check=real_check, prepare=lambda original, *args: original(*args), work=work):
            record = harness.run_trial(self.root / 'trial', request, call=self.configurator)
        return record, self.controller.call('result', {'run_id': record['run_id']})

    def test_a_legacy_trial_runs_on_the_harness_device_and_is_graded_from_its_recorded_reuse_once_after_the_run(self):
        # Catches a legacy run that unlocks truth or grades inside its stages, stops on a quality judgement, or is
        # never graded; and a grade that does not come from the recorded fresh reuse of the accepted package.
        from generative_driver.benchmark_support.truth import unlock
        record, result = self.trial()
        self.assertEqual(record['status'], 'evaluated', record)
        # The configurator validates the assigned model on its own copy; the probe worker gets no validator that may
        # write beside the model it checks.
        assigned = [e['data'] for e in self.controller.call('events', {'run_id': record['run_id']})['events'] if e['kind'] == 'stage.assigned']
        self.assertNotIn('model_validate', next(a for a in assigned if a['stage'] == 'probe')['allowed_tools'])
        equivalence = result['equivalence']
        self.assertEqual(record['equivalence'], equivalence)
        self.assertEqual((equivalence['status'], equivalence['equivalent'], equivalence['basis'], equivalence['method'],
                          equivalence['protocol']), ('graded', True, 'evaluator', 'recorded-reuse', 'assistive/1'))
        self.assertEqual([check['id'] for check in equivalence['checks']],
                         REUSE_CHECKS + ['generated_mcp_reuse', 'fresh_worker_mission'])
        self.assertEqual((result['status'], result['reason']), ('completed', 'Final verdict: equivalent'))
        # The probe worker is told the provided emulator's time policy, not the manifest's continuous virtual time.
        self.assertIn('paused between calls', self.objectives['probe'])
        self.assertEqual((result['progress']['equivalence'], result['evaluator_verdicts']), ('graded', []))
        self.assertNotIn('terminal_final_failure', result['progress'])
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']], list(STAGES))
        emitted = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
        self.assertEqual(equivalence['frozen_artifact_sha256'], emitted['artifacts'][0]['sha256'])
        # The truth was unlocked twice, by the harness alone: to project the device recipe before the run, and to grade
        # once the run had completed.
        self.assertEqual([(case, recipe, grading) for case, recipe, grading, _ in self.unlocked],
                         [('tq9', True, False), ('tq9', False, True)])
        self.assertEqual(self.unlocked[1][3], 'completed')
        # Its stages are observed: no stage verdict and no graded check anywhere in the run.
        state = json.loads((self.home / 'runs' / record['run_id'] / 'benchmark/state.json').read_text())
        self.assertNotIn('stage_verdicts', state)
        self.assertNotIn('probe_evaluation', state)
        # The device was the harness's, live calls carried its instrument readings, and the harness stopped it.
        self.assertEqual(record['device_stop'], {'stopped': True, 'confirmed': True})
        reuse = next(h for h in result['accepted_handoffs'] if h['stage'] == 'reuse')
        events = [json.loads(line) for line in Path(next(a['path'] for a in reuse['artifacts']
                                                         if a['path'].endswith('reuse-events.jsonl'))).read_text().splitlines()]
        self.assertEqual([e['operation'] for e in events], ['measure', 'arm', 'set_duty', 'disarm'])
        self.assertEqual([e['observation']['duty'] for e in events], [0, 0, .37, 0])
        self.assertEqual({e['observation']['source'] for e in events}, {'instrument'})
        # Nothing of the truth or the password reaches the configurator: not its spec, a handoff or any file.
        spec = self.controller._row(record['run_id'])['spec']
        for secret in ('password', str(self.password), str(self.root / 'renode')):
            self.assertNotIn(secret, spec)
        for path in home_files(self.home):
            content = path.read_bytes()
            for secret in (b'SENTINEL', b'reuse_goal', str(self.password).encode()):
                self.assertNotIn(secret, content, path)
        # The sealed evidence holds the single final, graded from the recorded reuse.
        sealed = unlock(self.root / 'trial/run-evidence.enc', self.password.read_text(), record['evidence']['sha256'])
        self.assertEqual((sealed['final']['method'], sealed['final']['phase']), ('recorded-reuse', 'final'))
        self.assertEqual([c['id'] for c in sealed['final']['contract']['checks']], REUSE_CHECKS)
        self.assertFalse((self.root / 'trial/private').exists())

    def test_a_legacy_driver_that_misses_the_requested_output_is_graded_not_equivalent(self):
        record, result = self.trial(duty=500, disarm=False)
        equivalence = result['equivalence']
        self.assertEqual((record['status'], equivalence['status'], equivalence['equivalent']), ('evaluated', 'graded', False))
        self.assertEqual([d['id'] for d in equivalence['diff']],
                         ['fresh_package_requested_output', 'fresh_package_disarm', 'fresh_worker_mission'])
        self.assertTrue(result['reason'].startswith('Final verdict: not equivalent'), result['reason'])
        for check in equivalence['checks'] + equivalence['diff']:
            self.assertNotIn('0.37', json.dumps(check))

    def test_the_recorded_verdict_regrades_offline_from_its_sealed_evidence(self):
        # Catches an assistive legacy run reported or scored under the earlier protocol's gates.
        from generative_driver.benchmark import score
        from generative_driver.reporting import report_run
        record, result = self.trial()
        with patch('generative_driver.client.call', side_effect=lambda method, params, **kwargs: self.controller.call(method, params)):
            report = report_run(record['run_id'], home=self.home, autostart=False)
        self.assertEqual((report['schema'], report['protocol'], report['verdict']), ('benchmark-report/3', 'assistive/1', 'equivalent'))
        regraded = score(report, str(self.password), evidence_path=self.root / 'trial/run-evidence.enc')
        self.assertEqual((regraded['verdict'], regraded['equivalence']['method']), ('equivalent', 'recorded-reuse'))

    def test_a_legacy_run_without_the_harness_device_stops_for_the_operator_and_takes_no_evaluator_options(self):
        # Catches the configurator starting or reaching a legacy emulator itself, or taking the evaluator password.
        secret = self.root / 'private-password-handle'
        for options in ({'evaluator_password_file': str(secret)}, {'renode': '/opt/renode'}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'password|emulator executable'):
                self.controller.start({'goal': 'g', 'case': 'tq9', 'case_options': options})
        def work(request):
            if request.stage == 'interpret':
                (Path(request.workspace) / 'model.json').write_text(json.dumps(tq9_model()))
            elif request.stage == 'acquire':
                self.tool(request, 'acquire_firmware_artifact', task_of(request.prompt)['context'])
        with scripted_stages(self.controller, check=self.controller._check, prepare=lambda original, *args: original(*args), work=work):
            run = self.controller.start({'goal': 'Legacy contract without a harness', 'case': 'tq9'})
            result = wait_stopped(self.controller, run)
        # The start says plainly, before any worker runs, that the run cannot pass probe without its bench harness.
        started = [e['data'] for e in self.controller.call('events', {'run_id': run['run_id']})['events'] if e['kind'] == 'run.started']
        self.assertIn('`benchmark run`', run['note'])
        self.assertEqual([event.get('note') for event in started], [run['note']])
        self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('blocked', 'operator', 'probe'))
        self.assertIn('bench harness', result['reason'])
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']], ['acquire', 'interpret'])
        self.assertFalse(self.peers)


class RecordedReuseGradeTests(unittest.TestCase):
    """The legacy tq9 grade of a recorded fresh reuse, as the in-loop legacy reuse check judged it before: an observed
    contradiction is the driver's whatever else failed, and evidence the host or the operator kept unavailable cannot
    grade it."""
    TRUTH = {'reuse_goal': {'fraction': .5, 'final_fraction': 0, 'tolerance': .01},
             'checks': [{'id': 'temperature', 'expected': 20, 'absolute_tolerance': 1}]}
    MAPPING = {'temperature': {'operation': 'read', 'output': 't'}}
    SHA = 'f' * 64

    def grade(self, events):
        from generative_driver.benchmark_support import evaluator
        checks, records, raw = evaluator._legacy_tq9(self.TRUTH, [dict(e, package_sha256=self.SHA) for e in events],
                                                     self.MAPPING, self.SHA, 0)
        return evaluator.final_record({'contract': {'schema': 'benchmark-behavior/1', 'artifact_sha256': self.SHA, 'checks': checks},
                                       'records': records, 'frozen_artifact_sha256': self.SHA, 'method': 'recorded-reuse'})

    def test_an_observed_contradiction_is_not_equivalent_whatever_else_failed(self):
        # A later unavailable call cannot erase an already observed contradiction.
        import itertools
        for (temperature, final_duty), later_fault, error_position in itertools.product(
                ((999, 0), (20, .5), (999, None), (None, .5)),
                ('no_failed_event', 'host', 'operator', None), ('before', 'after')):
            with self.subTest(temperature=temperature, final_duty=final_duty, later_fault=later_fault, error_position=error_position):
                events = [{'operation': 'read', 'result': {'ok': True, 'outputs': {'t': temperature}}, 'observation': {'duty': .5}},
                          {'operation': 'disarm', 'result': {'ok': True}, 'observation': {'duty': final_duty}}]
                if final_duty is None:
                    events = [dict(events[0], observation=None)]
                elif temperature is None:
                    events = [events[1]]
                if later_fault != 'no_failed_event':
                    failed = {'operation': 'read', 'result': {'ok': False, 'error': {'fault': later_fault, 'message': 'Unrelated call unavailable'}}}
                    events.insert(0 if error_position == 'before' else len(events), failed)
                record = self.grade(events)
                self.assertEqual((record['status'], record['equivalent'], record['method']), ('graded', False, 'recorded-reuse'))

    def test_evidence_the_host_or_operator_kept_unavailable_is_unscored(self):
        import itertools
        from generative_driver.benchmark_support.evaluator import Unscorable
        for fault, available in itertools.product(('host', 'operator', None), ('none', 'temperature', 'disarm')):
            with self.subTest(fault=fault, available=available):
                events = []
                if available == 'temperature':
                    events.append({'operation': 'read', 'result': {'ok': True, 'outputs': {'t': 20}}})
                elif available == 'disarm':
                    events.append({'operation': 'disarm', 'result': {'ok': True}, 'observation': {'duty': 0}})
                events.append({'operation': 'read', 'result': {'ok': False, 'error': {'fault': fault}}})
                if fault:
                    with self.assertRaisesRegex(Unscorable, 'failed for the ' + fault) as raised:
                        self.grade(events)
                    for secret in ('20', '0.5', '0.01'):
                        self.assertNotIn(secret, str(raised.exception))
                else:
                    # Nothing kept the evidence away: fresh reuse did not establish the functions with this driver.
                    self.assertFalse(self.grade(events)['equivalent'])

    def test_calls_of_another_package_never_grade_the_frozen_driver(self):
        # Catches a recorded call of another package standing for the frozen one's.
        events = [{'operation': 'read', 'result': {'ok': True, 'outputs': {'t': 20.4}}, 'observation': {'duty': 0}},
                  {'operation': 'disarm', 'result': {'ok': True}, 'observation': {'duty': 0}}]
        other = {'operation': 'set', 'result': {'ok': True}, 'observation': {'duty': .5}, 'package_sha256': 'e' * 64}
        from generative_driver.benchmark_support import evaluator
        checks, records, _ = evaluator._legacy_tq9(self.TRUTH, [dict(e, package_sha256=self.SHA) for e in events] + [other],
                                                   self.MAPPING, self.SHA, 0)
        record = evaluator.final_record({'contract': {'schema': 'benchmark-behavior/1', 'artifact_sha256': self.SHA,
                                                      'checks': checks}, 'records': records,
                                         'frozen_artifact_sha256': self.SHA, 'method': 'recorded-reuse'})
        self.assertEqual([d['id'] for d in record['diff']], ['fresh_package_requested_output'])

    def test_a_fully_observed_goal_is_equivalent(self):
        record = self.grade([{'operation': 'read', 'result': {'ok': True, 'outputs': {'t': 20.4}}, 'observation': {'duty': 0}},
                             {'operation': 'set', 'result': {'ok': True}, 'observation': {'duty': .505}},
                             {'operation': 'disarm', 'result': {'ok': True}, 'observation': {'duty': 0}}])
        self.assertEqual((record['equivalent'], [c['id'] for c in record['checks']]), (True, REUSE_CHECKS))


class ConcludeTests(unittest.TestCase):
    def test_recorded_evidence_that_cannot_grade_the_driver_is_posted_unscored_with_its_public_reason(self):
        # Catches recorded reuse evidence the evaluator cannot grade (an operator reference outside its limits, calls
        # the host or the operator failed) becoming a grade, or an unscored record that hides why.
        from generative_driver.benchmark_support import evaluator, harness
        reason = 'The operator reference cannot grade the fresh measurement: Reference channel/timestamp missing or stale'
        posted = []
        def call(method, params):
            posted.append((method, params)); return {'ok': True}
        result = {'run_id': 'run-recorded', 'status': 'completed', 'progress': {'equivalence': 'pending'},
                  'accepted_handoffs': [{'stage': 'emit', 'revision': 0, 'artifacts': [{'path': 'package', 'sha256': 'a' * 64}]}]}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(evaluator, 'grade', side_effect=evaluator.Unscorable(reason)), \
             patch.object(evaluator, 'seal', return_value={'path': 'run-evidence.enc', 'sha256': 'e' * 64}) as seal:
            done = harness.conclude(Path(temporary), 'bme280', Path(temporary) / 'run', result, password_file='unused',
                                    token='token', call=call)
        self.assertEqual(done['status'], 'unscored')
        self.assertEqual((done['equivalence']['status'], done['equivalence']['method'], done['equivalence']['diff']),
                         ('unscored', 'recorded-reuse', [{'id': 'evaluation', 'reason': reason}]))
        self.assertEqual(seal.call_args.kwargs['error'], reason)
        self.assertEqual([(method, params['equivalence']) for method, params in posted], [('record_evaluation', done['equivalence'])])


BINDING = {'url': 'ftdi://scripted/1'}
PHYSICAL_MAPPING = {'temperature': 't', 'humidity': 'h', 'pressure': 'p'}
PHYSICAL_TRUTH = {'max_reference_age_seconds': 60, 'max_tolerances': {'temperature': .2, 'humidity': 1, 'pressure': 10}}
UNHELD = {'confirmed': True, 'held': False}


class HarnessClock:
    """Control harness deadlines without advancing controller or reference clocks."""
    offset = 0
    def monotonic(self): return time.monotonic() + self.offset
    def time(self): return time.time() + self.offset
    def sleep(self, seconds): time.sleep(seconds)


def blocked_once(stage):
    """A scripted runtime whose first `stage` worker finds no runtime: an operator stop, which the operator resumes."""
    attempts = []
    def runtime(request):
        attempts.append(request.stage)
        if request.stage == stage and attempts.count(stage) == 1:
            return {'runtime': 'codex', 'status': 'blocked', 'cause': 'launch', 'reason': 'Cannot start configured runtime',
                    'elapsed_seconds': 0, 'usage': None, 'report': None}
    return runtime, attempts


class BoundDeviceFixture(unittest.TestCase):
    """bme280 runs on the in-process configurator under the operator's binding. The board is never reached: a
    measurement is scripted, and the truth the bench harness unlocks is a toy one."""

    def setUp(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.password = self.root / 'password'
        self.password.write_text('synthetic fixture credential')
        self.home = self.root / 'home'; self.home.mkdir(mode=0o700)
        (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': {'command': ['scripted-runtime']}}}))
        self.controller = Controller(self.home)
        self.addCleanup(self.controller.close)
        self.started, self.unlocked = {}, []

    def configurator(self, method, params=None):
        if method == 'ping':
            return {'ok': True}
        reply = self.controller.call(method, params or {})
        if method == 'start':
            self.started.update(reply)
        return reply

    def request(self, trial_id, **changes):
        return {'trial_id': trial_id, 'home': str(self.home), 'goal': 'Physical scripted contract', 'case': 'bme280',
                'executor': 'codex', 'budget_seconds': 120, 'effects': ['write'], 'case_options': {}, 'binding': BINDING,
                'scoped_tool_approval': 'bound-device', 'password_file': str(self.password), **changes}

    def tool(self, request, name, arguments):
        return self.controller.call('tool', {**gateway_identity(request), 'name': name, 'arguments': arguments})

    def status(self):
        run_id = self.started.get('run_id')
        return self.controller.call('status', {'run_id': run_id}) if run_id else None

    def guarded(self):
        """Only the bench harness unlocks the (toy) truth, through the evaluator, and nothing in the run does; every
        unlock is recorded with whether the harness admitted or graded and the run's status then."""
        from contextlib import ExitStack
        def case_truth(case_id, where, options):
            callers = {frame.function for frame in inspect.stack()}
            run = self.status()
            self.unlocked.append(('admit' in callers, 'grade' in callers, run and run['status']))
            if Path(options['evaluator_password_file']).read_text() != self.password.read_text():
                raise ValueError('The evaluator password does not unlock the case truth')
            return json.loads(json.dumps(PHYSICAL_TRUTH))
        stack = ExitStack()
        stack.enter_context(patch('generative_driver.benchmark_support.evaluator.case_truth', side_effect=case_truth))
        stack.enter_context(patch('generative_driver.benchmark_support.emulator.truth_for_case',
                                  side_effect=AssertionError('truth unlocked in the run')))
        return stack


class PhysicalTrialTests(BoundDeviceFixture):
    """A bme280 trial: the bench harness provides no device and never touches the operator's board. It authenticates the
    evaluator password before the run starts, follows the run while the operator answers and resumes it, an extended
    budget included, and grades it once the run has completed; `trial-resume` grades a run that completed after its
    harness stopped following it."""

    def test_a_wrong_evaluator_password_is_refused_before_the_bound_device_run_starts(self):
        # Catches a bme280 trial that first meets its password when it grades: a whole physical run left ungradeable,
        # whose token no password both unlocks and grades with.
        from generative_driver.benchmark_support import harness
        from test_benchmark_physical import reference_fixture
        resources, _, secret = reference_fixture(self.root / 'sealed')
        wrong = self.root / 'wrong-password'; wrong.write_text('another synthetic credential')
        calls = []
        def configurator(method, params=None):
            calls.append(method)
            # A start is refused here, so a trial the harness let through ends at once instead of running.
            return {'ok': True} if method == 'ping' else {'ok': False, 'reason': 'The run must not start'}
        with patch('generative_driver.benchmark.case_root', return_value=resources):
            record = harness.run_trial(self.root / 'refused', self.request('trial-refused', password_file=str(wrong)),
                                       call=configurator)
            self.assertEqual((record['status'], record['run_id'], calls), ('failed', None, ['ping']), record)
            self.assertFalse((self.root / 'refused' / harness.TOKEN).exists())
            # The password the truth is sealed under admits the case; the operator's board gets no provider.
            self.assertEqual(harness.admit('bme280', {}, self.root / 'admitted', password_file=secret['evaluator_password_file']),
                             (None, {}))

    def test_a_bound_device_trial_is_graded_after_its_run_without_the_harness_touching_the_board(self):
        from generative_driver.benchmark_support import harness
        runtime, attempts = blocked_once('acquire')
        def resume_when_blocked():
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = self.status()
                if status and status['status'] == 'blocked':
                    # Long enough for the harness, which reads the run's status every quarter second, to see the stop.
                    time.sleep(1.5)
                    self.controller.call('resume', {'run_id': status['run_id']}); return
                time.sleep(.02)
        import threading
        operator = threading.Thread(target=resume_when_blocked, daemon=True)
        with patch('generative_driver.benchmark_support.physical.package_execute', side_effect=AssertionError('board touched')), \
             self.guarded(), \
             scripted_stages(self.controller, check=self.controller._check, prepare=lambda original, *args: original(*args),
                             runtime=runtime):
            operator.start()
            record = harness.run_trial(self.root / 'trial', self.request('trial-physical'), call=self.configurator)
        operator.join(5)
        result = self.controller.call('result', {'run_id': record['run_id']})
        # No datasheet was acquired, so no driver was emitted: a recorded no-driver verdict, never a stop.
        self.assertEqual((record['status'], result['status']), ('evaluated', 'completed'), (record, result['reason']))
        self.assertEqual((result['equivalence']['status'], result['equivalence']['equivalent'], result['equivalence']['method']),
                         ('no-driver', False, 'recorded-reuse'))
        self.assertEqual(attempts.count('acquire'), 2)
        self.assertEqual(record['device_stop'], UNHELD)
        # The harness unlocked the truth once, to authenticate the password before the run started; no driver needs it.
        self.assertEqual(self.unlocked, [(True, False, None)])
        spec = self.controller._row(record['run_id'])['spec']
        self.assertNotIn('device', json.loads(spec))
        self.assertNotIn('password', spec.lower())

    def test_a_graded_bound_device_trial_follows_its_run_through_an_extended_budget(self):
        # Catches a harness that stops following a bound-device run at the end of the budget it was started with although
        # the operator extended it, a trial record a dead harness would leave unresumable, and the bme280 path broken
        # anywhere from the emitted package to the posted verdict: the reuse reference request, the operator's answer
        # and resume, the package's measurement, the reference and evidence handoff, the recorded-reuse grade, the seal
        # and record_evaluation.
        from generative_driver import toolkit
        from generative_driver.benchmark_support import evaluator, harness
        evidence = self.root / 'reference.txt'
        evidence.write_text('Scripted independent reference; no physical observation claimed.')
        reference = {'stage': 'reuse', 'channel': 'scripted independent meter', 'evidence_path': str(evidence),
                     'reference': {'temperature': {'value': 21.5, 'absolute_tolerance': .2},
                                   'humidity': {'value': 40, 'absolute_tolerance': 1},
                                   'pressure': {'value': 101325, 'absolute_tolerance': 10}}}
        import threading
        # Advance only the harness's clock after the operator has extended the
        # run. Its original deadline expires deterministically, independently of
        # how long six persisted stages take on a Windows runner.
        clock = HarnessClock()
        budget, extended_budget, began = 120, 360, clock.monotonic()
        advanced, renewed, stop = threading.Event(), threading.Event(), threading.Event()
        real_check, real_call = self.controller._check, toolkit.call_tool
        boards, calls, seen = [], [], []
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            # The real preparations from emit on; the earlier stages are scripted.
            return (original(spec, stage, run_dir, workspace, accepted) if stage in ('emit', 'reuse')
                    else {'inputs': [], 'allowed_tools': []})
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'probe':
                folder = run_dir / 'scripted-probe'; folder.mkdir()
                for name, content in (('model.json', '{}'), ('convert.py', '# scripted converter\n'),
                                      ('capabilities.json', json.dumps(PHYSICAL_MAPPING))):
                    (folder / name).write_text(content)
                return {'artifacts': [str(folder / n) for n in ('model.json', 'convert.py', 'capabilities.json')], 'checks': []}
            if stage in ('emit', 'reuse'):
                return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
            return {'artifacts': [], 'checks': []}
        def work(request):
            if request.stage == 'emit':
                # The package is built with emit_package through the configurator, which records its hash.
                self.tool(request, 'emit_package', {'model_dir': context_of(request.prompt)['model_dir']})
            if request.stage == 'reuse':
                # Wait until the harness has read the extended budget after its
                # original deadline, then measure after that deadline on its clock.
                self.assertTrue(renewed.wait(WORKFLOW_TIMEOUT), 'Harness never followed the extended budget')
                calls.append(self.tool(request, 'benchmark_package_execute',
                                          {'package_dir': context_of(request.prompt)['package_dir'], 'operation': 'measure'}))
        def call_tool(name, arguments):
            # emit_package builds a scripted package and the package check passes; everything else is the real toolkit.
            if name == 'emit_package':
                package = Path(arguments['run_dir']) / 'package'; (package / 'driver').mkdir(parents=True)
                (package / 'manifest.json').write_text(json.dumps({'name': 'scripted'}))
                (package / 'driver/model.json').write_text(json.dumps({'schema': 'interface-model/1',
                                                                        'operations': {'measure': [{'op': 'reg_write'}]}}))
                return {'ok': True, 'package_dir': str(package)}
            if name == 'emit_check':
                return {'ok': True, 'integrity_ok': True, 'runtime_current': True, 'seeds_run': 1}
            return real_call(name, arguments)
        def measure(package, binding):
            boards.append((binding, clock.monotonic()))
            return 0, json.dumps({'status': 'OK', 'values': {'t': 21.5, 'h': 40, 'p': 101325}}), ''
        def operator():
            # The operator answers the reuse reference request and resumes, extending the run's budget.
            answered, until = False, time.monotonic() + WORKFLOW_TIMEOUT
            while time.monotonic() < until and not stop.is_set():
                status = self.status()
                if status and status['status'] in ('completed', 'cancelled'):
                    return
                if status and status['status'] == 'blocked' and not status['stopping']:
                    seen.append((status['stage'], status['reason'], harness.read_trial(self.root / 'trial')))
                    if status['stage'] == 'reuse' and not answered:
                        self.controller.call('respond', {'run_id': status['run_id'],
                                                         'observation': {**reference, 'observed_at': time.time()}})
                        answered = True
                    try:
                        self.controller.call('resume', {'run_id': status['run_id'], 'budget_seconds': extended_budget,
                                                        'budget_reason': 'The operator supplied the reuse reference'})
                        if answered:
                            clock.offset = budget + 1
                            advanced.set()
                    except ValueError:
                        pass
                time.sleep(.02)
            stop.set()
        def configurator(method, params):
            result = self.configurator(method, params)
            if method == 'result' and advanced.is_set() and result.get('budget_seconds') == extended_budget:
                renewed.set()
            return result
        thread = threading.Thread(target=operator, daemon=True)
        with self.guarded(), patch.object(harness, 'GRACE', 0), patch.object(harness, 'time', clock), \
             patch('generative_driver.toolkit.call_tool', side_effect=call_tool), \
             patch('generative_driver.benchmark_support.physical._measure', side_effect=measure), \
             scripted_stages(self.controller, check=check, prepare=prepare, work=work):
            thread.start()
            try:
                record = harness.run_trial(self.root / 'trial', self.request('trial-graded', budget_seconds=budget),
                                           call=configurator, stop=stop)
            finally:
                stop.set(); renewed.set()
                thread.join(WORKFLOW_TIMEOUT)
                self.assertFalse(thread.is_alive(), 'Fixture operator did not stop')
                self.assertTrue(self.controller.close(timeout=WORKFLOW_TIMEOUT), 'Fixture worker did not stop')
        result = self.controller.call('result', {'run_id': record['run_id']})
        self.assertEqual((record['status'], result['status'], result['budget_seconds']), ('evaluated', 'completed', extended_budget),
                         (record, result['reason']))
        # The run stopped for the operator's reuse reference; meanwhile the trial record already named the device stop
        # of the operator's board, which the harness never held.
        self.assertEqual({stage for stage, _, _ in seen}, {'reuse'})
        self.assertIn('reference', seen[0][1])
        self.assertEqual((seen[0][2]['status'], seen[0][2]['device_stop']), ('running', UNHELD))
        # The package measured once, through the configurator on the operator's binding, after the harness's first
        # deadline.
        self.assertEqual(([binding for binding, _ in boards], [call['ok'] for call in calls]), ([BINDING], [True]))
        self.assertGreater(boards[0][1], began + budget)
        reuse = next(h for h in reversed(result['accepted_handoffs']) if h['stage'] == 'reuse')
        handed = sorted(Path(a['path']).name.split('-', 1)[1] for a in reuse['artifacts'])
        self.assertEqual(handed[:1] + handed[2:], ['package', 'reference.json', 'reuse-events.jsonl'])
        self.assertRegex(handed[1], r'^reference-reuse-[0-9a-f]{32}\.txt$')
        equivalence = result['equivalence']
        self.assertEqual((equivalence['status'], equivalence['equivalent'], equivalence['method'],
                          [c['id'] for c in equivalence['checks']]),
                         ('graded', True, 'recorded-reuse', ['temperature', 'humidity', 'pressure']))
        # Sealed before it was posted, and the evaluator unlocked the truth once more only after the run completed.
        sealed = self.root / 'trial' / evaluator.EVIDENCE
        self.assertEqual(equivalence['evidence_sha256'], hashlib.sha256(sealed.read_bytes()).hexdigest())
        self.assertEqual(self.unlocked, [(True, False, None), (False, True, 'completed')])
        self.assertFalse((self.root / 'trial' / 'private').exists())
        self.assertNotIn('password', self.controller._row(record['run_id'])['spec'].lower())

    def test_a_package_the_emit_worker_wrote_itself_is_never_handed_off_or_graded(self):
        # Catches bme280 emit handing on, and the harness then grading, a package the emit worker wrote itself rather
        # than built with emit_package, although it passes the configurator's emit_check.
        from generative_driver import toolkit
        from generative_driver.benchmark_support import harness
        real_check, real_call = self.controller._check, toolkit.call_tool
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            return (original(spec, stage, run_dir, workspace, accepted) if stage in ('emit', 'reuse')
                    else {'inputs': [], 'allowed_tools': []})
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'probe':
                folder = run_dir / 'scripted-probe'; folder.mkdir()
                for name, content in (('model.json', '{}'), ('convert.py', '# scripted converter\n'),
                                      ('capabilities.json', json.dumps(PHYSICAL_MAPPING))):
                    (folder / name).write_text(content)
                return {'artifacts': [str(folder / n) for n in ('model.json', 'convert.py', 'capabilities.json')], 'checks': []}
            if stage == 'emit':
                return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
            return {'artifacts': [], 'checks': []}
        def work(request):
            if request.stage == 'emit':
                package = Path(request.workspace) / 'package'; (package / 'driver').mkdir(parents=True)
                (package / 'manifest.json').write_text(json.dumps({'name': 'scripted'}))
                (package / 'driver/model.json').write_text(json.dumps({'schema': 'interface-model/1',
                                                                        'operations': {'measure': [{'op': 'reg_write'}]}}))
        def report(request):
            package = Path(request.workspace) / 'package'
            return {'status': 'completed', 'summary': 'scripted ' + request.stage, 'checks': [], 'unresolved': [],
                    'artifacts': [{'path': str(package), 'sha256': 'f' * 64, 'kind': 'package'}] if request.stage == 'emit' else []}
        def call_tool(name, arguments):
            # The package check passes; everything else is the real toolkit.
            if name == 'emit_check':
                return {'ok': True, 'integrity_ok': True, 'runtime_current': True, 'seeds_run': 1}
            return real_call(name, arguments)
        # A handed-on package would stop the run at reuse for the operator's reference: the harness then gives up at once.
        clock, budget = HarnessClock(), 120
        def configurator(method, params):
            result = self.configurator(method, params)
            if method == 'status' and result.get('status') == 'blocked' and not result.get('stopping'):
                clock.offset = budget + 1
            return result
        with self.guarded(), patch.object(harness, 'GRACE', 0), patch.object(harness, 'time', clock), \
             patch('generative_driver.toolkit.call_tool', side_effect=call_tool), \
             patch('generative_driver.benchmark_support.physical.package_execute', side_effect=AssertionError('board touched')), \
             scripted_stages(self.controller, check=check, prepare=prepare, work=work, report=report):
            record = harness.run_trial(self.root / 'trial', self.request('trial-own-package', budget_seconds=budget),
                                       call=configurator)
        result = self.controller.call('result', {'run_id': record['run_id']})
        self.assertEqual((record['status'], result['status']), ('evaluated', 'completed'), (record, result['reason']))
        emitted = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
        self.assertEqual(emitted['artifacts'], [])
        noted = next(c for c in emitted['checks'] if c['name'] == 'register_package_integrity_and_replay')
        self.assertEqual((noted['passed'], [row['reason'] for row in noted['not_handed_off']]),
                         (False, ['not built by emit_package in this assignment']))
        self.assertEqual([row['reason'] for row in emitted['notes']['inline']['configurator']['dropped_artifacts']],
                         ['Package not handed off: not built by emit_package in this assignment'])
        self.assertEqual((result['equivalence']['status'], result['equivalence']['equivalent']), ('no-driver', False))
        # The truth was unlocked once, to authenticate the password before the run; no driver was graded.
        self.assertEqual(self.unlocked, [(True, False, None)])

    def test_trial_resume_grades_a_bound_device_run_completed_after_its_harness_stopped_following(self):
        # Catches a bme280 trial left ungradeable once its harness stopped waiting (the deadline of the budget it was
        # started with passed, or it was stopped or died) before the operator completed the run, a resume that grades a
        # run still stopped, and a grade posted under another method than the case's.
        from generative_driver.benchmark_support import harness
        runtime, attempts = blocked_once('acquire')
        trial = self.root / 'trial'
        clock, budget = HarnessClock(), 120
        def configurator(method, params):
            result = self.configurator(method, params)
            if method == 'status' and result.get('status') == 'blocked' and not result.get('stopping'):
                clock.offset = budget + 1
            return result
        with self.guarded(), patch.object(harness, 'GRACE', 0), patch.object(harness, 'time', clock), \
             scripted_stages(self.controller, check=lambda *args: {'artifacts': [], 'checks': []}, runtime=runtime):
            record = harness.run_trial(trial, self.request('trial-stopped', budget_seconds=budget), call=configurator)
            self.assertEqual((record['status'], record['device_stop'], record['run_status']['status'],
                              record['run_status']['harness_stopped']), ('run-stopped', UNHELD, 'blocked', 'deadline'), record)
            run = {'run_id': record['run_id']}
            # Nothing is graded while the run is stopped: its operator answers and resumes it first. No device is provided
            # anew for the operator's board, and the refusal leaves the trial as it was.
            with self.assertRaisesRegex(ValueError, 'has not completed'):
                harness.resume(trial, password_file=self.password, call=self.configurator)
            with self.assertRaisesRegex(ValueError, 'no device to provide anew'):
                harness.reattach(trial, password_file=self.password, call=self.configurator)
            self.assertEqual(harness.read_trial(trial), record)
            self.controller.call('resume', {**run, 'budget_seconds': 360, 'budget_reason': 'The operator resumes the run'})
            self.assertEqual(wait_stopped(self.controller, run)['status'], 'completed')
            # The configurator takes a grade only under the case's method.
            token = harness.evaluation_token(trial, self.password, 'trial-stopped')
            other = {'schema': 'equivalence/1', 'status': 'no-driver', 'equivalent': False, 'basis': 'evaluator',
                     'method': 'fresh-final', 'checks': [], 'diff': [{'id': 'frozen_package', 'reason': 'no driver emitted'}],
                     'passed': 0, 'total': 0, 'mission': None, 'frozen_artifact_sha256': None, 'capabilities_sha256': None,
                     'evidence_sha256': 'e' * 64, 'protocol': 'assistive/1'}
            with self.assertRaisesRegex(ValueError, 'recorded-reuse'):
                self.controller.call('record_evaluation', {**run, 'token': token, 'equivalence': other})
            done = harness.resume(trial, password_file=self.password, call=self.configurator)
        self.assertEqual((done['status'], done['device_stop'], done['sealed']), ('evaluated', UNHELD, True), done)
        self.assertNotIn('sealing_failed', done)
        equivalence = self.controller.call('result', run)['equivalence']
        self.assertEqual((equivalence['status'], equivalence['method']), ('no-driver', 'recorded-reuse'))
        self.assertEqual(attempts.count('acquire'), 2)
        # Graded once: a second resume finds the verdict recorded.
        self.assertEqual(harness.resume(trial, password_file=self.password, call=self.configurator)['equivalence'], equivalence)


class PhysicalRepairTests(BoundDeviceFixture):
    def test_a_probe_device_diff_sends_bme280_back_to_interpretation_with_that_diff(self):
        # Catches a bme280 repair that re-interprets the same sealed datasheet blind: the configurator's diff of the
        # probed model, and the candidate it contradicts, must be sealed inputs of the repair interpretation.
        real_check, prepared = self.controller._check, []
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            if stage in ('interpret', 'probe'):
                done = original(spec, stage, run_dir, workspace, accepted)
                prepared.append((stage, spec.get('_revision'), done))
                return done
            return {'inputs': [], 'allowed_tools': []} if stage == 'acquire' else {'skip': 'Outside this contract'}
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'acquire':
                (workspace / 'datasheet.pdf').write_bytes(b'%PDF-1.4 scripted datasheet\n')
                (workspace / 'pages').mkdir(); (workspace / 'pages/index.json').write_text('{"pages": []}')
                return {'artifacts': [str(workspace / 'datasheet.pdf'), str(workspace / 'pages')], 'checks': []}
            if stage == 'interpret':
                # A candidate the configurator's own validation finds defects in.
                model = run_dir / 'scripted-interpret' / str(assignment['revision']); model.mkdir(parents=True)
                (model / 'model.json').write_text(json.dumps({'schema': 'interface-model/1', 'revision': assignment['revision']}))
                (model / 'convert.py').write_text('# scripted converter\n')
                return {'artifacts': [str(model / 'model.json'), str(model / 'convert.py')], 'checks': []}
            return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
        with self.guarded(), scripted_stages(self.controller, check=check, prepare=prepare):
            run = self.controller.start({'goal': 'Physical repair contract', 'case': 'bme280', 'effects': ['write'],
                                         'binding': BINDING, 'executor_config': {'command': ['scripted-runtime']}})
            result = wait_stopped(self.controller, run, 60)
        self.assertEqual(result['status'], 'completed', result['reason'])
        revisions = [e['data'] for e in self.controller.call('events', run)['events'] if e['kind'] == 'run.revision']
        self.assertEqual([r['revision'] for r in revisions], [1, 2])
        self.assertTrue(all(r['feedback'] and r['feedback'][0]['source'] == 'model_contract' for r in revisions), revisions)
        interpretations = [(revision, done) for stage, revision, done in prepared if stage == 'interpret']
        self.assertEqual([revision for revision, _ in interpretations], [0, 1, 2])
        sealed = lambda done: {Path(p).relative_to(done['work_dir']).as_posix(): Path(p) for p in done['inputs']}
        first = sealed(interpretations[0][1])
        self.assertFalse({name for name in first if 'DEFECTS' in name or name.startswith(('previous-model', 'REPAIR'))}, first)
        for (revision, done), recorded in zip(interpretations[1:], revisions):
            inputs = sealed(done)
            with self.subTest(revision=revision):
                # The repair sees what the device diff says the previous candidate got wrong, and that candidate.
                self.assertEqual(json.loads(inputs['repair/DEFECTS.json'].read_text()), recorded['feedback'])
                self.assertEqual(json.loads(inputs['previous-model/model.json'].read_text())['revision'], revision - 1)
                self.assertIn('previous-model/convert.py', inputs)
                context = json.loads(inputs['REPAIR_CONTEXT.json'].read_text())
                self.assertEqual((context['mode'], context['feedback'], context['previous_candidate']),
                                 ('repair', 'repair/DEFECTS.json', 'previous-model/'))
                # Still the datasheet's own sealed inputs, with no case truth.
                self.assertIn('datasheet.pdf', inputs)
        self.assertEqual(self.unlocked, [])


class StageBoundaryTests(unittest.TestCase):
    def test_no_stage_adapter_imports_what_decrypts_truth_or_grades(self):
        # Catches a stage preparation or observation of any case, legacy ones included, reaching the truth or the
        # evaluator: only the bench harness and the evaluator may.
        for name in ('cases.py', 'physical.py', 'ground.py', 'emulated.py', 'candidate_context.py'):
            found = imported(SOURCE / 'benchmark_support' / name)
            with self.subTest(module=name):
                self.assertFalse({n for n in found if FORBIDDEN & set(n.split('.'))}, found)

    def test_the_in_loop_legacy_evaluator_paths_are_gone(self):
        from generative_driver import benchmark, configurator
        from generative_driver.benchmark_support import cases, evaluate, legacy, physical
        from generative_driver.configurator import Controller
        for owner, names in ((Controller, ('_managed_probe',)), (configurator, ('_adapter_verdict', 'LEGACY_CASES')),
                             (benchmark, ('check_stage', 'evaluation_fault')), (cases, ('check_stage', '_load_case', '_session')),
                             (physical, ('check_stage',)), (legacy, ('package_execute', 'cleanup')),
                             (evaluate, ('evaluate_model',))):
            for name in names:
                self.assertFalse(hasattr(owner, name), name)
        self.assertNotIn('evaluator_workspace', (SOURCE / 'configurator.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

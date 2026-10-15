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

    def start(self, candidate, **changes):
        from test_harness import Device
        self.device = Device(self, self.root / 'resources/cases/tq9-v2/firmware.bin', self.root)
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
        # The run's execution snapshot names the replayed candidate by its hash, beside the replay runtime.
        executed = read_snapshot(self.home / 'runs' / started['run_id'])['executed']['runtime_configuration']
        self.assertEqual(executed, {'runtime': 'replay', 'candidate': str(candidate), 'candidate_sha256': digest(candidate)})

    def test_a_candidate_changed_after_the_run_recorded_it_stops_the_run_for_integrity(self):
        from generative_driver.configurator import Controller
        original = Controller._prepare

        def prepare(controller, spec, stage, *args, **kwargs):
            if stage == 'interpret':
                # Between acquire's handoff and interpretation, the saved interpretation is replaced.
                (self.candidate / 'model.json').write_text('{"schema": "another model"}')
            return original(controller, spec, stage, *args, **kwargs)
        with patch.object(Controller, '_prepare', prepare):
            started = self.start(self.candidate)
            result = self.finished(started['run_id'])
        self.assertEqual((result['status'], result['outcome_category'], result['progress']['next_stage']),
                         ('blocked', 'integrity', 'interpret'), result)
        self.assertIn('replay candidate changed', result['reason'])
        # No worker ran from the changed candidate.
        self.assertEqual([w['stage'] for w in result['worker_reports']], ['acquire'])
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']], ['acquire'])

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
             'replay step is')]
        for changes, message in refused:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.controller.start({key: value for key, value in {**good, **changes}.items() if value is not None})
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()

"""What a client gains at version 3 of the client interface, and what the service no longer has.

Feature 07 of the untangle Run (docs/features/untangle/07-suites.md): 07-B1 to 07-B4, the tool's side of 07-B5, and
07-B6. `status` finds a run by the request id its `start` gave, `result` gives back the request, a client can mark a
run sealed, and on a supplied run a given `executor_config` is the whole configuration. The service has no suite: no
suite call, no suite table and no private start. This module imports nothing from the benchmark package and names no
registered case, so it also runs where the benchmark is gone. The benchmark's side of the Feature is in the
benchmark's tests.

Temporary homes only; no model, no hardware and no password. The device is the hand-written one of
`tests/supplied_device.py`.
"""
import copy
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from generative_driver import integrity
from supplied_device import SuppliedDevice
from test_supplied_stages import MISSION, hand_written_firmware

TOKEN = 'a token only this test holds'
TOKEN_SHA256 = hashlib.sha256(TOKEN.encode()).hexdigest()
SEALED = 'A sealed run does not resume or take a device: '


class ServiceCase(unittest.TestCase):
    """A service in a temporary home, with the hand-written device, firmware and mission."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.controller = self.service()
        self.firmware = hand_written_firmware(self.root)

    def service(self):
        """A service on the test's home, closed when the test ends."""
        from generative_driver.configurator import Controller
        controller = Controller(self.home)
        self.addCleanup(controller.close)
        return controller

    def device(self, name='device'):
        """A hand-written device under the test's folder, closed when the test ends."""
        device = SuppliedDevice(self.root / name)
        self.addCleanup(device.close)
        return device

    def supplied(self, **more):
        """The params of a `start` on a new hand-written device, with `more` laid over them."""
        name = 'device-' + str(len(list(self.root.glob('device-*'))))
        return {'goal': 'Read the gauge', 'device': self.device(name).descriptor, 'firmware': dict(self.firmware),
                'mission': copy.deepcopy(MISSION), 'evaluation_token_sha256': TOKEN_SHA256,
                'executor_config': {'command': ['never-launched']}, **more}

    def held(self, params):
        """The reply to a `start` of `params` whose run is held back: it stays queued and no stage begins."""
        with patch.object(self.controller, '_spawn'):
            return self.controller.call('start', params)

    def call(self, method, **params):
        return self.controller.call(method, params)

    def refused(self, method, **params):
        """The text with which the service refuses the call, which must be a `ValueError` exactly."""
        with self.assertRaises(ValueError) as refusal:
            self.controller.call(method, params)
        self.assertIs(type(refusal.exception), ValueError, refusal.exception)
        return str(refusal.exception)


class StatusByRequestIdTests(ServiceCase):
    """`status` finds a run by the request id the client gave at `start` (07-B1)."""

    def test_07_B1_status_with_a_request_id_answers_as_status_with_the_run_id_of_that_start(self):
        first = self.held(self.supplied(request_id='gauge-1'))
        second = self.held(self.supplied(request_id='gauge-2'))
        generic = self.held({'goal': 'Read the gauge', 'executor_config': {'command': ['c']}, 'request_id': 'generic'})
        for started, request_id in ((first, 'gauge-1'), (second, 'gauge-2'), (generic, 'generic')):
            with self.subTest(request_id=request_id):
                by_run = self.call('status', run_id=started['run_id'])
                self.assertEqual(self.call('status', request_id=request_id), by_run)
                self.assertEqual(by_run['run_id'], started['run_id'])

    def test_07_B1_a_request_id_that_started_no_run_is_refused(self):
        self.assertEqual(self.refused('status', request_id='never-started'), 'Unknown request_id')
        self.held(self.supplied(request_id='gauge-1'))
        self.held(self.supplied())
        for request_id in ('never-started', 'gauge-10', '', None, 7, ['gauge-1'], {'id': 'gauge-1'}):
            with self.subTest(request_id=request_id):
                self.assertEqual(self.refused('status', request_id=request_id), 'Unknown request_id')

    def test_07_B1_with_both_the_run_id_is_used_and_the_request_id_is_not_read(self):
        first = self.held(self.supplied(request_id='gauge-1'))
        second = self.held(self.supplied(request_id='gauge-2'))
        of_first = self.call('status', run_id=first['run_id'])
        self.assertEqual(self.call('status', run_id=first['run_id'], request_id='gauge-2'), of_first)
        self.assertEqual(self.call('status', run_id=first['run_id'], request_id='never-started'), of_first)
        self.assertEqual(self.call('status', run_id=first['run_id'], request_id={'not': 'text'}), of_first)
        self.assertNotEqual(of_first['run_id'], second['run_id'])
        self.assertEqual(self.refused('status', run_id='run-unknown', request_id='gauge-1'), 'Unknown run_id')

    def test_07_B1_every_other_call_needs_a_run_id_as_before(self):
        self.held(self.supplied(request_id='gauge-1'))
        for method in ('events', 'result', 'cancel', 'seal', 'resume', 'respond', 'attach_device',
                       'record_evaluation', 'tools'):
            with self.subTest(method=method):
                self.assertEqual(self.refused(method, request_id='gauge-1'), 'Unknown run_id')
        self.assertEqual(self.refused('status'), 'Unknown run_id')


class RequestTests(ServiceCase):
    """`result` gives back the request the run was started with (07-B2)."""

    def test_07_B2_result_has_the_params_of_the_start_as_the_service_received_them(self):
        params = self.supplied(request_id='gauge-1', effects=['read', 'write'], budget_seconds=900,
                               client_record={'order': 17, 'nested': [1, {'deep': None}], 'text': 'größe'})
        started = self.held(params)
        request = self.call('result', run_id=started['run_id'])['request']
        self.assertEqual(request, json.loads(json.dumps(params)))
        self.assertEqual(request['client_record'], params['client_record'])
        # What the service settled for the run is no part of the request: only what the client sent.
        self.assertEqual(request['executor_config'], {'command': ['never-launched']})
        self.assertNotIn('executor', request)
        generic = {'goal': 'Read the gauge', 'executor_config': {'command': ['c']}, 'effects': ('read',)}
        started = self.held(generic)
        self.assertEqual(self.call('result', run_id=started['run_id'])['request'],
                         {**generic, 'effects': ['read']})

    def test_07_B2_a_resume_with_a_larger_budget_leaves_the_request_as_the_start_gave_it(self):
        params = self.supplied(budget_seconds=900)
        started = self.held(params)
        self.call('cancel', run_id=started['run_id'])
        with patch.object(self.controller, '_spawn'), patch.object(self.controller, '_resume_device'):
            self.call('resume', run_id=started['run_id'], budget_seconds=1800, budget_reason='A longer look')
        result = self.call('result', run_id=started['run_id'])
        self.assertEqual((result['budget_seconds'], result['request']['budget_seconds']), (1800, 900))

    def test_07_B2_a_run_an_earlier_version_stored_without_its_request_has_no_request(self):
        started = self.held(self.supplied(client_record={'order': 17}))
        # A run as an earlier version stored it: its spec holds what the service settled, and no request.
        with closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db, db:
            [spec] = db.execute('SELECT spec FROM runs WHERE id=?', (started['run_id'],)).fetchone()
            earlier = {key: value for key, value in json.loads(spec).items() if not key.startswith('_')}
            self.assertEqual(earlier['executor'], 'codex')
            self.assertIn('device', earlier)
            db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(earlier), started['run_id']))
        result = self.call('result', run_id=started['run_id'])
        self.assertNotIn('request', result)
        self.assertEqual(result['client_record'], {'order': 17})

    def test_07_B2_status_has_no_request(self):
        started = self.held(self.supplied(request_id='gauge-1'))
        self.assertNotIn('request', self.call('status', run_id=started['run_id']))
        self.assertNotIn('request', self.call('status', request_id='gauge-1'))
        self.assertIn('request', self.call('result', run_id=started['run_id']))

    def test_07_B2_start_refuses_a_secret_as_before_so_no_request_holds_one(self):
        text = ('The configurator takes no password, password file, emulator executable or set root; the client '
                'holds them')
        for number, key in enumerate(('evaluator_password', 'password_file', 'evaluator_password_files', 'renode',
                                      'set_root')):
            with self.subTest(key=key):
                request_id = 'secret-' + str(number)
                self.assertEqual(self.refused('start', **self.supplied(request_id=request_id, **{key: '/a/secret'})),
                                 text)
                self.assertEqual(self.refused('status', request_id=request_id), 'Unknown request_id')


class SealTests(ServiceCase):
    """A client can mark a run sealed (07-B3)."""

    def test_07_B3_seal_answers_that_the_run_is_sealed_and_the_same_call_again_answers_the_same(self):
        started = self.held(self.supplied())
        answer = {'ok': True, 'run_id': started['run_id'], 'sealed': True}
        self.assertEqual(self.call('seal', run_id=started['run_id']), answer)
        self.assertEqual(self.call('seal', run_id=started['run_id']), answer)

    def test_07_B3_status_and_result_of_a_sealed_run_say_so_and_of_any_other_run_do_not(self):
        sealed, other = self.held(self.supplied()), self.held(self.supplied())
        self.call('seal', run_id=sealed['run_id'])
        self.assertIs(self.call('status', run_id=sealed['run_id'])['sealed'], True)
        self.assertIs(self.call('result', run_id=sealed['run_id'])['sealed'], True)
        self.assertNotIn('sealed', self.call('status', run_id=other['run_id']))
        self.assertNotIn('sealed', self.call('result', run_id=other['run_id']))

    def test_07_B3_a_sealed_run_does_not_resume_or_take_a_device(self):
        started = self.held(self.supplied())
        run_id = started['run_id']
        self.call('cancel', run_id=run_id)
        self.call('seal', run_id=run_id)
        device = self.device('another')
        self.assertEqual(self.refused('resume', run_id=run_id), SEALED + run_id)
        self.assertEqual(self.refused('attach_device', run_id=run_id, token=TOKEN, device=device.descriptor),
                         SEALED + run_id)
        # Refused before anything asks a device.
        self.assertEqual(device.requests, [])

    def test_07_B3_seal_changes_nothing_else_of_the_run(self):
        started = self.held(self.supplied(client_record={'order': 17}))
        run_id = started['run_id']
        self.call('cancel', run_id=run_id)

        def seen():
            files = sorted((str(path.relative_to(self.home)), path.read_bytes() if path.is_file() else None)
                           for path in self.home.rglob('*') if 'sqlite3' not in path.name)
            return (self.call('status', run_id=run_id), self.call('result', run_id=run_id),
                    self.call('events', run_id=run_id), files)
        before = seen()
        self.call('seal', run_id=run_id)
        status, result, events, files = seen()
        self.assertEqual({key: value for key, value in status.items() if key != 'sealed'}, before[0])
        self.assertEqual({key: value for key, value in result.items() if key != 'sealed'}, before[1])
        self.assertEqual((events, files), before[2:])
        self.assertEqual((status['status'], status['equivalence']), ('cancelled', None))

    def test_07_B3_seal_with_an_unknown_run_id_is_refused(self):
        self.held(self.supplied(request_id='gauge-1'))
        for run_id in ('run-unknown', None, 'gauge-1'):
            with self.subTest(run_id=run_id):
                self.assertEqual(self.refused('seal', run_id=run_id), 'Unknown run_id')


CONFIGURED = {'command': ['configured-runtime'], 'model': 'configured-model', 'reasoning_effort': 'high'}


class WholeConfigurationTests(ServiceCase):
    """On a supplied run, a given `executor_config` is the whole configuration (07-B4)."""

    def setUp(self):
        super().setUp()
        (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': CONFIGURED}}), encoding='utf-8')

    def launched(self, params):
        """The configuration the first stage worker of a run of `params` is launched with."""
        seen = []

        def execute(request, config, cancel):
            seen.append(copy.deepcopy(config))
            return {'runtime': config.get('runtime'), 'model': config.get('model'), 'provider': None, 'report': None,
                    'usage': None, 'status': 'blocked', 'cause': 'runtime_unavailable', 'elapsed_seconds': 0,
                    'reason': 'The test launches no worker'}
        with patch('generative_driver.configurator.execute', execute):
            run = self.controller.call('start', params)
            until = time.monotonic() + 60
            while self.controller.call('status', run)['status'] in ('queued', 'running'):
                self.assertLess(time.monotonic(), until)
                time.sleep(.02)
        self.assertTrue(seen, self.controller.call('status', run))
        return seen[0], self.controller.call('result', run)

    def test_07_B4_a_supplied_run_started_with_an_executor_config_runs_with_exactly_that_object(self):
        given = {'command': ['given-runtime'], 'model': 'given-model'}
        config, result = self.launched(self.supplied(executor_config=given))
        # Nothing of the home's `executors` is laid under it: no `reasoning_effort`, and the given values stand.
        self.assertEqual(config, {**given, 'runtime': 'codex'})
        self.assertEqual(result['agent']['settings'], {})
        self.assertEqual(result['agent']['model'], 'given-model')

    def test_07_B4_a_supplied_run_started_without_one_runs_with_what_the_operator_configured(self):
        params = self.supplied()
        del params['executor_config']
        config, result = self.launched(params)
        self.assertEqual(config, {**CONFIGURED, 'runtime': 'codex'})
        self.assertEqual(result['agent']['settings'], {'reasoning_effort': 'high'})

    def test_07_B4_a_run_without_a_supplied_device_lays_its_executor_config_over_what_the_operator_configured(self):
        config, result = self.launched({'goal': 'Read the gauge', 'executor_config': {'model': 'given-model'}})
        self.assertEqual(config, {**CONFIGURED, 'model': 'given-model', 'runtime': 'codex'})
        self.assertEqual(result['agent']['settings'], {'reasoning_effort': 'high'})

    def test_07_B4_the_replay_executors_check_and_copy_of_its_candidate_are_unchanged(self):
        candidate = self.root / 'candidate'
        candidate.mkdir()
        (candidate / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {}}),
                                               encoding='utf-8')
        replay_config = {'executor': 'replay', 'executor_config': {'candidate': str(candidate)}}
        started = self.held({**self.supplied(), **replay_config})
        copied = self.home / 'runs' / started['run_id'] / 'replay-candidate'
        self.assertEqual(integrity.digest(copied), integrity.digest(candidate))
        # Only the candidate is taken: any other setting is refused, as before.
        self.assertEqual(self.refused('start', **{**self.supplied(), 'executor': 'replay',
                                                  'executor_config': {'candidate': str(candidate), 'model': 'm'}}),
                         'The replay executor takes only its saved candidate: executor_config {"candidate": '
                         '<directory>}')
        (candidate / 'replay.json').write_text('not a candidate', encoding='utf-8')
        self.assertIn('replay.json', self.refused('start', **{**self.supplied(), **replay_config}))


if __name__ == '__main__':
    unittest.main()

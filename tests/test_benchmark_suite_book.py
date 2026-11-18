"""The suite book: the bench harness keeps a suite's bookkeeping itself, and asks the service only through public calls.

Feature 07 of the untangle Run (docs/features/untangle/07-suites.md): the benchmark's side, 07-B7 to 07-B17, and the
last point of 07-B5. This file is a benchmark item: it runs a registered case and reads the deletion list, and the
deletion removes it with them. The tool's side of the Feature is in `tests/test_client_calls.py`, which stays.

A book is driven alone against a stand-in for the service that records its requests, or against an in-process
configurator. The scripted suite runs the installed `tq9-v2` with scripted stage observations, devices of synthetic
peers, a toy answer key and a scripted final phase; never a model, an emulator or a real password.
"""
import ast
import contextlib
import copy
import errno
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark_support import evaluator, harness, suite_book, suites, trial_store
from generative_driver.benchmark_support.suite_book import SuiteBook
from package_env import package_env
from suite_fixtures import scripted_manifest
from test_benchmark_runs import CASE, CONFIGURED, TrialCase
from test_configurator import scripted_stages
from test_evaluation import PASS, reference

REPO = Path(__file__).resolve().parents[1]
TOKEN = 'the token of the harness of these tests'
TOKEN_SHA256 = hashlib.sha256(TOKEN.encode()).hexdigest()
SHA = 'a' * 64
# The calls a book may send to the service, and the keys a run's `status` gained that no suite reply passes on.
PUBLIC = {'ping', 'start', 'status', 'result', 'events', 'cancel', 'resume', 'seal'}
ADDED = ('deadline', 'repairs', 'worker', 'log')
SUITE_CALLS = ('suite_start', 'suite_resume', 'suite_reserve', 'suite_attach', 'suite_start_child',
               'suite_resume_child', 'suite_settle', 'suite_no_run', 'suite_flag', 'suite_sealed', 'suite_detach',
               'suite_cancel', 'suite_status', 'suite_events', 'suite_result')
RESTARTED = 'Configurator restarted; explicit resume required'
HARNESS_TOKEN = 'Only the bench harness driving the suite may schedule it (harness token)'
TAKEN = "; the slot's request_id is taken by that child, so start a new suite"
MISMATCH = 'Frozen suite child identity mismatch: trial '
LOST = {'ok': False, 'reason': 'Configurator request interrupted after dispatch: the reply was lost'}
UNREACHABLE = {'ok': False, 'reason': 'Configurator is not reachable. Run generative-driver service start from an '
                                      'operator terminal, then reconnect this UI.'}
GRADED = {'schema': 'equivalence/2', 'status': 'graded', 'equivalent': True}


class Service:
    """A stand-in for the service that records every request, as (method, params), and answers the eight public
    calls from runs it keeps in memory, in the form the service answers them. `pid` is the process id its `ping`
    answers with. `lost[method]` counts the next requests of `method` that are carried out and get no answer, and
    `refused[method]` holds the reason the next request of `method` is refused with."""

    def __init__(self):
        self.pid, self.requests, self.runs = 4001, [], {}
        self.lost, self.refused, self.reachable = {}, {}, True

    def __call__(self, method, params=None):
        params = copy.deepcopy(params or {})
        self.requests.append((method, params))
        if not self.reachable:
            return dict(UNREACHABLE)
        try:
            if method in self.refused:
                raise ValueError(self.refused.pop(method))
            reply = getattr(self, '_' + method)(params)
        except ValueError as error:
            return {'ok': False, 'error': 'ValueError', 'reason': str(error)}
        if self.lost.get(method):
            self.lost[method] -= 1
            return dict(LOST)
        return copy.deepcopy(reply)

    def sent(self, method=None):
        """The params of every request of `method` so far, or the method of every request."""
        return [name for name, _ in self.requests] if method is None else [
            params for name, params in self.requests if name == method]

    def run(self, key):
        if key.get('run_id') is None and 'request_id' in key:
            found = [run for run in self.runs.values() if run['request_id'] == key['request_id']]
            if not found or not isinstance(key['request_id'], str):
                raise ValueError('Unknown request_id')
            return found[0]
        if key.get('run_id') not in self.runs:
            raise ValueError('Unknown run_id')
        return self.runs[key['run_id']]

    def end(self, run_id, status='completed', outcome_category=None, **changes):
        """The run has ended with `status`, nothing of it stopping, and `changes` laid over it."""
        self.runs[run_id].update(status=status, outcome_category=outcome_category or status, stopping=False,
                                 updated=self.runs[run_id]['created'] + 5, **changes)

    def event(self, run_id, kind, data=None):
        events = self.runs[run_id]['events']
        events.append({'id': len(events) + 1, 'kind': kind, 'data': data or {}, 'time': 1.0})

    def _ping(self, params):
        return {'ok': True, 'pid': self.pid, 'code': 'code-of-these-tests'}

    def _start(self, params):
        for key in ('case', 'case_options', 'case_pin', 'admission', 'public_inputs'):
            if key in params:
                raise ValueError('start takes no ' + key
                                 + '; a client supplies the device, the firmware and the mission')
        held = [run for run in self.runs.values()
                if params.get('request_id') and run['request_id'] == params['request_id']]
        if held:
            if held[0]['request'] != params:
                raise ValueError('request_id was already used with different inputs')
            return {'ok': True, 'run_id': held[0]['run_id'], 'duplicate': True}
        run_id = 'run-%016x' % (len(self.runs) + 1)
        self.runs[run_id] = {'run_id': run_id, 'request_id': params.get('request_id'), 'request': params,
                             'status': 'running', 'stage': 'acquire', 'reason': None, 'created': 100.0,
                             'updated': 100.0, 'outcome_category': 'unknown', 'uncertain_effect': False,
                             'stopping': False, 'equivalence': None, 'sealed': False,
                             'budget_seconds': params.get('budget_seconds', 10800), 'worker_reports': [], 'events': []}
        self.event(run_id, 'run.started')
        return {'ok': True, 'run_id': run_id, 'duplicate': False}

    def _status(self, params):
        run = self.run(params)
        return {'ok': True, **{key: run[key] for key in ('status', 'stage', 'reason', 'created', 'updated',
                                                         'outcome_category', 'run_id', 'uncertain_effect', 'stopping',
                                                         'equivalence')},
                **({'sealed': True} if run['sealed'] else {}), 'stage_notes': {},
                'deadline': run['created'] + run['budget_seconds'], 'repairs': {'used': 0, 'budget': 2},
                'worker': None, 'log': '/the/service/daemon.log'}

    def _result(self, params):
        run = self.run({'run_id': params.get('run_id')})
        return {**self._status({'run_id': run['run_id']}), 'budget_seconds': run['budget_seconds'],
                'worker_reports': run['worker_reports'], 'accepted_handoffs': [], 'evaluator_verdicts': [],
                'progress': {'protocol': 'assistive/1'}, 'request': run['request'],
                **({'client_record': run['request']['client_record']} if 'client_record' in run['request'] else {})}

    def _events(self, params):
        run = self.run({'run_id': params.get('run_id')})
        events = [event for event in run['events'] if event['id'] > params.get('after', 0)][:500]
        return {'ok': True, 'run_id': run['run_id'], 'events': events,
                'cursor': events[-1]['id'] if events else params.get('after', 0)}

    def _cancel(self, params):
        run = self.run({'run_id': params.get('run_id')})
        if not (run['status'] == 'completed' and run['equivalence']):
            run.update(status='cancelled', outcome_category='cancelled', stopping=False)
        return self._status({'run_id': run['run_id']})

    def _resume(self, params):
        run = self.run({'run_id': params.get('run_id')})
        if run['sealed']:
            raise ValueError('A sealed run does not resume or take a device: ' + run['run_id'])
        if run['status'] not in ('blocked', 'failed', 'cancelled'):
            raise ValueError('Only a stopped incomplete run can resume')
        if params.get('budget_seconds') is not None:
            run['budget_seconds'] = params['budget_seconds']
        run.update(status='queued', outcome_category='unknown', reason=None)
        self.event(run['run_id'], 'run.resumed')
        return self._status({'run_id': run['run_id']})

    def _seal(self, params):
        run = self.run({'run_id': params.get('run_id')})
        run['sealed'] = True
        return {'ok': True, 'run_id': run['run_id'], 'sealed': True}


def added_by_the_harness(request):
    """What a bench harness adds to the frozen child `request`: the five harness keys, as data of these tests."""
    return {'device': {'schema': 'device-descriptor/2', 'session_id': 'session-1'},
            'firmware': {'path': '/nowhere/firmware.bin', 'sha256': SHA}, 'mission': {'tasks': {}},
            'evaluation_token_sha256': 'e' * 64,
            'client_record': {'case': request['case'], 'case_pin': request['case_pin'], 'snapshot_sha256': 'f' * 64}}


def plain_start(request):
    """The `start` of a child from the request its harness hands in, as these tests expect a book to send it."""
    start = {key: value for key, value in request.items() if key not in ('case', 'case_pin', 'case_options')}
    start.update({key: request['case_options'][key] for key in ('ghidra_home', 'java_home')
                  if key in request['case_options']})
    return start


class BookCase(unittest.TestCase):
    """A book on a temporary home, driven alone against the stand-in service."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': CONFIGURED}}), encoding='utf-8')
        self.bench = self.root / 'home-bench'
        self.service = Service()
        self.book = SuiteBook(self.home, self.service)

    def call(self, method, **params):
        return self.book.call(method, params)

    def refused(self, method, **params):
        """The text of the book's own refusal of the call, a `ValueError` exactly."""
        with self.assertRaises(ValueError) as refusal:
            self.book.call(method, params)
        self.assertIs(type(refusal.exception), ValueError, refusal.exception)
        return str(refusal.exception)

    def start(self, repetitions=2, request_id='book-suite', options=None, **more):
        """The id of a suite the book froze for the harness of these tests."""
        reply = self.call('suite_start', manifest=scripted_manifest(repetitions), executor='codex',
                          options=options or {}, request_id=request_id, harness_token_sha256=TOKEN_SHA256,
                          harness_pid=os.getpid(), **more)
        self.assertEqual((reply['ok'], reply['duplicate'], reply['attached']), (True, False, True), reply)
        return reply['suite_id']

    def driving(self, suite_id, **params):
        return {'suite_id': suite_id, 'token': TOKEN, **params}

    def reserve(self, suite_id):
        return self.call('suite_reserve', **self.driving(suite_id))

    def start_child(self, suite_id, reserved=None, **changes):
        """(the reply to the start of the reserved slot's child, the request the harness handed in)."""
        reserved = reserved or self.reserve(suite_id)
        request = {**reserved['request'], **added_by_the_harness(reserved['request']), **changes}
        reply = self.call('suite_start_child', **self.driving(suite_id, ordinal=reserved['slot']['ordinal'],
                                                              request=request))
        return reply, request

    def running(self, repetitions=2, **more):
        """(suite id, run id of its first slot's child) of a suite whose first child runs."""
        suite_id = self.start(repetitions, **more)
        started, _ = self.start_child(suite_id)
        return suite_id, started['run_id']

    def outside(self, suite_id, **changes):
        """The run of a plain `start` under the reserved slot's request id, sent outside the book: (the reservation,
        its run id). `changes` are laid over the plain start."""
        reserved = self.reserve(suite_id)
        request = {**reserved['request'], **added_by_the_harness(reserved['request'])}
        started = self.service('start', {**plain_start(request), **changes})
        self.assertTrue(started['ok'], started)
        return reserved, started['run_id']

    def stored(self, repetitions=1, scoring=False, **changes):
        """The id of a running suite put into the book's store as it is frozen, for a suite a manifest cannot give:
        `scoring` marks it a scoring suite, and `changes` are laid over its frozen record."""
        from generative_driver.benchmark_support.snapshots import execution_provenance
        config = {**CONFIGURED, 'runtime': 'codex'}
        frozen = suites.freeze_suite(scripted_manifest(repetitions), 'codex', config, {},
                                     execution_provenance(config, case_ids=[CASE]), harness_token_sha256=TOKEN_SHA256)
        if scoring:
            frozen['scoring_set'] = {'registration': 'set-v1', 'manifest_sha256': SHA}
        frozen.update(changes)
        self.stored_suites = getattr(self, 'stored_suites', 0) + 1
        suite_id = self.book.store.create(frozen, 'stored-suite-' + str(self.stored_suites),
                                          harness_pid=os.getpid())['suite_id']
        self.book.store.set_state(suite_id, 'running')
        return suite_id

    def status(self, suite_id):
        return self.call('suite_status', suite_id=suite_id)

    def events(self, suite_id):
        return [(event['kind'], event['data']) for event in self.call('suite_events', suite_id=suite_id)['events']]

    def since(self, mark):
        """The methods the book sent to the service since `mark`, a length of the service's requests."""
        return self.service.sent()[mark:]


class StoreTests(BookCase):
    """The store lies in the bench home (07-B7)."""

    def test_07_B7_the_store_is_suites_sqlite3_directly_under_the_bench_home_and_trial_store_gives_its_path(self):
        self.assertEqual(trial_store.suite_store_path(self.home), trial_store.bench_home(self.home) / 'suites.sqlite3')
        self.assertEqual(trial_store.suite_store_path(self.home), self.bench / 'suites.sqlite3')
        suite_id = self.start()
        with contextlib.closing(sqlite3.connect(self.bench / 'suites.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT id,status FROM suites').fetchall(), [(suite_id, 'running')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suite_trials').fetchone(), (2,))
        # Nothing of the suite lies in the service's home.
        self.assertEqual([path.name for path in self.home.rglob('*') if 'suite' in path.name.lower()], [])
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ['config.json'])

    def test_07_B7_the_file_and_its_three_tables_are_made_when_a_book_first_needs_them_for_its_owner_alone(self):
        path = self.bench / 'suites.sqlite3'
        SuiteBook(self.home, self.service)
        self.assertFalse(self.bench.exists())
        self.assertEqual(self.refused('suite_status', suite_id='suite-0000000000000000'), 'Unknown suite_id')
        self.assertTrue(path.is_file())
        with contextlib.closing(sqlite3.connect(path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual({table for table in tables if not table.startswith('sqlite_')},
                         {'suites', 'suite_trials', 'suite_events'})
        if os.name != 'nt':
            # As `suite.json` is: for this user only, whatever else the store's database keeps beside its file.
            for file in self.bench.glob('suites.sqlite3*'):
                with self.subTest(file=file.name):
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_07_B7_the_tables_have_the_columns_of_today_and_suites_gains_the_process_id(self):
        self.start()
        with contextlib.closing(sqlite3.connect(self.bench / 'suites.sqlite3')) as db:
            columns = {table: [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
                       for table in ('suites', 'suite_trials', 'suite_events')}
        today = ['id', 'request_id', 'request_json', 'frozen_json', 'status', 'reason', 'created', 'updated',
                 'budget_seconds', 'cancelled', 'harness_token_sha256', 'harness_pid', 'harness_attached',
                 'harness_lock']
        self.assertEqual(columns['suites'][:len(today)], today)
        [gained] = columns['suites'][len(today):]
        self.assertIn('pid', gained)
        self.assertEqual(columns['suite_trials'], ['suite_id', 'ordinal', 'trial_json', 'child_request_id', 'run_id',
                                                   'status', 'outcome_category', 'result_json', 'attempt',
                                                   'first_attempt', 'no_run_reason', 'regrade', 'audit'])
        self.assertEqual(columns['suite_events'], ['seq', 'suite_id', 'kind', 'payload', 'created'])


class CallTests(BookCase):
    """The book serves the suite calls as the service did (07-B8)."""

    def test_07_B8_each_suite_call_gives_the_answer_it_gave(self):
        started = self.call('suite_start', manifest=scripted_manifest(2), executor='codex', options={},
                            request_id='one', harness_token_sha256=TOKEN_SHA256, harness_pid=os.getpid())
        suite_id = started['suite_id']
        self.assertEqual(started, {'ok': True, 'suite_id': suite_id, 'duplicate': False, 'attached': True})
        self.assertRegex(suite_id, '^suite-[0-9a-f]{16}$')
        reserved = self.reserve(suite_id)
        self.assertEqual(sorted(reserved), ['ok', 'request', 'scoring', 'slot', 'status', 'suite_id'])
        self.assertEqual((reserved['status'], reserved['scoring']), ('running', False))
        self.assertEqual(reserved['slot'], {'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': CASE,
                                            'child_request_id': 'suite:' + suite_id + ':trial:0', 'run_id': None})
        self.assertEqual(sorted(reserved['request']), ['budget_seconds', 'case', 'case_options', 'case_pin', 'effects',
                                                        'executor', 'executor_config', 'goal', 'request_id',
                                                        'scoped_tool_approval'])
        child, request = self.start_child(suite_id, reserved)
        run_id = child['run_id']
        self.assertEqual(child, {'ok': True, 'run_id': run_id, 'duplicate': False, 'suite_id': suite_id})
        status = self.status(suite_id)
        self.assertEqual(sorted(status), ['active_child_id', 'completed', 'created', 'equivalent', 'ok', 'planned',
                                          'reason', 'status', 'stopping', 'suite_id', 'updated'])
        self.assertEqual({key: status[key] for key in ('status', 'reason', 'active_child_id', 'planned', 'completed',
                                                       'equivalent', 'stopping')},
                         {'status': 'running', 'reason': None, 'active_child_id': run_id, 'planned': 2, 'completed': 0,
                          'equivalent': 0, 'stopping': False})
        self.assertEqual(self.call('suite_settle', **self.driving(suite_id, ordinal=0)),
                         {'ok': True, 'suite_id': suite_id, 'decision': 'wait', 'status': 'running'})
        self.service.end(run_id, equivalence=GRADED)
        self.assertEqual(self.call('suite_settle', **self.driving(suite_id, ordinal=0)),
                         {'ok': True, 'suite_id': suite_id, 'decision': 'advance', 'status': 'running'})
        result = self.call('suite_result', suite_id=suite_id, limit=1)
        self.assertEqual(sorted(set(result) - set(status)), ['experiment', 'next_offset', 'trials'])
        self.assertEqual(result['next_offset'], 1)
        [trial] = result['trials']
        self.assertEqual({key: trial[key] for key in ('ordinal', 'trial_key', 'case', 'run_id', 'status', 'equivalent',
                                                      'equivalence_status', 'interventions')},
                         {'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': CASE, 'run_id': run_id,
                          'status': 'finished', 'equivalent': True, 'equivalence_status': 'graded',
                          'interventions': []})
        experiment = result['experiment']
        self.assertEqual(sorted(experiment), ['comparison_identity', 'dimensions', 'evaluator_revisions',
                                              'execution_snapshots', 'schema'])
        self.assertEqual(experiment['schema'], 'benchmark-suite-experiment/1')
        self.assertEqual(experiment['execution_snapshots'],
                         [{'trial_key': 'entry-000-repeat-000', 'snapshot_sha256': 'f' * 64},
                          {'trial_key': 'entry-000-repeat-001', 'snapshot_sha256': None}])
        self.assertEqual(experiment['comparison_identity']['protocol'], 'assistive/1')
        page = self.call('suite_events', suite_id=suite_id, after=0)
        self.assertEqual(sorted(page), ['cursor', 'events', 'ok', 'suite_id'])
        self.assertEqual([event['kind'] for event in page['events']],
                         ['suite.started', 'suite.running', 'trial.launching', 'trial.started', 'trial.finished'])
        self.assertEqual(sorted(page['events'][0]), ['data', 'id', 'kind', 'time'])
        detached = self.call('suite_detach', **self.driving(suite_id, reason='trial x failed'))
        self.assertEqual(sorted(detached), sorted(status))
        self.assertEqual((detached['status'], detached['reason']),
                         ('blocked', 'The bench harness stopped driving the suite: trial x failed'))
        cancelled = self.call('suite_cancel', suite_id=suite_id)
        self.assertEqual(sorted(cancelled), sorted(status))
        self.assertEqual((cancelled['status'], cancelled['stopping']), ('cancelled', False))

    def test_07_B8_each_refusal_has_the_text_it_had(self):
        suite_id = self.start()
        self.assertEqual(self.refused('suite_status', suite_id='suite-unknown'), 'Unknown suite_id')
        self.assertEqual(self.refused('suite_never', suite_id=suite_id), 'Unknown suite method: suite_never')
        self.assertEqual(self.refused('suite_start', manifest=scripted_manifest(), executor='codex', options={}),
                         'A suite is started and driven by its bench harness: `benchmark suite start`')
        self.assertEqual(self.refused('suite_start', manifest=scripted_manifest(), executor='codex', options={},
                                      harness_token_sha256=TOKEN_SHA256, harness_pid=0),
                         'harness_pid must be the positive process id of the bench harness')
        self.assertEqual(self.refused('suite_start', manifest=scripted_manifest(), executor='codex', options={},
                                      harness_token_sha256=TOKEN_SHA256, harness_pid=os.getpid(),
                                      harness_lock='relative.lock'), 'harness_lock must be an absolute path')
        self.assertEqual(self.refused('suite_start', manifest=scripted_manifest(3), executor='codex', options={},
                                      request_id='book-suite', harness_token_sha256=TOKEN_SHA256,
                                      harness_pid=os.getpid()),
                         'request_id was already used with different suite inputs')
        self.assertIn('belong to the bench harness', self.refused(
            'suite_start', manifest=scripted_manifest(), executor='codex', options={'renode': '/an/emulator'},
            harness_token_sha256=TOKEN_SHA256, harness_pid=os.getpid()))
        self.assertEqual(self.refused('suite_settle', **self.driving(suite_id, ordinal=7)),
                         'Unknown or stale suite slot')
        self.assertEqual(self.refused('suite_attach', **self.driving(suite_id, ordinal=0, run_id='run-x')),
                         'The suite slot is not reserved')
        reserved = self.reserve(suite_id)
        self.assertEqual(self.refused('suite_attach', **self.driving(suite_id, ordinal=0, run_id='run-x')),
                         'The run is not the child of this suite slot')
        self.assertEqual(self.refused('suite_settle', **self.driving(suite_id, ordinal=0)),
                         'Settlement does not match attached child')
        self.assertEqual(self.refused('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id='run-x')),
                         'The run is not the attached child of this suite slot')
        self.assertEqual(self.refused('suite_start_child', **self.driving(suite_id, ordinal=1,
                                                                          request=reserved['request'])),
                         'The suite slot is not reserved')
        self.assertEqual(self.refused('suite_no_run', **self.driving(suite_id, ordinal=0, reason='start-refused')),
                         'Only a scoring suite records this: ' + suite_id)
        self.assertEqual(self.refused('suite_flag', **self.driving(suite_id, ordinal=0, findings=[])),
                         'Only a scoring suite records this: ' + suite_id)
        self.assertEqual(self.refused('suite_sealed', suite_id=suite_id, ordinal=0, run_id='run-x', token=TOKEN),
                         'Only a scoring suite seals its trials: ' + suite_id)
        self.assertEqual(self.refused('suite_resume', suite_id=suite_id, harness_token_sha256='b' * 64,
                                      harness_pid=os.getpid()), 'Only a stopped incomplete suite can resume')
        self.assertEqual(self.refused('suite_result', suite_id=suite_id, limit=0), 'Invalid suite trial pagination')
        self.assertEqual(self.refused('suite_events', suite_id=suite_id, after=-1), 'Invalid suite event cursor')
        # A stopped suite whose harness still runs has not let go of it yet.
        self.book.store.set_state(suite_id, 'blocked')
        self.assertEqual(self.refused('suite_resume', suite_id=suite_id, harness_token_sha256='b' * 64,
                                      harness_pid=os.getpid()), 'Previous suite work is still stopping')
        self.book.store.set_state(suite_id, 'running')
        self.call('suite_detach', **self.driving(suite_id))
        self.call('suite_cancel', suite_id=suite_id)
        self.assertEqual(self.refused('suite_start_child', **self.driving(
            suite_id, ordinal=0, request={**reserved['request'], **added_by_the_harness(reserved['request'])})),
            'The suite is cancelled; no child starts for it')
        self.assertEqual(self.refused('suite_resume', suite_id=suite_id, harness_pid=os.getpid()),
                         'A suite resumes through its bench harness: `benchmark suite resume`')

    def test_07_B8_the_calls_of_a_scoring_suite_answer_and_refuse_as_they_did(self):
        suite_id = self.stored(scoring=True)
        slot = self.reserve(suite_id)['slot']
        self.assertEqual(self.refused('suite_no_run', **self.driving(suite_id, ordinal=0, reason='it broke')),
                         'Not a reason a slot got no run: it broke')
        self.assertEqual(self.refused('suite_flag', **self.driving(suite_id, ordinal=0, findings=[{'x': 1}])),
                         'A flag names a slot with a child and findings of a transcript and a command hash')
        self.assertEqual(self.call('suite_no_run', **self.driving(suite_id, ordinal=0, reason='provider-failed')),
                         {'ok': True, 'suite_id': suite_id, 'decision': 'fault'})
        [row] = self.call('suite_result', suite_id=suite_id)['trials']
        self.assertEqual((row['status'], row['no_run_reason'], row['first_attempt']),
                         ('faulted', 'provider-failed', {'run_id': None, 'request_id': slot['child_request_id'],
                                                         'outcome': 'fault', 'tokens': None, 'seconds': None}))
        # The faulted slot comes back for its one rerun, under a request id of its own.
        again = self.reserve(suite_id)
        self.assertEqual((again['slot']['attempt'], again['slot']['child_request_id']),
                         (2, slot['child_request_id'] + ':rerun'))
        started, _ = self.start_child(suite_id, again)
        run_id = started['run_id']
        self.assertEqual(self.refused('suite_sealed', suite_id=suite_id, ordinal=0, run_id='run-other', token=TOKEN),
                         'The run is not a trial of this suite slot: run-other')
        self.assertEqual(self.refused('suite_sealed', suite_id=suite_id, ordinal=0, run_id=run_id), HARNESS_TOKEN)
        self.assertEqual(self.call('suite_sealed', suite_id=suite_id, ordinal=0, run_id=run_id, token=TOKEN),
                         {'ok': True, 'suite_id': suite_id, 'run_id': run_id})
        self.service.end(run_id, 'failed', 'model')
        findings = [{'transcript': 'acquire/_agent_events.jsonl', 'command_sha256': SHA}]
        self.assertEqual(self.call('suite_flag', **self.driving(suite_id, ordinal=0, findings=findings)),
                         {'ok': True, 'suite_id': suite_id, 'decision': 'flag'})
        [row] = self.call('suite_result', suite_id=suite_id)['trials']
        self.assertEqual((row['status'], row['audit']), ('flagged', findings))

    def test_07_B8_a_call_that_needed_the_harness_token_needs_it(self):
        suite_id, run_id = self.running()
        for method, params in (('suite_reserve', {}), ('suite_attach', {'ordinal': 0, 'run_id': run_id}),
                               ('suite_start_child', {'ordinal': 0, 'request': {}}),
                               ('suite_resume_child', {'ordinal': 0, 'run_id': run_id}),
                               ('suite_settle', {'ordinal': 0}), ('suite_no_run', {'ordinal': 0}),
                               ('suite_flag', {'ordinal': 0}), ('suite_detach', {})):
            for token in ({}, {'token': 'another token'}, {'token': TOKEN_SHA256}, {'token': None}):
                with self.subTest(method=method, token=token):
                    self.assertEqual(self.refused(method, suite_id=suite_id, **params, **token), HARNESS_TOKEN)
        for method in ('suite_status', 'suite_events', 'suite_result'):
            with self.subTest(method=method):
                self.assertIs(self.call(method, suite_id=suite_id)['ok'], True)
        self.assertEqual(self.status(suite_id)['status'], 'running')
        # The book holds the token's hash alone.
        with contextlib.closing(sqlite3.connect(self.bench / 'suites.sqlite3')) as db:
            stored = json.dumps([list(row) for row in db.execute('SELECT * FROM suites')])
        self.assertIn(TOKEN_SHA256, stored)
        self.assertNotIn(TOKEN, stored)

    def test_07_B8_a_suite_whose_harness_no_longer_runs_is_blocked_and_detached_at_the_next_call(self):
        # A harness process that has ended: its process id names no process any more.
        ended = subprocess.Popen([sys.executable, '-c', 'pass'])
        ended.wait()
        reply = self.call('suite_start', manifest=scripted_manifest(), executor='codex', options={},
                          request_id='ended', harness_token_sha256=TOKEN_SHA256, harness_pid=ended.pid)
        suite_id = reply['suite_id']
        status = self.status(suite_id)
        self.assertEqual((status['status'], status['reason'], status['stopping']),
                         ('blocked', 'The bench harness driving the suite ended; resume it through its harness', False))
        self.assertEqual([kind for kind, _ in self.events(suite_id)][-2:], ['suite.blocked', 'suite.harness_detached'])
        self.assertIs(self.book.store.get(suite_id)['harness']['attached'], False)
        # Once.
        self.status(suite_id)
        self.assertEqual([kind for kind, _ in self.events(suite_id)].count('suite.blocked'), 1)

    def test_07_B8_a_book_in_the_harness_own_process_finds_its_own_harness_alive(self):
        from generative_driver.client import hold_process_lock
        lock = self.root / 'harness.lock'
        held = hold_process_lock(lock)
        self.addCleanup(lambda: held and held.close())
        if held is None:
            self.skipTest('This file system offers no process lock')
        # The process id names no process; only the lock this process holds says that the harness runs.
        ended = subprocess.Popen([sys.executable, '-c', 'pass'])
        ended.wait()
        reply = self.call('suite_start', manifest=scripted_manifest(), executor='codex', options={},
                          request_id='own', harness_token_sha256=TOKEN_SHA256, harness_pid=ended.pid,
                          harness_lock=str(lock))
        self.assertEqual(self.status(reply['suite_id'])['status'], 'running')
        self.assertIs(self.reserve(reply['suite_id'])['slot']['run_id'], None)
        held.close()
        self.assertEqual(self.status(reply['suite_id'])['status'], 'blocked')

    ASKING = ('import json, sys\n'
              'from pathlib import Path\n'
              'from generative_driver.benchmark_support.suite_book import SuiteBook\n'
              'home, method, params = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n'
              'def service(name, request=None):\n'
              '    if name == "ping":\n'
              '        return {"ok": True, "pid": 4001}\n'
              '    if name == "status" and (request or {}).get("run_id"):\n'
              '        return {"ok": True, "run_id": request["run_id"], "status": "running", "stopping": False,\n'
              '                "equivalence": None, "uncertain_effect": False, "outcome_category": "unknown"}\n'
              '    return {"ok": False, "error": "ValueError", "reason": "Unknown request_id"}\n'
              'try:\n'
              '    reply = SuiteBook(Path(home), service).call(method, params)\n'
              'except ValueError as error:\n'
              '    reply = {"refused": str(error)}\n'
              'print(json.dumps(reply))\n')

    def asking(self, method, **params):
        """Another process that sends one suite call to a book of its own on the same home."""
        return subprocess.Popen([sys.executable, '-c', self.ASKING, str(self.home), method, json.dumps(params)],
                                env={**os.environ, **package_env()}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

    def answer(self, process, timeout=60):
        out, err = process.communicate(timeout=timeout)
        self.assertEqual(process.returncode, 0, err)
        return json.loads(out)

    def test_07_B8_two_processes_that_ask_for_the_same_suite_are_served_one_after_the_other(self):
        suite_id, run_id = self.running()
        entered, release = threading.Event(), threading.Event()
        answer = self.service._status

        def slow_status(params):
            # The book asks the child's status while it holds the suite's lock.
            entered.set()
            self.assertTrue(release.wait(60))
            return answer(params)
        self.service._status = slow_status
        first = {}
        thread = threading.Thread(target=lambda: first.update(reply=self.status(suite_id)), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(30))
        other = self.asking('suite_status', suite_id=suite_id)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                other.communicate(timeout=1.5)
            self.assertNotIn('reply', first)
        finally:
            release.set()
        thread.join(30)
        self.assertEqual(first['reply']['status'], 'running')
        self.assertEqual(self.answer(other)['status'], 'running')
        # Another suite's call is not held up by this suite's lock.
        entered.clear()
        release.clear()
        thread = threading.Thread(target=lambda: self.status(suite_id), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(30))
        try:
            self.assertEqual(self.answer(self.asking('suite_status', suite_id=self.stored()))['status'], 'running')
        finally:
            release.set()
            thread.join(30)

    def test_07_B8_a_slot_is_never_reserved_twice_by_processes_that_ask_at_once(self):
        suite_id = self.start(3)
        entered, release = threading.Event(), threading.Event()
        reserve = self.book.store.reserve

        def slow_reserve(*args, **kwargs):
            # The book reserves the slot while it holds the suite's lock: this reservation stays open, with the slot
            # not yet marked, until the test lets it go on.
            entered.set()
            self.assertTrue(release.wait(60))
            return reserve(*args, **kwargs)
        first = {}
        with patch.object(self.book.store, 'reserve', slow_reserve):
            thread = threading.Thread(target=lambda: first.update(reply=self.reserve(suite_id)), daemon=True)
            thread.start()
            self.assertTrue(entered.wait(30))
            asking = [self.asking('suite_reserve', **self.driving(suite_id)) for _ in range(3)]
            try:
                # Every other process asks while that reservation is open, and none is served before it is done.
                with self.assertRaises(subprocess.TimeoutExpired):
                    asking[0].communicate(timeout=3)
                self.assertEqual([process.poll() for process in asking], [None, None, None])
                self.assertNotIn('reply', first)
                self.assertEqual([trial['status'] for trial in self.book.store.get(suite_id)['trials']],
                                 ['pending', 'pending', 'pending'])
            finally:
                release.set()
                thread.join(30)
        replies = [first['reply'], *(self.answer(process) for process in asking)]
        self.assertEqual([reply['slot']['ordinal'] for reply in replies], [0, 0, 0, 0], replies)
        self.assertEqual([kind for kind, _ in self.events(suite_id)].count('trial.launching'), 1)
        self.assertEqual([trial['status'] for trial in self.book.store.get(suite_id)['trials']],
                         ['launching', 'pending', 'pending'])

    def stored_rows(self, bench=None):
        """Every row of the three tables of the store under `bench`."""
        with contextlib.closing(sqlite3.connect((bench or self.bench) / 'suites.sqlite3')) as db:
            return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1, 2').fetchall()
                    for table in ('suites', 'suite_trials', 'suite_events')}

    def refused_for_its_lock(self, lock, method, **params):
        """The words between the refusal's start and the lock's path, of a call the book refuses because it cannot
        hold `lock`: it wrote nothing to the store and sent the service nothing but the `ping` every suite call asks
        first, and the refusal reaches a caller in the service's form."""
        before, sent = self.stored_rows(), len(self.service.requests)
        text = self.refused(method, **params)
        self.assertEqual(suite_book.answered(self.book, method, params),
                         {'ok': False, 'error': 'ValueError', 'reason': text})
        self.assertEqual(self.stored_rows(), before)
        self.assertEqual(self.service.sent()[sent:], ['ping', 'ping'])
        start, end = 'Another suite call may be running: its lock cannot be taken (', '): ' + str(lock)
        self.assertTrue(text.startswith(start) and text.endswith(end), text)
        return text[len(start):-len(end)]

    def test_07_B8_a_lock_that_cannot_be_made_opened_or_taken_refuses_the_call_and_nothing_goes_on_without_it(self):
        suite_id = self.start(3)
        store_lock = trial_store.suite_lock_path(self.home)
        suite_lock = trial_store.suite_lock_path(self.home, suite_id)
        another = {'manifest': scripted_manifest(), 'executor': 'codex', 'options': {}, 'request_id': 'another',
                   'harness_token_sha256': TOKEN_SHA256, 'harness_pid': os.getpid()}
        # The file system can neither take nor test the lock.
        with patch.object(harness, '_take', side_effect=OSError(errno.ENOLCK, 'No locks available')):
            self.assertEqual(self.refused_for_its_lock(suite_lock, 'suite_reserve', **self.driving(suite_id)),
                             'OSError: No locks available')
            self.assertEqual(self.refused_for_its_lock(suite_lock, 'suite_status', suite_id=suite_id),
                             'OSError: No locks available')
            self.assertEqual(self.refused_for_its_lock(store_lock, 'suite_start', **another),
                             'OSError: No locks available')
        # The lock file cannot be opened: a folder lies where it would be.
        suite_lock.unlink(missing_ok=True)
        suite_lock.mkdir()
        self.assertRegex(self.refused_for_its_lock(suite_lock, 'suite_reserve', **self.driving(suite_id)),
                         r'^\w+Error: .+')
        suite_lock.rmdir()
        # Each refusal let go of the lock among this process's threads: another thread is served.
        reply = {}
        thread = threading.Thread(target=lambda: reply.update(self.reserve(suite_id)), daemon=True)
        thread.start()
        thread.join(30)
        self.assertEqual(reply['slot']['ordinal'], 0, reply)
        # The folder of the locks cannot be made: a file lies where it would be. No store is made either.
        home = self.root / 'other'
        home.mkdir(mode=0o700)
        bench = trial_store.bench_home(home)
        bench.mkdir(mode=0o700)
        (bench / 'suite-locks').write_text('', encoding='utf-8')
        sent = len(self.service.requests)
        with self.assertRaises(ValueError) as refusal:
            SuiteBook(home, self.service).call('suite_start', another)
        self.assertRegex(str(refusal.exception), r'^Another suite call may be running: its lock cannot be taken '
                         r'\(\w+Error: .+\): ' + re.escape(str(trial_store.suite_lock_path(home))) + '$')
        self.assertEqual(sorted(path.name for path in bench.iterdir()), ['suite-locks'])
        self.assertEqual(self.service.sent()[sent:], ['ping'])

    def test_07_B8_suite_start_finds_an_earlier_suite_of_the_same_request_under_a_lock_of_the_whole_store(self):
        request = {'manifest': scripted_manifest(), 'executor': 'codex', 'options': {}, 'request_id': 'same-request',
                   'harness_token_sha256': TOKEN_SHA256, 'harness_pid': os.getpid()}
        asking = [self.asking('suite_start', **request) for _ in range(4)]
        replies = [self.answer(process) for process in asking]
        self.assertEqual(sorted(reply['duplicate'] for reply in replies), [False, True, True, True], replies)
        self.assertEqual(len({reply['suite_id'] for reply in replies}), 1)
        with contextlib.closing(sqlite3.connect(self.bench / 'suites.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suites').fetchone(), (1,))
        # While one start freezes its suite, under the lock of the whole store, another process's start waits.
        entered, release = threading.Event(), threading.Event()
        freeze = suites.freeze_suite

        def slow_freeze(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(60))
            return freeze(*args, **kwargs)
        with patch.object(suites, 'freeze_suite', slow_freeze):
            thread = threading.Thread(target=lambda: self.start(request_id='held-here'), daemon=True)
            thread.start()
            self.assertTrue(entered.wait(30))
            other = self.asking('suite_start', **{**request, 'request_id': 'another-request'})
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    other.communicate(timeout=1.5)
            finally:
                release.set()
            thread.join(30)
        self.assertIs(self.answer(other)['duplicate'], False)

    def test_04_B5_a_suite_reply_built_from_a_childs_status_stays_as_it_was(self):
        from generative_driver.configurator import Controller
        controller = Controller(self.root / 'service-home')
        self.addCleanup(controller.close)
        with patch.object(controller, '_spawn'):
            run = controller.call('start', {'goal': 'Read the gauge', 'executor_config': {'command': ['c']}})
        controller.call('cancel', run)
        status = controller.call('status', run)
        self.assertEqual([key for key in ADDED if key not in status], [])
        # A suite's reply to its harness that passes its child's status on, as a resumed child's does.
        with patch.object(SuiteBook, '_suite_call', return_value={**status, 'duplicate': True}):
            reply = self.book.call('suite_resume_child', {'suite_id': 'suite-x'})
        self.assertEqual(set(reply), {'ok', 'status', 'stage', 'reason', 'created', 'updated', 'outcome_category',
                                      'run_id', 'uncertain_effect', 'stopping', 'equivalence', 'stage_notes',
                                      'duplicate'})
        self.assertEqual(reply, {key: value for key, value in status.items() if key in reply} | {'duplicate': True})

    def test_07_B8_a_reply_that_passes_a_childs_status_on_has_none_of_the_keys_a_status_gained(self):
        suite_id, run_id = self.running()
        self.assertEqual([key for key in ADDED if key not in self.service('status', {'run_id': run_id})], [])
        self.service.end(run_id, 'blocked', 'operator')
        self.service.event(run_id, 'device.attached', {'session_id': 'session-2'})
        resumed = self.call('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id=run_id,
                                                                session_id='session-2'))
        self.assertEqual((resumed['ok'], resumed['status'], resumed['run_id']), (True, 'queued', run_id))
        self.assertEqual([key for key in ADDED if key in resumed], [])
        again = self.call('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id=run_id,
                                                              session_id='session-2'))
        self.assertIs(again['duplicate'], True)
        self.assertEqual([key for key in ADDED if key in again], [])
        self.assertEqual(self.service.sent().count('resume'), 1)

    def test_07_B8_a_suite_frozen_before_the_harness_drove_suites_is_still_read_only(self):
        old = ('This suite was frozen before the bench harness drove suites (it holds evaluator password files or no '
               'scheduler) and is read-only: start a new suite')
        self.assertEqual(suite_book.OLD_SUITE, old)
        suite_id = self.stored(scheduler=None)
        for method, params in (('suite_reserve', {}), ('suite_attach', {'ordinal': 0, 'run_id': 'run-x'}),
                               ('suite_start_child', {'ordinal': 0, 'request': {}}),
                               ('suite_resume_child', {'ordinal': 0, 'run_id': 'run-x'}),
                               ('suite_settle', {'ordinal': 0}), ('suite_detach', {})):
            with self.subTest(method=method):
                self.assertEqual(self.refused(method, **self.driving(suite_id, **params)), old)
        self.assertEqual(self.refused('suite_resume', suite_id=suite_id, harness_token_sha256='b' * 64,
                                      harness_pid=os.getpid()), old)
        status = self.status(suite_id)
        self.assertEqual((status['status'], status['equivalent']), ('running', None))
        self.assertNotIn('protocol', self.call('suite_result', suite_id=suite_id)['experiment']['comparison_identity'])


class PublicCallTests(BookCase):
    """The book asks the service only through public calls (07-B9)."""

    def test_07_B9_every_suite_call_sends_only_the_eight_public_calls(self):
        served = set()

        def call(method, **params):
            served.add(method)
            try:
                return self.book.call(method, params)
            except ValueError:
                return None
        suite_id = self.start(3)
        served.add('suite_start')
        reserved = call('suite_reserve', **self.driving(suite_id))
        request = {**reserved['request'], **added_by_the_harness(reserved['request'])}
        run_id = call('suite_start_child', **self.driving(suite_id, ordinal=0, request=request))['run_id']
        call('suite_attach', **self.driving(suite_id, ordinal=0, run_id=run_id))
        call('suite_status', suite_id=suite_id)
        call('suite_events', suite_id=suite_id)
        call('suite_result', suite_id=suite_id)
        self.service.end(run_id, 'blocked', 'operator')
        self.service.event(run_id, 'device.attached', {'session_id': 'session-2'})
        call('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id=run_id, session_id='session-2'))
        self.service.end(run_id, equivalence=GRADED)
        call('suite_settle', **self.driving(suite_id, ordinal=0))
        call('suite_detach', **self.driving(suite_id))
        call('suite_resume', suite_id=suite_id, harness_token_sha256=TOKEN_SHA256, harness_pid=os.getpid())
        second = call('suite_reserve', **self.driving(suite_id))
        request = {**second['request'], **added_by_the_harness(second['request'])}
        running = call('suite_start_child', **self.driving(suite_id, ordinal=1, request=request))['run_id']
        # Its harness lets go first, so that the cancellation does not wait for it.
        call('suite_detach', **self.driving(suite_id))
        call('suite_cancel', suite_id=suite_id)
        self.assertEqual(self.service.runs[running]['status'], 'cancelled')
        scoring = self.stored(scoring=True)
        slot = call('suite_reserve', **self.driving(scoring))
        child = {**slot['request'], **added_by_the_harness(slot['request'])}
        scored = call('suite_start_child', **self.driving(scoring, ordinal=0, request=child))['run_id']
        call('suite_sealed', suite_id=scoring, ordinal=0, run_id=scored, token=TOKEN)
        self.service.end(scored, 'failed', 'model')
        call('suite_flag', **self.driving(scoring, ordinal=0, findings=[{'transcript': 't', 'command_sha256': SHA}]))
        call('suite_no_run', **self.driving(scoring, ordinal=0, reason='start-refused'))
        self.assertEqual(served, set(SUITE_CALLS))
        self.assertEqual(set(self.service.sent()) - PUBLIC, set())
        self.assertEqual(set(self.service.sent()), PUBLIC)

    def test_07_B9_the_book_imports_nothing_from_the_configurator(self):
        tree = ast.parse(Path(suite_book.__file__).read_bytes())
        imported = [(node.module or '', alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names]
        imported += [(alias.name, None) for node in ast.walk(tree) if isinstance(node, ast.Import)
                     for alias in node.names]
        # The scan sees the book's imports: it takes the lock helper from the benchmark's own module (08-B12 and
        # 08-B13 of docs/features/untangle/08-reports.md).
        self.assertTrue(any(module == 'process_lock' and name == 'process_lock_held' for module, name in imported))
        self.assertEqual([entry for entry in imported if 'configurator' in entry[0] or entry[1] == 'configurator'], [])

    def test_07_B9_a_child_starts_with_a_plain_start_of_the_slots_request_without_the_case(self):
        tools = {'ghidra_home': str(self.root / 'ghidra'), 'java_home': str(self.root / 'java')}
        suite_id = self.start(options={**tools, 'scoped_tool_approval': 'emulator'})
        reserved = self.reserve(suite_id)
        self.assertEqual({key: reserved['request']['case_options'][key] for key in tools}, tools)
        started, request = self.start_child(suite_id, reserved)
        [start] = self.service.sent('start')
        self.assertEqual(start, plain_start(request))
        self.assertEqual([key for key in ('case', 'case_pin', 'case_options') if key in start], [])
        self.assertEqual({key: start[key] for key in tools}, tools)
        self.assertEqual(start['request_id'], reserved['slot']['child_request_id'])
        self.assertEqual(start['client_record']['case'], CASE)
        self.assertEqual(self.service.runs[started['run_id']]['request'], start)

    def test_07_B9_it_finds_a_run_by_a_slots_request_id_with_status(self):
        suite_id = self.start()
        reserved, run_id = self.outside(suite_id)
        mark = len(self.service.requests)
        self.assertEqual(self.reserve(suite_id)['slot']['run_id'], run_id)
        self.assertIn({'request_id': reserved['slot']['child_request_id']}, self.service.sent('status')[-3:])
        self.assertNotIn('start', self.since(mark))

    def test_07_B9_it_marks_a_run_sealed_with_seal(self):
        suite_id = self.stored(scoring=True)
        started, _ = self.start_child(suite_id)
        self.call('suite_sealed', suite_id=suite_id, ordinal=0, run_id=started['run_id'], token=TOKEN)
        self.assertEqual(self.service.sent('seal'), [{'run_id': started['run_id']}])
        self.assertIs(self.service('status', {'run_id': started['run_id']})['sealed'], True)

    def test_07_B9_the_budget_and_client_record_come_from_result_the_verdict_from_status_the_rest_from_events(self):
        suite_id = self.stored(scoring=True)
        started, _ = self.start_child(suite_id)
        run_id = started['run_id']
        # An unscored evaluation, read from the child's events: the grade is graded again once.
        self.service.end(run_id)
        self.service.event(run_id, 'evaluation.unscored',
                           {'diff': [{'id': 'final', 'reason': 'evaluator host fault: X'}]})
        mark = len(self.service.requests)
        settled = self.call('suite_settle', **self.driving(suite_id, ordinal=0))
        self.assertEqual(settled['decision'], 'regrade')
        self.assertLessEqual({'status', 'events', 'result'}, set(self.since(mark)))
        # The budget the report calls effective is the child's own, from `result`.
        self.service.runs[run_id].update(budget_seconds=4, updated=self.service.runs[run_id]['created'] + 50,
                                         status='failed', outcome_category='model')
        self.reserve(suite_id)
        self.assertEqual(self.call('suite_settle', **self.driving(suite_id, ordinal=0))['decision'], 'advance')
        # The snapshot hash of the experiment record is the one in the child's client record, from `result`.
        experiment = self.call('suite_result', suite_id=suite_id)['experiment']
        self.assertEqual(experiment['execution_snapshots'][0]['snapshot_sha256'], 'f' * 64)
        self.assertEqual(experiment['comparison_identity']['budgets']['effective']['child_budget_overrides'],
                         [{'trial_key': 'entry-000-repeat-000', 'budget_seconds': 4}])

    def test_07_B9_in_the_harness_only_the_four_starting_suite_calls_may_start_a_service(self):
        sent = []

        def client_call(method, params=None, home=None, *, autostart=True, read_timeout=None):
            sent.append((method, autostart))
            return self.service(method, params)
        with patch('generative_driver.client.call', client_call):
            configurator = harness._configurator(self.home)
            for method in SUITE_CALLS:
                del sent[:]
                reply = configurator(method, {'suite_id': 'suite-unknown'})
                self.assertIs(reply['ok'], False)
                with self.subTest(method=method):
                    self.assertEqual(sent[0][0], 'ping')
                    self.assertEqual({autostart for _, autostart in sent},
                                     {method in ('suite_start', 'suite_resume', 'suite_start_child',
                                                 'suite_resume_child')})
            # A harness told to start no service starts none, whatever suite call the book serves.
            quiet = harness._configurator(self.home, autostart=False)
            for method in SUITE_CALLS:
                del sent[:]
                quiet(method, {'suite_id': 'suite-unknown'})
                self.assertEqual({autostart for _, autostart in sent}, {False}, method)

    def test_07_B9_a_command_may_start_a_service_where_its_suite_call_could_and_a_reader_may_not(self):
        sent = []

        def client_call(method, params=None, home=None, *, autostart=True, read_timeout=None):
            sent.append((method, autostart, Path(home)))
            return self.service(method, params)
        suite_id = self.start()
        with patch('generative_driver.client.call', client_call):
            for autostart in (True, False):
                del sent[:]
                reply = suite_book.suite_call('suite_status', {'suite_id': suite_id}, home=self.home,
                                              autostart=autostart)
                self.assertEqual(reply['status'], 'running')
                self.assertEqual(sent, [('ping', autostart, self.home)])
            del sent[:]
            with self.assertRaisesRegex(ValueError, 'measured only once nothing of it runs'):
                suites.refuse_unsettled_scoring(suite_id, self.home)
            self.assertEqual({autostart for _, autostart, _ in sent}, {False})
        source = (Path(suite_book.__file__).parent.parent / 'benchmark.py').read_text(encoding='utf-8')
        self.assertIn("suite_call('suite_' + args.action, params)", source)
        self.assertEqual(suite_book.suite_call.__kwdefaults__, {'autostart': True})


class PassingTests(BookCase):
    """Refusals and lost answers pass through the book unchanged (07-B10)."""

    def test_07_B10_a_request_the_service_refuses_ends_the_suite_call_as_that_refusal(self):
        suite_id = self.start()
        reserved = self.reserve(suite_id)
        self.service.refused['start'] = 'Invalid device descriptor: no session_id'
        request = {**reserved['request'], **added_by_the_harness(reserved['request'])}
        reply = self.call('suite_start_child', **self.driving(suite_id, ordinal=0, request=request))
        self.assertEqual(reply, {'ok': False, 'error': 'ValueError',
                                 'reason': 'Invalid device descriptor: no session_id'})
        # The suite goes on: the slot stays reserved for the harness, which records the trial's fault itself.
        self.assertEqual(self.status(suite_id)['status'], 'running')
        self.assertEqual(self.reserve(suite_id)['slot'], reserved['slot'])
        started, _ = self.start_child(suite_id, reserved)
        self.service.end(started['run_id'], 'blocked', 'operator')
        self.service.refused['resume'] = 'Previous worker is still stopping'
        self.assertEqual(self.call('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id=started['run_id'],
                                                                      session_id='session-2')),
                         {'ok': False, 'error': 'ValueError', 'reason': 'Previous worker is still stopping'})

    def test_07_B10_a_request_that_gets_no_answer_ends_the_suite_call_with_the_clients_reply_which_has_no_error(self):
        suite_id, run_id = self.running()
        for method, sends in (('suite_status', 'status'), ('suite_result', 'events'), ('suite_settle', 'status'),
                              ('suite_cancel', 'cancel')):
            with self.subTest(method=method):
                self.service.lost[sends] = 1
                reply = self.call(method, **self.driving(suite_id, ordinal=0))
                self.assertEqual(reply, LOST)
                self.assertNotIn('error', reply)
        # A callable that raises is no answer either, in the words the harness gives one.
        def raising(method, params=None):
            raise TimeoutError('Another client is starting the configurator')
        reply = SuiteBook(self.home, raising).call('suite_status', {'suite_id': suite_id})
        self.assertEqual(reply, {'ok': False, 'reason': 'TimeoutError: Another client is starting the configurator'})

    def test_07_B10_the_books_own_refusal_is_a_value_error_and_reaches_a_caller_in_the_services_form(self):
        suite_id = self.start()
        with self.assertRaises(ValueError) as raised:
            self.book.call('suite_reserve', {'suite_id': suite_id, 'token': 'another'})
        self.assertEqual(str(raised.exception), HARNESS_TOKEN)
        refusal = {'ok': False, 'error': 'ValueError', 'reason': HARNESS_TOKEN}
        client_call = lambda method, params=None, home=None, **options: self.service(method, params)
        with patch('generative_driver.client.call', client_call):
            # A command or a reader, and the harness's one callable for its requests.
            self.assertEqual(suite_book.suite_call('suite_reserve', {'suite_id': suite_id, 'token': 'another'},
                                                   home=self.home), refusal)
            self.assertEqual(harness._configurator(self.home)('suite_reserve', {'suite_id': suite_id, 'token': 'x'}),
                             refusal)
            self.assertEqual(suite_book.suite_call('suite_status', {'suite_id': 'suite-unknown'}, home=self.home),
                             {'ok': False, 'error': 'ValueError', 'reason': 'Unknown suite_id'})

    def test_07_B10_a_child_start_that_got_no_answer_sent_again_finds_the_child_and_starts_nothing_twice(self):
        suite_id = self.start()
        reserved = self.reserve(suite_id)
        self.service.lost['start'] = 1
        reply, request = self.start_child(suite_id, reserved)
        self.assertEqual(reply, LOST)
        self.assertEqual(len(self.service.runs), 1)
        self.assertIsNone(self.book.store.get(suite_id)['trials'][0]['run_id'])
        again = self.call('suite_start_child', **self.driving(suite_id, ordinal=0, request=request))
        [run_id] = self.service.runs
        self.assertEqual(again, {'ok': True, 'run_id': run_id, 'duplicate': True, 'suite_id': suite_id})
        self.assertEqual(len(self.service.sent('start')), 1)
        self.assertEqual(self.book.store.get(suite_id)['trials'][0]['run_id'], run_id)
        # Whatever the suite did since: a cancelled suite still answers the same start with the child it made.
        self.call('suite_detach', **self.driving(suite_id))
        self.call('suite_cancel', suite_id=suite_id)
        self.assertEqual(self.status(suite_id)['status'], 'cancelled')
        self.assertEqual(self.call('suite_start_child', **self.driving(suite_id, ordinal=0, request=request)), again)
        self.assertEqual(len(self.service.sent('start')), 1)

    def test_07_B10_the_harness_sends_again_only_what_it_sends_again_today(self):
        suite_id = self.start()
        reserved = self.reserve(suite_id)
        request = {**reserved['request'], **added_by_the_harness(reserved['request'])}
        client_call = lambda method, params=None, home=None, **options: self.service(method, params)
        scheduling = self.driving(suite_id, ordinal=0)
        with patch('generative_driver.client.call', client_call), patch.object(harness.time, 'sleep'):
            harnessed = harness._configurator(self.home)
            launch = lambda params: harnessed('suite_start_child', {**scheduling, 'request': params})
            # A start that got no answer is sent again, and finds the child the first one made.
            self.service.lost['start'] = 1
            started = harness._start_child(launch, request, threading.Event())
            self.assertEqual((started['ok'], started['duplicate']), (True, True))
            self.assertEqual(len(self.service.sent('start')), 1)
            self.assertEqual(self.service.sent().count('suite_start_child'), 0)
            # A refusal, the service's or the book's own, ends it at once: nothing is sent again.
            self.call('suite_detach', **self.driving(suite_id))
            self.call('suite_cancel', suite_id=suite_id)
            second = {**request, 'request_id': 'another'}
            before = len(self.service.requests)
            refused = harness._start_child(launch, second, threading.Event())
            self.assertEqual((refused['ok'], refused['error']), (False, 'ValueError'))
            self.assertEqual(self.since(before).count('ping'), 1)
        self.assertEqual(len(self.service.runs), 1)


class IdentityTests(BookCase):
    """A child is the slot's own only with the frozen request (07-B11)."""

    def blocked_by(self, suite_id, names='budget_seconds', key='entry-000-repeat-000'):
        """The suite stands blocked for a child whose request differs from the frozen slot in `names`."""
        status = self.status(suite_id)
        text = MISMATCH + key + "'s child request differs from the frozen slot in " + names + TAKEN
        self.assertEqual((status['status'], status['reason']), ('blocked', text))
        return text

    def test_07_B11_a_request_that_differs_from_the_frozen_slot_blocks_the_suite_before_anything_starts(self):
        for name, change in (('budget_seconds', {'budget_seconds': 999}), ('case', {'case': 'another-case'}),
                             ('executor_config', {'executor_config': {'command': ['other'], 'runtime': 'codex'}}),
                             ('never_frozen', {'never_frozen': 1})):
            with self.subTest(differs=name):
                suite_id = self.start(request_id='differs-in-' + name)
                reserved = self.reserve(suite_id)
                request = {**reserved['request'], **added_by_the_harness(reserved['request']), **change}
                text = MISMATCH + "entry-000-repeat-000's child request differs from the frozen slot in " + name
                self.assertEqual(self.refused('suite_start_child',
                                              **self.driving(suite_id, ordinal=0, request=request)), text)
                status = self.status(suite_id)
                self.assertEqual((status['status'], status['reason']), ('blocked', text))
        self.assertEqual(self.service.sent('start'), [])
        suite_id = self.start(request_id='no-request')
        self.reserve(suite_id)
        self.assertEqual(self.refused('suite_start_child', **self.driving(suite_id, ordinal=0, request=None)),
                         MISMATCH + 'entry-000-repeat-000 has no submitted request')

    def test_07_B11_a_run_is_the_slots_child_with_the_slots_child_start_and_its_case_and_pin(self):
        suite_id = self.start()
        reserved, run_id = self.outside(suite_id)
        # A plain start with the slot's request, case and pin is the slot's child: the next reservation attaches it.
        self.assertEqual(self.reserve(suite_id)['slot']['run_id'], run_id)
        self.assertEqual(self.status(suite_id)['status'], 'running')
        self.assertEqual(self.service.runs[run_id]['status'], 'running')
        self.assertIn(('trial.started', {'ordinal': 0, 'run_id': run_id}), self.events(suite_id))

    def test_07_B11_a_run_whose_request_or_client_record_differs_is_not_the_slots_child(self):
        other_pin = lambda request: {**request['client_record'],
                                     'case_pin': {**request['case_pin'], 'case_seed': 99}}
        for names, change in (('budget_seconds', lambda request: {'budget_seconds': 999}),
                              ('goal', lambda request: {'goal': 'Another goal'}),
                              ('case', lambda request: {'client_record': {**request['client_record'],
                                                                           'case': 'another-case'}}),
                              ('case_pin', lambda request: {'client_record': other_pin(request)}),
                              ('case, case_pin', lambda request: {'client_record': {'note': 'no case named'}}),
                              ('case, case_pin', lambda request: {'client_record': None}),
                              ('budget_seconds, case', lambda request: {
                                  'budget_seconds': 5, 'client_record': {**request['client_record'], 'case': 'x'}}),
                              ('ghidra_home', lambda request: {'ghidra_home': '/a/ghidra'})):
            with self.subTest(differs=names):
                suite_id = self.start(request_id='differs-in-' + names + str(len(self.service.runs)))
                reserved = self.reserve(suite_id)
                request = {**reserved['request'], **added_by_the_harness(reserved['request'])}
                start = {**plain_start(request), **change(request)}
                if start['client_record'] is None:
                    del start['client_record']
                run_id = self.service('start', start)['run_id']
                after = self.reserve(suite_id)
                self.assertIsNone(after['slot'])
                self.blocked_by(suite_id, names)
                # The child that took the slot's request id is cancelled, as today.
                self.assertEqual(self.service.runs[run_id]['status'], 'cancelled')
        # What the harness adds is no part of the identity: another device, firmware, mission or token hash.
        suite_id = self.start(request_id='harness-keys')
        reserved, run_id = self.outside(suite_id, device={'schema': 'device-descriptor/2', 'session_id': 'other'},
                                        firmware={'path': '/other.bin', 'sha256': 'b' * 64},
                                        mission={'tasks': {'other': {}}}, evaluation_token_sha256='c' * 64)
        self.assertEqual(self.reserve(suite_id)['slot']['run_id'], run_id)

    def test_07_B11_a_run_with_no_recorded_request_is_not_the_slots_child(self):
        suite_id = self.start()
        reserved, run_id = self.outside(suite_id)
        answer = self.service._result
        # A run an earlier version stored: its `result` holds no `request`.
        self.service._result = lambda params: {key: value for key, value in answer(params).items() if key != 'request'}
        self.assertIsNone(self.reserve(suite_id)['slot'])
        status = self.status(suite_id)
        self.assertEqual((status['status'], status['reason']),
                         ('blocked', MISMATCH + 'entry-000-repeat-000 has no submitted request' + TAKEN))

    def differing(self, **more):
        """(suite id, run id) of a suite whose first slot is attached to a child that then turns out to have been
        started with another budget than the frozen one."""
        suite_id, run_id = self.running(**more)
        self.service.runs[run_id]['request']['budget_seconds'] = 999
        return suite_id, run_id

    def test_07_B11_the_check_runs_at_a_reservation_that_finds_a_child_and_in_suite_attach(self):
        suite_id = self.start()
        reserved, run_id = self.outside(suite_id, budget_seconds=999)
        text = MISMATCH + "entry-000-repeat-000's child request differs from the frozen slot in budget_seconds" + TAKEN
        self.assertEqual(self.refused('suite_attach', **self.driving(suite_id, ordinal=0, run_id=run_id)), text)
        self.blocked_by(suite_id)
        self.assertEqual(self.service.runs[run_id]['status'], 'cancelled')
        self.assertIsNone(self.book.store.get(suite_id)['trials'][0]['run_id'])

    def test_07_B11_the_check_runs_in_suite_resume_child_suite_settle_and_suite_cancel(self):
        text = MISMATCH + "entry-000-repeat-000's child request differs from the frozen slot in budget_seconds" + TAKEN
        suite_id, run_id = self.differing()
        self.service.end(run_id, 'blocked', 'operator')
        self.assertEqual(self.refused('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id=run_id,
                                                                          session_id='s')), text)
        self.assertEqual(self.service.sent('resume'), [])
        suite_id, run_id = self.differing(request_id='settled')
        self.service.end(run_id, equivalence=GRADED)
        self.assertEqual(self.call('suite_settle', **self.driving(suite_id, ordinal=0)),
                         {'ok': True, 'suite_id': suite_id, 'decision': 'block', 'status': 'blocked'})
        self.blocked_by(suite_id)
        # A cancelled suite cancels whatever runs under its slot's request, and attaches only the slot's own child.
        # (Its harness has let go, so that the cancellation does not wait for it.)
        suite_id = self.start(request_id='cancelled')
        reserved, run_id = self.outside(suite_id, budget_seconds=999)
        self.book.store.detach_harness(suite_id)
        self.call('suite_cancel', suite_id=suite_id)
        self.assertEqual(self.service.runs[run_id]['status'], 'cancelled')
        self.assertIsNone(self.book.store.get(suite_id)['trials'][0]['run_id'])
        suite_id = self.start(request_id='cancelled-own')
        reserved, run_id = self.outside(suite_id)
        self.book.store.detach_harness(suite_id)
        self.call('suite_cancel', suite_id=suite_id)
        self.assertEqual(self.book.store.get(suite_id)['trials'][0]['run_id'], run_id)

    def test_07_B11_the_check_runs_in_suite_no_run_and_suite_flag(self):
        text = MISMATCH + "entry-000-repeat-000's child request differs from the frozen slot in budget_seconds" + TAKEN
        suite_id = self.stored(scoring=True)
        reserved, run_id = self.outside(suite_id, budget_seconds=999)
        self.assertEqual(self.refused('suite_no_run', **self.driving(suite_id, ordinal=0, reason='start-refused')),
                         text)
        self.blocked_by(suite_id)
        self.assertEqual(self.service.runs[run_id]['status'], 'cancelled')
        self.book.store.set_state(suite_id, 'cancelled')
        suite_id = self.stored(scoring=True)
        started, _ = self.start_child(suite_id)
        self.service.runs[started['run_id']]['request']['budget_seconds'] = 999
        self.service.end(started['run_id'], 'failed', 'model')
        findings = [{'transcript': 't', 'command_sha256': SHA}]
        self.assertEqual(self.call('suite_flag', **self.driving(suite_id, ordinal=0, findings=findings)),
                         {'ok': True, 'suite_id': suite_id, 'decision': 'block'})
        self.blocked_by(suite_id)

    def test_07_B11_the_check_runs_before_a_resume(self):
        suite_id, run_id = self.differing()
        self.service.end(run_id, 'blocked', 'operator')
        self.call('suite_detach', **self.driving(suite_id))
        text = MISMATCH + "entry-000-repeat-000's child request differs from the frozen slot in budget_seconds" + TAKEN
        self.assertEqual(self.refused('suite_resume', suite_id=suite_id, harness_token_sha256='b' * 64,
                                      harness_pid=os.getpid()), text)
        self.assertEqual(self.status(suite_id)['status'], 'blocked')
        # A slot attached to one run while another holds its request id is no child of the suite either.
        suite_id, run_id = self.running(request_id='another-run')
        self.service.runs[run_id]['request_id'] = 'moved'
        self.call('suite_detach', **self.driving(suite_id))
        self.assertEqual(self.refused('suite_resume', suite_id=suite_id, harness_token_sha256='b' * 64,
                                      harness_pid=os.getpid()), 'Frozen suite child identity mismatch')

    def test_07_B11_start_child_looks_for_the_child_first_and_sends_start_only_when_no_run_has_the_request_id(self):
        suite_id = self.start()
        reserved = self.reserve(suite_id)
        mark = len(self.service.requests)
        started, request = self.start_child(suite_id, reserved)
        sent = self.since(mark)
        self.assertEqual(sent[0], 'ping')
        self.assertLess(sent.index('status'), sent.index('start'))
        self.assertEqual(self.service.requests[mark + sent.index('status')][1],
                         {'request_id': reserved['slot']['child_request_id']})
        # The same start again is answered from the child's recorded request, whatever the suite did since.
        self.call('suite_detach', **self.driving(suite_id))
        mark = len(self.service.requests)
        again = self.call('suite_start_child', **self.driving(suite_id, ordinal=0, request=request))
        self.assertEqual(again, {'ok': True, 'run_id': started['run_id'], 'duplicate': True, 'suite_id': suite_id})
        self.assertEqual(sorted(set(self.since(mark))), ['ping', 'result', 'status'])
        # Another start under the slot's request id, on another device, finds a run that this start did not make:
        # nothing starts, and it is refused as the service refused a request id used with other inputs.
        suite_id = self.start(request_id='other-device')
        reserved = self.reserve(suite_id)
        started, request = self.start_child(suite_id, reserved)
        other = {**request, 'device': {'schema': 'device-descriptor/2', 'session_id': 'another-session'}}
        mark = len(self.service.requests)
        self.assertEqual(self.refused('suite_start_child', **self.driving(suite_id, ordinal=0, request=other)),
                         'request_id was already used with different inputs')
        self.assertNotIn('start', self.since(mark))
        self.assertEqual(self.status(suite_id)['status'], 'running')


class RestartTests(BookCase):
    """A suite does not go on past a restart of the service (07-B12)."""

    def recorded(self, suite_id):
        with contextlib.closing(sqlite3.connect(self.bench / 'suites.sqlite3')) as db:
            columns = [row[1] for row in db.execute('PRAGMA table_info(suites)')]
            row = db.execute('SELECT * FROM suites WHERE id=?', (suite_id,)).fetchone()
        return row[len(columns) - 1]

    def test_07_B12_suite_start_and_suite_resume_record_the_process_id_that_ping_answers_with(self):
        suite_id = self.start()
        self.assertEqual(self.recorded(suite_id), 4001)
        self.call('suite_detach', **self.driving(suite_id))
        self.service.pid = 4002
        resumed = self.call('suite_resume', suite_id=suite_id, harness_token_sha256='b' * 64, harness_pid=os.getpid())
        self.assertEqual((resumed['status'], resumed['attached']), ('running', True))
        self.assertEqual(self.recorded(suite_id), 4002)
        self.assertEqual(self.status(suite_id)['status'], 'running')

    def test_07_B12_every_suite_call_asks_ping_first_and_once(self):
        suite_id, run_id = self.running()
        for method, params in (('suite_status', {}), ('suite_events', {}), ('suite_result', {}),
                               ('suite_reserve', {}), ('suite_settle', {'ordinal': 0}),
                               ('suite_attach', {'ordinal': 0, 'run_id': run_id}), ('suite_detach', {}),
                               ('suite_cancel', {}),
                               ('suite_resume', {'harness_token_sha256': 'b' * 64, 'harness_pid': os.getpid()}),
                               ('suite_never', {})):
            with self.subTest(method=method):
                mark = len(self.service.requests)
                with contextlib.suppress(ValueError):
                    self.book.call(method, {**self.driving(suite_id), **params})
                sent = self.since(mark)
                self.assertEqual((sent[0], sent.count('ping')), ('ping', 1), sent)
        mark = len(self.service.requests)
        self.start(request_id='another')
        self.assertEqual((self.since(mark)[0], self.since(mark).count('ping')), ('ping', 1))

    def test_07_B12_another_process_id_blocks_a_queued_or_running_suite_once_with_the_text_and_the_event(self):
        suite_id, run_id = self.running()
        self.service.pid = 4002
        status = self.status(suite_id)
        self.assertEqual((status['status'], status['reason']), ('blocked', RESTARTED))
        self.assertEqual(self.events(suite_id)[-1], ('suite.recovered', {'reason': RESTARTED}))
        # The slot keeps its child, as before: nothing but the status, the text and the event is recorded.
        self.assertEqual(status['active_child_id'], run_id)
        self.assertIs(self.book.store.get(suite_id)['harness']['attached'], True)
        for _ in range(3):
            self.status(suite_id)
            self.call('suite_result', suite_id=suite_id)
        self.assertEqual([kind for kind, _ in self.events(suite_id)].count('suite.recovered'), 1)
        self.assertEqual([kind for kind, _ in self.events(suite_id)].count('suite.blocked'), 0)

    def test_07_B12_after_a_restart_a_reservation_gives_no_slot_and_a_child_neither_starts_nor_resumes(self):
        suite_id = self.start()
        reserved = self.reserve(suite_id)
        request = {**reserved['request'], **added_by_the_harness(reserved['request'])}
        self.service.pid = 4002
        self.assertEqual(self.refused('suite_start_child', **self.driving(suite_id, ordinal=0, request=request)),
                         'The suite is blocked; no child starts for it')
        self.assertEqual(self.service.sent('start'), [])
        self.assertEqual(self.reserve(suite_id), {'ok': True, 'suite_id': suite_id, 'status': 'blocked', 'slot': None})
        self.assertNotIn('start', self.service.sent())
        # A suite with a running child: its harness's next reservation tells it that the suite is blocked.
        suite_id, run_id = self.running(request_id='with-a-child')
        self.service.pid = 4003
        self.service.end(run_id, 'blocked', 'unknown', reason=RESTARTED)
        self.assertEqual(self.refused('suite_resume_child', **self.driving(suite_id, ordinal=0, run_id=run_id,
                                                                          session_id='s')),
                         'The suite is blocked; its child does not resume')
        self.assertEqual(self.service.sent('resume'), [])
        self.assertEqual(self.call('suite_settle', **self.driving(suite_id, ordinal=0))['decision'], 'block')

    def test_07_B12_a_suite_that_is_not_queued_or_running_or_holds_no_process_id_is_left_as_it_is(self):
        suite_id = self.start()
        self.call('suite_detach', **self.driving(suite_id))
        self.call('suite_cancel', suite_id=suite_id)
        self.service.pid = 4002
        self.assertEqual(self.status(suite_id)['status'], 'cancelled')
        self.assertNotIn('suite.recovered', [kind for kind, _ in self.events(suite_id)])
        # A suite stored without the process id of a service, as a test may store one, is never taken for restarted.
        stored = self.stored()
        self.assertEqual(self.status(stored)['status'], 'running')
        # Nor is a suite when the service's answer names no process id at all.
        suite_id = self.start(request_id='another')
        answer = self.service._ping
        self.service._ping = lambda params: {'ok': True}
        self.assertEqual(self.status(suite_id)['status'], 'running')
        self.service._ping = answer

    def test_07_B12_when_no_service_answers_the_call_fails_as_the_suite_call_failed_before(self):
        suite_id, run_id = self.running()
        self.service.reachable = False
        for method in ('suite_status', 'suite_events', 'suite_result', 'suite_reserve', 'suite_settle',
                       'suite_cancel', 'suite_detach'):
            with self.subTest(method=method):
                mark = len(self.service.requests)
                self.assertEqual(self.call(method, **self.driving(suite_id, ordinal=0)), UNREACHABLE)
                self.assertEqual(self.since(mark), ['ping'])
        self.service.reachable = True
        self.assertEqual(self.status(suite_id)['status'], 'running')

    def test_07_B12_a_child_the_restarted_service_stopped_is_settled_or_resumed_as_today_after_suite_resume(self):
        suite_id, run_id = self.running()
        # The restarted service blocked the child for an explicit resume, and the harness of the suite has ended.
        self.service.pid = 4002
        self.service.end(run_id, 'blocked', 'unknown', reason=RESTARTED)
        self.assertEqual(self.status(suite_id)['status'], 'blocked')
        self.call('suite_detach', **self.driving(suite_id))
        token = 'the token of the resuming harness'
        resumed = self.call('suite_resume', suite_id=suite_id, harness_pid=os.getpid(),
                            harness_token_sha256=hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual((resumed['status'], resumed['attached'], resumed['active_child_id']),
                         ('running', True, run_id))
        driving = {'suite_id': suite_id, 'token': token}
        self.assertEqual(self.call('suite_reserve', **driving)['slot']['run_id'], run_id)
        # The harness attached a device anew: the child resumes through its suite.
        self.service.event(run_id, 'device.attached', {'session_id': 'session-2'})
        again = self.call('suite_resume_child', **driving, ordinal=0, run_id=run_id, session_id='session-2')
        self.assertEqual((again['ok'], again['status']), (True, 'queued'))
        self.service.end(run_id, equivalence=GRADED)
        self.assertEqual(self.call('suite_settle', **driving, ordinal=0)['decision'], 'advance')
        # A child the restart left stopped and not resumed settles as a stopped child does: it blocks the suite.
        suite_id, run_id = self.running(request_id='left-stopped')
        self.service.pid = 4003
        self.service.end(run_id, 'blocked', 'unknown', reason=RESTARTED)
        self.call('suite_detach', **self.driving(suite_id))
        self.call('suite_resume', suite_id=suite_id, harness_pid=os.getpid(),
                  harness_token_sha256=hashlib.sha256(token.encode()).hexdigest())
        settled = self.call('suite_settle', suite_id=suite_id, token=token, ordinal=0)
        self.assertEqual((settled['decision'], settled['status']), ('block', 'blocked'))
        self.assertEqual(self.status(suite_id)['reason'], 'Trial entry-000-repeat-000 stopped: ' + RESTARTED)


class InProcessServiceTests(unittest.TestCase):
    """A book against an in-process configurator, through the shared helper of the suite tests."""

    def setUp(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        (self.home / 'config.json').write_text(json.dumps(
            {'executors': {'codex': {'command': ['missing-scripted-runtime']}}}), encoding='utf-8')
        self.controller = Controller(self.home)
        self.addCleanup(lambda: self.controller.close())

    def start(self, book):
        return book.call('suite_start', {'manifest': scripted_manifest(2), 'executor': 'codex', 'options': {},
                                         'request_id': 'in-process', 'harness_token_sha256': TOKEN_SHA256,
                                         'harness_pid': os.getpid()})['suite_id']

    def test_07_B12_a_second_service_object_on_the_same_home_is_a_restart_at_the_first_suite_call_after_it(self):
        from generative_driver.configurator import Controller
        from suite_fixtures import book, service
        first = service(self.controller)('ping')
        self.assertEqual(first, service(self.controller)('ping'))
        self.assertIs(type(first['pid']), int)
        suite_id = self.start(book(self.controller))
        self.assertEqual(book(self.controller).call('suite_status', {'suite_id': suite_id})['status'], 'running')
        self.controller.close()
        self.controller = Controller(self.home)
        self.assertNotEqual(service(self.controller)('ping')['pid'], first['pid'])
        # The service's start blocks no suite any more: the suite stands as it was until a suite call asks.
        with contextlib.closing(sqlite3.connect(trial_store.suite_store_path(self.home))) as db:
            self.assertEqual(db.execute('SELECT status FROM suites').fetchall(), [('running',)])
        status = book(self.controller).call('suite_status', {'suite_id': suite_id})
        self.assertEqual((status['status'], status['reason']), ('blocked', RESTARTED))
        events = book(self.controller).call('suite_events', {'suite_id': suite_id})['events']
        self.assertEqual((events[-1]['kind'], events[-1]['data']), ('suite.recovered', {'reason': RESTARTED}))

    def test_07_B9_a_book_starts_finds_and_seals_a_child_of_the_real_service_through_its_public_calls(self):
        from suite_fixtures import book, harness_keys
        calls = []
        real = self.controller.call

        def recorded(method, params=None):
            calls.append(method)
            return real(method, params)
        suite_book_of_home = book(self.controller)
        suite_id = self.start(suite_book_of_home)
        driving = {'suite_id': suite_id, 'token': TOKEN}
        reserved = suite_book_of_home.call('suite_reserve', driving)
        request = {**reserved['request'], **harness_keys(self, self.root / 'fixture', reserved['request']['case_pin'],
                                                         reserved['request']['executor_config'])}
        with patch.object(self.controller, 'call', recorded):
            started = suite_book_of_home.call('suite_start_child', {**driving, 'ordinal': 0, 'request': request})
            self.assertEqual((started['ok'], started['duplicate']), (True, False), started)
            again = suite_book_of_home.call('suite_start_child', {**driving, 'ordinal': 0, 'request': request})
            self.assertEqual((again['run_id'], again['duplicate']), (started['run_id'], True))
            result = real('result', {'run_id': started['run_id']})
            self.assertEqual(result['request'], json.loads(json.dumps(plain_start(request))))
            self.assertEqual(real('status', {'request_id': reserved['slot']['child_request_id']})['run_id'],
                             started['run_id'])
            deadline = time.monotonic() + 60
            while real('status', {'run_id': started['run_id']})['status'] in ('queued', 'running'):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            settled = suite_book_of_home.call('suite_settle', {**driving, 'ordinal': 0})
            self.assertEqual((settled['decision'], settled['status']), ('block', 'blocked'))
            suite_book_of_home.call('suite_detach', driving)
            suite_book_of_home.call('suite_cancel', {'suite_id': suite_id})
        self.assertEqual([method for method in calls if method.startswith('suite_')], [])
        self.assertLessEqual(set(calls), PUBLIC)
        self.assertIn('start', calls)


class ReplayTests(BookCase):
    """A replay suite stores its candidates as today (07-B15)."""

    def candidate(self, name='candidate'):
        folder = self.root / name
        folder.mkdir()
        (folder / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {}}),
                                            encoding='utf-8')
        return folder

    def replay(self, candidates, request_id='replay-suite'):
        return self.book.call('suite_start', {'manifest': scripted_manifest(2), 'executor': 'replay',
                                              'options': {'replay_candidates': candidates}, 'request_id': request_id,
                                              'harness_token_sha256': TOKEN_SHA256, 'harness_pid': os.getpid()})

    def test_07_B15_suite_start_of_a_replay_suite_checks_one_candidate_for_each_case_and_stores_each_by_its_hash(self):
        from generative_driver.integrity import digest
        candidate = self.candidate()
        sha256 = digest(candidate)
        for candidates, text in (({}, 'A replay suite takes one saved candidate per case'),
                                 (None, 'A replay suite takes one saved candidate per case'),
                                 ({CASE: str(candidate), 'another-case': str(candidate)},
                                  'one saved candidate per case'),
                                 ({CASE: str(self.root / 'missing')}, 'A replay candidate is an absolute directory')):
            with self.subTest(candidates=candidates), self.assertRaisesRegex(ValueError, text):
                self.replay(candidates)
        self.assertFalse((self.home / 'replay-candidates').exists())
        inside = self.bench / 'held'
        inside.mkdir(parents=True)
        (inside / 'replay.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'never lies in the bench home'):
            self.replay({CASE: str(inside)})
        suite_id = self.replay({CASE: str(candidate)})['suite_id']
        stored = self.home / 'replay-candidates' / sha256
        self.assertEqual(sorted(path.name for path in (self.home / 'replay-candidates').iterdir()), [sha256])
        self.assertEqual(digest(stored), sha256)
        frozen = self.book.store.get(suite_id)['frozen']
        self.assertEqual(frozen['executor_config'], {'runtime': 'replay', 'candidates': {
            CASE: {'candidate': str(stored), 'candidate_sha256': sha256}}})

    def test_07_B15_every_child_of_a_case_replays_the_stored_copy(self):
        from generative_driver.integrity import digest
        candidate = self.candidate()
        sha256 = digest(candidate)
        suite_id = self.replay({CASE: str(candidate)})['suite_id']
        stored = str(self.home / 'replay-candidates' / sha256)
        # The saved candidate changes after the suite froze: the children replay the stored copy all the same.
        (candidate / 'replay.json').write_text('changed', encoding='utf-8')
        configurations = []
        for ordinal in (0, 1):
            started, request = self.start_child(suite_id)
            configurations.append(self.service.sent('start')[-1]['executor_config'])
            self.service.end(started['run_id'], equivalence=GRADED)
            self.call('suite_settle', **self.driving(suite_id, ordinal=ordinal))
        self.assertEqual(configurations, [{'runtime': 'replay', 'candidate': stored, 'candidate_sha256': sha256}] * 2)
        experiment = self.call('suite_result', suite_id=suite_id)['experiment']
        self.assertEqual(experiment['dimensions']['replay_candidates'], {CASE: sha256})

    def test_07_B15_suite_candidates_and_stored_candidate_are_the_benchmarks_with_the_behaviour_they_had(self):
        from generative_driver import replay
        from generative_driver.integrity import digest
        candidate = self.candidate()
        sha256 = digest(candidate)
        self.assertFalse(hasattr(replay, 'suite_candidates'))
        self.assertFalse(hasattr(replay, 'stored_candidate'))
        checked = suite_book.suite_candidates({CASE}, {CASE: str(candidate)}, bench=self.bench)
        self.assertEqual(checked, {CASE: {'runtime': 'replay', 'candidate': str(candidate),
                                          'candidate_sha256': sha256}})
        with self.assertRaisesRegex(ValueError, r'options.replay_candidates \{case: directory\} for another, ' + CASE):
            suite_book.suite_candidates({CASE, 'another'}, {CASE: str(candidate)})
        store = self.root / 'store'
        first = suite_book.stored_candidate(checked[CASE], store)
        self.assertEqual(first, {'runtime': 'replay', 'candidate': str(store / sha256), 'candidate_sha256': sha256})
        self.assertEqual(suite_book.stored_candidate(checked[CASE], store), first)
        self.assertEqual(sorted(path.name for path in store.iterdir()), [sha256])
        (store / sha256 / 'replay.json').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'The stored replay candidate changed: '):
            suite_book.stored_candidate(checked[CASE], store)
        (candidate / 'replay.json').write_text('changed too', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'The replay candidate changed while it was stored: '):
            suite_book.stored_candidate(checked[CASE], self.root / 'another-store')
        # The suite start of the benchmark's own command checks the candidates through the same function.
        source = Path(suites.__file__).read_text(encoding='utf-8')
        self.assertIn('from .suite_book import suite_candidates', source)
        self.assertNotIn('from ..replay import suite_candidates', source)


class ScriptedSuiteCase(TrialCase):
    """One scripted suite of two `tq9-v2` trials, run by the bench harness with its own callable for requests against
    an in-process configurator, shared by the tests of a class."""

    @classmethod
    def setUpClass(cls):
        fixture = cls('shared_suite')
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        cls.fixture = fixture
        cls.changed_home = {**CONFIGURED, 'model': 'changed-model', 'reasoning_effort': 'high'}
        cls.frozen_at_start = []

        def service(method, params=None, home=None, **kwargs):
            # Before the service starts the first child, the operator's settings change.
            if method == 'start' and not cls.frozen_at_start:
                cls.frozen_at_start.append(copy.deepcopy(params['executor_config']))
                (fixture.home / 'config.json').write_text(json.dumps({'executors': {'codex': cls.changed_home}}),
                                                          encoding='utf-8')
            return fixture.configurator(method, params, home, **kwargs)
        cls.service = staticmethod(service)
        with scripted_stages(fixture.controller, check=fixture.check), \
                patch('generative_driver.client.call', side_effect=service), \
                patch.object(evaluator, 'reference_execute',
                             side_effect=lambda *args, **kwargs: reference(PASS)(*args, **kwargs)):
            cls.record = harness.run_suite(trial_store.suite_dir(fixture.home, 'suite-run-shared'), {
                'home': str(fixture.home), 'password_files': fixture.secrets['password_files'],
                'renode': fixture.secrets['renode'], 'manifest': scripted_manifest(2), 'executor': 'codex',
                'request_id': 'scripted-suite', 'options': {'scoped_tool_approval': 'emulator'}})
        cls.during = list(fixture.calls)
        cls.suite_id = cls.record.get('suite_id')

    def shared_suite(self):
        """Not a test: the name under which the class builds the fixture that holds the shared suite."""

    def asked(self, method, **params):
        """What a book of the shared home answers the suite call."""
        with patch('generative_driver.client.call', side_effect=self.fixture.configurator):
            return suite_book.suite_call(method, params, home=self.fixture.home, autostart=False)

    def children(self):
        """The `result` of the run of each slot of the shared suite, in order."""
        return [self.fixture.controller.call('result', {'run_id': trial['run_id']})
                for trial in self.asked('suite_result', suite_id=self.suite_id)['trials']]


class HarnessTests(ScriptedSuiteCase):
    """The bench harness asks the book (07-B13), and the store lies in the bench home (07-B7)."""

    def test_07_B13_a_scripted_suite_of_two_trials_completes(self):
        self.assertEqual((self.record['status'], self.record['accepted']), ('finished', True), self.record)
        self.assertEqual([row['decision'] for row in self.record['settled']], ['advance', 'advance'])
        status = self.asked('suite_status', suite_id=self.suite_id)
        self.assertEqual((status['status'], status['planned'], status['completed'], status['equivalent'],
                          status['stopping']), ('completed', 2, 2, 2, False), status)

    def test_07_B13_during_a_scripted_suite_the_service_receives_no_method_whose_name_starts_with_suite(self):
        methods = [method for method, _ in self.during]
        self.assertEqual([method for method in methods if method.startswith('suite_')], [])
        self.assertEqual(len([method for method in methods if method == 'start']), 2)

    def test_07_B13_the_harness_requests_for_a_run_are_unchanged_and_the_book_sends_the_childs_start(self):
        methods = {method for method, _ in self.during}
        self.assertEqual(methods, {'ping', 'start', 'status', 'result', 'record_evaluation'})
        # Every request for a run names it by its run id, but the book's search by the slot's request id.
        by_request = [params for method, params in self.during if method == 'status' and 'run_id' not in params]
        self.assertEqual({params['request_id'] for params in by_request},
                         {'suite:' + self.suite_id + ':trial:0', 'suite:' + self.suite_id + ':trial:1'})
        starts = [params for method, params in self.during if method == 'start']
        self.assertEqual([params['request_id'] for params in starts],
                         ['suite:' + self.suite_id + ':trial:0', 'suite:' + self.suite_id + ':trial:1'])
        posted = [params for method, params in self.during if method == 'record_evaluation']
        self.assertEqual(len(posted), 2)

    def test_07_B13_every_suite_call_of_the_harness_goes_to_a_book_in_its_own_process(self):
        served, sent = [], []

        def book_call(book, method, params=None):
            served.append((method, book.home))
            return {'ok': True, 'served': method}
        client_call = lambda method, params=None, home=None, **options: sent.append(method) or {'ok': True}
        with patch.object(SuiteBook, 'call', book_call), patch('generative_driver.client.call', client_call):
            configurator = harness._configurator(self.fixture.home)
            for method in SUITE_CALLS:
                self.assertEqual(configurator(method, {'suite_id': 'suite-x'}), {'ok': True, 'served': method})
            for method in ('ping', 'start', 'status', 'result', 'attach_device', 'record_evaluation', 'resume'):
                self.assertEqual(configurator(method, {}), {'ok': True})
        self.assertEqual(served, [(method, self.fixture.home) for method in SUITE_CALLS])
        self.assertEqual(sent, ['ping', 'start', 'status', 'result', 'attach_device', 'record_evaluation', 'resume'])
        # The harness's own functions send their suite calls through the one callable they are handed.
        source = Path(harness.__file__).read_text(encoding='utf-8')
        named = set(re.findall(r"'(suite_[a-z_]+)'", source))
        self.assertLessEqual({'suite_start', 'suite_resume', 'suite_reserve', 'suite_start_child',
                              'suite_resume_child', 'suite_settle', 'suite_no_run', 'suite_flag', 'suite_detach',
                              'suite_result'}, named)
        self.assertEqual([line for line in source.splitlines() if re.search(r"\bcall\('suite_", line)], [])

    def test_07_B13_each_childs_result_holds_its_client_record_and_its_request_without_the_case(self):
        children = self.children()
        self.assertEqual(len(children), 2)
        for ordinal, result in enumerate(children):
            with self.subTest(ordinal=ordinal):
                self.assertEqual(set(result['client_record']), {'case', 'case_pin', 'snapshot_sha256'})
                self.assertEqual(result['client_record']['case'], CASE)
                request = result['request']
                self.assertEqual([key for key in ('case', 'case_pin', 'case_options') if key in request], [])
                self.assertEqual(request['request_id'], 'suite:' + self.suite_id + ':trial:' + str(ordinal))
                self.assertEqual(request['client_record'], result['client_record'])
                self.assertLessEqual({'goal', 'device', 'firmware', 'mission', 'evaluation_token_sha256', 'executor',
                                      'executor_config', 'effects', 'budget_seconds'}, set(request))
                self.assertIs(result['equivalence']['equivalent'], True)

    def test_07_B13_a_childs_executor_config_is_the_frozen_one_whatever_the_homes_config_holds_by_then(self):
        [frozen] = self.frozen_at_start
        self.assertEqual(frozen, {**CONFIGURED, 'runtime': 'codex'})
        # The home's settings changed before the service started the first child: one setting the frozen
        # configuration has got another value, and one it lacks was added. The service laid neither under it.
        self.assertEqual(json.loads((self.fixture.home / 'config.json').read_text())['executors']['codex'],
                         self.changed_home)
        for result in self.children():
            self.assertEqual(result['request']['executor_config'], frozen)
            self.assertEqual(result['agent']['model'], 'configured-model')
            self.assertEqual(result['agent']['settings'], {})

    def test_07_B7_a_suite_that_ran_to_its_end_left_no_suite_row_table_or_file_in_the_services_home(self):
        home = self.fixture.home
        with contextlib.closing(sqlite3.connect(home / 'runs.sqlite3')) as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn('runs', tables)
        self.assertEqual([table for table in tables if 'suite' in table], [])
        self.assertEqual([str(path.relative_to(home)) for path in home.rglob('*') if 'suite' in path.name.lower()], [])
        with contextlib.closing(sqlite3.connect(trial_store.suite_store_path(home))) as db:
            self.assertEqual(db.execute('SELECT id,status FROM suites').fetchall(), [(self.suite_id, 'completed')])
            self.assertEqual(db.execute('SELECT status FROM suite_trials ORDER BY ordinal').fetchall(),
                             [('finished',), ('finished',)])


def keys_of(value):
    """Every key of every object in the JSON value."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys_of(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys_of(item)}
    return set()


class ReaderTests(ScriptedSuiteCase):
    """The benchmark's commands and readers ask the book (07-B14)."""

    def command(self, *arguments):
        """What `benchmark suite <arguments>` prints, as JSON, with the methods the service received meanwhile."""
        from generative_driver import benchmark
        mark = len(self.fixture.calls)
        printed = io.StringIO()
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.fixture.home)}), \
                patch('generative_driver.client.call', side_effect=self.fixture.configurator), \
                contextlib.redirect_stdout(printed):
            benchmark.main(['suite', *arguments])
        return json.loads(printed.getvalue()), [method for method, _ in self.fixture.calls[mark:]]

    def test_07_B14_the_suite_commands_print_what_a_book_in_their_own_process_answers(self):
        for action, arguments, params in (('status', (), {}), ('events', ('--after', '2'), {'after': 2}),
                                          ('result', ('--offset', '1', '--limit', '1'), {'offset': 1, 'limit': 1})):
            with self.subTest(action=action):
                printed, sent = self.command(action, self.suite_id, *arguments)
                self.assertEqual(printed, self.asked('suite_' + action, suite_id=self.suite_id, **params))
                self.assertIs(printed['ok'], True)
                self.assertEqual([method for method in sent if method.startswith('suite_')], [])
                self.assertEqual(sent[0], 'ping')
        printed, sent = self.command('status', 'suite-unknown')
        self.assertEqual(printed, {'ok': False, 'error': 'ValueError', 'reason': 'Unknown suite_id'})

    def test_07_B14_the_cancel_command_returns_what_it_did_from_a_book(self):
        # The benchmark's MCP tools, through which this test also read and cancelled a suite, are gone (Feature 09 of
        # this Run, docs/features/untangle/09-commands.md).
        # A suite of its own to cancel by the command. The shared suite stays complete.
        from suite_fixtures import book
        started = book(self.fixture.controller).call('suite_start', {
            'manifest': scripted_manifest(1), 'executor': 'codex', 'options': {},
            'request_id': 'to-cancel-by-command', 'harness_token_sha256': TOKEN_SHA256,
            'harness_pid': os.getpid()})
        # Its harness has let go, so that the cancellation does not wait for it.
        book(self.fixture.controller).store.detach_harness(started['suite_id'])
        cancelled, sent = self.command('cancel', started['suite_id'])
        self.assertEqual((cancelled['ok'], cancelled['status'], cancelled['suite_id']),
                         (True, 'cancelled', started['suite_id']))
        self.assertEqual(self.asked('suite_status', suite_id=started['suite_id'])['status'], 'cancelled')
        self.assertEqual(self.asked('suite_status', suite_id=self.suite_id)['status'], 'completed')

    def test_07_B14_the_readers_read_the_suite_from_the_book_and_the_runs_from_the_service(self):
        from generative_driver.benchmark_support import suite_reporting
        asked, sent = [], []
        real = suite_book.suite_call

        def suite_call(method, params=None, home=None, **options):
            asked.append((method, options.get('autostart', True)))
            return real(method, params, home, **options)

        def client_call(method, params=None, home=None, **options):
            sent.append(method)
            return self.fixture.configurator(method, params, home, **options)
        home = self.fixture.home
        with patch.object(suite_book, 'suite_call', suite_call), patch('generative_driver.client.call', client_call):
            suites.refuse_unsettled_scoring(self.suite_id, home)
            self.assertEqual((asked, [method for method in sent if method != 'ping']), ([('suite_status', False)], []))
            del asked[:], sent[:]
            suites._refuse_live_set_suite(self.suite_id, home)
            self.assertEqual(asked, [('suite_result', False)])
            del asked[:], sent[:]
            with tempfile.TemporaryDirectory() as output:
                report = suite_reporting._export_suite(self.suite_id, home=home, output=output)
            self.assertEqual(set(asked), {('suite_result', False)})
            self.assertLessEqual({'result', 'events'}, set(sent))
            self.assertEqual([method for method in sent if method.startswith('suite_')], [])
            self.assertEqual(report['suite_id'], self.suite_id)
            del asked[:], sent[:]
            # The sweep and the store of a scoring suite's trials ask through the callable they are handed: here a
            # reader's, which reads the suite from the book.
            reader = lambda method, params: (suite_call if method.startswith('suite_') else client_call)(
                method, params, home, autostart=False)
            with self.assertRaisesRegex(ValueError, 'Only a scoring suite seals its trials: ' + self.suite_id):
                trial_store.store(home, self.suite_id, self.fixture.root, None, reader)
            self.assertEqual([method for method, _ in asked], ['suite_status', 'suite_result'])
            rows, identity = trial_store._suite_rows(reader, self.suite_id)
            self.assertEqual(([row['status'] for row in rows], 'scoring_set' in identity), (['finished'] * 2, False))
        source = (Path(suite_book.__file__).parent.parent / 'benchmark.py').read_text(encoding='utf-8')
        storing = source[source.index("args.action == 'store'"):source.index("args.action == 'unseal'")]
        self.assertIn('suite_call', storing)
        for module in (suites, suite_reporting):
            text = Path(module.__file__).read_text(encoding='utf-8').replace('suite_call(', '')
            self.assertEqual(re.findall(r"\bcall\('suite_[a-z_]+'", text), [], module.__name__)

    def test_07_B14_a_report_a_scorecard_and_a_comparison_of_a_scripted_suite_hold_what_they_held_and_no_request(self):
        from generative_driver.benchmark_support import suite_reporting
        from generative_driver.benchmark_support.scorecard import bench_trial_dirs, suite_scorecard
        home = self.fixture.home
        with patch('generative_driver.client.call', side_effect=self.fixture.configurator), \
                tempfile.TemporaryDirectory() as output:
            report = suite_reporting.report_suite(self.suite_id, home=home, output=output)
            written = json.loads((Path(output) / 'suite.json').read_text(encoding='utf-8'))
            card = suite_scorecard(report, home=home, trial_dirs=bench_trial_dirs(report, home))
            comparison = suite_reporting.compare_suites(str(Path(output) / 'suite.json'),
                                                        str(Path(output) / 'suite.json'))
        self.assertEqual(report, written)
        self.assertEqual((report['schema'], report['suite_id'], report['protocol']),
                         ('benchmark-suite-report/3', self.suite_id, 'assistive/1'))
        self.assertEqual([(trial['trial_key'], trial['case'], trial['scored'], trial['outcome'])
                          for trial in report['trials']],
                         [('entry-000-repeat-000', CASE, 'pass', 'equivalent'),
                          ('entry-000-repeat-001', CASE, 'pass', 'equivalent')])
        self.assertEqual([trial['run_id'] for trial in report['trials']],
                         [result['run_id'] for result in self.children()])
        self.assertEqual(report['experiment']['dimensions']['model'], 'configured-model')
        self.assertEqual(card['suite_id'], self.suite_id)
        self.assertEqual((comparison['schema'], comparison['compatible']), ('benchmark-suite-comparison/1', True),
                         comparison)
        for name, value in (('report', report), ('scorecard', card), ('comparison', comparison)):
            with self.subTest(holds=name):
                self.assertNotIn('request', keys_of(value))
                text = json.dumps(value)
                for said in ('evaluation_token_sha256', 'endpoint_file'):
                    self.assertNotIn(said, text)


DESIGN = REPO / 'docs' / 'design.md'


def sentences(text):
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+', ' '.join(text.split())) if part.strip()]


# A sentence that says the configurator stores, starts, settles or checks a suite, or serves a suite call.
KEEPS_A_SUITE = (
    re.compile(r'configurator[^.]*\b(freezes|stores|settles|starts or resumes|starts)\b[^.]*\b(suite|slot|child)',
               re.IGNORECASE),
    re.compile(r'\b(suite|slot)s?\b[^.]*\b(stored|frozen|settled|kept|held) (by|in|on) the configurator',
               re.IGNORECASE),
    re.compile(r'has the configurator (start|resume|settle)', re.IGNORECASE),
    re.compile(r'configurator (serves|answers|offers)[^.]*`?suite_', re.IGNORECASE),
    re.compile(r'asks the configurator whether the suite', re.IGNORECASE),
    re.compile(r'the suite the configurator stores', re.IGNORECASE),
)


def keeping(text):
    return [sentence for sentence in sentences(text) if any(pattern.search(sentence) for pattern in KEEPS_A_SUITE)]


class DocumentTests(unittest.TestCase):
    """The documents say who keeps a suite (07-B16)."""

    def suites_paragraph(self):
        [paragraph] = [line for line in DESIGN.read_text(encoding='utf-8').splitlines()
                       if line.startswith('- **Suites.**')]
        return paragraph

    def test_07_B16_the_patterns_match_what_the_documents_said_before(self):
        before = ('The configurator only freezes and stores it, with the sha256 of the harness\'s token, and settles '
                  'each slot from the child\'s status and recorded verdict, which it reads itself.',
                  'It reserves each slot, has the configurator start or resume the child, and waits.',
                  'The configurator starts or resumes a child only while the suite runs and within its budget.',
                  'The harness provides devices and grades after runs; the configurator settles slots from recorded '
                  'status/verdict.',
                  'The harness asks the configurator whether the suite can resume.',
                  'The configurator serves `suite_status` to any client.')
        for sentence in before:
            with self.subTest(sentence=sentence):
                self.assertEqual(keeping(sentence), [sentence])
        for sentence in ('The bench harness freezes and stores a suite in its own store under the bench home.',
                         'It settles each slot from the child\'s status and recorded verdict, which it reads from the '
                         'configurator.', 'The harness asks its own store whether the suite can resume.'):
            with self.subTest(sentence=sentence):
                self.assertEqual(keeping(sentence), [])

    def test_07_B16_design_says_under_suites_that_the_bench_harness_freezes_stores_settles_and_starts(self):
        paragraph = self.suites_paragraph()
        for said in ('freezes and stores', 'its own store under the bench home',
                     "settles each slot from the child's status and recorded verdict, which it reads from the "
                     'configurator', 'starts a child with a plain `start` on a supplied device'):
            with self.subTest(said=said):
                self.assertIn(said, paragraph)
        self.assertRegex(paragraph, r'bench harness[^.]*freezes and stores')
        self.assertEqual(keeping(paragraph), [])
        for sentence in sentences(paragraph):
            with self.subTest(sentence=sentence[:60]):
                self.assertNotRegex(sentence, r'(?i)the configurator (only )?(freezes|stores|starts|settles|checks)')

    def test_07_B16_the_readme_of_the_bench_says_that_the_harness_settles_slots(self):
        text = (REPO / 'bench' / 'README.md').read_text(encoding='utf-8')
        [line] = [line for line in text.splitlines() if 'settles slots' in line]
        self.assertRegex(line, r'[Tt]he harness [^.;]*settles slots')
        self.assertNotIn('the configurator settles slots', text)

    def test_07_B16_case_authoring_says_that_the_harness_asks_its_own_store_whether_a_suite_can_resume(self):
        text = (REPO / 'docs' / 'benchmark-case-authoring.md').read_text(encoding='utf-8')
        self.assertIn('The harness asks its own store whether the suite can resume', text)
        self.assertNotIn('asks the configurator whether the suite can resume', text)

    def test_07_B16_no_document_says_that_the_configurator_stores_a_suite_or_serves_a_suite_call(self):
        documents = [path for path in sorted((REPO / 'docs').glob('*.md'))]
        documents += [REPO / 'README.md', REPO / 'bench' / 'README.md']
        self.assertIn(DESIGN, documents)
        self.assertGreater(len(documents), 8)
        for path in documents:
            with self.subTest(document=str(path.relative_to(REPO))):
                self.assertEqual(keeping(path.read_text(encoding='utf-8')), [])


class ListTests(unittest.TestCase):
    """The deletion list is true after this Feature (07-B17), and the tool's tests are named (07-B5)."""

    def rows(self, heading):
        lines = (REPO / 'docs' / 'benchmark-deletion-list.md').read_text(encoding='utf-8').splitlines()
        start = lines.index('## ' + heading)
        rows = []
        for line in lines[start + 1:]:
            if line.startswith('## '):
                break
            if line.startswith('| `'):
                rows.append([cell.strip() for cell in line.strip('|').split('|')])
        return rows

    def test_07_B5_tool_test_modules_names_the_tools_tests_of_this_feature_and_not_this_file(self):
        from deletion_marking import tool_test_modules
        modules = tool_test_modules(REPO)
        self.assertIn('tests/test_client_calls.py', modules)
        self.assertNotIn('tests/test_benchmark_suite_book.py', modules)
        self.assertIn('tests/test_run_status.py', modules)

    def test_07_B17_this_module_is_ready_to_delete_and_the_tools_module_is_not_on_the_list(self):
        ready = [row[0] for row in self.rows('Ready to delete')]
        self.assertIn('`tests/test_benchmark_suite_book.py`', ready)
        text = (REPO / 'docs' / 'benchmark-deletion-list.md').read_text(encoding='utf-8')
        self.assertNotIn('test_client_calls', text)
        self.assertEqual(text.count('`tests/test_benchmark_suite_book.py`'), 1)

    def test_07_B17_every_count_in_a_rows_reason_is_true(self):
        tests = REPO / 'tests'
        importers = {}
        for path in sorted(tests.glob('*.py')):
            for node in ast.walk(ast.parse(path.read_bytes())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                         [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in names:
                    importers.setdefault(name.split('.')[0], set()).add(path.name)
        counted = 0
        for row in self.rows('Still used by the tool'):
            found = re.fullmatch(r'`tests/([a-z0-9_]+\.py)` and (\d+) other test files? imports? it', row[1])
            if not found or not row[0].startswith('`tests/'):
                continue
            helper = row[0].strip('`').split('/')[-1][:-3]
            counted += 1
            with self.subTest(helper=helper):
                self.assertIn(found.group(1), importers[helper])
                self.assertEqual(int(found.group(2)), len(importers[helper]) - 1)
        self.assertGreater(counted, 3)

    def test_07_B17_every_count_under_passages_is_true_and_the_check_reports_no_problem(self):
        import deletion_marking
        self.assertEqual(deletion_marking.problems(REPO), [])
        # A file may have a row for each Feature that frees some of its lines: its rows count its lines together.
        counted = {}
        for row in self.rows('Passages'):
            counted[row[0]] = counted.get(row[0], 0) + int(row[1])
        self.assertIn('`docs/design.md`', counted)
        for document, lines_counted in counted.items():
            lines = (REPO / document.strip('`')).read_text(encoding='utf-8').splitlines()
            marked, unmarked = False, 0
            for line in lines:
                if line.strip() in ('<!-- legacy-benchmark:begin -->', '<!-- legacy-benchmark:end -->'):
                    marked = line.strip().endswith('begin -->')
                elif not marked and 'bench' in line.lower():
                    unmarked += 1
            with self.subTest(document=document):
                self.assertEqual(lines_counted, unmarked)

    def test_07_B17_the_service_is_no_longer_listed_as_a_tool_module_that_imports_the_benchmark(self):
        # It launched the bench harness until Feature 09, and the MCP server held the benchmark's tools (09-B11 of
        # docs/features/untangle/09-commands.md).
        modules = {row[0]: row[1] for row in self.rows('Tool modules that import the benchmark')}
        self.assertEqual(modules, {})


if __name__ == '__main__':
    unittest.main()

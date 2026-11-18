"""What a client gains at version 3 of the client interface, and what the service no longer has.

Feature 07 of the untangle Run (docs/features/untangle/07-suites.md): 07-B1 to 07-B4, the tool's side of 07-B5, and
07-B6. `status` finds a run by the request id its `start` gave, `result` gives back the request, a client can mark a
run sealed, and on a supplied run a given `executor_config` is the whole configuration. The service has no suite: no
suite call, no suite table and no private start. This module imports nothing from the benchmark package and names no
registered case, so it also runs where the benchmark is gone. The benchmark's side of the Feature is in
`tests/test_benchmark_suite_book.py`.

Temporary homes only; no model, no hardware and no password. The device is the hand-written one of
`tests/supplied_device.py`.
"""
import ast
import copy
import hashlib
import inspect
import json
import re
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from generative_driver import client_interface, integrity, replay, run_status
from supplied_device import SuppliedDevice
from test_shared_helpers import benchmark_imports
from test_supplied_stages import MISSION, hand_written_firmware

TOKEN = 'a token only this test holds'
TOKEN_SHA256 = hashlib.sha256(TOKEN.encode()).hexdigest()
# The methods the service served for a suite before this Feature.
SUITE_CALLS = ('suite_start', 'suite_resume', 'suite_reserve', 'suite_attach', 'suite_start_child',
               'suite_resume_child', 'suite_settle', 'suite_no_run', 'suite_flag', 'suite_sealed', 'suite_detach',
               'suite_cancel', 'suite_status', 'suite_events', 'suite_result')
SUITE_TABLES = ('suites', 'suite_trials', 'suite_events')
SEALED = 'A sealed run does not resume or take a device: '
DOCUMENT = Path(__file__).resolve().parents[1] / 'docs' / 'client-interface.md'


def service_source():
    from generative_driver import configurator
    return Path(configurator.__file__)


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


def enclosing(tree):
    """Every node of `tree` with the names of the functions it lies in, outermost first."""
    def walk(node, names):
        for child in ast.iter_child_nodes(node):
            inner = [*names, child.name] if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else names
            yield child, inner
            yield from walk(child, inner)
    return walk(tree, [])


class NoSuiteTests(ServiceCase):
    """The service has no suite (07-B5)."""

    def test_07_B5_a_suite_call_is_refused_as_any_method_the_service_does_not_have(self):
        started = self.held(self.supplied())
        for method in SUITE_CALLS:
            with self.subTest(method=method):
                self.assertEqual(self.refused(method, suite_id='suite-0123456789abcdef'), 'Unknown run_id')
                self.assertEqual(self.refused(method, run_id=started['run_id'], suite_id='suite-0123456789abcdef'),
                                 'Unknown method: ' + method)
        self.assertEqual(self.refused('never_a_method', run_id=started['run_id']), 'Unknown method: never_a_method')

    def tables(self):
        with closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_07_B5_in_a_new_home_the_services_database_has_no_suite_table(self):
        self.held(self.supplied())
        tables = self.tables()
        self.assertIn('runs', tables)
        self.assertEqual([table for table in SUITE_TABLES if table in tables], [])
        self.assertEqual([table for table in tables if 'suite' in table], [])

    def test_07_B5_in_a_home_that_has_the_suite_tables_the_service_neither_reads_nor_changes_them(self):
        self.controller.close()
        # The tables as the version before this Feature made them (that version makes them itself, so they are made
        # here only where they are missing), with a suite that version would have blocked when the service started.
        with closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS suites (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE, request_json TEXT NOT NULL,
                    frozen_json TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    budget_seconds REAL NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0,
                    harness_token_sha256 TEXT, harness_pid INTEGER, harness_attached INTEGER NOT NULL DEFAULT 0,
                    harness_lock TEXT);
                CREATE TABLE IF NOT EXISTS suite_trials (
                    suite_id TEXT NOT NULL, ordinal INTEGER NOT NULL, trial_json TEXT NOT NULL,
                    child_request_id TEXT NOT NULL UNIQUE, run_id TEXT UNIQUE,
                    status TEXT NOT NULL, outcome_category TEXT NOT NULL DEFAULT 'unknown',
                    result_json TEXT, attempt INTEGER NOT NULL DEFAULT 1, first_attempt TEXT, no_run_reason TEXT,
                    regrade TEXT, audit TEXT, PRIMARY KEY(suite_id,ordinal));
                CREATE TABLE IF NOT EXISTS suite_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, suite_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                INSERT INTO suites(id, request_id, request_json, frozen_json, status, created, updated, budget_seconds)
                    VALUES('suite-earlier', 'earlier', '{}', '{"trials": []}', 'running', 1.0, 1.0, 600.0);
                INSERT INTO suite_trials(suite_id, ordinal, trial_json, child_request_id, status)
                    VALUES('suite-earlier', 0, '{"ordinal": 0}', 'suite:suite-earlier:trial:0', 'launching');
                INSERT INTO suite_events(suite_id, kind, payload, created)
                    VALUES('suite-earlier', 'suite.started', '{}', 1.0);
            ''')

        def held_by_the_tables():
            with closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db:
                return {table: (db.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone(),
                                db.execute('SELECT * FROM ' + table).fetchall()) for table in SUITE_TABLES}
        before = held_by_the_tables()
        self.assertEqual([row[4] for row in before['suites'][1]], ['running'])
        self.controller = self.service()
        started = self.held(self.supplied(request_id='suite:suite-earlier:trial:0'))
        self.call('status', request_id='suite:suite-earlier:trial:0')
        self.call('result', run_id=started['run_id'])
        self.call('cancel', run_id=started['run_id'])
        self.call('seal', run_id=started['run_id'])
        self.assertEqual(held_by_the_tables(), before)
        self.assertEqual(self.refused('suite_status', suite_id='suite-earlier'), 'Unknown run_id')
        self.assertEqual(held_by_the_tables(), before)

    def test_07_B5_the_source_of_the_service_has_none_of_the_suite_names(self):
        source = service_source().read_text(encoding='utf-8')
        named = ('suite_start', 'find_request', '_replay_suite_config', '_harness_identity', '_harness_alive',
                 '_sealing', '_attached_to', '_assistive', 'OLD_SUITE', 'TAKEN', '_slot', '_child_evaluation',
                 '_child_mismatch', '_child_ended', '_cancel_live', '_started_from', '_resumed_on', '_budgeted',
                 '_first_attempt', '_reserved_for_scoring', '_verify_suite')
        self.assertEqual([name for name in named if re.search(r'(?<![A-Za-z0-9_])' + name + r'(?![A-Za-z0-9_])',
                                                              source)], [])
        tree = ast.parse(source)
        defined = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                             ast.ClassDef))]
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
                node, (ast.AnnAssign, ast.AugAssign)) else []
            defined += [name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)]
        self.assertIn('Controller', defined)
        self.assertIn('TERMINAL', defined)
        self.assertEqual([name for name in defined if 'suite' in name.lower()], [])
        # Nor does the service keep or call anything of that name on itself: no suite lock, store or method.
        self.assertEqual(sorted({node.attr for node in ast.walk(tree)
                                 if isinstance(node, ast.Attribute) and 'suite' in node.attr.lower()}), [])

    def test_07_B5_start_takes_the_params_alone(self):
        from generative_driver.configurator import Controller
        self.assertEqual(list(inspect.signature(Controller.start).parameters), ['self', 'params'])
        starts = {name: list(inspect.signature(value).parameters) for name, value in vars(Controller).items()
                  if 'start' in name and inspect.isfunction(value)}
        self.assertIn('start', starts)
        for name, parameters in starts.items():
            with self.subTest(function=name):
                # No start takes an intent to store or a flag for a complete configuration.
                self.assertEqual([parameter for parameter in parameters if 'intent' in parameter
                                  or 'complete' in parameter], [])
        # The request a run keeps is the params of its `start`, whatever start made it.
        params = self.supplied(request_id='gauge-1')
        started = self.held(params)
        self.assertEqual(self.call('result', run_id=started['run_id'])['request'], json.loads(json.dumps(params)))

    def test_07_B5_the_benchmark_imports_that_remain_in_the_service_are_exactly_the_named_ones(self):
        # The two others left in Feature 08 (08-B7 of docs/features/untangle/08-reports.md), and those that launched
        # a harness in Feature 09 (09-B8 of docs/features/untangle/09-commands.md): none remains.
        named = set()
        found = set()
        for node, names in enclosing(ast.parse(service_source().read_bytes())):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            module = ('generative_driver.' + (node.module or '') if isinstance(node, ast.ImportFrom) and node.level == 1
                      else getattr(node, 'module', None) or '')
            for alias in node.names:
                name = module if isinstance(node, ast.ImportFrom) else alias.name
                if name.startswith('generative_driver.benchmark'):
                    found.add((names[-1] if names else None, name, alias.name))
        self.assertEqual(found, named)
        self.assertEqual(sorted(benchmark_imports(service_source(), 'generative_driver')),
                         sorted((module, name) for _, module, name in named))

    def test_07_B5_the_helpers_only_suite_code_called_have_left_the_tool(self):
        self.assertFalse(hasattr(run_status, 'without'))
        self.assertFalse(hasattr(replay, 'suite_candidates'))
        self.assertFalse(hasattr(replay, 'stored_candidate'))
        # What the tool's own runs use of these modules stays.
        self.assertTrue(callable(run_status.added))
        self.assertTrue(callable(replay.candidate_config))

    def test_07_B5_this_module_imports_nothing_of_the_benchmark_and_names_no_registered_case(self):
        # The registered cases are read from the registrations table, which is data; where the benchmark is gone
        # there is none to name.
        table = Path(client_interface.__file__).parent / 'resources' / 'registrations' / 'cases.json'
        registered = json.loads(table.read_text(encoding='utf-8'))['cases'] if table.is_file() else []
        cases = [case['id'] for case in registered]
        self.assertEqual(benchmark_imports(Path(__file__), ''), [])
        texts = [node.value for node in ast.walk(ast.parse(Path(__file__).read_bytes()))
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        # A case's name as a text of its own, or inside a longer text when it is no everyday word.
        self.assertEqual([case for case in cases if case in texts
                          or '-' in case and any(case in text for text in texts)], [])


class VersionThreeTests(unittest.TestCase):
    """What version 3 made the interface document and module say (07-B6), under the number and with the names of
    version 4 (08-B9 of docs/features/untangle/08-reports.md)."""

    @classmethod
    def setUpClass(cls):
        cls.lines = DOCUMENT.read_text(encoding='utf-8').splitlines()
        cls.text = ' '.join(' '.join(cls.lines).split())

    def section(self, heading):
        """The text of the section under the second-level `heading`, as one line."""
        start = self.lines.index('## ' + heading) + 1
        end = next((number for number in range(start, len(self.lines)) if self.lines[number].startswith('## ')),
                   len(self.lines))
        return ' '.join(' '.join(self.lines[start:end]).split())

    def row(self, key):
        """The value cell of the row of the start table whose key cell holds `key`."""
        [line] = [line for line in self.lines if line.startswith('| `' + key + '`')]
        return line.strip('|').split('|', 1)[1].strip()

    def test_07_B6_the_module_and_the_documents_first_line_say_version_4(self):
        self.assertEqual(client_interface.VERSION, 4)
        self.assertEqual(self.lines[0], '# Client interface, version 4')

    def test_07_B6_the_calls_gain_cancel_and_seal_and_lose_nothing_and_the_names_are_those_of_version_4(self):
        self.assertEqual(client_interface.CALLS, ('ping', 'start', 'resume', 'status', 'events', 'result',
                                                  'attach_device', 'record_evaluation', 'cancel', 'seal'))
        self.assertEqual(sorted(client_interface.__all__),
                         ['CALLS', 'IMPORTS', 'PROTOCOL', 'VERDICT_FIELDS', 'VERSION', 'device_errors',
                          'firmware_errors', 'frozen_driver', 'mission_errors', 'verdict_errors'])
        for name in client_interface.CALLS:
            with self.subTest(call=name):
                self.assertIn('`' + name + '`', self.text)

    def test_07_B6_the_start_table_says_what_an_executor_config_and_a_request_id_do(self):
        config = self.row('executor_config')
        self.assertIn('On a supplied run it is the whole configuration, and without it the run has what the operator '
                      'configured.', config)
        self.assertIn('`status` finds the run by it', self.row('request_id'))

    def test_07_B6_it_says_that_ping_answers_with_the_process_id_of_the_service(self):
        [sentence] = [part for part in re.split(r'(?<=[.])\s', self.text) if '`pid`' in part]
        for said in ('`ping`', 'the process id of the service', 'another service process answers'):
            with self.subTest(said=said):
                self.assertIn(said, sentence)

    def test_07_B6_the_section_on_reading_records_gives_the_request_id_its_refusal_and_the_request(self):
        section = self.section('Read the records and the frozen driver')
        self.assertIn('`status` takes a `run_id` or a `request_id`', section)
        self.assertIn('`Unknown request_id`', section)
        self.assertRegex(section, r'`result` gives [^.]*`request`')

    def test_07_B6_a_new_section_gives_cancel_and_seal(self):
        headings = [line[3:] for line in self.lines if line.startswith('## ')]
        self.assertIn('Stop or seal a run', headings)
        section = self.section('Stop or seal a run')
        for said in ('`cancel`', '`seal`', '`run_id`', '`{"ok": true, "run_id": ..., "sealed": true}`', '`resume`',
                     '`attach_device`', '`A sealed run does not resume or take a device: <run_id>`'):
            with self.subTest(said=said):
                self.assertIn(said, section)

    def test_07_B6_no_line_of_the_document_says_bench_or_suite(self):
        self.assertEqual([f'{number}: {line}' for number, line in enumerate(self.lines, 1)
                          if 'bench' in line.lower() or 'suite' in line.lower()], [])


if __name__ == '__main__':
    unittest.main()

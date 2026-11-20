"""What a client may import and read at version 4 of the client interface, and what the service no longer knows.

The interface lists every tool name a client may import and names the records on disk a client on the same machine
may read.

Temporary homes only; no model, no hardware and no password. The device is the hand-written one of
`tests/supplied_device.py`.
"""
import contextlib
import copy
import importlib
import inspect
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from generative_driver import client, client_interface, integrity, replay
from test_client_calls import TOKEN, ServiceCase
from test_supplied_runs import WORKERS, ScriptedCase, gauge_model

DOCUMENT = Path(__file__).resolve().parents[1] / 'docs' / 'client-interface.md'
# What a client may import, by module: the names of 08-B9.
IMPORTS = {
    'generative_driver.client_interface': ('CALLS', 'IMPORTS', 'PROTOCOL', 'VERDICT_FIELDS', 'VERSION',
                                           'device_errors', 'firmware_errors', 'frozen_driver', 'mission_errors',
                                           'verdict_errors'),
    'generative_driver.client': ('call', 'default_home'),
    'generative_driver.integrity': ('digest',),
    'generative_driver.setup': ('SUPPORTED_EXECUTORS', 'resolve_executor'),
    'generative_driver.replay': ('candidate_config',),
    'generative_driver.toolkit': ('call_tool', 'resources_root'),
    'interface_runtime.engine': ('validate_model',),
    'interface_runtime': (),
}


def section(heading):
    """The lines of the document's section under the second-level `heading`, without the heading."""
    lines = DOCUMENT.read_text(encoding='utf-8').splitlines()
    start = lines.index('## ' + heading) + 1
    end = next((number for number in range(start, len(lines)) if lines[number].startswith('## ')), len(lines))
    return lines[start:end]


def said(heading):
    """The text of the section under `heading` as one line, for a sentence that may be broken over lines."""
    return ' '.join(' '.join(section(heading)).split())


def backticked(text):
    """The names `text` gives between backticks, in order."""
    return re.findall(r'`([^`]+)`', text)


class ImportListTests(unittest.TestCase):
    """The interface is version 4, and its module lists what a client may import (08-B9)."""

    def test_08_B9_imports_maps_each_module_to_the_names_a_client_may_take_from_it(self):
        self.assertEqual(dict(client_interface.IMPORTS), IMPORTS)
        self.assertEqual(list(client_interface.IMPORTS), list(IMPORTS))
        for module, names in client_interface.IMPORTS.items():
            with self.subTest(module=module):
                self.assertIs(type(names), tuple)
        # A client reads the list and cannot change it.
        self.assertIs(type(client_interface.IMPORTS), MappingProxyType)

    def test_08_B9_every_listed_module_can_be_imported_and_holds_each_name_listed_for_it(self):
        for module, names in client_interface.IMPORTS.items():
            loaded = importlib.import_module(module)
            self.assertTrue(inspect.ismodule(loaded), module)
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(loaded, name), module + ' has no ' + name)


class ImportedNameTests(ServiceCase):
    """What the section says of the names version 4 added is true of them (08-B10)."""

    def test_08_B10_resolve_executor_gives_what_a_start_runs_with_and_refuses_as_start_does(self):
        from generative_driver.setup import SUPPORTED_EXECUTORS, resolve_executor
        default = resolve_executor(None)
        self.assertIn(default, SUPPORTED_EXECUTORS)
        self.assertEqual([resolve_executor(executor) for executor in SUPPORTED_EXECUTORS], list(SUPPORTED_EXECUTORS))
        started = self.held(self.supplied())
        self.assertEqual(self.call('result', run_id=started['run_id'])['agent']['runtime'], default)
        with self.assertRaises(ValueError) as refusal:
            resolve_executor('another-runtime')
        self.assertEqual(self.refused('start', **self.supplied(executor='another-runtime')), str(refusal.exception))
        # Every executor the list names is one a `start` takes: each gets past the check of the executor.
        candidate = saved_candidate(self.root / 'candidate')
        configurations = {'codex': {'command': ['never-launched']}, 'replay': {'candidate': candidate}}
        self.assertEqual(sorted(configurations), sorted(SUPPORTED_EXECUTORS))
        for executor, config in configurations.items():
            with self.subTest(executor=executor):
                run = self.held(self.supplied(executor=executor, executor_config=config))
                self.assertEqual(self.call('result', run_id=run['run_id'])['agent']['runtime'], executor)

    def test_08_B10_candidate_config_gives_the_executor_config_of_a_start_with_the_replay_executor(self):
        candidate = saved_candidate(self.root / 'candidate')
        config = replay.candidate_config({'candidate': candidate})
        self.assertEqual(config, {'runtime': 'replay', 'candidate': str(Path(candidate).resolve()),
                                  'candidate_sha256': integrity.digest(candidate)})
        started = self.held(self.supplied(executor='replay', executor_config=config))
        self.assertTrue(started['ok'], started)
        self.assertEqual(self.call('result', run_id=started['run_id'])['request']['executor_config'], config)
        # The service checks the hash the configuration names, as `candidate_config` does.
        (Path(candidate) / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {},
                                                                 'description': 'changed'}), encoding='utf-8')
        changed = 'The replay candidate changed since its hash was recorded: ' + config['candidate']
        self.assertEqual(self.refused('start', **self.supplied(executor='replay', executor_config=config)), changed)
        with self.assertRaises(ValueError) as refusal:
            replay.candidate_config(config)
        self.assertEqual(str(refusal.exception), changed)

class ImportedToolTests(unittest.TestCase):
    """What the section says of the developer tools and of the runtime is true of them (08-B10)."""

    def test_08_B10_call_tool_calls_a_developer_tool_in_this_process_and_resources_root_holds_the_assets(self):
        from generative_driver import cli, toolkit
        assets = toolkit.resources_root()
        self.assertEqual(assets, Path(toolkit.__file__).resolve().parent / 'resources')
        self.assertTrue((assets / 'toolchain' / 'tools.json').is_file())
        self.assertIn('usage_summary', {tool['name'] for tool in toolkit.list_tools()})
        with tempfile.TemporaryDirectory() as temporary:
            arguments = {'run_dir': str(Path(temporary).resolve() / 'tool-run')}
            # No process is started for the call.
            with patch('subprocess.Popen', side_effect=AssertionError('a process was started')):
                result = toolkit.call_tool('usage_summary', dict(arguments))
            self.assertEqual((result['schema'], result['_exit']), ('inference-usage-summary/1', 0))
            # `generative-driver tool` prints what `call_tool` gives for the same tool and arguments.
            given = Path(temporary) / 'arguments.json'
            given.write_text(json.dumps(arguments), encoding='utf-8')
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                status = cli.main(['tool', 'usage_summary', '--arguments', str(given)])
            self.assertEqual(status, 0)
            again = json.loads(printed.getvalue())
            self.assertEqual({key: again[key] for key in again if key != '_event'},
                             {key: result[key] for key in result if key != '_event'})
        self.assertEqual(toolkit.call_tool('no_such_tool', {}),
                         {'ok': False, '_exit': 1, 'error': 'Unknown tool: no_such_tool'})

    def test_08_B10_validate_model_checks_a_device_model_without_opening_a_transport(self):
        from interface_runtime.engine import validate_model
        # A model whose device nothing listens for: validating it reaches for no connection.
        with patch('socket.socket', side_effect=AssertionError('a transport was opened')), \
                patch('socket.create_connection', side_effect=AssertionError('a transport was opened')):
            report = validate_model(gauge_model())
            self.assertEqual((report['ok'], report['defects']), (True, []))
            self.assertIs(validate_model({'schema': 'interface-model/7'})['ok'], False)


# The keys of `result` for any run, to which a supplied run adds the `client_record` its `start` sent.
RESULT_KEYS = ['accepted_handoffs', 'agent', 'budget_seconds', 'created', 'deadline', 'emulator_recoveries',
               'emulator_recovery_count', 'equivalence', 'evaluator_verdicts', 'limits', 'log', 'ok',
               'outcome_category', 'progress', 'reason', 'repairs', 'request', 'run_id', 'scoped_tool_approval',
               'stage', 'stage_notes', 'status', 'stopping', 'uncertain_effect', 'updated', 'worker', 'worker_reports']


class ResultTests(ServiceCase):
    """The keys of `result` (08-B4)."""

    def test_08_B4_every_other_key_of_result_is_as_before(self):
        supplied = self.held(self.supplied(client_record={'order': 17}))
        generic = self.held({'goal': 'Read the gauge', 'executor_config': {'command': ['never-launched']}})
        self.assertEqual(sorted(self.call('result', run_id=generic['run_id'])), RESULT_KEYS)
        self.assertEqual(sorted(self.call('result', run_id=supplied['run_id'])),
                         sorted([*RESULT_KEYS, 'client_record']))


TAKES_ONLY = 'The replay executor takes only its saved candidate: executor_config {"candidate": <directory>}'
NEEDS = 'The replay executor needs a saved candidate: executor_config {"candidate": <directory>}'


def saved_candidate(directory):
    """A saved replay candidate with no step at `directory`, as text of its path."""
    directory.mkdir(parents=True)
    (directory / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {}}), encoding='utf-8')
    return str(directory)


class ReplayCandidateTests(ServiceCase):
    """The service does not know the folder beside its home (08-B5)."""

    def refused_config(self, config):
        with self.assertRaises(ValueError) as refusal:
            replay.candidate_config(config)
        self.assertIs(type(refusal.exception), ValueError)
        return str(refusal.exception)

    def replay_start(self, candidate, **config):
        return {**self.supplied(), 'executor': 'replay', 'executor_config': {'candidate': candidate, **config}}

    def test_08_B5_it_makes_every_other_check_with_the_same_texts_in_the_same_order(self):
        candidate = saved_candidate(self.root / 'candidate')
        sha256 = integrity.digest(candidate)
        for config in ('candidate', None, {'candidate': candidate, 'model': 'm'}, {'candidate': '', 'model': 'm'}):
            with self.subTest(config=config):
                self.assertEqual(self.refused_config(config), TAKES_ONLY)
        for config in ({}, {'candidate': ''}, {'candidate': 7}, {'candidate': None, 'candidate_sha256': 'f' * 64}):
            with self.subTest(config=config):
                self.assertEqual(self.refused_config(config), NEEDS)
        # The candidate itself is checked before its recorded hash.
        self.assertEqual(self.refused_config({'candidate': 'relative', 'candidate_sha256': 'f' * 64}),
                         'A replay candidate is an absolute directory path: relative')
        self.assertEqual(self.refused_config({'candidate': str(self.root / 'absent')}),
                         'A replay candidate is an absolute directory path: ' + str(self.root / 'absent'))
        (self.root / 'empty').mkdir()
        self.assertEqual(self.refused_config({'candidate': str(self.root / 'empty')}),
                         'A replay candidate holds replay.json: ' + str(self.root / 'empty'))
        installed = Path(client_interface.__file__).parent / 'resources' / 'toolchain'
        self.assertEqual(self.refused_config({'candidate': str(installed)}),
                         'A replay candidate is saved worker output, never a folder of the installed resources')
        self.assertEqual(self.refused_config({'candidate': candidate, 'candidate_sha256': 'f' * 64}),
                         'The replay candidate changed since its hash was recorded: ' + candidate)
        self.assertEqual(replay.candidate_config({'runtime': 'replay', 'candidate': candidate,
                                                  'candidate_sha256': sha256})['candidate_sha256'], sha256)

    def test_08_B5_a_candidate_in_the_folder_beside_the_home_is_checked_as_any_other(self):
        beside = self.home.parent / (self.home.name + '-bench')
        candidate = saved_candidate(beside / 'candidate')
        self.assertEqual(replay.candidate_config({'candidate': candidate})['candidate'], candidate)
        started = self.held(self.replay_start(candidate))
        self.assertTrue(started['ok'], started)
        copied = self.home / 'runs' / started['run_id'] / 'replay-candidate'
        self.assertEqual(integrity.digest(copied), integrity.digest(candidate))

    def test_08_B5_a_replay_start_is_checked_by_the_service_as_before_but_for_that_refusal(self):
        beside = self.home.parent / (self.home.name + '-bench')
        candidate = saved_candidate(beside / 'candidate')
        generic = {'goal': 'Read the gauge', 'executor': 'replay', 'executor_config': {'candidate': candidate}}
        self.assertEqual(self.refused('start', **generic),
                         'The replay executor runs on a supplied device only; this start names none')
        self.assertEqual(self.refused('start', **self.replay_start(candidate, model='m')), TAKES_ONLY)
        self.assertEqual(self.refused('start', **self.replay_start('')), NEEDS)
        self.assertEqual(self.refused('start', **self.replay_start(candidate, candidate_sha256='f' * 64)),
                         'The replay candidate changed since its hash was recorded: ' + candidate)
        (beside / 'candidate' / 'replay.json').write_text('not a candidate', encoding='utf-8')
        self.assertIn('replay.json is not UTF-8 JSON', self.refused('start', **self.replay_start(candidate)))
        self.assertEqual(list((self.home / 'runs').glob('*')) if (self.home / 'runs').is_dir() else [], [])


CLEANUP_FAILED = 'Cleanup failed; operator reconciliation required'


class CleanupTextTests(ServiceCase):
    """The tool's status text for a failed cleanup is in the tool's terms (08-B8)."""

    def test_08_B8_a_run_whose_release_raised_before_its_verdict_is_blocked_for_the_host_with_the_tools_text(self):
        from generative_driver.configurator import Controller
        with patch.object(Controller, '_release', side_effect=RuntimeError('scripted release failure')):
            run = self.controller.call('start', {'goal': 'A run whose release fails',
                                                 'executor_config': {'command': ['no-such-worker-runtime']}})
            until = time.monotonic() + 60
            while True:
                status = self.call('status', **run)
                if status['status'] not in ('queued', 'running') and not status['stopping']:
                    break
                self.assertLess(time.monotonic(), until, status)
                time.sleep(.02)
        self.assertEqual((status['status'], status['reason'], status['outcome_category']),
                         ('blocked', CLEANUP_FAILED, 'host'))
        self.assertIsNone(status['equivalence'])
        # Its effect is marked uncertain, and the failure is an event of the run, as before.
        self.assertIs(status['uncertain_effect'], True)
        failed = [event['data'] for event in self.call('events', **run)['events'] if event['kind'] == 'cleanup.failed']
        self.assertEqual(failed, [{'reason': 'RuntimeError: scripted release failure'}])


RECORDS = 'Records on disk'
# The folders the Spec has the section name. The document adds each other one the source shows.
EIGHT_FOLDERS = ('supplied', 'work', 'accepted', 'checks', 'preparation', 'tool-calls', 'snapshots',
                 'replay-candidate')
# The verdict a client posts on the shared run: an equivalent driver, graded.
VERDICT = {'schema': 'equivalence/2', 'status': 'graded', 'equivalent': True, 'basis': 'evaluator',
           'checks': [{'id': 'reads the level', 'passed': True}], 'diff': [], 'passed': 1, 'total': 1, 'mission': None,
           'evidence_sha256': 'b' * 64, 'protocol': 'assistive/1'}


def sentence(start):
    """The one sentence of the section `Records on disk` that starts with `start`, to its full stop."""
    [found] = re.findall(re.escape(start) + r'.*?[.](?= |$)', said(RECORDS))
    return found


def named_tables():
    """The tables the section names, each with its columns, from the rows of its table."""
    rows = [backticked(line) for line in section(RECORDS) if line.startswith('| `')]
    return {table: columns for table, *columns in rows}


def read_only(home):
    """The service's database in `home`, opened as a client opens it: read-only."""
    database = (Path(home) / 'runs.sqlite3').resolve()
    return contextlib.closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True))


def tree(folder):
    """Every file and folder under `folder` with the bytes of each file, by its path from there."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() if path.is_file() else None
            for path in sorted(Path(folder).rglob('*'))}


def calls(controller, run):
    """What `status`, `events` and `result` answer for `run`."""
    return [controller.call(method, dict(run)) for method in ('status', 'events', 'result')]


class RecordsCase(ScriptedCase):
    """One supplied run through all six stages on the gauge, shared by the tests of a class and read as a client on
    the same machine reads it. While the first stage works, the client adds a file and a folder of its own to the
    run's folder. When the stages are done it posts its verdict and seals the run, then removes the files of the
    run's folder and the temporary workspaces."""

    @classmethod
    def setUpClass(cls):
        fixture = cls('shared_run')
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        cls.fixture = fixture
        gauge = fixture.device(units={'gauge': 'percent'}, values={'gauge': 42})
        added = {'client-note.txt': b'kept by the client', 'client-kept/inner.bin': b'\x00\x01'}

        def acquire(worker):
            folder = fixture.controller.home / 'runs' / worker.identity['run_id']
            for name, data in added.items():
                (folder / name).parent.mkdir(exist_ok=True)
                (folder / name).write_bytes(data)
            return WORKERS['acquire'](worker)
        result = fixture.run_with({**WORKERS, 'acquire': acquire}, **fixture.supplied(gauge, request_id='gauge-1'))
        controller, run, folder = fixture.controller, dict(fixture.run), fixture.run_dir()
        cls.home, cls.run_id, cls.added = controller.home, run['run_id'], added
        cls.ended = result
        cls.top = {path.name: path.is_dir() for path in folder.iterdir()}
        cls.kept = {name: (folder / name).read_bytes() if (folder / name).is_file() else None for name in added}
        cls.lines = (folder / 'supplied' / 'package-events.jsonl').read_text(encoding='utf-8').splitlines()
        cls.assigned = {event['data']['stage']: event['data'] for event in fixture.events('stage.assigned')}
        cls.workspaces = {worker.stage: worker.workspace for worker in fixture.seen}
        driver = client_interface.frozen_driver(result['accepted_handoffs'])
        controller.call('record_evaluation', {**run, 'token': TOKEN,
                                              'equivalence': {**VERDICT, 'frozen_artifact_sha256': driver['sha256']}})
        controller.call('seal', run)
        cls.sealed = calls(controller, run)
        with read_only(controller.home) as db:
            cls.columns = {table: [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
                           for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for path in folder.iterdir():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        shutil.rmtree(cls.workspaces['interpret'].parent)
        cls.emptied = calls(controller, run)

    def shared_run(self):
        """Not a test: the name under which the class builds the fixture that holds the shared run."""


class RecordsOnDiskTests(RecordsCase):
    """Each thing the document's section `Records on disk` says of a run's records is true of a run (08-B11)."""

    def test_08_B11_the_database_has_the_tables_and_columns_it_names_and_a_client_opens_it_read_only(self):
        self.assertIn('`runs.sqlite3` in the home is the SQLite database of the service\'s records, and a client '
                      'opens it read-only.', said(RECORDS))
        named = named_tables()
        self.assertEqual(named, {'runs': ['id', 'request_id'],
                                 'events': ['seq', 'run_id', 'kind', 'payload', 'created'],
                                 'reports': ['run_id', 'stage', 'payload', 'created'],
                                 'handoffs': ['run_id', 'payload', 'created'], 'verdicts': ['id', 'payload'],
                                 'progress': ['run_id', 'payload']})
        for table, columns in named.items():
            with self.subTest(table=table):
                self.assertEqual([column for column in columns if column not in self.columns.get(table, ())], [])
        with read_only(self.home) as db:
            for table, columns in named.items():
                db.execute('SELECT ' + ', '.join(columns) + ' FROM ' + table).fetchall()
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM events')

    def test_08_B11_a_payload_is_json_text_and_the_rows_are_what_events_and_result_are_made_from(self):
        for said_of_rows in ('A `payload` is JSON text.', 'The rows are what `events` and `result` are made from.'):
            self.assertIn(said_of_rows, said(RECORDS))
        # Rows of `handoffs` are read by `rowid`, and the final verdict's id ends in `:final`, as the section says.
        self.assertEqual(backticked(sentence('The rows of `handoffs`')), ['handoffs', 'rowid'])
        self.assertEqual(backticked(sentence('The final verdict of a run'))[-1], ':final')
        status, events, result = self.sealed
        one = (self.run_id,)
        with read_only(self.home) as db:
            self.assertEqual(db.execute('SELECT id, request_id FROM runs').fetchall(), [(self.run_id, 'gauge-1')])
            rows = db.execute('SELECT seq, kind, payload, created FROM events WHERE run_id=? ORDER BY seq', one)
            self.assertEqual([{'id': seq, 'kind': kind, 'data': json.loads(payload), 'time': created}
                              for seq, kind, payload, created in rows], events['events'])
            rows = db.execute('SELECT stage, payload FROM reports WHERE run_id=? ORDER BY created', one).fetchall()
            self.assertEqual([json.loads(payload) for _, payload in rows], result['worker_reports'])
            self.assertEqual([stage for stage, _ in rows], [report['stage'] for report in result['worker_reports']])
            rows = db.execute('SELECT payload FROM handoffs WHERE run_id=? ORDER BY rowid', one)
            self.assertEqual([json.loads(payload) for (payload,) in rows], result['accepted_handoffs'])
            [(payload,)] = db.execute('SELECT payload FROM verdicts WHERE id=?', (self.run_id + ':final',))
            self.assertEqual(json.loads(payload)['equivalence'], result['equivalence'])
            self.assertEqual(result['equivalence'], status['equivalence'])
            self.assertTrue(result['equivalence']['equivalent'])
            [(payload,)] = db.execute('SELECT payload FROM progress WHERE run_id=?', one)
            self.assertEqual({key: value for key, value in json.loads(payload).items() if key in result['progress']},
                             result['progress'])
        self.assertEqual(len(events['events']), events['cursor'])
        self.assertLess(len(events['events']), 500)

    def test_08_B11_a_line_of_package_events_has_the_keys_it_names(self):
        always = backticked(sentence('`supplied/package-events.jsonl` there holds'))
        self.assertEqual(always, ['supplied/package-events.jsonl', 'actor', 'stage', 'operation', 'revision',
                                  'attempt_id', 'assignment_id', 'package_sha256', 'parameters', 'result'])
        sometimes = backticked(sentence('A line has `mcp`'))
        self.assertEqual(sometimes, ['mcp', 'observation', 'before_observation'])
        rows = [json.loads(line) for line in self.lines]
        # One line for each call the fresh worker made on the driver package, through the package's own server, on a
        # device that offers readings.
        self.assertEqual([(row['actor'], row['stage'], row['operation']) for row in rows],
                         [('worker', 'reuse', 'level'), ('worker', 'reuse', 'status')])
        for row in rows:
            self.assertEqual(sorted(row), sorted([*always[1:], *sometimes]))

    def test_08_B11_the_folders_the_service_makes_in_a_runs_folder_are_among_those_it_names(self):
        from generative_driver import device_first, interpret_tasks, kit_analysis
        named = backticked(sentence('The service makes these folders directly in a run\'s folder:'))
        self.assertEqual(named[:len(EIGHT_FOLDERS)], list(EIGHT_FOLDERS))
        # The folders of the kit analysis, of interpret tasks and of device-first interpretation, which a run
        # without a firmware image or with interpret tasks makes.
        self.assertEqual(sorted(named[len(EIGHT_FOLDERS):]),
                         sorted([device_first.FOLDER, interpret_tasks.FOLDER, kit_analysis.FOLDER]))
        made = sorted(name for name, is_folder in self.top.items() if name.split('/')[0] not in ('client-note.txt',
                                                                                                 'client-kept'))
        self.assertEqual(made, ['accepted', 'checks', 'snapshots', 'supplied', 'work'])
        self.assertEqual([name for name in made if name not in named or not self.top[name]], [])

    def test_08_B11_what_a_client_adds_to_a_runs_folder_under_other_names_is_unchanged_when_the_run_has_ended(self):
        self.assertIn('A client may add files and folders of its own directly in a run\'s folder under other names, '
                      'and the service never changes or removes them.', said(RECORDS))
        self.assertEqual(self.ended['status'], 'completed')
        self.assertEqual(self.kept, self.added)
        self.assertEqual({name: is_folder for name, is_folder in self.top.items() if name.startswith('client-')},
                         {'client-note.txt': False, 'client-kept': True})

    def test_08_B11_an_interpret_worker_works_outside_the_runs_folder_in_the_workspace_its_assignment_names(self):
        self.assertIn('An interpret worker works in a temporary workspace outside the run\'s folder, which its '
                      '`stage.assigned` event names as `workspace`.', said(RECORDS))
        folder = self.home / 'runs' / self.run_id
        self.assertEqual(Path(self.assigned['interpret']['workspace']), self.workspaces['interpret'])
        self.assertFalse(self.workspaces['interpret'].is_relative_to(folder))
        for stage in ('acquire', 'probe', 'ground', 'emit', 'reuse'):
            with self.subTest(stage=stage):
                self.assertTrue(self.workspaces[stage].is_relative_to(folder / 'work'))

    def test_08_B11_a_stopped_and_sealed_run_answers_the_same_with_its_folder_emptied(self):
        self.assertIn('Once a run has stopped, with no worker of it still stopping, and is sealed, the client may '
                      'remove the files of the run\'s folder and those temporary workspaces. `status`, `events` and '
                      '`result` of the run answer as before without them.', said(RECORDS))
        status = self.sealed[0]
        self.assertEqual((status['status'], status['stopping'], status['sealed']), ('completed', False, True))
        self.assertEqual(list((self.home / 'runs' / self.run_id).iterdir()), [])
        self.assertFalse(self.workspaces['interpret'].exists())
        self.assertEqual(self.emptied, self.sealed)


class HomeRecordTests(ServiceCase):
    """What the section `Records on disk` says of the home itself is true (08-B11)."""

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
            self.stopped(run)
        return seen[0]

    def stopped(self, run):
        """The status of `run` once it has stopped and no worker of it is still stopping."""
        until = time.monotonic() + 60
        while True:
            status = self.call('status', run_id=run['run_id'])
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return status
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)

    def test_08_B11_the_home_is_the_folder_default_home_names_when_a_client_gives_none(self):
        self.assertIn('The home is the folder `default_home()` names when a client gives none.', said(RECORDS))
        self.assertIn('A client on the same machine may read what this section names, without asking the service.',
                      said(RECORDS))
        home = self.root / 'named by the environment'
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(home)}):
            self.assertEqual(client.default_home(), home)
            try:
                self.assertTrue(client.call('ping').get('ok'))
                self.assertTrue((home / 'runs.sqlite3').is_file())
            finally:
                until = time.monotonic() + 60
                while not client.call('shutdown').get('stopped'):
                    self.assertLess(time.monotonic(), until, 'The service did not shut down')
                    time.sleep(.1)

    def test_08_B11_config_json_holds_by_executor_the_settings_of_a_run_whose_start_gives_no_executor_config(self):
        for told in ('`config.json` in the home holds the operator\'s settings.',
                     'Its `executors` object holds, by the name of an executor, the settings a run has when its '
                     '`start` gives no `executor_config`.', 'The file may be absent.'):
            self.assertIn(told, said(RECORDS))
        config = self.home / 'config.json'

        def bare():
            """A `start` on a new device that gives no `executor_config`."""
            params = self.supplied()
            del params['executor_config']
            return params
        # Without the file nothing is configured, which the service says of the default executor.
        unconfigured = 'codex is not configured. Run `generative-driver configure --executor codex --command <path>`.'
        self.assertFalse(config.exists())
        self.assertEqual(self.refused('start', **bare()), unconfigured)
        configured = {'command': ['configured-runtime'], 'model': 'configured-model'}
        config.write_text(json.dumps({'executors': {'codex': configured}, 'other': 'settings'}), encoding='utf-8')
        self.assertEqual(self.launched(bare()), {**configured, 'runtime': 'codex'})
        # The settings are held by the executor's name: those of another name are not this run's.
        config.write_text(json.dumps({'executors': {'another': configured}}), encoding='utf-8')
        self.assertEqual(self.refused('start', **bare()), unconfigured)

    def test_08_B11_a_file_named_agent_events_under_the_runs_folder_is_the_transcript_of_one_worker(self):
        self.assertIn('Every file named `_agent_events.jsonl` under the run\'s folder is the transcript of one worker.',
                      said(RECORDS))
        printed = json.dumps({'type': 'note', 'text': 'what the worker printed'})
        run = self.controller.call('start', {'goal': 'A run whose worker prints one line', 'executor_config': {
            'command': [sys.executable, '-c', 'print(' + repr(printed) + ')']}})
        self.stopped(run)
        folder = self.home / 'runs' / run['run_id']
        reports = self.call('result', **run)['worker_reports']
        # The run launched a worker for more than one stage, and each left its own file.
        self.assertGreater(len(reports), 1)
        self.assertEqual(sorted(folder.rglob('_agent_events.jsonl')),
                         sorted(Path(report['transcript']) for report in reports))
        for report in reports:
            with self.subTest(stage=report['stage']):
                transcript = Path(report['transcript'])
                self.assertEqual(transcript.read_text(encoding='utf-8').splitlines(), [printed])
                self.assertEqual(transcript.parent.parent, folder / 'work' / report['stage'])

    def test_08_B11_saved_candidates_kept_in_the_home_are_never_changed_and_a_start_reads_the_one_it_names(self):
        for told in ('A client may keep saved candidates for the replay executor in the folder `replay-candidates` '
                     'in the home.', 'The service never changes or removes that folder, and reads in it only a '
                                     'candidate a `start` names.'):
            self.assertIn(told, said(RECORDS))
        kept = self.home / 'replay-candidates'
        self.assertFalse(kept.exists())
        named = saved_candidate(kept / 'named')
        (kept / 'other').mkdir()
        (kept / 'other' / 'replay.json').write_text('not a candidate a start could take', encoding='utf-8')
        before = tree(kept)
        started = self.held({**self.supplied(), 'executor': 'replay', 'executor_config': {'candidate': named}})
        copied = self.home / 'runs' / started['run_id'] / 'replay-candidate'
        self.assertEqual(integrity.digest(copied), integrity.digest(named))
        self.call('cancel', run_id=started['run_id'])
        self.call('seal', run_id=started['run_id'])
        self.assertEqual(tree(kept), before)
        # Another service on the same home leaves the folder as it is, too.
        self.controller.close()
        self.controller = self.service()
        self.call('status', run_id=started['run_id'])
        self.assertEqual(tree(kept), before)


if __name__ == '__main__':
    unittest.main()

"""Feature 07, trial sealing, at `trial_store`'s functions on plain temporary folders: what a seal holds, the stub, each
crash step, links and FIFOs, a failed delete, a wrong password and a workspace of the wrong shape. Real key derivations,
with no qualified set; never a real password, vault, device or model run."""
import ast
import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import stat
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generative_driver import benchmark
from generative_driver.benchmark_support import (generated_sets, generated_trials, harness, scorecard, suite_reporting,
                                                 suites, trial_store)
from generative_driver.benchmark_support.suite_store import _json
from generative_driver.benchmark_support.truth import EvaluatorPassword
from generative_driver.configurator import Controller

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'generative_driver'
LAYOUT_WORDS = ('suites', 'trials', '-bench')
# The export layout of a suite report, which is no part of a bench home.
EXPORT_LAYOUT = "relative=Path('trials')/trial['trial_key']/'report.json'"


def _names_layout(node):
    """Whether any string constant under `node` is, or holds as a path segment, a word of the bench home's layout."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            segments = inner.value.replace('\\', '/').split('/')
            if inner.value.endswith('-bench') or any(word in segments for word in LAYOUT_WORDS):
                return True
    return False


def layout_uses(source):
    """The line numbers at which `source` names the bench home's layout: as an operand of `/`, in the arguments of
    `joinpath` or `Path`, or in a glob pattern."""
    lines = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _names_layout(node.left) or _names_layout(node.right):
                lines.add(node.lineno)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', None)
            if name in ('joinpath', 'Path', 'PurePath', 'PurePosixPath', 'glob', 'rglob', 'iglob') and any(
                    _names_layout(argument) for argument in node.args):
                lines.add(node.lineno)
    return sorted(lines)


class OneModuleTests(unittest.TestCase):
    def test_07_B19_the_test_catches_each_way_of_naming_the_layout(self):
        for source in ("bench / 'suites'", "bench / 'trials' / key", "home.parent / (home.name + '-bench')",
                       "root / ('trials' if kind else 'suites')", "bench.joinpath('suites', key)", "Path('trials')",
                       "suites.glob('*/trials/*/trial.json')", "bench.rglob('suites/*')"):
            with self.subTest(source):
                self.assertEqual(layout_uses(source), [1])
        for source in ("'suites'", "record['trials']", "home / 'runs'", "suite['trials'][0]", "path.glob('*.enc')"):
            with self.subTest(source):
                self.assertEqual(layout_uses(source), [])

    def test_07_B19_only_trial_store_knows_the_bench_homes_layout(self):
        found = {}
        for path in sorted(SOURCE.rglob('*.py')):
            if path.name == 'trial_store.py':
                continue
            text = path.read_text(encoding='utf-8')
            lines = text.splitlines()
            uses = [line for line in layout_uses(text) if EXPORT_LAYOUT not in lines[line - 1]]
            if uses:
                found[str(path.relative_to(SOURCE))] = uses
        self.assertEqual(found, {})

    def test_07_B19_the_export_layout_is_still_where_the_allowance_says(self):
        text = (SOURCE / 'benchmark_support' / 'suite_reporting.py').read_text(encoding='utf-8')
        self.assertEqual(sum(EXPORT_LAYOUT in line for line in text.splitlines()), 1)

    def test_07_B19_the_harness_alias_is_the_modules_bench_home(self):
        from generative_driver.benchmark_support import harness, trial_store
        home = Path('/tmp/some-home')
        self.assertEqual(harness.bench_home(home), trial_store.bench_home(home))
        self.assertEqual(trial_store.suite_dir(home, 'key').parent.name, 'suites')
        self.assertEqual(trial_store.suite_dir(home, 'key').parent.parent, Path('/tmp/some-home-bench').resolve())


PASSWORD = EvaluatorPassword('the-trial-sealing-password')
WRONG = EvaluatorPassword('another-password')
SUITE = 'suite-0123456789abcdef'
RUN = 'run-0123456789'
HOST_FIELDS = ('level', 'framing', 'verdict', 'outcome', 'check_id', 'reason', 'mission', 'tokens', 'tool_counts',
               'device_id', 'image', 'truth', 'home', 'pid', 'device', 'equivalence', 'evidence', 'sealed',
               'run_status', 'device_stop')
RUN_FILES = {'work/probe/a1/_agent_events.jsonl': b'{"type": "item.completed"}\n', 'benchmark/state.json': b'{"s": 1}',
             'benchmark/tools/interpret/attempt1/model.json': b'{"m": 1}', 'handoffs/emit/package.bin': b'driver bytes'}
KEPT_FILES = {'benchmark/execution.json': b'{"snapshot": 1}', 'benchmark/inputs/firmware.bin': b'public image'}
TRIAL_FILES = {'device/journal.jsonl': b'{"kind": "started"}\n', 'endpoint/control.json': b'{"authkey": "aa"}',
               'public-inputs/mission.json': b'{"tasks": []}'}
TRIAL_KEPT = {'run-evidence.enc': b'opaque evidence', 'evaluator-draft.enc': b'opaque draft',
              'harness.lock': b'1 nonce', 'evaluation.lock': b''}
WORKSPACE_FILES = {'attempt1/model.json': b'{"w": 1}', 'attempt1/_agent_events.jsonl': b'{"type": "turn.started"}\n'}


def write_files(root, files):
    for relative, content in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class Configurator:
    """A recording `call` standing where the configurator stands."""

    def __init__(self):
        self.calls, self.events, self.rows, self.refused = [], [], [], {}
        self.status = {'ok': True, 'status': 'completed', 'stopping': False}
        self.identity = {'scoring_set': {'registration': 'set-v1', 'manifest_sha256': 'a' * 64},
                         'manifest': {'set': 'set-v1'}}

    def __call__(self, method, params=None):
        self.calls.append((method, params))
        if method in self.refused:
            return {'ok': False, 'reason': self.refused[method]}
        if method == 'suite_sealed':
            return {'ok': True}
        if method == 'events':
            after = params.get('after', 0)
            page = self.events[after:after + 500]
            return {'ok': True, 'run_id': params['run_id'], 'events': page, 'cursor': after + len(page)}
        if method == 'suite_result':
            offset, limit = params.get('offset', 0), params.get('limit', 50)
            following = offset + limit if offset + limit < len(self.rows) else None
            return {'ok': True, 'suite_id': params['suite_id'], 'status': self.status['status'],
                    'experiment': {'comparison_identity': self.identity}, 'trials': self.rows[offset:offset + limit],
                    'next_offset': following}
        if method == 'suite_status':
            return self.status
        return {'ok': False, 'reason': 'Unknown method: ' + method}

    def sealed_marks(self):
        return [params for method, params in self.calls if method == 'suite_sealed']


class FolderFixture(unittest.TestCase):
    token_bytes = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / 'home'
        self.run_dir = self.home / 'runs' / RUN
        self.call = Configurator()
        if FolderFixture.token_bytes is None:
            path = self.root / 'token'
            PASSWORD.seal({'schema': 'bench-evaluation-token/1', 'trial_id': 'trial', 'token': 'a' * 64}, path)
            FolderFixture.token_bytes = path.read_bytes()
            path.unlink()
        self.trial = self.make_trial()
        write_files(self.run_dir, {**RUN_FILES, **KEPT_FILES})
        self.parent = self.root / 'temp' / 'gd-candidate-abc'
        write_files(self.parent, WORKSPACE_FILES)

    def make_trial(self, *, ordinal=0, key='entry-000-repeat-000-aaaaaaaa', run_id=RUN, rerun=False, token=True,
                   **fields):
        trial = trial_store.suite_dir(self.home, 'suite-run-1') / 'trials' / key
        write_files(trial, {**TRIAL_FILES, **TRIAL_KEPT, '.trial.json.' + 'a' * 32: b'{"left": "by a writer"}',
                            'private/final/secret.json': b'{"hidden": "steps"}'})
        if token:
            (trial / 'evaluation-token.enc').write_bytes(self.token_bytes)
        request = 'suite:' + SUITE + ':trial:' + str(ordinal) + (':rerun' if rerun else '')
        record = {'schema': 'bench-trial/1', 'trial_id': key, 'request_id': request, 'case': 'generated',
                  'home': str(self.home), 'status': 'evaluated', 'run_id': run_id, 'pid': 12345, 'created': 1.5,
                  'start_sent': 2.5, 'finished': 3.5, 'level': 3, 'framing': 'text-lines', 'verdict': 'equivalent',
                  'outcome': 'pass', 'check_id': 'hidden-1', 'reason': 'Failed at ' + str(self.root / 'private'),
                  'mission': {'passed': True}, 'tokens': 7, 'tool_counts': {'probe_run': 1}, 'device_id': '0000d001',
                  'image': 'firmware.bin', 'truth': 'b' * 64, 'device': {'session_id': 's'}, 'sealed': True,
                  'equivalence': {'status': 'graded', 'equivalent': True}, 'evidence': {'path': 'run-evidence.enc'},
                  'run_status': {'status': 'completed'}, 'device_stop': {'confirmed': True}, **fields}
        (trial / 'trial.json').write_text(json.dumps(record), encoding='utf-8')
        return trial

    def seal(self, trial=None, run_dir=None, workspaces=None, password=PASSWORD, token='harness-token'):
        return trial_store.seal_trial(trial or self.trial, self.run_dir if run_dir is None else run_dir,
                                      [self.parent] if workspaces is None else workspaces, password, self.call, token)

    def generations(self, trial=None):
        return sorted(int(path.name.split('-')[1]) for path in (trial or self.trial).glob('sealed-*-index.enc'))

    def index(self, generation=1, trial=None):
        path = (trial or self.trial) / ('sealed-' + str(generation) + '-index.enc')
        return PASSWORD.unlock(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def members(self, generation=1, trial=None):
        """{member name: bytes} of one generation, read from the files as the seal's format describes them."""
        trial = trial or self.trial
        data = b''
        for part in self.index(generation, trial)['parts']:
            sealed = PASSWORD.unlock(trial / part['name'], part['sha256'])
            data += base64.b64decode(sealed['data'])
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
            return {member.name: tar.extractfile(member).read() for member in tar if member.isfile()}

    def assertPlaintextGone(self, trial=None):
        trial = trial or self.trial
        remaining = {path.relative_to(trial).as_posix() for path in trial.rglob('*') if path.is_file()}
        self.assertEqual({name for name in remaining if not (name.endswith('.enc') or name.endswith('.lock'))},
                         {'trial.json'})
        self.assertFalse((trial / 'private').exists())
        self.assertFalse((trial / 'device').exists())
        kept = {path.relative_to(self.run_dir).as_posix() for path in self.run_dir.rglob('*') if path.is_file()}
        self.assertEqual(kept, set(KEPT_FILES))
        self.assertFalse(self.parent.exists())

    def assertNothingDeleted(self):
        for relative, content in {**RUN_FILES, **KEPT_FILES}.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        for relative, content in WORKSPACE_FILES.items():
            self.assertEqual((self.parent / relative).read_bytes(), content)
        for relative, content in TRIAL_FILES.items():
            self.assertEqual((self.trial / relative).read_bytes(), content)
        self.assertNotEqual(harness.read_trial(self.trial).get('status'), 'sealed')


class WhatASealHoldsTests(FolderFixture):
    def test_07_B7_a_seal_holds_the_run_the_trial_and_the_workspaces_but_nothing_it_keeps(self):
        self.seal()
        members = self.members()
        expected = {**{'run/' + name: content for name, content in RUN_FILES.items()},
                    **{'trial/' + name: content for name, content in TRIAL_FILES.items()},
                    **{'workspaces/gd-candidate-abc/' + name: content for name, content in WORKSPACE_FILES.items()}}
        original = members.pop('trial/trial.json')
        self.assertEqual(members, expected)
        self.assertEqual(json.loads(original)['level'], 3)

    def test_07_B7_all_of_it_is_then_deleted_and_the_two_kept_paths_and_the_enc_files_stay(self):
        self.seal()
        self.assertPlaintextGone()
        self.assertEqual((self.trial / 'run-evidence.enc').read_bytes(), b'opaque evidence')
        self.assertEqual((self.trial / 'evaluation-token.enc').read_bytes(), self.token_bytes)
        self.assertFalse(list(self.trial.glob('.trial.json.*')))
        self.assertEqual((self.run_dir / 'benchmark/execution.json').read_bytes(),
                         KEPT_FILES['benchmark/execution.json'])
        self.assertEqual((self.run_dir / 'benchmark/inputs/firmware.bin').read_bytes(),
                         KEPT_FILES['benchmark/inputs/firmware.bin'])

    def test_07_B7_a_no_run_trial_gets_a_seal_with_no_run_folder(self):
        trial = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id=None)
        stub = trial_store.seal_trial(trial, None, [], PASSWORD, self.call, 'harness-token')
        members = self.members(trial=trial)
        self.assertEqual({name.split('/')[0] for name in members}, {'trial'})
        self.assertIn('trial/trial.json', members)
        self.assertEqual(stub['run_id'], None)
        self.assertEqual(self.call.sealed_marks(), [])

    def test_07_B8_private_is_removed_first_and_never_sealed(self):
        self.seal()
        self.assertFalse((self.trial / 'private').exists())
        self.assertFalse([name for name in self.members() if 'private' in name or 'secret' in name])

    def test_07_B8_the_index_lists_each_members_hash_its_original_path_and_the_parts(self):
        self.seal()
        index = self.index()
        members = self.members()
        for name, content in members.items():
            self.assertEqual(index['members'][name]['sha256'], hashlib.sha256(content).hexdigest(), name)
        self.assertEqual(index['members']['run/benchmark/state.json']['path'],
                         str(self.run_dir / 'benchmark/state.json'))
        self.assertEqual(index['members']['trial/device/journal.jsonl']['path'],
                         str(self.trial / 'device/journal.jsonl'))
        self.assertEqual(index['members']['workspaces/gd-candidate-abc/attempt1/model.json']['path'],
                         str(self.parent / 'attempt1/model.json'))
        self.assertEqual(index['generation'], 1)
        self.assertEqual([part['name'] for part in index['parts']], ['sealed-1-1.enc'])

    def test_07_B8_a_large_trial_is_split_into_bounded_parts(self):
        write_files(self.run_dir, {'work/large.bin': os.urandom(250_000)})
        with patch.object(trial_store, 'PART_BYTES', 100_000):
            self.seal()
        parts = self.index()['parts']
        self.assertEqual(len(parts), 3)
        for part in parts:
            sealed = PASSWORD.unlock(self.trial / part['name'], part['sha256'])
            self.assertLessEqual(len(base64.b64decode(sealed['data'])), 100_000)
        self.assertEqual(hashlib.sha256(self.members()['run/work/large.bin']).hexdigest(),
                         self.index()['members']['run/work/large.bin']['sha256'])

    def test_07_B8_a_file_still_growing_cannot_fail_verification(self):
        log = self.trial / 'device' / 'sessions' / 's' / 'emulator.log'
        log.parent.mkdir(parents=True)
        finished = threading.Event()
        stream = log.open('ab', buffering=0)
        self.addCleanup(stream.close)

        def grow():
            while not finished.is_set():
                stream.write(b'x' * 256)
        writer = threading.Thread(target=grow, daemon=True)
        stream.write(b'x' * 4096)
        writer.start()
        self.addCleanup(writer.join)
        self.addCleanup(finished.set)
        self.seal()
        finished.set()
        sealed = self.index()['members']['trial/device/sessions/s/emulator.log']
        self.assertEqual(hashlib.sha256(self.members()['trial/device/sessions/s/emulator.log']).hexdigest(),
                         sealed['sha256'])
        self.assertFalse(log.exists())


class PasswordProofTests(FolderFixture):
    def test_07_B6_a_wrong_password_stops_it_and_nothing_is_sealed_or_deleted(self):
        with self.assertRaisesRegex(trial_store.SealError, 'password') as raised:
            self.seal(password=WRONG)
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNothingDeleted()
        self.assertTrue((self.trial / 'private/final/secret.json').exists())
        self.assertEqual(self.generations(), [])
        self.assertEqual(self.call.sealed_marks(), [])

    def test_07_B6_a_no_run_trial_with_no_token_has_nothing_to_check(self):
        trial = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id=None, token=False)
        stub = trial_store.seal_trial(trial, None, [], WRONG, self.call, 'harness-token')
        self.assertEqual(stub['status'], 'sealed')


class StubTests(FolderFixture):
    def test_08_B5_the_figure_is_sealed_with_the_original_record_and_the_stub_holds_exactly_its_keys(self):
        figure = {'largest_bytes': 500, 'calls': 3}
        trial = self.make_trial(package_output=figure)
        stub = self.seal(trial)
        self.assertEqual(sorted(stub), ['case', 'finished', 'request_id', 'run_id', 'schema', 'sealed_files',
                                        'start_sent', 'status', 'trial_id'])
        self.assertNotIn('package_output', (trial / 'trial.json').read_text())
        self.assertEqual(json.loads(self.members(trial=trial)['trial/trial.json'])['package_output'], figure)

    def test_07_B11_the_stub_holds_exactly_these_keys(self):
        stub = self.seal()
        self.assertEqual(sorted(stub), ['case', 'finished', 'request_id', 'run_id', 'schema', 'sealed_files',
                                        'start_sent', 'status', 'trial_id'])
        self.assertEqual(json.loads((self.trial / 'trial.json').read_text()), stub)
        self.assertEqual(stub['status'], 'sealed')
        self.assertEqual((stub['run_id'], stub['case'], stub['start_sent'], stub['finished']),
                         (RUN, 'generated', 2.5, 3.5))
        self.assertEqual(stub['request_id'], 'suite:' + SUITE + ':trial:0')

    def test_07_B11_the_stub_lists_each_sealed_files_name_hash_and_time(self):
        stub = self.seal()
        files = {row['name']: row for row in stub['sealed_files']}
        self.assertEqual(sorted(files), ['sealed-1-1.enc', 'sealed-1-index.enc'])
        for name, row in files.items():
            self.assertEqual(sorted(row), ['name', 'sha256', 'time'])
            self.assertEqual(row['sha256'], hashlib.sha256((self.trial / name).read_bytes()).hexdigest())
            self.assertIs(type(row['time']), float)

    def test_07_B11_it_holds_no_private_field_and_a_trial_with_no_finished_time_gets_one(self):
        (self.trial / 'trial.json').write_text(json.dumps({**json.loads((self.trial / 'trial.json').read_text()),
                                                           'finished': None}))
        stub = self.seal()
        text = (self.trial / 'trial.json').read_text()
        self.assertIs(type(stub['finished']), float)
        for field in HOST_FIELDS:
            self.assertNotIn('"' + field + '":', text)
        for value in (str(self.home), str(self.root), 'text-lines', 'hidden-1', 'firmware.bin', 'b' * 64):
            self.assertNotIn(value, text)

    def test_07_B11_the_harness_works_from_a_stub(self):
        self.seal()
        self.assertEqual(harness._trial_of(trial_store.trial_dirs(self.home), RUN), self.trial)
        self.assertEqual(harness._trial_of(trial_store.trial_dirs(self.home), 'another-run'), None)
        record = harness.read_trial(self.trial)
        self.assertTrue(harness._harness_exited(None, record, self.trial))
        before = (self.trial / 'trial.json').read_bytes()
        harness._adopt(self.trial, RUN, True)
        self.assertEqual((self.trial / 'trial.json').read_bytes(), before)

    def test_07_B8_it_tells_the_configurator_the_suite_its_slot_and_the_run(self):
        self.seal()
        self.assertEqual(self.call.sealed_marks(), [{'suite_id': SUITE, 'ordinal': 0, 'run_id': RUN,
                                                     'token': 'harness-token'}])

    def test_07_B8_with_no_token_it_sends_none(self):
        self.seal(token=None)
        self.assertEqual(self.call.sealed_marks(), [{'suite_id': SUITE, 'ordinal': 0, 'run_id': RUN}])


class Crash(BaseException):
    """A harness killed at this step: no `except Exception` of the code under test sees it."""


def crashing(attribute, *, after=0, target=trial_store):
    """A patch of `attribute` of `target` under which its call number `after`, counted from 0, crashes."""
    real = getattr(target, attribute)
    calls = []

    def call(*args, **kwargs):
        calls.append(args)
        if len(calls) > after:
            raise Crash(attribute)
        return real(*args, **kwargs)
    return patch.object(target, attribute, call)


class SweepFixture(FolderFixture):
    def setUp(self):
        super().setUp()
        self.call.rows = [self.row()]
        self.call.events = [{'kind': 'stage.assigned', 'data': {'stage': 'probe',
                                                                'workspace': str(self.run_dir / 'work')}},
                            {'kind': 'stage.assigned', 'data': {'stage': 'interpret',
                                                                'workspace': str(self.parent / 'attempt1')}}]

    def row(self, ordinal=0, status='finished', run_id=RUN, **fields):
        return {'ordinal': ordinal, 'trial_key': 'entry-%03d-repeat-000' % ordinal, 'case': 'generated',
                'run_id': run_id, 'status': status, **fields}

    def sweep(self, password=PASSWORD, token='harness-token', **options):
        trial_store.sweep(self.home, SUITE, password, self.call, token, **options)

    def seal_files(self, trial=None):
        return {path.name: path.read_bytes() for path in (trial or self.trial).glob('sealed-*.enc')}

    def fresh_trial(self):
        """The fixture's trial, run and workspace as they were before anything was sealed."""
        shutil.rmtree(self.trial)
        shutil.rmtree(self.run_dir)
        shutil.rmtree(self.parent, ignore_errors=True)
        self.trial = self.make_trial()
        write_files(self.run_dir, {**RUN_FILES, **KEPT_FILES})
        write_files(self.parent, WORKSPACE_FILES)
        self.call.calls.clear()

    def assertAllSealed(self):
        """Everything of the fixture's trial is in its seal, its plaintext is gone and the configurator knows."""
        self.assertPlaintextGone()
        members = {}
        for generation in self.generations():
            members.update(self.members(generation))
        for name, content in RUN_FILES.items():
            self.assertEqual(members['run/' + name], content)
        for name, content in TRIAL_FILES.items():
            self.assertEqual(members['trial/' + name], content)
        for name, content in WORKSPACE_FILES.items():
            self.assertEqual(members['workspaces/gd-candidate-abc/' + name], content)
        self.assertEqual(json.loads(members['trial/trial.json'])['level'], 3)
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertIn({'suite_id': SUITE, 'ordinal': 0, 'run_id': RUN, 'token': 'harness-token'},
                      self.call.sealed_marks())


class CrashStepTests(SweepFixture):
    """Each step of 07-B8 in order: a harness killed there leaves a state the sweep resolves, and no plaintext is
    deleted before its seal has verified."""

    def assertSweepFinishes(self, deleted=False):
        if not deleted:
            self.assertNothingDeleted()
        self.sweep()
        self.assertAllSealed()

    def test_07_B8_killed_after_removing_private(self):
        with crashing('_write_generation'), self.assertRaises(Crash):
            self.seal()
        self.assertFalse((self.trial / 'private').exists())
        self.assertEqual(self.generations(), [])
        self.assertSweepFinishes()
        self.assertEqual(self.generations(), [1])

    def test_07_B8_killed_with_its_parts_written_and_no_index(self):
        with crashing('_seal_file', after=1), self.assertRaises(Crash):
            self.seal()
        self.assertEqual(sorted(self.seal_files()), ['sealed-1-1.enc'])
        self.assertSweepFinishes()
        # That generation never finished, so the seal is another, and the part left behind is not overwritten.
        self.assertEqual(self.generations(), [2])
        self.assertIn('sealed-1-1.enc', self.seal_files())

    def test_07_B8_killed_with_its_index_written_before_it_verified(self):
        with crashing('_verify_generation'), self.assertRaises(Crash):
            self.seal()
        before = self.seal_files()
        self.assertEqual(sorted(before), ['sealed-1-1.enc', 'sealed-1-index.enc'])
        self.assertSweepFinishes()
        self.assertEqual(self.generations(), [1])
        self.assertEqual(self.seal_files(), before)

    def test_07_B8_killed_after_it_verified_before_the_stub(self):
        with crashing('_private', target=harness), self.assertRaises(Crash):
            self.seal()
        self.assertNotEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertSweepFinishes()
        self.assertEqual(self.generations(), [1])

    def test_07_B8_killed_after_the_stub_before_it_told_the_configurator(self):
        with crashing('_mark_sealed'), self.assertRaises(Crash):
            self.seal()
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertEqual(self.call.sealed_marks(), [])
        for relative, content in {**RUN_FILES, **KEPT_FILES}.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        self.assertSweepFinishes(deleted=True)
        self.assertEqual(self.generations(), [1])

    def test_07_B8_killed_after_it_told_the_configurator_before_it_deleted(self):
        with crashing('_delete_plaintext'), self.assertRaises(Crash):
            self.seal()
        self.assertEqual(len(self.call.sealed_marks()), 1)
        self.assertSweepFinishes(deleted=True)
        # The delete branch sends the mark again.
        self.assertEqual(len(self.call.sealed_marks()), 2)
        self.assertEqual(self.generations(), [1])

    def test_07_B8_killed_part_way_through_the_delete(self):
        for after in (1, 3, 5):
            with self.subTest(after):
                self.fresh_trial()
                with crashing('_remove_file', after=after), self.assertRaises(Crash):
                    self.seal()
                self.assertSweepFinishes(deleted=True)
                self.assertEqual(self.generations(), [1])

    def test_07_B8_the_workspaces_go_first_so_leftovers_always_show_in_the_run(self):
        # Its first call is for `private/`, and its second for the workspace.
        with crashing('_remove_tree', after=1), self.assertRaises(Crash):
            self.seal()
        self.assertFalse((self.trial / 'private').exists())
        self.assertTrue(self.parent.exists())
        self.assertTrue((self.run_dir / 'benchmark/state.json').exists())
        with crashing('_remove_file', after=2), self.assertRaises(Crash):
            self.sweep()
        self.assertFalse(self.parent.exists())
        self.assertTrue(any(self.run_dir.rglob('*.json')))
        self.sweep()
        self.assertAllSealed()


class WhatTheSweepDecidesTests(SweepFixture):
    def test_07_B3_no_seal_and_plaintext_seals(self):
        self.sweep()
        self.assertAllSealed()
        self.assertEqual(self.generations(), [1])

    def test_07_B3_a_valid_seal_and_plaintext_deletes_what_matches_and_seals_what_changed_or_is_extra(self):
        with crashing('_mark_sealed'), self.assertRaises(Crash):
            self.seal()
        before = self.seal_files()
        (self.run_dir / 'benchmark/state.json').write_bytes(b'{"s": "changed"}')
        write_files(self.run_dir, {'report.json': b'{"written": "after"}'})
        write_files(self.trial, {'device/late.log': b'late'})
        self.sweep()
        self.assertEqual(self.generations(), [1, 2])
        self.assertEqual({name: self.seal_files()[name] for name in before}, before)
        self.assertEqual(self.members(2), {'run/benchmark/state.json': b'{"s": "changed"}',
                                           'run/report.json': b'{"written": "after"}',
                                           'trial/device/late.log': b'late'})
        self.assertFalse((self.run_dir / 'report.json').exists())
        self.assertPlaintextGone()
        names = {row['name'] for row in harness.read_trial(self.trial)['sealed_files']}
        self.assertEqual(names, set(self.seal_files()))

    def test_07_B3_a_member_missing_from_disk_is_no_disagreement(self):
        with crashing('_mark_sealed'), self.assertRaises(Crash):
            self.seal()
        (self.run_dir / 'benchmark/state.json').unlink()
        self.sweep()
        self.assertEqual(self.generations(), [1])
        self.assertPlaintextGone()

    def test_07_B3_a_seal_and_no_plaintext_is_done_with_no_key_derived_and_nothing_sent(self):
        self.sweep()
        self.call.calls.clear()
        before = self.seal_files(), (self.trial / 'trial.json').read_bytes()
        with patch.object(trial_store, '_open_file', side_effect=AssertionError('It unlocked something')):
            self.sweep()
        self.assertEqual((self.seal_files(), (self.trial / 'trial.json').read_bytes()), before)
        self.assertEqual([method for method, _ in self.call.calls], ['suite_result'])

    def test_07_B3_a_stub_with_no_local_seal_and_no_plaintext_is_done(self):
        self.sweep()
        for path in self.trial.glob('sealed-*.enc'):
            path.unlink()
        self.call.calls.clear()
        self.sweep()
        self.assertEqual([method for method, _ in self.call.calls], ['suite_result'])

    def test_07_B3_a_stub_with_no_local_seal_and_a_late_file_seals_that_file(self):
        self.sweep()
        for path in self.trial.glob('sealed-*.enc'):
            path.unlink()
        write_files(self.run_dir, {'report.json': b'{"written": "after"}'})
        self.sweep()
        self.assertEqual(self.generations(), [2])
        self.assertEqual(self.members(2), {'run/report.json': b'{"written": "after"}'})
        self.assertFalse((self.run_dir / 'report.json').exists())
        names = {row['name'] for row in harness.read_trial(self.trial)['sealed_files']}
        self.assertLessEqual({'sealed-1-index.enc', 'sealed-2-index.enc'}, names)

    def test_07_B3_no_stub_and_no_plaintext_stops_the_suite(self):
        shutil.rmtree(self.trial)
        for name in RUN_FILES:
            (self.run_dir / name).unlink()
        shutil.rmtree(self.run_dir / 'work')
        shutil.rmtree(self.run_dir / 'handoffs')
        shutil.rmtree(self.run_dir / 'benchmark/tools')
        with self.assertRaisesRegex(trial_store.SealError, 'neither a stub nor plaintext') as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_07_B3_a_no_run_slot_with_no_folder_stops_the_suite_too(self):
        self.call.rows = [self.row(1, 'finished', None)]
        with self.assertRaisesRegex(trial_store.SealError, 'neither a stub nor plaintext'):
            self.sweep()

    def test_07_B3_a_sealed_part_that_will_not_unlock_stops_the_suite_and_deletes_nothing(self):
        with crashing('_mark_sealed'), self.assertRaises(Crash):
            self.seal()
        (self.trial / 'sealed-1-1.enc').write_bytes(b'not what was sealed')
        with self.assertRaises(trial_store.SealError) as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))
        for relative, content in RUN_FILES.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        self.assertTrue(self.parent.exists())

    def test_07_B3_a_seal_under_another_password_stops_the_suite_and_deletes_nothing(self):
        trial = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id=None, token=False,
                                start_sent=None)
        self.call.rows = [self.row(1, 'finished', None)]
        with crashing('_mark_sealed'), self.assertRaises(Crash):
            trial_store.seal_trial(trial, self.run_dir, [], WRONG, self.call, 'harness-token')
        with self.assertRaises(trial_store.SealError):
            trial_store.seal_trial(trial, self.run_dir, [], PASSWORD, self.call, 'harness-token')
        self.assertEqual((self.run_dir / 'benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])

    def test_07_B3_an_unreadable_trial_json_beside_a_seal_stops_the_suite(self):
        self.seal()
        for damage, write in (('unreadable', lambda path: path.write_text('{not json')), ('missing', Path.unlink)):
            with self.subTest(damage):
                write(self.trial / 'trial.json')
                with self.assertRaisesRegex(trial_store.SealError, 'trial record') as raised:
                    self.sweep()
                self.assertNotIn(str(self.root), str(raised.exception))

    def test_07_B3_an_unreadable_record_of_another_suites_harness_is_not_this_suites_to_stop_at(self):
        self.seal()
        (self.trial / 'trial.json').write_text('{not json')
        (self.trial.parent.parent / 'suite.json').write_text(json.dumps({'suite_id': 'suite-other'}))
        self.call.rows = []
        self.sweep()

    def test_07_B3_a_folder_with_no_record_and_no_seal_is_a_trial_that_never_began(self):
        (self.trial / 'trial.json').unlink()
        for path in self.trial.glob('*.enc'):
            path.unlink()
        self.call.rows = []
        self.sweep()
        self.assertTrue((self.trial / 'device').exists())


class WhichTrialsTheSweepTakesTests(SweepFixture):
    def test_07_B2_each_settled_status_is_concluded(self):
        for status in ('finished', 'faulted', 'flagged'):
            with self.subTest(status):
                self.call.rows = [self.row(status=status)]
                self.call.calls.clear()
                self.fresh_trial()
                self.sweep()
                self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')

    def test_07_B2_a_slot_still_active_or_its_stopped_childs_status_is_not(self):
        for status in ('launching', 'running', 'pending', 'blocked', 'failed', 'cancelled', 'completed'):
            with self.subTest(status):
                self.call.rows = [self.row(status=status)]
                self.sweep()
                self.assertNothingDeleted()

    def test_07_B2_a_reruns_first_attempt_is_swept_under_its_own_request_while_the_rerun_is_not(self):
        first = self.make_trial(key='entry-000-repeat-000-first000', run_id='first-run')
        write_files(self.home / 'runs/first-run', RUN_FILES)
        rerun = self.make_trial(key='entry-000-repeat-000-second00', rerun=True)
        first_record = json.loads((first / 'trial.json').read_text())
        self.call.rows = [self.row(status='running', attempt=2, first_attempt={
            'run_id': 'first-run', 'request_id': first_record['request_id'], 'outcome': 'fault', 'tokens': None,
            'seconds': 1})]
        (self.trial / 'trial.json').unlink()
        shutil.rmtree(self.trial)
        self.sweep()
        self.assertEqual(harness.read_trial(first)['status'], 'sealed')
        self.assertEqual(harness.read_trial(first)['run_id'], 'first-run')
        self.assertEqual(harness.read_trial(rerun)['status'], 'evaluated')
        self.assertEqual((self.run_dir / 'benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])

    def test_07_B2_a_regrades_active_slot_is_safe_as_its_first_attempt_shares_its_request(self):
        record = json.loads((self.trial / 'trial.json').read_text())
        self.call.rows = [self.row(status='running', first_attempt={
            'run_id': RUN, 'request_id': record['request_id'], 'outcome': 'fault', 'tokens': None, 'seconds': 1})]
        self.sweep()
        self.assertNothingDeleted()

    def test_07_B2_a_no_run_slots_request_comes_from_its_ordinal_and_attempt(self):
        trial = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id=None, rerun=True)
        self.call.rows = [self.row(1, 'finished', None, attempt=2, no_run_reason='admit-refused')]
        self.sweep()
        self.assertEqual(harness.read_trial(trial)['status'], 'sealed')
        self.assertNothingDeleted()

    def test_07_B1_another_suites_trials_are_never_taken(self):
        other = self.trial.parent / 'other-suite-trial'
        write_files(other, TRIAL_FILES)
        (other / 'trial.json').write_text(json.dumps({'request_id': 'suite:suite-other:trial:0', 'run_id': RUN}))
        self.call.rows = []
        self.sweep()
        self.assertTrue((other / 'device/journal.jsonl').exists())
        self.assertNothingDeleted()

    def test_07_B1_a_suite_that_is_not_scoring_is_left_alone(self):
        del self.call.identity['scoring_set']
        self.sweep()
        self.assertNothingDeleted()
        self.assertEqual([method for method, _ in self.call.calls], ['suite_result'])

    def test_07_B2_it_reads_every_page_of_a_large_suite(self):
        self.call.rows = [self.row(number, 'pending', None) for number in range(120)] + [self.row(120)]
        self.call.rows[120]['ordinal'] = 0
        self.sweep()
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertEqual([params.get('offset', 0) for method, params in self.call.calls if method == 'suite_result'],
                         [0, 100])

    def test_07_B2_several_folders_of_one_request_are_all_sealed_and_only_the_one_that_ran_it_names_the_run(self):
        reserved_again = self.make_trial(key='entry-000-repeat-000-earlier0', run_id=None)
        self.sweep()
        self.assertEqual(harness.read_trial(reserved_again)['status'], 'sealed')
        self.assertEqual(harness.read_trial(reserved_again)['run_id'], None)
        self.assertEqual({name.split('/')[0] for name in self.members(trial=reserved_again)}, {'trial'})
        self.assertAllSealed()

    def test_07_B2_a_trial_that_never_heard_of_its_run_is_sealed_with_it(self):
        record = json.loads((self.trial / 'trial.json').read_text())
        (self.trial / 'trial.json').write_text(json.dumps({**record, 'run_id': None}))
        self.sweep()
        self.assertEqual(harness.read_trial(self.trial)['run_id'], RUN)
        self.assertIn('run/benchmark/state.json', self.members())

    def test_07_B7_a_run_with_no_trial_folder_gets_one_made_from_the_slots_row(self):
        shutil.rmtree(self.trial)
        self.sweep(proven=True)
        trial = trial_store.find_trial(self.home, SUITE, RUN)
        stub = harness.read_trial(trial)
        self.assertEqual((stub['status'], stub['run_id'], stub['case'], stub['start_sent']),
                         ('sealed', RUN, 'generated', None))
        self.assertEqual(stub['request_id'], 'suite:' + SUITE + ':trial:0')
        self.assertEqual(self.members(trial=trial)['run/benchmark/state.json'], RUN_FILES['benchmark/state.json'])
        self.assertIn('trial/trial.json', self.members(trial=trial))
        self.assertFalse((self.run_dir / 'benchmark/state.json').exists())

    def test_07_B6_a_folder_made_from_a_row_proves_the_password_on_another_trial_of_the_suite(self):
        neighbour = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id='run-2')
        shutil.rmtree(self.trial)
        self.call.rows = [self.row()]
        with self.assertRaisesRegex(trial_store.SealError, 'password'):
            self.sweep(password=WRONG)
        self.assertEqual((self.run_dir / 'benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])
        self.assertTrue((neighbour / 'device').exists())

    def test_07_B2_a_configurator_it_cannot_reach_is_a_distinct_failure_naming_no_path(self):
        self.call.refused['suite_result'] = 'No configurator at ' + str(self.home)
        with self.assertRaises(trial_store.Unreachable) as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNothingDeleted()

    def test_07_B2_a_lost_events_page_is_the_same_and_deletes_nothing(self):
        self.call.refused['events'] = 'No configurator at ' + str(self.home)
        with self.assertRaises(trial_store.Unreachable):
            self.sweep()
        self.assertNothingDeleted()


class FailureTests(SweepFixture):
    def test_07_B4_a_sealing_failure_stops_it_naming_no_path_and_a_second_sweep_seals_what_is_left(self):
        error = OSError(28, 'No space left', str(self.trial / 'sealed-1-1.enc'))
        with patch.object(trial_store, '_seal_file', side_effect=error), \
                self.assertRaisesRegex(trial_store.SealError, 'failed: OSError') as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNothingDeleted()
        self.sweep()
        self.assertAllSealed()

    def test_07_B4_a_verifying_failure_stops_it_with_nothing_deleted(self):
        real, read = trial_store._PartReader.readinto, []

        def damaged(reader, buffer):
            size = real(reader, buffer)
            if size > 16 and not read:
                # Early in the first read: a byte late in the stream may be only padding, or the gzip trailer, which
                # Python's stream reader ignores. The index holds hashes for that reason.
                read.append(size)
                buffer[16] = buffer[16] ^ 0xff
            return size
        with patch.object(trial_store._PartReader, 'readinto', damaged), self.assertRaises(trial_store.SealError):
            self.sweep()
        self.assertNothingDeleted()
        self.sweep()
        self.assertAllSealed()

    def test_07_B8_a_seal_that_differs_from_its_index_is_refused(self):
        real = trial_store._write_generation

        def lying(trial, generation, chosen, password):
            index = real(trial, generation, chosen, password)
            index['members']['run/benchmark/state.json']['sha256'] = '0' * 64
            return index
        with patch.object(trial_store, '_write_generation', lying), \
                self.assertRaisesRegex(trial_store.SealError, 'differs from its index'):
            self.sweep()
        self.assertNothingDeleted()

    def test_07_B4_a_deleting_failure_stops_it_and_a_second_sweep_finishes(self):
        real = os.unlink

        def refused(path, *args, **kwargs):
            if 'state.json' in str(path):
                raise PermissionError(13, 'Held open', str(path))
            return real(path, *args, **kwargs)
        with patch('os.unlink', refused), patch.object(trial_store, '_DELETE_PAUSE', 0), \
                self.assertRaisesRegex(trial_store.SealError, 'failed: PermissionError') as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertTrue((self.run_dir / 'benchmark/state.json').exists())
        self.sweep()
        self.assertAllSealed()

    def test_07_B9_deleting_retries_as_a_reader_lets_go_without_ignoring_an_error(self):
        real, refusals = os.unlink, []

        def briefly(path, *args, **kwargs):
            if 'state.json' in str(path) and len(refusals) < 3:
                refusals.append(path)
                raise PermissionError(13, 'Held open', str(path))
            return real(path, *args, **kwargs)
        with patch('os.unlink', briefly), patch.object(trial_store, '_DELETE_PAUSE', 0):
            self.sweep()
        self.assertEqual(len(refusals), 3)
        self.assertAllSealed()

    def test_07_B4_a_folder_that_will_not_go_stops_it_too(self):
        real = os.rmdir

        def refused(path, *args, **kwargs):
            if str(path).endswith('handoffs'):
                raise PermissionError(13, 'Held open', str(path))
            return real(path, *args, **kwargs)
        with patch('os.rmdir', refused), patch.object(trial_store, '_DELETE_PAUSE', 0), \
                self.assertRaisesRegex(trial_store.SealError, 'failed: PermissionError'):
            self.sweep()
        self.sweep()
        self.assertAllSealed()

    def test_07_B8_plaintext_still_there_after_the_delete_stops_it(self):
        real = trial_store._remove_entries

        def writer_comes_back(entries):
            real(entries)
            write_files(self.trial, {'device/again.log': b'it came back'})
        with patch.object(trial_store, '_remove_entries', writer_comes_back), \
                self.assertRaisesRegex(trial_store.SealError, 'could not be deleted'):
            self.sweep()

    def test_07_B4_a_refused_mark_stops_it_before_anything_is_deleted(self):
        self.call.refused['suite_sealed'] = 'Refused under ' + str(self.home)
        with self.assertRaisesRegex(trial_store.SealError, 'did not mark') as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))
        for relative, content in RUN_FILES.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        del self.call.refused['suite_sealed']
        self.sweep()
        self.assertAllSealed()

    def test_07_B4_a_mark_that_raises_is_a_refusal_too(self):
        def raises(method, params=None):
            if method == 'suite_sealed':
                raise ConnectionResetError('The reply was lost')
            return self.call(method, params)
        with self.assertRaisesRegex(trial_store.SealError, 'did not mark'):
            trial_store.sweep(self.home, SUITE, PASSWORD, raises, 'harness-token')


class LinksAndModesTests(SweepFixture):
    def test_07_B9_a_link_is_not_followed_listed_as_skipped_and_only_the_link_goes(self):
        outside = self.root / 'outside.txt'
        outside.write_bytes(b'not the trials to seal')
        folder = self.root / 'outside-folder'
        write_files(folder, {'inner.txt': b'not this either'})
        (self.run_dir / 'work/link-to-file').symlink_to(outside)
        (self.trial / 'device/link-to-folder').symlink_to(folder, target_is_directory=True)
        self.sweep()
        skipped = {row['name']: row['kind'] for row in self.index()['skipped']}
        self.assertEqual(skipped, {'run/work/link-to-file': 'symlink', 'trial/device/link-to-folder': 'symlink'})
        members = self.members()
        self.assertFalse([name for name in members if 'link-to' in name or 'inner' in name])
        self.assertFalse(os.path.lexists(self.run_dir / 'work/link-to-file'))
        self.assertEqual(outside.read_bytes(), b'not the trials to seal')
        self.assertEqual((folder / 'inner.txt').read_bytes(), b'not this either')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'This system has no FIFOs')
    def test_07_B9_a_fifo_is_listed_as_skipped_and_never_read(self):
        os.mkfifo(self.run_dir / 'work/a-pipe')
        self.sweep()
        self.assertEqual(self.index()['skipped'], [{'name': 'run/work/a-pipe', 'kind': 'fifo',
                                                    'path': str(self.run_dir / 'work/a-pipe')}])
        self.assertFalse(os.path.lexists(self.run_dir / 'work/a-pipe'))

    @unittest.skipUnless(hasattr(socket, 'AF_UNIX'), 'This system has no such sockets')
    def test_07_B9_a_socket_is_listed_as_skipped_and_never_read(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='gd-s-') as short, socket.socket(socket.AF_UNIX) as held:
            path = Path(short) / 's'
            held.bind(str(path))
            parent = Path(short) / 'gd-candidate-s'
            parent.mkdir()
            shutil.move(str(path), str(parent / 's'))
            self.call.events.append({'kind': 'stage.assigned', 'data': {'stage': 'interpret',
                                                                        'workspace': str(parent / 'attempt1')}})
            self.sweep()
            self.assertIn('socket', [row['kind'] for row in self.index()['skipped']])
            self.assertFalse(parent.exists())

    def test_07_B9_a_workers_chmod_000_stops_neither_the_seal_nor_the_delete(self):
        secret = self.run_dir / 'work/probe/a1/_agent_events.jsonl'
        folder = self.trial / 'device'
        workspace = self.parent / 'attempt1'
        secret.chmod(0)
        folder.chmod(0)
        workspace.chmod(0)
        self.addCleanup(lambda: [os.path.exists(path) and path.chmod(0o700) for path in (folder, workspace)])
        self.sweep()
        self.assertAllSealed()
        index = self.index()['members']
        self.assertEqual(index['run/work/probe/a1/_agent_events.jsonl']['mode'], 0)
        self.assertEqual(index['trial/device']['mode'], 0)

    def test_07_B9_a_whole_trial_folder_chmod_000_is_fixed_as_well(self):
        self.trial.chmod(0o700)
        self.run_dir.chmod(0)
        self.addCleanup(lambda: self.run_dir.chmod(0o700))
        self.sweep()
        self.assertAllSealed()

    def forged(self, build):
        """A generation of the fixture's trial, under the real password, whose tar.gz `build` writes."""
        (self.trial / 'trial.json').write_text(json.dumps({**json.loads((self.trial / 'trial.json').read_text()),
                                                           'status': 'sealed', 'sealed_files': []}))
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as tar:
            build(tar)
        part = PASSWORD.seal({'schema': 'bench-sealed-part/1', 'data': base64.b64encode(data.getvalue()).decode()},
                             self.trial / 'sealed-1-1.enc')
        PASSWORD.seal({'schema': 'bench-sealed-index/1', 'generation': 1, 'members': {}, 'skipped': [],
                       'parts': [{'name': 'sealed-1-1.enc', 'sha256': part}]}, self.trial / 'sealed-1-index.enc')

    def test_07_B9_extraction_accepts_only_plain_files_and_folders_with_relative_paths_inside_the_target(self):
        def named(name, kind=tarfile.REGTYPE, target=''):
            def build(tar):
                info = tarfile.TarInfo(name)
                info.type, info.linkname = kind, target
                info.size = 4 if kind == tarfile.REGTYPE else 0
                tar.addfile(info, io.BytesIO(b'evil') if kind == tarfile.REGTYPE else None)
            return build
        escape = self.root / 'escaped.txt'
        for label, build in (('climbs', named('../../../../escaped.txt')), ('absolute', named(str(escape))),
                             ('link', named('run/link', tarfile.SYMTYPE, str(escape))),
                             ('hard link', named('run/link', tarfile.LNKTYPE, str(escape))),
                             ('fifo', named('run/pipe', tarfile.FIFOTYPE))):
            with self.subTest(label):
                self.forged(build)
                with self.assertRaisesRegex(trial_store.SealError, 'other than plain files'):
                    with trial_store.opened(self.home, SUITE, self.trial, PASSWORD):
                        self.fail('It opened')
                self.assertFalse(escape.exists())

    def test_07_B9_a_name_a_windows_path_would_root_or_climb_is_refused_on_any_platform(self):
        def named(name):
            def build(tar):
                info = tarfile.TarInfo(name)
                info.size = 4
                tar.addfile(info, io.BytesIO(b'evil'))
            return build
        for name in ('\\evil', 'C:/evil', 'C:\\evil', 'C:evil', '\\\\server\\share\\evil', 'run\\..\\..\\evil', '/evil',
                     'run/../../evil'):
            with self.subTest(name):
                self.forged(named(name))
                with self.assertRaisesRegex(trial_store.SealError, 'other than plain files'):
                    with trial_store.opened(self.home, SUITE, self.trial, PASSWORD):
                        self.fail('It opened')

    def test_07_B9_a_member_its_index_does_not_list_is_refused(self):
        def build(tar):
            info = tarfile.TarInfo('run/unlisted')
            info.size = 4
            tar.addfile(info, io.BytesIO(b'evil'))
        self.forged(build)
        with self.assertRaisesRegex(trial_store.SealError, 'differs from its index'):
            with trial_store.opened(self.home, SUITE, self.trial, PASSWORD):
                self.fail('It opened')


class WorkspaceShapeTests(SweepFixture):
    def assertRefused(self, named):
        self.call.events = [{'kind': 'stage.assigned', 'data': {'stage': 'interpret', 'workspace': named}}]
        with self.assertRaisesRegex(trial_store.SealError, 'wrong shape') as raised:
            self.sweep()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNothingDeleted()
        self.assertTrue((self.trial / 'private/final/secret.json').exists())
        self.assertEqual(self.generations(), [])

    def test_07_B10_any_other_shape_stops_the_suite_and_deletes_nothing(self):
        victim = self.root / 'victim'
        write_files(victim, {'precious.txt': b'keep me'})
        for label, named in (('another folder', str(victim)),
                             ('another folder with an attempt', str(victim / 'attempt1')),
                             ('not an attempt', str(self.parent / 'workspace')),
                             ('a climbing path', str(self.parent / '../gd-candidate-abc/attempt1')),
                             ('inside the run', str(self.run_dir / 'gd-candidate-x/attempt1')),
                             ('relative', 'gd-candidate-abc/attempt1'),
                             ('no prefix tail', str(self.root / 'gd-candidate-/attempt1')),
                             ('none', None), ('not text', 7)):
            with self.subTest(label):
                self.assertRefused(named)
                self.assertEqual((victim / 'precious.txt').read_bytes(), b'keep me')

    def test_07_B10_a_parent_that_is_a_link_is_refused(self):
        victim = self.root / 'victim'
        write_files(victim, {'attempt1/precious.txt': b'keep me'})
        link = self.root / 'temp' / 'gd-candidate-link'
        link.symlink_to(victim, target_is_directory=True)
        self.assertRefused(str(link / 'attempt1'))
        self.assertEqual((victim / 'attempt1/precious.txt').read_bytes(), b'keep me')

    def test_07_B10_a_runs_workspaces_are_read_from_its_interpret_assignments_and_the_parent_goes_too(self):
        write_files(self.parent, {'attempt2/model.json': b'{"second": 1}', 'beside.txt': b'beside the attempts'})
        other = self.root / 'temp' / 'gd-candidate-other'
        write_files(other, {'attempt3/model.json': b'{"third": 1}'})
        decoy = self.root / 'temp' / 'gd-candidate-decoy'
        write_files(decoy, {'attempt1/model.json': b'{"not": "interpret"}'})
        for name, parent, stage in (('attempt1', self.parent, 'interpret'), ('attempt2', self.parent, 'interpret'),
                                    ('attempt3', other, 'interpret'), ('attempt1', decoy, 'probe')):
            self.call.events.append({'kind': 'stage.assigned', 'data': {'stage': stage,
                                                                        'workspace': str(parent / name)}})
        self.call.events.append({'kind': 'stage.accepted', 'data': {'stage': 'interpret', 'workspace': '/elsewhere'}})
        self.sweep()
        members = self.members()
        self.assertEqual(members['workspaces/gd-candidate-abc/beside.txt'], b'beside the attempts')
        self.assertEqual(members['workspaces/gd-candidate-abc/attempt2/model.json'], b'{"second": 1}')
        self.assertEqual(members['workspaces/gd-candidate-other/attempt3/model.json'], b'{"third": 1}')
        self.assertFalse(self.parent.exists())
        self.assertFalse(other.exists())
        self.assertTrue(decoy.exists())

    def test_07_B10_a_workspace_already_gone_is_nothing_to_seal(self):
        shutil.rmtree(self.parent)
        self.sweep()
        self.assertFalse([name for name in self.members() if name.startswith('workspaces/')])

    def test_07_B10_a_long_event_log_is_read_to_its_end(self):
        filler = {'kind': 'tool.started', 'data': {'stage': 'probe'}}
        self.call.events = [filler] * 500 + [{'kind': 'stage.assigned', 'data': {
            'stage': 'interpret', 'workspace': str(self.parent / 'attempt1')}}]
        self.sweep()
        self.assertIn('workspaces/gd-candidate-abc/attempt1/model.json', self.members())


class ReadingBackTests(SweepFixture):
    def assertOpensAsWritten(self, root):
        for name, content in RUN_FILES.items():
            self.assertEqual((root / 'run' / name).read_bytes(), content)
        for name, content in TRIAL_FILES.items():
            self.assertEqual((root / 'trial' / name).read_bytes(), content)
        self.assertEqual(json.loads((root / 'trial/trial.json').read_text())['level'], 3)

    def test_07_B13_it_reads_a_seal_back_into_a_private_folder_removed_afterwards(self):
        self.sweep()
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD) as root:
            self.assertOpensAsWritten(root)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['run', 'trial', 'workspaces'])
            self.assertEqual((root / 'workspaces/gd-candidate-abc/attempt1/model.json').read_bytes(),
                             WORKSPACE_FILES['attempt1/model.json'])
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertFalse((root / 'run/benchmark/execution.json').exists())
        self.assertFalse(root.exists())

    def test_07_B13_it_finds_a_trial_by_its_run_trial_or_folder(self):
        self.sweep()
        for key in (RUN, self.trial.name, self.trial):
            with self.subTest(str(key)), trial_store.opened(self.home, SUITE, key, PASSWORD) as root:
                self.assertOpensAsWritten(root)

    def test_07_B13_each_member_comes_from_its_latest_generation(self):
        with crashing('_mark_sealed'), self.assertRaises(Crash):
            self.seal()
        (self.run_dir / 'benchmark/state.json').write_bytes(b'{"s": "later"}')
        self.sweep()
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD) as root:
            self.assertEqual((root / 'run/benchmark/state.json').read_bytes(), b'{"s": "later"}')
            self.assertEqual((root / 'run/handoffs/emit/package.bin').read_bytes(),
                             RUN_FILES['handoffs/emit/package.bin'])

    def test_07_B13_a_trial_still_plaintext_on_a_blocked_suite_opens_the_same_way_and_stays(self):
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD) as root:
            self.assertOpensAsWritten(root)
            self.assertFalse((root / 'trial/private').exists())
            self.assertFalse((root / 'trial/run-evidence.enc').exists())
            self.assertFalse((root / 'run/benchmark/inputs').exists())
        self.assertNothingDeleted()

    def test_07_B13_a_seal_with_no_password_or_the_wrong_one_raises(self):
        self.sweep()
        for password in (None, WRONG):
            with self.subTest(str(password)), self.assertRaises(trial_store.SealError):
                with trial_store.opened(self.home, SUITE, RUN, password):
                    self.fail('It opened')

    def test_07_B13_a_trial_of_no_such_suite_or_name_raises(self):
        self.sweep()
        for suite, key in (('suite-other', RUN), (SUITE, 'no-such-run'), ('suite-other', self.trial)):
            with self.subTest(suite + str(key)), self.assertRaisesRegex(trial_store.SealError, 'No such trial'):
                with trial_store.opened(self.home, suite, key, PASSWORD):
                    self.fail('It opened')

    def test_07_B13_an_error_in_the_body_is_not_taken_for_a_seal_failure_and_the_folder_still_goes(self):
        self.sweep()
        with self.assertRaises(KeyError):
            with trial_store.opened(self.home, SUITE, RUN, PASSWORD) as root:
                raise KeyError('the reader')
        self.assertFalse(root.exists())


class RestoreTests(SweepFixture):
    def test_07_B14_a_restore_puts_every_member_back_at_its_original_path_with_its_mode(self):
        script = self.run_dir / 'handoffs/emit/package.bin'
        script.chmod(0o755)
        original = (self.trial / 'trial.json').read_bytes()
        self.sweep()
        trial_store.restore(self.trial, PASSWORD)
        self.assertEqual((self.trial / 'trial.json').read_bytes(), original)
        for relative, content in {**RUN_FILES, **KEPT_FILES}.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        for relative, content in TRIAL_FILES.items():
            self.assertEqual((self.trial / relative).read_bytes(), content)
        for relative, content in WORKSPACE_FILES.items():
            self.assertEqual((self.parent / relative).read_bytes(), content)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)

    def test_07_B14_a_file_a_worker_closed_comes_back_readable(self):
        secret = self.run_dir / 'benchmark/state.json'
        secret.chmod(0)
        self.sweep()
        trial_store.restore(self.trial, PASSWORD)
        self.assertEqual(secret.read_bytes(), RUN_FILES['benchmark/state.json'])

    def test_07_B14_a_repeat_changes_nothing_and_derives_no_key(self):
        self.sweep()
        trial_store.restore(self.trial, PASSWORD)
        (self.run_dir / 'benchmark/state.json').write_bytes(b'{"s": "since the restore"}')
        with patch.object(trial_store, '_open_file', side_effect=AssertionError('It unlocked something')):
            trial_store.restore(self.trial, PASSWORD)
        self.assertEqual((self.run_dir / 'benchmark/state.json').read_bytes(), b'{"s": "since the restore"}')

    def test_07_B14_killed_part_way_the_stub_stands_and_a_repeat_finishes(self):
        self.sweep()
        with crashing('_write_restored', after=3), self.assertRaises(Crash):
            trial_store.restore(self.trial, PASSWORD)
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        trial_store.restore(self.trial, PASSWORD)
        self.assertEqual(harness.read_trial(self.trial)['status'], 'evaluated')
        self.assertEqual((self.run_dir / 'benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])

    def test_07_B14_a_restore_that_fails_raises_naming_no_path(self):
        self.sweep()
        for label, damage in (('a part gone', lambda: (self.trial / 'sealed-1-1.enc').unlink()),
                              ('all moved', lambda: [path.unlink() for path in self.trial.glob('sealed-*.enc')])):
            with self.subTest(label):
                damage()
                with self.assertRaises(trial_store.SealError) as raised:
                    trial_store.restore(self.trial, PASSWORD)
                self.assertNotIn(str(self.root), str(raised.exception))
                self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')

    def test_07_B14_after_a_regrade_the_old_seal_stays_trial_json_is_a_further_generation_and_the_plaintext_goes(self):
        self.sweep()
        before = self.seal_files()
        trial_store.restore(self.trial, PASSWORD)
        record = json.loads((self.trial / 'trial.json').read_text())
        harness._private(self.trial / 'trial.json', {**record, 'status': 'evaluated', 'resumed': 9.5})
        write_files(self.trial, {'private/final/new.json': b'{"grading": 2}', 'run-evidence.enc': b'resealed'})
        self.sweep()
        self.assertEqual({name: self.seal_files()[name] for name in before}, before)
        self.assertEqual(self.generations(), [1, 2])
        self.assertEqual(list(self.members(2)), ['trial/trial.json'])
        self.assertEqual(json.loads(self.members(2)['trial/trial.json'])['resumed'], 9.5)
        self.assertAllSealed()
        self.assertEqual((self.trial / 'run-evidence.enc').read_bytes(), b'resealed')
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD) as root:
            self.assertEqual(json.loads((root / 'trial/trial.json').read_text())['resumed'], 9.5)


class StoreTests(SweepFixture):
    def setUp(self):
        super().setUp()
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.stored = self.vault / 'sealed-trials' / SUITE / self.trial.name

    def store(self, password=PASSWORD):
        trial_store.store(self.home, SUITE, self.vault, password, self.call)

    def test_07_B16_it_sweeps_then_moves_only_the_seals_and_stubs_and_other_enc_files_stay(self):
        self.store()
        self.assertEqual(sorted(path.name for path in self.stored.iterdir()), ['sealed-1-1.enc', 'sealed-1-index.enc'])
        self.assertEqual(list(self.trial.glob('sealed-*.enc')), [])
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        for name in ('run-evidence.enc', 'evaluator-draft.enc', 'evaluation-token.enc'):
            self.assertTrue((self.trial / name).exists(), name)
        self.assertPlaintextGone()
        self.assertEqual(self.call.sealed_marks(), [{'suite_id': SUITE, 'ordinal': 0, 'run_id': RUN}])

    def test_07_B16_read_back_finds_a_seal_in_either_place_given_the_vault_folder(self):
        self.sweep()
        (self.vault / 'sealed-trials' / SUITE / self.trial.name).mkdir(parents=True)
        shutil.move(str(self.trial / 'sealed-1-1.enc'), str(self.stored / 'sealed-1-1.enc'))
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD, vault=self.vault) as root:
            self.assertEqual((root / 'run/benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])
        self.store()
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD, vault=self.vault) as root:
            self.assertEqual((root / 'run/benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])
        with self.assertRaisesRegex(trial_store.SealError, 'vault folder'):
            with trial_store.opened(self.home, SUITE, RUN, PASSWORD):
                self.fail('It opened')

    def test_07_B16_it_refuses_a_suite_that_is_not_completed_or_cancelled_or_is_still_stopping(self):
        for status, stopping in (('running', False), ('queued', False), ('blocked', False), ('failed', False),
                                 ('completed', True), ('cancelled', True)):
            with self.subTest(status + str(stopping)):
                self.call.status = {'ok': True, 'status': status, 'stopping': stopping}
                with self.assertRaisesRegex(ValueError, 'suite store takes a completed suite'):
                    self.store()
                self.assertNothingDeleted()
                self.assertFalse(self.stored.exists())

    def test_07_B16_it_refuses_when_the_configurator_does_not_answer(self):
        self.call.refused['suite_status'] = 'No configurator'
        with self.assertRaises(trial_store.Unreachable):
            self.store()
        self.assertNothingDeleted()

    def test_07_B16_it_refuses_a_suite_that_is_not_scoring(self):
        del self.call.identity['scoring_set']
        with self.assertRaisesRegex(ValueError, 'Only a scoring suite'):
            self.store()
        self.assertNothingDeleted()

    def test_07_B16_it_refuses_a_vault_folder_that_is_not_there(self):
        self.vault.rmdir()
        with self.assertRaisesRegex(ValueError, 'vault folder') as raised:
            self.store()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertFalse(self.vault.exists())
        self.assertNothingDeleted()

    def test_07_B16_it_refuses_on_a_sweep_failure_and_moves_nothing(self):
        with self.assertRaises(trial_store.SealError):
            self.store(password=WRONG)
        self.assertFalse(self.stored.exists())
        self.assertNothingDeleted()

    def test_07_B16_a_cancelled_suite_seals_and_marks_every_readable_trial_even_an_unsettled_slots(self):
        self.call.status = {'ok': True, 'status': 'cancelled', 'stopping': False}
        self.call.rows = [self.row(status='cancelled'), self.row(1, 'pending', None), self.row(2, 'launching', None)]
        launching = self.make_trial(ordinal=2, key='entry-002-repeat-000-cccccccc', run_id=None)
        self.store()
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertEqual(harness.read_trial(launching)['status'], 'sealed')
        self.assertTrue((self.vault / 'sealed-trials' / SUITE / launching.name / 'sealed-1-index.enc').exists())
        self.assertEqual(self.call.sealed_marks(), [{'suite_id': SUITE, 'ordinal': 0, 'run_id': RUN}])
        self.assertPlaintextGone()

    def test_07_B16_a_completed_suite_leaves_an_unsettled_slot_alone_as_it_has_none(self):
        self.call.rows = [self.row(status='pending', run_id=None)]
        self.store()
        self.assertNothingDeleted()

    def test_07_B16_a_differing_vault_file_is_never_replaced_and_the_seal_stays_here(self):
        write_files(self.stored, {'sealed-1-1.enc': b'something else was here'})
        with self.assertRaisesRegex(trial_store.SealError, 'differs') as raised:
            self.store()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertEqual((self.stored / 'sealed-1-1.enc').read_bytes(), b'something else was here')
        self.assertTrue((self.trial / 'sealed-1-1.enc').exists())

    def test_07_B16_a_copy_that_will_not_unlock_keeps_the_original(self):
        self.sweep()
        with patch.object(trial_store, '_open_file', side_effect=ValueError('Cannot unlock')), \
                self.assertRaises(trial_store.SealError):
            self.store()
        self.assertTrue((self.trial / 'sealed-1-1.enc').exists())

    def test_07_B16_each_copy_is_verified_by_unlocking_it_before_the_original_goes(self):
        self.sweep()
        real, seen = trial_store._open_file, []

        def opening(password, path, expected=None):
            if str(path).startswith(str(self.vault)):
                seen.append((Path(path).name, (self.trial / Path(path).name).exists()))
            return real(password, path, expected)
        with patch.object(trial_store, '_open_file', opening):
            self.store()
        self.assertEqual(seen, [('sealed-1-1.enc', True), ('sealed-1-index.enc', True)])

    def test_07_B16_a_failure_part_way_leaves_every_seal_in_at_least_one_place_and_a_repeat_finishes(self):
        for step, attribute in (('before the delete', '_sync_file'), ('at the delete', '_remove_file')):
            with self.subTest(step):
                self.sweep()
                names = sorted(path.name for path in self.trial.glob('sealed-*.enc'))
                with crashing(attribute, after=2 if attribute == '_sync_file' else 0), self.assertRaises(Crash):
                    self.store()
                for name in names:
                    self.assertTrue((self.trial / name).exists() or (self.stored / name).exists(), name)
                self.store()
                self.assertEqual(list(self.trial.glob('sealed-*.enc')), [])
                self.assertEqual(sorted(path.name for path in self.stored.iterdir() if path.name in names), names)
                for path in list(self.stored.iterdir()):
                    shutil.move(str(path), str(self.trial / path.name))


class Driven(Configurator):
    """The configurator a suite's harness talks to: it answers `reserve` from a list, and says the suite is scoring."""

    def __init__(self, reservations=(), decision='advance'):
        super().__init__()
        self.reservations, self.decision = list(reservations), decision

    def __call__(self, method, params=None):
        replies = {'ping': {'ok': True},
                   'suite_resume': {'ok': True, 'suite_id': SUITE, 'status': 'running', 'attached': True,
                                    'scoring_set': 'set-v1'},
                   'suite_detach': {'ok': True},
                   'suite_settle': {'ok': True, 'decision': self.decision, 'status': 'running'}}
        if method in replies and method not in self.refused:
            self.calls.append((method, params))
            return replies[method]
        if method == 'suite_reserve' and method not in self.refused:
            self.calls.append((method, params))
            return self.reservations.pop(0) if self.reservations else {'ok': True, 'slot': None, 'status': 'completed'}
        return super().__call__(method, params)

    def methods(self):
        return [method for method, _ in self.calls]


class HarnessFixture(SweepFixture):
    def setUp(self):
        super().setUp()
        self.handle = self.root / 'password-handle'
        self.handle.write_text('the-trial-sealing-password')
        self.driven = Driven()
        self.driven.rows = [self.row(status='running')]
        self.driven.events = self.call.events
        self.call = self.driven

    def slot(self, ordinal=0, run_id=RUN):
        return {'ok': True, 'status': 'running', 'scoring': True, 'request': {},
                'slot': {'ordinal': ordinal, 'trial_key': 'entry-%03d-repeat-000' % ordinal, 'case': 'generated',
                         'child_request_id': 'suite:' + SUITE + ':trial:' + str(ordinal), 'run_id': run_id}}

    def resume(self, *, recorded=None, files=True, stop=None, **request):
        """One resumed harness, with `_suite_trial` returning `recorded`: its record."""
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        with patch.object(harness, '_suite_trial', return_value=recorded):
            return harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE,
                                              **({'password_files': {'set-v1': str(self.handle)}} if files else {}),
                                              **request}, call=self.driven, stop=stop)

    def assertSealed(self):
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        self.assertPlaintextGone()

    def assertUntouched(self):
        self.assertNothingDeleted()
        self.assertEqual(self.generations(), [])


class WhichDecisionsSealTests(HarnessFixture):
    def test_07_B1_a_settlement_of_advance_fault_flag_or_regrade_seals_before_the_next_reservation(self):
        self.driven.rows = []
        self.driven.reservations = [self.slot()]
        for decision in ('advance', 'fault', 'flag', 'regrade'):
            with self.subTest(decision):
                self.driven.decision = decision
                self.driven.calls.clear()
                self.driven.reservations = [self.slot()]
                self.fresh_trial()
                record = self.resume()
                self.assertEqual(record['status'], 'finished', record)
                self.assertSealed()
                methods = self.driven.methods()
                self.assertLess(methods.index('suite_settle'), methods.index('suite_sealed'))
                self.assertLess(methods.index('suite_sealed'), len(methods) - 1 - methods[::-1].index('suite_reserve'))

    def test_07_B1_a_slot_waiting_or_blocked_is_not_sealed(self):
        for decision in ('wait', 'block'):
            with self.subTest(decision):
                self.driven.decision = decision
                self.driven.reservations = [self.slot()]
                self.resume()
                self.assertUntouched()

    def test_07_B1_a_no_run_fault_or_advance_seals_its_folder_by_its_request(self):
        no_run = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id=None)
        for recorded in ('fault', 'advance'):
            with self.subTest(recorded):
                self.driven.rows = []
                self.driven.reservations = [self.slot(1, None)]
                self.resume(recorded=recorded)
                self.assertEqual(harness.read_trial(no_run)['status'], 'sealed')
                self.assertEqual(harness.read_trial(no_run)['run_id'], None)
                self.assertUntouched()

    def test_07_B1_a_slot_attached_or_unsettled_is_not_sealed(self):
        for recorded in ('attached', 'unsettled'):
            with self.subTest(recorded):
                self.driven.reservations = [self.slot()]
                self.resume(recorded=recorded)
                self.assertUntouched()
                self.assertNotIn('suite_settle', self.driven.methods())

    def test_07_B1_a_slot_with_no_folder_is_left_to_the_sweep_that_follows(self):
        shutil.rmtree(self.trial)
        self.driven.reservations = [self.slot()]
        self.driven.rows = [self.row(status='running')]
        self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id='run-2')

        def settled(method, params=None):
            if method == 'suite_settle':
                self.driven.rows[0]['status'] = 'finished'
            return self.driven(method, params)
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        with patch.object(harness, '_suite_trial', return_value=None):
            record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
                'set-v1': str(self.handle)}}, call=settled)
        self.assertEqual(record['status'], 'finished', record)
        made = trial_store.find_trial(self.home, SUITE, RUN)
        self.assertEqual(harness.read_trial(made)['status'], 'sealed')

    def test_07_B1_any_other_suite_is_never_swept_or_sealed(self):
        self.driven.reservations = [{**self.slot(), 'scoring': False}]

        def plain(method, params=None, real=self.driven.__call__):
            reply = real(method, params)
            return {key: value for key, value in reply.items() if key != 'scoring_set'}
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        del self.driven.identity['scoring_set']
        with patch.object(harness, '_suite_trial', return_value=None):
            record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE}, call=plain)
        self.assertEqual(record['status'], 'finished', record)
        self.assertUntouched()
        self.assertNotIn('suite_sealed', self.driven.methods())
        self.assertEqual(self.driven.methods(), ['ping', 'suite_result', 'suite_resume', 'suite_reserve',
                                                 'suite_settle', 'suite_reserve', 'suite_detach'])


class WhenTheHarnessSweepsTests(HarnessFixture):
    def test_07_B2_on_its_start_once_the_resume_answered_and_before_its_first_reserve_and_on_its_way_out(self):
        self.driven.rows = []
        self.resume()
        self.assertEqual(self.driven.methods(), ['ping', 'suite_result', 'suite_resume', 'suite_result',
                                                 'suite_reserve', 'suite_result', 'suite_detach'])

    def test_07_B2_before_every_reservation_so_no_worker_starts_beside_a_concluded_trial(self):
        self.driven.rows = [self.row()]
        started = []

        def trial(*_args, **_kwargs):
            started.append(harness.read_trial(self.trial)['status'])
        self.driven.reservations = [self.slot(1, None)]
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        with patch.object(harness, '_suite_trial', side_effect=trial):
            harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
                'set-v1': str(self.handle)}}, call=self.driven)
        self.assertEqual(started, ['sealed'])

    def test_07_B2_a_trial_crashed_between_settling_and_sealing_is_sealed_on_the_start(self):
        self.driven.rows = [self.row()]
        self.resume()
        self.assertSealed()
        self.assertEqual(self.driven.sealed_marks()[0]['token'] is not None, True)

    def test_07_B2_a_sweep_that_fails_on_the_start_stops_before_any_reservation(self):
        self.driven.rows = [self.row()]
        self.driven.refused['suite_sealed'] = 'Refused under ' + str(self.home)
        record = self.resume()
        self.assertEqual(record['status'], 'failed')
        self.assertNotIn('suite_reserve', self.driven.methods())
        self.assertNotIn(str(self.root), record['reason'])

    def test_07_B2_a_configurator_it_cannot_reach_on_the_start_stops_it(self):
        reads = []
        real = self.driven.__call__

        def losing(method, params=None):
            if method == 'suite_result':
                reads.append(method)
                if len(reads) == 2:
                    return {'ok': False, 'reason': 'Lost at ' + str(self.home)}
            return real(method, params)
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
            'set-v1': str(self.handle)}}, call=losing)
        self.assertEqual(record['status'], 'failed')
        self.assertNotIn('suite_reserve', self.driven.methods())
        self.assertNotIn(str(self.root), record['reason'])

    def test_07_B2_on_its_way_out_it_seals_what_concluded_when_it_was_told_to_stop(self):
        stop = threading.Event()
        self.driven.rows = [self.row(status='running')]
        real = self.driven.__call__

        def stopping(method, params=None):
            if method == 'suite_reserve':
                stop.set()
                self.driven.rows[0]['status'] = 'finished'
                return self.slot()
            return real(method, params)
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        with patch.object(harness, '_suite_trial', return_value='unsettled'):
            record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
                'set-v1': str(self.handle)}}, call=stopping, stop=stop)
        self.assertEqual(record['status'], 'finished', record)
        self.assertSealed()
        methods = self.driven.methods()
        self.assertLess(methods.index('suite_sealed'), methods.index('suite_detach'))

    def test_07_B2_a_failure_on_the_way_out_is_a_stop_named_to_the_configurator_with_no_path(self):
        stop = threading.Event()
        stop.set()
        self.driven.rows = [self.row()]
        self.driven.refused['suite_sealed'] = 'Refused under ' + str(self.home)
        record = self.resume(stop=stop)
        self.assertEqual(record['status'], 'failed')
        self.assertNotIn(str(self.root), record['reason'])
        detach = [params for method, params in self.driven.calls if method == 'suite_detach']
        self.assertIn('did not mark', detach[0]['reason'])
        self.assertNotIn(str(self.root), detach[0]['reason'])

    def test_07_B2_a_failure_on_the_way_out_does_not_hide_the_one_that_stopped_it(self):
        self.driven.rows = [self.row(status='running')]
        self.driven.reservations = [self.slot()]
        real = self.driven.__call__

        def failing(method, params=None):
            if method == 'suite_settle':
                self.driven.rows[0]['status'] = 'finished'
                self.driven.refused['suite_sealed'] = 'Refused'
                return {'ok': False, 'reason': 'The settlement was refused'}
            return real(method, params)
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        with patch.object(harness, '_suite_trial', return_value=None):
            record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
                'set-v1': str(self.handle)}}, call=failing)
        self.assertIn('The configurator refused the settlement', record['reason'])

    def test_07_B2_a_configurator_it_cannot_reach_makes_a_way_out_sweep_do_nothing(self):
        stop = threading.Event()
        stop.set()
        reads = []
        real = self.driven.__call__

        def losing(method, params=None):
            if method == 'suite_result':
                reads.append(method)
                if len(reads) == 2:
                    return {'ok': False, 'reason': 'Unreachable'}
            return real(method, params)
        self.driven.rows = [self.row()]
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
            'set-v1': str(self.handle)}}, call=losing, stop=stop)
        self.assertEqual(record['status'], 'finished', record)
        self.assertUntouched()
        self.assertEqual(self.driven.methods()[-1], 'suite_detach')

    def test_07_B2_a_harness_not_attached_to_the_suite_does_not_sweep(self):
        real = self.driven.__call__

        def duplicate(method, params=None):
            reply = real(method, params)
            return {**reply, 'duplicate': True, 'attached': False} if method == 'suite_resume' else reply
        self.driven.rows = [self.row()]
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
            'set-v1': str(self.handle)}}, call=duplicate)
        self.assertEqual(record['status'], 'finished', record)
        self.assertUntouched()

    def test_07_B4_a_seal_that_fails_after_a_settlement_stops_the_suite_with_a_reason_naming_no_path(self):
        self.driven.reservations = [self.slot()]
        self.driven.rows = []
        with patch.object(trial_store, '_seal_file', side_effect=OSError(28, 'Full', str(self.trial))):
            record = self.resume()
        self.assertEqual(record['status'], 'failed')
        self.assertNotIn(str(self.root), record['reason'])
        self.assertIn('Sealing a concluded trial failed: OSError', record['reason'])
        detach = next(params for method, params in self.driven.calls if method == 'suite_detach')
        self.assertEqual(detach['reason'], record['reason'])
        self.assertEqual(self.driven.methods().count('suite_reserve'), 1)
        self.assertUntouched()

    def test_07_B4_a_resume_seals_what_is_left_before_any_worker_starts(self):
        self.driven.reservations = [self.slot()]
        self.driven.rows = []
        with patch.object(trial_store, '_seal_file', side_effect=OSError(28, 'Full', str(self.trial))):
            self.resume()
        self.driven.calls.clear()
        self.driven.rows = [self.row()]
        started = []

        def trial(*_args, **_kwargs):
            started.append(harness.read_trial(self.trial)['status'])
        self.driven.reservations = [self.slot(1, None)]
        folder = trial_store.suite_dir(self.home, 'suite-run-again')
        with patch.object(harness, '_suite_trial', side_effect=trial):
            harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
                'set-v1': str(self.handle)}}, call=self.driven)
        self.assertEqual(started, ['sealed'])


class ResumeNeedsItsPasswordTests(HarnessFixture):
    SENTENCE = suites.NO_RESUME_PASSWORD + ': set-v1'

    def test_07_B5_the_harness_refuses_between_ping_and_the_resume(self):
        record = self.resume(files=False)
        self.assertEqual((record['status'], record['accepted'], record['reason']), ('failed', False, self.SENTENCE))
        self.assertEqual(self.driven.methods(), ['ping', 'suite_result'])

    def test_07_B5_a_password_file_of_another_name_is_no_password_file(self):
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
            'other-set': str(self.handle)}}, call=self.driven)
        self.assertEqual(record['reason'], self.SENTENCE)

    def test_07_B5_with_a_set_root_it_refuses_in_the_same_sentence_with_the_vault_still_open(self):
        self.driven.status = {'ok': True, 'status': 'blocked', 'stopping': False}
        with patch.object(harness, '_held_set', side_effect=AssertionError('The set was held')):
            record = self.resume(files=False, set_root=str(self.root / 'set'))
        self.assertEqual(record['reason'], self.SENTENCE)
        self.assertNotIn('suite_resume', self.driven.methods())

    def test_07_B5_a_suite_result_it_did_not_get_goes_on_and_the_attached_harness_still_stops_it(self):
        real = self.driven.__call__
        reads = []

        def losing(method, params=None):
            if method == 'suite_result' and not reads:
                reads.append(method)
                return {'ok': False, 'reason': 'Lost'}
            return real(method, params)
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        record = harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE}, call=losing)
        self.assertEqual((record['status'], record['reason']), ('failed', self.SENTENCE))
        self.assertIn('suite_resume', self.driven.methods())
        self.assertNotIn('suite_reserve', self.driven.methods())

    def resume_suite(self, identity, options=None):
        spawned = []

        def spawn(where, _arguments, payload):
            spawned.append(payload)
            (Path(where) / 'suite.json').write_text(json.dumps({'status': 'failed', 'accepted': False, 'finished': 1}))
            return SimpleNamespace(poll=lambda: 0)

        def call(method, params=None, home=None, **_options):
            status = {'ok': True, 'status': 'blocked', 'stopping': False, 'active_child_id': None}
            if identity is None:
                return {'ok': False, 'reason': 'No configurator'}
            return {'suite_result': {**status, 'experiment': {'comparison_identity': identity}},
                    'suite_status': status}.get(method, {'ok': False})
        with patch.object(harness, '_spawn', spawn), patch('generative_driver.client.call', call):
            suites.resume_suite(SUITE, options, home=self.home, autostart=False)
        return spawned

    def test_07_B5_resume_suite_refuses_when_the_configurator_answers_before_any_harness(self):
        with self.assertRaisesRegex(ValueError, '^' + self.SENTENCE + '$'):
            self.resume_suite(self.driven.identity)
        with self.assertRaisesRegex(ValueError, '^' + self.SENTENCE + '$'):
            self.resume_suite(self.driven.identity, {'evaluator_password_files': {'other': str(self.handle)}})

    def test_07_B5_resume_suite_goes_on_with_it_or_on_any_other_suite_or_with_no_answer(self):
        with_file = {'evaluator_password_files': {'set-v1': str(self.handle)}}
        self.assertEqual(len(self.resume_suite(self.driven.identity, with_file)), 1)
        self.assertEqual(len(self.resume_suite({'manifest': {'set': 'set-v1'}})), 1)
        self.assertEqual(len(self.resume_suite({'manifest': {}})), 1)
        self.assertEqual(len(self.resume_suite(None)), 1)


class BesideAReadableTrialTests(HarnessFixture):
    def start(self, *, scoring=True, gate=(True,)):
        self.driven.rows = []
        folder = trial_store.suite_dir(self.home, 'suite-run-new')
        real = self.driven.__call__

        def replying(method, params=None):
            if method == 'suite_start':
                self.driven.calls.append((method, params))
                return {'ok': True, 'suite_id': 'suite-new', 'duplicate': False, 'attached': True}
            if method == 'suite_reserve':
                self.driven.calls.append((method, params))
                return {'ok': True, 'slot': None, 'status': 'completed'}
            return real(method, params)
        with patch.object(harness, '_set_device_pins', return_value=(SimpleNamespace(registration='set-v1'), {})), \
                patch.object(generated_sets, 'is_scoring', return_value=scoring), \
                patch.object(generated_trials, 'set_unreachable', side_effect=gate):
            return harness.run_suite(folder, {'home': str(self.home), 'manifest': {'set': 'set-v1'},
                                              'password_files': {'set-v1': str(self.handle)}}, call=replying)

    def test_07_B5_a_new_scoring_suite_refuses_after_ping_and_before_the_gate(self):
        record = self.start(gate=AssertionError('The gate ran'))
        self.assertEqual((record['status'], record['accepted']), ('failed', False))
        self.assertNotIn('The gate ran', record['reason'])
        self.assertIn(SUITE, record['reason'])
        self.assertIn('set-v1', record['reason'])
        self.assertNotIn(str(self.root), record['reason'])
        self.assertEqual(self.driven.methods(), ['ping', 'suite_result'])

    def test_07_B5_any_folder_that_is_not_a_stub_counts_concluded_abandoned_or_active(self):
        for status in ('evaluated', 'running', 'admitting', 'failed'):
            with self.subTest(status):
                self.driven.calls.clear()
                record = json.loads((self.trial / 'trial.json').read_text())
                (self.trial / 'trial.json').write_text(json.dumps({**record, 'status': status}))
                self.assertEqual(self.start()['accepted'], False)

    def test_07_B5_an_unreadable_record_of_that_suites_harness_counts(self):
        (self.trial / 'trial.json').write_text('{not json')
        (self.trial.parent.parent / 'suite.json').write_text(json.dumps({'suite_id': SUITE}))
        self.assertEqual(self.start()['accepted'], False)
        self.assertEqual(self.driven.methods(), ['ping', 'suite_result'])

    def test_07_B5_a_folder_of_another_set_lets_it_start(self):
        self.driven.identity = {'scoring_set': {}, 'manifest': {'set': 'another-set'}}
        record = self.start()
        self.assertEqual(record['accepted'], True, record)
        self.assertEqual(self.driven.methods()[:3], ['ping', 'suite_result', 'suite_start'])

    def test_07_B5_a_suite_the_configurator_cannot_name_counts_as_the_same_set(self):
        self.driven.refused['suite_result'] = 'Unknown suite_id'
        self.assertEqual(self.start()['accepted'], False)

    def test_07_B5_with_only_stubs_there_is_no_suite_result_call(self):
        self.seal()
        self.driven.calls.clear()
        record = self.start()
        self.assertEqual(record['accepted'], True, record)
        self.assertEqual(self.driven.methods()[:2], ['ping', 'suite_start'])

    def test_07_B5_with_no_folder_there_is_no_suite_result_call(self):
        shutil.rmtree(self.trial)
        record = self.start()
        self.assertEqual(self.driven.methods()[:2], ['ping', 'suite_start'])
        self.assertEqual(record['accepted'], True, record)

    def test_07_B5_a_development_set_is_not_held_to_it(self):
        record = self.start(scoring=False)
        self.assertEqual(self.driven.methods()[:2], ['ping', 'suite_start'])
        self.assertEqual(record['accepted'], True, record)


class SealedRunMarkTests(unittest.TestCase):
    TOKEN = 'harness-token-text'

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve() / 'home'
        self.controller = Controller(self.home)
        self.addCleanup(self.controller.close)
        self.suite = self.frozen_suite(scoring=True)
        self.run = self.add_run('run-1', self.suite)

    def frozen_suite(self, *, scoring):
        frozen = {'manifest': {'suite_budget_seconds': 1000}, 'request': {'n': scoring}, 'scheduler': suites.SCHEDULER,
                  'harness_token_sha256': hashlib.sha256(self.TOKEN.encode()).hexdigest(), 'options': {},
                  'trials': [{'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': 'generated',
                              'scenario': 'original', 'case_seed': 0, 'repeat_index': 0, 'entry_index': 0}],
                  **({'scoring_set': {'registration': 'set-v1', 'manifest_sha256': 'a' * 64}} if scoring else {})}
        suite_id = self.controller._suites.create(frozen, harness_pid=os.getpid())['suite_id']
        self.controller._suites.set_state(suite_id, 'running')
        return suite_id

    def add_run(self, run_id, suite_id, status='failed'):
        spec = {'executor': 'codex', 'executor_config': {}, 'budget_seconds': 10, 'case': 'generated'}
        with self.controller._db() as db:
            db.execute('INSERT INTO runs(id,request_id,spec,status,created,updated) VALUES(?,?,?,?,1,2)',
                       (run_id, 'request-' + run_id, _json(spec), status))
        slot = self.controller._suites.reserve(suite_id)
        self.controller._suites.attach(suite_id, slot['ordinal'], slot['child_request_id'], run_id)
        return run_id

    def mark(self, **params):
        return self.controller.call('suite_sealed', {'suite_id': self.suite, 'ordinal': 0, 'run_id': self.run,
                                                     **params})

    def test_07_B12_a_marked_run_says_so_in_status_and_result_and_any_other_does_not(self):
        self.assertNotIn('sealed', self.controller.call('status', {'run_id': self.run}))
        self.assertNotIn('sealed', self.controller.call('result', {'run_id': self.run}))
        self.assertEqual(self.mark(token=self.TOKEN)['ok'], True)
        self.assertIs(self.controller.call('status', {'run_id': self.run})['sealed'], True)
        self.assertIs(self.controller.call('result', {'run_id': self.run})['sealed'], True)

    def test_07_B12_resume_and_attach_device_refuse_a_sealed_run_before_it_asks_the_device(self):
        self.mark(token=self.TOKEN)
        for method, params in (('resume', {}), ('attach_device', {'token': 'x', 'device': {}})):
            with self.subTest(method), \
                    patch.object(self.controller, '_resume_device', side_effect=AssertionError('It asked')), \
                    self.assertRaisesRegex(ValueError, '^A sealed run does not resume or take a device: run-1$'):
                self.controller.call(method, {'run_id': self.run, **params})

    def test_07_B12_a_run_not_marked_goes_on_to_what_it_did_before(self):
        with self.assertRaisesRegex(ValueError, 'attach_device|Only a run started'):
            self.controller.call('attach_device', {'run_id': self.run, 'token': 'x', 'device': {}})

    def test_07_B12_the_call_is_repeatable_since_the_delete_branch_sends_it_again(self):
        self.assertEqual(self.mark(token=self.TOKEN)['ok'], True)
        self.assertEqual(self.mark(token=self.TOKEN)['ok'], True)

    def test_07_B12_a_bad_token_while_a_harness_is_attached_is_refused(self):
        for token in ({'token': 'another'}, {}):
            with self.subTest(str(token)), self.assertRaisesRegex(ValueError, 'harness token'):
                self.mark(**token)
        self.assertNotIn('sealed', self.controller.call('status', {'run_id': self.run}))

    def test_07_B12_with_no_harness_attached_it_takes_no_token(self):
        self.controller._suites.detach_harness(self.suite)
        self.assertEqual(self.mark()['ok'], True)
        self.assertIs(self.controller.call('status', {'run_id': self.run})['sealed'], True)

    def test_07_B12_a_harness_that_died_attached_counts_as_none(self):
        with patch.object(Controller, '_harness_alive', staticmethod(lambda suite: False)):
            self.assertEqual(self.mark()['ok'], True)

    def test_07_B12_a_suite_that_is_not_scoring_is_refused(self):
        suite = self.frozen_suite(scoring=False)
        run = self.add_run('run-2', suite)
        with self.assertRaisesRegex(ValueError, 'Only a scoring suite'):
            self.controller.call('suite_sealed', {'suite_id': suite, 'ordinal': 0, 'run_id': run, 'token': self.TOKEN})
        self.assertNotIn('sealed', self.controller.call('status', {'run_id': run}))

    def test_07_B12_it_accepts_the_slots_run_or_its_first_attempts_and_no_other(self):
        first = self.run
        with self.controller._suites.open_db() as db:
            db.execute('UPDATE suite_trials SET first_attempt=?,run_id=NULL WHERE suite_id=?',
                       (_json({'run_id': first, 'request_id': 'r'}), self.suite))
        self.assertEqual(self.mark(token=self.TOKEN)['ok'], True)
        self.assertIs(self.controller.call('status', {'run_id': first})['sealed'], True)
        with self.assertRaisesRegex(ValueError, 'not a trial of this suite slot'):
            self.mark(run_id='run-other', token=self.TOKEN)
        with self.assertRaisesRegex(ValueError, 'not a trial of this suite slot'):
            self.mark(run_id=None, token=self.TOKEN)

    def test_07_B12_a_database_from_before_this_gains_the_mark(self):
        self.controller.close()
        with contextlib.closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db:
            db.execute('ALTER TABLE runs DROP COLUMN sealed')
            db.commit()
        again = Controller(self.home)
        self.addCleanup(again.close)
        self.assertNotIn('sealed', again.call('status', {'run_id': self.run}))


def scoring_report(**fields):
    """A `benchmark-suite-report/3` down to what a scorecard reads of a scoring suite's: one device, no run."""
    identity = {'scoring_set': {'registration': 'set-v1', 'manifest_sha256': 'a' * 64}, 'evaluator_revision': 'b' * 64,
                'execution': 'actual-agent-emulation', 'evidence_track': 'firmware', 'scope': 'full-workflow',
                'budgets': {'effective': {'child_budget_seconds': 1, 'suite_budget_seconds': 1,
                                          'child_budget_overrides': []}},
                'intervention_policy': {'scoped_tool_approval': 'emulator', 'max_model_repairs': 2}}
    return {'schema': 'benchmark-suite-report/3', 'suite_id': SUITE, 'protocol': 'assistive/1',
            'qualification': 'recorded-controller-verdicts',
            'experiment': {'comparison_identity': identity, 'dimensions': {}},
            'trials': [{'trial_key': 'entry-000-repeat-000', 'device_id': '0000d001', 'case': 'generated',
                        'scored': 'not_run', 'run_id': None, 'family': 'generated'}], **fields}


class ScoringReadRefusalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.vault = self.root / 'vault'
        self.set_root = self.vault / 'set'
        self.set_root.mkdir(parents=True)
        self.status = {'ok': True, 'status': 'completed', 'stopping': False}
        self.calls = []

    def call(self, method, params=None, home=None, **_options):
        self.calls.append(method)
        if method == 'suite_status':
            return self.status
        return {'ok': True, 'suite_id': params['suite_id'], 'status': 'completed', 'trials': [], 'next_offset': None,
                'experiment': {'comparison_identity': {'scoring_set': {'registration': 'set-v1'}}}}

    def report(self, **changes):
        arguments = {'output': self.vault / 'report', 'password': PASSWORD, 'set_root': self.set_root,
                     'vault': self.vault, **changes}
        with patch('generative_driver.client.call', self.call):
            return suite_reporting.report_suite(SUITE, home=self.root / 'home', **arguments)

    def assertRefused(self, pattern, **changes):
        with self.assertRaisesRegex(ValueError, pattern) as raised:
            self.report(**changes)
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertFalse((self.vault / 'report').exists())
        self.assertFalse((self.root / 'elsewhere').exists())

    def test_07_B18_a_scoring_report_needs_the_password_file_the_set_root_and_a_vault_folder(self):
        for missing in ('password', 'set_root', 'vault'):
            with self.subTest(missing):
                self.assertRefused('needs its set.s password file, its set root and a vault folder', **{missing: None})

    def test_07_B18_it_refuses_unless_the_set_root_and_the_output_lie_under_the_vault_folder(self):
        self.assertRefused('set root under the vault folder', set_root=self.root / 'set')
        self.assertRefused('written only under the vault folder', output=self.root / 'elsewhere')

    def test_07_B18_a_link_out_of_the_vault_folder_does_not_count_as_under_it(self):
        (self.vault / 'link').symlink_to(self.root, target_is_directory=True)
        self.assertRefused('written only under the vault folder', output=self.vault / 'link/elsewhere')

    def test_07_B17_B18_it_refuses_while_the_suite_is_queued_running_or_stopping(self):
        for status, stopping in (('queued', False), ('running', False), ('completed', True), ('blocked', True)):
            with self.subTest(status + str(stopping)):
                self.status = {'ok': True, 'status': status, 'stopping': stopping}
                self.assertRefused('measured only once nothing of it runs')

    def test_07_B17_a_status_it_could_not_read_is_refused_too(self):
        self.status = {'ok': False, 'reason': 'No configurator'}
        self.assertRefused('did not report the suite')

    def test_07_B18_one_normalised_output_goes_to_the_check_and_to_the_export(self):
        written = []

        def export(suite_id, *, home, output, check, **_options):
            check()
            written.append(output)
        with patch.dict(os.environ, {'HOME': str(self.root), 'USERPROFILE': str(self.root)}), \
                patch.object(suite_reporting, '_export_suite', export), \
                patch('generative_driver.client.call', self.call):
            suite_reporting.report_suite(SUITE, home=self.root / 'home', output='~/vault/report', password=PASSWORD,
                                         set_root=self.set_root, vault='~/vault')
        self.assertEqual(written, [self.vault / 'report'])

    def test_07_B13_B17_a_scoring_scorecard_with_no_password_or_set_root_raises_rather_than_give_none_figures(self):
        for missing in ({'password': None, 'set_root': 'x'}, {'password': PASSWORD, 'set_root': None}):
            with self.subTest(str(missing)), self.assertRaisesRegex(ValueError, 'needs its set.s password file'):
                scorecard.suite_scorecard(scoring_report(), home=self.root / 'home', **missing)

    def test_07_B17_a_scoring_scorecard_refuses_a_live_suite(self):
        self.status = {'ok': True, 'status': 'running', 'stopping': False}
        with patch('generative_driver.client.call', self.call), \
                self.assertRaisesRegex(ValueError, 'measured only once nothing of it runs'):
            scorecard.suite_scorecard(scoring_report(), home=self.root / 'home', password=PASSWORD, set_root='x')

    def test_07_B17_it_joins_level_and_framing_in_memory_and_saves_neither(self):
        manifest = {'manifest_sha256': 'a' * 64, 'devices': [{'id': '0000d001', 'level': 3, 'framing': 'text-lines'}]}
        report = scoring_report()
        with patch('generative_driver.client.call', self.call), \
                patch.object(generated_sets, 'load_set', return_value=manifest):
            card = scorecard.suite_scorecard(report, home=self.root / 'home', password=PASSWORD, set_root='x')
        self.assertEqual(sorted(card['groups']), ['family', 'framing', 'level'])
        self.assertEqual(list(card['groups']['level']), ['3'])
        self.assertEqual(list(card['groups']['framing']), ['text-lines'])
        self.assertNotIn('level', report['trials'][0])
        self.assertNotIn('0000d001', json.dumps(card))
        self.assertNotIn('trials', card)

    def test_07_B17_a_set_root_holding_another_manifest_is_refused(self):
        manifest = {'manifest_sha256': 'c' * 64, 'devices': []}
        with patch('generative_driver.client.call', self.call), \
                patch.object(generated_sets, 'load_set', return_value=manifest), \
                self.assertRaisesRegex(ValueError, 'another manifest than the suite froze'):
            scorecard.suite_scorecard(scoring_report(), home=self.root / 'home', password=PASSWORD, set_root='x')

    def test_07_B13_the_audit_of_a_scoring_suite_with_no_password_raises(self):
        with self.assertRaisesRegex(ValueError, 'audit needs its set.s password file'):
            suite_reporting._audited(self.root, SUITE, RUN, ('/vault',), None, None)


class UnsealCommandTests(SweepFixture):
    def main(self, *arguments):
        output = io.StringIO()
        handle = self.root / 'password-handle'
        handle.write_text('the-trial-sealing-password')
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.home)}), contextlib.redirect_stdout(output):
            code = benchmark.main(['suite', *arguments, '--password-file', str(handle)])
        return code, json.loads(output.getvalue())

    def test_07_B15_it_writes_one_sealed_trial_under_the_vault_folder(self):
        vault = self.root / 'vault'
        vault.mkdir()
        self.sweep()
        code, printed = self.main('unseal', SUITE, '--trial', RUN, '--vault-folder', str(vault), '--output',
                                  str(vault / 'checking'))
        self.assertEqual((code, printed['ok']), (0, True), printed)
        self.assertEqual((vault / 'checking/run/work/probe/a1/_agent_events.jsonl').read_bytes(),
                         RUN_FILES['work/probe/a1/_agent_events.jsonl'])
        self.assertEqual(json.loads((vault / 'checking/trial/trial.json').read_text())['level'], 3)
        self.assertPlaintextGone()

    def test_07_B15_it_finds_a_trial_by_its_trial_id_too(self):
        vault = self.root / 'vault'
        vault.mkdir()
        self.sweep()
        code, printed = self.main('unseal', SUITE, '--trial', self.trial.name, '--vault-folder', str(vault), '--output',
                                  str(vault / 'by-id'))
        self.assertEqual(code, 0, printed)
        self.assertTrue((vault / 'by-id/run/benchmark/state.json').exists())

    def test_07_B15_it_refuses_any_other_location(self):
        vault = self.root / 'vault'
        vault.mkdir()
        write_files(vault, {'full/already.txt': b'here'})
        self.sweep()
        for label, folder, output, reason in (
                ('outside', vault, self.root / 'elsewhere', 'only under the given vault folder'),
                ('the folder itself', vault, vault, 'only under the given vault folder'),
                ('no such vault', self.root / 'closed', self.root / 'closed/inside',
                 'only under the given vault folder'),
                ('not empty', vault, vault / 'full', 'new or empty folder')):
            with self.subTest(label):
                code, printed = self.main('unseal', SUITE, '--trial', RUN, '--vault-folder', str(folder), '--output',
                                          str(output))
                self.assertEqual(code, 1)
                self.assertIn(reason, printed['error'])
                self.assertFalse((self.root / 'elsewhere').exists())
                self.assertFalse((self.root / 'closed').exists())

    def test_07_B15_it_needs_the_password_file(self):
        vault = self.root / 'vault'
        vault.mkdir()
        output = io.StringIO()
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.home)}), contextlib.redirect_stderr(output), \
                self.assertRaises(SystemExit):
            benchmark.main(['suite', 'unseal', SUITE, '--trial', RUN, '--vault-folder', str(vault), '--output',
                            str(vault / 'x')])
        self.assertIn('--password-file', output.getvalue())


class SmallPathsTests(SweepFixture):
    def test_07_B8_a_file_system_that_refuses_a_full_sync_gets_the_plain_one(self):
        fcntl = fcntl_module()
        path = self.root / 'a-file'
        path.write_bytes(b'x')
        with patch.object(fcntl, 'fcntl', side_effect=OSError(45, 'Not supported')), \
                patch('os.fsync', wraps=os.fsync) as plain:
            trial_store._sync_file(path)
        self.assertEqual(plain.call_count, 2)

    def test_07_B4_a_reply_that_is_no_object_is_a_refusal(self):
        def call(method, params=None):
            return None if method == 'suite_sealed' else self.call(method, params)
        with self.assertRaisesRegex(trial_store.SealError, 'did not mark'):
            trial_store.sweep(self.home, SUITE, PASSWORD, call, 'harness-token')

    def test_07_B9_a_file_or_folder_gone_by_its_turn_is_no_failure(self):
        trial_store._remove_file(self.root / 'gone')
        trial_store._remove_folder(self.root / 'gone')

    def test_07_B13_B14_a_member_that_differs_from_its_index_is_refused(self):
        def forged(content, listed):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w:gz') as tar:
                info = tarfile.TarInfo('trial/trial.json')
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            part = PASSWORD.seal({'data': base64.b64encode(data.getvalue()).decode()}, self.trial / 'sealed-1-1.enc')
            members = {'trial/trial.json': {'kind': 'file', 'path': str(self.trial / 'trial.json'), 'mode': 0o600,
                                            'sha256': listed}}
            PASSWORD.seal({'generation': 1, 'members': members, 'skipped': [],
                           'parts': [{'name': 'sealed-1-1.enc', 'sha256': part}]}, self.trial / 'sealed-1-index.enc')
        record = {**json.loads((self.trial / 'trial.json').read_text()), 'status': 'sealed', 'sealed_files': []}
        (self.trial / 'trial.json').write_text(json.dumps(record))
        forged(b'{"forged": true}', '0' * 64)
        with self.assertRaisesRegex(trial_store.SealError, 'differs from its index'):
            with trial_store.opened(self.home, SUITE, self.trial, PASSWORD):
                self.fail('It opened')
        with self.assertRaisesRegex(trial_store.SealError, 'differs from its index'):
            trial_store.restore(self.trial, PASSWORD)
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')

    def test_07_B1_B16_another_suites_trial_is_neither_found_nor_stored(self):
        other = self.trial.parent / 'other-suite-trial'
        write_files(other, {'sealed-1-1.enc': b'another suites seal'})
        (other / 'trial.json').write_text(json.dumps({'request_id': 'suite:suite-other:trial:0', 'run_id': 'run-other',
                                                      'status': 'sealed', 'trial_id': 'other-suite-trial'}))
        vault = self.root / 'vault'
        vault.mkdir()
        self.assertEqual(trial_store.find_trial(self.home, SUITE, 'run-other'), None)
        self.assertEqual(trial_store.trial_dirs(self.home, SUITE), [self.trial])
        self.assertEqual(trial_store.find_trial(self.home, 'suite-other', 'run-other'), other)
        trial_store.store(self.home, SUITE, vault, PASSWORD, self.call)
        self.assertEqual((other / 'sealed-1-1.enc').read_bytes(), b'another suites seal')
        self.assertFalse((vault / 'sealed-trials' / 'suite-other').exists())


def fcntl_module():
    try:
        import fcntl
    except ImportError:
        raise unittest.SkipTest('This system has no fcntl') from None
    return fcntl


class PasswordRuleTests(SweepFixture):
    """07-B6 as ruled: a proof at the top of a sweep or `store`, a trial's own token, and a stop with nothing
    deleted."""

    def neighbour(self, key='entry-001-repeat-000-bbbbbbbb', ordinal=1, damaged=False):
        """Another trial of the suite, with its own token, that no row of the test concludes."""
        trial = self.make_trial(ordinal=ordinal, key=key, run_id='run-' + key[-8:])
        if damaged:
            (trial / trial_store.TOKEN).write_bytes(b'not what was sealed')
        return trial

    def assertRunKept(self):
        for relative, content in RUN_FILES.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        for relative, content in WORKSPACE_FILES.items():
            self.assertEqual((self.parent / relative).read_bytes(), content)

    def test_07_B6_a_trial_with_a_run_and_a_start_and_no_token_stops_with_nothing_deleted(self):
        (self.trial / trial_store.TOKEN).unlink()
        with self.assertRaisesRegex(trial_store.SealError, 'evaluation token is missing') as raised:
            self.seal()
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNothingDeleted()
        self.assertTrue((self.trial / 'private/final/secret.json').exists())
        self.assertEqual(self.generations(), [])
        self.assertEqual(self.call.sealed_marks(), [])

    def test_07_B6_it_is_the_same_from_a_sweep_even_with_the_set_held(self):
        self.neighbour()
        (self.trial / trial_store.TOKEN).unlink()
        for options in ({}, {'proven': True}):
            with self.subTest(str(options)), \
                    self.assertRaisesRegex(trial_store.SealError, 'evaluation token is missing'):
                self.sweep(**options)
            self.assertNothingDeleted()

    def test_07_B6_has_a_run_comes_from_the_run_folder_as_an_adopted_silent_folder_names_none_on_disk(self):
        record = json.loads((self.trial / 'trial.json').read_text())
        (self.trial / 'trial.json').write_text(json.dumps({**record, 'run_id': None}))
        self.neighbour()
        (self.trial / trial_store.TOKEN).unlink()
        with self.assertRaisesRegex(trial_store.SealError, 'evaluation token is missing'):
            self.sweep()
        self.assertNothingDeleted()

    def test_07_B6_every_pass_that_would_delete_proves_again_after_a_crash(self):
        with crashing('_delete_plaintext'), self.assertRaises(Crash):
            self.seal()
        (self.trial / trial_store.TOKEN).unlink()
        with self.assertRaisesRegex(trial_store.SealError, 'evaluation token is missing'):
            self.seal()
        self.assertRunKept()

    def test_07_B6_a_stub_with_nothing_left_never_stops_on_a_missing_token(self):
        self.seal()
        (self.trial / trial_store.TOKEN).unlink()
        stub = self.seal()
        self.assertEqual(stub['status'], 'sealed')
        self.sweep()

    def test_07_B6_killed_after_making_a_folder_from_a_row_the_next_sweep_still_needs_its_proof(self):
        self.neighbour()
        shutil.rmtree(self.trial)
        with crashing('seal_trial'), self.assertRaises(Crash):
            self.sweep()
        made = trial_store.find_trial(self.home, SUITE, RUN)
        self.assertEqual(harness.read_trial(made)['start_sent'], None)
        with self.assertRaisesRegex(trial_store.SealError, 'password'):
            self.sweep(password=WRONG)
        self.assertRunKept()
        self.assertEqual(harness.read_trial(made)['status'], 'made-from-row')
        self.sweep()
        self.assertEqual(harness.read_trial(made)['status'], 'sealed')

    def test_07_B6_a_folder_made_from_a_row_with_nothing_to_prove_on_stops_with_nothing_deleted_or_made(self):
        shutil.rmtree(self.trial)
        for password in (PASSWORD, WRONG):
            with self.subTest(password is PASSWORD), \
                    self.assertRaisesRegex(trial_store.SealError, 'prove the password'):
                self.sweep(password=password)
            self.assertRunKept()
            self.assertEqual(trial_store.trial_dirs(self.home, SUITE), [])

    def test_07_B6_killed_after_making_it_with_nothing_to_prove_on_it_stops_too(self):
        shutil.rmtree(self.trial)
        with crashing('seal_trial'), self.assertRaises(Crash):
            self.sweep(proven=True)
        with self.assertRaisesRegex(trial_store.SealError, 'prove the password'):
            self.sweep()
        self.assertRunKept()

    def test_07_B6_a_harness_holding_the_set_has_proved_it(self):
        shutil.rmtree(self.trial)
        with patch.object(trial_store, '_open_file', wraps=trial_store._open_file) as unlocking:
            self.sweep(proven=True)
        made = trial_store.find_trial(self.home, SUITE, RUN)
        self.assertEqual(harness.read_trial(made)['status'], 'sealed')
        self.assertNotIn(trial_store.TOKEN, [Path(call.args[1]).name for call in unlocking.call_args_list])

    def test_07_B6_a_damaged_token_does_not_stop_the_right_password(self):
        self.neighbour(key='entry-001-repeat-000-00000000', damaged=True)
        self.neighbour(key='entry-002-repeat-000-11111111', ordinal=2)
        shutil.rmtree(self.trial)
        self.sweep()
        self.assertEqual(harness.read_trial(trial_store.find_trial(self.home, SUITE, RUN))['status'], 'sealed')

    def test_07_B6_a_token_that_cannot_be_read_does_not_stop_it_either(self):
        damaged = self.neighbour(key='entry-001-repeat-000-00000000')
        self.neighbour(key='entry-002-repeat-000-11111111', ordinal=2)
        shutil.rmtree(self.trial)
        real = trial_store._open_file

        def unreadable(password, path, expected=None):
            if Path(path).parent == damaged:
                raise OSError(5, 'Input/output error', str(path))
            return real(password, path, expected)
        with patch.object(trial_store, '_open_file', unreadable):
            self.sweep()
        self.assertEqual(harness.read_trial(trial_store.find_trial(self.home, SUITE, RUN))['status'], 'sealed')

    def test_07_B6_tokens_none_of_which_unlock_stop_it_before_anything_is_sealed(self):
        no_run = self.make_trial(ordinal=1, key='entry-000-repeat-000-0000none', run_id=None, token=False)
        self.call.rows = [self.row(1, 'finished', None), self.row()]
        with self.assertRaisesRegex(trial_store.SealError, 'unlocks no evaluation token') as raised:
            self.sweep(password=WRONG)
        self.assertNotIn(str(self.root), str(raised.exception))
        self.assertNothingDeleted()
        self.assertEqual(harness.read_trial(no_run)['status'], 'evaluated')
        self.assertTrue((no_run / 'device/journal.jsonl').exists())
        self.assertEqual(self.generations(no_run), [])

    def test_07_B6_a_no_run_trial_in_a_suite_with_no_token_at_all_is_sealed_without_proof(self):
        (self.trial / trial_store.TOKEN).unlink()
        no_run = self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id=None, token=False)
        self.call.rows = [self.row(1, 'finished', None)]
        self.sweep(password=WRONG)
        self.assertEqual(harness.read_trial(no_run)['status'], 'sealed')

    def test_07_B6_with_nothing_to_seal_no_key_is_derived_whatever_the_password(self):
        self.sweep()
        with patch.object(trial_store, '_open_file', side_effect=AssertionError('It unlocked something')):
            self.sweep(password=WRONG)

    def test_07_B6_B16_store_under_a_wrong_password_seals_and_deletes_nothing(self):
        no_run = self.make_trial(ordinal=1, key='entry-000-repeat-000-0000none', run_id=None, token=False)
        self.call.rows = [self.row(1, 'finished', None), self.row()]
        vault = self.root / 'vault'
        vault.mkdir()
        with self.assertRaisesRegex(trial_store.SealError, 'unlocks no evaluation token'):
            trial_store.store(self.home, SUITE, vault, WRONG, self.call)
        self.assertNothingDeleted()
        self.assertEqual(harness.read_trial(no_run)['status'], 'evaluated')
        self.assertTrue((no_run / 'device/journal.jsonl').exists())
        self.assertEqual(list(vault.iterdir()), [])
        self.assertEqual(self.call.sealed_marks(), [])

    def test_07_B6_B16_a_suite_with_nothing_to_prove_on_cannot_be_stored(self):
        shutil.rmtree(self.trial)
        vault = self.root / 'vault'
        vault.mkdir()
        with self.assertRaisesRegex(trial_store.SealError, 'prove the password'):
            trial_store.store(self.home, SUITE, vault, PASSWORD, self.call)
        self.assertRunKept()
        self.assertEqual(list(vault.iterdir()), [])


class HarnessProofTests(HarnessFixture):
    def sweeps(self, **request):
        """The `proven` of each sweep one resumed harness makes."""
        with patch.object(trial_store, 'sweep') as sweeping:
            self.resume(**request)
        return [call.kwargs['proven'] for call in sweeping.call_args_list]

    def test_07_B6_both_sweep_sites_say_nothing_is_proven_with_no_set_held(self):
        self.driven.reservations = [self.slot()]
        self.driven.decision = 'block'
        self.assertEqual(self.sweeps(), [False, False])

    def test_07_B6_both_sweep_sites_say_it_is_proven_by_a_held_set(self):
        self.driven.reservations = [self.slot()]
        self.driven.decision = 'block'
        held = SimpleNamespace(registration='set-v1')
        with patch.object(harness, '_held_for_resume', return_value=held), \
                patch.object(generated_trials, 'set_unreachable', return_value=True):
            self.assertEqual(self.sweeps(set_root=str(self.root / 'set')), [True, True])

    def test_07_B6_a_harness_holding_the_set_seals_a_run_that_has_lost_its_folder(self):
        shutil.rmtree(self.trial)
        self.driven.rows = [self.row()]
        held = SimpleNamespace(registration='set-v1')
        with patch.object(harness, '_held_for_resume', return_value=held), \
                patch.object(generated_trials, 'set_unreachable', return_value=True):
            record = self.resume(set_root=str(self.root / 'set'))
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(harness.read_trial(trial_store.find_trial(self.home, SUITE, RUN))['status'], 'sealed')

    def test_07_B6_a_harness_holding_nothing_stops_at_it_and_starts_no_worker(self):
        shutil.rmtree(self.trial)
        self.driven.rows = [self.row()]
        record = self.resume()
        self.assertEqual(record['status'], 'failed')
        self.assertIn('prove the password', record['reason'])
        self.assertNotIn(str(self.root), record['reason'])
        self.assertNotIn('suite_reserve', self.driven.methods())
        self.assertEqual((self.run_dir / 'benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])


class SettledFolderWithNoStartTests(HarnessFixture):
    """A settling slot whose folder has a run and no start, as one made from a row has: no token of its own, so the
    sweep that follows must prove the password or stop, and the sweep before it saw an active slot and proved
    nothing."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.trial)
        self.trial = self.make_trial(token=False, start_sent=None)

    def settled(self, *, set_root=None):
        """One resumed harness over that slot, which concludes when it settles: its record."""
        self.driven.reservations = [self.slot()]
        self.driven.rows = [self.row(status='running')]

        def concluding(method, params=None):
            if method == 'suite_settle':
                self.driven.rows[0]['status'] = 'finished'
            return self.driven(method, params)
        folder = trial_store.suite_dir(self.home, 'suite-run-resumed')
        with patch.object(harness, '_suite_trial', return_value=None):
            return harness.run_suite(folder, {'home': str(self.home), 'suite_id': SUITE, 'password_files': {
                'set-v1': str(self.handle)}, **({'set_root': set_root} if set_root else {})}, call=concluding)

    def assertKept(self):
        for relative, content in RUN_FILES.items():
            self.assertEqual((self.run_dir / relative).read_bytes(), content)
        self.assertEqual(self.generations(), [])
        self.assertEqual(harness.read_trial(self.trial)['status'], 'evaluated')
        self.assertNotIn('suite_sealed', self.driven.methods())

    def test_07_B6_a_settled_folder_with_a_run_and_no_start_under_a_wrong_password_stops_with_nothing_deleted(self):
        self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id='run-2')
        self.handle.write_text('another-password')
        record = self.settled()
        self.assertEqual(record['status'], 'failed', record)
        self.assertEqual(record['reason'], 'The password unlocks no evaluation token of this suite')
        self.assertKept()

    def test_07_B6_with_nothing_to_prove_on_it_stops_too(self):
        record = self.settled()
        self.assertEqual(record['status'], 'failed', record)
        self.assertIn('prove the password', record['reason'])
        self.assertKept()

    def test_07_B6_the_right_password_seals_it_in_the_sweep_that_follows(self):
        self.make_trial(ordinal=1, key='entry-001-repeat-000-bbbbbbbb', run_id='run-2')
        record = self.settled()
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')
        methods = self.driven.methods()
        settled = methods.index('suite_settle')
        self.assertEqual(methods[settled + 1:settled + 4], ['suite_result', 'events', 'suite_sealed'])

    def test_07_B6_a_harness_holding_the_set_seals_it_there_with_nothing_else_to_prove_on(self):
        held = SimpleNamespace(registration='set-v1')
        with patch.object(harness, '_held_for_resume', return_value=held), \
                patch.object(generated_trials, 'set_unreachable', return_value=True):
            record = self.settled(set_root=str(self.root / 'set'))
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(harness.read_trial(self.trial)['status'], 'sealed')


class OnlyThisSuitesRunsTests(SweepFixture):
    OTHER = 'run-other'

    def setUp(self):
        super().setUp()
        write_files(self.home / 'runs' / self.OTHER, {'work/secret.txt': b'another suites plaintext'})
        with contextlib.closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, request_id TEXT)')
            db.executemany('INSERT OR REPLACE INTO runs VALUES(?,?)', [
                (RUN, 'suite:' + SUITE + ':trial:0'), (self.OTHER, 'suite:suite-other:trial:0'),
                ('run-solo', 'trial-request')])
            db.commit()

    def assertRefused(self, suite, key, password=PASSWORD):
        with self.assertRaisesRegex(trial_store.SealError, 'No such trial') as raised:
            with trial_store.opened(self.home, suite, key, password):
                self.fail('It opened')
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_07_B13_a_name_that_is_no_run_of_this_suite_is_refused_though_it_has_plaintext(self):
        self.assertRefused(SUITE, self.OTHER)
        write_files(self.home / 'runs/run-solo', {'work/secret.txt': b'plaintext'})
        self.assertRefused(SUITE, 'run-solo')
        write_files(self.home / 'runs/run-unknown', {'work/secret.txt': b'plaintext'})
        self.assertRefused(SUITE, 'run-unknown')

    def test_07_B13_a_name_that_is_not_plain_is_refused(self):
        write_files(self.root / 'outside', {'work/secret.txt': b'plaintext'})
        names = ('../../outside', '..', '.', '', 'a/b', str(self.root / 'outside'))
        for name in names:
            with self.subTest(name):
                self.assertRefused(SUITE, name)
        # The database is readable and writable by the same user, so a row saying so does not make it plain.
        with contextlib.closing(sqlite3.connect(self.home / 'runs.sqlite3')) as db:
            db.executemany('INSERT INTO runs VALUES(?,?)', [(name, 'suite:' + SUITE + ':trial:0') for name in names])
            db.commit()
        for name in names:
            with self.subTest(name + ' with a row'):
                self.assertRefused(SUITE, name)

    def test_07_B13_a_run_of_this_suite_with_no_folder_still_opens(self):
        shutil.rmtree(self.trial)
        with trial_store.opened(self.home, SUITE, RUN, PASSWORD) as root:
            self.assertEqual((root / 'run/benchmark/state.json').read_bytes(), RUN_FILES['benchmark/state.json'])

    def test_07_B13_with_no_database_nothing_says_it_is_this_suites(self):
        shutil.rmtree(self.trial)
        (self.home / 'runs.sqlite3').unlink()
        self.assertRefused(SUITE, RUN)

    def test_07_B13_plaintext_needs_the_password_too(self):
        with self.assertRaisesRegex(trial_store.SealError, 'password file'):
            with trial_store.opened(self.home, SUITE, RUN, None):
                self.fail('It opened')

    def test_07_B15_unseal_writes_nothing_of_another_suites_run(self):
        vault = self.root / 'vault'
        vault.mkdir()
        handle = self.root / 'password-handle'
        handle.write_text('the-trial-sealing-password')
        output = io.StringIO()
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.home)}), contextlib.redirect_stdout(output):
            code = benchmark.main(['suite', 'unseal', SUITE, '--trial', self.OTHER, '--password-file', str(handle),
                                   '--vault-folder', str(vault), '--output', str(vault / 'checking')])
        self.assertEqual(code, 1, output.getvalue())
        self.assertIn('No such trial', json.loads(output.getvalue())['error'])
        self.assertFalse((vault / 'checking').exists())


class OneTokenNameTests(unittest.TestCase):
    def test_07_B6_the_harness_uses_the_one_token_name(self):
        self.assertIs(harness.TOKEN, trial_store.TOKEN)


if __name__ == '__main__':
    unittest.main()

"""A generated set qualified once: both reference drivers on every device, the mutants on a sample of two devices per
level and framing, and the record sealed beside the set's manifest; then the admission a trial on one device needs from
that record. Every run emits a package with the reference runner and grades it with the evaluator, each on a fresh
hand-written fixture device (`generated_fixtures`), never generator output, with NativeSession.start patched."""
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generative_driver
from generated_fixtures import (FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, PASSWORD, PASSWORD_TEXT, REFERENCE_A, FixtureDevice,
                                fixture_mission, fixture_start, registrations_table, write_fixture_set)
from generative_driver.benchmark import main
from generative_driver.benchmark_support import generated_sets, reference_calibration, set_qualification, truth
from generative_driver.benchmark_support.grader_identity import grader_identity
from generative_driver.benchmark_support.generated_sets import qualification_reasons
from generative_driver.benchmark_support.set_qualification import qualify, sample
from generative_driver.benchmark_support.truth import EvaluatorPassword, read_password

SET = 'fixture-v1'
START = 'generative_driver.benchmark_support.native.NativeSession.start'
MUTANTS = [mutant['id'] for mutant in FIXTURE_ANSWER_KEY['mutants']]
BOOT_PC = 0x08000100  # what a fixture device's monitor answers to `cpu PC`


def write_set(root, keys):
    """Seal one fixture device per entry of `keys` ({device id: answer key}), each at level 1 with text lines, under
    `root`, and write the manifest of set fixture-v1 beside them: the registrations that pin it."""
    devices = [generated_sets.seal_device(root, {'id': device_id, 'level': 1, 'framing': 'text-lines'},
                                          image=FIXTURE_IMAGE, mission=fixture_mission(device_id), answer_key=key,
                                          password=PASSWORD)
               for device_id, key in keys.items()]
    return {SET: generated_sets.write_manifest(root, SET, devices)}


def run_qualify(root, registrations, output, **device):
    """qualify(...) of set fixture-v1 under `root`; each emulator it starts is a fresh FixtureDevice(**device)."""
    with patch(START, side_effect=fixture_start(**device)):
        return qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=output, registrations=registrations)


def booting(reading, *, whole_run=False):
    """A stand-in for `NativeSession.start` whose emulators are fresh fixture devices, each with the program counter
    `reading(run, session)` gives: `run` the run's name below its device (with the device, under `whole_run`), `session`
    the session's directory, `session` for the grade and `session-emission` for the emission."""
    def start(*, recipe, private_dir=None, **_):
        session = Path(private_dir)
        run = session.parent.name
        named = session.parent.parent.name + '/' + run if whole_run else run
        return FixtureDevice(pc=hex(reading(named, session.name))).session(recipe, private_dir)
    return start


def files(root):
    """{path relative to `root`: bytes} of every file under `root`."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in Path(root).rglob('*') if path.is_file()}


class Workspace:
    def workspace(self):
        """A new set directory, and the path of an output directory beside it that does not exist yet."""
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'set'
        root.mkdir()
        return root, Path(directory.name) / 'qualification'


class QualifiedSetTests(Workspace, unittest.TestCase):
    """The two-device fixture set, qualified once for the class: one qualification is twelve emulator runs."""

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(); cls.addClassCleanup(directory.cleanup)
        cls.root, cls.out = Path(directory.name) / 'set', Path(directory.name) / 'qualification'
        cls.root.mkdir()
        # The output exists already, empty, in a mode that lets others read it.
        cls.out.mkdir()
        cls.out.chmod(0o755)
        cls.registered = {SET: write_fixture_set(cls.root)}
        cls.before = files(cls.root)
        cls.record = run_qualify(cls.root, cls.registered, cls.out, process=True)

    def admitted(self, device_id='0000d001', *, root=None, password=PASSWORD, registrations=None):
        return generated_sets.admitted(root or self.root, SET, device_id, password,
                                       registrations=registrations or self.registered)

    def copy(self):
        """A copy of the qualified set, to change."""
        root, _ = self.workspace()
        shutil.copytree(self.root, root, dirs_exist_ok=True)
        return root

    def test_a_set_qualifies_when_references_pass_everywhere_and_sampled_mutants_fail(self):
        record = self.record
        self.assertEqual((record['status'], record['sample'], record['failures']), ('passed', ['0000d001', '0000d002'], []))
        self.assertEqual(self.admitted('0000d001'), [])
        self.assertEqual(self.admitted('0000d002'), [])

    def test_the_record_times_every_run_and_is_sealed_beside_the_unchanged_manifest_with_a_plaintext_copy(self):
        record = self.record
        self.assertEqual({key: record[key] for key in ('schema', 'registration', 'manifest_sha256', 'devices')},
                         {'schema': 'generated-set-qualification/1', 'registration': SET,
                          'manifest_sha256': self.registered[SET],
                          'devices': {'0000d001': {'a': True, 'b': True}, '0000d002': {'a': True, 'b': True}}})
        self.assertEqual(record['grader_identity'], grader_identity(case_id='generated'))
        # Each sampled device rejected every mutant, on at least the checks the mutant targets.
        self.assertEqual([(row['device'], row['id'], row['rejected']) for row in record['mutants']],
                         [(device, mutant, True) for device in ('0000d001', '0000d002') for mutant in MUTANTS])
        targets = {mutant['id']: mutant['expected_failed_checks'] for mutant in FIXTURE_ANSWER_KEY['mutants']}
        for row in record['mutants']:
            self.assertLessEqual(set(targets[row['id']]), set(row['failed_checks']))
        runs = ['reference-a', 'reference-b'] + ['mutant-' + mutant for mutant in MUTANTS]
        self.assertEqual(list(record['seconds']), [device + '/' + run for device in ('0000d001', '0000d002') for run in runs])
        self.assertTrue(all(type(seconds) is float and seconds > 0 for seconds in record['seconds'].values()))
        # The record is sealed beside the manifest. The manifest and the device files stay as registered, so
        # requalifying never changes the manifest hash.
        sealed = self.root / 'qualification.enc'
        self.assertEqual(PASSWORD.unlock(sealed, hashlib.sha256(sealed.read_bytes()).hexdigest()), record)
        after = files(self.root)
        after.pop('qualification.enc')
        self.assertEqual(after, self.before)
        self.assertEqual(json.loads((self.out / 'qualification.json').read_text(encoding='utf-8')), record)
        # Each device's decrypted image was removed after its runs.
        images = [path for path in self.out.rglob('*') if path.is_file() and path.read_bytes() == FIXTURE_IMAGE]
        self.assertEqual(images, [])

    def test_the_output_and_the_run_directories_in_it_are_private(self):
        # Catches the reference drivers and hidden checks left readable by other users: an existing empty output kept
        # its mode, and runs/, native/ and each device's directory in them took the default one.
        made = ('0000d001', '0000d002', *self.record['seconds'])  # each device's directory, then each run's
        directories = ['.', 'runs', 'native', *(top + '/' + name for top in ('runs', 'native') for name in made)]
        self.assertTrue(all((self.out / name).is_dir() for name in directories))
        if os.name == 'posix':
            self.assertEqual({name: (self.out / name).stat().st_mode & 0o777 for name in directories},
                             dict.fromkeys(directories, 0o700))

    def test_admission_needs_the_registered_manifest_and_the_current_family_identity(self):
        current = grader_identity(case_id='generated')
        # The whole identity counts, not its sha256 alone.
        for name, another in (('another sha256', {**current, 'sha256': 'f' * 64}),
                              ('another module under the same sha256',
                               {**current, 'modules': {**current['modules'], 'benchmark_support.generated': '0' * 64}})):
            with self.subTest(name), patch.object(generated_sets, 'grader_identity', return_value=another):
                self.assertEqual(self.admitted(), ['qualification under another grader identity'])
        self.assertEqual(self.admitted(registrations={SET: '0' * 64}), ['manifest refused: ValueError'])
        # The same manifest in other bytes, registered anew, is another manifest than the one qualified.
        root = self.copy()
        manifest = root / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes().replace(b'\n', b'\r\n'))
        registered_anew = {SET: hashlib.sha256(manifest.read_bytes()).hexdigest()}
        self.assertEqual(self.admitted(root=root, registrations=registered_anew), ['qualification of another manifest'])

    def test_admission_refuses_a_record_it_cannot_read_or_one_of_another_set_or_device_and_never_raises(self):
        unqualified = self.copy()
        (unqualified / 'qualification.enc').unlink()
        changed = self.copy()
        sealed = bytearray((changed / 'qualification.enc').read_bytes())
        sealed[len(sealed) // 2] ^= 1
        (changed / 'qualification.enc').write_bytes(bytes(sealed))
        # JSON nested deeper than the parser reaches, as the sealed record and as a manifest registered by its hash.
        nested = b'[' * 100000 + b']' * 100000
        nested_record, nested_manifest = self.copy(), self.copy()
        (nested_record / 'qualification.enc').write_bytes(nested)
        (nested_manifest / 'manifest.json').write_bytes(nested)
        resealed = {}
        for name, record in (('another schema', {**self.record, 'schema': 'generated-set-qualification/2'}),
                             ('a missing field', {k: v for k, v in self.record.items() if k != 'seconds'}),
                             ('devices that are no map', {**self.record, 'devices': ['0000d001']}),
                             ('no object', ['0000d001']),
                             ('another registration', {**self.record, 'registration': 'other-v1'}),
                             ('references that pass as numbers',
                              {**self.record, 'devices': {**self.record['devices'], '0000d001': {'a': 1, 'b': 1.0}}})):
            resealed[name] = self.copy()
            generated_sets.seal_qualification(resealed[name], record, PASSWORD)
        malformed = ('another schema', 'a missing field', 'devices that are no map', 'no object')
        rows = [('no qualification', {'root': unqualified}, ['qualification unreadable: FileNotFoundError']),
                ('another password', {'password': truth.EvaluatorPassword('another credential')}, ['qualification unreadable: ValueError']),
                ('a changed byte', {'root': changed}, ['qualification unreadable: ValueError']),
                ('a nested record', {'root': nested_record}, ['qualification unreadable: RecursionError']),
                ('a nested registered manifest',
                 {'root': nested_manifest, 'registrations': {SET: hashlib.sha256(nested).hexdigest()}},
                 ['manifest refused: RecursionError']),
                *((name, {'root': resealed[name]}, ['qualification malformed']) for name in malformed),
                ('another registration', {'root': resealed['another registration']},
                 ['qualification of another registration']),
                # A reference passed only when the record says True, never a number equal to it.
                ('references that pass as numbers', {'root': resealed['references that pass as numbers']},
                 ['references not passed:0000d001']),
                ('a device the set does not hold', {'device_id': '0000d009'}, ['references not passed:0000d009'])]
        for name, arguments, reasons in rows:
            with self.subTest(name):
                self.assertEqual(self.admitted(**arguments), reasons)
        with self.subTest('the shipped registrations, which register no set'):
            self.assertEqual(generated_sets.admitted(self.root, SET, '0000d001', PASSWORD),
                             ['manifest refused: ValueError'])
        with self.subTest('no grader identity'), \
                patch.object(generated_sets, 'grader_identity', side_effect=ValueError('Grader module missing')):
            self.assertEqual(self.admitted(), ['grader identity unavailable: ValueError'])

    def resealed(self, change):
        """A copy of the qualified set whose record `change` altered, sealed again under its password."""
        root, record = self.copy(), copy.deepcopy(self.record)
        change(record)
        generated_sets.seal_qualification(root, record, PASSWORD)
        return root

    def test_04_B3_admission_checks_the_whole_record_and_not_only_the_asked_for_device(self):
        def drop_required(record):
            record['mutants'] = [row for row in record['mutants']
                                 if (row['device'], row['id']) != ('0000d002', 'wrong-state')]
            del record['runs']['0000d002/mutant-wrong-state']

        def skip_all(record):
            record['mutants'] = []
            record['runs'] = {name: row for name, row in record['runs'].items() if 'mutant' not in name}

        def not_native(record):
            for row in record['runs'].values():
                row['native_process_observed'] = False
        rows = [('a failure', lambda record: record['failures'].append('/private/path'),
                 ['qualification failures recorded']),
                ('another device failed', lambda record: record['devices']['0000d002'].update(b=False),
                 ['references not passed:0000d002']),
                ('a required mutant skipped', drop_required, ['required mutant missing:0000d002']),
                ('every mutant skipped', skip_all,
                 ['required mutant missing:0000d001', 'required mutant missing:0000d002']),
                ('no native process', not_native,
                 ['native process not observed:0000d001', 'native process not observed:0000d002']),
                ('runs of a field of no shape', lambda record: record.update(runs=7),
                 ['qualification runs differ from the manifest and mutants'])]
        for name, change, reasons in rows:
            with self.subTest(name):
                self.assertEqual(self.admitted('0000d001', root=self.resealed(change)), reasons)

    def test_04_B3_todays_reasons_stay_without_repeats(self):
        def pending(record):
            record['status'] = 'pending'
            record['devices']['0000d001'] = {'a': False, 'b': True}
        self.assertEqual(self.admitted('0000d001', root=self.resealed(pending)),
                         ['qualification pending', 'references not passed:0000d001'])

    def test_04_B3_a_refused_manifest_has_no_other_reason_from_the_record(self):
        def not_native(record):
            record['failures'].append('anything')
        self.assertEqual(self.admitted(root=self.resealed(not_native), registrations={SET: '0' * 64}),
                         ['manifest refused: ValueError'])

    def test_04_B4_admission_refuses_runs_whose_boot_readings_differ_or_are_missing(self):
        def differs(record):
            record['runs']['0000d002/reference-a']['boot_reading'] = {'pc': 1}

        def missing(record):
            del record['runs']['0000d001/reference-b']['boot_reading']
        self.assertEqual(self.admitted('0000d001', root=self.resealed(differs)), ['boot reading differs:0000d002'])
        self.assertEqual(self.admitted('0000d002', root=self.resealed(missing)), ['boot reading missing:0000d001'])


def clean_record():
    """(a record `qualification_reasons` has nothing to say of, its manifest): three devices of one level and framing,
    the third outside the sample. Each sampled device has the required mutants and one more."""
    ids = ('0000d001', '0000d002', '0000d003')
    manifest = {'schema': 'generated-set-manifest/1', 'set': SET, 'manifest_sha256': 'a' * 64,
                'devices': [{'id': identity, 'level': 1, 'framing': 'text-lines'} for identity in ids]}
    mutants = [{'device': device, 'id': mutant, 'rejected': True, 'failed_checks': ['s1/1/answer']}
               for device in ids[:2] for mutant in ('constant-output', 'wrong-scale', 'wrong-state', 'extra')]
    names = [device + '/reference-' + name for device in ids for name in 'ab']
    names += [row['device'] + '/mutant-' + row['id'] for row in mutants]
    return ({'schema': 'generated-set-qualification/1', 'registration': SET, 'manifest_sha256': 'a' * 64,
             'grader_identity': {'sha256': 'b' * 64}, 'status': 'passed', 'failures': [],
             'devices': {device: {'a': True, 'b': True} for device in ids}, 'sample': list(ids[:2]), 'mutants': mutants,
             'seconds': dict.fromkeys(names, 1.0),
             'runs': {name: {'backend': 'native-renode', 'native_process_observed': True, 'boot_reading': {'pc': 1}}
                      for name in names}}, manifest)


def changed(change):
    """What `qualification_reasons` gives for the clean record once `change(record)` altered it."""
    record, manifest = clean_record()
    change(record)
    return qualification_reasons(record, manifest)


def drop_mutant(record, device, mutant):
    record['mutants'] = [row for row in record['mutants'] if (row['device'], row['id']) != (device, mutant)]
    del record['runs'][device + '/mutant-' + mutant]


def add_mutant(record, device, mutant, **row):
    record['mutants'].append({'device': device, 'id': mutant, 'rejected': True, 'failed_checks': [], **row})
    record['runs'][device + '/mutant-' + mutant] = {'backend': 'native-renode', 'native_process_observed': True,
                                                    'boot_reading': {'pc': 1}}


class QualificationReasonsTests(unittest.TestCase):
    def test_04_B3_a_complete_record_has_no_reason(self):
        self.assertEqual(qualification_reasons(*clean_record()), [])

    def test_04_B3_it_gives_a_reason_for_each_thing_the_whole_record_must_show(self):
        def set_field(field, value):
            return lambda record: record.update({field: value})

        def reference(device, name, value):
            return lambda record: record['devices'][device].update({name: value})

        def row(index, **fields):
            return lambda record: record['mutants'][index].update(fields)

        def extra_run(record):
            record['runs']['0000d003/reference-c'] = dict(record['runs']['0000d003/reference-a'])

        def missing_run(record):
            del record['runs']['0000d002/reference-b']

        def repeated(record):
            record['mutants'].append(dict(record['mutants'][0]))

        def not_native(name, value):
            return lambda record: record['runs'][name].update(native_process_observed=value)
        runs_differ = 'qualification runs differ from the manifest and mutants'
        rows = [
            ('a failure recorded', set_field('failures', ['anything']), ['qualification failures recorded']),
            ('a device missing', lambda record: record['devices'].pop('0000d003'),
             ['qualification devices differ from the manifest', 'references not passed:0000d003']),
            ('a device the manifest does not list',
             lambda record: record['devices'].update({'0000dfff': {'a': True, 'b': True}}),
             ['qualification devices differ from the manifest']),
            ('a reference of an unasked device failed', reference('0000d003', 'b', False),
             ['references not passed:0000d003']),
            ('a reference passes as a number', reference('0000d002', 'a', 1), ['references not passed:0000d002']),
            ('another sample', set_field('sample', ['0000d001', '0000d003']),
             ['qualification sample differs from the manifest']),
            ('a mutant not rejected', row(0, rejected=False), ['mutant not rejected:0000d001']),
            ('a mutant rejected as a number', row(4, rejected=1), ['mutant not rejected:0000d002']),
            ('a mutant row repeated', repeated, ['mutant repeated:0000d001']),
            ('a required mutant skipped', lambda record: drop_mutant(record, '0000d002', 'wrong-state'),
             ['required mutant missing:0000d002']),
            ('a mutant outside the sample', lambda record: add_mutant(record, '0000d003', 'extra'),
             ['mutant outside the sample:0000d003']),
            ('a run too many', extra_run, [runs_differ]),
            ('a run missing', missing_run, [runs_differ]),
            ('a run of no native process', not_native('0000d003/reference-a', False),
             ['native process not observed:0000d003']),
            ('a native process as a number', not_native('0000d001/mutant-extra', 1),
             ['native process not observed:0000d001']),
            ('a native process unknown', not_native('0000d001/mutant-extra', None),
             ['native process not observed:0000d001'])]
        for name, change, reasons in rows:
            with self.subTest(name):
                self.assertEqual(changed(change), reasons)

    def test_04_B3_a_dropped_extra_mutant_is_not_detected(self):
        # The record holds no list of the key's mutants, and its tag authenticates it.
        self.assertEqual(changed(lambda record: drop_mutant(record, '0000d001', 'extra')), [])

    def test_04_B3_it_never_reads_the_backend(self):
        def another(record):
            for row in record['runs'].values():
                row['backend'] = 'anything at all'
        self.assertEqual(changed(another), [])
        def absent(record):
            for row in record['runs'].values():
                del row['backend']
        self.assertEqual(changed(absent), [])

    def test_04_B3_a_reason_names_at_most_a_device_id_and_copies_nothing_from_the_record(self):
        private = '/Users/someone/vault/set'

        def change(record):
            record['failures'] = ['reference a error:0000d001 (OSError: ' + private + ')']
            record['devices'][private] = {'a': False, 'b': False}
            record['sample'] = [private]
            add_mutant(record, private, private, rejected=private)
            record['mutants'].append(dict(record['mutants'][-1]))
            record['runs'][private + '/reference-a'] = {'native_process_observed': private, 'boot_reading': private}
            record['runs'][private + '/reference-b'] = {'native_process_observed': False,
                                                        'boot_reading': {'pc': private}}
        reasons = changed(change)
        self.assertGreater(len(reasons), 4)
        self.assertNotIn('someone', json.dumps(reasons))
        self.assertEqual({reason for reason in reasons if ':' in reason}, set())

    def test_04_B3_it_never_raises_whatever_the_shape_of_a_field(self):
        deep = current = []
        for _ in range(100000):
            current.append([])
            current = current[0]
        shapes = (None, 0, 1.5, True, '', 'text', [], {}, [None], [[]], [{}], {'0000d001': None}, {'a/b': 7},
                  {'0000d001/reference-a': []}, [{'device': []}], [{'device': 'x', 'id': []}],
                  [{'device': {}, 'id': {}}], deep)
        for field in ('failures', 'devices', 'sample', 'mutants', 'runs', 'schema', 'seconds'):
            for shape in shapes:
                with self.subTest(field=field, shape=type(shape).__name__ if shape is deep else shape):
                    self.assertIsInstance(changed(lambda record: record.update({field: shape})), list)
        for record in (None, [], 'text'):
            with self.subTest(record=record):
                self.assertEqual(qualification_reasons(record, clean_record()[1]), ['qualification malformed'])
        self.assertIn('qualification failures recorded', qualification_reasons({}, clean_record()[1]))
        with self.subTest('readings too deep to compare'):
            def deep_readings(record):
                for row in record['runs'].values():
                    nested = current = []
                    for _ in range(100000):
                        current.append([])
                        current = current[0]
                    row['boot_reading'] = {'pc': nested}
            self.assertEqual(changed(deep_readings), ['qualification malformed'])

    def test_04_B4_a_devices_runs_must_all_show_the_same_reading(self):
        def reading(name, value):
            return lambda record: record['runs'][name].update(boot_reading=value)

        def absent(record):
            del record['runs']['0000d002/reference-a']['boot_reading']
        rows = [('one run differs', reading('0000d002/mutant-extra', {'pc': 2}), ['boot reading differs:0000d002']),
                ('another key', reading('0000d003/reference-b', {'sp': 1}), ['boot reading differs:0000d003']),
                ('a missing reading', absent, ['boot reading missing:0000d002']),
                ('a reading of None', reading('0000d001/reference-a', None), ['boot reading missing:0000d001']),
                ('an empty reading', reading('0000d001/reference-a', {}), ['boot reading missing:0000d001']),
                ('a reading that is no object', reading('0000d001/reference-a', 7), ['boot reading missing:0000d001'])]
        for name, change, reasons in rows:
            with self.subTest(name):
                self.assertEqual(changed(change), reasons)

    def test_04_B4_devices_may_read_differently_from_each_other(self):
        def by_device(record):
            for name, row in record['runs'].items():
                row['boot_reading'] = {'pc': int(name[7])}
        self.assertEqual(changed(by_device), [])


class PendingSetTests(Workspace, unittest.TestCase):
    def test_04_B5_a_record_of_fixture_devices_with_no_process_stand_in_is_refused(self):
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        record = run_qualify(root, registered, out)
        self.assertEqual((record['status'], record['failures']), ('passed', []))
        self.assertEqual(generated_sets.admitted(root, SET, '0000d001', PASSWORD, registrations=registered),
                         ['native process not observed:0000d001'])
        # The same set qualified with the stand-in is admitted.
        record = run_qualify(root, registered, out.with_name('again'), process=True)
        self.assertEqual((record['status'], record['failures']), ('passed', []))
        self.assertEqual(generated_sets.admitted(root, SET, '0000d001', PASSWORD, registrations=registered), [])

    def test_04_B8_each_run_records_its_backend_whether_a_native_emulator_process_ran_and_its_boot_reading(self):
        # Catches a record made on test fixtures that reads like one made under Renode: every run here was graded on an
        # attached fixture session, so no native emulator process was observed.
        root, out = self.workspace()
        record = run_qualify(root, write_set(root, {'0000d001': FIXTURE_ANSWER_KEY}), out)
        self.assertEqual(record['runs'], {run: {'backend': 'native-renode', 'native_process_observed': False,
                                                'boot_reading': {'pc': BOOT_PC}}
                                          for run in record['seconds']})
        self.assertEqual((record['schema'], record['status'], record['failures']),
                         ('generated-set-qualification/1', 'passed', []))

    def test_04_B8_a_run_that_raised_has_no_reading_even_if_it_left_partial_evidence(self):
        # On PING the machine stops, in each grade's second sequence, so each grade raises with its evidence saved.
        root, out = self.workspace()
        record = run_qualify(root, write_set(root, {'0000d001': FIXTURE_ANSWER_KEY}), out, dies_on='PING')
        self.assertEqual(record['runs'], {run: {'backend': None, 'native_process_observed': None, 'boot_reading': None}
                                          for run in record['seconds']})
        partial = json.loads((out / 'runs/0000d001/reference-a/raw.json').read_text(encoding='utf-8'))
        self.assertEqual((partial['incomplete'], partial['boot_reading']), (True, {'pc': BOOT_PC}))
        # A run with no reading is compared with nothing, so no boot reading failure is added for it.
        self.assertEqual([failure for failure in record['failures'] if 'boot reading' in failure], [])

    def test_04_B8_readings_that_differ_add_one_failure_after_all_the_devices_runs_and_no_run(self):
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        start = booting(lambda run, session: 0x08000200 if run == 'mutant-refusal-as-value' else BOOT_PC)
        with patch(START, side_effect=start):
            record = qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
        self.assertEqual((record['status'], record['failures']), ('pending', ['boot reading differs:0000d001']))
        runs = ['reference-a', 'reference-b'] + ['mutant-' + mutant for mutant in MUTANTS]
        self.assertEqual(list(record['runs']), ['0000d001/' + run for run in runs])
        self.assertEqual(record['runs']['0000d001/mutant-refusal-as-value']['boot_reading'], {'pc': 0x08000200})
        self.assertEqual(record['runs']['0000d001/reference-a']['boot_reading'], {'pc': BOOT_PC})
        self.assertEqual((record['schema'], record['devices']),
                         ('generated-set-qualification/1', {'0000d001': {'a': True, 'b': True}}))
        self.assertTrue(all(row['rejected'] for row in record['mutants']))

    def test_04_B8_a_finished_run_whose_evidence_cannot_be_read_has_no_reading_and_admission_refuses(self):
        # The evidence of three finished runs is gone, is not JSON and holds no object: each row says so, and the
        # qualification goes on to its end instead of raising.
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        real = reference_calibration.calibration_run
        spoiled = {'reference-a': None, 'reference-b': b'not json', 'mutant-wrong-scale': b'[]'}

        def spoiling(*arguments, output_dir, **keywords):
            result = real(*arguments, output_dir=output_dir, **keywords)
            evidence = Path(output_dir) / 'raw.json'
            if Path(output_dir).name in spoiled:
                evidence.unlink()
                if spoiled[Path(output_dir).name] is not None:
                    evidence.write_bytes(spoiled[Path(output_dir).name])
            return result
        with patch.object(reference_calibration, 'calibration_run', spoiling), \
                patch(START, side_effect=fixture_start(process=True)):
            record = qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
        self.assertEqual((record['status'], record['failures']), ('passed', []))
        for run, row in record['runs'].items():
            reading = None if run.partition('/')[2] in spoiled else {'pc': BOOT_PC}
            expected = {'backend': 'native-renode', 'native_process_observed': True, 'boot_reading': reading}
            self.assertEqual(row, expected, run)
        self.assertEqual(generated_sets.admitted(root, SET, '0000d001', PASSWORD, registrations=registered),
                         ['boot reading missing:0000d001'])

    def test_04_B8_readings_compare_within_a_device_and_only_on_the_grading_session(self):
        # Two devices boot differently, and each run's emission session reads another value: neither is a failure.
        root, out = self.workspace()
        registered = write_set(root, dict.fromkeys(('0000d001', '0000d002'), FIXTURE_ANSWER_KEY))
        readings = iter(range(0x09000000, 0x0a000000))

        def reading(run, session):
            if session == 'session-emission':
                return next(readings)
            return BOOT_PC if run.startswith('0000d001') else 0x08000200
        with patch(START, side_effect=booting(reading, whole_run=True)):
            record = qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
        self.assertEqual((record['status'], record['failures']), ('passed', []))
        read = {device: {row['boot_reading']['pc'] for run, row in record['runs'].items() if run.startswith(device)}
                for device in ('0000d001', '0000d002')}
        self.assertEqual(read, {'0000d001': {BOOT_PC}, '0000d002': {0x08000200}})

    def test_a_failing_reference_or_a_passing_mutant_leaves_the_set_pending(self):
        reference_b_fails = copy.deepcopy(FIXTURE_ANSWER_KEY)  # its reference b reports the mode in another unit
        reference_b_fails['references']['b']['model']['operations']['query']['outputs']['current']['unit'] = 'count'
        reference_b_fails['references']['b']['capabilities']['tasks']['get_mode']['outputs']['mode']['unit'] = 'count'
        a_mutant_passes = copy.deepcopy(FIXTURE_ANSWER_KEY)
        a_mutant_passes['mutants'].append({'id': 'copy-of-a', **copy.deepcopy(REFERENCE_A),
                                           'expected_failed_checks': ['s1/1/answer']})
        passing_copy = {'device': '0000d001', 'id': 'copy-of-a', 'rejected': False, 'failed_checks': []}
        rows = [('reference b fails', reference_b_fails, 'reference b failed:0000d001', {'a': True, 'b': False}, [],
                 ['qualification pending', 'references not passed:0000d001']),
                ('a mutant passes', a_mutant_passes, 'mutant copy-of-a passed:0000d001', {'a': True, 'b': True},
                 [passing_copy], ['qualification pending'])]
        for name, key, failure, references, unrejected, reasons in rows:
            with self.subTest(name):
                root, out = self.workspace()
                registered = write_set(root, {'0000d001': key})
                record = run_qualify(root, registered, out)
                self.assertEqual((record['status'], record['failures'], record['devices']),
                                 ('pending', [failure], {'0000d001': references}))
                self.assertEqual([row for row in record['mutants'] if not row['rejected']], unrejected)
                # A pending set admits no device.
                admission = generated_sets.admitted(root, SET, '0000d001', PASSWORD, registrations=registered)
                self.assertLessEqual(set(reasons), set(admission))

    def test_undecided_checks_and_run_errors_neither_pass_a_reference_nor_reject_a_mutant(self):
        # The device cannot read its mode while in mode 3. Both references set mode 3, so two of their state checks are
        # undecided: an unknown grade, which is no pass. offset-two sets the mode two above the one asked, so its run
        # fails, but s1/3/state:c1, a check it targets, is undecided, never failed. invalid-model never reaches a
        # device: its run raises, since its model is empty though it maps the mission's tasks. The required mutants never
        # reach mode 3, or fail a check before it, so each is rejected.
        offset_two = copy.deepcopy(REFERENCE_A)
        offset_two['capabilities']['tasks']['set_mode']['inputs']['mode'] = {'parameter': 'mode', 'kind': 'affine',
                                                                             'scale': 1, 'offset': 2}
        key = copy.deepcopy(FIXTURE_ANSWER_KEY)
        key['mutants'] += [
            {'id': 'offset-two', **offset_two, 'expected_failed_checks': ['s1/3/state:c1', 's1/4/out:mode']},
            {'id': 'invalid-model', 'model': {}, 'capabilities': copy.deepcopy(REFERENCE_A['capabilities']),
             'expected_failed_checks': ['s1/1/answer']}]
        root, out = self.workspace()
        record = run_qualify(root, write_set(root, {'0000d001': key}), out, unreadable_in_mode=3)
        self.assertEqual((record['status'], record['devices']), ('pending', {'0000d001': {'a': False, 'b': False}}))
        self.assertEqual(record['failures'], [
            'reference a unknown:0000d001', 'reference b unknown:0000d001',
            'mutant offset-two missed expected checks:0000d001',
            'mutant invalid-model error:0000d001 (ValueError: Reference candidates must be structurally valid before native '
            'calibration)'])
        rows = {row['id']: row for row in record['mutants']}
        offset, invalid = rows['offset-two'], rows['invalid-model']
        self.assertEqual((offset['rejected'], 's1/4/out:mode' in offset['failed_checks'],
                          's1/3/state:c1' in offset['failed_checks']), (False, True, False))
        self.assertEqual(invalid, {'device': '0000d001', 'id': 'invalid-model', 'rejected': False, 'failed_checks': []})
        self.assertEqual([identifier for identifier, row in rows.items() if row['rejected']], MUTANTS)
        self.assertEqual(list(record['seconds']),
                         ['0000d001/reference-a', '0000d001/reference-b']
                         + ['0000d001/mutant-' + mutant['id'] for mutant in key['mutants']])
        # A run that raised left no result to say how it ran.
        self.assertEqual((list(record['runs']), record['runs']['0000d001/mutant-invalid-model']),
                         (list(record['seconds']),
                          {'backend': None, 'native_process_observed': None, 'boot_reading': None}))

    def test_a_device_outside_the_sample_runs_no_mutant(self):
        # 0000d001 and 0000d002 are the sample of (level 1, text lines); 0000d003 lies outside it.
        root, out = self.workspace()
        devices = ('0000d001', '0000d002', '0000d003')
        record = run_qualify(root, write_set(root, dict.fromkeys(devices, FIXTURE_ANSWER_KEY)), out)
        self.assertEqual((record['status'], record['sample'], record['failures']),
                         ('passed', ['0000d001', '0000d002'], []))
        self.assertEqual({row['device'] for row in record['mutants']}, {'0000d001', '0000d002'})
        self.assertEqual([run for run in record['seconds'] if run.startswith('0000d003/')],
                         ['0000d003/reference-a', '0000d003/reference-b'])

    def test_a_grader_change_while_the_set_qualifies_leaves_it_pending(self):
        # The grader's modules load as the runs need them, so a change that lands during the runs can grade the later
        # runs with code the identity stamped at the start does not describe; that identity must still be the grader's
        # at the end. Only the two ends are compared, so a change undone before the end goes unseen.
        identity = grader_identity(case_id='generated')
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        with patch.object(set_qualification, 'grader_identity', side_effect=[identity, {**identity, 'sha256': 'e' * 64}]):
            record = run_qualify(root, registered, out)
        self.assertEqual((record['status'], record['failures'], record['grader_identity']),
                         ('pending', ['grader identity changed during qualification'], identity))
        self.assertIn('qualification pending',
                      generated_sets.admitted(root, SET, '0000d001', PASSWORD, registrations=registered))

    def test_a_decrypted_image_the_qualification_could_not_remove_leaves_the_set_pending(self):
        # A removal that fails, as on Windows while another process holds the file, is one that removes nothing here.
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        with patch.object(set_qualification, 'rmtree'):
            record = run_qualify(root, registered, out)
        self.assertEqual(record['failures'], ['decrypted image not removed:0000d001'])
        self.assertEqual([path.read_bytes() for path in out.glob('image-*/image')], [FIXTURE_IMAGE])

    def test_each_run_keeps_its_emulator_sessions_in_a_directory_of_its_own(self):
        # A run's emission session once lay beside its grading session, under the run's name with '-emission' added,
        # so mutant X's emission and the grading of mutant X-emission shared one directory.
        key = copy.deepcopy(FIXTURE_ANSWER_KEY)
        wrong_scale = key['mutants'][0]
        key['mutants'].append({**copy.deepcopy(wrong_scale), 'id': wrong_scale['id'] + '-emission'})
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': key})
        sessions, start = [], fixture_start()

        def recording(**arguments):
            sessions.append(Path(arguments['private_dir']))
            return start(**arguments)

        with patch(START, side_effect=recording):
            record = qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
        self.assertEqual([row['rejected'] for row in record['mutants']], [True] * len(key['mutants']))
        expected = [out.resolve() / 'native' / run / name for run in record['seconds']
                    for name in ('session', 'session-emission')]
        self.assertEqual(sorted(sessions), sorted(expected))
        self.assertEqual(len(set(sessions)), len(sessions))

    def test_the_sample_is_the_first_two_devices_by_id_of_each_level_and_framing(self):
        devices = [{'id': identity, 'level': level, 'framing': framing} for identity, level, framing in (
            ('0000d005', 1, 'text-lines'), ('0000d001', 1, 'text-lines'), ('0000d003', 1, 'text-lines'), ('0000d007', 2, 'text-lines'),
            ('0000d002', 2, 'text-lines'), ('0000d006', 2, 'text-lines'), ('0000d004', 1, 'binary-frames'))]
        self.assertEqual(sample(devices), ['0000d001', '0000d002', '0000d003', '0000d004', '0000d006'])

    def test_nothing_runs_or_is_sealed_when_the_output_is_unsafe_or_a_device_cannot_be_opened(self):
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        # An earlier record stays as it was: a refused qualification replaces nothing.
        generated_sets.seal_qualification(root, {'schema': 'earlier record'}, PASSWORD)
        earlier = (root / 'qualification.enc').read_bytes()
        inside = Path(generative_driver.__file__).resolve().parent / 'set-qualification-output'
        crowded = out.with_name('crowded')
        crowded.mkdir()
        (crowded / 'earlier.json').write_text('{}', encoding='utf-8')
        rows = [('an output inside the repository', {'output_dir': inside}, 'outside repository'),
                ('an output that is not empty', {'output_dir': crowded}, 'empty output directory'),
                ('a set that is not registered', {'registrations': {}}, 'not registered'),
                ('another password', {'password': truth.EvaluatorPassword('another credential')}, 'Cannot unlock')]
        for name, change, pattern in rows:
            arguments = {'password': PASSWORD, 'output_dir': out, 'registrations': registered, **change}
            with self.subTest(name), patch(START) as start:
                with self.assertRaisesRegex(ValueError, pattern):
                    qualify(root, SET, arguments.pop('password'), renode=Path('/toy'), **arguments)
                start.assert_not_called()
                self.assertEqual((root / 'qualification.enc').read_bytes(), earlier)
        self.assertFalse(inside.exists())
        with self.subTest('a malformed answer key'):
            malformed, out = self.workspace()
            # Sealing refuses such a key, so the set stands for one written without the validator.
            with patch.object(generated_sets, 'validate_device'):
                registered = write_set(malformed, {'0000d001': {**FIXTURE_ANSWER_KEY, 'nonce': 'not hexadecimal'}})
            with patch(START) as start, self.assertRaisesRegex(ValueError, 'Invalid generated answer key'):
                qualify(malformed, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
            start.assert_not_called()
            self.assertFalse((malformed / 'qualification.enc').exists())

    def test_nothing_runs_or_is_sealed_for_a_set_inside_the_repository_or_a_workers_directory(self):
        # Catches a set root that qualification never placed, though the sealed record is written into it: the root is
        # held to the output's placement, outside the repository, the installed package and known candidate roots, such
        # as a worker's work directory, which holds `_report_schema.json`.
        base, out = self.workspace()
        work = base.with_name('work')
        work.mkdir()
        (work / '_report_schema.json').write_text('{}', encoding='utf-8')
        in_work = work / 'set'
        in_work.mkdir()
        registered = write_set(in_work, {'0000d001': FIXTURE_ANSWER_KEY})
        in_package = Path(generative_driver.__file__).resolve().parent / 'generated-set'
        for name, root in (('a worker\'s directory', in_work), ('the installed package', in_package)):
            with self.subTest(name), patch(START) as start:
                with self.assertRaisesRegex(ValueError, 'set must lie outside the repository'):
                    qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
                start.assert_not_called()
        self.assertFalse((in_work / 'qualification.enc').exists())
        self.assertFalse(in_package.exists())
        self.assertFalse(out.exists())


class RedoingAPassedQualificationTests(Workspace, unittest.TestCase):
    """A set whose table is the one `_SETS_FILE` reads, as `qualify` reads the mark from there. Past the check,
    `qualify` opens the set's devices, so a refused `open_device` shows how far it got."""

    def scoring_set(self, *, scoring=True):
        """A one-device set under the patched table, with its mark: (root, output, table)."""
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        table = root.with_name('sets.json')
        table.write_text(registrations_table(registered, scoring=scoring), encoding='utf-8')
        guard = patch.object(generated_sets, '_SETS_FILE', table)
        guard.start()
        self.addCleanup(guard.stop)
        return root, out, registered

    @staticmethod
    def seal(root, **fields):
        record = {'status': 'passed', 'grader_identity': grader_identity(case_id='generated'), **fields}
        generated_sets.seal_qualification(root, record, PASSWORD)
        return (root / 'qualification.enc').read_bytes()

    def assertReachesOpening(self, root, out, password=PASSWORD, **arguments):
        with patch.object(set_qualification, 'open_device', side_effect=ValueError('Reached the devices')):
            with self.assertRaisesRegex(ValueError, '^Reached the devices$'):
                qualify(root, SET, password, renode=Path('/toy'), output_dir=out, **arguments)

    def test_04_B12_a_passed_record_of_a_scoring_set_is_not_replaced_by_accident(self):
        root, out, _ = self.scoring_set()
        sealed = self.seal(root)
        with patch(START) as start, \
                patch.object(set_qualification, 'open_device', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(ValueError, 'already passed.*' + SET):
                qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out)
            start.assert_not_called()
        self.assertEqual((root / 'qualification.enc').read_bytes(), sealed)
        self.assertFalse(out.exists())

    def test_04_B12_replace_qualifies_it_again(self):
        root, out, _ = self.scoring_set()
        sealed = self.seal(root)
        with patch(START, side_effect=fixture_start(process=True)):
            record = qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, replace=True)
        self.assertEqual((record['status'], record['failures']), ('passed', []))
        self.assertNotEqual((root / 'qualification.enc').read_bytes(), sealed)
        self.assertEqual(generated_sets.admitted(root, SET, '0000d001', PASSWORD), [])

    def test_04_B12_every_other_record_qualifies_as_it_did(self):
        # No RED of their own: each pins that the check lets it by. A mutation shows they can fail.
        root, out, _ = self.scoring_set()
        self.seal(root, grader_identity={**grader_identity(case_id='generated'), 'sha256': 'f' * 64})
        with self.subTest('another identity'):
            self.assertReachesOpening(root, out)
        self.seal(root, status='pending')
        with self.subTest('a pending record'):
            self.assertReachesOpening(root, out)
        generated_sets.seal_qualification(root, ['no object'], PASSWORD)
        with self.subTest('a record that is no object'):
            self.assertReachesOpening(root, out)
        self.seal(root)
        changed_byte = bytearray((root / 'qualification.enc').read_bytes())
        changed_byte[len(changed_byte) // 2] ^= 1
        (root / 'qualification.enc').write_bytes(bytes(changed_byte))
        with self.subTest('an unreadable record'):
            self.assertReachesOpening(root, out)
        (root / 'qualification.enc').unlink()
        with self.subTest('no record'):
            self.assertReachesOpening(root, out)
        self.seal(root)
        with self.subTest('a passed record another password does not unlock'):
            self.assertReachesOpening(root, out, password=EvaluatorPassword('another credential'))

    def test_04_B12_a_development_set_qualifies_as_it_did(self):
        root, out, _ = self.scoring_set(scoring=False)
        self.seal(root)
        self.assertReachesOpening(root, out)

    def test_04_B12_a_call_with_its_own_registrations_counts_as_a_development_set(self):
        root, out, registered = self.scoring_set()
        self.seal(root)
        self.assertReachesOpening(root, out, registrations=registered)


class CommandTests(Workspace, unittest.TestCase):
    def command(self, root, password_file, out, *more):
        """`benchmark set qualify` as the command line runs it: (exit code, what it printed, what it logged)."""
        printed, logged = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(logged):
            code = main(['set', 'qualify', '--set', SET, '--root', str(root), '--password-file', str(password_file),
                         '--renode', '/toy', '--output', str(out), *more])
        return code, printed.getvalue(), logged.getvalue()

    def password_file(self, root):
        path = root.with_name('set.password')
        path.write_text(PASSWORD_TEXT + '\n', encoding='utf-8')
        return path

    def test_04_B12_without_the_flag_the_command_refuses_a_passed_scoring_set_and_with_it_goes_on(self):
        root, out = self.workspace()
        registered = write_set(root, {'0000d001': FIXTURE_ANSWER_KEY})
        table = root.with_name('sets.json')
        table.write_text(registrations_table(registered, scoring=True), encoding='utf-8')
        generated_sets.seal_qualification(
            root, {'status': 'passed', 'grader_identity': grader_identity(case_id='generated')}, PASSWORD)
        sealed = (root / 'qualification.enc').read_bytes()
        with patch.object(generated_sets, '_SETS_FILE', table):
            code, printed, _ = self.command(root, self.password_file(root), out)
            self.assertEqual(code, 1)
            self.assertRegex(json.loads(printed)['error'], 'already passed.*--replace-passed')
            self.assertEqual((root / 'qualification.enc').read_bytes(), sealed)
            with patch(START, side_effect=fixture_start(process=True)):
                code, printed, _ = self.command(root, self.password_file(root), out, '--replace-passed')
        self.assertEqual((code, json.loads(printed)['status']), (0, 'passed'))
        self.assertNotEqual((root / 'qualification.enc').read_bytes(), sealed)

    def test_02_B9_the_command_reads_its_file_once(self):
        root, out = self.workspace()
        write_fixture_set(root)
        handle = self.password_file(root)
        real = truth.read_password
        with patch.object(truth, 'read_password', side_effect=real) as reading, \
                patch('generative_driver.benchmark_support.set_qualification.qualify',
                      return_value={'status': 'passed', 'failures': [], 'seconds': {}}):
            code, _, _ = self.command(root, handle, out)
        self.assertEqual(code, 0)
        reading.assert_called_once_with(str(handle))

    def test_the_command_refuses_a_set_the_shipped_table_does_not_register(self):
        root, out = self.workspace()
        write_fixture_set(root)
        with patch(START) as start:
            code, printed, logged = self.command(root, self.password_file(root), out)
        self.assertEqual((code, json.loads(printed)), (1, {'ok': False, 'error': 'Generated set not registered: ' + SET}))
        start.assert_not_called()
        self.assertNotIn(PASSWORD_TEXT, printed + logged)
        self.assertFalse((root / 'qualification.enc').exists())

    def test_the_command_prints_the_status_and_each_runs_seconds_and_never_the_password(self):
        root, out = self.workspace()
        seconds = {'0000d001/reference-a': 1.25, '0000d001/reference-b': 1.5}
        for status, failures, code in (('passed', [], 0), ('pending', ['reference b failed:0000d001'], 1)):
            with self.subTest(status), patch('generative_driver.benchmark_support.set_qualification.qualify',
                                             return_value={'status': status, 'failures': failures,
                                                           'seconds': seconds}) as qualifying:
                exit_code, printed, logged = self.command(root, self.password_file(root), out)
                # The password reaches the qualifier without its file's trailing newline.
                self.assertEqual(qualifying.call_count, 1)
                (called_root, called_set, called), keywords = qualifying.call_args
                self.assertEqual((called_root, called_set, keywords), (root, SET, {'renode': Path('/toy'), 'output_dir': out}))
                sealed = called.seal({'a': 1}, out.with_name('sealed.enc'))
                self.assertEqual(PASSWORD.unlock(out.with_name('sealed.enc'), sealed), {'a': 1})
                self.assertEqual((exit_code, json.loads(printed)),
                                 (code, {'ok': status == 'passed', 'status': status, 'failures': failures,
                                         'seconds': seconds}))
                self.assertNotIn(PASSWORD_TEXT, printed + logged)


if __name__ == '__main__':
    unittest.main()

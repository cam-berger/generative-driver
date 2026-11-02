"""A generated set qualified once: both reference drivers on every device, the mutants on a sample of two devices per
level and framing, and the record sealed beside the set's manifest; then the admission a trial on one device needs from
that record. Every run emits a package with the reference runner and grades it with the evaluator, each on a fresh
hand-written fixture device (`generated_fixtures`), never generator output, with NativeSession.start patched."""
import contextlib
import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generative_driver
from generated_fixtures import (FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, MUTANT_ERR_AS_VALUE, PASSWORD, REFERENCE_A,
                                fixture_mission, fixture_start, write_fixture_set)
from generative_driver.benchmark import main
from generative_driver.benchmark_support import generated_sets, set_qualification, truth
from generative_driver.benchmark_support.grader_identity import grader_identity
from generative_driver.benchmark_support.set_qualification import qualify, sample

SET = 'fixture-v1'
START = 'generative_driver.benchmark_support.native.NativeSession.start'
MUTANTS = [mutant['id'] for mutant in FIXTURE_ANSWER_KEY['mutants']]


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
        cls.registered = {SET: write_fixture_set(cls.root)}
        cls.before = files(cls.root)
        cls.record = run_qualify(cls.root, cls.registered, cls.out)

    def admitted(self, device_id='d001', *, root=None, password=PASSWORD, registrations=None):
        return generated_sets.admitted(root or self.root, SET, device_id, password,
                                       registrations=registrations or self.registered)

    def copy(self):
        """A copy of the qualified set, to change."""
        root, _ = self.workspace()
        shutil.copytree(self.root, root, dirs_exist_ok=True)
        return root

    def test_a_set_qualifies_when_references_pass_everywhere_and_sampled_mutants_fail(self):
        record = self.record
        self.assertEqual((record['status'], record['sample'], record['failures']), ('passed', ['d001', 'd002'], []))
        self.assertEqual(self.admitted('d001'), [])
        self.assertEqual(self.admitted('d002'), [])

    def test_the_record_times_every_run_and_is_sealed_beside_the_unchanged_manifest_with_a_plaintext_copy(self):
        record = self.record
        self.assertEqual({key: record[key] for key in ('schema', 'registration', 'manifest_sha256', 'devices')},
                         {'schema': 'generated-set-qualification/1', 'registration': SET,
                          'manifest_sha256': self.registered[SET],
                          'devices': {'d001': {'a': True, 'b': True}, 'd002': {'a': True, 'b': True}}})
        self.assertEqual(record['grader_identity'], grader_identity(case_id='generated'))
        # Each sampled device rejected every mutant, on at least the checks the mutant targets.
        self.assertEqual([(row['device'], row['id'], row['rejected']) for row in record['mutants']],
                         [(device, mutant, True) for device in ('d001', 'd002') for mutant in MUTANTS])
        targets = {mutant['id']: mutant['expected_failed_checks'] for mutant in FIXTURE_ANSWER_KEY['mutants']}
        for row in record['mutants']:
            self.assertLessEqual(set(targets[row['id']]), set(row['failed_checks']))
        runs = ['reference-a', 'reference-b'] + ['mutant-' + mutant for mutant in MUTANTS]
        self.assertEqual(list(record['seconds']), [device + '/' + run for device in ('d001', 'd002') for run in runs])
        self.assertTrue(all(type(seconds) is float and seconds > 0 for seconds in record['seconds'].values()))
        # The record is sealed beside the manifest. The manifest and the device files stay as registered, so
        # requalifying never changes the manifest hash.
        sealed = self.root / 'qualification.enc'
        self.assertEqual(truth.unlock(sealed, PASSWORD, hashlib.sha256(sealed.read_bytes()).hexdigest()), record)
        after = files(self.root)
        after.pop('qualification.enc')
        self.assertEqual(after, self.before)
        self.assertEqual(json.loads((self.out / 'qualification.json').read_text(encoding='utf-8')), record)
        # Each device's decrypted image was removed after its runs.
        images = [path for path in self.out.rglob('*') if path.is_file() and path.read_bytes() == FIXTURE_IMAGE]
        self.assertEqual(images, [])

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
                             ('devices that are no map', {**self.record, 'devices': ['d001']}),
                             ('no object', ['d001']),
                             ('another registration', {**self.record, 'registration': 'other-v1'}),
                             ('references that pass as numbers',
                              {**self.record, 'devices': {**self.record['devices'], 'd001': {'a': 1, 'b': 1.0}}})):
            resealed[name] = self.copy()
            generated_sets.seal_qualification(resealed[name], record, PASSWORD)
        malformed = ('another schema', 'a missing field', 'devices that are no map', 'no object')
        rows = [('no qualification', {'root': unqualified}, ['qualification unreadable: FileNotFoundError']),
                ('another password', {'password': 'another credential'}, ['qualification unreadable: ValueError']),
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
                 ['references not passed:d001']),
                ('a device the set does not hold', {'device_id': 'd009'}, ['references not passed:d009'])]
        for name, arguments, reasons in rows:
            with self.subTest(name):
                self.assertEqual(self.admitted(**arguments), reasons)
        with self.subTest('the shipped registrations, which register no set'):
            self.assertEqual(generated_sets.admitted(self.root, SET, 'd001', PASSWORD),
                             ['manifest refused: ValueError'])
        with self.subTest('no grader identity'), \
                patch.object(generated_sets, 'grader_identity', side_effect=ValueError('Grader module missing')):
            self.assertEqual(self.admitted(), ['grader identity unavailable: ValueError'])


class PendingSetTests(Workspace, unittest.TestCase):
    def test_a_failing_reference_or_a_passing_mutant_leaves_the_set_pending(self):
        reference_b_fails = copy.deepcopy(FIXTURE_ANSWER_KEY)
        reference_b_fails['references']['b'] = copy.deepcopy(MUTANT_ERR_AS_VALUE)
        a_mutant_passes = copy.deepcopy(FIXTURE_ANSWER_KEY)
        a_mutant_passes['mutants'].append({'id': 'copy-of-a', **copy.deepcopy(REFERENCE_A),
                                           'expected_failed_checks': ['s1/1/answer']})
        passing_copy = {'device': 'd001', 'id': 'copy-of-a', 'rejected': False, 'failed_checks': []}
        rows = [('reference b fails', reference_b_fails, 'reference b failed:d001', {'a': True, 'b': False}, [],
                 ['qualification pending', 'references not passed:d001']),
                ('a mutant passes', a_mutant_passes, 'mutant copy-of-a passed:d001', {'a': True, 'b': True},
                 [passing_copy], ['qualification pending'])]
        for name, key, failure, references, unrejected, reasons in rows:
            with self.subTest(name):
                root, out = self.workspace()
                registered = write_set(root, {'d001': key})
                record = run_qualify(root, registered, out)
                self.assertEqual((record['status'], record['failures'], record['devices']),
                                 ('pending', [failure], {'d001': references}))
                self.assertEqual([row for row in record['mutants'] if not row['rejected']], unrejected)
                # A pending set admits no device.
                self.assertEqual(generated_sets.admitted(root, SET, 'd001', PASSWORD, registrations=registered),
                                 reasons)

    def test_undecided_checks_and_run_errors_neither_pass_a_reference_nor_reject_a_mutant(self):
        # The device cannot read its mode while in mode 3. Both references set mode 3, so two of their state checks are
        # undecided: an unknown grade, which is no pass. offset-two sets the mode two above the one asked, so its run
        # fails, but s1/3/state:mode, a check it targets, is undecided, never failed. invalid-model never reaches a
        # device: its run raises. The device lists no required mutant, which only adds the inventory failure.
        offset_two = copy.deepcopy(REFERENCE_A)
        offset_two['capabilities']['tasks']['set_mode']['inputs']['mode'] = {'parameter': 'mode', 'kind': 'affine',
                                                                             'scale': 1, 'offset': 2}
        key = {**copy.deepcopy(FIXTURE_ANSWER_KEY), 'mutants': [
            {'id': 'offset-two', **offset_two, 'expected_failed_checks': ['s1/3/state:mode', 's1/4/out:mode']},
            {'id': 'invalid-model', 'model': {}, 'capabilities': {}, 'expected_failed_checks': ['s1/1/answer']}]}
        root, out = self.workspace()
        record = run_qualify(root, write_set(root, {'d001': key}), out, unreadable_in_mode=3)
        self.assertEqual((record['status'], record['devices']), ('pending', {'d001': {'a': False, 'b': False}}))
        self.assertEqual(record['failures'], [
            'mutant inventory:d001', 'reference a unknown:d001', 'reference b unknown:d001',
            'mutant offset-two missed expected checks:d001',
            'mutant invalid-model error:d001 (ValueError: Reference candidates must be structurally valid before native '
            'calibration)'])
        offset, invalid = record['mutants']
        self.assertEqual((offset['rejected'], 's1/4/out:mode' in offset['failed_checks'],
                          's1/3/state:mode' in offset['failed_checks']), (False, True, False))
        self.assertEqual(invalid, {'device': 'd001', 'id': 'invalid-model', 'rejected': False, 'failed_checks': []})
        self.assertEqual(list(record['seconds']), ['d001/reference-a', 'd001/reference-b', 'd001/mutant-offset-two',
                                                   'd001/mutant-invalid-model'])

    def test_a_device_short_of_the_required_mutants_or_of_two_distinct_references_leaves_the_set_pending(self):
        # d001 and d002, the sample of (level 1, text lines), list no mutant, and d001's reference b is a copy of a.
        # d003 lists every mutant but lies outside the sample, so none of them runs.
        no_mutants = {**copy.deepcopy(FIXTURE_ANSWER_KEY), 'mutants': []}
        same_references = copy.deepcopy(no_mutants)
        same_references['references']['b'] = copy.deepcopy(REFERENCE_A)
        root, out = self.workspace()
        registered = write_set(root, {'d001': same_references, 'd002': no_mutants, 'd003': FIXTURE_ANSWER_KEY})
        record = run_qualify(root, registered, out)
        self.assertEqual((record['status'], record['sample'], record['failures']),
                         ('pending', ['d001', 'd002'],
                          ['mutant inventory:d001', 'references not distinct:d001', 'mutant inventory:d002']))
        self.assertEqual((record['mutants'], list(record['seconds'])),
                         ([], [device + '/reference-' + name for device in ('d001', 'd002', 'd003') for name in 'ab']))

    def test_a_grader_change_while_the_set_qualifies_leaves_it_pending(self):
        # The grader's modules load as the runs need them: a change that lands during the runs and is undone before
        # they end would otherwise seal a passed record under an identity that graded only part of them.
        identity = grader_identity(case_id='generated')
        root, out = self.workspace()
        registered = write_set(root, {'d001': FIXTURE_ANSWER_KEY})
        with patch.object(set_qualification, 'grader_identity', side_effect=[identity, {**identity, 'sha256': 'e' * 64}]):
            record = run_qualify(root, registered, out)
        self.assertEqual((record['status'], record['failures'], record['grader_identity']),
                         ('pending', ['grader identity changed during qualification'], identity))
        self.assertEqual(generated_sets.admitted(root, SET, 'd001', PASSWORD, registrations=registered),
                         ['qualification pending'])

    def test_a_decrypted_image_the_qualification_could_not_remove_leaves_the_set_pending(self):
        # A removal that fails, as on Windows while another process holds the file, is one that removes nothing here.
        # The device's key lists no mutant, which only adds the inventory failure.
        root, out = self.workspace()
        registered = write_set(root, {'d001': {**copy.deepcopy(FIXTURE_ANSWER_KEY), 'mutants': []}})
        with patch.object(set_qualification, 'rmtree'):
            record = run_qualify(root, registered, out)
        self.assertEqual(record['failures'], ['mutant inventory:d001', 'decrypted image not removed:d001'])
        self.assertEqual([path.read_bytes() for path in out.glob('image-*/image')], [FIXTURE_IMAGE])

    def test_each_run_keeps_its_emulator_sessions_in_a_directory_of_its_own(self):
        # A run's emission session once lay beside its grading session, under the run's name with '-emission' added,
        # so mutant X's emission and the grading of mutant X-emission shared one directory.
        wrong_scale = copy.deepcopy(FIXTURE_ANSWER_KEY['mutants'][0])
        key = {**copy.deepcopy(FIXTURE_ANSWER_KEY),
               'mutants': [wrong_scale, {**copy.deepcopy(wrong_scale), 'id': wrong_scale['id'] + '-emission'}]}
        root, out = self.workspace()
        registered = write_set(root, {'d001': key})
        sessions, start = [], fixture_start()

        def recording(**arguments):
            sessions.append(Path(arguments['private_dir']))
            return start(**arguments)

        with patch(START, side_effect=recording):
            record = qualify(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
        self.assertEqual([row['rejected'] for row in record['mutants']], [True, True])
        expected = [out.resolve() / 'native' / run / name for run in record['seconds']
                    for name in ('session', 'session-emission')]
        self.assertEqual(sorted(sessions), sorted(expected))
        self.assertEqual(len(set(sessions)), len(sessions))

    def test_the_sample_is_the_first_two_devices_by_id_of_each_level_and_framing(self):
        devices = [{'id': identity, 'level': level, 'framing': framing} for identity, level, framing in (
            ('d005', 1, 'text-lines'), ('d001', 1, 'text-lines'), ('d003', 1, 'text-lines'), ('d007', 2, 'text-lines'),
            ('d002', 2, 'text-lines'), ('d006', 2, 'text-lines'), ('d004', 1, 'binary-frames'))]
        self.assertEqual(sample(devices), ['d001', 'd002', 'd003', 'd004', 'd006'])

    def test_nothing_runs_or_is_sealed_when_the_output_is_unsafe_or_a_device_cannot_be_opened(self):
        root, out = self.workspace()
        registered = write_set(root, {'d001': FIXTURE_ANSWER_KEY})
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
                ('another password', {'password': 'another credential'}, 'Cannot unlock')]
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
            registered = write_set(malformed, {'d001': {**FIXTURE_ANSWER_KEY, 'nonce': 'not hexadecimal'}})
            with patch(START) as start, self.assertRaisesRegex(ValueError, 'Invalid generated answer key'):
                qualify(malformed, SET, PASSWORD, renode=Path('/toy'), output_dir=out, registrations=registered)
            start.assert_not_called()
            self.assertFalse((malformed / 'qualification.enc').exists())


class CommandTests(Workspace, unittest.TestCase):
    def command(self, root, password_file, out):
        """`benchmark set qualify` as the command line runs it: (exit code, what it printed, what it logged)."""
        printed, logged = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(logged):
            code = main(['set', 'qualify', '--set', SET, '--root', str(root), '--password-file', str(password_file),
                         '--renode', '/toy', '--output', str(out)])
        return code, printed.getvalue(), logged.getvalue()

    def password_file(self, root):
        path = root.with_name('set.password')
        path.write_text(PASSWORD + '\n', encoding='utf-8')
        return path

    def test_the_command_refuses_a_set_the_shipped_table_does_not_register(self):
        root, out = self.workspace()
        write_fixture_set(root)
        with patch(START) as start:
            code, printed, logged = self.command(root, self.password_file(root), out)
        self.assertEqual((code, json.loads(printed)), (1, {'ok': False, 'error': 'Generated set not registered: ' + SET}))
        start.assert_not_called()
        self.assertNotIn(PASSWORD, printed + logged)
        self.assertFalse((root / 'qualification.enc').exists())

    def test_the_command_prints_the_status_and_each_runs_seconds_and_never_the_password(self):
        root, out = self.workspace()
        seconds = {'d001/reference-a': 1.25, 'd001/reference-b': 1.5}
        for status, failures, code in (('passed', [], 0), ('pending', ['reference b failed:d001'], 1)):
            with self.subTest(status), patch('generative_driver.benchmark_support.set_qualification.qualify',
                                             return_value={'status': status, 'failures': failures,
                                                           'seconds': seconds}) as qualifying:
                exit_code, printed, logged = self.command(root, self.password_file(root), out)
                # The password reaches the qualifier without its file's trailing newline.
                qualifying.assert_called_once_with(root, SET, PASSWORD, renode=Path('/toy'), output_dir=out)
                self.assertEqual((exit_code, json.loads(printed)),
                                 (code, {'ok': status == 'passed', 'status': status, 'failures': failures,
                                         'seconds': seconds}))
                self.assertNotIn(PASSWORD, printed + logged)


if __name__ == '__main__':
    unittest.main()

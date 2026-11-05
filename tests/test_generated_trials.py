"""Feature 03, generated trials: a suite of one generated set, whose harness builds each device's pin and whose
configurator only freezes it, runs each device through the six stages to a grade. Hand-written fixture devices behind
local sockets (`generated_fixtures`), scripted workers and synthetic credentials only; never Renode, a real set, a vault
or a model run."""
import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generated_fixtures import (FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, PASSWORD, PASSWORD_TEXT, REFERENCE_A, REFERENCE_B,
                                fixture_mission, fixture_start, registrations_table, write_fixture_set)
from generative_driver.benchmark_support import (evaluator, generated_sets, harness, registry, set_qualification,
                                                  snapshots)
from generative_driver.benchmark_support.families import family_for
from generative_driver.benchmark_support.registrations import registration_for
from generative_driver.benchmark_support.registry import PendingCaseError, pin_case
from generative_driver.benchmark_support.snapshots import canonical_digest
from generative_driver.benchmark_support.suites import (SCHEDULER, child_request, harness_driven, normalize_manifest,
                                                        preflight_selection, refuse_secrets, resume_suite, start_suite)
from generative_driver.benchmark_support.truth import UnlockedTruth
from suite_fixtures import drive_suite, public_case_fixture
from test_configurator import gateway_identity, scripted_stages, task_of
from test_evaluation import PendingRunFixture
from test_unlocked_keys import SealedTrialFixture

SET = 'fixture-v1'
SETS_FILE = 'generative_driver.benchmark_support.generated_sets._SETS_FILE'
START = 'generative_driver.benchmark_support.native.NativeSession.start'
EVIDENCE = 'e' * 64
_QUALIFIED = []


def qualified_set():
    """The fixture set, really qualified, once in this test process since that takes twelve emulator runs, and
    registered in a table for as long as the process's tests run: its root, its password file and its loaded manifest.
    No test changes it."""
    if not _QUALIFIED:
        directory = tempfile.TemporaryDirectory()
        unittest.addModuleCleanup(directory.cleanup)
        base = Path(directory.name).resolve()
        root = base / 'set'
        root.mkdir()
        (base / 'sets.json').write_text(registrations_table({SET: write_fixture_set(root)}))
        guard = patch(SETS_FILE, base / 'sets.json')
        guard.start()
        unittest.addModuleCleanup(guard.stop)
        with patch(START, side_effect=fixture_start(process=True)):
            record = set_qualification.qualify(root, SET, PASSWORD, renode=Path('/toy'),
                                               output_dir=base / 'qualification')
        assert record['status'] == 'passed', record['failures']
        (base / 'password-handle').write_text(PASSWORD_TEXT)
        _QUALIFIED.extend((root, base / 'password-handle', generated_sets.load_set(root, SET)))
    return tuple(_QUALIFIED)


def recorded_digest(root):
    """The digest of the set's qualification record, as a pin names it."""
    path = Path(root) / 'qualification.enc'
    return canonical_digest(PASSWORD.unlock(path, hashlib.sha256(path.read_bytes()).hexdigest()))


def set_manifest(*device_ids, **changes):
    """A suite manifest of set fixture-v1 over `device_ids`."""
    return {'schema': 'benchmark-suite/1', 'id': 'set-suite', 'version': '1', 'set': 'fixture-v1',
            'entries': [{'case': 'generated', 'scenario': 'original', 'case_seed': 0, 'device_id': device_id}
                        for device_id in device_ids or ('0000d001', '0000d002')],
            'repetitions': 1, 'child_budget_seconds': 120, 'suite_budget_seconds': 900, 'max_active_children': 1,
            **changes}


def handmade_manifest(**changes):
    return {'schema': 'benchmark-suite/1', 'id': 'handmade', 'version': '1',
            'entries': [{'case': 'tq9-v2', 'scenario': 'original', 'case_seed': 0}], 'repetitions': 2,
            'child_budget_seconds': 120, 'suite_budget_seconds': 900, 'max_active_children': 1, **changes}


class SuiteManifestTests(unittest.TestCase):
    def test_03_B1_a_manifest_of_one_set_names_its_devices(self):
        manifest = set_manifest()
        self.assertEqual(normalize_manifest(manifest), manifest)
        for name, value in (('a blank set', {'set': ' '}), ('a set that is no string', {'set': 7})):
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'nonblank'):
                normalize_manifest({**manifest, **value})
        for name, change, message in (
                ('a case of no set', {'case': 'tq9-v2'}, 'registered through a set'),
                ('an unknown case', {'case': 'unregistered'}, 'registered through a set'),
                ('another scenario', {'scenario': 'other'}, 'original'),
                ('another seed', {'case_seed': 1}, 'seed'),
                ('an id of another form', {'device_id': 'ABC'}, 'Invalid device id'),
                ('an id that is no string', {'device_id': 7}, 'Invalid device id')):
            changed = copy.deepcopy(manifest)
            changed['entries'][0].update(change)
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                normalize_manifest(changed)
        with self.assertRaisesRegex(ValueError, 'Repetitions'):
            normalize_manifest({**manifest, 'repetitions': 0})
        self.assertEqual(normalize_manifest({**manifest, 'repetitions': 2})['repetitions'], 2)

    def test_03_B1_a_manifest_without_a_set_normalizes_as_it_did(self):
        self.assertEqual(normalize_manifest(handmade_manifest()), handmade_manifest())
        self.assertNotIn('set', normalize_manifest(handmade_manifest()))

    def test_03_B2_what_a_set_manifest_and_a_handmade_one_refuse(self):
        with_id = handmade_manifest(entries=[{'case': 'tq9-v2', 'scenario': 'original', 'case_seed': 0,
                                              'device_id': '0000d001'}])
        generated = handmade_manifest(entries=[{'case': 'generated', 'scenario': 'original', 'case_seed': 0}])
        handmade_in_set = set_manifest(entries=[{'case': 'tq9-v2', 'scenario': 'original', 'case_seed': 0,
                                                 'device_id': '0000d001'}])
        no_id = set_manifest(entries=[{'case': 'generated', 'scenario': 'original', 'case_seed': 0}])
        twice = set_manifest('0000d001', '0000d001')
        for name, manifest, message in (('an id without a set', with_id, 'Invalid suite entry fields'),
                                        ('a generated entry without a set', generated, 'one set'),
                                        ('a hand-made case with a set', handmade_in_set, 'registered through a set'),
                                        ('an entry without an id', no_id, 'Invalid suite entry fields'),
                                        ('the same id twice', twice, 'Duplicate suite entry')):
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                normalize_manifest(manifest)


def registered_set(test):
    """The two-device fixture set under a new directory, registered in a table the test redirects to: (set root,
    manifest)."""
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    root = Path(directory.name).resolve() / 'set'
    root.mkdir()
    table = Path(directory.name) / 'sets.json'
    table.write_text(registrations_table({SET: write_fixture_set(root)}))
    guard = patch(SETS_FILE, table)
    guard.start()
    test.addCleanup(guard.stop)
    return root, generated_sets.load_set(root, SET)


def resources_with_case(test, root):
    """The tq9-v2 case of `public_case_fixture` under `root`, the case root patched to it: its (case file, image,
    truth)."""
    manifest = public_case_fixture(root, calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
    for target in ('generative_driver.benchmark.case_root',
                   'generative_driver.benchmark_support.case_assets.case_root'):
        guard = patch(target, return_value=root)
        guard.start()
        test.addCleanup(guard.stop)
    case = root / 'cases/tq9-v2'
    return case / 'case.json', case / 'firmware.bin', root / manifest['truth']['path']


class CurrentPinHookTests(unittest.TestCase):
    """Every existing case's pin is what `pin_case` makes of it, and each site refuses one that differs, as it always
    did."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.case, self.image, self.truth = resources_with_case(self, self.root / 'resources')
        self.pin = pin_case('tq9-v2', 'original', 0)

    def changes(self):
        """What can change a case after its pin: its manifest, image or truth file, each as (name, change, undo)."""
        def changing(path, content):
            kept = path.read_bytes()
            return lambda: path.write_bytes(content), lambda: path.write_bytes(kept)
        manifest = json.loads(self.case.read_text())
        return (('manifest', *changing(self.case, json.dumps({**manifest, 'limitations': ['changed']}).encode())),
                ('image', *changing(self.image, b'another image')),
                ('truth', *changing(self.truth, b'another truth')))

    def test_03_B9_a_family_without_the_hook_answers_with_pin_case(self):
        hook = family_for('tq9-v2').current_pin
        self.assertEqual(hook(self.pin), self.pin)
        self.assertEqual(hook(self.pin, check_truth=False), pin_case('tq9-v2', 'original', 0, check_truth=False))
        with patch.object(registry, 'pin_case', return_value='what pin_case gave') as made:
            self.assertEqual(hook(self.pin, check_truth=False), 'what pin_case gave')
        made.assert_called_once_with('tq9-v2', 'original', 0, check_truth=False)
        self.truth.unlink()
        self.assertEqual(hook(self.pin, check_truth=False), self.pin)
        with self.assertRaisesRegex(PendingCaseError, 'Case truth is unbuilt'):
            hook(self.pin)

    def test_03_B9_admission_refuses_a_changed_case_with_todays_errors(self):
        expected = {'manifest': 'Calibration admission case pin changed', 'image': 'Case image hash mismatch',
                    'truth': 'Case truth hash mismatch'}
        for name, change, undo in self.changes():
            change()
            with self.subTest(name), self.assertRaisesRegex(ValueError, expected[name]):
                registry.admitted_truth(self.pin, PASSWORD)
            undo()

    def test_03_B9_a_snapshot_refuses_a_changed_case_with_todays_errors(self):
        expected = {'manifest': 'Case pin changed before capture', 'image': 'Case image hash mismatch',
                    'truth': 'Case truth hash mismatch'}
        for name, change, undo in self.changes():
            change()
            with self.subTest(name), self.assertRaisesRegex(ValueError, expected[name]):
                snapshots.snapshot_case(self.root / 'runs' / name, self.pin, {})
            undo()

    def test_03_B9_grading_refuses_a_changed_case_but_not_a_changed_truth_file(self):
        run = self.root / 'graded-run'
        saved = snapshots.snapshot_case(run, self.pin, {'evaluator_revision': 'not this grader'})
        result = {'case_pin': self.pin, 'snapshot_sha256': saved['snapshot_sha256']}
        expected = {'manifest': (evaluator.IntegrityError if hasattr(evaluator, 'IntegrityError') else ValueError,
                                 'differs from the run'),
                    'image': (ValueError, 'Case image hash mismatch'),
                    # Past the pin, to the check that follows it: the truth file is not read.
                    'truth': (ValueError, 'evaluator revision')}
        for name, change, undo in self.changes():
            change()
            with self.subTest(name), self.assertRaisesRegex(expected[name][0], expected[name][1]):
                evaluator.accepted_driver('tq9-v2', self.root / 'trial', run, result)
            undo()
        with self.assertRaisesRegex(ValueError, 'differs from the run'):
            evaluator.accepted_driver('tq9-v3', self.root / 'trial', run, result)


class StartAndResumeSitesTests(PendingRunFixture):
    """The configurator's two sites, on the toy tq9-v2 case of the fixture."""

    def test_03_B9_a_start_refuses_a_pin_that_is_not_the_current_cases_or_not_the_starts(self):
        pin = pin_case('tq9-v2', 'original', 0)
        request = {'goal': 'A start', 'case': 'tq9-v2', 'case_pin': pin, 'executor': 'codex', 'executor_config': {},
                   'case_options': {'scenario_id': 'original', 'case_seed': 0}}
        message = 'Expected case pin differs from current case inputs'
        for name, changed in (('a changed pin', {**request, 'case_pin': {**pin, 'scope': 'changed'}}),
                              ('another case', {**request, 'case': 'tq9-v3'}),
                              ('another scenario',
                               {**request, 'case_options': {'scenario_id': 'other', 'case_seed': 0}}),
                              ('another seed',
                               {**request, 'case_options': {'scenario_id': 'original', 'case_seed': 1}})):
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                self.controller.call('start', changed)

    def test_03_B9_a_start_with_a_pin_refuses_a_seed_that_is_no_integer(self):
        request = {'goal': 'A start', 'case': 'tq9-v2', 'executor': 'codex', 'executor_config': {}}
        for name, pinned, seed in (('False over 0', 0, False), ('0.0 over 0', 0, 0.0), ('1.0 over 1', 1, 1.0)):
            options = {'scenario_id': 'original', 'case_seed': seed}
            with self.subTest(name), self.assertRaisesRegex(ValueError, '^case_seed must be an integer$'):
                self.controller.call('start', {**request, 'case_pin': pin_case('tq9-v2', 'original', pinned),
                                               'case_options': options})

    def test_03_B9_a_start_without_a_pin_refuses_a_changed_case_with_todays_errors(self):
        request = {'goal': 'A start', 'case': 'tq9-v2', 'executor': 'codex',
                   'case_options': {'scenario_id': 'original', 'case_seed': 0}}
        truth = self.root / 'resources/groundtruth/tq9-v2.enc'
        for name, path, message in (('image', self.image, 'Case image hash mismatch'),
                                    ('truth', truth, 'Case truth hash mismatch')):
            kept = path.read_bytes()
            path.write_bytes(b'changed since it was hashed')
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                self.controller.call('start', request)
            path.write_bytes(kept)
        with self.assertRaisesRegex(ValueError, '^case_seed must be an integer$'):
            self.controller.call('start', {**request, 'case_options': {'scenario_id': 'original', 'case_seed': False}})

    def test_03_B9_a_suite_resume_refuses_a_changed_case_with_todays_error(self):
        started = self.controller.call('suite_start', {
            'manifest': handmade_manifest(repetitions=1), 'executor': 'codex', 'options': {}, 'request_id': 'site',
            'harness_token_sha256': 'a' * 64, 'harness_pid': 1})
        self.assertTrue(started['ok'], started)
        self.controller._suites.set_state(started['suite_id'], 'blocked', 'for this test')
        self.controller._suites.detach_harness(started['suite_id'])
        case = self.image.parent / 'case.json'
        case.write_text(json.dumps({**json.loads(case.read_text()), 'limitations': ['changed']}))
        with self.assertRaisesRegex(ValueError, 'Frozen suite case pin changed'):
            self.controller.call('suite_resume', {'suite_id': started['suite_id'], 'harness_token_sha256': 'b' * 64,
                                                  'harness_pid': 1})


class DevicePinTests(unittest.TestCase):
    def setUp(self):
        self.root, self.manifest = registered_set(self)
        self.entries = {entry['id']: entry for entry in self.manifest['devices']}

    def pin(self, device_id='0000d001'):
        from generative_driver.benchmark_support.generated_trials import device_pin
        return device_pin(self.manifest, device_id, EVIDENCE)

    def test_03_B6_a_pin_holds_the_keys_of_pin_case_and_the_devices_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            resources = Path(temp).resolve()
            with patch('generative_driver.benchmark_support.case_assets.case_root', return_value=resources):
                public_case_fixture(resources, calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
                keys = set(pin_case('tq9-v2', 'original', 0))
        pin, entry = self.pin(), self.entries['0000d001']
        self.assertEqual(set(pin), keys | {'set', 'device_id'})
        policy = json.loads((Path(registry.__file__).resolve().parents[1]
                             / 'resources/bench/cases/parameter-store-v1/case.json').read_text())['time_policy']
        stages = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse']
        self.assertEqual(pin, {
            'schema': 'benchmark-case-pin/1', 'case': 'generated', 'case_id': 'generated', 'case_version': '1',
            'evaluator_version': '1', 'family': 'generated', 'scenario_id': 'original', 'case_seed': 0,
            'set': SET, 'device_id': '0000d001', 'manifest_sha256': self.manifest['manifest_sha256'],
            'truth_sha256': entry['answer']['sha256'], 'image_hashes': {'firmware.bin': entry['image_sha256']},
            'calibration': {'status': 'passed', 'evidence_sha256': EVIDENCE},
            'execution': 'actual-agent-emulation', 'evidence_track': 'firmware', 'scope': 'full-workflow',
            'time_policy': policy,
            'manifest': {'schema': 'benchmark-case/2', 'id': 'generated', 'family': 'generated', 'version': '1',
                         'evaluator_version': '1', 'execution': 'actual-agent-emulation', 'evidence_track': 'firmware',
                         'scope': 'full-workflow', 'scenarios': ['original'], 'required_stages': stages,
                         'images': {'firmware.bin': entry['image_sha256']},
                         'assets': {'mission.json': entry['mission_sha256']},
                         'truth': {'sha256': entry['answer']['sha256']}, 'default_effects': ['write', 'actuate']}})
        from generative_driver.benchmark_support.execution import artifact_name
        self.assertEqual(artifact_name(pin['manifest']), 'firmware.bin')
        registry.validate_pinned_workflow(pin)
        with self.assertRaisesRegex(ValueError, 'Unknown generated device'):
            self.pin('0000dfff')

    def test_03_B6_two_devices_pins_differ_only_in_id_and_hashes(self):
        def varying(pin):
            pin = copy.deepcopy(pin)
            for key in ('device_id', 'truth_sha256', 'image_hashes'):
                pin.pop(key)
            for key in ('images', 'assets', 'truth'):
                pin['manifest'].pop(key)
            return pin
        first, second = self.pin('0000d001'), self.pin('0000d002')
        self.assertNotEqual(first['truth_sha256'], second['truth_sha256'])
        self.assertNotEqual(first['manifest']['assets'], second['manifest']['assets'])
        self.assertEqual(varying(first), varying(second))
        self.assertEqual(generated_sets.canonical_digest(fixture_mission('0000d001')),
                         first['manifest']['assets']['mission.json'])

    def test_03_B10_the_hook_rebuilds_the_pin_and_checks_its_set_and_opens_no_file_of_it(self):
        hook = family_for('generated').current_pin
        pin = self.pin()
        shutil.rmtree(self.root)  # Nothing of the set can be read, as once the answer vault is unmounted.
        self.assertEqual(hook(pin), pin)
        self.assertEqual(hook(pin, check_truth=False), pin)
        # Another device's hash, under the right set's hash: only `admit`, which opens the set, can tell.
        other = copy.deepcopy(pin)
        other['truth_sha256'] = other['manifest']['truth']['sha256'] = 'f' * 64
        self.assertEqual(hook(other), other)

    def test_03_B10_the_hook_refuses_what_the_template_would_not_build(self):
        hook = family_for('generated').current_pin
        pin = self.pin()
        wrong_image = copy.deepcopy(pin)
        wrong_image['image_hashes']['firmware.bin'] = 'f' * 64
        bad_manifest = copy.deepcopy(pin)
        bad_manifest['manifest']['framing'] = 'text-lines'
        template, registered, field = 'differs from its template', 'does not register', 'lacks a field'
        for name, changed, message in (
                ('an extra level', {**pin, 'level': 1}, template),
                ('a wrong set hash', {**pin, 'manifest_sha256': 'f' * 64}, registered),
                ('an unregistered set', {**pin, 'set': 'another-set'}, registered),
                ('another scope', {**pin, 'scope': 'stage'}, template),
                ('the image hashes disagree', wrong_image, template),
                ('a framing in its manifest', bad_manifest, template),
                ('no calibration', {key: value for key, value in pin.items() if key != 'calibration'}, field),
                ('no image hashes', {**pin, 'image_hashes': None}, field)):
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                hook(changed)

    def test_03_B11_pin_case_still_refuses_a_generated_case(self):
        with self.assertRaises(PendingCaseError):
            pin_case('generated', 'original', 0)
        with self.assertRaises(PendingCaseError):
            registry.resolve_case('generated')

    def test_03_B11_benchmark_run_of_the_generated_case_stays_refused(self):
        import contextlib
        import io
        from generative_driver.benchmark import main
        printed = io.StringIO()
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(printed), \
                patch('generative_driver.benchmark_support.harness._spawn',
                      side_effect=AssertionError('a harness started')):
            code = main(['run', '--home', str(Path(temp) / 'home'), '--case', 'generated', '--executor', 'codex'])
        self.assertEqual(code, 1, printed.getvalue())
        self.assertIn('pending', json.loads(printed.getvalue())['error'])


class RowTests(unittest.TestCase):
    def test_03_B19_every_generated_trial_has_the_same_effects_and_approval_from_its_row(self):
        row = registration_for('generated')
        self.assertEqual((row.execution, row.evidence_track, row.approval_scope, row.default_effects),
                         ('actual-agent-emulation', 'firmware', 'emulator', ['write', 'actuate']))
        selected = registry.case_selection('generated')
        self.assertEqual((selected.id, selected.execution, selected.evidence_track, selected.approval_scope,
                          selected.default_effects),
                         ('generated', 'actual-agent-emulation', 'firmware', 'emulator', ['write', 'actuate']))
        from generative_driver.configurator import validate_scoped_approval
        validate_scoped_approval({'case': 'generated', 'scoped_tool_approval': 'emulator'})

    def test_03_B19_a_handmade_row_still_reads_its_changed_case(self):
        for case in ('tq9', 'tq9-v2'):
            with self.subTest(case), patch.object(registry, 'resolve_case', side_effect=ValueError('resolved')):
                with self.assertRaisesRegex(ValueError, 'resolved'):
                    registry.case_selection(case)
        from generative_driver.configurator import _replay_config, validate_scoped_approval
        with patch.object(registry, 'resolve_case', side_effect=ValueError('resolved')):
            with self.assertRaisesRegex(ValueError, 'resolved'):
                validate_scoped_approval({'case': 'tq9-v2', 'scoped_tool_approval': 'emulator'})
            with self.assertRaisesRegex(ValueError, 'resolved'):
                _replay_config({'case': 'tq9-v2'}, {}, Path('home'))


class SetRootOptionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.spawned = []

        def spawn(where, arguments, payload):
            self.spawned.append((arguments, payload))
            (Path(where) / 'suite.json').write_text(json.dumps({'status': 'failed', 'accepted': False, 'finished': 1,
                                                                'reason': 'stopped here'}))
            return type('Ended', (), {'poll': lambda self: 0})()
        guard = patch.object(harness, '_spawn', side_effect=spawn)
        guard.start()
        self.addCleanup(guard.stop)

    def options(self, **changes):
        return {'set_root': str(self.root / 'some-set'), 'evaluator_password_files': {SET: str(self.root / 'handle')},
                **changes}

    def test_03_B3_refused_anywhere_but_the_harness_stdin(self):
        from generative_driver.configurator import _refuse_secrets
        with self.assertRaisesRegex(ValueError, 'belong to the bench harness'):
            refuse_secrets({'set_root': 'a path'})
        for spec in ({'set_root': 'a path'}, {'case_options': {'set_root': 'a path'}},
                     {'case': {'id': 'generated', 'options': {'set_root': 'a path'}}}):
            with self.subTest(spec), self.assertRaisesRegex(ValueError, 'harness'):
                _refuse_secrets(spec)
        self.assertFalse(harness_driven({'scheduler': SCHEDULER, 'options': {'set_root': 'a path'}}))

    def test_03_B3_a_start_hands_the_root_to_the_harness_on_its_stdin(self):
        with patch('generative_driver.client.call', return_value={'ok': False}):
            refused = start_suite(set_manifest(), 'codex', self.options(), home=self.root / 'home', autostart=False)
        self.assertEqual(refused['ok'], False)
        arguments, payload = self.spawned[0]
        self.assertEqual(payload['set_root'], str(self.root / 'some-set'))
        self.assertEqual(payload['password_files'], {SET: str(self.root / 'handle')})
        self.assertNotIn('set_root', payload['options'])
        self.assertNotIn('some-set', json.dumps(arguments))

    def test_03_B3_a_resume_hands_it_over_the_same_way(self):
        refused = resume_suite('suite-1', self.options(), home=self.root / 'home', autostart=False)
        self.assertEqual(refused['ok'], False)
        arguments, payload = self.spawned[0]
        self.assertEqual((payload['suite_id'], payload['set_root']), ('suite-1', str(self.root / 'some-set')))
        self.assertNotIn('some-set', json.dumps(arguments))

    def test_03_B3_password_files_of_a_set_suite_are_keyed_by_its_set(self):
        with self.assertRaisesRegex(ValueError, 'must name selected'):
            harness.launch_suite({'manifest': set_manifest()}, home=self.root / 'home',
                                 password_files={'generated': str(self.root / 'handle')})
        for bad in (7, ' '):
            with self.subTest(bad), self.assertRaisesRegex(ValueError, 'set root must be a path'):
                harness.launch_suite({'manifest': set_manifest()}, home=self.root / 'home', set_root=bad)


class SuiteStartFixture(unittest.TestCase):
    """`run_suite` up to the configurator's first `suite_start`, which refuses: what it does before first contact."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        return {'ok': True} if method == 'ping' else {'ok': False, 'reason': 'the configurator stops this test here'}

    def run_suite(self, **request):
        self.directory = self.base / ('suite-' + str(len(list(self.base.iterdir()))))
        return harness.run_suite(self.directory,
                                 {'home': str(self.base / 'home'), 'manifest': set_manifest(), **request},
                                 call=self.call)

    def assertRefused(self, reason, **request):
        record = self.run_suite(**request)
        self.assertEqual(self.calls, [])
        self.assertEqual((record['status'], record['accepted'], record['reason']), ('failed', False, reason))
        self.assertEqual(json.loads((self.directory / 'suite.json').read_text())['reason'], reason)
        return record


class SuiteStartPinTests(SuiteStartFixture):
    def test_03_B4_every_entry_gets_its_pin_before_the_configurator_is_contacted(self):
        root, password, manifest = qualified_set()
        from generative_driver.benchmark_support.generated_trials import device_pin
        with patch.object(generated_sets, 'load_set', wraps=generated_sets.load_set) as loading:
            def call(method, params=None):
                self.assertTrue(loading.called)
                return self.call(method, params)
            harness.run_suite(self.base / 'suite', {'home': str(self.base / 'home'), 'manifest': set_manifest(),
                                                    'password_files': {SET: str(password)}, 'set_root': str(root)},
                              call=call)
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])
        digest = recorded_digest(root)
        self.assertEqual(self.calls[1][1]['device_pins'], {device: device_pin(manifest, device, digest)
                                                           for device in ('0000d001', '0000d002')})
        written = (self.base / 'suite/suite.json').read_text()
        self.assertNotIn(str(root), written)

    def test_03_B4_a_handmade_suite_sends_no_pins(self):
        harness.run_suite(self.base / 'suite', {'home': str(self.base / 'home'), 'manifest': handmade_manifest()},
                          call=self.call)
        self.assertNotIn('device_pins', self.calls[1][1])

    def test_03_B4_what_it_needs_before_any_contact(self):
        root, password, _ = qualified_set()
        prefix = 'The generated set ' + SET + ' is not admitted: '
        supplied = {'password_files': {SET: str(password)}, 'set_root': str(root)}
        self.assertRefused(prefix + 'no set root was supplied', password_files={SET: str(password)})
        self.assertRefused(prefix + 'no evaluator password file was supplied', set_root=str(root))
        self.assertRefused('The evaluator password file of ' + SET + ' cannot be read: FileNotFoundError',
                           password_files={SET: str(self.base / 'absent')}, set_root=str(root))
        self.assertRefused(prefix + 'Unknown generated device: 0000dfff', manifest=set_manifest('0000dfff'), **supplied)
        self.assertRefused(prefix + 'FileNotFoundError', password_files=supplied['password_files'],
                           set_root=str(self.base / 'no-such-set'))
        (self.base / 'wrong-handle').write_text('another credential')
        self.assertRefused(prefix + 'qualification unreadable: ValueError',
                           password_files={SET: str(self.base / 'wrong-handle')}, set_root=str(root))
        with patch.object(generated_sets, 'set_registrations', return_value={}):
            self.assertRefused(prefix + 'Generated set not registered: ' + SET, **supplied)
        with patch.object(generated_sets, 'admitted',
                          return_value=['qualification pending', 'references not passed:0000d001']):
            self.assertRefused(prefix + 'qualification pending; references not passed:0000d001', **supplied)

    def test_03_B4_an_unqualified_set_is_refused_with_the_reason_of_admitted(self):
        root, _ = registered_set(self)
        (self.base / 'handle').write_text(PASSWORD_TEXT)
        self.assertRefused('The generated set ' + SET + ' is not admitted: qualification unreadable: FileNotFoundError',
                           password_files={SET: str(self.base / 'handle')}, set_root=str(root))

    def test_03_B4_launch_suite_reports_the_reason_and_the_same_request_starts_afresh_once_fixed(self):
        root, password, _ = qualified_set()
        home = self.base / 'detached-home'
        request = {'manifest': set_manifest(), 'executor': 'codex', 'options': {}, 'request_id': 'fixed-later'}
        refused = harness.launch_suite(request, home=home, password_files={SET: str(password)}, autostart=False,
                                       timeout=60)
        self.assertEqual(refused['ok'], False)
        self.assertEqual(refused['reason'], 'The generated set ' + SET + ' is not admitted: no set root was supplied')
        self.assertFalse(home.exists())
        # The detached harness reads the table the test registered the set in.
        fixture = self.base / 'registered_harness_fixture.py'
        fixture.write_text('from pathlib import Path\nfrom generative_driver.benchmark_support import generated_sets\n'
                           'generated_sets._SETS_FILE = Path(' + repr(str(generated_sets._SETS_FILE)) + ')\n')
        with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
            again = harness.launch_suite(request, home=home, password_files={SET: str(password)}, set_root=str(root),
                                         autostart=False, timeout=60)
        self.assertEqual(again['ok'], False)
        self.assertNotIn('not admitted', again['reason'])
        self.assertNotEqual(again['suite_dir'], refused['suite_dir'])


def as_scoring_set(test):
    """The qualified set, registered for this test in a second table with the same hash and the mark `scoring`: the
    table's path."""
    _, _, manifest = qualified_set()
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    table = Path(directory.name) / 'sets.json'
    table.write_text(registrations_table({SET: manifest['manifest_sha256']}, scoring=True))
    guard = patch(SETS_FILE, table)
    guard.start()
    test.addCleanup(guard.stop)
    return table


def resealed_set(test, change):
    """A copy of the qualified set, under the same registered manifest, its record altered by `change` and resealed."""
    source, _, _ = qualified_set()
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    root = Path(directory.name).resolve() / 'set'
    shutil.copytree(source, root)
    path = root / 'qualification.enc'
    record = PASSWORD.unlock(path, hashlib.sha256(path.read_bytes()).hexdigest())
    change(record)
    generated_sets.seal_qualification(root, record, PASSWORD)
    return root


def no_native_process(record):
    for row in record['runs'].values():
        row['native_process_observed'] = False


class WholeRecordRefusalTests(SuiteStartFixture):
    def test_04_B6_a_suite_on_a_set_with_such_a_reason_refuses_before_any_contact(self):
        _, password, _ = qualified_set()

        def failure(record):
            record['failures'].append('/private/path')
        prefix = 'The generated set ' + SET + ' is not admitted: '
        self.assertRefused(prefix + 'native process not observed:0000d001; native process not observed:0000d002',
                           password_files={SET: str(password)}, set_root=str(resealed_set(self, no_native_process)))
        self.assertRefused(prefix + 'qualification failures recorded', password_files={SET: str(password)},
                           set_root=str(resealed_set(self, failure)))

    def test_04_B6_each_slots_admit_refuses_the_same_way(self):
        from generative_driver.benchmark_support.generated_trials import SetDevice, device_pin
        root = resealed_set(self, no_native_process)
        _, _, manifest = qualified_set()
        trial = self.base / 'trial'
        trial.mkdir()
        with patch.object(generated_sets, 'open_device', side_effect=AssertionError('opened')), \
                self.assertRaisesRegex(ValueError, 'is not admitted: native process not observed:0000d001; '
                                                   'native process not observed:0000d002'):
            harness.admit('generated', {}, trial, password=PASSWORD, renode='emulator-handle',
                          device=SetDevice(root, SET, '0000d001'), pin=device_pin(manifest, '0000d001', EVIDENCE))
        self.assertEqual(list(trial.iterdir()), [])


class ScoringSuiteStartTests(SuiteStartFixture):
    REFUSED = 'The generated set ' + SET + ' is not admitted: a scoring suite runs every device of its set once'

    def supplied(self):
        root, password, _ = qualified_set()
        return {'password_files': {SET: str(password)}, 'set_root': str(root)}

    def test_04_B9_a_scoring_suite_must_name_every_device(self):
        as_scoring_set(self)
        self.assertRefused(self.REFUSED, manifest=set_manifest('0000d001'), **self.supplied())

    def test_04_B9_a_scoring_suite_runs_once(self):
        as_scoring_set(self)
        self.assertRefused(self.REFUSED, manifest=set_manifest(repetitions=2), **self.supplied())

    def test_04_B9_the_same_device_twice_is_refused_before_contact_as_for_any_set(self):
        # No RED of its own: `normalize_manifest` refuses it for every set, in its own form.
        as_scoring_set(self)
        self.assertRefused('Duplicate suite entry', manifest=set_manifest('0000d001', '0000d001', '0000d002'),
                           **self.supplied())

    def test_04_B9_every_device_once_starts(self):
        as_scoring_set(self)
        self.run_suite(**self.supplied())
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])

    def test_04_B9_a_development_set_may_run_any_devices_any_number_of_times(self):
        self.run_suite(manifest=set_manifest('0000d002', repetitions=3), **self.supplied())
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])


class FixtureVariableTests(SuiteStartFixture):
    """A test's own table stands for the shipped file, which is never edited."""
    VARIABLE = {'GD_TEST_HARNESS_FIXTURE': 'a fixture file'}

    def setUp(self):
        super().setUp()
        from generative_driver.benchmark_support import generated_trials
        self.trials = generated_trials
        self.root, self.handle, self.manifest = qualified_set()
        self.shipped = self.base / 'shipped-sets.json'
        self.shipped.write_text(registrations_table({SET: self.manifest['manifest_sha256']}))
        self.supplied = {'password_files': {SET: str(self.handle)}, 'set_root': str(self.root)}

    def test_04_B13_with_the_variable_a_shipped_set_has_a_reason_that_names_it(self):
        with patch.dict(os.environ, self.VARIABLE):
            reasons = self.trials.fixture_variable_reasons(SET, self.shipped)
            self.assertEqual(len(reasons), 1)
            self.assertIn('GD_TEST_HARNESS_FIXTURE', reasons[0])
            self.assertEqual(self.trials.fixture_variable_reasons('another-set', self.shipped), [])

    def test_04_B13_without_the_variable_it_has_none(self):
        with patch.dict(os.environ):
            os.environ.pop('GD_TEST_HARNESS_FIXTURE', None)
            self.assertEqual(self.trials.fixture_variable_reasons(SET, self.shipped), [])

    def test_04_B13_it_reads_the_shipped_file_by_the_packages_own_path_and_not_through_the_table_a_test_redirects(self):
        # `_SETS_FILE` registers the set here; the shipped file does not.
        self.assertIn(SET, generated_sets.set_registrations())
        shipped = Path(generated_sets.__file__).resolve().parents[1] / 'resources/registrations/sets.json'
        self.assertEqual(self.trials._SHIPPED_SETS, shipped)
        with patch.dict(os.environ, self.VARIABLE):
            self.assertEqual(self.trials.fixture_variable_reasons(SET), [])

    def test_04_B13_a_suite_on_a_shipped_set_refuses_at_its_start(self):
        with patch.dict(os.environ, self.VARIABLE), patch.object(self.trials, '_SHIPPED_SETS', self.shipped):
            record = self.run_suite(**self.supplied)
        self.assertEqual(self.calls, [])
        self.assertEqual(record['status'], 'failed')
        self.assertRegex(record['reason'], '^The generated set ' + SET + ' is not admitted: .*GD_TEST_HARNESS_FIXTURE')

    def test_04_B13_a_slots_admit_refuses_a_shipped_set(self):
        trial = self.base / 'trial'
        trial.mkdir()
        device = self.trials.SetDevice(self.root, SET, '0000d001')
        pin = self.trials.device_pin(self.manifest, '0000d001', recorded_digest(self.root))
        with patch.dict(os.environ, self.VARIABLE), patch.object(self.trials, '_SHIPPED_SETS', self.shipped), \
                patch.object(generated_sets, 'open_device', side_effect=AssertionError('opened')), \
                self.assertRaisesRegex(ValueError, 'is not admitted: .*GD_TEST_HARNESS_FIXTURE'):
            harness.admit('generated', {}, trial, password=PASSWORD, renode='emulator-handle', device=device, pin=pin)
        self.assertEqual(list(trial.iterdir()), [])

    def test_04_B13_a_set_outside_the_shipped_list_runs_as_it_did(self):
        with patch.dict(os.environ, self.VARIABLE):
            self.run_suite(**self.supplied)
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])


class SuiteResumeTests(unittest.TestCase):
    """A resumed set suite: each slot needs its device and password only when it provides or grades."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.root, self.password, self.manifest = qualified_set()
        from generative_driver.benchmark_support.generated_trials import device_pin
        self.pin = device_pin(self.manifest, '0000d001', recorded_digest(self.root))

    def resume(self, status, **request):
        """A harness resuming a suite whose one slot has a child in `status`: its record."""
        reservations = [{'ok': True, 'status': 'running', 'slot': {
            'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': 'generated', 'child_request_id': 'child',
            'run_id': 'run-1', 'device_id': '0000d001'}, 'request': {'budget_seconds': 1, 'case_pin': self.pin}},
            {'ok': True, 'status': 'completed', 'slot': None}]

        def call(method, params=None):
            return {'ping': {'ok': True}, 'suite_resume': {'ok': True, 'suite_id': 'suite-1', 'attached': True},
                    'status': {'ok': True, 'stopping': False, **status},
                    'suite_settle': {'ok': True, 'decision': 'advance'}}.get(method) or (
                        reservations.pop(0) if method == 'suite_reserve' else {'ok': True})
        return harness.run_suite(self.base / 'suite',
                                 {'home': str(self.base / 'home'), 'suite_id': 'suite-1', **request}, call=call)

    def test_03_B5_a_resume_loads_no_set_before_contact_and_settles_recorded_slots_without_either(self):
        with patch.object(generated_sets, 'load_set', side_effect=AssertionError('the set was loaded')):
            for name, status in (('a recorded verdict', {'status': 'completed', 'equivalence': {'status': 'graded'}}),
                                 ('an uncertain effect', {'status': 'blocked', 'uncertain_effect': True})):
                with self.subTest(name):
                    record = self.resume(status)
                    self.assertEqual(record['status'], 'finished', record)

    def test_03_B5_a_slot_that_needs_its_device_fails_only_there_for_what_is_missing(self):
        needs = {'status': 'completed', 'equivalence': None}
        record = self.resume(needs, set_root=str(self.root))
        self.assertEqual((record['status'], record['accepted']), ('failed', True), record)
        self.assertEqual(record['reason'],
                         'The evaluator password file of ' + SET + ' is required to provide and grade its device')
        record = self.resume(needs, password_files={SET: str(self.password)})
        self.assertEqual((record['status'], record['accepted']), ('failed', True), record)
        self.assertEqual(record['reason'], 'The set root of ' + SET + ' is required to provide and grade its device')

    def test_03_B5_a_slot_builds_its_device_from_its_frozen_pin_and_its_password_by_set(self):
        from generative_driver.benchmark_support.generated_trials import SetDevice
        needs = {'status': 'completed', 'equivalence': None}
        with patch.object(harness, '_trial_of', return_value=self.base / 'trial'), patch.object(harness, '_adopt'), \
                patch.object(harness, 'resume', return_value={'status': 'evaluated'}) as resumed:
            record = self.resume(needs, password_files={SET: str(self.password)}, set_root=str(self.root))
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(resumed.call_args.kwargs['device'], SetDevice(self.root, SET, '0000d001'))
        self.assertEqual(resumed.call_args.args, (self.base / 'trial',))


class SetSuiteFixture(PendingRunFixture):
    def setUp(self):
        super().setUp()
        self.set_root, self.handle, self.manifest = qualified_set()
        from generative_driver.benchmark_support.generated_trials import device_pin
        self.pins = {device: device_pin(self.manifest, device, EVIDENCE) for device in ('0000d001', '0000d002')}

    def suite_start(self, device_pins, **changes):
        return self.controller.call('suite_start', {
            'manifest': set_manifest(), 'executor': 'codex', 'options': {}, 'request_id': 'frozen',
            'harness_pid': os.getpid(), 'harness_token_sha256': 'a' * 64,
            **({} if device_pins is None else {'device_pins': device_pins}), **changes})


class SuiteFreezeTests(SetSuiteFixture):
    def test_03_B8_the_configurator_freezes_the_pins_it_is_sent_and_starts_each_child_from_its_own(self):
        started = self.suite_start(self.pins)
        self.assertTrue(started['ok'], started)
        suite = self.controller._suites.get(started['suite_id'])
        frozen = suite['frozen']
        self.assertEqual(frozen['device_pins'], self.pins)
        self.assertEqual(frozen['entry_pins'], [self.pins['0000d001'], self.pins['0000d002']])
        self.assertEqual(frozen['entry_effects'], [['write', 'actuate'], ['write', 'actuate']])
        for slot, device in zip(suite['trials'], ('0000d001', '0000d002')):
            request = child_request(frozen, {**slot, 'child_request_id': 'child'})
            self.assertEqual(request['case_pin'], self.pins[device])
            self.assertEqual(request['effects'], ['write', 'actuate'])
        # The same request with other pins is another request.
        again = {**self.pins, '0000d001': device_pin_with(self.pins['0000d001'], evidence='f' * 64)}
        with self.assertRaisesRegex(ValueError, 'different suite inputs'):
            self.suite_start(again)

    def test_03_B8_it_refuses_pins_that_are_not_exactly_the_entries_or_that_the_hook_refuses(self):
        one, two = self.pins['0000d001'], self.pins['0000d002']
        for name, pins, message in (
                ('no pins', None, 'one device pin per entry'),
                ('a missing pin', {'0000d001': one}, 'one device pin per entry'),
                ('one too many', {**self.pins, '0000d003': two}, 'one device pin per entry'),
                ('another device under its id', {'0000d001': two, '0000d002': two}, 'another device'),
                ('a level', {'0000d001': {**one, 'level': 1}, '0000d002': two}, 'differs from its template'),
                ('another set hash', {'0000d001': {**one, 'manifest_sha256': 'f' * 64}, '0000d002': two},
                 'differs|does not register'),
                ('no object', {'0000d001': 'a pin', '0000d002': two}, 'another device')):
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                self.suite_start(pins, request_id='refused-' + name)
        with self.assertRaisesRegex(ValueError, 'belong to a set suite'):
            self.suite_start(self.pins, manifest=handmade_manifest(), request_id='handmade')

    def test_03_B8_with_no_configurator_only_the_selection_is_checked(self):
        with patch.object(generated_sets, 'load_set', side_effect=AssertionError('a file of the set was read')), \
                patch.object(registry, 'resolve_case', side_effect=AssertionError('no case file to read')):
            normalized, definitions, pins = preflight_selection(set_manifest())
        self.assertEqual(normalized, set_manifest())
        self.assertEqual(pins, [])
        self.assertEqual([case.id for case in definitions], ['generated', 'generated'])


class SetTableAfterFreezeTests(SetSuiteFixture):
    """A suite frozen on a copy of the package whose own `sets.json` registers the set, so that a change to that file
    reaches both the fingerprint and `current_pin`, as it would on an installation. The shipped file is never edited."""

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        package = Path(temporary.name).resolve() / 'generative_driver'
        source = Path(snapshots.__file__).resolve().parents[1]
        for name, target in ((source, package),
                             (source.parent / 'interface_runtime', package.parent / 'interface_runtime')):
            shutil.copytree(name, target, ignore=shutil.ignore_patterns('__pycache__'))
        self.package = package
        self.table = package / 'resources/registrations/sets.json'
        self.write_table({SET: {'manifest_sha256': self.manifest['manifest_sha256'], 'scoring': False}})
        for guard in (patch(SETS_FILE, self.table),
                      patch.object(snapshots, '__file__', str(package / 'benchmark_support/snapshots.py'))):
            guard.start()
            self.addCleanup(guard.stop)

    def write_table(self, sets):
        self.table.write_text(json.dumps({'schema': 'generated-set-registrations/2', 'sets': sets}))

    def resume(self, suite_id):
        self.controller._suites.set_state(suite_id, 'blocked', 'for this test')
        self.controller._suites.detach_harness(suite_id)
        return self.controller.call('suite_resume', {'suite_id': suite_id, 'harness_token_sha256': 'b' * 64,
                                                     'harness_pid': 1})

    def test_04_B2_a_change_to_another_set_or_any_mark_does_not_block_a_resume(self):
        started = self.suite_start(self.pins)
        self.assertTrue(started['ok'], started)
        own = {'manifest_sha256': self.manifest['manifest_sha256']}
        for name, sets in (('another set added', {SET: {**own, 'scoring': False},
                                                  'other-v1': {'manifest_sha256': 'b' * 64, 'scoring': False}}),
                           ('another set changed', {SET: {**own, 'scoring': False},
                                                    'other-v1': {'manifest_sha256': 'c' * 64, 'scoring': True}}),
                           ('its own mark changed', {SET: {**own, 'scoring': True}})):
            self.write_table(sets)
            with self.subTest(name):
                self.assertTrue(self.resume(started['suite_id'])['ok'])

    def test_04_B2_a_change_to_its_own_hash_refuses_the_resume_through_current_pin(self):
        started = self.suite_start(self.pins)
        self.assertTrue(started['ok'], started)
        self.write_table({SET: {'manifest_sha256': 'f' * 64, 'scoring': False}})
        with self.assertRaisesRegex(ValueError, 'names a manifest its set does not register: ' + SET):
            self.resume(started['suite_id'])

    def test_04_B2_another_change_under_the_package_still_refuses_the_resume(self):
        started = self.suite_start(self.pins)
        self.assertTrue(started['ok'], started)
        (self.package / 'resources/another.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Frozen suite implementation or execution environment changed'):
            self.resume(started['suite_id'])


class ScoringFreezeTests(SetSuiteFixture):
    def test_04_B10_a_scoring_suite_freezes_its_set_and_its_report_names_it(self):
        table = as_scoring_set(self)
        started = self.suite_start(self.pins)
        self.assertTrue(started['ok'], started)
        scoring = {'registration': SET, 'manifest_sha256': self.manifest['manifest_sha256']}
        self.assertEqual(self.controller._suites.get(started['suite_id'])['frozen']['scoring_set'], scoring)

        def identity():
            result = self.controller.call('suite_result', {'suite_id': started['suite_id']})
            return result['experiment']['comparison_identity']
        self.assertEqual(identity()['scoring_set'], scoring)
        # A later change to the table changes nothing frozen.
        pins = {SET: self.manifest['manifest_sha256'], 'other-v1': 'b' * 64}
        table.write_text(registrations_table(pins, scoring=False))
        self.assertEqual(self.controller._suites.get(started['suite_id'])['frozen']['scoring_set'], scoring)
        self.assertEqual(identity()['scoring_set'], scoring)
        table.write_text(registrations_table({SET: 'c' * 64}, scoring=True))
        self.assertEqual(self.controller._suites.get(started['suite_id'])['frozen']['scoring_set'], scoring)

    def test_04_B10_a_development_suite_has_no_such_key(self):
        # No RED of its own: this pins what must stay.
        started = self.suite_start(self.pins)
        self.assertTrue(started['ok'], started)
        self.assertNotIn('scoring_set', self.controller._suites.get(started['suite_id'])['frozen'])
        result = self.controller.call('suite_result', {'suite_id': started['suite_id']})
        self.assertNotIn('scoring_set', result['experiment']['comparison_identity'])

    def test_04_B10_a_handmade_suite_has_no_such_key(self):
        # No RED of its own: this pins what must stay.
        started = self.suite_start(None, manifest=handmade_manifest(repetitions=1), request_id='handmade')
        self.assertTrue(started['ok'], started)
        self.assertNotIn('scoring_set', self.controller._suites.get(started['suite_id'])['frozen'])


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.trial = self.base / 'trial'
        self.trial.mkdir()

    def qualified(self, device_id='0000d001'):
        from generative_driver.benchmark_support.generated_trials import SetDevice, device_pin
        root, _, manifest = qualified_set()
        return root, manifest, SetDevice(root, SET, device_id), device_pin(manifest, device_id, recorded_digest(root))

    def admit(self, device, pin):
        return harness.admit('generated', {}, self.trial, password=PASSWORD, renode='emulator-handle', device=device,
                             pin=pin)

    def test_03_B12_a_device_of_a_set_not_admitted_is_refused_with_every_reason(self):
        from generative_driver.benchmark_support.generated_trials import SetDevice, device_pin
        root, manifest = registered_set(self)
        pin = device_pin(manifest, '0000d001', EVIDENCE)
        with self.assertRaisesRegex(ValueError, 'is not admitted: qualification unreadable: FileNotFoundError'):
            self.admit(SetDevice(root, SET, '0000d001'), pin)
        with patch.object(generated_sets, 'admitted',
                          return_value=['the first reason', 'the second reason']) as asked, \
                patch.object(generated_sets, 'open_device', side_effect=AssertionError('opened')), \
                self.assertRaisesRegex(ValueError, 'the first reason; the second reason'):
            self.admit(SetDevice(root, SET, '0000d001'), pin)
        self.assertEqual(asked.call_args.args, (root, SET, '0000d001', PASSWORD))
        self.assertEqual(sorted(path.name for path in self.trial.iterdir()), [])

    def test_03_B13_it_opens_the_device_once_and_returns_the_grade_its_answer_key(self):
        root, manifest, device, pin = self.qualified()
        from generative_driver.benchmark_support import execution
        with patch.object(generated_sets, 'open_device', wraps=generated_sets.open_device) as opened, \
                patch.object(execution, 'validate_setup', wraps=execution.validate_setup) as validated:
            provider, admission, unlocked = self.admit(device, pin)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.kwargs, {'answer': True})
        self.assertEqual(validated.call_count, 1)
        self.assertEqual(admission, {'calibration_evidence_sha256': recorded_digest(root)})
        entry = next(entry for entry in manifest['devices'] if entry['id'] == '0000d001')
        self.assertIsInstance(unlocked, UnlockedTruth)
        self.assertEqual((unlocked.payload, unlocked.sha256), (FIXTURE_ANSWER_KEY, entry['answer']['sha256']))
        self.assertEqual(provider.recipe, FIXTURE_ANSWER_KEY['recipe'])
        self.assertEqual(provider.image_sha256, entry['image_sha256'])

    def test_03_B13_it_refuses_a_pin_whose_hashes_are_not_its_devices(self):
        root, manifest, device, pin = self.qualified()
        other = self.qualified('0000d002')[3]
        wrong_truth = {**other, 'device_id': '0000d001'}
        with patch.object(generated_sets, 'open_device', side_effect=AssertionError('opened')), \
                self.assertRaisesRegex(ValueError, 'differs from its set'):
            self.admit(device, wrong_truth)
        self.assertEqual(sorted(path.name for path in self.trial.iterdir()), [])

    def test_03_B14_it_writes_what_a_worker_receives_and_the_provider_starts_from_that_image(self):
        root, manifest, device, pin = self.qualified()
        for attempt in ('first', 'again'):  # A reattachment writes the folder again.
            with self.subTest(attempt):
                provider = self.admit(device, pin)[0]
        folder = self.trial / 'public-inputs'
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['firmware.bin', 'mission.json'])
        self.assertEqual((folder / 'firmware.bin').read_bytes(), FIXTURE_IMAGE)
        mission = (folder / 'mission.json').read_bytes()
        self.assertEqual(json.loads(mission), fixture_mission('0000d001'))
        self.assertEqual(hashlib.sha256(mission).hexdigest(), pin['manifest']['assets']['mission.json'])
        started = []
        real = fixture_start(process=True)

        def start(**kwargs):
            started.append(kwargs['image'])
            return real(**kwargs)
        with patch(START, side_effect=start):
            provider.start()
            self.addCleanup(provider.stop)
        self.assertEqual(started, [folder / 'firmware.bin'])
        self.assertEqual(provider.stop()['confirmed'], True)

    def test_03_B13_the_answer_key_of_a_resume_is_its_devices(self):
        from generative_driver.benchmark_support.generated_trials import answer_key
        root, manifest, device, pin = self.qualified('0000d002')
        unlocked = answer_key(device, PASSWORD)
        self.assertEqual((unlocked.payload, unlocked.sha256), (FIXTURE_ANSWER_KEY, pin['truth_sha256']))


class SnapshotSourceTests(unittest.TestCase):
    def setUp(self):
        from generative_driver.benchmark_support.generated_trials import device_pin
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        _, _, manifest = qualified_set()
        self.pin = device_pin(manifest, '0000d001', EVIDENCE)
        self.folder = self.base / 'public-inputs'
        self.folder.mkdir()
        (self.folder / 'firmware.bin').write_bytes(FIXTURE_IMAGE)
        (self.folder / 'mission.json').write_bytes(json.dumps(fixture_mission('0000d001'), sort_keys=True,
                                                              separators=(',', ':'), ensure_ascii=False).encode())

    def test_03_B15_the_snapshot_copies_from_the_folder_the_harness_sends(self):
        from generative_driver.benchmark_support.case_assets import case_inputs
        run = self.base / 'run'
        saved = snapshots.snapshot_case(run, self.pin, {}, public_inputs=str(self.folder))
        self.assertEqual(saved['public_files'], {**self.pin['image_hashes'], **self.pin['manifest']['assets']})
        shutil.rmtree(self.folder)
        inputs, manifest = case_inputs('generated', run)
        self.assertEqual(inputs, run / 'benchmark/inputs')
        self.assertEqual((inputs / 'firmware.bin').read_bytes(), FIXTURE_IMAGE)
        self.assertEqual(manifest, self.pin['manifest'])

    def test_03_B15_it_refuses_a_file_that_is_not_the_pins(self):
        for name in ('firmware.bin', 'mission.json'):
            kept = (self.folder / name).read_bytes()
            (self.folder / name).write_bytes(b'another file')
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'Snapshot source hash mismatch: ' + name):
                snapshots.snapshot_case(self.base / ('run-' + name), self.pin, {}, public_inputs=str(self.folder))
            (self.folder / name).write_bytes(kept)

    def test_03_B15_a_case_with_no_folder_copies_from_its_case_root(self):
        with self.assertRaises(PendingCaseError):
            snapshots.snapshot_case(self.base / 'no-folder', self.pin, {})
        resources = self.base / 'resources'
        resources_with_case(self, resources)
        pin = pin_case('tq9-v2', 'original', 0)
        saved = snapshots.snapshot_case(self.base / 'handmade', pin, {})
        self.assertEqual(saved['public_files'], pin['image_hashes'])
        self.assertEqual((self.base / 'handmade/benchmark/inputs/firmware.bin').read_bytes(),
                         (resources / 'cases/tq9-v2/firmware.bin').read_bytes())


class AdapterTests(unittest.TestCase):
    def test_03_B16_adapter_for_reads_the_registration_and_opens_no_file(self):
        from generative_driver.benchmark_support import adapters, emulated
        opened = []
        real = Path.read_bytes

        def read_bytes(path):
            opened.append(path)
            return real(path)
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'read_bytes', read_bytes):
            resources = Path(temp).resolve()
            resources_with_case(self, resources)
            (resources / 'cases/tq9-v2/case.json').write_text('{"a changed": "manifest"}')
            (resources / 'cases/tq9-v2/firmware.bin').unlink()
            opened.clear()
            self.assertIs(adapters.adapter_for('tq9-v2'), emulated)
            self.assertIs(adapters.adapter_for('generated'), emulated)
        self.assertEqual(opened, [])
        with self.assertRaisesRegex(ValueError, 'Unknown benchmark case'):
            adapters.adapter_for('unregistered')


class GeneratedTrialFixture(PendingRunFixture):
    """An in-process configurator and the suite's harness in a thread, with a scripted worker through the real
    `_prepare` and `_check`, on the qualified fixture set. Nothing of admission, the provider or the grade is patched
    but the emulator session, each a fresh fixture device."""

    def setUp(self):
        super().setUp()
        self.set_root, self.handle, self.manifest = qualified_set()
        self.prepared, self.checked, self.results = {}, {}, []
        guard = patch(START, side_effect=fixture_start(process=True))
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.join_harnesses)

    def join_harnesses(self):
        self.assertTrue(self.controller.close(timeout=60), 'Test workers still own files at cleanup')
        for thread in getattr(self.controller, '_test_harness_threads', []):
            thread.join(60)
            self.assertFalse(thread.is_alive(), 'Test harness still owns files at cleanup')

    def tool(self, request, name, arguments):
        result = self.controller.call('tool', {**gateway_identity(request), 'name': name, 'arguments': arguments})
        self.results.append((request.stage, name, result))
        return result

    def work(self, candidate, *, after_acquire=None, reuse=True):
        """A scripted worker whose driver is `candidate`: it hands on its model and mapping and calls its first task."""
        import asyncio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from package_env import package_env
        operation = candidate['capabilities']['tasks']['get_mode']['operation']

        def work(request):
            workspace = Path(request.workspace)
            if request.stage == 'interpret':
                (workspace / 'model.json').write_text(json.dumps(candidate['model']))
                return
            task = task_of(request.prompt)
            if request.stage == 'acquire':
                self.tool(request, 'acquire_firmware_artifact', task['context'])
                if after_acquire:
                    after_acquire()
            elif request.stage == 'probe':
                measure = {'model_dir': task['context']['model_dir'], 'operation': operation, 'parameters': {}}
                self.assertTrue(self.tool(request, 'interface_execute', measure)['ok'])
                self.assertTrue(self.tool(request, 'probe_run', {**measure, 'n': 1})['ok'])
                (workspace / 'capabilities.json').write_text(json.dumps(candidate['capabilities']))
            elif request.stage == 'emit':
                self.assertTrue(self.tool(request, 'emit_package', task['context'])['ok'])
            elif request.stage == 'reuse' and reuse:
                async def call_it():
                    parameters = StdioServerParameters(command=request.gateway['command'], args=request.gateway['args'],
                                                       env=package_env())
                    async with stdio_client(parameters) as streams:
                        async with ClientSession(*streams) as client:
                            await client.initialize()
                            called = await client.call_tool('device_' + operation, {
                                '_request_id': gateway_identity(request)['assignment_id'] + ':' + operation})
                            self.assertTrue(called.structured_content['ok'], called)
                asyncio.run(call_it())
        return work

    def staged(self, candidate, *, runtime=None, **options):
        """The scripted stages of a worker with `candidate`, recording what the real `_prepare` and `_check`
        returned."""
        from owner_ipc_fixture import owner_ipc
        from contextlib import ExitStack
        real_check = self.controller._check

        def prepare(original, spec, stage, run_dir, workspace, accepted):
            self.prepared[stage] = original(spec, stage, run_dir, workspace, accepted)
            return self.prepared[stage]

        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            self.checked[stage] = real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
            return self.checked[stage]
        stack = ExitStack()
        stack.enter_context(owner_ipc(self.controller))
        stack.enter_context(scripted_stages(self.controller, check=check, prepare=prepare, runtime=runtime,
                                            work=self.work(candidate, **options)))
        return stack

    def drive(self, *devices, calls=None, intercept=None, **request):
        return drive_suite(self.controller, self.root / 'home-bench/suites', {
            'manifest': set_manifest(*devices), 'executor': 'codex', 'options': {'scoped_tool_approval': 'emulator'},
            'password_files': {SET: str(self.handle)}, 'set_root': str(self.set_root), 'renode': 'emulator-handle',
            **request},
            calls=calls, intercept=intercept)

    def row_results(self, suite_id):
        rows = self.controller.call('suite_result', {'suite_id': suite_id})['trials']
        return rows, [self.controller.call('result', {'run_id': row['run_id']}) for row in rows]


class GradedTrialTests(GeneratedTrialFixture):
    def test_03_B17_a_stage_reads_the_devices_mission_from_the_runs_snapshot(self):
        with self.staged(REFERENCE_A):
            record = self.drive('0000d002', request_id='mission')
        self.assertEqual(record['status'], 'finished', record)
        result = self.row_results(record['suite_id'])[1][0]
        self.assertEqual(result['status'], 'completed', result['reason'])
        mission = fixture_mission('0000d002')
        self.assertIn(mission['reuse_objective'], self.prepared['reuse']['objective'])
        self.assertIn('tasks get_mode, ping, set_mode.', self.prepared['probe']['objective'])
        tasks = next(path for path in self.prepared['probe']['inputs'] if path.endswith('TASKS.json'))
        self.assertEqual(json.loads(Path(tasks).read_text())['tasks'], mission['tasks'])
        mapping = next(check for check in self.checked['probe']['checks'] if check['name'] == 'capability_mapping')
        self.assertEqual(mapping['passed'], True, mapping)

    def test_03_B20_a_reference_driver_passes_on_the_grade(self):
        for name, candidate in (('a', REFERENCE_A), ('b', REFERENCE_B)):
            with self.subTest(name), self.staged(candidate):
                record = self.drive('0000d001', request_id='reference-' + name)
                self.assertEqual(record['status'], 'finished', record)
                rows, results = self.row_results(record['suite_id'])
                result = results[0]
                self.assertEqual(result['status'], 'completed', (result['reason'], result['status']))
                status = self.controller.call('suite_status', {'suite_id': record['suite_id']})
                self.assertEqual(status['status'], 'completed')
                self.assertEqual([handoff['stage'] for handoff in result['accepted_handoffs']],
                                 ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
                equivalence = result['equivalence']
                self.assertEqual((equivalence['status'], equivalence['equivalent'], equivalence['method']),
                                 ('graded', True, 'fresh-final'), equivalence)
                self.assertEqual(rows[0]['equivalent'], True)

    def test_03_B20_a_mutant_driver_fails_with_its_checks_failed(self):
        for mutant in FIXTURE_ANSWER_KEY['mutants']:
            with self.subTest(mutant['id']), self.staged(mutant):
                record = self.drive('0000d001', request_id='mutant-' + mutant['id'])
                self.assertEqual(record['status'], 'finished', record)
                _, results = self.row_results(record['suite_id'])
                result = results[0]
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([handoff['stage'] for handoff in result['accepted_handoffs']],
                                 ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
                emit = next(handoff for handoff in result['accepted_handoffs'] if handoff['stage'] == 'emit')
                self.assertTrue(emit['artifacts'], emit)
                equivalence = result['equivalence']
                self.assertEqual((equivalence['status'], equivalence['equivalent']), ('graded', False), equivalence)
                failed = {check['id'] for check in equivalence['checks'] if check['passed'] is False}
                self.assertLessEqual(set(mutant['expected_failed_checks']), failed)


class WorkerWordingTests(GeneratedTrialFixture):
    def assertNoReadings(self, text, where):
        for word in ('reading', 'instrument', 'before_observation'):
            self.assertNotIn(word, str(text).lower(), where)

    def test_03_B18_a_device_with_no_readings_is_not_described_as_having_any(self):
        with self.staged(REFERENCE_A):
            record = self.drive('0000d001', request_id='no-readings')
        self.assertEqual(record['status'], 'finished', record)
        for stage, prepared in self.prepared.items():
            self.assertNoReadings(prepared.get('objective'), stage)
            for path in prepared.get('inputs', []):
                # What interpretation seals beside these is the shared skill, whose words promise no reading of this
                # device.
                shared = stage == 'interpret' and Path(path).name not in ('TASKS.json', 'BINDING_CONTEXT.json')
                if Path(path).is_file() and Path(path).suffix in ('.json', '.md') and not shared:
                    self.assertNoReadings(Path(path).read_text(), path)
        for stage, checked in self.checked.items():
            self.assertNoReadings(checked.get('validation_scope'), stage)
        self.assertIn('runs only inside each live call', self.prepared['probe']['objective'])
        self.assertNotIn('Compare those', self.prepared['probe']['objective'])
        evidence = next(path for path in self.prepared['ground']['inputs'] if path.endswith('device-observations.json'))
        self.assertNotIn('instrument', json.loads(Path(evidence).read_text()))

    def test_03_B18_its_live_results_and_journal_hold_no_observation(self):
        with self.staged(REFERENCE_A):
            record = self.drive('0000d001', request_id='no-observations')
        self.assertEqual(record['status'], 'finished', record)
        live = [result for _, name, result in self.results if name == 'interface_execute']
        self.assertTrue(live)
        for result in live:
            self.assertTrue(result['ok'], result)
            self.assertNotIn('before_observation', result)
            self.assertNotIn('observation', result)
        run = self.home / 'runs' / self.row_results(record['suite_id'])[0][0]['run_id'] / 'benchmark'
        journals = [*run.glob('live-observations-*.jsonl'), run / 'package-events.jsonl']
        self.assertTrue(all(path.is_file() for path in journals), journals)
        for path in journals:
            for line in path.read_text().splitlines():
                self.assertFalse({'before_observation', 'observation'} & set(json.loads(line)), path.name)


NOT_JUDGED = {'name': 'fresh_worker_mission', 'passed': None, 'reason': 'This case does not judge fresh reuse'}


class FreshReuseTests(GeneratedTrialFixture):
    def test_03_B22_a_family_that_returns_none_is_not_judged_on_any_grade_path(self):
        from generative_driver.benchmark_support import case_assets
        run = self.root / 'no-run'
        run.mkdir()
        self.assertEqual(case_assets.reuse_mission('generated', [], {}), NOT_JUDGED)
        # No driver, no mapping, and a driver and mapping with or without calls of it.
        for frozen, capabilities in ((None, None), ('a' * 64, None), ('a' * 64, {'schema': 'a mapping'})):
            with self.subTest(frozen=frozen, capabilities=capabilities):
                self.assertEqual(evaluator.reuse_mission('generated', run, [], frozen, capabilities), NOT_JUDGED)

    def test_03_B22_the_owner_proof_does_not_apply_to_it(self):
        final = {'contract': {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64, 'checks': []},
                 'records': [], 'frozen_artifact_sha256': 'a' * 64, 'generated_reuse_required': True,
                 'generated_reuse_verified': False}
        with patch('generative_driver.benchmark_support.behavior.validate_records',
                   return_value={'verdict': 'passed', 'checks': []}):
            self.assertEqual(evaluator.final_record(final, 'c' * 64, NOT_JUDGED, protocol='assistive/1')['mission'],
                             NOT_JUDGED)
            other = {'name': 'fresh_worker_mission', 'passed': True}
            self.assertEqual(evaluator.final_record(final, 'c' * 64, other, protocol='assistive/1')['mission'],
                             {'name': 'fresh_worker_mission', 'passed': False, 'reason': evaluator.OWNER_PROOF})
        with patch('generative_driver.benchmark_support.mcp_reuse.required', return_value=True), \
                patch('generative_driver.benchmark_support.mcp_reuse.verified', return_value=False):
            self.assertEqual(evaluator.reuse_mission('generated', self.root, [], 'a' * 64, {}), NOT_JUDGED)

    def test_03_B22_every_other_family_is_judged_as_it_was(self):
        from generative_driver.benchmark_support import case_assets, families
        from dataclasses import replace
        real = families.family_for
        judged = patch.object(case_assets, 'family_for', side_effect=lambda case: replace(
            real(case), reuse_passed=lambda events, capabilities: False))
        with judged:
            found = case_assets.reuse_mission('tq9-v2', [], {})
        self.assertEqual(found['passed'], False)
        self.assertEqual(case_assets.reuse_mission('bme280', [], {})['passed'], None)
        self.assertNotEqual(case_assets.reuse_mission('bme280', [], {})['reason'], NOT_JUDGED['reason'])
        run = self.root / 'no-run'
        run.mkdir()
        self.assertEqual(evaluator.reuse_mission('tq9-v2', run, [], None, None)['reason'],
                         'Fresh reuse did not run the frozen package')
        self.assertEqual(evaluator.reuse_mission('unregistered', run, [], None, None)['reason'],
                         'Fresh reuse did not run the frozen package')

    def test_03_B22_a_graded_trial_reports_it_whether_or_not_the_worker_called_the_driver(self):
        from generative_driver.benchmark_support.scorecard import trial_metrics
        for name, options in (('called', {}), ('not called', {'reuse': False})):
            with self.subTest(name), self.staged(REFERENCE_A, **options):
                record = self.drive('0000d001', request_id='reuse-' + name)
                result = self.row_results(record['suite_id'])[1][0]
                self.assertEqual(result['equivalence']['equivalent'], True, result['reason'])
                self.assertEqual(result['equivalence']['mission'], NOT_JUDGED)
                self.assertIn(NOT_JUDGED, self.checked['reuse']['checks'])
                # The scorecard's claim agreement treats it as any `passed: None`: neither agreement nor disagreement.
                claim = trial_metrics(self.home, result['run_id'])['claim']
                self.assertEqual((claim['mission_passed'], claim['agrees']), (None, None))

    def test_03_B22_a_trial_with_no_driver_reports_it_too(self):
        def no_package(spec, stage, *args):
            return {'artifacts': [], 'checks': []} if stage in ('probe', 'emit') else real(spec, stage, *args)
        real = self.controller._check
        with patch.object(self.controller, '_check', no_package), self.staged(REFERENCE_A, reuse=False):
            record = self.drive('0000d001', request_id='no-driver')
        result = self.row_results(record['suite_id'])[1][0]
        self.assertEqual(result['equivalence']['status'], 'no-driver', result['reason'])
        self.assertEqual(result['equivalence']['mission'], NOT_JUDGED)


class LaterHarnessTests(GeneratedTrialFixture):
    def test_03_B21_a_resumed_slot_grades_from_the_key_unlocked_through_its_set(self):
        from generative_driver.benchmark_support import generated_trials
        with self.staged(REFERENCE_A), patch.object(evaluator, 'reference_execute', side_effect=RuntimeError('host')):
            first = self.drive('0000d001', request_id='graded-later')
        self.assertEqual(self.row_results(first['suite_id'])[1][0]['equivalence'], None)
        with self.staged(REFERENCE_A), \
                patch.object(generated_trials, 'answer_key', wraps=generated_trials.answer_key) as unlocked:
            second = self.drive('0000d001', suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertEqual(unlocked.call_count, 1)
        self.assertEqual(unlocked.call_args.args[0], generated_trials.SetDevice(self.set_root, SET, '0000d001'))
        self.assertEqual(self.row_results(first['suite_id'])[1][0]['equivalence']['equivalent'], True)

    def test_03_B21_a_reattached_slot_is_admitted_through_its_set(self):
        stopped = []

        def stop_probe(request):
            if request.stage == 'probe' and not stopped:
                stopped.append(request.stage)
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                        'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
        with self.staged(REFERENCE_A, runtime=stop_probe):
            first = self.drive('0000d001', request_id='reattached')
        self.assertEqual(stopped, ['probe'])
        from generative_driver.benchmark_support import generated_trials
        with self.staged(REFERENCE_A), \
                patch.object(generated_sets, 'admitted', wraps=generated_sets.admitted) as admitted, \
                patch.object(generated_sets, 'open_device', wraps=generated_sets.open_device) as opened, \
                patch.object(generated_trials, 'answer_key', side_effect=AssertionError('decrypted again')):
            second = self.drive('0000d001', suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertEqual(admitted.call_count, 1)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.row_results(first['suite_id'])[1][0]['equivalence']['equivalent'], True)

    def test_03_B21_a_resume_without_its_suite_is_refused_saying_so(self):
        import contextlib
        import io
        import sys
        from generative_driver.benchmark import main
        trial = self.root / 'a-trial'
        trial.mkdir()
        (trial / 'trial.json').write_text(json.dumps({'case': 'generated', 'trial_id': 't', 'run_id': 'r',
                                                      'home': str(self.home)}))
        with self.assertRaisesRegex(ValueError, 'through its suite'):
            harness.resume(trial, password=PASSWORD, renode='emulator-handle')
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = main(['trial-resume', str(trial), '--password-file', str(self.handle)])
        self.assertEqual(code, 1, printed.getvalue())
        self.assertIn('through its suite', printed.getvalue())
        stdin = type('Stdin', (), {'buffer': io.BytesIO(json.dumps({'password_file': str(self.handle)}).encode())})()
        with patch.object(sys, 'stdin', stdin), self.assertRaisesRegex(ValueError, 'through its suite'):
            harness.main(['--trial-dir', str(trial), '--resume'])


class WhatTheConfiguratorHoldsTests(GeneratedTrialFixture):
    def test_03_B7_no_pin_spec_call_or_home_file_holds_the_design_or_the_root(self):
        calls = []
        with self.staged(REFERENCE_A):
            record = self.drive(calls=calls, request_id='held')
        self.assertEqual(record['status'], 'finished', record)
        mission = fixture_mission('0000d001')
        sent = json.dumps(calls, default=str)
        for held in (str(self.set_root), '"level"', '"framing"', 'text-lines', mission['reuse_objective'], 'meaning',
                     '"tasks"'):
            self.assertNotIn(held, sent)
        rows, _ = self.row_results(record['suite_id'])
        self.assertEqual(len(rows), 2)
        for row in rows:
            spec = self.controller._row(row['run_id'])['spec']
            for held in (str(self.set_root), '"level"', '"framing"', 'text-lines', 'reuse_objective'):
                self.assertNotIn(held, spec)
        for path in self.home.rglob('*'):
            # An emitted package's bundled runtime and the shared skills speak of framing in general.
            if path.is_file() and path.suffix not in ('.py', '.md'):
                content = path.read_bytes()
                for held in (str(self.set_root).encode(), b'"level"', b'"framing"', b'text-lines'):
                    self.assertNotIn(held, content, path)
        directory = next((self.root / 'home-bench/suites').iterdir())
        self.assertNotIn(str(self.set_root), (directory / 'suite.json').read_text())

    def test_03_B15_after_the_snapshot_the_run_reads_only_from_it(self):
        # The folder goes as soon as the snapshot is taken, so that any later read of the image or mission from it stops
        # the run. A fixture device ignores its image, so the session factory's `image` shows where each session's came
        # from.
        real_snapshot, removed = snapshots.snapshot_case, []

        def snapshot_then_remove(run_dir, pin, provenance, *, public_inputs=None):
            saved = real_snapshot(run_dir, pin, provenance, public_inputs=public_inputs)
            shutil.rmtree(public_inputs)
            removed.append(Path(public_inputs))
            return saved
        started, real_start = [], fixture_start(process=True)

        def start(**kwargs):
            started.append(Path(kwargs['image']))
            return real_start(**kwargs)
        with self.staged(REFERENCE_A), patch.object(snapshots, 'snapshot_case', snapshot_then_remove), \
                patch(START, side_effect=start):
            record = self.drive('0000d001', request_id='snapshot-only')
        self.assertEqual(record['status'], 'finished', record)
        rows, results = self.row_results(record['suite_id'])
        self.assertEqual(results[0]['status'], 'completed', results[0]['reason'])
        self.assertEqual(results[0]['equivalence']['equivalent'], True)
        inputs = self.home / 'runs' / rows[0]['run_id'] / 'benchmark/inputs'
        # The provider's session started from the folder, before the snapshot; the grade's from the snapshot.
        self.assertEqual(started, [removed[0] / 'firmware.bin', inputs / 'firmware.bin'])
        self.assertEqual(Path(self.prepared['acquire']['context']['source_path']).read_bytes(), FIXTURE_IMAGE)


class SuiteRowTests(GeneratedTrialFixture):
    def test_03_B23_the_id_flows_into_the_slot_and_the_row_and_only_a_set_suites_hold_it(self):
        calls = []
        with self.staged(REFERENCE_A):
            record = self.drive(calls=calls, request_id='rows')
        rows, _ = self.row_results(record['suite_id'])
        self.assertEqual([row['device_id'] for row in rows], ['0000d001', '0000d002'])
        trials = self.controller._suites.get(record['suite_id'])['trials']
        self.assertEqual([trial['device_id'] for trial in trials], ['0000d001', '0000d002'])
        handmade = self.controller.call('suite_start', {
            'manifest': handmade_manifest(repetitions=1), 'executor': 'codex', 'options': {}, 'request_id': 'handmade',
            'harness_token_sha256': 'a' * 64, 'harness_pid': os.getpid()})
        self.assertTrue(handmade['ok'], handmade)
        for row in self.controller.call('suite_result', {'suite_id': handmade['suite_id']})['trials']:
            self.assertNotIn('device_id', row)


class PairingTests(unittest.TestCase):
    @staticmethod
    def report(outcomes, repetitions=1):
        """A saved report (`test_pairing.suite_report`) whose devices are entries of the generated case, as a set
        suite's."""
        from test_pairing import suite_report
        report = suite_report('assistive/1', outcomes)
        identity = report['experiment']['comparison_identity']
        for entry, slot, row in zip(identity['manifest']['entries'], identity['trials'], report['trials']):
            device = entry['case']
            for held in (entry, slot, row):
                held.update(case='generated', device_id=device)
        for pin, entry in zip(identity['entry_pins'], identity['manifest']['entries']):
            pin.update(case_id='generated', device_id=entry['device_id'])
        return report

    def test_03_B24_two_devices_of_one_case_pair_as_two(self):
        from generative_driver.benchmark_support.pairing import paired_comparison
        compared = paired_comparison(self.report({'0000d001': 'pass', '0000d002': 'failure'}),
                                     self.report({'0000d001': 'failure', '0000d002': 'failure'}))
        self.assertTrue(compared['compatible'], compared)
        self.assertEqual(compared['devices'], {'both': 0, 'neither': 1, 'only_before': 1, 'only_after': 0})
        self.assertEqual(compared['excluded'], {'repeated': 0, 'absent': 0, 'ungraded': 0})
        self.assertNotIn('0000d00', json.dumps(compared))

    def test_03_B24_a_device_only_one_report_tried_is_absent(self):
        from generative_driver.benchmark_support.pairing import paired_comparison
        compared = paired_comparison(self.report({'0000d001': 'pass', '0000d002': 'pass'}),
                                     self.report({'0000d001': 'pass'}))
        self.assertTrue(compared['compatible'], compared)
        self.assertEqual(compared['devices']['both'], 1)
        self.assertEqual(compared['excluded']['absent'], 1)

    def test_03_B24_a_device_whose_pin_differs_is_incompatible_only_for_that_device(self):
        from generative_driver.benchmark_support.pairing import paired_comparison
        before = self.report({'0000d001': 'pass', '0000d002': 'pass'})
        after = self.report({'0000d001': 'pass', '0000d002': 'pass'})
        after['experiment']['comparison_identity']['entry_pins'][1]['truth_sha256'] = 'f' * 64
        self.assertEqual(paired_comparison(before, after)['incompatible_fields'], ['entry_pins'])

    def test_03_B24_a_repeated_set_manifest_counts_its_devices_repeated(self):
        from generative_driver.benchmark_support.pairing import paired_comparison
        before = self.report({'0000d001': 'pass', '0000d002': 'pass'})
        again = copy.deepcopy(next(row for row in before['trials'] if row['device_id'] == '0000d001'))
        before['trials'].append(again)
        compared = paired_comparison(before, self.report({'0000d001': 'pass', '0000d002': 'pass'}))
        self.assertEqual(compared['excluded']['repeated'], 1)
        self.assertEqual(compared['devices']['both'], 1)

    def test_03_B24_handmade_reports_pair_as_they_did(self):
        from generative_driver.benchmark_support.pairing import paired_comparison
        from test_pairing import suite_report
        compared = paired_comparison(suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure'}),
                                     suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'}))
        self.assertEqual(compared['devices'], {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 1})


def replay_candidate(directory, driver):
    """A saved candidate of the replay executor whose driver is `driver`: scripted, never a model run."""
    root = Path(directory)
    root.mkdir(parents=True)
    (root / 'model.json').write_text(json.dumps(driver['model']))
    (root / 'capabilities.json').write_text(json.dumps(driver['capabilities']))
    context = lambda key: {'$context': key}
    operation = driver['capabilities']['tasks']['get_mode']['operation']
    measure = {'model_dir': context('model_dir'), 'operation': operation, 'parameters': {}}
    (root / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {
        'acquire': [{'call': 'acquire_firmware_artifact',
                     'arguments': {key: context(key) for key in ('source_path', 'expected_sha256', 'origin')}}],
        'interpret': [{'write': 'model.json'}],
        'probe': [{'call': 'interface_execute', 'arguments': measure},
                  {'call': 'probe_run', 'arguments': {**measure, 'n': 1}},
                  {'write': 'capabilities.json'}],
        'emit': [{'call': 'emit_package',
                  'arguments': {'model_dir': context('model_dir'), 'probe_json': context('probe')}},
                 {'call': 'emit_check', 'arguments': {'package_dir': {'$result': 'package_dir'}}}],
        'reuse': [{'call': 'device_' + operation, 'arguments': {'_request_id': 'saved-reuse'}}]}}))
    return root


HARNESS_FIXTURE = '''
# Tests only: a spawned bench harness reads the test's set table, and each emulator session it starts is a fixture
# device.
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, TESTS)
from generated_fixtures import fixture_start
from generative_driver.benchmark_support import generated_sets
generated_sets._SETS_FILE = Path(TABLE)
patch(START, side_effect=fixture_start(process=True)).start()
'''


class WholeSuiteTests(unittest.TestCase):
    """A replay suite through its detached harness, as `ReplaySuiteTests` runs one: one candidate driver, two
    devices."""

    def settled(self, call, suite_id):
        import time
        until = time.monotonic() + 480
        while time.monotonic() < until:
            status = call('suite_status', {'suite_id': suite_id})
            if status.get('status') not in ('queued', 'running') and not status.get('stopping'):
                return status
            time.sleep(.2)
        self.fail(call('suite_status', {'suite_id': suite_id}))

    def finished_suite(self):
        """A replay suite over the two devices through its detached harness, under whichever table the test registers
        the set in: (its home, its saved report, read as a saved one is)."""
        from generative_driver.benchmark_support.suite_reporting import _saved_report, report_suite
        from suite_fixtures import wait_harnesses
        from test_replay_executor import serve
        set_root, handle, _ = qualified_set()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        candidate = replay_candidate(root / 'candidate', REFERENCE_A)
        fixture = root / 'harness_fixture.py'
        fixture.write_text(HARNESS_FIXTURE.replace('TESTS', repr(str(Path(__file__).resolve().parent)))
                           .replace('TABLE', repr(str(generated_sets._SETS_FILE))).replace('START', repr(START)))
        home = root / 'home'
        call = serve(self, home)
        options = {'replay_candidates': {'generated': str(candidate)}, 'evaluator_password_files': {SET: str(handle)},
                   'set_root': str(set_root), 'renode': str(root / 'renode-executable-handle')}
        with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
            started = start_suite(set_manifest(), 'replay', options, 'whole-suite', home=home)
            self.assertTrue(started.get('ok'), started)
            status = self.settled(call, started['suite_id'])
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 2, 2), status)
        wait_harnesses(home)
        report_suite(started['suite_id'], home=home, output=root / 'report')
        return home, _saved_report(json.loads((root / 'report/suite.json').read_text()))

    def test_04_B11_a_saved_report_of_a_scoring_suite_pairs_with_no_hand_added_value(self):
        from generative_driver.benchmark_support.pairing import paired_comparison
        from generative_driver.benchmark_support.scorecard import bench_trial_dirs, suite_scorecard
        as_scoring_set(self)
        _, _, manifest = qualified_set()
        home, saved = self.finished_suite()
        scoring = {'registration': SET, 'manifest_sha256': manifest['manifest_sha256']}
        self.assertEqual(saved['experiment']['comparison_identity']['scoring_set'], scoring)
        compared = paired_comparison(saved, copy.deepcopy(saved))
        self.assertEqual((compared['compatible'], compared['devices']['both'], compared['excluded']['repeated']),
                         (True, 2, 0), compared)
        card = suite_scorecard(saved, home=home, trial_dirs=bench_trial_dirs(saved, home))
        self.assertEqual(card['conditions']['scoring_set'], scoring)
        self.assertEqual(card['pass_line'], '2/2 pass, 0 unknown, 0 faults, 2 planned')
        self.assertNotIn('trials', card)
        text = json.dumps(card)
        for row in saved['trials']:
            for private in (row['trial_key'], row['device_id'], row['run_id']):
                self.assertNotIn(private, text)

    def test_04_B11_a_development_suites_report_is_refused_by_that_check(self):
        # No RED of its own: this pins what must stay.
        from generative_driver.benchmark_support.pairing import paired_comparison
        _, saved = self.finished_suite()
        self.assertNotIn('scoring_set', saved['experiment']['comparison_identity'])
        with self.assertRaisesRegex(ValueError, 'Pairing needs two reports that carry a scoring set'):
            paired_comparison(saved, copy.deepcopy(saved))

    def test_03_B25_a_suite_over_two_devices_with_one_candidate_finishes_and_regrades(self):
        from generative_driver.benchmark_support.harness import read_suite
        from generative_driver.benchmark_support.pairing import paired_comparison
        from generative_driver.benchmark_support.suite_reporting import _saved_report, report_suite
        from suite_fixtures import wait_harnesses
        from test_replay_executor import serve
        set_root, handle, _ = qualified_set()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        candidate = replay_candidate(root / 'candidate', REFERENCE_A)
        fixture = root / 'harness_fixture.py'
        fixture.write_text(HARNESS_FIXTURE.replace('TESTS', repr(str(Path(__file__).resolve().parent)))
                           .replace('TABLE', repr(str(generated_sets._SETS_FILE))).replace('START', repr(START)))
        home = root / 'home'
        call = serve(self, home)
        options = {'replay_candidates': {'generated': str(candidate)}, 'evaluator_password_files': {SET: str(handle)},
                   'set_root': str(set_root), 'renode': str(root / 'renode-executable-handle')}
        with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
            started = start_suite(set_manifest(), 'replay', options, 'whole-suite', home=home)
            self.assertTrue(started.get('ok'), started)
            status = self.settled(call, started['suite_id'])
        suite_dir = Path(started['suite_dir'])
        self.assertEqual((status['status'], status['planned'], status['completed'], status['equivalent']),
                         ('completed', 2, 2, 2), (status, read_suite(suite_dir)))
        # The configurator reports the suite settled once its harness detaches, which comes before the harness writes its
        # final record: only the harness's exit says that record is written.
        wait_harnesses(home)
        self.assertEqual(read_suite(suite_dir)['status'], 'finished')
        recorded = report_suite(started['suite_id'], home=home, output=root / 'recorded-report')
        self.assertEqual([(row['device_id'], row['scored']) for row in recorded['trials']],
                         [('0000d001', 'pass'), ('0000d002', 'pass')])
        evidence = {row['trial_key']: next(suite_dir.glob('trials/' + row['trial_key'] + '-*/run-evidence.enc'))
                    for row in recorded['trials']}
        regraded = report_suite(started['suite_id'], home=home, output=root / 'regraded-report',
                                evidence_files={key: str(path) for key, path in evidence.items()},
                                password_files={SET: str(handle)})
        self.assertEqual([(row['device_id'], row['scored']) for row in regraded['trials']],
                         [('0000d001', 'pass'), ('0000d002', 'pass')])
        self.assertEqual(regraded['equivalence']['equivalent'], 2)
        self.assertEqual({(row['equivalence'] or {}).get('mission', {}).get('reason') for row in regraded['trials']},
                         {NOT_JUDGED['reason']})
        self.assertEqual([row['equivalent_and_mission'] for row in regraded['trials']], [False, False])
        with self.assertRaisesRegex(ValueError, 'password handle|Regrade evidence'):
            report_suite(started['suite_id'], home=home, output=root / 'keyed-by-case', evidence_files=evidence,
                         password_files={'generated': str(handle)})
        # Saved reports over the two devices, each with a scoring set added by hand, as this set is a development set.
        saved = json.loads((root / 'regraded-report/suite.json').read_text())
        saved['experiment']['comparison_identity']['scoring_set'] = {'registration': SET, 'manifest_sha256': 'a' * 64}
        saved = _saved_report(saved)
        self.assertEqual([row['device_id'] for row in saved['trials']], ['0000d001', '0000d002'])
        compared = paired_comparison(saved, copy.deepcopy(saved))
        self.assertEqual((compared['compatible'], compared['devices']['both'], compared['excluded']['repeated']),
                         (True, 2, 0), compared)


class RepairContextTests(unittest.TestCase):
    def test_03_B18_a_repair_context_mentions_readings_only_of_a_device_that_offers_them(self):
        from generative_driver.benchmark_support.candidate_context import repair_sources
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'run/benchmark').mkdir(parents=True)
            (root / 'workspace').mkdir()
            sources = repair_sources(root / 'run', root / 'workspace', [], readings=False)
            self.assertNotIn('instrument', Path(sources['REPAIR_CONTEXT.json']).read_text())
            sources = repair_sources(root / 'run', root / 'workspace', [])
            self.assertIn('instrument readings in those journals are readings of the emulated device, device evidence '
                          'like any other. Use them to correct the latest candidate.',
                          json.loads(Path(sources['REPAIR_CONTEXT.json']).read_text())['evidence_policy'])


class ReadingsStayTests(SealedTrialFixture):
    """A case with readings: today's exact words and fields, on tq9-v2 through the real `_prepare` and `_check`."""

    def tool(self, request, name, arguments):
        result = super().tool(request, name, arguments)
        self.results.append((name, result))
        return result

    def test_03_B18_a_case_with_readings_keeps_its_exact_words_and_fields(self):
        self.results, prepared, checked = [], {}, {}
        real_prepare, real_check = self.controller._prepare, self.controller._check

        def prepare(spec, stage, *args):
            prepared[stage] = real_prepare(spec, stage, *args)
            return prepared[stage]

        def check(spec, stage, *args):
            checked[stage] = real_check(spec, stage, *args)
            return checked[stage]
        with patch.object(self.controller, '_prepare', prepare), patch.object(self.controller, '_check', check):
            _, record, result = self.staged_trial('readings')
        self.assertEqual(result['status'], 'completed', result['reason'])
        self.assertIn('Correct mapping errors before submission. Live interface_execute and probe_run results carry '
                      'before_observation and observation: instrument readings of the emulated device with units. They '
                      "are device evidence, as a real device's readings would be, never a grade. The provided emulator "
                      'runs only inside each live call and is paused between calls, so device time advances during a '
                      'call and not while you work between calls. Compare those readings and the observed effects with '
                      'your decoded outputs. Preserve capabilities.json even when incomplete;',
                      prepared['probe']['objective'])
        self.assertIn('through the configurator-owned emulator connection. Live results carry before_observation and '
                      'observation, instrument readings of the emulated device with units; they are device evidence, '
                      "as a real device's readings would be, never a grade. Compare decoded outputs and effects with "
                      'those '
                      'readings, the supplied probe records, capability mapping and journal '
                      '(ground-evidence/device-observations.json).', prepared['ground']['objective'])
        self.assertIn('Find names and arguments in the package reference. Each result also carries an instrument '
                      'reading of the emulated device after the call. Report actual observations and limitations.',
                      prepared['reuse']['objective'])
        evidence = next(path for path in prepared['ground']['inputs'] if path.endswith('device-observations.json'))
        self.assertEqual(json.loads(Path(evidence).read_text())['instrument'],
                         'before_observation and observation are instrument readings of the emulated device with '
                         "units: device evidence, as a real device's readings would be, never a grade.")
        self.assertEqual(checked['probe']['validation_scope'], "The worker's mapping, the adapted model and the live "
                         'journal with instrument readings: device evidence, not a grade')
        self.assertEqual(checked['reuse']['validation_scope'],
                         "The fresh worker's package calls and the instrument readings after them; a note, not a grade")
        live = next(result for name, result in self.results if name == 'interface_execute')
        self.assertEqual(live['before_observation']['source'], 'instrument')
        self.assertEqual(live['observation']['source'], 'instrument')
        run = self.home / 'runs' / record['run_id'] / 'benchmark'
        for path in (*run.glob('live-observations-*.jsonl'), run / 'package-events.jsonl'):
            self.assertLessEqual({'before_observation', 'observation'},
                                 set(json.loads(path.read_text().splitlines()[0])))


def device_pin_with(pin, *, evidence):
    changed = copy.deepcopy(pin)
    changed['calibration']['evidence_sha256'] = evidence
    return changed


if __name__ == '__main__':
    unittest.main()

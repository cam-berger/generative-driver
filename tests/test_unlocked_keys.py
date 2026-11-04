"""Feature 02, unlocked keys: each process reads a password file once and holds it in an object that can only seal and
unlock; a trial unlocks its answer key once and hands it to the grader. Synthetic credentials, toy truth and scripted
stages only; never a real password, vault, device or model run."""
import ast
import base64
import copy
import hashlib
import json
import os
import pickle
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark_support import evaluator, families, harness, registry, truth
from generative_driver.benchmark_support.snapshots import canonical_digest
from suite_fixtures import legacy_case, public_case_fixture
from test_configurator import scripted_stages
from test_evaluation import CASE_TRUTH, FAIL, HarnessTrialFixture, PASS, PendingRunFixture, reference
from test_legacy_cases import silicon_start
from test_suite_harness import HarnessSuiteFixture, manifest as suite_manifest

SECRET = 'synthetic-unlocked-keys-credential'
SOURCE = Path(truth.__file__).resolve().parents[1]
EVIDENCE = '9' * 64
SENTINEL = 'UNLOCKED KEYS SENTINEL'
EMULATOR = 'scripted-emulator-handle'
CASE_ROOTS = ('generative_driver.benchmark.case_root', 'generative_driver.benchmark_support.case_assets.case_root')


def toy_truth(**changes):
    """A toy tq9-v2 answer key: a device recipe the fixtures' provider accepts and a sentinel nothing public may hold."""
    from test_harness import RECIPE
    return {'private_sentinel': SENTINEL, 'recipe': RECIPE, 'calibration': {'toy': True}, **changes}


def sealed_case(test, resources, password, payload=None, **changes):
    """A tq9-v2 case under `resources` whose answer key is really sealed under `password`, with the case root patched to
    it: (manifest, truth file)."""
    manifest = public_case_fixture(resources, calibration={'status': 'passed', 'evidence_sha256': EVIDENCE}, **changes)
    path = resources / manifest['truth']['path']
    manifest['truth']['sha256'] = password.seal(toy_truth() if payload is None else payload, path)
    (resources / 'cases/tq9-v2/case.json').write_text(json.dumps(manifest), encoding='utf-8')
    for target in CASE_ROOTS:
        guard = patch(target, return_value=resources)
        guard.start(); test.addCleanup(guard.stop)
    return manifest, path


def admits_calibration(test):
    """The family's admission check accepts the toy calibration record: a real one needs native qualification."""
    real = families.family_for
    guard = patch.object(registry, 'family_for',
                         side_effect=lambda case: replace(real(case), admission_valid=lambda pin, payload, record: True))
    guard.start(); test.addCleanup(guard.stop)


class UnlockSpy:
    """Counts each `EvaluatorPassword.unlock` of the file at `path`, and lets it through."""

    def __init__(self, path=None):
        self.path, self.opened = path, []
        real = truth.EvaluatorPassword.unlock
        spy = self

        def unlock(password, path, expected_sha256):
            spy.opened.append(Path(path))
            return real(password, path, expected_sha256)
        self.patcher = patch.object(truth.EvaluatorPassword, 'unlock', unlock)

    def __enter__(self):
        self.patcher.start()
        return self

    def __exit__(self, *_):
        self.patcher.stop()

    @property
    def count(self):
        return len([path for path in self.opened if self.path is None or path == Path(self.path)])


class PasswordObjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_02_B1_reads_utf8_strips_and_refuses_what_is_no_password(self):
        path = self.root / 'password'
        path.write_bytes(('  ' + SECRET + 'é\r\n').encode('utf-8'))
        password = truth.read_password(path)
        self.assertIsInstance(password, truth.EvaluatorPassword)
        # The stripped text is the key: sealed under the literal, opened by what was read.
        digest = truth.EvaluatorPassword(SECRET + 'é').seal({'a': 1}, self.root / 'sealed.enc')
        self.assertEqual(password.unlock(self.root / 'sealed.enc', digest), {'a': 1})
        # A string path reads too, as a command passes it.
        self.assertEqual(truth.read_password(str(path)).unlock(self.root / 'sealed.enc', digest), {'a': 1})
        for empty in (b'', b' \r\n\t '):
            path.write_bytes(empty)
            with self.subTest(empty=empty), self.assertRaisesRegex(ValueError, '^A nonempty evaluator password is required$'):
                truth.read_password(path)
        path.write_bytes(b'not-text-QUOTED\xff\xfe')
        with self.assertRaises(ValueError) as caught:
            truth.read_password(path)
        for shown in (str(caught.exception), repr(caught.exception), repr(caught.exception.__context__),
                      repr(caught.exception.__cause__)):
            self.assertNotIn('QUOTED', shown)
            self.assertNotIn('xff', shown)
        with self.assertRaises(FileNotFoundError):
            truth.read_password(self.root / 'absent')
        with self.assertRaises(OSError):
            truth.read_password(self.root)

    def test_02_B1_a_byte_order_mark_stays_as_it_reads_today(self):
        path = self.root / 'password'
        path.write_bytes(b'\xef\xbb\xbf' + SECRET.encode())
        digest = truth.read_password(path).seal({'a': 1}, self.root / 'sealed.enc')
        self.assertEqual(truth.EvaluatorPassword('﻿' + SECRET).unlock(self.root / 'sealed.enc', digest), {'a': 1})
        with self.assertRaisesRegex(ValueError, 'invalid password'):
            truth.EvaluatorPassword(SECRET).unlock(self.root / 'sealed.enc', digest)

    def test_02_B2_the_value_never_prints_or_serializes(self):
        password = truth.EvaluatorPassword(SECRET)
        for shown in (repr(password), str(password), format(password), '{}'.format(password), f'{password!s}',
                      repr({'password': password})):
            self.assertNotIn(SECRET, shown)
        for name, operation in (('json', json.dumps), ('pickle', pickle.dumps), ('deepcopy', copy.deepcopy)):
            for label, value in (('alone', password), ('inside', {'password': password})):
                with self.subTest(operation=name, form=label), self.assertRaises(TypeError):
                    operation(value)
        with self.assertRaises(ValueError):
            truth.EvaluatorPassword('')
        self.assertFalse([name for name in vars(type(password)) if not name.startswith('_')
                          and name not in ('seal', 'unlock')])

    def test_02_B3_seals_and_unlocks_in_the_format_shipped_ciphertexts_hold(self):
        password = truth.EvaluatorPassword(SECRET)
        path = self.root / 'nested/sealed.enc'
        digest = password.seal({'answer': [1, 2]}, path)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(password.unlock(path, digest), {'answer': [1, 2]})
        with self.assertRaisesRegex(ValueError, 'invalid password or envelope'):
            truth.EvaluatorPassword('another').unlock(path, digest)
        with self.assertRaisesRegex(ValueError, 'does not match its pinned hash'):
            password.unlock(path, '0' * 64)
        envelope = json.loads(path.read_bytes())
        self.assertEqual(set(envelope), {'schema', 'kdf', 'iterations', 'cipher', 'salt', 'nonce', 'ciphertext'})
        self.assertEqual((envelope['schema'], envelope['kdf'], envelope['iterations'], envelope['cipher']),
                         ('generative-driver-groundtruth/1', 'PBKDF2-HMAC-SHA256', 600000, 'AES-256-GCM'))
        # An envelope built directly with the cipher, as today's shipped ciphertexts were.
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        salt, nonce = os.urandom(16), os.urandom(12)
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000).derive(SECRET.encode())
        encrypted = AESGCM(key).encrypt(nonce, b'{"shipped":true}', b'generative-driver-groundtruth/1')
        shipped = {'schema': 'generative-driver-groundtruth/1', 'kdf': 'PBKDF2-HMAC-SHA256', 'iterations': 600000,
                   'cipher': 'AES-256-GCM', 'salt': base64.b64encode(salt).decode(), 'nonce': base64.b64encode(nonce).decode(),
                   'ciphertext': base64.b64encode(encrypted).decode()}
        built = self.root / 'shipped.enc'
        built.write_text(json.dumps(shipped, sort_keys=True) + '\n', encoding='utf-8')
        self.assertEqual(password.unlock(built, hashlib.sha256(built.read_bytes()).hexdigest()), {'shipped': True})


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.password = truth.EvaluatorPassword(SECRET)
        self.resources = self.root / 'resources'

    def test_02_B19_admitted_truth_returns_the_unlocked_truth_and_require_calibration_the_admission(self):
        manifest, path = sealed_case(self, self.resources, self.password)
        admits_calibration(self)
        pin = registry.pin_case('tq9-v2', 'original', 0)
        with UnlockSpy(path) as spy:
            unlocked, admission = registry.admitted_truth(pin, self.password)
            self.assertEqual(spy.count, 1)
        self.assertIsInstance(unlocked, truth.UnlockedTruth)
        self.assertEqual(unlocked.payload, toy_truth())
        self.assertEqual(unlocked.sha256, pin['truth_sha256'])
        self.assertEqual(admission, {'ok': True, 'evidence_sha256': canonical_digest({'toy': True})})
        self.assertEqual(registry.require_calibration(pin, self.password), admission)

    def test_02_B19_refusals_come_in_todays_order_and_before_any_decrypting(self):
        manifest, path = sealed_case(self, self.resources, self.password)
        pin = registry.pin_case('tq9-v2', 'original', 0)
        for name, changed, message in (
                ('a changed manifest', {**pin, 'manifest': {**pin['manifest'], 'limitations': []}}, 'Calibration admission case pin changed'),
                ('a changed truth hash', {**pin, 'truth_sha256': '0' * 64}, 'Calibration admission case pin changed')):
            with self.subTest(name), UnlockSpy() as spy, self.assertRaisesRegex(ValueError, message):
                try:
                    registry.admitted_truth(changed, self.password)
                finally:
                    self.assertEqual(spy.count, 0)
        manifest['calibration'] = {'status': 'pending'}
        (self.resources / 'cases/tq9-v2/case.json').write_text(json.dumps(manifest), encoding='utf-8')
        pending = registry.pin_case('tq9-v2', 'original', 0)
        with UnlockSpy() as spy, self.assertRaisesRegex(ValueError, 'independently validated passed calibration'):
            try:
                registry.admitted_truth(pending, self.password)
            finally:
                self.assertEqual(spy.count, 0)

    def test_02_B19_a_password_that_opens_nothing_or_a_stale_record_is_refused(self):
        sealed_case(self, self.resources, self.password)
        pin = registry.pin_case('tq9-v2', 'original', 0)
        for name, password in (('another password', truth.EvaluatorPassword('another')), ('no password', None)):
            with self.subTest(name), self.assertRaisesRegex(
                    ValueError, 'Authenticated calibration evidence requires the evaluator password file'):
                registry.admitted_truth(pin, password)
        # The toy record is not calibration evidence for the family's own check.
        with self.assertRaisesRegex(ValueError, 'Calibration evidence is missing, stale, or incomplete'):
            registry.admitted_truth(pin, self.password)
        with self.assertRaisesRegex(ValueError, 'Calibration evidence is missing, stale, or incomplete'):
            registry.require_calibration(pin, self.password)

    def test_02_B19_a_schema_1_pin_is_admitted_without_decrypting(self):
        pin = registry.pin_case('tq9', None, 0)
        with UnlockSpy() as spy:
            self.assertEqual(registry.admitted_truth(pin, None), (None, {'ok': True}))
            self.assertEqual(registry.admitted_truth(pin, self.password), (None, {'ok': True}))
            self.assertEqual(registry.require_calibration(pin, self.password), {'ok': True})
        self.assertEqual(spy.count, 0)


class PinWithoutTruthFileTests(PendingRunFixture):
    def test_02_B22_pin_case_opens_the_truth_file_by_default_and_not_when_told(self):
        truth_file = self.root / 'resources/groundtruth/tq9-v2.enc'
        pin = registry.pin_case('tq9-v2', 'original', 0)
        kept = truth_file.read_bytes()
        truth_file.write_bytes(b'replaced')
        with self.assertRaisesRegex(ValueError, 'Case truth hash mismatch'):
            registry.pin_case('tq9-v2', 'original', 0)
        self.assertEqual(registry.pin_case('tq9-v2', 'original', 0, check_truth=False), pin)
        truth_file.unlink()
        with self.assertRaisesRegex(registry.PendingCaseError, 'Case truth is unbuilt'):
            registry.resolve_case('tq9-v2')
        with self.assertRaisesRegex(registry.PendingCaseError, 'Case truth is unbuilt'):
            registry.pin_case('tq9-v2', 'original', 0)
        self.assertEqual(registry.resolve_case('tq9-v2', check_truth=False).manifest, pin['manifest'])
        self.assertEqual(registry.pin_case('tq9-v2', 'original', 0, check_truth=False), pin)
        # What stays checked: the manifest's own truth hash, the image and any asset.
        case = self.root / 'resources/cases/tq9-v2/case.json'
        manifest = json.loads(case.read_text())
        truth_file.write_bytes(kept)
        for name, changed, error, message in (
                ('a malformed truth hash', {**manifest, 'truth': {**manifest['truth'], 'sha256': 'short'}}, ValueError, 'Malformed SHA-256'),
                ('an escaping truth path', {**manifest, 'truth': {**manifest['truth'], 'path': '../outside'}}, ValueError, 'escapes root'),
                ('a changed image hash', {**manifest, 'images': {'firmware.bin': '0' * 64}}, ValueError, 'Case image hash mismatch')):
            case.write_text(json.dumps(changed))
            with self.subTest(name), self.assertRaisesRegex(error, message):
                registry.pin_case('tq9-v2', 'original', 0, check_truth=False)
        case.write_text(json.dumps(manifest))

    def test_02_B22_grading_checks_the_pin_without_opening_the_truth_file(self):
        run, result = self.pending()
        run_dir = self.home / 'runs' / run['run_id']
        truth_file = self.root / 'resources/groundtruth/tq9-v2.enc'
        truth_file.unlink()
        snapshot, driver = evaluator.accepted_driver('tq9-v2', self.root / 'trial', run_dir, result)
        self.assertEqual(driver[1], self.emitted(result))
        truth_file.write_bytes(b'replaced after the run started')
        self.assertEqual(evaluator.accepted_driver('tq9-v2', self.root / 'trial', run_dir, result)[1][1], driver[1])
        # A changed manifest or a changed pinned truth hash still raises IntegrityError, and a changed image fails.
        from generative_driver.benchmark_support.integrity import IntegrityError
        case = self.root / 'resources/cases/tq9-v2/case.json'
        manifest = json.loads(case.read_text())
        for name, changed in (('the manifest', {**manifest, 'limitations': ['changed']}),
                              ('the pinned truth hash', {**manifest, 'truth': {**manifest['truth'], 'sha256': '0' * 64}})):
            case.write_text(json.dumps(changed))
            with self.subTest(name), self.assertRaisesRegex(IntegrityError, 'installed case differs'):
                evaluator.accepted_driver('tq9-v2', self.root / 'trial', run_dir, result)
        case.write_text(json.dumps(manifest))
        self.image.write_bytes(b'changed image')
        with self.assertRaisesRegex(ValueError, 'Case image hash mismatch'):
            evaluator.accepted_driver('tq9-v2', self.root / 'trial', run_dir, result)

    def test_02_B22_a_configurator_start_and_admit_refuse_a_missing_or_replaced_truth_file(self):
        truth_file = self.root / 'resources/groundtruth/tq9-v2.enc'
        kept = truth_file.read_bytes()
        start = {'goal': 'g', 'case': 'tq9-v2', 'effects': ['write', 'actuate'], 'case_options': {'scenario_id': 'original'},
                 'executor_config': {'command': ['scripted-runtime']}}
        for name, change, error, message in (
                ('missing', lambda: truth_file.unlink(), registry.PendingCaseError, 'Case truth is unbuilt'),
                ('replaced', lambda: truth_file.write_bytes(b'replaced'), ValueError, 'Case truth hash mismatch')):
            change()
            with self.subTest(name, seam='configurator'), self.assertRaisesRegex(error, message):
                self.controller.call('start', start)
            with self.subTest(name, seam='admit'), self.assertRaisesRegex(error, message):
                harness.admit('tq9-v2', {'scenario_id': 'original'}, self.root / 'trial', password=truth.EvaluatorPassword(SECRET))
            truth_file.write_bytes(kept)


def read_spy(before=None):
    """A patch of `read_password` that reads as usual, and counts: (patcher, [paths read]). `before()` runs at each read."""
    real, paths = truth.read_password, []

    def read(path):
        paths.append(str(path))
        if before:
            before()
        return real(path)
    return patch.object(truth, 'read_password', side_effect=read), paths


class TrialPasswordTests(HarnessTrialFixture):
    refused_unlocks = ()

    def run_failing(self, name, password_file, **given):
        """A trial whose admission must never run: (trial dir, record, every configurator call)."""
        calls = []

        def call(method, params=None):
            calls.append(method)
            return self.configurator(method, params)
        trial = self.root / 'bench' / name
        request = {'trial_id': name, 'home': str(self.home), 'case': 'tq9-v2', 'goal': 'g',
                   **({'password_file': str(password_file)} if password_file else {})}
        with patch.object(harness, 'admit', side_effect=AssertionError('a device was started')):
            record = harness.run_trial(trial, request, call=call, **given)
        return trial, record, calls

    def test_02_B8_an_unreadable_or_empty_file_fails_the_trial_before_any_configurator_call_or_device(self):
        for name, content, named in (('missing', None, 'FileNotFoundError'), ('empty', b' \r\n', 'ValueError'),
                                     ('not-utf8', b'QUOTED\xff\xfe', 'ValueError')):
            path = self.root / ('private-handle-' + name)
            if content is not None:
                path.write_bytes(content)
            with self.subTest(file=name):
                trial, record, calls = self.run_failing(name, path)
                self.assertEqual(record['status'], 'failed')
                self.assertEqual(record['reason'], 'The evaluator password file of tq9-v2 cannot be read: ' + named)
                self.assertEqual(calls, [])
                self.assertEqual(harness.read_trial(trial), record)
                written = (trial / 'trial.json').read_text()
                for secret in (str(path), path.name, 'QUOTED'):
                    self.assertNotIn(secret, written)

    def test_02_B8_the_file_is_read_once_before_the_first_configurator_contact_unless_given_the_object(self):
        calls = []
        real = self.configurator
        self.configurator = lambda method, params=None, home=None, **kwargs: calls.append(method) or real(method, params)
        seen = []
        spy, paths = read_spy(before=lambda: seen.append(list(calls)))
        unlocked = []
        with spy, patch(CASE_TRUTH, side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            _, record = self.trial('read-once')
        self.assertEqual(record['status'], 'evaluated', record)
        self.assertEqual(paths, [str(self.password)])
        self.assertEqual(seen, [[]])
        self.assertTrue(calls)
        # A suite's harness passes the object it holds: the trial reads nothing, and no path goes with it.
        spy, paths = read_spy()
        with spy:
            given = truth.EvaluatorPassword('synthetic fixture credential')
            provider_trial, failed, _ = self.run_failing('given', None, password=given)
        self.assertEqual(paths, [])
        self.assertIn('a device was started', failed['reason'])

    def test_02_B8_a_trial_given_no_password_fails_naming_the_password_as_required(self):
        from generative_driver.benchmark_support.emulator import truth_for_case
        with self.assertRaisesRegex(ValueError, 'Evaluator password file is required'):
            truth_for_case(self.root, {'truth': {'path': 'x', 'sha256': '0' * 64}}, None)
        trial = self.root / 'bench/no-password'
        spy, paths = read_spy()
        with spy:
            record = harness.run_trial(trial, {'trial_id': 'none', 'home': str(self.home), 'case': 'tq9-v2', 'goal': 'g',
                                               'case_options': {'scenario_id': 'original'}}, call=self.configurator)
        self.assertEqual(record['status'], 'failed')
        self.assertIn('requires the evaluator password file', record['reason'])
        self.assertEqual(paths, [])
        self.assertFalse((trial / 'device').exists())
        with self.assertRaisesRegex(ValueError, 'needs the evaluator password file'):
            harness.evaluation_token(trial, None, 'none')


class AdmitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.password = truth.EvaluatorPassword(SECRET)

    def test_02_B20_an_emulated_v2_case_decrypts_once_and_its_provider_receives_only_the_recipe(self):
        from test_harness import RECIPE
        _, path = sealed_case(self, self.root / 'resources', self.password)
        admits_calibration(self)
        with UnlockSpy(path) as spy:
            provider, admission, unlocked = harness.admit('tq9-v2', {'scenario_id': 'original'}, self.root / 'trial',
                                                          password=self.password, renode='scripted-emulator')
        self.assertEqual(spy.count, 1)
        self.assertEqual(provider.recipe, RECIPE)
        self.assertEqual(set(admission), {'calibration_evidence_sha256'})
        self.assertEqual((unlocked.payload, unlocked.sha256), (toy_truth(), registry.pin_case('tq9-v2', 'original', 0)['truth_sha256']))
        self.assertNotIn(SENTINEL, json.dumps(provider.descriptor() if provider.session else provider.recipe))

    def test_02_B20_a_legacy_emulated_case_decrypts_once(self):
        from tq9_workflow_fixture import legacy_truth
        path = legacy_case(self, self.root / 'resources', 'tq9', self.password, legacy_truth())
        with UnlockSpy(path) as spy:
            provider, admission, unlocked = harness.admit('tq9', {}, self.root / 'trial', password=self.password, renode='r')
        self.assertEqual((spy.count, admission), (1, {}))
        self.assertEqual(provider.recipe, harness.device_recipe('tq9', unlocked))
        self.assertEqual(unlocked.payload, legacy_truth())
        self.assertNotIn('reuse_goal', json.dumps(provider.recipe))

    def test_02_B20_a_bound_device_case_authenticates_the_password_and_keeps_the_truth_for_its_grade(self):
        payload = {'max_reference_age_seconds': 60, 'max_tolerances': {'temperature': .2}}
        path = legacy_case(self, self.root / 'resources', 'bme280', self.password, payload)
        with UnlockSpy(path) as spy:
            provider, admission, unlocked = harness.admit('bme280', {}, self.root / 'trial', password=self.password)
        self.assertEqual((provider, admission, unlocked.payload, spy.count), (None, {}, payload, 1))
        with self.assertRaisesRegex(ValueError, 'invalid password'):
            harness.admit('bme280', {}, self.root / 'trial', password=truth.EvaluatorPassword('another'))


class SealedTrialFixture(HarnessTrialFixture):
    """A real admission of a case whose answer key is really sealed: the spies count real decryptions."""
    refused_unlocks = ()

    def setUp(self):
        super().setUp()
        self.literal = truth.EvaluatorPassword('synthetic fixture credential')
        self.truth_file = self.root / 'resources/groundtruth/tq9-v2.enc'
        manifest = json.loads((self.image.parent / 'case.json').read_text())
        manifest['truth']['sha256'] = self.literal.seal(toy_truth(), self.truth_file)
        manifest['calibration']['evidence_sha256'] = canonical_digest(toy_truth()['calibration'])
        (self.image.parent / 'case.json').write_text(json.dumps(manifest))
        admits_calibration(self)
        guard = patch('generative_driver.benchmark_support.native.NativeSession.start',
                      side_effect=silicon_start(self, [], self.root))
        guard.start(); self.addCleanup(guard.stop)

    def real_trial(self, name, *, during=None, **options):
        """A trial through the real `admit`; `during()` runs once, at the first status read of its started run."""
        pending = [during] if during else []

        def call(method, params=None):
            if method == 'status' and pending:
                pending.pop()()
            return self.configurator(method, params)
        trial = self.root / 'bench' / name
        with scripted_stages(self.controller, check=self.check(**options)):
            record = harness.run_trial(trial, {'trial_id': name, 'home': str(self.home), 'goal': harness.GOAL, 'case': 'tq9-v2',
                                               'case_options': {'scenario_id': 'original'}, 'effects': ['write', 'actuate'],
                                               'budget_seconds': 120, 'password_file': str(self.password),
                                               'renode': str(self.root / 'renode')}, call=call)
        return trial, record


class UnlockOnceTests(SealedTrialFixture):
    def test_02_B11_a_fresh_final_trial_decrypts_its_answer_key_once(self):
        graded = []

        def execute(pin, unlocked, *args, **kwargs):
            graded.append(unlocked)
            return reference(PASS)(pin, unlocked, *args, **kwargs)
        with UnlockSpy(self.truth_file) as spy, patch.object(evaluator, 'reference_execute', side_effect=execute):
            trial, record = self.real_trial('once')
        self.assertEqual((record['status'], record['equivalence']['equivalent']), ('evaluated', True), record)
        self.assertEqual(spy.count, 1)
        self.assertEqual(graded, [toy_truth()])
        self.assertTrue((trial / 'evaluation-token.enc').is_file() and (trial / 'evaluator-draft.enc').is_file())
        # The token and the draft still unlock as they do today, and a resume that finds the verdict opens no answer key.
        with UnlockSpy() as spy:
            self.resume(trial)
        self.assertEqual(spy.opened, [trial / 'evaluation-token.enc'])

    def test_02_B12_deleting_or_replacing_the_files_once_the_run_started_changes_neither_grade_nor_seal(self):
        kept, secret = self.truth_file.read_bytes(), self.password.read_bytes()
        fields = ('status', 'equivalent', 'checks', 'diff', 'passed', 'total', 'method', 'protocol')

        def deleted():
            self.truth_file.unlink(); self.password.unlink()

        def replaced():
            self.truth_file.write_bytes(b'a worker changed this'); self.password.write_text('another credential')
        with patch.object(evaluator, 'reference_execute', side_effect=reference(FAIL)):
            _, undisturbed = self.real_trial('undisturbed')
        self.assertEqual((undisturbed['status'], undisturbed['equivalence']['equivalent']), ('evaluated', False), undisturbed)
        for name, change in (('deleted', deleted), ('replaced', replaced)):
            with self.subTest(change=name), patch.object(evaluator, 'reference_execute', side_effect=reference(FAIL)):
                trial, record = self.real_trial(name, during=change)
                self.assertEqual(record['status'], 'evaluated', record)
                self.assertIs(record['sealed'], True)
                self.assertEqual({key: record['equivalence'][key] for key in fields},
                                 {key: undisturbed['equivalence'][key] for key in fields})
                sealed = self.literal.unlock(trial / 'run-evidence.enc', record['equivalence']['evidence_sha256'])
                self.assertEqual(sealed['equivalence']['status'], 'graded')
                self.assertIsNotNone(sealed['final'])
                self.assertIsNone(sealed['evaluation_error'])
            self.truth_file.write_bytes(kept); self.password.write_bytes(secret)


class GraderSourceTests(unittest.TestCase):
    def calls(self, module):
        tree = ast.parse((SOURCE / 'benchmark_support' / module).read_text(encoding='utf-8'))
        return {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, (ast.Name, ast.Attribute))}

    def test_02_B13_the_grader_gets_the_answer_key_only_from_truth_source(self):
        for module in ('evaluator.py', 'recorded_grading.py'):
            with self.subTest(module=module):
                self.assertFalse(self.calls(module) & {'case_truth', 'truth_for_case', 'admitted_truth'})
                self.assertIn('truth_source', (SOURCE / 'benchmark_support' / module).read_text(encoding='utf-8'))
        self.assertFalse(hasattr(evaluator, 'case_truth'))
        self.assertIn('truth_source', self.calls('evaluator.py'))


class GradeTruthSourceTests(HarnessTrialFixture):
    def source(self):
        def asked():
            self.asked.append(1)
            return self.unlocked_truth()
        self.asked = []
        return asked

    def grade(self, name, run, result, **given):
        run_dir = self.home / 'runs' / run['run_id']
        return evaluator.grade('tq9-v2', self.root / 'grading' / name, run_dir, result, password=self.password_object(),
                               truth_source=self.source(), renode='scripted-emulator', **given)

    def test_02_B14_grade_asks_for_the_answer_key_only_when_it_grades_and_in_todays_order(self):
        unreadable = lambda package: (package / 'driver/model.json').write_text('{not json')
        for name, options, status, reason in (
                ('no-driver', {'emit': False}, 'no-driver', 'no driver emitted'),
                ('no-mapping', {'mapping': False}, 'graded', 'no capability mapping'),
                ('no-mapping-and-unreadable-model', {'mapping': False, 'edit': unreadable}, 'graded', 'no capability mapping'),
                ('unreadable-model', {'edit': unreadable}, 'graded', "the frozen driver's model is missing or not JSON")):
            with self.subTest(case=name), patch.object(evaluator, 'reference_execute', side_effect=AssertionError('device')):
                run, result = self.pending(**options)
                (self.root / 'grading' / name).mkdir(parents=True)
                equivalence, final = self.grade(name, run, result)
                self.assertEqual(self.asked, [])
                self.assertEqual((equivalence['status'], [d['reason'] for d in equivalence['diff']], final), (status, [reason], None))

    def test_02_B14_grade_asks_once_and_never_when_the_draft_holds_the_final(self):
        run, result = self.pending()
        (self.root / 'grading/held').mkdir(parents=True)
        with patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            first, final = self.grade('held', run, result)
            self.assertEqual(self.asked, [1])
        self.assertEqual(first['status'], 'graded')
        with patch.object(evaluator, 'reference_execute', side_effect=AssertionError('graded again')):
            again, held = self.grade('held', run, result)
        self.assertEqual(self.asked, [])
        self.assertEqual((again, held), (first, final))


class AnswerKeyIntegrityTests(HarnessTrialFixture):
    def test_02_B15_an_answer_key_the_pin_does_not_name_is_not_graded(self):
        wrong = truth.UnlockedTruth({'recipe': {}}, 'f' * 64)
        with patch.object(evaluator, 'reference_execute', side_effect=AssertionError('graded')) as grading:
            trial, record = self.trial('wrong-key', truth=wrong)
        grading.assert_not_called()
        equivalence = record['equivalence']
        self.assertEqual((record['status'], equivalence['status'], equivalence['equivalent']), ('unscored', 'unscored', None), record)
        self.assertTrue(equivalence['diff'][0]['reason'].startswith('integrity: '), equivalence)
        # It seals as other integrity failures do: the handoffs and why, and no final.
        self.assertIs(record['sealed'], True)
        sealed = self.password_object().unlock(trial / 'run-evidence.enc', record['evidence']['sha256'])
        self.assertIsNone(sealed['final'])
        self.assertTrue(sealed['evaluation_error'])
        # The one helper hands a key the pin names on as its payload.
        self.assertEqual(evaluator.answer_key(lambda: self.unlocked_truth({'a': 1}), {'truth_sha256': self.unlocked_truth().sha256}), {'a': 1})


class ResumeDecryptTests(HarnessTrialFixture):
    def test_02_B17_resume_decrypts_once_only_when_it_grades(self):
        unlocked = []
        with patch(CASE_TRUTH, side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=OSError('host grading interrupted')):
            trial, record = self.trial('interrupted')
        self.assertEqual(record['status'], 'unscored')
        unlocked.clear()
        with patch(CASE_TRUTH, side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(FAIL)):
            resumed = self.resume(trial, renode='scripted-emulator')
        self.assertEqual((resumed['status'], len(unlocked)), ('evaluated', 1), resumed)
        self.assertEqual(unlocked[0][:2], ('tq9-v2', self.home / 'runs' / record['run_id']))
        self.assertIsInstance(unlocked[0][2], truth.EvaluatorPassword)
        # A verdict recorded: nothing is decrypted.
        with patch(CASE_TRUTH, side_effect=AssertionError('truth unlocked')):
            again = self.resume(trial, renode='scripted-emulator')
        self.assertEqual(again['equivalence'], resumed['equivalence'])

    def test_02_B17_a_held_final_is_not_decrypted_for(self):
        unlocked = []
        with patch(CASE_TRUTH, side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)), \
                patch.object(evaluator, 'seal', side_effect=SystemExit('harness killed after grading')), \
                self.assertRaises(SystemExit):
            self.trial('held')
        with patch(CASE_TRUTH, side_effect=AssertionError('truth unlocked')), \
                patch.object(evaluator, 'reference_execute', side_effect=AssertionError('graded again')):
            resumed = self.resume(self.root / 'bench/held')
        self.assertEqual(resumed['status'], 'evaluated', resumed)

    def test_02_B17_a_run_stopped_without_a_pending_grade_is_not_decrypted_for(self):
        unlocked = []
        with patch(CASE_TRUTH, side_effect=self.truth(unlocked)), patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            trial, record = self.trial('stopped')
        harness._private(trial / 'trial.json', {**record, 'status': 'run-stopped'})
        with self.controller._db() as db:
            db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (record['run_id'],))
        with patch(CASE_TRUTH, side_effect=AssertionError('truth unlocked')), self.assertRaisesRegex(ValueError, 'awaits no evaluation'):
            self.resume(trial)

    def test_02_B17_a_password_that_does_not_unlock_the_token_is_refused_before_anything_else(self):
        unlocked = []
        with patch(CASE_TRUTH, side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=OSError('host grading interrupted')):
            trial, record = self.trial('another-password')
        with patch(CASE_TRUTH, side_effect=AssertionError('truth unlocked')), \
                patch.object(evaluator, 'grade', side_effect=AssertionError('graded')), \
                self.assertRaisesRegex(ValueError, 'password does not unlock'):
            harness.resume(trial, password=truth.EvaluatorPassword('another'), renode='scripted-emulator', call=self.configurator)


class SuitePasswordTests(HarnessSuiteFixture):
    SECRET_TEXT = 'synthetic fixture credential'

    def assertHoldsNoSecret(self, *written, password=None):
        path = str(password or self.password)
        for text in written:
            for secret in (path, Path(path).name, self.SECRET_TEXT, SENTINEL):
                self.assertNotIn(secret, text)

    def test_02_B5_every_supplied_file_is_read_once_before_the_first_configurator_contact(self):
        other = self.root / 'other-password-handle'
        other.write_text('another synthetic credential')
        calls, seen = [], []
        spy, paths = read_spy(before=lambda: seen.append(list(calls)))
        with spy, self.harness(PASS):
            record = self.drive(calls, manifest=suite_manifest(1), executor='codex', options={},
                                password_files={'tq9-v2': str(self.password), 'tq9': str(other)})
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(paths, [str(self.password), str(other)])
        self.assertEqual(seen, [[], []])
        self.assertEqual(calls[0][0], 'ping')

    def test_02_B5_a_file_two_cases_share_is_read_once(self):
        spy, paths = read_spy()
        with spy:
            held = harness._supplied_passwords({'tq9-v2': str(self.password), 'tq9': str(self.password)})
        self.assertEqual(paths, [str(self.password)])
        self.assertIs(held['tq9-v2'], held['tq9'])

    def test_02_B5_a_suite_whose_files_are_deleted_still_provides_grades_and_seals_each_trial(self):
        kept = self.password.read_text()
        self.addCleanup(lambda: self.password.write_text(kept))

        def intercept(method, params, forward):
            # The harness read its files before this, its first request past the ping.
            self.password.unlink(missing_ok=True)
            return forward(method, params)
        with self.harness(PASS, FAIL):
            record = self.drive(intercept=intercept, manifest=suite_manifest(2), executor='codex', options={})
        self.assertEqual(record['status'], 'finished', record)
        self.assertFalse(self.password.exists())
        status = self.controller.call('suite_status', {'suite_id': record['suite_id']})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 2, 1))
        self.assertEqual(len(self.peers), 2)
        literal = truth.EvaluatorPassword(self.SECRET_TEXT)
        for admitted in self.admitted:
            trial = harness.read_trial(admitted['trial'])
            self.assertEqual((trial['status'], trial['sealed']), ('evaluated', True), trial)
            literal.unlock(admitted['trial'] / 'run-evidence.enc', trial['evidence']['sha256'])

    def test_02_B6_an_unreadable_or_empty_file_ends_the_suite_with_no_configurator_call(self):
        from suite_fixtures import drive_suite
        for name, content, named in (('missing', None, 'FileNotFoundError'), ('empty', b'\n', 'ValueError'),
                                     ('not-utf8', b'QUOTED\xff', 'ValueError')):
            path = self.root / ('private-handle-' + name)
            if content is not None:
                path.write_bytes(content)
            directory, calls = self.root / 'home-bench/suites' / name, []

            def call(method, params=None):
                calls.append(method)
                return {'ok': True}
            for request in ({'manifest': suite_manifest(1), 'executor': 'codex'}, {'suite_id': 'suite-to-resume'}):
                with self.subTest(file=name, request=sorted(request)):
                    record = harness.run_suite(directory, {'home': str(self.home), 'password_files': {'tq9-v2': str(path)},
                                                           'renode': EMULATOR, **request}, call=call)
                    self.assertEqual((record['status'], record['accepted']), ('failed', False))
                    self.assertEqual(record['reason'], 'The evaluator password file of tq9-v2 cannot be read: ' + named)
                    self.assertEqual(calls, [])
                    written = (directory / 'suite.json').read_text()
                    self.assertEqual(json.loads(written)['reason'], record['reason'])
                    self.assertHoldsNoSecret(written, password=path)
                    self.assertNotIn('QUOTED', written)

    def test_02_B6_launch_suite_reports_the_reason_and_the_same_request_starts_afresh_once_fixed(self):
        path = self.root / 'private-handle'
        home = self.root / 'detached-home'
        request = {'manifest': suite_manifest(1), 'executor': 'codex', 'options': {}, 'request_id': 'fixed-later'}
        refused = harness.launch_suite(request, home=home, password_files={'tq9-v2': str(path)}, autostart=False, timeout=60)
        self.assertEqual(refused['ok'], False)
        self.assertEqual(refused['reason'], 'The evaluator password file of tq9-v2 cannot be read: FileNotFoundError')
        self.assertFalse(home.exists())
        path.write_text('synthetic fixture credential')
        again = harness.launch_suite(request, home=home, password_files={'tq9-v2': str(path)}, autostart=False, timeout=60)
        # Past the file now, it meets the absent configurator: the same request started a new harness.
        self.assertEqual(again['ok'], False)
        self.assertNotIn('cannot be read', again['reason'])
        self.assertNotEqual(again['suite_dir'], refused['suite_dir'])

    def test_02_B7_a_case_with_no_supplied_file_fails_only_when_a_slot_needs_its_password(self):
        with self.harness():
            record = self.drive(manifest=suite_manifest(1), executor='codex', options={}, password_files={})
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['reason'], 'The evaluator password file of tq9-v2 is required to provide and grade its device')
        self.assertTrue(record['accepted'])
        self.assertEqual(self.admitted, [])

    def test_02_B7_a_child_whose_verdict_is_recorded_settles_without_one(self):
        def intercept(method, params, forward):
            if method == 'suite_settle':
                return {'ok': False, 'reason': 'ConnectionResetError: the request never arrived'}
            return forward(method, params)
        with self.harness(PASS):
            record = self.drive(intercept=intercept, manifest=suite_manifest(1), executor='codex', options={})
        with self.harness():
            resumed = self.drive(suite_id=record['suite_id'], password_files={}, renode=None)
        self.assertEqual(resumed['status'], 'finished', resumed)
        self.assertEqual(self.controller.call('suite_status', {'suite_id': record['suite_id']})['status'], 'completed')

    def test_02_B7_a_child_whose_effect_is_uncertain_settles_without_one(self):
        spy, paths = read_spy()
        reservation = {'slot': {'case': 'tq9-v2', 'run_id': 'run-uncertain', 'trial_key': 'k', 'ordinal': 0,
                                'child_request_id': 'r'}, 'request': {'budget_seconds': 1}}
        status = {'ok': True, 'status': 'blocked', 'uncertain_effect': True, 'stopping': False}
        with spy:
            harness._suite_trial(self.root / 'home-bench/suites/one', reservation['slot'], reservation['request'],
                                 lambda method, params=None: status, home=self.home, passwords={}, renode=None,
                                 launch=None, resume_run=None, heartbeat=None, stop=None)
        self.assertEqual(paths, [])

    def test_02_B16_a_suite_that_succeeds_leaves_no_password_path_or_sentinel_in_any_call_or_record(self):
        self.fixture_payload = toy_truth()
        calls = []
        with self.harness(PASS):
            record = self.drive(calls, manifest=suite_manifest(1), executor='codex', options={}, request_id='clean')
        self.assertEqual(record['status'], 'finished', record)
        self.assertHoldsNoSecret(json.dumps(calls, default=str), json.dumps(record))
        directory = next((self.root / 'home-bench/suites').iterdir())
        trial = self.admitted[0]['trial']
        for path in [*(p for p in self.home.rglob('*') if p.is_file()), directory / 'suite.json', trial / 'trial.json']:
            content = path.read_bytes().decode('utf-8', 'replace')
            with self.subTest(file=path.name):
                self.assertHoldsNoSecret(content)

    def test_02_B16_the_failures_of_B6_and_B8_hold_none_either(self):
        path = self.root / 'private-unreadable-handle'
        calls = []

        def call(method, params=None):
            calls.append((method, params))
            return {'ok': True}
        directory = self.root / 'home-bench/suites/refused'
        suite = harness.run_suite(directory, {'home': str(self.home), 'password_files': {'tq9-v2': str(path)},
                                              'manifest': suite_manifest(1)}, call=call)
        trial = harness.run_trial(self.root / 'bench/refused', {'trial_id': 'refused', 'home': str(self.home), 'case': 'tq9-v2',
                                                                'password_file': str(path)}, call=call)
        self.assertEqual(calls, [])
        self.assertEqual((suite['status'], trial['status']), ('failed', 'failed'))
        self.assertHoldsNoSecret(json.dumps(suite), json.dumps(trial), (directory / 'suite.json').read_text(),
                                 (self.root / 'bench/refused/trial.json').read_text(), password=path)

    def test_02_B16_an_unlocked_truth_shows_no_answer_values_and_does_not_serialize(self):
        unlocked = truth.UnlockedTruth(toy_truth(), 'a' * 64)
        for shown in (repr(unlocked), str(unlocked), '{}'.format(unlocked), repr({'truth': unlocked})):
            self.assertNotIn(SENTINEL, shown)
            self.assertNotIn('recipe', shown)
        for value in (unlocked, {'truth': unlocked}):
            with self.assertRaises(TypeError):
                json.dumps(value)

    def test_02_B18_reattach_decrypts_once_in_its_admit_and_the_reattached_run_is_graded_from_that_truth(self):
        generations, graded = [], []
        real = self.admit

        def admit(case_id, options, trial_dir, *, password, renode=None):
            provider, admission, _ = real(case_id, options, trial_dir, password=password, renode=renode)
            generations.append(len(generations) + 1)
            return provider, admission, self.unlocked_truth({'recipe': {}, 'generation': generations[-1]})

        def execute(pin, unlocked, *args, **kwargs):
            graded.append(unlocked)
            return reference(PASS)(pin, unlocked, *args, **kwargs)
        self.admit = admit
        with self.harness(runtime=self.stopping_probe()):
            stopped = self.drive(manifest=suite_manifest(1), executor='codex', options={})
        self.assertEqual(generations, [1])
        with self.harness(), patch.object(evaluator, 'reference_execute', side_effect=execute), \
                patch(CASE_TRUTH, side_effect=AssertionError('decrypted outside admit')):
            resumed = self.drive(suite_id=stopped['suite_id'])
        self.assertEqual(resumed['status'], 'finished', resumed)
        self.assertEqual(generations, [1, 2])
        self.assertEqual(graded, [{'recipe': {}, 'generation': 2}])

    def test_02_B22_a_suite_resume_refuses_a_missing_or_replaced_truth_file(self):
        with self.harness(runtime=self.stopping_probe()):
            stopped = self.drive(manifest=suite_manifest(1), executor='codex', options={})
        truth_file = self.root / 'resources/groundtruth/tq9-v2.enc'
        kept = truth_file.read_bytes()
        for name, change, message in (('missing', truth_file.unlink, 'Case truth is unbuilt'),
                                      ('replaced', lambda: truth_file.write_bytes(b'replaced'), 'Case truth hash mismatch')):
            change()
            with self.subTest(name), self.harness(), self.assertRaisesRegex(ValueError, message):
                self.drive(suite_id=stopped['suite_id'])
            truth_file.write_bytes(kept)


class PasswordReadBoundaryTests(unittest.TestCase):
    READS = ('read_text', 'read_bytes', 'open')

    @staticmethod
    def names_password(node):
        for inner in ast.walk(node):
            for text in (getattr(inner, 'id', None), getattr(inner, 'attr', None), getattr(inner, 'name', None),
                         inner.value if isinstance(inner, ast.Constant) and isinstance(inner.value, str) else None):
                if isinstance(text, str) and 'password' in text.lower():
                    return True
        return False

    def violations(self, source, *, in_truth=False):
        found = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', None)
                if name in self.READS and self.names_password(node):
                    found.append('reads a password file: line ' + str(node.lineno))
                if name == 'EvaluatorPassword' and not in_truth:
                    found.append('builds an EvaluatorPassword: line ' + str(node.lineno))
            if (isinstance(node, ast.Attribute) and node.attr in ('seal', 'unlock') and isinstance(node.value, ast.Name)
                    and node.value.id == 'truth'):
                found.append('uses truth.' + node.attr + ': line ' + str(node.lineno))
            if isinstance(node, ast.ImportFrom) and (node.module or '').rpartition('.')[2] == 'truth' and (
                    {alias.name for alias in node.names} & {'seal', 'unlock'}):
                found.append('imports seal or unlock: line ' + str(node.lineno))
        return found

    def test_02_B4_the_walk_catches_each_way_around_read_password(self):
        for source in ("Path(password_file).read_text()", "handle.password.read_bytes()", "open(options['evaluator_password_file'])",
                       "os.open(password_path, 0)", "EvaluatorPassword('x')", "truth.EvaluatorPassword('x')",
                       "truth.seal({}, path, 'x')", "truth.unlock(path, 'x', 'y')", "from .truth import seal",
                       "from .truth import unlock as opened"):
            with self.subTest(source=source):
                self.assertTrue(self.violations(source))
        for source in ("Path(path).read_text()", "password.seal({}, path)", "password.unlock(path, digest)",
                       "evaluator.seal(trial)", "read_password(path)", "truth.read_password(path)"):
            with self.subTest(source=source):
                self.assertEqual(self.violations(source), [])

    def test_02_B4_the_package_reads_a_password_only_through_read_password(self):
        found = {}
        for path in sorted(SOURCE.parent.rglob('*.py')):
            problems = self.violations(path.read_text(encoding='utf-8'), in_truth=path == Path(truth.__file__).resolve())
            if problems:
                found[str(path.relative_to(SOURCE.parent))] = problems
        self.assertEqual(found, {})
        self.assertFalse(hasattr(truth, 'seal') or hasattr(truth, 'unlock'))


class CommandReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.handle = self.root / 'password-handle'
        self.handle.write_text(SECRET)
        self.password = truth.EvaluatorPassword(SECRET)

    def test_02_B9_score_reads_its_file_once(self):
        from generative_driver import benchmark
        for schema, regrade in (('benchmark-report/3', 'regrade_v3'), ('benchmark-report/2', 'regrade_v2')):
            spy, paths = read_spy()
            with self.subTest(schema=schema), spy, patch('generative_driver.benchmark_support.evidence.' + regrade,
                                                         return_value={'ok': True}) as grading:
                benchmark.score({'schema': schema}, str(self.handle), evidence_path=self.root / 'evidence')
                self.assertEqual(paths, [str(self.handle)])
                self.assertIsInstance(grading.call_args.args[2], truth.EvaluatorPassword)
        spy, paths = read_spy()
        with spy, patch('generative_driver.benchmark_support.evidence.regrade_v3', return_value={'ok': True}) as grading:
            benchmark.score({'schema': 'benchmark-report/3'})
        self.assertEqual((paths, grading.call_args.args[2]), ([], None))

    def test_02_B9_report_suite_reads_once_per_case_across_all_its_trials(self):
        from generative_driver.benchmark_support.suite_reporting import report_suite
        from test_suite_reporting import export_fixture
        call, _, _, evidence, passwords = export_fixture(self.root, one_case=True)
        self.assertEqual((len(evidence), len(passwords)), (2, 1))
        spy, paths = read_spy()
        with spy, patch('generative_driver.client.call', side_effect=call):
            result = report_suite('suite-toy', home=self.root, output=self.root / 'regraded', evidence_files=evidence, password_files=passwords)
        self.assertEqual(result['success']['passed'], 2)
        self.assertEqual(paths, [str(path) for path in passwords.values()])

    def corpus(self, passwords, admission=None):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        from test_corpus_preflight import public_case
        with public_case() as (catalog, row, pin):
            plan = plan_corpus(catalog, repetitions=1)
            spy, paths = read_spy()
            with spy, patch('generative_driver.benchmark_support.registry.require_calibration', **(admission or {})) as admitting:
                result = check_corpus(plan, {'evaluator_password_files': passwords(row['case'])})
        return next(case for case in result['cases'] if case['lineage'] == row['id']), result, paths, admitting

    def test_02_B9_check_corpus_reads_a_given_file_once(self):
        checked, _, paths, admitting = self.corpus(lambda case: {case: str(self.handle)},
                                                   {'return_value': {'ok': True, 'evidence_sha256': 'd' * 64}})
        self.assertEqual(paths, [str(self.handle)])
        self.assertIsInstance(admitting.call_args.args[1], truth.EvaluatorPassword)

    def test_02_B10_check_corpus_reports_the_same_statuses_as_today(self):
        checked, result, _, _ = self.corpus(lambda case: {}, {'side_effect': AssertionError('admitted')})
        self.assertEqual((checked['status'], [row['code'] for row in result['failures']]), ('missing-credential', ['missing-credential']))
        for name, passwords, admission in (
                ('an unreadable file', lambda case: {case: str(self.root / 'absent')}, {'side_effect': AssertionError('admitted')}),
                ('a wrong password', lambda case: {case: str(self.handle)}, {'side_effect': ValueError('invalid password')})):
            with self.subTest(name):
                checked, result, _, _ = self.corpus(passwords, admission)
                self.assertEqual([row['code'] for row in result['failures']], ['qualification-failed'])
                self.assertEqual(checked['status'], 'unqualified')
                self.assertNotIn(str(self.root), json.dumps(result))
        checked, result, _, _ = self.corpus(lambda case: {case: str(self.handle)},
                                            {'return_value': {'ok': True, 'evidence_sha256': 'd' * 64}})
        self.assertEqual((checked['status'], checked['evidence_sha256'], result['ok']), ('qualified', 'd' * 64, True))

    def test_02_B9_manage_reads_its_file_once_and_rekey_each_file_once(self):
        from generative_driver.benchmark_support.evaluator_cli import manage
        from tq9_workflow_fixture import legacy_truth
        legacy_case(self, self.root / 'resources', 'tq9', self.password, legacy_truth())
        new = self.root / 'new-handle'
        new.write_text('another synthetic credential')
        spy, paths = read_spy()
        with spy:
            manage('unlock', 'tq9', str(self.handle), self.root / 'unlocked')
        self.assertEqual(paths, [str(self.handle)])
        spy, paths = read_spy()
        with spy:
            result = manage('rekey', 'tq9', str(self.handle), self.root / 'rekeyed', str(new))
        self.assertEqual(paths, [str(self.handle), str(new)])
        self.assertEqual(truth.EvaluatorPassword('another synthetic credential').unlock(result['ciphertext'], result['sha256']), legacy_truth())

    def test_02_B9_manage_calibrate_reads_once_and_hands_the_object_to_the_native_calibration(self):
        from generative_driver.benchmark_support.evaluator_cli import manage
        for case, target in (('zephyr-modbus-v1', 'upstream_qualification.calibrate'), ('tq9-v2', 'authoring.calibrate')):
            spy, paths = read_spy()
            with self.subTest(case=case), spy, patch('generative_driver.benchmark_support.' + target, return_value={'ok': True}) as calibrating:
                manage('calibrate', case, str(self.handle), self.root / 'out')
                self.assertEqual(paths, [str(self.handle)])
                options = calibrating.call_args.args[1]
                self.assertIsInstance(options['evaluator_password'], truth.EvaluatorPassword)
                self.assertNotIn('evaluator_password_file', options)

    def test_02_B9_family_calibration_reads_its_sibling_file_once(self):
        from generative_driver.benchmark_support import family_calibration
        for name in ('renode', 'compiler'):
            (self.root / name).write_text('')
        ghidra, java = self.root / 'ghidra', self.root / 'java'
        (ghidra / 'support').mkdir(parents=True); (ghidra / 'support/analyzeHeadless').write_text('')
        (java / 'bin').mkdir(parents=True); (java / 'bin' / ('java.exe' if os.name == 'nt' else 'java')).write_text('')
        author = self.root / 'private' / 'author'; author.mkdir(parents=True)
        handle = author.parent / 'sampled-sensor-v1.password'
        handle.write_text(SECRET)
        output = self.root / 'output'

        def native(case, options):
            self.assertIsInstance(options['evaluator_password'], truth.EvaluatorPassword)
            self.assertNotIn('evaluator_password_file', options)
            output.mkdir(exist_ok=True)
            (output / 'calibration-record.json').write_text('{}')
        spy, paths = read_spy()
        with spy, patch.object(family_calibration, 'authoring_payload', return_value={'case_id': 'sampled-sensor-v1'}), \
                patch.object(family_calibration, 'validate_calibration', return_value={'ok': True}), \
                patch('generative_driver.benchmark_support.authoring.calibrate', side_effect=native):
            family_calibration.calibrate('sampled-sensor-v1', authoring_root=author, renode=self.root / 'renode', ghidra_home=ghidra,
                                         java_home=java, output_dir=output, compiler=self.root / 'compiler')
        self.assertEqual(paths, [str(handle)])
        for name, content in (('empty', b'\n'), ('absent', None)):
            handle.unlink(missing_ok=True)
            if content is not None:
                handle.write_bytes(content)
            with self.subTest(name), patch.object(family_calibration, 'authoring_payload', return_value={'case_id': 'sampled-sensor-v1'}), \
                    self.assertRaisesRegex(ValueError, 'Evaluator password handle required'):
                family_calibration.calibrate('sampled-sensor-v1', authoring_root=author, renode=self.root / 'renode', ghidra_home=ghidra,
                                             java_home=java, output_dir=self.root / 'empty-output', compiler=self.root / 'compiler')

    def test_02_B9_prepare_execution_reads_its_file_once(self):
        from generative_driver.benchmark_support import upstream_build
        build = self.root / 'build'; build.mkdir()
        image = b'\x7fELFtoy'
        (build / 'firmware.elf').write_bytes(image)
        digest = hashlib.sha256(image).hexdigest()
        (build / 'build-report.json').write_text(json.dumps({
            'schema': 'benchmark-upstream-build/1', 'case': 'zephyr-modbus-v1', 'execution': 'native-source-build', 'ok': True,
            'source': {'commit': upstream_build.SOURCE_COMMIT}, 'images': {'firmware.elf': digest}, 'rebuild_hashes': [digest, digest],
            'tool_versions': {}, 'execution_setup': {'method': 'native-sim', 'artifact': 'firmware.elf', 'substitutions': ['TTY UART'],
                                                     'validated_behavior': [], 'hardware_gaps': ['Test only']}}))
        spy, paths = read_spy()
        with spy, patch.object(upstream_build, 'native_recipe', return_value={'backend': 'native-sim'}), \
                patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value={}) as unlocking:
            upstream_build.prepare_execution(build, str(self.handle), self.root / 'resources')
        self.assertEqual(paths, [str(self.handle)])
        self.assertIsInstance(unlocking.call_args.args[2], truth.EvaluatorPassword)

    def test_02_B9_exercise_execution_reads_its_file_once(self):
        from generative_driver.benchmark_support.rehosting import exercise_execution
        spy, paths = read_spy()
        with spy, patch('generative_driver.benchmark_support.emulator.truth_for_case', side_effect=ValueError('stop here')) as unlocking, \
                self.assertRaisesRegex(ValueError, 'stop here'):
            exercise_execution('zephyr-modbus-v1', str(self.handle), self.root / 'output', renode='r')
        self.assertEqual(paths, [str(self.handle)])
        self.assertIsInstance(unlocking.call_args.args[2], truth.EvaluatorPassword)

    def test_02_B9_the_option_filters_drop_the_password_object(self):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        from generative_driver.benchmark_support.snapshots import public_configuration
        from generative_driver.reporting import _public
        self.assertEqual(public_configuration({'evaluator_password': self.password, 'kept': 1}), {'kept': 1})
        self.assertEqual(_public({'evaluator_password': self.password, 'kept': 1}, self.root), {'kept': 1})
        with self.assertRaisesRegex(ValueError, 'recipe only'):
            DeviceProvider('tq9-v2', self.root / 'image', {'evaluator_password': self.password}, None, self.root / 'device')


class TrialResumeCommandTests(HarnessTrialFixture):
    def test_02_B9_trial_resume_and_the_harness_resume_read_their_file_once(self):
        import contextlib
        import io
        import sys
        import types
        from generative_driver import benchmark
        unlocked = []
        with patch(CASE_TRUTH, side_effect=self.truth(unlocked)), patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            trial, record = self.trial('command')
        self.assertEqual(record['status'], 'evaluated', record)
        spy, paths = read_spy()
        with spy, patch('generative_driver.client.call', side_effect=self.configurator), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(benchmark.main(['trial-resume', str(trial), '--password-file', str(self.password)]), 0)
        self.assertEqual(paths, [str(self.password)])
        spy, paths = read_spy()
        request = types.SimpleNamespace(buffer=io.BytesIO(json.dumps({'password_file': str(self.password)}).encode()))
        with spy, patch('generative_driver.client.call', side_effect=self.configurator), patch.object(sys, 'stdin', request), \
                contextlib.redirect_stdout(io.StringIO()):
            harness.main(['--trial-dir', str(trial), '--resume'])
        self.assertEqual(paths, [str(self.password)])


SET = 'fixture-v1'


class GeneratedSetPasswordTests(unittest.TestCase):
    def setUp(self):
        from generated_fixtures import write_fixture_set
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'set'
        self.sha = write_fixture_set(self.root)

    def listing(self, root=None):
        return sorted(path for path in (root or self.root).parent.rglob('*'))

    def test_02_B21_generated_sets_take_the_password_object(self):
        from generated_fixtures import FIXTURE_IMAGE, PASSWORD
        from generative_driver.benchmark_support import generated_sets
        registrations = {SET: self.sha}
        manifest = generated_sets.load_set(self.root, SET, registrations=registrations)
        self.assertIsInstance(PASSWORD, truth.EvaluatorPassword)
        self.assertEqual(generated_sets.open_device(self.root, manifest, '0000d001', PASSWORD)['image'], FIXTURE_IMAGE)
        generated_sets.seal_qualification(self.root, {'schema': 'toy'}, PASSWORD)
        self.assertTrue(generated_sets.admitted(self.root, SET, '0000d001', PASSWORD, registrations=registrations))

    def test_02_B21_a_plain_string_is_refused_and_writes_no_file(self):
        from generated_fixtures import FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, fixture_mission
        from generative_driver.benchmark_support import generated_sets, set_qualification
        registrations = {SET: self.sha}
        manifest = generated_sets.load_set(self.root, SET, registrations=registrations)
        before = self.listing()
        fresh = self.root.parent / 'fresh'
        output = self.root.parent / 'output'
        for name, call in (
                ('seal_device', lambda: generated_sets.seal_device(fresh, {'id': '0000d001', 'level': 1, 'framing': 'text-lines'},
                                                                   image=FIXTURE_IMAGE, mission=fixture_mission('0000d001'),
                                                                   answer_key=FIXTURE_ANSWER_KEY, password=SECRET)),
                ('open_device', lambda: generated_sets.open_device(self.root, manifest, '0000d001', SECRET)),
                ('seal_qualification', lambda: generated_sets.seal_qualification(self.root, {'schema': 'toy'}, SECRET)),
                ('admitted', lambda: generated_sets.admitted(self.root, SET, '0000d001', SECRET, registrations=registrations)),
                ('qualify', lambda: set_qualification.qualify(self.root, SET, SECRET, renode=Path('/toy'), output_dir=output,
                                                              registrations=registrations))):
            with self.subTest(function=name), self.assertRaises((AttributeError, TypeError)):
                call()
            self.assertEqual(self.listing(), before, name)


if __name__ == '__main__':
    unittest.main()

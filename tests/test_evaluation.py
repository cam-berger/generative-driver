"""After the run: the bench harness grades a completed run's frozen driver once, on a fresh emulator of its own, seals
the run evidence in its trial directory and posts the verdict to the configurator with record_evaluation. Scripted
stages on a provided device of synthetic TCP peers, a toy contract and a scripted reference execution; never Renode,
real truth or a real password."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_configurator import scripted_stages, wait_stopped
from test_harness import Device, EVIDENCE, unstarted_provider

TOKEN = 'scripted harness token'
# A value only the evaluator's contract holds: no public record, trial record or configurator file may carry it.
EXPECTED = 271.828
PASS, FAIL = (EXPECTED, 370), (EXPECTED, 0)


def contract(sha, revision=0):
    return {'schema': 'benchmark-behavior/1', 'artifact_sha256': sha, 'checks': [
        {'id': 'final/temperature-0/temperature', 'revision': revision, 'kind': 'number', 'expected': EXPECTED,
         'absolute_tolerance': .01, 'channel': 'runtime-transcript', 'unit': 'degC'},
        {'id': 'final/effect-1/duty', 'revision': revision, 'kind': 'number', 'expected': 370,
         'absolute_tolerance': .002, 'channel': 'independent-monitor', 'unit': 'permille'}]}


def reference(values, calls=None, before=None):
    """A scripted final phase in reference_execute's shape: the toy contract, records with `values` and raw evidence."""
    def execute(pin, truth, model, capabilities, *, renode, image, output_dir, phase='final', package=None,
                expected_package_sha256=None, package_final=True):
        if before:
            before()
        if calls is not None:
            calls.append({'pin': pin, 'model': model, 'capabilities': capabilities, 'image': Path(image), 'phase': phase,
                          'package': Path(package), 'sha': expected_package_sha256, 'output': Path(output_dir)})
        sha, revision = expected_package_sha256, pin['revision']
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        (Path(output_dir) / 'raw.json').write_text(json.dumps({'events': [], 'monitor': EXPECTED}))
        checks = contract(sha, revision)['checks']
        return {'contract': contract(sha, revision),
                'records': [{'id': c['id'], 'task_id': c['id'].rsplit('/', 1)[-1], 'revision': revision, 'artifact_sha256': sha,
                             'channel': c['channel'], 'unit': c['unit'], 'value': value} for c, value in zip(checks, values)]}
    return execute


def evaluation(frozen, equivalent=True, **changes):
    """A well-formed equivalence/1 record as the harness posts it."""
    checks = [{'id': 'final/temperature-0/temperature', 'passed': True}, {'id': 'final/effect-1/duty', 'passed': equivalent}]
    return {'schema': 'equivalence/1', 'status': 'graded', 'equivalent': equivalent, 'basis': 'evaluator',
            'method': 'fresh-final', 'checks': checks,
            'diff': [] if equivalent else [{'id': 'final/effect-1/duty', 'reason': 'value mismatch'}],
            'passed': 2 if equivalent else 1, 'total': 2,
            'mission': {'name': 'fresh_worker_mission', 'passed': None, 'reason': 'No readable capability mapping was handed on'},
            'frozen_artifact_sha256': frozen, 'capabilities_sha256': 'c' * 64, 'evidence_sha256': 'e' * 64,
            'protocol': 'assistive/1', **changes}


def files(root):
    return [path for path in Path(root).rglob('*') if path.is_file()]


class PendingRunFixture(unittest.TestCase):
    """A completed emulated run awaiting its independent evaluation: scripted stages on a device the harness provides.
    The configurator unlocks no truth; the evaluator's truth is a toy one."""

    def setUp(self):
        from suite_fixtures import public_case_fixture
        from tq9_workflow_fixture import capabilities, model
        from generative_driver.configurator import Controller
        self.model = model(); self.model['channel'] = {'type': 'tcp'}
        self.mapping = capabilities()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        public_case_fixture(self.root / 'resources', calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
        self.image = self.root / 'resources/cases/tq9-v2/firmware.bin'
        guard = patch('generative_driver.benchmark.case_root', return_value=self.root / 'resources')
        guard.start(); self.addCleanup(guard.stop)
        for target in ('generative_driver.benchmark_support.registry.require_calibration',
                       'generative_driver.benchmark_support.emulator.truth_for_case'):
            refused = patch(target, side_effect=AssertionError('the configurator unlocked case truth'))
            refused.start(); self.addCleanup(refused.stop)
        self.password = self.root / 'password-handle'
        self.password.write_text('synthetic fixture credential')
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': {'command': ['scripted-runtime']}}}))
        self.controller = Controller(self.home)
        self.addCleanup(lambda: self.controller.close())

    def check(self, *, emit=True, mapping=True, tamper=False):
        """Scripted stage observations: probe hands on the model and its mapping, emit a package of that model."""
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'probe':
                folder = workspace / 'model'; folder.mkdir()
                (folder / 'model.json').write_text(json.dumps(self.model, indent=2) + '\n')
                found = [folder / 'model.json']
                if mapping:
                    (workspace / 'capabilities.json').write_text(json.dumps(self.mapping)); found.append(workspace / 'capabilities.json')
                return {'artifacts': [str(p) for p in found], 'checks': []}
            if stage == 'emit' and emit:
                package = workspace / 'package'; (package / 'driver').mkdir(parents=True)
                (package / 'manifest.json').write_text(json.dumps({'schema': 'interface-package/1', 'name': 'scripted'}))
                (package / 'driver/model.json').write_text(json.dumps(self.model, indent=2) + '\n')
                return {'artifacts': [str(package)], 'checks': [{'name': 'package_integrity_and_replay', 'passed': True}]}
            if stage == 'reuse' and tamper:
                # The accepted package changes after its handoff was recorded.
                emitted = next(h for h in reversed(accepted) if h['stage'] == 'emit')
                (Path(emitted['artifacts'][0]['path']) / 'driver/model.json').write_text('{}')
            return {'artifacts': [], 'checks': []}
        return check

    def pending(self, **options):
        """A completed run awaiting its evaluation, started as the harness starts one."""
        device = Device(self, self.image, self.root / ('device-' + str(len(list(self.root.glob('device-*'))))))
        with scripted_stages(self.controller, check=self.check(**options)):
            run = self.controller.start({'goal': 'g', 'case': 'tq9-v2', 'effects': ['write', 'actuate'],
                                         'case_options': {'scenario_id': 'original'},
                                         'executor_config': {'command': ['scripted-runtime']},
                                         'device': device.server.descriptor,
                                         'admission': {'calibration_evidence_sha256': EVIDENCE},
                                         'evaluation_token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest()})
            result = wait_stopped(self.controller, run)
        self.assertEqual((result['status'], result['progress']['equivalence']), ('completed', 'pending'), result)
        return run, result

    @staticmethod
    def emitted(result):
        return next(h['artifacts'][0]['sha256'] for h in reversed(result['accepted_handoffs'])
                    if h['stage'] == 'emit' and h['artifacts'])

    def events(self, run, kind):
        return [e['data'] for e in self.controller.call('events', run)['events'] if e['kind'] == kind]

    def verdicts(self, run):
        with self.controller._db() as db:
            return [(row['id'], json.loads(row['payload'])) for row in
                    db.execute("SELECT id,payload FROM verdicts WHERE run_id=?", (run['run_id'],))]


class RecordEvaluationTests(PendingRunFixture):
    def record(self, run, equivalence, token=TOKEN, controller=None):
        return (controller or self.controller).call('record_evaluation', {**run, 'token': token, 'equivalence': equivalence})

    def test_only_the_runs_harness_records_one_evaluation_of_the_accepted_driver(self):
        # Catches a forged or malformed verdict, one about another package, a grade published with its expected values,
        # an unscored grade taken for a verdict, and a second grade replacing the first.
        from generative_driver.configurator import Controller, IntegrityError, PENDING
        run, result = self.pending()
        frozen = self.emitted(result)
        good = evaluation(frozen, equivalent=False)
        checks = good['checks']
        for case, token, record, error, message in (
                ('forged token', 'forged token', good, ValueError, 'evaluation token'),
                ('no token', None, good, ValueError, 'evaluation token'),
                ('another package', TOKEN, {**good, 'frozen_artifact_sha256': '0' * 64}, IntegrityError, 'accepted emitted package'),
                ('no package', TOKEN, {**good, 'frozen_artifact_sha256': None}, IntegrityError, 'accepted emitted package'),
                ('expected values', TOKEN, {**good, 'checks': [{**checks[0], 'expected': EXPECTED}, checks[1]]}, ValueError, 'Invalid evaluation'),
                ('tolerances', TOKEN, {**good, 'tolerances': {'final/effect-1/duty': .002}}, ValueError, 'Invalid evaluation'),
                ('diff with a value', TOKEN, {**good, 'diff': [{**good['diff'][0], 'observed': 0}]}, ValueError, 'Invalid evaluation'),
                ('self-evidence', TOKEN, {**good, 'basis': 'self-evidence'}, ValueError, 'Invalid evaluation'),
                ('equivalent with a failed check', TOKEN, {**good, 'equivalent': True}, ValueError, 'Invalid evaluation'),
                ('not equivalent without a difference', TOKEN, {**good, 'diff': []}, ValueError, 'Invalid evaluation'),
                ('counts', TOKEN, {**good, 'passed': 2}, ValueError, 'Invalid evaluation'),
                ('unsealed grade', TOKEN, {**good, 'evidence_sha256': None}, ValueError, 'sealed'),
                ('boolean counts', TOKEN, {**good, 'passed': True}, ValueError, 'Invalid evaluation'),
                ('mission verdict as a number', TOKEN, {**good, 'mission': {'name': 'fresh_worker_mission', 'passed': 0}},
                 ValueError, 'Invalid evaluation'),
                ('another protocol', TOKEN, {**good, 'protocol': 'gated/0'}, ValueError, 'Invalid evaluation'),
                ('no record', TOKEN, 'equivalent', ValueError, 'Invalid evaluation')):
            with self.subTest(case=case), self.assertRaisesRegex(error, message):
                self.record(run, record, token)
        status = self.controller.call('status', run)
        self.assertEqual((status['status'], status['reason'], status['equivalence']), ('completed', PENDING, None))
        self.assertFalse(self.verdicts(run))
        self.assertFalse(self.events(run, 'run.finalized') + self.events(run, 'evaluation.unscored'))
        # An unscored evaluation is an event: the run still awaits its grade.
        unscored = evaluation(frozen, status='unscored', equivalent=None, checks=[], passed=0, total=0,
                              diff=[{'id': 'evaluation', 'reason': 'evaluator host fault: RuntimeError'}])
        self.record(run, unscored)
        self.assertEqual([e['diff'] for e in self.events(run, 'evaluation.unscored')], [unscored['diff']])
        status = self.controller.call('result', run)
        self.assertEqual((status['reason'], status['equivalence'], status['progress']['equivalence']), (PENDING, None, 'pending'))
        self.assertFalse(self.verdicts(run))
        # The graded evaluation is recorded once, as the run's final verdict.
        status = self.record(run, good)
        self.assertEqual((status['status'], status['equivalence']), ('completed', good))
        self.assertEqual(status['reason'], 'Final verdict: not equivalent: value mismatch')
        expected = [(run['run_id'] + ':final', {'stage': 'finalize', 'equivalence': good, 'protocol': 'assistive/1',
                                                'frozen_artifact_sha256': frozen})]
        self.assertEqual(self.verdicts(run), expected)
        result = self.controller.call('result', run)
        self.assertEqual((result['progress']['equivalence'], result['evaluator_verdicts']), ('graded', []))
        self.assertEqual(self.events(run, 'run.finalized'), [{'equivalent': False, 'basis': 'evaluator', 'diff': good['diff']}])
        # An identical repeat is idempotent; any other evaluation is refused and the first stands.
        self.assertEqual(self.record(run, good)['equivalence'], good)
        for other in (evaluation(frozen), unscored):
            with self.assertRaisesRegex(ValueError, 'different evaluation'):
                self.record(run, other)
        self.assertEqual(self.verdicts(run), expected)
        self.assertEqual(len(self.events(run, 'run.finalized')), 1)
        # A restart keeps the verdict; the token is stored nowhere.
        self.controller.close()
        reopened = Controller(self.home); self.addCleanup(reopened.close)
        self.assertEqual(reopened.call('status', run)['equivalence'], good)
        self.assertEqual(self.record(run, good, controller=reopened)['reason'], 'Final verdict: not equivalent: value mismatch')
        for path in files(self.home):
            self.assertNotIn(TOKEN.encode(), path.read_bytes(), path)

    def test_a_run_that_is_not_completed_or_is_left_uncertain_records_no_evaluation(self):
        # Catches a verdict written over a run that is still running or stopped, one whose effect is unreconciled, one
        # that awaits no independent grade, and a run the harness never started.
        from generative_driver.configurator import _json
        run, result = self.pending()
        good = evaluation(self.emitted(result))
        for case, change, message in (
                ('stopped', "UPDATE runs SET status='blocked' WHERE id=?", 'completed run'),
                ('uncertain', 'UPDATE runs SET uncertain=1 WHERE id=?', 'uncertain')):
            with self.subTest(case=case):
                with self.controller._db() as db:
                    saved = dict(db.execute('SELECT status,uncertain FROM runs WHERE id=?', (run['run_id'],)).fetchone())
                    db.execute(change, (run['run_id'],))
                with self.assertRaisesRegex(ValueError, message):
                    self.record(run, good)
                with self.controller._db() as db:
                    db.execute('UPDATE runs SET status=?,uncertain=? WHERE id=?', (saved['status'], saved['uncertain'], run['run_id']))
        with self.controller._db() as db:
            progress = json.loads(db.execute('SELECT payload FROM progress WHERE run_id=?', (run['run_id'],)).fetchone()['payload'])
            db.execute('UPDATE progress SET payload=? WHERE run_id=?', (_json({**progress, 'equivalence': None}), run['run_id']))
        with self.assertRaisesRegex(ValueError, 'awaits no independent evaluation'):
            self.record(run, good)
        generic = self.controller.start({'goal': 'g', 'executor_config': {'command': ['missing-runtime-b5']}})
        wait_stopped(self.controller, generic)
        with self.assertRaisesRegex(ValueError, 'bench harness'):
            self.record(generic, good)
        self.assertFalse(self.verdicts(run) + self.verdicts(generic))

    def test_cancelling_a_run_that_awaits_its_evaluation_leaves_it_completed(self):
        # Catches a cancellation after the run ended turning it cancelled, so that its grade could never be recorded.
        from generative_driver.configurator import PENDING
        run, result = self.pending()
        status = self.controller.call('cancel', run)
        self.assertEqual((status['status'], status['reason']), ('completed', PENDING))
        good = evaluation(self.emitted(result))
        self.assertEqual(self.record(run, good)['reason'], 'Final verdict: equivalent')


class HarnessGradingTests(PendingRunFixture):
    """The harness's finish: grade once after the run is terminal and the device stopped, seal, post."""

    def configurator(self, method, params=None, home=None, **kwargs):
        if method == 'ping':
            return {'ok': True}
        try:
            return self.controller.call(method, params or {})
        except Exception as error:
            return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}

    def trial(self, name='trial', **options):
        """One trial through the harness against the in-process configurator: (trial dir, trial record)."""
        from generative_driver.benchmark_support import harness
        trial = self.root / 'bench' / name
        self.peers = []
        provider = unstarted_provider(self, self.image, trial, self.peers)
        with scripted_stages(self.controller, check=self.check(**options)), \
                patch.object(harness, 'admit', return_value=(provider, {'calibration_evidence_sha256': EVIDENCE})), \
                patch('generative_driver.client.call', side_effect=self.configurator):
            record = harness.run_trial(trial, {'trial_id': name, 'home': str(self.home), 'goal': harness.GOAL, 'case': 'tq9-v2',
                                               'case_options': {'scenario_id': 'original'}, 'effects': ['write', 'actuate'],
                                               'budget_seconds': 120, 'password_file': str(self.password)})
        return trial, record

    def resume(self, trial):
        from generative_driver.benchmark_support import harness
        with patch('generative_driver.client.call', side_effect=self.configurator):
            return harness.resume(trial, password_file=self.password)

    def truth(self, unlocked):
        return lambda case_id, run_dir, options: unlocked.append((case_id, Path(run_dir), options)) or {'recipe': {}}

    def test_the_harness_grades_the_frozen_driver_once_after_the_run_ended_and_its_device_stopped(self):
        # Catches grading before the run is terminal or while the run's device still runs, grading another package or
        # mapping than the run accepted, a public verdict that carries expected values, evaluator evidence left in
        # plaintext or written into the configurator's home, and a verdict the configurator never records.
        from generative_driver.benchmark_support import evaluator, harness
        from generative_driver.benchmark_support.truth import unlock
        for name, values, equivalent in (('good', PASS, True), ('wrong', FAIL, False)):
            with self.subTest(driver=name):
                trial, observed, calls, unlocked = self.root / 'bench' / name, [], [], []
                def before(trial=trial):
                    record = harness.read_trial(trial)
                    status = self.controller.call('status', {'run_id': record['run_id']})
                    journal = [json.loads(line)['kind'] for line in (trial / 'device/journal.jsonl').read_text().splitlines()]
                    observed.append((status['status'], status['stopping'], journal[-1], record['device_stop']['confirmed']))
                with patch.object(evaluator, 'case_truth', side_effect=self.truth(unlocked)), \
                        patch.object(evaluator, 'reference_execute', side_effect=reference(values, calls, before)):
                    trial, record = self.trial(name)
                run = {'run_id': record['run_id']}
                result = self.controller.call('result', run)
                frozen = self.emitted(result)
                probe = next(h for h in result['accepted_handoffs'] if h['stage'] == 'probe')
                mapping = next(a for a in probe['artifacts'] if a['path'].endswith('capabilities.json'))
                self.assertEqual(observed, [('completed', False, 'stopped', True)])
                self.assertEqual(len(calls), 1); self.assertEqual(len(unlocked), 1)
                call = calls[0]
                self.assertEqual((call['phase'], call['sha'], call['capabilities'], call['model']),
                                 ('final', frozen, self.mapping, self.model))
                self.assertEqual((call['pin']['case_id'], call['pin']['revision']), ('tq9-v2', 0))
                self.assertTrue(call['package'].is_relative_to(trial)); self.assertTrue(call['output'].is_relative_to(trial))
                self.assertEqual(call['image'], self.home / 'runs' / record['run_id'] / 'benchmark/inputs/firmware.bin')
                self.assertEqual(unlocked[0][1], self.home / 'runs' / record['run_id'])
                # The verdict the harness posted is the one the configurator holds.
                equivalence = record['equivalence']
                self.assertEqual(record['status'], 'evaluated')
                self.assertEqual(result['equivalence'], equivalence)
                self.assertEqual(result['reason'], 'Final verdict: equivalent' if equivalent else
                                 'Final verdict: not equivalent: value mismatch')
                self.assertEqual(result['progress']['equivalence'], 'graded')
                self.assertEqual({key: equivalence[key] for key in ('schema', 'status', 'equivalent', 'basis', 'method', 'protocol',
                                                                    'frozen_artifact_sha256', 'capabilities_sha256', 'passed', 'total')},
                                 {'schema': 'equivalence/1', 'status': 'graded', 'equivalent': equivalent, 'basis': 'evaluator',
                                  'method': 'fresh-final', 'protocol': 'assistive/1', 'frozen_artifact_sha256': frozen,
                                  'capabilities_sha256': mapping['sha256'], 'passed': 2 if equivalent else 1, 'total': 2})
                self.assertEqual(equivalence['checks'], [{'id': 'final/temperature-0/temperature', 'passed': True},
                                                         {'id': 'final/effect-1/duty', 'passed': equivalent}])
                self.assertEqual(equivalence['diff'], [] if equivalent else [{'id': 'final/effect-1/duty', 'reason': 'value mismatch'}])
                self.assertEqual(equivalence['mission']['name'], 'fresh_worker_mission')
                # The sealed evidence lives in the trial directory and names the accepted driver it graded.
                self.assertEqual(record['evidence'], {'path': 'run-evidence.enc', 'sha256': equivalence['evidence_sha256']})
                payload = unlock(trial / 'run-evidence.enc', self.password.read_text(), equivalence['evidence_sha256'])
                self.assertEqual((payload['schema'], payload['protocol'], payload['run_id']),
                                 ('benchmark-run-evidence/2', 'assistive/1', record['run_id']))
                self.assertEqual((payload['final']['frozen_artifact_sha256'], payload['final']['contract']),
                                 (frozen, contract(frozen)))
                self.assertEqual(payload['equivalence'], {k: v for k, v in equivalence.items() if k != 'evidence_sha256'})
                rows = {(row['stage'], row['kind'], row['sha256']) for row in payload['accepted_artifacts']}
                for handoff in result['accepted_handoffs']:
                    self.assertLessEqual({(handoff['stage'], 'artifact', a['sha256']) for a in handoff['artifacts']}, rows)
                    self.assertIn((handoff['stage'], 'notes', handoff['notes']['sha256']), rows)
                journal = hashlib.sha256((trial / 'device/journal.jsonl').read_bytes()).hexdigest()
                self.assertEqual(payload['device'], {'receipts': [], 'journal_sha256': journal})
                # Nothing graded stays readable: no plaintext evaluation output, and no expected value anywhere public.
                self.assertFalse((trial / 'private').exists())
                for path in files(trial) + files(self.home):
                    self.assertNotIn(str(EXPECTED).encode(), path.read_bytes(), path)
                self.assertFalse([p for p in files(self.home) if p.name in ('run-evidence.enc', 'evaluator-draft.enc')])
                self.assertEqual(harness.read_trial(trial), record)

    def test_a_harness_that_died_after_grading_resumes_without_grading_again(self):
        # Catches a resumed harness grading the frozen driver a second time, and a resume that records nothing.
        from generative_driver.benchmark_support import evaluator, harness
        calls, unlocked = [], []
        with patch.object(evaluator, 'case_truth', side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(FAIL, calls)), \
                patch.object(evaluator, 'seal', side_effect=SystemExit('harness killed after grading')):
            with self.assertRaises(SystemExit):
                self.trial()
        trial = self.root / 'bench/trial'
        record = harness.read_trial(trial)
        self.assertEqual((record['status'], record['device_stop']['confirmed']), ('evaluating', True))
        self.assertTrue(record['finished']); self.assertTrue((trial / 'evaluator-draft.enc').is_file())
        run = {'run_id': record['run_id']}
        self.assertEqual(self.controller.call('result', run)['progress']['equivalence'], 'pending')
        self.assertEqual((len(calls), len(unlocked)), (1, 1))
        for attempt in (1, 2):
            with self.subTest(resume=attempt), \
                    patch.object(evaluator, 'case_truth', side_effect=AssertionError('truth unlocked again')), \
                    patch.object(evaluator, 'reference_execute', side_effect=AssertionError('graded again')):
                resumed = self.resume(trial)
            self.assertEqual(resumed['status'], 'evaluated')
            self.assertIs(resumed['equivalence']['equivalent'], False)
            self.assertEqual(resumed['equivalence']['diff'], [{'id': 'final/effect-1/duty', 'reason': 'value mismatch'}])
            self.assertEqual(self.controller.call('status', run)['equivalence'], resumed['equivalence'])
            self.assertEqual(len(self.verdicts(run)), 1); self.assertEqual(len(self.events(run, 'run.finalized')), 1)
            self.assertEqual(harness.read_trial(trial), resumed)

    def test_a_grade_is_posted_only_once_it_is_sealed(self):
        # Catches a verdict recorded whose evidence was never sealed, which no one could regrade.
        from generative_driver.benchmark_support import evaluator
        real_seal = evaluator.seal
        with patch.object(evaluator, 'case_truth', side_effect=self.truth([])), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)), \
                patch.object(evaluator, 'seal', side_effect=OSError('No space left on device')):
            trial, record = self.trial()
        run = {'run_id': record['run_id']}
        self.assertEqual((record['status'], record['sealed']), ('awaiting-evaluation', False))
        self.assertIn('No space left on device', record['sealing_failed'])
        self.assertIsNone(record['equivalence']['evidence_sha256'])
        self.assertIsNone(self.controller.call('status', run)['equivalence'])
        self.assertFalse(self.verdicts(run))
        with patch.object(evaluator, 'case_truth', side_effect=AssertionError('truth unlocked again')), \
                patch.object(evaluator, 'reference_execute', side_effect=AssertionError('graded again')), \
                patch.object(evaluator, 'seal', side_effect=real_seal):
            resumed = self.resume(trial)
        self.assertEqual((resumed['status'], resumed['sealed']), ('evaluated', True))
        self.assertEqual(self.controller.call('status', run)['equivalence'], resumed['equivalence'])
        self.assertEqual(resumed['equivalence']['evidence_sha256'], resumed['evidence']['sha256'])

    def test_a_final_the_draft_holds_is_rebuilt_never_graded_again(self):
        # Catches a saved final submission replayed on the device (ported from the in-loop final's replay guard): the
        # evaluator rebuilds its grade from the draft, with no truth unlocked and no emulator started.
        from generative_driver.benchmark_support import evaluator
        with patch.object(evaluator, 'case_truth', side_effect=self.truth([])), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(FAIL)):
            trial, record = self.trial()
        result = self.controller.call('result', {'run_id': record['run_id']})
        with patch.object(evaluator, 'case_truth', side_effect=AssertionError('truth unlocked again')), \
                patch.object(evaluator, 'reference_execute', side_effect=AssertionError('device accessed')):
            again, final = evaluator.grade('tq9-v2', trial, self.home / 'runs' / record['run_id'], result,
                                           password_file=self.password)
        self.assertEqual(again, {k: v for k, v in record['equivalence'].items() if k != 'evidence_sha256'})
        self.assertEqual(final['frozen_artifact_sha256'], self.emitted(result))
        self.assertFalse((trial / 'private/final').exists())

    def test_an_evaluator_host_fault_is_unscored_until_an_explicit_resume_grades(self):
        # Catches a host fault recorded as a failed driver, its message (which may name a hidden stimulus) published,
        # and an unscored grade that can never be completed.
        from generative_driver.benchmark_support import evaluator
        from generative_driver.benchmark_support.truth import unlock
        from generative_driver.configurator import PENDING
        unlocked = []
        with patch.object(evaluator, 'case_truth', side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=RuntimeError('monitor refused temp ' + str(EXPECTED))):
            trial, record = self.trial()
        run = {'run_id': record['run_id']}
        self.assertEqual(record['status'], 'unscored')
        self.assertEqual((record['equivalence']['status'], record['equivalence']['equivalent']), ('unscored', None))
        self.assertEqual(record['equivalence']['diff'], [{'id': 'evaluation', 'reason': 'evaluator host fault: RuntimeError'}])
        result = self.controller.call('result', run)
        self.assertEqual((result['status'], result['reason'], result['equivalence'], result['progress']['equivalence']),
                         ('completed', PENDING, None, 'pending'))
        self.assertEqual([e['diff'] for e in self.events(run, 'evaluation.unscored')], [record['equivalence']['diff']])
        for path in files(trial) + files(self.home):
            self.assertNotIn(str(EXPECTED).encode(), path.read_bytes(), path)
        # The handoffs are sealed all the same; the fault's message stays sealed with them.
        payload = unlock(trial / 'run-evidence.enc', self.password.read_text(), record['evidence']['sha256'])
        self.assertIsNone(payload['final']); self.assertIn(str(EXPECTED), payload['evaluation_error'])
        with patch.object(evaluator, 'case_truth', side_effect=self.truth(unlocked)), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            resumed = self.resume(trial)
        self.assertEqual((resumed['status'], resumed['equivalence']['equivalent']), ('evaluated', True))
        self.assertEqual(self.controller.call('status', run)['reason'], 'Final verdict: equivalent')

    def test_no_driver_no_mapping_or_a_changed_package_is_judged_without_the_device(self):
        # Catches an emulator started or truth unlocked for a run that emitted no driver or handed on no mapping, a
        # missing mapping scored as unscored rather than not equivalent, and a package changed after its handoff graded.
        from generative_driver.benchmark_support import evaluator
        for name, options, status, equivalent, reason in (
                ('no-package', {'emit': False}, 'no-driver', False, 'no driver emitted'),
                ('no-mapping', {'mapping': False}, 'graded', False, 'no capability mapping'),
                ('changed-package', {'tamper': True}, 'unscored', None, 'integrity: Accepted package bytes changed')):
            with self.subTest(case=name), \
                    patch.object(evaluator, 'case_truth', side_effect=AssertionError('truth unlocked')), \
                    patch.object(evaluator, 'reference_execute', side_effect=AssertionError('device started')):
                trial, record = self.trial(name, **options)
                equivalence = record['equivalence']
                self.assertEqual((equivalence['status'], equivalence['equivalent']), (status, equivalent))
                self.assertEqual([d['reason'] for d in equivalence['diff']], [reason])
                self.assertEqual(len(self.peers), 1)
                # Changed accepted bytes are never sealed either; the unscored grade carries no evidence.
                self.assertIs(record['sealed'], name != 'changed-package')
                self.assertEqual(equivalence['evidence_sha256'] is None, name == 'changed-package')
                result = self.controller.call('result', {'run_id': record['run_id']})
                if status == 'unscored':
                    self.assertEqual((record['status'], result['equivalence']), ('unscored', None))
                else:
                    self.assertEqual((record['status'], result['equivalence']), ('evaluated', equivalence))
                    self.assertEqual(result['reason'], 'Final verdict: not equivalent: ' + reason)
                self.assertEqual(equivalence['frozen_artifact_sha256'], None if name == 'no-package' else self.emitted(result))


class SealedEvidenceTests(PendingRunFixture):
    def test_sealed_run_evidence_grades_only_the_accepted_emitted_driver(self):
        # Catches sealing a final of another package than the run accepted, accepted bytes that changed, or a snapshot
        # whose identity no longer holds.
        from generative_driver.benchmark_support import evaluator
        from generative_driver.benchmark_support.truth import unlock
        run, result = self.pending()
        frozen = self.emitted(result)
        run_dir = self.home / 'runs' / run['run_id']
        final = {'run_id': run['run_id'], 'phase': 'final', 'revision': 0, 'frozen_artifact_sha256': frozen,
                 'capabilities_sha256': None, **{k: v for k, v in reference(PASS)(
                     {'revision': 0}, None, None, None, renode=None, image='x', output_dir=self.root / 'out',
                     expected_package_sha256=frozen, package=self.root).items()}, 'raw': None}
        sealed = evaluator.seal(self.root / 'trial', run_dir, result, password_file=self.password, final=final)
        payload = unlock(self.root / 'trial/run-evidence.enc', self.password.read_text(), sealed['sha256'])
        self.assertEqual(payload['final']['frozen_artifact_sha256'], frozen)
        other = {**final, 'frozen_artifact_sha256': '0' * 64, 'contract': contract('0' * 64),
                 'records': [{**r, 'artifact_sha256': '0' * 64} for r in final['records']]}
        with self.assertRaisesRegex(ValueError, 'accepted emit'):
            evaluator.seal(self.root / 'other', run_dir, result, password_file=self.password, final=other)
        snapshot = run_dir / 'benchmark/execution.json'
        saved = snapshot.read_bytes()
        snapshot.write_text(json.dumps({**json.loads(saved), 'executed': {'changed': True}}))
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            evaluator.seal(self.root / 'snapshot', run_dir, result, password_file=self.password, final=final)
        snapshot.write_bytes(saved)
        emitted = Path(next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')['artifacts'][0]['path'])
        (emitted / 'driver/model.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            evaluator.seal(self.root / 'changed', run_dir, result, password_file=self.password, final=final)
        self.assertFalse([p for p in files(self.root) if p.name == 'run-evidence.enc' and p.parent.name != 'trial'])


if __name__ == '__main__':
    unittest.main()

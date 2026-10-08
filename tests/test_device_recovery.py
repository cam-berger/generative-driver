"""Recovery through the device handle, and the device a stopped emulated run resumes on. The configurator holds no
evaluator code: the bench harness's provider replaces a device, the configurator commits the replacement, and a run
resumes only on a device its harness attaches. Synthetic TCP peers and a missing scripted runtime; never Renode, truth or
a password."""
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_configurator import wait_stopped
from test_device_provider import keys
from test_harness import Device, EVIDENCE, SOURCE

TOKEN = 'scripted harness token'
# Modules that decrypt truth or grade: neither the configurator nor its device modules import them.
FORBIDDEN = {'emulator', 'truth', 'calibration', 'emulated_evidence', 'reference_calibration', 'evaluator'}


def imported(path):
    """Every module a source file imports, at any depth (function bodies too), as written: '.x.y' for relative ones."""
    names = set()
    for node in ast.walk(ast.parse(Path(path).read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = '.' * node.level + (node.module or '')
            names |= {base} | {base + ('.' if node.module else '') + alias.name for alias in node.names}
    return names


class ImportBoundaryTests(unittest.TestCase):
    def test_the_configurator_and_its_device_modules_import_nothing_that_decrypts_truth_or_grades(self):
        for name in ('configurator.py', 'device_client.py', 'device_recovery.py'):
            found = imported(SOURCE / name)
            with self.subTest(module=name):
                self.assertFalse({n for n in found if FORBIDDEN & set(n.split('.'))}, found)
                self.assertFalse({n for n in found if 'emulated_recovery' in n.split('.')}, found)
        # Recovery reaches the device through its handle alone.
        self.assertLessEqual(imported(SOURCE / 'device_recovery.py'),
                             {'json', 'threading', 'time', '.device_client', '.device_client.open_device'})
        loaded = subprocess.run([sys.executable, '-c', 'import sys,json\n'
            'import generative_driver.configurator, generative_driver.device_client, generative_driver.device_recovery\n'
            'print(json.dumps(sorted(m for m in sys.modules if m.startswith("generative_driver"))))'],
            capture_output=True, text=True, check=True)
        self.assertFalse({m.rsplit('.', 1)[-1] for m in json.loads(loaded.stdout)} & FORBIDDEN)

    def test_the_in_process_device_and_evaluator_paths_are_gone(self):
        from generative_driver import benchmark
        from generative_driver.benchmark_support import emulated
        from generative_driver.configurator import Controller
        self.assertIsNone(importlib.util.find_spec('generative_driver.benchmark_support.emulated_recovery'))
        # The evaluator grades after the run, in the harness: its code left the stage adapter.
        self.assertIsNone(importlib.util.find_spec('generative_driver.benchmark_support.emulated_evidence'))
        for name in ('_OWNERS', '_truth', '_session', 'cleanup', 'package_execute', 'worker_tool', 'recover_emulator',
                     'device_recipe', 'reference_execute', 'run_call'):
            self.assertFalse(hasattr(emulated, name), name)
        self.assertFalse(hasattr(benchmark, 'recover_emulator'))
        self.assertFalse(hasattr(Controller, '_evaluator_operation'))
        for name in ('configurator.py', 'benchmark_support/emulated.py'):
            self.assertNotIn('start_device', (SOURCE / name).read_text(encoding='utf-8'), name)


class ProvidedRunTests(unittest.TestCase):
    """An emulated run on a device the harness provides. Its configured runtime is missing, so every attempt stops at
    acquire for the operator, after which the run has released its device, as any stopped run has."""

    def setUp(self):
        from suite_fixtures import public_case_fixture
        from tq9_workflow_fixture import model
        from generative_driver.configurator import Controller
        self.model = model(); self.model['channel'] = {'type': 'tcp'}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        public_case_fixture(self.root / 'resources', calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
        self.image = self.root / 'resources/cases/tq9-v2/firmware.bin'
        guard = patch('generative_driver.benchmark.case_root', return_value=self.root / 'resources')
        guard.start(); self.addCleanup(guard.stop)
        # Nothing here unlocks the case truth: the harness admitted the case before the run.
        for target in ('generative_driver.benchmark_support.registry.require_calibration',
                       'generative_driver.benchmark_support.emulator.truth_for_case',
                       'generative_driver.benchmark_support.truth.unlock'):
            refused = patch(target, side_effect=AssertionError('the configurator unlocked case truth'))
            refused.start(); self.addCleanup(refused.stop)
        self.controller = Controller(self.root / 'home')
        self.addCleanup(lambda: self.controller.close())
        self.first = Device(self, self.image, self.root / 'first')
        self.run = self.controller.start({
            'goal': 'g', 'case': 'tq9-v2', 'effects': ['write', 'actuate'], 'case_options': {'scenario_id': 'original'},
            'executor_config': {'command': ['missing-runtime-b4']}, 'device': self.first.server.descriptor,
            'admission': {'calibration_evidence_sha256': EVIDENCE},
            'evaluation_token_sha256': hashlib.sha256(TOKEN.encode()).hexdigest()})
        self.run_id = self.run['run_id']
        self.stopped = wait_stopped(self.controller, self.run)

    def result(self):
        return self.controller.call('result', self.run)

    def events(self, kind=None):
        found = self.controller.call('events', self.run)['events']
        return [e['data'] for e in found if kind is None or e['kind'] == kind]

    def spec(self):
        return json.loads(self.controller._row(self.run_id)['spec'])

    def attach(self, device, token=TOKEN):
        return self.controller.call('attach_device', {**self.run, 'token': token, 'device': device.server.descriptor})

    def identities(self):
        with self.controller._db() as db:
            return {row['identity'] for row in db.execute('SELECT identity FROM bindings WHERE run_id=?', (self.run_id,))}

    def identity(self, binding):
        from generative_driver.configurator import binding_identity, _json
        return hashlib.sha256(_json(binding_identity(binding)).encode()).hexdigest()

    def uncertain_write(self, device):
        """The durable record a worker's write leaves when it reached the device and failed for the model, as the gateway
        records it: the run running at probe on `device`, its dispatch pending and the run uncertain."""
        from generative_driver.configurator import _json
        run_dir = self.root / 'home/runs' / self.run_id
        workspace = run_dir / 'work/probe/write'; workspace.mkdir(parents=True)
        binding = device.server.descriptor['binding']
        assignment = {'id': 'write', 'stage': 'probe', 'workspace': str(workspace), 'inputs': {},
                      'allowed_tools': ['interface_execute'], 'report_required': True, 'binding': binding,
                      'effects': ['write', 'actuate'], 'revision': 0}
        failed = {'schema': 'interface-result/1', 'ok': False, 'identity_verified': True, 'transport_open_attempted': True,
                  'transcript': [{'tx': 'D\n', 'rx': 'OK\n'}], 'outputs': {}, 'units': {},
                  'error': {'fault': 'model', 'code': 'reply', 'message': 'reply differs from the model'}}
        with self.controller._db() as db:
            db.execute("UPDATE runs SET status='running',stage='probe',uncertain=1,cancelled=0 WHERE id=?", (self.run_id,))
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                       ('write', self.run_id, 'probe', 'active', _json(assignment), time.time()))
            progress = {**self.controller._workflow_progress(self.run_id, db), 'next_stage': 'probe'}
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (self.run_id, _json(progress)))
            self.controller._claim_binding(binding, self.run_id, db)
            started = self.controller._event(self.run_id, 'tool.started', {'stage': 'probe', 'assignment_id': 'write',
                'name': 'interface_execute', 'effectful': True, 'actor': 'worker'}, db)
            db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)', (started, self.run_id, 'write', 'worker'))
            finished = self.controller._event(self.run_id, 'tool.finished', {'stage': 'probe', 'assignment_id': 'write',
                'name': 'interface_execute', 'ok': False, 'result': failed, 'dispatch_id': str(started),
                'effectful': True, 'effects_reconciled': False, 'actor': 'worker'}, db)
        return assignment, progress, started, finished

    def reconcile(self, assignment, progress):
        from generative_driver.device_recovery import reconcile
        spec = {**self.spec(), '_device': progress.get('device')}
        return reconcile(self.controller, self.run_id, spec, assignment, self.root / 'home/runs' / self.run_id, progress)

    def pending(self):
        with self.controller._db() as db:
            return [row['dispatch_id'] for row in db.execute('SELECT dispatch_id FROM pending_effects WHERE run_id=?',
                                                             (self.run_id,))]

    def test_a_stopped_run_resumes_only_on_a_verified_device_its_harness_attaches(self):
        from generative_driver.configurator import IntegrityError, SafetyError, _json
        self.assertEqual((self.stopped['status'], self.stopped['outcome_category'], self.stopped['stage']),
                         ('blocked', 'operator', 'acquire'))
        # The run released its device when it stopped; the harness stops it. Resuming on it stops for the operator,
        # before any worker or device call.
        self.assertTrue(self.first.provider.released)
        with self.assertRaisesRegex(ValueError, 'attach a verified device'):
            self.controller.call('resume', self.run)
        status = self.controller.call('status', self.run)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'operator'))
        self.assertIn('attach a verified device', status['reason'])
        self.assertEqual(len(self.events('stage.assigned')), 1)
        second = Device(self, self.image, self.root / 'second')
        descriptor = second.server.descriptor
        held = self.result()['progress']['device']
        for params, error, message in (
                ({'token': 'forged token'}, ValueError, 'evaluation token'),
                ({'token': None}, ValueError, 'evaluation token'),
                ({'device': {**descriptor, 'image_sha256': '0' * 64}}, IntegrityError, 'image'),
                ({'device': {**descriptor, 'control': {}}}, ValueError, 'control endpoint'),
                ({'device': {**descriptor, 'session_id': 'another-session'}}, ValueError, 'descriptor names'),
                # The device the run released no longer answers healthy: the harness must provide a verified one.
                ({'device': self.first.server.descriptor}, ValueError, 'descriptor names')):
            with self.subTest(message=message), self.assertRaisesRegex(error, message):
                self.controller.call('attach_device', {**self.run, 'token': TOKEN, 'device': descriptor, **params})
        for column, value, message in (('uncertain', 1, 'uncertain'), ('status', 'running', 'stopped')):
            with self.subTest(column=column):
                with self.controller._db() as db:
                    saved = db.execute('SELECT ' + column + ' FROM runs WHERE id=?', (self.run_id,)).fetchone()[0]
                    db.execute('UPDATE runs SET ' + column + '=? WHERE id=?', (value, self.run_id))
                with self.assertRaisesRegex(ValueError, message):
                    self.attach(second)
                with self.controller._db() as db:
                    db.execute('UPDATE runs SET ' + column + '=? WHERE id=?', (saved, self.run_id))
        # Nothing refused changed the run's device.
        self.assertEqual(self.result()['progress']['device'], held)
        self.assertEqual(self.spec()['device'], self.first.server.descriptor)
        self.assertFalse(self.events('device.attached'))
        self.attach(second)
        progress = self.result()['progress']
        self.assertEqual(progress['device'], {'session_id': descriptor['session_id'], 'binding': descriptor['binding'],
                                              'receipts': []})
        self.assertEqual(self.spec()['device'], descriptor)
        self.assertEqual(self.events('device.attached'), [{'session_id': descriptor['session_id'],
            'binding': descriptor['binding'], 'provider_id': descriptor['provider_id'],
            'previous': {'session_id': held['session_id'], 'binding': held['binding']}}])
        # Resume claims the attached device's binding: another active run that owns it refuses the resume.
        with self.controller._db() as db:
            now = time.time()
            db.execute("INSERT INTO runs(id,spec,status,stage,created,updated) VALUES('run-other','{}','running','probe',?,?)",
                       (now, now))
            db.execute("INSERT INTO bindings VALUES(?,'run-other')", (self.identity(descriptor['binding']),))
        with self.assertRaisesRegex(SafetyError, 'already owned'):
            self.controller.call('resume', self.run)
        with self.controller._db() as db:
            db.execute("DELETE FROM bindings WHERE run_id='run-other'")
            db.execute("DELETE FROM runs WHERE id='run-other'")
        self.controller.call('resume', self.run)
        stopped = wait_stopped(self.controller, self.run)
        self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']), ('blocked', 'operator', 'acquire'))
        self.assertEqual(len(self.events('stage.assigned')), 2)
        # The resumed run let go of the attached device; the first one saw nothing more, and no device reached a peer.
        self.assertTrue(second.provider.released)
        self.assertEqual([peer.device_commands for peer in self.first.peers + second.peers], [[], []])
        self.assertNotIn(TOKEN, _json(self.spec()) + json.dumps(self.events()))

    def test_an_emulated_run_records_no_stage_verdict_whatever_its_observation_carries(self):
        # Catches the legacy in-loop grading path (a stage verdict, the terminal frozen final) reaching an emulated run:
        # its stages are observed without truth, and only the evaluator grades it, after the run.
        from generative_driver.configurator import PENDING
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        graded = {'ok': True, 'artifacts': [], 'checks': [], 'final_evaluation': True, 'fault': 'model',
                  'evaluator': {'verdict': 'failed', 'passed': 0, 'total': 1, 'checks': [{'id': 'final/x', 'passed': False}]}}
        def execute(request, config, cancel):
            return {'runtime': 'scripted-contract-fixture', 'status': 'completed', 'elapsed_seconds': 0, 'usage': None,
                    'report': {'status': 'completed', 'summary': 'fixture', 'artifacts': [], 'checks': [], 'unresolved': []}}
        with patch.object(self.controller, '_prepare', return_value={'objective': 'fixture', 'inputs': [], 'allowed_tools': []}), \
                patch.object(self.controller, '_check', return_value=graded), \
                patch('generative_driver.configurator.execute', side_effect=execute):
            self.controller.call('resume', self.run)
            result = wait_stopped(self.controller, self.run)
        self.assertEqual((result['status'], result['reason']), ('completed', PENDING), result)
        self.assertEqual(result['evaluator_verdicts'], [])
        self.assertEqual(result['progress']['equivalence'], 'pending')
        self.assertNotIn('terminal_final_failure', result['progress'])
        self.assertFalse(self.events('evaluation.final_failed'))
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM verdicts WHERE run_id=?', (self.run_id,)).fetchone()[0], 0)

    def test_a_device_session_changed_outside_the_run_stops_its_resume_for_the_operator(self):
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        # The provider's session changes without the run: a resume names it, and nothing starts.
        second.provider.replace(second.provider.session_id, [], [])
        with self.assertRaisesRegex(ValueError, 'Device session changed; attach a verified device'):
            self.controller.call('resume', self.run)
        status = self.controller.call('status', self.run)
        self.assertEqual((status['status'], status['outcome_category'], status['reason']),
                         ('blocked', 'operator', 'Device session changed; attach a verified device'))
        self.assertEqual(len(self.events('stage.assigned')), 1)
        # Once the harness attaches the session it now holds, the run may resume on it.
        self.attach(second)
        self.controller.call('resume', self.run)
        self.assertEqual(wait_stopped(self.controller, self.run)['outcome_category'], 'operator')
        self.assertEqual(len(self.events('stage.assigned')), 2)

    def test_a_dispatch_on_a_binding_other_than_the_provided_device_is_refused_before_it_is_marked(self):
        from generative_driver.configurator import _json
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        assignment, progress, _, _ = self.uncertain_write(second)
        with self.controller._db() as db:
            db.execute('DELETE FROM pending_effects WHERE run_id=?', (self.run_id,))
            db.execute('UPDATE runs SET uncertain=0 WHERE id=?', (self.run_id,))
            wrong = {**assignment, 'id': 'wrong', 'binding': {'host': '127.0.0.1', 'port': 1}}
            db.execute("UPDATE assignments SET state='superseded' WHERE id='write'")
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)', ('wrong', self.run_id, 'probe', 'active', _json(wrong), time.time()))
        model = Path(assignment['workspace']) / 'model'; model.mkdir()
        (model / 'model.json').write_text(json.dumps(self.model))
        before = len(self.events('tool.started'))
        with self.assertRaisesRegex(ValueError, 'Assigned binding differs from the provided device'):
            self.controller.call('tool', {**self.run, 'assignment_id': 'wrong', 'name': 'interface_execute',
                                          'arguments': {'model_dir': str(model), 'operation': 'disarm', 'parameters': {}}})
        self.assertEqual(len(self.events('tool.started')), before)
        self.assertEqual([e['name'] for e in self.events('tool.refused')], ['interface_execute'])
        self.assertFalse(self.controller.call('status', self.run)['uncertain_effect'])
        self.assertEqual(self.pending(), [])
        self.assertEqual([peer.device_commands for peer in second.peers], [[]])

    def test_an_emulated_run_without_a_provided_device_neither_resumes_nor_dispatches(self):
        # A run recorded before the harness provided devices (no descriptor): the configurator holds no emulator to
        # start for it, so it neither resumes a stage nor dispatches a live call.
        from generative_driver.configurator import _json
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        assignment, _, _, _ = self.uncertain_write(second)
        with self.controller._db() as db:
            db.execute('DELETE FROM pending_effects WHERE run_id=?', (self.run_id,))
            db.execute('UPDATE runs SET uncertain=0,spec=? WHERE id=?',
                       (_json({k: v for k, v in self.spec().items() if k != 'device'}), self.run_id))
        model = Path(assignment['workspace']) / 'model'; model.mkdir()
        (model / 'model.json').write_text(json.dumps(self.model))
        with self.assertRaisesRegex(ValueError, 'runs only on the device the bench harness provides'):
            self.controller.call('tool', {**self.run, 'assignment_id': 'write', 'name': 'interface_execute',
                                          'arguments': {'model_dir': str(model), 'operation': 'disarm', 'parameters': {}}})
        self.assertEqual(len(self.events('tool.started')), 1)  # The recorded write only; nothing was dispatched.
        with self.controller._db() as db:
            db.execute("UPDATE runs SET status='blocked' WHERE id=?", (self.run_id,))
        with self.assertRaisesRegex(ValueError, 'start a new trial'):
            self.controller.call('resume', self.run)
        self.assertEqual(len(self.events('stage.assigned')), 1)
        self.assertEqual([peer.device_commands for peer in second.peers], [[]])

    def test_a_verified_replacement_commits_with_the_handoff_and_moves_the_binding_lease(self):
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        old = second.server.descriptor
        assignment, progress, started, finished = self.uncertain_write(second)
        checked = self.reconcile(assignment, progress)
        recovery = checked['recovery']
        receipt = recovery['receipt']
        self.assertEqual((receipt['schema'], receipt['old_session_id']), ('emulator-recovery/2', old['session_id']))
        self.assertEqual((recovery['unresolved_dispatch_ids'], recovery['unresolved_evidence_ids']),
                         ([str(started)], [str(finished)]))
        self.assertTrue(second.peers[0].closed); self.assertEqual(len(second.peers), 2)
        # Until the commit the write stays uncertain and the run's device record names the old session.
        self.assertEqual(self.pending(), [started])
        self.assertTrue(self.controller.call('status', self.run)['uncertain_effect'])
        self.assertEqual(self.result()['progress']['device']['session_id'], old['session_id'])
        progress.update(revision=1, next_stage='interpret', repairs=1, feedback=[{'source': 'interface_execute'}])
        handoff = self.controller._accept(self.run_id, assignment, {'artifacts': [], 'checks': []},
                                          self.root / 'home/runs' / self.run_id, Path(assignment['workspace']), progress,
                                          {'checks': []}, revise='Run evidence contradicts the model', recovery=recovery)
        self.assertEqual(handoff['route'], 'interpret')
        result = self.result()
        self.assertFalse(result['uncertain_effect']); self.assertEqual(self.pending(), [])
        self.assertEqual(result['progress']['device'], {'session_id': receipt['new_session_id'], 'binding': receipt['binding'],
                                                        'receipts': [receipt]})
        self.assertEqual((result['progress']['revision'], result['progress']['next_stage']), (1, 'interpret'))
        # The lease moved to the replacement's binding in the same commit; the stopped emulator's lease is released.
        self.assertEqual(self.identities(), {self.identity(receipt['binding'])})
        self.assertEqual(result['emulator_recovery_count'], 1)
        self.assertEqual(result['emulator_recoveries'][0]['receipt'], receipt)
        # The receipt carries the proof's hash; the proof body stays in the provider's journal.
        self.assertFalse(keys(result['emulator_recoveries']) & {'proof', 'old_startup_observation', 'new_startup_observation'})
        kinds = [e['kind'] for e in self.controller.call('events', self.run)['events']]
        self.assertEqual(kinds[-3:], ['stage.accepted', 'emulator.recovered', 'run.revision'])
        self.assertEqual(second.provider.status()['session_id'], receipt['new_session_id'])

    def test_a_crash_between_the_replacement_and_its_commit_stays_uncertain_on_restart(self):
        from generative_driver.configurator import Controller
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        old = second.server.descriptor
        assignment, progress, started, _ = self.uncertain_write(second)
        receipt = self.reconcile(assignment, progress)['recovery']['receipt']
        # The configurator stops before the replacement commits with the stage's handoff; another one starts.
        self.controller.close()
        self.controller = Controller(self.root / 'home')
        status = self.controller.call('status', self.run)
        self.assertEqual(status['status'], 'blocked'); self.assertTrue(status['uncertain_effect'])
        result = self.result()
        self.assertEqual(self.pending(), [started])
        self.assertEqual(result['progress']['device'], {'session_id': old['session_id'], 'binding': old['binding'],
                                                        'receipts': []})
        self.assertEqual(result['emulator_recovery_count'], 0)
        self.assertNotIn('probe', [h['stage'] for h in result['accepted_handoffs']])
        # The uncertain run keeps its lease on the binding it wrote to and holds none on the replacement's.
        self.assertEqual(self.identities(), {self.identity(old['binding'])})
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.controller.call('resume', self.run)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.attach(second)
        # After the operator reconciles, the provider's new session is not the run's: resume stops for the operator.
        self.controller.call('respond', {**self.run, 'message': 'checked the device', 'effect_resolution': 'confirmed_safe'})
        with self.assertRaisesRegex(ValueError, 'Device session changed; attach a verified device'):
            self.controller.call('resume', self.run)
        self.assertEqual(second.provider.status()['session_id'], receipt['new_session_id'])
        self.assertEqual(self.controller.call('status', self.run)['outcome_category'], 'operator')
        # The failed write ran once, outside this test, and nothing re-executed it.
        self.assertEqual([peer.device_commands for peer in second.peers], [[], []])

    def test_a_recovery_that_cannot_be_verified_keeps_the_write_uncertain_and_stops_for_safety(self):
        from generative_driver.device_client import DeviceClient
        second = Device(self, self.image, self.root / 'second')
        self.attach(second)
        assignment, progress, started, _ = self.uncertain_write(second)
        held = self.result()['progress']['device']
        original = DeviceClient.replace
        def forged(client, *args, **kwargs):
            return {**original(client, *args, **kwargs), 'old_session_id': 'another-session'}
        def dying(*args, **kwargs):
            second.provider.session.stop()
            raise RuntimeError('provider process died during the replacement')
        cases = (('no provided device', {'device': None}, None, 'Outstanding effect requires operator reconciliation'),
                 ('physical approval', {'_case_pin': {**self.spec()['_case_pin'], 'manifest': {
                     **self.spec()['_case_pin']['manifest'], 'approval_scope': 'bound-device'}}}, None,
                  'Outstanding effect requires operator reconciliation'),
                 ('forged receipt', {}, patch.object(DeviceClient, 'replace', forged), 'Emulator recovery unverified'),
                 ('provider died', {}, patch.object(second.provider, 'replace', side_effect=dying), 'Emulator recovery unverified'),
                 ('provider gone', {}, None, 'Emulator recovery unverified: Device provider unavailable'))
        from generative_driver.device_recovery import reconcile
        for name, changes, fault, reason in cases:
            with self.subTest(case=name):
                if name == 'provider gone':
                    second.server.close()
                spec = {**self.spec(), **changes, '_device': held}
                before = len(self.events('emulator.recovery_failed'))
                if fault is None:
                    checked = reconcile(self.controller, self.run_id, spec, assignment, self.root / 'home/runs' / self.run_id, progress)
                else:
                    with fault:
                        checked = reconcile(self.controller, self.run_id, spec, assignment, self.root / 'home/runs' / self.run_id, progress)
                self.assertEqual(checked['stop']['category'], 'safety'); self.assertIn(reason, checked['stop']['reason'])
                self.assertNotIn('recovery', checked)
                # The write stays uncertain and pending, the run's device record is unchanged, and nothing routed.
                self.assertEqual(self.pending(), [started])
                self.assertTrue(self.controller.call('status', self.run)['uncertain_effect'])
                self.assertEqual(self.result()['progress']['device'], held)
                self.assertFalse(self.events('run.revision')); self.assertFalse(self.events('emulator.recovered'))
                gated = reason.startswith('Outstanding')
                self.assertEqual(len(self.events('emulator.recovery_failed')), before + (0 if gated else 1))
        # The gate replaced nothing: only the forged receipt's replacement started a second emulator.
        self.assertEqual(len(self.events('emulator.recovery_started')), 3)
        self.assertEqual(len(second.peers), 2)
        self.assertEqual([peer.device_commands for peer in second.peers], [[], []])


if __name__ == '__main__':
    unittest.main()

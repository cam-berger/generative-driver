"""Suites driven by the bench harness. The configurator freezes and stores the suite and its slots; the harness reserves
each slot, provides the device, starts the child, waits, stops the device, grades the frozen driver after the run and
posts the verdict; the configurator settles the slot from the child's status and recorded evaluation, which it reads
itself. Scripted stages on synthetic TCP peers, a toy contract and a scripted reference execution; never Renode, real
truth or a real password."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from suite_fixtures import drive_suite
from test_configurator import scripted_stages
from test_evaluation import FAIL, PASS, PendingRunFixture, files, reference
from test_harness import EVIDENCE, unstarted_provider

EMULATOR = 'scripted-emulator-handle'


def manifest(repetitions=3):
    return {'schema': 'benchmark-suite/1', 'id': 'harness-suite', 'version': '1',
            'entries': [{'case': 'tq9-v2', 'scenario': 'original', 'case_seed': 0}], 'repetitions': repetitions,
            'child_budget_seconds': 120, 'suite_budget_seconds': 900, 'max_active_children': 1}


class HarnessSuiteFixture(PendingRunFixture):
    """A configured in-process configurator, a toy emulated case and a harness whose admission hands out one fresh
    provider of synthetic peers per trial."""

    def setUp(self):
        super().setUp()
        self.peers, self.admitted, self.graded = [], [], []

    def admit(self, case_id, options, trial_dir, *, password_file, renode=None):
        self.admitted.append({'case': case_id, 'trial': Path(trial_dir), 'password_file': password_file, 'renode': renode,
                              'options': dict(options)})
        return unstarted_provider(self, self.image, trial_dir, self.peers), {'calibration_evidence_sha256': EVIDENCE}

    def grading(self, *outcomes):
        """A scripted final phase per grading call, in order: values to grade, or an exception the evaluator meets."""
        queue = list(outcomes)
        def execute(*args, **kwargs):
            outcome = queue.pop(0)
            # The trial directory whose frozen driver copy is graded.
            self.graded.append(next(p for p in Path(kwargs['package']).parents if p.parent.name == 'trials').name)
            if isinstance(outcome, Exception):
                raise outcome
            return reference(outcome)(*args, **kwargs)
        return execute

    def harness(self, *outcomes, runtime=None):
        from contextlib import ExitStack
        from generative_driver.benchmark_support import evaluator, harness
        stack = ExitStack()
        stack.enter_context(scripted_stages(self.controller, check=self.check(), runtime=runtime))
        stack.enter_context(patch.object(harness, 'admit', side_effect=self.admit))
        stack.enter_context(patch.object(evaluator, 'case_truth', return_value={'recipe': {}}))
        stack.enter_context(patch.object(evaluator, 'reference_execute', side_effect=self.grading(*outcomes)))
        return stack

    def drive(self, calls=None, **request):
        return drive_suite(self.controller, self.root / 'home-bench/suites',
                           {'password_files': {'tq9-v2': str(self.password)}, 'renode': EMULATOR, **request}, calls=calls)

    def suite_rows(self):
        with self.controller._db() as db:
            return {table: [dict(row) for row in db.execute('SELECT * FROM ' + table)]
                    for table in ('suites', 'suite_trials', 'suite_events')}


class HarnessSuiteTests(HarnessSuiteFixture):
    def test_a_suite_settles_graded_children_and_stops_on_an_unscored_one_until_its_harness_resumes(self):
        # Catches a suite that advances past a child whose grade is unscored or still pending, a slot settled from an
        # outcome the harness reported rather than what the configurator holds, a child graded twice, and a suite
        # whose tables hold a password file or emulator path.
        calls = []
        with self.harness(PASS, FAIL, RuntimeError('evaluator host stopped')):
            record = self.drive(calls, manifest=manifest(), executor='codex', options={'scoped_tool_approval': 'emulator'},
                                request_id='three-children')
        self.assertEqual(record['status'], 'finished', record)
        sid = record['suite_id']
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['planned'], status['completed'], status['equivalent'], status['stopping']),
                         ('blocked', 3, 2, 1, False))
        page = self.controller.call('suite_result', {'suite_id': sid})
        rows = page['trials']
        self.assertEqual([(row['status'], row['equivalence_status'], row['equivalent']) for row in rows],
                         [('finished', 'graded', True), ('finished', 'graded', False), ('completed', 'unscored', None)])
        self.assertEqual(status['active_child_id'], rows[2]['run_id'])
        self.assertEqual(page['experiment']['comparison_identity']['protocol'], 'assistive/1')
        runs = [row['run_id'] for row in rows]
        self.assertEqual(len(set(runs)), 3)
        # Each child ran on its own device, which the harness provided with the case's password file and stopped.
        self.assertEqual([(a['password_file'], a['renode'], a['options']['scenario_id']) for a in self.admitted],
                         [(str(self.password), EMULATOR, 'original')] * 3)
        self.assertEqual(len(self.peers), 3); self.assertTrue(all(peer.closed for peer in self.peers))
        # Each child was graded once, after its run, in its own trial; the third grade met a host fault and is unscored.
        for run_id, expected in zip(runs, ('graded', 'graded', 'pending')):
            result = self.controller.call('result', {'run_id': run_id})
            self.assertEqual((result['status'], result['progress']['equivalence']), ('completed', expected))
        self.assertEqual(self.graded, [admitted['trial'].name for admitted in self.admitted])
        self.assertEqual(len(set(self.graded)), 3)
        self.assertEqual(len(self.events({'run_id': runs[2]}, 'evaluation.unscored')), 1)
        finished = [e['data'] for e in self.controller.call('suite_events', {'suite_id': sid})['events'] if e['kind'] == 'trial.finished']
        self.assertEqual([(e['ordinal'], e['equivalent'], e['equivalence_status']) for e in finished],
                         [(0, True, 'graded'), (1, False, 'graded')])
        # The configurator settles from what it reads itself: an outcome the harness names is ignored, and only its
        # harness may schedule the suite.
        token = next(params['token'] for method, params in calls if method == 'suite_reserve')
        forged = self.controller.call('suite_settle', {'suite_id': sid, 'token': token, 'ordinal': 2, 'status': 'completed',
                                                       'equivalence': {'status': 'graded', 'equivalent': True}})
        self.assertEqual(forged['decision'], 'block')
        # Nor may the harness attach a run the configurator does not find as the slot's child.
        with self.assertRaisesRegex(ValueError, 'not the child'):
            self.controller.call('suite_attach', {'suite_id': sid, 'token': token, 'ordinal': 2, 'run_id': runs[0]})
        for method in ('suite_reserve', 'suite_settle', 'suite_detach'):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, 'harness token'):
                self.controller.call(method, {'suite_id': sid, 'token': 'forged token', 'ordinal': 2})
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['completed'], 2)
        # Neither a password file, the password nor the emulator executable is in the suite tables or the database.
        tables = json.dumps(self.suite_rows())
        for secret in (str(self.password), self.password.read_text(), EMULATOR, 'evaluator_password'):
            self.assertNotIn(secret, tables)
        for path in files(self.home):
            for secret in (str(self.password).encode(), self.password.read_bytes(), EMULATOR.encode()):
                self.assertNotIn(secret, path.read_bytes(), path)
        frozen = self.controller._suites.get(sid)['frozen']
        self.assertEqual(frozen['scheduler'], 'harness/1')
        # The configurator holds the sha256 of the harness's token, never the token.
        self.assertEqual(frozen['harness_token_sha256'], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, tables)
        # A harness relaunched for the suite grades the unscored child once and the suite completes; no child is
        # started again and no settled child is graded again.
        with self.harness(PASS):
            resumed = self.drive(suite_id=sid)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent'], status['active_child_id']),
                         ('completed', 3, 2, None))
        rows = self.controller.call('suite_result', {'suite_id': sid})['trials']
        self.assertEqual([row['run_id'] for row in rows], runs)
        self.assertEqual((rows[2]['equivalence_status'], rows[2]['equivalent']), ('graded', True))
        self.assertEqual(self.graded[3:], [self.graded[2]])
        self.assertEqual(len(self.events({'run_id': runs[2]}, 'run.finalized')), 1)
        self.assertEqual(len(self.peers), 3)

    def test_a_resumed_suite_attaches_a_device_anew_to_its_stopped_child(self):
        # Catches a resumed suite that starts a second child for the slot, resumes the child on the device the previous
        # harness stopped, or never grades it.
        from generative_driver.benchmark_support.harness import read_trial
        stopped = []
        def runtime(request):
            if request.stage == 'probe' and not stopped:
                stopped.append(request.stage)
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                        'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
        with self.harness(runtime=runtime):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['stopping']), ('blocked', 0, False))
        child = {'run_id': status['active_child_id']}
        result = self.controller.call('result', child)
        self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('blocked', 'operator', 'probe'))
        trial = self.admitted[0]['trial']
        self.assertEqual((read_trial(trial)['status'], read_trial(trial)['device_stop']['confirmed']), ('run-stopped', True))
        first = result['progress']['device']
        # While the child's effect is uncertain nothing resumes: no device is provided, attached or started, the suite
        # stays blocked and the write is never retried.
        with self.controller._db() as db:
            db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (child['run_id'],))
        with self.harness(PASS, runtime=runtime), self.assertRaisesRegex(ValueError, 'uncertain'):
            self.drive(suite_id=sid)
        self.assertEqual((len(self.admitted), self.events(child, 'device.attached')), (1, []))
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'blocked')
        with self.controller._db() as db:
            db.execute('UPDATE runs SET uncertain=0 WHERE id=?', (child['run_id'],))
        with self.harness(PASS, runtime=runtime):
            resumed = self.drive(suite_id=sid)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        rows = self.controller.call('suite_result', {'suite_id': sid})['trials']
        self.assertEqual([row['run_id'] for row in rows], [child['run_id']])
        result = self.controller.call('result', child)
        self.assertEqual((result['status'], result['progress']['equivalence'], result['equivalence']['equivalent']),
                         ('completed', 'graded', True))
        # The same trial directory: its device was provided anew and attached with the trial's evaluation token.
        self.assertEqual([a['trial'] for a in self.admitted], [trial, trial])
        attached = self.events(child, 'device.attached')
        self.assertEqual(len(attached), 1)
        self.assertEqual(attached[0]['previous']['session_id'], first['session_id'])
        self.assertNotEqual(result['progress']['device']['session_id'], first['session_id'])
        journal = [json.loads(line)['kind'] for line in (trial / 'device/journal.jsonl').read_text().splitlines()]
        self.assertEqual(journal, ['started', 'stopped', 'started', 'stopped'])
        self.assertEqual((read_trial(trial)['status'], len(self.graded)), ('evaluated', 1))
        self.assertTrue(all(peer.closed for peer in self.peers))


    def test_a_harness_that_cannot_provide_a_device_leaves_the_suite_blocked_and_its_slot_reserved(self):
        # Catches a suite left running with no harness driving it, a second child started for the slot, and a slot
        # lost when its trial could not start.
        admit = self.admit
        def unavailable(*args, **kwargs):
            raise ValueError('emulator executable not found: ' + EMULATOR)
        with self.harness(PASS), patch('generative_driver.benchmark_support.harness.admit', side_effect=unavailable):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        self.assertEqual(record['status'], 'failed')
        self.assertIn('emulator executable not found', record['reason'])
        sid = record['suite_id']
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['stopping'], status['active_child_id'], status['completed']),
                         ('blocked', False, None, 0))
        self.assertEqual(status['reason'], 'Explicit suite resolution required')
        self.assertEqual([t['status'] for t in self.controller._suites.get(sid)['trials']], ['launching'])
        self.assertFalse(self.peers)
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
        # The relaunched harness takes the reserved slot up again in a trial of its own and completes the suite.
        self.admit = admit
        with self.harness(PASS):
            resumed = self.drive(suite_id=sid)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)

    def test_a_second_harness_cannot_take_over_a_suite_its_harness_still_drives(self):
        # Catches two harnesses driving one suite: a suite stopped (as a configurator restart stops it) while its
        # harness still waits for the child is not resumable until that harness has let go of it.
        import threading
        import time
        from suite_fixtures import settled_suite
        release = threading.Event()
        def runtime(request):
            if request.stage == 'acquire':
                release.wait(20)
        with self.harness(PASS, PASS, runtime=runtime):
            record = drive_suite(self.controller, self.root / 'home-bench/suites',
                                 {'password_files': {'tq9-v2': str(self.password)}, 'renode': EMULATOR,
                                  'manifest': manifest(2), 'executor': 'codex', 'options': {}}, wait=False)
            sid = record['suite_id']
            until = time.monotonic() + 10
            while not self.controller.call('suite_status', {'suite_id': sid})['active_child_id']:
                self.assertLess(time.monotonic(), until); time.sleep(.02)
            self.controller._suites.set_state(sid, 'blocked', 'Configurator restarted; explicit resume required')
            self.assertTrue(self.controller.call('suite_status', {'suite_id': sid})['stopping'])
            with self.assertRaisesRegex(ValueError, 'still stopping'):
                self.drive(suite_id=sid)
            release.set()
            status = settled_suite(lambda method, params, home: self.controller.call(method, params), sid)
            self.assertEqual((status['status'], status['stopping'], status['completed']), ('blocked', False, 1))
            resumed = self.drive(suite_id=sid)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 2, 2))
        self.assertEqual(len(self.admitted), 2)

    def test_a_suite_whose_harness_ended_without_letting_go_is_blocked_for_a_resume(self):
        # Catches a suite reported running, or stopping, forever after its harness process died.
        import subprocess
        import sys
        with self.harness(PASS):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        ended = subprocess.Popen([sys.executable, '-c', 'pass']); ended.wait()
        with self.controller._db() as db:
            db.execute("UPDATE suites SET status='running',harness_attached=1,harness_pid=? WHERE id=?", (ended.pid, sid))
        alive = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((alive['status'], alive['stopping']), ('blocked', False))
        self.assertEqual(self.controller._suites.get(sid)['harness']['attached'], False)
        kinds = [e['kind'] for e in self.controller.call('suite_events', {'suite_id': sid})['events']]
        self.assertEqual(kinds[-2:], ['suite.blocked', 'suite.harness_detached'])


class SuiteStartTests(HarnessSuiteFixture):
    def test_a_suite_frozen_before_the_harness_drove_suites_is_read_only(self):
        # Catches resuming a suite whose frozen options hold evaluator password files, or that no harness drives: its
        # children would carry the password through the configurator.
        from generative_driver.benchmark_support.snapshots import execution_provenance
        from generative_driver.benchmark_support.suites import freeze_suite
        config = {'command': ['scripted-runtime'], 'runtime': 'codex'}
        frozen = freeze_suite(manifest(), 'codex', config, {}, execution_provenance(config),
                              harness_token_sha256=hashlib.sha256(b'old harness').hexdigest())
        old = {key: value for key, value in frozen.items() if key not in ('scheduler', 'harness_token_sha256')}
        handles = {**old, 'options': {'evaluator_password_files': {'tq9-v2': 'old-private-handle'}, 'renode': 'old-renode'}}
        token = 'relaunched harness token'
        for name, saved in (('no scheduler', old), ('password files', handles)):
            with self.subTest(frozen=name):
                suite = self.controller._suites.create(saved, 'old-' + name.replace(' ', '-'))
                sid = suite['suite_id']
                self.controller._suites.set_state(sid, 'blocked', 'Configurator restarted; explicit resume required')
                with self.assertRaisesRegex(ValueError, 'read-only'):
                    self.drive(suite_id=sid)
                with self.assertRaisesRegex(ValueError, 'read-only'):
                    self.controller.call('suite_resume', {'suite_id': sid,
                                                          'harness_token_sha256': hashlib.sha256(token.encode()).hexdigest()})
                for method in ('suite_reserve', 'suite_attach', 'suite_settle', 'suite_detach'):
                    with self.assertRaisesRegex(ValueError, 'read-only'):
                        self.controller.call(method, {'suite_id': sid, 'token': token, 'ordinal': 0, 'run_id': 'run-x'})
                status = self.controller.call('suite_status', {'suite_id': sid})
                self.assertEqual((status['status'], status['completed']), ('blocked', 0))
                self.assertNotIn('protocol', self.controller.call('suite_result', {'suite_id': sid})['experiment']['comparison_identity'])
        self.assertEqual(self.admitted, [])


if __name__ == '__main__':
    unittest.main()

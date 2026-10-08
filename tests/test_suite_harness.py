"""Suites driven by the bench harness. The configurator freezes and stores the suite and its slots; the harness reserves
each slot, provides the device, starts the child, waits, stops the device, grades the frozen driver after the run and
posts the verdict; the configurator settles the slot from the child's status and recorded evaluation, which it reads
itself. Scripted stages on synthetic TCP peers, a toy contract and a scripted reference execution; never Renode, real
truth or a real password."""
import hashlib
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from suite_fixtures import drive_suite
from test_configurator import scripted_stages
from test_evaluation import FAIL, PASS, PendingRunFixture, files, reference
from test_harness import EVIDENCE, unstarted_provider

EMULATOR = 'scripted-emulator-handle'


# How a harness might alter a slot's child request: the model, the budget, or the case it runs.
ALTERATIONS = (
    ('model', lambda request: {**request, 'executor_config': {**request['executor_config'], 'model': 'SUBSTITUTED-MODEL'}}),
    ('budget', lambda request: {**request, 'budget_seconds': 999}),
    ('legacy case', lambda request: {**{key: value for key, value in request.items()
                                        if key not in ('case_pin', 'device', 'admission', 'evaluation_token_sha256')},
                                     'case': 'tq9'}),
)


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

    def drive(self, calls=None, intercept=None, wait=True, **request):
        return drive_suite(self.controller, self.root / 'home-bench/suites',
                           {'password_files': {'tq9-v2': str(self.password)}, 'renode': EMULATOR, **request}, calls=calls,
                           intercept=intercept, wait=wait)

    def stopping_probe(self):
        """A runtime that stops the first probe for the operator, as a runtime that cannot start does."""
        stopped = []
        def runtime(request):
            if request.stage == 'probe' and not stopped:
                stopped.append(request.stage)
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                        'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
        return runtime

    def slow_admission(self):
        """The next admissions wait until released; set before the harness patches admission."""
        entered, release = threading.Event(), threading.Event()
        admit = self.admit
        def slow(*args, **kwargs):
            entered.set()
            release.wait(20)
            return admit(*args, **kwargs)
        self.admit = slow
        return entered, release

    def settled(self, suite_id, seconds=30):
        from suite_fixtures import settled_suite
        return settled_suite(lambda method, params, home: self.controller.call(method, params), suite_id, seconds=seconds)

    def child(self, suite_id, ordinal=0):
        """The run carrying a slot's child request, attached to the slot or not."""
        return self.controller.find_request(self.controller._suites.get(suite_id)['trials'][ordinal]['child_request_id'])

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
        for method in ('suite_reserve', 'suite_start_child', 'suite_resume_child', 'suite_settle', 'suite_detach'):
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
        # harness stopped, or never grades it; and a trial record that still reads the earlier device stop as confirmed
        # while the device provided anew runs, so a harness lost then would leave that emulator forgotten.
        from generative_driver.benchmark_support.harness import read_trial
        runtime = self.stopping_probe()
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
        during = []
        def intercept(method, params, forward):
            if method == 'attach_device':
                # What a harness lost at this moment leaves on disk: the new device, its stop not confirmed.
                during.append(read_trial(trial))
            return forward(method, params)
        with self.harness(PASS, runtime=runtime):
            resumed = self.drive(suite_id=sid, intercept=intercept)
        self.assertEqual(resumed['status'], 'finished', resumed)
        self.assertEqual(len(during), 1)
        self.assertIs(during[0]['device_stop']['confirmed'], False)
        self.assertNotEqual(during[0]['device']['session_id'], first['session_id'])
        self.assertIs(read_trial(trial)['device_stop']['confirmed'], True)
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
        # The harness's failure text, which names the emulator executable it was handed, stays in its own record: the
        # suite tables say only which trial failed and where that record is.
        self.assertNotIn(EMULATOR, json.dumps(self.suite_rows()))
        self.assertIn('entry-000-repeat-000', self.controller._suites.get(sid)['reason'])
        self.assertIn('suite.json', self.controller._suites.get(sid)['reason'])
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

    def test_a_suite_read_as_no_longer_stopping_carries_the_count_its_harness_settled(self):
        # Catches a status read whose slots are read before, and whose harness liveness is read after, the harness
        # settled its slot and let go: a client that waits for the suite to stop stopping would then read the count
        # from before that settlement.
        from generative_driver.client import process_lock_held
        from generative_driver.configurator import Controller
        release, settling, polling = threading.Event(), threading.Event(), threading.Event()
        def runtime(request):
            if request.stage == 'acquire':
                release.wait(20)
        def intercept(method, params, forward):
            if method == 'suite_settle':
                # The harness settles only once a client's status read is under way.
                settling.set()
                polling.wait(10)
            return forward(method, params)
        poller, alive = threading.get_ident(), Controller._harness_alive
        def widened(suite):
            if threading.get_ident() == poller and settling.is_set() and not polling.is_set():
                polling.set()
                # The harness settles, lets go and ends meanwhile, unless the suite's lock holds it off.
                lock, until = suite['harness'].get('lock'), time.monotonic() + 3
                while lock and time.monotonic() < until and process_lock_held(lock) is True:
                    time.sleep(.02)
            return alive(suite)
        with self.harness(PASS, PASS, runtime=runtime), patch.object(Controller, '_harness_alive', staticmethod(widened)):
            sid = self.drive(intercept=intercept, wait=False, manifest=manifest(2), executor='codex', options={})['suite_id']
            until = time.monotonic() + 10
            while not self.controller.call('suite_status', {'suite_id': sid})['active_child_id']:
                self.assertLess(time.monotonic(), until); time.sleep(.02)
            self.controller._suites.set_state(sid, 'blocked', 'Configurator restarted; explicit resume required')
            release.set()
            status = self.settled(sid)
        self.assertTrue(polling.is_set())
        self.assertEqual((status['status'], status['stopping'], status['completed'], status['equivalent']),
                         ('blocked', False, 1, 1))

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


    def test_a_suite_whose_harness_ended_is_blocked_even_when_its_process_id_was_reused(self):
        # Catches harness liveness read from a bare process id: once the id of an ended harness names another running
        # process (after a reboot, or as Windows reuses ids), the suite would read running, or stopping, forever.
        import os
        with self.harness(PASS):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        with self.controller._db() as db:
            # This test's own process stands in for an unrelated process that took over the ended harness's id.
            db.execute("UPDATE suites SET status='running',harness_attached=1,harness_pid=? WHERE id=?", (os.getpid(), sid))
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['stopping']), ('blocked', False))
        self.assertEqual(self.controller._suites.get(sid)['harness']['attached'], False)

    def test_a_harness_whose_start_or_resume_reply_was_lost_still_drives_the_suite(self):
        # Catches a harness that, when the client sends its suite_start or suite_resume again after a lost reply, reads
        # the configurator's answer to the repeat as another harness's suite and leaves its own suite undriven.
        def lose(name):
            lost = []
            def intercept(method, params, forward):
                if method == name and not lost:
                    lost.append(forward(method, params))  # The configurator acted; its reply never arrived.
                return forward(method, params)
            return intercept, lost
        for request_id in ('lost-reply', None):
            with self.subTest(request_id=request_id):
                intercept, lost = lose('suite_start')
                with self.harness(PASS):
                    record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={},
                                        request_id=request_id)
                self.assertEqual((record['status'], len(lost)), ('finished', 1), record)
                status = self.controller.call('suite_status', {'suite_id': record['suite_id']})
                self.assertEqual((status['status'], status['completed'], status['stopping']), ('completed', 1, False))
                self.assertFalse(self.controller._suites.get(record['suite_id'])['harness']['attached'])
        # The repeated start created no second suite.
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suites').fetchone()[0], 2)
        with self.harness(runtime=self.stopping_probe()):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'blocked')
        intercept, lost = lose('suite_resume')
        with self.harness(PASS):
            resumed = self.drive(suite_id=sid, intercept=intercept)
        self.assertEqual((resumed['status'], len(lost)), ('finished', 1), resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))

    def test_a_harness_whose_child_resume_reply_was_lost_follows_the_child_on_the_device_it_attached(self):
        # Catches a harness that stops the device it attached anew under the child the configurator resumed on it,
        # because the reply to that resume was lost; and a resume sent again after a lost reply that is refused, or
        # that resumes the child twice.
        runtime = self.stopping_probe()
        with self.harness(runtime=runtime):
            sid = self.drive(manifest=manifest(1), executor='codex', options={})['suite_id']
        child = {'run_id': self.child(sid)['run_id']}
        repeated = []
        def intercept(method, params, forward):
            if method == 'suite_resume_child' and not repeated:
                # The configurator resumed the child and its reply never arrived; the client sends the resume again.
                repeated.extend([forward(method, params), forward(method, params)])
                return {'ok': False, 'reason': 'ConnectionResetError: the reply was lost'}
            return forward(method, params)
        with self.harness(PASS, runtime=runtime):
            resumed = self.drive(suite_id=sid, intercept=intercept)
        self.assertEqual(resumed['status'], 'finished', resumed)
        self.assertEqual([reply.get('ok') for reply in repeated], [True, True], repeated)
        self.assertEqual(len(self.events(child, 'run.resumed')), 1)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        self.assertEqual((self.controller.call('result', child)['status'], len(self.graded)), ('completed', 1))
        self.assertTrue(all(peer.closed for peer in self.peers))

    def test_a_resume_sent_again_never_resumes_a_child_its_operator_cancelled_after_the_first_resume(self):
        # Catches a resume sent again after a lost reply that finds the child the first resume started cancelled
        # meanwhile and resumes it a second time, undoing the operator's cancellation, or is refused, which has the
        # harness record a resume that took place as one that never did.
        runtime = self.stopping_probe()
        with self.harness(runtime=runtime):
            sid = self.drive(manifest=manifest(1), executor='codex', options={})['suite_id']
        child = {'run_id': self.child(sid)['run_id']}
        repeated = []
        def intercept(method, params, forward):
            if method == 'suite_resume_child' and not repeated:
                # The configurator resumed the child and its reply never arrived; the operator cancels the child before
                # the client sends the resume again, and that reply arrives.
                repeated.append(forward(method, params))
                self.controller.call('cancel', child)
                repeated.append(forward(method, params))
                return repeated[-1]
            return forward(method, params)
        with self.harness(runtime=runtime):
            resumed = self.drive(suite_id=sid, intercept=intercept)
        self.assertEqual(resumed['status'], 'finished', resumed)
        self.assertEqual([reply.get('ok') for reply in repeated], [True, True], repeated)
        self.assertEqual(len(self.events(child, 'run.resumed')), 1)
        self.assertEqual(self.controller.call('result', child)['status'], 'cancelled')
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'blocked')
        self.assertTrue(all(peer.closed for peer in self.peers))

    def test_a_child_resumed_and_cancelled_while_its_resume_reply_was_lost_keeps_its_device_until_it_stopped(self):
        # Catches a harness that, its child's resume reply lost and the resume sent again refused because the suite was
        # cancelled meanwhile, stops the device it attached anew under the child the configurator resumed on it, while
        # that child's worker is still stopping.
        stopped, entered, release, observed = [], threading.Event(), threading.Event(), []
        def runtime(request):
            if request.stage == 'probe' and not stopped:
                stopped.append(request.stage)
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                        'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
            if request.stage == 'probe':
                entered.set()
                release.wait(20)
                # Whether the device this resume runs on was stopped under the stage.
                observed.append([getattr(peer, 'closed', False) for peer in self.peers[provided:]])
        with self.harness(runtime=runtime):
            sid = self.drive(manifest=manifest(1), executor='codex', options={})['suite_id']
        child = {'run_id': self.child(sid)['run_id']}
        provided, cancelling = len(self.peers), []
        def intercept(method, params, forward):
            if method == 'suite_resume_child' and not cancelling:
                forward(method, params)
                self.assertTrue(entered.wait(10))
                # The operator cancels the suite before the resume is sent again; its child is still stopping.
                cancelling.append(threading.Thread(target=self.controller.call, args=('suite_cancel', {'suite_id': sid}),
                                                   daemon=True))
                cancelling[0].start()
                until = time.monotonic() + 10
                while self.controller.call('status', child)['status'] != 'cancelled':
                    self.assertLess(time.monotonic(), until); time.sleep(.02)
                again = forward(method, params)
                self.assertFalse(again.get('ok'), again)
                threading.Timer(1, release.set).start()
                return {'ok': False, 'reason': 'ConnectionResetError: the reply was lost'}
            return forward(method, params)
        with self.harness(runtime=runtime):
            self.drive(suite_id=sid, intercept=intercept)
        cancelling[0].join(20)
        self.assertEqual(observed, [[False] * (len(self.peers) - provided)])
        self.assertGreater(len(self.peers), provided)
        self.assertEqual(len(self.events(child, 'run.resumed')), 1)
        self.assertEqual(self.controller.call('result', child)['status'], 'cancelled')
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'cancelled')
        self.assertTrue(all(peer.closed for peer in self.peers))

    def test_a_suite_resume_requests_the_resume_of_a_child_again_after_a_dropped_connection(self):
        # Catches a resume of a suite's child that the client does not send again after a dropped connection, which
        # leaves the harness stopping the device under a child the configurator may have resumed.
        from generative_driver import client
        from generative_driver.benchmark_support.harness import _configurator
        sent = []
        def request(directory, method, params, **options):
            sent.append(method)
            if len(sent) == 1:
                raise ConnectionResetError('the reply was lost')
            return {'ok': True}
        with patch.object(client, '_request', side_effect=request), patch.object(client, '_start') as started:
            reply = _configurator(self.home)('suite_resume_child', {'suite_id': 'suite-x', 'token': 't', 'ordinal': 0})
        self.assertEqual((reply, sent), ({'ok': True}, ['suite_resume_child', 'suite_resume_child']))
        self.assertEqual(started.call_count, 1)

    def test_a_resumed_suite_settles_a_child_graded_before_its_harness_ended_without_the_password_file(self):
        # Catches a resume that requires the evaluator password file to settle a child whose verdict is already
        # recorded (its harness lost the settlement's reply), and so leaves the suite blocked for want of it.
        def intercept(method, params, forward):
            if method == 'suite_settle':
                return {'ok': False, 'reason': 'ConnectionResetError: the request never arrived'}
            return forward(method, params)
        with self.harness(PASS):
            record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
        self.assertEqual(record['status'], 'failed', record)
        sid = record['suite_id']
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'blocked')
        self.assertEqual(self.controller.call('result', {'run_id': self.child(sid)['run_id']})['equivalence']['equivalent'], True)
        with self.harness():
            resumed = self.drive(suite_id=sid, password_files={}, renode=None)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        self.assertEqual((len(self.admitted), len(self.graded)), (1, 1))


class ChildStartReplyTests(HarnessSuiteFixture):
    """A start of a suite's child whose reply never reached its harness: the configurator started the child on the
    harness's device and attached it, and the harness learns of it only by sending the same start again."""

    LOST = {'ok': False, 'reason': 'ConnectionResetError: the reply was lost'}
    UNREACHABLE = {'ok': False, 'reason': 'Configurator is not reachable. Run generative-driver service start from an '
                                          'operator terminal, then reconnect this UI.'}

    def held_acquire(self):
        """A runtime that holds the child's first acquire until released and records, as that stage returns, whether
        the device the child started on was stopped under it."""
        entered, release, observed = threading.Event(), threading.Event(), []
        def runtime(request):
            if request.stage == 'acquire' and not entered.is_set():
                entered.set()
                release.wait(20)
                observed.append([getattr(peer, 'closed', False) for peer in self.peers])
        return runtime, entered, release, observed

    def trials(self):
        from generative_driver.benchmark_support import harness
        return [harness.read_trial(path.parent) for path in (self.root / 'home-bench/suites').glob('*/trials/*/trial.json')]

    def test_a_harness_whose_child_start_got_no_reply_sends_it_again_and_follows_the_child(self):
        # Catches a harness that takes a start of its child that got no reply (a client that sends nothing again, as
        # with no configurator to start, or a reply timed out) for a child that never started: it stops the device
        # under the child the configurator started and attached, and records no run, so the slot is never continued.
        sent = []
        def intercept(method, params, forward):
            if method == 'suite_start_child':
                sent.append(forward(method, params))
                if len(sent) == 1:
                    return self.UNREACHABLE
                return sent[-1]
            return forward(method, params)
        with self.harness(PASS):
            record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
        self.assertEqual(record['status'], 'finished', record)
        sid = record['suite_id']
        child = self.child(sid)['run_id']
        self.assertEqual([(reply.get('ok'), reply.get('run_id')) for reply in sent], [(True, child), (True, child)])
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        [trial] = self.trials()
        self.assertEqual((trial['run_id'], trial['status']), (child, 'evaluated'))
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)
        self.assertEqual((len(self.admitted), len(self.graded)), (1, 1))

    def test_a_child_whose_start_reply_was_lost_while_its_suite_was_cancelled_keeps_its_device_until_it_stopped(self):
        # Catches a start sent again after a lost reply that a suite cancelled meanwhile refuses: the harness then
        # stops the device under the child the first start started, while that child's worker is still in its stage,
        # and records no run for it.
        runtime, entered, release, observed = self.held_acquire()
        cancelling, sent = [], []
        def intercept(method, params, forward):
            if method == 'suite_start_child' and not sent:
                sent.append(forward(method, params))
                self.assertTrue(entered.wait(10))
                # The operator cancels the suite before the start is sent again; its child is still stopping.
                cancelling.append(threading.Thread(target=self.controller.call,
                                                   args=('suite_cancel', {'suite_id': params['suite_id']}), daemon=True))
                cancelling[0].start()
                until = time.monotonic() + 10
                while self.controller.call('status', {'run_id': sent[0]['run_id']})['status'] != 'cancelled':
                    self.assertLess(time.monotonic(), until); time.sleep(.02)
                threading.Timer(1, release.set).start()
                return self.LOST
            return forward(method, params)
        with self.harness(PASS, runtime=runtime):
            record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
        cancelling[0].join(20)
        sid, child = record['suite_id'], sent[0]['run_id']
        self.assertEqual(observed, [[False] * len(self.peers)])
        self.assertEqual(self.controller.call('result', {'run_id': child})['status'], 'cancelled')
        [trial] = self.trials()
        self.assertEqual((trial['run_id'], trial['status'], trial['device_stop']['confirmed']), (child, 'run-stopped', True))
        status = self.settled(sid)
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('cancelled', False, 0))
        self.assertEqual(self.graded, [])
        self.assertTrue(all(peer.closed for peer in self.peers))

    def test_a_child_whose_start_reply_was_lost_as_its_suite_budget_ran_out_is_continued_by_a_resume(self):
        # Catches a start sent again after a lost reply that a suite whose budget ran out meanwhile refuses: the
        # harness stops the device under the child, records no run for it, and every later resume fails to find the
        # child's trial, so the slot is never continued.
        runtime, entered, release, observed = self.held_acquire()
        sent = []
        def intercept(method, params, forward):
            if method == 'suite_start_child' and not sent:
                sent.append(forward(method, params))
                self.assertTrue(entered.wait(10))
                with self.controller._db() as db:
                    # As if the suite had started long enough ago for its whole budget to have passed.
                    db.execute('UPDATE suites SET created=? WHERE id=?', (time.time() - 86400, params['suite_id']))
                threading.Timer(1, release.set).start()
                return self.LOST
            return forward(method, params)
        with self.harness(runtime=runtime):
            record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
        self.assertEqual(record['status'], 'finished', record)
        sid, child = record['suite_id'], {'run_id': sent[0]['run_id']}
        self.assertEqual(observed, [[False] * len(self.peers)])
        self.assertEqual(self.controller.call('result', child)['status'], 'cancelled')
        self.assertEqual(self.controller._suites.get(sid)['reason'], 'Suite wall-clock budget exhausted')
        [trial] = self.trials()
        self.assertEqual((trial['run_id'], trial['status'], trial['device_stop']['confirmed']),
                         (child['run_id'], 'run-stopped', True))
        with self.harness(PASS, runtime=runtime):
            resumed = self.drive(suite_id=sid, suite_budget_seconds=172800, budget_reason='Operator approved a longer suite')
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        self.assertEqual(self.child(sid)['run_id'], child['run_id'])
        self.assertEqual((len(self.events(child, 'run.resumed')), len(self.events(child, 'device.attached'))), (1, 1))
        self.assertEqual((len(self.admitted), len(self.graded)), (2, 1))
        self.assertTrue(all(peer.closed for peer in self.peers))

    def test_a_trial_whose_child_start_never_got_a_reply_is_found_by_its_request_and_completed_on_resume(self):
        # Catches a slot whose child exists but whose trial names no run, because the configurator never answered its
        # start: a resume finds no trial for the child and fails for good, its sealed evaluation token unused.
        from generative_driver.benchmark_support import harness
        sent = []
        def intercept(method, params, forward):
            if method == 'suite_start_child':
                if not sent:
                    sent.append(forward(method, params))
                return self.UNREACHABLE
            return forward(method, params)
        with self.harness(PASS), patch.object(harness, 'UNREACHABLE', .5):
            record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
        self.assertEqual(record['status'], 'failed', record)
        sid, child = record['suite_id'], {'run_id': sent[0]['run_id']}
        [trial] = self.trials()
        self.assertEqual((trial['run_id'], trial['status'], trial['device_stop']['confirmed']), (None, 'failed', True))
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'blocked')
        until = time.monotonic() + 20
        while (self.controller.call('status', child)['status'], self.controller.call('status', child)['stopping']) != ('completed', False):
            self.assertLess(time.monotonic(), until); time.sleep(.05)
        with self.harness(PASS):
            resumed = self.drive(suite_id=sid)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        [trial] = self.trials()
        self.assertEqual((trial['run_id'], trial['status']), (child['run_id'], 'evaluated'))
        # The trial keeps why its harness never heard of the run it started.
        self.assertIn('not reachable', trial['start_failed'])
        self.assertEqual((len(self.admitted), len(self.graded)), (1, 1))


class FrozenChildTests(HarnessSuiteFixture):
    def test_a_child_requested_with_other_inputs_than_its_frozen_slot_is_refused_before_it_exists(self):
        # Catches a configurator that starts, for a suite slot, whatever child its harness requests: a substituted
        # model or budget, or a v2 slot run as a legacy case that no evaluation would ever grade. The request must
        # be the frozen slot's own, less only what the harness adds (device, admission, evaluation token hash).
        for name, alter in ALTERATIONS:
            with self.subTest(alteration=name):
                def intercept(method, params, forward, alter=alter):
                    if method == 'suite_start_child':
                        params = {**params, 'request': alter(params['request'])}
                    return forward(method, params)
                with self.harness(PASS):
                    record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
                sid = record['suite_id']
                suite = self.controller._suites.get(sid)
                self.assertEqual(suite['status'], 'blocked')
                self.assertIn('Frozen suite child identity mismatch', suite['reason'])
                self.assertEqual([(t['status'], t['run_id']) for t in suite['trials']], [('launching', None)])
                self.assertIsNone(self.child(sid))
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
        self.assertEqual(self.graded, [])
        self.assertTrue(self.peers and all(peer.closed for peer in self.peers))
        # A harness that requests the frozen child then runs the slot, and the suite completes.
        with self.harness(PASS):
            resumed = self.drive(suite_id=sid)
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))

    def test_a_child_started_outside_its_suite_with_other_inputs_is_never_attached_settled_or_resumed(self):
        # Catches a configurator that attaches a child by its request_id alone: a harness that starts the slot's
        # request_id with a substituted model through a plain `start` has that child cancelled and the suite blocked.
        calls, altered = [], []
        def intercept(method, params, forward):
            if method == 'suite_start_child':
                altered.append(ALTERATIONS[0][1](params['request']))
                return forward('start', altered[-1])
            return forward(method, params)
        def runtime(request):
            if request.stage == 'acquire':
                time.sleep(.5)
        with self.harness(PASS, runtime=runtime):
            record = self.drive(calls, intercept=intercept, manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        suite = self.controller._suites.get(sid)
        self.assertEqual(suite['status'], 'blocked')
        self.assertIn('Frozen suite child identity mismatch', suite['reason'])
        self.assertIsNone(suite['trials'][0]['run_id'])
        child = self.child(sid)
        self.assertEqual(child['status'], 'cancelled')
        self.assertEqual(self.graded, [])
        token = next(params['token'] for method, params in calls if method == 'suite_reserve')
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.controller.call('suite_attach', {'suite_id': sid, 'token': token, 'ordinal': 0, 'run_id': child['run_id']})
        # The suite's start of that very request is not a start sent again: the child it names is not the slot's.
        with self.assertRaises(ValueError):
            self.controller.call('suite_start_child', {'suite_id': sid, 'token': token, 'ordinal': 0, 'request': altered[0]})
        self.assertIsNone(self.controller._suites.get(sid)['trials'][0]['run_id'])
        with self.harness(PASS), self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.drive(suite_id=sid)
        self.assertIsNone(self.controller._suites.get(sid)['trials'][0]['run_id'])
        self.assertEqual(self.controller.call('result', child)['status'], 'cancelled')

    def test_a_slot_is_settled_only_from_a_child_whose_recorded_request_is_the_frozen_one(self):
        # Catches a settlement that trusts the attached run without comparing its recorded request with the slot's.
        def intercept(method, params, forward):
            if method == 'suite_settle':
                run_id = self.controller._suites.get(params['suite_id'])['trials'][params['ordinal']]['run_id']
                with self.controller._db() as db:
                    spec = json.loads(db.execute('SELECT spec FROM runs WHERE id=?', (run_id,)).fetchone()['spec'])
                    spec['_submitted_intent']['executor_config']['model'] = 'SUBSTITUTED-MODEL'
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(spec), run_id))
            return forward(method, params)
        with self.harness(PASS):
            record = self.drive(intercept=intercept, manifest=manifest(1), executor='codex', options={})
        suite = self.controller._suites.get(record['suite_id'])
        self.assertEqual(suite['status'], 'blocked')
        self.assertIn('Frozen suite child identity mismatch', suite['reason'])
        self.assertEqual([t['status'] for t in suite['trials']], ['running'])
        self.assertEqual(self.controller.call('suite_status', {'suite_id': record['suite_id']})['completed'], 0)


class CancellationTests(HarnessSuiteFixture):
    def cancelled_during_admission(self, admission, **request):
        """Cancel the suite while its harness is admitting a device, then let the admission finish."""
        entered, release = admission
        record = self.drive(wait=False, **request)
        self.assertTrue(entered.wait(10))
        threading.Timer(.3, release.set).start()
        self.controller.call('suite_cancel', {'suite_id': record['suite_id']})
        return record['suite_id'], self.settled(record['suite_id'])

    def test_a_suite_cancelled_while_its_harness_provides_the_device_starts_no_child(self):
        # Catches a child started, run to its budget and graded inside a suite cancelled while the harness admitted
        # and started its device.
        admission = self.slow_admission()
        with self.harness(PASS):
            sid, status = self.cancelled_during_admission(admission, manifest=manifest(1), executor='codex', options={})
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('cancelled', False, 0))
        self.assertNotEqual(self.controller._suites.get(sid)['trials'][0]['status'], 'finished')
        self.assertIsNone(self.child(sid))
        self.assertEqual(self.graded, [])
        self.assertTrue(self.peers and all(peer.closed for peer in self.peers))
        self.assertFalse(self.controller._suites.get(sid)['harness']['attached'])

    def test_a_suite_cancelled_while_its_harness_provides_a_device_anew_never_resumes_the_child(self):
        # Catches a resumed suite's stopped child resumed, run and graded after the suite was cancelled while its
        # harness provided the device anew.
        with self.harness(runtime=self.stopping_probe()):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        child = {'run_id': self.child(sid)['run_id']}
        admission = self.slow_admission()
        with self.harness(PASS):
            _, status = self.cancelled_during_admission(admission, suite_id=sid)
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('cancelled', False, 0))
        self.assertEqual(self.controller.call('result', child)['status'], 'cancelled')
        self.assertEqual(self.events(child, 'run.resumed'), [])
        self.assertNotEqual(self.controller._suites.get(sid)['trials'][0]['status'], 'finished')
        self.assertEqual(self.graded, [])
        self.assertTrue(all(peer.closed for peer in self.peers))


    def test_a_child_started_outside_a_cancelled_suite_is_cancelled_on_the_next_reservation(self):
        # Catches a cancelled suite whose child, started with a plain `start` under the slot's request_id rather than
        # through the suite, runs to its budget: the harness's next reservation cancels it, and an attach does too.
        calls = []
        def intercept(method, params, forward):
            if method == 'suite_start_child':
                self.controller._suites.cancel(params['suite_id'])
                return forward('start', params['request'])
            return forward(method, params)
        def runtime(request):
            if request.stage == 'acquire':
                time.sleep(.5)
        with self.harness(PASS, runtime=runtime):
            record = self.drive(calls, intercept=intercept, manifest=manifest(1), executor='codex', options={})
        sid = record['suite_id']
        status = self.settled(sid)
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('cancelled', False, 0))
        child = self.child(sid)
        self.assertEqual(child['status'], 'cancelled')
        self.assertEqual(self.graded, [])
        token = next(params['token'] for method, params in calls if method == 'suite_reserve')
        attached = self.controller.call('suite_attach', {'suite_id': sid, 'token': token, 'ordinal': 0, 'run_id': child['run_id']})
        self.assertEqual((attached['run_id'], attached.get('cancelled')), (child['run_id'], True))
        self.assertEqual(self.controller.call('result', {'run_id': child['run_id']})['status'], 'cancelled')


class SuiteBudgetTests(HarnessSuiteFixture):
    def spent_during_admission(self, admission, **request):
        """Spend the suite's wall-clock budget while its harness is admitting a device, then let the admission finish."""
        entered, release = admission
        sid = self.drive(wait=False, **request)['suite_id']
        self.assertTrue(entered.wait(10))
        with self.controller._db() as db:
            # As if the suite had started long enough ago for its whole budget to have passed.
            db.execute('UPDATE suites SET created=? WHERE id=?', (time.time() - 86400, sid))
        release.set()
        until = time.monotonic() + 30
        while True:
            status = self.controller.call('suite_status', {'suite_id': sid})
            if status['status'] != 'running' and not status['stopping']:
                return sid, status
            self.assertLess(time.monotonic(), until, status); time.sleep(.02)

    def test_a_suite_whose_budget_ran_out_while_its_harness_provided_the_device_starts_no_child(self):
        # Catches a child started, and its worker dispatched, after the suite's wall-clock budget ran out while the
        # harness admitted and started the device, only for the next reservation to cancel it.
        admission = self.slow_admission()
        with self.harness(PASS):
            sid, status = self.spent_during_admission(admission, manifest=manifest(1), executor='codex', options={})
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('blocked', False, 0))
        self.assertEqual(self.controller._suites.get(sid)['reason'], 'Suite wall-clock budget exhausted')
        self.assertIsNone(self.child(sid))
        self.assertEqual(self.graded, [])
        self.assertTrue(self.peers and all(peer.closed for peer in self.peers))

    def test_a_suite_whose_budget_ran_out_while_its_harness_provided_a_device_anew_never_resumes_the_child(self):
        # Catches a stopped child resumed, and its worker dispatched, after the suite's wall-clock budget ran out while
        # a resumed suite's harness provided the device anew.
        with self.harness(runtime=self.stopping_probe()):
            sid = self.drive(manifest=manifest(1), executor='codex', options={})['suite_id']
        child = {'run_id': self.child(sid)['run_id']}
        admission = self.slow_admission()
        with self.harness(PASS):
            _, status = self.spent_during_admission(admission, suite_id=sid)
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('blocked', False, 0))
        self.assertEqual(self.controller._suites.get(sid)['reason'], 'Suite wall-clock budget exhausted')
        self.assertEqual(self.events(child, 'run.resumed'), [])
        # The suite's budget ends its active child, as on every reservation.
        self.assertEqual(self.controller.call('result', child)['status'], 'cancelled')
        self.assertEqual(self.graded, [])
        self.assertTrue(all(peer.closed for peer in self.peers))


class ReservationTests(HarnessSuiteFixture):
    def test_a_run_under_a_slot_the_suite_has_not_reserved_is_never_attached(self):
        # Catches an attach that takes the run found under a pending slot's request: that slot would run beside the
        # active one, out of order and past the suite's one active child.
        from generative_driver.benchmark_support.suites import child_request
        from test_harness import EVIDENCE, Device
        calls = []
        with self.harness(runtime=self.stopping_probe()):
            sid = self.drive(calls, manifest=manifest(2), executor='codex', options={})['suite_id']
            suite = self.controller._suites.get(sid)
            self.assertEqual([t['status'] for t in suite['trials']], ['running', 'pending'])
            (self.root / 'outside').mkdir()
            device = Device(self, self.image, self.root / 'outside')
            # The pending slot's own request, on a device of its own: nothing about it but its slot is out of place.
            outside = self.controller.call('start', {**child_request(suite['frozen'], suite['trials'][1]),
                                                     'device': device.server.descriptor,
                                                     'admission': {'calibration_evidence_sha256': EVIDENCE},
                                                     'evaluation_token_sha256': hashlib.sha256(b'outside').hexdigest()})
            token = next(params['token'] for method, params in calls if method == 'suite_reserve')
            with self.assertRaisesRegex(ValueError, 'not reserved'):
                self.controller.call('suite_attach', {'suite_id': sid, 'token': token, 'ordinal': 1,
                                                      'run_id': outside['run_id']})
            self.controller.call('cancel', {'run_id': outside['run_id']})
        self.assertEqual([(t['status'], t['run_id']) for t in self.controller._suites.get(sid)['trials']][1],
                         ('pending', None))


class ChildBudgetTests(HarnessSuiteFixture):
    def test_a_stopped_child_out_of_its_own_budget_continues_with_an_approved_child_extension(self):
        # Catches an emulated suite child stopped by its own wall-clock budget that no suite resume can continue: the
        # child's extension reaches the configurator's resume once its device is attached anew.
        slow = []
        def runtime(request):
            if request.stage == 'interpret' and not slow:
                slow.append(request.stage)
                time.sleep(2)
        with self.harness(runtime=runtime):
            record = self.drive(manifest=dict(manifest(1), child_budget_seconds=1.5), executor='codex', options={})
        sid = record['suite_id']
        child = {'run_id': self.child(sid)['run_id']}
        result = self.controller.call('result', child)
        self.assertEqual((result['status'], result['outcome_category'], result['reason']),
                         ('blocked', 'operator', 'Run exhausted its wall-clock budget'))
        # Without an extension the child cannot continue; its suite stays blocked and the device is stopped.
        with self.harness(PASS, runtime=runtime):
            refused = self.drive(suite_id=sid)
        self.assertEqual(refused['status'], 'failed')
        self.assertIn('budget', refused['reason'])
        self.assertEqual(self.controller.call('suite_status', {'suite_id': sid})['status'], 'blocked')
        self.assertTrue(all(peer.closed for peer in self.peers))
        with self.harness(PASS, runtime=runtime):
            resumed = self.drive(suite_id=sid, child_budget_seconds=600, child_budget_reason='Operator approved a longer child')
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': sid})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))
        extended = self.events(child, 'run.budget_extended')
        self.assertEqual([(e['old_budget_seconds'], e['new_budget_seconds'], e['reason']) for e in extended],
                         [(1.5, 600, 'Operator approved a longer child')])
        overrides = self.controller.call('suite_result', {'suite_id': sid})['experiment']['comparison_identity']['budgets']['effective']
        self.assertEqual(overrides['child_budget_overrides'], [{'trial_key': 'entry-000-repeat-000', 'budget_seconds': 600}])
        self.assertTrue(all(peer.closed for peer in self.peers))


class SuiteStartTests(HarnessSuiteFixture):
    def test_a_suite_is_started_or_resumed_only_for_a_harness_that_names_its_process(self):
        # Catches a suite attached to a harness with no process to watch: one that died without letting go of it would
        # leave the suite running, or stopping, with no way to resume it.
        token = hashlib.sha256(b'harness token').hexdigest()
        for pid in (None, 0, -1, 'pid', True):
            with self.subTest(pid=pid), self.assertRaisesRegex(ValueError, 'harness_pid'):
                self.controller.call('suite_start', {'manifest': manifest(1), 'executor': 'codex', 'options': {},
                                                     'harness_token_sha256': token,
                                                     **({} if pid is None else {'harness_pid': pid})})
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suites').fetchone()[0], 0)
        with self.harness(runtime=self.stopping_probe()):
            record = self.drive(manifest=manifest(1), executor='codex', options={})
        with self.assertRaisesRegex(ValueError, 'harness_pid'):
            self.controller.call('suite_resume', {'suite_id': record['suite_id'], 'harness_token_sha256': token})
        self.assertEqual(self.controller.call('suite_status', {'suite_id': record['suite_id']})['status'], 'blocked')

    def test_a_request_whose_harness_ended_before_it_recorded_the_configurator_reply_reports_the_suite_it_froze(self):
        # Catches a request_id claim held for good by a harness that ended (killed, or its machine restarted) after the
        # configurator froze its suite and before it wrote the reply down: every later start with that request_id would
        # report the harness's exit and never the suite_id, leaving the operator no way to reach the frozen suite.
        import subprocess
        import sys
        from types import SimpleNamespace
        from generative_driver.benchmark_support import harness
        ended = subprocess.Popen([sys.executable, '-c', 'pass'])
        ended.wait()
        frozen, spawned = [], []
        def configurator(method, params=None):
            if method == 'ping':
                return {'ok': True}
            try:
                return self.controller.call(method, params or {})
            except Exception as error:
                return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
        def spawn(directory, arguments, payload):
            spawned.append(directory)
            if len(spawned) == 1:
                # The first harness: the configurator froze its suite, and the harness ended before it heard the reply.
                harness._private(Path(directory) / 'suite.json', {
                    'schema': harness.SUITE, 'home': payload['home'], 'suite_id': None, 'request_id': payload['request_id'],
                    'status': 'starting', 'accepted': False, 'pid': ended.pid, 'created': time.time(), 'settled': []})
                frozen.append(configurator('suite_start', {
                    **{key: payload[key] for key in ('manifest', 'executor', 'options', 'request_id')},
                    'harness_token_sha256': hashlib.sha256(b'ended harness').hexdigest(), 'harness_pid': ended.pid}))
                return SimpleNamespace(poll=lambda: 0)
            thread = threading.Thread(target=harness.run_suite, args=(directory, payload), kwargs={'call': configurator},
                                      daemon=True)
            thread.start()
            return SimpleNamespace(poll=lambda: None if thread.is_alive() else 0)
        request = {'manifest': manifest(1), 'executor': 'codex', 'options': {}, 'request_id': 'harness-ended'}
        launched = []
        with patch.object(harness, '_spawn', side_effect=spawn):
            for _ in range(2):
                launched.append(harness.launch_suite(dict(request), home=self.home,
                                                     password_files={'tq9-v2': str(self.password)}, renode=EMULATOR))
        self.assertEqual(frozen[0]['ok'], True, frozen)
        self.assertEqual(launched[0]['ok'], False, launched)
        self.assertEqual((launched[1]['ok'], launched[1]['suite_id'], launched[1]['duplicate']),
                         (True, frozen[0]['suite_id'], True), launched)
        # The second harness took nothing over: the suite whose harness ended is blocked for a resume.
        until = time.monotonic() + 10
        while harness.read_suite(spawned[1]).get('finished') is None:
            self.assertLess(time.monotonic(), until); time.sleep(.02)
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suites').fetchone()[0], 1)
        status = self.controller.call('suite_status', {'suite_id': frozen[0]['suite_id']})
        self.assertEqual((status['status'], status['stopping'], status['completed']), ('blocked', False, 0))
        with self.harness(PASS):
            resumed = self.drive(suite_id=frozen[0]['suite_id'])
        self.assertEqual(resumed['status'], 'finished', resumed)
        status = self.controller.call('suite_status', {'suite_id': frozen[0]['suite_id']})
        self.assertEqual((status['status'], status['completed'], status['equivalent']), ('completed', 1, 1))

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
                for method in ('suite_reserve', 'suite_attach', 'suite_start_child', 'suite_resume_child', 'suite_settle',
                               'suite_detach'):
                    with self.assertRaisesRegex(ValueError, 'read-only'):
                        self.controller.call(method, {'suite_id': sid, 'token': token, 'ordinal': 0, 'run_id': 'run-x'})
                status = self.controller.call('suite_status', {'suite_id': sid})
                # No equivalent count applies to a suite whose children no harness grades after their runs.
                self.assertEqual((status['status'], status['completed'], status['equivalent']), ('blocked', 0, None))
                self.assertNotIn('protocol',self.controller.call('suite_result', {'suite_id': sid})['experiment']['comparison_identity'])
        self.assertEqual(self.admitted, [])


if __name__ == '__main__':
    unittest.main()

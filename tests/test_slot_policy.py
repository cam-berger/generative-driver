"""Feature 06, slot policy: a scoring suite moves past a trial that ended badly, reruns a faulted device once at the end
and does not grade a trial the transcript audit flags. Hand-written fixture devices, scripted workers and synthetic
credentials only; never Renode, a real set, a vault or a model run."""
import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from generated_fixtures import REFERENCE_A, FixtureDevice, fixture_start
from generative_driver.benchmark_support import evaluator, generated_sets, generated_trials, harness
from generative_driver.benchmark_support.outcomes import classify
from generative_driver.benchmark_support.suite_store import SuiteStore
from generative_driver.benchmark_support.suites import child_disposition, scoring_disposition
from suite_fixtures import drive_suite, sqlite_context
from test_configurator import gateway_identity
from test_generated_trials import SET, START, GeneratedTrialFixture, SetSuiteFixture, as_scoring_set, set_manifest


def ended(status='failed', category='host', **changes):
    """What the configurator reads of an ended child, for `scoring_disposition`."""
    return {'run_id': 'r', 'status': status, 'outcome_category': category, 'stopping': False, 'uncertain_effect': False,
            'created': 100.0, 'updated': 110.0, 'effective_budget_seconds': 120, 'equivalence': None, 'unscored': None,
            'reason': None, **changes}


def graded(status='graded', equivalent=True):
    return ended('completed', 'completed', equivalence={'status': status, 'equivalent': equivalent})


SPENT = {'updated': 220.0}


class ClassifyTests(unittest.TestCase):
    def test_06_B4_a_row_that_never_got_a_run_and_says_why_is_a_fault(self):
        row = {'run_id': None, 'status': 'faulted', 'outcome_category': 'host', 'no_run_reason': 'admit-refused'}
        self.assertEqual(classify(row, row), 'fault')

    def test_06_B4_a_row_with_no_run_and_no_reason_is_still_not_run(self):
        row = {'run_id': None, 'status': 'pending', 'outcome_category': 'unknown'}
        self.assertEqual(classify(row, row), 'not_run')

    def unscored(self, reason, **keyword):
        row = {'run_id': 'r', 'status': 'finished', 'outcome_category': 'completed'}
        report = {'workflow_status': 'completed', 'equivalence_status': 'unscored', 'reason': reason}
        return classify(row, report, **keyword)

    def test_06_B11_an_integrity_grade_is_a_failure_only_under_the_keyword(self):
        self.assertEqual(self.unscored('Unscored: integrity: a hash differs', unscored_integrity_fails=True), 'failure')
        self.assertEqual(self.unscored('integrity: a hash differs', unscored_integrity_fails=True), 'failure')
        self.assertEqual(self.unscored('Unscored: integrity: a hash differs'), 'fault')

    def test_06_B11_any_other_unscored_reason_is_a_fault_on_a_scoring_suite(self):
        for reason in ('Unscored: evaluator host fault: RuntimeError', 'Unscored: no reason recorded', None,
                       'Unscored: evaluator host fault: RuntimeError; integrity: a later reason',
                       'Unscored: says integrity: inside'):
            with self.subTest(reason):
                self.assertEqual(self.unscored(reason, unscored_integrity_fails=True), 'fault')

    def test_06_B11_the_keyword_reads_only_an_unscored_grade(self):
        row = {'run_id': 'r', 'status': 'finished', 'outcome_category': 'host'}
        stopped = {'workflow_status': 'failed', 'reason': 'integrity: a stop the reason merely names'}
        self.assertEqual(classify(row, stopped, unscored_integrity_fails=True), 'fault')
        finished = {'run_id': 'r', 'status': 'finished', 'outcome_category': 'completed'}
        none = {'workflow_status': 'completed', 'reason': 'integrity: no grade at all'}
        self.assertEqual(classify(finished, none, unscored_integrity_fails=True), 'fault')


class ScoringDispositionTests(unittest.TestCase):
    def assertDisposition(self, expected, result, **slot):
        self.assertEqual(scoring_disposition(result, {'attempt': 1, **slot}), expected)

    def test_06_B2_a_pass_a_failure_and_an_unknown_advance(self):
        for result in (graded(), graded(equivalent=False), graded('no-driver', False), graded('unknown', None)):
            with self.subTest(result['equivalence']):
                self.assertDisposition('advance', result)

    def test_06_B2_a_spent_budget_and_a_stop_the_system_caused_advance(self):
        self.assertDisposition('advance', ended('blocked', 'operator', **SPENT))
        for category in ('safety', 'integrity', 'model'):
            with self.subTest(category):
                self.assertDisposition('advance', ended('failed', category))

    def test_06_B3_a_host_an_operator_and_a_cancel_stop_fault(self):
        self.assertDisposition('fault', ended('failed', 'host'))
        self.assertDisposition('fault', ended('blocked', 'operator'))
        self.assertDisposition('fault', ended('cancelled', 'cancelled'))

    def test_06_B3_missing_evidence_faults(self):
        self.assertDisposition('fault', ended('completed', 'completed', equivalence={'status': 'not a grade'}))

    def test_06_B3_a_child_still_running_or_stopping_is_waited_for(self):
        for result in (ended('running', 'unknown'), ended('queued', 'unknown'), ended('failed', 'host', stopping=True)):
            with self.subTest(result['status']):
                self.assertDisposition('wait', result)

    def test_06_B3_a_completed_child_awaiting_its_grade_blocks(self):
        self.assertDisposition('block', ended('completed', 'completed'))

    def test_06_B5_an_uncertain_effect_blocks_even_with_a_spent_budget(self):
        self.assertDisposition('block', ended('blocked', 'operator', uncertain_effect=True, **SPENT))
        self.assertDisposition('block', ended('failed', 'host', uncertain_effect=True))

    def test_06_B5_a_reading_that_does_not_say_it_ended_blocks(self):
        self.assertDisposition('block', {key: value for key, value in ended().items() if key != 'stopping'})

    def test_06_B9_a_reruns_fault_stands(self):
        for result in (ended('failed', 'host'), ended('blocked', 'operator'), ended('cancelled', 'cancelled')):
            with self.subTest(result['status']):
                self.assertDisposition('advance', result, attempt=2)

    def test_06_B11_a_first_unscored_grade_regrades_the_same_driver(self):
        host = ended('completed', 'completed', unscored='evaluator host fault: RuntimeError')
        self.assertDisposition('regrade', host)
        self.assertDisposition('regrade', host, attempt=2)

    def test_06_B11_after_a_spent_regrade_it_faults_and_a_reruns_stands(self):
        host = ended('completed', 'completed', unscored='evaluator host fault: RuntimeError')
        self.assertDisposition('fault', host, regrade='spent')
        self.assertDisposition('advance', host, attempt=2, regrade='spent')

    def test_06_B11_a_grade_unscored_for_integrity_advances_and_is_no_rerun(self):
        self.assertDisposition('advance', ended('completed', 'completed', unscored='integrity: a hash differs'))

    def test_06_B13_a_flagged_trial_flags_whatever_its_own_outcome(self):
        for result in (graded(), ended('failed', 'host'), ended('completed', 'completed')):
            with self.subTest(result['status']):
                self.assertDisposition('flag', result, audit=[{'transcript': 't', 'command_sha256': 'c' * 64}])

    def test_06_B14_a_flagged_child_with_an_uncertain_effect_still_blocks(self):
        finding = [{'transcript': 't', 'command_sha256': 'c' * 64}]
        self.assertDisposition('block', ended('failed', 'host', uncertain_effect=True), audit=finding)
        self.assertDisposition('wait', ended('failed', 'host', stopping=True), audit=finding)

    def test_06_B1_child_disposition_stands_for_any_other_suite(self):
        self.assertEqual(child_disposition(ended('failed', 'host')), 'block')
        self.assertEqual(child_disposition(graded()), 'advance')
        self.assertEqual(child_disposition(ended('running', 'unknown')), 'wait')


SCORING = {'scoring_set': {'registration': 'fixture-v1', 'manifest_sha256': 'a' * 64}}
FINDING = {'transcript': 'work/probe/a/_agent_events.jsonl', 'command_sha256': 'c' * 64}
HISTORY = {'run_id': 'r', 'request_id': 'q', 'outcome': 'fault', 'tokens': None, 'seconds': 3.0}


def frozen_of(slots=3, **changes):
    return {'manifest': {'suite_budget_seconds': 100}, 'request': {'slots': slots, **changes},
            'trials': [{'ordinal': ordinal, 'trial_key': 'key-%d' % ordinal, 'case': 'generated',
                        'scenario': 'original', 'case_seed': 0, 'repeat_index': 0} for ordinal in range(slots)],
            **changes}


class StoreFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'suites.sqlite'
        self.store = SuiteStore(sqlite_context(self.path))
        self.runs = 0

    def suite(self, slots=3, **changes):
        suite_id = self.store.create(frozen_of(slots, **changes))['suite_id']
        self.store.set_state(suite_id, 'running')
        return suite_id

    def started(self, suite_id):
        """The next slot reserved, with a new child attached: the slot and that child's id."""
        slot = self.store.reserve(suite_id)
        self.runs += 1
        run_id = 'run-%d' % self.runs
        self.store.attach(suite_id, slot['ordinal'], slot['child_request_id'], run_id)
        return slot, run_id

    def stopped(self, run_id, category='host', status='failed'):
        return {'run_id': run_id, 'status': status, 'outcome_category': category, 'stopping': False,
                'uncertain_effect': False}

    def row(self, suite_id, ordinal):
        return self.store.get(suite_id)['trials'][ordinal]

    def reserved(self, suite_id):
        slot = self.store.reserve(suite_id)
        self.assertIsNotNone(slot, 'No slot was reserved')
        return slot

    def fault(self, suite_id, disposition='fault'):
        slot, run_id = self.started(suite_id)
        self.store.settle(suite_id, slot['ordinal'], self.stopped(run_id), disposition=disposition,
                          first_attempt={**HISTORY, 'run_id': run_id, 'request_id': slot['child_request_id']})
        return slot, run_id


class RerunReservationTests(StoreFixture):
    def test_06_B8_once_no_slot_is_pending_each_faulted_slot_reruns_in_ordinal_order(self):
        suite = self.suite()
        self.fault(suite)
        slot, run_id = self.started(suite)
        self.store.settle(suite, slot['ordinal'], self.stopped(run_id, 'model'))
        self.fault(suite)
        first = self.reserved(suite)
        self.assertEqual((first['ordinal'], first['status'], first['attempt']), (0, 'launching', 2))
        self.store.attach(suite, 0, first['child_request_id'], 'rerun-0')
        self.store.settle(suite, 0, self.stopped('rerun-0'))
        second = self.reserved(suite)
        self.assertEqual((second['ordinal'], second['status'], second['attempt']), (2, 'launching', 2))

    def test_06_B8_a_rerun_starts_with_a_new_request_and_its_frozen_slot_and_clears_the_first_attempts_fields(self):
        suite = self.suite(1)
        slot, run_id = self.fault(suite)
        rerun = self.reserved(suite)
        self.assertNotEqual(rerun['child_request_id'], slot['child_request_id'])
        self.assertEqual({key: rerun[key] for key in ('ordinal', 'trial_key')}, {'ordinal': 0, 'trial_key': 'key-0'})
        self.assertEqual((rerun['run_id'], rerun['result'], rerun['outcome_category']), (None, None, 'unknown'))
        self.assertNotIn('regrade', rerun)
        self.assertNotIn('no_run_reason', rerun)
        self.assertEqual(rerun['first_attempt']['run_id'], run_id)
        self.assertEqual(self.row(suite, 0)['child_request_id'], rerun['child_request_id'])

    def test_06_B8_there_is_no_third_attempt(self):
        suite = self.suite(1)
        self.fault(suite)
        self.reserved(suite)
        self.store.attach(suite, 0, self.row(suite, 0)['child_request_id'], 'rerun')
        self.store.settle(suite, 0, self.stopped('rerun'))
        self.assertIsNone(self.store.reserve(suite))
        self.assertEqual(self.row(suite, 0)['status'], 'finished')

    def test_06_B8_a_crash_during_a_rerun_resumes_that_same_attempt(self):
        suite = self.suite(1)
        self.fault(suite)
        rerun = self.reserved(suite)
        again = self.reserved(suite)
        self.assertEqual(again['child_request_id'], rerun['child_request_id'])
        self.assertEqual((again['status'], again['attempt']), ('launching', 2))
        self.store.attach(suite, 0, rerun['child_request_id'], 'rerun')
        attached = self.reserved(suite)
        self.assertEqual((attached['status'], attached['attempt'], attached['run_id']), ('running', 2, 'rerun'))

    def test_06_B8_the_events_tell_of_the_rerun(self):
        suite = self.suite(1)
        self.fault(suite)
        self.reserved(suite)
        kinds = [event['kind'] for event in self.store.events(suite)['events']]
        self.assertIn('trial.faulted', kinds)
        self.assertIn('trial.rerun', kinds)


class RegradeReservationTests(StoreFixture):
    def test_06_B11_a_regrade_returns_running_with_its_request_and_child_and_may_settle_again(self):
        suite = self.suite(1)
        slot, run_id = self.fault(suite, 'regrade')
        again = self.reserved(suite)
        self.assertEqual((again['status'], again['child_request_id'], again['run_id'], again['regrade']),
                         ('running', slot['child_request_id'], run_id, 'spent'))
        self.assertIsNone(again['result'])
        self.assertEqual(self.reserved(suite)['regrade'], 'spent')
        graded = {**self.stopped(run_id, 'completed', 'completed'),
                  'equivalence': {'status': 'graded', 'equivalent': True}}
        self.store.settle(suite, 0, graded)
        self.assertEqual(self.row(suite, 0)['status'], 'finished')

    def test_06_B11_a_grade_after_the_regrade_clears_the_first_attempt(self):
        suite = self.suite(1)
        _, run_id = self.fault(suite, 'regrade')
        self.reserved(suite)
        self.store.settle(suite, 0, self.stopped(run_id, 'completed', 'completed'))
        self.assertNotIn('first_attempt', self.row(suite, 0))

    def test_06_B11_a_spent_regrade_on_a_first_attempt_starts_the_rerun(self):
        suite = self.suite(1)
        _, run_id = self.fault(suite, 'regrade')
        self.reserved(suite)
        self.store.settle(suite, 0, self.stopped(run_id), disposition='fault',
                          first_attempt={**HISTORY, 'run_id': run_id})
        rerun = self.reserved(suite)
        self.assertEqual((rerun['status'], rerun['attempt'], rerun['run_id']), ('launching', 2, None))
        self.assertNotIn('regrade', rerun)

    def test_06_B11_a_reruns_grading_fault_gets_one_regrade_and_then_stands(self):
        suite = self.suite(1)
        self.fault(suite)
        self.reserved(suite)
        request = self.row(suite, 0)['child_request_id']
        self.store.attach(suite, 0, request, 'rerun')
        self.store.settle(suite, 0, self.stopped('rerun'), disposition='regrade')
        kept = self.row(suite, 0)
        self.assertEqual((kept['status'], kept['regrade'], kept['first_attempt']['run_id']),
                         ('faulted', 'due', 'run-1'))
        again = self.reserved(suite)
        self.assertEqual((again['status'], again['attempt'], again['run_id'], again['regrade']),
                         ('running', 2, 'rerun', 'spent'))
        self.store.settle(suite, 0, self.stopped('rerun'))
        self.assertEqual(self.row(suite, 0)['status'], 'finished')
        self.assertIsNone(self.store.reserve(suite))


class SettlementTests(StoreFixture):
    def test_06_B2_a_scoring_suite_settles_a_safety_or_integrity_stop(self):
        for category in ('safety', 'integrity'):
            with self.subTest(category):
                suite = self.suite(1, **SCORING)
                _, run_id = self.started(suite)
                self.store.settle(suite, 0, self.stopped(run_id, category))
                self.assertEqual((self.row(suite, 0)['status'], self.row(suite, 0)['outcome_category']),
                                 ('finished', category))

    def test_06_B1_any_other_suite_still_refuses_those_categories(self):
        suite = self.suite(1)
        _, run_id = self.started(suite)
        with self.assertRaisesRegex(ValueError, 'Invalid child outcome category'):
            self.store.settle(suite, 0, self.stopped(run_id, 'safety'))

    def test_06_B3_a_faulted_slot_keeps_its_run_and_first_attempt(self):
        suite = self.suite(1)
        slot, run_id = self.fault(suite)
        row = self.row(suite, 0)
        self.assertEqual((row['status'], row['run_id']), ('faulted', run_id))
        self.assertEqual(row.get('first_attempt'),
                         {**HISTORY, 'run_id': run_id, 'request_id': slot['child_request_id']})

    def test_06_B10_a_rerun_keeps_the_first_attempt_it_finished_after(self):
        suite = self.suite(1)
        _, run_id = self.fault(suite)
        self.reserved(suite)
        self.store.attach(suite, 0, self.row(suite, 0)['child_request_id'], 'rerun')
        self.store.settle(suite, 0, self.stopped('rerun'), first_attempt=None)
        row = self.row(suite, 0)
        self.assertEqual((row['status'], row['attempt'], row['first_attempt']['run_id']), ('finished', 2, run_id))

    def test_06_B12_a_flagged_slot_is_settled_and_gets_no_rerun(self):
        suite = self.suite(1)
        _, run_id = self.started(suite)
        self.store.store_audit(suite, 0, [FINDING])
        self.assertEqual(self.reserved(suite).get('audit'), [FINDING])
        self.store.settle(suite, 0, self.stopped(run_id), disposition='flag')
        self.assertEqual(self.row(suite, 0)['status'], 'flagged')
        self.assertIsNone(self.store.reserve(suite))

    def test_06_B4_a_first_slot_with_no_run_faults_with_its_word_and_a_reruns_stands(self):
        suite = self.suite(1)
        slot = self.store.reserve(suite)
        self.store.settle_no_run(suite, 0, 'admit-refused', {**HISTORY, 'run_id': None})
        row = self.row(suite, 0)
        self.assertEqual((row['status'], row['outcome_category'], row.get('no_run_reason'), row['run_id']),
                         ('faulted', 'host', 'admit-refused', None))
        rerun = self.reserved(suite)
        self.assertEqual((rerun['attempt'], rerun['status']), (2, 'launching'))
        self.assertNotIn('no_run_reason', rerun)
        self.assertNotEqual(rerun['child_request_id'], slot['child_request_id'])
        self.store.settle_no_run(suite, 0, 'start-refused', None)
        row = self.row(suite, 0)
        self.assertEqual((row['status'], row['no_run_reason'], row['first_attempt']['outcome']),
                         ('finished', 'start-refused', 'fault'))
        self.assertIsNone(self.store.reserve(suite))

    def test_06_B10_the_public_row_carries_the_history_and_a_row_with_none_has_no_new_field(self):
        suite = self.suite(2)
        _, run_id = self.fault(suite)
        slot, other = self.started(suite)
        rows = self.store.trials(suite)['trials']
        self.assertEqual(rows[0]['first_attempt']['run_id'], run_id)
        self.assertEqual(set(rows[1]), {'ordinal', 'trial_key', 'case', 'scenario', 'case_seed', 'repeat_index',
                                        'run_id', 'status', 'outcome_category', 'equivalent', 'equivalence_status'})
        self.store.settle(suite, slot['ordinal'], self.stopped(other, 'model'))
        self.reserved(suite)
        self.assertEqual(self.store.trials(suite)['trials'][0].get('attempt'), 2)

    def test_06_B4_the_public_row_carries_the_word_and_B15_the_findings(self):
        suite = self.suite(2)
        self.store.reserve(suite)
        self.store.settle_no_run(suite, 0, 'provider-failed', None)
        _, run_id = self.started(suite)
        self.store.store_audit(suite, 1, [FINDING])
        rows = self.store.trials(suite)['trials']
        self.assertEqual(rows[0].get('no_run_reason'), 'provider-failed')
        self.assertEqual(rows[1].get('audit'), [FINDING])

    def test_06_B1_a_database_from_before_this_gains_the_new_fields_with_their_defaults(self):
        with sqlite3.connect(self.path) as db:
            for column in ('attempt', 'first_attempt', 'no_run_reason', 'regrade', 'audit'):
                db.execute('ALTER TABLE suite_trials DROP COLUMN ' + column)
        store = SuiteStore(sqlite_context(self.path))
        suite = store.create(frozen_of(1))['suite_id']
        self.assertEqual(json.loads(json.dumps(store.get(suite)['trials'][0]))['status'], 'pending')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT attempt FROM suite_trials').fetchone(), (1,))


HOST = {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'failed',
        'cause': 'exit_error', 'reason': 'Scripted runtime exit'}
OPERATOR = {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
            'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
FIRST, SECOND = '0000d001', '0000d002'


def run_of(request):
    """The run a stage works for, by its gateway; interpret, which runs in a temporary folder, has none: None."""
    return gateway_identity(request)['run_id'] if request.gateway else None


class ScoringFixture(GeneratedTrialFixture):
    """A scoring suite of the qualified fixture set, driven by an in-process harness whose records a test reads. A child
    is told from another by the order it started in."""

    def setUp(self):
        super().setUp()
        self.table = as_scoring_set(self)
        self.children = []
        self.calls = []

    def nth(self, request):
        """Which child started this stage, counted from 0, or None for a stage that does not say."""
        run_id = run_of(request)
        if run_id is None:
            return None
        if run_id not in self.children:
            self.children.append(run_id)
        return self.children.index(run_id)

    def stopping(self, *stops, stage='probe'):
        """A `runtime` under which the nth child stops at `stage` as `stops[n]`; one with no entry, or None, runs on."""
        def runtime(request):
            nth = self.nth(request)
            if request.stage == stage and nth is not None and nth < len(stops):
                return stops[nth]
        return runtime

    def scripted(self, *stops, candidate=REFERENCE_A, **options):
        return self.staged(candidate, runtime=self.stopping(*stops), **options)

    def run_harness(self, *, intercept=None, stop=None, **request):
        """One harness on the suite, seen to its end: its record. A refusal before the suite is taken on raises
        ValueError."""
        accepted = drive_suite(self.controller, self.root / 'home-bench/suites', {
            **({} if 'suite_id' in request else {'manifest': set_manifest()}), 'executor': 'codex',
            'options': {'scoped_tool_approval': 'emulator'}, 'password_files': {SET: str(self.handle)},
            'set_root': str(self.set_root), 'renode': 'emulator-handle', **request},
            wait=False, calls=self.calls, intercept=intercept, stop=stop)
        thread = self.controller._test_harness_threads[-1]
        thread.join(300)
        self.assertFalse(thread.is_alive(), 'The harness did not finish')
        records = [harness.read_suite(path.parent) for path in (self.root / 'home-bench/suites').glob('*/suite.json')]
        return max((record for record in records if record.get('suite_id') == accepted['suite_id']),
                   key=lambda record: record['created'])

    def status(self, record):
        return self.controller.call('suite_status', {'suite_id': record['suite_id']})

    def rows(self, record):
        return self.controller.call('suite_result', {'suite_id': record['suite_id']})['trials']

    def result(self, run_id):
        return self.controller.call('result', {'run_id': run_id})

    def trial_records(self):
        return [harness.read_trial(path.parent)
                for path in (self.root / 'home-bench/suites').glob('*/trials/*/trial.json')]

    def assertCompleted(self, record):
        self.assertEqual((record['status'], self.status(record)['status']), ('finished', 'completed'), record)

    def until(self, condition, seconds=120):
        deadline = time.monotonic() + seconds
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'The condition was not met in time')
            time.sleep(.01)

    def slot_status(self, record, ordinal):
        """The status the slot itself holds, where `suite_result` shows an unsettled slot's child's."""
        return self.controller._suites.get(record['suite_id'])['trials'][ordinal]['status']

    def set_uncertain(self, run_id, value=1):
        with self.controller._db() as db:
            db.execute('UPDATE runs SET uncertain=? WHERE id=?', (value, run_id))

    def spend_suite_budget(self, suite_id):
        with self.controller._suites.open_db() as db:
            db.execute('UPDATE suites SET created=created-10000 WHERE id=?', (suite_id,))

    def dying_at(self, stop, stage, nth, then=None):
        """A `runtime` under which the nth child, at `stage`, waits for its harness to stop and be gone, as one that
        dies does, and then ends as `then`, or runs on."""
        def runtime(request):
            if self.nth(request) == nth and request.stage == stage and not stop.is_set():
                stop.set()
                self.until(lambda: not self.controller._test_harness_threads[-1].is_alive())
                return then
        return runtime

    def ended(self, run_id):
        """Wait until nothing of the child is left running or stopping."""
        def done():
            status = self.controller.call('status', {'run_id': run_id})
            return status['status'] in ('completed', 'failed', 'blocked', 'cancelled') and not status['stopping']
        self.until(done)

    def grading_faults(self, **remaining):
        """A patch of `reference_execute` that raises for a device while it has faults `remaining` ({device id: count}),
        and records each device it was asked to grade (`graded`)."""
        real = evaluator.reference_execute
        self.graded = []

        def execute(pin, *args, **kwargs):
            self.graded.append(pin['device_id'])
            if remaining.get('d' + pin['device_id'], 0) > 0:
                remaining['d' + pin['device_id']] -= 1
                raise (self.grading_error if hasattr(self, 'grading_error') else RuntimeError('Scripted host'))
            return real(pin, *args, **kwargs)
        return patch.object(evaluator, 'reference_execute', execute)


class MovingOnTests(ScoringFixture):
    def test_06_B2_a_stop_the_system_caused_settles_and_the_suite_goes_on_with_no_rerun(self):
        real = self.controller._check
        for category in ('safety', 'integrity', 'model'):
            with self.subTest(category):
                self.children.clear()

                def check(spec, stage, run_dir, workspace, report, accepted, assignment, category=category):
                    if stage == 'probe' and Path(run_dir).name == self.children[0]:
                        return {'stop': {'category': category, 'reason': 'Scripted ' + category}, 'artifacts': [],
                                'checks': []}
                    return real(spec, stage, run_dir, workspace, report, accepted, assignment)
                with patch.object(self.controller, '_check', side_effect=check), self.scripted():
                    record = self.run_harness(request_id='caused-' + category)
                self.assertCompleted(record)
                rows = self.rows(record)
                self.assertEqual([(row['status'], row['outcome_category']) for row in rows],
                                 [('finished', category), ('finished', 'completed')])
                self.assertEqual(len(self.children), 2)
                self.assertNotIn('attempt', rows[0])

    def test_06_B2_a_spent_child_budget_settles_as_it_stands_with_no_rerun(self):
        def runtime(request):
            self.nth(request)
            if request.stage == 'acquire':
                time.sleep(1.2)
        with self.staged(REFERENCE_A, runtime=runtime):
            record = self.run_harness(request_id='spent', manifest=set_manifest(child_budget_seconds=1))
        self.assertCompleted(record)
        rows = self.rows(record)
        self.assertEqual([(row['status'], row['outcome_category']) for row in rows],
                         [('finished', 'operator'), ('finished', 'operator')])
        self.assertEqual(len(self.children), 2)

    def test_06_B3_a_host_stop_moves_on_and_its_slot_reruns_at_the_end(self):
        with self.scripted(HOST):
            record = self.run_harness(request_id='host')
        self.assertCompleted(record)
        rows = self.rows(record)
        self.assertEqual(len(self.children), 3)
        self.assertEqual([row['run_id'] for row in rows], [self.children[2], self.children[1]])
        self.assertEqual([(row['status'], row['equivalent']) for row in rows], [('finished', True), ('finished', True)])
        self.assertEqual((rows[0]['attempt'], rows[0]['first_attempt']['run_id']), (2, self.children[0]))
        self.assertEqual(self.result(self.children[0])['outcome_category'], 'host')

    def test_06_B3_an_operator_stop_that_is_no_spent_budget_moves_on_and_reruns(self):
        with self.scripted(OPERATOR):
            record = self.run_harness(request_id='operator')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 3)
        self.assertEqual(self.result(self.children[0])['outcome_category'], 'operator')

    def test_06_B3_an_operators_cancel_of_the_child_moves_on_and_reruns(self):
        def runtime(request):
            if self.nth(request) == 0 and request.stage == 'probe':
                run_id = self.children[0]
                threading.Thread(target=self.controller.call, args=('cancel', {'run_id': run_id}), daemon=True).start()
                self.until(lambda: self.controller.call('status', {'run_id': run_id})['status'] == 'cancelled')
        with self.staged(REFERENCE_A, runtime=runtime):
            record = self.run_harness(request_id='cancelled')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 3)
        self.assertEqual(self.rows(record)[0]['first_attempt']['run_id'], self.children[0])

    def test_06_B3_a_completed_child_awaiting_its_grade_stops_the_suite_until_a_resume_grades_it(self):
        stop = threading.Event()
        with self.staged(REFERENCE_A, runtime=self.dying_at(stop, 'reuse', 0)), self.grading_faults():
            first = self.run_harness(request_id='awaiting', stop=stop)
            self.ended(self.children[0])
            status = self.status(first)
            self.assertEqual((status['status'], self.slot_status(first, 0)), ('blocked', 'running'))
            self.assertEqual(self.graded, [])
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 2)
        self.assertEqual([row['equivalent'] for row in self.rows(first)], [True, True])

    def test_06_B3_a_verdict_whose_post_was_lost_stops_the_suite_and_a_resume_posts_it_ungraded_again(self):
        lost = []

        def intercept(method, params, forward):
            if method == 'record_evaluation' and not lost:
                lost.append(method)
                return {'ok': False, 'reason': 'The reply was lost'}
            return forward(method, params)
        with self.scripted(), self.grading_faults():
            first = self.run_harness(request_id='lost-verdict', intercept=intercept)
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.slot_status(first, 0), 'running')
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [FIRST, SECOND])
        self.assertEqual([row['equivalent'] for row in self.rows(first)], [True, True])


class NoRunTests(ScoringFixture):
    def refusing_once(self, target, attribute, error):
        real = getattr(target, attribute)
        refused = []

        def once(*args, **kwargs):
            if not refused:
                refused.append(True)
                raise error
            return real(*args, **kwargs)
        return patch.object(target, attribute, side_effect=once)

    def assertRerunAfter(self, record, word, secret):
        self.assertCompleted(record)
        rows = self.rows(record)
        self.assertEqual((rows[0]['attempt'], rows[0]['first_attempt']['run_id'], rows[0]['first_attempt']['outcome']),
                         (2, None, 'fault'))
        self.assertEqual([row['equivalent'] for row in rows], [True, True])
        events = self.controller.call('suite_events', {'suite_id': record['suite_id']})['events']
        faulted = next(event for event in events if event['kind'] == 'trial.faulted')
        self.assertEqual(faulted['data']['no_run_reason'], word)
        self.assertNotIn(secret, json.dumps(self.calls, default=str) + json.dumps(events))
        self.assertIn(secret, json.dumps(self.trial_records()))

    def test_06_B4_a_refused_admit_faults_with_its_word_and_reruns(self):
        with self.scripted(), self.refusing_once(harness, 'admit', ValueError('Refused under /private/admit-path')):
            record = self.run_harness(request_id='admit')
        self.assertRerunAfter(record, 'admit-refused', '/private/admit-path')

    def test_06_B4_a_provider_that_would_not_start_faults_with_its_word_and_reruns(self):
        with self.scripted(), patch(START, side_effect=self.failing_first_start()):
            record = self.run_harness(request_id='provider')
        self.assertRerunAfter(record, 'provider-failed', '/private/provider-path')

    def failing_first_start(self):
        from generated_fixtures import fixture_start
        real, calls = fixture_start(process=True), []

        def start(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise OSError('No emulator at /private/provider-path')
            return real(**kwargs)
        return start

    def test_06_B4_a_refused_start_faults_with_its_word_and_reruns(self):
        error = ValueError('Refused under /private/start-path')
        with self.scripted(), self.refusing_once(self.controller, 'start', error):
            record = self.run_harness(request_id='start')
        self.assertRerunAfter(record, 'start-refused', '/private/start-path')

    def test_06_B4_a_child_found_after_a_lost_start_reply_is_attached_rather_than_faulted(self):
        lost = []

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_start_child' and not lost:
                lost.append(reply['run_id'])
                return {'ok': False, 'error': 'ValueError', 'reason': 'The reply said otherwise'}
            return reply
        with self.staged(REFERENCE_A, runtime=self.stopping(HOST, stage='acquire')):
            record = self.run_harness(request_id='lost', intercept=intercept)
        self.assertCompleted(record)
        rows = self.rows(record)
        self.assertEqual(rows[0]['first_attempt']['run_id'], lost[0])
        self.assertNotIn('no_run_reason', rows[0])

    def test_06_B5_a_child_found_after_a_lost_start_reply_whose_request_differs_stops_the_suite(self):
        lost = []

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_start_child' and not lost:
                lost.append(reply['run_id'])
                with self.controller._db() as db:
                    row = db.execute('SELECT spec FROM runs WHERE id=?', (reply['run_id'],)).fetchone()
                    spec = json.loads(row['spec'])
                    spec['_submitted_intent']['goal'] = 'Another goal'
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(spec), reply['run_id']))
                return {'ok': False, 'error': 'ValueError', 'reason': 'The reply said otherwise'}
            return reply
        with self.staged(REFERENCE_A, runtime=self.stopping(HOST, stage='acquire')):
            record = self.run_harness(request_id='lost-mismatch', intercept=intercept)
        self.assertEqual(record['status'], 'failed')
        status = self.status(record)
        self.assertEqual((status['status'], self.slot_status(record, 0)), ('blocked', 'running'))
        self.assertIn('child identity mismatch', status['reason'])
        self.ended(lost[0])

    def test_06_B4_a_refused_or_unreachable_reply_to_the_fault_stops_the_suite(self):
        for name, reply in (('refused', {'ok': False, 'error': 'ValueError', 'reason': 'Refused'}),
                            ('unreachable', {'ok': False, 'reason': 'ConnectionError: gone'})):
            with self.subTest(name):
                def intercept(method, params, forward, reply=reply):
                    return reply if method == 'suite_no_run' else forward(method, params)
                with self.scripted(), self.refusing_once(harness, 'admit', ValueError('Refused')):
                    record = self.run_harness(request_id='reply-' + name, intercept=intercept)
                self.assertEqual(record['status'], 'failed')
                self.assertEqual(self.status(record)['status'], 'blocked')
                self.assertEqual(self.rows(record)[0]['status'], 'launching')

    def test_06_B4_a_reply_that_never_came_to_a_start_is_no_fault(self):
        calm = patch.object(harness, 'UNREACHABLE', 0)

        def intercept(method, params, forward):
            if method == 'suite_start_child':
                return {'ok': False, 'reason': 'ConnectionError: gone'}
            return forward(method, params)
        with self.scripted(), calm:
            record = self.run_harness(request_id='never', intercept=intercept)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(self.status(record)['status'], 'blocked')
        self.assertNotIn('suite_no_run', [method for method, _ in self.calls])

    def test_06_B7_a_suite_whose_every_slot_faults_runs_every_slot_and_rerun_and_completes(self):
        with self.scripted(HOST, HOST, HOST, HOST):
            record = self.run_harness(request_id='all')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 4)
        rows = self.rows(record)
        self.assertEqual([(row['status'], row['outcome_category'], row['attempt']) for row in rows],
                         [('finished', 'host', 2), ('finished', 'host', 2)])

    def test_06_B7_a_suite_with_no_run_anywhere_also_completes(self):
        with self.scripted(), patch.object(harness, 'admit', side_effect=ValueError('Refused')):
            record = self.run_harness(request_id='none')
        self.assertCompleted(record)
        rows = self.rows(record)
        self.assertEqual([(row['status'], row['no_run_reason'], row['attempt']) for row in rows],
                         [('finished', 'admit-refused', 2), ('finished', 'admit-refused', 2)])
        self.assertEqual(self.children, [])


class StillStopsTests(ScoringFixture):
    def test_06_B5_an_uncertain_effect_stops_the_suite_until_it_is_reconciled(self):
        def runtime(request):
            if self.nth(request) == 0 and request.stage == 'probe':
                self.set_uncertain(self.children[0])
                return OPERATOR
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id='uncertain')
            self.assertEqual((self.status(first)['status'], self.slot_status(first, 0)), ('blocked', 'running'))
            self.assertEqual(len(self.children), 1)
            with self.assertRaisesRegex(ValueError, 'Outstanding effect is uncertain'):
                self.run_harness(suite_id=first['suite_id'])
            self.set_uncertain(self.children[0], 0)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.rows(first)[0]['first_attempt']['run_id'], self.children[0])

    def test_06_B5_a_child_identity_mismatch_stops_the_suite(self):
        def intercept(method, params, forward):
            if method == 'suite_settle':
                run_id = self.children[0]
                with self.controller._db() as db:
                    spec = json.loads(db.execute('SELECT spec FROM runs WHERE id=?', (run_id,)).fetchone()['spec'])
                    spec['_submitted_intent']['goal'] = 'Another goal'
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(spec), run_id))
            return forward(method, params)
        with self.scripted(HOST):
            record = self.run_harness(request_id='mismatch', intercept=intercept)
        status = self.status(record)
        self.assertEqual((status['status'], self.slot_status(record, 0)), ('blocked', 'running'))
        self.assertIn('child identity mismatch', status['reason'])

    def test_06_B5_a_configurator_it_cannot_reach_stops_the_suite(self):
        def intercept(method, params, forward):
            if method == 'suite_settle':
                return {'ok': False, 'reason': 'ConnectionError: gone'}
            return forward(method, params)
        with self.staged(REFERENCE_A, runtime=self.stopping(HOST, stage='acquire')):
            record = self.run_harness(request_id='unreachable', intercept=intercept)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual((self.status(record)['status'], self.slot_status(record, 0)), ('blocked', 'running'))

    def test_06_B5_the_vault_not_closed_in_time_stops_the_suite_and_faults_no_slot(self):
        looks = []

        def unreachable(_root):
            looks.append(True)
            return len(looks) <= 2
        with self.staged(REFERENCE_A, runtime=self.stopping(HOST, stage='acquire')), \
                patch.object(generated_trials, 'set_unreachable', side_effect=unreachable), \
                patch.object(harness, 'UNMOUNT_WAIT', .05), patch.object(harness, 'UNMOUNT_POLL', .005):
            record = self.run_harness(request_id='vault')
        self.assertEqual((record['status'], record.get('reason')),
                         ('failed', 'The vault holding set ' + SET + ' was not closed in time'))
        self.assertEqual((self.slot_status(record, 0), self.slot_status(record, 1)), ('faulted', 'launching'))
        self.assertEqual(self.status(record)['status'], 'blocked')

    def test_06_B5_the_suites_budget_running_out_stops_it_and_a_resume_with_more_settles_its_child(self):
        started = []

        def runtime(request):
            if self.nth(request) == 0 and request.stage == 'acquire':
                self.until(lambda: started)
                self.spend_suite_budget(started[0])
                run_id = self.children[0]
                self.until(lambda: self.controller.call('status', {'run_id': run_id})['status'] == 'cancelled')

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_start_child' and not started:
                started.append(params['suite_id'])
            return reply
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id='budget', intercept=intercept)
            status = self.status(first)
            self.assertEqual((status['status'], status['reason']), ('blocked', 'Suite wall-clock budget exhausted'))
            self.assertEqual(self.slot_status(first, 1), 'pending')
            with self.assertRaisesRegex(ValueError, 'Suite budget exhausted'):
                self.run_harness(suite_id=first['suite_id'])
            second = self.run_harness(suite_id=first['suite_id'], suite_budget_seconds=20000, budget_reason='Approved')
        self.assertCompleted(second)
        self.assertEqual(self.rows(first)[0]['first_attempt']['run_id'], self.children[0])

    def test_06_B5_a_cancel_of_the_suite_stops_it_and_a_resume_settles_its_child_by_classify(self):
        started = []

        def runtime(request):
            if self.nth(request) == 0 and request.stage == 'acquire':
                self.until(lambda: started)
                threading.Thread(target=self.controller.call, args=('suite_cancel', {'suite_id': started[0]}),
                                 daemon=True).start()
                run_id = self.children[0]
                self.until(lambda: self.controller.call('status', {'run_id': run_id})['status'] == 'cancelled')

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_start_child' and not started:
                started.append(params['suite_id'])
            return reply
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id='cancel', intercept=intercept)
            self.assertEqual(self.status(first)['status'], 'cancelled')
            self.assertEqual(len(self.children), 1)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.rows(first)[0]['first_attempt']['run_id'], self.children[0])


class ResumedSlotTests(unittest.TestCase):
    """A resumed scoring suite's harness over a recording `call`: what a slot needs, given no set root or password."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()

    def resume(self, child, **slot):
        self.calls = []
        reservations = iter([
            {'ok': True, 'status': 'running', 'scoring': True,
             'request': {'budget_seconds': 1, 'case_pin': {'set': SET, 'device_id': FIRST}},
             'slot': {'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': 'generated', 'device_id': FIRST,
                      'child_request_id': 'child', 'run_id': 'run-1', **slot}},
            {'ok': True, 'status': 'completed', 'slot': None}])

        def call(method, params=None):
            self.calls.append(method)
            return {'ping': {'ok': True}, 'suite_resume': {'ok': True, 'suite_id': 'suite-1', 'attached': True},
                    'status': {'ok': True, 'stopping': False, 'uncertain_effect': False, 'equivalence': None, **child},
                    'suite_settle': {'ok': True, 'decision': 'advance', 'status': 'running'},
                    'suite_detach': {'ok': True}}[method] if method != 'suite_reserve' else next(reservations)
        return harness.run_suite(self.base / ('suite-' + str(len(list(self.base.iterdir())))),
                                 {'home': str(self.base / 'home'), 'suite_id': 'suite-1'}, call=call)

    def test_06_B13_a_stopped_slot_with_no_root_cannot_be_checked_and_fails_there(self):
        record = self.resume({'status': 'failed'})
        self.assertEqual((record['status'], record.get('reason')),
                         ('failed', 'The set root of ' + SET + ' is required to audit its stopped trial'))
        self.assertNotIn('suite_settle', self.calls)

    def test_06_B13_slots_with_a_verdict_or_an_uncertain_effect_settle_without_one(self):
        for name, child in (('a verdict', {'status': 'completed', 'equivalence': {'status': 'graded'}}),
                            ('a stop with an uncertain effect', {'status': 'blocked', 'uncertain_effect': True}),
                            ('a completed run with an uncertain effect',
                             {'status': 'completed', 'uncertain_effect': True})):
            with self.subTest(name):
                self.assertEqual(self.resume(child)['status'], 'finished')
                self.assertIn('suite_settle', self.calls)

    def test_06_B5_a_password_or_root_missing_at_a_slot_that_needs_it_stops_the_suite(self):
        record = self.resume({'status': 'completed'})
        self.assertEqual((record['status'], record['reason']), (
            'failed', 'The evaluator password file of ' + SET + ' is required to provide and grade its device'))

    def test_06_B13_a_stopped_slot_whose_findings_are_stored_needs_no_root(self):
        record = self.resume({'status': 'failed'}, audit=[FINDING])
        self.assertEqual(record['status'], 'finished', record)
        self.assertIn('suite_settle', self.calls)


class UnconfirmedStopTests(ScoringFixture):
    """The first emulator stops and never says so; every later one does."""

    def setUp(self):
        super().setUp()
        real, starts = fixture_start(process=True), []

        def start(*, recipe, private_dir=None, **others):
            starts.append(recipe)
            if len(starts) > 1:
                return real(recipe=recipe, private_dir=private_dir, **others)
            session = FixtureDevice().session(recipe, private_dir)
            session.process = type('Running', (), {'poll': lambda _self: None})()
            return session
        guard = patch(START, side_effect=start)
        guard.start()
        self.addCleanup(guard.stop)

    def assertStoppedAtTheSlot(self, record):
        self.assertEqual(record['status'], 'failed')
        self.assertIn('stop-unconfirmed', record['reason'])
        self.assertEqual((self.status(record)['status'], self.slot_status(record, 0)), ('blocked', 'running'))

    def test_06_B6_it_stops_the_suite_and_a_plain_resume_reruns_a_host_stop(self):
        with self.scripted(HOST):
            first = self.run_harness(request_id='unconfirmed-host')
            self.assertStoppedAtTheSlot(first)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 3)
        self.assertEqual(self.rows(first)[0]['first_attempt']['run_id'], self.children[0])
        self.assertEqual([method for method, _ in self.calls].count('attach_device'), 0)

    def test_06_B6_a_workers_own_stop_stays_a_failure_with_no_rerun(self):
        real = self.controller._check

        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'probe' and Path(run_dir).name == self.children[0]:
                return {'stop': {'category': 'model', 'reason': 'Scripted model'}, 'artifacts': [], 'checks': []}
            return real(spec, stage, run_dir, workspace, report, accepted, assignment)
        with patch.object(self.controller, '_check', side_effect=check), self.scripted():
            first = self.run_harness(request_id='unconfirmed-model')
            self.assertStoppedAtTheSlot(first)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.rows(first)[0]['outcome_category'], 'model')

    def test_06_B6_a_completed_child_with_no_confirmed_stop_grades_on_the_resume(self):
        with self.scripted(), self.grading_faults():
            first = self.run_harness(request_id='unconfirmed-completed')
            self.assertStoppedAtTheSlot(first)
            self.assertEqual(self.graded, [])
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.graded, [FIRST, SECOND])
        self.assertEqual([row['equivalent'] for row in self.rows(first)], [True, True])


class RerunTests(ScoringFixture):
    def test_06_B8_a_rerun_is_a_fresh_trial_under_a_new_request_with_the_same_pin_and_the_full_budget(self):
        with self.scripted(HOST):
            record = self.run_harness(request_id='fresh')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 3)
        first, rerun = self.children[0], self.children[2]
        specs = {}
        with self.controller._db() as db:
            for run_id in (first, rerun):
                specs[run_id] = json.loads(db.execute('SELECT spec FROM runs WHERE id=?', (run_id,)).fetchone()['spec'])
        self.assertNotEqual(specs[first]['request_id'], specs[rerun]['request_id'])
        self.assertEqual(specs[first]['_case_pin'], specs[rerun]['_case_pin'])
        self.assertEqual(specs[rerun]['budget_seconds'], set_manifest()['child_budget_seconds'])
        trials = [trial for trial in self.trial_records() if trial['run_id'] in (first, rerun)]
        self.assertEqual(len({trial['trial_id'] for trial in trials}), 2)
        self.assertEqual({trial['request_id'] for trial in trials},
                         {specs[first]['request_id'], specs[rerun]['request_id']})
        row = self.rows(record)[0]
        self.assertEqual(row['first_attempt']['request_id'], specs[first]['request_id'])
        self.assertEqual(row['first_attempt']['outcome'], 'fault')
        self.assertIs(type(row['first_attempt']['seconds']), float)
        self.assertIsNone(row['first_attempt']['tokens'])

    def test_06_B8_a_crash_during_a_rerun_resumes_that_same_attempt_and_B9_its_fault_stands(self):
        stop = threading.Event()
        died = self.dying_at(stop, 'acquire', 2, then=HOST)

        def runtime(request):
            return self.stopping(HOST, None)(request) or died(request)
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id='crash', stop=stop)
            self.assertEqual(self.status(first)['status'], 'blocked')
            self.ended(self.children[2])
            self.assertEqual(self.slot_status(first, 0), 'running')
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 3)
        row = self.rows(first)[0]
        self.assertEqual((row['run_id'], row['attempt'], row['status'], row['outcome_category']),
                         (self.children[2], 2, 'finished', 'host'))
        self.assertEqual(row['first_attempt']['run_id'], self.children[0])

    def test_06_B9_a_rerun_that_faults_finishes_and_is_never_run_again(self):
        with self.staged(REFERENCE_A, runtime=self.stopping(HOST, None, OPERATOR, stage='acquire')):
            record = self.run_harness(request_id='stands')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 3)
        row = self.rows(record)[0]
        self.assertEqual((row['status'], row['outcome_category'], row['attempt']), ('finished', 'operator', 2))


class RegradeTests(ScoringFixture):
    def kinds(self, record):
        page = self.controller.call('suite_events', {'suite_id': record['suite_id']})
        return [event['kind'] for event in page['events']]

    def test_06_B11_a_grading_fault_regrades_the_same_driver_in_rerun_order_with_no_new_worker(self):
        with self.scripted(), self.grading_faults(d0000d001=1):
            record = self.run_harness(request_id='regrade')
        self.assertCompleted(record)
        self.assertEqual(self.graded, [FIRST, SECOND, FIRST])
        self.assertEqual(len(self.children), 2)
        row = self.rows(record)[0]
        self.assertEqual((row['run_id'], row['equivalent'], row['status']), (self.children[0], True, 'finished'))
        self.assertNotIn('attempt', row)
        self.assertNotIn('first_attempt', row)
        self.assertIn('trial.regrade', self.kinds(record))

    def test_06_B11_a_regrade_unscored_again_starts_a_new_trial(self):
        with self.scripted(), self.grading_faults(d0000d001=2):
            record = self.run_harness(request_id='twice')
        self.assertCompleted(record)
        self.assertEqual(self.graded, [FIRST, SECOND, FIRST, FIRST])
        self.assertEqual(len(self.children), 3)
        row = self.rows(record)[0]
        self.assertEqual((row['run_id'], row['attempt'], row['equivalent']), (self.children[2], 2, True))
        self.assertEqual(row['first_attempt']['run_id'], self.children[0])

    def test_06_B11_a_reruns_grading_fault_gets_one_regrade_and_then_stands_as_a_fault(self):
        with self.scripted(), self.grading_faults(d0000d001=4):
            record = self.run_harness(request_id='four')
        self.assertCompleted(record)
        self.assertEqual(self.graded, [FIRST, SECOND, FIRST, FIRST, FIRST])
        self.assertEqual(len(self.children), 3)
        row = self.rows(record)[0]
        self.assertEqual((row['run_id'], row['attempt'], row['status'], row['equivalent']),
                         (self.children[2], 2, 'finished', None))

    def test_06_B11_a_crash_repeats_the_regrade(self):
        stop = threading.Event()

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_reserve' and (reply.get('slot') or {}).get('regrade') == 'spent':
                stop.set()
            return reply
        with self.scripted(), self.grading_faults(d0000d001=1):
            first = self.run_harness(request_id='regrade-crash', intercept=intercept, stop=stop)
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.slot_status(first, 0), 'running')
            self.assertEqual(self.graded, [FIRST, SECOND])
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [FIRST, SECOND, FIRST])
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.rows(first)[0]['equivalent'], True)

    def test_06_B11_a_lost_reply_to_a_regrades_verdict_is_not_taken_for_an_unscored_grade(self):
        posts = []

        def intercept(method, params, forward):
            if method == 'record_evaluation':
                posts.append(params['run_id'])
                if posts.count(params['run_id']) == 2 and len(posts) == 3:
                    return {'ok': False, 'reason': 'The reply was lost'}
            return forward(method, params)
        with self.scripted(), self.grading_faults(d0000d001=1):
            first = self.run_harness(request_id='regrade-lost', intercept=intercept)
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.rows(first)[0]['equivalent'], True)

    def unsealed_once_after(self, gradings):
        """A patch of `seal` that fails once, for the grade that made `gradings` calls of `reference_execute` so far."""
        real, failed = evaluator.seal, []

        def seal(*args, **kwargs):
            if len(self.graded) == gradings and not failed:
                failed.append(True)
                raise OSError('The disk is full')
            return real(*args, **kwargs)
        return patch.object(evaluator, 'seal', seal)

    def test_06_B11_a_regrade_that_grades_but_cannot_seal_stops_the_suite_and_a_resume_keeps_its_grade(self):
        with self.scripted(), self.grading_faults(d0000d001=1), self.unsealed_once_after(3):
            first = self.run_harness(request_id='unsealed')
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.slot_status(first, 0), 'running')
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [FIRST, SECOND, FIRST])
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.rows(first)[0]['equivalent'], True)

    def test_06_B11_a_reruns_regrade_that_cannot_seal_does_not_stand_as_a_fault(self):
        with self.scripted(), self.grading_faults(d0000d001=3), self.unsealed_once_after(5):
            first = self.run_harness(request_id='rerun-unsealed')
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.slot_status(first, 0), 'running')
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [FIRST, SECOND, FIRST, FIRST, FIRST])
        self.assertEqual(len(self.children), 3)
        row = self.rows(first)[0]
        self.assertEqual((row['attempt'], row['equivalent']), (2, True))

    def test_06_B11_a_grade_unscored_for_integrity_stands_with_no_regrade_or_rerun(self):
        from generative_driver.configurator import IntegrityError
        self.grading_error = IntegrityError('A hash differs')
        with self.scripted(), self.grading_faults(d0000d001=1):
            record = self.run_harness(request_id='integrity')
        self.assertCompleted(record)
        self.assertEqual(self.graded, [FIRST, SECOND])
        self.assertEqual(len(self.children), 2)
        self.assertEqual((self.rows(record)[0]['status'], self.rows(record)[0]['equivalent']), ('finished', None))


class ResumingTests(ScoringFixture):
    def test_06_B16_a_resume_never_continues_a_stopped_child_on_a_new_device(self):
        stop = threading.Event()
        with self.staged(REFERENCE_A, runtime=self.dying_at(stop, 'acquire', 0, then=HOST)):
            first = self.run_harness(request_id='stopped', stop=stop)
            self.ended(self.children[0])
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        methods = [method for method, _ in self.calls]
        self.assertNotIn('attach_device', methods)
        self.assertNotIn('suite_resume_child', methods)
        self.assertEqual(len(self.children), 3)
        self.assertEqual(self.controller.call('status', {'run_id': self.children[0]})['status'], 'failed')

    def test_06_B16_a_completed_ungraded_child_still_grades_on_a_resume(self):
        stop = threading.Event()
        with self.staged(REFERENCE_A, runtime=self.dying_at(stop, 'reuse', 0)), self.grading_faults():
            first = self.run_harness(request_id='completed', stop=stop)
            self.ended(self.children[0])
            self.assertEqual(self.graded, [])
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [FIRST, SECOND])
        self.assertEqual(self.rows(first)[0]['equivalent'], True)

    def test_06_B16_a_missing_trial_folder_for_a_completed_child_keeps_stopping_the_suite(self):
        stop = threading.Event()
        with self.staged(REFERENCE_A, runtime=self.dying_at(stop, 'reuse', 0)):
            first = self.run_harness(request_id='missing', stop=stop)
            self.ended(self.children[0])
            for path in (self.root / 'home-bench/suites').glob('*/trials'):
                shutil.rmtree(path)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'failed')
        self.assertIn('is not under this bench home', second['reason'])
        self.assertEqual((self.status(first)['status'], self.slot_status(first, 0)), ('blocked', 'running'))

    def test_06_B18_a_child_that_ended_settles_before_the_suites_budget_is_checked(self):
        spent = []

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_start_child' and not spent:
                spent.append(params['suite_id'])
            elif method == 'record_evaluation':
                self.spend_suite_budget(spent[0])
            return reply
        with self.scripted():
            record = self.run_harness(request_id='paid', intercept=intercept)
        status = self.status(record)
        self.assertEqual((status['status'], status['reason']), ('blocked', 'Suite wall-clock budget exhausted'))
        self.assertEqual((self.slot_status(record, 0), self.rows(record)[0]['equivalent']), ('finished', True))
        self.assertEqual(self.slot_status(record, 1), 'pending')


class ConfiguratorCallTests(SetSuiteFixture):
    """The calls that record a slot's fault or flag, as the configurator answers its harness."""
    token = 't' * 64

    def setUp(self):
        super().setUp()
        as_scoring_set(self)

    def suite(self, **changes):
        started = self.suite_start(self.pins, harness_token_sha256=hashlib.sha256(self.token.encode()).hexdigest(),
                                   **changes)
        self.assertTrue(started['ok'], started)
        return started['suite_id']

    def asked(self, method, suite_id, **params):
        return self.controller.call(method, {'suite_id': suite_id, 'token': self.token, 'ordinal': 0, **params})

    def reserved(self, suite_id):
        reply = self.asked('suite_reserve', suite_id)
        self.assertTrue(reply['slot'], reply)
        return reply

    def test_06_B1_a_reservation_says_whether_the_suite_froze_a_scoring_set(self):
        self.assertIs(self.reserved(self.suite())['scoring'], True)

    def test_06_B1_the_mark_is_fixed_at_freeze(self):
        suite = self.suite()
        as_table = json.loads(generated_sets._SETS_FILE.read_text())
        as_table['sets'][SET]['scoring'] = False
        generated_sets._SETS_FILE.write_text(json.dumps(as_table))
        self.assertIs(self.reserved(suite)['scoring'], True)

    def test_06_B4_it_records_a_no_run_fault_only_with_its_closed_word(self):
        suite = self.suite()
        self.reserved(suite)
        with self.assertRaisesRegex(ValueError, 'Not a reason a slot got no run: /private/path'):
            self.asked('suite_no_run', suite, reason='/private/path')
        self.assertEqual(self.asked('suite_no_run', suite, reason='admit-refused')['decision'], 'fault')

    def test_06_B4_B14_each_call_refuses_a_slot_not_reserved(self):
        suite = self.suite()
        for method, params in (('suite_no_run', {'reason': 'admit-refused'}), ('suite_flag', {'findings': [FINDING]})):
            with self.subTest(method), self.assertRaisesRegex(ValueError, 'The suite slot is not reserved'):
                self.asked(method, suite, **params)

    def test_06_B4_B14_each_call_refuses_a_suite_not_running(self):
        suite = self.suite()
        self.reserved(suite)
        self.controller._suites.set_state(suite, 'blocked', 'Stopped')
        for method, params in (('suite_no_run', {'reason': 'admit-refused'}), ('suite_flag', {'findings': [FINDING]})):
            with self.subTest(method), self.assertRaisesRegex(ValueError, 'The suite is blocked; it records nothing'):
                self.asked(method, suite, **params)

    def test_06_B4_B14_each_call_needs_the_harness_token(self):
        suite = self.suite()
        for method in ('suite_no_run', 'suite_flag'):
            with self.subTest(method), self.assertRaisesRegex(ValueError, 'Only the bench harness'):
                self.asked(method, suite, token='another')

    def test_06_B14_a_flag_on_a_slot_with_no_child_is_refused(self):
        suite = self.suite()
        self.reserved(suite)
        with self.assertRaisesRegex(ValueError, 'A flag names a slot with a child'):
            self.asked('suite_flag', suite, findings=[FINDING])
        self.assertNotIn('audit', self.controller._suites.get(suite)['trials'][0])


class AnyOtherSuiteCallTests(SetSuiteFixture):
    token = 't' * 64

    def test_06_B4_B14_each_call_refuses_a_suite_with_no_scoring_set(self):
        started = self.suite_start(self.pins, harness_token_sha256=hashlib.sha256(self.token.encode()).hexdigest())
        ask = {'suite_id': started['suite_id'], 'token': self.token, 'ordinal': 0}
        self.assertIs(self.controller.call('suite_reserve', ask)['scoring'], False)
        for method, params in (('suite_no_run', {'reason': 'admit-refused'}), ('suite_flag', {'findings': [FINDING]})):
            with self.subTest(method), self.assertRaisesRegex(ValueError, 'Only a scoring suite records this'):
                self.controller.call(method, {**ask, **params})


class OtherSuiteTests(GeneratedTrialFixture):
    """A set suite that is no scoring suite: the qualified fixture set under its development mark."""

    def test_06_B1_a_development_suite_still_stops_at_a_host_fault(self):
        children = []

        def runtime(request):
            children.append(run_of(request) or children[-1])
            if request.stage == 'probe':
                return HOST
        with self.staged(REFERENCE_A, runtime=runtime):
            record = self.drive(request_id='development')
        self.assertEqual(record['status'], 'finished', record)
        status = self.controller.call('suite_status', {'suite_id': record['suite_id']})
        self.assertEqual((status['status'], status['completed'], len(set(children))), ('blocked', 0, 1))
        self.assertIn('stopped: Scripted runtime exit', status['reason'])

    def test_06_B18_a_development_suites_budget_is_still_checked_before_its_child(self):
        spent = []

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == 'suite_start_child' and not spent:
                spent.append(params['suite_id'])
            elif method == 'record_evaluation':
                with self.controller._suites.open_db() as db:
                    db.execute('UPDATE suites SET created=created-10000 WHERE id=?', (spent[0],))
            return reply
        with self.staged(REFERENCE_A):
            record = self.drive('0000d001', request_id='budget', intercept=intercept)
        rows = self.controller.call('suite_result', {'suite_id': record['suite_id']})['trials']
        self.assertEqual(self.controller.call('suite_status', {'suite_id': record['suite_id']})['completed'], 0, rows)

    def test_06_B4_a_development_suites_refused_admit_still_stops_it_with_no_fault_call(self):
        calls = []
        with self.staged(REFERENCE_A), patch.object(harness, 'admit', side_effect=ValueError('Refused')):
            record = self.drive(request_id='refused', calls=calls)
        self.assertEqual(record['status'], 'failed', record)
        self.assertIn('Trial entry-000-repeat-000 failed: ValueError: Refused', record['reason'])
        self.assertNotIn('suite_no_run', [method for method, _ in calls])


if __name__ == '__main__':
    unittest.main()

"""Feature 06, slot policy: what a scoring suite's report and scorecard say of a rerun, and what becomes of a trial the
transcript audit flags. Hand-written fixture devices, scripted workers and synthetic credentials only; never Renode, a
real set, a vault or a model run."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from generative_driver import benchmark
from generative_driver.benchmark_support import harness, trial_store
from generative_driver.benchmark_support.audit import TRANSCRIPT, audit_commands
from generative_driver.benchmark_support.outcomes import pass_rate_line
from generative_driver.benchmark_support.scorecard import scorecard_markdown, suite_scorecard, trial_metrics
from generative_driver.benchmark_support.suite_reporting import compare_suites, report_suite
from generative_driver.benchmark_support.suites import resume_suite
from generated_fixtures import REFERENCE_A
from test_generated_trials import PASSWORD, SET, set_manifest
from test_slot_policy import FIRST, HOST, OPERATOR, SECOND, ScoringFixture

EXTENSION = ('A scoring suite runs every child on the child budget it froze, which pairing relies on: '
             'child_budget_seconds')


class ReportFixture(ScoringFixture):
    def setUp(self):
        super().setUp()
        self.vault = self.set_root.parent
        self.exports = self.vault / ('exports-' + uuid.uuid4().hex)
        self.addCleanup(shutil.rmtree, self.exports, ignore_errors=True)

    def report(self, record, name='export', **options):
        return report_suite(record['suite_id'], home=self.home, output=self.exports / name, password=PASSWORD,
                            set_root=self.set_root, vault=self.vault, **options)

    def card(self, report):
        return suite_scorecard(report, home=self.home, password=PASSWORD, set_root=self.set_root)

    def assertSavedReportReads(self, report):
        """Two copies of the report compare, device by device, as saved reports do."""
        paired = compare_suites(json.loads(json.dumps(report)), json.loads(json.dumps(report)))
        self.assertEqual(paired.get('compatible'), True, paired)
        return paired


class RerunReportTests(ReportFixture):
    def test_06_B10_a_rerun_stays_one_row_with_its_result_and_its_first_attempt_as_history(self):
        with self.scripted(HOST):
            record = self.run_harness(request_id='rerun-report')
            report = self.report(record)
            card = self.card(report)
        first, rerun = self.children[0], self.children[2]
        self.assertEqual(report['provisional'], False)
        self.assertEqual(len(report['trials']), 2)
        row = report['trials'][0]
        self.assertEqual((row['run_id'], row['status'], row['scored'], row['attempt']), (rerun, 'finished', 'pass', 2))
        history = row['first_attempt']
        self.assertEqual((history['run_id'], history['outcome'], history['tokens']), (first, 'fault', None))
        self.assertGreaterEqual(row['first_attempt']['seconds'], 0)
        self.assertEqual(report['pass_rate']['passes'], 2)
        # Pairing counts the device by the rerun's result and does not call it repeated.
        paired = self.assertSavedReportReads(report)
        self.assertEqual(paired['devices']['both'], 2)
        self.assertEqual(paired['excluded']['repeated'], 0)
        # The first attempt's records are in no stage figure or resource total.
        self.assertEqual(report['usage']['started_trials'], 2)
        self.assertEqual(card['measured_trials'], 2)
        self.assertEqual(card['stages']['acquire']['attempts'], 2)
        self.assertEqual(card['reruns'], {'count': 1, 'first_attempt_tokens': None,
                                          'first_attempt_seconds': row['first_attempt']['seconds'],
                                          'measured': {'first_attempt_tokens': 0, 'first_attempt_seconds': 1}})
        lines = scorecard_markdown(card).splitlines()
        self.assertEqual(sum(line.startswith('Reruns: 1') for line in lines), 1, lines)
        self.assertNotIn(first, json.dumps(card))

    def test_06_B10_a_first_attempts_reported_tokens_are_kept_and_a_card_with_none_says_zero(self):
        reported = {**HOST, 'usage': {'input_tokens': 5, 'output_tokens': 7}}
        with self.staged(REFERENCE_A, runtime=self.stopping(reported, reported, reported, reported, stage='acquire')):
            record = self.run_harness(request_id='tokens')
            report = self.report(record)
            card = self.card(report)
        self.assertEqual([row['first_attempt']['tokens'] for row in report['trials']], [12, 12])
        self.assertEqual((card['reruns']['count'], card['reruns']['first_attempt_tokens']), (2, 24))
        self.assertEqual(card['reruns']['measured']['first_attempt_tokens'], 2)
        self.assertEqual(report['pass_rate']['faults'], 2)
        self.assertEqual(self.assertSavedReportReads(report)['excluded']['ungraded'], 2)

    def test_06_B9_the_report_is_provisional_until_every_rerun_is_done(self):
        stop = threading.Event()
        stopping = self.stopping(HOST, stage='acquire')
        dying = self.dying_at(stop, 'acquire', 2, then=HOST)

        def runtime(request):
            return stopping(request) or dying(request)
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id='provisional', stop=stop)
            self.ended(self.children[2])
            self.assertEqual(self.report(first, 'before')['provisional'], True)
            second = self.run_harness(suite_id=first['suite_id'])
            report = self.report(second, 'after')
        self.assertCompleted(second)
        self.assertEqual(report['provisional'], False)
        self.assertEqual(report['trials'][0]['scored'], 'fault')
        self.assertEqual(report['counts']['stopped'], {'host': 1})

    def test_06_B4_a_row_with_no_run_is_a_fault_counted_under_stopped_host(self):
        with self.scripted(), patch.object(harness, 'admit', side_effect=ValueError('Refused')):
            record = self.run_harness(request_id='no-run')
            report = self.report(record)
        self.assertEqual(report['pass_rate']['faults'], 2)
        self.assertEqual(report['counts']['stopped'], {'host': 2})
        self.assertEqual((report['counts']['started'], report['counts']['not_run']), (0, 0))
        row = report['trials'][0]
        self.assertEqual((row['scored'], row['outcome'], row['no_run_reason'], row['reason']),
                         ('fault', 'stopped', 'admit-refused', 'admit-refused'))
        self.assertEqual(report['provisional'], False)
        self.assertSavedReportReads(report)

    def test_06_B11_a_grade_unscored_for_integrity_is_a_failure_in_the_report_and_its_saved_form(self):
        from generative_driver.configurator import IntegrityError
        self.grading_error = IntegrityError('A hash differs')
        with self.scripted(), self.grading_faults(d0000d001=1):
            record = self.run_harness(request_id='integrity')
            report = self.report(record)
        self.assertEqual([row['scored'] for row in report['trials']], ['failure', 'pass'])
        self.assertEqual(self.assertSavedReportReads(report)['devices']['both'], 1)

    def test_06_B2_the_exporters_unqualified_verdict_is_a_fault_with_no_rerun(self):
        with self.scripted():
            record = self.run_harness(request_id='unqualified')
            with patch('generative_driver.benchmark_support.suite_reporting._recorded_equivalence', return_value=False):
                report = self.report(record)
        self.assertEqual([row['scored'] for row in report['trials']], ['fault', 'fault'])
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 2)


    def test_06_B2_a_later_hand_change_to_a_child_reports_as_classify_reads_it_and_starts_no_rerun(self):
        changed = []

        def intercept(method, params, forward):
            # While the suite still runs and can still reserve a rerun.
            if method == 'suite_start_child' and self.children and not changed:
                changed.append(self.children[0])
                with self.controller._db() as db:
                    db.execute("UPDATE runs SET status='failed',outcome_category='host' WHERE id=?", changed)
            return forward(method, params)
        with self.scripted():
            record = self.run_harness(request_id='hand-change', intercept=intercept)
            report = self.report(record)
        self.assertEqual(changed, [self.children[0]])
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 2)
        self.assertEqual((report['trials'][0]['scored'], report['trials'][0]['status']), ('fault', 'finished'))
        self.assertNotIn('attempt', report['trials'][0])


class OtherSuiteReportTests(ReportFixture):
    def setUp(self):
        super().setUp()
        self.table.write_text(self.table.read_text().replace('"scoring": true', '"scoring": false'))

    def test_06_B1_a_development_suite_is_not_held_to_the_scoring_rule_for_integrity(self):
        from generative_driver.configurator import IntegrityError
        self.grading_error = IntegrityError('A hash differs')
        with self.scripted(), self.grading_faults(d0000d001=1):
            record = self.run_harness(request_id='development', manifest=set_manifest(FIRST))
            report = self.report(record)
        self.assertEqual(self.status(record)['status'], 'blocked')
        self.assertEqual(report['trials'][0]['scored'], 'fault')
        self.assertNotIn('flagged', report['counts'])
        card = self.card(report)
        self.assertEqual(card['reruns']['count'], 0)
        self.assertFalse(any(line.startswith('Reruns:') for line in scorecard_markdown(card).splitlines()))


class AuditFixture(ReportFixture):
    def command(self, path=None):
        return 'cat ' + str(path or self.set_root) + '/manifest.json'

    def write_transcript(self, request, path=None):
        (Path(request.workspace) / TRANSCRIPT).write_text(json.dumps({
            'type': 'item.completed', 'item': {'type': 'command_execution', 'command': self.command(path)}}) + '\n')

    def naming(self, nth=0, then=None, stage='acquire', path=None):
        """A `runtime` under which the nth child's worker runs a command naming the set's folder, and ends as `then`."""
        def runtime(request):
            if self.nth(request) == nth and request.stage == stage:
                self.write_transcript(request, path)
                return then
        return runtime

    def findings(self, path=None):
        """The hash a stored finding gives the command of `naming`."""
        return hashlib.sha256(self.command(path).encode()).hexdigest()

    def assertHoldsNoRoot(self):
        """No call to the configurator, no record of the harness and nothing the configurator stored holds the root."""
        root = str(self.set_root)
        self.assertNotIn(root, json.dumps(self.calls, default=str))
        suites = self.root / 'home-bench/suites'
        for path in [*suites.glob('*/suite.json'), *suites.glob('*/trials/*/trial.json')]:
            self.assertNotIn(root, path.read_text(), path)
            self.assertNotIn('command_sha256', path.read_text(), path)
        with self.controller._suites.open_db() as db:
            for table in ('suites', 'suite_trials', 'suite_events'):
                for row in db.execute('SELECT * FROM ' + table).fetchall():
                    self.assertNotIn(root, json.dumps(tuple(row), default=str), table)


class FlaggedTrialTests(AuditFixture):
    def test_06_B12_a_flagged_trial_is_not_graded_settles_flagged_gets_no_rerun_and_the_suite_goes_on(self):
        with self.staged(REFERENCE_A, runtime=self.naming()), self.grading_faults():
            record = self.run_harness(request_id='flagged')
        self.assertCompleted(record)
        self.assertEqual(self.graded, [SECOND])
        self.assertEqual(len(self.children), 2)
        rows = self.rows(record)
        self.assertEqual((rows[0]['status'], rows[0]['equivalent']), ('flagged', None))
        self.assertEqual(rows[1]['equivalent'], True)
        self.assertEqual(self.status(record)['completed'], 2)

    def test_06_B14_what_is_stored_is_the_transcripts_name_and_the_commands_hash_and_no_record_holds_the_root(self):
        with self.staged(REFERENCE_A, runtime=self.naming()):
            record = self.run_harness(request_id='stored')
        audit = self.rows(record)[0]['audit']
        self.assertEqual(len(audit), 1)
        self.assertEqual(set(audit[0]), {'transcript', 'command_sha256'})
        self.assertEqual(audit[0]['command_sha256'], self.findings())
        self.assertTrue(audit[0]['transcript'].endswith(TRANSCRIPT))
        self.assertFalse(Path(audit[0]['transcript']).is_absolute())
        self.assertHoldsNoRoot()

    def test_06_B14_a_flagged_trials_handoffs_are_sealed_with_no_grade(self):
        with self.staged(REFERENCE_A, runtime=self.naming()):
            record = self.run_harness(request_id='sealed')
        trial = self.original_record(record['suite_id'], self.children[0])
        self.assertEqual((trial['status'], trial['sealed'], trial.get('equivalence')), ('flagged', True, None))
        self.assertEqual(self.result(self.children[0])['equivalence'], None)
        self.assertCompleted(record)

    def test_06_B13_a_trial_that_would_have_faulted_is_flagged_with_no_rerun(self):
        with self.staged(REFERENCE_A, runtime=self.naming(then=HOST)):
            record = self.run_harness(request_id='faulted')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 2)
        row = self.rows(record)[0]
        self.assertEqual((row['status'], row['outcome_category']), ('flagged', 'host'))
        self.assertNotIn('attempt', row)

    def test_06_B13_a_rerun_with_a_flagged_command_is_flagged_and_keeps_its_first_attempt(self):
        stops = self.stopping(HOST, stage='acquire')
        names = self.naming(nth=2)

        def runtime(request):
            return stops(request) or names(request)
        with self.staged(REFERENCE_A, runtime=runtime):
            record = self.run_harness(request_id='rerun-flagged')
        self.assertCompleted(record)
        self.assertEqual(len(self.children), 3)
        row = self.rows(record)[0]
        self.assertEqual((row['status'], row['attempt'], row['first_attempt']['run_id']),
                         ('flagged', 2, self.children[0]))

    def test_06_B14_a_failed_flag_call_stops_the_suite_and_a_resume_audits_again_and_grades_nothing(self):
        refused = []

        def intercept(method, params, forward):
            if method == 'suite_flag' and not refused:
                refused.append(method)
                return {'ok': False, 'reason': 'The configurator refused the flag'}
            return forward(method, params)
        with self.staged(REFERENCE_A, runtime=self.naming()), self.grading_faults():
            first = self.run_harness(request_id='refused', intercept=intercept)
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.graded, [])
            trial = self.original_record(first['suite_id'], self.children[0])
            self.assertEqual(trial['device_stop']['confirmed'], True)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [SECOND])
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')

    def test_06_B14_a_flagged_child_with_an_uncertain_effect_stops_and_settles_flagged_after_reconciliation(self):
        names = self.naming(then=OPERATOR, stage='probe')

        def runtime(request):
            stopped = names(request)
            if stopped:
                self.set_uncertain(self.children[0])
            return stopped
        with self.staged(REFERENCE_A, runtime=runtime), self.grading_faults():
            first = self.run_harness(request_id='uncertain')
            self.assertEqual((self.status(first)['status'], self.slot_status(first, 0)), ('blocked', 'running'))
            stored = self.controller._suites.get(first['suite_id'])['trials'][0]['audit']
            self.assertEqual(stored[0]['command_sha256'], self.findings())
            self.set_uncertain(self.children[0], 0)
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [SECOND])
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')
        self.assertEqual(len(self.children), 2)

    def test_06_B12_a_grading_resume_checks_first(self):
        stop = threading.Event()
        names = self.naming()
        dies = self.dying_at(stop, 'reuse', 0)

        def runtime(request):
            return names(request) or dies(request)
        with self.staged(REFERENCE_A, runtime=runtime), self.grading_faults():
            first = self.run_harness(request_id='grading-resume', stop=stop)
            self.ended(self.children[0])
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.graded, [SECOND])
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')
        trial = self.original_record(first['suite_id'], self.children[0])
        self.assertEqual((trial['status'], trial['sealed']), ('flagged', True))

    def stopped_while_dead(self, request_id):
        """A suite whose first child ran a command naming the folder and stopped once its harness was gone: its
        record."""
        stop = threading.Event()
        names = self.naming()
        dies = self.dying_at(stop, 'probe', 0, then=HOST)

        def runtime(request):
            return names(request) or dies(request)
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id=request_id, stop=stop)
        self.ended(self.children[0])
        return first

    def test_06_B12_a_child_that_stopped_while_the_harness_was_dead_is_checked_on_the_resume_and_sealed(self):
        first = self.stopped_while_dead('dead')
        before = next(row for row in self.trial_records() if row['run_id'] == self.children[0])
        self.assertNotEqual(before.get('sealed'), True)
        with self.scripted():
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')
        self.assertEqual(len(self.children), 2)
        trial = self.original_record(first['suite_id'], self.children[0])
        self.assertEqual((trial['status'], trial['sealed'], trial['evidence']['path']),
                         ('flagged', True, 'run-evidence.enc'))

    def test_06_B12_a_stopped_child_with_no_trial_folder_is_still_flagged(self):
        first = self.stopped_while_dead('dead-folder')
        for path in (self.root / 'home-bench/suites').glob('*/trials'):
            shutil.rmtree(path)
        with self.scripted():
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')

    def test_06_B12_a_run_it_could_not_read_stops_the_suite_rather_than_settle_unaudited(self):
        lost = []

        def intercept(method, params, forward):
            if method == 'result' and not lost:
                lost.append(method)
                return {'ok': False, 'reason': 'The reply was lost'}
            return forward(method, params)
        with self.staged(REFERENCE_A, runtime=self.naming(then=HOST)):
            first = self.run_harness(request_id='unread', intercept=intercept)
            self.assertEqual((first['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.slot_status(first, 0), 'running')
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')

    def test_06_B14_a_flag_holds_only_a_transcripts_name_and_a_commands_hash(self):
        refused = []

        def intercept(method, params, forward):
            if method == 'suite_flag' and not refused:
                self.assertEqual(len(params['findings']), 1)
                valid = params['findings'][0]
                for findings in (None, [], [{'transcript': 't', 'command': self.command()}],
                                 [{'transcript': 't', 'command_sha256': 'not a hash'}],
                                 [{'transcript': 1, 'command_sha256': valid['command_sha256']}]):
                    refused.append(forward(method, {**params, 'findings': findings}))
            return forward(method, params)
        with self.staged(REFERENCE_A, runtime=self.naming()):
            record = self.run_harness(request_id='shape', intercept=intercept)
        self.assertEqual(len(refused), 5)
        for reply in refused:
            self.assertEqual(reply.get('ok'), False, reply)
            self.assertIn('A flag names a slot with a child', reply['reason'])
        row = self.rows(record)[0]
        self.assertEqual(row['status'], 'flagged')
        self.assertEqual([finding['command_sha256'] for finding in row['audit']], [self.findings()])

    def test_06_B5_a_flagged_child_whose_request_differs_stops_the_suite(self):
        def intercept(method, params, forward):
            if method == 'suite_flag':
                with self.controller._db() as db:
                    row = db.execute('SELECT spec FROM runs WHERE id=?', (self.children[0],)).fetchone()
                    spec = json.loads(row['spec'])
                    spec['_submitted_intent']['goal'] = 'Another goal'
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(spec), self.children[0]))
            return forward(method, params)
        with self.staged(REFERENCE_A, runtime=self.naming(then=HOST)):
            record = self.run_harness(request_id='flag-mismatch', intercept=intercept)
        status = self.status(record)
        self.assertEqual((status['status'], self.slot_status(record, 0)), ('blocked', 'running'))
        self.assertIn('child identity mismatch', status['reason'])
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.controller._suites.get(record['suite_id'])['trials'][0]['audit'][0]['command_sha256'],
                         self.findings())

    def test_06_B13_a_child_that_ended_after_its_harness_stopped_waiting_is_not_settled_unaudited(self):
        stop = threading.Event()
        names = self.naming()

        def released():
            return any(trial and trial['run_id'] == self.children[0] and trial.get('finished')
                       for trial in self.trial_records())

        def runtime(request):
            names(request)
            if self.nth(request) == 0 and request.stage == 'probe':
                # The harness stops waiting and has finished with the trial before this child ends.
                stop.set()
                self.until(released)
                return HOST

        def intercept(method, params, forward):
            if method == 'suite_settle':
                self.ended(self.children[0])
            return forward(method, params)
        with self.staged(REFERENCE_A, runtime=runtime):
            first = self.run_harness(request_id='ended-by-settle', stop=stop, intercept=intercept)
            self.ended(self.children[0])
            self.assertEqual((self.status(first)['status'], self.slot_status(first, 0)), ('blocked', 'running'))
            second = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(second)
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')

    def test_06_B14_a_stopped_run_it_could_not_read_stays_unflagged_until_a_resume_can_seal_it(self):
        first = self.stopped_while_dead('dead-unread')
        lost = []

        def intercept(method, params, forward):
            if method == 'result' and not lost:
                lost.append(method)
                return {'ok': False, 'reason': 'The reply was lost'}
            return forward(method, params)
        with self.scripted():
            second = self.run_harness(suite_id=first['suite_id'], intercept=intercept)
            self.assertEqual(lost, ['result'])
            self.assertEqual((second['status'], self.status(first)['status']), ('failed', 'blocked'))
            self.assertEqual(self.slot_status(first, 0), 'running')
            third = self.run_harness(suite_id=first['suite_id'])
        self.assertCompleted(third)
        self.assertEqual(self.rows(first)[0]['status'], 'flagged')
        trial = self.original_record(first['suite_id'], self.children[0])
        self.assertEqual((trial['status'], trial['sealed']), ('flagged', True))

    def test_06_B12_the_audit_only_reads_transcripts_of_its_own_set(self):
        elsewhere = self.root / 'not-the-set'
        with self.staged(REFERENCE_A, runtime=self.naming(path=elsewhere)):
            record = self.run_harness(request_id='clean')
        self.assertCompleted(record)
        self.assertEqual([row['status'] for row in self.rows(record)], ['finished', 'finished'])
        self.assertNotIn('suite_flag', [method for method, _ in self.calls])


class FlaggedReportTests(AuditFixture):
    def test_06_B15_flagged_is_settled_in_the_report_and_outside_the_pass_rate(self):
        with self.staged(REFERENCE_A, runtime=self.naming()):
            record = self.run_harness(request_id='report')
            report = self.report(record)
        self.assertEqual(report['provisional'], False)
        self.assertEqual(report['unscored'], [])
        self.assertEqual((report['counts']['flagged'], report['counts']['finished']), (1, 2))
        row = report['trials'][0]
        self.assertEqual((row['status'], row['scored'], row['outcome']), ('flagged', 'flagged', 'flagged'))
        self.assertEqual(set(row['audit'][0]), {'transcript', 'command_sha256'})
        self.assertEqual(report['pass_rate']['flagged'], 1)
        self.assertEqual(pass_rate_line(report['pass_rate']), '1/1 pass, 0 unknown, 0 faults, 2 planned, 1 flagged')
        self.assertEqual(self.status(record)['completed'], 2)
        self.assertEqual(self.assertSavedReportReads(report)['excluded']['ungraded'], 1)
        self.assertNotIn(str(self.set_root), json.dumps(report))

    def test_06_B15_a_report_path_never_overwrites_stored_findings_and_may_flag_a_settled_row(self):
        elsewhere = self.root / 'elsewhere'
        names = self.naming()
        other = self.naming(nth=1, path=elsewhere)

        def runtime(request):
            return names(request) or other(request)
        with self.staged(REFERENCE_A, runtime=runtime):
            record = self.run_harness(request_id='union')
            plain = self.report(record, 'plain')
            audited = self.report(record, 'audited', audit_paths=(str(elsewhere),))
            both = self.report(record, 'both', audit_paths=(str(elsewhere), str(self.set_root)))
        self.assertEqual([row['scored'] for row in plain['trials']], ['flagged', 'pass'])
        first, second = audited['trials']
        self.assertEqual(set(first['audit'][0]), {'transcript', 'command_sha256'})
        self.assertEqual((second['status'], second['outcome'], second['scored']), ('finished', 'equivalent', 'flagged'))
        self.assertEqual(second['audit'][0]['command'], self.command(elsewhere))
        self.assertEqual(self.assertSavedReportReads(audited)['pass_rate']['after']['flagged'], 2)
        # A stored finding and the same finding read again are one.
        self.assertEqual([set(finding) for finding in both['trials'][0]['audit']], [{'transcript', 'command'}])


class ChildBudgetExtensionTests(ScoringFixture):
    def stopped(self):
        stop = threading.Event()
        with self.staged(REFERENCE_A, runtime=self.dying_at(stop, 'acquire', 0, then=HOST)):
            first = self.run_harness(request_id='stopped', stop=stop)
        self.ended(self.children[0])
        return first

    def test_06_B17_with_the_vault_still_open_the_harness_refuses_it(self):
        first = self.stopped()
        with patch.object(harness, '_held_set', side_effect=AssertionError('The set was held')), \
                self.assertRaisesRegex(ValueError, '^' + EXTENSION + '$'):
            self.run_harness(suite_id=first['suite_id'], child_budget_seconds=600, child_budget_reason='Approved')
        self.assertEqual(self.status(first)['status'], 'blocked')
        self.assertNotIn('suite_resume', [method for method, _ in self.calls])

    def test_06_B17_the_configurator_refuses_it_as_the_authority(self):
        first = self.stopped()
        with self.assertRaisesRegex(ValueError, '^' + EXTENSION + '$'):
            self.run_harness(suite_id=first['suite_id'], set_root=None, child_budget_seconds=600,
                             child_budget_reason='Approved')
        self.assertEqual(self.status(first)['status'], 'blocked')

    def test_06_B17_a_suite_budget_extension_stays_as_it_was(self):
        first = self.stopped()
        with self.scripted():
            second = self.run_harness(suite_id=first['suite_id'], suite_budget_seconds=1000, budget_reason='Approved')
        self.assertCompleted(second)
        self.assertEqual(self.controller._suites.get(first['suite_id'])['budget_seconds'], 1000)
        self.assertEqual(self.rows(first)[0]['first_attempt']['run_id'], self.children[0])
        with self.controller._db() as db:
            row = db.execute('SELECT spec FROM runs WHERE id=?', (self.rows(first)[0]['run_id'],)).fetchone()
        spec = json.loads(row['spec'])
        self.assertEqual(spec['budget_seconds'], set_manifest()['child_budget_seconds'])


class ResumeSuiteRefusalTests(unittest.TestCase):
    """`resume_suite` over a recording configurator client, before any harness."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.identity = {'manifest': set_manifest(), 'scoring_set': {'registration': SET, 'manifest_sha256': 'a' * 64}}
        self.spawned = []

        def spawn(where, _arguments, payload):
            self.spawned.append(payload)
            (Path(where) / 'suite.json').write_text(json.dumps({'status': 'failed', 'accepted': False, 'finished': 1,
                                                                'reason': 'stopped here'}))
            return type('Ended', (), {'poll': lambda _self: 0})()
        guard = patch.object(harness, '_spawn', spawn)
        guard.start()
        self.addCleanup(guard.stop)

    def call(self, method, params=None, home=None, **_options):
        suite = {'ok': True, 'status': 'blocked', 'stopping': False, 'active_child_id': None}
        return {'suite_result': {**suite, 'experiment': {'comparison_identity': self.identity}},
                'suite_status': suite}.get(method, {'ok': False, 'reason': 'unknown'})

    def resume(self):
        with patch('generative_driver.client.call', self.call):
            return resume_suite('suite-1', {'set_root': str(self.base / 'set')}, home=self.base / 'home',
                                autostart=False, child_budget_seconds=600, child_budget_reason='Approved')

    def test_06_B17_it_refuses_a_scoring_suites_child_budget_extension_before_any_harness(self):
        with self.assertRaisesRegex(ValueError, '^' + EXTENSION + '$'):
            self.resume()
        self.assertEqual(self.spawned, [])

    def test_06_B17_a_development_suite_goes_on_to_the_harness(self):
        del self.identity['scoring_set']
        self.resume()
        self.assertEqual(len(self.spawned), 1)


class SealedSuiteTests(AuditFixture):
    """Feature 07, trial sealing, end to end through scripted suites, with real key derivations."""
    real_seals = True

    def stub_of(self, suite_id, key):
        return harness.read_trial(trial_store.find_trial(self.home, suite_id, key))

    def main(self, *arguments):
        """`benchmark suite ...` in this home: (exit code, what it printed)."""
        output = io.StringIO()
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.home)}), contextlib.redirect_stdout(output):
            code = benchmark.main(['suite', *arguments])
        return code, json.loads(output.getvalue())

    def recording_figures(self):
        """A patch under which what each run's figures were is kept, as they stand just before its first seal:
        (`seal_trial` patch, {run id: (metrics, public report)})."""
        real, figures = trial_store.seal_trial, {}

        def seal(trial, run_dir, workspaces, *args):
            if run_dir is not None and run_dir.name not in figures:
                figures[run_dir.name] = self.figures_of(run_dir.name, trial, run_dir)
            return real(trial, run_dir, workspaces, *args)
        return patch.object(trial_store, 'seal_trial', seal), figures

    def figures_of(self, run_id, trial_dir, run_dir):
        from generative_driver.reporting import report_run
        from generative_driver.benchmark_support.suite_reporting import _public_report
        with tempfile.TemporaryDirectory() as temporary:
            report = report_run(run_id, home=self.home, output=Path(temporary) / 'report.json', autostart=False)
        return trial_metrics(self.home, run_id, trial_dir=trial_dir, run_dir=run_dir), _public_report(report)

    def assertFiguresStand(self, suite_id, figures):
        self.assertGreater(len(figures), 0)
        for run_id, before in figures.items():
            with trial_store.opened(self.home, suite_id, run_id, PASSWORD) as root:
                self.assertEqual(self.figures_of(run_id, root / 'trial', root / 'run'), before, run_id)

    def test_07_B1_B7_B13_each_trial_is_sealed_as_it_concludes_and_its_figures_stand(self):
        host = self.stopping(HOST, stage='probe')
        names = self.naming(nth=1)
        readable_at_a_start, starts = [], []

        def runtime(request):
            return host(request) or names(request)

        def intercept(method, params, forward):
            if method == 'suite_start_child':
                starts.append(params['suite_id'])
                for row in forward('suite_result', {'suite_id': params['suite_id']})['trials']:
                    if row['status'] in ('finished', 'faulted', 'flagged'):
                        request = 'suite:' + params['suite_id'] + ':trial:' + str(row['ordinal'])
                        record = self.stub_of(params['suite_id'], request)
                        if record['status'] != 'sealed':
                            readable_at_a_start.append(request)
            return forward(method, params)
        patcher, figures = self.recording_figures()
        with self.staged(REFERENCE_A, runtime=runtime), patcher:
            record = self.run_harness(request_id='sealed-suite', intercept=intercept)
            self.assertCompleted(record)
            suite_id = record['suite_id']
            self.assertEqual(len(self.children), 3)
            self.assertEqual(len(starts), 3)
            self.assertEqual(readable_at_a_start, [])
            first, flagged, rerun = self.children
            self.assertEqual(sorted(figures), sorted(self.children))
            self.assertFiguresStand(suite_id, figures)
            rows = self.rows(record)
            self.assertEqual([row['status'] for row in rows], ['finished', 'flagged'])
            for run_id in self.children:
                stub = self.stub_of(suite_id, run_id)
                self.assertEqual(stub['status'], 'sealed')
                self.assertEqual(sorted(stub), ['case', 'finished', 'request_id', 'run_id', 'schema', 'sealed_files',
                                                'start_sent', 'status', 'trial_id'])
                self.assertIs(self.controller.call('status', {'run_id': run_id})['sealed'], True)
                run_dir = self.home / 'runs' / run_id
                self.assertEqual(sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob('*')
                                        if path.is_file() and 'inputs' not in path.parts), ['benchmark/execution.json'])
                trial = trial_store.find_trial(self.home, suite_id, run_id)
                self.assertFalse((trial / 'private').exists())
                self.assertFalse((trial / 'device').exists())
            # What the audit found of the flagged trial's transcript is read back from its seal.
            with trial_store.opened(self.home, suite_id, flagged, PASSWORD) as root:
                self.assertEqual(len(audit_commands(root / 'run', (str(self.set_root),))), 1)
                # The temporary interpret workspace went into the seal, and out of the temporary folder.
                self.assertTrue(list((root / 'workspaces').glob('gd-candidate-*/attempt*/model.json')))
            with self.controller._db() as db:
                workspaces = [json.loads(row['payload'])['workspace'] for row in db.execute(
                    "SELECT payload FROM events WHERE kind='stage.assigned'").fetchall()
                    if json.loads(row['payload'])['stage'] == 'interpret']
            self.assertTrue(workspaces)
            self.assertFalse([path for path in workspaces if Path(path).exists()])
            # `_verify_suite` reads only the kept paths: a report and a settled suite still stand.
            report = self.report(record)
            self.assertEqual([row['scored'] for row in report['trials']], ['pass', 'flagged'])
            with self.assertRaisesRegex(ValueError, 'A sealed run does not resume or take a device'):
                self.controller.call('resume', {'run_id': first})
            self.assertEqual(self.card(report)['pass_line'], pass_rate_line(report['pass_rate']))
            self.assertHoldsNoRoot()

    def test_07_B14_a_trial_due_a_regrade_is_restored_graded_again_and_sealed_again(self):
        restored = []
        real = trial_store.restore

        def restore(trial, password):
            real(trial, password)
            restored.append(harness.read_trial(trial)['status'])
        patcher, figures = self.recording_figures()
        with self.scripted(), self.grading_faults(d0000d001=1), patch.object(trial_store, 'restore', restore), patcher:
            record = self.run_harness(request_id='regraded')
            self.assertCompleted(record)
            suite_id, child = record['suite_id'], self.children[0]
            self.assertEqual(len(self.children), 2)
            self.assertEqual([row['equivalent'] for row in self.rows(record)], [True, True])
            self.assertEqual(restored[0], 'unscored')
            trial = trial_store.find_trial(self.home, suite_id, child)
            self.assertEqual(sorted(int(path.name.split('-')[1]) for path in trial.glob('sealed-*-index.enc')), [1, 2])
            index = trial / 'sealed-2-index.enc'
            members = PASSWORD.unlock(index, hashlib.sha256(index.read_bytes()).hexdigest())['members']
            self.assertEqual(list(members), ['trial/trial.json'])
            self.assertEqual(self.original_record(suite_id, child)['status'], 'evaluated')
            self.assertEqual(self.stub_of(suite_id, child)['status'], 'sealed')
            self.assertFalse(list((self.home / 'runs' / child).rglob('_agent_events.jsonl')))
            self.assertFiguresStand(suite_id, {self.children[1]: figures[self.children[1]]})

    def test_07_B4_a_failed_seal_stops_the_suite_and_a_resume_seals_it_before_any_worker_starts(self):
        real, disk_full, started = trial_store._seal_file, [True], []

        def seal(password, value, path):
            if disk_full:
                raise OSError(28, 'No space left on device', str(path))
            return real(password, value, path)

        def intercept(method, params, forward):
            if method == 'suite_start_child':
                started.append(self.stub_of(params['suite_id'], 'suite:' + params['suite_id'] + ':trial:0')['status'])
            return forward(method, params)
        with self.scripted(), patch.object(trial_store, '_seal_file', seal):
            first = self.run_harness(request_id='failed-seal', intercept=intercept)
            self.assertEqual(first['status'], 'failed')
            self.assertNotIn(str(self.root), first['reason'])
            status = self.status(first)
            self.assertEqual(status['status'], 'blocked')
            self.assertIn('Sealing a concluded trial failed: OSError', status['reason'])
            self.assertEqual(self.slot_status(first, 0), 'finished')
            self.assertEqual(len(self.children), 1)
            self.assertNotEqual(self.stub_of(first['suite_id'], self.children[0])['status'], 'sealed')
            disk_full.clear()
            second = self.run_harness(suite_id=first['suite_id'], intercept=intercept)
            self.assertCompleted(second)
        self.assertEqual(started[-1], 'sealed')
        for run_id in self.children:
            self.assertEqual(self.stub_of(first['suite_id'], run_id)['status'], 'sealed')

    def test_07_B5_B16_a_new_suite_waits_for_cancel_then_store_and_a_resume_of_what_was_stored_stops_there(self):
        stop = threading.Event()
        vault = self.root / 'vault'
        vault.mkdir()
        with self.staged(REFERENCE_A, runtime=self.dying_at(stop, 'probe', 0, then=HOST)):
            first = self.run_harness(request_id='abandoned', stop=stop)
            suite_id = first['suite_id']
            self.ended(self.children[0])
            self.assertEqual(self.status(first)['status'], 'blocked')
            self.calls.clear()
            with self.assertRaisesRegex(ValueError, 'still has a readable trial.*' + suite_id):
                self.run_harness(request_id='beside')
            self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_result'])
            code, printed = self.main('store', suite_id, '--password-file', str(self.handle), '--vault-folder',
                                      str(vault))
            self.assertEqual(code, 1, printed)
            self.assertIn('takes a completed suite', printed['error'])
            self.controller.call('suite_cancel', {'suite_id': suite_id})
            code, printed = self.main('store', suite_id, '--password-file', str(self.handle), '--vault-folder',
                                      str(vault))
            self.assertEqual((code, printed), (0, {'ok': True, 'suite_id': suite_id, 'stored': True}))
            stub = self.stub_of(suite_id, self.children[0])
            self.assertEqual(stub['status'], 'sealed')
            trial = trial_store.find_trial(self.home, suite_id, self.children[0])
            self.assertEqual(list(trial.glob('sealed-*.enc')), [])
            self.assertTrue((vault / 'sealed-trials' / suite_id / trial.name / 'sealed-1-index.enc').exists())
            self.assertIs(self.controller.call('status', {'run_id': self.children[0]})['sealed'], True)
            with trial_store.opened(self.home, suite_id, self.children[0], PASSWORD, vault=vault) as root:
                self.assertTrue((root / 'trial/trial.json').exists())
            # A later resume reaching such a slot fails safe.
            again = self.run_harness(suite_id=suite_id)
            self.assertEqual(again['status'], 'failed')
            self.assertIn('no longer beside its stub', again['reason'])
            self.assertEqual(self.slot_status(first, 0), 'running')
            self.controller.call('suite_cancel', {'suite_id': suite_id})
            # With nothing left readable, a new suite of the set starts.
            self.children.clear()
            new = self.run_harness(request_id='after-the-store')
            self.assertCompleted(new)

    def test_07_B15_B16_B17_B18_after_store_the_commands_read_it_from_the_vault_folder(self):
        vault = self.exports / 'vault'
        vault.mkdir(parents=True)
        handle = ['--password-file', str(self.handle)]
        place = ['--vault-folder', str(vault)]
        with self.staged(REFERENCE_A, runtime=self.naming()):
            record = self.run_harness(request_id='commands')
            suite_id = record['suite_id']
            flagged = self.children[0]
            self.assertCompleted(record)
            before = self.main('scorecard', suite_id, '--output', str(self.root / 'card-before'), *handle,
                               '--set-root', str(self.set_root))
            self.assertEqual(before[0], 0, before[1])
            code, stored = self.main('store', suite_id, *handle, *place)
            self.assertEqual(code, 0, stored)
            code, card = self.main('scorecard', suite_id, '--output', str(self.root / 'card'), *handle, *place,
                                   '--set-root', str(self.set_root), '--audit-path', str(self.set_root))
            self.assertEqual(code, 0, card)
            self.assertEqual(card['stages'], before[1]['stages'])
            self.assertEqual(card['pass_line'], before[1]['pass_line'])
            self.assertEqual(list(card['groups']['level']), ['1'])
            self.assertEqual(list(card['groups']['framing']), ['text-lines'])
            text = json.dumps(card) + (self.root / 'card/scorecard.md').read_text()
            for private in (flagged, '0000d001', str(self.set_root), str(vault)):
                self.assertNotIn(private, text)
            code, sealed_away = self.main('scorecard', suite_id, '--output', str(self.root / 'no-card'), *handle,
                                          '--set-root', str(self.set_root))
            self.assertEqual(code, 1, sealed_away)
            self.assertIn('vault folder', sealed_away['error'])
            self.assertFalse((self.root / 'no-card').exists())
            code, none = self.main('scorecard', suite_id, '--output', str(self.root / 'no-card'))
            self.assertEqual(code, 1, none)
            self.assertIn('password file', none['error'])
            code, refused = self.main('report', suite_id, '--output', str(self.root / 'no-report'))
            self.assertEqual(code, 1, refused)
            self.assertIn('password file', refused['error'])
            self.assertFalse((self.root / 'no-report').exists())
            # The set root must lie under the vault folder here too, so this report is written beside it.
            code, report = self.main('report', suite_id, '--output', str(self.exports / 'report'), *handle,
                                     '--set-root', str(self.set_root), '--vault-folder', str(self.vault),
                                     '--audit-path', str(self.set_root))
            self.assertEqual(code, 1, report)
            self.assertIn('vault folder', report['error'])
            code, report = self.main('report', suite_id, '--output', str(self.exports / 'report'), *handle,
                                     '--set-root', str(self.set_root), '--vault-folder', str(self.vault))
            self.assertEqual(code, 0, report)
            self.assertEqual([row['scored'] for row in report['trials']], ['flagged', 'pass'])
            self.assertEqual(self.assertSavedReportReads(report)['devices']['both'], 1)
            code, unsealed = self.main('unseal', suite_id, '--trial', flagged, *handle, *place, '--output',
                                       str(vault / 'checking'))
            self.assertEqual(code, 0, unsealed)
            found = audit_commands(vault / 'checking/run', (str(self.set_root),))
            self.assertEqual([finding['command'] for finding in found], [self.command()])
            self.assertNotIn(str(vault), json.dumps(self.calls, default=str))


if __name__ == '__main__':
    unittest.main()

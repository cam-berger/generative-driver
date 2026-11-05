"""Feature 06, slot policy: what a scoring suite's report and scorecard say of a rerun, and what becomes of a trial the
transcript audit flags. Hand-written fixture devices, scripted workers and synthetic credentials only; never Renode, a
real set, a vault or a model run."""
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark_support import harness
from generative_driver.benchmark_support.audit import TRANSCRIPT
from generative_driver.benchmark_support.outcomes import pass_rate_line
from generative_driver.benchmark_support.scorecard import scorecard_markdown, suite_scorecard
from generative_driver.benchmark_support.suite_reporting import compare_suites, report_suite
from generative_driver.benchmark_support.suites import resume_suite
from generated_fixtures import REFERENCE_A
from test_generated_trials import SET, set_manifest
from test_slot_policy import FIRST, HOST, OPERATOR, SECOND, ScoringFixture

EXTENSION = ('A scoring suite runs every child on the child budget it froze, which pairing relies on: '
             'child_budget_seconds')


class ReportFixture(ScoringFixture):
    def report(self, record, name='export', **options):
        return report_suite(record['suite_id'], home=self.home, output=self.root / name, **options)

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
            card = suite_scorecard(report, home=self.home)
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
            card = suite_scorecard(report, home=self.home)
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
        card = suite_scorecard(report, home=self.home)
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
        trial = next(row for row in self.trial_records() if row['run_id'] == self.children[0])
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
            trial = next(row for row in self.trial_records() if row['run_id'] == self.children[0])
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
        trial = next(row for row in self.trial_records() if row['run_id'] == self.children[0])
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
        trial = next(row for row in self.trial_records() if row['run_id'] == self.children[0])
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
        trial = next(row for row in self.trial_records() if row['run_id'] == self.children[0])
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


if __name__ == '__main__':
    unittest.main()

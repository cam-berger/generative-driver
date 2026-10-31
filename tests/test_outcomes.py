"""One outcome for every planned trial, and the pass rate printed from the outcomes."""
import unittest

from generative_driver.benchmark_support.outcomes import classify, pass_rate, pass_rate_line

done = {'run_id': 'r', 'status': 'finished', 'outcome_category': 'completed'}


def graded(equivalent):
    return {'workflow_status': 'completed', 'equivalence_status': 'graded',
            'equivalence': {'status': 'graded', 'equivalent': equivalent}}


no_driver = {'workflow_status': 'completed', 'equivalence_status': 'no-driver',
             'equivalence': {'status': 'no-driver', 'equivalent': False}}
unknown = {'workflow_status': 'completed', 'equivalence_status': 'unknown',
           'equivalence': {'status': 'unknown', 'equivalent': None}}


def stopped(category):
    return {'run_id': 'r', 'status': 'blocked', 'outcome_category': category}


def exported(trial, report):
    """The row a suite report exports for a trial: its slot fields and its report's grade, but no workflow_status and
    no effective_budget_seconds."""
    grade = {key: report[key] for key in ('verdict', 'equivalence', 'equivalence_status', 'elapsed_seconds')
             if key in report}
    return {**trial, **grade}


class ClassifyTests(unittest.TestCase):
    def assertOutcome(self, trial, report, expected):
        """The slot with its full report and the exported row alone give the trial the same outcome."""
        row = exported(trial, report)
        self.assertEqual((classify(trial, report), classify(row, row)), (expected, expected))

    def test_grades(self):
        self.assertEqual([classify(done, graded(True)), classify(done, graded(False))], ['pass', 'failure'])
        self.assertEqual(classify(done, {'workflow_status': 'completed', 'equivalence_status': 'no-driver',
                                         'equivalence': {'status': 'no-driver', 'equivalent': False}}), 'failure')

    def test_a_graded_record_is_a_pass_only_when_it_says_equivalent(self):
        for report in (graded(None), {**graded(True), 'equivalence': None}):
            with self.subTest(report=report):
                self.assertEqual(classify(done, report), 'failure')

    def test_spent_budget_is_a_failure_and_other_operator_stops_are_faults(self):
        spent = {'workflow_status': 'blocked', 'elapsed_seconds': 10800, 'effective_budget_seconds': 10800}
        self.assertEqual(classify(stopped('operator'), spent), 'failure')
        self.assertEqual(classify(stopped('operator'), {**spent, 'elapsed_seconds': 100}), 'fault')
        self.assertEqual(classify(stopped('operator'), {'workflow_status': 'blocked'}), 'fault')

    def test_self_caused_stops_fail_and_environment_stops_are_faults(self):
        categories = ('safety', 'integrity', 'model', 'host')
        self.assertEqual([classify(stopped(c), {'workflow_status': 'blocked'}) for c in categories],
                         ['failure', 'failure', 'failure', 'fault'])

    def test_missing_grade_is_a_fault_pending_is_running_unknown_is_unknown(self):
        undecided = {'status': 'unknown', 'equivalent': None}
        self.assertEqual([classify(done, {'workflow_status': 'completed', 'equivalence_status': s,
                                          'equivalence': None if s != 'unknown' else undecided})
                          for s in ('unscored', 'pending', 'unknown')], ['fault', 'running', 'unknown'])

    def test_flagged_and_not_run(self):
        self.assertEqual((classify(done, graded(True), flagged=True), classify({'run_id': None}, None)),
                         ('flagged', 'not_run'))

    def test_a_run_still_in_flight_is_running(self):
        for status in ('pending', 'launching', 'running', 'queued'):
            with self.subTest(status=status):
                self.assertEqual(classify({**done, 'status': status}, None), 'running')
                self.assertEqual(classify({'run_id': 'r'}, {'workflow_status': status}), 'running')

    def test_every_stopped_status_is_judged_by_its_category(self):
        for status in ('blocked', 'failed', 'cancelled'):
            with self.subTest(status=status):
                report = {'workflow_status': status}
                self.assertEqual(classify({**stopped('safety'), 'status': status}, report), 'failure')
                others = [classify({**stopped(category), 'status': status}, report)
                          for category in ('cancelled', 'unknown', None)]
                self.assertEqual(others, ['fault', 'fault', 'fault'])

    def test_a_stop_recorded_on_the_row_alone_counts_in_either_call_shape(self):
        # report_suite blocks the row of a completed child that is still stopping or has an uncertain effect, and leaves
        # its report completed and graded.
        self.assertOutcome({**done, 'status': 'blocked'}, graded(True), 'fault')

    def test_a_grade_the_exporter_could_not_confirm_is_a_fault_in_either_call_shape(self):
        # report_suite marks a claimed equivalent that the owner's records do not confirm unqualified, and the row it
        # exports carries that verdict.
        self.assertOutcome(done, {**graded(True), 'verdict': 'unqualified'}, 'fault')
        # A stop is judged before the verdict.
        self.assertOutcome(stopped('model'), {'workflow_status': 'blocked', 'verdict': 'unqualified'}, 'failure')

    def test_a_grade_whose_slot_has_not_settled_is_running_even_when_marked_unqualified(self):
        # The exporter marks a graded equivalent driver unqualified until its slot settles. A settled slot whose grade
        # the exporter could not confirm stays a fault.
        for report in (graded(True), graded(False), no_driver, unknown):
            unconfirmed = {**report, 'verdict': 'unqualified'}
            with self.subTest(grade=report['equivalence_status'], equivalent=report['equivalence']['equivalent']):
                self.assertOutcome({**done, 'status': 'completed'}, unconfirmed, 'running')
                self.assertOutcome(done, unconfirmed, 'fault')

    def test_a_recorded_grade_counts_only_once_its_slot_is_settled(self):
        # Settled, each counts (done is finished).
        for report, expected in ((graded(True), 'pass'), (graded(False), 'failure'), (no_driver, 'failure'),
                                 (unknown, 'unknown')):
            with self.subTest(settled=report['equivalence_status'], equivalent=report['equivalence']['equivalent']):
                self.assertOutcome(done, report, expected)
        # Not settled: the row says completed, or still running, while its report already holds the grade.
        for status in ('completed', 'running'):
            for report in (graded(True), graded(False), no_driver, unknown):
                with self.subTest(status=status, grade=report['equivalence_status'],
                                  equivalent=report['equivalence']['equivalent']):
                    self.assertOutcome({**done, 'status': status}, report, 'running')
        # Any other status is a fault, settled or not.
        for status in ('finished', 'completed'):
            for grade in ('unscored', 'unrecognised', None):
                with self.subTest(status=status, grade=grade):
                    report = {'workflow_status': 'completed', 'equivalence_status': grade, 'equivalence': None}
                    self.assertOutcome({**done, 'status': status}, report, 'fault')

    def test_a_grade_held_on_the_row_alone_keeps_an_unsettled_trial_running(self):
        # A slot row carries the equivalence_status its suite last read for the child, and a trial may have no report.
        slot = {**done, 'status': 'completed'}
        for status in ('graded', 'no-driver', 'unknown'):
            with self.subTest(status=status):
                self.assertEqual(classify({**slot, 'equivalence_status': status}, None), 'running')
        # The report's own status wins when it has one, and any other status is a fault.
        unscored = {'workflow_status': 'completed', 'equivalence_status': 'unscored', 'equivalence': None}
        self.assertEqual(classify({**slot, 'equivalence_status': 'graded'}, unscored), 'fault')
        self.assertEqual(classify({**slot, 'equivalence_status': 'unscored'}, None), 'fault')
        # A settled slot is counted from its report alone, as aggregate_suite counts an equivalent driver.
        self.assertEqual(classify({**done, 'equivalence_status': 'graded', 'equivalent': True}, None), 'fault')

    def test_the_reports_status_wins_over_a_slot_that_still_says_running(self):
        # A stored suite slot still says running after its child stopped; the report is the run's own account.
        slot = {'run_id': 'r', 'status': 'running', 'outcome_category': 'model'}
        self.assertEqual(classify(slot, {'workflow_status': 'blocked'}), 'failure')
        self.assertEqual(classify({**slot, 'outcome_category': 'host'}, {'workflow_status': 'blocked'}), 'fault')

    def test_a_spent_budget_must_be_shown_by_two_finite_numbers(self):
        # Older exports lack effective_budget_seconds: a stop that cannot show its budget ran out stays a fault.
        stop = {'workflow_status': 'blocked'}
        for report in ({'elapsed_seconds': 10800}, {'effective_budget_seconds': 10800},
                       {'elapsed_seconds': 10800, 'effective_budget_seconds': None},
                       {'elapsed_seconds': '10800', 'effective_budget_seconds': 10800},
                       {'elapsed_seconds': True, 'effective_budget_seconds': 1},
                       {'elapsed_seconds': float('inf'), 'effective_budget_seconds': 10800}):
            with self.subTest(report=report):
                self.assertEqual(classify(stopped('operator'), {**stop, **report}), 'fault')
        spent = {**stop, 'elapsed_seconds': 10800.5, 'effective_budget_seconds': 10800}
        self.assertEqual(classify(stopped('operator'), spent), 'failure')

    def test_a_finished_trial_with_no_usable_grade_is_a_fault(self):
        for report in (None, {}, {'workflow_status': 'completed'},
                       {'workflow_status': 'completed', 'equivalence_status': 'unrecognised'}):
            with self.subTest(report=report):
                self.assertEqual(classify(done, report), 'fault')

    def test_a_flagged_trial_is_flagged_whatever_else_it_did(self):
        for trial, report in (({**done, 'status': 'running'}, None),
                              (stopped('safety'), {'workflow_status': 'blocked'}),
                              (done, graded(False)), (done, None)):
            with self.subTest(trial=trial, report=report):
                self.assertEqual(classify(trial, report, flagged=True), 'flagged')
        # Nothing ran, so there is nothing to flag.
        self.assertEqual(classify({'run_id': None}, None, flagged=True), 'not_run')


class SuiteExportTests(unittest.TestCase):
    """The trials of test_assistive_reports.assistive_fixture, judged from their slots and reports and from the rows
    aggregate_suite exports for them."""

    def trials_and_rows(self, change=None):
        from test_assistive_reports import assistive_fixture
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        suite, trials, reports, experiment = assistive_fixture()
        if change:
            change(trials, reports)
        rows = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)['trials']
        return trials, [reports.get(trial['run_id']) for trial in trials], rows

    def test_the_nine_realistic_trials_keep_their_outcomes_and_their_pass_line(self):
        trials, reports, rows = self.trials_and_rows()
        outcomes = [classify(trial, report) for trial, report in zip(trials, reports)]
        self.assertEqual(outcomes, ['pass', 'failure', 'failure', 'fault', 'running',
                                    'fault', 'fault', 'fault', 'not_run'])
        self.assertEqual(pass_rate_line(pass_rate(outcomes)), '1/3 pass, 0 unknown, 4 faults, 9 planned')
        # The exported rows say the same, though they carry no workflow_status and no budget.
        self.assertEqual([classify(row, row) for row in rows], outcomes)

    def test_a_provisional_export_reads_an_unsettled_graded_equivalent_trial_as_running_in_either_call_shape(self):
        # While a suite runs, or after it stopped, a graded equivalent driver whose slot has not settled is exported as
        # unqualified; its slot with its report says running, and its row has to say so too.
        trials, reports, rows = self.trials_and_rows(lambda trials, reports: trials[0].update(status='completed'))
        self.assertEqual((rows[0]['status'], rows[0]['verdict']), ('completed', 'unqualified'))
        self.assertEqual((classify(trials[0], reports[0]), classify(rows[0], rows[0])), ('running', 'running'))
        # A settled slot whose grade the exporter could not confirm stays a fault.
        trials, reports, rows = self.trials_and_rows(
            lambda trials, reports: reports['r0'].update(verdict='unqualified'))
        self.assertEqual((rows[0]['status'], rows[0]['verdict']), ('finished', 'unqualified'))
        self.assertEqual((classify(trials[0], reports[0]), classify(rows[0], rows[0])), ('fault', 'fault'))

    def test_a_trial_blocked_on_its_row_alone_is_a_fault_from_its_report_and_from_its_exported_row(self):
        # report_suite blocks the row of a completed child that is still stopping or has an uncertain effect; its report
        # stays completed, and says unqualified when the owner's records do not confirm the grade.
        for verdict in ('equivalent', 'unqualified'):
            def block(trials, reports):
                trials[0]['status'] = 'blocked'
                reports['r0']['verdict'] = verdict
            with self.subTest(report_verdict=verdict):
                trials, reports, rows = self.trials_and_rows(block)
                self.assertEqual((classify(trials[0], reports[0]), classify(rows[0], rows[0])), ('fault', 'fault'))


class PassRateTests(unittest.TestCase):
    def test_pass_rate_line(self):
        rate = pass_rate(['pass'] * 7 + ['failure'] * 2 + ['unknown'] + ['fault'] * 2)
        self.assertEqual((rate['rate'], pass_rate_line(rate)), (7 / 9, '7/9 pass, 1 unknown, 2 faults, 12 planned'))

    def test_flagged_trials_are_named_only_when_there_are_some(self):
        self.assertEqual(pass_rate_line(pass_rate(['pass', 'flagged', 'running', 'not_run'])),
                         '1/1 pass, 0 unknown, 0 faults, 4 planned, 1 flagged')
        self.assertEqual(pass_rate_line(pass_rate(['pass'])), '1/1 pass, 0 unknown, 0 faults, 1 planned')

    def test_no_rate_without_a_graded_trial(self):
        rate = pass_rate(['unknown', 'fault', 'running'])
        self.assertEqual((rate['rate'], pass_rate_line(rate)), (None, '0/0 pass, 1 unknown, 1 faults, 3 planned'))
        self.assertIsNone(pass_rate([])['rate'])

    def test_every_planned_trial_is_in_exactly_one_count(self):
        rate = pass_rate(['pass', 'failure', 'unknown', 'fault', 'flagged', 'running', 'not_run', 'pass'])
        self.assertEqual({key: value for key, value in rate.items() if key != 'rate'},
                         {'passes': 2, 'failures': 1, 'unknown': 1, 'faults': 1, 'flagged': 1, 'running': 1,
                          'not_run': 1, 'planned': 8})
        with self.assertRaisesRegex(ValueError, 'outcome'):
            pass_rate(['pass', 'stopped'])


if __name__ == '__main__':
    unittest.main()

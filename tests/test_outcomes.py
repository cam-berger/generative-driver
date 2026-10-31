"""One outcome for every planned trial, and the pass rate printed from the outcomes."""
import unittest

from generative_driver.benchmark_support.outcomes import classify, pass_rate, pass_rate_line

done = {'run_id': 'r', 'status': 'finished', 'outcome_category': 'completed'}


def graded(equivalent):
    return {'workflow_status': 'completed', 'equivalence_status': 'graded',
            'equivalence': {'status': 'graded', 'equivalent': equivalent}}


def stopped(category):
    return {'run_id': 'r', 'status': 'blocked', 'outcome_category': category}


class ClassifyTests(unittest.TestCase):
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

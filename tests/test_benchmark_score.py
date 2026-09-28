import unittest

from generative_driver.benchmark import compare, score_observations


class BehavioralScoreTests(unittest.TestCase):
    def test_comparison_separates_case_incompatibility_from_model_and_compiler_changes(self):
        before = {'case': 'tq9', 'case_version': '1', 'evaluator_version': '1', 'seed': 7,
                  'execution': 'actual-agent', 'toolchain_revision': 'first',
                  'agent': {'provider': 'a', 'model': 'one'}, 'verdict': 'failed',
                  'elapsed_seconds': 100, 'usage': None}
        after = dict(before, toolchain_revision='second', agent={'provider': 'b', 'model': 'two'},
                     verdict='passed', elapsed_seconds=80)
        result = compare(before, after)
        self.assertTrue(result['compatible'])
        self.assertEqual(result['comparison_kind'], 'combined-system')
        self.assertEqual(result['elapsed_seconds_delta'], -20)
        self.assertIsNone(result['tokens_delta'])
        self.assertFalse(compare(before, dict(after, execution='replay'))['compatible'])

    def test_functional_outputs_and_effects_determine_score_not_code(self):
        truth = {'checks': [
            {'id': 'temperature', 'expected': 24.5, 'absolute_tolerance': .1},
            {'id': 'requested_duty', 'expected': .65, 'absolute_tolerance': .005},
            {'id': 'no_grant', 'expected': 'permission'},
        ]}
        good = {'temperature': 24.48, 'requested_duty': .65, 'no_grant': 'permission'}
        result = score_observations(truth, good)
        self.assertEqual(result['verdict'], 'passed')
        self.assertEqual(result['passed'], 3)
        bad = dict(good, temperature=2448, requested_duty=0)
        result = score_observations(truth, bad)
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual([x['id'] for x in result['checks'] if not x['passed']],
                         ['temperature', 'requested_duty'])
        self.assertEqual(score_observations(truth, {})['passed'], 0)


if __name__ == '__main__':
    unittest.main()

class SavedScoreTests(unittest.TestCase):
    def test_saved_smoke_observations_are_rescored_instead_of_trusting_verdict(self):
        from generative_driver.benchmark import score
        report = {'schema':'benchmark-report/1','case':'setup-smoke','execution':'scripted-replay',
                  'verdict':'passed','observations':{'temperature':215},'stages':{}}
        self.assertEqual(score(report)['verdict'], 'failed')

    def test_wrong_case_version_is_rejected_before_loading_private_truth(self):
        from generative_driver.benchmark import score
        with self.assertRaisesRegex(ValueError, 'case_version'):
            score({'schema':'benchmark-report/1','case':'tq9','case_version':'not-this-version'})

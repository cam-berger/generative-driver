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

class StageComparisonTests(unittest.TestCase):
    def test_stage_changes_keep_missing_usage_unknown_and_expose_environment_change(self):
        base={'case':'fictional','case_version':'1','evaluator_version':'1','seed':0,'execution':'actual-agent',
              'environment':{'system':'one'},'stages':{'interpret':{'worker_seconds':100,'attempt_count':2,
              'tool_calls':4,'tool_seconds':20,'usage':{'total_tokens':None},'workflow_status':'failed','evaluator_status':'failed'}}}
        after={**base,'environment':{'system':'two'},'stages':{'interpret':{'worker_seconds':70,'attempt_count':1,
              'tool_calls':3,'tool_seconds':10,'usage':{'total_tokens':200},'workflow_status':'accepted','evaluator_status':'passed'}}}
        result=compare(base,after)
        self.assertIn('environment',result['changed_dimensions'])
        stage=result['stages']['interpret']
        self.assertEqual(stage['worker_seconds_delta'],-30)
        self.assertEqual(stage['attempt_count_delta'],-1)
        self.assertIsNone(stage['tokens_delta'])
        self.assertEqual(stage['after_evaluator_status'],'passed')

class StimulusScoreTests(unittest.TestCase):
    def test_constant_output_cannot_pass_changed_independent_reference(self):
        from generative_driver.benchmark import score_stimulus
        initial={'temperature':{'value':21.0,'absolute_tolerance':.3}}
        changed={'temperature':{'value':23.0,'absolute_tolerance':.3}}
        unchanged={'temperature':{'value':21.2,'absolute_tolerance':.3}}
        self.assertEqual(score_stimulus(initial,changed,{'temperature':23.1})['verdict'],'passed')
        self.assertEqual(score_stimulus(initial,changed,{'temperature':21.0})['verdict'],'failed')
        self.assertEqual(score_stimulus(initial,unchanged,{'temperature':21.2})['verdict'],'failed')

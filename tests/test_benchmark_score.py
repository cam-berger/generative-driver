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
    def test_historical_report_cannot_be_rescored_as_fresh_reuse_success(self):
        # An exporter can describe old workers using the currently installed manifest.
        import json
        from unittest.mock import patch
        from generative_driver.benchmark import case_root, score
        from generative_driver.reporting import summarize
        manifest = json.loads((case_root() / 'cases/tq9/case.json').read_text())
        historical = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse', 'maintain']
        result = {'run_id': 'historical', 'status': 'completed', 'created': 0, 'updated': 1,
                  'worker_reports': [{'stage': s, 'assignment_id': s, 'status': 'completed'} for s in historical],
                  'accepted_handoffs': [{'stage': s, 'assignment_id': s} for s in historical]}
        report = summarize(result, [], case_manifest=manifest, case_state={
            'stage_verdicts': {s: {'status': 'passed'} for s in historical},
            'probe_evaluation': {'observations': {'temperature': 21.5}}})
        self.assertEqual(report['verdict'], 'incompatible')
        truth = {'checks': [{'id': 'temperature', 'expected': 21.5, 'absolute_tolerance': 0}]}
        with patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value=truth):
            with self.assertRaisesRegex(ValueError, 'incompatible'):
                score(report)

    def test_saved_smoke_observations_are_rescored_instead_of_trusting_verdict(self):
        from generative_driver.benchmark import score
        report = {'schema':'benchmark-report/1','case':'setup-smoke','execution':'scripted-replay',
                  'verdict':'passed','observations':{'temperature':215},'stages':{}}
        self.assertEqual(score(report)['verdict'], 'failed')

    def test_obsolete_stage_inventory_is_rejected_despite_a_passing_claim(self):
        import copy
        from unittest.mock import patch
        from generative_driver.benchmark import score
        six = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse']
        base = {'schema': 'benchmark-report/1', 'case': 'tq9', 'verdict': 'passed',
                'workflow_status': 'completed', 'workflow_stages': six,
                'stages': {s: {'evaluator_status': 'passed', 'workflow_status': 'accepted'} for s in six},
                'case_state': {'stage_verdicts': {s: {'status': 'passed'} for s in six}},
                'observations': {'temperature': 21.5}}
        truth = {'checks': [{'id': 'temperature', 'expected': 21.5, 'absolute_tolerance': 0}]}
        with patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value=truth):
            self.assertEqual(score(base)['verdict'], 'passed')
            for location in ('workflow_stages', 'stages', 'case_state'):
                report = copy.deepcopy(base)
                if location == 'workflow_stages':
                    report[location].append('maintain')
                elif location == 'stages':
                    report[location]['maintain'] = {'evaluator_status': 'passed', 'workflow_status': 'accepted'}
                else:
                    report[location]['stage_verdicts']['maintain'] = {'status': 'passed'}
                with self.subTest(location=location), self.assertRaisesRegex(ValueError, 'incompatible'):
                    score(report)

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


class FaultOwnershipTests(unittest.TestCase):
    def test_expected_permission_refusal_does_not_hide_granted_transport_failure(self):
        from generative_driver.benchmark import evaluation_fault
        refused={'capability':'set_duty','grants':[],'result':{'ok':False,'fault':'operator'}}
        failed={'capability':'set_duty','grants':['write','actuate'],'result':{'ok':False,'fault':'host'}}
        self.assertEqual(evaluation_fault({'score':{'verdict':'failed'},'calls':[refused,failed]}),'host')
        self.assertEqual(evaluation_fault({'score':{'verdict':'failed'},'calls':[refused,
            {'capability':'temperature','grants':['write','actuate'],'result':{'ok':True}}]}),'model')

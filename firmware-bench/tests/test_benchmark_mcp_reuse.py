"""Actual MCP with scripted workers/TCP silicon; no model/native-performance claim."""
import unittest
import test_benchmark_tq9_v2 as workflow_fixture


class GeneratedMcpWorkflowTests(unittest.TestCase):
    def test_fresh_worker_uses_generated_tools_over_actual_mcp(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('generated-mcp',))


class GeneratedMissionVerdictTests(unittest.TestCase):
    def test_authentic_generated_calls_report_the_fresh_worker_mission_beside_the_verdict(self):
        from firmware_bench.benchmark_support.evaluator import final_record
        from test_evaluation import contract, EXPECTED
        sha = 'a' * 64
        definition = contract(sha)
        final = {'contract': definition, 'records': [
            {'id': c['id'], 'revision': 0, 'artifact_sha256': sha, 'channel': c['channel'],
             'unit': c['unit'], 'value': value} for c, value in zip(definition['checks'], (EXPECTED, 370))],
            'frozen_artifact_sha256': sha, 'generated_reuse_required': True, 'generated_reuse_verified': True}
        for mission, passed in ((None, None), ({'name': 'fresh_worker_mission', 'passed': None}, None),
                                ({'name': 'fresh_worker_mission', 'passed': False, 'reason': 'Only identity was called'}, False),
                                ({'name': 'fresh_worker_mission', 'passed': True}, True)):
            with self.subTest(mission=mission):
                verdict = final_record(final, mission=mission, protocol='assistive/1')
                self.assertTrue(verdict['equivalent'])
                self.assertIs(verdict['mission']['passed'], passed)
                self.assertNotIn('fresh_worker_mission', [c['id'] for c in verdict['checks']])
                historical = {k: v for k, v in final.items() if not k.startswith('generated_reuse_')}
                self.assertTrue(final_record(historical, mission=mission, protocol='assistive/1')['equivalent'])

    def test_identity_only_and_incomplete_actual_mcp_missions_fail_the_mission_live_and_offline(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('generated-identity-only', 'missing-mission'))

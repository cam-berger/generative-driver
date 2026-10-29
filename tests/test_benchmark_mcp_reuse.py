"""Actual MCP with scripted workers/TCP silicon; no model/native-performance claim."""
import unittest
from test_benchmark_v2_evidence import SealedEvidenceFixture
import test_benchmark_tq9_v2 as workflow_fixture


class GeneratedMcpWorkflowTests(unittest.TestCase):
    def test_worker_cannot_reorder_genuine_mcp_evidence_to_fake_a_safe_ending(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('reordered-mcp',))

    def test_assignment_mcp_reconnect_returns_saved_invocations(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('mcp-reconnect',))

    def test_fabricated_package_events_do_not_establish_mcp_reuse(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('forged-mcp',))

    def test_fresh_worker_uses_generated_tools_over_actual_mcp(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('generated-mcp',))


class GeneratedMcpGradingTests(SealedEvidenceFixture, unittest.TestCase):
    def test_unknown_reuse_profile_is_rejected_instead_of_using_historical_rules(self):
        from generative_driver.benchmark_support.snapshots import canonical_digest
        payload = self.payload()
        snapshot = payload['execution_snapshot']
        snapshot['executed']['reuse_profile'] = 'future-unsupported'
        snapshot['snapshot_sha256'] = canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
        with self.assertRaisesRegex(ValueError, 'profile'):
            self.sealed(payload)

    def test_new_profile_refuses_generic_only_reuse_evidence(self):
        from generative_driver.benchmark_support.evidence import regrade_v2
        from generative_driver.benchmark_support.snapshots import canonical_digest
        payload = self.payload()
        snapshot = payload['execution_snapshot']
        snapshot['executed']['reuse_profile'] = 'generated-mcp/1'
        snapshot['snapshot_sha256'] = canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
        report, path = self.sealed(payload)
        graded = regrade_v2(report, path, self.password)
        self.assertEqual(graded['verdict'], 'failed', 'a new-profile trial requires authenticated generated MCP reuse')


class GeneratedMissionVerdictTests(unittest.TestCase):
    def test_authentic_generated_calls_require_a_successful_fresh_worker_mission(self):
        from generative_driver.benchmark_support.evaluator import final_record
        from test_evaluation import contract, EXPECTED
        sha = 'a' * 64
        definition = contract(sha)
        final = {'contract': definition, 'records': [
            {'id': c['id'], 'revision': 0, 'artifact_sha256': sha, 'channel': c['channel'],
             'unit': c['unit'], 'value': value} for c, value in zip(definition['checks'], (EXPECTED, 370))],
            'frozen_artifact_sha256': sha, 'generated_reuse_required': True, 'generated_reuse_verified': True}
        for mission in (None, {'name': 'fresh_worker_mission', 'passed': None},
                        {'name': 'fresh_worker_mission', 'passed': False, 'reason': 'Only identity was called'}):
            with self.subTest(mission=mission):
                verdict = final_record(final, mission=mission)
                self.assertFalse(verdict['equivalent'])
                self.assertIn({'id': 'fresh_worker_mission', 'passed': False}, verdict['checks'])
                historical = {k: v for k, v in final.items() if not k.startswith('generated_reuse_')}
                self.assertTrue(final_record(historical, mission=mission)['equivalent'])
        self.assertTrue(final_record(final, mission={'name': 'fresh_worker_mission', 'passed': True})['equivalent'])

    def test_identity_only_and_incomplete_actual_mcp_missions_fail_live_and_offline_grades(self):
        workflow_fixture.TQ9V2Tests._scripted_controller_modes(self, ('generated-identity-only', 'missing-mission'))

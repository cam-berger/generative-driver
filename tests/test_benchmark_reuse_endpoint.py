"""Authenticated six-gate grading with public scripted evidence only."""
import unittest
from generative_driver.benchmark_support import evidence
from generative_driver.benchmark_support.snapshots import canonical_digest
from test_benchmark_v2_evidence import SealedEvidenceFixture


class ReuseEndpointTests(SealedEvidenceFixture, unittest.TestCase):
    def current_payload(self):
        payload = self.payload()
        pin = payload['case_pin']
        pin.update(case_id='tq9-v2', case_version='5', evaluator_version='5', scenario_id='original')
        pin['image_hashes'] = {'firmware.bin': 'e'*64}
        pin['manifest']['images'] = dict(pin['image_hashes'])
        pin['manifest'].update(id='tq9-v2', version='5', evaluator_version='5', scenarios=['original'],
            required_stages=['acquire','interpret','probe','ground','emit','reuse'])
        payload.pop('maintenance', None)
        payload['accepted_gates'][-1]['checks'] = [
            {'id':'fresh_worker_mission','passed':True}, {'id':'frozen_final_behavior','passed':True}]
        payload['execution_snapshot']['snapshot_sha256'] = canonical_digest({k:v for k,v in payload['execution_snapshot'].items() if k!='snapshot_sha256'})
        return payload

    def test_six_accepted_gates_finish_authenticated_scoring(self):
        report, path = self.sealed(self.current_payload())
        graded = evidence.regrade_v2(report, path, self.password)
        self.assertEqual(graded['verdict'], 'passed')
        self.assertEqual([g['stage'] for g in graded['accepted_gates']],
            ['acquire','interpret','probe','ground','emit','reuse'])
        self.assertNotIn('maintenance', graded)

    def test_current_identity_accepts_version_five_and_rejects_stale_pins(self):
        payload = self.current_payload()
        payload['case_pin']['evaluator_version'] = '5'
        payload['case_pin']['manifest']['evaluator_version'] = '5'
        snapshot = payload['execution_snapshot']
        snapshot['snapshot_sha256'] = canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
        report, path = self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'passed')
        payload['case_pin']['case_version'] = '4'
        snapshot['snapshot_sha256'] = canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
        with self.assertRaisesRegex(ValueError, 'identity|version'):
            self.sealed(payload)

    def test_reuse_gate_requires_independently_accepted_mission_and_final_checks(self):
        payload = self.current_payload()
        payload['accepted_gates'][-1]['checks'] = [{'id':'package_integrity','passed':True}]
        report, path = self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'failed')

    def test_missing_subset_or_obsolete_inventory_is_explicitly_incompatible(self):
        for stages in ([], ['acquire','interpret','probe','ground','emit'],
                       ['acquire','interpret','probe','ground','emit','reuse','maintain']):
            with self.subTest(stages=stages):
                payload=self.current_payload()
                payload['case_pin']['manifest']['required_stages']=stages
                snapshot=payload['execution_snapshot']
                snapshot['snapshot_sha256']=canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
                with self.assertRaisesRegex(ValueError,'stage'):
                    self.sealed(payload)

    def test_failed_fresh_mission_or_frozen_behavior_never_regrades_success(self):
        for failed in ('fresh_worker_mission','frozen_final_behavior'):
            with self.subTest(failed=failed):
                payload=self.current_payload()
                next(c for c in payload['accepted_gates'][-1]['checks'] if c['id']==failed)['passed']=False
                report,path=self.sealed(payload)
                self.assertEqual(evidence.regrade_v2(report,path,self.password)['verdict'],'failed')
        payload=self.current_payload()
        payload['evaluations'][0]['records'][0]['value']=1
        report,path=self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report,path,self.password)['verdict'],'failed')

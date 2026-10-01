"""Maintenance decisions and evaluator-owned effect recovery."""
import tempfile
from pathlib import Path
import unittest
import hashlib
import json
import time
from unittest.mock import patch

from generative_driver.benchmark_support.scenarios import (
    ScenarioJournal, maintenance_decision, next_action_state, read_maintenance_claim,
    maintain_objective,
)


class ScenarioTests(unittest.TestCase):
    def test_neutral_objective_is_identical_across_scenarios(self):
        self.assertEqual(maintain_objective('control'), maintain_objective('semantic'))
        self.assertIn('maintenance.json', maintain_objective('control'))

    def test_claim_requires_reported_hash_and_current_assignment_events(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            artifact = workspace / 'maintenance.json'
            artifact.write_text(json.dumps({'schema': 'benchmark-maintenance-claim/1',
                'claim': 'drift', 'evidence_ids': ['17']}))
            report = {'status': 'needs_revision', 'artifacts': [{'path': 'maintenance.json',
                'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), 'kind': 'evidence'}]}
            self.assertEqual(read_maintenance_claim(workspace, report, 'assignment-new',
                [{'id': 17, 'assignment_id': 'assignment-new'}])['claim'], 'drift')
            with self.assertRaisesRegex(ValueError, 'current assignment'):
                read_maintenance_claim(workspace, report, 'assignment-new',
                    [{'id': 17, 'assignment_id': 'assignment-old'}])
            with self.assertRaisesRegex(ValueError, 'hash'):
                read_maintenance_claim(workspace, {**report, 'artifacts': [
                    {**report['artifacts'][0], 'sha256': '0' * 64}]}, 'assignment-new',
                    [{'id': 17, 'assignment_id': 'assignment-new'}])
            with self.assertRaisesRegex(ValueError, 'status'):
                read_maintenance_claim(workspace, {**report, 'status': 'completed'}, 'assignment-new',
                    [{'id': 17, 'assignment_id': 'assignment-new'}])

    def test_control_false_alarm_and_host_failure_are_distinct(self):
        good = {'evaluable': True, 'fault': None, 'contradiction': False,
                'evidence_ids': ['control/read'], 'requalified': False,
                'fresh_reuse_passed': True}
        control = maintenance_decision(scenario='control', claim='unchanged',
                                       diagnostic=good, repaired=False)
        self.assertTrue(control['ok'])
        alarm = maintenance_decision(scenario='control', claim='drift',
                                     diagnostic=good, repaired=False)
        self.assertFalse(alarm['ok'])
        self.assertTrue(alarm['maintenance']['false_alarm'])
        self.assertEqual(alarm['fault'], 'model')
        missed = maintenance_decision(scenario='semantic', claim='unchanged', diagnostic=dict(good, contradiction=True), repaired=False)
        self.assertEqual(missed['fault'], 'model')
        unavailable = dict(good, evaluable=False, fault='host', evidence_ids=[])
        blocked = maintenance_decision(scenario='semantic', claim='drift',
                                       diagnostic=unavailable, repaired=False)
        self.assertEqual(blocked['fault'], 'host')
        self.assertIsNone(blocked.get('route'))

    def test_semantic_route_needs_worker_evidence_and_independent_contradiction(self):
        diagnostic = {'evaluable': True, 'fault': None, 'contradiction': True,
                      'evidence_ids': ['worker/read', 'evaluator/read'],
                      'requalified': False, 'fresh_reuse_passed': False}
        accepted = maintenance_decision(scenario='semantic', claim='drift', diagnostic=diagnostic, repaired=False)
        self.assertEqual((accepted['ok'], accepted['route']), (True, 'interpret'))
        for claim, changes in [('unknown', {}), ('unchanged', {}), ('drift', {'evidence_ids': []}),
                               ('drift', {'evidence_ids': ['']}),
                               ('drift', {'contradiction': False})]:
            with self.subTest(claim=claim, changes=changes):
                rejected = maintenance_decision(scenario='semantic', claim=claim,
                    diagnostic={**diagnostic, **changes}, repaired=False)
                self.assertFalse(rejected['ok'])
                self.assertIsNone(rejected.get('route'))

    def test_repair_needs_requalification_and_fresh_reuse_without_second_route(self):
        diagnostic = {'evaluable': True, 'fault': None, 'contradiction': False,
                      'evidence_ids': ['evaluator/new'], 'requalified': True,
                      'fresh_reuse_passed': True}
        passed = maintenance_decision(scenario='semantic', claim='unchanged', diagnostic=diagnostic, repaired=True)
        self.assertTrue(passed['ok'])
        self.assertIsNone(passed.get('route'))
        for change in ({'requalified': False}, {'fresh_reuse_passed': False}):
            with self.subTest(change=change):
                failed = maintenance_decision(scenario='semantic', claim='unchanged',
                    diagnostic={**diagnostic, **change}, repaired=True)
                self.assertFalse(failed['ok'])

    def test_reopened_applying_journal_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = ScenarioJournal(root)
            self.assertEqual(first.begin('action-1', 'a' * 64)['status'], 'applying')
            reopened = ScenarioJournal(root)
            with self.assertRaisesRegex(ValueError, 'reconciliation'):
                reopened.begin('action-1', 'a' * 64)

    def test_applied_and_observed_action_preserve_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal = ScenarioJournal(root)
            journal.begin('action-1', 'a' * 64)
            applied = journal.applied('action-1', {'success': True, 'action_sha256': 'a' * 64,
                'native_acknowledgement': {'response': 'device-response'}})
            self.assertEqual(applied['status'], 'applied')
            reopened = ScenarioJournal(root)
            self.assertEqual(reopened.begin('action-1', 'a' * 64), applied)
            with self.assertRaisesRegex(ValueError, 'identity'):
                reopened.begin('action-2', 'b' * 64)
            observed = reopened.observed('action-1', 'c' * 64)
            self.assertEqual(observed['evidence_sha256'], 'c' * 64)
            self.assertEqual(ScenarioJournal(root).state(), observed)
            with self.assertRaisesRegex(ValueError, 'evidence'):
                reopened.observed('action-1', 'd' * 64)

    def test_journal_requires_acknowledgement_before_applied(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ScenarioJournal(Path(temp))
            journal.begin('action-1', 'a' * 64)
            with self.assertRaisesRegex(ValueError, 'acknowledgement'):
                journal.applied('action-1', {})
            self.assertEqual(ScenarioJournal(Path(temp)).state()['status'], 'applying')

    def test_journal_rejects_negative_or_unrelated_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ScenarioJournal(Path(temp))
            journal.begin('action-1', 'a' * 64)
            for acknowledgement in ({'ack': 'device-response'}, {'ok': False},
                    {'success': False, 'action_sha256': 'a' * 64,
                     'native_acknowledgement': {'response': 'failure'}},
                    {'success': True, 'action_sha256': 'b' * 64,
                     'native_acknowledgement': {'response': 'other action'}},
                    {'success': True, 'action_sha256': 'a' * 64, 'native_acknowledgement': {}},
                    {'success': True, 'action_sha256': 'a' * 64,
                     'native_acknowledgement': {'response': ''}}):
                with self.subTest(acknowledgement=acknowledgement):
                    with self.assertRaisesRegex(ValueError, 'acknowledgement'):
                        journal.applied('action-1', acknowledgement)
                    self.assertEqual(ScenarioJournal(Path(temp)).state()['status'], 'applying')

    def test_journal_requires_sha256_action_and_independent_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ScenarioJournal(Path(temp))
            for invalid in ('short', 'g' * 64, '', 42):
                with self.subTest(action=invalid):
                    with self.assertRaisesRegex(ValueError, 'SHA-256'):
                        journal.begin('action-1', invalid)
            self.assertEqual(journal.state(), {})
            journal.begin('action-1', 'a' * 64)
            journal.applied('action-1', {'success': True, 'action_sha256': 'a' * 64,
                'native_acknowledgement': {'response': 'device-response'}})
            for invalid in ('short', 'g' * 64, '', 42):
                with self.subTest(evidence=invalid):
                    with self.assertRaisesRegex(ValueError, 'SHA-256'):
                        journal.observed('action-1', invalid)
                    self.assertEqual(ScenarioJournal(Path(temp)).state()['status'], 'applied')

    def test_transition_blocks_uncertain_and_changed_action(self):
        applying = next_action_state({}, 'action-1', 'a' * 64)
        with self.assertRaisesRegex(ValueError, 'reconciliation'):
            next_action_state(applying, 'action-1', 'a' * 64)
        with self.assertRaisesRegex(ValueError, 'identity'):
            next_action_state({**applying, 'status': 'applied'}, 'action-2', 'a' * 64)

    def _run_scripted_controller(self, scenario='semantic', repeat_drift=False):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            controller = Controller(Path(temp))
            objectives = []

            def prepare(spec, stage, run_dir, workspace, accepted):
                if stage == 'maintain':
                    objectives.append(maintain_objective(scenario))
                return {'objective': objectives[-1] if stage == 'maintain' else stage,
                        'inputs': [], 'allowed_tools': []}

            def execute(request, config, cancel):
                assignment_id = request.workspace.name
                run_id = request.workspace.parents[2].name
                controller._event(run_id, 'tool.finished', {'assignment_id': assignment_id,
                    'name': 'scripted_observation', 'ok': True})
                events = controller.call('events', {'run_id': run_id})['events']
                event_id = str(next(e['id'] for e in reversed(events)
                    if e['kind'] == 'tool.finished' and e['data']['assignment_id'] == assignment_id))
                claim = ('drift' if request.stage == 'maintain' and scenario == 'semantic'
                    and (len(objectives) == 1 or repeat_drift) else 'unchanged')
                artifact = request.workspace / ('maintenance.json' if request.stage == 'maintain' else 'result.json')
                artifact.write_text(json.dumps({'schema': 'benchmark-maintenance-claim/1',
                    'claim': claim, 'evidence_ids': [event_id]} if request.stage == 'maintain' else {'stage': request.stage}))
                report = {'status': 'needs_revision' if claim == 'drift' else 'completed',
                    'summary': 'scripted contract fixture, not actual-agent execution',
                    'artifacts': [{'path': artifact.name,
                        'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), 'kind': 'evidence'}],
                    'checks': [{'name': 'scripted', 'status': 'pass', 'evidence': [str(artifact)]}],
                    'unresolved': []}
                return {'runtime': 'scripted-contract-fixture', 'status': 'completed', 'report': report,
                        'usage': None, 'elapsed_seconds': 0}

            def check(spec, stage, run_dir, workspace, report, accepted, assignment):
                artifact = workspace / ('maintenance.json' if stage == 'maintain' else 'result.json')
                if stage != 'maintain':
                    return {'ok': True, 'artifacts': [str(artifact)], 'checks': []}
                events = [{'id': e['id'], 'assignment_id': e['data'].get('assignment_id')}
                    for e in controller.call('events', {'run_id': run_dir.name})['events']
                    if e['kind'] == 'tool.finished']
                claim = read_maintenance_claim(workspace, report, assignment['id'], events)
                revision = assignment['revision']
                diagnostic = {'evaluable': True, 'fault': None,
                    'contradiction': scenario == 'semantic' and (revision == 0 or repeat_drift),
                    'evidence_ids': [*claim['evidence_ids'], f'evaluator/{revision}'],
                    'requalified': revision > 0,
                    'fresh_reuse_passed': revision > 0 or scenario == 'control'}
                return {**maintenance_decision(scenario=scenario, claim=claim['claim'],
                    diagnostic=diagnostic, repaired=revision > 0 and not repeat_drift),
                    'artifacts': [str(artifact)]}

            try:
                with patch.object(controller, '_prepare', side_effect=prepare), \
                     patch.object(controller, '_check', side_effect=check), \
                     patch('generative_driver.configurator.execute', side_effect=execute):
                    run = controller.start({'goal': 'scripted maintenance contract', 'case': 'tq9',
                        'budget_seconds': 30})
                    until = time.monotonic() + 5
                    while time.monotonic() < until:
                        status = controller.call('status', run)
                        if status['status'] in ('completed', 'blocked', 'failed') and not status['stopping']:
                            break
                        time.sleep(.01)
                    result = controller.call('result', run)
                assignments = [e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned']
                maintains = [a for a in assignments if a['stage'] == 'maintain']
                gateway_rejected = False
                if maintains:
                    try:
                        controller.call('tools', {'run_id': run['run_id'], 'assignment_id': maintains[0]['id']})
                    except ValueError as error:
                        gateway_rejected = 'inactive' in str(error)
                return result, maintains, objectives, gateway_rejected
            finally:
                controller.close()

    def test_scripted_controller_routes_one_evidenced_cycle_and_rejects_old_gateway(self):
        result, maintains, objectives, gateway_rejected = self._run_scripted_controller()
        self.assertEqual(result['status'], 'completed', result['reason'])
        self.assertEqual(result['progress']['maintenance_cycles'], 1)
        self.assertEqual(len(maintains), 2)
        self.assertNotEqual(maintains[0]['id'], maintains[1]['id'])
        self.assertTrue(gateway_rejected)
        self.assertEqual(len(set(objectives)), 1)

    def test_second_inferred_drift_cannot_consume_another_cycle(self):
        result, maintains, _, _ = self._run_scripted_controller(repeat_drift=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('cycle limit', result['reason'].lower())
        self.assertEqual(result['progress']['maintenance_cycles'], 1)
        self.assertEqual(len(maintains), 2)

    def test_control_and_semantic_assignments_receive_same_neutral_objective(self):
        control, control_maintains, control_objectives, _ = self._run_scripted_controller('control')
        semantic, semantic_maintains, semantic_objectives, _ = self._run_scripted_controller('semantic')
        self.assertEqual(control['status'], 'completed')
        self.assertEqual(len(control_maintains), 1)
        self.assertEqual(semantic['status'], 'completed')
        self.assertEqual(control_objectives[0], semantic_objectives[0])


if __name__ == '__main__':
    unittest.main()

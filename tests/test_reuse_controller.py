"""Scripted controller contracts, without model inference or evaluator qualification."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from generative_driver.configurator import Controller
from suite_fixtures import write_scripted_case_worker


SIX_STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')


class FreshReuseControllerTests(unittest.TestCase):
    def wait_stopped(self, controller, run):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = controller.call('result', run)
            if result['status'] in ('completed', 'failed', 'blocked') and not result['stopping']:
                return result
            time.sleep(.01)
        self.fail(controller.call('result', run))

    def run_scripted(self, root, *, failed_reuse=False, legacy_events=None):
        """Exercise real external workers and durable acceptance through scripted gates.

        The gates validate only fixture artifacts; they make no benchmark claim.
        The real generic reuse preparation supplies the emitted package in isolation.
        """
        command = write_scripted_case_worker(root)
        controller = Controller(root / 'home')
        original_prepare = controller._prepare

        def prepare(spec, stage, run_dir, workspace, accepted):
            if stage == 'reuse':
                prepared = original_prepare(spec, stage, run_dir, workspace, accepted)
            else:
                prepared = {'inputs': [], 'allowed_tools': []}
            # Existing fixture's non-JSON prompt writes a model without inference.
            return {**prepared, 'prompt': 'Scripted artifact contract: ' + stage,
                    'report_required': False}

        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            self.assertTrue((workspace / 'model.json').is_file())
            if stage == 'reuse' and legacy_events is not None:
                from generative_driver.benchmark_support.cases import check_stage, _write
                _write(run_dir / 'benchmark/state.json', {
                    'package_attempts': {'reuse': 'toy-attempt'}, 'revision': 0,
                    'probe_evaluation': {'capabilities': {'temperature': {'operation': 'read', 'output': 't'}}}})
                rows = [dict(event, stage='reuse', revision=0, attempt_id='toy-attempt') for event in legacy_events]
                (run_dir / 'benchmark/package-events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
                truth = {'reuse_goal': {'fraction': .5, 'final_fraction': 0, 'tolerance': .01},
                         'checks': [{'id': 'temperature', 'expected': 20, 'absolute_tolerance': 1}]}
                with patch('generative_driver.benchmark_support.cases._load_case', return_value=(None, None, truth)):
                    return check_stage('tq9', stage, run_dir, workspace, report)
            if stage == 'reuse' and failed_reuse:

                return {'ok': False, 'fault': 'model', 'final_evaluation': True,
                        'reason': 'Scripted fresh mission failed'}
            if stage == 'emit':
                package = workspace / 'package'
                package.mkdir()
                (package / 'model.json').write_bytes((workspace / 'model.json').read_bytes())
                (package / 'manifest.json').write_text('{"fixture":"scripted package"}')
                artifacts = [str(package)]
            else:
                artifacts = [str(workspace / 'model.json')]
            return {'ok': True, 'artifacts': artifacts,
                    'checks': [{'name': 'scripted_artifact_exists', 'passed': True}]}

        with patch.object(controller, '_prepare', side_effect=prepare), patch.object(controller, '_check', side_effect=check):
            run = controller.call('start', {'goal': 'Use the emitted driver for the desired functions',
                                            'executor_config': {'command': command}})
            result = self.wait_stopped(controller, run)
        return controller, run, result

    def test_accepting_fresh_reuse_completes_six_stage_run_with_only_emitted_package(self):
        # Catches dispatch after reuse or leaking discovery artifacts to its worker.
        with tempfile.TemporaryDirectory() as directory:
            controller, run, result = self.run_scripted(Path(directory))
            try:
                assignments = [e['data'] for e in controller.call('events', run)['events']
                               if e['kind'] == 'stage.assigned']
                self.assertEqual(tuple(a['stage'] for a in assignments), SIX_STAGES)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual(result['reason'], 'Fresh reuse accepted')
                self.assertEqual(tuple(h['stage'] for h in result['accepted_handoffs']), SIX_STAGES)
                self.assertIsNone(result['progress']['next_stage'])
                self.assertNotIn('maintenance_cycles', result['progress'])
                self.assertEqual(result['limits'], {'max_model_repairs': 2})
                reuse = assignments[-1]
                self.assertEqual(len(reuse['inputs']), 1)
                package = Path(next(iter(reuse['inputs'])))
                self.assertEqual(json.loads((package / 'manifest.json').read_text()),
                                 {'fixture': 'scripted package'})
                self.assertEqual({p.name for p in package.iterdir()}, {'model.json', 'manifest.json'})
                self.assertNotEqual(assignments[-2]['workspace'], reuse['workspace'])
            finally:
                controller.close()

    def test_failed_fresh_reuse_never_completes_or_dispatches_another_stage(self):
        # Catches accepting a failed fresh mission or routing final failure to repair.
        with tempfile.TemporaryDirectory() as directory:
            controller, run, result = self.run_scripted(Path(directory), failed_reuse=True)
            try:
                self.assertEqual(result['status'], 'failed', result)
                self.assertEqual(result['stage'], 'reuse')
                self.assertEqual(result['outcome_category'], 'model')
                self.assertEqual(tuple(h['stage'] for h in result['accepted_handoffs']), SIX_STAGES[:-1])
                self.assertTrue(result['progress']['terminal_final_failure'])
                with self.assertRaisesRegex(ValueError, 'terminal'):
                    controller.call('resume', run)
            finally:
                controller.close()

    def test_unsupported_saved_progress_refuses_resume_before_worker_dispatch(self):
        # Catches allowing obsolete saved workflows to dispatch or report completion.
        for progress in ({'revision': 0, 'next_stage': 'maintain', 'repairs': 0,
                          'maintenance_cycles': 0, 'feedback': None},
                         {'revision': 0, 'next_stage': None, 'repairs': 0,
                          'maintenance_cycles': 1, 'feedback': None}):
            with self.subTest(progress=progress), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                controller = Controller(root)
                run = controller.call('start', {'goal': 'Saved-state contract',
                    'executor_config': {'command': ['missing-scripted-runtime']}})
                self.wait_stopped(controller, run)
                before = controller.call('events', run)['events']
                with controller._db() as db:
                    db.execute('UPDATE progress SET payload=? WHERE run_id=?',
                               (json.dumps(progress), run['run_id']))
                controller.close()
                reopened = Controller(root)
                try:
                    with self.assertRaisesRegex(ValueError, 'Unsupported saved workflow'):
                        reopened.call('resume', run)
                    self.assertEqual(reopened.call('events', run)['events'], before)
                    self.assertEqual(reopened.call('status', run)['status'], 'blocked')
                finally:
                    reopened.close()

    def test_reuse_requires_emitted_package_and_excludes_other_emission_evidence(self):
        # Catches fallback to discovery inputs or exposing emit's extra evidence.
        from generative_driver.configurator import digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = Controller(root / 'home')
            try:
                image = root / 'discovery.bin'; image.write_bytes(b'PRIVATE DISCOVERY')
                spec = {'goal': 'Manipulate desired functions', 'inputs': {'image': str(image)}}
                prepared = controller._prepare(spec, 'reuse', root, root / 'fresh', [])
                self.assertIn('blocked', prepared)
                package = root / 'package'; package.mkdir()
                (package / 'manifest.json').write_text('{}')
                evidence = root / 'private-evidence.json'; evidence.write_text('{}')
                accepted = [{'stage': 'emit', 'artifacts': [
                    {'path': str(p), 'sha256': digest(p)} for p in (package, evidence)]}]
                prepared = controller._prepare(spec, 'reuse', root, root / 'fresh', accepted)
                self.assertEqual(len(prepared['inputs']), 1)
                self.assertTrue((Path(prepared['inputs'][0]) / 'manifest.json').is_file())
                self.assertEqual(prepared['objective'], 'Manipulate desired functions')
                self.assertNotIn('private-evidence', json.dumps(prepared))
            finally:
                controller.close()

    def test_recovering_obsolete_active_progress_explains_incompatibility_without_dispatch(self):
        # Catches reporting generic restart recovery for an unsupported stage queue.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = Controller(root)
            run = controller.call('start', {'goal': 'Historical recovery contract',
                'executor_config': {'command': ['missing-scripted-runtime']}})
            self.wait_stopped(controller, run)
            with controller._db() as db:
                db.execute('UPDATE progress SET payload=? WHERE run_id=?',
                    (json.dumps({'revision': 0, 'next_stage': 'maintain', 'repairs': 0,
                                 'maintenance_cycles': 0, 'feedback': None}), run['run_id']))
                db.execute("UPDATE runs SET status='queued',stage='maintain' WHERE id=?", (run['run_id'],))
            before = [e for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned']
            controller.close()
            reopened = Controller(root)
            try:
                status = reopened.call('status', run)
                self.assertEqual(status['status'], 'blocked')
                self.assertIn('Unsupported saved workflow', status['reason'])
                after = [e for e in reopened.call('events', run)['events'] if e['kind'] == 'stage.assigned']
                self.assertEqual(after, before)
                with self.assertRaisesRegex(ValueError, 'Unsupported saved workflow'):
                    reopened.call('resume', run)
            finally:
                reopened.close()

    def test_actual_legacy_final_contradiction_is_terminal_across_restart(self):
        # A later unavailable call cannot erase an already observed contradiction.
        import itertools
        for (temperature, final_duty), later_fault, error_position in itertools.product(
                ((999, 0), (20, .5), (999, None), (None, .5)),
                ('no_failed_event', 'host', 'operator', None), ('before', 'after')):
            with self.subTest(temperature=temperature, final_duty=final_duty, later_fault=later_fault, error_position=error_position), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events = [{'ok': True, 'operation': 'read', 'result': {'ok': True, 'outputs': {'t': temperature}},
                           'observation': {'duty': .5}},
                          {'ok': True, 'operation': 'disarm', 'result': {'ok': True}, 'observation': {'duty': final_duty}}]
                if final_duty is None:
                    events = [dict(events[0], observation=None)]
                elif temperature is None:
                    events = [events[1]]
                if later_fault != 'no_failed_event':
                    failed = {'ok': False, 'operation': 'read',
                              'result': {'ok': False, 'error': {'fault': later_fault, 'message': 'Unrelated call unavailable'}}}
                    events.insert(0 if error_position == 'before' else len(events), failed)
                controller, run, result = self.run_scripted(root, legacy_events=events)
                try:
                    self.assertEqual((result['status'], result['outcome_category']), ('failed', 'model'))
                    self.assertTrue(result['progress']['terminal_final_failure'])
                    self.assertEqual(tuple(h['stage'] for h in result['accepted_handoffs']), SIX_STAGES[:-1])
                    self.assertEqual(result['evaluator_verdicts'][-1]['verdict'], 'failed')
                    saved = root / 'home/runs' / run['run_id'] / 'benchmark/package-events.jsonl'
                    if later_fault != 'no_failed_event':
                        saved_failures = [json.loads(line)['result'] for line in saved.read_text().splitlines()
                                          if not json.loads(line)['ok']]
                        self.assertEqual(saved_failures, [failed['result']])
                    with self.assertRaisesRegex(ValueError, 'terminal'):
                        controller.call('resume', run)
                finally:
                    controller.close()
                reopened = Controller(root / 'home')
                try:
                    self.assertTrue(reopened.call('result', run)['progress']['terminal_final_failure'])
                    with self.assertRaisesRegex(ValueError, 'terminal'):
                        reopened.call('resume', run)
                finally:
                    reopened.close()

    def test_legacy_unavailable_observation_preserves_recovery(self):
        import itertools
        for fault, available in itertools.product(('host', 'operator', None), ('none', 'temperature', 'disarm')):
            with self.subTest(fault=fault, available=available), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                events = []
                if available == 'temperature':
                    events.append({'ok': True, 'operation': 'read', 'result': {'ok': True, 'outputs': {'t': 20}}})
                elif available == 'disarm':
                    events.append({'ok': True, 'operation': 'disarm', 'result': {'ok': True}, 'observation': {'duty': 0}})
                events.append({'ok': False, 'operation': 'read', 'result': {'ok': False, 'error': {'fault': fault}}})
                controller, run, result = self.run_scripted(root, legacy_events=events)
                try:
                    self.assertEqual((result['status'], result['outcome_category']), ('blocked', fault or 'unknown'))
                    self.assertFalse(result['progress'].get('terminal_final_failure'))
                finally:
                    controller.close()
                reopened = Controller(root / 'home')
                try:
                    with patch.object(reopened, '_spawn'):
                        self.assertTrue(reopened.call('resume', run)['ok'])
                finally:
                    reopened.close()

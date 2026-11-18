"""Public toy evidence; no native calibration or model execution."""
import json
import unittest

from generative_driver.benchmark_support import evidence
from generative_driver.benchmark_support.truth import EvaluatorPassword


class V2EvidenceTests(unittest.TestCase):
    def test_public_report_retains_gate_assignment_and_repair_resources(self):
        public = evidence.public_v2_report({'additional_attempts': 2, 'progress': {'repairs': 2,
            'maintenance_cycles': 1, 'revision': 3, 'feedback': 'PRIVATE SENTINEL'},
            'accepted_gates': [{'stage': 'probe', 'revision': 1, 'assignment_id': 'attempt-2',
                'artifact_sha256': 'a'*64}], 'attempt_limits': {'max_revisions': 2, 'secret': 'PRIVATE SENTINEL'},
            'case_pin': {'execution': 'scripted-contract-fixture', 'evidence_track': 'toy', 'scope': 'full-workflow'}})
        self.assertEqual(public['accepted_gates'][0]['assignment_id'], 'attempt-2')
        self.assertEqual(public['additional_attempts'], 2)
        self.assertEqual(public['progress'], {'repairs': 2, 'revision': 3})
        self.assertEqual(public['case_pin']['execution'], 'scripted-contract-fixture')
        self.assertNotIn('PRIVATE SENTINEL', json.dumps(public))

    def test_public_export_uses_nested_allowlists(self):
        secret = 'PRIVATE SENTINEL'
        private = {'schema': 'benchmark-run-evidence/1',
            'case_pin': {'case_id': 'fixture', 'scenario_id': 'control', 'root': secret,
                         'manifest': {'source': secret}, 'pin_sha256': {'unexpected': secret}},
            'records': [{'value': secret}], 'contract': {'checks': [{'expected': secret}]},
            'unexpected': secret,
            'final_evaluation': {'verdict': 'failed', 'passed': 0, 'total': 1, 'checks': [secret]},
            'usage': {'total_tokens': 12, 'new_secret': secret},
            'maintenance': {'false_alarm': False, 'diagnostic': secret},
            'stages': {'probe': {'attempts': [{'assignment_id': 'a', 'usage': {'total_tokens': 12, 'secret': secret}, 'report': secret}], 'checks': [secret]}},
            'executed': {'runtime_configuration': {'runtime': 'codex', 'password_file': secret},
                         'environment': {'system': 'toy', 'secret': secret}}}
        public = evidence.public_v2_report(private)
        self.assertEqual(public['schema'], 'benchmark-report/2')
        self.assertEqual(public['final_evaluation']['total'], 1)
        self.assertEqual(public['usage']['total_tokens'], 12)
        self.assertEqual(public['stages']['probe']['attempts'][0]['assignment_id'], 'a')
        self.assertNotIn(secret, json.dumps(public))
        self.assertNotIn('records', public)


class SealedEvidenceFixture:
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.password = EvaluatorPassword('test-only password')
        self.password_file = self.root / 'password'
        self.password_file.write_text('test-only password')

    def payload(self, scenario='original', repaired=False):
        from generative_driver.benchmark_support.snapshots import canonical_digest, execution_provenance
        stages = ['acquire','interpret','probe','ground','emit','reuse']
        pin = {'execution':'scripted-contract-fixture', 'case_id':'tq9-v2', 'case_version':'5',
            'scenario_id':scenario, 'case_seed':7, 'manifest_sha256':'c'*64, 'truth_sha256':'d'*64,
            'image_hashes':{'firmware.bin':'e'*64}, 'time_policy':{}, 'evaluator_version':'5',
            'manifest':{'schema':'benchmark-case/2','id':'tq9-v2','version':'5','evaluator_version':'5',
                'scenarios':['original'], 'images':{'firmware.bin':'e'*64}, 'required_stages':stages}}
        snapshot = {'schema':'benchmark-execution-snapshot/1','case_pin':pin,
            'public_files':{}, 'executed':execution_provenance({'runtime':'scripted-contract-fixture'}, case_ids=(pin['case_id'],))}
        snapshot['snapshot_sha256'] = canonical_digest(snapshot)
        revision = 1 if repaired else 0
        digest = 'b'*64 if repaired else 'a'*64
        def evaluation(phase, revision, digest, value=9):
            check = {'id':phase+'/0/value','revision':revision,'kind':'number','expected':9,
                'absolute_tolerance':0,'unit':'V','channel':'independent-monitor'}
            record = {'id':check['id'],'revision':revision,'value':value,'unit':'V',
                'channel':'independent-monitor','artifact_sha256':digest}
            return {'phase':phase,'revision':revision,'frozen_artifact_sha256':digest,
                'contract':{'schema':'benchmark-behavior/1','artifact_sha256':digest,'checks':[check]},'records':[record]}
        evaluations = [evaluation('diagnostic',0,'a'*64,1)] if repaired else []
        evaluations.append(evaluation('final',revision,digest))
        gates, artifacts = [], []
        for stage in stages:
            identity = {'stage':stage,'revision':0 if stage=='acquire' else revision,
                'assignment_id':stage+'-'+str(revision)}
            artifacts.append({**identity,'sha256':digest})
            checks = [{'id':'fresh_worker_mission','passed':True},{'id':'frozen_final_behavior','passed':True}] if stage=='reuse' else [{'id':'accepted-evaluator-check','passed':True}]
            gates.append({**identity,'artifact_sha256':digest,'checks':checks,
                'evaluation_refs':[{'phase':'final','revision':revision}] if stage=='reuse' else []})
        return {'schema':'benchmark-run-evidence/1','case_pin':pin,'execution_snapshot':snapshot,
            'evaluations':evaluations,'accepted_gates':gates,'accepted_artifacts':artifacts}

    def sealed(self, payload):
        sealed = evidence.seal_run_evidence(payload, self.root / 'evidence.json', self.password)
        report = evidence.public_v2_report({**payload,
            'snapshot_sha256': payload['execution_snapshot']['snapshot_sha256'],
            'executed': payload['execution_snapshot']['executed'],
            'final_evaluation': {'evidence_sha256': sealed['sha256']}})
        return report, self.root / sealed['path']



class EvidenceBoundaryTests(unittest.TestCase):
    def test_ground_receives_device_evidence_and_never_diagnostic_records(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from generative_driver.supplied.ground import device_evidence
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / 'model.json'
            model.write_text('{}')
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            probe = root / 'probe-1.json'
            probe.write_text(json.dumps({'schema': 'interface-probe/1', 'model_sha256': digest, 'operation': 'visible',
                'ok': True, 'results': [{'ok': True, 'outputs': {'value': 7}, 'units': {'value': 'V'}}]}))
            observed = root / 'observations.json'
            observed.write_text(json.dumps({'schema': 'benchmark-observations/2', 'model_sha256': digest,
                'diagnostics': [{'phase': 'diagnostic', 'records': [{'task_id': 'graded', 'value': 7,
                    'unit': 'V', 'expected': 'FINAL SECRET'}]},
                    {'phase': 'final', 'records': [{'task_id': 'hidden', 'value': 'FINAL SECRET'}]}],
                'records': [{'value': 'FINAL SECRET'}]}))
            accepted = [{'stage': 'probe', 'artifacts': [{'path': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in (model, probe, observed)]}]
            result = device_evidence(accepted, root / 'worker')
            text = json.dumps(result) + ''.join(Path(path).read_text() for path in result['inputs'] if Path(path).is_file())
            self.assertIn('visible', text)
            for forbidden in ('FINAL SECRET', 'hidden', 'graded', 'expected'):
                self.assertNotIn(forbidden, text)

    def test_cli_accepts_evidence_and_requires_it_for_v2(self):
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark import main
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'report.json'
            report.write_text(json.dumps({'schema': 'benchmark-report/2'}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['score', str(report), '--evidence', str(Path(temp) / 'absent'), '--password-file', str(Path(temp) / 'password')])
            self.assertEqual(code, 1)
            self.assertIn('error', json.loads(output.getvalue()))

    def test_a_frozen_final_decision_a_stage_check_returns_is_no_verdict_even_during_cancellation(self):
        # Catches a stage observation that grades: a frozen final decision returned by reuse's check, while the run is
        # being cancelled, recording a stage verdict or the gated protocol's terminal flag. Nothing is graded inside a
        # run; the cancellation stands and the run may resume.
        import hashlib
        import tempfile
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = Controller(root)
            check_started, cancel_committed = threading.Event(), threading.Event()
            assigned = []
            real_state = controller._state
            def state(run_id, status, reason=None, stage=None, **metadata):
                result = real_state(run_id, status, reason, stage, **metadata)
                if status == 'cancelled':
                    cancel_committed.set()
                return result
            def execute(request, config, cancel):
                assigned.append(request.stage)
                target = request.workspace / 'artifact.json'
                target.write_text('{}')
                return {'runtime': 'scripted-contract-fixture', 'status': 'completed', 'elapsed_seconds': 0,
                    'usage': None, 'report': {'status': 'completed', 'summary': 'scripted-contract-fixture',
                    'artifacts': [{'path': target.name, 'kind': 'evidence', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}],
                    'checks': [], 'unresolved': []}}
            def check(spec, stage, run_dir, workspace, report, accepted, assignment):
                if stage != 'reuse':
                    return {'ok': True, 'artifacts': [str(workspace / 'artifact.json')], 'checks': []}
                contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a'*64, 'checks': [{
                    'id': 'hidden/value', 'revision': 0, 'kind': 'number', 'expected': 9,
                    'absolute_tolerance': 0, 'unit': 'V', 'channel': 'independent-monitor'}]}
                decision = evidence.frozen_final_decision(contract, [])
                check_started.set()
                if not cancel_committed.wait(60):
                    raise AssertionError('External cancellation did not commit')
                return decision
            try:
                with patch.object(Controller, '_prepare', return_value={'objective': 'fixture', 'inputs': [], 'allowed_tools': []}), \
                     patch.object(Controller, '_check', side_effect=check), \
                     patch('generative_driver.configurator.execute', side_effect=execute):
                    with patch.object(controller, '_state', side_effect=state), ThreadPoolExecutor(max_workers=1) as pool:
                        run = controller.start({'goal': 'scripted-contract-fixture', 'budget_seconds': 120,
                                                 'executor_config': {'command': ['scripted-runtime']}})
                        # Six persisted stages precede this barrier. Test the
                        # cancellation ordering without requiring their file/DB
                        # work to fit a five-second startup allowance.
                        if not check_started.wait(60):
                            self.fail('Final check never ran: ' + str(controller.call('result', run)))
                        cancelled = pool.submit(controller.call, 'cancel', run).result(timeout=60)
                        self.assertFalse(cancelled['stopping'])
                    result = controller.call('result', run)
                    self.assertEqual((result['status'], result['outcome_category']), ('cancelled', 'cancelled'))
                    self.assertEqual(result['evaluator_verdicts'], [])
                    self.assertIsNone(result['equivalence'])
                    self.assertNotIn('terminal_final_failure', result['progress'])
                    self.assertEqual(assigned, ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
                controller.close()
                controller = Controller(root)
                with patch.object(controller, '_spawn'):
                    self.assertTrue(controller.call('resume', run)['ok'])
            finally:
                cancel_committed.set()
                controller.close()

    def test_a_run_recorded_with_the_gated_terminal_final_failure_stays_terminal_after_reopening(self):
        # The gated protocol's terminal shapes, which runs recorded before keep: the progress flag, or a failed frozen
        # final verdict saved by versions that could lose the flag, also by a process that stopped while running.
        import tempfile
        import time
        from pathlib import Path
        from generative_driver.configurator import Controller
        from tool_fixtures import wait_stopped
        for gap in ('none', 'verdict', 'running'):
            with self.subTest(old_marker_gap=gap), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                controller = Controller(root)
                try:
                    run = controller.start({'goal': 'scripted-contract-fixture', 'executor_config': {'command': ['missing-runtime']}})
                    wait_stopped(controller, run)
                    before = [e for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned']
                    with controller._db() as db:
                        progress = json.loads(db.execute('SELECT payload FROM progress WHERE run_id=?', (run['run_id'],)).fetchone()['payload'])
                        if gap == 'none':
                            db.execute('UPDATE progress SET payload=? WHERE run_id=?', (json.dumps({**progress, 'terminal_final_failure': True}), run['run_id']))
                        else:
                            verdict = {'stage': 'reuse', 'assignment_id': 'old-final', 'final_evaluation': True, 'fault': 'model',
                                       'verdict': 'failed', 'passed': 0, 'total': 1}
                            db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)', ('old-final', run['run_id'], 'reuse', json.dumps(verdict), time.time()))
                        db.execute("UPDATE runs SET status=?,outcome_category='model' WHERE id=?",
                                   ('running' if gap == 'running' else 'failed', run['run_id']))
                finally:
                    controller.close()
                controller = Controller(root)
                try:
                    if gap == 'running':
                        self.assertEqual(controller.call('status', run)['status'], 'failed')
                    with self.assertRaisesRegex(ValueError, 'final'):
                        controller.call('resume', run)
                    self.assertEqual([e for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'], before)
                finally:
                    controller.close()

"""Public toy evidence; no native calibration or model execution."""
import json
import unittest

from generative_driver.benchmark_support import evidence


class V2EvidenceTests(unittest.TestCase):
    def test_public_report_retains_gate_assignment_and_repair_resources(self):
        public = evidence.public_v2_report({'additional_attempts': 2, 'progress': {'repairs': 2,
            'maintenance_cycles': 1, 'revision': 3, 'feedback': 'PRIVATE SENTINEL'},
            'accepted_gates': [{'stage': 'probe', 'revision': 1, 'assignment_id': 'attempt-2',
                'artifact_sha256': 'a'*64}], 'attempt_limits': {'max_revisions': 2, 'secret': 'PRIVATE SENTINEL'},
            'case_pin': {'execution': 'scripted-contract-fixture', 'evidence_track': 'toy', 'scope': 'full-workflow'}})
        self.assertEqual(public['accepted_gates'][0]['assignment_id'], 'attempt-2')
        self.assertEqual(public['additional_attempts'], 2)
        self.assertEqual(public['progress'], {'repairs': 2, 'maintenance_cycles': 1, 'revision': 3})
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


class SealedEvidenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.password = self.root / 'password'
        self.password.write_text('test-only password')

    def payload(self, scenario='control', repaired=False):
        from generative_driver.benchmark_support.snapshots import canonical_digest, execution_provenance
        from generative_driver.benchmark import STAGES
        pin = {'execution': 'scripted-contract-fixture', 'case_id': 'toy', 'case_version': '2', 'scenario_id': scenario, 'case_seed': 7,
            'manifest_sha256': 'c'*64, 'truth_sha256': 'd'*64, 'image_hashes': {'toy.bin': 'e'*64},
            'time_policy': {}, 'evaluator_version': '2', 'manifest': {'schema': 'benchmark-case/2', 'required_stages': list(STAGES)}}
        snapshot = {'schema': 'benchmark-execution-snapshot/1', 'case_pin': pin,
            'public_files': {}, 'executed': execution_provenance({'runtime': 'scripted-contract-fixture'})}
        snapshot['snapshot_sha256'] = canonical_digest(snapshot)
        evaluations, gates, artifacts = [], [], []
        for revision in range(2 if repaired else 1):
            digest = ('a' if revision == 0 else 'b')*64
            check = {'id': 'hidden/0/value', 'revision': revision, 'kind': 'number',
                'expected': 9, 'absolute_tolerance': 0, 'unit': 'V', 'channel': 'independent-monitor'}
            record = {'id': 'hidden/0/value', 'revision': revision, 'value': 9, 'unit': 'V',
                'channel': 'independent-monitor', 'artifact_sha256': digest}
            evaluations.append({'phase': 'final', 'revision': revision, 'frozen_artifact_sha256': digest,
                'contract': {'schema': 'benchmark-behavior/1', 'artifact_sha256': digest, 'checks': [check]},
                'records': [record]})
            for stage in STAGES:
                if revision and stage == 'acquire':
                    continue
                identity = {'stage': stage, 'revision': revision, 'assignment_id': f'{stage}-{revision}'}
                artifacts.append({**identity, 'sha256': digest})
                gates.append({**identity, 'artifact_sha256': digest,
                    'checks': [{'id': 'accepted-evaluator-check', 'passed': True}],
                    'evaluation_refs': [{'phase': 'final', 'revision': revision}] if stage == 'reuse' else []})
        initial = {'revision': 0, 'claim': 'unchanged' if scenario == 'control' else 'drift',
            'diagnostic': {'evaluable': True, 'fault': None, 'contradiction': scenario != 'control',
                'evidence_ids': ['observation-1'], 'fresh_reuse_passed': True, 'requalified': False}}
        maintenance = {'initial': initial}
        if repaired:
            maintenance['repaired'] = {'revision': 1, 'claim': 'unchanged', 'diagnostic': {
                'evaluable': True, 'fault': None, 'contradiction': False, 'evidence_ids': ['observation-2'],
                'fresh_reuse_passed': True, 'requalified': True}}
        return {'schema': 'benchmark-run-evidence/1', 'case_pin': pin, 'execution_snapshot': snapshot,
            'evaluations': evaluations, 'accepted_gates': gates, 'accepted_artifacts': artifacts,
            'maintenance': maintenance}

    def sealed(self, payload):
        sealed = evidence.seal_run_evidence(payload, self.root / 'evidence.json', self.password)
        report = evidence.public_v2_report({**payload,
            'snapshot_sha256': payload['execution_snapshot']['snapshot_sha256'],
            'executed': payload['execution_snapshot']['executed'],
            'final_evaluation': {'evidence_sha256': sealed['sha256']}})
        return report, self.root / sealed['path']

    def test_offline_regrades_values_without_service_or_process(self):
        from unittest.mock import patch
        from generative_driver.benchmark import score
        payload = self.payload()
        report, path = self.sealed(payload)
        with patch('generative_driver.client.call', side_effect=AssertionError('service invoked')), \
             patch('subprocess.Popen', side_effect=AssertionError('process invoked')):
            self.assertEqual(score(report, self.password, evidence_path=path)['verdict'], 'passed')
        payload['evaluations'][0]['records'][0]['value'] = 1
        report, path = self.sealed(payload)
        report['verdict'] = report['final_evaluation']['verdict'] = 'passed'
        self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'failed')

    def test_preserves_submissions_and_rejects_cross_revision_records(self):
        payload = self.payload('semantic', repaired=True)
        payload['evaluations'][0]['records'][0]['value'] = 1
        report, path = self.sealed(payload)
        graded = evidence.regrade_v2(report, path, self.password)
        self.assertEqual([row['verdict'] for row in graded['evaluations']], ['failed', 'passed'])
        self.assertEqual(graded['verdict'], 'failed')
        payload['evaluations'][0]['records'] = payload['evaluations'][1]['records']
        with self.assertRaisesRegex(ValueError, 'artifact|revision'):
            self.sealed(payload)

    def test_sealing_rejects_modified_accepted_package(self):
        import hashlib
        payload = self.payload()
        package = self.root / 'accepted-package'
        package.write_bytes(b'frozen package')
        artifact = payload['accepted_artifacts'][0]
        artifact.update(path=str(package), sha256=hashlib.sha256(package.read_bytes()).hexdigest())
        package.write_bytes(b'changed package')
        with self.assertRaisesRegex(ValueError, 'artifact'):
            self.sealed(payload)

    def test_final_freeze_must_match_accepted_emit_identity(self):
        payload = self.payload()
        next(row for row in payload['accepted_artifacts'] if row['stage'] == 'emit')['sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'accepted|artifact'):
            self.sealed(payload)

    def test_unsupported_evaluator_version_rejected_even_with_matching_identity(self):
        from generative_driver.benchmark_support.snapshots import canonical_digest
        payload = self.payload()
        payload['case_pin']['evaluator_version'] = 'future-unsupported'
        snapshot = payload['execution_snapshot']
        snapshot['snapshot_sha256'] = canonical_digest({k: v for k, v in snapshot.items() if k != 'snapshot_sha256'})
        report, path = self.sealed(payload)
        with self.assertRaisesRegex(ValueError, 'version'):
            evidence.regrade_v2(report, path, self.password)

    def test_missing_final_can_be_saved_but_not_passed(self):
        payload = self.payload()
        payload['evaluations'] = []
        report, path = self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'failed')

    def test_regrade_rejects_falsely_relabelled_execution_and_revisions(self):
        payload = self.payload()
        report, path = self.sealed(payload)
        for key, value in [('toolchain_revision', 'f'*64), ('skills_revision', 'f'*64),
                           ('execution', 'actual-agent-physical'), ('model_benchmark', True)]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity'):
                evidence.regrade_v2({**report, key: value}, path, self.password)

    def test_regrade_rejects_conflicting_public_identity_aliases(self):
        payload = self.payload()
        report, path = self.sealed(payload)
        for key in ('case', 'case_version', 'evaluator_version', 'scenario_id', 'case_seed', 'evaluator_revision'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                evidence.regrade_v2({**report, key: 'changed'}, path, self.password)

    def test_first_frozen_submission_after_diagnostic_repairs_uses_acquire_zero(self):
        payload = self.payload()
        for row in payload['evaluations']:
            row['revision'] = 2
            row['contract']['checks'][0]['revision'] = 2
            row['records'][0]['revision'] = 2
        for row in payload['accepted_gates'] + payload['accepted_artifacts']:
            if row['stage'] != 'acquire':
                row['revision'] = 2
            for ref in row.get('evaluation_refs', []):
                ref['revision'] = 2
        payload['maintenance']['initial']['revision'] = 2
        report, path = self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'passed')

    def test_missing_records_and_gates_cannot_pass(self):
        for missing in ('records', 'gate', 'reference', 'artifact', 'checks'):
            with self.subTest(missing=missing):
                payload = self.payload()
                if missing == 'records':
                    payload['evaluations'][0]['records'] = []
                elif missing == 'gate':
                    payload['accepted_gates'].pop()
                elif missing == 'reference':
                    payload['accepted_gates'][-2]['evaluation_refs'] = []
                elif missing == 'artifact':
                    payload['accepted_artifacts'].pop()
                else:
                    payload['accepted_gates'][-1]['checks'] = []
                report, path = self.sealed(payload)
                self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'failed')

    def test_report_ciphertext_snapshot_and_evaluator_identity_are_verified(self):
        import copy
        from generative_driver.benchmark_support.truth import seal
        payload = self.payload()
        report, path = self.sealed(payload)
        for field in ('scenario_id', 'case_id', 'manifest_sha256', 'truth_sha256'):
            changed = copy.deepcopy(report)
            changed['case_pin'][field] = 'changed'
            with self.subTest(field=field), self.assertRaises(ValueError):
                evidence.regrade_v2(changed, path, self.password)
        for field in ('snapshot_sha256',):
            with self.assertRaises(ValueError):
                evidence.regrade_v2({**report, field: 'f'*64}, path, self.password)
        changed = copy.deepcopy(report)
        changed['evaluations'][0]['frozen_artifact_sha256'] = 'f'*64
        with self.assertRaises(ValueError):
            evidence.regrade_v2(changed, path, self.password)
        payload['execution_snapshot']['executed']['evaluator_revision'] = 'f'*64
        digest = seal(payload, path, 'test-only password')
        report['final_evaluation']['evidence_sha256'] = digest
        with self.assertRaises(ValueError):
            evidence.regrade_v2(report, path, self.password)
        seal(self.payload(), path, 'test-only password')
        with self.assertRaisesRegex(ValueError, 'hash'):
            evidence.regrade_v2(report, path, self.password)

    def test_maintenance_requires_worker_detection_and_repair(self):
        for scenario, repaired, claim in [('control', False, 'drift'), ('semantic', False, 'drift'),
                                          ('semantic', True, 'unchanged')]:
            payload = self.payload(scenario, repaired)
            payload['maintenance']['initial']['claim'] = claim
            report, path = self.sealed(payload)
            self.assertEqual(evidence.regrade_v2(report, path, self.password)['verdict'], 'failed')
        payload = self.payload('semantic', True)
        report, path = self.sealed(payload)
        graded = evidence.regrade_v2(report, path, self.password)
        self.assertEqual(graded['verdict'], 'passed')
        self.assertEqual(graded['maintenance'], {'evaluable': True, 'drift_claimed': True, 'drift_observed': True,
            'false_alarm': False, 'repair_completed': True, 'requalified': True, 'fresh_reuse_passed': True})


class EvidenceBoundaryTests(unittest.TestCase):
    def test_ground_projects_only_accepted_diagnostics(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.ground import prepare
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / 'model.json'
            model.write_text('{}')
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            observed = root / 'observations.json'
            observed.write_text(json.dumps({'schema': 'benchmark-observations/2', 'model_sha256': digest,
                'diagnostics': [{'phase': 'diagnostic', 'records': [{'task_id': 'visible', 'value': 7,
                    'unit': 'V', 'expected': 'FINAL SECRET'}]},
                    {'phase': 'final', 'records': [{'task_id': 'hidden', 'value': 'FINAL SECRET'}]}],
                'records': [{'value': 'FINAL SECRET'}]}))
            accepted = [{'stage': 'probe', 'artifacts': [{'path': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in (model, observed)]}]
            result = prepare(accepted, root / 'worker')
            text = json.dumps(result) + ''.join(Path(path).read_text() for path in result['inputs'])
            self.assertIn('visible', text)
            self.assertNotIn('FINAL SECRET', text)
            self.assertNotIn('hidden', text)

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

    def test_cancel_during_final_return_persists_terminal_and_old_verdict_blocks_resume(self):
        import hashlib
        import tempfile
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        for simulate_old_marker_gap in ('none', 'cancelled', 'running'):
            with self.subTest(old_marker_gap=simulate_old_marker_gap), tempfile.TemporaryDirectory() as temp:
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
                    if not cancel_committed.wait(5):
                        raise AssertionError('External cancellation did not commit')
                    return decision
                try:
                    with patch.object(Controller, '_prepare', return_value={'objective': 'fixture', 'inputs': [], 'allowed_tools': []}), \
                         patch.object(Controller, '_check', side_effect=check), \
                         patch('generative_driver.configurator.execute', side_effect=execute):
                        with patch.object(controller, '_state', side_effect=state), ThreadPoolExecutor(max_workers=1) as pool:
                            run = controller.start({'goal': 'scripted-contract-fixture', 'case': 'tq9', 'budget_seconds': 30})
                            self.assertTrue(check_started.wait(5), 'Final check never ran')
                            cancelled = pool.submit(controller.call, 'cancel', run).result(timeout=5)
                            self.assertFalse(cancelled['stopping'])
                        result = controller.call('result', run)
                        self.assertEqual(result['evaluator_verdicts'][-1]['verdict'], 'failed')
                        self.assertEqual(result['outcome_category'], 'cancelled')
                        if simulate_old_marker_gap != 'none':
                            # Model records saved before terminal marker persistence was atomic.
                            with controller._db() as db:
                                progress = result['progress']
                                progress.pop('terminal_final_failure', None)
                                db.execute('UPDATE progress SET payload=? WHERE run_id=?', (json.dumps(progress), run['run_id']))
                        else:
                            self.assertTrue(result['progress'].get('terminal_final_failure'))
                        controller.close()
                        if simulate_old_marker_gap == 'running':
                            with controller._db() as db:
                                db.execute("UPDATE runs SET status='running' WHERE id=?", (run['run_id'],))
                        controller = Controller(root)
                        if simulate_old_marker_gap == 'running':
                            self.assertEqual(controller.call('status', run)['status'], 'failed')
                        with self.assertRaisesRegex(ValueError, 'final'):
                            controller.call('resume', run)
                        self.assertEqual(assigned, ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
                finally:
                    cancel_committed.set()
                    controller.close()

    def test_frozen_final_failure_is_terminal_even_after_reopening_controller(self):
        import hashlib
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = Controller(root)
            assigned = []
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
                return evidence.frozen_final_decision(contract, [{'id': 'hidden/value', 'revision': 0,
                    'artifact_sha256': 'a'*64, 'value': 1, 'unit': 'V', 'channel': 'independent-monitor'}])
            try:
                with patch.object(controller, '_prepare', return_value={'objective': 'fixture', 'inputs': [], 'allowed_tools': []}), \
                     patch.object(controller, '_check', side_effect=check), \
                     patch('generative_driver.configurator.execute', side_effect=execute):
                    run = controller.start({'goal': 'scripted-contract-fixture', 'case': 'tq9', 'budget_seconds': 30})
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        status = controller.call('status', run)
                        if status['status'] in ('failed', 'blocked', 'completed') and not status['stopping']:
                            break
                        time.sleep(.01)
                    result = controller.call('result', run)
                    self.assertEqual(result['status'], 'failed')
                    self.assertTrue(result['progress'].get('terminal_final_failure'))
                    self.assertEqual(result['evaluator_verdicts'][-1]['fault'], 'model')
                    self.assertNotIn('route', result['evaluator_verdicts'][-1])
                    with self.assertRaisesRegex(ValueError, 'final'):
                        controller.call('resume', run)
                    self.assertEqual(assigned, ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
                controller.close()
                controller = Controller(root)
                with self.assertRaisesRegex(ValueError, 'final'):
                    controller.call('resume', run)
            finally:
                controller.close()


class ReportV2Tests(unittest.TestCase):
    def test_report_uses_snapshot_and_allows_only_public_attempt_summaries(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.reporting import report_run
        payload = SealedEvidenceTests().payload()
        snapshot = payload['execution_snapshot']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = root / 'runs' / 'fixture' / 'benchmark'
            saved.mkdir(parents=True)
            (saved / 'execution.json').write_text(json.dumps(snapshot))
            (saved / 'state.json').write_text(json.dumps({**payload,
                'final_evaluation': {'verdict': 'failed', 'passed': 0, 'total': 1, 'evidence_sha256': 'f'*64}}))
            result = {'ok': True, 'run_id': 'fixture', 'status': 'failed', 'created': 0, 'updated': 5,
                'case_pin': payload['case_pin'], 'snapshot_sha256': snapshot['snapshot_sha256'],
                'configuration': {'case': 'toy'}, 'accepted_handoffs': [],
                'worker_reports': [{'assignment_id': 'a', 'stage': 'probe', 'status': 'completed', 'elapsed_seconds': 1,
                    'usage': None, 'report': {'status': 'completed', 'summary': 'PRIVATE SENTINEL'}}],
                'evaluator_verdicts': [{'assignment_id': 'a', 'stage': 'probe', 'revision': 0, 'verdict': 'failed',
                    'passed': 0, 'total': 1, 'records': ['PRIVATE SENTINEL']},
                    {'assignment_id': 'b', 'stage': 'probe', 'revision': 1, 'verdict': 'passed', 'passed': 1, 'total': 1}]}
            def call(method, params, **kwargs):
                return result if method == 'result' else {'events': [], 'cursor': 0}
            with patch('generative_driver.client.call', side_effect=call):
                report = report_run('fixture', home=root)
            self.assertEqual(report['schema'], 'benchmark-report/2')
            self.assertEqual(report['final_evaluation']['evidence_sha256'], 'f'*64)
            self.assertEqual([r['verdict'] for r in report['evaluator_verdicts']], ['failed', 'passed'])
            self.assertEqual(report['executed']['evaluator_revision'], snapshot['executed']['evaluator_revision'])
            self.assertNotIn('PRIVATE SENTINEL', (root / 'runs/fixture/report.json').read_text())
            self.assertNotIn('records', json.dumps(report))


class MaintenanceEvaluabilityTests(unittest.TestCase):
    def test_live_maintenance_projects_initial_evaluator_availability_without_private_diagnostic(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import hashlib
        from generative_driver.benchmark_support.emulated import _maintain
        from generative_driver.benchmark_support.emulated_evidence import _private
        from generative_driver.benchmark_support.snapshots import canonical_digest
        for evaluable,fault,want in ((True,None,True),(False,None,False),(True,'host',False),(True,'operator',False)):
            with self.subTest(evaluable=evaluable,fault=fault),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);bench=root/'benchmark';bench.mkdir();workspace=root/'worker';workspace.mkdir()
                password=root/'password';password.write_text('toy-password',encoding='utf-8')
                capabilities=root/'capabilities.json';capabilities.write_text('{}',encoding='utf-8')
                state={'package_copies':{'maintain':str(root/'package')},'capabilities_path':str(capabilities),'fresh_reuse_passed':True}
                (bench/'state.json').write_text(json.dumps(state),encoding='utf-8')
                snapshot={'schema':'benchmark-execution-snapshot/1','case_pin':{'scenario_id':'control'},'public_files':{}}
                snapshot['snapshot_sha256']=canonical_digest(snapshot)
                (bench/'execution.json').write_text(json.dumps(snapshot),encoding='utf-8')
                claim=workspace/'maintenance.json'
                claim.write_text(json.dumps({'schema':'benchmark-maintenance-claim/1','claim':'drift','evidence_ids':['7']}),encoding='utf-8')
                report={'status':'needs_revision','artifacts':[{'path':'maintenance.json','sha256':hashlib.sha256(claim.read_bytes()).hexdigest()}]}
                options={'evaluator_password_file':str(password),'assignment_id':'a','worker_events':[{'id':7,'actor':'worker','assignment_id':'a'}]}
                observation={'evaluable':evaluable,'fault':fault,'contradiction':False,'evidence_ids':['toy-observation'],'private':'PRIVATE'}
                with patch('generative_driver.benchmark_support.emulated_evidence.maintenance_observation',return_value=observation):
                    _maintain('toy',root,workspace,report,options)
                public=evidence.public_v2_report(json.loads((bench/'state.json').read_text()))
                self.assertIs(public['maintenance'].get('evaluable'),want)
                self.assertIs(public['maintenance']['false_alarm'],want)
                self.assertNotIn('PRIVATE',json.dumps(public))
                self.assertEqual(_private(root,options)['maintenance']['initial']['diagnostic']['private'],'PRIVATE')

    def test_authenticated_regrade_derives_evaluability_including_failed_control_claims(self):
        import tempfile
        from pathlib import Path
        for evaluable,fault,want in ((True,None,True),(False,None,False),(True,'host',False),(True,'operator',False)):
            with self.subTest(evaluable=evaluable,fault=fault),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);password=root/'password';password.write_text('toy-password',encoding='utf-8')
                payload=SealedEvidenceTests().payload()
                initial=payload['maintenance']['initial'];initial['claim']='drift'
                initial['diagnostic'].update(evaluable=evaluable,fault=fault,unknown='PRIVATE')
                sealed=evidence.seal_run_evidence(payload,root/'sidecar.enc',password)
                report=evidence.public_v2_report({**payload,'snapshot_sha256':payload['execution_snapshot']['snapshot_sha256'],
                    'executed':payload['execution_snapshot']['executed'],'final_evaluation':{'evidence_sha256':sealed['sha256']}})
                graded=evidence.regrade_v2(report,root/'sidecar.enc',password)
                self.assertEqual(graded['verdict'],'failed')
                self.assertIs(graded['maintenance'].get('evaluable'),want)
                self.assertIs(graded['maintenance']['false_alarm'],want)
                self.assertNotIn('PRIVATE',json.dumps(graded))

class MaintenanceRecoveryTests(SealedEvidenceTests):
    def live_attempt(self, payload, claim_value, diagnostic, revision=0):
        import hashlib
        from unittest.mock import patch
        from generative_driver.benchmark_support.emulated import _maintain
        from generative_driver.benchmark_support.emulated_evidence import _private
        bench=self.root/'benchmark';bench.mkdir(exist_ok=True)
        workspace=self.root/'worker';workspace.mkdir(exist_ok=True)
        capabilities=self.root/'capabilities.json';capabilities.write_text('{}')
        state={'revision':revision,'package_copies':{'maintain':str(self.root/'package')},
            'capabilities_path':str(capabilities),'fresh_reuse_passed':True,'diagnostic':{'passed':True}}
        (bench/'state.json').write_text(json.dumps(state))
        (bench/'execution.json').write_text(json.dumps(payload['execution_snapshot']))
        assignment='attempt-'+str(len(payload['maintenance'].get('attempts',[])))
        claim=workspace/'maintenance.json';claim.write_text(json.dumps({'schema':'benchmark-maintenance-claim/1',
            'claim':claim_value,'evidence_ids':['7']}))
        report={'status':'needs_revision' if claim_value=='drift' else 'completed',
            'artifacts':[{'path':'maintenance.json','sha256':hashlib.sha256(claim.read_bytes()).hexdigest()}]}
        options={'evaluator_password_file':str(self.password),'assignment_id':assignment,
            'worker_events':[{'id':7,'actor':'worker','assignment_id':assignment}]}
        _private(self.root,options,payload)
        accepted=[dict(g,route='interpret' if g['stage']=='maintain' and payload['case_pin']['scenario_id']!='control' and g['revision']==0 else None)
            for g in payload['accepted_gates'] if g['stage']!='maintain' or g['revision']<revision]
        with patch('generative_driver.benchmark_support.emulated_evidence.maintenance_observation',return_value=dict(diagnostic)):
            result=_maintain('toy',self.root,workspace,report,options,accepted=accepted)
        saved=_private(self.root,options)
        summary=json.loads((bench/'state.json').read_text())['maintenance']
        # A successful controller handoff binds the evaluator attempt to this revision.
        if result['ok']:
            for gate in saved['accepted_gates']:
                if gate['stage']=='maintain' and gate['revision']==revision:
                    old=gate['assignment_id'];gate.update(assignment_id=assignment,route=result.get('route'))
                    for artifact in saved['accepted_artifacts']:
                        if artifact['assignment_id']==old:artifact['assignment_id']=assignment
        return result,saved,summary

    def test_control_retries_preserve_attempts_without_repair_credit_and_regrade_agrees(self):
        from generative_driver.benchmark_support.suite_reporting import _maintenance_ok
        good={'evaluable':True,'fault':None,'contradiction':False,'evidence_ids':['toy']}
        for first_claim, first_observation in (('unknown',good),('unchanged',dict(good,evaluable=False,fault='host'))):
            with self.subTest(first_claim=first_claim):
                payload=self.payload();payload['maintenance']={}
                failed,payload,_=self.live_attempt(payload,first_claim,first_observation)
                self.assertFalse(failed['ok'])
                passed,payload,summary=self.live_attempt(payload,'unchanged',good)
                self.assertTrue(passed['ok']);self.assertTrue(_maintenance_ok(summary))
                self.assertFalse(summary['repair_completed']);self.assertFalse(summary['requalified'])
                self.assertTrue(summary['evaluable']);self.assertEqual(len(payload['maintenance']['attempts']),2)
                report,path=self.sealed(payload);graded=evidence.regrade_v2(report,path,self.password)
                self.assertEqual(graded['verdict'],'passed');self.assertEqual(graded['maintenance'],summary)

    def test_false_alarm_is_retained_and_cannot_be_erased_by_retry(self):
        good={'evaluable':True,'fault':None,'contradiction':False,'evidence_ids':['toy']}
        payload=self.payload();payload['maintenance']={}
        failed,payload,_=self.live_attempt(payload,'drift',good)
        self.assertEqual(failed['fault'],'model')
        later,payload,summary=self.live_attempt(payload,'unchanged',good)
        self.assertFalse(later['ok']);self.assertTrue(summary['false_alarm'])
        report,path=self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report,path,self.password)['verdict'],'failed')

    def test_semantic_repair_requires_actual_accepted_route(self):
        drift={'evaluable':True,'fault':None,'contradiction':True,'evidence_ids':['toy']}
        payload=self.payload('semantic',repaired=True);payload['maintenance']={}
        first,payload,_=self.live_attempt(payload,'drift',drift)
        self.assertEqual(first['route'],'interpret')
        second,payload,summary=self.live_attempt(payload,'unchanged',dict(drift,contradiction=False),revision=1)
        self.assertTrue(second['ok']);self.assertTrue(summary['repair_completed'])
        report,path=self.sealed(payload);graded=evidence.regrade_v2(report,path,self.password)
        self.assertEqual(graded['verdict'],'passed');self.assertEqual(graded['maintenance'],summary)
        payload['accepted_gates'][6]['route']=None
        report,path=self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report,path,self.password)['verdict'],'failed')

    def test_extra_successful_final_kept_without_inventing_maintenance_gate(self):
        import copy
        payload=self.payload('semantic',repaired=True)
        extra=copy.deepcopy(payload['evaluations'][-1]);extra['revision']=2
        for row in extra['records']+extra['contract']['checks']:row['revision']=2
        payload['evaluations'].append(extra)
        for key in ('accepted_gates','accepted_artifacts'):
            for row in list(payload[key]):
                if row['revision']==1 and row['stage']!='maintain':
                    item=copy.deepcopy(row);item['revision']=2;item['assignment_id']+='-extra'
                    for ref in item.get('evaluation_refs',[]):ref['revision']=2
                    payload[key].append(item)
        payload['maintenance']['repaired']['revision']=2
        for key in ('accepted_gates','accepted_artifacts'):
            for row in payload[key]:
                if row['stage']=='maintain' and row['revision']==1:row['revision']=2
        report,path=self.sealed(payload);graded=evidence.regrade_v2(report,path,self.password)
        self.assertEqual(graded['verdict'],'passed');self.assertEqual(len(graded['evaluations']),3)
        payload['evaluations'][1]['records'][0]['value']=0
        report,path=self.sealed(payload)
        self.assertEqual(evidence.regrade_v2(report,path,self.password)['verdict'],'failed')

import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark import prepare_stage


class PhysicalPrerequisitesTests(unittest.TestCase):
    def test_physical_profile_requests_explicit_binding_before_agent_or_io(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare_stage('bme280','acquire',Path(directory)/'run',Path(directory)/'worker')
            self.assertIn('blocked',prepared)
            self.assertIn('binding',prepared['blocked'].lower())
            self.assertNotIn('unsupported',prepared['blocked'].lower())

    def test_worker_claim_cannot_supply_its_own_independent_physical_reference(self):
        from generative_driver.benchmark import check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            prepared=prepare_stage('bme280','ground',root/'run',root/'worker',options={'binding':{'url':'ftdi://selected/1'},'configured_effects':['write']})
            self.assertIn('driver_respond',prepared['blocked'])
            checked=check_stage('bme280','ground',root/'run',root/'worker',
                {'status':'completed','reference':{'temperature':20}},options={'binding':{'url':'ftdi://selected/1'},'configured_effects':['write']})
            self.assertFalse(checked['ok'])
            self.assertIn('Operator reference missing',checked['reason'])

    def test_physical_probe_does_not_bypass_operator_grants_or_managed_dispatch(self):
        from generative_driver.benchmark import check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            calls=[]
            checked=check_stage('bme280','probe',root/'run',root/'worker',{'status':'completed'},
                options={'binding':{'url':'ftdi://selected/1'},'configured_effects':[],
                         'managed_probe':lambda *args:calls.append(args)})
            self.assertFalse(checked['ok'])
            self.assertEqual(calls,[])

    def test_probe_evaluator_consumes_managed_observation_not_worker_numbers(self):
        import json
        from generative_driver.benchmark import check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/'worker/model';model.mkdir(parents=True)
            (model/'model.json').write_text('{}');(model/'convert.py').write_text('# external transport fixture\n')
            caps={'temperature':'temperature_C','humidity':'humidity_RH','pressure':'pressure_Pa'}
            (root/'worker/capabilities.json').write_text(json.dumps(caps))
            (root/'run/benchmark').mkdir(parents=True)
            (root/'run/benchmark/state.json').write_text(json.dumps({'physical_model_dir':str(model)}))
            evidence=root/'scripted-device-observation.json';evidence.write_text('{"execution":"scripted-external-device-fixture"}')
            calls=[]
            def device_boundary(model_dir,n):
                calls.append((model_dir,n))
                return {'ok':True,'samples':[{'temperature_C':21.5,'humidity_RH':40.0,'pressure_Pa':101325.0}],
                        'probe':str(evidence),'evidence_kind':'scripted-external-device-fixture'}
            checked=check_stage('bme280','probe',root/'run',root/'worker',
                {'status':'completed','claimed_temperature':999},
                options={'binding':{'url':'ftdi://fixture/1'},'configured_effects':['write'],'managed_probe':device_boundary})
            self.assertTrue(checked['ok'],checked)
            self.assertEqual(len(calls),1)
            self.assertEqual(checked['evaluator']['samples'][0]['temperature_C'],21.5)
            self.assertIn(str(evidence),checked['artifacts'])

    def test_fresh_reuse_does_not_inherit_a_previous_attempts_success(self):
        import json
        from generative_driver.benchmark import check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state_dir=root/'run/benchmark';state_dir.mkdir(parents=True)
            (state_dir/'state.json').write_text(json.dumps({'capabilities':{'temperature':'t','humidity':'h','pressure':'p'},
                'package_attempts':{'reuse':'new-attempt'}}))
            (state_dir/'physical-package-events.jsonl').write_text(json.dumps({'stage':'reuse','attempt_id':'old-attempt',
                'ok':True,'values':{'t':21.5,'h':40,'p':101325}})+'\n')
            checked=check_stage('bme280','reuse',root/'run',root/'worker',{'status':'completed'})
            self.assertFalse(checked['ok'])

    def reference_fixture(self, root, age=0):
        import hashlib,json,time
        from generative_driver.benchmark_support.truth import seal
        resources=root/'resources';case=resources/'cases/bme280';case.mkdir(parents=True)
        password=root/'password';password.write_text('public toy fixture password')
        truth={'max_reference_age_seconds':60,'max_tolerances':{'temperature':.2,'humidity':1,'pressure':10}}
        encrypted=resources/'groundtruth/bme280.enc'
        digest=seal(truth,encrypted,password.read_text())
        (case/'case.json').write_text(json.dumps({'schema':'benchmark-case/1','id':'bme280','version':'3','evaluator_version':'3',
            'execution':'actual-agent-physical','scenarios':['original'],
            'required_stages':['acquire','interpret','probe','ground','emit','reuse'],
            'truth':{'path':'groundtruth/bme280.enc','sha256':digest}}))
        evidence=root/'reference.txt';evidence.write_text('Scripted independent reference; no physical observation claimed.')
        observed={'stage':'ground','channel':'scripted independent meter','observed_at':time.time()-age,
            'evidence_path':str(evidence),'evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
            'reference':{'temperature':{'value':21.5,'absolute_tolerance':.2},'humidity':{'value':40,'absolute_tolerance':1},
                         'pressure':{'value':101325,'absolute_tolerance':10}}}
        return resources,observed,{'evaluator_password_file':str(password)}

    def prepared_reuse_fixture(self, root):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import prepare_stage
        bench=root/'benchmark';bench.mkdir()
        resources,observed,options=self.reference_fixture(root)
        observed=dict(observed,stage='reuse');observed.pop('evidence_sha256')
        emitted=root/'emitted';emitted.mkdir();(emitted/'manifest.json').write_text('{}')
        (bench/'state.json').write_text(json.dumps({'package_dir':str(emitted),
            'capabilities':{'temperature':'t','humidity':'h','pressure':'p'}}))
        options.update(binding={'url':'ftdi://scripted/1'},configured_effects=['write'],operator_observations=[observed])
        with patch('generative_driver.benchmark.case_root',return_value=resources):
            prepared=prepare_stage('bme280','reuse',root,root/'worker',options=options)
        self.assertNotIn('blocked',prepared)
        state=json.loads((bench/'state.json').read_text())
        return resources,observed,options,state

    def write_measurement(self, root, attempt, measured):
        import json
        (root/'benchmark/physical-package-events.jsonl').write_text(json.dumps({'stage':'reuse','attempt_id':attempt,
            'ok':True,'time':measured,'values':{'t':21.5,'h':40,'p':101325}})+'\n')

    def test_prepared_reference_survives_changed_or_deleted_original_evidence(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        for mutation in ('changed','deleted'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
                saved=state['physical_reuse_reference']
                source=Path(observed['evidence_path'])
                if mutation=='changed':source.write_text('Changed original, not the saved independent evidence.')
                else:source.unlink()
                self.write_measurement(root,state['package_attempts']['reuse'],observed['observed_at'])
                with patch('generative_driver.benchmark.case_root',return_value=resources):
                    checked=check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)
                self.assertTrue(checked['ok'],checked)
                final=json.loads((root/'benchmark/state.json').read_text())['physical_final']
                self.assertEqual(final['reference'],saved)
                self.assertEqual(final['measurement_time'],observed['observed_at'])

    def test_final_uses_event_time_even_when_grading_is_delayed(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        for offset,expected in ((-120,False),(0,True)):
            with self.subTest(offset=offset),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
                bench=root/'benchmark';measured=observed['observed_at']+offset
                self.write_measurement(root,state['package_attempts']['reuse'],measured)
                with patch('generative_driver.benchmark.case_root',return_value=resources),patch(
                        'generative_driver.benchmark_support.physical.time.time',return_value=observed['observed_at']+(120 if offset==0 else 0)):
                    checked=check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)
                self.assertEqual(checked['ok'],expected,checked)
                if expected:
                    final=json.loads((bench/'state.json').read_text())['physical_final']
                    self.assertEqual(final['measurement_time'],measured)
                    from generative_driver.benchmark_support.legacy import score
                    report={'schema':'benchmark-report/1','case':'bme280','case_version':'3','evaluator_version':'3',
                        'workflow_status':'completed','stages':{stage:{'workflow_status':'accepted','evaluator_status':'passed'}
                            for stage in ('acquire','interpret','probe','ground','emit','reuse')},
                        'case_state':{'physical_final':final}}
                    with patch('generative_driver.benchmark_support.legacy.case_root',return_value=resources):
                        self.assertEqual(score(report,options['evaluator_password_file'])['verdict'],'passed')

    def test_fresh_measurement_must_match_independent_reference(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        for temperature, expected in ((21.5, True), (999, False)):
            with self.subTest(temperature=temperature), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
                bench=root/'benchmark'
                (bench/'physical-package-events.jsonl').write_text(json.dumps({'stage':'reuse',
                    'attempt_id':state['package_attempts']['reuse'],'time':observed['observed_at'],
                    'ok':True,'values':{'t':temperature,'h':40,'p':101325}})+'\n')
                with patch('generative_driver.benchmark.case_root',return_value=resources):
                    checked=check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)
                self.assertEqual(checked['ok'],expected)

    def test_stale_reference_blocks_reuse_until_a_new_independent_reference_arrives(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import prepare_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bench=root/'benchmark';bench.mkdir()
            resources,observed,options=self.reference_fixture(root,age=120)
            (bench/'state.json').write_text(json.dumps({'physical_grounding':{'reference':observed}}))
            options.update(binding={'url':'ftdi://scripted/1'},configured_effects=['write'])
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                prepared=prepare_stage('bme280','reuse',root,root/'worker',options=options)
            self.assertIn('blocked',prepared)
            self.assertIn('driver_respond',prepared['blocked'])
            import time
            emitted=root/'emitted';emitted.mkdir();(emitted/'manifest.json').write_text('{}')
            (bench/'state.json').write_text(json.dumps({'package_dir':str(emitted),'physical_grounding':{'reference':observed}}))
            options['operator_observations']=[dict(observed,stage='reuse',observed_at=time.time())]
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                prepared=prepare_stage('bme280','reuse',root,root/'fresh-worker',options=options)
            self.assertNotIn('blocked',prepared)
            self.assertEqual(len(prepared['inputs']),1)
            self.assertEqual(Path(prepared['inputs'][0]).name,'package')

    def test_stale_reference_cannot_grade_finite_final_values(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
            self.write_measurement(root,state['package_attempts']['reuse'],observed['observed_at']+120)
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                checked=check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)
            self.assertFalse(checked['ok'])
            self.assertEqual(checked['fault'],'operator')

    def test_reuse_requires_a_finite_event_timestamp(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        for measured in (None,True,float('nan'),float('inf')):
            with self.subTest(measured=measured),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
                self.write_measurement(root,state['package_attempts']['reuse'],measured)
                with patch('generative_driver.benchmark.case_root',return_value=resources):
                    checked=check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)
                self.assertFalse(checked['ok'])

    def test_new_reference_requires_new_preparation_and_preserves_old_copy(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import prepare_stage,check_stage
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
            saved=state['physical_reuse_reference'];old_attempt=state['package_attempts']['reuse']
            original_copy=Path(saved['evidence_path']).read_bytes()
            new=dict(observed,reference={**observed['reference'],'temperature':{'value':999,'absolute_tolerance':.2}})
            options['operator_observations'].append(new)
            self.write_measurement(root,old_attempt,observed['observed_at'])
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                self.assertTrue(check_stage('bme280','reuse',root,root/'worker',{},options=options)['ok'])
                prepared=prepare_stage('bme280','reuse',root,root/'new-worker',options=options)
                self.assertNotIn('blocked',prepared)
                self.assertFalse(check_stage('bme280','reuse',root,root/'new-worker',{},options=options)['ok'])
            updated=json.loads((root/'benchmark/state.json').read_text())
            self.assertNotEqual(updated['package_attempts']['reuse'],old_attempt)
            self.assertNotEqual(updated['physical_reuse_reference']['evidence_path'],saved['evidence_path'])
            self.assertEqual(Path(saved['evidence_path']).read_bytes(),original_copy)
            self.assertEqual(updated['physical_reuse_reference']['reference']['temperature']['value'],999)

    def test_offline_grade_authenticates_the_saved_final_reference_digest(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        from generative_driver.benchmark_support.legacy import score
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources,observed,options,state=self.prepared_reuse_fixture(root)
            Path(observed['evidence_path']).unlink()
            self.write_measurement(root,state['package_attempts']['reuse'],observed['observed_at'])
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                self.assertTrue(check_stage('bme280','reuse',root,root/'worker',{},options=options)['ok'])
            final=json.loads((root/'benchmark/state.json').read_text())['physical_final']
            self.assertEqual(final['reference']['evidence_sha256'],state['physical_reuse_reference']['evidence_sha256'])
            report={'schema':'benchmark-report/1','case':'bme280','case_version':'3','evaluator_version':'3',
                'workflow_status':'completed','stages':{s:{'workflow_status':'accepted','evaluator_status':'passed'}
                    for s in ('acquire','interpret','probe','ground','emit','reuse')},'case_state':{'physical_final':final}}
            with patch('generative_driver.benchmark_support.legacy.case_root',return_value=resources):
                self.assertEqual(score(report,options['evaluator_password_file'])['verdict'],'passed')
                Path(final['reference']['evidence_path']).write_text('Tampered saved copy')
                with self.assertRaisesRegex(ValueError,'reference'):
                    score(report,options['evaluator_password_file'])

    def test_offline_physical_grade_rechecks_final_values_and_measurement_reference_age(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support.legacy import score
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources,observed,options=self.reference_fixture(root)
            report={'schema':'benchmark-report/1','case':'bme280','case_version':'3','evaluator_version':'3',
                'workflow_status':'completed','stages':{s:{'workflow_status':'accepted','evaluator_status':'passed'}
                    for s in ('acquire','interpret','probe','ground','emit','reuse')},
                'case_state':{'physical_final':{'reference':observed,'measurement_time':observed['observed_at'],
                    'observations':{'temperature':21.5,'humidity':40,'pressure':101325}}}}
            with patch('generative_driver.benchmark_support.legacy.case_root',return_value=resources):
                self.assertEqual(score(report,options['evaluator_password_file'])['verdict'],'passed')
                report['case_state']['physical_final']['observations']['temperature']=999
                self.assertEqual(score(report,options['evaluator_password_file'])['verdict'],'failed')
                report['case_state']['physical_final']['observations']['temperature']=21.5
                report['case_state']['physical_final']['measurement_time']=observed['observed_at']+120
                with self.assertRaisesRegex(ValueError,'reference'):
                    score(report,options['evaluator_password_file'])

    def test_exported_physical_report_scores_with_explicit_reference_from_another_directory(self):
        # Catches losing the private reference path at export without a portable resolver.
        import contextlib, io, json, os, shutil
        from unittest.mock import patch
        from generative_driver.reporting import report_run
        from generative_driver.benchmark import main
        from generative_driver.benchmark_support.physical import check_stage
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);home=base/'private-home';root=home/'runs/toy';root.mkdir(parents=True)
            resources,observed,options,state=self.prepared_reuse_fixture(root)
            self.write_measurement(root,state['package_attempts']['reuse'],observed['observed_at'])
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                self.assertTrue(check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)['ok'])
            state=json.loads((root/'benchmark/state.json').read_text())
            stages=('acquire','interpret','probe','ground','emit','reuse')
            state['stage_verdicts']={s:{'status':'passed'} for s in stages}
            (root/'benchmark/state.json').write_text(json.dumps(state))
            result={'ok':True,'run_id':'toy','status':'completed','created':0,'updated':1,
                    'configuration':{'case':'bme280','limits':{'max_model_repairs':2}},
                    'worker_reports':[{'stage':s,'assignment_id':s,'status':'completed'} for s in stages],
                    'accepted_handoffs':[{'stage':s,'assignment_id':s,'artifacts':[]} for s in stages]}
            def saved_client(method,*args,**kwargs):
                return result if method=='result' else {'events':[],'cursor':0}
            output=base/'export/report.json'
            with patch('generative_driver.client.call',side_effect=saved_client), patch('generative_driver.benchmark.case_root',return_value=resources):
                public=report_run('toy',home=home,output=output,autostart=False)
            self.assertNotIn(str(base),output.read_text())
            saved=Path(state['physical_final']['reference']['evidence_path'])
            portable=base/'evaluator/reference.txt';portable.parent.mkdir();shutil.copy2(saved,portable)
            elsewhere=base/'elsewhere';elsewhere.mkdir();previous=Path.cwd()
            try:
                os.chdir(elsewhere)
                def invoke(evidence):
                    capture=io.StringIO()
                    args=['score',str(output),'--password-file',options['evaluator_password_file']]
                    if evidence is not None:args+=['--evidence',str(evidence)]
                    with patch('generative_driver.benchmark_support.legacy.case_root',return_value=resources), contextlib.redirect_stdout(capture):
                        status=main(args)
                    self.assertNotIn(str(base),capture.getvalue())
                    return status,json.loads(capture.getvalue())
                status,score=invoke(portable)
                self.assertEqual(status,0,score);self.assertEqual(score['verdict'],'passed')
                self.assertEqual(invoke(None)[0],1)
                self.assertEqual(invoke(base/'missing.txt')[0],1)
                portable.write_text('tampered')
                self.assertEqual(invoke(portable)[0],1)
                shutil.copy2(saved,portable)
                public['case_state']['physical_final']['measurement_time']=observed['observed_at']+120
                output.write_text(json.dumps(public))
                self.assertEqual(invoke(portable)[0],1)
            finally:
                os.chdir(previous)

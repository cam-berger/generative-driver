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

    def test_fresh_measurement_must_match_independent_reference(self):
        import json
        from unittest.mock import patch
        from generative_driver.benchmark_support.physical import check_stage
        for temperature, expected in ((21.5, True), (999, False)):
            with self.subTest(temperature=temperature), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);bench=root/'benchmark';bench.mkdir()
                resources,observed,options=self.reference_fixture(root)
                (bench/'state.json').write_text(json.dumps({'capabilities':{'temperature':'t','humidity':'h','pressure':'p'},
                    'package_attempts':{'reuse':'current'},'physical_grounding':{'reference':observed}}))
                (bench/'physical-package-events.jsonl').write_text(json.dumps({'stage':'reuse','attempt_id':'current',
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
            root=Path(directory);bench=root/'benchmark';bench.mkdir()
            resources,observed,options=self.reference_fixture(root,age=120)
            (bench/'state.json').write_text(json.dumps({'capabilities':{'temperature':'t','humidity':'h','pressure':'p'},
                'package_attempts':{'reuse':'current'},'physical_grounding':{'reference':observed}}))
            (bench/'physical-package-events.jsonl').write_text(json.dumps({'stage':'reuse','attempt_id':'current','ok':True,
                'values':{'t':21.5,'h':40,'p':101325}})+'\n')
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                checked=check_stage('bme280','reuse',root,root/'worker',{'status':'completed'},options=options)
            self.assertFalse(checked['ok'])
            self.assertEqual(checked['fault'],'operator')

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

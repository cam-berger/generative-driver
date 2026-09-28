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

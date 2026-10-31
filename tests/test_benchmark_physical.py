"""The physical bme280 profile, observed without truth: its stages hand on what the worker and the configurator observed,
and after the run the evaluator grades the recorded fresh measurement of the frozen package against the operator's
independent reference within the truth's limits. A scripted measurement, a toy reference and a sealed toy truth only;
never a physical device."""
import hashlib
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark import prepare_stage

BINDING = {'url': 'ftdi://scripted/1'}
MAPPING = {'temperature': 't', 'humidity': 'h', 'pressure': 'p'}
STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')


def handoff(stage, paths, assignment=None):
    from generative_driver.configurator import digest
    return {'stage': stage, 'assignment_id': assignment or stage, 'revision': 0,
            'artifacts': [{'path': str(path), 'sha256': digest(path)} for path in paths]}


def reference_fixture(root, age=0):
    """A toy bme280 case whose toy truth is sealed under a synthetic credential, and an operator reference for reuse:
    (resources root, observation, evaluator options)."""
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
    observed={'stage':'reuse','channel':'scripted independent meter','observed_at':time.time()-age,
        'evidence_path':str(evidence),
        'reference':{'temperature':{'value':21.5,'absolute_tolerance':.2},'humidity':{'value':40,'absolute_tolerance':1},
                     'pressure':{'value':101325,'absolute_tolerance':10}}}
    return resources,observed,{'evaluator_password_file':str(password)}


class PhysicalPrerequisitesTests(unittest.TestCase):
    def test_physical_profile_requests_explicit_binding_before_agent_or_io(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare_stage('bme280','acquire',Path(directory)/'run',Path(directory)/'worker')
            self.assertIn('blocked',prepared)
            self.assertIn('binding',prepared['blocked'].lower())
            self.assertNotIn('unsupported',prepared['blocked'].lower())

    def test_physical_probe_worker_cannot_validate_its_sealed_register_model_in_place(self):
        """The register validator writes defects.json beside the model it checks, which would alter the probe stage's
        sealed input and stop the run for integrity; the configurator validates each assigned model on its own copy."""
        from generative_driver.benchmark_support.physical import prepare_stage as physical_prepare
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/'interpreted';model.mkdir()
            (model/'model.json').write_text(json.dumps({'schema':'interface-model/1'}));(model/'convert.py').write_text('')
            prepared=physical_prepare('bme280','probe',root,root/'worker',accepted=[handoff('interpret',[model/'model.json',model/'convert.py'])],
                options={'binding':BINDING,'configured_effects':['write']})
            self.assertEqual(prepared['inputs'],[str((root/'worker'/'model').resolve())])
            self.assertEqual((root/'worker/model/model.json').read_bytes(),(model/'model.json').read_bytes())
            self.assertIn('probe_run',prepared['allowed_tools'])
            self.assertNotIn('model_validate',prepared['allowed_tools'])

    def test_physical_probe_does_not_bypass_operator_grants_or_reach_the_device_itself(self):
        # Catches a physical stage that issues a device action of its own (the managed probe it once had) or runs
        # without the operator's write grant.
        from generative_driver.benchmark_support.physical import observe_stage, prepare_stage as physical_prepare
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/'interpreted';model.mkdir()
            (model/'model.json').write_text('{}');(model/'convert.py').write_text('')
            accepted=[handoff('interpret',[model/'model.json',model/'convert.py'])]
            blocked=physical_prepare('bme280','probe',root/'run',root/'worker',accepted=accepted,options={'binding':BINDING,'configured_effects':[]})
            self.assertIn('write effect grant',blocked['blocked'])
            self.assertFalse((root/'worker/model').exists())
            def device(*args,**kwargs):raise AssertionError('a stage observation reached the device')
            (root/'worker').mkdir(exist_ok=True);(root/'worker/capabilities.json').write_text(json.dumps(MAPPING))
            with patch('generative_driver.benchmark_support.physical._measure',side_effect=device), \
                 patch('generative_driver.toolkit.call_tool',side_effect=device):
                checked=observe_stage('bme280','probe',root/'run',root/'worker',{'status':'completed'},accepted,
                                      {'binding':BINDING,'configured_effects':['write'],'managed_probe':device})
            self.assertEqual(checked['checks'],[{'name':'capability_mapping','passed':True}])
            for key in ('ok','fault','route','stop','final_evaluation','evaluator'):self.assertNotIn(key,checked)


class RecordedReuseTests(unittest.TestCase):
    """The reuse stage prepared against the operator reference and observed after a scripted measurement, then graded
    by the evaluator after the run from what the stage handed on."""

    def reference_fixture(self, root, age=0):
        return reference_fixture(root, age)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.resources,self.observed,self.secret=self.reference_fixture(self.root)
        for target in ('generative_driver.benchmark.case_root','generative_driver.benchmark_support.case_assets.case_root'):
            guard=patch(target,return_value=self.resources);guard.start();self.addCleanup(guard.stop)
        from generative_driver.benchmark_support.registry import pin_case
        from generative_driver.benchmark_support.snapshots import snapshot_case
        self.run_dir=self.root/'run';self.pin=pin_case('bme280',None,0)
        self.snapshot=snapshot_case(self.run_dir,self.pin,{})
        accepted=self.root/'accepted';(accepted/'probe').mkdir(parents=True)
        for name,content in (('model.json','{}'),('convert.py','# scripted converter\n'),('capabilities.json',json.dumps(MAPPING))):
            (accepted/'probe'/name).write_text(content)
        package=accepted/'emit/0-package';(package/'driver').mkdir(parents=True)
        (package/'manifest.json').write_text('{"name":"scripted"}');(package/'driver/model.json').write_text('{}')
        self.accepted=[handoff('probe',[accepted/'probe'/n for n in ('model.json','convert.py','capabilities.json')]),
                       handoff('emit',[package])]
        self.options={'binding':BINDING,'configured_effects':['write'],'operator_observations':[self.observed]}

    def prepare(self, workspace='worker', observations=None):
        from generative_driver.benchmark_support.physical import prepare_stage as physical_prepare
        options={**self.options,**({'operator_observations':observations} if observations is not None else {})}
        prepared=physical_prepare('bme280','reuse',self.run_dir,self.root/workspace,accepted=self.accepted,options=options)
        return prepared,json.loads((self.run_dir/'benchmark/state.json').read_text()) if (self.run_dir/'benchmark/state.json').is_file() else {}

    def measure(self, state, measured, values=None, assignment='reuse-1', attempt=None):
        with (self.run_dir/'benchmark/physical-package-events.jsonl').open('a') as stream:
            stream.write(json.dumps({'stage':'reuse','attempt_id':attempt or state['package_attempts']['reuse'],
                'assignment_id':assignment,'ok':True,'time':measured,'values':values or {'t':21.5,'h':40,'p':101325}})+'\n')

    def observe(self, workspace='worker', assignment='reuse-1', report=None):
        from generative_driver.benchmark_support.physical import observe_stage
        checked=observe_stage('bme280','reuse',self.run_dir,self.root/workspace,report or {'status':'completed'},self.accepted,
                              {**self.options,'assignment_id':assignment})
        return checked,handoff('reuse',[Path(p) for p in checked['artifacts']],assignment)

    def grade(self, reuse, protocol='assistive/1'):
        from generative_driver.benchmark_support import recorded_grading
        result={'run_id':'run-physical','case_pin':self.pin,'snapshot_sha256':self.snapshot['snapshot_sha256'],'status':'completed',
                'progress':{'equivalence':'pending','protocol':protocol},'accepted_handoffs':[*self.accepted,reuse]}
        trial=self.root/'trial';trial.mkdir(exist_ok=True)
        with patch('generative_driver.benchmark_support.physical._measure',side_effect=AssertionError('the evaluator touched the board')):
            return recorded_grading.grade('bme280',trial,self.run_dir,result,password_file=self.secret['evaluator_password_file'],
                                          protocol=result['progress']['protocol'])

    def test_the_recorded_measurement_is_graded_against_the_operator_reference_after_the_run(self):
        prepared,state=self.prepare()
        self.assertEqual([Path(p).name for p in prepared['inputs']],['package'])
        self.measure(state,self.observed['observed_at'])
        checked,reuse=self.observe()
        self.assertEqual([c['name'] for c in checked['checks']],['fresh_physical_measurement','operator_reference'])
        self.assertTrue(all(c['passed'] for c in checked['checks']),checked)
        record,final=self.grade(reuse)
        self.assertEqual((record['status'],record['equivalent'],record['method']),('graded',True,'recorded-reuse'))
        self.assertEqual([c['id'] for c in record['checks']],['temperature','humidity','pressure'])
        self.assertEqual(final['raw']['measurement']['time'],self.observed['observed_at'])
        for forbidden in ('101325','40','21.5','tolerance'):
            self.assertNotIn(forbidden,json.dumps(record))

    def test_prepared_reference_survives_changed_or_deleted_original_evidence(self):
        for mutation in ('changed','deleted'):
            with self.subTest(mutation=mutation):
                prepared,state=self.prepare('worker-'+mutation)
                saved=state['physical_reuse_reference']
                source=Path(self.observed['evidence_path'])
                if mutation=='changed':source.write_text('Changed original, not the saved independent evidence.')
                else:source.unlink()
                self.measure(state,self.observed['observed_at'],assignment='reuse-'+mutation)
                _,reuse=self.observe('worker-'+mutation,'reuse-'+mutation)
                record,final=self.grade(reuse)
                self.assertTrue(record['equivalent'],record)
                self.assertEqual(final['raw']['reference']['evidence_sha256'],saved['evidence_sha256'])
                self.assertEqual(final['raw']['measurement']['time'],self.observed['observed_at'])
                Path(self.observed['evidence_path']).write_text('Scripted independent reference; no physical observation claimed.')
                (self.root/'trial/evaluator-draft.enc').unlink()

    def test_final_uses_event_time_even_when_grading_is_delayed(self):
        from generative_driver.benchmark_support.recorded_grading import Unscorable
        for offset,expected in ((-120,False),(0,True)):
            with self.subTest(offset=offset):
                prepared,state=self.prepare('worker'+str(offset))
                measured=self.observed['observed_at']+offset
                self.measure(state,measured,assignment='reuse'+str(offset))
                _,reuse=self.observe('worker'+str(offset),'reuse'+str(offset))
                with patch('generative_driver.benchmark_support.recorded_grading.time.time',return_value=self.observed['observed_at']+(120 if offset==0 else 0)):
                    if expected:
                        record,final=self.grade(reuse)
                        self.assertTrue(record['equivalent'],record)
                        self.assertEqual(final['raw']['measurement']['time'],measured)
                    else:
                        with self.assertRaisesRegex(Unscorable,'stale'):
                            self.grade(reuse)

    def test_fresh_measurement_must_match_independent_reference(self):
        for temperature, expected in ((21.5, True), (999, False)):
            with self.subTest(temperature=temperature):
                prepared,state=self.prepare('worker'+str(temperature))
                self.measure(state,self.observed['observed_at'],{'t':temperature,'h':40,'p':101325},assignment='reuse'+str(temperature))
                _,reuse=self.observe('worker'+str(temperature),'reuse'+str(temperature))
                record,_=self.grade(reuse)
                self.assertEqual(record['equivalent'],expected)
                if not expected:
                    self.assertEqual(record['diff'],[{'id':'temperature','reason':'value mismatch'}])
                (self.root/'trial/evaluator-draft.enc').unlink(missing_ok=True)

    def test_worker_claim_cannot_supply_its_own_independent_physical_reference(self):
        # Catches a reference taken from the worker's report, or grounding blocked on a reference: ground runs with
        # operator observations where given, and fresh reuse waits for the operator's own reference.
        from generative_driver.benchmark_support.physical import observe_stage, prepare_stage as physical_prepare
        ground=physical_prepare('bme280','ground',self.run_dir,self.root/'ground',accepted=self.accepted,
                                options={'binding':BINDING,'configured_effects':['write']})
        self.assertNotIn('blocked',ground)
        self.assertTrue(any('No operator observation' in note for note in ground['input_notes']))
        prepared,_=self.prepare(observations=[])
        self.assertIn('driver_respond',prepared['blocked'])
        claimed={'status':'completed','reference':{'temperature':20},'summary':'measured against my own meter'}
        checked=observe_stage('bme280','reuse',self.run_dir,self.root/'worker',claimed,self.accepted,{**self.options,'assignment_id':'reuse-1'})
        self.assertEqual(checked['checks'][1],{'name':'operator_reference','passed':False,'reason':'No operator reference for this reuse'})
        self.assertFalse([p for p in checked['artifacts'] if Path(p).name in ('reference.json','reference.txt')])

    def test_the_grade_takes_the_recorded_measurement_never_the_workers_numbers(self):
        prepared,state=self.prepare()
        self.measure(state,self.observed['observed_at'])
        _,reuse=self.observe(report={'status':'completed','summary':'temperature 999','values':{'t':999}})
        record,final=self.grade(reuse)
        self.assertTrue(record['equivalent'],record)
        self.assertEqual(final['records'][0]['value'],21.5)

    def test_fresh_reuse_does_not_inherit_a_previous_attempts_success(self):
        prepared,state=self.prepare()
        # A success of another attempt and another assignment stays out of this reuse's record.
        self.measure(state,self.observed['observed_at'],attempt='old-attempt')
        self.measure(state,self.observed['observed_at'],assignment='reuse-old')
        checked,reuse=self.observe()
        self.assertFalse(checked['checks'][0]['passed'])
        record,final=self.grade(reuse)
        self.assertIsNone(final)
        self.assertEqual((record['equivalent'],[d['id'] for d in record['diff']]),(False,['fresh_physical_measurement']))

    def test_stale_reference_waits_for_a_reuse_reference_from_the_operator(self):
        # Catches fresh reuse prepared against an earlier stage's reference: only the operator's reference for this
        # reuse counts, and its age is judged against the measurement after the run.
        stale=dict(self.observed,stage='ground',observed_at=time.time()-120)
        prepared,_=self.prepare(observations=[stale])
        self.assertIn('blocked',prepared)
        self.assertIn('driver_respond',prepared['blocked'])
        prepared,_=self.prepare('fresh-worker',observations=[stale,dict(self.observed,observed_at=time.time())])
        self.assertNotIn('blocked',prepared)
        self.assertEqual(len(prepared['inputs']),1)
        self.assertEqual(Path(prepared['inputs'][0]).name,'package')

    def test_stale_reference_cannot_grade_finite_final_values(self):
        from generative_driver.benchmark_support.recorded_grading import Unscorable
        prepared,state=self.prepare()
        self.measure(state,self.observed['observed_at']+120)
        _,reuse=self.observe()
        with self.assertRaisesRegex(Unscorable,'operator reference'):
            self.grade(reuse)
        self.assertFalse((self.root/'trial/evaluator-draft.enc').exists())

    def test_a_recorded_grade_names_the_runs_protocol_and_a_run_naming_none_is_not_graded(self):
        # Catches a recorded-reuse grade stamped with no protocol, as the evaluator's own grade refuses one.
        prepared,state=self.prepare()
        self.measure(state,self.observed['observed_at'])
        _,reuse=self.observe()
        for protocol in (None,''):
            with self.subTest(protocol=protocol),self.assertRaisesRegex(ValueError,'protocol'):
                self.grade(reuse,protocol)
        self.assertFalse((self.root/'trial/evaluator-draft.enc').exists())
        self.assertEqual(self.grade(reuse,'learning/1')[0]['protocol'],'learning/1')

    def test_reuse_requires_a_finite_event_timestamp(self):
        for measured in (None,True,float('nan'),float('inf')):
            with self.subTest(measured=measured):
                prepared,state=self.prepare('worker'+str(measured))
                self.measure(state,measured,assignment='reuse'+str(measured))
                _,reuse=self.observe('worker'+str(measured),'reuse'+str(measured))
                record,final=self.grade(reuse)
                self.assertIsNone(final)
                self.assertFalse(record['equivalent'])

    def test_new_reference_requires_new_preparation_and_preserves_old_copy(self):
        prepared,state=self.prepare()
        saved=state['physical_reuse_reference'];old_attempt=state['package_attempts']['reuse']
        original_copy=Path(saved['evidence_path']).read_bytes()
        new=dict(self.observed,reference={**self.observed['reference'],'temperature':{'value':999,'absolute_tolerance':.2}})
        self.measure(state,self.observed['observed_at'])
        _,reuse=self.observe()
        self.assertTrue(self.grade(reuse)[0]['equivalent'])
        prepared,updated=self.prepare('new-worker',observations=[self.observed,new])
        self.assertNotIn('blocked',prepared)
        _,fresh=self.observe('new-worker','reuse-2')
        (self.root/'trial/evaluator-draft.enc').unlink()
        self.assertFalse(self.grade(fresh)[0]['equivalent'])
        self.assertNotEqual(updated['package_attempts']['reuse'],old_attempt)
        self.assertNotEqual(updated['physical_reuse_reference']['evidence_path'],saved['evidence_path'])
        self.assertEqual(Path(saved['evidence_path']).read_bytes(),original_copy)
        self.assertEqual(updated['physical_reuse_reference']['reference']['temperature']['value'],999)


class EarlierProtocolScoreTests(unittest.TestCase):
    """Reports recorded under the earlier protocol, whose physical final was graded inside the run, regrade as before."""

    def final(self, root, observed):
        """The physical final such a run recorded: the measured values, the saved copy of the reference evidence with its
        digest, and the measurement time."""
        import shutil
        copy=root/'saved-reference.txt';shutil.copy2(observed['evidence_path'],copy)
        return {'observations':{'temperature':21.5,'humidity':40,'pressure':101325},'measurement_time':observed['observed_at'],
                'reference':{**observed,'evidence_path':str(copy),'evidence_sha256':hashlib.sha256(copy.read_bytes()).hexdigest()}}

    def report(self, final):
        return {'schema':'benchmark-report/1','case':'bme280','case_version':'3','evaluator_version':'3',
            'workflow_status':'completed','stages':{s:{'workflow_status':'accepted','evaluator_status':'passed'} for s in STAGES},
            'case_state':{'physical_final':final}}

    def test_offline_grade_authenticates_the_saved_final_reference_digest(self):
        from generative_driver.benchmark_support.legacy import score
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources,observed,options=RecordedReuseTests.reference_fixture(self,root)
            final=self.final(root,observed);Path(observed['evidence_path']).unlink()
            report=self.report(final)
            with patch('generative_driver.benchmark_support.legacy.case_root',return_value=resources):
                self.assertEqual(score(report,options['evaluator_password_file'])['verdict'],'passed')
                Path(final['reference']['evidence_path']).write_text('Tampered saved copy')
                with self.assertRaisesRegex(ValueError,'reference'):
                    score(report,options['evaluator_password_file'])

    def test_offline_physical_grade_rechecks_final_values_and_measurement_reference_age(self):
        from generative_driver.benchmark_support.legacy import score
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);resources,observed,options=RecordedReuseTests.reference_fixture(self,root)
            observed['evidence_sha256']=hashlib.sha256(Path(observed['evidence_path']).read_bytes()).hexdigest()
            report=self.report({'reference':observed,'measurement_time':observed['observed_at'],
                    'observations':{'temperature':21.5,'humidity':40,'pressure':101325}})
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
        import contextlib, io, os, shutil
        from generative_driver.reporting import report_run
        from generative_driver.benchmark import main
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);home=base/'private-home';root=home/'runs/toy';(root/'benchmark').mkdir(parents=True)
            resources,observed,options=RecordedReuseTests.reference_fixture(self,root)
            final=self.final(root,observed)
            state={'physical_final':final,'stage_verdicts':{s:{'status':'passed'} for s in STAGES}}
            (root/'benchmark/state.json').write_text(json.dumps(state))
            result={'ok':True,'run_id':'toy','status':'completed','created':0,'updated':1,
                    'configuration':{'case':'bme280','limits':{'max_model_repairs':2}},
                    'worker_reports':[{'stage':s,'assignment_id':s,'status':'completed'} for s in STAGES],
                    'accepted_handoffs':[{'stage':s,'assignment_id':s,'artifacts':[]} for s in STAGES]}
            def saved_client(method,*args,**kwargs):
                return result if method=='result' else {'events':[],'cursor':0}
            output=base/'export/report.json'
            with patch('generative_driver.client.call',side_effect=saved_client), patch('generative_driver.benchmark.case_root',return_value=resources), \
                    patch('generative_driver.benchmark_support.case_assets.case_root',return_value=resources):
                public=report_run('toy',home=home,output=output,autostart=False)
            self.assertNotIn(str(base),output.read_text())
            saved=Path(final['reference']['evidence_path'])
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


if __name__ == '__main__':
    unittest.main()

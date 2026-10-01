"""Synthetic unrelated inventories cannot qualify distributed cases."""
import base64
import copy
import hashlib
import unittest
from generative_driver.benchmark_support.asset_build import inventory
from generative_driver.benchmark_support.snapshots import canonical_digest
from generative_driver.benchmark_support.calibration import decode_inventory

class InventoryTests(unittest.TestCase):
    def fixture(self):
        entries = {name: {'sha256': hashlib.sha256(b'{}').hexdigest(),
                          'base64': base64.b64encode(b'{}').decode()}
                   for name in inventory('sampled-sensor')}
        return {'schema': 'benchmark-family-truth/1', 'case_id': 'sampled-sensor-v1',
                'family': 'sampled-sensor', 'inventory_state': 'pending', 'inventory': entries,
                'inventory_sha256': canonical_digest({k:v['sha256'] for k,v in entries.items()})}

    def test_exact_authenticated_inventory_decodes(self):
        self.assertEqual(set(decode_inventory(self.fixture())), inventory('sampled-sensor'))

    def test_invalid_inventory_cannot_be_used(self):
        for defect in ('extra','missing','base64','hash','commitment','state','entry'):
            value=self.fixture()
            if defect=='extra': value['inventory']['../escape']={'sha256':'a'*64,'base64':'e30='}
            if defect=='missing': value['inventory'].pop('LICENSE')
            if defect=='base64': value['inventory']['LICENSE']['base64']='e30=!!'
            if defect=='hash': value['inventory']['LICENSE']['sha256']='a'*64
            if defect=='commitment': value['inventory_sha256']='a'*64
            if defect=='state': value['inventory_state']='other'
            if defect=='entry': value['inventory']['LICENSE']['extra']=True
            with self.subTest(defect=defect), self.assertRaises(ValueError): decode_inventory(value)

    def test_qualified_inventory_adds_only_record(self):
        value=self.fixture(); value['inventory_state']='qualified'
        with self.assertRaises(ValueError): decode_inventory(value)
        value['inventory']['calibration/calibration.json']=copy.deepcopy(value['inventory']['LICENSE'])
        value['inventory_sha256']=canonical_digest({k:v['sha256'] for k,v in value['inventory'].items()})
        self.assertIn('calibration/calibration.json',decode_inventory(value))

class AdmissionTests(unittest.TestCase):
    def fixture(self):
        from generative_driver.benchmark_support.reference_calibration import calibration_identity
        h='a'*64
        identity=calibration_identity()
        runtime=canonical_digest({k.split('/',1)[1]:v for k,v in identity['files'].items() if k.startswith('interface_runtime/') and k.count('/')==1})
        runs=[]
        refs=[]
        for ref in ('a','b'):
            ids=[]
            for phase,scenario in (('diagnostic','original'),('final','original'),('maintenance','semantic'),('maintenance','control')):
                name=ref+'-'+phase+'-'+scenario; ids.append(name)
                runs.append(dict(id=name, phase=phase, scenario=scenario, passed=True,
                    native_process_observed=True, process={'pid':123,'executable':'/toy/renode','image_sha256':('b'*64 if scenario=='semantic' else h)},
                    image_sha256=('b'*64 if scenario=='semantic' else h), evidence_sha256=h, contract_sha256=h, model_sha256=(ref*64), capabilities_sha256=(ref*64),
                    package_sha256=h if phase=='final' else None, runtime_sha256=runtime, check_ids=['toy/ok'], checks=[{'id':'toy/ok','passed':True}]))
            refs.append(dict(id=ref,model_sha256=('b' if ref=='b' else 'a')*64,passed=True,runs=ids,execution_inputs={r['id']:{k:r[k] for k in ('model_sha256','capabilities_sha256')} for r in runs if r['id'] in ids}))
        mutants=[]
        for name in ('constant','scale','signedness','stale','false-drift'):
            run=copy.deepcopy(runs[0]); run.update(id=name,passed=False,check_ids=['toy/'+name],checks=[{'id':'toy/'+name,'passed':False,'reason':'value mismatch'}]); runs.append(run)
            mutants.append(dict(id=name,rejected=True,run=name,source_sha256=h,mutated_sha256='b'*64,
                expected_failed_checks=['toy/'+name],failed_checks=['toy/'+name],execution_input={k:run[k] for k in ('model_sha256','capabilities_sha256')}))
        false=mutants[-1];false.update(expected_failed_checks=['maintenance/false-drift'],failed_checks=['maintenance/false-drift'],
            source_assessment_sha256=canonical_digest({'maintenance_claim':'unchanged'}),
            mutated_assessment_sha256=canonical_digest({'maintenance_claim':'drift'}))
        runs[-1].update(passed=True,check_ids=['toy/ok'],phase='maintenance',scenario='control',native_control_passed=True,assessment_decision={'ok':False,'fault':None,'route':None,'reason':'Control maintenance claim failed','maintenance':{'false_alarm':True}},
            checks=[{'id':'toy/ok','passed':True},{'id':'maintenance/false-drift','passed':False,'reason':'false drift claim'}])
        record=dict(schema='benchmark-calibration/1',status='passed',case='toy-case',execution='reference-calibration',
            backend='native-renode',evaluator_version='2',images={'firmware.bin':h,'firmware-drift.bin':'b'*64},
            input_hashes={k:h for k in ('source','contract','recipe','references','mutations','build','inventory')},evaluator_identity=calibration_identity(),native_process_observed=True,
            references=refs,mutants=mutants,required_mutants=[r['id'] for r in mutants],runs=runs,
            scenarios={'semantic':{'passed':True},'control':{'passed':True}},
            tools={k:'toy-version' for k in ('compiler','objcopy','renode','ghidra','java')},
            analysis={name:{'returncode':0,'export_hashes':{'decompiled.c':h}} for name in ('firmware.bin','firmware-drift.bin')})
        manifest=dict(id='toy-case',family='sampled-sensor',evaluator_version='2',images=record['images'],
            calibration={'input_hashes':record['input_hashes']})
        return manifest,record

    def test_complete_measured_record_passes(self):
        from generative_driver.benchmark_support.calibration import validate_calibration
        self.assertTrue(validate_calibration(*self.fixture())['ok'])

    def test_extra_declared_behavioral_failures_are_allowed_but_anchor_is_required(self):
        from generative_driver.benchmark_support.calibration import validate_calibration
        manifest,record=self.fixture()
        row=record['mutants'][0];run=next(r for r in record['runs'] if r['id']==row['run'])
        run['check_ids'].append('toy/secondary')
        run['checks'].append({'id':'toy/secondary','passed':False,'reason':'value mismatch'})
        row['failed_checks'].append('toy/secondary')
        self.assertTrue(validate_calibration(manifest,record)['ok'])
        for defect in ('anchor','unknown','structural','duplicate'):
            bad=copy.deepcopy(record);mutant=bad['mutants'][0];measured=next(r for r in bad['runs'] if r['id']==mutant['run'])
            if defect=='anchor':mutant['expected_failed_checks']=['toy/absent']
            if defect=='unknown':measured['check_ids'].remove('toy/secondary')
            if defect=='structural':measured['checks'][0]['reason']='missing evidence'
            if defect=='duplicate':mutant['expected_failed_checks']*=2
            with self.subTest(defect=defect):self.assertFalse(validate_calibration(manifest,bad)['ok'])

    def test_incomplete_or_self_reported_success_cannot_qualify(self):
        from generative_driver.benchmark_support.calibration import validate_calibration
        for defect in ('backend','image','identity','reference','same-model','mutant','both-lists','unchanged',
                       'wrong-failure','run','process','package','analysis','tools','malformed','false-control','false-decision','false-digest','input-inventory','runtime','scenario-image','tool-type','native-type','ordinary-reason','false-reason','false-host','duplicate-check','duplicate-run','analysis-type','executed-model','executed-capabilities'):
            manifest,record=self.fixture()
            if defect=='backend':record['backend']='python-socket-fixture'
            if defect=='image':record['images']={}
            if defect=='identity':record['evaluator_identity']={}
            if defect=='reference':record['references'].pop()
            if defect=='same-model':record['references'][1]['model_sha256']='a'*64
            if defect=='mutant':record['mutants'].pop()
            if defect=='both-lists':record['mutants'].pop();record['required_mutants'].pop()
            if defect=='unchanged':record['mutants'][0]['mutated_sha256']='a'*64
            if defect=='wrong-failure':record['mutants'][0]['failed_checks']=['unrelated']
            if defect=='run':record['runs'].pop(0)
            if defect=='process':record['runs'][0]['process']={}
            if defect=='package':record['runs'][1]['package_sha256']=None
            if defect=='analysis':record['analysis'].pop('firmware-drift.bin')
            if defect=='tools':record['tools']['java']=''
            if defect=='malformed':record['references']=None
            if defect=='false-control':record['runs'][-1]['native_control_passed']=False
            if defect=='false-decision':record['runs'][-1]['assessment_decision']['ok']=True
            if defect=='scenario-image':record['runs'][2]['image_sha256']='a'*64;record['runs'][2]['process']['image_sha256']='a'*64
            if defect=='tool-type':record['tools']['renode']=True
            if defect=='native-type':record['native_process_observed']=1
            if defect=='ordinary-reason':record['runs'][-5]['checks'][0]['reason']='false drift claim'
            if defect=='false-reason':record['runs'][-1]['checks'][-1]['reason']='value mismatch'
            if defect=='false-host':record['runs'][-1]['assessment_decision']['fault']='host'
            if defect=='duplicate-check':record['runs'][0]['checks']*=2
            if defect=='duplicate-run':record['runs'].append(copy.deepcopy(record['runs'][0]))
            if defect=='analysis-type':record['analysis']['firmware.bin']['returncode']=False
            if defect=='input-inventory':record['input_hashes']={'inventory':'a'*64};manifest['calibration']['input_hashes']=record['input_hashes']
            if defect=='executed-model':record['runs'][0]['model_sha256']='e'*64
            if defect=='executed-capabilities':record['runs'][0]['capabilities_sha256']='e'*64
            if defect=='runtime':record['runs'][0]['runtime_sha256']='c'*64
            if defect=='false-digest':record['mutants'][-1]['mutated_assessment_sha256']='c'*64
            with self.subTest(defect=defect):self.assertFalse(validate_calibration(manifest,record)['ok'])

class MutationTests(unittest.TestCase):
    def test_assessment_patch_has_exact_typed_allowlist(self):
        from generative_driver.benchmark_support.calibration import apply_mutation
        model={'toy':{'value':2}}; caps={'tasks':{}}
        mutation={'id':'false-drift','reference':'a','phase':'maintenance','scenario':'control',
            'expected_failed_checks':['maintenance/false-drift'],
            'patches':[{'target':'assessment','op':'replace','path':'/maintenance_claim','value':'drift'}]}
        changed=apply_mutation(model,caps,mutation)
        self.assertEqual(changed['assessment'],{'maintenance_claim':'drift'})
        self.assertEqual(changed['model'],model)
        self.assertNotEqual(changed['source_sha256'],changed['mutated_sha256'])
        for field,value in (('path','/other'),('value','unchanged'),('op','add'),('target','callback')):
            bad=copy.deepcopy(mutation);bad['patches'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):apply_mutation(model,caps,bad)

    def test_model_patch_changes_only_named_value_without_mutating_reference(self):
        from generative_driver.benchmark_support.calibration import apply_mutation
        model={'toy':{'value':2}}
        row={'id':'scale','reference':'a','phase':'final','expected_failed_checks':['toy/value'],
             'patches':[{'target':'model','op':'replace','path':'/toy/value','value':7}]}
        changed=apply_mutation(model,{'tasks':{}},row)
        self.assertEqual(changed['model']['toy']['value'],7)
        self.assertEqual(model['toy']['value'],2)
        self.assertNotEqual(changed['source_sha256'],changed['mutated_sha256'])

class DispatchTests(unittest.TestCase):
    def test_family_dispatch_uses_family_phase_and_measured_oracle(self):
        from generative_driver.benchmark_support.emulated import family_for
        sensor=family_for('sampled-sensor-v1')
        result=sensor.observations({'values':{'latched_q4':65520,'acquisition_counter':8}},
            {'ok':True,'outputs':{'temperature':-1,'sequence':8}}, {})
        self.assertEqual(result['reference_temperature'],-1)
        self.assertEqual(family_for('parameter-store-v1').observations.monitor_units['generation'],'count')
        with self.assertRaises(ValueError):family_for('unknown')

class HydrationTests(unittest.TestCase):
    def test_authenticated_inventory_supplies_complete_phases_and_candidate_hash(self):
        from generative_driver.benchmark_support.calibration import hydrate_truth
        from benchmark_family_fixtures import toy_sensor_phase
        pin,truth=toy_sensor_phase();phase=truth['phases']['diagnostic']
        phase['actions']=[{'kind':'reset','values':{},'episode':'toy','step':0},
            {'kind':'call','task':'sample','inputs':{},'grants':['write'],'episode':'toy','step':1},
            {'kind':'observe','checks':[c['id'] for c in phase['contract']['checks']],'episode':'toy','step':2}]
        payload=InventoryTests().fixture()
        values={'native/observation-map.json':{'source_file_paths':{'device.resc':'native/device.resc'},'settle_seconds':0},
            'diagnostic/episodes.json':phase,'final/episodes.json':phase,
            'maintenance/semantic.json':phase,'maintenance/control.json':phase}
        import json
        for name,value in values.items():
            data=json.dumps(value).encode();payload['inventory'][name]={'sha256':hashlib.sha256(data).hexdigest(),'base64':base64.b64encode(data).decode()}
        payload['inventory_sha256']=canonical_digest({k:v['sha256'] for k,v in payload['inventory'].items()})
        hydrated=hydrate_truth(payload)
        self.assertEqual(hydrated['recipe']['source_files'],{'device.resc':'{}'})
        self.assertEqual(hydrated['scenario_phases']['control']['final'],phase)
        self.assertEqual(hydrated['input_hashes']['inventory'],payload['inventory_sha256'])
        self.assertNotIn('artifact_sha256',hydrated)

class ReferenceExecutionTests(unittest.TestCase):
    def test_final_uses_real_emitted_package_and_rejects_frozen_tampering(self):
        import socketserver,threading,tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from benchmark_family_fixtures import toy_sample_model,toy_sensor_phase,sample_reply
        from generative_driver.benchmark_support.emulated import reference_execute
        from generative_driver.configurator import digest
        class Device(socketserver.StreamRequestHandler):
            def handle(self):
                while True:
                    cmd=self.rfile.readline()
                    if not cmd:return
                    replies={b'ID\n':b'TOY-SAMPLE\n',b'ACQUIRE\n':b'OK\n',b'SAMPLE\n':sample_reply(-80,3)}
                    if cmd not in replies:raise AssertionError('Unknown toy wire request')
                    self.wfile.write(replies[cmd]);self.wfile.flush()
        class Server(socketserver.ThreadingTCPServer):allow_reuse_address=True;daemon_threads=True
        with tempfile.TemporaryDirectory() as temp,Server(('127.0.0.1',0),Device) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            class Session:
                process=None;running=False;stopped=False
                info={'fixture':True};binding={'host':'127.0.0.1','port':server.server_address[1]}
                def set_running(self,value):self.running=value
                def reset(self,values):return {'values':values}
                def observe(self):
                    if self.running:raise AssertionError('Unpaused monitor')
                    return {'values':{'latched_q4':65456,'acquisition_counter':3}}
                def stop(self):self.stopped=True
            pin,truth=toy_sensor_phase();pin['case_id']='sampled-sensor-v1'
            phase=truth['phases']['diagnostic'];phase['actions']=[{'kind':'reset','values':{}},
                {'kind':'call','task':'sample','inputs':{},'grants':['write']},
                {'kind':'observe','checks':[c['id'] for c in phase['contract']['checks']]}]
            truth['phases']['final']=phase;truth['recipe']={};truth['contracts']={'time_policy':{'sample_settle_seconds':0}}
            caps={'schema':'benchmark-capabilities/2','tasks':{'sample':{'operation':'sample','constants':{},'inputs':{},
                'outputs':{k:{'output':k,'unit':u} for k,u in (('temperature','degC'),('sequence','count'))}}}}
            session=Session()
            try:
                with patch('generative_driver.benchmark_support.native.NativeSession.start',return_value=session):
                    result=reference_execute(pin,truth,toy_sample_model(),caps,renode=Path('/toy'),image=Path('/toy'),output_dir=Path(temp)/'good')
                self.assertEqual(result['grade']['verdict'],'passed')
                self.assertTrue(session.stopped)
                self.assertFalse(result['native_process_observed'])
                with patch('generative_driver.benchmark_support.native.NativeSession.start',return_value=Session()):
                    modeled=reference_execute(pin,truth,toy_sample_model(),caps,renode=Path('/toy'),image=Path('/toy'),
                        output_dir=Path(temp)/'modeled',package_final=False)
                self.assertEqual(modeled['grade']['verdict'],'passed')
                self.assertIsNone(modeled['package_sha256'])
                self.assertEqual(modeled['contract']['artifact_sha256'],modeled['model_sha256'])
                package=Path(result['package_dir']);frozen=result['package_sha256']
                self.assertEqual(digest(package),frozen)
                self.assertEqual(result['contract']['artifact_sha256'],frozen)
                (package/'driver/model.json').write_text('{}')
                with patch('generative_driver.benchmark_support.native.NativeSession.start',side_effect=AssertionError('Device accessed')):
                    with self.assertRaisesRegex(ValueError,'Frozen package'):
                        reference_execute(pin,truth,toy_sample_model(),caps,renode=Path('/toy'),image=Path('/toy'),
                            output_dir=Path(temp)/'bad',package=package,expected_package_sha256=frozen)
            finally:server.shutdown();thread.join()

class FamilyAdmissionBoundaryTests(unittest.TestCase):
    def test_registry_admission_consumes_family_validator_and_authenticated_inputs(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import require_calibration
        manifest,record=AdmissionTests().fixture()
        manifest.update(schema='benchmark-case/2')
        manifest['calibration'].update(status='passed',evidence_sha256=canonical_digest(record))
        pin={'case_id':'sampled-sensor-v1','scenario_id':'control','case_seed':0,'manifest':manifest,
             'calibration':manifest['calibration']}
        truth={'calibration':record,'images':manifest['images'],'input_hashes':record['input_hashes'],
               'family':'sampled-sensor','inventory_state':'qualified','mutations':{'mutants':[
                   {'id':row['id'],'expected_failed_checks':row['expected_failed_checks']} for row in record['mutants']]}}
        model={'toy':{'value':2}};capabilities={'tasks':{}}
        truth['references']={'a':{'model':model,'capabilities':capabilities},
                             'b':{'model':{'toy':{'value':3}},'capabilities':capabilities}}
        for ref in record['references']:ref['model_sha256']=canonical_digest(truth['references'][ref['id']]['model'])
        for definition,row in zip(truth['mutations']['mutants'],record['mutants']):
            assessment={'maintenance_claim':'drift' if row['id']=='false-drift' else 'unchanged'}
            definition.update(reference='a',phase='maintenance' if row['id']=='false-drift' else 'final',
                patches=[{'target':'assessment' if row['id']=='false-drift' else 'model','op':'replace',
                    'path':'/maintenance_claim' if row['id']=='false-drift' else '/toy/value',
                    'value':'drift' if row['id']=='false-drift' else 7}])
            if row['id']=='false-drift':definition['scenario']='control'
            measured=next(r for r in record['runs'] if r['id']==row['run'])
            measured['phase']=definition['phase']
            if definition['phase']=='final':measured['package_sha256']='c'*64
            row['source_sha256']=canonical_digest({'model':model,'capabilities':capabilities,'assessment':{'maintenance_claim':'unchanged'}})
            row['mutated_sha256']=canonical_digest({'model':model if row['id']=='false-drift' else {'toy':{'value':7}},
                'capabilities':capabilities,'assessment':assessment})
        truth['oracle']={'semantic_model_patches':{ref:[{'op':'replace','path':'/toy/value','value':9}] for ref in ('a','b')}}
        identifiers=['toy/ok','toy/constant','toy/scale','toy/signedness','toy/stale']
        checks=[{'id':name,'revision':0,'kind':'boolean','expected':True,'unit':'boolean','channel':'runtime-transcript'} for name in identifiers]
        phase={'contract':{'schema':'benchmark-behavior/1','artifact_sha256':'a'*64,'checks':checks},'actions':[]}
        truth['scenario_phases']={scenario:{name:copy.deepcopy(phase) for name in ('diagnostic','final','maintenance')} for scenario in ('semantic','control')}
        def identity(model,caps):
            import json
            return {'model_sha256':hashlib.sha256((json.dumps({**model,'channel':{'type':'tcp'}},indent=2,allow_nan=False)+'\n').encode()).hexdigest(),
                    'capabilities_sha256':canonical_digest(caps)}
        for ref in record['references']:
            ref['execution_inputs']={}
            for name in ref['runs']:
                measured=next(r for r in record['runs'] if r['id']==name)
                expected_model={'toy':{'value':9}} if measured['scenario']=='semantic' else truth['references'][ref['id']]['model']
                expected=identity(expected_model,capabilities)
                measured.update(expected);ref['execution_inputs'][name]=expected
        for row in record['mutants']:
            expected=identity(model if row['id']=='false-drift' else {'toy':{'value':7}},capabilities)
            next(r for r in record['runs'] if r['id']==row['run']).update(expected)
            row['execution_input']=expected
        for measured in record['runs']:
            failed={c['id']:c for c in measured['checks'] if not c['passed']}
            measured['check_ids']=list(identifiers)
            measured['checks']=[failed.get(name,{'id':name,'passed':True}) for name in identifiers]
            if measured['id']=='false-drift':measured['checks'].append(failed['maintenance/false-drift'])
            measured['contract_sha256']=canonical_digest({**phase['contract'],'artifact_sha256':measured['package_sha256'] or measured['model_sha256']})
        manifest['calibration']['evidence_sha256']=canonical_digest(record)
        with patch('generative_driver.benchmark_support.registry.pin_case',return_value=pin),patch(
                'generative_driver.benchmark_support.emulator.truth_for_case',return_value=truth):
            self.assertTrue(require_calibration(pin,{})['ok'])
            for index in (0,2,4,8):
                measured=record['runs'][index]
                original_model=measured['model_sha256'];original_contract=measured['contract_sha256']
                owner=next((ref['execution_inputs'][measured['id']] for ref in record['references'] if measured['id'] in ref['runs']),None)
                if owner is None:owner=next(row['execution_input'] for row in record['mutants'] if row['run']==measured['id'])
                measured['model_sha256']='e'*64;owner['model_sha256']='e'*64
                measured['contract_sha256']=canonical_digest({**phase['contract'],
                    'artifact_sha256':measured['package_sha256'] or measured['model_sha256']})
                manifest['calibration']['evidence_sha256']=canonical_digest(record)
                with self.subTest(executed_run=measured['id']),self.assertRaisesRegex(ValueError,'Calibration'):
                    require_calibration(pin,{})
                measured['model_sha256']=original_model;owner['model_sha256']=original_model;measured['contract_sha256']=original_contract
            manifest['calibration']['evidence_sha256']=canonical_digest(record)
            original=record['mutants'][0]['source_sha256']
            record['mutants'][0]['source_sha256']='e'*64
            manifest['calibration']['evidence_sha256']=canonical_digest(record)
            with self.assertRaisesRegex(ValueError,'Calibration'):require_calibration(pin,{})
            record['mutants'][0]['source_sha256']=original
            manifest['calibration']['evidence_sha256']=canonical_digest(record)
            record['runs'][0]['check_ids'].pop();record['runs'][0]['checks'].pop()
            manifest['calibration']['evidence_sha256']=canonical_digest(record)
            with self.assertRaisesRegex(ValueError,'Calibration'):require_calibration(pin,{})
            truth['input_hashes']={'inventory':'c'*64}
            with self.assertRaisesRegex(ValueError,'Calibration'):require_calibration(pin,{})

class ReuseBoundaryTests(unittest.TestCase):
    def test_sample_reuse_requires_two_new_measured_acquisitions(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.emulated import check_stage
        from generative_driver.benchmark_support.cases import _write
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);caps=root/'caps.json'
            _write(caps,{'tasks':{'measure':{'operation':'sample','outputs':{
                'temperature':{'output':'t'},'sequence':{'output':'n'}}}}})
            _write(root/'benchmark/state.json',{'package_attempts':{'reuse':'new'},'capabilities_path':str(caps),
                'package_copies':{'reuse':str(root/'package')}})
            def events(sequence):
                return [{'actor':'worker','stage':'reuse','attempt_id':'new','revision':0,'operation':'sample',
                    'result':{'ok':True,'outputs':{'t':-5,'n':n}},'observation':{
                        'reference_temperature':-5,'monitor_sequence':n}} for n in sequence]
            with patch('generative_driver.benchmark_support.emulated._final',return_value={'ok':True}):
                for sequence,passed in (([3],False),([3,3],False),([3,4],True)):
                    (root/'benchmark/package-events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events(sequence)))
                    self.assertEqual(check_stage('sampled-sensor-v1','reuse',root,root,{'status':'completed'})['ok'],passed)

class StoreReuseTests(unittest.TestCase):
    def test_commit_read_and_abort_require_observed_state_and_bank_isolation(self):
        from generative_driver.benchmark_support import parameter_store
        caps={'tasks':{k:{'operation':k,'outputs':{'value':{'output':'v'}} if k=='read' else {}}
                       for k in ('read','update','stage','abort')}}
        before={**{'cell_'+bank+'_'+str(i):0 for bank in ('A','B') for i in range(4)},'generation':0,'pending_active':False}
        committed={**before,'cell_B_1':-7,'generation':1}
        staged={**committed,'pending_active':True,'pending_B_1':9}
        events=[{'operation':op,'result':{'ok':True,'outputs':{'v':-7} if op=='read' else {}},
                 'before_observation':before,'observation':observation}
                for op,observation in (('update',committed),('stage',staged),('abort',committed),('read',committed))]
        self.assertTrue(parameter_store.reuse_passed(events,caps))
        for defect in ('read','abort','bank','commit','left-pending'):
            bad=copy.deepcopy(events)
            if defect=='read':bad[-1]['result']['outputs']['v']=0
            if defect=='abort':bad[2]['observation']['pending_active']=True
            if defect=='bank':bad[-1]['observation']['cell_A_1']=-7
            if defect=='commit':bad[-1]['observation']['generation']=0
            if defect=='left-pending':bad.append(copy.deepcopy(bad[1]))
            with self.subTest(defect=defect):self.assertFalse(parameter_store.reuse_passed(bad,caps))

    def test_explicit_commit_and_harmless_reads_satisfy_same_observed_mission(self):
        from generative_driver.benchmark_support import parameter_store
        caps={'tasks':{k:{'operation':'toy_'+k,'outputs':{'value':{'output':'v'}} if k=='read' else {}}
                       for k in ('read','update','stage','commit','abort')}}
        before={**{'cell_'+bank+'_'+str(i):0 for bank in ('A','B') for i in range(4)},
                'generation':4,'pending_active':False}
        committed={**before,'cell_B_1':-7,'generation':5}
        first_stage={**before,'pending_active':True,'pending_B_1':-7}
        last_stage={**committed,'pending_active':True,'pending_B_1':9}
        plans={
            'explicit-commit':[('stage',first_stage),('commit',committed),('stage',last_stage),
                               ('abort',committed),('read',committed)],
            'harmless-reads':[('read',before),('update',committed),('read',committed),
                              ('stage',last_stage),('abort',committed),('read',committed)],
        }
        for name,plan in plans.items():
            events=[];previous=before
            for operation,observed in plan:
                events.append({'operation':'toy_'+operation,'before_observation':previous,
                    'observation':observed,'result':{'ok':True,'outputs':{'v':observed['cell_B_1']} if operation=='read' else {}}})
                previous=observed
            with self.subTest(composition=name):
                self.assertTrue(parameter_store.reuse_passed(events,caps))

class StageDispatchTests(unittest.TestCase):
    def test_diagnostic_stage_uses_family_contract_and_monitor_units(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from benchmark_family_fixtures import toy_sensor_phase
        from generative_driver.benchmark_support.emulated_evidence import _diagnose
        from generative_driver.benchmark_support.cases import _write
        pin,truth=toy_sensor_phase();pin.update(case_id='sampled-sensor-v1',revision=0)
        phase=truth['phases']['diagnostic'];phase['actions']=[{'kind':'reset','values':{}},
            {'kind':'call','task':'measure','inputs':{},'grants':['write']},
            {'kind':'observe','checks':[c['id'] for c in phase['contract']['checks']]}]
        truth['contracts']={'time_policy':{'sample_settle_seconds':0}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);password=root/'password';password.write_text('unrelated-toy-test')
            _write(root/'model/model.json',{'toy':True})
            class Session:
                info={'private_dir':str(root/'private')};binding={}
                def reset(self,values):return {}
                def set_running(self,value):pass
                def observe(self):return {'values':{'latched_q4':65456,'acquisition_counter':3}}
            def invoke(*args):return {'ok':True,'outputs':{'temperature':-5,'sequence':3},'units':{'temperature':'degC','sequence':'count'}}
            with patch('generative_driver.benchmark_support.emulated._session',return_value=Session()),patch(
                'generative_driver.benchmark_support.emulated._truth',return_value=truth),patch(
                'generative_driver.benchmark_support.emulated_evidence.read_snapshot',return_value={'case_pin':pin}),patch(
                'generative_driver.benchmark_support.emulated_actions.model_invoker',return_value=invoke):
                grade,records,_,_=_diagnose('sampled-sensor-v1',root,root/'model',{}, {'evaluator_password_file':str(password)})
            self.assertEqual(grade['verdict'],'passed')
            self.assertEqual({r['unit'] for r in records},{'degC','count','boolean'})

    def test_changed_frozen_final_package_is_refused_before_session_access(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.emulated_evidence import _final,_private
        from generative_driver.benchmark_support.cases import _write
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);password=root/'password';password.write_text('toy-secret')
            opts={'evaluator_password_file':str(password)}
            package=root/'package';package.mkdir();(package/'model').write_text('changed')
            _write(root/'benchmark/state.json',{'package_sha256':'a'*64})
            _private(root,opts,{'evaluations':[]})
            with patch('generative_driver.benchmark_support.emulated._session',side_effect=AssertionError('Native access')):
                with self.assertRaisesRegex(ValueError,'Frozen package'):_final('sampled-sensor-v1',root,package,{},opts)

class TruthBoundaryTests(unittest.TestCase):
    def test_authenticated_but_nonexecutable_family_inventory_is_refused(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.truth import seal
        from generative_driver.benchmark_support.emulated import _truth
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);password=root/'password';password.write_text('toy')
            digest=seal(InventoryTests().fixture(),root/'truth.enc','toy')
            manifest={'truth':{'path':'truth.enc','sha256':digest}}
            with patch('generative_driver.benchmark_support.emulated._inputs',return_value=(root,manifest)),patch(
                    'generative_driver.benchmark.case_root',return_value=root):
                with self.assertRaises((ValueError,KeyError)):
                    _truth('sampled-sensor-v1',root,{'evaluator_password_file':str(password)})

class PublicObservationTests(unittest.TestCase):
    def test_public_package_feedback_uses_selected_family_monitor(self):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.emulated import package_execute
        from generative_driver.benchmark_support.cases import _write
        from generative_driver.configurator import digest
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();package=root/'package';package.mkdir();(package/'toy').write_text('toy')
            _write(root/'benchmark/state.json',{'package_copies':{'reuse':str(package)},'package_sha256':digest(package),
                'package_attempts':{'reuse':'toy'}})
            class Session:
                binding={'host':'toy'}
                def set_running(self,value):pass
                def observe(self):return {'values':{'latched_q4':65456,'acquisition_counter':3}}
            with patch('generative_driver.benchmark_support.emulated._session',return_value=Session()),patch(
                'generative_driver.benchmark_support.emulated_actions.package_invoker',return_value=lambda *args:{'ok':True,'outputs':{}}):
                result=package_execute('sampled-sensor-v1',root,'reuse',package,'toy',binding={'host':'toy'})
            self.assertEqual(result['observation']['reference_temperature'],-5)
            self.assertEqual(result['observation']['monitor_sequence'],3)
            row=json.loads((root/'benchmark/package-events.jsonl').read_text())
            self.assertEqual(row['before_observation']['monitor_sequence'],3)

class ScenarioDispatchTests(unittest.TestCase):
    def test_control_installs_original_image_and_exact_family_reset(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.emulated import _apply_scenario
        from generative_driver.benchmark_support.cases import _state
        from generative_driver.benchmark_support.scenarios import ScenarioJournal
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            truth={'images':{'firmware.bin':'a'*64,'firmware-drift.bin':'b'*64},
                   'scenario_phases':{'control':{'maintenance':{'actions':[{'kind':'reset','values':{'toy_source':2}}]}}}}
            class Session:
                def reset(self,values):
                    if values!={'toy_source':2}:raise AssertionError('Wrong family reset')
                    return {'values':values,'controls':[{'response':'toy reset acknowledged'}]}
            with patch('generative_driver.benchmark_support.emulated._truth',return_value=truth),patch(
                'generative_driver.benchmark_support.emulated.read_snapshot',return_value={'case_pin':{'scenario_id':'control'}}),patch(
                'generative_driver.benchmark_support.emulated.cleanup'),patch(
                'generative_driver.benchmark_support.emulated._session',return_value=Session()):
                _apply_scenario('sampled-sensor-v1',root,{})
            self.assertEqual(_state(root)[1]['image_name'],'firmware.bin')
            self.assertEqual(ScenarioJournal(root/'benchmark').state()['status'],'applied')

class WrapperTests(unittest.TestCase):
    def test_missing_native_configuration_fails_before_shared_runner(self):
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.calibration import calibrate
        with patch('generative_driver.benchmark_support.authoring.calibrate',side_effect=AssertionError('Native runner started')):
            with self.assertRaises(ValueError):
                calibrate('sampled-sensor-v1',authoring_root=Path('/absent-authoring'),renode=Path('relative'),
                    ghidra_home=Path('/absent-ghidra'),java_home=Path('/absent-java'),output_dir=Path('/absent-output'))

class SeedEpisodeTests(unittest.TestCase):
    def test_seed_rotates_only_whole_reset_delimited_episodes(self):
        from generative_driver.benchmark_support.family_support import private_phase
        actions=[{'kind':'reset','episode':'a'},{'kind':'call','episode':'a'},
                 {'kind':'observe','episode':'a'},{'kind':'reset','episode':'b'},
                 {'kind':'call','episode':'b'},{'kind':'observe','episode':'b'}]
        truth={'family':'sampled-sensor','artifact_sha256':'a'*64,
               'phases':{'final':{'contract':{'checks':[]},'actions':actions}}}
        result=private_phase({'family':'sampled-sensor','case_seed':1},truth,'final')
        self.assertEqual([a['episode'] for a in result['actions']],['b','b','b','a','a','a'])
        self.assertEqual([a['kind'] for a in result['actions']],['reset','call','observe','reset','call','observe'])
        self.assertEqual(truth['phases']['final']['actions'][0]['episode'],'a')

class FailedProducerTests(unittest.TestCase):
    def test_failed_native_preflight_keeps_pending_inventory_without_qualification_record(self):
        import json,tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import calibrate
        from generative_driver.benchmark_support.truth import unlock
        from benchmark_family_fixtures import toy_sensor_phase
        _,truth=toy_sensor_phase();phase=truth['phases']['diagnostic']
        phase['actions']=[{'kind':'reset','values':{}},{'kind':'call','task':'toy','inputs':{},'grants':[]},
                          {'kind':'observe','checks':[c['id'] for c in phase['contract']['checks']]}]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);author=root/'author';author.mkdir();password=root/'password';password.write_text('toy-password')
            for name in inventory('sampled-sensor'):
                path=author/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
            values={'AUTHORING.json':{'schema':'benchmark-authoring/1','id':'sampled-sensor-v1',
                'family':'sampled-sensor','execution':'reference-build'},
                'native/observation-map.json':{'source_file_paths':{},'settle_seconds':0},
                'diagnostic/episodes.json':phase,'final/episodes.json':phase,
                'maintenance/semantic.json':phase,'maintenance/control.json':phase}
            for name,value in values.items():(author/name).write_text(json.dumps(value))
            result=calibrate('sampled-sensor-v1',{'authoring_root':str(author),'output':str(root/'out'),
                'compiler':'/missing-compiler','renode':'/missing-renode','ghidra_home':'/missing-ghidra',
                'java_home':'/missing-java','evaluator_password_file':str(password)})
            self.assertFalse(result['ok'])
            manifest=json.loads((root/'out/case.json').read_text())
            payload=unlock(root/'out/sampled-sensor-v1.enc','toy-password',manifest['truth']['sha256'])
            self.assertEqual(payload['inventory_state'],'pending')
            self.assertNotIn('calibration/calibration.json',payload['inventory'])

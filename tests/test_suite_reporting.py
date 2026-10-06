"""Suite reporting uses public toy data and scripted owner records, never model baselines."""
import copy
import hashlib
import json
import unittest

STAGES=('acquire','interpret','probe','ground','emit','reuse')

def fixture():
    manifest={'schema':'benchmark-suite/1','id':'toy','version':'1','entries':[
        {'case':'tq9-v2','scenario':'original','case_seed':0},
        {'case':'sampled-sensor-v1','scenario':'original','case_seed':0},
        {'case':'parameter-store-v1','scenario':'original','case_seed':0}],
        'repetitions':1,'child_budget_seconds':10,'suite_budget_seconds':60,'max_active_children':1}
    pins=[];slots=[];trials=[];snapshots=[];reports={}
    for index,(entry,family) in enumerate(zip(manifest['entries'],('one','one','two'))):
        pin={'case_id':entry['case'],'case_version':'4','evaluator_version':'4','family':family,
            'scenario_id':entry['scenario'],'case_seed':0,'execution':'actual-agent-emulation','evidence_track':'firmware',
            'scope':'full-workflow','manifest_sha256':'a'*64,'truth_sha256':'b'*64,'image_hashes':{'image.bin':'c'*64},'pin_sha256':str(index+1)*64}
        pins.append(pin)
        slot={**entry,'ordinal':index,'entry_index':index,'repeat_index':0,'trial_key':f'entry-{index:03d}-repeat-000'}
        slots.append(slot)
        run_id=f'r{index+1}' if index<2 else None
        trials.append({**slot,'family':family,'run_id':run_id,'status':'finished' if run_id else 'pending',
            'outcome_category':('completed','model','unknown')[index]})
        snapshots.append({'trial_key':slot['trial_key'],'snapshot_sha256':'d'*64 if run_id else None})
        if not run_id:continue
        passed=index==0
        reports[run_id]={'schema':'benchmark-report/2','run_id':run_id,'case':entry['case'],'scenario_id':entry['scenario'],'case_seed':0,
            'case_pin':pin,'snapshot_sha256':'d'*64,'workflow_status':'completed' if passed else 'failed','verdict':'passed' if passed else 'failed',
            'worker_seconds':4 if passed else 6,'elapsed_seconds':5 if passed else 8,'tool_seconds':1,
            'usage':{'total_tokens':10 if passed else None,'observed_total_tokens':10 if passed else 7},
            'stages':{stage:{'workflow_status':'accepted','evaluator_status':'passed','attempt_count':1,
                'attempts':[{'assignment_id':stage,'accepted':True,'status':'completed'}]} for stage in STAGES} if passed else {},
            'accepted_gates':[{'stage':stage,'revision':0,'assignment_id':stage,'artifact_sha256':'e'*64,'verdict':'passed','passed':2 if stage=='reuse' else 1,'total':2 if stage=='reuse' else 1} for stage in STAGES] if passed else [],
            'evaluations':[{'phase':phase,'revision':0,'frozen_artifact_sha256':'e'*64,'verdict':'passed','passed':2,'total':2} for phase in ('diagnostic','final')] if passed else [],
            'final_evaluation':{'verdict':'passed','passed':2,'total':2,'evidence_sha256':'f'*64} if passed else {},
            'progress':{'revision':0,'repairs':0}}
    policy={'child_budget_seconds':10,'suite_budget_seconds':60}
    experiment={'schema':'benchmark-suite-experiment/1','comparison_identity':{
        'manifest':manifest,'manifest_sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'entry_pins':pins,'trials':slots,'repetitions':1,'execution':'actual-agent-emulation','evidence_track':'firmware','scope':'full-workflow',
        'time_policy':[{} for _ in pins],'evaluator_revision':'a'*64,
        'budgets':{'original':policy,'effective':{**policy,'child_budget_overrides':[]}},
        'intervention_policy':{'scoped_tool_approval':None,'max_model_repairs':2}},
        'dimensions':{'runtime':'codex','model':'toy','skills_revision':'b'*64,'toolchain_revision':'c'*64},'execution_snapshots':snapshots}
    return manifest,trials,reports,experiment

class AggregateTests(unittest.TestCase):
    def test_unstarted_and_failed_resources_remain_in_denominators(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        manifest,trials,reports,experiment=fixture()
        result=aggregate_suite(manifest,trials,reports,'recorded-controller-verdicts',experiment=experiment)
        self.assertEqual(result['counts']['planned'],3);self.assertEqual(result['counts']['not_run'],1)
        self.assertEqual(result['success'],{'passed':1,'denominator':3,'rate':1/3})
        self.assertTrue(result['provisional']);self.assertEqual(result['worker_seconds'],10)
        self.assertIsNone(result['usage']['total_tokens']);self.assertEqual(result['usage']['observed_total_tokens'],17)
        self.assertEqual(result['family_macro_success'],.25)

    def test_rejects_incomplete_misidentified_and_invalid_measurements(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        def changes():
            yield lambda m,t,r,e:t.pop()
            yield lambda m,t,r,e:t[1].update(ordinal=0)
            yield lambda m,t,r,e:t[0].update(case='sampled-sensor-v1')
            yield lambda m,t,r,e:t[0].update(family='invented')
            yield lambda m,t,r,e:t[1].update(run_id='r1')
            yield lambda m,t,r,e:e['comparison_identity'].update(manifest_sha256='0'*64)
            yield lambda m,t,r,e:r['r1'].update(case_seed=1)
            yield lambda m,t,r,e:r['r1'].update(snapshot_sha256='1'*64)
            for value in (-1,float('nan'),float('inf'),True):
                yield lambda m,t,r,e,value=value:r['r1'].update(worker_seconds=value)
            yield lambda m,t,r,e:r['r1']['stages']['probe'].update(worker_seconds=-1)
            for value in (-1,True,1.5):
                yield lambda m,t,r,e,value=value:r['r1']['usage'].update(total_tokens=value)
        for index,change in enumerate(changes()):
            m,t,r,e=fixture();change(m,t,r,e)
            with self.subTest(index=index),self.assertRaises(ValueError):aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        m,t,r,e=fixture()
        with self.assertRaises(ValueError):aggregate_suite(m,t,r,'worker-says-passed',experiment=e)

    def test_bare_verdict_or_incomplete_final_gates_never_earns_success(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        for field in ('stages','accepted_gates','final_evaluation'):
            m,t,r,e=fixture();r['r1'].pop(field)
            self.assertEqual(aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)['success']['passed'],0,field)

    def test_stale_case_versions_are_rejected_even_when_report_and_experiment_agree(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        m,t,r,e=fixture()
        e['comparison_identity']['entry_pins'][0]['case_version']='2'
        r['r1']['case_pin']['case_version']='2'
        with self.assertRaisesRegex(ValueError,'version|identity'):
            aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)

    def test_obsolete_extra_gate_cannot_qualify_current_aggregate(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        m,t,r,e=fixture()
        r['r1']['accepted_gates'].append({'stage':'maintain','revision':0,'assignment_id':'obsolete',
            'artifact_sha256':'e'*64,'verdict':'passed','passed':1,'total':1})
        self.assertEqual(aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)['success']['passed'],0)

    def test_missing_report_preserves_known_time_without_inventing_total(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        m,t,r,e=fixture();r.pop('r2')
        result=aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        self.assertIsNone(result['worker_seconds']);self.assertEqual(result['observed_worker_seconds'],4)
        self.assertIsNone(result['usage']['total_tokens'])

    def test_public_descriptive_metrics_exclude_unknown_payloads(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        m,t,r,e=fixture()
        r['r1']['surprise']='/private/answer';r['r1']['stages']['probe']['unknown']={'secret':'PRIVATE'}
        r['r1']['stages']['probe']['attempts'][0]['novel']='PRIVATE'
        t[0]['interventions']=[{'event_id':3,'kind':'operator.response','time':7,'novel':'PRIVATE'}]
        result=aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('/private',json.dumps(result))
        self.assertEqual(result['experiment'],e)
        self.assertEqual(len(result['trials']),3)
        self.assertEqual(result['trials'][0]['interventions'],[{'event_id':3,'kind':'operator.response','time':7}])
        self.assertEqual(result['first_attempt_success'],1);self.assertEqual(result['repaired_success'],0)
        self.assertEqual(result['latency'],{'sample_count':2,'includes_failed_trials':True,'min':5,'median':6.5,'max':8})
        self.assertNotIn('false_alarms', result)
        self.assertNotIn('repair_seconds', result)
        self.assertEqual(result['case_success']['tq9-v2'],{'passed':1,'denominator':1,'rate':1.0})
        self.assertEqual(result['scenario_success']['original']['denominator'],3)
        self.assertEqual(result['stage_counts']['probe']['accepted'],1)
        r['r1']['stages']['probe']['attempt_count']=2
        result=aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        self.assertEqual(result['first_attempt_success'],0);self.assertEqual(result['repaired_success'],1)


def comparison_fixture():
    _,trials,_,experiment=fixture()
    return {'schema':'benchmark-suite-report/1','qualification':'recorded-controller-verdicts','experiment':experiment,
        'trials':[dict(t,verdict='passed' if t['run_id']=='r1' else 'failed' if t['run_id'] else None) for t in trials],
        'success':{'passed':1,'denominator':3,'rate':1/3},'usage':{'total_tokens':None},'latency':{'median':6.5}}

class ComparisonTests(unittest.TestCase):
    def test_only_dimensions_vary_in_a_paired_claim_and_unknown_tokens_stay_unknown(self):
        from generative_driver.benchmark_support.suite_reporting import compare_suites
        before=comparison_fixture();after=copy.deepcopy(before)
        after['experiment']['dimensions']['model']='other'
        after['experiment']['execution_snapshots'][0]['snapshot_sha256']='1'*64
        result=compare_suites(before,after)
        self.assertTrue(result['compatible']);self.assertEqual(result['changed_dimensions'],['model'])
        self.assertIsNone(result['tokens_delta'])
        self.assertEqual(result['before_success'],before['success'])
        after['experiment']['dimensions']['toolchain_revision']='2'*64
        self.assertEqual(compare_suites(before,after)['change_scope'],'combined-system')
        after['experiment']['comparison_identity']['budgets']['effective']['suite_budget_seconds']=61
        refusal=compare_suites(before,after)
        self.assertFalse(refusal['compatible']);self.assertNotIn('success_delta',refusal)
        after=copy.deepcopy(before)
        after['experiment']['comparison_identity']['budgets']['effective']['child_budget_overrides']=[{'trial_key':'entry-000-repeat-000','budget_seconds':11}]
        self.assertFalse(compare_suites(before,after)['compatible'])
        after=copy.deepcopy(before);after['success']={'passed':2,'denominator':3,'rate':2/3}
        self.assertFalse(compare_suites(before,after)['compatible'])
        after=copy.deepcopy(before);after['trials'].pop()
        self.assertFalse(compare_suites(before,after)['compatible'])
        after=copy.deepcopy(before);after['experiment']['comparison_identity']['unexplained']='value'
        self.assertFalse(compare_suites(before,after)['compatible'])

    def test_saved_file_comparison_creates_no_owner(self):
        import os,subprocess,sys,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);saved=root/'suite.json';saved.write_text(json.dumps(comparison_fixture()),encoding='utf-8')
            home=root/'unused owner'
            program='import json,sys; from generative_driver.benchmark_support.suite_reporting import compare_suites; print(json.dumps(compare_suites(sys.argv[1],sys.argv[1])))'
            result=subprocess.run([sys.executable,'-c',program,str(saved)],env={**os.environ,'GENERATIVE_DRIVER_HOME':str(home)},capture_output=True,text=True,check=True)
            self.assertTrue(json.loads(result.stdout)['compatible']);self.assertFalse(home.exists())


def owner_fixture():
    m,t,r,e=fixture();report=r['r1']
    handoffs=[{'stage':stage,'revision':0,'assignment_id':stage,'route':None,
        'artifacts':[{'path':'/private/candidate','sha256':'e'*64,'kind':'evidence'}],
        'checks':[{'name':name,'passed':True} for name in ('fresh_worker_mission','frozen_final_behavior')]
            if stage=='reuse' else [{'name':'toy-check','passed':True}]} for stage in STAGES]
    owner={'ok':True,'run_id':'r1','case':'tq9-v2','case_pin':dict(report['case_pin'],manifest={'schema':'benchmark-case/2'}),
        'snapshot_sha256':'d'*64,'status':'completed','outcome_category':'completed','stopping':False,'uncertain_effect':False,
        'progress':{'revision':0,'next_stage':None},'accepted_handoffs':handoffs,
        'evaluator_verdicts':[{'stage':stage,'assignment_id':stage,'verdict':'passed',**({'passed':2,'total':2} if stage!='emit' else {}),**({'final_evaluation':True} if stage=='reuse' else {})} for stage in ('probe','emit','reuse')],
        'benchmark_summary':{key:copy.deepcopy(report[key]) for key in ('accepted_gates','evaluations','final_evaluation')}}
    report['evaluator_verdicts']=copy.deepcopy(owner['evaluator_verdicts'])
    events=[]
    for index,handoff in enumerate(handoffs):
        events.extend([{'id':index*2+1,'kind':'stage.assigned','data':{'id':handoff['assignment_id'],'stage':handoff['stage'],'revision':0},'time':index},
            {'id':index*2+2,'kind':'stage.accepted','data':copy.deepcopy(handoff),'time':index+.5}])
    return owner,events,report

class QualificationTests(unittest.TestCase):
    def test_success_requires_owner_gates_sealed_summary_and_settled_lifecycle(self):
        from generative_driver.benchmark_support.suite_reporting import _recorded_success
        owner,events,report=owner_fixture()
        self.assertTrue(_recorded_success(owner,events,report))
        mutations=[lambda o,e,r:o.update(stopping=True),lambda o,e,r:o.update(uncertain_effect=True),
            lambda o,e,r:o.update(outcome_category='unknown'),lambda o,e,r:o['accepted_handoffs'][2]['checks'][0].update(passed=False),
            lambda o,e,r:e.pop(),lambda o,e,r:o['benchmark_summary']['final_evaluation'].update(verdict='failed'),
            lambda o,e,r:o['benchmark_summary']['final_evaluation'].pop('evidence_sha256'),
            lambda o,e,r:o['evaluator_verdicts'][0].update(passed=1),
            lambda o,e,r:o['accepted_handoffs'][4]['artifacts'][0].update(sha256='0'*64),
            lambda o,e,r:r['accepted_gates'][1].update(assignment_id='forged'),
            lambda o,e,r:o['progress'].update(terminal_final_failure=True),
            lambda o,e,r:r['stages']['probe']['attempts'][0].update(assignment_id='forged')]
        for index,mutate in enumerate(mutations):
            o,e,r=owner_fixture();mutate(o,e,r)
            with self.subTest(index=index):self.assertFalse(_recorded_success(o,e,r))

    def test_recorded_v2_requires_both_fresh_mission_and_frozen_behavior_checks(self):
        from generative_driver.benchmark_support.suite_reporting import _recorded_success
        for missing in ('fresh_worker_mission','frozen_final_behavior'):
            with self.subTest(missing=missing):
                owner,events,report=owner_fixture()
                reuse=owner['accepted_handoffs'][-1]
                reuse['checks']=[c for c in reuse['checks'] if c['name']!=missing]
                events[-1]['data']=copy.deepcopy(reuse)
                for gates in (report['accepted_gates'],owner['benchmark_summary']['accepted_gates']):
                    gates[-1].update(passed=1,total=1)
                self.assertFalse(_recorded_success(owner,events,report))

    def test_legacy_uses_coherent_owner_handoffs_without_invented_v2_fields(self):
        from generative_driver.benchmark_support.suite_reporting import _recorded_success
        owner,events,report=owner_fixture()
        owner.update(case='tq9',case_pin={'case_id':'tq9','evaluator_version':'2'});owner.pop('benchmark_summary')
        report.update(schema='benchmark-report/1',case='tq9')
        for key in ('accepted_gates','evaluations','final_evaluation'):report.pop(key)
        for handoff in owner['accepted_handoffs']:report['stages'][handoff['stage']]['checks']=copy.deepcopy(handoff['checks'])
        self.assertTrue(_recorded_success(owner,events,report))
        report['stages']['probe']['checks'][0]['name']='different'
        self.assertFalse(_recorded_success(owner,events,report))

class ExportTests(unittest.TestCase):
    def test_report_run_forwards_no_autostart_through_all_event_pages(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.reporting import report_run
        calls=[]
        def owner(method,params,**kwargs):
            calls.append((method,kwargs.get('autostart')))
            if method=='result':return {'ok':True,'run_id':'r','status':'failed','created':0,'updated':1}
            events=[{'id':i,'kind':'noop','time':i,'data':{}} for i in range(1,501)] if params['after']==0 else []
            return {'ok':True,'run_id':'r','events':events,'cursor':500}
        with tempfile.TemporaryDirectory() as temporary,patch('generative_driver.client.call',side_effect=owner):
            report_run('r',home=temporary,output=Path(temporary)/'report.json',autostart=False)
        self.assertEqual(calls,[('result',False),('events',False),('events',False)])

    def test_explicit_output_is_required_before_contacting_owner(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support.suite_reporting import report_suite
        with patch('generative_driver.client.call',side_effect=AssertionError('owner contacted')):
            for output in (None,'',' '):
                with self.subTest(output=output),self.assertRaisesRegex(ValueError,'output'):report_suite('suite',output=output)


def export_fixture(root):
    """Two sealed toy trials served through a fake external IPC boundary; real report/seal/regrade code."""
    from pathlib import Path
    from test_benchmark_v2_evidence import SealedEvidenceTests
    from generative_driver.benchmark_support.evidence import seal_run_evidence,public_pin,public_v2_report
    from generative_driver.benchmark_support.snapshots import canonical_digest
    manifest,trials,_,experiment=fixture()
    manifest['entries']=manifest['entries'][:2]
    manifest['entries'][0]['scenario']='original'
    manifest['entries'][0]['case_seed']=manifest['entries'][1]['case_seed']=7
    experiment['comparison_identity'].update(manifest=manifest,manifest_sha256=_test_digest(manifest),trials=[] ,entry_pins=[],time_policy=[{},{}])
    experiment['execution_snapshots']=[]
    owners={};event_rows={};evidence_files={};password_files={};slots=[]
    for index,entry in enumerate(manifest['entries']):
        rid=f'r{index+1}';payload=SealedEvidenceTests().payload()
        pin=payload['case_pin'];pin.update(case_id=entry['case'],family='toy-family',scope='full-workflow',evidence_track='firmware',execution='actual-agent-emulation')
        pin['manifest'].update(id=entry['case'],version='4',evaluator_version='4',execution='actual-agent-emulation')
        snapshot=payload['execution_snapshot'];snapshot['snapshot_sha256']=canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
        private=root/'runs'/rid/'benchmark';private.mkdir(parents=True)
        (private/'execution.json').write_text(json.dumps(snapshot),encoding='utf-8')
        password=root/f'{rid}.password';password.write_text('test-only password',encoding='utf-8')
        sealed=seal_run_evidence(payload,private/'run-evidence.enc',password)
        handoffs=[];events=[]
        for n,gate in enumerate(payload['accepted_gates']):
            handoff={k:gate[k] for k in ('stage','revision','assignment_id')}
            handoff.update(route=None,artifacts=[{'path':'/private/package','sha256':gate['artifact_sha256'],'kind':'evidence'}],checks=[{'name':check['id'],'passed':check['passed']} for check in gate['checks']])
            handoffs.append(handoff)
            events.extend([{'id':2*n+1,'kind':'stage.assigned','data':{'id':gate['assignment_id'],'stage':gate['stage'],'revision':0},'time':n},
                {'id':2*n+2,'kind':'stage.accepted','data':handoff,'time':n+.5}])
        gates=[{k:g[k] for k in ('stage','revision','assignment_id','artifact_sha256')}|{'verdict':'passed','passed':len(g['checks']),'total':len(g['checks'])} for g in payload['accepted_gates']]
        summary={'accepted_gates':gates,'evaluations':[{'phase':'final','revision':0,'frozen_artifact_sha256':'a'*64,'verdict':'passed','passed':1,'total':1}],
            'final_evaluation':{'verdict':'passed','passed':1,'total':1,'evidence_sha256':sealed['sha256']}}
        state={**summary,'stage_verdicts':{s:{'status':'passed','checks':[{'name':'toy','passed':True}]} for s in STAGES},'unknown':{'answer':'PRIVATE'}}
        (private/'state.json').write_text(json.dumps(state),encoding='utf-8')
        owner={'ok':True,'run_id':rid,'case':entry['case'],'case_pin':pin,'snapshot_sha256':snapshot['snapshot_sha256'],'created':0,'updated':10,
            'status':'completed','outcome_category':'completed','stopping':False,'uncertain_effect':False,'reason':'PRIVATE /private/reason','progress':{'revision':0,'next_stage':None},
            'accepted_handoffs':handoffs,'evaluator_verdicts':[{'stage':s,'assignment_id':s+'-0','verdict':'passed',**({'passed':1,'total':1,'final_evaluation':True} if s=='reuse' else {})} for s in ('emit','reuse')],
            'worker_reports':[{'stage':s,'assignment_id':s+'-0','status':'completed','report':{'status':'completed'},'elapsed_seconds':1} for s in STAGES],
            'benchmark_summary':summary}
        owners[rid]=owner;event_rows[rid]=events
        slot={**entry,'ordinal':index,'entry_index':index,'repeat_index':0,'trial_key':f'entry-{index:03d}-repeat-000'}
        experiment['comparison_identity']['trials'].append(slot);experiment['comparison_identity']['entry_pins'].append(public_pin(pin))
        experiment['execution_snapshots'].append({'trial_key':slot['trial_key'],'snapshot_sha256':snapshot['snapshot_sha256']})
        slots.append({**{k:v for k,v in slot.items() if k!='entry_index'},'status':'finished','outcome_category':'completed','run_id':rid,'interventions':[]})
        evidence_files[slot['trial_key']]=private/'run-evidence.enc';password_files[entry['case']]=password
    experiment['comparison_identity']['evaluator_revision']=snapshot['executed']['evaluator_revision']
    experiment['dimensions']={'runtime':'scripted-contract-fixture','skills_revision':snapshot['executed']['skills_revision'],'toolchain_revision':snapshot['executed']['toolchain_revision']}
    calls=[]
    def call(method,params,**kwargs):
        calls.append((method,kwargs.get('autostart')))
        if method=='suite_result':
            offset=params.get('offset',0)
            return {'ok':True,'suite_id':'suite-toy','status':'completed','created':0,'updated':20,'experiment':experiment,'trials':slots[offset:offset+1],'next_offset':offset+1 if offset==0 else None}
        if method=='result':return copy.deepcopy(owners[params['run_id']])
        if method=='events':
            rows=[row for row in event_rows[params['run_id']] if row['id']>params.get('after',0)]
            return {'ok':True,'run_id':params['run_id'],'events':copy.deepcopy(rows),'cursor':rows[-1]['id'] if rows else params.get('after',0)}
        raise AssertionError(method)
    return call,calls,owners,evidence_files,password_files


def _test_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class ExportIntegrationTests(unittest.TestCase):
    def test_export_uses_owner_identity_and_real_report_producer_with_relative_files(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.suite_reporting import report_suite
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);call,calls,owners,_,_=export_fixture(root)
            with patch('generative_driver.client.call',side_effect=call):result=report_suite('suite-toy',home=root,output=root/'export')
            self.assertEqual(result['success']['passed'],2)
            self.assertTrue(all(flag is False for _,flag in calls))
            self.assertEqual(sum(method=='suite_result' for method,_ in calls),2)
            self.assertTrue((root/'export/suite.json').is_file());self.assertTrue((root/'export/report.md').is_file())
            self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('/private',json.dumps(result))
            for path in (root/'export').rglob('*'):
                if path.is_file():
                    self.assertNotIn('PRIVATE',path.read_text(encoding='utf-8'))
                    self.assertNotIn('/private',path.read_text(encoding='utf-8'))
            self.assertEqual(list((root/'export/trials').rglob('*.md')),[])
            for trial in result['trials']:
                saved=root/'export/trials'/trial['trial_key']/'report.json'
                self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),trial['report_sha256'])
            def changed_experiment(method,params,**kwargs):
                response=call(method,params,**kwargs)
                if method=='suite_result':
                    response=copy.deepcopy(response);response['experiment']['comparison_identity']['evaluator_revision']='0'*64
                return response
            with patch('generative_driver.client.call',side_effect=changed_experiment),self.assertRaises(ValueError):
                report_suite('suite-toy',home=root,output=root/'wrong-evaluator')
            owners['r1']['snapshot_sha256']='0'*64
            with patch('generative_driver.client.call',side_effect=call),self.assertRaises(ValueError):report_suite('suite-toy',home=root,output=root/'refused')

    def test_regrade_requires_two_matching_sidecars_and_case_keyed_handles(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.suite_reporting import report_suite
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);call,_,_,evidence,passwords=export_fixture(root)
            with patch('generative_driver.client.call',side_effect=call):
                result=report_suite('suite-toy',home=root,output=root/'regraded',evidence_files=evidence,password_files=passwords)
                self.assertEqual(result['qualification'],'regraded-encrypted-evidence');self.assertEqual(result['success']['passed'],2)
                missing=dict(evidence);missing.pop('entry-001-repeat-000')
                with self.assertRaises(ValueError):report_suite('suite-toy',home=root,output=root/'missing',evidence_files=missing,password_files=passwords)
                swapped=dict(evidence);swapped['entry-000-repeat-000']=evidence['entry-001-repeat-000']
                with self.assertRaises(ValueError):report_suite('suite-toy',home=root,output=root/'swapped',evidence_files=swapped,password_files=passwords)

class RepairedQualificationTests(unittest.TestCase):
    def test_failed_diagnostic_before_coherent_success_is_visible_but_not_terminal(self):
        from generative_driver.benchmark_support.suite_reporting import _recorded_success
        owner,events,report=owner_fixture()
        for handoff in owner['accepted_handoffs']:
            if handoff['stage']!='acquire':handoff['revision']=1;handoff['assignment_id']+='-1'
        for gate in owner['benchmark_summary']['accepted_gates']:
            if gate['stage']!='acquire':gate['revision']=1;gate['assignment_id']+='-1'
        for row in owner['evaluator_verdicts']:row['assignment_id']+='-1'
        for row in owner['benchmark_summary']['evaluations']:row['revision']=1
        for stage in STAGES:
            if stage!='acquire':report['stages'][stage]['attempts'][0]['assignment_id']+='-1'
        prior={'stage':'interpret','revision':0,'assignment_id':'interpret-before-repair','route':None,
            'artifacts':[{'path':'/private/old','sha256':'0'*64,'kind':'evidence'}],'checks':[{'name':'toy-check','passed':True}]}
        owner['accepted_handoffs'].insert(1,prior)
        owner['benchmark_summary']['accepted_gates'].insert(1,{'stage':'interpret','revision':0,'assignment_id':'interpret-before-repair','artifact_sha256':'0'*64,'verdict':'passed','passed':1,'total':1})
        owner['benchmark_summary']['evaluations'].insert(0,{'phase':'diagnostic','revision':0,'frozen_artifact_sha256':'0'*64,'verdict':'failed','passed':1,'total':2})
        owner['evaluator_verdicts'].insert(0,{'stage':'probe','assignment_id':'failed-probe','verdict':'failed','passed':1,'total':2})
        events=[]
        for n,h in enumerate(owner['accepted_handoffs']):
            events.extend([{'id':2*n+1,'kind':'stage.assigned','time':n,'data':{'id':h['assignment_id'],'stage':h['stage'],'revision':h['revision']}},
                {'id':2*n+2,'kind':'stage.accepted','time':n+.5,'data':copy.deepcopy(h)}])
        events.append({'id':100,'kind':'stage.assigned','time':1.5,'data':{'id':'failed-probe','stage':'probe','revision':0}})
        report.update(copy.deepcopy(owner['benchmark_summary']));report['evaluator_verdicts']=copy.deepcopy(owner['evaluator_verdicts'])
        self.assertTrue(_recorded_success(owner,events,report))
        owner['benchmark_summary']['evaluations'].insert(0,{'phase':'final','revision':0,'frozen_artifact_sha256':'0'*64,'verdict':'failed','passed':1,'total':2})
        report['evaluations']=copy.deepcopy(owner['benchmark_summary']['evaluations'])
        self.assertFalse(_recorded_success(owner,events,report))


class AdaptedDiagnosticIdentityTests(unittest.TestCase):
    def test_diagnostic_binds_accepted_probe_model_not_pre_adaptation_interpretation(self):
        from generative_driver.benchmark_support.suite_reporting import _recorded_success
        owner,events,report=owner_fixture()
        owner['accepted_handoffs'][2]['artifacts'][0]['sha256']='1'*64
        owner['benchmark_summary']['accepted_gates'][2]['artifact_sha256']='1'*64
        owner['benchmark_summary']['evaluations'][0]['frozen_artifact_sha256']='1'*64
        events[5]['data']=copy.deepcopy(owner['accepted_handoffs'][2])
        report.update(copy.deepcopy(owner['benchmark_summary']))
        self.assertTrue(_recorded_success(owner,events,report))
        owner['benchmark_summary']['evaluations'][0]['frozen_artifact_sha256']='2'*64
        report.update(copy.deepcopy(owner['benchmark_summary']))
        self.assertFalse(_recorded_success(owner,events,report))

class PublicBoundaryFixTests(unittest.TestCase):
    def test_nested_experiment_values_cannot_retain_private_payloads(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        private={'unfamiliar':'/private/PRIVATE_POLICY'}
        changes=[lambda e:e['comparison_identity']['intervention_policy'].update(scoped_tool_approval=private),
            lambda e:e['comparison_identity']['intervention_policy'].update(max_model_repairs=private),
            lambda e:e['comparison_identity']['intervention_policy'].update(max_model_repairs=True),
            lambda e:e['comparison_identity']['intervention_policy'].update(scoped_tool_approval='physical'),
            lambda e:e['comparison_identity']['entry_pins'][0].update(variant=private),
            lambda e:e['comparison_identity']['entry_pins'][0].update(case_version=private),
            lambda e:e['comparison_identity']['entry_pins'][0].update(family=private),
            lambda e:e['comparison_identity'].update(execution=private),
            lambda e:e['comparison_identity']['time_policy'].__setitem__(0,{'clock':private}),
            lambda e:e['dimensions'].update(model=private),
            lambda e:e['dimensions'].update(max_turns=True),
            lambda e:e['dimensions'].update(skills_revision=private),
            lambda e:e['comparison_identity']['entry_pins'][0].update(image_hashes={'/private/PRIVATE_IMAGE':'a'*64}),
            lambda e:e['comparison_identity']['entry_pins'][0].update(image_hashes={'C:\\private\\PRIVATE_IMAGE':'a'*64}),
            lambda e:e['comparison_identity']['entry_pins'][0].update(image_hashes={7:'a'*64})]
        for index,change in enumerate(changes):
            m,t,r,e=fixture();change(e)
            with self.subTest(index=index),self.assertRaises(ValueError):
                aggregate_suite(m,t,{},'recorded-controller-verdicts',experiment=e)
        m,t,r,e=fixture();e['comparison_identity']['intervention_policy']['scoped_tool_approval']='emulator'
        result=aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        e['comparison_identity']['intervention_policy']['scoped_tool_approval']=private
        self.assertEqual(result['experiment']['comparison_identity']['intervention_policy']['scoped_tool_approval'],'emulator')
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_every_exported_usage_counter_is_validated_at_each_level(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        fields=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens','observed_total_tokens','reported_attempts','attempts')
        for level in ('report','stage','attempt'):
            for field in fields:
                for invalid in (-7,True,1.5,float('nan'),float('inf'),'7',{'private':'PRIVATE'}):
                    m,t,r,e=fixture();record=r['r1']
                    if level!='report':record=record['stages']['probe']
                    if level=='attempt':record=record['attempts'][0]
                    record.setdefault('usage',{})[field]=invalid
                    with self.subTest(level=level,field=field,invalid=invalid),self.assertRaises(ValueError):
                        aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
            for invalid in (-.1,1.1,True,float('inf')):
                m,t,r,e=fixture();record=r['r1'] if level=='report' else r['r1']['stages']['probe']
                if level=='attempt':record=record['attempts'][0]
                record.setdefault('usage',{})['coverage']=invalid
                with self.subTest(level=level,coverage=invalid),self.assertRaises(ValueError):aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        m,t,r,e=fixture()
        for record in (r['r1'],r['r1']['stages']['probe'],r['r1']['stages']['probe']['attempts'][0]):
            record['usage']={'input_tokens':None,'output_tokens':0,'total_tokens':None,'coverage':.5,'counting':'observed subset','unfamiliar':{'private':'PRIVATE'}}
        result=aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        attempt=result['trials'][0]['stages']['probe']['attempts'][0]
        self.assertIsNone(attempt['usage']['total_tokens']);self.assertEqual(attempt['usage']['output_tokens'],0)
        self.assertEqual(attempt['usage']['coverage'],.5);self.assertNotIn('PRIVATE',json.dumps(result))

    def test_all_projected_duration_fields_reject_negative_nonfinite_and_boolean_values(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        groups={'report':('elapsed_seconds','worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds','repair_seconds','original_budget_seconds','effective_budget_seconds'),
            'stage':('worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds'),'attempt':('elapsed_seconds',)}
        for level,fields in groups.items():
            for field in fields:
                for invalid in (-1,True,float('nan'),float('inf')):
                    m,t,r,e=fixture();record=r['r1'] if level=='report' else r['r1']['stages']['probe']
                    if level=='attempt':record=record['attempts'][0]
                    record[field]=invalid
                    with self.subTest(level=level,field=field,invalid=invalid),self.assertRaises(ValueError):aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)

        from generative_driver.benchmark_support.suite_reporting import _public_report
        for value in (None,0,1.5):
            m,t,r,e=fixture()
            r['r1'].update(original_budget_seconds=value,effective_budget_seconds=value)
            aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
            public=_public_report(r['r1'])
            self.assertEqual(public['original_budget_seconds'],value)
            self.assertEqual(public['effective_budget_seconds'],value)
        m,t,r,e=fixture()
        aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        self.assertNotIn('original_budget_seconds',_public_report(r['r1']))
        self.assertNotIn('effective_budget_seconds',_public_report(r['r1']))

    def test_published_intervention_durations_are_validated(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        for source in ('trial','report'):
            for field in ('extension_seconds','old_budget_seconds','new_budget_seconds'):
                for invalid in (-1,True,float('inf')):
                    m,t,r,e=fixture();record=t[0] if source=='trial' else r['r1']
                    record['interventions']=[{'event_id':1,'kind':'run.budget_extended','time':1,field:invalid}]
                    with self.subTest(source=source,field=field,invalid=invalid),self.assertRaises(ValueError):
                        aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)

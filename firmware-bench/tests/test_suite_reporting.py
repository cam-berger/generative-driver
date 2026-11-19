"""Suite reporting uses public toy data and scripted owner records, never model baselines."""
import copy
import hashlib
import json
import unittest

from firmware_bench.benchmark_support.truth import read_password
# The stand-in for the service answers the suite calls too, which a reader asks of the suite book.
from suite_fixtures import standing_in

STAGES=('acquire','interpret','probe','ground','emit','reuse')

def fixture():
    manifest={'schema':'benchmark-suite/1','id':'toy','version':'1','entries':[
        {'case':'tq9-v2','scenario':'original','case_seed':0},
        {'case':'sampled-sensor-v1','scenario':'original','case_seed':0},
        {'case':'parameter-store-v1','scenario':'original','case_seed':0}],
        'repetitions':1,'child_budget_seconds':10,'suite_budget_seconds':60,'max_active_children':1}
    pins=[];slots=[];trials=[];snapshots=[];reports={}
    for index,(entry,family) in enumerate(zip(manifest['entries'],('one','one','two'))):
        pin={'case_id':entry['case'],'case_version':'5','evaluator_version':'5','family':family,
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
    def test_rejects_incomplete_misidentified_and_invalid_measurements(self):
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
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

    def test_per_case_revisions_are_read_only_beside_the_revision_they_combine_into(self):
        # Catches per-case revisions that miss one of the suite's cases or are not those its comparison identity names:
        # trials would be checked against revisions the suite is not compared by. A suite without them is read as before.
        from firmware_bench.benchmark_support.snapshots import canonical_digest
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
        manifest,trials,reports,experiment=fixture()
        revisions={entry['case']:str(index+1)*64 for index,entry in enumerate(manifest['entries'])}
        experiment['comparison_identity']['evaluator_revision']=canonical_digest(revisions)
        experiment['evaluator_revisions']=revisions
        result=aggregate_suite(manifest,trials,reports,'recorded-controller-verdicts',experiment=copy.deepcopy(experiment))
        self.assertEqual(result['experiment']['evaluator_revisions'],revisions)
        for name,change in (('combined',lambda e:e['comparison_identity'].update(evaluator_revision='9'*64)),
                            ('missing case',lambda e:e['evaluator_revisions'].pop('tq9-v2')),
                            ('other case',lambda e:e['evaluator_revisions'].update({'other-v1':'9'*64})),
                            ('digest',lambda e:e['evaluator_revisions'].update({'tq9-v2':'not-a-digest'}))):
            changed=copy.deepcopy(experiment);change(changed)
            with self.subTest(name),self.assertRaisesRegex(ValueError,'evaluator revisions'):
                aggregate_suite(manifest,trials,reports,'recorded-controller-verdicts',experiment=changed)

    def test_stale_case_versions_are_rejected_even_when_report_and_experiment_agree(self):
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
        m,t,r,e=fixture()
        e['comparison_identity']['entry_pins'][0]['case_version']='2'
        r['r1']['case_pin']['case_version']='2'
        with self.assertRaisesRegex(ValueError,'version|identity'):
            aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)

    def test_missing_report_preserves_known_time_without_inventing_total(self):
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
        m,t,r,e=fixture();r.pop('r2')
        result=aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)
        self.assertIsNone(result['worker_seconds']);self.assertEqual(result['observed_worker_seconds'],4)
        self.assertIsNone(result['usage']['total_tokens'])

    def test_public_descriptive_metrics_exclude_unknown_payloads(self):
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
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
    def test_saved_file_comparison_creates_no_owner(self):
        import os,subprocess,sys,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);saved=root/'suite.json';saved.write_text(json.dumps(comparison_fixture()),encoding='utf-8')
            home=root/'unused owner'
            program='import json,sys; from firmware_bench.benchmark_support.suite_reporting import compare_suites; print(json.dumps(compare_suites(sys.argv[1],sys.argv[1])))'
            result=subprocess.run([sys.executable,'-c',program,str(saved)],env={**os.environ,'GENERATIVE_DRIVER_HOME':str(home)},capture_output=True,text=True,check=True)
            self.assertTrue(json.loads(result.stdout)['compatible']);self.assertFalse(home.exists())

class ExportTests(unittest.TestCase):
    def test_report_run_forwards_no_autostart_through_all_event_pages(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from firmware_bench.benchmark_support.run_reporting import report_run
        calls=[]
        def owner(method,params,**kwargs):
            calls.append((method,kwargs.get('autostart')))
            if method=='result':return {'ok':True,'run_id':'r','status':'failed','created':0,'updated':1}
            events=[{'id':i,'kind':'noop','time':i,'data':{}} for i in range(1,501)] if params['after']==0 else []
            return {'ok':True,'run_id':'r','events':events,'cursor':500}
        with tempfile.TemporaryDirectory() as temporary,standing_in(side_effect=owner):
            report_run('r',home=temporary,output=Path(temporary)/'report.json',autostart=False)
        self.assertEqual(calls,[('result',False),('events',False),('events',False)])

    def test_explicit_output_is_required_before_contacting_owner(self):
        from unittest.mock import patch
        from firmware_bench.benchmark_support.suite_reporting import report_suite
        with standing_in(side_effect=AssertionError('owner contacted')):
            for output in (None,'',' '):
                with self.subTest(output=output),self.assertRaisesRegex(ValueError,'output'):report_suite('suite',output=output)

    def test_an_empty_audit_path_is_refused_before_contacting_owner(self):
        # Catches an audit path that is empty (an unset shell variable, say) that audits nothing, or flags every command
        # of every run, once the owner has been asked; a path to audit is never accepted blank.
        from unittest.mock import patch
        from firmware_bench.benchmark_support.suite_reporting import report_suite
        with standing_in(side_effect=AssertionError('owner contacted')):
            for paths in (('',),('/vault',' ')):
                with self.subTest(paths=paths),self.assertRaisesRegex(ValueError,'audit path'):report_suite('suite',output='out',audit_paths=paths)


def export_fixture(root,per_case=True,one_case=False):
    """Two sealed toy trials of two families served through a fake external IPC boundary; real report/seal/regrade
    code. Each child is stamped with its own case's revision, and the suite freezes them beside the revision they
    combine into. With `per_case` false, the suite is one frozen before cases had their own grader identities: every
    child is stamped with its one revision. With `one_case`, both trials are of one case, under one password file."""
    from pathlib import Path
    from test_benchmark_v2_evidence import SealedEvidenceFixture
    from firmware_bench.benchmark_support.evidence import seal_run_evidence,public_pin,public_v2_report
    from firmware_bench.benchmark_support.grader_identity import case_revisions
    from firmware_bench.benchmark_support.snapshots import canonical_digest
    manifest,trials,_,experiment=fixture()
    manifest['entries']=manifest['entries'][:2]
    manifest['entries'][0]['scenario']='original'
    manifest['entries'][0]['case_seed']=manifest['entries'][1]['case_seed']=7
    if one_case:manifest['entries'][1].update(case=manifest['entries'][0]['case'],case_seed=8)
    experiment['comparison_identity'].update(manifest=manifest,manifest_sha256=_test_digest(manifest),trials=[] ,entry_pins=[],time_policy=[{},{}])
    experiment['execution_snapshots']=[]
    owners={};event_rows={};evidence_files={};password_files={};slots=[]
    for index,entry in enumerate(manifest['entries']):
        rid=f'r{index+1}';payload=SealedEvidenceFixture().payload()
        pin=payload['case_pin'];pin.update(case_id=entry['case'],case_seed=entry['case_seed'],family='toy-family',scope='full-workflow',evidence_track='firmware',execution='actual-agent-emulation')
        pin['manifest'].update(id=entry['case'],version='5',evaluator_version='5',execution='actual-agent-emulation')
        snapshot=payload['execution_snapshot']
        if per_case:snapshot['executed'].update(case_revisions((entry['case'],)))
        else:snapshot['executed'].pop('evaluator_revisions')
        snapshot['snapshot_sha256']=canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_sha256'})
        private=root/'runs'/rid/'benchmark';private.mkdir(parents=True)
        (private/'execution.json').write_text(json.dumps(snapshot),encoding='utf-8')
        password=root/f'{rid}.password';password.write_text('test-only password',encoding='utf-8')
        sealed=seal_run_evidence(payload,private/'run-evidence.enc',read_password(password))
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
        # The run's own records lie under `supplied/`; the snapshot stays under `benchmark/`.
        (private.parent/'supplied').mkdir()
        (private.parent/'supplied/state.json').write_text(json.dumps(state),encoding='utf-8')
        owner={'ok':True,'run_id':rid,'created':0,'updated':10,
            'client_record':{'case':entry['case'],'case_pin':pin,'snapshot_sha256':snapshot['snapshot_sha256']},
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
    if per_case:
        frozen=case_revisions([entry['case'] for entry in manifest['entries']])
        experiment['comparison_identity']['evaluator_revision']=frozen['evaluator_revision']
        experiment['evaluator_revisions']=frozen['evaluator_revisions']
    else:experiment['comparison_identity']['evaluator_revision']=snapshot['executed']['evaluator_revision']
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
    def test_each_trial_is_compared_with_the_frozen_revision_of_its_own_case(self):
        # Catches a trial checked against the suite's combined revision, which no child of a suite of several families is
        # stamped with, or against another case's revision.
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from firmware_bench.benchmark_support.snapshots import canonical_digest
        from firmware_bench.benchmark_support.suite_reporting import report_suite
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);call,_,_,_,_=export_fixture(root)
            with standing_in(side_effect=call):
                self.assertEqual(report_suite('suite-toy',home=root,output=root/'export')['success']['passed'],2)
            def swapped(method,params,**kwargs):
                # The two cases' frozen revisions trade places, still combined into the revision the suite names.
                response=call(method,params,**kwargs)
                if method=='suite_result':
                    response=copy.deepcopy(response);experiment=response['experiment']
                    revisions=experiment['evaluator_revisions'];first,second=sorted(revisions)
                    revisions[first],revisions[second]=revisions[second],revisions[first]
                    experiment['comparison_identity']['evaluator_revision']=canonical_digest(revisions)
                return response
            with standing_in(side_effect=swapped),self.assertRaisesRegex(ValueError,'Executed evaluator differs'):
                report_suite('suite-toy',home=root,output=root/'swapped')

    def test_an_earlier_suite_is_not_exported_with_an_audit_it_cannot_apply(self):
        # Catches a gated suite exported with forbidden paths to audit that audits nothing and says nothing: only an
        # assistive suite scores a flagged trial, so asking for the audit of an earlier one is an error.
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from firmware_bench.benchmark_support.suite_reporting import report_suite
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);call,_,_,_,_=export_fixture(root)
            with standing_in(side_effect=call):
                with self.assertRaisesRegex(ValueError,'assistive'):report_suite('suite-toy',home=root,output=root/'refused',audit_paths=('/vault',))
                self.assertFalse((root/'refused').exists())
                self.assertEqual(report_suite('suite-toy',home=root,output=root/'export')['success']['passed'],2)

    def test_the_export_names_its_tree_hash_for_what_it_is_and_leaves_evaluator_revision_to_the_grader(self):
        # Catches the exporter's whole-tree hash of benchmark_support named `evaluator_revision`, the name of the grader
        # identity that a run is stamped with and the grade checks.
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import hashlib
        from firmware_bench.benchmark_support import suite_reporting, tool_install
        from firmware_bench.benchmark_support.integrity import tree_hash
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);call,_,_,_,_=export_fixture(root)
            with standing_in(side_effect=call):
                result=suite_reporting.report_suite('suite-toy',home=root,output=root/'export')
            self.assertEqual(set(result['exporter']),{'benchmark_support_tree_sha256','toolchain_revision'})
            self.assertEqual(result['exporter']['benchmark_support_tree_sha256'],tree_hash(Path(suite_reporting.__file__).parent))
            # The exporter's toolchain revision joins the tool's package folder and the benchmark's, which were one
            # folder before the benchmark became a package of its own (12-B2 of Feature 12 of the untangle Run).
            package=Path(suite_reporting.__file__).resolve().parents[1]
            self.assertEqual(result['exporter']['toolchain_revision'],
                             hashlib.sha256((tree_hash(tool_install.tool_folder())+tree_hash(package)).encode()).hexdigest())
            self.assertNotEqual(result['exporter']['toolchain_revision'],tree_hash(package))
            saved=json.loads((root/'export/suite.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['exporter'],result['exporter'])

class PublicBoundaryFixTests(unittest.TestCase):
    def test_nested_experiment_values_cannot_retain_private_payloads(self):
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
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
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
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
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
        groups={'report':('elapsed_seconds','worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds','repair_seconds','original_budget_seconds','effective_budget_seconds'),
            'stage':('worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds'),'attempt':('elapsed_seconds',)}
        for level,fields in groups.items():
            for field in fields:
                for invalid in (-1,True,float('nan'),float('inf')):
                    m,t,r,e=fixture();record=r['r1'] if level=='report' else r['r1']['stages']['probe']
                    if level=='attempt':record=record['attempts'][0]
                    record[field]=invalid
                    with self.subTest(level=level,field=field,invalid=invalid),self.assertRaises(ValueError):aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)

        from firmware_bench.benchmark_support.suite_reporting import _public_report
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
        from firmware_bench.benchmark_support.suite_reporting import aggregate_suite
        for source in ('trial','report'):
            for field in ('extension_seconds','old_budget_seconds','new_budget_seconds'):
                for invalid in (-1,True,float('inf')):
                    m,t,r,e=fixture();record=t[0] if source=='trial' else r['r1']
                    record['interventions']=[{'event_id':1,'kind':'run.budget_extended','time':1,field:invalid}]
                    with self.subTest(source=source,field=field,invalid=invalid),self.assertRaises(ValueError):
                        aggregate_suite(m,t,r,'recorded-controller-verdicts',experiment=e)

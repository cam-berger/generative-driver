"""Allowlisted suite measurements; owner qualification and offline comparison."""

import copy
import hashlib
import json
import math
import statistics
from .suites import normalize_manifest,expand_trials
from .evidence import public_pin,public_v2_report,PIN
from ..benchmark import STAGES

QUALIFICATIONS={'recorded-controller-verdicts','regraded-encrypted-evidence'}

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _sha(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)

def _exact(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys):raise ValueError('Incomplete or unknown experiment fields')

def _number(value,integer=False):
    if value is not None and (type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or value<0):
        raise ValueError('Invalid observed resource measurement')

def _experiment(value):
    value=copy.deepcopy(value)
    _exact(value,('schema','comparison_identity','dimensions','execution_snapshots'))
    if value['schema']!='benchmark-suite-experiment/1':raise ValueError('Unsupported experiment')
    identity=value['comparison_identity']
    _exact(identity,('manifest','manifest_sha256','entry_pins','trials','repetitions','execution','evidence_track','scope','time_policy','evaluator_revision','budgets','intervention_policy'))
    manifest=normalize_manifest(identity['manifest']);slots=expand_trials(manifest)
    if identity['manifest_sha256']!=_digest(manifest) or identity['trials']!=slots or identity['repetitions']!=manifest['repetitions']:
        raise ValueError('Frozen suite identity mismatch')
    if not _sha(identity['evaluator_revision']):raise ValueError('Missing evaluator identity')
    pins=identity['entry_pins']
    if len(pins)!=len(manifest['entries']) or len(identity['time_policy'])!=len(pins):raise ValueError('Incomplete entry pins')
    for entry,pin,policy in zip(manifest['entries'],pins,identity['time_policy']):
        if not isinstance(pin,dict) or set(pin)-set((*PIN,'pin_sha256','image_hashes')):raise ValueError('Unknown public pin field')
        if any(pin.get(k)!=entry[v] for k,v in (('case_id','case'),('scenario_id','scenario'),('case_seed','case_seed'))):raise ValueError('Entry pin mismatch')
        if any(pin.get(k)!=identity[k] for k in ('execution','evidence_track','scope')) or not pin.get('family'):raise ValueError('Mixed or missing pin scope')
        if any(not _sha(pin.get(k)) for k in ('manifest_sha256','truth_sha256','pin_sha256')) or not pin.get('case_version') or not pin.get('evaluator_version'):raise ValueError('Incomplete pin identity')
        if not isinstance(pin.get('image_hashes'),dict) or any(not _sha(v) for v in pin['image_hashes'].values()):raise ValueError('Invalid image identities')
        if not isinstance(policy,dict) or set(policy)-{'probe','package_calls','clock','limitation'} or any(not isinstance(v,str) for v in policy.values()):raise ValueError('Invalid time policy')
    _exact(identity['budgets'],('original','effective'))
    original=identity['budgets']['original'];effective=identity['budgets']['effective']
    _exact(original,('child_budget_seconds','suite_budget_seconds'))
    _exact(effective,('child_budget_seconds','suite_budget_seconds','child_budget_overrides'))
    if original!={k:manifest[k] for k in original}:raise ValueError('Original budget mismatch')
    for key in original:
        _number(effective[key])
        if effective[key] is None or not original[key]<=effective[key]<=604800:raise ValueError('Invalid effective budget')
    positions={slot['trial_key']:i for i,slot in enumerate(slots)};prior=-1
    for override in effective['child_budget_overrides']:
        _exact(override,('trial_key','budget_seconds'));position=positions.get(override['trial_key'],-1)
        _number(override['budget_seconds'])
        if position<=prior or not original['child_budget_seconds']<override['budget_seconds']<=604800:raise ValueError('Invalid child budget override')
        prior=position
    _exact(identity['intervention_policy'],('scoped_tool_approval','max_model_repairs','max_maintenance_cycles'))
    dimensions=value['dimensions']
    if set(dimensions)-{'runtime','model','provider','version','reasoning_effort','max_turns','skills_revision','toolchain_revision'} or any(v is not None and type(v) not in (str,int,float,bool) for v in dimensions.values()):raise ValueError('Unknown dimensions')
    if len(value['execution_snapshots'])!=len(slots):raise ValueError('Incomplete snapshot coverage')
    for slot,snapshot in zip(slots,value['execution_snapshots']):
        _exact(snapshot,('trial_key','snapshot_sha256'))
        if snapshot['trial_key']!=slot['trial_key'] or snapshot['snapshot_sha256'] is not None and not _sha(snapshot['snapshot_sha256']):raise ValueError('Invalid snapshot identity')
    return value

def _maintenance_ok(value):
    return (value.get('false_alarm') is False and type(value.get('drift_claimed')) is bool
        and value.get('drift_claimed')==value.get('drift_observed') and value.get('fresh_reuse_passed') is True
        and (not value['drift_observed'] or value.get('repair_completed') is True and value.get('requalified') is True))

def _counts_ok(value):
    return (value.get('verdict')=='passed' and type(value.get('total')) is int and value['total']>0
        and type(value.get('passed')) is int and value['passed']==value['total'])

def _report_success(report):
    if report.get('verdict')!='passed' or report.get('workflow_status')!='completed':return False
    stages=report.get('stages',{})
    if any(stages.get(stage,{}).get('workflow_status')!='accepted' or stages.get(stage,{}).get('evaluator_status')!='passed' for stage in STAGES):return False
    if report.get('schema')=='benchmark-report/1':return report.get('case')=='tq9'
    gates=report.get('accepted_gates',[])
    return (all(any(gate.get('stage')==stage and _counts_ok(gate) for gate in gates) for stage in STAGES)
        and _counts_ok(report.get('final_evaluation',{})) and _maintenance_ok(report.get('maintenance',{})))

def _validate_trials(manifest,trials,reports,experiment):
    expected=expand_trials(manifest);identity=experiment['comparison_identity']
    if manifest!=identity['manifest'] or len(trials)!=len(expected):raise ValueError('Aggregate requires every frozen slot')
    runs=set()
    for trial,slot,snapshot in zip(trials,expected,experiment['execution_snapshots']):
        if any(type(trial.get(k)) is not type(v) or trial.get(k)!=v for k,v in slot.items()):raise ValueError('Trial differs from frozen slot')
        pin=identity['entry_pins'][slot['entry_index']]
        if trial.get('family',pin['family'])!=pin['family']:raise ValueError('Invented family')
        rid=trial.get('run_id')
        if rid:
            if not isinstance(rid,str) or rid in runs:raise ValueError('Duplicate or invalid run')
            runs.add(rid)
        report=reports.get(rid)
        if report is None:continue
        if (report.get('run_id')!=rid or report.get('case')!=slot['case'] or report.get('scenario_id')!=slot['scenario']
                or report.get('case_seed')!=slot['case_seed'] or public_pin(report.get('case_pin',{}))!=pin
                or report.get('snapshot_sha256')!=snapshot['snapshot_sha256'] or not _sha(snapshot['snapshot_sha256'])):
            raise ValueError('Report does not match frozen trial identity')
        for key in ('elapsed_seconds','worker_seconds','tool_seconds','repair_seconds'):_number(report.get(key))
        for key in ('total_tokens','observed_total_tokens'):_number((report.get('usage') or {}).get(key),True)
        for stage in report.get('stages',{}).values():
            for key in ('worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds'):_number(stage.get(key))
            _number(stage.get('attempt_count'),True)
            for attempt in stage.get('attempts',[]):_number(attempt.get('elapsed_seconds'))
    if set(reports)-runs:raise ValueError('Report outside planned trials')


def aggregate_suite(manifest,trials,reports,qualification,*,experiment):
    if qualification not in QUALIFICATIONS:raise ValueError('Suite aggregates require qualified trial evidence')
    experiment=_experiment(experiment)
    _validate_trials(manifest,trials,reports,experiment)
    trials=[dict(trial,family=experiment['comparison_identity']['entry_pins'][trial['entry_index']]['family']) for trial in trials]
    passed=[trial for trial in trials if trial['status']=='finished' and trial['outcome_category']=='completed'
            and _report_success(reports.get(trial.get('run_id'),{}))]
    started=[trial for trial in trials if trial.get('run_id')]
    finished=[trial for trial in trials if trial['status']=='finished']
    observed=[reports[trial['run_id']] for trial in started if trial['run_id'] in reports]
    families=sorted({trial['family'] for trial in trials})
    rates={family:sum(t['family']==family for t in passed)/sum(t['family']==family for t in trials) for family in families}
    tokens=[(report.get('usage') or {}).get('total_tokens') for report in observed]
    known=[(report.get('usage') or {}).get('observed_total_tokens') for report in observed]
    seconds=[report.get('worker_seconds') for report in observed]
    result={'schema':'benchmark-suite-report/1','qualification':qualification,
        'counts':{'planned':len(trials),'started':len(started),'finished':len(finished),
            'failed':sum(t['outcome_category']=='model' for t in finished),
            'blocked':sum(t['status']=='blocked' for t in trials),
            'cancelled':sum(bool(t.get('run_id')) and t['status']=='cancelled' for t in trials),'not_run':len(trials)-len(started)},
        'success':{'passed':len(passed),'denominator':len(trials),'rate':len(passed)/len(trials)},
        'provisional':len(finished)!=len(trials),'family_success':rates,'family_macro_success':sum(rates.values())/len(rates),
        'worker_seconds':sum(seconds) if started and len(observed)==len(started) and all(type(v) in (int,float) for v in seconds) else None,
        'observed_worker_seconds':sum(v for v in seconds if type(v) in (int,float)) if any(type(v) in (int,float) for v in seconds) else None,
        'usage':{'total_tokens':sum(tokens) if started and len(observed)==len(started) and all(type(v) is int for v in tokens) else None,
            'observed_total_tokens':sum(v for v in known if type(v) is int) if any(type(v) is int for v in known) else None,
            'reported_trials':sum(type(v) is int for v in tokens),'started_trials':len(started)}}

    def rates_by(key):
        return {value:{'passed':sum(t[key]==value for t in passed),'denominator':sum(t[key]==value for t in trials),
            'rate':sum(t[key]==value for t in passed)/sum(t[key]==value for t in trials)} for value in sorted({t[key] for t in trials})}
    first=sum(all(reports[t['run_id']]['stages'][stage].get('attempt_count')==1 for stage in STAGES) for t in passed)
    latencies=[r['elapsed_seconds'] for r in observed if r.get('elapsed_seconds') is not None]
    control_trials=[t for t in trials if t['scenario']=='control']
    control_measurements=[reports.get(t['run_id'],{}).get('maintenance',{}) for t in control_trials if t.get('run_id')]
    controls=[m for m in control_measurements if m.get('evaluable') is True]
    if any(type(m.get('false_alarm')) is not bool for m in controls):raise ValueError('Evaluable control requires false-alarm outcome')
    alarms=sum(m['false_alarm'] for m in controls)
    repairs=[r.get('repair_seconds') for r in observed if r.get('maintenance',{}).get('drift_observed') is True]
    public_trials=[]
    for trial in trials:
        report=reports.get(trial.get('run_id'),{})
        safe=public_v2_report(report)
        row={key:trial[key] for key in ('trial_key','ordinal','entry_index','repeat_index','case','family','scenario','case_seed','run_id','status','outcome_category')}
        row.update({key:safe[key] for key in ('verdict','stages','maintenance','final_evaluation','elapsed_seconds','worker_seconds','tool_seconds','usage') if key in safe})
        if row.get('verdict')=='passed' and trial not in passed:row['verdict']='unqualified'
        row['report_sha256']=_digest(_public_report(report)) if report else None
        row['interventions']=[{key:item[key] for key in ('event_id','kind','time','effect_resolution','scoped_tool_approval','old_budget_seconds','new_budget_seconds','extension_seconds','old_deadline','new_deadline','stage','uncertain_effect','has_observation')
            if key in item and (item[key] is None or type(item[key]) in (str,int,float,bool))} for item in trial.get('interventions',[])]
        public_trials.append(row)
    result.update(experiment=experiment,trials=public_trials,case_success=rates_by('case'),scenario_success=rates_by('scenario'),
        stage_counts={stage:{'accepted':sum(r.get('stages',{}).get(stage,{}).get('workflow_status')=='accepted' for r in observed),
            'attempts':sum(r.get('stages',{}).get(stage,{}).get('attempt_count',0) for r in observed)} for stage in STAGES},
        first_attempt_success=first,repaired_success=len(passed)-first,
        control_coverage={'planned':len(control_trials),'started':len(control_measurements),'evaluable':len(controls),
            'unevaluable':sum(m.get('evaluable') is False for m in control_measurements),
            'unknown':sum(type(m.get('evaluable')) is not bool for m in control_measurements),
            'not_run':len(control_trials)-len(control_measurements)},
        false_alarms={'count':alarms,'denominator':len(controls),'rate':alarms/len(controls) if controls else None},
        latency={'sample_count':len(latencies),'includes_failed_trials':any(r.get('verdict')!='passed' and r.get('elapsed_seconds') is not None for r in observed),
            'min':min(latencies) if latencies else None,'median':statistics.median(latencies) if latencies else None,'max':max(latencies) if latencies else None},
        repair_seconds=sum(repairs) if repairs and all(v is not None for v in repairs) else None,
        tool_seconds=sum(r['tool_seconds'] for r in observed) if started and len(observed)==len(started) and all(r.get('tool_seconds') is not None for r in observed) else None)
    return result


def _public_report(report):
    safe=public_v2_report(report)
    safe['schema']=report.get('schema','benchmark-report/2')
    return safe


def _read_saved(value):
    from pathlib import Path
    return copy.deepcopy(value) if isinstance(value,dict) else json.loads(Path(value).read_text(encoding='utf-8'))


def _saved_report(value):
    report=_read_saved(value)
    if report.get('schema')!='benchmark-suite-report/1' or report.get('qualification') not in QUALIFICATIONS:raise ValueError('Unsupported suite report')
    experiment=_experiment(report['experiment'])
    _validate_trials(experiment['comparison_identity']['manifest'],report['trials'],{},experiment)
    success=report['success'];planned=len(report['trials'])
    if type(success.get('passed')) is not int or not 0<=success['passed']<=planned or success.get('denominator')!=planned or success.get('rate')!=success['passed']/planned:
        raise ValueError('Invalid success denominator')
    if success['passed']!=sum(t.get('verdict')=='passed' and t.get('status')=='finished' and t.get('outcome_category')=='completed' for t in report['trials']):
        raise ValueError('Saved success count differs from trial coverage')
    _number(report.get('usage',{}).get('total_tokens'),True)
    _number(report.get('latency',{}).get('median'))
    return report


def compare_suites(before,after):
    """Compare saved public data only; this does not authenticate either report."""
    result={'schema':'benchmark-suite-comparison/1','compatible':False}
    try:
        before=_saved_report(before);after=_saved_report(after)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {**result,'reason':'Invalid or incomplete saved suite report'}
    result.update(before_success=before['success'],after_success=after['success'])
    if before['experiment']['comparison_identity']!=after['experiment']['comparison_identity']:
        return {**result,'reason':'Frozen experiment identities differ'}
    if before['qualification']!=after['qualification']:
        return {**result,'reason':'Qualification modes differ'}
    left=before['experiment']['dimensions'];right=after['experiment']['dimensions']
    changed=sorted(key for key in left.keys()|right.keys() if left.get(key)!=right.get(key))
    def delta(a,b):return b-a if a is not None and b is not None else None
    return {**result,'compatible':True,'changed_dimensions':changed,
        'change_scope':'combined-system' if len(changed)>1 else 'single-dimension' if changed else 'unchanged',
        'success_delta':after['success']['rate']-before['success']['rate'],
        'latency_median_delta':delta(before.get('latency',{}).get('median'),after.get('latency',{}).get('median')),
        'tokens_delta':delta(before.get('usage',{}).get('total_tokens'),after.get('usage',{}).get('total_tokens'))}


def _recorded_success(owner,events,report):
    """Trust configured owner records, not report claims or worker self-grading."""
    if (owner.get('status')!='completed' or owner.get('outcome_category')!='completed'
            or owner.get('stopping') is not False or owner.get('uncertain_effect') is not False
            or owner.get('progress',{}).get('next_stage') is not None
            or owner.get('progress',{}).get('terminal_final_failure')
            or any(event.get('kind')=='evaluation.final_failed' for event in events)
            or not _report_success(report)):
        return False
    handoffs=owner.get('accepted_handoffs',[])
    identities=set();gates=[]
    for handoff in handoffs:
        stage=handoff.get('stage');revision=handoff.get('revision');assignment=handoff.get('assignment_id')
        key=(stage,revision,assignment)
        if stage not in STAGES or type(revision) is not int or not isinstance(assignment,str) or key in identities:return False
        identities.add(key)
        checks=handoff.get('checks',[]);artifacts=handoff.get('artifacts',[])
        if not checks or any(check.get('passed') is not True for check in checks) or not artifacts or not _sha(artifacts[0].get('sha256')):return False
        if not any(event.get('kind')=='stage.assigned' and all(event.get('data',{}).get(k)==v for k,v in (('id',assignment),('stage',stage),('revision',revision))) for event in events):return False
        if not any(event.get('kind')=='stage.accepted' and event.get('data')==handoff for event in events):return False
        gates.append({'stage':stage,'revision':revision,'assignment_id':assignment,'artifact_sha256':artifacts[0]['sha256'],'verdict':'passed','passed':len(checks),'total':len(checks)})
    for stage in STAGES:
        accepted=[h for h in handoffs if h['stage']==stage]
        if not accepted:return False
        latest=accepted[-1];attempts=report.get('stages',{}).get(stage,{}).get('attempts',[])
        if not attempts or attempts[-1].get('assignment_id')!=latest['assignment_id'] or attempts[-1].get('accepted') is not True:return False
    if report.get('schema')=='benchmark-report/1':
        if owner.get('case')!='tq9' or owner.get('case_pin',{}).get('evaluator_version')!='1':return False
        revision=handoffs[-1]['revision']
        for stage in STAGES:
            selected=[h for h in handoffs if h['stage']==stage and h['revision']==(0 if stage=='acquire' else revision)]
            if len(selected)!=1:return False
            def check_identity(rows):return [(row.get('name',row.get('id')),row.get('passed')) for row in rows]
            if check_identity(report['stages'][stage].get('checks',[]))!=check_identity(selected[0]['checks']):return False
        return handoffs[-1]['stage']=='maintain' and not handoffs[-1].get('route')
    summary=owner.get('benchmark_summary',{})
    if any(report.get(key)!=summary.get(key) for key in ('accepted_gates','evaluations','final_evaluation','maintenance')):return False
    if gates!=summary.get('accepted_gates') or not _sha(summary.get('final_evaluation',{}).get('evidence_sha256')):return False
    safe_verdicts=public_v2_report({'evaluator_verdicts':owner.get('evaluator_verdicts',[])})['evaluator_verdicts']
    if report.get('evaluator_verdicts',[])!=safe_verdicts:return False
    evaluations=summary.get('evaluations',[]);finals=[row for row in evaluations if row.get('phase')=='final']
    if not finals or any(not _counts_ok(row) for row in finals):return False
    for final in finals:
        revision=final.get('revision');digest=final.get('frozen_artifact_sha256')
        for stage in STAGES:
            selected=[h for h in handoffs if h['stage']==stage and h['revision']==(0 if stage=='acquire' else revision)]
            if len(selected)!=1:return False
            if stage in ('emit','reuse','maintain') and selected[0]['artifacts'][0]['sha256']!=digest:return False
    last=finals[-1]
    if any(summary['final_evaluation'].get(key)!=last.get(key) for key in ('verdict','passed','total')):return False
    if handoffs[-1]['stage']!='maintain' or handoffs[-1].get('route'):return False
    for evaluation in evaluations:
        phase=evaluation.get('phase');stage='probe' if phase=='diagnostic' else 'reuse' if phase=='final' else None
        matching=[h for h in handoffs if h['stage']==stage and h['revision']==evaluation.get('revision')]
        if phase=='diagnostic' and evaluation.get('verdict')=='failed' and not matching:
            assignments={event['data'].get('id') for event in events if event.get('kind')=='stage.assigned'
                and event.get('data',{}).get('stage')=='probe' and event['data'].get('revision')==evaluation.get('revision')}
            verdicts=[v for v in owner.get('evaluator_verdicts',[]) if v.get('stage')=='probe' and v.get('assignment_id') in assignments]
        else:
            if len(matching)!=1:return False
            handoff=matching[0]
            verdicts=[v for v in owner.get('evaluator_verdicts',[]) if v.get('stage')==stage and v.get('assignment_id')==handoff['assignment_id']]
        if len(verdicts)!=1 or any(verdicts[0].get(key)!=evaluation.get(key) for key in ('verdict','passed','total')):return False
        if phase=='final' and verdicts[0].get('final_evaluation') is not True:return False
        # Probe accepts the adapted model; interpretation's original bytes differ.
        # Failed diagnostics have no accepted probe and earn no gate credit.
        if phase=='final' or evaluation.get('verdict')=='passed':
            source='probe' if phase=='diagnostic' else 'emit'
            if not any(h['stage']==source and h['revision']==evaluation['revision'] and h['artifacts'][0]['sha256']==evaluation.get('frozen_artifact_sha256') for h in handoffs):return False
    for handoff in handoffs:
        if handoff['stage']=='emit' and not any(v.get('stage')=='emit' and v.get('assignment_id')==handoff['assignment_id'] and v.get('verdict')=='passed' for v in owner.get('evaluator_verdicts',[])):return False
    return _maintenance_ok(summary.get('maintenance',{}))


def report_suite(suite_id,home=None,output=None,*,evidence_files=None,password_files=None):
    if output is None or not str(output).strip():raise ValueError('Suite export requires an explicit output directory')
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from ..client import call,default_home
    from ..reporting import report_run,_tree_hash
    from .evidence import regrade_v2
    root=Path(home or default_home()).resolve();destination=Path(output).resolve()
    regrading=evidence_files is not None or password_files is not None
    if regrading and (not isinstance(evidence_files,dict) or not isinstance(password_files,dict)):
        raise ValueError('Regrade requires trial-keyed evidence and case-keyed password files')
    offset=0;rows=[];first=None
    while True:
        page=call('suite_result',{'suite_id':suite_id,'offset':offset,'limit':100},home=root,autostart=False)
        if not page.get('ok') or page.get('suite_id')!=suite_id:raise ValueError('Cannot read suite from configured owner')
        if first is None:first=page
        elif page.get('experiment')!=first.get('experiment'):raise ValueError('Suite changed during export; retry after it settles')
        rows.extend(page['trials']);following=page.get('next_offset')
        if following is None:break
        if type(following) is not int or following<=offset:raise ValueError('Invalid suite pagination')
        offset=following
    experiment=_experiment(first['experiment']);identity=experiment['comparison_identity'];manifest=identity['manifest']
    expected=identity['trials']
    if len(rows)!=len(expected):raise ValueError('Missing owner suite slots')
    trials=[{**slot,**row,'family':identity['entry_pins'][slot['entry_index']]['family']} for slot,row in zip(expected,rows)]
    _validate_trials(manifest,trials,{},experiment)
    started=[trial for trial in trials if trial.get('run_id')]
    if regrading and (set(evidence_files)!={trial['trial_key'] for trial in started}
            or set(password_files)-{trial['case'] for trial in trials}):
        raise ValueError('Regrade evidence must cover every started trial exactly')
    reports={}
    with TemporaryDirectory(prefix='gd-suite-report-') as temporary:
        for trial in started:
            rid=trial['run_id'];pin=identity['entry_pins'][trial['entry_index']]
            owner=call('result',{'run_id':rid},home=root,autostart=False)
            snapshot=experiment['execution_snapshots'][trial['ordinal']]['snapshot_sha256']
            if (not owner.get('ok') or owner.get('run_id')!=rid or owner.get('case')!=trial['case']
                    or public_pin(owner.get('case_pin',{}))!=pin or owner.get('snapshot_sha256')!=snapshot):
                raise ValueError('Owner child identity differs from frozen slot')
            events=[];cursor=0
            while True:
                page=call('events',{'run_id':rid,'after':cursor},home=root,autostart=False)
                if not page.get('ok') or page.get('run_id')!=rid:raise ValueError('Cannot read child events')
                events.extend(page['events'])
                if page['cursor']==cursor or len(page['events'])<500:break
                if type(page['cursor']) is not int or page['cursor']<=cursor:raise ValueError('Invalid child event cursor')
                cursor=page['cursor']
            report=report_run(rid,home=root,output=Path(temporary)/trial['trial_key']/'report.json',autostart=False)
            _validate_trials(manifest,trials,{rid:report},experiment)
            executed=report.get('executed',{})
            if executed.get('evaluator_revision')!=identity['evaluator_revision']:
                raise ValueError('Executed evaluator differs from frozen experiment')
            dimensions=experiment['dimensions'];runtime=executed.get('runtime_configuration',{})
            for key,value in dimensions.items():
                actual=executed.get(key) if key in ('skills_revision','toolchain_revision') else runtime.get(key)
                if actual!=value:raise ValueError('Executed dimensions differ from frozen experiment')
            if regrading:
                if report.get('schema')!='benchmark-report/2':raise ValueError('Legacy trials do not support sidecar regrade')
                if trial['case'] not in password_files:raise ValueError('Missing case password handle')
                report=regrade_v2(report,Path(evidence_files[trial['trial_key']]),Path(password_files[trial['case']]))
                qualified=owner.get('status')=='completed' and owner.get('outcome_category')=='completed' and owner.get('stopping') is False and owner.get('uncertain_effect') is False and _report_success(report)
            else:
                qualified=_recorded_success(owner,events,report)
            if report.get('verdict')=='passed' and not qualified:report={**report,'verdict':'unqualified'}
            trial['outcome_category']=owner.get('outcome_category','unknown')
            if owner.get('stopping') is not False or owner.get('uncertain_effect') is not False:trial['status']='blocked'
            reports[rid]=report
        qualification='regraded-encrypted-evidence' if regrading else 'recorded-controller-verdicts'
        result=aggregate_suite(manifest,trials,reports,qualification,experiment=experiment)
        result.update(suite_id=suite_id,status=first['status'],suite_wall_seconds=max(0,first['updated']-first['created']),
            exporter={'evaluator_revision':_tree_hash(Path(__file__).parent),'toolchain_revision':_tree_hash(Path(__file__).parents[1])},
            qualification_meaning='Trusted configured owner records; not cryptographic authentication.' if not regrading else 'Authenticated sidecars; run association comes from the configured owner.')
        destination.mkdir(parents=True,exist_ok=True)
        for trial in result['trials']:
            if trial['run_id'] not in reports:continue
            safe=_public_report(reports[trial['run_id']]);encoded=json.dumps(safe,indent=2,allow_nan=False)+'\n'
            relative=Path('trials')/trial['trial_key']/'report.json';path=destination/relative
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(encoded.encode('utf-8'))
            trial['report_sha256']=hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            trial['report_path']=relative.as_posix()
        (destination/'suite.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        (destination/'report.md').write_text('# Benchmark suite\n\n'+f"Success: {result['success']['passed']}/{result['success']['denominator']}. Qualification: {qualification}.\n\n"+
            'All planned slots remain in the denominator. Missing resource totals remain unknown. Worker, child wall, suite wall and tool durations overlap.\n',encoding='utf-8')
    return result

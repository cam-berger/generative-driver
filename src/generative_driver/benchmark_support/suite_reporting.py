"""Allowlisted suite measurements; owner qualification and offline comparison."""

import copy
import hashlib
import json
import math
import re
import statistics
from pathlib import PurePosixPath,PureWindowsPath
from .suites import normalize_manifest,expand_trials
from .evidence import public_equivalence,public_pin,public_v2_report,public_v3_report,PIN,USAGE
from .audit import audit_commands,forbidden_paths
from .outcomes import classify,pass_rate,pass_rate_line
from .pairing import paired_comparison
from ..benchmark import STAGES

QUALIFICATIONS={'recorded-controller-verdicts','regraded-encrypted-evidence'}

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _sha(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)

def _exact(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys):raise ValueError('Incomplete or unknown experiment fields')

def _protocol_name(value):
    """Whether `value` is a protocol of the form `<name>/<n>`: a lowercase name and a positive integer."""
    return isinstance(value,str) and re.fullmatch('[a-z]+/[1-9][0-9]*',value) is not None

def _number(value,integer=False):
    if value is not None and (type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or value<0):
        raise ValueError('Invalid observed resource measurement')

def _usage_measurements(value):
    if value is None:return
    if not isinstance(value,dict):raise ValueError('Invalid usage record')
    for key in USAGE:
        if key not in ('coverage','counting'):_number(value.get(key),True)
    coverage=value.get('coverage')
    _number(coverage)
    if coverage is not None and coverage>1:raise ValueError('Invalid usage coverage')
    if value.get('counting') is not None and not isinstance(value['counting'],str):raise ValueError('Invalid usage counting description')

def _intervention_measurements(rows):
    for row in rows:
        for key in ('time','old_deadline','new_deadline','extension_seconds','old_budget_seconds','new_budget_seconds'):
            _number(row.get(key))

def _experiment(value):
    _exact(value,('schema','comparison_identity','dimensions','execution_snapshots'))
    if value['schema']!='benchmark-suite-experiment/1':raise ValueError('Unsupported experiment')
    identity=value['comparison_identity']
    # A suite the bench harness drives names its protocol; one frozen before it names none and keeps its meaning. A suite
    # that ran a sealed scoring set names that too.
    _exact(identity,('manifest','manifest_sha256','entry_pins','trials','repetitions','execution','evidence_track','scope','time_policy','evaluator_revision','budgets','intervention_policy')
           +(('protocol',) if 'protocol' in identity else ())+(('scoring_set',) if 'scoring_set' in identity else ()))
    # Only a suite that ran a scoring set may name another protocol: it is compared with systems of any protocol, device by device.
    if 'protocol' in identity and identity['protocol']!='assistive/1' and not ('scoring_set' in identity and _protocol_name(identity['protocol'])):
        raise ValueError('Unsupported suite protocol')
    if 'scoring_set' in identity:
        scoring=identity['scoring_set']
        if (not isinstance(scoring,dict) or set(scoring)!={'registration','manifest_sha256'} or not isinstance(scoring['registration'],str)
                or not scoring['registration'].strip() or not _sha(scoring['manifest_sha256'])):raise ValueError('Invalid scoring set')
    manifest=normalize_manifest(identity['manifest']);slots=expand_trials(manifest)
    if identity['manifest_sha256']!=_digest(manifest) or identity['trials']!=slots or identity['repetitions']!=manifest['repetitions']:
        raise ValueError('Frozen suite identity mismatch')
    if not _sha(identity['evaluator_revision']):raise ValueError('Missing evaluator identity')
    for key in ('execution','evidence_track','scope'):
        if not isinstance(identity[key],str) or not identity[key].strip():raise ValueError('Invalid experiment scope')
    pins=identity['entry_pins']
    if not isinstance(pins,list) or not isinstance(identity['time_policy'],list):raise ValueError('Invalid entry inventory')
    public_pins=[]
    if len(pins)!=len(manifest['entries']) or len(identity['time_policy'])!=len(pins):raise ValueError('Incomplete entry pins')
    for entry,pin,policy in zip(manifest['entries'],pins,identity['time_policy']):
        if not isinstance(pin,dict) or set(pin)-set((*PIN,'pin_sha256','image_hashes')):raise ValueError('Unknown public pin field')
        for key in PIN:
            if key not in pin:continue
            item=pin[key]
            if key=='case_seed':
                if type(item) is not int:raise ValueError('Invalid pinned seed')
            elif key.endswith('_sha256'):
                if item is not None and not _sha(item):raise ValueError('Invalid pin digest')
            elif item is not None and (not isinstance(item,str) or not item.strip()):raise ValueError('Invalid pin identity value')
        from .registry import validate_public_workflow_identity
        validate_public_workflow_identity(pin)
        if any(pin.get(k)!=entry[v] for k,v in (('case_id','case'),('scenario_id','scenario'),('case_seed','case_seed'))):raise ValueError('Entry pin mismatch')
        if any(pin.get(k)!=identity[k] for k in ('execution','evidence_track','scope')) or not pin.get('family'):raise ValueError('Mixed or missing pin scope')
        if any(not _sha(pin.get(k)) for k in ('manifest_sha256','truth_sha256','pin_sha256')) or not pin.get('case_version') or not pin.get('evaluator_version'):raise ValueError('Incomplete pin identity')
        if not isinstance(pin.get('image_hashes'),dict) or any(not _sha(v) for v in pin['image_hashes'].values()):raise ValueError('Invalid image identities')
        for name in pin['image_hashes']:
            if (not isinstance(name,str) or not name or PurePosixPath(name).is_absolute()
                    or PureWindowsPath(name).drive or '..' in PurePosixPath(name).parts
                    or '..' in PureWindowsPath(name).parts):raise ValueError('Invalid public image name')
        public_pins.append({**{key:pin[key] for key in PIN if key in pin},'pin_sha256':pin['pin_sha256'],
            'image_hashes':{key:digest for key,digest in pin['image_hashes'].items()}})
        if not isinstance(policy,dict) or set(policy)-{'probe','package_calls','clock','limitation'} or any(not isinstance(v,str) for v in policy.values()):raise ValueError('Invalid time policy')
    _exact(identity['budgets'],('original','effective'))
    original=identity['budgets']['original'];effective=identity['budgets']['effective']
    _exact(original,('child_budget_seconds','suite_budget_seconds'))
    _exact(effective,('child_budget_seconds','suite_budget_seconds','child_budget_overrides'))
    if original!={k:manifest[k] for k in original}:raise ValueError('Original budget mismatch')
    for key in original:
        _number(effective[key])
        if effective[key] is None or not original[key]<=effective[key]<=604800:raise ValueError('Invalid effective budget')
    if not isinstance(effective['child_budget_overrides'],list):raise ValueError('Invalid child overrides')
    positions={slot['trial_key']:i for i,slot in enumerate(slots)};prior=-1
    for override in effective['child_budget_overrides']:
        _exact(override,('trial_key','budget_seconds'));position=positions.get(override['trial_key'],-1)
        _number(override['budget_seconds'])
        if position<=prior or not original['child_budget_seconds']<override['budget_seconds']<=604800:raise ValueError('Invalid child budget override')
        prior=position
    _exact(identity['intervention_policy'],('scoped_tool_approval','max_model_repairs'))
    policy=identity['intervention_policy']
    if policy['scoped_tool_approval'] is not None and policy['scoped_tool_approval']!='emulator':raise ValueError('Invalid intervention approval')
    for key in ('max_model_repairs',):
        if type(policy[key]) is not int or policy[key]<0:raise ValueError('Invalid intervention limit')
    dimensions=value['dimensions']
    dimension_fields=('runtime','model','provider','version','reasoning_effort','max_turns','skills_revision','toolchain_revision',
                      'replay_candidates')
    if not isinstance(dimensions,dict) or set(dimensions)-set(dimension_fields):raise ValueError('Unknown dimensions')
    for key,item in dimensions.items():
        if item is None:continue
        if key=='max_turns':
            if type(item) is not int or item<0:raise ValueError('Invalid turn limit')
        elif key in ('skills_revision','toolchain_revision'):
            if not _sha(item):raise ValueError('Invalid dimension digest')
        elif key=='replay_candidates':
            # A replay suite's candidate hash for each case of its manifest.
            if (not isinstance(item,dict) or set(item)!={entry['case'] for entry in manifest['entries']}
                    or not all(_sha(sha) for sha in item.values())):raise ValueError('Invalid replay candidates')
        elif not isinstance(item,str) or not item.strip():raise ValueError('Invalid runtime dimension')
    if (dimensions.get('runtime')=='replay')!=isinstance(dimensions.get('replay_candidates'),dict):
        raise ValueError('Replay candidates belong to a replay suite, which names them')
    if not isinstance(value['execution_snapshots'],list) or len(value['execution_snapshots'])!=len(slots):raise ValueError('Incomplete snapshot coverage')
    for slot,snapshot in zip(slots,value['execution_snapshots']):
        _exact(snapshot,('trial_key','snapshot_sha256'))
        if snapshot['trial_key']!=slot['trial_key'] or snapshot['snapshot_sha256'] is not None and not _sha(snapshot['snapshot_sha256']):raise ValueError('Invalid snapshot identity')
    return {'schema':'benchmark-suite-experiment/1','comparison_identity':{
        **({'protocol':identity['protocol']} if 'protocol' in identity else {}),
        **({'scoring_set':{key:identity['scoring_set'][key] for key in ('registration','manifest_sha256')}} if 'scoring_set' in identity else {}),
        'manifest':manifest,'manifest_sha256':identity['manifest_sha256'],'entry_pins':public_pins,
        'trials':slots,'repetitions':manifest['repetitions'],
        **{key:identity[key] for key in ('execution','evidence_track','scope','evaluator_revision')},
        'time_policy':[{key:row[key] for key in ('probe','package_calls','clock','limitation') if key in row} for row in identity['time_policy']],
        'budgets':{'original':{key:original[key] for key in ('child_budget_seconds','suite_budget_seconds')},
            'effective':{**{key:effective[key] for key in ('child_budget_seconds','suite_budget_seconds')},
                'child_budget_overrides':[{'trial_key':row['trial_key'],'budget_seconds':row['budget_seconds']} for row in effective['child_budget_overrides']]}},
        'intervention_policy':{key:policy[key] for key in ('scoped_tool_approval','max_model_repairs')}},
        'dimensions':{key:copy.deepcopy(dimensions[key]) for key in dimension_fields if key in dimensions},
        'execution_snapshots':[{'trial_key':row['trial_key'],'snapshot_sha256':row['snapshot_sha256']} for row in value['execution_snapshots']]}

def _counts_ok(value):
    return (value.get('verdict')=='passed' and type(value.get('total')) is int and value['total']>0
        and type(value.get('passed')) is int and value['passed']==value['total'])

def _report_success(report):
    if report.get('verdict')!='passed' or report.get('workflow_status')!='completed':return False
    stages=report.get('stages',{})
    if set(stages) != set(STAGES):return False
    if any(stages.get(stage,{}).get('workflow_status')!='accepted' or stages.get(stage,{}).get('evaluator_status')!='passed' for stage in STAGES):return False
    if report.get('schema')=='benchmark-report/1':return report.get('case')=='tq9'
    gates=report.get('accepted_gates',[])
    if any(gate.get('stage') not in STAGES for gate in gates):return False
    finals=[row for row in report.get('evaluations',[]) if row.get('phase')=='final']
    if len(finals)!=1 or not _counts_ok(finals[0]):return False
    revision=finals[0].get('revision')
    if type(revision) is not int or revision<0:return False
    for stage in STAGES:
        selected=[g for g in gates if g.get('stage')==stage and g.get('revision')==(0 if stage=='acquire' else revision)]
        if len(selected)!=1 or not _counts_ok(selected[0]):return False
    return (all(any(gate.get('stage')==stage and _counts_ok(gate) for gate in gates) for stage in STAGES)
        and _counts_ok(report.get('final_evaluation',{})))

def _report_equivalent(report,protocol=None):
    """An assistive report's success: its one recorded verdict, a well-formed evaluator grade of a completed run, holds
    the frozen driver equivalent. The report and its grade name `protocol`, by default this installation's."""
    from ..configurator import PROTOCOL,_evaluation_errors
    protocol=protocol or PROTOCOL
    equivalence=report.get('equivalence')
    return (report.get('schema')=='benchmark-report/3' and report.get('protocol')==protocol
        and report.get('workflow_status')=='completed' and report.get('verdict')=='equivalent'
        # A grade names the protocol of its report. The configurator's schema holds a grade to this installation's, so it
        # checks the rest of the record with that field set to its own.
        and isinstance(equivalence,dict) and equivalence.get('protocol')==protocol
        and not _evaluation_errors({**equivalence,'protocol':PROTOCOL})
        and equivalence['status']=='graded' and equivalence['equivalent'] is True)

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
        _intervention_measurements(trial.get('interventions',[]))
        report=reports.get(rid)
        if report is None:continue
        # An assistive suite holds assistive reports alone, and an earlier suite none: reports never mix protocols.
        assistive='protocol' in identity
        if (report.get('schema')=='benchmark-report/3')!=assistive or assistive and report.get('protocol')!=identity['protocol']:
            raise ValueError('Report protocol differs from the frozen suite protocol')
        if (report.get('run_id')!=rid or report.get('case')!=slot['case'] or report.get('scenario_id')!=slot['scenario']
                or report.get('case_seed')!=slot['case_seed'] or public_pin(report.get('case_pin',{}))!=pin
                or report.get('snapshot_sha256')!=snapshot['snapshot_sha256'] or not _sha(snapshot['snapshot_sha256'])):
            raise ValueError('Report does not match frozen trial identity')
        for key in ('elapsed_seconds','worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds',
                    'repair_seconds','original_budget_seconds','effective_budget_seconds'):_number(report.get(key))
        _usage_measurements(report.get('usage'))
        _intervention_measurements(report.get('interventions',[]))
        for stage in report.get('stages',{}).values():
            for key in ('worker_seconds','tool_seconds','worker_tool_seconds','evaluator_tool_seconds'):_number(stage.get(key))
            _number(stage.get('attempt_count'),True)
            _usage_measurements(stage.get('usage'))
            for attempt in stage.get('attempts',[]):
                _number(attempt.get('elapsed_seconds'))
                _usage_measurements(attempt.get('usage'))
    if set(reports)-runs:raise ValueError('Report outside planned trials')


def _outcome(trial,report,equivalent):
    """A planned trial of an assistive suite as its count holds it: not_run, equivalent (settled and qualified),
    not_equivalent (a recorded grade, or no driver), unqualified (a claimed equivalent that is not counted), unscored (a
    completed run awaiting or without its grade), stopped or running."""
    if not trial.get('run_id'):return 'not_run'
    if trial in equivalent:return 'equivalent'
    status=report.get('workflow_status',trial['status']);verdict=report.get('verdict')
    if status in ('blocked','failed','cancelled'):return 'stopped'
    if verdict=='not equivalent':return 'not_equivalent'
    if verdict in ('equivalent','unqualified'):return 'unqualified'
    return 'unscored' if status=='completed' else 'running'


# Why a trial's claimed equivalent verdict is not counted: the run's own reason names only its verdict.
UNQUALIFIED='Not counted: the equivalent verdict is not a settled, qualified record'


def _outcomes(trials,reports,equivalent):
    """Each planned trial of an assistive suite in exactly one count (`_outcome`), stopped trials by their stop
    category; and the unscored trials, listed apart."""
    counts={'planned':len(trials),'started':0,'finished':0,'not_run':0,'equivalent':len(equivalent),'not_equivalent':0,
            'no_driver':0,'unqualified':0,'unscored':0,'running':0,'stopped':{}}
    unscored=[]
    for trial in trials:
        counts['finished']+=trial['status']=='finished'
        report=reports.get(trial.get('run_id')) or {}
        outcome=_outcome(trial,report,equivalent)
        if outcome=='not_run':
            counts['not_run']+=1;continue
        counts['started']+=1
        if outcome=='stopped':
            category=trial.get('outcome_category') if isinstance(trial.get('outcome_category'),str) else 'unknown'
            counts['stopped'][category]=counts['stopped'].get(category,0)+1
        elif outcome=='not_equivalent':
            counts['not_equivalent']+=1
            counts['no_driver']+=(report.get('equivalence') or {}).get('status')=='no-driver'
        elif outcome in ('unqualified','unscored','running'):
            counts[outcome]+=1
        if outcome=='unscored':
            unscored.append({'trial_key':trial['trial_key'],'run_id':trial['run_id'],'equivalence_status':report.get('equivalence_status')})
    return counts,unscored


def aggregate_suite(manifest,trials,reports,qualification,*,experiment):
    """A suite's measurements. An assistive suite (its frozen identity names the protocol) counts equivalent drivers out
    of those planned, from benchmark-report/3 reports alone, and leads with its pass rate: each trial's `scored` outcome,
    classified from its slot and its full report, and the rate counted from them (benchmark-suite-report/3). A trial
    that carries `audit` findings (the transcript audit's, which `report_suite` attaches) scores `flagged` and is
    not graded; the equivalent drivers counted stay those the evaluator found equivalent, flagged or not. An earlier
    suite keeps its gated success count (benchmark-suite-report/1)."""
    if qualification not in QUALIFICATIONS:raise ValueError('Suite aggregates require qualified trial evidence')
    experiment=_experiment(experiment)
    _validate_trials(manifest,trials,reports,experiment)
    protocol=experiment['comparison_identity'].get('protocol')
    win,success='equivalent' if protocol else 'passed',_report_equivalent if protocol else _report_success
    trials=[dict(trial,family=experiment['comparison_identity']['entry_pins'][trial['entry_index']]['family']) for trial in trials]
    passed=[trial for trial in trials if trial['status']=='finished' and trial['outcome_category']=='completed'
            and success(reports.get(trial.get('run_id'),{}))]
    started=[trial for trial in trials if trial.get('run_id')]
    finished=[trial for trial in trials if trial['status']=='finished']
    observed=[reports[trial['run_id']] for trial in started if trial['run_id'] in reports]
    families=sorted({trial['family'] for trial in trials})
    rates={family:sum(t['family']==family for t in passed)/sum(t['family']==family for t in trials) for family in families}
    tokens=[(report.get('usage') or {}).get('total_tokens') for report in observed]
    known=[(report.get('usage') or {}).get('observed_total_tokens') for report in observed]
    seconds=[report.get('worker_seconds') for report in observed]
    resources={'worker_seconds':sum(seconds) if started and len(observed)==len(started) and all(type(v) in (int,float) for v in seconds) else None,
        'observed_worker_seconds':sum(v for v in seconds if type(v) in (int,float)) if any(type(v) in (int,float) for v in seconds) else None,
        'usage':{'total_tokens':sum(tokens) if started and len(observed)==len(started) and all(type(v) is int for v in tokens) else None,
            'observed_total_tokens':sum(v for v in known if type(v) is int) if any(type(v) is int for v in known) else None,
            'reported_trials':sum(type(v) is int for v in tokens),'started_trials':len(started)}}
    if protocol:
        counts,unscored=_outcomes(trials,reports,passed)
        missions=[public_equivalence((reports.get(t.get('run_id')) or {}).get('equivalence')) or {} for t in trials]
        mission=sum((m.get('mission') or {}).get('passed') is True for m in missions)
        reused=[t for t in passed if (reports[t['run_id']]['equivalence'].get('mission') or {}).get('passed') is True]
        result={'schema':'benchmark-suite-report/3','protocol':protocol,'qualification':qualification,'counts':counts,
            'equivalence':{'equivalent':len(passed),'denominator':len(trials),'rate':len(passed)/len(trials)},'unscored':unscored,
            'provisional':len(finished)!=len(trials),'family_equivalence':rates,'family_macro_equivalence':sum(rates.values())/len(rates),
            # Secondary: whether fresh reuse did the public mission with the frozen package; never the verdict.
            'fresh_mission':{'passed':mission,'denominator':len(trials),'rate':mission/len(trials)},
            'equivalent_and_mission':{'passed':len(reused),'denominator':len(trials),'rate':len(reused)/len(trials)},**resources}
    else:
        result={'schema':'benchmark-suite-report/1','qualification':qualification,
            'counts':{'planned':len(trials),'started':len(started),'finished':len(finished),
                'failed':sum(t['outcome_category']=='model' for t in finished),
                'blocked':sum(t['status']=='blocked' for t in trials),
                'cancelled':sum(bool(t.get('run_id')) and t['status']=='cancelled' for t in trials),'not_run':len(trials)-len(started)},
            'success':{'passed':len(passed),'denominator':len(trials),'rate':len(passed)/len(trials)},
            'provisional':len(finished)!=len(trials),'family_success':rates,'family_macro_success':sum(rates.values())/len(rates),**resources}

    if experiment['dimensions'].get('runtime')=='replay':
        # No model ran a replay suite's children: its results are never model performance.
        from ..replay import execution_label
        result.update(execution=execution_label(experiment['comparison_identity']['execution'],'replay'),model_benchmark=False)

    def rates_by(key):
        return {value:{win:sum(t[key]==value for t in passed),'denominator':sum(t[key]==value for t in trials),
            'rate':sum(t[key]==value for t in passed)/sum(t[key]==value for t in trials)} for value in sorted({t[key] for t in trials})}
    first=sum(all(reports[t['run_id']]['stages'].get(stage,{}).get('attempt_count')==1 for stage in STAGES) for t in passed)
    latencies=[r['elapsed_seconds'] for r in observed if r.get('elapsed_seconds') is not None]
    public_trials=[]
    for trial in trials:
        report=reports.get(trial.get('run_id'),{})
        # An assistive trial that has no report yet carries no verdict, not even an unscored one.
        safe=(public_v3_report(report) if report else {}) if protocol else public_v2_report(report)
        row={key:trial[key] for key in ('trial_key','ordinal','entry_index','repeat_index','case','family','scenario','case_seed','run_id','status','outcome_category')}
        # `workflow_status` and `effective_budget_seconds` let the row alone repeat the outcome its report gave.
        row.update({key:safe[key] for key in ('verdict','equivalence','equivalence_status','stages','final_evaluation','elapsed_seconds',
            'worker_seconds','tool_seconds','usage','workflow_status','effective_budget_seconds') if key in safe})
        if row.get('verdict')==win and trial not in passed:row['verdict']='unqualified'
        if protocol:
            # Why the trial stands as it does: its outcome as its count holds it, the reason its report gives (paths
            # scrubbed by `report_run`), the stage where it stopped and the repairs it used.
            outcome=_outcome(trial,report,passed);repairs=(safe.get('progress') or {}).get('repairs');audit=trial.get('audit')
            row.update(equivalent_and_mission=trial in reused,
                       outcome=outcome,reason=UNQUALIFIED if outcome=='unqualified' else safe.get('reason'),
                       stopped_stage=safe.get('stopped_stage'),repairs=repairs if type(repairs) is int else None,
                       # From the slot and the full report, with the verdict the row carries: a claimed equivalent the count
                       # refuses is unqualified here as in `report_suite`, so its passes are the equivalent drivers counted.
                       # A trial whose transcripts the audit flagged is not graded, whatever its grade.
                       scored=classify(trial,{**report,'verdict':row.get('verdict')},flagged=bool(audit)))
            # The findings of an audit that ran, none when it found nothing; a trial that nothing audited has no key.
            if audit is not None:row['audit']=[{key:finding[key] for key in ('transcript','command')} for finding in audit]
        row['report_sha256']=_digest(_public_report(report)) if report else None
        row['interventions']=[{key:item[key] for key in ('event_id','kind','time','effect_resolution','scoped_tool_approval','old_budget_seconds','new_budget_seconds','extension_seconds','old_deadline','new_deadline','stage','uncertain_effect','has_observation')
            if key in item and (item[key] is None or type(item[key]) in (str,int,float,bool))} for item in trial.get('interventions',[])]
        public_trials.append(row)
    prefix='first_attempt_'+('equivalent' if protocol else 'success'),'repaired_'+('equivalent' if protocol else 'success')
    if protocol:result['pass_rate']=pass_rate([row['scored'] for row in public_trials])
    result.update({'experiment':experiment,'trials':public_trials,
        ('case_equivalence' if protocol else 'case_success'):rates_by('case'),
        ('scenario_equivalence' if protocol else 'scenario_success'):rates_by('scenario'),
        'stage_counts':{stage:{'accepted':sum(r.get('stages',{}).get(stage,{}).get('workflow_status')=='accepted' for r in observed),
            'attempts':sum(r.get('stages',{}).get(stage,{}).get('attempt_count',0) for r in observed)} for stage in STAGES},
        prefix[0]:first,prefix[1]:len(passed)-first,
        'latency':{'sample_count':len(latencies),'includes_failed_trials':any(r.get('verdict')!=win and r.get('elapsed_seconds') is not None for r in observed),
            'min':min(latencies) if latencies else None,'median':statistics.median(latencies) if latencies else None,'max':max(latencies) if latencies else None},
        'tool_seconds':sum(r['tool_seconds'] for r in observed) if started and len(observed)==len(started) and all(r.get('tool_seconds') is not None for r in observed) else None})
    return result


def _public_report(report):
    if report.get('schema')=='benchmark-report/3':return public_v3_report(report)
    safe=public_v2_report(report)
    safe['schema']=report.get('schema','benchmark-report/2')
    return safe


def _read_saved(value):
    from pathlib import Path
    return copy.deepcopy(value) if isinstance(value,dict) else json.loads(Path(value).read_text(encoding='utf-8'))


def _saved_report(value):
    """A saved suite report: an earlier suite's gated success (benchmark-suite-report/1), or an assistive suite's
    equivalent drivers (benchmark-suite-report/2, or /3, which adds the pass rate and each trial's scored outcome), each
    counted again from its trials' verdicts and, for /3, its pass rate from the outcomes its rows store. Only a /3 file
    stores outcomes: a reader that trusts a stored one under any schema would count a label an earlier file never had,
    so an earlier file whose rows carry one is refused."""
    report=_read_saved(value)
    assistive=('benchmark-suite-report/2','benchmark-suite-report/3')
    if report.get('schema') not in ('benchmark-suite-report/1',*assistive) or report.get('qualification') not in QUALIFICATIONS:raise ValueError('Unsupported suite report')
    experiment=_experiment(report['experiment'])
    protocol=experiment['comparison_identity'].get('protocol')
    if (report['schema'] in assistive)!=bool(protocol) or report.get('protocol')!=protocol:raise ValueError('Unsupported suite report')
    _validate_trials(experiment['comparison_identity']['manifest'],report['trials'],{},experiment)
    if report['schema']!='benchmark-suite-report/3' and any('scored' in t for t in report['trials']):
        raise ValueError('An earlier suite report stores no scored outcome')
    if report['schema']!='benchmark-suite-report/3' and 'scoring_set' in experiment['comparison_identity']:
        raise ValueError('An earlier suite report names no scoring set')
    key,win=('equivalence','equivalent') if protocol else ('success','passed')
    success=report[key];planned=len(report['trials'])
    if type(success.get(win)) is not int or not 0<=success[win]<=planned or success.get('denominator')!=planned or success.get('rate')!=success[win]/planned:
        raise ValueError('Invalid '+key+' denominator')
    counted=lambda t:t.get('verdict')==win and t.get('status')=='finished' and t.get('outcome_category')=='completed'
    if success[win]!=sum(counted(t) for t in report['trials']):
        raise ValueError('Saved '+key+' count differs from trial coverage')
    if protocol:
        joint=sum(t.get('status')=='finished' and t.get('outcome_category')=='completed'
                  and _report_equivalent({**t,'schema':'benchmark-report/3','protocol':protocol,'workflow_status':'completed'},protocol)
                  and ((t.get('equivalence') or {}).get('mission') or {}).get('passed') is True for t in report['trials'])
        metric={'passed':joint,'denominator':planned,'rate':joint/planned}
        if 'equivalent_and_mission' in report and report['equivalent_and_mission']!=metric:
            raise ValueError('Saved joint success count differs from trial evidence')
        report['equivalent_and_mission']=metric
        if report['schema']=='benchmark-suite-report/3':
            # A forged or missing outcome is no outcome, and `pass_rate` refuses it.
            rate=pass_rate([t.get('scored') for t in report['trials']])
            # A pass is a counted equivalent driver: the row's own verdict, status and category must show what its label says.
            # A counted driver whose transcripts the audit flagged scores `flagged`, which is no pass, and no other outcome.
            if any(t['scored']=='pass' and not counted(t) or counted(t) and t['scored'] not in ('pass','flagged')
                   for t in report['trials']):raise ValueError('Saved outcome differs from the trial it scores')
            if 'pass_rate' in report and report['pass_rate']!=rate:raise ValueError('Saved pass rate differs from trial outcomes')
            report['pass_rate']=rate
    _number(report.get('usage',{}).get('total_tokens'),True)
    _number(report.get('latency',{}).get('median'))
    return report


def compare_suites(before,after,*,leak_check=None):
    """Compare saved public data only; this does not authenticate any report. Reports that both carry a scoring set pair
    device by device whatever their protocols (`paired_comparison`), with `leak_check`, if given, the after system's fresh
    batch. Any others compare as whole suites, and only within one protocol; they take no leak check."""
    result={'schema':'benchmark-suite-comparison/1','compatible':False}
    try:
        before=_saved_report(before);after=_saved_report(after)
        if leak_check is not None:leak_check=_saved_report(leak_check)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {**result,'reason':'Invalid or incomplete saved suite report'}
    replayed=lambda report:report['experiment']['dimensions'].get('runtime')=='replay'
    if replayed(before)!=replayed(after):
        # No model ran a replay suite's children: replay suites are a series of their own, never set beside model suites.
        return {**result,'reason':'Replay results and model runs are separate series and are never compared'}
    ran_scoring_set=lambda report:'scoring_set' in report['experiment']['comparison_identity']
    if ran_scoring_set(before) and ran_scoring_set(after):
        try:
            return paired_comparison(before,after,leak_check=leak_check)
        except ValueError as exc:
            return {**result,'reason':str(exc)}
    if leak_check is not None:
        return {**result,'reason':'A leak check needs two reports that carry a scoring set'}
    protocol=before.get('protocol')
    if protocol!=after.get('protocol'):
        # Results of different protocols are separate series: neither count is set beside the other.
        return {**result,'reason':'Protocols differ: results of different protocols are separate series and are never compared'}
    key='equivalence' if protocol else 'success'
    result.update({'before_'+key:before[key],'after_'+key:after[key]})
    if protocol:
        result.update(before_equivalent_and_mission=before['equivalent_and_mission'],
                      after_equivalent_and_mission=after['equivalent_and_mission'])
    if before['experiment']['comparison_identity']!=after['experiment']['comparison_identity']:
        return {**result,'reason':'Frozen experiment identities differ'}
    if before['qualification']!=after['qualification']:
        return {**result,'reason':'Qualification modes differ'}
    left=before['experiment']['dimensions'];right=after['experiment']['dimensions']
    changed=sorted(key for key in left.keys()|right.keys() if left.get(key)!=right.get(key))
    def delta(a,b):return b-a if a is not None and b is not None else None
    return {**result,'compatible':True,'changed_dimensions':changed,
        'change_scope':'combined-system' if len(changed)>1 else 'single-dimension' if changed else 'unchanged',
        key+'_delta':after[key]['rate']-before[key]['rate'],
        **({'equivalent_and_mission_delta':after['equivalent_and_mission']['rate']-before['equivalent_and_mission']['rate']}
           if protocol else {}),
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
        if owner.get('case')!='tq9' or owner.get('case_pin',{}).get('evaluator_version')!='2':return False
        revision=handoffs[-1]['revision']
        for stage in STAGES:
            selected=[h for h in handoffs if h['stage']==stage and h['revision']==(0 if stage=='acquire' else revision)]
            if len(selected)!=1:return False
            def check_identity(rows):return [(row.get('name',row.get('id')),row.get('passed')) for row in rows]
            if check_identity(report['stages'][stage].get('checks',[]))!=check_identity(selected[0]['checks']):return False
        return handoffs[-1]['stage']=='reuse' and not handoffs[-1].get('route')
    for handoff in handoffs:
        if handoff['stage']=='reuse' and not {'fresh_worker_mission','frozen_final_behavior'}<= {
                check.get('name',check.get('id')) for check in handoff['checks']}:return False
    summary=owner.get('benchmark_summary',{})
    if any(report.get(key)!=summary.get(key) for key in ('accepted_gates','evaluations','final_evaluation')):return False
    if gates!=summary.get('accepted_gates') or not _sha(summary.get('final_evaluation',{}).get('evidence_sha256')):return False
    safe_verdicts=public_v2_report({'evaluator_verdicts':owner.get('evaluator_verdicts',[])})['evaluator_verdicts']
    if report.get('evaluator_verdicts',[])!=safe_verdicts:return False
    evaluations=summary.get('evaluations',[]);finals=[row for row in evaluations if row.get('phase')=='final']
    if len(finals)!=1 or any(not _counts_ok(row) for row in finals):return False
    for final in finals:
        revision=final.get('revision');digest=final.get('frozen_artifact_sha256')
        for stage in STAGES:
            selected=[h for h in handoffs if h['stage']==stage and h['revision']==(0 if stage=='acquire' else revision)]
            if len(selected)!=1:return False
            if stage in ('emit','reuse') and selected[0]['artifacts'][0]['sha256']!=digest:return False
    last=finals[-1]
    if any(summary['final_evaluation'].get(key)!=last.get(key) for key in ('verdict','passed','total')):return False
    if handoffs[-1]['stage']!='reuse' or handoffs[-1].get('route'):return False
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
    return True


def _recorded_equivalence(owner,events,report):
    """Trust the configured owner's own record of an assistive run, not the report's claim: a completed, settled run of
    the protocol whose one final verdict the configurator recorded (one run.finalized event) is the report's, and grades
    the run's accepted emitted driver: the first artifact of its latest emit handoff that handed one on."""
    from ..configurator import PROTOCOL
    progress=owner.get('progress') or {};equivalence=owner.get('equivalence')
    if (owner.get('status')!='completed' or owner.get('outcome_category')!='completed'
            or owner.get('stopping') is not False or owner.get('uncertain_effect') is not False
            or progress.get('next_stage') is not None or progress.get('protocol')!=PROTOCOL
            or not isinstance(equivalence,dict) or progress.get('equivalence')!=equivalence.get('status')
            or not _report_equivalent(report) or report.get('equivalence')!=public_equivalence(equivalence)):
        return False
    frozen=next((h['artifacts'][0].get('sha256') for h in reversed(owner.get('accepted_handoffs') or [])
                 if h.get('stage')=='emit' and h.get('artifacts')),None)
    finalized=[event for event in events if event.get('kind')=='run.finalized']
    return (_sha(frozen) and equivalence.get('frozen_artifact_sha256')==frozen and len(finalized)==1
            and finalized[0].get('data')=={key:equivalence.get(key) for key in ('equivalent','basis','diff')})


def _cell(value,missing=''):
    """One Markdown table cell: `missing` when not recorded, text on one line with its pipes escaped."""
    if value is None:return missing
    if isinstance(value,float):return f'{value:.2f}'
    return str(value).replace('|','\\|').replace('\r',' ').replace('\n',' ')


def _summary(result):
    """An assistive suite's report.md body, built from its trial rows: the pass rate, why the suite stands as it does, its
    dimensions, each trial's scored outcome and outcome with its reason, stop stage, repairs, time and tokens, and the
    check ids that failed most often. Expected values and tolerances are in none of these fields."""
    rows,counts=result['trials'],result['counts']
    joint=result['equivalent_and_mission']
    dimensions=', '.join(f'{key} `{value}`' for key,value in result['experiment']['dimensions'].items() if value is not None)
    tallied={}
    for row in rows:tallied[row['outcome']]=tallied.get(row['outcome'],0)+1
    detail={'not_equivalent':f"{counts['no_driver']} without a driver" if counts['no_driver'] else '',
            'stopped':', '.join(f'{count} {category}' for category,count in counts['stopped'].items())}
    failing={}
    for row in rows:
        for diff in (row.get('equivalence') or {}).get('diff') or []:
            if isinstance(diff,dict) and isinstance(diff.get('id'),str):failing[diff['id']]=failing.get(diff['id'],0)+1
    common=sorted(failing.items(),key=lambda item:(-item[1],item[0]))[:5]
    unscored=', '.join(row['trial_key'] for row in result['unscored'])
    lines=[f"{pass_rate_line(result['pass_rate'])}. Protocol: {result['protocol']}, "
           f"a series never compared with earlier gated results. Qualification: {result['qualification']}.",'',
           f"Equivalent drivers with successful fresh reuse: {joint['passed']}/{joint['denominator']}. "
           'Both conditions must hold for the same trial; missing mission evidence is no success.','',
           'Suite status: '+str(result['status'])+(': '+_cell(result['reason']) if result.get('reason') else '')+'.','',
           'Dimensions: '+(dimensions or 'none recorded')+'.','',
           'Outcomes: '+', '.join(f'{count} {outcome}'+(f' ({detail[outcome]})' if detail.get(outcome) else '')
                                  for outcome,count in tallied.items())+'.','',
           '| Trial | Case | Scored | Outcome | Reason | Stopped stage | Repairs | Wall seconds | Tokens |',
           '|---|---|---|---|---|---|---:|---:|---:|']
    for row in rows:
        # A started trial's missing time or tokens are unknown; a trial never started has none.
        measured=[_cell(value,'unknown' if row['run_id'] else '') for value in (row.get('elapsed_seconds'),(row.get('usage') or {}).get('total_tokens'))]
        lines.append('| '+' | '.join([*(_cell(value) for value in (row['trial_key'],row['case'],row['scored'],row['outcome'],row['reason'],
                                                                     row['stopped_stage'],row['repairs'])),*measured])+' |')
    lines+=['','Most frequent failing checks: '+(', '.join(f'`{key}` ({count})' for key,count in common) or 'none recorded')+'.']
    return '\n'.join(lines)+'\n\n'+(f'Unscored: {unscored}.\n\n' if unscored else '')


def report_suite(suite_id,home=None,output=None,*,evidence_files=None,password_files=None,audit_paths=()):
    """Export a suite's measurements from its configured owner. An earlier suite's regrade needs the sealed evidence of
    every started trial; an assistive suite's, that of exactly the trials whose verdict was recorded (each names its
    evidence), since an unscored or stopped trial has no verdict to authenticate.

    With `audit_paths` (forbidden paths, private to the operator), each trial's run directory in the owner's home is
    audited (`audit_commands`): every row of the export carries its `audit` findings, and a trial with any is `flagged`,
    not graded. A report without `audit_paths` has no `audit` on its rows. The paths are in no output but the commands
    that name them, which the findings quote."""
    if output is None or not str(output).strip():raise ValueError('Suite export requires an explicit output directory')
    audit_paths=forbidden_paths(audit_paths)
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from ..client import call,default_home
    from ..reporting import report_run,_public,_tree_hash
    from .evidence import regrade_v2,regrade_v3
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
    protocol=identity.get('protocol');keys={trial['trial_key'] for trial in started}
    if regrading and ((set(evidence_files)-keys if protocol else set(evidence_files)!=keys)
            or set(password_files)-{trial['case'] for trial in trials}):
        raise ValueError('Regrade evidence must cover every started trial exactly')
    if audit_paths and not protocol:raise ValueError('The transcript audit covers assistive suites only')
    if audit_paths:
        for trial in trials:trial['audit']=[]
    reports={};regraded=set()
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
                if key=='replay_candidates':
                    # Each child replays its own case's candidate.
                    actual,value=runtime.get('candidate_sha256'),value.get(trial['case'])
                else:
                    actual=executed.get(key) if key in ('skills_revision','toolchain_revision') else runtime.get(key)
                if actual!=value:raise ValueError('Executed dimensions differ from frozen experiment')
            settled=owner.get('status')=='completed' and owner.get('outcome_category')=='completed' and owner.get('stopping') is False and owner.get('uncertain_effect') is False
            if regrading and protocol:
                if (report.get('equivalence') or {}).get('evidence_sha256'):
                    if trial['trial_key'] not in evidence_files:raise ValueError('Regrade evidence must cover every trial with a recorded verdict exactly')
                    if trial['case'] not in password_files:raise ValueError('Missing case password handle')
                    report=regrade_v3(report,Path(evidence_files[trial['trial_key']]),Path(password_files[trial['case']]))
                    regraded.add(trial['trial_key'])
                qualified=settled and _report_equivalent(report)
            elif regrading:
                if report.get('schema')!='benchmark-report/2':raise ValueError('Legacy trials do not support sidecar regrade')
                if trial['case'] not in password_files:raise ValueError('Missing case password handle')
                report=regrade_v2(report,Path(evidence_files[trial['trial_key']]),Path(password_files[trial['case']]))
                qualified=settled and _report_success(report)
            else:
                qualified=_recorded_equivalence(owner,events,report) if protocol else _recorded_success(owner,events,report)
            if report.get('verdict')==('equivalent' if protocol else 'passed') and not qualified:report={**report,'verdict':'unqualified'}
            trial['outcome_category']=owner.get('outcome_category','unknown')
            if owner.get('stopping') is not False or owner.get('uncertain_effect') is not False:trial['status']='blocked'
            # The run directory is where the owner keeps what the run's workers left (`runs/<run_id>` in its home).
            if audit_paths:trial['audit']=audit_commands(root/'runs'/rid,audit_paths)
            reports[rid]=report
        if regrading and protocol and set(evidence_files)!=regraded:
            raise ValueError('Regrade evidence must cover every trial with a recorded verdict exactly')
        qualification='regraded-encrypted-evidence' if regrading else 'recorded-controller-verdicts'
        result=aggregate_suite(manifest,trials,reports,qualification,experiment=experiment)
        result.update(suite_id=suite_id,status=first['status'],reason=_public(first.get('reason'),root),
            suite_wall_seconds=max(0,first['updated']-first['created']),
            exporter={'benchmark_support_tree_sha256':_tree_hash(Path(__file__).parent),'toolchain_revision':_tree_hash(Path(__file__).parents[1])},
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
        if protocol:
            summary=_summary(result)
            if result.get('execution'):
                summary += (f"Execution: {result['execution']}: saved candidates replayed without a model, never model "
                            "performance or a series compared with model suites.\n\n")
            denominators='The pass rate divides by graded trials; the fresh-reuse count divides by all planned slots.'
        else:
            summary=f"Success: {result['success']['passed']}/{result['success']['denominator']}. Qualification: {qualification}.\n\n"
            denominators='All planned slots remain in the denominator.'
        (destination/'report.md').write_text('# Benchmark suite\n\n'+summary+denominators+
            ' Missing resource totals remain unknown. Worker, child wall, suite wall and tool durations overlap.\n',encoding='utf-8')
    return result

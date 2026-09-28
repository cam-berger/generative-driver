"""Public benchmark run, independent score and comparison interface.

Replay setup checks and actual agent trials have distinct execution labels.
Evaluator truth and process options must never be included in worker assignments.
"""
import json
import argparse
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse', 'maintain')


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    if case_id == 'bme280':
        from .benchmark_support.physical import prepare_stage as prepare
    else:
        from .benchmark_support.cases import prepare_stage as prepare
    return prepare(case_id, stage, run_dir, workspace, accepted, options)


def check_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    if case_id == 'bme280':
        from .benchmark_support.physical import check_stage as check
    else:
        from .benchmark_support.cases import check_stage as check
    result = check(case_id, stage, run_dir, workspace, report, accepted, options)
    from .benchmark_support.cases import _state, _write
    state_path, state = _state(run_dir)
    state.setdefault('stage_verdicts', {})[stage] = {'status': 'passed' if result.get('ok') else 'failed',
        'checks': result.get('checks', []), 'revision': state.get('revision', 0), 'reason': result.get('reason')}
    _write(state_path, state)
    return result


def package_execute(case_id, run_dir, stage, package_dir, operation, parameters=None,
                    binding=None, allow_effects=None, options=None):
    if case_id == 'bme280':
        from .benchmark_support.physical import package_execute as execute
        return execute(case_id,run_dir,stage,package_dir,operation,parameters,binding,allow_effects,options)
    from .benchmark_support.cases import _state, _load_case
    from .benchmark_support.emulator import RenodeSession
    from .toolkit import call_tool
    _, state = _state(run_dir)
    package = Path(package_dir).resolve()
    expected = Path(state.get('package_copies', {}).get(stage, '')).resolve()
    if package != expected:
        return {'ok': False, 'error': {'code': 'package', 'message': 'Only the stage-assigned package may execute'}}
    checked = call_tool('emit_check', {'run_dir': str(Path(run_dir).resolve()/'benchmark/package-checks'),
                                      'package_dir': str(package),
                                      'expected_manifest_sha256': state['package_sha256']})
    if not (checked.get('ok') and checked.get('integrity_ok') and checked.get('runtime_current')
            and checked.get('replay_status') == 'passed'):
        return {'ok': False, 'error': {'code': 'package', 'message': 'Package integrity/current-runtime check failed'}}
    session = state['session']
    if binding != session['binding']:
        return {'ok': False, 'error': {'code': 'binding', 'message': 'Package binding differs from evaluator-selected emulator'}}
    command = [sys.executable, '-I', '-S', '-B', '-c',
        'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
        str(package), 'execute', operation, '--parameters', json.dumps(parameters or {}),
        '--binding', json.dumps(binding)]
    for effect in allow_effects or []:
        # Reads are implicit in the emitted CLI; only side effects need a flag.
        if effect != 'read':
            command.extend(['--allow-effect', effect])
    emulator = RenodeSession(session)
    emulator.monitor('start')
    try:
        completed = subprocess.run(command, cwd=package.parent, capture_output=True, text=True, timeout=30)
    finally:
        emulator.monitor('pause')
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        result = {'ok': False, 'error': {'code': 'package', 'message': completed.stderr[-1000:]}}
    _, _, truth = _load_case(options or {})
    observed = emulator.observe(truth)
    row = {'stage': stage, 'revision': state.get('revision', 0), 'attempt_id': state['package_attempts'][stage], 'operation': operation,
           'parameters': parameters or {}, 'ok': completed.returncode == 0 and result.get('ok') is True,
           'result': result, 'observation': observed, 'time': time.time(), 'package_sha256': state['package_sha256'], 'time_policy': 'emulation paused between package calls'}
    path = Path(run_dir)/'benchmark/package-events.jsonl'
    with path.open('a') as stream:
        stream.write(json.dumps(row, allow_nan=False)+'\n')
    return {**result, 'observation': observed, 'evidence': str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='gd benchmark')
    commands = parser.add_subparsers(dest='command', required=True)
    running = commands.add_parser('run')
    running.add_argument('--case', default='setup-smoke', choices=['setup-smoke', 'tq9', 'bme280'])
    running.add_argument('--output')
    running.add_argument('--executor', choices=['codex', 'goose'], default='codex')
    running.add_argument('--budget-seconds', type=int, default=10800)
    running.add_argument('--password-file')
    running.add_argument('--renode')
    running.add_argument('--ghidra-home')
    running.add_argument('--java-home')
    running.add_argument('--home')
    running.add_argument('--request-id')
    approval_flags=running.add_mutually_exclusive_group()
    approval_flags.add_argument('--approve-bound-device-tools', action='store_true', help='Explicitly authorize assigned BME280 tools for the selected physical binding and effects')
    approval_flags.add_argument('--approve-emulator-tools', action='store_true', help='Explicitly authorize scoped tq9 emulator MCP tools; no physical device approval')
    running.add_argument('--binding-json', help='Operator-owned binding JSON, e.g. {"url":"ftdi://selected/1"}')
    comparing = commands.add_parser('compare')
    comparing.add_argument('before')
    comparing.add_argument('after')
    scoring = commands.add_parser('score')
    scoring.add_argument('report')
    scoring.add_argument('--password-file')
    evaluator = commands.add_parser('truth')
    evaluator.add_argument('action', choices=['unlock','rekey','rebuild'])
    evaluator.add_argument('--case',default='tq9',choices=['tq9','bme280'])
    evaluator.add_argument('--password-file',required=True)
    evaluator.add_argument('--output',required=True)
    evaluator.add_argument('--new-password-file')
    evaluator.add_argument('--compiler')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            result = run(args.case, args.output, args.executor,
                         {k: v for k, v in {'budget_seconds': args.budget_seconds,
                          'evaluator_password_file': args.password_file, 'renode': args.renode,
                          'ghidra_home': args.ghidra_home, 'java_home': args.java_home,
                          'home': args.home, 'request_id': args.request_id,
                          'binding': json.loads(args.binding_json) if args.binding_json else None,
                          'scoped_tool_approval': 'bound-device' if args.approve_bound_device_tools else 'emulator' if args.approve_emulator_tools else None}.items() if v is not None})
        elif args.command == 'truth':
            from .benchmark_support.evaluator_cli import manage
            result = manage(args.action,args.case,args.password_file,args.output,args.new_password_file,args.compiler)
        elif args.command == 'compare':
            result = compare(args.before, args.after)
        else:
            result = score(args.report, args.password_file)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result.get('verdict') != 'failed' else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1


def case_root():
    return Path(__file__).resolve().parent / 'resources' / 'bench'


def run(case='setup-smoke', output_dir=None, executor=None, options=None):
    options = dict(options or {})
    approval = options.pop('scoped_tool_approval', None)
    if approval is not None and (case,approval) not in (('tq9','emulator'),('bme280','bound-device')):
        raise ValueError('Scoped approval requires tq9/emulator or bme280/bound-device')
    if approval == 'bound-device' and (not isinstance(options.get('binding'),dict) or not options['binding']):
        raise ValueError('Bound-device approval requires an explicit operator binding')
    if case in ('tq9', 'bme280'):
        from .client import call
        options = dict(options or {})
        home, request_id = options.pop('home', None), options.pop('request_id', None)
        budget = options.pop('budget_seconds', 10800)
        effects = options.pop('effects', ['write', 'actuate'] if case == 'tq9' else ['write'])
        args = {'goal': 'Recover, check, package, freshly reuse and maintain the benchmark device interface.',
                'case': case, 'executor': executor or 'codex', 'budget_seconds': budget,
                'effects': effects, 'case_options': options}
        if options.get('binding') is not None:
            args['binding'] = options['binding']
        if approval is not None:
            args['scoped_tool_approval'] = approval
        if request_id:
            args['request_id'] = request_id
        if output_dir:
            args['output_dir'] = str(Path(output_dir).resolve())
        return call('start', args, home=home)
    if case != 'setup-smoke':
        raise ValueError('Unknown benchmark case: ' + str(case))
    from .toolkit import call_tool
    started = time.monotonic()
    output = Path(output_dir or tempfile.mkdtemp(prefix='generative-driver-smoke-')).resolve()
    output.mkdir(parents=True, exist_ok=True)
    model = output / 'model'
    model.mkdir(exist_ok=True)
    fixture = case_root() / 'cases/setup-smoke'
    shutil.copy2(fixture / 'model.json', model / 'model.json')
    replay = output / 'replay.json'
    shutil.copy2(fixture / 'replay.json', replay)
    calls = []
    def call(name, **arguments):
        result = call_tool(name, {'run_dir': str(output / 'tools'), **arguments})
        calls.append({'tool': name, 'ok': result.get('ok', result.get('_exit', 0) == 0)})
        if result.get('_exit', 0) != 0 or result.get('ok') is False:
            raise RuntimeError(f'Setup fixture {name} failed: {result}')
        return result
    call('model_validate', model_dir=str(model))
    probe = call('probe_run', model_dir=str(model), operation='measure', n=1, replay=str(replay))
    emitted = call('emit_package', model_dir=str(model), probe=probe['probe'])
    package = output / 'relocated package'
    if package.exists():
        raise ValueError('Choose an empty output directory for a new benchmark run')
    shutil.move(emitted['package_dir'], package)
    call('emit_check', package_dir=str(package), expected_manifest_sha256=emitted['manifest_sha256'])
    completed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
        str(package), 'replay'], cwd=output, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise RuntimeError('Relocated package replay failed: ' + completed.stderr[-2000:])
    values = json.loads(completed.stdout)['results'][0]['outputs']
    grade = score_observations({'checks': [{'id': 'temperature', 'expected': 21.5,
                                          'absolute_tolerance': 0}]}, values)
    report = {'schema': 'benchmark-report/1', 'case': case, 'case_version': '1',
              'evaluator_version': '1', 'seed': 0, 'execution': 'scripted-replay',
              'model_benchmark': False, 'agent': None, 'usage': None,
              'elapsed_seconds': time.monotonic() - started, 'verdict': grade['verdict'],
              'observations': values, 'score': grade, 'calls': calls, 'package': str(package),
              'stages': {s: {'status': 'passed' if s in ('probe', 'emit') else 'not_applicable',
                             'execution': 'scripted-replay'} for s in STAGES}}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    report['report'] = str(output / 'report.json')
    return report


def _read(value):
    return json.loads(Path(value).read_text()) if isinstance(value, (str, Path)) else value


def compare(before, after):
    before, after = _read(before), _read(after)
    required = ('case', 'case_version', 'evaluator_version', 'seed', 'execution')
    incompatible = [k for k in required if k not in before or k not in after or before[k] != after[k]]
    changed = [k for k in ('agent', 'toolchain_revision', 'skills_revision', 'budget_seconds', 'environment', 'time_policy')
               if before.get(k) != after.get(k)]
    def delta(key):
        a, b = before.get(key), after.get(key)
        return b - a if type(a) in (int, float) and type(b) in (int, float) else None
    old_usage, new_usage = before.get('usage') or {}, after.get('usage') or {}
    old_tokens, new_tokens = old_usage.get('total_tokens'), new_usage.get('total_tokens')
    stage_changes = {}
    for stage in sorted(set(before.get('stages', {})) | set(after.get('stages', {}))):
        a, b = before.get('stages', {}).get(stage, {}), after.get('stages', {}).get(stage, {})
        row = {key+'_delta': b[key]-a[key] if type(a.get(key)) in (int,float) and type(b.get(key)) in (int,float) else None
               for key in ('elapsed_seconds','worker_seconds','tool_seconds','tool_calls','attempt_count')}
        at,bt = (a.get('usage') or {}).get('total_tokens'), (b.get('usage') or {}).get('total_tokens')
        row['tokens_delta'] = bt-at if type(at) is int and type(bt) is int else None
        for key in ('workflow_status','evaluator_status'):
            row['before_'+key],row['after_'+key] = a.get(key),b.get(key)
        stage_changes[stage] = row
    return {'schema': 'benchmark-comparison/1', 'compatible': not incompatible,
            'incompatible_fields': incompatible, 'changed_dimensions': changed,
            'comparison_kind': 'combined-system' if len(changed) > 1 else changed[0] if changed else 'repeat',
            'before_verdict': before.get('verdict'), 'after_verdict': after.get('verdict'),
            'before_workflow_status':before.get('workflow_status'), 'after_workflow_status':after.get('workflow_status'),
            'stages':stage_changes,
            'elapsed_seconds_delta': delta('elapsed_seconds'),
            'tokens_delta': new_tokens - old_tokens if type(old_tokens) is int and type(new_tokens) is int else None}


def score_observations(truth, observations):
    """Compare observed outputs/effects with independent evidence, never code equality."""
    rows = []
    for check in truth['checks']:
        ident, expected = check['id'], check['expected']
        present = ident in observations
        actual = observations.get(ident)
        if 'absolute_tolerance' in check:
            passed = (present and type(actual) in (int, float) and math.isfinite(actual)
                      and abs(actual - expected) <= check['absolute_tolerance'])
        else:
            passed = present and type(actual) is type(expected) and actual == expected
        rows.append({'id': ident, 'passed': passed, 'observed': actual,
                     'expected': expected, 'reason': None if present else 'missing_observation',
                     **({'absolute_tolerance': check['absolute_tolerance']}
                        if 'absolute_tolerance' in check else {})})
    return {'verdict': 'passed' if rows and all(r['passed'] for r in rows) else 'failed',
            'passed': sum(r['passed'] for r in rows), 'total': len(rows), 'checks': rows}


def cleanup(case_id, run_dir, options=None):
    """Stop only the evaluator-owned emulator; preserve evidence for diagnosis."""
    from .benchmark_support.cases import _state, _write
    from .benchmark_support.emulator import RenodeSession
    path, state = _state(run_dir)
    if state.get('session'):
        RenodeSession(state['session']).stop()
        state['previous_session'] = state.pop('session')
        _write(path, state)
    return {'ok': True}


def score(report, password_file=None):
    """Regrade recorded behavior; missing stage evidence cannot become overall success."""
    data = _read(report)
    case = data.get('case')
    if isinstance(case, dict):
        case = case['id']
    if case == 'setup-smoke':
        grade = score_observations({'checks':[{'id':'temperature','expected':21.5,'absolute_tolerance':0}]},
                                   data.get('observations', {}))
        return {'schema':'benchmark-score/1','case':case,'execution':'scripted-replay',
                'model_benchmark':False, **grade}
    manifest = json.loads((case_root()/'cases'/str(case)/'case.json').read_text())
    for key, expected in {'case_version':manifest['version'], 'evaluator_version':manifest['evaluator_version'], 'truth_sha256':manifest['truth']['sha256']}.items():
        if key in data and data[key] != expected:
            raise ValueError('Report '+key+' does not match installed case')
    from .benchmark_support.emulator import truth_for_case
    truth = truth_for_case(case_root(), manifest, {'evaluator_password_file':password_file})
    state = data.get('case_state', {})
    run_dir = data.get('run_dir')
    if not state and run_dir:
        path = Path(run_dir)/'benchmark/state.json'
        if path.is_file():
            state = json.loads(path.read_text())
    observations = data.get('observations', state.get('probe_evaluation', {}).get('observations', {}))
    if case == 'bme280':
        grounding = state.get('physical_grounding', {})
        observations = grounding.get('observations', {})
        reference = grounding.get('reference', {}).get('reference', {})
        if set(reference) != set(truth['max_tolerances']) or any(
                type(v.get('absolute_tolerance')) not in (int,float) or not 0<v['absolute_tolerance']<=truth['max_tolerances'][k]
                for k,v in reference.items()):
            raise ValueError('Recorded physical reference is incomplete or exceeds evaluator uncertainty limits')
        contract = {'checks':[{'id':k,'expected':v['value'],'absolute_tolerance':v['absolute_tolerance']} for k,v in reference.items()]}
        baseline_reference=grounding.get('reference',{}).get('baseline_reference',{})
        grade = score_stimulus(baseline_reference, reference, observations) if truth.get('require_maintain_stimulus') else score_observations(contract, observations)
    else:
        grade = score_observations(truth, observations) if truth.get('checks') else {'verdict':'unscored','checks':[]}
    stages = data.get('stages', state.get('stage_verdicts', {}))
    missing = [s for s in manifest['required_stages']
               if stages.get(s, {}).get('evaluator_status', stages.get(s, {}).get('status')) != 'passed']
    if data.get('schema') == 'benchmark-report/1':
        if data.get('workflow_status') != 'completed':
            missing.append('completed_workflow')
        missing.extend(s+'_accepted_handoff' for s in manifest['required_stages'] if stages.get(s, {}).get('workflow_status') != 'accepted')
    if case == 'tq9' and not state.get('drift_detected'):
        missing.append('observed_identity_drift')
    return {'schema':'benchmark-score/1','case':case,'execution':manifest['execution'],
            'model_benchmark':True,'verdict':'passed' if grade['verdict']=='passed' and not missing else 'failed',
            'behavior':grade,'missing_required_gates':missing,'truth_sha256':manifest['truth']['sha256'],
            'scope':'Recorded evidence regraded; does not rerun a model or a device'}


def score_stimulus(initial_reference, changed_reference, observations):
    """Require independent ambient change beyond combined uncertainty and a matching fresh reading."""
    changed=[]
    for name, current in changed_reference.items():
        previous=initial_reference.get(name)
        if not isinstance(previous,dict) or not isinstance(current,dict):
            continue
        values=[previous.get('value'),current.get('value'),previous.get('absolute_tolerance'),current.get('absolute_tolerance')]
        if all(type(v) in (int,float) and math.isfinite(v) for v in values) and min(values[2:])>0 and abs(values[1]-values[0])>sum(values[2:]):
            changed.append(name)
    contract={'checks':[{'id':name,'expected':row['value'],'absolute_tolerance':row['absolute_tolerance']}
                        for name,row in changed_reference.items()]}
    behavior=score_observations(contract,observations)
    return {'verdict':'passed' if changed and behavior['verdict']=='passed' else 'failed',
            'changed_channels':changed,'behavior':behavior,
            'criterion':'At least one independently measured change exceeds the sum of both stated uncertainties'}


def evaluation_fault(evaluation):
    """Assign an independently observed failure; expected refusal is not a defect."""
    if evaluation.get('score',{}).get('verdict')=='passed':
        return None
    calls=evaluation.get('calls',[])
    if not calls:
        return 'host'
    faults=[]
    for call in calls:
        result=call.get('result',{})
        if result.get('ok'):
            continue
        fault=result.get('fault')
        if call.get('capability')=='set_duty' and call.get('grants')==[] and fault=='operator':
            continue
        faults.append(fault or 'host')
    return 'operator' if 'operator' in faults else 'host' if 'host' in faults else 'model'

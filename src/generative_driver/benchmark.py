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
    from .benchmark_support.registry import adapter_for
    return adapter_for(case_id).prepare_stage(case_id, stage, run_dir, workspace, accepted, options)


def check_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    from .benchmark_support.registry import adapter_for
    result = adapter_for(case_id).check_stage(case_id, stage, run_dir, workspace, report, accepted, options)
    from .benchmark_support.cases import _state, _write
    state_path, state = _state(run_dir)
    state.setdefault('stage_verdicts', {})[stage] = {'status': 'passed' if result.get('ok') else 'failed',
        'checks': result.get('checks', []), 'revision': state.get('revision', 0), 'reason': result.get('reason')}
    _write(state_path, state)
    return result


def package_execute(case_id, run_dir, stage, package_dir, operation, parameters=None,
                    binding=None, allow_effects=None, options=None):
    from .benchmark_support.registry import adapter_for
    return adapter_for(case_id).package_execute(case_id, run_dir, stage, package_dir,
        operation, parameters, binding, allow_effects, options)


def main(argv=None):
    from .benchmark_support.registry import case_ids, case_descriptors
    parser = argparse.ArgumentParser(prog='gd benchmark')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('cases')
    running = commands.add_parser('run')
    running.add_argument('--case', default='setup-smoke', choices=case_ids())
    running.add_argument('--scenario')
    running.add_argument('--case-seed', type=int, default=0)
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
    scoring.add_argument('--evidence')
    evaluator = commands.add_parser('truth')
    evaluator.add_argument('action', choices=['unlock','rekey','rebuild','calibrate'])
    evaluator.add_argument('--case',default='tq9',choices=case_ids())
    evaluator.add_argument('--password-file')
    evaluator.add_argument('--output',required=True)
    evaluator.add_argument('--new-password-file')
    evaluator.add_argument('--compiler')
    evaluator.add_argument('--authoring-dir')
    evaluator.add_argument('--renode')
    evaluator.add_argument('--ghidra-home')
    evaluator.add_argument('--java-home')
    args = parser.parse_args(argv)
    try:
        if args.command == 'cases':
            result = case_descriptors()
        elif args.command == 'run':
            result = run(args.case, args.output, args.executor,
                         {k: v for k, v in {'budget_seconds': args.budget_seconds,
                          'evaluator_password_file': args.password_file, 'renode': args.renode,
                          'ghidra_home': args.ghidra_home, 'java_home': args.java_home,
                          'home': args.home, 'request_id': args.request_id,
                          'scenario_id': args.scenario, 'case_seed': args.case_seed,
                          'binding': json.loads(args.binding_json) if args.binding_json else None,
                          'scoped_tool_approval': 'bound-device' if args.approve_bound_device_tools else 'emulator' if args.approve_emulator_tools else None}.items() if v is not None})
        elif args.command == 'truth':
            from .benchmark_support.evaluator_cli import manage
            result = manage(args.action,args.case,args.password_file,args.output,args.new_password_file,args.compiler,args.authoring_dir,
                            renode=args.renode,ghidra_home=args.ghidra_home,java_home=args.java_home)
        elif args.command == 'compare':
            result = compare(args.before, args.after)
        else:
            result = score(args.report, args.password_file, evidence_path=args.evidence)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if not isinstance(result, dict) or result.get('verdict') != 'failed' else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1


def case_root():
    return Path(__file__).resolve().parent / 'resources' / 'bench'


def run(case='setup-smoke', output_dir=None, executor=None, options=None, *, autostart=True):
    from .benchmark_support.registry import resolve_case, pin_case
    options = dict(options or {})
    definition = resolve_case(case)
    pin = pin_case(case, options.get('scenario_id'), options.get('case_seed', 0))
    if definition.manifest.get('schema') == 'benchmark-case/2':
        from .benchmark_support.registry import require_calibration
        require_calibration(pin, options)
    approval = options.pop('scoped_tool_approval', None)
    if approval == 'emulator' and not (definition.execution == 'actual-agent-emulation' and definition.approval_scope == 'emulator'):
        raise ValueError('Scoped approval requires tq9/emulator or bme280/bound-device')
    if approval == 'emulator' and 'binding' in options:
        raise ValueError('Emulator approval cannot authorize an operator-supplied binding')
    if approval not in (None, 'emulator') and approval != definition.approval_scope:
        raise ValueError('Scoped approval requires tq9/emulator or bme280/bound-device')
    if approval == 'bound-device' and (not isinstance(options.get('binding'),dict) or not options['binding']):
        raise ValueError('Bound-device approval requires an explicit operator binding')
    if definition.execution.startswith('actual-agent-'):
        from .client import call
        options = dict(options or {})
        home, request_id = options.pop('home', None), options.pop('request_id', None)
        budget = options.pop('budget_seconds', 10800)
        effects = options.pop('effects', list(definition.default_effects))
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
        return call('start', args, home=home, autostart=autostart)
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
    from .benchmark_support.registry import adapter_for
    return adapter_for(case_id).cleanup(case_id, run_dir, options)


def score(report, password_file=None, *, evidence_path=None):
    from .benchmark_support.registry import adapter_for
    data = _read(report)
    if data.get('schema') == 'benchmark-report/2':
        from .benchmark_support.evidence import regrade_v2
        return regrade_v2(data, evidence_path, password_file)
    case = data.get('case')
    case_id = case.get('id') if isinstance(case, dict) else case
    return adapter_for(case_id).score(data, password_file)


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

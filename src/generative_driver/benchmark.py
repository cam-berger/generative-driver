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

# The evaluator uses these too, so they are defined in a module of its own and imported here under their old names.
from .benchmark_support.case_assets import STAGES, case_root


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    from .benchmark_support.adapters import adapter_for
    return adapter_for(case_id).prepare_stage(case_id, stage, run_dir, workspace, accepted, options)


def observe_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    """Truth-free stage observation: the checked contract of the configurator's stage loop (artifacts, checks as
    notes, optional stop). It runs no evaluator, grades nothing and records no stage verdict."""
    from .benchmark_support.adapters import adapter_for
    return adapter_for(case_id).observe_stage(case_id, stage, run_dir, workspace, report, accepted, options)


def package_call(case_id, run_dir, stage, arguments):
    """A provided device's package call, checked before anything is dispatched; returns the call to run."""
    from .benchmark_support.adapters import adapter_for
    return adapter_for(case_id).package_call(case_id, run_dir, stage, arguments)


def record_call(case_id, run_dir, stage, name, arguments, result, before, after, options=None):
    """Journal one live call on a provided device with the instrument readings around it."""
    from .benchmark_support.adapters import adapter_for
    return adapter_for(case_id).record_call(case_id, run_dir, stage, name, arguments, result, before, after, options)


def package_execute(case_id, run_dir, stage, package_dir, operation, parameters=None,
                    binding=None, allow_effects=None, options=None):
    """A package call on the operator's bound device (bme280), which the configurator dispatches itself."""
    from .benchmark_support.adapters import adapter_for
    return adapter_for(case_id).package_execute(case_id, run_dir, stage, package_dir,
        operation, parameters, binding, allow_effects, options)


def main(argv=None):
    from .benchmark_support.registry import case_ids, case_descriptors
    from .benchmark_support.registrations import build_case_ids
    parser = argparse.ArgumentParser(prog='gd benchmark')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('cases')
    corpus = commands.add_parser('corpus', help='Inspect upstream coverage and freeze a software-first corpus plan')
    corpus_actions = corpus.add_subparsers(dest='action', required=True)
    for action in ('list', 'plan'):
        selection = corpus_actions.add_parser(action)
        selection.add_argument('--catalog', help='Versioned corpus catalog; defaults to the seven-area pilot')
        if action == 'plan':
            selection.add_argument('--repetitions', type=int, default=3)
            selection.add_argument('--include', action='append', metavar='LINEAGE',
                                   help='Select lineage IDs from the full catalog; repeat for a subset')
            selection.add_argument('--exclude', action='append', nargs=2, metavar=('LINEAGE', 'REASON'),
                                   help='Record a lineage exclusion and its reason; keep its coverage slots')
        selection.add_argument('--output', help='Save the catalog or frozen plan as JSON')
    building = corpus_actions.add_parser('build', help='Rebuild a pinned upstream firmware twice; no inference')
    building.add_argument('--case', choices=build_case_ids(), required=True)
    building.add_argument('--backend', choices=['renode', 'native-sim'], default='renode')
    for name in ('source', 'compiler', 'west', 'output'):
        building.add_argument('--' + name, required=True)
    comparing_execution = corpus_actions.add_parser('compare-execution', help='Compare retained native/Renode RTU episodes; no inference')
    comparing_execution.add_argument('native')
    comparing_execution.add_argument('reference')
    comparing_execution.add_argument('--output')
    exercising = corpus_actions.add_parser('exercise-execution', help='Run the same bounded RTU qualification on a registered execution setup')
    exercising.add_argument('--case', choices=case_ids(), required=True)
    exercising.add_argument('--password-file', required=True)
    exercising.add_argument('--renode')
    exercising.add_argument('--output', required=True)
    preparing_execution = corpus_actions.add_parser('prepare-execution', help='Prepare an external case root from native build evidence; admission remains pending')
    preparing_execution.add_argument('--build', required=True)
    preparing_execution.add_argument('--password-file', required=True)
    preparing_execution.add_argument('--native-record')
    preparing_execution.add_argument('--reference-record')
    preparing_execution.add_argument('--output', required=True)
    corpus_start = corpus_actions.add_parser('start', help='Run admitted cases from an unchanged frozen plan')
    corpus_start.add_argument('--plan', required=True)
    corpus_start.add_argument('--executor', choices=['codex', 'goose'])
    corpus_start.add_argument('--options')
    corpus_start.add_argument('--request-id')
    corpus_check = corpus_actions.add_parser('check', help='Authenticate selected case qualification without launching agents')
    corpus_check.add_argument('--plan', required=True)
    corpus_check.add_argument('--options', help='Operator-only evaluator password handles; native tools are not run')
    corpus_check.add_argument('--output', help='Save the public qualification result as JSON')
    corpus_report = corpus_actions.add_parser('report', help='Join a frozen coverage plan with its saved suite report')
    corpus_report.add_argument('--plan', required=True)
    report_source = corpus_report.add_mutually_exclusive_group()
    report_source.add_argument('--suite-report', help='Previously exported suite.json')
    report_source.add_argument('--suite-id', help='Read this suite from the configured owner')
    corpus_report.add_argument('--output')
    suites = commands.add_parser('suite').add_subparsers(dest='action', required=True)
    starting = suites.add_parser('start')
    starting.add_argument('--manifest', required=True)
    starting.add_argument('--executor', choices=['codex', 'goose', 'replay'],
                          help='Default: the one configured runtime; replay runs options.replay_candidates without a model')
    starting.add_argument('--options')
    starting.add_argument('--request-id')
    for action in ('status', 'events', 'result', 'cancel', 'resume'):
        lifecycle = suites.add_parser(action)
        lifecycle.add_argument('suite_id')
        if action == 'events':
            lifecycle.add_argument('--after', type=int, default=0)
        elif action == 'result':
            lifecycle.add_argument('--offset', type=int, default=0)
            lifecycle.add_argument('--limit', type=int, default=50)
        elif action == 'resume':
            lifecycle.add_argument('--suite-budget-seconds', type=float)
            lifecycle.add_argument('--budget-reason')
            lifecycle.add_argument('--child-budget-seconds', type=float,
                                   help='Approved new total budget of the stopped child the resume continues')
            lifecycle.add_argument('--child-budget-reason')
            lifecycle.add_argument('--options', help='JSON file with the evaluator password files and emulator executable '
                                   'for the bench harness that resumes the suite')
    exporting = suites.add_parser('report')
    exporting.add_argument('suite_id')
    exporting.add_argument('--output', required=True)
    exporting.add_argument('--audit-path', action='append', metavar='PATH',
                           help='A forbidden path: flag a trial whose worker ran a command that names it, and leave '
                           'that trial ungraded. Repeat for each path. The export shows a path only in the commands '
                           'that name it')
    scorecard = suites.add_parser('scorecard', help='Measure a suite from its retained records: its pass rate and, per stage, '
                                  'time, tokens, tool and device use; a scoring suite shows aggregates only')
    scorecard.add_argument('suite_id')
    scorecard.add_argument('--output', required=True, help='Directory for scorecard.json and scorecard.md')
    scorecard.add_argument('--audit-path', action='append', metavar='PATH',
                           help='A forbidden path: flag a trial whose worker ran a command that names it, and leave '
                           'that trial ungraded. Repeat for each path. The scorecard quotes no command')
    suite_comparing = suites.add_parser('compare')
    suite_comparing.add_argument('before')
    suite_comparing.add_argument('after')
    suite_comparing.add_argument('--leak-check', metavar='REPORT',
                                 help='Saved suite report of the after system on a fresh batch of devices: with two '
                                 'reports of one scoring set, tests whether the set leaked')
    running = commands.add_parser('run')
    running.add_argument('--case', default='setup-smoke', choices=case_ids())
    running.add_argument('--scenario')
    running.add_argument('--case-seed', type=int, default=0)
    running.add_argument('--output')
    running.add_argument('--executor', choices=['codex', 'goose', 'replay'],
                         help='Default: the one configured runtime; replay runs --replay-candidate without a model')
    running.add_argument('--replay-candidate', help='Saved candidate directory for --executor replay: replay.json and '
                         'the worker outputs it names')
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
    running.add_argument('--wait', action='store_true', help='Follow a trial until its bench harness has finished')
    resuming = commands.add_parser('trial-resume', help='Complete the evaluation of a trial whose harness has finished: '
                                   'grade it if it is unscored (never a final already graded), or a bound-device trial '
                                   'whose run completed after its harness stopped following it, and post the verdict')
    resuming.add_argument('trial_dir')
    resuming.add_argument('--password-file', required=True)
    resuming.add_argument('--renode', help='Emulator executable; required when the trial\'s frozen driver still needs grading')
    comparing = commands.add_parser('compare')
    comparing.add_argument('before')
    comparing.add_argument('after')
    scoring = commands.add_parser('score')
    scoring.add_argument('report')
    scoring.add_argument('--password-file')
    scoring.add_argument('--evidence', help='V2 sealed evidence, or the saved BME280 independent reference file (digest checked)')
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
        if args.command == 'corpus':
            if args.action == 'prepare-execution':
                from .benchmark_support.upstream_build import prepare_execution
                result = prepare_execution(args.build, args.password_file, args.output,
                    native_record=args.native_record, reference_record=args.reference_record)
                print(json.dumps(result, indent=2, allow_nan=False))
                return 0
            if args.action == 'exercise-execution':
                from .benchmark_support.rehosting import exercise_execution
                result = exercise_execution(args.case, args.password_file, args.output, renode=args.renode)
                print(json.dumps(result, indent=2, allow_nan=False))
                return 0 if result['ok'] else 1
            if args.action == 'compare-execution':
                from .benchmark_support.rehosting import compare_executions
                result = compare_executions(_read(args.native), _read(args.reference))
                encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
                if args.output:
                    Path(args.output).write_text(encoded, encoding='utf-8')
                print(encoded, end='')
                return 0 if result['verdict'] == 'passed' else 1
            from .benchmark_support.corpus import load_catalog, plan_corpus, report_corpus, start_corpus
            if args.action == 'build':
                from .benchmark_support.upstream_build import build
                result = build(args.source, args.compiler, args.west, args.output, backend=args.backend)
                print(json.dumps(result, indent=2, allow_nan=False))
                return 0
            if args.action in ('start', 'report', 'check'):
                plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
                if args.action == 'start':
                    result = start_corpus(plan, args.executor,
                        json.loads(Path(args.options).read_text(encoding='utf-8')) if args.options else {}, args.request_id)
                    print(json.dumps(result, indent=2, allow_nan=False))
                    return 0 if result.get('ok') else 1
                if args.action == 'check':
                    from .benchmark_support.preflight import check_corpus
                    result = check_corpus(plan,
                        json.loads(Path(args.options).read_text(encoding='utf-8')) if args.options else {})
                else:
                    if args.suite_id:
                        from .benchmark_support.suite_reporting import report_suite
                        if args.output:
                            destination = Path(args.output)
                            suite = report_suite(args.suite_id,
                                output=destination.with_name(destination.stem + '-suite'))
                        else:
                            from tempfile import TemporaryDirectory
                            with TemporaryDirectory(prefix='gd-corpus-report-') as temporary:
                                suite = report_suite(args.suite_id, output=temporary)
                    else:
                        suite = args.suite_report
                    result = report_corpus(plan, suite)
            else:
                catalog = load_catalog(args.catalog)
                result = plan_corpus(catalog, args.repetitions, include=args.include,
                                     exclude=args.exclude) if args.action == 'plan' else catalog
            encoded = json.dumps(result, indent=2, allow_nan=False) + '\n'
            if args.output:
                Path(args.output).write_text(encoded, encoding='utf-8')
            print(encoded, end='')
            return 0 if result.get('ok', True) else 1
        if args.command == 'suite':
            if args.action == 'compare':
                from .benchmark_support.suite_reporting import compare_suites
                result = compare_suites(args.before, args.after, leak_check=args.leak_check)
            elif args.action == 'report':
                from .benchmark_support.suite_reporting import report_suite
                result = report_suite(args.suite_id, output=args.output, audit_paths=tuple(args.audit_path or ()))
            elif args.action == 'scorecard':
                from .client import default_home
                from .benchmark_support.scorecard import bench_trial_dirs, suite_scorecard, write_scorecard
                from .benchmark_support.suite_reporting import report_suite
                home = Path(default_home()).expanduser().resolve()
                # The export holds each trial's details, which a scoring suite never shows: only its aggregate stays.
                with tempfile.TemporaryDirectory(prefix='gd-scorecard-') as temporary:
                    report = report_suite(args.suite_id, home=home, output=temporary, audit_paths=tuple(args.audit_path or ()))
                result = suite_scorecard(report, home=home, trial_dirs=bench_trial_dirs(report, home))
                write_scorecard(result, args.output)
            elif args.action == 'start':
                from .benchmark_support.suites import start_suite
                params = {'manifest': json.loads(Path(args.manifest).read_text(encoding='utf-8')),
                    'executor': args.executor,
                    'options': json.loads(Path(args.options).read_text(encoding='utf-8')) if args.options else {},
                    'request_id': args.request_id}
                result = start_suite(**params)
            elif args.action == 'resume':
                # A newly launched bench harness takes the suite over; password files and the emulator go to it on stdin.
                from .benchmark_support.suites import resume_suite
                result = resume_suite(args.suite_id, json.loads(Path(args.options).read_text(encoding='utf-8')) if args.options else None,
                                      suite_budget_seconds=args.suite_budget_seconds, budget_reason=args.budget_reason,
                                      child_budget_seconds=args.child_budget_seconds,
                                      child_budget_reason=args.child_budget_reason)
            else:
                from .client import call
                params = {'suite_id': args.suite_id}
                for key in ('after', 'offset', 'limit'):
                    if hasattr(args, key) and getattr(args, key) is not None:
                        params[key] = getattr(args, key)
                result = call('suite_' + args.action, params)
            print(json.dumps(result, indent=2, allow_nan=False))
            return 0 if result.get('ok', result.get('compatible', True)) else 1
        if args.command == 'cases':
            result = case_descriptors()
        elif args.command == 'run':
            result = run(args.case, args.output, args.executor,
                         {k: v for k, v in {'budget_seconds': args.budget_seconds,
                          'evaluator_password_file': args.password_file, 'renode': args.renode,
                          'ghidra_home': args.ghidra_home, 'java_home': args.java_home,
                          'home': args.home, 'request_id': args.request_id, 'replay_candidate': args.replay_candidate,
                          'scenario_id': args.scenario, 'case_seed': args.case_seed,
                          'binding': json.loads(args.binding_json) if args.binding_json else None,
                          'scoped_tool_approval': 'bound-device' if args.approve_bound_device_tools else 'emulator' if args.approve_emulator_tools else None}.items() if v is not None},
                         wait=args.wait)
        elif args.command == 'truth':
            from .benchmark_support.evaluator_cli import manage
            result = manage(args.action,args.case,args.password_file,args.output,args.new_password_file,args.compiler,args.authoring_dir,
                            renode=args.renode,ghidra_home=args.ghidra_home,java_home=args.java_home)
        elif args.command == 'trial-resume':
            from .benchmark_support.harness import resume
            result = resume(args.trial_dir, password_file=args.password_file, renode=args.renode)
        elif args.command == 'compare':
            result = compare(args.before, args.after)
        else:
            result = score(args.report, args.password_file, evidence_path=args.evidence)
        print(json.dumps(result, indent=2, allow_nan=False))
        failed = isinstance(result, dict) and (result.get('verdict') in ('failed', 'not equivalent')
                                               or args.command == 'truth' and result.get('ok') is False)
        return 1 if failed else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1


def run(case='setup-smoke', output_dir=None, executor=None, options=None, *, autostart=True, wait=False, via_owner=False):
    """Run the installation replay, or start an actual-agent trial. Every actual-agent case starts a detached bench
    harness, which admits the case, holds the evaluator password file and grades the frozen driver once after the run;
    it provides the emulator of an emulated case (v2 or legacy tq9) and leaves the operator's bound device (bme280)
    alone. The configurator never receives the password file. That returns {trial_id, run_id}; `wait` follows the
    trial until the harness has finished with it."""
    from .benchmark_support.registry import resolve_case, pin_case
    options = dict(options or {})
    definition = resolve_case(case)
    if executor == 'replay' and definition.execution != 'actual-agent-emulation':
        raise ValueError('The replay executor runs emulated benchmark cases only; ' + case + ' is ' + definition.execution)
    pin_case(case, options.get('scenario_id'), options.get('case_seed', 0))
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
        from .benchmark_support.harness import launch
        if not autostart:
            # A configurator that must already run (Windows) is asked before any harness or device starts.
            from .client import call, default_home
            reachable = call('ping', {}, home=Path(options.get('home') or default_home()).expanduser(), autostart=False)
            if not reachable.get('ok'):
                return reachable
        return launch(case, output_dir, executor, options, approval, effects=definition.default_effects,
                      autostart=autostart, wait=wait, via_owner=via_owner)
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


def _protocol(report):
    """The protocol a run report was produced under; None for an earlier one."""
    progress = report.get('progress')
    return report.get('protocol') or (progress.get('protocol') if isinstance(progress, dict) else None)


def _replayed(report):
    """Whether a report is a replayed candidate's: by its execution label or the runtime it records."""
    executed = report.get('executed') if isinstance(report.get('executed'), dict) else {}
    configuration = executed.get('runtime_configuration') if isinstance(executed.get('runtime_configuration'), dict) else {}
    agent = report.get('agent') if isinstance(report.get('agent'), dict) else {}
    return str(report.get('execution') or '').startswith('replay') or 'replay' in (configuration.get('runtime'),
                                                                                    agent.get('runtime'))


def _report_field(report, path):
    for key in path.split('.'):
        report = report.get(key) if isinstance(report, dict) else None
    return report


def compare(before, after):
    before, after = _read(before), _read(after)
    if _protocol(before) != _protocol(after):
        # Results of different protocols are separate series: nothing of one is set beside the other.
        return {'schema': 'benchmark-comparison/1', 'compatible': False, 'incompatible_fields': ['protocol'],
                'reason': 'Reports of different protocols are separate series and are never compared'}
    if _replayed(before) != _replayed(after):
        # No model ran a replayed candidate: replays are a series of their own, never set beside model runs.
        return {'schema': 'benchmark-comparison/1', 'compatible': False, 'incompatible_fields': ['execution'],
                'reason': 'Replay results and model runs are separate series and are never compared'}
    v3 = 'benchmark-report/3' in (before.get('schema'), after.get('schema'))
    required = ('case', 'case_version', 'evaluator_version', 'seed', 'execution')
    dimensions = {key: key for key in ('agent', 'toolchain_revision', 'skills_revision', 'budget_seconds',
                                      'environment', 'time_policy')}
    if v3:
        required = ('schema', 'case', 'case_version', 'evaluator_version', 'scenario_id', 'case_seed', 'execution')
        # Public v3 exports retain the executed configuration, not the old agent/environment aliases.
        dimensions = {key: 'executed.runtime_configuration.' + key
                      for key in ('runtime', 'model', 'provider', 'version', 'reasoning_effort', 'max_turns', 'candidate_sha256')}
        dimensions.update(environment='executed.environment', toolchain_revision='toolchain_revision',
                          skills_revision='skills_revision', original_budget_seconds='original_budget_seconds',
                          effective_budget_seconds='effective_budget_seconds')
    incompatible = [k for k in required if k not in before or k not in after or before[k] != after[k]]
    if v3:
        identities = ('evaluator_revision', 'case_pin.manifest_sha256', 'case_pin.truth_sha256', 'case_pin.pin_sha256')
        incompatible.extend(key for key in identities if _report_field(before, key) is None
                            or _report_field(before, key) != _report_field(after, key))
    changed = [key for key, path in dimensions.items() if _report_field(before, path) != _report_field(after, path)]
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
    """No stage adapter owns a device. An emulated case's device belongs to the bench harness, which alone stops it, and
    a bound device is the operator's: nothing here may stop or forget it, whatever a run state records."""
    from .benchmark_support.adapters import adapter_for
    adapter_for(case_id)
    raise RuntimeError('The bench harness owns this case\'s device; the configurator holds no ownership to clean up')


def score(report, password_file=None, *, evidence_path=None):
    from .benchmark_support.adapters import adapter_for
    data = _read(report)
    if data.get('schema') == 'benchmark-report/3':
        from .benchmark_support.evidence import regrade_v3
        return regrade_v3(data, evidence_path, password_file)
    if data.get('schema') == 'benchmark-report/2':
        from .benchmark_support.evidence import regrade_v2
        return regrade_v2(data, evidence_path, password_file)
    case = data.get('case')
    case_id = case.get('id') if isinstance(case, dict) else case
    if case_id == 'bme280':
        return adapter_for(case_id).score(data, password_file, evidence_path=evidence_path)
    return adapter_for(case_id).score(data, password_file)



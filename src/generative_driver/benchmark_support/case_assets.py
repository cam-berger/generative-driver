"""Case files, accepted artifacts and legacy mission checks that the stage adapters and the evaluator both use; they live
here so the evaluator imports neither the benchmark module nor the stage adapters."""
import json
from pathlib import Path

from .instruments import family_for
from .registry import resolve_case, validate_pinned_workflow
from .snapshots import read_snapshot

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
# The record of the package calls of a fresh reuse, which the reuse stage hands on.
REUSE_EVENTS = 'reuse-events.jsonl'
TASKS = ('temperature', 'arm', 'set_duty', 'disarm')
# The public reuse mission requests 37 percent output, then disarm; the instrument reads the duty as a fraction.
REQUESTED = .37


def case_root():
    import os
    selected = {}
    for name in ('GENERATIVE_DRIVER_BENCH_ROOT', 'GENERATIVE_DRIVER_CASE_ROOT'):
        if name in os.environ:
            path = Path(os.environ[name]).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(name + ' must name an existing absolute resource directory')
            selected[name] = path.resolve()
    if len(set(selected.values())) > 1:
        raise ValueError('Conflicting GENERATIVE_DRIVER_BENCH_ROOT and GENERATIVE_DRIVER_CASE_ROOT')
    if selected:
        return next(iter(selected.values()))
    return Path(__file__).resolve().parents[1] / 'resources' / 'bench'


def case_inputs(case_id, run_dir):
    if (Path(run_dir)/'benchmark/execution.json').exists():
        saved = read_snapshot(run_dir)
        validate_pinned_workflow(saved['case_pin'])
        if saved['case_pin']['case_id'] != case_id:
            raise ValueError('Execution snapshot case mismatch')
        return Path(run_dir)/'benchmark/inputs', saved['case_pin']['manifest']
    case = resolve_case(case_id)
    return case.root, case.manifest


def _named(path, name):
    prefix, separator, original = Path(path).name.partition('-')
    return Path(path).name == name or bool(separator) and prefix.isdigit() and original == name


def accepted_artifact(handoff, name, sha256=None):
    """(path, recorded sha256) of `name` in one accepted handoff (with that hash, when given), or None."""
    for artifact in (handoff or {}).get('artifacts', []):
        if _named(artifact['path'], name) and sha256 in (None, artifact['sha256']):
            return Path(artifact['path']), artifact['sha256']
    return None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def mapping_errors(mapping, model):
    """The structural errors of a legacy tq9 capability mapping against its model, or []: every task an object naming
    one of the model's operations, with object parameters, and temperature naming an output of its operation."""
    if not isinstance(mapping, dict):
        return [{'path': 'capabilities.json', 'message': 'The mapping must be a JSON object of tasks'}]
    operations = model.get('operations') if isinstance(model, dict) and isinstance(model.get('operations'), dict) else {}
    errors = []
    for task in TASKS:
        entry = mapping.get(task)
        if not isinstance(entry, dict):
            errors.append({'path': task, 'message': 'Task is missing or not an object'})
            continue
        operation = operations.get(entry.get('operation')) if isinstance(entry.get('operation'), str) else None
        if not isinstance(operation, dict):
            errors.append({'path': task + '.operation', 'message': 'Names no operation of the model'})
        if not isinstance(entry.get('parameters', {}), dict):
            errors.append({'path': task + '.parameters', 'message': 'Parameters must be an object'})
        outputs = operation.get('outputs') if isinstance(operation, dict) else None
        if task == 'temperature' and not (isinstance(entry.get('output'), str) and isinstance(outputs, dict)
                                          and entry['output'] in outputs):
            errors.append({'path': 'temperature.output', 'message': 'Names no output of the task\'s operation'})
    return errors


def mission_done(events, capabilities):
    """Whether fresh reuse did the public legacy mission with the frozen package: a temperature read, the requested 37
    percent output and disarm, judged from the package results and the instrument's duty readings after them. A note on
    the reuse stage, never a grade."""
    spec = capabilities['temperature']
    duties = [e['observation']['duty'] for e in events if e['result'].get('ok') is True and isinstance(e.get('observation'), dict)]
    read = any(e.get('operation') == spec['operation'] and e['result'].get('ok') is True
               and type(e['result'].get('outputs', {}).get(spec['output'])) in (int, float) for e in events)
    return read and bool(duties) and any(abs(d - REQUESTED) <= .002 for d in duties) and duties[-1] == 0


def reuse_mission(case_id, events, capabilities):
    """Whether the fresh worker's package calls did the public mission, judged from their outputs and the instrument
    readings of the emulated device: a note on the reuse stage, never a grade. Failed calls are attributed only on
    trusted evidence: untyped or mixed failures blame nobody."""
    try:
        if case_id == 'tq9':
            done = mission_done(events, capabilities)
        else:
            done = family_for(case_id).reuse_passed(events, capabilities)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as error:
        return {'name': 'fresh_worker_mission', 'passed': None,
                'reason': 'The capability mapping cannot judge the mission: ' + type(error).__name__ + ': ' + str(error)}
    if done:
        return {'name': 'fresh_worker_mission', 'passed': True}
    failed = [e['result'] for e in events if e['result'].get('ok') is not True]
    faults = [r['error'].get('fault') if isinstance(r.get('error'), dict) else None for r in failed]
    default = None if any(f != 'model' for f in faults) else 'model'
    fault = next((f for f in faults if f in ('host', 'operator')), default)
    return {'name': 'fresh_worker_mission', 'passed': False,
            'reason': 'Fresh worker mission lacks observed reading, requested effect or disarm', **({'fault': fault} if fault else {})}

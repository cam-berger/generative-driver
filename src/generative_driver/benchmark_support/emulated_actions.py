"""Private evaluator action execution; no service, worker gateway or process owner."""
from .cases import _write
from .emulated import run_call


def execute_plan(session, invoke, plan, contract, evidence_path, observations, settle_seconds=0):
    """Invoke canonical tasks; persist raw responses and IDs before returning records.

    invoke(task, inputs, grants) returns canonical outputs and measured runtime units.
    observations(raw, result, inputs) returns check suffix -> value. Monitor units
    are supplied by that measurement function's optional ``monitor_units`` map.
    """
    checks = {row['id']: row for row in contract['checks']}
    records, raw_events = [], []
    result, inputs = {}, {}
    needs_settle = False
    if not 0 <= settle_seconds <= 10:
        raise ValueError('Invalid evaluator settle time')
    try:
        for action in plan:
            kind = action['kind']
            event = {'action': action}
            if kind in ('reset', 'stimulate'):
                event['raw'] = getattr(session, kind)(action['values'])
                needs_settle = True
            elif kind == 'call':
                inputs = action['inputs']
                def operation(task, values):
                    if needs_settle:
                        __import__('time').sleep(settle_seconds)
                    return invoke(task, values, action['grants'])
                result = run_call(session, operation,
                                  {'operation': action['task'], 'parameters': inputs})
                needs_settle = False
                event['result'] = result
                if result.get('error', {}).get('fault') == 'host':
                    raw_events.append(event)
                    raise RuntimeError('Host execution failed; behavioral grading is unavailable')
            elif kind == 'observe':
                raw = session.observe()
                event['raw'] = raw
                measured = observations(raw, result, inputs)
                monitor_units = getattr(observations, 'monitor_units', {})
                for identifier in action['checks']:
                    check = checks[identifier]
                    suffix = identifier.rsplit('/', 1)[-1]
                    units = monitor_units if check['channel'] == 'independent-monitor' else result.get('units', {})
                    if suffix == 'refused_without_io':
                        units = {'refused_without_io': 'boolean'}
                    if suffix == 'operation_ok' and check['channel'] == 'runtime-transcript':
                        units = {'operation_ok': 'boolean'}
                        value = result.get('ok') is True
                    else:
                        value = measured.get(suffix)
                    record = {'id': identifier, 'task_id': suffix, 'revision': check['revision'],
                        'artifact_sha256': contract['artifact_sha256'], 'channel': check['channel'],
                        'value': value, 'unit': units.get(suffix)}
                    error = result.get('error', {})
                    if error.get('fault') == 'model' and error.get('code') == 'capability_mapping':
                        record['capability_error'] = error
                    records.append(record)
            else:
                raise ValueError('Unknown evaluator action')
            raw_events.append(event)
            _write(evidence_path, {'schema': 'benchmark-action-evidence/1', 'events': raw_events, 'records': records})
    except Exception:
        _write(evidence_path, {'schema': 'benchmark-action-evidence/1', 'events': raw_events, 'records': records,
                               'incomplete': True})
        raise
    return records


def model_invoker(model_dir, capabilities, binding, output_dir, probes):
    """Use the toolkit's normal probe operation; retain executable probe evidence."""
    import json
    from pathlib import Path
    from ..toolkit import call_tool
    from .behavior import bind_task
    model = json.loads((Path(model_dir)/'model.json').read_text())
    def invoke(task, inputs, grants):
        try:
            request = bind_task(capabilities, task, inputs, model)
        except ValueError as error:
            return {'ok': False, 'error': {'fault': 'model', 'code': 'capability_mapping',
                                         'task': task, 'message': str(error)},
                    'outputs': {}, 'units': {}, 'transcript': []}
        probed = call_tool('probe_run', {'run_dir': str(output_dir), 'model_dir': str(model_dir),
            **request, 'n': 1, 'binding': binding, 'allow_effects': grants})
        if probed.get('probe'):
            probes.append(probed['probe'])
        result = (probed.get('results') or [{}])[0]
        outputs = capabilities['tasks'][task]['outputs']
        return {**result, 'ok': bool(probed.get('ok') and result.get('ok')),
            'outputs': {key: result.get('outputs', {}).get(mapping['output']) for key, mapping in outputs.items()},
            'units': {key: result.get('units', {}).get(mapping['output']) for key, mapping in outputs.items()}}
    return invoke


def package_invoker(package, binding, grants):
    """Normal installed package CLI, isolated from the source checkout."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    def invoke(operation, parameters):
        command = [sys.executable, '-I', '-S', '-B', '-c',
            'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
            str(package), 'execute', operation, '--parameters', json.dumps(parameters), '--binding', json.dumps(binding)]
        for effect in grants:
            if effect != 'read':
                command.extend(['--allow-effect', effect])
        completed = subprocess.run(command, cwd=Path(package).parent, capture_output=True, text=True, timeout=30)
        try:
            result = json.loads(completed.stdout)
        except ValueError as error:
            raise RuntimeError('Package did not return a runtime result') from error
        return result
    return invoke


def canonical_package_invoker(package, capabilities, binding):
    import json
    from pathlib import Path
    from .behavior import bind_task
    model = json.loads((Path(package)/'driver/model.json').read_text())
    def invoke(task, inputs, grants):
        request = bind_task(capabilities, task, inputs, model)
        result = package_invoker(package, binding, grants)(**request)
        mappings = capabilities['tasks'][task]['outputs']
        return {**result,
            'outputs': {key: result.get('outputs', {}).get(mapping['output']) for key, mapping in mappings.items()},
            'units': {key: result.get('units', {}).get(mapping['output']) for key, mapping in mappings.items()}}
    return invoke

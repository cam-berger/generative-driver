"""Private evaluator action execution; no service, worker gateway or process owner."""
from .behavior import _check_rejection_evidence
from .case_assets import write_json


# A package call runs the identity operation, then the requested one (each up to the 120 s a valid model may declare),
# after a 5 s connect and the process start. A package that outlasts this failed on its own, as one that exits without a
# result does: grading on a healthy device counts that against the driver (`execute_plan`).
PACKAGE_CALL_SECONDS = 300


def _device_healthy(session):
    """Whether the evaluator's own emulator still runs, answers its monitor and accepts a connection on the channel the
    driver uses. Firmware that stops answering on a working channel after the driver's call is the driver's doing."""
    import socket
    process = getattr(session, 'process', None)
    if process is not None and process.poll() is not None:
        return False
    try:
        session.observe()
        binding = session.binding
        socket.create_connection((binding['host'], binding['port']), timeout=2).close()
    except Exception:
        return False
    return True


def run_call(session, invoke, request):
    """One evaluator call inside the device's window: the emulator runs only for the call, paused between calls."""
    session.set_running(True)
    try:
        return invoke(request['operation'], request['parameters'])
    finally:
        session.set_running(False)


def _binding_failure(task, error):
    return {'ok': False, 'error': {'fault': 'model', 'code': 'capability_mapping',
                                 'task': task, 'message': str(error)},
            'outputs': {}, 'units': {}, 'transcript': [], 'transport_open_attempted': False}


def execute_plan(session, invoke, plan, contract, evidence_path, observations, settle_seconds=0, *,
                 healthy_device_failures=False):
    """Invoke canonical tasks; persist raw responses and IDs before returning records.

    invoke(task, inputs, grants) returns canonical outputs and measured runtime units.
    observations(raw, result, inputs) returns check suffix -> value. Monitor units
    are supplied by that measurement function's optional ``monitor_units`` map.
    With ``healthy_device_failures`` (grading a candidate's frozen package, never calibration), a call reported as a host
    fault on a device that is still healthy is graded as the driver's failure, unless its package process could not
    start or was killed from outside.
    """
    # Validate evaluator-owned witnesses before any device access. Candidate receive
    # completion and reject prefixes are diagnostic claims, not protocol truth.
    for check in contract['checks']:
        _check_rejection_evidence(check)
    checks = {row['id']: row for row in contract['checks']}
    records, raw_events = [], []
    result, inputs, task = {}, {}, None
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
                task = action['task']
                def operation(task, values):
                    if needs_settle:
                        __import__('time').sleep(settle_seconds)
                    return invoke(task, values, action['grants'])
                result = run_call(session, operation,
                                  {'operation': action['task'], 'parameters': inputs})
                needs_settle = False
                event['result'] = result
                error = result.get('error', {})
                if error.get('fault') == 'host':
                    # The device was verified healthy before the episode and only this call touched it. If it still runs,
                    # answers its monitor and accepts a connection, the call failed on a healthy device: the driver's
                    # failure, graded. So is a frozen package that hung or exited without a result, since its bundled
                    # runtime is the system's code. A package process that could not start, or was killed by a signal the
                    # grader did not send, is the host's, and so is any host fault while calibrating, where an outage must
                    # never pass for a mutant's rejection.
                    if healthy_device_failures and error.get('source') != 'package-process':
                        event['device_healthy'] = _device_healthy(session)
                    if not event.get('device_healthy'):
                        raw_events.append(event)
                        raise RuntimeError('Host execution failed; behavioral grading is unavailable')
                    result = event['result'] = {**result, 'error': {**error, 'fault': 'model', 'reported_fault': 'host',
                                                                     'classified': 'healthy-device'}}
            elif kind == 'observe':
                raw = session.observe()
                event['raw'] = raw
                measured = observations(raw, result, inputs)
                monitor_units = getattr(observations, 'monitor_units', {})
                for identifier in action['checks']:
                    check = checks[identifier]
                    suffix = identifier.rsplit('/', 1)[-1]
                    units = (monitor_units if check['channel'] == 'independent-monitor' else
                             {**getattr(observations, 'runtime_units', {}), **result.get('units', {})})
                    if suffix == 'refused_without_io':
                        units = {'refused_without_io': 'boolean'}
                    if suffix == 'operation_rejected' and check['channel'] == 'runtime-transcript':
                        units = {'operation_rejected': 'boolean'}
                        value = (result.get('ok') is False and result.get('identity_verified') is True
                            and result.get('error', {}).get('fault') == 'model'
                            and result.get('error', {}).get('code') == 'execution_error'
                            and bool(result.get('transcript'))
                            and result['transcript'][-1].get('operation') == result.get('operation')
                            and result['transcript'][-1].get('status') == 'completed'
                            and result['transcript'][-1].get('op') in ('exchange', 'usb_control')
                            and result['transcript'][-1].get('rx_hex') == check['rejection_evidence']['rx_hex'])
                    elif suffix == 'operation_ok' and check['channel'] == 'runtime-transcript':
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
                    if result.get('ok') is False and check['channel'] == 'runtime-transcript':
                        record['invocation_failed'] = True
                        if error.get('fault') in ('model', 'operator'):
                            grant = (error['fault'] == 'operator'
                                     and error.get('message') == 'operation requires explicit effect grant')
                            record['invocation_error'] = {'fault': error['fault'], 'task': task,
                                'code': 'effect_grant_required' if grant else 'operation_failed'}
                    records.append(record)
            else:
                raise ValueError('Unknown evaluator action')
            raw_events.append(event)
            write_json(evidence_path, {'schema': 'benchmark-action-evidence/1', 'events': raw_events, 'records': records})
    except Exception:
        write_json(evidence_path, {'schema': 'benchmark-action-evidence/1', 'events': raw_events, 'records': records,
                                   'incomplete': True})
        raise
    return records


def package_invoker(package, binding, grants):
    """Normal installed package CLI, isolated from the source checkout. A call that ends without a result is reported as
    a host fault, never raised: from `package` when the package outlasted the call limit or exited without a result
    (`execute_plan` may grade that as the driver's), from `package-process` when the process could not start or a signal
    the grader did not send killed it."""
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
        failed = lambda source, code, message: {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [],
                                                'error': {'fault': 'host', 'source': source, 'code': code,
                                                          'message': message}}
        try:
            # Output that does not decode is the package's own too: no result, never an exception.
            completed = subprocess.run(command, cwd=Path(package).parent, capture_output=True, text=True,
                                       errors='replace', timeout=PACKAGE_CALL_SECONDS)
        except subprocess.TimeoutExpired:
            return failed('package', 'timeout', f'The package call did not finish within {PACKAGE_CALL_SECONDS} s')
        except OSError as error:
            return failed('package-process', 'spawn', 'The package process could not start: ' + type(error).__name__
                          + ': ' + str(error))
        try:
            result = json.loads(completed.stdout)
        except (ValueError, RecursionError):
            result = None
        if isinstance(result, dict):
            return result
        if completed.returncode < 0:
            # The grader's own kill, at the call limit, ends in TimeoutExpired above.
            return failed('package-process', 'signal',
                          'The package process was killed by signal %d' % -completed.returncode)
        return failed('package', 'no_result', 'The package returned no runtime result (exit %d)' % completed.returncode)
    return invoke


def canonical_package_invoker(package, capabilities, binding):
    import json
    from pathlib import Path
    from .behavior import bind_task
    model = json.loads((Path(package)/'driver/model.json').read_bytes())
    def invoke(task, inputs, grants):
        try:
            request = bind_task(capabilities, task, inputs, model)
        except ValueError as error:
            return _binding_failure(task, error)
        result = package_invoker(package, binding, grants)(**request)
        mappings = capabilities['tasks'][task]['outputs']
        return {**result,
            'outputs': {key: result.get('outputs', {}).get(mapping['output']) for key, mapping in mappings.items()},
            'units': {key: result.get('units', {}).get(mapping['output']) for key, mapping in mappings.items()}}
    return invoke

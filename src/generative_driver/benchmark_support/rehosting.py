"""Same public qualification episodes across firmware execution adapters.

Retained traffic alone earns no admission. These observations qualify only the
declared requests; authenticated reference/mutant calibration still gates agents.
"""
import json
import socket
import time
from pathlib import Path

from .snapshots import canonical_digest


def load_plan():
    path = Path(__file__).resolve().parents[1]/'resources/bench/qualification/zephyr-modbus-rtu.json'
    return json.loads(path.read_text(encoding='utf-8'))


def exercise_execution(case_id, password_file, output, *, renode=None):
    """Evaluator-owned scripted reference exercise; no model or configurator is launched."""
    from ..benchmark import case_root
    from .registry import resolve_case
    from .execution import artifact_name
    from .emulator import truth_for_case
    from .device_provider import DeviceProvider
    from .authoring import evaluator_directory
    plan = load_plan()
    case = resolve_case(case_id)
    if case.id != plan['case']:
        raise ValueError('No execution qualification plan for this case')
    truth = truth_for_case(case_root(), case.manifest, {'evaluator_password_file': password_file})
    method = case.manifest.get('execution_setup', {}).get('method', 'renode')
    if (truth['images'] != case.manifest['images'] or truth['source']['commit'] != plan['source_commit']
            or truth['recipe'].get('backend', 'renode') != method):
        raise ValueError('Authenticated execution setup differs from the registered case')
    output = evaluator_directory(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Execution exercise requires an empty output directory')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact = artifact_name(case.manifest)
    provider = DeviceProvider(case.id, case.root/artifact, truth['recipe'], renode, output/'device',
                              image_sha256=case.manifest['images'][artifact])
    record = {'schema': 'firmware-execution-observation/1', 'case': case.id,
        'execution': 'firmware-reference-exercise', 'model_benchmark': False, 'method': method,
        'source_commit': plan['source_commit'], 'plan_sha256': canonical_digest(plan),
        'manifest_sha256': case.manifest_sha256, 'image_sha256': case.manifest['images'][artifact],
        'complete': False, 'records': []}
    path = output/'observations.json'
    def save():
        path.write_text(json.dumps(record, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    try:
        provider.start()
        record['process'] = dict(provider.session.info)
        save()
        for step in plan['steps']:
            reply = None
            if step['kind'] == 'reset':
                provider.session.reset({})
                with provider.window():
                    time.sleep(.15)
            else:
                with provider.window(), socket.create_connection(
                        (provider.binding['host'], provider.binding['port']), timeout=2) as peer:
                    # Every request is dispatched exactly once, including writes and malformed frames.
                    peer.sendall(bytes.fromhex(step['request_hex']))
                    peer.settimeout(.3 if not step['reply_hex'] else 2)
                    response = bytearray()
                    while len(response) <= 256:
                        try:
                            block = peer.recv(257-len(response))
                        except socket.timeout:
                            break
                        if not block:
                            raise RuntimeError('Host fault: target disconnected during qualification')
                        response.extend(block)
                        peer.settimeout(.05)
                    reply = response.hex()
            raw = provider.session.observe()
            from .instruments import public_observation
            record['records'].append({'id': step['id'], 'request_hex': step.get('request_hex'),
                'reply_hex': reply, 'state': public_observation(case_id, raw)['values'],
                'raw_observation': raw, 'time': time.time()})
            save()
        record['complete'] = True
    except (OSError, ValueError, RuntimeError) as error:
        record['failure'] = type(error).__name__ + ': ' + str(error)
    finally:
        provider.stop()
        record['stop_confirmed'] = not provider.status()['healthy']
        save()
    record['passed'] = bool(record['complete'] and all(
        row['reply_hex'] == step.get('reply_hex') and row['state'] == step['state']
        for step, row in zip(plan['steps'], record['records'])))
    save()
    return {'ok': record['passed'], 'execution': record['execution'], 'model_benchmark': False,
            'method': method, 'steps': len(record['records']), 'record': str(path),
            **({'failure': record['failure']} if 'failure' in record else {})}


def compare_executions(native, reference):
    plan = load_plan()
    checks = []
    def check(name, passed):
        checks.append({'id': name, 'passed': passed is True})
    for method, record in (('native-sim', native), ('renode', reference)):
        check(method+'/identity', isinstance(record, dict)
              and record.get('schema') == 'firmware-execution-observation/1'
              and record.get('case') == plan['case'] and record.get('method') == method
              and record.get('plan_sha256') == canonical_digest(plan))
        check(method+'/completed-and-stopped', isinstance(record, dict)
              and record.get('complete') is True and record.get('stop_confirmed') is True
              and 'failure' not in record)
        process = record.get('process', {}) if isinstance(record, dict) else {}
        image = record.get('image_sha256') if isinstance(record, dict) else None
        check(method+'/executed-firmware', isinstance(record, dict)
              and record.get('source_commit') == plan['source_commit']
              and isinstance(image, str) and len(image) == 64 and all(c in '0123456789abcdef' for c in image)
              and isinstance(process, dict) and process.get('image_sha256') == image
              and type(process.get('pid')) is int and process['pid'] > 0
              and isinstance(process.get('executable'), str) and bool(process['executable']))
        rows = record.get('records', []) if isinstance(record, dict) else []
        check(method+'/complete-inventory', isinstance(rows, list)
              and [r.get('id') for r in rows if isinstance(r, dict)] == [s['id'] for s in plan['steps']])
        by_id = {r.get('id'): r for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}
        for step in plan['steps']:
            row = by_id.get(step['id'], {})
            prefix = method + '/' + step['id']
            check(prefix+'/request', row.get('request_hex') == step.get('request_hex'))
            check(prefix+'/reply', row.get('reply_hex') == step.get('reply_hex'))
            state = row.get('state', {})
            check(prefix+'/state-inventory', isinstance(state, dict) and set(state) == set(step['state']))
            for name, expected in step['state'].items():
                check(prefix+'/state/'+name, isinstance(state, dict) and type(state.get(name)) is int
                      and state[name] == expected)
    failed = [c['id'] for c in checks if not c['passed']]
    scripted = any(r.get('execution') != 'firmware-reference-exercise' for r in (native, reference) if isinstance(r, dict))
    return {'schema': 'firmware-rehosting-comparison/1', 'case': plan['case'],
            'execution': 'scripted-test' if scripted else 'firmware-rehosting-validation', 'model_benchmark': False,
            'verdict': 'failed' if failed else 'passed', 'checks': checks, 'failed_checks': failed,
            'plan_sha256': canonical_digest(plan),
            'image_hashes': {name: r.get('image_sha256') for name, r in (('native-sim', native), ('renode', reference))
                             if isinstance(r, dict)},
            'limitations': ['Only the declared request sequences and observed state were checked.',
                            'No physical UART/RS-485 timing or electrical behavior was measured.',
                            'Reference/mutant admission and model performance are separate checks.']}

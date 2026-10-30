"""Same public qualification episodes across firmware execution adapters.

Retained traffic alone earns no admission. These observations qualify only the
declared requests; authenticated reference/mutant calibration still gates agents.
"""
import json
import socket
import time

from .snapshots import canonical_digest
# The evaluator admits a native-sim case by this comparison, so it is defined with its execution setup and imported here.
from .execution import compare_executions, load_plan


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

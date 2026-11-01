"""Execution adapters used by the provider and evaluator, with one lifecycle Interface."""
import json
from pathlib import Path

from .snapshots import canonical_digest


def artifact_name(manifest):
    """One selected executable, shared by acquisition, interpretation, provider and evaluator."""
    setup = manifest.get('execution_setup')
    if setup is None:
        return 'firmware.bin'
    required = {'method', 'artifact', 'substitutions', 'validated_behavior', 'hardware_gaps'}
    if not isinstance(setup, dict) or set(setup) != required or setup['method'] not in ('renode', 'native-sim'):
        raise ValueError('Invalid case execution setup')
    name = setup['artifact']
    if (not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name
            or setup['method'] == 'native-sim' and not name.endswith('.elf')):
        raise ValueError('Invalid executed firmware artifact')
    for key in ('substitutions', 'validated_behavior', 'hardware_gaps'):
        if not isinstance(setup[key], list) or any(not isinstance(v, str) or not v.strip() for v in setup[key]):
            raise ValueError('Invalid execution setup ' + key)
    return name


def start_session(*, recipe, **arguments):
    backend = recipe.get('backend', 'renode')
    if backend == 'native-sim':
        from .native_firmware import NativeFirmwareSession
        return NativeFirmwareSession.start(recipe=recipe, **arguments)
    if backend != 'renode':
        raise ValueError('Unknown firmware execution method: ' + str(backend))
    from .native import NativeSession
    return NativeSession.start(recipe=recipe, **arguments)


def validate_setup(manifest, truth):
    """Bind the public method to its authenticated Adapter and native comparison evidence."""
    artifact = artifact_name(manifest)
    method = manifest.get('execution_setup', {}).get('method', 'renode')
    if truth.get('recipe', {}).get('backend', 'renode') != method:
        raise ValueError('Authenticated recipe backend differs from the registered execution setup')
    if method != 'native-sim':
        return
    proof = truth.get('build', {}).get('execution_validation', {})
    if not isinstance(proof, dict) or not {'native', 'renode'} <= proof.keys():
        raise ValueError('Native admission requires measured native/Renode comparison evidence')
    comparison = compare_executions(proof['native'], proof['renode'])
    if (comparison['verdict'] != 'passed' or comparison['execution'] != 'firmware-rehosting-validation'
            or comparison['image_hashes'].get('native-sim') != manifest['images'][artifact]
            or comparison['image_hashes'].get('renode') != proof.get('reference_image_sha256')
            or manifest['execution_setup']['validated_behavior'] != [r['id'] for r in proof['native']['records']]):
        raise ValueError('Native execution comparison evidence is incomplete, stale or names different images')


def load_plan():
    path = Path(__file__).resolve().parents[1]/'resources/bench/qualification/zephyr-modbus-rtu.json'
    return json.loads(path.read_text(encoding='utf-8'))


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

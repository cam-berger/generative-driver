"""Compare a firmware's native-simulator and Renode executions on the same public qualification plan. Only the native
execution of the upstream Modbus case needs this: `zephyr_modbus` admits the case by it, and the rehosting and build
tools measure with it, so it lies outside the grading core (Feature 01, family-seam)."""
import json
from pathlib import Path

from .snapshots import canonical_digest


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

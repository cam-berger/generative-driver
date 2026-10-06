"""Bound canonical task inputs and grade evaluator-owned observations."""
from .capabilities import numeric_value, affine_value, bind_task, validate_capabilities


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _check_contract(contract):
    if type(contract) is not dict or contract.get('schema') != 'benchmark-behavior/1' or not _sha(contract.get('artifact_sha256')):
        raise ValueError('Invalid behavior contract')
    checks = contract.get('checks')
    if type(checks) is not list or not checks:
        raise ValueError('Behavior contract requires checks')
    seen = set()
    for check in checks:
        if type(check) is not dict or type(check.get('id')) is not str or not check['id'] or check['id'] in seen:
            raise ValueError('Invalid or duplicate check ID')
        seen.add(check['id'])
        if type(check.get('revision')) is not int or check['revision'] < 0 or check.get('kind') not in ('number', 'boolean', 'text'):
            raise ValueError('Invalid check definition')
        if type(check.get('unit')) is not str or check.get('channel') not in ('independent-monitor', 'runtime-transcript'):
            raise ValueError('Invalid check unit or channel')
        kind = check['kind']
        value = check.get('expected')
        if kind == 'number':
            try:
                numeric_value(value)
                tolerance = numeric_value(check['absolute_tolerance'])
            except (KeyError, ValueError) as exc:
                raise ValueError('Invalid numeric check') from exc
            if tolerance < 0:
                raise ValueError('Negative tolerance')
        elif kind == 'boolean':
            if type(value) is not bool:
                raise ValueError('Invalid boolean check')
        elif type(value) is not str:
            raise ValueError('Invalid text check')


def _record_reason(check, record, artifact):
    if record is None:
        return 'missing evidence'
    if record.get('revision') != check['revision'] or type(record.get('revision')) is not int:
        return 'revision mismatch'
    if record.get('artifact_sha256') != artifact:
        return 'artifact mismatch'
    if record.get('unit') != check['unit']:
        return 'unit mismatch'
    if record.get('channel') != check['channel']:
        return 'channel mismatch'
    value = record.get('value')
    if check['kind'] == 'number':
        try:
            numeric_value(value)
        except ValueError:
            return 'invalid observation'
        lower = check['expected'] - check['absolute_tolerance']
        upper = check['expected'] + check['absolute_tolerance']
        if value < lower or value > upper:
            return 'value mismatch'
    elif check['kind'] == 'boolean':
        if type(value) is not bool:
            return 'invalid observation'
        if value != check['expected']:
            return 'value mismatch'
    else:
        if type(value) is not str:
            return 'invalid observation'
        if value != check['expected']:
            return 'value mismatch'
    return None


def validate_records(contract: dict, records: list[dict]) -> dict:
    _check_contract(contract)
    if type(records) is not list:
        records = []
    by_id = {}
    extras = []
    for record in records:
        if type(record) is not dict or type(record.get('id')) is not str:
            extras.append({'id': None, 'passed': False, 'reason': 'invalid evidence'})
            continue
        by_id.setdefault(record['id'], []).append(record)
    results = []
    known = {check['id'] for check in contract['checks']}
    for check in contract['checks']:
        found = by_id.get(check['id'], [])
        reason = 'duplicate evidence' if len(found) > 1 else _record_reason(check, found[0] if found else None, contract['artifact_sha256'])
        result = {'id': check['id'], 'passed': reason is None}
        if 'scenario' in check:
            result['scenario'] = check['scenario']
        if reason:
            result['reason'] = reason
        results.append(result)
    for identifier, found in by_id.items():
        if identifier not in known:
            extras.extend({'id': identifier, 'passed': False, 'reason': 'unexpected evidence'} for _ in found)
    results.extend(extras)
    passed = sum(row['passed'] for row in results)
    return {'verdict': 'passed' if passed == len(results) else 'failed',
            'passed': passed, 'total': len(results), 'checks': results}

def project_feedback(records: list[dict], grade=None) -> dict:
    """Expose diagnostics without evaluator-owned expectations or evidence paths."""
    allowed = {'missing evidence', 'duplicate evidence', 'unexpected evidence',
               'invalid evidence', 'revision mismatch', 'artifact mismatch',
               'unit mismatch', 'channel mismatch', 'invalid observation', 'value mismatch'}
    projected = []
    capability_errors = []
    invocation_errors = []
    checks = {row['id']: row for row in (grade or {}).get('checks', [])}
    for record in records:
        if type(record) is not dict:
            continue
        invocation = record.get('invocation_error')
        if (type(invocation) is dict and invocation.get('fault') in ('model', 'operator')
                and invocation.get('code') in ('effect_grant_required', 'operation_failed')
                and type(invocation.get('task')) is str):
            safe = {key: invocation[key] for key in ('fault', 'code')}
            safe['task'] = invocation['task'][:64]
            if safe not in invocation_errors:
                invocation_errors.append(safe)
        error = record.get('capability_error')
        if (type(error) is dict and error.get('fault') == 'model' and error.get('code') == 'capability_mapping'
                and type(error.get('task')) is str and type(error.get('message')) is str):
            safe = {'fault': 'model', 'code': 'capability_mapping',
                    'task': error['task'][:64], 'message': error['message'][:512]}
            if safe not in capability_errors:
                capability_errors.append(safe)
        row = {}
        if record.get('invocation_failed') is True:
            row['invocation_failed'] = True
        for field in ('id', 'task_id', 'value', 'unit'):
            if field in record and type(record[field]) in (str, int, float, bool):
                row[field] = record[field]
        if record.get('reason') in allowed:
            row['reason'] = record['reason']
        if record.get('channel') in ('runtime-transcript', 'independent-monitor'):
            row['channel'] = record['channel']
        check = checks.get(record.get('id'))
        if check and type(check.get('passed')) is bool:
            row['passed'] = check['passed']
            if check.get('reason') in allowed:
                row['reason'] = check['reason']
        projected.append(row)
    return {'records': projected,
            **({'capability_errors': capability_errors} if capability_errors else {}),
            **({'invocation_errors': invocation_errors} if invocation_errors else {})}

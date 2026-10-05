"""Bound canonical task inputs and grade evaluator-owned observations."""
import math


def numeric_value(value):
    if type(value) not in (int, float):
        raise ValueError('Expected a finite numeric value')
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ValueError('Expected a finite numeric value') from exc
    if not finite:
        raise ValueError('Expected a finite numeric value')
    return value


def affine_value(value, scale, offset, parameter):
    result = numeric_value(value) * numeric_value(scale) + numeric_value(offset)
    numeric_value(result)
    if parameter['type'] == 'integer':
        if result != int(result):
            raise ValueError('Integer parameter requires exact conversion')
        result = int(result)
    if not parameter['minimum'] <= result <= parameter['maximum']:
        raise ValueError('Canonical input exceeds candidate parameter bounds')
    return result


def _parameter_value(value, parameter):
    kind = parameter.get('type')
    if kind == 'string':
        if type(value) is not str or value not in parameter['enum']:
            raise ValueError('Canonical input violates candidate enum')
        return value
    if kind in ('integer', 'number'):
        numeric_value(value)
        if kind == 'integer' and type(value) is not int:
            raise ValueError('Integer parameter requires an integer')
        if not parameter['minimum'] <= value <= parameter['maximum']:
            raise ValueError('Canonical input exceeds candidate parameter bounds')
        return value
    raise ValueError('Unsupported candidate parameter')


def bind_task(capabilities: dict, task: str, inputs: dict, model: dict) -> dict:
    try:
        if capabilities['schema'] != 'benchmark-capabilities/2' or type(inputs) is not dict:
            raise ValueError('Invalid capability schema or input')
        spec = capabilities['tasks'][task]
        operation = spec['operation']
        operation_spec = model['operations'][operation]
        parameters = operation_spec['parameters']
        if type(spec['constants']) is not dict or type(spec['inputs']) is not dict or type(spec['outputs']) is not dict:
            raise ValueError('Invalid capability mapping')
        if inputs.keys() != spec['inputs'].keys():
            raise ValueError('Canonical inputs must match exactly')
        destinations = list(spec['constants']) + [mapping['parameter'] for mapping in spec['inputs'].values()]
        if len(destinations) != len(set(destinations)) or set(destinations) != set(parameters):
            raise ValueError('Destination parameters must match exactly')
        bound = {name: _parameter_value(value, parameters[name]) for name, value in spec['constants'].items()}
        for name, mapping in spec['inputs'].items():
            target = mapping['parameter']
            if mapping.get('kind') == 'copy':
                value = _parameter_value(inputs[name], parameters[target])
            elif mapping.get('kind') in (None, 'affine'):
                if parameters[target]['type'] not in ('integer', 'number'):
                    raise ValueError('Affine mapping requires numeric parameter')
                value = affine_value(inputs[name], mapping['scale'], mapping['offset'], parameters[target])
            else:
                raise ValueError('Unsupported input mapping')
            bound[target] = value
        for mapping in spec['outputs'].values():
            output = operation_spec['outputs'][mapping['output']]
            if mapping['unit'] != output.get('unit'):
                raise ValueError('Output unit differs from candidate model')
        return {'operation': operation, 'parameters': bound}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Invalid capability mapping or candidate model') from exc

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
    checks = {row['id']: row for row in (grade or {}).get('checks', [])}
    for record in records:
        if type(record) is not dict:
            continue
        row = {}
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
    return {'records': projected}

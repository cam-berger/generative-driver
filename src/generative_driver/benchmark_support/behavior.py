"""Bound canonical task inputs and grade evaluator-owned observations."""
from .capabilities import numeric_value, affine_value, bind_task, validate_capabilities


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _check_rejection_evidence(check):
    """Validate a private complete-response witness, never a candidate receive rule."""
    if check['id'].rsplit('/', 1)[-1] == 'operation_rejected':
        witness = check.get('rejection_evidence')
        if (check['kind'] != 'boolean' or check['channel'] != 'runtime-transcript'
                or type(witness) is not dict
                or set(witness) != {'schema', 'rx_hex'}
                or witness.get('schema') != 'benchmark-rejection/1'
                or type(witness.get('rx_hex')) is not str or not witness['rx_hex']
                or len(witness['rx_hex']) % 2
                or any(c not in '0123456789abcdef' for c in witness['rx_hex'])):
            raise ValueError('Invalid or missing evaluator rejection evidence')


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
        # The evidence of a check: an instrument reading, the driver's runtime result, or the evaluator's own read of a
        # generated device's state in memory.
        if (type(check.get('unit')) is not str
                or check.get('channel') not in ('independent-monitor', 'runtime-transcript', 'device-state')):
            raise ValueError('Invalid check unit or channel')
        _check_rejection_evidence(check)
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


def _undecided(record):
    """The reason the evaluator left a check undecided (its device state could not be read, or its sequence did not start
    from a verified reset), from the record it wrote for it; None for a record that carries no `undecided`. The reason is
    a non-empty string of at most 300 characters, the limit of an equivalence/1 text field, and is published in the grade's
    difference, so it names no expected value. Anything else under the key, null included, only the evaluator can have
    written: ValueError, as an invalid contract is, so that its defect is a host fault and never the driver's failure."""
    if 'undecided' not in record:
        return None
    reason = record['undecided']
    if type(reason) is not str or not 0 < len(reason) <= 300:
        raise ValueError('Invalid evaluator undecided reason')
    return reason


def _record_reason(check, record, artifact):
    """Why a check fails against its record, or None when it passes or the record leaves it undecided (`_undecided`). An
    undecided record is held to the identity of the check like any other, but its value is never judged."""
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
    if 'undecided' in record:
        return None
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
    """Grade the evaluator's records against its contract. A check passes (True), fails (False) or, when its record says
    the evaluator could not decide it, is undecided (None, with the record's reason). The verdict is `failed` when any
    check failed, else `unknown` when any is undecided, else `passed`; `passed` counts the checks that passed. A record
    whose `undecided` is no reason raises ValueError, whichever check it is for."""
    _check_contract(contract)
    if type(records) is not list:
        records = []
    by_id = {}
    extras = []
    for record in records:
        if type(record) is not dict or type(record.get('id')) is not str:
            extras.append({'id': None, 'passed': False, 'reason': 'invalid evidence'})
            continue
        _undecided(record)
        by_id.setdefault(record['id'], []).append(record)
    results = []
    known = {check['id'] for check in contract['checks']}
    for check in contract['checks']:
        found = by_id.get(check['id'], [])
        reason = 'duplicate evidence' if len(found) > 1 else _record_reason(check, found[0] if found else None, contract['artifact_sha256'])
        undecided = _undecided(found[0]) if found and reason is None else None
        result = {'id': check['id'], 'passed': None if undecided else reason is None}
        if 'scenario' in check:
            result['scenario'] = check['scenario']
        if reason or undecided:
            result['reason'] = reason or undecided
        results.append(result)
    for identifier, found in by_id.items():
        if identifier not in known:
            extras.extend({'id': identifier, 'passed': False, 'reason': 'unexpected evidence'} for _ in found)
    results.extend(extras)
    passed = sum(row['passed'] is True for row in results)
    failed = any(row['passed'] is False for row in results)
    return {'verdict': 'failed' if failed else 'passed' if passed == len(results) else 'unknown',
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

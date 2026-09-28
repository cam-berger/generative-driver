"""Provider counter accounting. Tokens measure reported volume, not FLOPs or cost.

Input includes cached input; output includes reasoning. Sources whose counters
exclude those subsets must normalize them before using this API. Cumulative
streams must start at zero in a dedicated session/stage; never import a shared
session's lifetime total and label it as a single stage.
"""
import hashlib
import json
import os
import re

from . import core

COUNTERS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens')
_TOKEN_KEYS = (*COUNTERS, 'uncached_input_tokens')
_LABEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$')


def _label(value, name, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError(name + ' must be a bounded identifier')
    return value


def normalize_record(stage, usage, source, model=None, attempt=None, record_id=None):
    """Validate and normalize only explicit counters and identity metadata."""
    if not isinstance(usage, dict) or set(usage) - set(COUNTERS) - {
            'mode', 'session_id', 'inference_id', 'status', 'reason', 'last_tokens'}:
        raise ValueError('usage contains unsupported fields')
    mode = usage.get('mode')
    if mode not in ('delta', 'cumulative', 'unavailable'):
        raise ValueError('usage.mode must be delta, cumulative, or unavailable')
    counters = {key: usage.get(key) for key in COUNTERS}
    for value in counters.values():
        if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
            raise ValueError('token counters must be nonnegative bounded integers or null')
    if mode == 'unavailable' and any(value is not None for value in counters.values()):
        raise ValueError('unavailable usage cannot contain counters')
    if mode != 'unavailable' and not any(value is not None for value in counters.values()):
        raise ValueError('measured usage requires at least one counter')
    for subset, whole in (('cached_input_tokens', 'input_tokens'), ('reasoning_tokens', 'output_tokens')):
        if counters[subset] is not None and counters[whole] is not None and counters[subset] > counters[whole]:
            raise ValueError('token subset exceeds its containing counter')
    if counters['input_tokens'] is not None and counters['output_tokens'] is not None:
        total = counters['input_tokens'] + counters['output_tokens']
        if counters['total_tokens'] not in (None, total):
            raise ValueError('total_tokens must equal input_tokens plus output_tokens')
        counters['total_tokens'] = total
    if counters['total_tokens'] is not None:
        if any(value is not None and value > counters['total_tokens'] for value in counters.values()):
            raise ValueError('token counter exceeds total_tokens')
    status = usage.get('status', 'unknown')
    if status not in ('completed', 'failed', 'unknown'):
        raise ValueError('usage.status must be completed, failed, or unknown')
    record = {'schema': 'inference-usage/1', 'stage': _label('unattributed' if stage is None else stage, 'stage'),
              'source': _label(source, 'source'), 'model': _label(model, 'model', True),
              'attempt': _label(str(attempt) if type(attempt) is int else attempt, 'attempt', True),
              'session_id': _label(usage.get('session_id'), 'session_id', mode == 'unavailable'),
              'inference_id': _label(usage.get('inference_id'), 'inference_id', True),
              'mode': mode, 'status': status, 'reason': _label(usage.get('reason'), 'reason', True),
              'tokens': counters}
    if mode == 'delta' and record_id is None and record['inference_id'] is None:
        raise ValueError('delta usage needs record_id or inference_id for idempotency')
    if usage.get('last_tokens') is not None:
        last = usage['last_tokens']
        if not isinstance(last, dict) or set(last) - set(COUNTERS):
            raise ValueError('last_tokens must contain only token counters')
        record['last_tokens'] = normalize_record(stage, {**last, 'mode': 'delta',
            'session_id': record['session_id']}, source, model, attempt, 'last')['tokens']
    fallback_id = record['inference_id'] if mode == 'delta' else None
    record['record_id'] = _label(record_id if record_id is not None else fallback_id, 'record_id', True)
    if record['record_id'] is None:
        record['record_id'] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return record


def _stream(record):
    return tuple(record.get(key) for key in ('source', 'session_id', 'model', 'attempt'))


def validate_append(records, record):
    """Validate a normalized stream append; return False for an exact duplicate."""
    stream = [old for old in records if _stream(old) == _stream(record)]
    for old in stream:
        if old['record_id'] == record['record_id']:
            if old != record:
                raise ValueError('record identity already exists with different data')
            return False
    measured = [old for old in stream if old['mode'] != 'unavailable']
    if measured and record['mode'] != 'unavailable':
        if any(old['mode'] != record['mode'] for old in measured):
            raise ValueError('cannot mix delta and cumulative accounting in one stream')
        if record['mode'] == 'cumulative':
            if any(old['stage'] != record['stage'] for old in measured):
                raise ValueError('a cumulative stream must keep one host-assigned stage')
            known = {key: max((old['tokens'][key] for old in measured if old['tokens'][key] is not None),
                             default=None) for key in COUNTERS}
            for key, value in record['tokens'].items():
                if value is None and known[key] is not None:
                    raise ValueError('cumulative snapshots cannot drop previously reported counters')
                if value is not None and known[key] is not None and value < known[key]:
                    raise ValueError('cumulative counters decreased; use a new session or attempt')
            combined = {key: record['tokens'][key] if record['tokens'][key] is not None else known[key]
                        for key in COUNTERS}
            for subset, whole in (('cached_input_tokens', 'input_tokens'), ('reasoning_tokens', 'output_tokens')):
                if combined[subset] is not None and combined[whole] is not None and combined[subset] > combined[whole]:
                    raise ValueError('cumulative subset exceeds its containing counter')
    return True


def _read(stream):
    stream.seek(0)
    return [json.loads(line) for line in stream if line.strip()]


@core.tool('usage_record')
def usage_record(run_dir, stage, usage, source, model=None, attempt=None, record_id=None, **_):
    """Append an idempotent, explicitly normalized provider usage observation."""
    record = normalize_record(stage, usage, source, model, attempt, record_id)
    run = core.resolve_run(run_dir)
    os.makedirs(run, exist_ok=True)
    path = os.path.join(run, 'usage.jsonl')
    with core.file_lock(path), open(path, 'a+', encoding='utf-8') as stream:
        records = _read(stream)
        appended = validate_append(records, record)
        if appended:
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + '\n')
            stream.flush()
    return {'record': record, 'duplicate': not appended}


def summarize_records(records):
    """Summarize normalized observations; cumulative snapshots form one unit."""
    units = {}
    for record in records:
        identity = (_stream(record), record['mode'],
                    None if record['mode'] == 'cumulative' else record['record_id'])
        if identity not in units:
            units[identity] = {**record, 'tokens': dict(record['tokens'])}
        elif record['mode'] == 'cumulative':
            unit = units[identity]
            for key, value in record['tokens'].items():
                if value is not None:
                    unit['tokens'][key] = max(unit['tokens'][key] or 0, value)
            if record['status'] == 'failed':
                unit['status'] = 'failed'
    units = list(units.values())
    for unit in units:
        tokens = unit['tokens']
        tokens['uncached_input_tokens'] = (tokens['input_tokens'] - tokens['cached_input_tokens']
            if tokens['input_tokens'] is not None and tokens['cached_input_tokens'] is not None else None)

    def aggregate(items):
        tokens, available = {}, {}
        for key in _TOKEN_KEYS:
            values = [item['tokens'][key] for item in items if item['tokens'][key] is not None]
            tokens[key] = sum(values) if values else None
            available[key] = len(values)
        return {'tokens': tokens, 'coverage': {
            'accounting_units': len(items), 'available_units_by_counter': available,
            'unavailable_units': sum(item['mode'] == 'unavailable' for item in items),
            'failed_units': sum(item['status'] == 'failed' for item in items),
            'all_recorded_units_have_totals': bool(items) and available['total_tokens'] == len(items)}}

    result = aggregate(units)
    total = result['tokens']['total_tokens']
    result['schema'] = 'inference-usage-summary/1'
    result['observations'] = len(records)
    result['stages'] = {}
    for stage in sorted({unit['stage'] for unit in units}):
        stage_summary = aggregate([unit for unit in units if unit['stage'] == stage])
        count = stage_summary['tokens']['total_tokens']
        stage_summary['measured_token_share'] = count / total if total and count is not None else None
        result['stages'][stage] = stage_summary
    result['coverage']['whole_run_coverage'] = 'unknown'
    result['notes'] = [
        'Shares divide measured stage totals by measured total tokens, not whole-run compute, FLOPs, or dollars.',
        'Input includes cached input; output includes reasoning. Subsets are never added to total tokens.',
        'Missing counters are null. Sums include only measured units; per-counter coverage may differ.',
        'Unrecorded inference and provider retries without counters cannot be counted.']
    return result


def _tool_summary(run):
    result = {'calls': 0, 'wall_s': 0.0, 'stages': {},
              'timing_basis': 'sum_of_tool_call_wall_times_inclusive_of_nested_calls_not_run_elapsed'}
    path = core.log_path(run)
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as stream:
        for raw in stream:
            event = json.loads(raw)
            if event.get('tool') in ('usage_record', 'usage_summary'):
                continue
            stage = event.get('stage') or core.tool_stage(event.get('tool', ''))
            group = result['stages'].setdefault(stage, {'calls': 0, 'wall_s': 0.0, 'unavailable_wall_calls': 0})
            result['calls'] += 1
            group['calls'] += 1
            wall = event.get('t_wall_s')
            if wall is None:
                group['unavailable_wall_calls'] += 1
            else:
                group['wall_s'] += wall
                result['wall_s'] += wall
    return result


@core.tool('usage_summary')
def usage_summary(run_dir, **_):
    """Return measured token shares and independent inclusive tool timing."""
    run = core.resolve_run(run_dir)
    path = os.path.join(run, 'usage.jsonl')
    if os.path.exists(path):
        with core.file_lock(path), open(path, encoding='utf-8') as stream:
            records = _read(stream)
    else:
        records = []
    result = summarize_records(records)
    result['tools'] = _tool_summary(run)
    return result

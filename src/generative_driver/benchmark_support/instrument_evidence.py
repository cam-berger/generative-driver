"""Repair evidence from public task quantities and the configurator's live instrument readings.

No evaluator contracts, final vectors, source coordinates or worker discrepancy claims are read here.
"""
import hashlib
import json
import math
from pathlib import Path

from .candidate_context import task_contract
from .capabilities import bind_task


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def discrepancies(case_id, assignment, events):
    """Compare successful assigned-model calls with paired readings, through a checked capability mapping.

    The sensor's mapping identifies the promised quantity, never its expected value. A batch probe has only one
    after-reading, so only its last result can be compared. Missing or ambiguous evidence supplies no repair claim.
    """
    from ..configurator import IntegrityError, digest, _ran_assigned_model
    stage = assignment.get('stage')
    if case_id != 'sampled-sensor-v1' or stage not in ('probe', 'ground'):
        return []
    workspace = Path(assignment['workspace']).resolve()
    path = workspace/('capabilities.json' if stage == 'probe' else 'ground-evidence/capabilities.json')
    if path.is_symlink() or not path.resolve().is_relative_to(workspace) or not path.is_file():
        return []
    raw = path.read_bytes()
    mapping_sha = hashlib.sha256(raw).hexdigest()
    inputs = assignment.get('inputs') or {}
    if stage == 'ground':
        if str(path) not in inputs:
            return []
        if mapping_sha != inputs[str(path)]:
            raise IntegrityError('Assigned capability mapping differs from its recorded hash')
    try:
        mapping = json.loads(raw)
    except (ValueError, UnicodeError):
        return []
    if not isinstance(mapping, dict) or not isinstance(mapping.get('tasks'), dict):
        return []
    models = {}
    for name, expected in inputs.items():
        model = Path(name)/'model.json'
        if model.is_file():
            if digest(name) != expected:
                raise IntegrityError('Assigned model differs from its recorded hash')
            try:
                models[expected] = json.loads(model.read_bytes())
            except (ValueError, UnicodeError):
                continue  # The existing model-contract diff handles an invalid model.
    tasks = task_contract(case_id, [])['tasks']
    diff = []
    for event in events:
        result = event.get('result') or {}
        if (event.get('name') not in ('interface_execute', 'probe_run') or event.get('ok') is not True
                or event.get('connected') is not True or not _ran_assigned_model(event, assignment)
                or result.get('evidence_kind', 'live') != 'live'):
            continue
        model_sha = event['argument_sha256']['model_dir']
        model = models.get(model_sha)
        if not isinstance(model, dict):
            continue
        before, after = result.get('before_observation'), result.get('observation')
        session = event.get('device_session_id')
        if (not session or any(not isinstance(o, dict) or o.get('source') != 'instrument'
                or o.get('session_id') != session for o in (before, after))):
            continue
        first, last = before.get('monitor_sequence'), after.get('monitor_sequence')
        if (type(first) is not int or type(last) is not int or
                any(o.get('units', {}).get('monitor_sequence') != 'count' for o in (before, after))):
            continue
        samples = result.get('results') if event['name'] == 'probe_run' else [result]
        if not isinstance(samples, list) or not samples or any(not isinstance(s, dict) or s.get('ok') is not True for s in samples):
            continue
        sample = samples[-1]
        for task, contract in tasks.items():
            try:
                bound = bind_task(mapping, task, {}, model)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue  # An unsupported or malformed mapping alone is not a device contradiction.
            if bound != {'operation': event.get('operation'), 'parameters': event.get('parameters')}:
                continue
            evidence = {'source': 'instrument_comparison', 'dispatch_id': event.get('dispatch_id'),
                'task': task, 'operation': bound['operation'], 'parameters': bound['parameters'],
                'model_sha256': model_sha, 'mapping_sha256': mapping_sha, 'session_id': session,
                'sequence': {'before': first, 'after': last}, 'sample_index': len(samples)-1}
            if (contract['sampling'] == 'acquire' and first == last
                    or contract['sampling'] == 'retained' and first != last):
                diff.append({**evidence, 'quantity': 'sequence', 'reason':
                    'no new acquisition' if first == last else 'retained read acquired a new sample'})
                continue
            for quantity, required in contract['outputs'].items():
                observation = required['instrument']
                reference = after.get(observation)
                if not _number(reference) or after.get('units', {}).get(observation) != required['unit']:
                    continue
                output = mapping['tasks'][task]['outputs'].get(quantity)
                if not isinstance(output, dict):
                    continue
                name = output['output']
                value, unit = sample.get('outputs', {}).get(name), sample.get('units', {}).get(name)
                reason = ('unit mismatch' if unit != required['unit'] else
                    'invalid numeric output' if not _number(value) else
                    'value mismatch' if abs(value-reference) > required['absolute_tolerance'] else None)
                if reason:
                    diff.append({**evidence, 'quantity': quantity, 'reason': reason,
                        'candidate': {'output': name, 'value': value, 'unit': unit},
                        'instrument': {'quantity': observation, 'value': reference, 'unit': required['unit']},
                        'absolute_tolerance': required['absolute_tolerance']})
    return diff

"""Public requested behaviors shared by interpretation, probing and fresh reuse."""
import copy
import json
import re


def validate_mission(value, case_id):
    required = {'schema', 'case_id', 'tasks', 'reuse_objective'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {'operating_conditions'}
            or value['schema'] != 'benchmark-public-mission/1' or value['case_id'] != case_id
            or not isinstance(value['reuse_objective'], str) or not value['reuse_objective'].strip()):
        raise ValueError('Invalid public mission identity or objective')
    if 'operating_conditions' in value:
        conditions = value['operating_conditions']
        if (not isinstance(conditions, list) or not 1 <= len(conditions) <= 16
                or any(not isinstance(condition, str) or not condition.strip() or len(condition) > 1000
                       for condition in conditions)):
            raise ValueError('Public operating conditions require 1 through 16 nonblank strings of at most 1000 characters')
    tasks = value['tasks']
    if not isinstance(tasks, dict) or not tasks:
        raise ValueError('Public mission requires tasks')
    for name, task in tasks.items():
        if (not isinstance(name, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', name)
                or not isinstance(task, dict) or not {'meaning', 'inputs', 'outputs'} <= set(task)
                or set(task) - {'meaning', 'inputs', 'outputs', 'sampling'}
                or not isinstance(task['meaning'], str) or not task['meaning'].strip()
                or not isinstance(task['inputs'], dict) or not isinstance(task['outputs'], dict)):
            raise ValueError('Invalid public task: ' + str(name))
        if 'sampling' in task and task['sampling'] not in ('acquire', 'retained'):
            raise ValueError('Unknown public sampling requirement')
        for spec in task['inputs'].values():
            if not isinstance(spec, dict) or spec.get('type') not in ('integer', 'number', 'string', 'bytes'):
                raise ValueError('Invalid public task input')
        for spec in task['outputs'].values():
            if (not isinstance(spec, dict) or 'unit' not in spec
                    or spec['unit'] is not None and (not isinstance(spec['unit'], str) or not spec['unit'])):
                raise ValueError('Public output requires an explicit unit (or null)')
    return copy.deepcopy(value)


def load_mission(case_id):
    from .case_assets import case_root
    from .registry import _unique_object
    if not isinstance(case_id, str) or not re.fullmatch('[a-z0-9][a-z0-9-]*', case_id):
        raise ValueError('Invalid public mission case ID')
    path = case_root() / 'missions' / (case_id + '.json')
    try:
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except FileNotFoundError:
        raise ValueError('No public mission is registered for ' + case_id) from None
    return validate_mission(value, case_id)

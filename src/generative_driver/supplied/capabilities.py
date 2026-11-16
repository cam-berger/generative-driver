"""Candidate-only capability binding and validation; no evaluator contracts or I/O."""
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


def validate_capabilities(capabilities: dict, model: dict, required_tasks=()) -> dict:
    """Validate candidate-owned mapping structure without canonical vectors or I/O."""
    errors = []
    def error(path, message):
        errors.append({'fault': 'model', 'code': 'capability_mapping',
                       'path': path[:256], 'message': message})
    if (type(capabilities) is not dict or capabilities.get('schema') != 'benchmark-capabilities/2'
            or type(capabilities.get('tasks')) is not dict):
        error('tasks', 'Declare schema benchmark-capabilities/2 and a tasks object')
        return {'ok': False, 'errors': errors}
    tasks = capabilities['tasks']
    for task in required_tasks:
        if task not in tasks:
            error('tasks.' + task, 'Required canonical task is missing')
    for task, spec in tasks.items():
        prefix = 'tasks.' + str(task)[:64]
        if type(spec) is not dict:
            error(prefix, 'Task must be an object')
            continue
        operation = spec.get('operation')
        if type(operation) is not str or operation not in model.get('operations', {}):
            error(prefix + '.operation', 'Name an operation in the candidate model')
            continue
        candidate = model['operations'][operation]
        parameters = candidate['parameters']
        if any(type(spec.get(field)) is not dict for field in ('constants', 'inputs', 'outputs')):
            error(prefix, 'Declare constants, inputs and outputs objects, even when empty')
            continue
        destinations = list(spec['constants'])
        for name, value in spec['constants'].items():
            path = prefix + '.constants.' + str(name)[:64]
            if name not in parameters:
                error(path, 'Constant must name a candidate parameter')
            else:
                try:
                    _parameter_value(value, parameters[name])
                except ValueError:
                    error(path, 'Constant violates the candidate parameter type or bounds')
        for name, mapping in spec['inputs'].items():
            path = prefix + '.inputs.' + str(name)[:64]
            if (type(mapping) is not dict or type(mapping.get('parameter')) is not str
                    or mapping['parameter'] not in parameters):
                error(path + '.parameter', 'Name a candidate parameter')
                continue
            target = mapping['parameter']
            destinations.append(target)
            kind = mapping.get('kind')
            if kind == 'copy':
                continue
            if kind not in (None, 'affine') or not {'scale', 'offset'} <= mapping.keys():
                error(path + '.kind', 'Use kind="copy", or kind="affine" with scale and offset')
                continue
            if parameters[target]['type'] not in ('integer', 'number'):
                error(path + '.kind', 'Affine mapping requires a numeric candidate parameter')
                continue
            for field in ('scale', 'offset'):
                try:
                    numeric_value(mapping[field])
                except ValueError:
                    error(path + '.' + field, 'Affine coefficient must be a finite number')
        if len(destinations) != len(set(destinations)) or set(destinations) != set(parameters):
            error(prefix + '.inputs', 'Constants and inputs must cover every candidate parameter exactly once')
        for name, mapping in spec['outputs'].items():
            path = prefix + '.outputs.' + str(name)[:64]
            if (type(mapping) is not dict or type(mapping.get('output')) is not str
                    or mapping['output'] not in candidate['outputs']):
                error(path + '.output', 'Name an output in the candidate model')
            elif 'unit' not in mapping or mapping['unit'] != candidate['outputs'][mapping['output']].get('unit'):
                error(path + '.unit', 'Declare the candidate output unit explicitly, including null for unitless outputs')
    return {'ok': not errors, 'errors': errors}


def bind_task(capabilities: dict, task: str, inputs: dict, model: dict) -> dict:
    try:
        if capabilities['schema'] != 'benchmark-capabilities/2' or type(inputs) is not dict:
            raise ValueError('Invalid capability schema or input')
        spec = capabilities['tasks'][task]
        validated = validate_capabilities({'schema': capabilities['schema'], 'tasks': {task: spec}}, model)
        if not validated['ok']:
            defect = validated['errors'][0]
            raise ValueError(defect['path'] + ': ' + defect['message'])
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

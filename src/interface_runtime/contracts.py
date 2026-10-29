"""Public driver metadata and its declarative wire implementation.

The executable model is a derived view. Public parameters, effects and units
have one authority, independent of transport and decoder details.
"""
from copy import deepcopy
import math

from .engine import validate_model


def operation_warnings(operation, scope):
    definitions = [(scope, operation)] + [
        (scope+'.'+group+'.'+name, definition)
        for group in ('parameters', 'outputs') for name, definition in operation[group].items()]
    return [{**entry, 'scope': path} for path, definition in definitions
            for entry in definition['uncertainties']]


def validate_schema(schema):
    """Accept only the value constraints that this runtime understands."""
    if type(schema) is not dict or 'type' not in schema:
        raise ValueError('Public value schema must declare a type')
    allowed = {'type', 'enum'}
    if schema['type'] in ('integer', 'number'):
        allowed |= {'minimum', 'maximum'}
    if schema['type'] == 'string':
        allowed |= {'minLength', 'maxLength', 'contentEncoding'}
    if schema.keys() - allowed:
        raise ValueError('Unsupported public value schema constraint')
    if schema['type'] not in ('integer', 'number', 'string', 'boolean', 'null'):
        raise ValueError('Unsupported public value schema type')
    for key in ('minimum', 'maximum'):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise ValueError('Public numeric bounds must be finite numbers')
    if 'minimum' in schema and 'maximum' in schema and schema['minimum'] > schema['maximum']:
        raise ValueError('Public numeric bounds must be ordered')
    if 'enum' in schema and (type(schema['enum']) is not list or not schema['enum']):
        raise ValueError('Public enums must be nonempty lists')
    for key in ('minLength', 'maxLength'):
        if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 65536):
            raise ValueError('Public string bounds must be bounded nonnegative integers')
    if 'contentEncoding' in schema and (schema['contentEncoding'] != 'hex' or
            any(type(schema.get(key)) is not int or schema[key] % 2 for key in ('minLength', 'maxLength'))):
        raise ValueError('Hex strings need even, explicit character bounds')


def validate_value(value, schema, path='value'):
    """Validate a decoded public JSON value without optional dependencies."""
    kind = schema.get('type')
    numeric = type(value) in (int, float)
    try:
        finite = numeric and math.isfinite(value)
    except OverflowError:
        finite = False
    matches = {'number': finite,
               'integer': finite and (type(value) is int or value.is_integer()),
               'string': type(value) is str, 'boolean': type(value) is bool,
               'null': value is None, 'object': type(value) is dict, 'array': type(value) is list}
    if kind not in matches or not matches[kind]:
        raise ValueError(path + ' must satisfy public type ' + str(kind))
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + ' is not an allowed value')
    if finite and (('minimum' in schema and value < schema['minimum']) or
                   ('maximum' in schema and value > schema['maximum'])):
        raise ValueError(path + ' violates public numeric bounds')
    if kind == 'string':
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 65536):
            raise ValueError(path + ' violates public string bounds')
        if schema.get('contentEncoding') == 'hex' and (len(value) % 2 or
                any(character not in '0123456789abcdefABCDEF' for character in value)):
            raise ValueError(path + ' must contain a complete hexadecimal byte string')


def _public_parameter(schema):
    if schema['type'] == 'bytes':
        return {'type': 'string', 'contentEncoding': 'hex',
                'minLength': schema['min_length'] * 2, 'maxLength': schema['max_length'] * 2}
    return deepcopy(schema)


def _wire_parameter(schema):
    if schema.get('contentEncoding') == 'hex':
        return {'type': 'bytes', 'min_length': schema['minLength'] // 2, 'max_length': schema['maxLength'] // 2}
    return deepcopy(schema)


def _fields(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Invalid driver contract/protocol fields; expected ' + ', '.join(required))


def _unknown(subject):
    return [{'code': 'semantics_unknown',
             'message': subject + ' meaning and operating assumptions are not documented.',
             'evidence_refs': []}]


def _metadata(value):
    description = value['description']
    caveats = value['uncertainties']
    if type(description) is not str or not description.strip() or len(description) > 8192:
        raise ValueError('Driver descriptions must be bounded nonempty text')
    if type(caveats) is not list or len(caveats) > 64:
        raise ValueError('Uncertainty must be a bounded list of scoped caveats')
    for caveat in caveats:
        _fields(caveat, ('code', 'message', 'evidence_refs'))
        if any(type(caveat[key]) is not str or not caveat[key].strip() or len(caveat[key]) > 8192
               for key in ('code', 'message')):
            raise ValueError('Uncertainty requires a code and explanation')
        references = caveat['evidence_refs']
        if type(references) is not list or len(references) > 128 or any(
                type(item) is not str or not item.strip() or len(item) > 2048 for item in references):
            raise ValueError('Uncertainty evidence references must be bounded text')
    if 'unit' in value and value['unit'] is not None and type(value['unit']) is not str:
        raise ValueError('Unit must be text or explicitly unknown')


def from_model(model):
    """Import a supported executable model without inferring device meanings."""
    checked = validate_model(model)
    if not checked['ok']:
        raise ValueError(str(checked['defects']))
    contract = {'schema': 'driver-contract/1', 'device': deepcopy(model['device']),
                'identity': deepcopy(model['identity']), 'operations': {},
                'provenance': deepcopy(model['provenance'])}
    protocol = {'schema': 'driver-protocol/1', 'runtime_schema': model['schema'],
                'channel': deepcopy(model['channel']), 'operations': {}}
    for name, operation in model['operations'].items():
        outputs = {}
        decoders = deepcopy(operation['outputs'])
        for key, decoder in decoders.items():
            kind = decoder['kind']
            unit = decoder.pop('unit', None)
            outputs[key] = {'schema': {'type': 'number' if kind in
                ('integer', 'float32', 'ascii_number', 'kv_number') else 'string'},
                'description': 'Decoded output ' + key + '.', 'unit': unit,
                'uncertainties': [] if unit is not None else _unknown('Output ' + key)}
        contract['operations'][name] = {'effect': operation['effect'],
            'description': 'Execute public operation ' + name + '.',
            'uncertainties': _unknown('Operation ' + name),
            'parameters': {key: {'schema': _public_parameter(spec), 'description': 'Argument ' + key + '.',
                'unit': None, 'uncertainties': _unknown('Parameter ' + key)}
                for key, spec in operation['parameters'].items()}, 'outputs': outputs}
        protocol['operations'][name] = {'steps': deepcopy(operation['steps']), 'outputs': decoders}
    return contract, protocol


def compile_contract(contract, protocol):
    """Build and validate the engine view from its two authoritative documents."""
    _fields(contract, ('schema', 'device', 'identity', 'operations', 'provenance'))
    _fields(protocol, ('schema', 'runtime_schema', 'channel', 'operations'))
    if contract.get('schema') != 'driver-contract/1' or protocol.get('schema') != 'driver-protocol/1':
        raise ValueError('Unsupported driver contract or protocol schema')
    if contract['operations'].keys() != protocol['operations'].keys():
        raise ValueError('Contract and protocol operations differ')
    model = {'schema': protocol['runtime_schema'], 'device': deepcopy(contract['device']),
             'channel': deepcopy(protocol['channel']), 'identity': deepcopy(contract['identity']),
             'operations': {}, 'provenance': deepcopy(contract['provenance'])}
    for name, public in contract['operations'].items():
        wire = protocol['operations'][name]
        _fields(public, ('effect', 'parameters', 'outputs', 'description', 'uncertainties'), ('preconditions',))
        _fields(wire, ('steps', 'outputs'))
        _metadata(public)
        for definitions in (public['parameters'], public['outputs']):
            if type(definitions) is not dict:
                raise ValueError('Public parameters and outputs must be objects')
            for definition in definitions.values():
                _fields(definition, ('schema', 'description', 'unit', 'uncertainties'))
                _metadata(definition)
                validate_schema(definition['schema'])
        if public['outputs'].keys() != wire['outputs'].keys():
            raise ValueError('Contract and protocol outputs differ: ' + name)
        outputs = deepcopy(wire['outputs'])
        for key, decoder in outputs.items():
            if 'unit' in decoder:
                raise ValueError('Protocol decoders cannot declare public units')
            numeric = decoder.get('kind') in ('integer', 'float32', 'ascii_number', 'kv_number')
            compatible = ('integer', 'number') if numeric else ('string',)
            if public['outputs'][key]['schema']['type'] not in compatible:
                raise ValueError('Public output type is incompatible with its decoder: '+name+'.'+key)
            if public['outputs'][key].get('unit') is not None:
                decoder['unit'] = public['outputs'][key]['unit']
        model['operations'][name] = {'effect': public['effect'],
            'parameters': {key: _wire_parameter(spec['schema']) for key, spec in public['parameters'].items()},
            'steps': deepcopy(wire['steps']),
            'outputs': outputs}
    checked = validate_model(model)
    if not checked['ok']:
        raise ValueError(str(checked['defects']))
    _preconditions(contract, model)
    return model


def _preconditions(contract, model):
    from .engine import _arguments
    for name, operation in contract['operations'].items():
        conditions = operation.get('preconditions', [])
        if type(conditions) is not list or len(conditions) > 16:
            raise ValueError('Preconditions must be a bounded list')
        seen = set()
        for condition in conditions:
            _fields(condition, ('id', 'description', 'observation', 'operator', 'expected'))
            if (type(condition['id']) is not str or not condition['id'] or condition['id'] in seen
                    or type(condition['description']) is not str or not condition['description'].strip()):
                raise ValueError('Preconditions need unique identities and readable rules')
            seen.add(condition['id'])
            if condition['operator'] != 'eq':
                raise ValueError('Unsupported precondition operator')
            source = condition['observation']
            _fields(source, ('operation', 'parameters', 'output'))
            observed = contract['operations'].get(source['operation'])
            if (observed is None or observed['effect'] != 'read' or observed.get('preconditions')
                    or source['output'] not in observed['outputs']):
                raise ValueError('Preconditions require a declared unguarded read observation')
            _arguments(model['operations'][source['operation']]['parameters'], source['parameters'])
            validate_value(condition['expected'], observed['outputs'][source['output']]['schema'],
                           'precondition.expected')

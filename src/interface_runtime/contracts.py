"""The driver contract, as a view of an interface model.

The interface model is the one statement of a driver. Its public part (operations, meanings, value schemas, units,
scoped caveats and preconditions) is computed from it wherever it is shown, so no second file has to be kept equal to
the model (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3).
"""
from copy import deepcopy

from . import meanings

# What a view shows where the model gives no description, with the caveat that says so.
UNDOCUMENTED = 'Meaning not documented.'
_UNKNOWN = 'semantics_unknown'
# The fields of a parameter that state a meaning; the rest is the wire.
_PARAMETER_MEANINGS = ('description', 'unit', 'uncertainties')


def operation_warnings(operation, scope):
    definitions = [(scope, operation)] + [
        (scope+'.'+group+'.'+name, definition)
        for group in ('parameters', 'outputs') for name, definition in operation[group].items()]
    return [{**entry, 'scope': path} for path, definition in definitions
            for entry in definition['uncertainties']]


def _metadata(value):
    """Raise ValueError unless `value`, an application tool or one of its parameters or outputs, has a description and
    uncertainties as a driver's have, and a unit that is text or null."""
    meanings.check_description(value['description'])
    meanings.check_uncertainties(value['uncertainties'])
    if 'unit' in value and value['unit'] is not None and type(value['unit']) is not str:
        raise ValueError('Unit must be text or explicitly unknown')


_MARKING = ' meaning and operating assumptions are not documented.'


def _meaning(definition, subject):
    """The description and the uncertainties a view shows for `definition`: what the model says, or the marking of a
    missing description after the uncertainties the worker wrote, unless one of them says so already."""
    caveats = deepcopy(definition.get('uncertainties', []))
    if 'description' in definition:
        return definition['description'], caveats
    if not any(caveat['code'] == _UNKNOWN for caveat in caveats):
        caveats.append({'code': _UNKNOWN, 'message': subject + _MARKING, 'evidence_refs': []})
    return UNDOCUMENTED, caveats


def stated_uncertainties(definition):
    """The uncertainties the model states for `definition`, an operation, a parameter or an output of a driver
    contract: all it holds but the marking a view adds where the model gives no description."""
    return [caveat for caveat in definition['uncertainties']
            if not (definition['description'] == UNDOCUMENTED and caveat['code'] == _UNKNOWN
                    and caveat['evidence_refs'] == [] and caveat['message'].endswith(_MARKING))]


def _parameter_schema(spec):
    if spec['type'] == 'bytes':
        return {'type': 'string', 'contentEncoding': 'hex',
                'minLength': spec['min_length'] * 2, 'maxLength': spec['max_length'] * 2}
    return {key: deepcopy(value) for key, value in spec.items() if key not in _PARAMETER_MEANINGS}


def _definition(schema, definition, subject):
    description, caveats = _meaning(definition, subject)
    return {'schema': schema, 'description': description, 'unit': definition.get('unit'), 'uncertainties': caveats}


def contract_view(model):
    """The driver contract of `model`, an interface model that validates: its operations with their effects, meanings,
    value schemas, units, uncertainties and preconditions, in the shape `driver-contract/1`. A meaning the model leaves
    out is marked as not documented. The model is left as it is."""
    operations = {}
    for name, operation in model['operations'].items():
        description, caveats = _meaning(operation, 'Operation ' + name)
        operations[name] = {
            'effect': operation['effect'], 'description': description, 'uncertainties': caveats,
            'parameters': {key: _definition(_parameter_schema(spec), spec, 'Parameter ' + key)
                           for key, spec in operation['parameters'].items()},
            'outputs': {key: _definition(deepcopy(decoder.get('value', meanings.default_value(decoder['kind']))),
                                         decoder, 'Output ' + key)
                        for key, decoder in operation['outputs'].items()}}
        if 'preconditions' in operation:
            operations[name]['preconditions'] = deepcopy(operation['preconditions'])
    return {'schema': 'driver-contract/1', 'device': deepcopy(model['device']),
            'identity': deepcopy(model['identity']), 'operations': operations,
            'provenance': deepcopy(model['provenance'])}

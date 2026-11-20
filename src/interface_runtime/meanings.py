"""The rules of the meaning fields an interface model holds beside its wire steps.

A driver is stated once: each operation, parameter and output of an interface model may say what it means
(docs/adr/0010-a-package-is-data-in-one-format.md, decision 3). The engine applies these rules when it validates a
model and when it decodes an output; the driver contract is a view of the same fields (`contracts`).
"""
import math

# The decoders that give a number; every other decoder gives text.
NUMERIC_DECODERS = ('integer', 'float32', 'ascii_number', 'kv_number')


def _exactly(value, fields, message):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError(message)


def check_description(description):
    """Raise ValueError unless `description` is text that is not blank, of at most 8192 characters."""
    if type(description) is not str or not description.strip() or len(description) > 8192:
        raise ValueError('Driver descriptions must be bounded nonempty text')


def check_uncertainties(caveats):
    """Raise ValueError unless `caveats` is a list of at most 64 entries, each exactly `code`, `message` and
    `evidence_refs`."""
    if type(caveats) is not list or len(caveats) > 64:
        raise ValueError('Uncertainty must be a bounded list of scoped caveats')
    for caveat in caveats:
        _exactly(caveat, ('code', 'message', 'evidence_refs'),
                 'Uncertainty holds exactly code, message and evidence_refs')
        if any(type(caveat[key]) is not str or not caveat[key].strip() or len(caveat[key]) > 8192
               for key in ('code', 'message')):
            raise ValueError('Uncertainty requires a code and explanation')
        references = caveat['evidence_refs']
        if type(references) is not list or len(references) > 128 or any(
                type(item) is not str or not item.strip() or len(item) > 2048 for item in references):
            raise ValueError('Uncertainty evidence references must be bounded text')


def default_value(kind):
    """What an output of decoder `kind` may be when its model narrows nothing: any number, or any text."""
    return {'type': 'number' if kind in NUMERIC_DECODERS else 'string'}


def check_value_rule(rule, kind):
    """Raise ValueError unless `rule` is a `value` an output of decoder `kind` may state: a type the decoder gives, with
    only the constraints that type takes."""
    if type(rule) is not dict or 'type' not in rule:
        raise ValueError('Output value must declare a type')
    numeric = kind in NUMERIC_DECODERS
    if rule['type'] not in (('integer', 'number') if numeric else ('string',)):
        raise ValueError('Output value type must be ' + ('integer or number' if numeric else 'string')
                         + ' for decoder ' + str(kind))
    if rule.keys() - {'type', 'enum'} - ({'minimum', 'maximum'} if numeric else {'minLength', 'maxLength'}):
        raise ValueError('Unsupported output value constraint')
    for key in ('minimum', 'maximum'):
        if key in rule and (type(rule[key]) not in (int, float) or not math.isfinite(rule[key])):
            raise ValueError('Output value bounds must be finite numbers')
    if 'minimum' in rule and 'maximum' in rule and rule['minimum'] > rule['maximum']:
        raise ValueError('Output value bounds must be ordered')
    if 'enum' in rule and (type(rule['enum']) is not list or not rule['enum']):
        raise ValueError('Output value enums must be nonempty lists')
    for key in ('minLength', 'maxLength'):
        if key in rule and (type(rule[key]) is not int or not 0 <= rule[key] <= 65536):
            raise ValueError('Output value lengths must be integers from 0 to 65536')


def check_value(value, rule, path='value'):
    """Raise ValueError unless the decoded or expected `value` is one that `rule` allows."""
    kind = rule.get('type')
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    matches = {'number': finite, 'integer': finite and (type(value) is int or value.is_integer()),
               'string': type(value) is str}
    if not matches.get(kind, False):
        raise ValueError(path + ' must satisfy public type ' + str(kind))
    if 'enum' in rule and value not in rule['enum']:
        raise ValueError(path + ' is not an allowed value')
    if finite and (('minimum' in rule and value < rule['minimum']) or
                   ('maximum' in rule and value > rule['maximum'])):
        raise ValueError(path + ' violates public numeric bounds')
    if kind == 'string' and (len(value) < rule.get('minLength', 0) or len(value) > rule.get('maxLength', 65536)):
        raise ValueError(path + ' violates public string bounds')


def undocumented(model):
    """The path of each operation, parameter and output of `model`, an interface model that validates, that has no
    description, such as `operations.measure.outputs.temperature`."""
    paths = []
    for name, operation in model['operations'].items():
        scope = 'operations.' + name
        definitions = [(scope, operation)] + [
            (scope + '.' + group + '.' + key, definition)
            for group in ('parameters', 'outputs') for key, definition in operation[group].items()]
        paths += [path for path, definition in definitions if 'description' not in definition]
    return paths


def advice(model):
    """What `model`, an interface model that validates, leaves undocumented, as lines for the worker who wrote it: one
    for each operation, parameter and output without a description, and one for each output that is a number with
    neither a unit nor an uncertainty of the code `unit_not_stated`. Advice is no defect."""
    lines = [path + ' has no description' for path in undocumented(model)]
    for name, operation in model['operations'].items():
        for key, decoder in operation['outputs'].items():
            stated = any(caveat['code'] == 'unit_not_stated' for caveat in decoder.get('uncertainties', []))
            if decoder['kind'] in NUMERIC_DECODERS and 'unit' not in decoder and not stated:
                lines.append('operations.' + name + '.outputs.' + key
                             + ' has no unit and no unit_not_stated uncertainty')
    return lines

"""Deterministic MCP descriptions derived from public contract data; no MCP dependency.

Every renderer version a kit has written stays here, so an earlier package's catalog still verifies; new packages use
`RENDERER` (docs/adr/0008-run-the-installed-runtime-over-package-data.md)."""
from copy import deepcopy

# Renderer 3 differs from 2 only in a null unit that a `unit_not_stated` uncertainty explains.
RENDERERS = ('2', '3')
RENDERER = '3'
_UNIT_NOT_STATED = 'unit_not_stated'


def _unit(definition, renderer_version):
    if definition['unit'] is not None:
        return definition['unit']
    if renderer_version != '2' and any(item['code'] == _UNIT_NOT_STATED for item in definition['uncertainties']):
        return 'not stated by the device'
    return 'unknown'


def _description(definition, renderer_version):
    parts = [definition['description']]
    if 'unit' in definition:
        parts.append('Unit: ' + _unit(definition, renderer_version) + '.')
    parts.extend('Warning: ' + item['message'] for item in definition['uncertainties'])
    parts.extend('Precondition ' + item['id'] + ': ' + item['description']
                 for item in definition.get('preconditions', []))
    return '\n'.join(parts)


def _known(renderer_version):
    if renderer_version not in RENDERERS:
        raise ValueError('MCP catalog renderer version is not one this kit knows: ' + repr(renderer_version))


def render_tool(name, operation, renderer_version=RENDERER):
    _known(renderer_version)
    if '_request_id' in operation['parameters']:
        raise ValueError('_request_id is reserved for managed invocation context')
    parameters = {key: {**deepcopy(spec['schema']), 'description': _description(spec, renderer_version)}
                  for key, spec in sorted(operation['parameters'].items())}
    required = sorted(parameters.keys() | ({'_request_id'} if operation['effect'] != 'read' else set()))
    parameters['_request_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 128,
        'description': 'Caller-chosen invocation ID. Required for writes/actuation. Reuse it only to retrieve this same invocation after a lost reply; never assign a new ID as an automatic retry.'}
    outputs = {key: {**deepcopy(spec['schema']), 'description': _description(spec, renderer_version)}
               for key, spec in sorted(operation['outputs'].items())}
    return {'name': name, 'description': _description(operation, renderer_version),
        'inputSchema': {'type': 'object', 'properties': parameters,
                        'required': required, 'additionalProperties': False},
        'outputSchema': {'type': 'object', 'properties': {
            'schema': {'const': 'driver-result/1'}, 'operation': {'type': 'string'},
            'ok': {'type': 'boolean'}, 'outputs': {'type': 'object', 'properties': outputs},
            'warnings': {'type': 'array', 'items': {'type': 'object'}},
            'error': {'type': 'object'}}, 'required': ['schema', 'operation', 'ok', 'outputs', 'warnings'],
            'allOf': [{'if': {'properties': {'ok': {'const': True}}},
                       'then': {'properties': {'outputs': {'required': sorted(outputs)}}}}]},
        'annotations': {'readOnlyHint': operation['effect'] == 'read',
                        'destructiveHint': operation['effect'] != 'read', 'openWorldHint': False}}


def render_catalog(contract, contract_sha256, namespace='device', renderer_version=RENDERER):
    _known(renderer_version)
    if (type(namespace) is not str or not 1 <= len(namespace) <= 48
            or not namespace.isascii() or not namespace[0].isalpha()
            or not all(character.isalnum() or character == '_' for character in namespace)):
        raise ValueError('MCP namespace must be a bounded ASCII name')
    operations = {namespace+'_'+name: name for name in sorted(contract['operations'])}
    return {'schema': 'driver-mcp/1', 'renderer_version': renderer_version, 'namespace': namespace,
            'contract_sha256': contract_sha256, 'operations': operations,
            'tools': [render_tool(tool, contract['operations'][operation], renderer_version)
                      for tool, operation in operations.items()]}

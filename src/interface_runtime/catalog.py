"""Deterministic MCP descriptions derived from public contract data; no MCP dependency."""
from copy import deepcopy


def _description(definition):
    parts = [definition['description']]
    if 'unit' in definition:
        parts.append('Unit: ' + (definition['unit'] if definition['unit'] is not None else 'unknown') + '.')
    parts.extend('Warning: ' + item['message'] for item in definition['uncertainties'])
    parts.extend('Precondition ' + item['id'] + ': ' + item['description']
                 for item in definition.get('preconditions', []))
    return '\n'.join(parts)


def render_tool(name, operation):
    if '_request_id' in operation['parameters']:
        raise ValueError('_request_id is reserved for managed invocation context')
    parameters = {key: {**deepcopy(spec['schema']), 'description': _description(spec)}
                  for key, spec in sorted(operation['parameters'].items())}
    required = sorted(parameters.keys() | ({'_request_id'} if operation['effect'] != 'read' else set()))
    parameters['_request_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 128,
        'description': 'Caller-chosen invocation ID. Required for writes/actuation. Reuse it only to retrieve this same invocation after a lost reply; never assign a new ID as an automatic retry.'}
    outputs = {key: {**deepcopy(spec['schema']), 'description': _description(spec)}
               for key, spec in sorted(operation['outputs'].items())}
    return {'name': name, 'description': _description(operation),
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


def render_catalog(contract, contract_sha256, namespace='device'):
    if (type(namespace) is not str or not 1 <= len(namespace) <= 48
            or not namespace.isascii() or not namespace[0].isalpha()
            or not all(character.isalnum() or character == '_' for character in namespace)):
        raise ValueError('MCP namespace must be a bounded ASCII name')
    operations = {namespace+'_'+name: name for name in sorted(contract['operations'])}
    return {'schema': 'driver-mcp/1', 'renderer_version': '2', 'namespace': namespace,
            'contract_sha256': contract_sha256, 'operations': operations,
            'tools': [render_tool(tool, contract['operations'][operation])
                      for tool, operation in operations.items()]}

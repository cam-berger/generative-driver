"""The names, the tools and `mcp.json` of a package, rendered from its drivers; no MCP dependency.

One module renders them, so an agent reads the same words wherever a package is served: in `mcp.json`, from the
command that serves a package, in fresh reuse, in a managed session's description and in an application of the SDK
(docs/adr/0010-a-package-is-data-in-one-format.md)."""
from copy import deepcopy
import hashlib
import json
import re

from .contracts import contract_view, stated_uncertainties

SCHEMA = 'driver-mcp/2'
_UNIT_NOT_STATED = 'unit_not_stated'


def driver_name(model):
    """The name of the driver of `model`, an interface model that validates. A driver is named after its device: the
    device's name in lower case, each run of other characters than a to z, the digits and `_` as one `_`, without `_`
    at either end. An empty name is `device`, one that starts with a digit gets `device_` in front, and a name is at
    most 48 characters long."""
    name = re.sub('[^a-z0-9_]+', '_', (model['device']['name'] or '').lower()).strip('_')
    if not name:
        return 'device'
    if name[0].isdigit():
        name = 'device_' + name
    return name[:48].strip('_')


def _unit(definition, number):
    """The unit a description names for `definition`, or None for no unit line. An output that is a number always
    has one; a parameter, and an output that is text, only when a unit is stated."""
    if definition.get('unit') is not None:
        return definition['unit']
    if not number:
        return None
    if any(item['code'] == _UNIT_NOT_STATED for item in definition['uncertainties']):
        return 'not stated by the device'
    return 'unknown'


def describe(definition, output=False):
    """The lines that describe `definition`, an operation, a parameter or, with `output`, an output of a driver
    contract: its meaning, then its unit, each uncertainty the model states as a warning and each precondition, one
    line each. Where the model gives no description the meaning reads `Meaning not documented.`, and no warning
    repeats it."""
    parts = [definition['description']]
    number = output and definition['schema'].get('type') in ('number', 'integer')
    unit = _unit(definition, number)
    if unit is not None:
        parts.append('Unit: ' + unit + '.')
    parts.extend('Warning: ' + item['message'] for item in stated_uncertainties(definition))
    parts.extend('Precondition ' + item['id'] + ': ' + item['description']
                 for item in definition.get('preconditions', []))
    return parts


def _description(definition, output=False):
    return '\n'.join(describe(definition, output))


def render_tool(name, operation):
    """The MCP tool `name` of `operation`, an operation of a driver contract: its description, the schema of its
    arguments and the schema of the `driver-result/1` it returns."""
    if '_request_id' in operation['parameters']:
        raise ValueError('_request_id is reserved for managed invocation context')
    parameters = {key: {**deepcopy(spec['schema']), 'description': _description(spec)}
                  for key, spec in sorted(operation['parameters'].items())}
    required = sorted(parameters.keys() | ({'_request_id'} if operation['effect'] != 'read' else set()))
    parameters['_request_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 128,
        'description': 'Caller-chosen invocation ID. Required for writes/actuation. Reuse it only to retrieve this same invocation after a lost reply; never assign a new ID as an automatic retry.'}
    outputs = {key: {**deepcopy(spec['schema']), 'description': _description(spec, output=True)}
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


def render_mcp(drivers):
    """The document `mcp.json` of a package, for `drivers`, which maps the name of the package's one driver to its
    interface model: the server's name, which is the driver's, a tool `<driver>_<operation>` for each operation, the
    driver and operation behind each tool, and the entry an MCP client needs, with the package's folder and the
    binding left as the words PACKAGE and BINDING for the operator."""
    if len(drivers) != 1:
        raise ValueError('a package of several drivers is not built yet')
    [(driver, model)] = drivers.items()
    operations = contract_view(model)['operations']
    behind = {driver + '_' + name: {'driver': driver, 'operation': name} for name in sorted(operations)}
    return {'schema': SCHEMA, 'server': driver,
            'tools': [render_tool(tool, operations[entry['operation']]) for tool, entry in behind.items()],
            'operations': behind,
            'client': {'mcpServers': {driver: {
                'command': 'generative-driver', 'args': ['serve', 'PACKAGE', '--binding', 'BINDING']}}}}


def tools_digest(tools):
    """The SHA-256 of `tools` as they were rendered: of their JSON with sorted keys, in UTF-8."""
    return hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False, allow_nan=False)
                          .encode('utf-8')).hexdigest()

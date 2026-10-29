"""Shared validation and execution conventions for device and application tools."""
from copy import deepcopy
import re
import uuid
import json


def _inline_schema(schema, depth=0):
    if depth > 32:
        raise ValueError('Tool schemas must have bounded depth')
    if type(schema) is dict:
        if '$ref' in schema or '$dynamicRef' in schema:
            raise ValueError('Tool schema references are unsupported; inline the packaged schema')
        for value in schema.values():
            _inline_schema(value, depth+1)
    elif type(schema) is list:
        for value in schema:
            _inline_schema(value, depth+1)


def failure(operation, code, message, fault='operator'):
    return {'schema': 'driver-result/1', 'operation': operation, 'ok': False,
            'outputs': {}, 'warnings': [], 'calls': [],
            'error': {'code': code, 'message': message, 'fault': fault}}


def _invalid_output(name, result, message, invalid):
    return {**result, **failure(name, 'output_schema_violation', message, 'model'),
            'invalid_outputs': invalid, 'calls': result.get('calls', []),
            'warnings': result.get('warnings', [])}


class ToolAdapter:
    def __init__(self, tools, invoke):
        from jsonschema import Draft202012Validator
        self.tools = deepcopy(tools)
        self._definitions = {tool['name']: tool for tool in self.tools}
        if len(self._definitions) != len(self.tools):
            raise ValueError('Duplicate tool identity')
        self._invoke = invoke
        self._inputs = {}
        self._outputs = {}
        for name, tool in self._definitions.items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', name):
                raise ValueError('Tool identity must be a bounded ASCII name')
            _inline_schema(tool['inputSchema'])
            _inline_schema(tool['outputSchema'])
            Draft202012Validator.check_schema(tool['inputSchema'])
            self._inputs[name] = Draft202012Validator(tool['inputSchema'])
            Draft202012Validator.check_schema(tool['outputSchema'])
            self._outputs[name] = Draft202012Validator(tool['outputSchema'])

    def invoke(self, name, arguments, *, cancel=None):
        errors = list(self._inputs[name].iter_errors(arguments))
        if errors:
            return failure(name, 'invalid_arguments', errors[0].message)
        arguments = dict(arguments)
        request_id = arguments.pop('_request_id', None) or uuid.uuid4().hex
        result = self._invoke(name, arguments, cancel=cancel, request_id=request_id)
        result.setdefault('request_id', request_id)
        try:
            json.dumps(result, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as error:
            return _invalid_output(name, result, 'Result is not finite JSON: '+str(error),
                                   repr(result.get('outputs'))[:8192])
        errors = list(self._outputs[name].iter_errors(result))
        if errors:
            return _invalid_output(name, result, errors[0].message, result.get('outputs'))
        return result

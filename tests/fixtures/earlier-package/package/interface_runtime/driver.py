"""Execute public operations, preserving contract rules across one connection."""
from pathlib import Path
import json

from .contracts import compile_contract, validate_value, operation_warnings
from .engine import Session, _arguments
from .evidence import read_json, ReplayFactory, RecordingFactory, _evidence_bound
from .package import verify_package, digest
from . import transports

MAX_DRIVER_RESULT_BYTES = 2 * 1024 * 1024  # Leaves room in managed IPC and MCP envelopes.


class PackageSession:
    """The contract executor used by single calls and uninterrupted package sequences."""

    def __init__(self, root, *, binding=None, allow_effects=(), replay=None):
        self.root = Path(root)
        checked = verify_package(self.root)
        if not checked['ok']:
            raise ValueError(str(checked['defects']))
        self._manifest_pin = digest(self.root/'manifest.json')
        self._manifest = checked['manifest']
        self._contract = read_json(self.root/'driver/contract.json')
        self._model = compile_contract(self._contract, read_json(self.root/'driver/protocol.json'))
        self._player = ReplayFactory(replay) if replay is not None else None
        self._recorder = RecordingFactory(self._player if self._player is not None else transports.open_transport)
        self._effects = allow_effects
        self._last_result = None
        self._engine = Session(self._model, binding=binding, allow_effects=allow_effects,
            transport_factory=self._recorder,
            delay_fn=(lambda seconds: None) if self._player is not None else None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:
            self._engine.close()
        except Exception as error:
            if self._last_result is None:
                raise
            prior = self._last_result.get('error')
            self._last_result.update(ok=False, outputs={}, units={}, transport_open_attempted=True,
                error={'code': 'close_error', 'message': str(error), 'fault': 'host'})
            if prior is not None:
                self._last_result['prior_error'] = prior

    def execute(self, operation, parameters=None):
        checked = verify_package(self.root, expected_manifest_sha256=self._manifest_pin)
        if not checked['ok']:
            raise ValueError(str(checked['defects']))
        manifest, contract, model = self._manifest, self._contract, self._model
        if operation not in contract['operations']:
            raise ValueError('Unknown public operation')
        public = contract['operations'][operation]
        arguments = {} if parameters is None else parameters
        player, recorder, allow_effects = self._player, self._recorder, self._effects
        recorder.transactions.clear()
        observations = []
        warnings = operation_warnings(public, 'operations.'+operation)
        for condition in public.get('preconditions', []):
            source = condition['observation']['operation']
            for warning in operation_warnings(contract['operations'][source], 'operations.'+source):
                if warning not in warnings:
                    warnings.append(warning)

        def result(value):
            requested = any(step['operation'] == operation and step['op'] != 'delay'
                            for step in value.get('transcript', []))
            self._last_result = {**value, 'schema': 'driver-result/1', 'operation': operation,
                    'observations': observations, 'warnings': warnings,
                    'requested_command_dispatched': requested,
                    'transactions': list(recorder.transactions),
                    'evidence_kind': 'replay' if player is not None else 'live',
                    'timing_policy': 'skip_delays' if player is not None else 'real',
                    'model_sha256': manifest['model_sha256'], 'contract_sha256': manifest['contract_sha256']}
            return self._last_result

        def refuse(code, message, details=None, fault='operator'):
            error = {'code': code, 'message': message, 'fault': fault}
            if details is not None:
                error['precondition'] = details
            return result({'ok': False, 'outputs': {}, 'units': {}, 'transcript': [],
                           'identity_verified': False, 'transport_open_attempted': bool(observations), 'error': error})

        try:
            _arguments(model['operations'][operation]['parameters'], arguments)
            for key, definition in public['parameters'].items():
                validate_value(arguments[key], definition['schema'], 'parameters.'+key)
        except ValueError as error:
            return refuse('validation_error', str(error))
        if public['effect'] != 'read' and public['effect'] not in allow_effects:
            return refuse('validation_error', 'operation requires explicit effect grant')
        bounds = _evidence_bound(model, operation, arguments, allow_effects, 1)
        for condition in public.get('preconditions', []):
            source = condition['observation']
            bounds += _evidence_bound(model, source['operation'], source['parameters'], allow_effects, 1)
        # Guard evidence also appears in rejection details. Include metadata, JSON
        # escaping, invalid decoded outputs and the managed result envelope.
        bounds = 2 * bounds + 2 * len(json.dumps(warnings)) + 2 * len(json.dumps(public.get('preconditions', []))) + 16384
        if bounds > MAX_DRIVER_RESULT_BYTES:
            bounded = refuse('evidence_bound_exceeded',
                'Public operation exceeds the aggregate result/evidence bound before I/O; inspect the contract for omitted caveats.', fault='model')
            bounded.update(warnings=[], warnings_omitted=len(warnings), warnings_ref='driver/contract.json')
            return bounded

        def invoke(name, arguments):
            outcome = self._engine.execute(name, arguments)
            if outcome['ok']:
                try:
                    for key, definition in contract['operations'][name]['outputs'].items():
                        validate_value(outcome['outputs'][key], definition['schema'], 'outputs.'+key)
                except (ValueError, KeyError) as error:
                    outcome.update(ok=False, invalid_outputs=outcome['outputs'], outputs={}, units={},
                                   error={'code': 'output_schema_violation', 'message': str(error), 'fault': 'model'})
            return {**outcome, 'warnings': operation_warnings(
                contract['operations'][name], 'operations.'+name)}

        for condition in public.get('preconditions', []):
            source = condition['observation']
            observed = invoke(source['operation'], source['parameters'])
            observations.append({'precondition_id': condition['id'], 'result': observed})
            value = observed.get('outputs', {}).get(source['output'])
            details = {key: condition[key] for key in ('id', 'description', 'operator', 'expected')}
            details['observed'] = value
            if not observed['ok']:
                return refuse('precondition_unavailable', 'Could not check: '+condition['description'], details,
                              observed.get('error', {}).get('fault', 'host'))
            if value != condition['expected']:
                return refuse('precondition_failed', condition['description'], details)
        outcome = invoke(operation, arguments)
        if player is not None and outcome['ok'] and not player.exhausted():
            outcome.update(ok=False, outputs={}, units={}, error={
                'code': 'replay_mismatch', 'message': 'unused extra replay transactions', 'fault': 'host'})
        return result(outcome)


def execute_package(root, operation, parameters=None, *, binding=None, allow_effects=(), replay=None):
    with PackageSession(root, binding=binding, allow_effects=allow_effects, replay=replay) as current:
        return current.execute(operation, parameters)

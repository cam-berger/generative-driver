"""Supported managed-driver and application-tool authoring interface.

Device bindings and grants belong to operator-opened sessions. Application
handlers receive a context which records the constituent managed calls.
"""
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import uuid


def _call_with_cancellation(method, params, home, *, cancel=None, cancel_method, cancel_params):
    """Cancel one queued invocation without cancelling another intent on its session.

    Once dispatched, wait for the original durable outcome. Cancellation never
    resubmits the requested command or turns a lost reply into a safe retry.
    """
    from concurrent.futures import ThreadPoolExecutor
    from .client import call
    if cancel is None:
        return call(method, params, home, autostart=False)
    if cancel.is_set():
        result = call(cancel_method, cancel_params, home, autostart=False)
        if result.get('schema') == 'driver-result/1' and result.get('operation') is None:
            result = {**result, 'operation': params.get('operation', params.get('name', ''))}
        return result
    completed = threading.Event()
    def invoke():
        try:
            return call(method, params, home, autostart=False)
        finally:
            completed.set()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='managed-invocation') as executor:
        original = executor.submit(invoke)
        while not completed.wait(0.02):
            if cancel.is_set():
                call(cancel_method, cancel_params, home, autostart=False)
                break
        return original.result()


class ManagedDriver:
    def __init__(self, package, session_id, *, home=None):
        from interface_runtime.package import verify_package, digest
        from interface_runtime.evidence import read_json
        self.package = Path(package).expanduser().resolve()
        checked = verify_package(self.package)
        if not checked['ok'] or checked['manifest']['schema'] != 'interface-package/2':
            raise ValueError('Managed use requires a verified driver-contract package: '+str(checked.get('defects')))
        self.manifest_sha256 = digest(self.package/'manifest.json')
        self.contract = read_json(self.package/'driver/contract.json')
        self.session_id, self.home = session_id, home

    @classmethod
    def open(cls, package, *, binding, effects=('read',), home=None, request_id=None):
        """Operator-only configuration; a tool handler should receive an existing client."""
        from .client import call
        driver = cls(package, '', home=home)
        opened = call('device_open', {'package_dir': str(driver.package), 'binding': binding,
            'effects': list(effects), 'expected_manifest_sha256': driver.manifest_sha256,
            'request_id': request_id or uuid.uuid4().hex}, home)
        if not opened.get('ok'):
            raise ValueError(opened.get('reason', 'Cannot open managed driver session'))
        driver.session_id = opened['session_id']
        return driver

    def _control(self, method, **params):
        from .client import call
        result = call(method, {'session_id': self.session_id, **params}, self.home, autostart=False)
        if result.get('ok') and result.get('manifest_sha256') != self.manifest_sha256:
            raise ValueError('Client package identity differs from the managed session package')
        return result

    def describe(self):
        return self._control('device_describe')

    def status(self, request_id=None):
        return self._control('device_status', **({'request_id': request_id} if request_id is not None else {}))

    def cancel(self):
        return self._control('device_cancel')

    def close(self):
        return self._control('device_close')

    def reconcile(self, observation, *, effect_resolution):
        """Operator attestation after checking the device and stopping prior execution."""
        return self._control('device_reconcile', observation=observation, effect_resolution=effect_resolution)

    def invoke(self, operation, parameters=None, *, request_id=None, cancel=None):
        request_id = request_id or uuid.uuid4().hex
        result = _call_with_cancellation('device_execute', {'session_id': self.session_id,
            'expected_manifest_sha256': self.manifest_sha256,
            'request_id': request_id, 'operation': operation,
            'parameters': {} if parameters is None else parameters}, self.home, cancel=cancel,
            cancel_method='device_cancel_invocation',
            cancel_params={'session_id': self.session_id, 'request_id': request_id})
        if result.get('schema') == 'driver-result/1':
            return result
        return {'schema': 'driver-result/1', 'operation': operation, 'ok': False,
                'session_id': self.session_id, 'request_id': request_id,
                'outputs': {}, 'warnings': [], 'error': {'code': 'managed_execution_failed',
                'fault': 'operator', 'message': result.get('reason', 'Managed execution failed')}}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    outputs: dict
    dependencies: dict
    effect: str = 'read'
    uncertainties: list = field(default_factory=list)

    def render(self):
        from interface_runtime.catalog import render_tool
        return render_tool(self.name, {'description': self.description, 'effect': self.effect,
            'parameters': self.parameters, 'outputs': self.outputs, 'uncertainties': self.uncertainties})


class InvocationContext:
    def __init__(self, drivers, spec, cancel=None, request_id=None):
        self._drivers, self._spec = drivers, spec
        self._cancel = cancel or threading.Event()
        self.calls = []
        self.request_id = request_id

    def check_cancelled(self):
        if self._cancel.is_set():
            raise ApplicationCancelled('Application invocation cancelled before further dispatch')

    def invoke(self, driver, operation, parameters=None):
        self.check_cancelled()
        if operation not in self._spec.dependencies.get(driver, ()):
            raise ValueError('Application operation is not a declared dependency')
        child_id = hashlib.sha256((self.request_id+':'+driver+':'+str(len(self.calls))).encode()).hexdigest()
        result = self._drivers[driver].invoke(operation, parameters, request_id=child_id, cancel=self._cancel)
        self.calls.append(result)
        if not result['ok']:
            raise ChildOperationError(result)
        return result


class ChildOperationError(Exception):
    """A child failure, including the original rule explanation and evidence."""
    def __init__(self, result):
        super().__init__(result['error']['message'])
        self.result = result


class ApplicationCancelled(Exception):
    pass


class Application:
    def __init__(self, name, *, drivers):
        self.name, self.drivers = name, dict(drivers)
        self._tools = {}

    def register(self, spec, handler):
        from .tool_adapter import ToolAdapter
        from interface_runtime.contracts import _metadata
        if spec.name in self._tools:
            raise ValueError('Duplicate application tool identity: '+spec.name)
        _metadata({'description': spec.description, 'uncertainties': spec.uncertainties})
        for definition in [*spec.parameters.values(), *spec.outputs.values()]:
            _metadata(definition)
        ToolAdapter([spec.render()], None)
        effects = {'read': 0, 'write': 1, 'actuate': 2}
        if spec.effect not in effects:
            raise ValueError('Unknown application effect')
        for driver, operations in spec.dependencies.items():
            for operation in operations:
                dependency = self.drivers[driver].contract['operations'][operation]
                if effects[dependency['effect']] > effects[spec.effect]:
                    raise ValueError('Application effect understates a declared driver dependency effect')
        self._tools[spec.name] = (deepcopy(spec), handler)

    def invoke(self, name, arguments, *, cancel=None):
        from .tool_adapter import ToolAdapter
        return ToolAdapter(self._catalog(), self._invoke).invoke(name, arguments, cancel=cancel)

    def _catalog(self):
        from interface_runtime.catalog import render_tool
        tools = []
        for spec, _ in self._tools.values():
            tool = spec.render()
            for driver, operations in sorted(spec.dependencies.items()):
                for operation in sorted(operations):
                    child = render_tool(operation, self.drivers[driver].contract['operations'][operation])
                    tool['description'] += '\nUses '+driver+'.'+operation+': '+child['description']
            tools.append(tool)
        return sorted(tools, key=lambda tool: tool['name'])

    def _invoke(self, name, arguments, *, cancel=None, request_id=None):
        from interface_runtime.contracts import operation_warnings
        spec, handler = self._tools[name]
        context = InvocationContext(self.drivers, spec, cancel, request_id)
        try:
            outputs = handler(context, arguments)
            context.check_cancelled()
            outcome = {'ok': True, 'outputs': outputs}
        except ChildOperationError as error:
            outcome = {'ok': False, 'outputs': {}, 'error': error.result['error']}
        except ApplicationCancelled as error:
            outcome = {'ok': False, 'outputs': {}, 'error': {
                'code': 'cancelled', 'fault': 'operator', 'message': str(error)}}
        except Exception as error:
            outcome = {'ok': False, 'outputs': {}, 'error': {
                'code': 'application_error', 'fault': 'model',
                'message': type(error).__name__+': '+str(error)}}
        return {'schema': 'driver-result/1', 'operation': name, **outcome,
                'units': {key: definition['unit'] for key, definition in spec.outputs.items()},
                'warnings': operation_warnings({'parameters': spec.parameters, 'outputs': spec.outputs,
                    'uncertainties': spec.uncertainties}, 'tools.'+name) +
                    [warning for child in context.calls for warning in child['warnings']],
                'calls': context.calls}

    def serve(self):
        from .device_mcp import serve_tools
        serve_tools(self.name, self._catalog(), self._invoke)

"""One operation of a package, run in a process of its own on the installed runtime.

A package is data, so the tool runs it: a managed session, a generated tool call and a live test each start this
module the same way (docs/adr/0010-a-package-is-data-in-one-format.md). The process that starts it gives it its own
import path, so the child can import what that process can, an optional hardware package among them. This is no
documented command.
"""
import json
import os
import sys

# Read the request, take the caller's import path, then run the operation. The interpreter starts in isolated mode, so
# nothing but that path decides what the child imports.
_BOOT = ('import json,sys; request=json.load(sys.stdin); sys.path[:]=request["path"]; '
         'from interface_runtime.child import main; raise SystemExit(main(request))')


def command():
    """The argument array that starts the child with the interpreter of this process."""
    return [sys.executable, '-I', '-B', '-c', _BOOT]


def request(package, operation, parameters, binding, allow_effects):
    """What the child reads from its standard input: the import path of this process, the package's folder, the
    operation, its parameters, the binding and the effect grants, as one JSON document."""
    return json.dumps({'path': [os.path.abspath(entry) for entry in sys.path], 'package': str(package),
                       'operation': operation, 'parameters': parameters, 'binding': binding,
                       'allow_effects': list(allow_effects)}, allow_nan=False)


def main(request):
    """Run the operation `request` names and print its `driver-result/1`; the exit status is 0 when it succeeded. A
    package or a request the runtime refuses is printed as a failed result with the code `package_error`."""
    from .driver import execute_package
    try:
        result = execute_package(request['package'], request['operation'], request['parameters'],
                                 binding=request['binding'], allow_effects=tuple(request['allow_effects']))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {'ok': False, 'error': {'code': 'package_error', 'message': str(exc)}}
    print(json.dumps(result, allow_nan=False))
    return 0 if result.get('ok', True) else 1

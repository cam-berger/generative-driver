"""The runner of an emitted package's own command line, for the package calls of fresh reuse.

The stage code runs the stage-assigned copy of a package with it. The benchmark's grader has a runner of its own,
which the tool may not import, so this is the tool's copy of what the stage code calls (Feature 05 of the untangle
Run, docs/features/untangle/05-supplied-device-stages.md).
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# A package call runs the identity operation, then the requested one (each up to the 120 s a valid model may declare),
# after a 5 s connect and the process start. A package that outlasts this failed on its own, as one that exits without a
# result does.
PACKAGE_CALL_SECONDS = 300
# How deep a package result may nest objects and arrays, the result itself being the first level. The runtime's own
# results nest at most about 7 levels (a precondition read's transcript entries), and the records of a call nest a
# result a few levels deeper, far within the JSON reader's and writer's reach on every supported Python.
MAX_RESULT_DEPTH = 64
# How much of a print that no runtime writes the result keeps, beside the whole print's length and SHA-256.
PRINTED_PREFIX_CHARACTERS = 4096


def _deeper_than(value, levels):
    """Whether `value` nests objects and arrays more than `levels` deep, `value` itself being the first level. It walks
    the value without recursion, so no nesting can exhaust the stack."""
    pending = [(value, 1)] if isinstance(value, (dict, list)) else []
    while pending:
        item, level = pending.pop()
        if level > levels:
            return True
        pending.extend((child, level + 1) for child in (item.values() if isinstance(item, dict) else item)
                       if isinstance(child, (dict, list)))
    return False


def _malformation(result):
    """What makes a package's parsed result one that no runtime writes, or None: `outputs`, `units` or `error` that is
    not an object, a `transcript` that is not a list of objects, objects or arrays nested more than MAX_RESULT_DEPTH
    levels deep, or a value JSON cannot hold, such as a number that is not finite. The cap keeps every later write of
    the result, which nests it deeper, within the JSON writer's reach on every Python."""
    fields = [field for field in ('outputs', 'units', 'error') if not isinstance(result.get(field, {}), dict)]
    transcript = result.get('transcript', [])
    if not isinstance(transcript, list) or any(not isinstance(entry, dict) for entry in transcript):
        fields.append('transcript')
    if fields:
        return 'a malformed ' + ', '.join(fields)
    if _deeper_than(result, MAX_RESULT_DEPTH):
        return 'objects or arrays nested more than %d levels deep' % MAX_RESULT_DEPTH
    try:
        json.dumps(result, indent=2, allow_nan=False)
    except ValueError as error:
        return 'a value the evidence cannot record: ' + str(error)[:200]
    return None


def _malformed_result(printed, defect):
    """The driver's failure for a package result that no runtime writes. It keeps the print as `printed`: its first
    PRINTED_PREFIX_CHARACTERS characters (`prefix`), its length in characters (`characters`) and the SHA-256 of its
    UTF-8 text as read (`sha256`). The package ran and may have reached the device, so this is never a refusal before
    contact."""
    return {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [], 'transport_open_attempted': True,
            'error': {'fault': 'model', 'code': 'malformed_result', 'message': 'The package result has ' + defect},
            'printed': {'prefix': printed[:PRINTED_PREFIX_CHARACTERS], 'characters': len(printed),
                        'sha256': hashlib.sha256(printed.encode('utf-8')).hexdigest()}}


def package_invoker(package, binding, grants):
    """The call of an emitted package: `invoke(operation, parameters)` runs the package's own command line, isolated
    from the source checkout, over `binding` with `grants`, and returns its result. A call that ends without a result
    is reported as a host fault, never raised: from `package` when the package outlasted the call limit or exited
    without a result, from `package-process` when the process could not start (`spawn`) or a signal killed it
    (`signal`). Only these failures carry a `source`: one the package prints itself is dropped, so that a package
    cannot print its way to that label. A printed object that no runtime writes (`_malformation`), which the honest
    command line never prints, is the driver's `malformed_result`, keeping the start of the print with its length and
    SHA-256 (`_malformed_result`)."""
    def invoke(operation, parameters):
        command = [sys.executable, '-I', '-S', '-B', '-c',
            'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
            str(package), 'execute', operation, '--parameters', json.dumps(parameters),
            '--binding', json.dumps(binding)]
        for effect in grants:
            if effect != 'read':
                command.extend(['--allow-effect', effect])
        failed = lambda source, code, message: {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [],
                                                'error': {'fault': 'host', 'source': source, 'code': code,
                                                          'message': message}}
        try:
            # Output that does not decode is the package's own too: no result, never an exception.
            completed = subprocess.run(command, cwd=Path(package).parent, capture_output=True, text=True,
                                       errors='replace', timeout=PACKAGE_CALL_SECONDS)
        except subprocess.TimeoutExpired:
            return failed('package', 'timeout', f'The package call did not finish within {PACKAGE_CALL_SECONDS} s')
        except OSError as error:
            return failed('package-process', 'spawn', 'The package process could not start: ' + type(error).__name__
                          + ': ' + str(error))
        try:
            result = json.loads(completed.stdout)
        except (ValueError, RecursionError):
            result = None
        if isinstance(result, dict):
            defect = _malformation(result)
            if defect is not None:
                return _malformed_result(completed.stdout, defect)
            error = result.get('error')
            if isinstance(error, dict) and 'source' in error:
                result = {**result, 'error': {key: value for key, value in error.items() if key != 'source'}}
            return result
        if completed.returncode < 0:
            # The runner's own kill, at the call limit, ends in TimeoutExpired above.
            return failed('package-process', 'signal',
                          'The package process was killed by signal %d' % -completed.returncode)
        return failed('package', 'no_result', 'The package returned no runtime result (exit %d)' % completed.returncode)
    return invoke

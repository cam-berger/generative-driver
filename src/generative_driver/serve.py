"""`generative-driver serve PACKAGE --binding JSON`, the one way a package is served to an agent
(docs/adr/0010-a-package-is-data-in-one-format.md; docs/features/package-format/03-package.md, Behaviours 03-B10 to
03-B12).

The command checks the package, opens a managed session through the service and serves the package's tools over MCP on
its standard input and output. Every call runs through that session, so the service keeps exclusive device access, the
saved result of each call and the record of an uncertain effect. The command closes its session when it ends; a
session it may not close it leaves to the operator and names on standard error."""
import contextlib
import json
import os
import signal
import sys
import threading
from pathlib import Path


def _say(text):
    """Write one line of the command's own to standard error. Standard output carries MCP and nothing else."""
    with contextlib.suppress(OSError, ValueError):
        sys.stderr.write('generative-driver serve: ' + text + '\n')
        sys.stderr.flush()


class _Refused(Exception):
    """The reason why the command does not serve."""


def _session(package, binding, effects, home):
    """A managed session on `package`, opened with `binding` (JSON text) and the read plus `effects`, with the model
    of the package's driver: (the session's client, the model). Raises `_Refused` when the package does not pass the
    check, the binding is not JSON or does not fit the driver's channel, or the service refuses the session."""
    from interface_runtime.package import the_driver, verify_package
    from interface_runtime.transports import TransportError, validate_binding
    from ._toolchain.interface import refusal_text
    from .sdk import ManagedDriver
    checked = verify_package(package)
    if not checked['ok']:
        raise _Refused(refusal_text(checked))
    model = the_driver(checked)[1]
    try:
        bound = json.loads(binding)
    except ValueError as error:
        raise _Refused('the binding is not JSON: ' + str(error)) from None
    try:
        validate_binding(model['channel'], bound)
    except TransportError as error:
        raise _Refused('the binding does not fit the channel of this driver: ' + str(error)) from None
    try:
        return ManagedDriver.open(package, binding=bound, effects=['read', *effects], home=home), model
    except (OSError, RuntimeError, ValueError) as error:
        raise _Refused(str(error)) from None


def _tools(driver):
    """The tools the session of `driver` serves, as the service describes them: those of the copy of the package the
    session keeps. Raises `_Refused` when the service does not describe the session."""
    try:
        described = driver.describe()
    except (OSError, RuntimeError, ValueError) as error:
        raise _Refused('the service did not describe the session: ' + str(error)) from None
    if not described.get('ok') or type(described.get('catalog')) is not dict:
        raise _Refused('the service did not describe the session: '
                       + str(described.get('reason', 'its answer names no tools')))
    return described['catalog']


def _missing_grant(operation, effect, resets, effects):
    """What to say of a call the command was not started to permit, or None when it may run: `operation` has an
    `effect` other than a read that is not among `effects`, or the channel resets the device when it opens and
    `reset` is not among them."""
    if effect != 'read' and effect not in effects:
        needed, why = effect, 'Operation ' + operation + ' has the effect ' + effect
    elif resets and 'reset' not in effects:
        needed, why = 'reset', 'This channel resets the device when it opens'
    else:
        return None
    return why + '; start the command with --allow-effect ' + needed + ' to permit it'


def _close(driver):
    """The exit status after one attempt to close the session of `driver`: 0 when it is closed. A close the service
    refuses is said on standard error with the session's id and what holds it, read from the session's status, and
    the session stays as it is for the operator."""
    closed = driver.close()
    if closed.get('ok') and closed.get('status') == 'closed':
        return 0
    status = driver.status()
    held = [text for key, text in (('in_flight', 'an invocation is still active'),
                                   ('uncertain_effect', 'an effect is uncertain, which the operator resolves'))
            if status.get(key)]
    _say('session ' + driver.session_id + ' stays open: '
         + (' and '.join(held) or str(closed.get('reason', 'the service did not close it'))))
    return 1


def serve(package, binding, effects=(), *, home=None):
    """The exit status of serving `package` over MCP on standard input and output until the client closes the input
    or, on macOS and Linux, the command gets SIGINT or SIGTERM: 0 when the session it opened is closed, 1 when it
    refused to serve or left its session open. `binding` is the connection to the device as JSON text and `effects`
    the effects beside the read that calls may have. A call that needs another effect is answered with a failed
    `driver-result/1` that names the missing grant, and never reaches the session.

    The session's id is written to standard error as soon as the session is open. Whatever ends the command after
    that, the same one attempt to close the session is made. A service that does not describe the session's tools is
    said on standard error, and the status is 1. An error of the server itself, such as a reply it cannot write
    because the client is gone, is raised after the attempt."""
    from .device_mcp import serve_tools
    from .tool_adapter import failure
    try:
        driver, model = _session(Path(package).expanduser().resolve(), binding, effects, home)
    except _Refused as refusal:
        _say(str(refusal))
        return 1
    running, idle = [], threading.Condition()

    def on_signal(number, frame):
        # The server cannot end while it waits for input, so the one attempt to close is made here.
        for each in (signal.SIGINT, signal.SIGTERM):
            signal.signal(each, signal.SIG_IGN)
        os._exit(_close(driver))
    refused = False
    try:
        _say('session ' + driver.session_id + ' is open')
        if os.name != 'nt':
            for each in (signal.SIGINT, signal.SIGTERM):
                signal.signal(each, on_signal)
        catalog = _tools(driver)
        resets = bool(model['channel'].get('resets_on_open'))

        def invoke(name, arguments, *, cancel=None, request_id=None):
            operation = catalog['operations'][name]['operation']
            missing = _missing_grant(operation, driver.contract['operations'][operation]['effect'], resets, effects)
            if missing is not None:
                return {**failure(operation, 'effect', missing),
                        'requested_command_dispatched': False, 'transport_open_attempted': False}
            with idle:
                running.append(request_id)
            try:
                return driver.invoke(operation, arguments, request_id=request_id, cancel=cancel)
            finally:
                with idle:
                    running.remove(request_id)
                    idle.notify_all()
        serve_tools(catalog['server'], catalog['tools'], invoke)
    except _Refused as refusal:
        _say(str(refusal))
        refused = True
    finally:
        # Also when the server ends with an error, as it does when the client is gone and a reply cannot be written.
        with idle:
            idle.wait_for(lambda: not running)
        if os.name != 'nt':
            for each in (signal.SIGINT, signal.SIGTERM):
                signal.signal(each, signal.SIG_IGN)
        status = _close(driver)
    return 1 if refused else status

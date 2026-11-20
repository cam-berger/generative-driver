"""Native IPC forwarding to an in-process controller; no daemon or inference."""
from contextlib import contextmanager
import itertools
import json
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import tempfile
import threading

_PIDS = itertools.count(70001)


def service_pid(controller):
    """The process id an in-process configurator answers `ping` with: one for each `Controller` object, so that a
    second one on the same home reads as a restart of the service, as another daemon process would."""
    if not hasattr(controller, '_test_pid'):
        controller._test_pid = next(_PIDS)
    return controller._test_pid


@contextmanager
def owner_ipc(controller):
    """Serve `controller` over native IPC at its home, as the daemon serves one: `ping` is answered here, with the
    code this process runs and the controller's `service_pid`, and a refusal carries its `error`."""
    from generative_driver.client import code_identity
    with tempfile.TemporaryDirectory(prefix='gd-test-ipc-') as directory:
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        address = r'\\.\pipe\gd-'+secrets.token_hex(8) if os.name == 'nt' else str(Path(directory)/'owner.sock')
        key = secrets.token_bytes(32)
        requests = []
        with Listener(address, family=family, authkey=key) as listener:
            (controller.home/'service.json').write_text(json.dumps({
                'family': family, 'address': address, 'authkey': key.hex(), 'code': code_identity()}))

            def dispatch(connection, request):
                with connection:
                    try:
                        if request['method'] == 'ping':
                            result = {'ok': True, 'pid': service_pid(controller), 'code': code_identity()}
                        else:
                            result = controller.call(request['method'], request.get('params', {}))
                    except Exception as error:
                        result = {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
                    connection.send_bytes(json.dumps(result, allow_nan=False).encode())

            def serve():
                while True:
                    connection = listener.accept()
                    request = json.loads(connection.recv_bytes())
                    if request['method'] == '_fixture_stop':
                        connection.close()
                        return
                    thread = threading.Thread(target=dispatch, args=(connection, request), daemon=True)
                    requests.append(thread)
                    thread.start()

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                yield
            finally:
                with Client(address, family=family, authkey=key) as connection:
                    connection.send_bytes(b'{"method":"_fixture_stop"}')
                thread.join(10)
                for request in requests:
                    request.join(10)

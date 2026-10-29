"""Native IPC forwarding to an in-process controller; no daemon or inference."""
from contextlib import contextmanager
import json
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import tempfile
import threading


@contextmanager
def owner_ipc(controller):
    with tempfile.TemporaryDirectory(prefix='gd-test-ipc-') as directory:
        family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
        address = r'\\.\pipe\gd-'+secrets.token_hex(8) if os.name == 'nt' else str(Path(directory)/'owner.sock')
        key = secrets.token_bytes(32)
        requests = []
        with Listener(address, family=family, authkey=key) as listener:
            (controller.home/'service.json').write_text(json.dumps({
                'family': family, 'address': address, 'authkey': key.hex()}))

            def dispatch(connection, request):
                with connection:
                    try:
                        result = controller.call(request['method'], request.get('params', {}))
                    except Exception as error:
                        result = {'ok': False, 'reason': str(error)}
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

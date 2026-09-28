"""Native background service: Unix socket on macOS, named pipe on Windows."""
import argparse
import json
from multiprocessing.connection import Listener
from multiprocessing import AuthenticationError
import os
from pathlib import Path
import threading

from .configurator import Controller


def serve(home):
    home = Path(home).resolve()
    endpoint = json.loads((home / 'service.json').read_text(encoding='utf-8'))
    controller = Controller(home)
    listener = Listener(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey']))
    ordinary_slots = threading.BoundedSemaphore(24)
    tool_slots = threading.BoundedSemaphore(8)
    if os.name != 'nt':
        Path(endpoint['address']).chmod(0o600)
    def dispatch(conn, method, params, slot):
        try:
            with conn:
                try:
                    result = controller.call(method, params)
                except Exception as exc:
                    result = {'ok':False,'error':type(exc).__name__,'reason':str(exc)}
                try:
                    conn.send_bytes(json.dumps(result,allow_nan=False).encode())
                except (OSError,EOFError):
                    pass
        finally:
            slot.release()
    try:
        while True:
            try:
                conn = listener.accept()
            except (OSError, EOFError, AuthenticationError):
                continue
            try:
                shutdown = False
                try:
                    if not conn.poll(10):
                        continue
                    request = json.loads(conn.recv_bytes(2 * 1024 * 1024))
                    method, params = request['method'], request.get('params', {})
                    if method == 'ping':
                        result = {'ok': True, 'pid': os.getpid()}
                    elif method == 'shutdown':
                        controller.close()
                        result, shutdown = {'ok': True, 'stopped': True}, True
                    else:
                        slot = tool_slots if method == 'tool' else ordinary_slots
                        if not slot.acquire(blocking=False):
                            raise RuntimeError('Configurator request capacity reached; retry later')
                        threading.Thread(target=dispatch,args=(conn,method,params,slot),daemon=True).start()
                        conn = None
                        continue
                except Exception as exc:
                    result = {'ok': False, 'error': type(exc).__name__, 'reason': str(exc)}
                try:
                    conn.send_bytes(json.dumps(result, allow_nan=False).encode())
                except (OSError, EOFError):
                    pass
                if shutdown:
                    break
            finally:
                if conn is not None:
                    conn.close()
    finally:
        controller.close()
        listener.close()
        if endpoint['family'] == 'AF_UNIX':
            Path(endpoint['address']).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    serve(parser.parse_args().home)


if __name__ == '__main__':
    main()

"""Native background service: Unix socket on macOS, named pipe on Windows."""
import argparse
import json
from multiprocessing.connection import Listener
from multiprocessing import AuthenticationError
import os
import sys
from pathlib import Path
import threading
import time

from .client import SERVICE_LOCK, code_identity, hold_process_lock, process_lock_held
from .configurator import Controller


def _hold_home(home):
    """The lock this configurator holds on its home for as long as it runs, taken before the starting client publishes
    service.json: by it clients tell that the configurator still runs, which its process id cannot tell once it ended.
    None on a file system without such locks. A home another running configurator holds is refused before anything of
    it is read: starting there would recover that configurator's runs as though it had stopped."""
    path = Path(home).resolve() / SERVICE_LOCK
    deadline = time.monotonic() + 2
    while True:
        lock = hold_process_lock(path)
        if lock is not None or process_lock_held(path) is None:
            return lock
        if time.monotonic() >= deadline:
            raise SystemExit('Another configurator runs from this home: it holds ' + str(path))
        time.sleep(.01)  # A client may be testing the lock just now; a running configurator holds it for good.


def serve(home):
    home = Path(home).resolve()
    endpoint = json.loads((home / 'service.json').read_text(encoding='utf-8'))
    if endpoint.get('code') != code_identity():
        # The starting client loaded other code than this process imports: serving would mislabel every reply.
        raise RuntimeError('service.json records other generative-driver code than this configurator runs; '
                           'start the service again from one installation')
    controller = Controller(home)
    # Queue concurrent worker, harness and UI handshakes rather than refusing a
    # burst before the ordinary/tool capacity limits can handle the requests.
    listener = Listener(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey']), backlog=64)
    ordinary_slots = threading.BoundedSemaphore(24)
    tool_slots = threading.BoundedSemaphore(8)
    requests = set()
    requests_lock = threading.Lock()
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
            with requests_lock:
                requests.discard(threading.current_thread())
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
                        result = {'ok': True, 'pid': os.getpid(), 'code': code_identity()}
                    elif method == 'shutdown':
                        deadline = time.monotonic() + 20
                        settled = controller.close(timeout=10)
                        with requests_lock:
                            pending = list(requests)
                        for thread in pending:
                            thread.join(max(0, deadline - time.monotonic()))
                        shutdown = settled and not any(thread.is_alive() for thread in pending)
                        result = {'ok': shutdown, 'stopped': shutdown, 'pid': os.getpid()}
                        if not shutdown:
                            result['reason'] = 'Shutdown is pending: a worker or accepted operation is still stopping; retry shutdown after it finishes'
                    else:
                        slot = tool_slots if method == 'tool' else ordinary_slots
                        if not slot.acquire(blocking=False):
                            raise RuntimeError('Configurator request capacity reached; retry later')
                        thread = threading.Thread(target=dispatch,args=(conn,method,params,slot),daemon=True)
                        with requests_lock:
                            requests.add(thread)
                        thread.start()
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
    parser.add_argument('--wait-for-startup', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    lock = _hold_home(args.home)  # Never let go of: the system does when this process ends, however it ends.
    if args.wait_for_startup and sys.stdin.buffer.read(1) != b'1':
        parser.exit(1, 'Startup cancelled before endpoint publication\n')
    serve(args.home)


if __name__ == '__main__':
    main()

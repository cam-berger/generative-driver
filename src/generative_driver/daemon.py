"""Native background service: Unix socket on macOS, named pipe on Windows."""
import argparse
import contextlib
import json
from multiprocessing.connection import Client, Listener
from multiprocessing import AuthenticationError
import os
import sys
from pathlib import Path
import threading
import time

from ._ipc import accept_handshake
from .client import SERVICE_LOCK, code_identity, hold_process_lock, process_lock_held
from .configurator import Controller

HANDSHAKE_TIMEOUT = 10


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


def _named(method, params):
    """What a log entry may name of a request: its method, its run and, for a gateway call, its tool. Nothing else of
    the request's parameters reaches the log."""
    params = params if isinstance(params, dict) else {}
    return {'method': method if isinstance(method, str) else 'request', 'run_id': params.get('run_id'),
            'tool': params.get('name') if method in ('tool', 'mcp_tool') else None}


def serve(home):
    home = Path(home).resolve()
    endpoint = json.loads((home / 'service.json').read_text(encoding='utf-8'))
    if endpoint.get('code') != code_identity():
        # The starting client loaded other code than this process imports: serving would mislabel every reply.
        raise RuntimeError('service.json records other generative-driver code than this configurator runs; '
                           'start the service again from one installation')
    controller = Controller(home, for_service=True)
    log = controller.log
    authkey = bytes.fromhex(endpoint['authkey'])
    # Accept raw connections without inline challenge so a stalled peer handshake can never block the accept loop;
    # each connection runs its bounded authkey handshake on its own handler thread.
    listener = Listener(endpoint['address'], family=endpoint['family'], authkey=None, backlog=64)
    handshake_slots = threading.BoundedSemaphore(64)
    ordinary_slots = threading.BoundedSemaphore(24)
    tool_slots = threading.BoundedSemaphore(8)
    requests = set()
    requests_lock = threading.Lock()
    shutdown_lock = threading.Lock()
    shutdown_done = threading.Event()
    if os.name != 'nt':
        Path(endpoint['address']).chmod(0o600)

    def _wake_listener():
        with contextlib.suppress(OSError, EOFError, AuthenticationError):
            Client(endpoint['address'], family=endpoint['family'], authkey=None).close()

    def handle(conn):
        handshake_held = True
        slot = None
        tracked = False
        trigger_shutdown = False
        try:
            with conn:
                if shutdown_done.is_set():
                    return
                deadline = time.monotonic() + HANDSHAKE_TIMEOUT
                try:
                    timed = accept_handshake(conn, endpoint['family'], authkey, deadline)
                    raw = timed.recv_bytes(2 * 1024 * 1024)
                except (OSError, EOFError, ValueError, AuthenticationError):
                    return
                finally:
                    if handshake_held:
                        handshake_slots.release()
                        handshake_held = False
                method = params = None
                try:
                    request = json.loads(raw)
                    method, params = request['method'], request.get('params', {})
                    if method == 'ping':
                        result = {'ok': True, 'pid': os.getpid(), 'code': code_identity()}
                    elif method == 'shutdown':
                        with shutdown_lock:
                            stop_deadline = time.monotonic() + 20
                            settled = controller.close(timeout=10)
                            with requests_lock:
                                pending = [t for t in requests if t is not threading.current_thread()]
                            for thread in pending:
                                if thread.is_alive():
                                    thread.join(max(0, stop_deadline - time.monotonic()))
                            stopped = settled and not any(thread.is_alive() for thread in pending)
                            result = {'ok': stopped, 'stopped': stopped, 'pid': os.getpid()}
                            if not stopped:
                                result['reason'] = 'Shutdown is pending: a worker or accepted operation is still stopping; retry shutdown after it finishes'
                            if stopped:
                                shutdown_done.set()
                                trigger_shutdown = True
                    else:
                        chosen = tool_slots if method in ('tool', 'device_execute', 'mcp_tool') else ordinary_slots
                        if not chosen.acquire(blocking=False):
                            raise RuntimeError('Configurator request capacity reached; retry later')
                        slot = chosen
                        with requests_lock:
                            requests.add(threading.current_thread())
                        tracked = True
                        result = controller.call(method, params)
                except Exception as exc:
                    log.replied(exc, **_named(method, params))
                    result = {'ok': False, 'error': type(exc).__name__, 'reason': str(exc)}
                with contextlib.suppress(OSError, EOFError):
                    conn.send_bytes(json.dumps(result, allow_nan=False).encode())
        finally:
            if handshake_held:
                handshake_slots.release()
            if slot is not None:
                slot.release()
            if tracked:
                with requests_lock:
                    requests.discard(threading.current_thread())
            if trigger_shutdown:
                threading.Thread(target=_wake_listener, daemon=True).start()

    try:
        while not shutdown_done.is_set():
            try:
                conn = listener.accept()
            except (OSError, EOFError, AuthenticationError):
                if shutdown_done.is_set():
                    break
                continue
            if shutdown_done.is_set():
                with contextlib.suppress(OSError):
                    conn.close()
                break
            if not handshake_slots.acquire(blocking=False):
                with contextlib.suppress(OSError):
                    conn.close()
                continue
            thread = threading.Thread(target=handle, args=(conn,), daemon=True)
            try:
                thread.start()
            except BaseException as exc:
                handshake_slots.release()
                with contextlib.suppress(OSError):
                    conn.close()
                log.replied(exc, method='accept')
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
    lock = _hold_home(args.home)  # Held while this process serves; it goes as the process ends, however that ends.
    if args.wait_for_startup and sys.stdin.buffer.read(1) != b'1':
        parser.exit(1, 'Startup cancelled before endpoint publication\n')
    serve(args.home)


if __name__ == '__main__':
    main()

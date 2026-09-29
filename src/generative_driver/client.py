"""Short-lived clients reconnect to one local owner over private native IPC."""
from contextlib import suppress
import hashlib
import json
from multiprocessing.connection import Client
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import threading


def _wait_for_exit(pid, timeout=10):
    """Observe process exit without terminating it or inferring it from IPC closure."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            if ctypes.get_last_error() == 87:  # Process already gone.
                return True
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
            if result == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 0
        finally:
            kernel.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while True:
        # A daemon started in this client is reaped by its process.wait thread.
        # Reconnected clients may observe an orphan and cannot waitpid it.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(.01, remaining))


def _confirmed_shutdown(pid, timeout=10):
    if type(pid) is not int or pid <= 0:
        return {'ok': False, 'stopped': False, 'reason': 'Daemon exit cannot be confirmed without its process identity'}
    try:
        stopped = _wait_for_exit(pid, timeout)
    except OSError as exc:
        return {'ok': False, 'stopped': False, 'reason': 'Daemon exit verification failed: ' + str(exc)}
    return {'ok': stopped, 'stopped': stopped, 'pid': pid,
            **({} if stopped else {'reason': 'Daemon exit was not confirmed before the shutdown deadline'})}


def default_home():
    if os.environ.get('GENERATIVE_DRIVER_HOME'):
        return Path(os.environ['GENERATIVE_DRIVER_HOME']).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'GenerativeDriver'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/GenerativeDriver'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'generative-driver'


def _request(home, method, params):
    endpoint = json.loads((home / 'service.json').read_text(encoding='utf-8'))
    with Client(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey'])) as conn:
        conn.send_bytes(json.dumps({'method': method, 'params': params}).encode('utf-8'))
        timeout = 620 if method == 'tool' else 30
        if not conn.poll(timeout):
            raise TimeoutError('Configurator request timed out')
        return json.loads(conn.recv_bytes(4 * 1024 * 1024))


def _start(home):
    lock = home / 'startup.lock'
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            try:
                if _request(home, 'ping', {}).get('ok'):
                    return
            except (OSError, EOFError, ValueError):
                pass
            if time.monotonic() > deadline:
                if time.time() - lock.stat().st_mtime > 60:
                    shutil.rmtree(lock)
                    continue
                raise TimeoutError('Another client is starting the configurator')
            time.sleep(.1)
    try:
        try:
            if _request(home, 'ping', {}).get('ok'):
                return
        except (OSError, EOFError, ValueError):
            pass
        identity = hashlib.sha256(str(home).encode()).hexdigest()[:20]
        if os.name == 'nt':
            family, address = 'AF_PIPE', r'\\.\pipe\generative-driver-' + identity
        else:
            family = 'AF_UNIX'
            ipc_dir = Path(tempfile.gettempdir()) / ('gd-' + identity)
            ipc_dir.mkdir(mode=0o700, exist_ok=True)
            address = str(ipc_dir / 'owner.sock')
            Path(address).unlink(missing_ok=True)
        endpoint = {'family': family, 'address': address, 'authkey': secrets.token_hex(32)}
        path = home / 'service.json'
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        with (home / 'daemon.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'generative_driver.daemon', '--home', str(home), '--wait-for-startup'],
                             cwd=home, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, **options)
        threading.Thread(target=process.wait, daemon=True).start()
        try:
            # Publish once, with the child PID already known. The child waits
            # until this writer closes; it never renames a file readers hold.
            endpoint['pid'] = process.pid
            path.write_text(json.dumps(endpoint), encoding='utf-8')
            if os.name != 'nt':
                path.chmod(0o600)
            process.stdin.write(b'1')
            process.stdin.flush()
        except BaseException:
            with suppress(OSError):
                process.stdin.close()  # EOF cancels an unpublished startup.
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            raise
        else:
            process.stdin.close()
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'Configurator exited during startup (exit {process.returncode}); inspect ' + str(home / 'daemon.log'))
            try:
                if _request(home, 'ping', {}).get('ok'):
                    return
            except (OSError, EOFError, ValueError):
                time.sleep(.05)
        raise TimeoutError('Configurator failed to start; inspect ' + str(home / 'daemon.log'))
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def call(method, params=None, home=None, *, autostart=True):
    """Send one JSON request; starting or disconnecting a UI never owns a run."""
    directory = Path(home or default_home()).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    params = params or {}
    try:
        result = _request(directory, method, params)
    except (OSError, EOFError, ValueError):
        if method == 'shutdown':
            try:
                endpoint = json.loads((directory / 'service.json').read_text(encoding='utf-8'))
            except FileNotFoundError:
                return {'ok': True, 'stopped': True}
            except (OSError, ValueError) as exc:
                return {'ok': False, 'stopped': False, 'reason': 'Cannot confirm daemon identity: ' + str(exc)}
            return _confirmed_shutdown(endpoint.get('pid'), timeout=0)
        if not autostart:
            return {'ok': False, 'reason': 'Configurator is not reachable. Run generative-driver service start from an operator terminal, then reconnect this UI.'}
        _start(directory)
        return _request(directory, method, params)
    if method == 'shutdown' and result.get('stopped'):
        return _confirmed_shutdown(result.get('pid'))
    return result

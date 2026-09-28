"""Short-lived clients reconnect to one local owner over private native IPC."""
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
        path.write_text(json.dumps(endpoint), encoding='utf-8')
        if os.name != 'nt':
            path.chmod(0o600)
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        options = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        with (home / 'daemon.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'generative_driver.daemon', '--home', str(home)],
                             cwd=home, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        threading.Thread(target=process.wait, daemon=True).start()
        while time.monotonic() < deadline:
            try:
                if _request(home, 'ping', {}).get('ok'):
                    return
            except (OSError, EOFError, ValueError):
                time.sleep(.05)
        raise TimeoutError('Configurator failed to start; inspect ' + str(home / 'daemon.log'))
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def call(method, params=None, home=None):
    """Send one JSON request; starting or disconnecting a UI never owns a run."""
    directory = Path(home or default_home()).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    params = params or {}
    try:
        return _request(directory, method, params)
    except (OSError, EOFError, ValueError):
        if method == 'shutdown':
            return {'ok': True, 'stopped': True}
        _start(directory)
        return _request(directory, method, params)

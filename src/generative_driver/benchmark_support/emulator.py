"""Evaluator-owned Renode process with a separate monitor observation channel.

The recipe and target register addresses come from encrypted case evidence.
Workers receive only the UART socket binding. TCP does not validate serial baud.
"""
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _path(value):
    text = str(Path(value).resolve()).replace('\\', '/')
    if any(x in text for x in ('"', '\n', '\r')):
        raise ValueError('Unsupported character in emulator path')
    return '@' + text.replace(' ', '\\ ')


class RenodeSession:
    def __init__(self, info, process=None):
        self.info, self.process = info, process

    @classmethod
    def start(cls, executable, image, truth, timeout=60):
        if not executable or not Path(executable).is_file():
            raise ValueError('A native Renode executable is required for the emulation profile')
        private = Path(tempfile.mkdtemp(prefix='gd-evaluator-'))
        private.chmod(0o700)
        model = private / 'sensor.cs'
        model.write_text(truth['source_files']['BME280Bench.cs'])
        script = private / 'device.resc'
        script.write_text(truth['renode_setup'])
        uart, monitor = _free_port(), _free_port()
        log_path = private / 'renode.log'
        with log_path.open('w') as log:
            process = subprocess.Popen([str(executable), '--disable-gui', '--port', str(monitor),
                '-e', f'$bin={_path(image)}; $uart={uart}; include {_path(model)}; include {_path(script)}'],
                cwd=str(Path(executable).parent), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        info = {'pid': process.pid, 'monitor_port': monitor, 'uart_port': uart,
                'private_dir': str(private), 'log': str(log_path), 'binding': {'host': '127.0.0.1', 'port': uart},
                'image_sha256': __import__('hashlib').sha256(Path(image).read_bytes()).hexdigest()}
        session = cls(info, process)
        deadline = time.monotonic() + timeout
        monitor_connection = None
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Renode exited during startup: ' + log_path.read_text(errors='replace')[-4000:])
                if monitor_connection is None:
                    try:
                        monitor_connection = socket.create_connection(('127.0.0.1', monitor), timeout=.2)
                        monitor_connection.settimeout(.05)
                        monitor_connection.sendall(b'\xff\xfb\x00\xff\xfd\x01\xff\xfd\x03')
                    except OSError:
                        pass
                elif monitor_connection is not None:
                    try:
                        received = monitor_connection.recv(65536)
                        with (private / 'monitor-startup.log').open('ab') as monitor_log:
                            monitor_log.write(received)
                    except socket.timeout:
                        pass
                if 'Machine started' in log_path.read_text(errors='replace'):
                    with socket.create_connection(('127.0.0.1', uart), timeout=1):
                        pass
                    # Renode has loaded these evaluator sources; they need not remain on disk.
                    model.unlink()
                    script.unlink()
                    if monitor_connection:
                        monitor_connection.close()
                    return session
                time.sleep(.1)
            raise RuntimeError('Renode startup timed out: ' + log_path.read_text(errors='replace')[-4000:])
        except BaseException:
            if monitor_connection:
                monitor_connection.close()
            session.stop()
            raise

    def monitor(self, command, timeout=5):
        if '\n' in command or '\r' in command:
            raise ValueError('Monitor observation must be one command')
        with socket.create_connection(('127.0.0.1', self.info['monitor_port']), timeout=timeout) as sock:
            sock.settimeout(.1)
            sock.sendall(b'\xff\xfb\x00\xff\xfd\x01\xff\xfd\x03')
            try:
                while sock.recv(65536):
                    pass
            except socket.timeout:
                pass
            sock.sendall(command.encode() + b'\n')
            data, deadline = b'', time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    if data and '(device)' in data.decode(errors='replace'):
                        break
                    continue
                if not chunk:
                    break
                data += chunk
            return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', data.decode(errors='replace'))

    def read_u32(self, address):
        command = f'sysbus ReadDoubleWord 0x{int(address):08x}'
        response = self.monitor(command)
        tail = response.split(command, 1)[-1]
        values = re.findall(r'0x[0-9a-fA-F]{1,8}', tail)
        if not values:
            raise RuntimeError('Independent monitor returned no register value')
        return int(values[0], 16)

    def observe(self, truth):
        registers = truth['monitor']
        compare = self.read_u32(registers['pwm_compare'])
        reload = self.read_u32(registers['pwm_reload'])
        return {'duty': compare / (reload + 1), 'compare': compare, 'reload': reload,
                'channel': 'Renode monitor register observation', 'physical': False, 'time': time.time()}

    def stop(self):
        try:
            self.monitor('quit', timeout=1)
        except OSError:
            pass
        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        private = Path(self.info['private_dir'])
        for name in ('sensor.cs', 'device.resc'):
            (private / name).unlink(missing_ok=True)


def truth_for_case(case_root, manifest, options):
    from .truth import unlock
    password_file = options.get('evaluator_password_file')
    if not password_file:
        raise ValueError('Evaluator password file is required; candidate workers must not receive it')
    password = Path(password_file).read_text().strip()
    return unlock(Path(case_root) / manifest['truth']['path'], password, manifest['truth']['sha256'])

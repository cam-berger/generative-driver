"""Evaluator-only native controls. Only ``binding`` may reach a candidate."""
import copy
import re
import socket
import hashlib
import subprocess
import tempfile
import time
from pathlib import Path


def _command(value, *, template=False):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\n\r;\x00'):
        raise ValueError('Monitor command must be one nonempty command')
    remaining = value
    if template:
        if value.count('{value}') != 1:
            raise ValueError('Invalid stimulus command template')
        remaining = value.replace('{value}', '')
    if '{' in remaining or '}' in remaining:
        raise ValueError('Invalid monitor command template')
    return value


def _validate_recipe(recipe):
    if not isinstance(recipe, dict):
        raise ValueError('Recipe must be an object')
    for field in ('stimuli', 'observations', 'source_files'):
        if not isinstance(recipe.get(field, {}), dict):
            raise ValueError('Invalid recipe ' + field)
    for name, content in recipe.get('source_files', {}).items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(cs|resc|repl)', name)
                or not isinstance(content, str)):
            raise ValueError('Invalid recipe source filename or content')
    for spec in recipe.get('stimuli', {}).values():
        if (not isinstance(spec, dict) or type(spec.get('minimum')) is not int
                or type(spec.get('maximum')) is not int or spec['minimum'] > spec['maximum']):
            raise ValueError('Invalid stimulus integer bounds')
        _command(spec.get('command'), template=True)
    for spec in recipe.get('observations', {}).values():
        if (not isinstance(spec, dict) or type(spec.get('width')) is not int
                or spec['width'] not in (8, 16, 32, 64) or type(spec.get('count')) is not int
                or not 1 <= spec['count'] <= 4096):
            raise ValueError('Invalid observation width/count')
        _command(spec.get('command'))
    if not isinstance(recipe.get('reset_commands', []), list):
        raise ValueError('Invalid reset commands')
    for command in recipe.get('reset_commands', []):
        _command(command)


def render_stimuli(recipe, values):
    _validate_recipe(recipe)
    if not isinstance(values, dict) or not set(values) <= set(recipe.get('stimuli', {})):
        raise ValueError('Unknown stimulus')
    rendered = []
    for name, value in values.items():
        spec = recipe['stimuli'][name]
        if type(value) is not int or not spec['minimum'] <= value <= spec['maximum']:
            raise ValueError('Stimulus outside integer bounds')
        rendered.append(spec['command'].replace('{value}', str(value)))
    return rendered


class NativeSession:
    def __init__(self, info, recipe):
        _validate_recipe(recipe)
        self.info = copy.deepcopy(info)
        self.recipe = copy.deepcopy(recipe)
        self.process = None

    @classmethod
    def start(cls, *, renode, image, recipe, timeout_seconds=60.0):
        from .emulator import _free_port, _path
        _validate_recipe(recipe)
        renode, image = Path(renode).resolve(), Path(image).resolve()
        if not renode.is_file() or not image.is_file():
            raise ValueError('Native Renode executable and image are required')
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 600:
            raise ValueError('Invalid native startup timeout')
        sources = recipe.get('source_files', {})
        setup, includes = recipe.get('setup'), recipe.get('includes', [])
        if (not isinstance(setup, str) or setup not in sources or not setup.endswith('.resc')
                or not isinstance(includes, list) or any(not isinstance(n, str) or n not in sources for n in includes)):
            raise ValueError('Invalid native setup sources')
        # Validate paths before creating any ephemeral plaintext or process.
        _path(image)
        private = Path(tempfile.mkdtemp(prefix='gd-native-evaluator-'))
        private.chmod(0o700)
        session = None
        try:
            for name, content in sources.items():
                (private / name).write_text(content, encoding='utf-8')
            uart, monitor = _free_port(), _free_port()
            log_path = private / 'renode.log'
            script = f'$bin={_path(image)}; $uart={uart}; '
            script += '; '.join('include ' + _path(private / name) for name in [*includes, setup])
            info = {'monitor_port': monitor, 'binding': {'host': '127.0.0.1', 'port': uart},
                    'private_dir': str(private), 'log': str(log_path),
                    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                    'executable': str(renode)}
            session = cls(info, recipe)
            argv = [str(renode), '--disable-gui', '--port', str(monitor), '-e', script]
            with log_path.open('w') as log:
                session.process = subprocess.Popen(argv, cwd=renode.parent, stdin=subprocess.DEVNULL,
                                                   stdout=log, stderr=subprocess.STDOUT)
            session.info.update(pid=session.process.pid, argv=argv)
            deadline = time.monotonic() + timeout_seconds
            connection = None
            try:
                while time.monotonic() < deadline:
                    if session.process.poll() is not None:
                        raise RuntimeError('Host fault: Renode exited during startup; see ' + str(log_path))
                    if connection is None:
                        try:
                            connection = socket.create_connection(('127.0.0.1', monitor), timeout=.2)
                            connection.settimeout(.05)
                            connection.sendall(b'\xff\xfb\x00\xff\xfd\x01\xff\xfd\x03')
                        except OSError:
                            if connection:
                                connection.close()
                            connection = None
                    else:
                        try:
                            connection.recv(65536)
                        except socket.timeout:
                            pass
                    if 'Machine started' in log_path.read_text(errors='replace'):
                        with socket.create_connection(('127.0.0.1', uart), timeout=1):
                            pass
                        connection.close()
                        connection = None
                        session.set_running(False)
                        return session
                    time.sleep(.05)
                raise RuntimeError('Host fault: Renode startup timed out; see ' + str(log_path))
            finally:
                if connection:
                    connection.close()
        except BaseException:
            if session:
                session.stop()
            raise
        finally:
            for name in sources:
                (private / name).unlink(missing_ok=True)

    def stop(self):
        # Attached observers have no process handle and cannot terminate a process by PID.
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    @classmethod
    def attach(cls, info, recipe):
        return cls(info, recipe)

    @property
    def binding(self):
        return {key: self.info['binding'][key] for key in ('host', 'port')}

    def _monitor(self, command):
        _command(command)
        timeout = self.info.get('monitor_timeout_seconds', 5.0)
        try:
            with socket.create_connection(('127.0.0.1', self.info['monitor_port']), timeout=timeout) as sock:
                sock.settimeout(min(.1, timeout))
                sock.sendall(b'\xff\xfb\x00\xff\xfd\x01\xff\xfd\x03')
                deadline = time.monotonic() + timeout
                greeting = b''
                while time.monotonic() < deadline:
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        break  # Renode reconnects may send negotiation without a prompt.
                    if not chunk:
                        raise RuntimeError('Host fault: monitor disconnected')
                    greeting += chunk
                    if re.search(r'\(device\)\s*$', re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', greeting.decode(errors='replace'))):
                        break
                else:
                    raise RuntimeError('Host fault: monitor greeting timed out')
                sock.sendall(command.encode() + b'\n')
                data = b''
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise RuntimeError('Host fault: monitor disconnected')
                    data += chunk
                    clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', data.decode(errors='replace'))
                    if re.search(r'\(device\)\s*$', clean):
                        if re.search(r'(?:Error|Exception|Could not|No such)', clean, re.I):
                            raise RuntimeError('Host fault: monitor rejected command: ' + clean)
                        return data.decode(errors='replace')
                raise RuntimeError('Host fault: monitor response timed out')
        except OSError as error:
            raise RuntimeError('Host fault: monitor connection lost') from error

    def observe(self):
        values, reads = {}, []
        for name, spec in self.recipe.get('observations', {}).items():
            command = spec['command']
            response = self._monitor(command)
            clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', response)
            tail = clean.split(command, 1)[-1].split('(device)', 1)[0].strip()
            tokens = re.findall(r'(?<![\w.])-?(?:0x[0-9a-fA-F]+|[0-9]+)(?![\w.])', tail)
            numbers = [int(token, 16 if '0x' in token else 10) for token in tokens]
            if (len(numbers) != spec['count'] or
                    any(not -(2 ** (spec['width'] - 1)) <= value < 2 ** spec['width'] for value in numbers)):
                raise RuntimeError('Host fault: incomplete or invalid monitor observation: ' + name)
            value = numbers[0] if spec['count'] == 1 else numbers
            values[name] = value
            reads.append({'name': name, 'command': command, 'response': response,
                          'value': value, 'time': time.time()})
        return {'values': values, 'reads': reads, 'time': time.time(), 'physical': False}

    def set_running(self, running):
        if type(running) is not bool:
            raise ValueError('running must be boolean')
        self._monitor('start' if running else 'pause')

    def stimulate(self, values):
        commands = render_stimuli(self.recipe, values)
        controls = [{'command': command, 'response': self._monitor(command), 'time': time.time()}
                    for command in commands]
        return {**self.observe(), 'controls': controls}

    def reset(self, initial):
        commands = render_stimuli(self.recipe, initial)
        self.set_running(False)
        controls = [{'command': command, 'response': self._monitor(command), 'time': time.time()}
                    for command in self.recipe.get('reset_commands', []) + commands]
        return {**self.observe(), 'controls': controls}

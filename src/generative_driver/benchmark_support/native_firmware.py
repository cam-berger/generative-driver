"""Optional Linux native firmware session: raw TTY transport and independent process memory.

No protocol handler is replaced. Memory coordinates come from evaluator-owned build evidence.
POSIX-only imports happen after the platform check, so the core remains importable elsewhere.
"""
import copy
import hashlib
import os
from pathlib import Path
import select
import socketserver
import subprocess
import sys
import threading
import time


class NativeFirmwareSession:
    @classmethod
    def start(cls, *, image, recipe, private_dir=None, renode=None, timeout_seconds=60):
        if sys.platform != 'linux':
            raise RuntimeError('Host fault: native-sim execution requires an optional Linux environment')
        import pty
        import tty
        import tempfile
        image = Path(image).resolve()
        if not image.is_file() or image.read_bytes()[:4] != b'\x7fELF':
            raise ValueError('Native firmware must be the executable ELF supplied to the agent')
        args = recipe.get('uart_args')
        observations = recipe.get('observations')
        if (not isinstance(args, list) or not args or any(not isinstance(a, str) or '\x00' in a for a in args)
                or sum(a.count('{tty}') for a in args) != 1
                or any('{' in a.replace('{tty}', '') or '}' in a.replace('{tty}', '') for a in args)
                or not isinstance(observations, dict) or not observations or recipe.get('stimuli')):
            raise ValueError('Native recipe requires one TTY argument, bounded observations and no stimuli')
        for spec in observations.values():
            if (not isinstance(spec, dict) or set(spec) != {'address', 'width', 'count'}
                    or type(spec['address']) is not int or not 0 < spec['address'] < 2**64
                    or type(spec['width']) is not int or spec['width'] not in (8, 16, 32, 64)
                    or type(spec['count']) is not int or not 1 <= spec['count'] <= 4096):
                raise ValueError('Invalid native memory observation')
        self = cls()
        self.image, self.recipe = image, copy.deepcopy(recipe)
        self.private = Path(private_dir) if private_dir else Path(tempfile.mkdtemp(prefix='gd-native-firmware-'))
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process, self.running, self.bridge_error = None, False, None
        self._closed, self._wire_lock = False, threading.Lock()
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.argv = [str(image), *[a.replace('{tty}', os.ttyname(self.slave)) for a in args]]
        self.info = {'backend': 'native-sim', 'executable': str(image),
                     'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                     'private_dir': str(self.private), 'log': str(self.private/'native.log')}
        class Bridge(socketserver.BaseRequestHandler):
            def handle(handler):
                with self._wire_lock:
                    try:
                        while not self._closed:
                            ready, _, _ = select.select([handler.request, self.master], [], [], .05)
                            if handler.request in ready:
                                data = handler.request.recv(65536)
                                if not data:
                                    return
                                view = memoryview(data)
                                while view:
                                    view = view[os.write(self.master, view):]
                            if self.master in ready:
                                handler.request.sendall(os.read(self.master, 65536))
                    except (OSError, ValueError) as error:
                        if not self._closed:
                            self.bridge_error = str(error)
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
        self.server = Server(('127.0.0.1', 0), Bridge)
        self.info['binding'] = {'host': '127.0.0.1', 'port': self.server.server_address[1]}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        try:
            self._boot()
            return self
        except BaseException:
            self.stop()
            raise

    @property
    def binding(self):
        return dict(self.info['binding'])

    def _boot(self):
        if hashlib.sha256(self.image.read_bytes()).hexdigest() != self.info['image_sha256']:
            raise ValueError('Native firmware image hash changed before launch')
        with (self.private/'native.log').open('ab') as log:
            self.process = subprocess.Popen(self.argv, stdin=subprocess.DEVNULL, stdout=log,
                                            stderr=subprocess.STDOUT, cwd=self.private)
        self.info.update(pid=self.process.pid, argv=list(self.argv))
        self.running = True
        time.sleep(.2)
        self.set_running(False)

    def _healthy(self):
        if self.process is None or self.process.poll() is not None or self.bridge_error:
            raise RuntimeError('Host fault: native target or byte transport stopped; see ' + str(self.private/'native.log'))

    def set_running(self, running):
        import signal
        if type(running) is not bool:
            raise ValueError('running must be boolean')
        self._healthy()
        if running == self.running:
            return
        self.process.send_signal(signal.SIGCONT if running else signal.SIGSTOP)
        if not running:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                self._healthy()
                state = Path('/proc', str(self.process.pid), 'status').read_text()
                if any(line.startswith('State:') and 'T' in line for line in state.splitlines()):
                    break
                time.sleep(.005)
            else:
                raise RuntimeError('Host fault: native target pause was not confirmed')
        self.running = running

    def observe(self):
        self._healthy()
        if self.running:
            raise RuntimeError('Host fault: native observations require a confirmed paused target')
        values, reads = {}, []
        with Path('/proc', str(self.process.pid), 'mem').open('rb', buffering=0) as memory:
            for name, spec in self.recipe['observations'].items():
                size = spec['width']//8
                memory.seek(spec['address'])
                data = memory.read(size * spec['count'])
                if len(data) != size * spec['count']:
                    raise RuntimeError('Host fault: incomplete native memory observation')
                decoded = [int.from_bytes(data[i:i+size], 'little') for i in range(0, len(data), size)]
                value = decoded[0] if len(decoded) == 1 else decoded
                values[name] = value
                reads.append({'name': name, 'command': 'process-memory ' + hex(spec['address']),
                              'response': data.hex(), 'value': value, 'time': time.time()})
        return {'values': values, 'reads': reads, 'time': time.time(), 'physical': False}

    def stimulate(self, values):
        if values:
            raise ValueError('Native target has no declared stimuli')
        return self.observe()

    def reset(self, initial):
        if initial:
            raise ValueError('Native target has no declared reset stimuli')
        if hashlib.sha256(self.image.read_bytes()).hexdigest() != self.info['image_sha256']:
            raise ValueError('Native firmware image hash changed before reset')
        self._terminate()
        # Discard transport residue only at explicit cold reset, never after an uncertain write.
        while select.select([self.master], [], [], 0)[0]:
            os.read(self.master, 65536)
        self._boot()
        return self.observe()

    def _terminate(self):
        if self.process is not None and self.process.poll() is None:
            import signal
            self.process.send_signal(signal.SIGCONT)
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.running = False

    def stop(self):
        self._terminate()
        if not self._closed:
            self._closed = True
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            os.close(self.master)
            os.close(self.slave)

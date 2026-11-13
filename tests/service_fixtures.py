"""Shared fixtures for the tests of the service's diagnostics (docs/features/kit-fixes/04-service-diagnostics.md):
reading its log, a service in a thread of the test and a worker that idles.

Temporary homes only; no model and no hardware."""
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# An entry of the service's log starts with its time.
STAMP = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\S* ', re.M)
# A worker that does nothing: the launch runs it in place of Codex and it sleeps until it is stopped.
IDLE_WORKER = [sys.executable, '-c', 'import time; time.sleep(120)']


def entries(home):
    """Each entry of the home's log: its first line, which starts with its time, and the lines that follow it."""
    path = Path(home) / 'daemon.log'
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    starts = [found.start() for found in STAMP.finditer(text)]
    return [text[start:end] for start, end in zip(starts, starts[1:] + [len(text)])]


def entry(home, *needles):
    """The one entry of the home's log whose first line holds every needle."""
    found = [text for text in entries(home) if all(needle in text.splitlines()[0] for needle in needles)]
    if len(found) != 1:
        raise AssertionError(str(len(found)) + ' entries name ' + repr(needles) + ' in:\n' + '\n'.join(entries(home)))
    return found[0]


def serve(test, home):
    """The service in a thread of this process at `home`, as the service process runs it, so that a test can replace
    what it calls; worker gateway processes reach it as they reach any service. Returns its `call`."""
    from generative_driver import client, daemon
    home.mkdir(mode=0o700, exist_ok=True)
    if os.name == 'nt':
        family, address = 'AF_PIPE', r'\\.\pipe\gd-test-' + secrets.token_hex(8)
    else:
        # A short folder of its own: macOS refuses a socket path over 104 bytes.
        sockets = Path(tempfile.mkdtemp(prefix='gd-t-'))
        test.addCleanup(shutil.rmtree, sockets, True)
        family, address = 'AF_UNIX', str(sockets / 'owner.sock')
    record = {'family': family, 'address': address, 'authkey': secrets.token_hex(32), 'pid': os.getpid(),
              'code': client.code_identity()}
    (home / 'service.json').write_text(json.dumps(record), encoding='utf-8')
    thread = threading.Thread(target=daemon.serve, args=(home,), daemon=True)
    thread.start()

    def stop():
        if thread.is_alive():
            client._request(home, 'shutdown', {})
            thread.join(30)
    test.addCleanup(stop)
    until = time.monotonic() + 10
    while not client.call('ping', home=home, autostart=False).get('ok'):
        test.assertLess(time.monotonic(), until)
        time.sleep(.05)
    return lambda method, params=None: client.call(method, params or {}, home=home, autostart=False)

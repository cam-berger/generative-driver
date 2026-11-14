"""The stand-in device: a fictional line-protocol device that `generative-driver smoke` serves on a localhost TCP port
for the length of one smoke (docs/features/kit-fixes/03-service-smoke.md). It answers what the stand-in model in
`resources/smoke/model.json` asks; its exchanges are not physical evidence."""
import contextlib
import socket
import threading

# Each request line, without its line feed, and the reply the device sends to it.
REPLIES = {b'ID?': b'DEMO-42\n', b'T': b'T:21.5\n'}


@contextlib.contextmanager
def serving(replies=None):
    """Serve the stand-in device on 127.0.0.1, on a port the system picks, until the block ends. Yields the TCP binding
    an operator would supply for it. `replies` replaces some of its `REPLIES`, which only a test of the smoke does."""
    replies = {**REPLIES, **(replies or {})}
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    listener.settimeout(0.1)
    stop = threading.Event()

    def answer(connection):
        connection.settimeout(5)
        pending = b''
        with connection:
            while not stop.is_set():
                try:
                    data = connection.recv(256)
                except OSError:
                    return
                if not data:
                    return
                pending += data
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    reply = replies.get(line.rstrip(b'\r'))
                    if reply is not None:
                        connection.sendall(reply)

    def accept():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=answer, args=(connection,), daemon=True).start()

    server = threading.Thread(target=accept, daemon=True)
    server.start()
    try:
        yield {'host': '127.0.0.1', 'port': listener.getsockname()[1]}
    finally:
        stop.set()
        server.join(2)
        listener.close()

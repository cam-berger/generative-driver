"""A fictional line-protocol device served on a localhost TCP socket for contract tests. No hardware is involved."""
import contextlib
import socket
import threading


@contextlib.contextmanager
def line_device(replies, seen=None):
    """Answer each request line with `replies[line]` (bytes, looked up at each request, so a test may change the dict
    between calls); a line without a reply gets none. Every request line is appended to `seen` when given. Yields the
    TCP binding an operator would supply."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    listener.settimeout(0.1)
    stop = threading.Event()

    def handle(connection):
        connection.settimeout(5)
        buffer = b''
        with connection:
            while not stop.is_set():
                try:
                    data = connection.recv(256)
                except OSError:
                    return
                if not data:
                    return
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if seen is not None:
                        seen.append(line)
                    reply = replies.get(line)
                    if reply is not None:
                        connection.sendall(reply)

    def serve():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=handle, args=(connection,), daemon=True).start()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    try:
        yield {'host': '127.0.0.1', 'port': listener.getsockname()[1]}
    finally:
        stop.set()
        server.join(2)
        listener.close()

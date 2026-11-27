"""Bounded multiprocessing.connection IPC helpers shared by the daemon, client, and device client.

Every connect, authkey challenge handshake, send, and receive shares one monotonic deadline so a stalled or slow peer
never blocks an accept loop or leaves a caller waiting past its budget.
"""
import contextlib
import errno
import math
import os
import select
import socket
import struct
import threading
import time
from multiprocessing.connection import Client, Connection, answer_challenge, deliver_challenge

if os.name == 'nt':
    import _winapi
    from multiprocessing.connection import PipeConnection as _PipeConnection
else:
    _winapi = None
    _PipeConnection = None

_IO_CHUNK = 65536


def _is_timeout_oserror(error):
    return isinstance(error, (TimeoutError, BlockingIOError)) or getattr(error, 'errno', None) in (
        errno.EAGAIN, errno.EWOULDBLOCK, errno.ETIMEDOUT)


@contextlib.contextmanager
def _nonblocking_fd(fd):
    """Put a POSIX file descriptor in non-blocking mode for the duration of a deadline-bounded transfer."""
    if os.name == 'nt':
        yield
        return
    was_blocking = os.get_blocking(fd)
    if was_blocking:
        os.set_blocking(fd, False)
    try:
        yield
    finally:
        if was_blocking:
            with contextlib.suppress(OSError):
                os.set_blocking(fd, True)


def _send_all_conn(conn, data, deadline):
    """Write every byte of `data` to socket-backed `Connection` `conn` before monotonic `deadline`."""
    view = memoryview(data)
    if view.itemsize > 1:
        view = view.cast('B')
    fd = conn.fileno()
    write = conn._write
    sent = 0
    total = view.nbytes
    with _nonblocking_fd(fd):
        while sent < total:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('IPC send timed out')
            _, writable, _ = select.select([], [fd], [], remaining)
            if not writable:
                raise TimeoutError('IPC send timed out')
            if time.monotonic() >= deadline:
                raise TimeoutError('IPC send timed out')
            try:
                n = write(fd, view[sent:sent + _IO_CHUNK])
            except OSError as error:
                if _is_timeout_oserror(error):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('IPC send timed out') from error
                    continue
                raise
            if n == 0:
                raise OSError(errno.EPIPE, 'broken pipe during IPC send')
            sent += n


def _recv_exact_conn(conn, size, deadline, *, eof_ok=False):
    """Read exactly `size` bytes from socket-backed `Connection` `conn` before monotonic `deadline`."""
    if size == 0:
        return b''
    fd = conn.fileno()
    read = conn._read
    chunks = []
    received = 0
    with _nonblocking_fd(fd):
        while received < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('IPC receive timed out')
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                raise TimeoutError('IPC receive timed out')
            if time.monotonic() >= deadline:
                raise TimeoutError('IPC receive timed out')
            try:
                chunk = read(fd, min(size - received, _IO_CHUNK))
            except OSError as error:
                if _is_timeout_oserror(error):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('IPC receive timed out') from error
                    continue
                raise
            if not chunk:
                if received == 0 and eof_ok:
                    raise EOFError
                raise OSError('got end of file during message')
            chunks.append(chunk)
            received += len(chunk)
    return b''.join(chunks)


def _socket_send_bytes(conn, buf, deadline):
    """Send one `multiprocessing.connection.Connection` length-prefixed frame within `deadline`."""
    conn._check_closed()
    conn._check_writable()
    view = memoryview(buf)
    if view.itemsize > 1:
        view = view.cast('B')
    n = view.nbytes
    if n > 0x7FFFFFFF:
        _send_all_conn(conn, struct.pack('!iQ', -1, n), deadline)
        _send_all_conn(conn, view, deadline)
    elif n > 16384:
        _send_all_conn(conn, struct.pack('!i', n), deadline)
        _send_all_conn(conn, view, deadline)
    else:
        _send_all_conn(conn, struct.pack('!i', n) + view.tobytes(), deadline)


def _socket_recv_bytes(conn, maxlength, deadline):
    """Receive one `multiprocessing.connection.Connection` length-prefixed frame within `deadline`."""
    conn._check_closed()
    conn._check_readable()
    if maxlength is not None and maxlength < 0:
        raise ValueError('negative maxlength')
    header = _recv_exact_conn(conn, 4, deadline, eof_ok=True)
    size, = struct.unpack('!i', header)
    if size == -1:
        size, = struct.unpack('!Q', _recv_exact_conn(conn, 8, deadline))
    if size < 0 or (maxlength is not None and size > maxlength):
        conn._bad_message_length()
    return _recv_exact_conn(conn, size, deadline)


def _pipe_wait_overlapped(ov, err, deadline, timeout_message):
    """Wait for one Windows overlapped pipe operation within `deadline`, cancelling on timeout or interruption."""
    timed_out = False
    cancelled = False
    interrupted = False
    try:
        if err == _winapi.ERROR_IO_PENDING:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    ov.cancel()
                    cancelled = True
                    break
                timeout_ms = min(0x7FFFFFFF, max(1, int(math.ceil(remaining * 1000))))
                waitres = _winapi.WaitForMultipleObjects([ov.event], False, timeout_ms)
                if waitres == _winapi.WAIT_OBJECT_0:
                    break
                if waitres == _winapi.WAIT_TIMEOUT:
                    if timeout_ms < 0x7FFFFFFF or time.monotonic() >= deadline:
                        timed_out = True
                        ov.cancel()
                        cancelled = True
                        break
                    continue
                ov.cancel()
                cancelled = True
                raise OSError(f'WaitForMultipleObjects returned unexpected status {waitres}')
    except BaseException:
        interrupted = True
        if not cancelled:
            ov.cancel()
        raise
    finally:
        if interrupted:
            with contextlib.suppress(OSError):
                ov.GetOverlappedResult(True)
        else:
            try:
                transferred, res_err = ov.GetOverlappedResult(True)
            except OSError:
                if timed_out:
                    raise TimeoutError(timeout_message) from None
                raise
    if timed_out:
        raise TimeoutError(timeout_message)
    return transferred, res_err


def _pipe_send_bytes(conn, buf, deadline):
    """Send one message on a Windows `PipeConnection` within `deadline`, cancelling the write on timeout."""
    conn._check_closed()
    conn._check_writable()
    if getattr(conn, '_send_ov', None) is not None:
        raise ValueError('concurrent send_bytes() calls are not supported')
    data = memoryview(buf)
    if data.itemsize > 1:
        data = data.cast('B')
    if deadline - time.monotonic() <= 0:
        raise TimeoutError('IPC send timed out')
    ov, err = _winapi.WriteFile(conn.fileno(), data, overlapped=True)
    conn._send_ov = ov
    try:
        nwritten, res_err = _pipe_wait_overlapped(ov, err, deadline, 'IPC send timed out')
    finally:
        conn._send_ov = None
    if res_err == _winapi.ERROR_OPERATION_ABORTED:
        raise OSError(errno.EPIPE, 'handle is closed')
    if res_err != 0:
        raise OSError(res_err, 'pipe write failed')
    if nwritten != data.nbytes:
        raise OSError(errno.EPIPE, f'short pipe write: {nwritten} of {data.nbytes} bytes')


def _pipe_recv_bytes(conn, maxlength, deadline):
    """Receive one message from a Windows `PipeConnection` within `deadline`, cancelling reads on timeout."""
    conn._check_closed()
    conn._check_readable()
    if maxlength is not None and maxlength < 0:
        raise ValueError('negative maxlength')
    if deadline - time.monotonic() <= 0:
        raise TimeoutError('IPC receive timed out')
    if getattr(conn, '_got_empty_message', False):
        conn._got_empty_message = False
        return b''
    handle = conn.fileno()
    bsize = 128 if maxlength is None else min(maxlength, 128)
    try:
        ov, err = _winapi.ReadFile(handle, bsize, overlapped=True)
        _, res_err = _pipe_wait_overlapped(ov, err, deadline, 'IPC receive timed out')
        chunks = [bytes(ov.getbuffer())]
        total = len(chunks[0])
        while res_err == _winapi.ERROR_MORE_DATA:
            if deadline - time.monotonic() <= 0:
                raise TimeoutError('IPC receive timed out')
            left = _winapi.PeekNamedPipe(handle)[1]
            if maxlength is not None and total + left > maxlength:
                conn._bad_message_length()
            read_size = left if left > 0 else _IO_CHUNK
            if maxlength is not None and total + read_size > maxlength:
                read_size = maxlength - total
            ov, err = _winapi.ReadFile(handle, read_size, overlapped=True)
            _, res_err = _pipe_wait_overlapped(ov, err, deadline, 'IPC receive timed out')
            piece = bytes(ov.getbuffer())
            chunks.append(piece)
            total += len(piece)
            if res_err == _winapi.ERROR_MORE_DATA and maxlength is not None and total >= maxlength:
                conn._bad_message_length()
        if res_err == _winapi.ERROR_OPERATION_ABORTED:
            raise OSError(errno.EPIPE, 'handle is closed')
        if res_err != 0:
            raise OSError(res_err, 'pipe read failed')
        return b''.join(chunks)
    except OSError as error:
        if getattr(error, 'winerror', None) == _winapi.ERROR_BROKEN_PIPE:
            raise EOFError from None
        raise


class TimedConnection:
    """Thin wrapper over a `multiprocessing.connection` handle enforcing one monotonic `deadline`."""

    def __init__(self, conn, deadline, family=None):
        self._conn = conn
        self.deadline = deadline
        self._family = family

    def _remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('IPC deadline exceeded')
        return remaining

    def send_bytes(self, buf):
        self._remaining()
        if _PipeConnection is not None and isinstance(self._conn, _PipeConnection):
            return _pipe_send_bytes(self._conn, buf, self.deadline)
        if isinstance(self._conn, Connection):
            return _socket_send_bytes(self._conn, buf, self.deadline)
        try:
            return self._conn.send_bytes(buf)
        except OSError as error:
            if _is_timeout_oserror(error):
                raise TimeoutError('IPC send timed out') from error
            raise

    def recv_bytes(self, maxlength=None):
        remaining = self._remaining()
        if _PipeConnection is not None and isinstance(self._conn, _PipeConnection):
            return _pipe_recv_bytes(self._conn, maxlength, self.deadline)
        if isinstance(self._conn, Connection):
            return _socket_recv_bytes(self._conn, maxlength, self.deadline)
        if not self._conn.poll(remaining):
            raise TimeoutError('IPC receive timed out')
        self._remaining()
        try:
            return self._conn.recv_bytes(maxlength) if maxlength is not None else self._conn.recv_bytes()
        except OSError as error:
            if _is_timeout_oserror(error):
                raise TimeoutError('IPC receive timed out') from error
            raise


def open_connection(address, family, authkey, deadline):
    """Open and authenticate a `multiprocessing.connection` handle to `address` within monotonic `deadline`."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('IPC connect timed out')
    if family == 'AF_PIPE':
        outcome, timed_out = {}, threading.Event()
        def _connect():
            try:
                conn = Client(address, family=family, authkey=None)
            except BaseException as error:
                outcome['error'] = error
                return
            if timed_out.is_set():
                with contextlib.suppress(OSError):
                    conn.close()
                return
            outcome['conn'] = conn
        worker = threading.Thread(target=_connect, daemon=True)
        worker.start()
        worker.join(remaining)
        if worker.is_alive():
            timed_out.set()
            raise TimeoutError('IPC connect timed out')
        if 'error' in outcome:
            raise outcome['error']
        conn = outcome['conn']
    else:
        sock_family = getattr(socket, family)
        sock = socket.socket(sock_family)
        try:
            sock.settimeout(remaining)
            sock.connect(address)
            sock.setblocking(True)
            conn = Connection(sock.detach())
        except BaseException:
            sock.close()
            raise
    try:
        if authkey is not None:
            timed = TimedConnection(conn, deadline, family=family)
            answer_challenge(timed, authkey)
            deliver_challenge(timed, authkey)
        return conn
    except BaseException:
        with contextlib.suppress(OSError):
            conn.close()
        raise


def accept_handshake(conn, family, authkey, deadline):
    """Perform server-side authkey challenge on an accepted raw `conn` within monotonic `deadline`."""
    timed = TimedConnection(conn, deadline, family=family)
    if authkey is not None:
        deliver_challenge(timed, authkey)
        answer_challenge(timed, authkey)
    return timed

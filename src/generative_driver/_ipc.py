"""Bounded multiprocessing.connection IPC helpers shared by the daemon, client, and device client.

Every connect, authkey challenge handshake, send, and receive shares one monotonic deadline so a stalled peer
never blocks an accept loop or leaves a caller waiting past its budget.
"""
import contextlib
import errno
import os
import select
import socket
import struct
import threading
import time
from multiprocessing.connection import Client, Connection, answer_challenge, deliver_challenge


def _set_socket_timeout(sock, seconds):
    """Apply kernel-level SO_RCVTIMEO and SO_SNDTIMEO on a POSIX socket so partial-frame stalls time out too."""
    if os.name == 'nt':
        return
    seconds = max(0.001, float(seconds))
    sec = int(seconds)
    usec = int((seconds - sec) * 1_000_000)
    if sec == 0 and usec == 0:
        usec = 1
    for fmt in ('ll', 'li'):
        try:
            tv = struct.pack(fmt, sec, usec)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, tv)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, tv)
            return
        except OSError:
            continue


def _set_fd_timeout(conn, family, seconds):
    """Apply kernel-level read/write timeouts to an existing POSIX socket-backed Connection."""
    if os.name == 'nt' or family == 'AF_PIPE':
        return
    try:
        sock_family = getattr(socket, family)
        fd = conn.fileno()
    except (AttributeError, OSError, ValueError):
        return
    with contextlib.suppress(OSError, ValueError):
        with socket.fromfd(fd, sock_family, socket.SOCK_STREAM) as dup:
            _set_socket_timeout(dup, seconds)


def _is_timeout_oserror(error):
    return isinstance(error, (TimeoutError, BlockingIOError)) or getattr(error, 'errno', None) in (
        errno.EAGAIN, errno.EWOULDBLOCK, errno.ETIMEDOUT)


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
        remaining = self._remaining()
        if os.name != 'nt':
            try:
                fd = self._conn.fileno()
            except (AttributeError, OSError, ValueError):
                fd = None
            if fd is not None:
                _, writable, _ = select.select([], [fd], [], remaining)
                if not writable:
                    raise TimeoutError('IPC send timed out')
                _set_fd_timeout(self._conn, self._family, remaining)
        try:
            return self._conn.send_bytes(buf)
        except OSError as error:
            if _is_timeout_oserror(error):
                raise TimeoutError('IPC send timed out') from error
            raise

    def recv_bytes(self, maxlength=None):
        remaining = self._remaining()
        if not self._conn.poll(remaining):
            raise TimeoutError('IPC receive timed out')
        remaining = self._remaining()
        if os.name != 'nt':
            _set_fd_timeout(self._conn, self._family, remaining)
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
            _set_socket_timeout(sock, max(0.001, deadline - time.monotonic()))
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

"""The lock a bench harness holds for as long as it runs, and how another process tells whether it still runs.

These are copies of the tool's own helpers for its service's lock (`client.hold_process_lock`,
`client.process_lock_held` and `client._wait_for_exit`), statement for statement. The client interface lists none of
them, so the benchmark keeps its own (Feature 08 of the untangle Run, docs/features/untangle/08-reports.md, 08-B13).
A lock one side holds is one the other sees held.
"""
import os
import time


def wait_for_exit(pid, timeout=10):
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


def hold_process_lock(path):
    """Lock the file at `path` for as long as this process keeps the returned stream open; the operating system lets
    go of it when the process ends, however it ends. None when the file system offers no such lock."""
    stream = open(path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        return None
    return stream


def process_lock_held(path):
    """Whether a running process holds the lock `hold_process_lock` takes on `path`: unlike a process id, which another
    process may take over once its holder ended, a lock ends with its holder. True or False; None when it cannot be
    told (the caller then decides by other means)."""
    import errno
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                return True if error.errno in (errno.EACCES, errno.EDEADLOCK) else None
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return False
        import fcntl
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)

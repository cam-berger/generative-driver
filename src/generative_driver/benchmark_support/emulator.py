"""Emulator helpers and the case truth unlock, for the bench harness and the evaluator only.

The recipe and target register addresses come from encrypted case evidence. Workers receive only the UART socket
binding. TCP does not validate serial baud.
"""
import socket
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


def truth_for_case(case_root, manifest, options):
    from .truth import unlock
    password_file = options.get('evaluator_password_file')
    if not password_file:
        raise ValueError('Evaluator password file is required; candidate workers must not receive it')
    password = Path(password_file).read_text().strip()
    return unlock(Path(case_root) / manifest['truth']['path'], password, manifest['truth']['sha256'])

"""Password-encrypted evaluator evidence, with an independently pinned ciphertext hash."""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

SCHEMA = 'generative-driver-groundtruth/1'


def _key(password, salt):
    if not isinstance(password, str) or not password:
        raise ValueError('A nonempty evaluator password is required')
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=600000).derive(password.encode('utf-8'))


def seal(evidence, path, password):
    salt, nonce = os.urandom(16), os.urandom(12)
    payload = json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    encrypted = AESGCM(_key(password, salt)).encrypt(nonce, payload, SCHEMA.encode())
    envelope = {'schema': SCHEMA, 'kdf': 'PBKDF2-HMAC-SHA256', 'iterations': 600000,
                'cipher': 'AES-256-GCM', **{k: base64.b64encode(v).decode('ascii')
                for k, v in {'salt': salt, 'nonce': nonce, 'ciphertext': encrypted}.items()}}
    data = (json.dumps(envelope, sort_keys=True) + '\n').encode()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _replace(Path(path), data)
    return hashlib.sha256(data).hexdigest()


def _replace(path, data):
    """Write `data` beside `path`, then move it into place in one step: a write cut short (a killed process, a full disk)
    leaves the previous file whole."""
    temporary = path.with_name('.' + path.name + '.' + os.urandom(8).hex())
    try:
        temporary.write_bytes(data)
        for attempt in range(50):
            try:
                return os.replace(temporary, path)
            except PermissionError:  # Windows refuses to replace a file a reader holds open; the reader closes it soon.
                if attempt == 49:
                    raise
                time.sleep(.05)
    finally:
        temporary.unlink(missing_ok=True)


def unlock(path, password, expected_sha256):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('Groundtruth ciphertext does not match its pinned hash')
    try:
        envelope = json.loads(data)
        if (envelope['schema'], envelope['kdf'], envelope['iterations'], envelope['cipher']) != (
                SCHEMA, 'PBKDF2-HMAC-SHA256', 600000, 'AES-256-GCM'):
            raise ValueError('Unsupported groundtruth envelope')
        salt, nonce, encrypted = (base64.b64decode(envelope[k], validate=True)
                                   for k in ('salt', 'nonce', 'ciphertext'))
        payload = AESGCM(_key(password, salt)).decrypt(nonce, encrypted, SCHEMA.encode())
        return json.loads(payload)
    except (InvalidTag, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Cannot unlock groundtruth: invalid password or envelope') from error

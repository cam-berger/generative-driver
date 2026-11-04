"""Password-encrypted evaluator evidence, with an independently pinned ciphertext hash, and the only way to reach a
password or the cipher (Feature 02, unlocked keys; spec decision 17). A process reads a password file once, here, and
holds the result in an object that can only seal and unlock. This guards against mistakes in our own code. It is not
isolation: a worker running as the same user could still read the memory of the process holding the password."""
import base64
from dataclasses import dataclass
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


def read_password(path):
    """The `EvaluatorPassword` in the file at `path`, read as UTF-8 on every platform and stripped. ValueError for an
    empty file or one that is not UTF-8, quoting nothing from the file; an unreadable file raises its OSError."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except UnicodeDecodeError:
        text = None  # Raised below, so that the decoding error and the bytes it holds are not even its context.
    if text is None:
        raise ValueError('The evaluator password file is not UTF-8')
    return EvaluatorPassword(text.strip())


class EvaluatorPassword:
    """An evaluator password once read: it seals and unlocks, and cannot be printed, serialized or copied. Tests build
    fixtures with it directly; the package reads one only through `read_password`."""
    __slots__ = ('__value',)

    def __init__(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError('A nonempty evaluator password is required')
        self.__value = value

    def __repr__(self):
        return 'EvaluatorPassword(<hidden>)'

    def __format__(self, _spec):
        return repr(self)

    def __reduce_ex__(self, _protocol):
        raise TypeError('An evaluator password cannot be serialized or copied')

    def seal(self, evidence, path):
        """Encrypt `evidence` into the file at `path`, replacing it in one step, and return the file's SHA-256."""
        salt, nonce = os.urandom(16), os.urandom(12)
        payload = json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        encrypted = AESGCM(_key(self.__value, salt)).encrypt(nonce, payload, SCHEMA.encode())
        envelope = {'schema': SCHEMA, 'kdf': 'PBKDF2-HMAC-SHA256', 'iterations': 600000,
                    'cipher': 'AES-256-GCM', **{k: base64.b64encode(v).decode('ascii')
                    for k, v in {'salt': salt, 'nonce': nonce, 'ciphertext': encrypted}.items()}}
        data = (json.dumps(envelope, sort_keys=True) + '\n').encode()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _replace(Path(path), data)
        return hashlib.sha256(data).hexdigest()

    def unlock(self, path, expected_sha256):
        """The evidence sealed in the file at `path`. ValueError for a file whose hash is not `expected_sha256`, the
        wrong password or a broken envelope."""
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
            payload = AESGCM(_key(self.__value, salt)).decrypt(nonce, encrypted, SCHEMA.encode())
            return json.loads(payload)
        except (InvalidTag, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('Cannot unlock groundtruth: invalid password or envelope') from error


@dataclass(frozen=True, repr=False)
class UnlockedTruth:
    """A case's answer key decrypted in the bench harness's memory: `payload`, hydrated by the case's family, and
    `sha256`, the manifest's truth hash it was unlocked under, which the grader checks against the run's pin."""
    payload: dict
    sha256: str

    def __repr__(self):
        return 'UnlockedTruth(sha256=' + repr(self.sha256) + ')'


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

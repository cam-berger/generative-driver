"""Artifact and tree hashes, and the error for an artifact whose hash changed. The configurator, the reports and the
evaluator all use them; they live here so the evaluator does not import the configurator."""
import hashlib
import json
from pathlib import Path


def digest(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Artifact symlinks are not admissible')
    if path.is_file():
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        return hasher.hexdigest()
    if path.is_dir():
        entries = []
        for item in sorted(path.rglob('*')):
            if item.is_symlink():
                raise ValueError('Artifact symlinks are not admissible')
            if item.is_file():
                entries.append([item.relative_to(path).as_posix(), digest(item)])
        return hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()
    raise ValueError('Artifact does not exist: ' + str(path))


class IntegrityError(ValueError):
    """A recorded or observed hash, sealed input or handoff no longer matches: the run stops for integrity."""


def tree_hash(root):
    hasher = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
            hasher.update(path.relative_to(root).as_posix().encode() + b'\0')
            hasher.update(hashlib.sha256(path.read_bytes()).digest())
    return hasher.hexdigest()

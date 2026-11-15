"""The hash the tool records for an artifact, and the error that stops a run when a hash no longer matches.

The configurator hashes every artifact and handoff a run accepts, and the scripted worker and the replay executor
report their artifacts by the same hash. The tool keeps both names here, in its own package, so that each has one
place (Feature 02 of the untangle Run, docs/features/untangle/02-shared-helpers.md).
"""
import hashlib
import json
from pathlib import Path

__all__ = ['IntegrityError', 'digest']


def digest(path):
    """The SHA-256 the tool records for the artifact at `path`, in hex. A file's is the hash of its bytes. A
    directory's is the hash of the list of its files, each with its POSIX path from `path` and its own hash, so a
    folder that holds no file adds nothing. A symlink, at `path` or under it, raises `ValueError`, and so does a
    `path` that does not exist."""
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
    """A hash the run recorded, a sealed input or a handoff no longer matches what is there now: the configurator
    stops the run for integrity."""

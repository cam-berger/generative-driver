"""Two helpers the stage modules share: the finder of an artifact in an accepted handoff, and the writer of the JSON
files a stage leaves."""
import contextlib
import json
import os
import tempfile
from pathlib import Path


def _named(path, name):
    """Whether the file at `path` is the artifact `name`: it has that name, or the name the configurator gives an
    accepted copy, `<index>-<name>`."""
    prefix, separator, original = Path(path).name.partition('-')
    return Path(path).name == name or bool(separator) and prefix.isdigit() and original == name


def accepted_artifact(handoff, name, sha256=None):
    """(path, recorded sha256) of `name` in one accepted handoff (with that hash, when given), or None."""
    for artifact in (handoff or {}).get('artifacts', []):
        if _named(artifact['path'], name) and sha256 in (None, artifact['sha256']):
            return Path(artifact['path']), artifact['sha256']
    return None


def write_json(path, value):
    """Write `value` as indented JSON at `path`, atomically through a temporary file in its folder."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode('utf-8')
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

"""Two helpers the stage modules share: the finder of an artifact in an accepted handoff, and the writer of the JSON
files a stage leaves. The benchmark's grader has both in a module of its own, which the tool may not import, so these
are the tool's (Feature 05 of the untangle Run, docs/features/untangle/05-supplied-device-stages.md)."""
import json
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
    """Write `value` as indented JSON at `path`, after making the folder it lies in."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

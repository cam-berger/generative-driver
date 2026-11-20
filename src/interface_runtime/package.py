"""Read a package: check its five files of data and give its manifest and each driver's model.

A package holds no code (docs/adr/0010-a-package-is-data-in-one-format.md): `manifest.json`, `mcp.json`, `README.md`,
one driver file and the probe record that driver wrote. The check reads those bytes and nothing of the folder is
imported or run. Hashes establish integrity, not a signature: the hash of `manifest.json` is the package's pin, and
only a pin kept outside the package tells one package from another that is made the same way.
"""
import hashlib
from pathlib import Path, PurePosixPath

from .evidence import read_json_hashed

SCHEMA = 'interface-package/3'
_MANIFEST = 'manifest.json'
_FIELDS = {'schema', 'name', 'version', 'drivers', 'files'}
_DRIVER_FIELDS = {'name', 'model', 'evidence', 'evidence_kind'}
# What a package holds beside its manifest, its driver file and its probe record.
RENDERED = ('README.md', 'mcp.json')
_LIMIT = 1048576


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def format_refusal(manifest):
    """Why this runtime refuses a package for the format its `manifest` names, or None for the one format it reads.
    The reason names the format found and the format read."""
    found = manifest.get('schema') if type(manifest) is dict else None
    if found == SCHEMA:
        return None
    if found is None:
        return 'package names no format; this kit reads ' + SCHEMA
    return 'package format is not one this kit knows: ' + repr(found)[:120] + '; it reads ' + SCHEMA


def _entries(root):
    """The hash of every file below `root` but the manifest, and the folders below it. A link is refused."""
    files, folders = {}, []
    for item in sorted(root.rglob('*')):
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            raise ValueError('package may not contain links: ' + relative)
        if item.is_dir():
            folders.append(relative)
        elif not item.is_file():
            raise ValueError('package may hold only files and folders: ' + relative)
        elif relative != _MANIFEST:
            files[relative] = digest(item)
    return files, folders


def package_files(root):
    """The hash of every file of the package in the folder `root` but its manifest, by its path in the package."""
    return _entries(Path(root))[0]


def _text(value, limit):
    return type(value) is str and 1 <= len(value) <= limit


def text_defect(field, value):
    """Why the check refuses `value` as the `name` or the `version` (`field`) of a manifest, or None when it accepts
    it: each is a text of at least one character, a name of at most 256 and a version of at most 64."""
    limit = {'name': 256, 'version': 64}[field]
    return None if _text(value, limit) else field + ' must be a text of 1 to ' + str(limit) + ' characters'


def _driver(manifest):
    """The entry of the one driver a `manifest` of this format lists, or a ValueError that says why there is none."""
    listed = manifest['drivers']
    if type(listed) is not list:
        raise ValueError('invalid package manifest: drivers must be a list')
    if not listed:
        raise ValueError('package manifest lists no driver')
    if len(listed) > 1:
        raise ValueError(f'package manifest lists {len(listed)} drivers; a package of several drivers is not built yet')
    entry = listed[0]
    if type(entry) is not dict or set(entry) != _DRIVER_FIELDS or not _text(entry['name'], 48):
        raise ValueError('invalid package manifest: a driver holds exactly name, model, evidence and evidence_kind')
    name = entry['name']
    if (entry['model'], entry['evidence']) != (f'drivers/{name}.json', f'evidence/{name}.probe.json'):
        raise ValueError('invalid package manifest: the files of driver ' + repr(name) + ' must be drivers/' + name
                         + '.json and evidence/' + name + '.probe.json')
    if entry['evidence_kind'] not in ('live', 'replay'):
        raise ValueError('invalid package manifest: evidence_kind must be live or replay')
    return entry


def _inventory(manifest, entry, files, folders):
    """Compare the `files` and `folders` a folder holds with those a package of this driver `entry` holds and with
    the hashes `manifest` lists, raising a ValueError that names each file that is missing, unexpected or changed."""
    listed = manifest['files']
    if type(listed) is not dict or any(
            type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
            for value in listed.values()):
        raise ValueError('invalid package manifest: files must map each file to its SHA-256')
    expected = {*RENDERED, entry['model'], entry['evidence']}
    if set(listed) != expected:
        detail = '; '.join(f'{label}: {", ".join(sorted(names))}' for label, names in (
            ('not listed', expected - set(listed)), ('not a file of a package', set(listed) - expected)) if names)
        raise ValueError('package manifest must list the four files a package holds beside it; ' + detail)
    known = {str(PurePosixPath(name).parent) for name in expected} - {'.'}
    missing = sorted(set(listed) - set(files))
    unexpected = sorted(set(files) - set(listed)) + sorted(folder + '/' for folder in folders if folder not in known)
    changed = sorted(name for name in set(listed) & set(files) if listed[name] != files[name])
    if missing or unexpected or changed:
        raise ValueError('package file inventory/hash mismatch; ' + '; '.join(
            f'{label}: {", ".join(names)}' for label, names in (
                ('missing', missing), ('unexpected', unexpected), ('changed', changed)) if names))


def verify_package(root, expected_model_sha256=None, expected_manifest_sha256=None):
    """The check of the package in the folder `root`: `ok`, the `defects` that refuse it, its `manifest`, the hash of
    that manifest as `manifest_sha256`, and `drivers`, which maps the name of each driver to its interface model.

    A package passes when its manifest names the format this runtime reads and lists one driver, when the folder holds
    the files of that driver, `README.md`, `mcp.json` and nothing else, each with the hash the manifest gives, and when
    the driver is a valid interface model named after its device. `expected_manifest_sha256` is a pin the manifest
    must have, and `expected_model_sha256` the hash the driver file must have. The check reads data only: no file of
    the folder is imported or run."""
    from .catalog import driver_name
    from .engine import format_refusal as model_refusal, validate_model
    defects, manifest, pin, drivers = [], None, None, {}
    try:
        root = Path(root)
        path = root / _MANIFEST
        if path.is_symlink():
            raise ValueError('package may not contain links: ' + _MANIFEST)
        if not path.is_file():
            raise ValueError('package has no ' + _MANIFEST)
        manifest, pin = read_json_hashed(path, _LIMIT)
        refusal = format_refusal(manifest)
        if refusal is not None:
            raise ValueError(refusal)
        if set(manifest) != _FIELDS or any(text_defect(field, manifest[field]) for field in ('name', 'version')):
            raise ValueError('invalid package manifest: it holds exactly schema, name, version, drivers and files')
        if expected_manifest_sha256 is not None and pin != expected_manifest_sha256:
            raise ValueError('manifest pin mismatch')
        entry = _driver(manifest)
        files, folders = _entries(root)
        model = sha = None
        if entry['model'] in files:
            # A driver of another format is refused by the name of that format, before any hash is compared.
            model, sha = read_json_hashed(root / entry['model'], _LIMIT)
            refusal = model_refusal(model)
            if refusal is not None:
                raise ValueError(refusal)
        _inventory(manifest, entry, files, folders)
        if sha != manifest['files'][entry['model']]:
            raise ValueError('package file inventory/hash mismatch; changed: ' + entry['model'])
        validated = validate_model(model)
        if not validated['ok']:
            raise ValueError(entry['model'] + ' is not a valid interface model: '
                             + '; '.join(d['where'] + ': ' + d['what'] for d in validated['defects'][:4]))
        if driver_name(model) != entry['name']:
            raise ValueError('driver ' + repr(entry['name']) + ' is not named after its device: '
                             + driver_name(model))
        if expected_model_sha256 is not None and sha != expected_model_sha256:
            raise ValueError('package belongs to different model bytes')
        drivers = {entry['name']: model}
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        defects.append({'where': 'package', 'what': str(exc)})
    return {'ok': not defects, 'defects': defects, 'manifest': manifest, 'manifest_sha256': pin, 'drivers': drivers}


def the_driver(checked):
    """The one driver of a package that passed `verify_package`: its entry in the manifest and its interface model."""
    [entry] = checked['manifest']['drivers']
    return entry, checked['drivers'][entry['name']]

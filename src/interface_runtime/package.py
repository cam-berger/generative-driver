"""Non-executing package integrity checks; hashes are integrity, not signatures."""
import hashlib
from pathlib import Path, PurePosixPath
from .evidence import read_json


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def package_files(root):
    root = Path(root)
    found = {}
    for item in root.rglob('*'):
        rel = item.relative_to(root).as_posix()
        if '__pycache__' in item.relative_to(root).parts:
            continue
        if item.is_symlink():
            raise ValueError('package may not contain symlinks: ' + rel)
        if item.is_file() and rel not in ('manifest.json','manifest.sha256'):
            found[rel] = digest(item)
    return dict(sorted(found.items()))


def verify_package(root, expected_model_sha256=None, expected_manifest_sha256=None):
    defects = []
    manifest = None
    try:
        root = Path(root)
        if (root/'manifest.json').is_symlink() or (root/'manifest.sha256').is_symlink():
            raise ValueError('manifest may not be a symlink')
        manifest = read_json(root/'manifest.json', 1048576)
        if (type(manifest) is not dict or set(manifest) != {'schema','name','version','model_sha256',
                'probe_sha256','source_evidence_kind','files'} or manifest['schema'] != 'interface-package/1'):
            raise ValueError('invalid package manifest')
        actual_manifest = digest(root/'manifest.json')
        checksum = (root/'manifest.sha256').read_text(encoding="utf-8").strip()
        if checksum != actual_manifest or (expected_manifest_sha256 is not None and actual_manifest != expected_manifest_sha256):
            raise ValueError('manifest checksum/pin mismatch')
        files = manifest['files']
        if type(files) is not dict or len(files) > 200:
            raise ValueError('invalid manifest file inventory')
        for name, value in files.items():
            p = PurePosixPath(name)
            if (p.is_absolute() or '..' in p.parts or str(p) != name or '\\' in name or
                    type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)):
                raise ValueError('invalid manifest path or digest')
        actual_files = package_files(root)
        if actual_files != files:
            missing = sorted(set(files) - set(actual_files))
            unexpected = sorted(set(actual_files) - set(files))
            changed = sorted(k for k in set(files) & set(actual_files) if files[k] != actual_files[k])
            detail = '; '.join(f'{label}: {", ".join(names)}' for label, names in (
                ('missing',missing),('unexpected',unexpected),('changed',changed)) if names)
            raise ValueError('package file inventory/hash mismatch; ' + detail)
        if (files.get('driver/model.json') != manifest['model_sha256'] or
                files.get('probe.json') != manifest['probe_sha256']):
            raise ValueError('manifest model/probe hash mismatch')
        if expected_model_sha256 is not None and manifest['model_sha256'] != expected_model_sha256:
            raise ValueError('package belongs to different model bytes')
        if manifest['source_evidence_kind'] not in ('live','replay'):
            raise ValueError('invalid package evidence kind')
        for needed in ('driver/__init__.py','driver/__main__.py','interface_runtime/engine.py',
                       'interface_runtime/transports.py','interface_runtime/evidence.py',
                       'interface_runtime/package.py','REFERENCE.md','requirements.txt'):
            if needed not in files:
                raise ValueError('required package file missing: ' + needed)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        defects.append({'where':'package','what':str(exc)})
    return {'ok':not defects,'defects':defects,'manifest':manifest}


def inspect_package(root, trusted_python, expected_model_sha256=None, expected_manifest_sha256=None):
    """Non-executing integrity and currency check, including historical runtime inventories.

    A matching self-contained manifest establishes integrity, not the trust to execute its Python.
    Callers that execute must also require current trusted bytes and copy only their allowlisted files.
    """
    checked = verify_package(root, expected_model_sha256, expected_manifest_sha256)
    files = checked['manifest']['files'] if checked['ok'] else {}
    current = checked['ok'] and {k:v for k,v in files.items() if k.endswith('.py')} == trusted_python
    return {**checked, 'integrity_ok':checked['ok'], 'runtime_current':bool(current)}

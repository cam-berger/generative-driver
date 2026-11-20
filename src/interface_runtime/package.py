"""Non-executing package integrity checks; hashes are integrity, not signatures.

A package states its driver once, in `driver/model.json`; its contract, protocol and tool catalog are checked as what
the runtime computes from that model (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3)."""
import hashlib
from pathlib import Path, PurePosixPath
from .evidence import read_json

_PACKAGE_FORMATS = ('interface-package/1', 'interface-package/2')


def _named(value):
    """A format name a package gives, bounded for a refusal's reason."""
    return repr(value)[:120]


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
        if type(manifest) is dict and manifest.get('schema') not in _PACKAGE_FORMATS:
            raise ValueError('package format is not one this kit knows: ' + _named(manifest.get('schema')))
        # The model's tag is looked at before anything is compared, so a package emitted with an earlier driver format
        # is refused by that name and not for a hash (docs/adr/0010-a-package-is-data-in-one-format.md).
        model = root/'driver/model.json'
        if model.is_file() and not model.is_symlink():
            from .engine import format_refusal
            refusal = format_refusal(read_json(model, 1048576))
            if refusal is not None:
                raise ValueError(refusal)
        fields = {'schema','name','version','model_sha256','probe_sha256','source_evidence_kind','files'}
        if type(manifest) is dict and manifest.get('schema') == 'interface-package/2':
            fields |= {'contract_sha256', 'protocol_sha256', 'mcp_catalog_sha256'}
        if type(manifest) is not dict or set(manifest) != fields:
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
        if manifest['schema'] == 'interface-package/2':
            for name in ('contract', 'protocol'):
                if files.get('driver/'+name+'.json') != manifest[name+'_sha256']:
                    raise ValueError('manifest '+name+' hash mismatch')
            from .contracts import contract_view, protocol_view
            from .engine import validate_model
            stated = read_json(root/'driver/model.json')
            validated = validate_model(stated)
            if not validated['ok']:
                raise ValueError('driver/model.json is not a valid interface model: '
                                 + '; '.join(d['where'] + ': ' + d['what'] for d in validated['defects'][:4]))
            contract = contract_view(stated)
            # The two files are copies of the model's views until the package's files change, so each is compared as a
            # whole and no tag of its own is looked at.
            for name, view in (('driver/contract.json', contract), ('driver/protocol.json', protocol_view(stated))):
                if read_json(root/name) != view:
                    raise ValueError(name + ' differs from the view of driver/model.json')
            from .catalog import render_catalog, RENDERERS
            catalog = read_json(root/'mcp-tools.json')
            if files.get('mcp-tools.json') != manifest['mcp_catalog_sha256']:
                raise ValueError('manifest MCP catalog hash mismatch')
            if type(catalog) is not dict or catalog.get('schema') != 'driver-mcp/1':
                raise ValueError('MCP catalog format is not one this kit knows: '
                                 + _named(catalog.get('schema') if type(catalog) is dict else catalog))
            if catalog.get('renderer_version') not in RENDERERS:
                raise ValueError('MCP catalog renderer version is not one this kit knows: '
                                 + _named(catalog.get('renderer_version')))
            # Each catalog is checked under the renderer it names, so a kit that renders new catalogs differently
            # still verifies the ones it wrote before.
            if catalog != render_catalog(contract, manifest['contract_sha256'],
                                         catalog.get('namespace'), catalog['renderer_version']):
                raise ValueError('MCP catalog differs from the public driver contract')
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

    A matching self-contained manifest establishes integrity, not the trust to execute its Python. The kit therefore
    never imports a package's Python: it runs its installed runtime over the package's data files (ADR 0008).
    """
    checked = verify_package(root, expected_model_sha256, expected_manifest_sha256)
    files = checked['manifest']['files'] if checked['ok'] else {}
    current = checked['ok'] and {k:v for k,v in files.items() if k.endswith('.py')} == trusted_python
    return {**checked, 'integrity_ok':checked['ok'], 'runtime_current':bool(current)}

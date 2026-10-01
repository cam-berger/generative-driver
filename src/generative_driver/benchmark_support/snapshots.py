"""Immutable public case inputs and the configuration captured before execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .registry import resolve_case, resource_path, pin_case


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def read_snapshot(run_dir: Path) -> dict:
    path = Path(run_dir) / 'benchmark/execution.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    body = {k: v for k, v in record.items() if k != 'snapshot_sha256'}
    if record.get('schema') != 'benchmark-execution-snapshot/1' or canonical_digest(body) != record.get('snapshot_sha256'):
        raise ValueError('Execution snapshot hash mismatch')
    for name, expected in record['public_files'].items():
        source = resource_path(path.parent / 'inputs', name)
        if source.is_symlink() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('Snapshot input hash mismatch: ' + name)
    return record


def require_snapshot(run_dir: Path, pin: dict) -> dict:
    record = read_snapshot(run_dir)
    if record['case_pin'] != pin:
        raise ValueError('Execution snapshot case pin mismatch')
    return record


def public_configuration(value):
    """Exclude evaluator handles and credentials from public execution evidence."""
    if isinstance(value, dict):
        return {key: public_configuration(item) for key, item in value.items()
                if key not in {'password', 'evaluator_password_file', 'authkey', 'api_key', 'token'}}
    if isinstance(value, list):
        return [public_configuration(item) for item in value]
    return value


def snapshot_case(run_dir: Path, pin: dict, provenance: dict) -> dict:
    target = Path(run_dir) / 'benchmark'
    target.mkdir(parents=True, exist_ok=True)
    public_files = {**pin['manifest'].get('images', {}), **pin['manifest'].get('assets', {})}
    body = {'schema': 'benchmark-execution-snapshot/1', 'case_pin': pin,
            'public_files': public_files, 'executed': public_configuration(provenance)}
    record = {**body, 'snapshot_sha256': canonical_digest(body)}
    if (target / 'execution.json').exists():
        saved = require_snapshot(run_dir, pin)
        if saved != record:
            raise ValueError('Execution snapshot provenance mismatch')
        return saved
    if pin_case(pin['case_id'], pin['scenario_id'], pin['case_seed']) != pin:
        raise ValueError('Case pin changed before capture')
    definition = resolve_case(pin['case_id'])
    temporary = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=target))
    try:
        inputs = temporary / 'inputs'
        inputs.mkdir()
        for name, expected in public_files.items():
            source = resource_path(definition.root, name)
            destination = resource_path(inputs, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
                raise ValueError('Snapshot source hash mismatch: ' + name)
        (temporary / 'execution.json').write_text(json.dumps(record, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        os.rename(inputs, target / 'inputs')
        os.replace(temporary / 'execution.json', target / 'execution.json')
    finally:
        shutil.rmtree(temporary)
    return require_snapshot(run_dir, pin)


def execution_provenance(config: dict) -> dict:
    """Capture implementation identity and nonsecret runtime settings before launch."""
    import platform
    from ..reporting import _tree_hash
    package = Path(__file__).resolve().parents[1]
    return {'toolchain_revision': hashlib.sha256(
                (_tree_hash(package) + _tree_hash(package.parent / 'interface_runtime')).encode()).hexdigest(),
            'skills_revision': _tree_hash(package / 'resources/toolchain/skills'),
            'evaluator_revision': _tree_hash(package / 'benchmark_support'),
            'runtime_configuration': public_configuration(config),
            'environment': {'system': platform.system(), 'release': platform.release(),
                            'architecture': platform.machine(), 'python': platform.python_version()}}

"""Static, read-only benchmark case admission and identity."""
from dataclasses import dataclass
from pathlib import Path
import copy
import hashlib
import json
import re
from types import SimpleNamespace


@dataclass(frozen=True)
class CaseDefinition:
    id: str
    family: str
    version: str
    evaluator_version: str
    execution: str
    evidence_track: str
    adapter_key: str
    approval_scope: str | None
    default_effects: tuple[str, ...]
    root: Path
    manifest: dict
    manifest_sha256: str


_BUILTINS = {
    'setup-smoke': ('setup-smoke', 'smoke'),
    'tq9': ('tq9', 'legacy-emulator'),
    'bme280': ('bme280', 'legacy-physical'),
    'tq9-v2': ('tq9-v2', 'emulator-v2'),
    'sampled-sensor-v1': ('sampled-sensor-v1', 'emulator-v2'),
    'parameter-store-v1': ('parameter-store-v1', 'emulator-v2'),
}


def case_ids() -> tuple[str, ...]:
    return tuple(_BUILTINS)


def case_descriptors() -> list[dict]:
    from ..benchmark import case_root
    root = case_root()
    rows = []
    for case_id in case_ids():
        if case_id != 'setup-smoke' and not (root/'cases'/case_id/'case.json').is_file():
            rows.append({'id': case_id, 'status': 'pending'})
            continue
        try:
            case = resolve_case(case_id)
        except ValueError:
            if case_id in ('tq9', 'bme280'):
                raise
            rows.append({'id': case_id, 'status': 'pending'})
            continue
        rows.append({'id': case.id, 'status': 'available', 'family': case.family,
                     'version': case.version, 'execution': case.execution,
                     'evidence_track': case.evidence_track,
                     'scenarios': copy.deepcopy(case.manifest.get('scenarios', ['identity'] if case.id == 'tq9' else []))})
    return rows


def _legacy_emulator():
    from . import cases, legacy
    return SimpleNamespace(prepare_stage=cases.prepare_stage, check_stage=cases.check_stage,
                           package_execute=legacy.package_execute, cleanup=legacy.cleanup,
                           score=legacy.score)


def _legacy_physical():
    from . import physical, legacy
    return SimpleNamespace(prepare_stage=physical.prepare_stage, check_stage=physical.check_stage,
                           package_execute=physical.package_execute, cleanup=legacy.cleanup,
                           score=legacy.score)


def _smoke():
    from . import legacy
    def unsupported(*args, **kwargs):
        raise ValueError('Setup smoke has no agent stages')
    return SimpleNamespace(prepare_stage=unsupported, check_stage=unsupported,
                           package_execute=unsupported, cleanup=unsupported, score=legacy.score)


_ADAPTERS = {'smoke': _smoke, 'legacy-emulator': _legacy_emulator,
             'legacy-physical': _legacy_physical}


def adapter_for(case_id: str):
    definition = resolve_case(case_id)
    loader = _ADAPTERS.get(definition.adapter_key)
    if loader is None:
        raise ValueError('Benchmark adapter is pending: ' + definition.adapter_key)
    return loader()


def resolve_case(case_id: str) -> CaseDefinition:
    if not isinstance(case_id, str) or case_id not in _BUILTINS:
        raise ValueError('Unknown benchmark case: ' + str(case_id))
    from ..benchmark import case_root
    root = case_root()
    if case_id == 'setup-smoke':
        return CaseDefinition(case_id, 'setup-smoke', '1', '1', 'scripted-replay', 'recorded-replay',
                              'smoke', None, (), root/'cases'/case_id, {}, '')
    path = root/'cases'/case_id/'case.json'
    if not path.is_file():
        raise ValueError('Benchmark case is pending: ' + case_id)
    return load_definition(path, resource_root=root)


def resource_path(root: Path, relative: str) -> Path:
    root = Path(root)
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '\\' in relative:
        raise ValueError('Invalid case resource path')
    parts = Path(relative).parts
    if any(part in ('.', '..') for part in parts):
        raise ValueError('Case resource path escapes root')
    candidate = root/relative
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError('Case resource path escapes root')
    return candidate


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('Malformed SHA-256')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest key: ' + key)
        result[key] = value
    return result


def load_definition(path: Path, *, resource_root: Path) -> CaseDefinition:
    path = Path(path)
    root = Path(resource_root).resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('Case manifest escapes resources')
    raw = path.read_bytes()
    manifest = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or manifest.get('schema') not in ('benchmark-case/1', 'benchmark-case/2'):
        raise ValueError('Unsupported case schema')
    case_id = manifest.get('id')
    if not isinstance(case_id, str) or case_id != path.parent.name or case_id not in _BUILTINS:
        raise ValueError('Case ID mismatch or unknown case')
    if manifest['schema'] != ('benchmark-case/1' if case_id in ('tq9', 'bme280') else 'benchmark-case/2'):
        raise ValueError('Case schema does not match registered version')
    identity_fields = {'version', 'evaluator_version', 'execution'}
    if identity_fields - manifest.keys() or any(not isinstance(manifest[key], str) or not manifest[key]
                                                for key in identity_fields if key in manifest):
        raise ValueError('Missing or invalid required case identity fields')
    adapter_key = _BUILTINS[case_id][1]
    if 'adapter_key' in manifest and manifest['adapter_key'] != adapter_key:
        raise ValueError('Unknown case adapter key')
    if manifest['schema'] == 'benchmark-case/2':
        required = {'family', 'evidence_track', 'scenarios', 'required_stages', 'images',
                    'truth', 'adapter_key', 'time_policy', 'approval_scope',
                    'default_effects', 'provenance', 'limitations'}
        if required - manifest.keys():
            raise ValueError('Missing required v2 case fields: ' + ', '.join(sorted(required - manifest.keys())))
        if not isinstance(manifest['scenarios'], list) or not manifest['scenarios']:
            raise ValueError('V2 case requires scenarios')
        scenarios = [row.get('id') if isinstance(row, dict) else row for row in manifest['scenarios']]
        if any(not isinstance(name, str) or not name or '/' in name or '\\' in name for name in scenarios):
            raise ValueError('Invalid v2 scenario ID')
        if len(scenarios) != len(set(scenarios)):
            raise ValueError('duplicate scenario ID')
        if not isinstance(manifest['required_stages'], list) or not manifest['required_stages']:
            raise ValueError('V2 case requires stages')
        if not isinstance(manifest['images'], dict) or not manifest['images']:
            raise ValueError('V2 case requires images')
        if not isinstance(manifest['truth'], dict) or not manifest['truth']:
            raise ValueError('V2 case requires truth')
        effects = manifest['default_effects']
        if not isinstance(effects, list) or any(effect not in ('read', 'write', 'actuate') for effect in effects) or len(effects) != len(set(effects)):
            raise ValueError('Invalid v2 default effects')
        if manifest['execution'] != 'actual-agent-emulation' or manifest['evidence_track'] != 'firmware':
            raise ValueError('Unsupported v2 execution or evidence track')
    for name, expected in manifest.get('images', {}).items():
        _sha(expected)
        image = resource_path(path.parent, name)
        if not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest() != expected:
            raise ValueError('Case image hash mismatch: ' + name)
    for name, expected in manifest.get('assets', {}).items():
        _sha(expected)
        asset = resource_path(path.parent, name)
        if not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != expected:
            raise ValueError('Case asset hash mismatch: ' + name)
    truth = manifest.get('truth', {})
    if truth:
        _sha(truth.get('sha256'))
        truth_path = resource_path(root, truth.get('path'))
        if not truth_path.is_file() or hashlib.sha256(truth_path.read_bytes()).hexdigest() != truth['sha256']:
            raise ValueError('Case truth hash mismatch')
    if case_id == 'tq9':
        family, track, approval, effects = 'tq9', 'firmware', 'emulator', ('write', 'actuate')
    elif case_id == 'bme280':
        family, track, approval, effects = 'bme280', 'physical', 'bound-device', ('write',)
    else:
        family = manifest.get('family')
        track = manifest.get('evidence_track')
        approval = manifest.get('approval_scope')
        effects = tuple(manifest.get('default_effects', ()))
    return CaseDefinition(case_id, family, str(manifest['version']), str(manifest['evaluator_version']),
                          manifest['execution'], track, adapter_key, approval, effects, path.parent,
                          copy.deepcopy(manifest), hashlib.sha256(raw).hexdigest())


def pin_case(case_id: str, scenario_id: str | None, case_seed: int) -> dict:
    if type(case_seed) is not int:
        raise ValueError('case_seed must be an integer')
    definition = resolve_case(case_id)
    if definition.id in ('tq9', 'bme280', 'setup-smoke'):
        default = 'identity' if definition.id == 'tq9' else None
        if scenario_id not in (None, default) or case_seed != 0:
            raise ValueError('Legacy case does not support the selected scenario or seed')
        scenario_id = default
        calibration = {'status': 'legacy-not-required'}
    else:
        scenarios = definition.manifest.get('scenarios', [])
        names = [row.get('id') if isinstance(row, dict) else row for row in scenarios]
        if scenario_id not in names:
            raise ValueError('Unknown case scenario')
        calibration = definition.manifest.get('calibration', {})
    manifest = copy.deepcopy(definition.manifest)
    return {'schema': 'benchmark-case-pin/1', 'case': definition.id,
            'case_id': definition.id, 'case_version': definition.version,
            'evaluator_version': definition.evaluator_version, 'family': definition.family,
            'scenario_id': scenario_id, 'case_seed': case_seed, 'manifest': manifest,
            'manifest_sha256': definition.manifest_sha256,
            'image_hashes': copy.deepcopy(manifest.get('images', {})),
            'truth_sha256': manifest.get('truth', {}).get('sha256'),
            'execution': definition.execution, 'evidence_track': definition.evidence_track,
            'scope': manifest.get('scope', 'full-workflow' if definition.id == 'tq9' else 'installation' if definition.id == 'setup-smoke' else 'full-workflow'),
            'time_policy': copy.deepcopy(manifest.get('time_policy', {})),
            'calibration': copy.deepcopy(calibration)}

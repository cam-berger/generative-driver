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


class PendingCaseError(ValueError):
    """A registered case is missing distributable build outputs."""


_BUILTINS = {
    'setup-smoke': ('setup-smoke', 'smoke'),
    'tq9': ('tq9', 'legacy-emulator'),
    'bme280': ('bme280', 'legacy-physical'),
    'tq9-v2': ('tq9-v2', 'emulator-v2'),
    'sampled-sensor-v1': ('sampled-sensor-v1', 'emulator-v2'),
    'parameter-store-v1': ('parameter-store-v1', 'emulator-v2'),
}


CURRENT_VERSIONS = {'tq9': '2', 'bme280': '3', 'tq9-v2': '3',
                    'sampled-sensor-v1': '3', 'parameter-store-v1': '3'}


def validate_workflow_manifest(manifest):
    from ..benchmark import STAGES
    case_id = manifest.get('id')
    version = CURRENT_VERSIONS.get(case_id)
    if version is None or manifest.get('version') != version or manifest.get('evaluator_version') != version:
        raise ValueError('Unsupported case or evaluator version identity')
    if manifest.get('required_stages') != list(STAGES):
        raise ValueError('Incompatible required stage inventory: six acceptance gates are required')
    scenarios = [row.get('id') if isinstance(row, dict) else row for row in manifest.get('scenarios', [])]
    if scenarios != ['original']:
        raise ValueError('Unsupported case scenario inventory: original is required')
    if case_id != 'bme280' and list(manifest.get('images', {})) != ['firmware.bin']:
        raise ValueError('Stable case requires one original firmware image')


def validate_public_workflow_identity(pin):
    version = CURRENT_VERSIONS.get(pin.get('case_id'))
    if (version is None or pin.get('case_version') != version or pin.get('evaluator_version') != version
            or pin.get('scenario_id') != 'original'):
        raise ValueError('Unsupported pinned workflow identity or version')


def validate_pinned_workflow(pin):
    validate_public_workflow_identity(pin)
    manifest = pin.get('manifest', {})
    validate_workflow_manifest(manifest)
    if (pin.get('case_id') != manifest['id'] or pin.get('case_version') != manifest['version']
            or pin.get('evaluator_version') != manifest['evaluator_version']
            or pin.get('scenario_id') != 'original'):
        raise ValueError('Conflicting pinned case identity or version')


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
        except ValueError as error:
            if case_id in ('tq9', 'bme280'):
                raise
            rows.append({'id': case_id, 'status': 'pending' if isinstance(error, PendingCaseError) else 'invalid',
                         'error': str(error)})
            continue
        calibration = case.manifest.get('calibration', {})
        status = 'pending' if case.adapter_key == 'emulator-v2' and calibration.get('status') != 'passed' else 'available'
        rows.append({'id': case.id, 'status': status, 'family': case.family,
                     **({'calibration': copy.deepcopy(calibration)} if case.adapter_key == 'emulator-v2' else {}),
                     'version': case.version, 'execution': case.execution,
                     'evidence_track': case.evidence_track,
                     'scenarios': copy.deepcopy(case.manifest.get('scenarios', []))})
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


def _emulated():
    from . import emulated
    return emulated


_ADAPTERS = {'emulator-v2': _emulated, 'smoke': _smoke, 'legacy-emulator': _legacy_emulator,
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
        raise PendingCaseError('Benchmark case is pending: ' + case_id)
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
    validate_workflow_manifest(manifest)
    if 'calibration' in manifest and not isinstance(manifest['calibration'], dict):
        raise ValueError('Invalid calibration map')
    for field in ('images', 'assets', 'truth'):
        if field in manifest and not isinstance(manifest[field], dict):
            raise ValueError('Invalid case resource map: ' + field)
    for name, expected in manifest.get('images', {}).items():
        _sha(expected)
        image = resource_path(path.parent, name)
        if not image.is_file():
            raise PendingCaseError('Case image is unbuilt: ' + name)
        if hashlib.sha256(image.read_bytes()).hexdigest() != expected:
            raise ValueError('Case image hash mismatch: ' + name)
    for name, expected in manifest.get('assets', {}).items():
        _sha(expected)
        asset = resource_path(path.parent, name)
        if not asset.is_file():
            raise PendingCaseError('Case asset is unbuilt: ' + name)
        if hashlib.sha256(asset.read_bytes()).hexdigest() != expected:
            raise ValueError('Case asset hash mismatch: ' + name)
    truth = manifest.get('truth', {})
    if truth:
        _sha(truth.get('sha256'))
        truth_path = resource_path(root, truth.get('path'))
        if not truth_path.is_file():
            raise PendingCaseError('Case truth is unbuilt')
        if hashlib.sha256(truth_path.read_bytes()).hexdigest() != truth['sha256']:
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
    if scenario_id not in (None, 'original'):
        raise ValueError('Unknown case scenario')
    definition = resolve_case(case_id)
    if definition.id in ('tq9', 'bme280', 'setup-smoke'):
        default = None if definition.id == 'setup-smoke' else 'original'
        if scenario_id not in (None, default) or case_seed != 0:
            raise ValueError('Legacy case does not support the selected scenario or seed')
        scenario_id = default
        calibration = {'status': 'legacy-not-required'}
    else:
        scenarios = definition.manifest.get('scenarios', [])
        names = [row.get('id') if isinstance(row, dict) else row for row in scenarios]
        scenario_id = scenario_id or 'original'
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


def require_calibration(pin: dict, options: dict) -> dict:
    """Admission is evaluator-owned; public labels alone cannot launch an agent."""
    current = pin_case(pin['case_id'], pin['scenario_id'], pin['case_seed'])
    if pin != current:
        raise ValueError('Calibration admission case pin changed')
    if pin['manifest'].get('schema') != 'benchmark-case/2':
        return {'ok': True}
    if pin.get('calibration', {}).get('status') != 'passed':
        raise ValueError('V2 case requires independently validated passed calibration before a run')
    from ..benchmark import case_root
    from .emulator import truth_for_case
    from .reference_calibration import input_hashes, validate_record, CORE_MUTANTS, tq9_execution_inputs
    from .snapshots import canonical_digest
    manifest = pin['manifest']
    try:
        truth = truth_for_case(case_root(), manifest, options)
        if truth.get('schema') == 'benchmark-family-truth/1':
            from .calibration import hydrate_truth
            truth = hydrate_truth(truth)
    except (OSError, ValueError) as error:
        raise ValueError('Authenticated calibration evidence requires the evaluator password file') from error
    record = truth.get('calibration', {})
    try:
        if manifest.get('family') in ('sampled-sensor', 'parameter-store'):
            from .calibration import validate_calibration, apply_mutation, execution_input_identity
            expected = {r['id']: r['expected_failed_checks'] for r in truth['mutations']['mutants']}
            changes = {}
            mutant_inputs = {}
            for mutation in truth['mutations']['mutants']:
                source = truth['references'][mutation['reference']]
                changed = apply_mutation(source['model'], source['capabilities'], mutation)
                mutant_inputs[mutation['id']]=execution_input_identity(changed['model'],changed['capabilities'])
                changes[mutation['id']] = (changed['source_sha256'], changed['mutated_sha256'])
            valid = (truth.get('inventory_state') == 'qualified'
                     and canonical_digest(record) == manifest['calibration']['evidence_sha256']
                     and truth['input_hashes'] == record['input_hashes']
                     and truth['images'] == manifest['images']
                     and {r['id']: r['expected_failed_checks'] for r in record['mutants']} == expected
                     and {r['id']: (r['source_sha256'], r['mutated_sha256']) for r in record['mutants']} == changes
                     and {r['id']: r['model_sha256'] for r in record['references']} ==
                         {name: canonical_digest(ref['model']) for name, ref in truth['references'].items()}
                     and validate_calibration(manifest, record)['ok'])
            from .emulated import family_for
            family=family_for(pin['case_id'])
            runs={r['id']:r for r in record['runs']}
            for reference in record['references']:
                source=truth['references'][reference['id']]
                for name in reference['runs']:
                    run=runs[name];model=source['model']
                    valid = (valid and reference['execution_inputs'][name]==execution_input_identity(model,source['capabilities']))
            for row in record['mutants']:
                valid = (valid and row['execution_input']==mutant_inputs[row['id']])
            for run in runs.values():
                scenario=run['scenario']
                artifact=run['package_sha256'] or run['model_sha256']
                contract=family.contract({'family':manifest['family'],'scenario_id':scenario,'case_seed':0,'revision':0},
                    {**truth,'artifact_sha256':artifact},run['phase'])
                valid = (valid and canonical_digest(contract)==run['contract_sha256']
                         and [c['id'] for c in contract['checks']]==run['check_ids'])
            rows={r['id']:r for r in record['mutants']}
            for mutation in truth['mutations']['mutants']:
                run=runs[rows[mutation['id']]['run']]
                valid = (valid and run['phase']==mutation['phase']
                         and run['scenario']==mutation.get('scenario','original'))

        else:
            valid = (canonical_digest(record) == manifest['calibration']['evidence_sha256']
                     and input_hashes(truth) == record['input_hashes']
                     and truth['images'] == manifest['images']
                     and set(record['required_mutants']) == set(CORE_MUTANTS) | set(truth['mutations']['required'])
                     and record['execution_inputs']==tq9_execution_inputs(truth)
                     and {r['id']: r['expected_failed_checks'] for r in record['mutants']} ==
                         truth['mutations']['expected_failed_checks']
                     and validate_record(manifest, record)['ok'])
            from .tq9_v2 import contract as tq9_contract
            for run in record['runs']:
                artifact=run['package_sha256'] or run['model_sha256']
                contract=tq9_contract({'scenario_id':run['scenario'],'case_seed':0,'revision':0},
                    {**truth,'artifact_sha256':artifact},run['phase'])
                valid = (valid and canonical_digest(contract)==run['contract_sha256']
                         and [c['id'] for c in contract['checks']]==run['check_ids'])
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('Calibration evidence is missing, stale, or incomplete; rerun native calibration')
    return {'ok': True, 'evidence_sha256': canonical_digest(record)}

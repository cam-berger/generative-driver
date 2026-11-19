"""Static, read-only benchmark case admission and identity."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import json
import re
from .families import family_for
from .registrations import registered_case_ids, registration_for


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


CURRENT_VERSIONS = {case_id: registration_for(case_id).version for case_id in registered_case_ids()
                    if registration_for(case_id).schema is not None}


def validate_workflow_manifest(manifest):
    from .case_assets import STAGES
    case_id = manifest.get('id')
    version = CURRENT_VERSIONS.get(case_id)
    if version is None or manifest.get('version') != version or manifest.get('evaluator_version') != version:
        raise ValueError('Unsupported case or evaluator version identity')
    if manifest.get('required_stages') != list(STAGES):
        raise ValueError('Incompatible required stage inventory: six acceptance gates are required')
    scenarios = [row.get('id') if isinstance(row, dict) else row for row in manifest.get('scenarios', [])]
    if scenarios != ['original']:
        raise ValueError('Unsupported case scenario inventory: original is required')
    from .execution import artifact_name
    if registration_for(case_id).single_image and list(manifest.get('images', {})) != [artifact_name(manifest)]:
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
    return registered_case_ids()


def case_descriptors() -> list[dict]:
    """Discover public assets and labels without authenticating admission."""
    from .case_assets import case_root
    root = case_root()
    rows = []
    for case_id in case_ids():
        registration = registration_for(case_id)
        if registration.has_case_file and not (root/'cases'/case_id/'case.json').is_file():
            rows.append({'id': case_id, 'status': 'pending'})
            continue
        try:
            case = resolve_case(case_id)
        except ValueError as error:
            if not registration.requires_calibration:
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
                     **({'execution_setup': copy.deepcopy(case.manifest['execution_setup'])}
                        if 'execution_setup' in case.manifest else {}),
                     'scenarios': copy.deepcopy(case.manifest.get('scenarios', []))})
    return [{**row, 'availability_basis': 'public-metadata',
             'authenticated_admission': 'not-checked'} for row in rows]


def resolve_case(case_id: str, *, check_truth: bool = True) -> CaseDefinition:
    registration = registration_for(case_id)
    from .case_assets import case_root
    root = case_root()
    if not registration.has_case_file:
        return CaseDefinition(case_id, registration.family, registration.version, registration.version,
                              registration.execution, registration.evidence_track, registration.adapter_key,
                              registration.approval_scope, tuple(registration.default_effects), root/'cases'/case_id,
                              {}, '')
    path = root/'cases'/case_id/'case.json'
    if not path.is_file():
        raise PendingCaseError('Benchmark case is pending: ' + case_id)
    return load_definition(path, resource_root=root, check_truth=check_truth)


def case_selection(case_id: str) -> SimpleNamespace:
    """The `id`, `execution`, `evidence_track`, `approval_scope` and `default_effects` of a case, for a site that reads
    only those. A row that fills them all (a generated case's: it has no manifest to read) answers alone. Any other
    answers with `resolve_case` for what its row leaves null, so such a site still refuses a changed case."""
    row, names = registration_for(case_id), ('execution', 'evidence_track', 'approval_scope', 'default_effects')
    values = {name: getattr(row, name) for name in names}
    if None in values.values():
        definition = resolve_case(case_id)
        values = {name: getattr(definition, name) if value is None else value for name, value in values.items()}
    return SimpleNamespace(id=case_id, **values)


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


def _refuse_unsafe_path(relative) -> None:
    """The lexical refusals of `resource_path`, for a path whose file is not to be touched: the resolve in its
    containment check would follow a symlink a worker planted."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '\\' in relative:
        raise ValueError('Invalid case resource path')
    if any(part in ('.', '..') for part in Path(relative).parts):
        raise ValueError('Case resource path escapes root')


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


def load_definition(path: Path, *, resource_root: Path, check_truth: bool = True) -> CaseDefinition:
    """The checked definition of the case whose manifest is at `path`. With `check_truth` False the truth file is
    neither looked for, hashed nor resolved, so that stages and grading do not depend on a file a worker could change;
    the manifest's truth hash and the truth path's form are still validated."""
    path = Path(path)
    root = Path(resource_root).resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('Case manifest escapes resources')
    raw = path.read_bytes()
    manifest = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or manifest.get('schema') not in ('benchmark-case/1', 'benchmark-case/2'):
        raise ValueError('Unsupported case schema')
    case_id = manifest.get('id')
    if not isinstance(case_id, str) or case_id != path.parent.name or case_id not in case_ids():
        raise ValueError('Case ID mismatch or unknown case')
    registration = registration_for(case_id)
    if manifest['schema'] != registration.schema:
        raise ValueError('Case schema does not match registered version')
    identity_fields = {'version', 'evaluator_version', 'execution'}
    if identity_fields - manifest.keys() or any(not isinstance(manifest[key], str) or not manifest[key]
                                                for key in identity_fields if key in manifest):
        raise ValueError('Missing or invalid required case identity fields')
    adapter_key = registration.adapter_key
    if 'adapter_key' in manifest and manifest['adapter_key'] != adapter_key:
        raise ValueError('Unknown case adapter key')
    if manifest['schema'] == 'benchmark-case/2':
        required = {'family', 'evidence_track', 'scenarios', 'required_stages', 'images',
                    'truth', 'adapter_key', 'time_policy', 'approval_scope',
                    'default_effects', 'provenance', 'limitations'}
        if required - manifest.keys():
            raise ValueError('Missing required v2 case fields: ' + ', '.join(sorted(required - manifest.keys())))
        if manifest['family'] != registration.family:
            raise ValueError('Case family does not match registered identity')
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
        if not check_truth:
            _refuse_unsafe_path(truth.get('path'))
        else:
            truth_path = resource_path(root, truth.get('path'))
            if not truth_path.is_file():
                raise PendingCaseError('Case truth is unbuilt')
            if hashlib.sha256(truth_path.read_bytes()).hexdigest() != truth['sha256']:
                raise ValueError('Case truth hash mismatch')
    if not registration.requires_calibration:
        family, track = registration.family, registration.evidence_track
        approval, effects = registration.approval_scope, tuple(registration.default_effects)
    else:
        family = manifest.get('family')
        track = manifest.get('evidence_track')
        approval = manifest.get('approval_scope')
        effects = tuple(manifest.get('default_effects', ()))
    return CaseDefinition(case_id, family, str(manifest['version']), str(manifest['evaluator_version']),
                          manifest['execution'], track, adapter_key, approval, effects, path.parent,
                          copy.deepcopy(manifest), hashlib.sha256(raw).hexdigest())


def pin_case(case_id: str, scenario_id: str | None, case_seed: int, *, check_truth: bool = True) -> dict:
    if type(case_seed) is not int:
        raise ValueError('case_seed must be an integer')
    if scenario_id not in (None, 'original'):
        raise ValueError('Unknown case scenario')
    definition = resolve_case(case_id, check_truth=check_truth)
    registration = registration_for(case_id)
    if not registration.requires_calibration:
        default = 'original' if registration.has_case_file else None
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
            'scope': manifest.get('scope', registration.default_scope),
            'time_policy': copy.deepcopy(manifest.get('time_policy', {})),
            'calibration': copy.deepcopy(calibration)}


def case_truth(case_id, run_dir, password):
    """The case's `UnlockedTruth`, decrypted with `password`. The manifest is the run's pinned snapshot when `run_dir`
    holds one. The truth is hydrated by the case's family; one that declares no `hydrate_truth` gets it back as
    unlocked."""
    from .case_assets import case_inputs, case_root
    from .emulator import truth_for_case
    from .truth import UnlockedTruth
    _, manifest = case_inputs(case_id, run_dir)
    return UnlockedTruth(family_for(case_id).hydrate_truth(truth_for_case(case_root(), manifest, password)),
                         manifest['truth']['sha256'])


def require_calibration(pin: dict, password) -> dict:
    """The admission of `admitted_truth`, for a caller with no use for the truth."""
    return admitted_truth(pin, password)[1]


def admitted_truth(pin: dict, password) -> tuple:
    """Admission is evaluator-owned; public labels alone cannot launch an agent. (`UnlockedTruth` or None, admission): a
    changed pin, or a v2 pin without passed calibration, raises before anything is decrypted, and a schema-1 pin, which
    has no calibration to authenticate, is admitted with None and nothing decrypted."""
    current = family_for(pin['case_id']).current_pin(pin)
    if pin != current:
        raise ValueError('Calibration admission case pin changed')
    if pin['manifest'].get('schema') != 'benchmark-case/2':
        return None, {'ok': True}
    if pin.get('calibration', {}).get('status') != 'passed':
        raise ValueError('V2 case requires independently validated passed calibration before a run')
    from .case_assets import case_root
    from .emulator import truth_for_case
    from .execution import validate_setup
    from .snapshots import canonical_digest
    from .truth import UnlockedTruth
    manifest = pin['manifest']
    family = family_for(pin['case_id'])
    try:
        truth = family.hydrate_truth(truth_for_case(case_root(), manifest, password))
    except (OSError, ValueError) as error:
        raise ValueError('Authenticated calibration evidence requires the evaluator password file') from error
    record = truth.get('calibration', {})
    validate_setup(manifest, truth)
    try:
        valid = family.admission_valid(pin, truth, record)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError('Calibration evidence is missing, stale, or incomplete; rerun native calibration')
    return UnlockedTruth(truth, manifest['truth']['sha256']), {'ok': True, 'evidence_sha256': canonical_digest(record)}

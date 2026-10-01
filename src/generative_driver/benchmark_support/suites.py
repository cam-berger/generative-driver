"""Suite selection, frozen inputs and a thin client of the existing owner."""
import copy
import math


def normalize_manifest(manifest: dict) -> dict:
    required = {'schema', 'id', 'version', 'entries', 'repetitions',
                'child_budget_seconds', 'suite_budget_seconds', 'max_active_children'}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError('Suite manifest fields are incomplete or unknown')
    result = copy.deepcopy(manifest)
    if result['schema'] != 'benchmark-suite/1':
        raise ValueError('Unsupported suite schema')
    if any(not isinstance(result[key], str) or not result[key].strip() for key in ('id', 'version')):
        raise ValueError('Suite id and version must be nonblank strings')
    if type(result['repetitions']) is not int or result['repetitions'] < 1:
        raise ValueError('Repetitions must be a positive integer')
    if type(result['max_active_children']) is not int or result['max_active_children'] != 1:
        raise ValueError('Suites support one active child')
    for key in ('child_budget_seconds', 'suite_budget_seconds'):
        value = result[key]
        if type(value) not in (int, float) or not 0 < value <= 604800 or not math.isfinite(value):
            raise ValueError('Suite budgets must be finite, positive and at most seven days')
    if result['child_budget_seconds'] > result['suite_budget_seconds']:
        raise ValueError('Child budget exceeds suite budget')
    if not isinstance(result['entries'], list) or not result['entries']:
        raise ValueError('Suite requires entries')
    identities = set()
    for entry in result['entries']:
        if not isinstance(entry, dict) or set(entry) != {'case', 'scenario', 'case_seed'}:
            raise ValueError('Invalid suite entry fields')
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ('case', 'scenario')):
            raise ValueError('Case and scenario must be nonblank strings')
        if type(entry['case_seed']) is not int:
            raise ValueError('Case seed must be an integer, never a model seed')
        identity = (entry['case'], entry['scenario'], entry['case_seed'])
        if identity in identities:
            raise ValueError('Duplicate suite entry')
        identities.add(identity)
    return result


def expand_trials(manifest: dict) -> list[dict]:
    manifest = normalize_manifest(manifest)
    return [dict(entry, ordinal=repeat * len(manifest['entries']) + index,
                 entry_index=index, repeat_index=repeat,
                 trial_key=f'entry-{index:03d}-repeat-{repeat:03d}')
            for repeat in range(manifest['repetitions'])
            for index, entry in enumerate(manifest['entries'])]


def preflight_selection(manifest: dict) -> tuple[dict, list, list[dict]]:
    """Check public selection and pin registered assets without starting processes.

    Authenticated calibration and runtime configuration remain owner admission.
    """
    from .registry import resolve_case, pin_case
    normalized = normalize_manifest(manifest)
    definitions = [resolve_case(entry['case']) for entry in normalized['entries']]
    if any(case.execution != 'actual-agent-emulation' for case in definitions):
        raise ValueError('Suite requires emulated actual-agent cases')
    if len({case.evidence_track for case in definitions}) != 1:
        raise ValueError('Mixed evidence tracks cannot share a suite aggregate')
    pins = [pin_case(entry['case'], entry['scenario'], entry['case_seed']) for entry in normalized['entries']]
    for definition, pin in zip(definitions, pins):
        calibration = pin.get('calibration', {})
        status = calibration.get('status') if isinstance(calibration, dict) else None
        allowed_status = 'legacy-not-required' if definition.id == 'tq9' else 'passed'
        if status != allowed_status:
            raise ValueError('Suite contains a case without required reference calibration')
        if pin.get('scope') != 'full-workflow':
            raise ValueError('Stage-local evidence cannot enter a full-workflow suite')
    return normalized, definitions, pins


def start_suite(manifest: dict, executor: str = 'codex', options: dict | None = None,
                request_id: str | None = None, *, home=None, autostart=True) -> dict:
    """Reconnect before mutable preflight; never become an execution owner."""
    from pathlib import Path
    from ..client import call, default_home
    normalized = normalize_manifest(manifest)
    if options is not None and not isinstance(options, dict):
        raise ValueError('Suite options must be an object')
    selected = dict(options or {})
    directory = Path(home or default_home()).expanduser().resolve()
    selected_home = selected.pop('home', None)
    if selected_home is not None:
        if not isinstance(selected_home, str) or not selected_home.strip():
            raise ValueError('Suite options.home must be a nonblank path')
        if Path(selected_home).expanduser().resolve() != directory:
            raise ValueError('Suite options.home must match the configured owner home')
    params = {'manifest': normalized, 'executor': executor, 'options': selected, 'request_id': request_id}
    if call('ping', {}, directory, autostart=False).get('ok'):
        # Saved-request dedup precedes asset reads in the owner. If it disappears
        # after this ping, preserve that failure instead of creating another owner.
        return call('suite_start', params, directory, autostart=False)
    preflight_selection(normalized)
    return call('suite_start', params, directory, autostart=autostart)


def freeze_suite(manifest: dict, executor: str, executor_config: dict,
                 options: dict, provenance: dict) -> dict:
    """Pin inputs without creating an owner, run, snapshot or native process.

    This private frozen object is not a public result. The child owner separately
    authenticates v2 calibration at admission before starting any operation.
    """
    import hashlib
    import json
    from ..configurator import validate_scoped_approval
    normalized = normalize_manifest(manifest)
    allowed = {'renode', 'ghidra_home', 'java_home', 'evaluator_password_files', 'scoped_tool_approval'}
    if not isinstance(options, dict) or set(options) - allowed:
        raise ValueError('Unknown suite options')
    if (executor not in ('codex', 'goose') or not isinstance(executor_config, dict)
            or not isinstance(executor_config.get('command'), list) or not executor_config['command']
            or any(not isinstance(arg, str) or not arg.strip() for arg in executor_config['command'])):
        raise ValueError('Configure a worker runtime command before starting a suite')
    if options.get('scoped_tool_approval') not in (None, 'emulator'):
        raise ValueError('Suites permit only explicit emulator approval')
    for key in ('renode', 'ghidra_home', 'java_home'):
        if key in options and (not isinstance(options[key], str) or not options[key].strip()):
            raise ValueError('Suite tool paths must be nonblank strings')
    selected = {entry['case'] for entry in normalized['entries']}
    handles = options.get('evaluator_password_files', {})
    if (not isinstance(handles, dict) or set(handles) - selected
            or any(not isinstance(value, str) or not value.strip() for value in handles.values())):
        raise ValueError('Evaluator handles must name selected cases and file paths')
    normalized, definitions, pins = preflight_selection(normalized)
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    frozen = copy.deepcopy({'manifest': normalized, 'manifest_sha256': hashlib.sha256(encoded).hexdigest(),
        'request': {'manifest': normalized, 'executor': executor, 'options': options},
        'executor': executor, 'executor_config': executor_config, 'options': options,
        'scope': 'full-workflow', 'entry_pins': pins,
        'entry_effects': [list(case.default_effects) for case in definitions],
        'provenance': provenance, 'trials': expand_trials(normalized)})
    for trial in frozen['trials']:
        validate_scoped_approval(child_request(frozen, dict(trial, child_request_id='freeze-' + trial['trial_key'])))
    return frozen


def child_request(frozen: dict, trial: dict) -> dict:
    """Project only this reserved slot's evaluator inputs into a child request."""
    if not isinstance(trial, dict) or type(trial.get('ordinal')) is not int:
        raise ValueError('Child requires a frozen trial slot')
    ordinal = trial['ordinal']
    if not 0 <= ordinal < len(frozen['trials']):
        raise ValueError('Child trial ordinal is outside the frozen suite')
    expected = frozen['trials'][ordinal]
    if any(type(trial.get(key)) is not type(value) or trial.get(key) != value
           for key, value in expected.items()):
        raise ValueError('Child trial identity differs from the frozen slot')
    if not isinstance(trial.get('child_request_id'), str) or not trial['child_request_id'].strip():
        raise ValueError('Child trial requires a reserved request ID')
    options = copy.deepcopy(frozen['options'])
    handles = options.pop('evaluator_password_files', {})
    approval = options.pop('scoped_tool_approval', None)
    if trial['case'] in handles:
        options['evaluator_password_file'] = handles[trial['case']]
    pin = copy.deepcopy(frozen['entry_pins'][trial['entry_index']])
    options.update(scenario_id=pin['scenario_id'], case_seed=pin['case_seed'])
    return {'goal': 'Execute one registered benchmark trial with checked evidence.',
            'case': trial['case'], 'case_pin': pin, 'executor': frozen['executor'],
            'executor_config': copy.deepcopy(frozen['executor_config']), 'case_options': options,
            'request_id': trial['child_request_id'],
            'effects': list(frozen['entry_effects'][trial['entry_index']]),
            'scoped_tool_approval': approval, 'budget_seconds': frozen['manifest']['child_budget_seconds']}


def child_disposition(result: dict) -> str:
    """Only an explicitly settled successful or model-failed child may advance."""
    if result.get('stopping') or result.get('status') in ('queued', 'running'):
        return 'wait'
    if result.get('stopping') is not False or result.get('uncertain_effect') is not False:
        return 'block'
    category = result.get('outcome_category', 'unknown')
    if category == 'completed' and result.get('status') == 'completed':
        return 'advance'
    if category == 'model' and result.get('status') in ('failed', 'blocked'):
        return 'advance'
    return 'block'

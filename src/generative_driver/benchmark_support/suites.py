"""Suite selection, frozen inputs and the clients that launch the bench harness which drives a suite."""
import copy
import json
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


def _launch_options(options, home):
    """Split suite options: evaluator password files and the emulator executable go to the bench harness on its stdin,
    never into the frozen suite; the rest is frozen with the suite."""
    from pathlib import Path
    from ..client import default_home
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
    return directory, selected, selected.pop('evaluator_password_files', None), selected.pop('renode', None)


def start_suite(manifest: dict, executor: str | None = None, options: dict | None = None,
                request_id: str | None = None, *, home=None, autostart=True, via_owner=False) -> dict:
    """Start a suite through a detached bench harness, which drives it; the configurator only freezes and stores it.
    Password files and the emulator executable in `options` go to the harness on its stdin, never into the suite tables
    or a child's run. A selection is checked before any configurator starts; a repeated request_id with the
    same inputs follows the suite it started."""
    from ..client import call
    from .harness import launch_suite
    normalized = normalize_manifest(manifest)
    directory, selected, passwords, renode = _launch_options(options, home)
    if not call('ping', {}, directory, autostart=False).get('ok'):
        # Refuse an invalid selection before a configurator exists; a running one checks it again as it freezes.
        preflight_selection(normalized)
    from ..setup import configured_executor
    return launch_suite({'manifest': normalized, 'executor': configured_executor(executor, directory), 'options': selected,
                         'request_id': request_id},
                        home=directory, password_files=passwords, renode=renode, autostart=autostart, via_owner=via_owner)


def resume_suite(suite_id: str, options: dict | None = None, *, home=None, autostart=True, via_owner=False,
                 suite_budget_seconds=None, budget_reason=None, child_budget_seconds=None,
                 child_budget_reason=None) -> dict:
    """Resume a stopped suite through a newly launched bench harness, which takes over its scheduling. Only the password
    files and the emulator executable are read from `options`, and they go to that harness as at the start: the suite
    keeps the options it was frozen with. An approved suite budget extension raises the suite's total; an approved child budget
    extension raises the total of the stopped child the resume continues (an emulated child once its device is
    attached anew), and no other child's."""
    from .harness import launch_suite
    if not isinstance(suite_id, str) or not suite_id.strip():
        raise ValueError('suite_id must be a nonblank string')
    if child_budget_seconds is not None and (
            type(child_budget_seconds) not in (int, float) or not math.isfinite(child_budget_seconds)
            or not 0 < child_budget_seconds <= 604800 or not isinstance(child_budget_reason, str)
            or not child_budget_reason.strip()):
        raise ValueError('A child budget extension requires a finite positive total of at most seven days and a '
                         'nonblank child_budget_reason')
    directory, _, passwords, renode = _launch_options(options, home)
    request = {'suite_id': suite_id, **{key: value for key, value in (
        ('suite_budget_seconds', suite_budget_seconds), ('budget_reason', budget_reason),
        ('child_budget_seconds', child_budget_seconds),
        ('child_budget_reason', child_budget_reason if child_budget_seconds is not None else None)) if value is not None}}
    return launch_suite(request, home=directory, password_files=passwords, renode=renode, autostart=autostart, via_owner=via_owner)


SCHEDULER = 'harness/1'
# Suite option keys that would carry a credential or the emulator executable; they belong to the bench harness.
SECRET = ('password', 'renode')


def refuse_secrets(options) -> None:
    """Suite options never carry an evaluator password file or the emulator executable: the bench harness holds them."""
    if not isinstance(options, dict):
        raise ValueError('Unknown suite options')
    if any(any(word in str(key).lower() for word in SECRET) for key in options):
        raise ValueError('Evaluator password files and the emulator executable belong to the bench harness, which takes '
                         'them on its stdin; a suite never holds them')


def harness_driven(frozen: dict) -> bool:
    """Whether a frozen suite is one the bench harness drives; one frozen before it (with evaluator password files, or
    no scheduler) is read-only."""
    options = frozen.get('options') if isinstance(frozen.get('options'), dict) else {}
    return frozen.get('scheduler') == SCHEDULER and not any(
        any(word in str(key).lower() for word in SECRET) for key in options)


def freeze_suite(manifest: dict, executor: str, executor_config: dict,
                 options: dict, provenance: dict, *, harness_token_sha256: str) -> dict:
    """Pin inputs without creating an owner, run, snapshot or native process.

    This private frozen object is not a public result. The bench harness that drives the suite holds evaluator password
    files and the emulator executable; the frozen suite holds only the sha256 of that harness's token. The child owner
    separately compares the harness's admission of each v2 child with its public pin before starting any operation.
    """
    import hashlib
    import json
    import re
    from ..configurator import validate_scoped_approval
    normalized = normalize_manifest(manifest)
    refuse_secrets(options)
    if set(options) - {'ghidra_home', 'java_home', 'scoped_tool_approval'}:
        raise ValueError('Unknown suite options')
    if not isinstance(harness_token_sha256, str) or not re.fullmatch('[0-9a-f]{64}', harness_token_sha256):
        raise ValueError('A suite is frozen for the bench harness that drives it: the sha256 of its token is required')
    if (executor not in ('codex', 'goose') or not isinstance(executor_config, dict)
            or not isinstance(executor_config.get('command'), list) or not executor_config['command']
            or any(not isinstance(arg, str) or not arg.strip() for arg in executor_config['command'])):
        raise ValueError('Configure a worker runtime command before starting a suite')
    if options.get('scoped_tool_approval') not in (None, 'emulator'):
        raise ValueError('Suites permit only explicit emulator approval')
    for key in ('ghidra_home', 'java_home'):
        if key in options and (not isinstance(options[key], str) or not options[key].strip()):
            raise ValueError('Suite tool paths must be nonblank strings')
    normalized, definitions, pins = preflight_selection(normalized)
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    frozen = copy.deepcopy({'manifest': normalized, 'manifest_sha256': hashlib.sha256(encoded).hexdigest(),
        'request': {'manifest': normalized, 'executor': executor, 'options': options},
        'executor': executor, 'executor_config': executor_config, 'options': options,
        'scope': 'full-workflow', 'entry_pins': pins,
        'entry_effects': [list(case.default_effects) for case in definitions],
        'provenance': provenance, 'trials': expand_trials(normalized),
        'scheduler': SCHEDULER, 'harness_token_sha256': harness_token_sha256})
    for trial in frozen['trials']:
        validate_scoped_approval(child_request(frozen, dict(trial, child_request_id='freeze-' + trial['trial_key'])))
    return frozen


def child_request(frozen: dict, trial: dict) -> dict:
    """Project this reserved slot into a child request. It names no password file or emulator executable: the bench
    harness adds the device, its admission (a v2 case's) and the evaluation token hash."""
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
    approval = options.pop('scoped_tool_approval', None)
    pin = copy.deepcopy(frozen['entry_pins'][trial['entry_index']])
    options.update(scenario_id=pin['scenario_id'], case_seed=pin['case_seed'])
    return {'goal': 'Execute one registered benchmark trial with checked evidence.',
            'case': trial['case'], 'case_pin': pin, 'executor': frozen['executor'],
            'executor_config': copy.deepcopy(frozen['executor_config']), 'case_options': options,
            'request_id': trial['child_request_id'],
            'effects': list(frozen['entry_effects'][trial['entry_index']]),
            'scoped_tool_approval': approval, 'budget_seconds': frozen['manifest']['child_budget_seconds']}


# What the bench harness adds to a frozen child request: the child's device, its admission and the evaluation token hash.
HARNESS_KEYS = ('device', 'admission', 'evaluation_token_sha256')


def child_identity_differs(frozen: dict, trial: dict, submitted) -> str | None:
    """Why a child request (as submitted, or as a run recorded it) is not the frozen slot's own, or None when it is:
    the slot's `child_request` plus only what the bench harness adds."""
    if not isinstance(submitted, dict):
        return 'Frozen suite child identity mismatch: trial ' + str(trial.get('trial_key')) + ' has no submitted request'
    request = copy.deepcopy(submitted)
    for key in HARNESS_KEYS:
        request.pop(key, None)
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    expected = child_request(frozen, trial)
    if canonical(request) == canonical(expected):
        return None
    differing = sorted(key for key in set(request) | set(expected)
                       if canonical(request.get(key)) != canonical(expected.get(key)))
    return ('Frozen suite child identity mismatch: trial ' + trial['trial_key'] + "'s child request differs from the "
            'frozen slot in ' + ', '.join(differing))


def child_disposition(result: dict) -> str:
    """Whether a suite's active child lets the suite advance. Every child is graded once after its run by the bench
    harness: it advances only once it completed with a graded or no-driver evaluation recorded. An unscored or still
    pending evaluation, and every safety, integrity, operator, host or other stop, blocks the suite."""
    if result.get('stopping') or result.get('status') in ('queued', 'running'):
        return 'wait'
    if result.get('stopping') is not False or result.get('uncertain_effect') is not False:
        return 'block'
    completed = result.get('outcome_category', 'unknown') == 'completed' and result.get('status') == 'completed'
    equivalence = result.get('equivalence') if isinstance(result.get('equivalence'), dict) else {}
    return 'advance' if completed and equivalence.get('status') in ('graded', 'no-driver') else 'block'

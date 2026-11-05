"""Suite selection, frozen inputs and the clients that launch the bench harness which drives a suite."""
import copy
import json
import math


def _registered_through_a_set(case) -> bool:
    from .registrations import registered_case_ids, registration_for
    return case in registered_case_ids() and registration_for(case).calibration_kind == 'set'


def _check_set_entry(entry) -> None:
    """ValueError unless `entry` of a set manifest names a device of a case registered through its set, as its pin will:
    the only scenario and seed a generated device has."""
    from .generated_device import validate_device_id
    if not _registered_through_a_set(entry['case']):
        raise ValueError('A set suite runs only cases registered through a set: ' + entry['case'])
    if entry['scenario'] != 'original':
        raise ValueError('A generated device has only the original scenario: ' + entry['scenario'])
    if entry['case_seed'] != 0:
        raise ValueError('A generated device has only case seed 0: ' + str(entry['case_seed']))
    validate_device_id(entry['device_id'])


def normalize_manifest(manifest: dict) -> dict:
    """The checked copy of a suite manifest. One with a top-level `set` names one generated set, and each of its entries
    a device of it by `device_id` (Feature 03, generated trials); a generated case runs in no other suite."""
    required = {'schema', 'id', 'version', 'entries', 'repetitions',
                'child_budget_seconds', 'suite_budget_seconds', 'max_active_children'}
    if not isinstance(manifest, dict) or set(manifest) - {'set'} != required:
        raise ValueError('Suite manifest fields are incomplete or unknown')
    result = copy.deepcopy(manifest)
    set_suite = 'set' in result
    if set_suite and (not isinstance(result['set'], str) or not result['set'].strip()):
        raise ValueError('A suite set must be a nonblank string')
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
        fields = {'case', 'scenario', 'case_seed', *(('device_id',) if set_suite else ())}
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError('Invalid suite entry fields')
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ('case', 'scenario')):
            raise ValueError('Case and scenario must be nonblank strings')
        if type(entry['case_seed']) is not int:
            raise ValueError('Case seed must be an integer, never a model seed')
        if set_suite:
            _check_set_entry(entry)
        elif _registered_through_a_set(entry['case']):
            raise ValueError('A generated case runs only in a suite of one set: ' + entry['case'])
        identity = (entry['case'], entry['scenario'], entry['case_seed'], entry.get('device_id'))
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

    Authenticated calibration and runtime configuration remain owner admission. A set manifest's definitions come from
    its row, and it has no pins here: the bench harness builds them, and `freeze_suite` checks what it sends.
    """
    from .registry import case_selection, resolve_case, pin_case
    normalized = normalize_manifest(manifest)
    select = case_selection if 'set' in normalized else resolve_case
    definitions = [select(entry['case']) for entry in normalized['entries']]
    if any(case.execution != 'actual-agent-emulation' for case in definitions):
        raise ValueError('Suite requires emulated actual-agent cases')
    if len({case.evidence_track for case in definitions}) != 1:
        raise ValueError('Mixed evidence tracks cannot share a suite aggregate')
    if 'set' in normalized:
        return normalized, definitions, []
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
    """Split suite options: evaluator password files, the emulator executable and a set suite's `set_root` go to the
    bench harness on its stdin, never into the frozen suite; the rest is frozen with the suite."""
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
    return (directory, selected, selected.pop('evaluator_password_files', None), selected.pop('renode', None),
            selected.pop('set_root', None))


def start_suite(manifest: dict, executor: str | None = None, options: dict | None = None,
                request_id: str | None = None, *, home=None, autostart=True, via_owner=False) -> dict:
    """Start a suite through a detached bench harness, which drives it; the configurator only freezes and stores it.
    Password files (keyed by the set's name for a set suite), the emulator executable and a set suite's `set_root` in
    `options` go to the harness on its stdin, never into the suite tables or a child's run. A selection is checked
    before any configurator starts; a repeated request_id with the
    same inputs follows the suite it started."""
    from ..client import call
    from .harness import launch_suite
    normalized = normalize_manifest(manifest)
    directory, selected, passwords, renode, set_root = _launch_options(options, home)
    from ..setup import configured_executor
    executor = configured_executor(executor, directory)
    if executor != 'replay' and 'replay_candidates' in selected:
        raise ValueError('Only a replay suite takes replay_candidates')
    if executor == 'replay':
        # Relative paths name directories from the caller's directory; checked before any harness starts, and again by
        # the configurator as it freezes the suite.
        from pathlib import Path
        from ..replay import suite_candidates
        from .harness import bench_home
        candidates = selected.get('replay_candidates')
        if isinstance(candidates, dict) and all(isinstance(path, str) and path.strip() for path in candidates.values()):
            selected['replay_candidates'] = candidates = {case: str(Path(path).expanduser().resolve())
                                                          for case, path in candidates.items()}
        suite_candidates({entry['case'] for entry in normalized['entries']}, candidates, bench=bench_home(directory))
    if not call('ping', {}, directory, autostart=False).get('ok'):
        # Refuse an invalid selection before a configurator exists; a running one checks it again as it freezes.
        preflight_selection(normalized)
    return launch_suite({'manifest': normalized, 'executor': executor, 'options': selected,
                         'request_id': request_id},
                        home=directory, password_files=passwords, renode=renode, set_root=set_root, autostart=autostart,
                        via_owner=via_owner)


# The one refusal of an extension of a scoring suite's child budget: `resume_suite` before it launches a harness, the
# harness with the vault still open and the configurator, which decides.
NO_CHILD_EXTENSION = ('A scoring suite runs every child on the child budget it froze, which pairing relies on: '
                      'child_budget_seconds')


def _refuse_live_set_suite(suite_id, home, child_budget_seconds=None) -> None:
    """ValueError for a set suite whose active child is queued or running, or that has anything still stopping: resuming
    it would have its operator open the vault while workers run, for a refusal. So too for a scoring suite given a child
    budget extension. A configurator that cannot be reached, a
    suite it does not know and a suite of no set go on to the harness, which reports them. A slot launching with no
    attached child, which arises only after a configurator crash, is not visible here."""
    from ..client import call
    result = call('suite_result', {'suite_id': suite_id, 'limit': 1}, home, autostart=False)
    identity = (result.get('experiment') or {}).get('comparison_identity') or {}
    if 'set' not in (identity.get('manifest') or {}):
        return
    if child_budget_seconds is not None and 'scoring_set' in identity:
        raise ValueError(NO_CHILD_EXTENSION)
    status = call('suite_status', {'suite_id': suite_id}, home, autostart=False)
    child = status.get('active_child_id')
    child_status = call('status', {'run_id': child}, home, autostart=False).get('status') if child else None
    if child_status in ('queued', 'running') or status.get('stopping'):
        raise ValueError('The suite still has work running; stop it or let it finish before resuming it: ' + suite_id)


def resume_suite(suite_id: str, options: dict | None = None, *, home=None, autostart=True, via_owner=False,
                 suite_budget_seconds=None, budget_reason=None, child_budget_seconds=None,
                 child_budget_reason=None) -> dict:
    """Resume a stopped suite through a newly launched bench harness, which takes over its scheduling. Only the password
    files, the emulator executable and `set_root` are read from `options`, and they go to that harness as at the start:
    the suite
    keeps the options it was frozen with. An approved suite budget extension raises the suite's total; an approved child budget
    extension raises the total of the stopped child the resume continues (an emulated child once its device is
    attached anew), and no other child's, but a scoring suite refuses one. A set suite with work still running is
    refused here (`_refuse_live_set_suite`), before its operator opens the vault."""
    from .harness import launch_suite
    if not isinstance(suite_id, str) or not suite_id.strip():
        raise ValueError('suite_id must be a nonblank string')
    if child_budget_seconds is not None and (
            type(child_budget_seconds) not in (int, float) or not math.isfinite(child_budget_seconds)
            or not 0 < child_budget_seconds <= 604800 or not isinstance(child_budget_reason, str)
            or not child_budget_reason.strip()):
        raise ValueError('A child budget extension requires a finite positive total of at most seven days and a '
                         'nonblank child_budget_reason')
    directory, _, passwords, renode, set_root = _launch_options(options, home)
    _refuse_live_set_suite(suite_id, directory, child_budget_seconds)
    request = {'suite_id': suite_id, **{key: value for key, value in (
        ('suite_budget_seconds', suite_budget_seconds), ('budget_reason', budget_reason),
        ('child_budget_seconds', child_budget_seconds),
        ('child_budget_reason', child_budget_reason if child_budget_seconds is not None else None)) if value is not None}}
    return launch_suite(request, home=directory, password_files=passwords, renode=renode, set_root=set_root,
                        autostart=autostart, via_owner=via_owner)


SCHEDULER = 'harness/1'
# Suite option keys that would carry a credential, the emulator executable or a set's root; the bench harness holds
# them.
SECRET = ('password', 'renode', 'set_root')


def refuse_secrets(options) -> None:
    """Suite options never carry an evaluator password file, the emulator executable or a set's root: the bench harness
    holds them."""
    if not isinstance(options, dict):
        raise ValueError('Unknown suite options')
    if any(any(word in str(key).lower() for word in SECRET) for key in options):
        raise ValueError('Evaluator password files, the emulator executable and a set root belong to the bench '
                         'harness, which takes them on its stdin; a suite never holds them')


def harness_driven(frozen: dict) -> bool:
    """Whether a frozen suite is one the bench harness drives; one frozen before it (with evaluator password files, or
    no scheduler) is read-only."""
    options = frozen.get('options') if isinstance(frozen.get('options'), dict) else {}
    return frozen.get('scheduler') == SCHEDULER and not any(
        any(word in str(key).lower() for word in SECRET) for key in options)


def _frozen_device_pins(manifest, device_pins):
    """The pins of a set manifest's entries, in order, from `device_pins` ({device id: pin}), as its bench harness built
    them: exactly one per entry, each naming that entry's set and device, and each what its case's family holds current.
    The configurator takes the harness's word for the image and answer hashes (Feature 03, generated trials)."""
    from .families import family_for
    entries = manifest['entries']
    if not isinstance(device_pins, dict) or set(device_pins) != {entry['device_id'] for entry in entries}:
        raise ValueError('A set suite is frozen with one device pin per entry, from its bench harness: device_pins')
    for entry in entries:
        pin = device_pins[entry['device_id']]
        if (not isinstance(pin, dict) or pin.get('device_id') != entry['device_id'] or pin.get('set') != manifest['set']
                or pin.get('case_id') != entry['case']):
            raise ValueError('A device pin names another device: ' + entry['device_id'])
        if family_for(entry['case']).current_pin(pin) != pin:
            raise ValueError('A device pin differs from what its family holds current: ' + entry['device_id'])
    return [device_pins[entry['device_id']] for entry in entries]


def freeze_suite(manifest: dict, executor: str, executor_config: dict,
                 options: dict, provenance: dict, *, harness_token_sha256: str,
                 device_pins: dict | None = None) -> dict:
    """Pin inputs without creating an owner, run, snapshot or native process.

    This private frozen object is not a public result. The bench harness that drives the suite holds evaluator password
    files and the emulator executable; the frozen suite holds only the sha256 of that harness's token. The child owner
    separately compares the harness's admission of each v2 child with its public pin before starting any operation. A
    set suite is frozen with the `device_pins` its harness built; no other suite takes any. A suite of a scoring set
    also holds `scoring_set`, its registration and its manifest hash from the pins, so that no later change to the
    registrations changes what the suite ran.
    """
    import hashlib
    import json
    import re
    from ..configurator import validate_scoped_approval
    normalized = normalize_manifest(manifest)
    refuse_secrets(options)
    # A replay suite's candidates belong to its executor configuration: its children's options never carry them.
    child_options = {key: value for key, value in options.items() if key != 'replay_candidates'}
    if set(child_options) - {'ghidra_home', 'java_home', 'scoped_tool_approval'}:
        raise ValueError('Unknown suite options')
    if not isinstance(harness_token_sha256, str) or not re.fullmatch('[0-9a-f]{64}', harness_token_sha256):
        raise ValueError('A suite is frozen for the bench harness that drives it: the sha256 of its token is required')
    if executor != 'replay' and 'replay_candidates' in options:
        raise ValueError('Only a replay suite takes replay_candidates')
    if executor == 'replay':
        if not _replay_candidates(executor_config, normalized):
            raise ValueError('A replay suite takes one saved candidate per case: options.replay_candidates '
                             '{case: directory}')
    elif (executor not in ('codex', 'goose') or not isinstance(executor_config, dict)
            or not isinstance(executor_config.get('command'), list) or not executor_config['command']
            or any(not isinstance(arg, str) or not arg.strip() for arg in executor_config['command'])):
        raise ValueError('Configure a worker runtime command before starting a suite')
    if options.get('scoped_tool_approval') not in (None, 'emulator'):
        raise ValueError('Suites permit only explicit emulator approval')
    for key in ('ghidra_home', 'java_home'):
        if key in options and (not isinstance(options[key], str) or not options[key].strip()):
            raise ValueError('Suite tool paths must be nonblank strings')
    normalized, definitions, pins = preflight_selection(normalized)
    scoring = {}
    if 'set' in normalized:
        from .generated_sets import is_scoring
        pins = _frozen_device_pins(normalized, device_pins)
        if is_scoring(normalized['set']):
            scoring = {'scoring_set': {'registration': normalized['set'],
                                       'manifest_sha256': pins[0]['manifest_sha256']}}
    elif device_pins is not None:
        raise ValueError('Device pins belong to a set suite')
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    frozen = copy.deepcopy({'manifest': normalized, 'manifest_sha256': hashlib.sha256(encoded).hexdigest(),
        'request': {'manifest': normalized, 'executor': executor, 'options': options,
                    **({'device_pins': device_pins} if device_pins is not None else {})},
        'executor': executor, 'executor_config': executor_config, 'options': child_options,
        'scope': 'full-workflow', 'entry_pins': pins,
        **({'device_pins': device_pins} if device_pins is not None else {}), **scoring,
        'entry_effects': [list(case.default_effects) for case in definitions],
        'provenance': provenance, 'trials': expand_trials(normalized),
        'scheduler': SCHEDULER, 'harness_token_sha256': harness_token_sha256})
    for trial in frozen['trials']:
        validate_scoped_approval(child_request(frozen, dict(trial, child_request_id='freeze-' + trial['trial_key'])))
    return frozen


def _replay_candidates(executor_config, manifest):
    """Whether a replay suite's configuration names exactly one stored candidate per case of its manifest."""
    candidates = executor_config.get('candidates') if isinstance(executor_config, dict) else None
    return (isinstance(candidates, dict) and set(executor_config) == {'runtime', 'candidates'}
            and executor_config['runtime'] == 'replay' and set(candidates) == {entry['case'] for entry in manifest['entries']}
            and all(isinstance(entry, dict) and set(entry) == {'candidate', 'candidate_sha256'}
                    and all(isinstance(value, str) and value for value in entry.values()) for entry in candidates.values()))


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
    executor_config = copy.deepcopy(frozen['executor_config'])
    if frozen['executor'] == 'replay':
        # Each child replays its own case's candidate.
        executor_config = {'runtime': 'replay', **executor_config['candidates'][trial['case']]}
    return {'goal': 'Execute one registered benchmark trial with checked evidence.',
            'case': trial['case'], 'case_pin': pin, 'executor': frozen['executor'],
            'executor_config': executor_config, 'case_options': options,
            'request_id': trial['child_request_id'],
            'effects': list(frozen['entry_effects'][trial['entry_index']]),
            'scoped_tool_approval': approval, 'budget_seconds': frozen['manifest']['child_budget_seconds']}


# What the bench harness adds to a frozen child request: the child's device, its admission, the evaluation token hash
# and the folder of its public inputs, for a device of a set.
HARNESS_KEYS = ('device', 'admission', 'evaluation_token_sha256', 'public_inputs')


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
    harness: it advances only once it completed with a graded, no-driver or unknown evaluation recorded. An unscored or
    still pending evaluation, and every safety, integrity, operator, host or other stop, blocks the suite."""
    if result.get('stopping') or result.get('status') in ('queued', 'running'):
        return 'wait'
    if result.get('stopping') is not False or result.get('uncertain_effect') is not False:
        return 'block'
    completed = result.get('outcome_category', 'unknown') == 'completed' and result.get('status') == 'completed'
    equivalence = result.get('equivalence') if isinstance(result.get('equivalence'), dict) else {}
    return 'advance' if completed and equivalence.get('status') in ('graded', 'no-driver', 'unknown') else 'block'


# Why a scoring suite's slot never got a run, in the one closed word its row keeps.
NO_RUN = ('admit-refused', 'provider-failed', 'start-refused')


def scoring_disposition(result: dict, slot: dict) -> str:
    """What a scoring suite does with its slot's child (Feature 06, slot policy; spec decision 20): `advance` (it
    stands), `fault` (the slot gets its rerun), `flag` (the audit found a command naming the set's folder), `regrade`
    (the same driver is graded again), `wait` or `block`. `result` is what the configurator read of the child, its
    `equivalence`, `unscored` reason and `effective_budget_seconds` included, and `slot` the suite's row. Settlement and
    the report call the same `classify`, here on a projection of the same records. A rerun's fault stands. A grade
    unscored for a host fault is graded once more, on either attempt, before it counts as one."""
    from .outcomes import classify
    if result.get('stopping') or result.get('status') in ('queued', 'running'):
        return 'wait'
    if result.get('stopping') is not False or result.get('uncertain_effect') is not False:
        return 'block'
    equivalence = result.get('equivalence') if isinstance(result.get('equivalence'), dict) else None
    awaiting = 'unscored' if result.get('unscored') else 'pending' if result.get('status') == 'completed' else None
    outcome = classify(
        {'run_id': result.get('run_id'), 'status': 'finished', 'outcome_category': result.get('outcome_category')},
        {'workflow_status': result.get('status'), 'elapsed_seconds': result['updated'] - result['created'],
         'effective_budget_seconds': result.get('effective_budget_seconds'), 'equivalence': equivalence,
         'equivalence_status': equivalence.get('status') if equivalence else awaiting,
         'reason': result.get('unscored') or result.get('reason')},
        flagged=bool(slot.get('audit')), unscored_integrity_fails=True)
    if outcome != 'fault':
        return {'flagged': 'flag', 'running': 'block'}.get(outcome, 'advance')
    if result.get('status') == 'completed' and equivalence is None and slot.get('regrade') != 'spent':
        return 'regrade'
    return 'advance' if slot.get('attempt', 1) == 2 else 'fault'

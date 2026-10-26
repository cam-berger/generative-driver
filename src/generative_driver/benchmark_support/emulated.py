"""Native benchmark stages, observed without truth. The bench harness provides the emulator and the configurator reaches it
through its device handle; this adapter starts, holds and stops no device, and unlocks no truth for the stages. Legacy
tq9 (`cases.py`) shares its stages but interpretation and the probe's task mapping."""
import hashlib
import re
import shutil
import tempfile
import json
from pathlib import Path

from .cases import _state, _write
from .instruments import family_for
from .registry import resolve_case
from .snapshots import read_snapshot

# Instrument readings are device evidence, as a real device's readings would be; workers see them, nothing grades them.
INSTRUMENT = ('Live interface_execute and probe_run results carry before_observation and observation: instrument '
              'readings of the emulated device with units. They are device evidence, as a real device\'s readings would '
              'be, never a grade. The provided emulator runs only inside each live call and is paused between calls, so '
              'device time advances during a call and not while you work between calls. ')


def _development_conditions(case_id, prepared, workspace, options):
    """Give probe/ground the operator's public inputs; no control coordinates or evaluator answers."""
    catalog = ((options or {}).get('device') or {}).get('development_conditions')
    if case_id != 'sampled-sensor-v1' or not catalog:
        return prepared
    path = Path(workspace)/'DEVELOPMENT_CONDITIONS.json'
    _write(path, catalog)
    guidance = (' Read DEVELOPMENT_CONDITIONS.json. On interface_execute or probe_run, condition_id selects a fixed '
        'public source condition before that live call; omit it to keep the source. Changing the source does not '
        'acquire a sample: read may still return the retained value. Perform a new acquisition, check its sequence '
        'and observation.condition.sample_after_change, and compare decoded outputs with the measured instrument '
        'reference. Use distinct reference conditions to investigate scale and offset, then additional conditions '
        'to check the conversion; zero-only readings cannot establish scale. Each probe_run may use n=1. '
        'The requested reference is an experimental input, not proof of a new measurement. Preserve the observed '
        'discrepancies in your notes; final evaluation is separate.')
    return {**prepared, 'objective': prepared['objective'] + guidance,
            'inputs': [*prepared.get('inputs', []), str(path)],
            'context': {**prepared.get('context', {}), 'development_conditions': str(path)}}


def _required_tasks(case_id):
    return {'tq9-v2': ('temperature', 'arm', 'set_duty', 'disarm'),
            'sampled-sensor-v1': ('measure', 'read'),
            'parameter-store-v1': ('read', 'update', 'stage', 'commit', 'abort')}[case_id]


def _capability_guidance(workspace, case_id):
    from . import capabilities
    rules = workspace/'_capability_rules.py'
    shutil.copyfile(capabilities.__file__, rules)
    helper = workspace/'validate_capabilities.py'
    helper.write_text('''import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _capability_rules import validate_capabilities
model = json.loads((Path(sys.argv[1])/'model.json').read_text(encoding='utf-8'))
try:
    mapping = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
except (ValueError, UnicodeError):
    result = {'ok': False, 'errors': [{'fault': 'model', 'code': 'capability_mapping',
        'path': 'capabilities.json', 'message': 'Capability file must contain valid UTF-8 JSON'}]}
else:
    result = validate_capabilities(mapping, model, ''' + repr(_required_tasks(case_id)) + ''')
print(json.dumps(result, allow_nan=False))
sys.exit(0 if result['ok'] else 1)
''', encoding='utf-8')
    guide = workspace/'CAPABILITY_FORMAT.md'
    guide.write_text('''# Capability mapping format

Declare schema `benchmark-capabilities/2` and a `tasks` object. Every task has `operation`, `constants`, `inputs` and `outputs`; use empty objects when there are none. Operation, parameter and output names must refer to your candidate model. Constants and mapped inputs cover every operation parameter exactly once.

Copy an input with `{"parameter": "candidate_parameter", "kind": "copy"}`. Numeric conversion uses `{"parameter": "candidate_parameter", "kind": "affine", "scale": 1, "offset": 0}`. `mapping` is not the selector key. Affine conversion is canonical input * scale + offset, checked against the candidate type and bounds.

Map an output with `{"output": "candidate_output", "unit": "candidate_unit"}`. Always include `unit`; use JSON `null` when the candidate output has no unit. This field must agree with the candidate model. Canonical physical units still need to match the public task objective. An operation with only acknowledgment text can use empty outputs when no canonical output is requested.

Example shape, using placeholder names rather than inferred device facts:

```json
{"schema":"benchmark-capabilities/2","tasks":{"task_name":{"operation":"candidate_operation","constants":{},"inputs":{"canonical_input":{"parameter":"candidate_parameter","kind":"copy"}},"outputs":{"canonical_output":{"output":"candidate_output","unit":null}}}}}
```

Run `python validate_capabilities.py <absolute-model-directory> <absolute-capabilities-json>` before submitting. This pure check uses the same binding rules as the controller, without a device or evaluator answer key. Correct capability-file errors here; report `needs_revision` if the immutable model itself needs repair. Do not edit the supplied validator or rules. A passed mapping check establishes structure, not functional behavior.
''', encoding='utf-8')
    return [str(helper), str(rules), str(guide)]


def _inputs(case_id, run_dir):
    if (Path(run_dir)/'benchmark/execution.json').exists():
        saved = read_snapshot(run_dir)
        from .registry import validate_pinned_workflow
        validate_pinned_workflow(saved['case_pin'])
        if saved['case_pin']['case_id'] != case_id:
            raise ValueError('Execution snapshot case mismatch')
        return Path(run_dir)/'benchmark/inputs', saved['case_pin']['manifest']
    case = resolve_case(case_id)
    return case.root, case.manifest


def _named(path, name):
    prefix, separator, original = Path(path).name.partition('-')
    return Path(path).name == name or bool(separator) and prefix.isdigit() and original == name


def _artifact(handoff, name, sha256=None):
    """(path, recorded sha256) of `name` in one accepted handoff (with that hash, when given), or None."""
    for artifact in (handoff or {}).get('artifacts', []):
        if _named(artifact['path'], name) and sha256 in (None, artifact['sha256']):
            return Path(artifact['path']), artifact['sha256']
    return None


def _handoff_with(accepted, stages, name, sha256=None):
    """The latest accepted handoff of these stages that carries `name` (with that hash, when given), or None. A
    handoff is chosen first and its files after, so files of different candidates are never paired."""
    return next((h for h in reversed(accepted or []) if h.get('stage') in stages and _artifact(h, name, sha256)), None)


def _latest(accepted, stages, name):
    """(path, recorded sha256) of `name` in the latest accepted handoff of these stages that carries it, or None."""
    return _artifact(_handoff_with(accepted, stages, name), name)


def _verified(found, what):
    """The bytes of an accepted artifact, checked against the hash recorded when it was accepted."""
    from ..configurator import IntegrityError
    path, expected = found
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise IntegrityError('Accepted ' + what + ' bytes changed: ' + path.name)
    return content


def _observed(artifacts, checks, dropped=(), scope='Truth-free stage evidence; nothing here is graded'):
    """The configurator's checked contract: what the stage hands on, with its checks as notes."""
    artifacts = [str(path) for path in dict.fromkeys(artifacts)]
    return {'artifacts': artifacts, 'checks': checks, 'validation_scope': scope,
            'notes': {'checks': checks, 'dropped_artifacts': list(dropped)}}


def _candidate_files(accepted, model_sha=None):
    """The interpreted candidate's own replies and notes, hash-verified, from one interpret handoff only: the one whose
    model.json is `model_sha` (the model probed), else the latest that carries a model.json."""
    handoff, files = _handoff_with(accepted, ('interpret',), 'model.json', model_sha), {}
    for name in ('replies.json', 'NOTES.md'):
        found = _artifact(handoff, name)
        if found is not None:
            _verified(found, 'interpretation')
            files[name] = found[0]
    return files


def _emitted_mapping(accepted):
    """(path, recorded sha256) of the capability mapping in the probe handoff whose model emit packaged (the latest
    probe handoff that carries a model.json), or None when that handoff has none: never another model's mapping."""
    return _artifact(_handoff_with(accepted, ('probe',), 'model.json'), 'capabilities.json')


def adapt(run_dir, ws, accepted, options):
    """Probe's model: the latest interpreted model, hash-verified and wired to the emulator binding in `ws/model`, which
    the run state records; {'binding'} of the provided device, or a stop for the operator without one. With no model, or
    one the emulator binding cannot carry, the probe skips and no device is used: the candidate is kept for the repair
    its configurator diff drives."""
    from .evaluate import adapt_model
    found = _latest(accepted, ('interpret',), 'model.json')
    if found is None:
        return {'skip': 'Probe skipped: no interpreted model available'}
    from ..configurator import IntegrityError
    _verified(found, 'interpretation')
    try:
        adaptation = adapt_model(found[0], ws/'model')
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # A model the emulator cannot carry is the candidate's, never a stop, and no emulator starts for it. The
        # configurator validates it and that diff drives repair, so the candidate is kept for the repair.
        from .candidate_context import remember
        remember(run_dir, 'probe', {'model.json': found[0], **_candidate_files(accepted, found[1])}, workspace=ws)
        reason = ('The emulator binding (a UART byte stream over TCP) cannot carry this model: '
                  + type(error).__name__ + ': ' + str(error))
        return {'skip': 'Probe skipped: the interpreted model cannot be wired to the emulator: ' + str(error),
                'unwired_model': {'path': str(found[0]), 'sha256': found[1], 'reason': reason}}
    if adaptation['original_model_sha256'] != found[1]:
        raise IntegrityError('Accepted interpretation bytes changed while adapting the model')
    binding = _binding(options)
    if binding is None:
        return {'blocked': NO_DEVICE}
    path, state = _state(run_dir)
    state.update(adaptation=adaptation, adapted_model_dir=str(ws/'model'), revision=int(options.get('revision', state.get('revision', 0))))
    _write(path, state)
    return {'binding': binding}


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    options = options or {}
    if options.get('scenario_id') not in (None, 'original'):
        raise ValueError('Unknown case scenario')
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    inputs, manifest = _inputs(case_id, run_dir)
    if stage == 'acquire':
        source = inputs/'firmware.bin'
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['images']['firmware.bin']:
            raise ValueError('Supplied firmware hash mismatch')
        target = ws/'description.bin'
        shutil.copyfile(source, target)
        return {'objective': 'Import the supplied firmware with acquire_firmware_artifact, verify its hash, and report artifact and provenance paths.',
                'inputs': [str(target)], 'allowed_tools': ['acquire_firmware_artifact'],
                'context': {'source_path': str(target), 'expected_sha256': manifest['images']['firmware.bin'],
                            'origin': 'provided_binary'}, 'boundary': 'provided artifact import'}
    if stage == 'probe':
        adapted = adapt(run_dir, ws, accepted, options)
        if adapted.get('skip') or adapted.get('blocked'):
            return adapted
        binding = adapted['binding']
        guidance = _capability_guidance(ws, case_id)
        from .candidate_context import task_contract
        _write(ws/'TASKS.json', task_contract(case_id, options.get('configured_effects', [])))
        guidance.append(str(ws/'TASKS.json'))
        objective = 'Test the supplied immutable model over the emulator binding. Write capabilities.json with schema benchmark-capabilities/2 and tasks temperature, arm, set_duty, disarm. Each task has operation, constants, inputs and outputs. Map canonical set_duty input duty (permille) with copy or affine parameter mapping. Map canonical temperature output to its candidate output and declared unit. Report unsupported capabilities honestly.'
        if case_id == 'sampled-sensor-v1':
            objective = 'Test the supplied model over the emulator binding. Write benchmark-capabilities/2 tasks measure and read, mapping temperature (degC) and sequence (count) outputs. Measure performs a new acquisition; read returns the current sample. Report unsupported capabilities honestly.'
        elif case_id == 'parameter-store-v1':
            objective = 'Test the supplied model over the emulator binding. Write benchmark-capabilities/2 tasks read, update, stage, commit and abort. Read takes bank and slot and maps value (configuration-unit). Update and stage take bank, slot and value; commit and abort have no inputs. Every task declares operation, constants, inputs and outputs.'
        objective += (' Read TASKS.json for required task semantics, outputs and effect grants. '
            'Read CAPABILITY_FORMAT.md and run validate_capabilities.py with the supplied model directory '
            'and your capabilities.json. Correct mapping errors before submission. ' + INSTRUMENT +
            'Compare those readings and the observed effects with your decoded outputs. Preserve capabilities.json even '
            'when incomplete; report needs_revision and the observed discrepancy if the immutable model needs repair. '
            'Do not invent unsupported units or change the model here. Your observed probe records, capabilities.json and '
            'notes are handed on; where the model fails on the device, that difference goes back to interpretation.')
        return _development_conditions(case_id, {'objective': objective,
                'inputs': [str(ws/'model'), *guidance],
                'context': {'model_dir': str(ws/'model'), 'capabilities_output': str(ws/'capabilities.json')},
                'allowed_tools': ['interface_describe', 'interface_execute', 'probe_run', 'probe_diff'],
                'binding': binding, 'effects': ['write', 'actuate']}, ws, options)
    if stage == 'ground':
        from .ground import device_evidence
        prepared = device_evidence(accepted, ws)
        if prepared.get('skip'):
            return prepared
        if _binding(options) is None:
            return {'blocked': NO_DEVICE}
        if case_id == 'sampled-sensor-v1':
            from .candidate_context import task_contract
            tasks = ws/'TASKS.json'
            _write(tasks, task_contract(case_id, options.get('configured_effects', [])))
            prepared['inputs'].append(str(tasks))
            prepared['objective'] += ' Read TASKS.json for the public quantity comparisons, tolerances and acquisition requirements.'
        return _development_conditions(case_id,
            {**prepared, 'allowed_tools': ['interface_execute', 'probe_run', 'probe_diff'],
             'binding': _binding(options)}, ws, options)
    if stage == 'emit':
        found = _latest(accepted, ('probe',), 'model.json')
        if found is None:
            return {'skip': 'Emit skipped: no interpreted model available'}
        content = _verified(found, 'probe')
        (ws/'model').mkdir(exist_ok=True)
        (ws/'model/model.json').write_bytes(content)
        model_sha = hashlib.sha256(content).hexdigest()
        # The latest successful probe record a worker made of this model, from probe or ground.
        for handoff in reversed(accepted or []):
            if handoff.get('stage') not in ('probe', 'ground'):
                continue
            for artifact in handoff.get('artifacts', []):
                candidate = Path(artifact['path'])
                if candidate.suffix != '.json' or not candidate.is_file():
                    continue
                raw = _verified((candidate, artifact['sha256']), handoff['stage'])
                try:
                    probe = json.loads(raw)
                except ValueError:
                    continue
                if (isinstance(probe, dict) and probe.get('schema') == 'interface-probe/1' and probe.get('ok') is True
                        and probe.get('model_sha256') == model_sha):
                    (ws/'probe.json').write_bytes(raw)
                    return {'objective': 'Use emit_package with the supplied immutable model and successful probe, then emit_check. Report the complete package directory.',
                            'inputs': [str(ws/'model/model.json'), str(ws/'probe.json')],
                            'context': {'model_dir': str(ws/'model'), 'probe': str(ws/'probe.json')},
                            'allowed_tools': ['emit_package', 'emit_check']}
        missing = 'No successful probe record of the model available'
        return {'objective': 'Use emit_package with the supplied immutable model, then emit_check. ' + missing +
                    '; emit_package needs one, so report what is missing.',
                'inputs': [str(ws/'model/model.json')], 'context': {'model_dir': str(ws/'model'), 'input_notes': [missing]},
                'allowed_tools': ['emit_package', 'emit_check'], 'input_notes': [missing]}
    if stage == 'reuse':
        from ..configurator import digest, IntegrityError
        import uuid
        source = next(((Path(a['path']), a['sha256']) for h in reversed(accepted or []) if h.get('stage') == 'emit'
                       for a in h.get('artifacts', []) if (Path(a['path'])/'manifest.json').is_file()), None)
        if source is None:
            return {'skip': 'Fresh reuse skipped: no accepted emitted package to reuse'}
        package = ws/'package'
        shutil.copytree(source[0], package)
        path, state = _state(run_dir)
        copied = digest(package)
        if copied != source[1] or copied != state.get('package_sha256'):
            raise IntegrityError('Accepted package identity changed')
        binding = _binding(options)
        if binding is None:
            return {'blocked': NO_DEVICE}
        state.setdefault('package_copies', {})[stage] = str(package)
        state.setdefault('package_attempts', {})[stage] = uuid.uuid4().hex
        capabilities = _emitted_mapping(accepted)
        if capabilities is not None:
            _verified(capabilities, 'probe')
        state['capabilities_path'] = str(capabilities[0]) if capabilities else None
        _write(path, state)
        mission = 'Read temperature, enable the device, request 37 percent output, then disarm.'
        if case_id == 'sampled-sensor-v1':
            mission = 'Acquire and read two fresh temperature samples. Report their measured sequence numbers.'
        elif case_id == 'parameter-store-v1':
            mission = 'Commit bank B slot 1 to -7. Stage bank B slot 1 to 9, abort that transaction, then read bank B slot 1. Report observations and whether other cells changed.'
        return {'objective': 'Use only the emitted package and this objective: ' + mission + ' Use benchmark_package_execute for each operation. Find names and arguments in the package reference. Each result also carries an instrument reading of the emulated device after the call. Report actual observations and limitations.',
                'inputs': [str(package)], 'allowed_tools': ['benchmark_package_execute'],
                'context': {'package_dir': str(package)}, 'binding': binding,
                'boundary': 'fresh package-only mission'}
    if stage == 'interpret':
        from ..toolkit import call_tool
        from ..analysis import firmware_analysis_inputs
        state_path, state = _state(run_dir)
        state['revision'] = int(options.get('revision', state.get('revision', 0)))
        image = inputs/'firmware.bin'
        sources = {'image.bin': str(image), **firmware_analysis_inputs(ws, options)}
        wiring = ws/'BINDING_CONTEXT.json'
        _write(wiring, {'schema': 'interpret-binding-context/1',
            'runtime_channel': {'type': 'tcp'}, 'physical_channel': {'type': 'uart'},
            'unverified': ['baudrate', 'bytesize', 'parity', 'stopbits', 'pins'],
            'instruction': 'The operator supplies a UART byte stream through a TCP emulator binding. '
                'Use runtime_channel for this executable model; the configurator supplies the endpoint later. '
                'Recover protocol bytes and semantics from the binary. Record any inferred physical settings '
                'and unresolved facts in NOTES.md. TCP cannot verify physical UART settings; do not invent them.'})
        sources['BINDING_CONTEXT.json'] = str(wiring)
        from .candidate_context import task_contract, repair_sources
        _write(ws/'TASKS.json', task_contract(case_id, options.get('configured_effects', [])))
        sources['TASKS.json'] = str(ws/'TASKS.json')
        if options.get('feedback'):
            sources.update(repair_sources(run_dir, ws, options['feedback']))
        attempt = int(state.get('interpret_attempt', 0)) + 1
        prepared = call_tool('interpret_run', {'run_dir': str(Path(run_dir).resolve()/'benchmark/tools'),
            'front_end': 'interpret-interface', 'inputs': sources, 'mode': 'prepare', 'attempt': attempt,
            'workspace_root': tempfile.mkdtemp(prefix='gd-candidate-')})
        if not prepared.get('ok'):
            raise ValueError('Cannot prepare sealed interpretation')
        state.update(interpret_attempt=attempt, interpret=prepared)
        _write(state_path, state)
        return {'work_dir': prepared['workspace'], 'prompt': prepared['prompt_for_agent'],
                'report_required': False, 'objective': 'Recover the external interface from the supplied binary.',
                'inputs': [str(Path(prepared['workspace'])/name) for name in prepared['sealed_inputs']],
                'allowed_tools': [], 'boundary': 'sealed inputs; encrypted evaluator evidence'}
    raise ValueError('Native stage is not implemented: ' + stage)


def _mission(case_id, events, capabilities):
    """Whether the fresh worker's package calls did the public mission, judged from their outputs and the instrument
    readings of the emulated device: a note on the reuse stage, never a grade. Failed calls are attributed only on
    trusted evidence: untyped or mixed failures blame nobody."""
    try:
        if case_id in ('sampled-sensor-v1', 'parameter-store-v1'):
            done = family_for(case_id).reuse_passed(events, capabilities)
        elif case_id == 'tq9':
            from .cases import mission_done
            done = mission_done(events, capabilities)
        else:
            duties = [e['observation']['duty'] for e in events if e['result'].get('ok')]
            mapping = capabilities['tasks']['temperature']
            output = mapping['outputs']['temperature']['output']
            read = any(e.get('operation') == mapping['operation'] and e['result'].get('ok')
                       and type(e['result'].get('outputs', {}).get(output)) in (int, float) for e in events)
            done = read and any(abs(value - 370) <= 2 for value in duties) and bool(duties) and duties[-1] == 0
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as error:
        return {'name': 'fresh_worker_mission', 'passed': None,
                'reason': 'The capability mapping cannot judge the mission: ' + type(error).__name__ + ': ' + str(error)}
    if done:
        return {'name': 'fresh_worker_mission', 'passed': True}
    failed = [e['result'] for e in events if e['result'].get('ok') is not True]
    faults = [r['error'].get('fault') if isinstance(r.get('error'), dict) else None for r in failed]
    default = None if any(f != 'model' for f in faults) else 'model'
    fault = next((f for f in faults if f in ('host', 'operator')), default)
    return {'name': 'fresh_worker_mission', 'passed': False,
            'reason': 'Fresh worker mission lacks observed reading, requested effect or disarm', **({'fault': fault} if fault else {})}


def _mapping_errors(case_id, mapping, model):
    """The structural check of a probe's capability mapping against its model: {ok, errors}. Legacy tq9 maps its
    tasks in the legacy form (`cases.mapping_errors`)."""
    if case_id == 'tq9':
        from .cases import mapping_errors
        errors = mapping_errors(mapping, model)
        return {'ok': not errors, 'errors': errors}
    from .behavior import validate_capabilities
    return validate_capabilities(mapping, model, _required_tasks(case_id))


# Cases graded after the run from the fresh reuse the run recorded: their reuse hands on that record.
RECORDED = ('tq9',)
REUSE_EVENTS = 'reuse-events.jsonl'


def _host(result):
    """emit_check could not run on this host: the configurator's positive list, or an error that declares a host fault."""
    from ..configurator import _host_fault
    error = result.get('error')
    return _host_fault(result) or isinstance(error, dict) and error.get('fault') == 'host'


def _emitted(ws, report, options):
    """The packages an emit may hand on, as generic emit decides: bytes the configurator observed, here only a package
    an emit_package of this assignment built, by the hash it recorded when the tool returned (`options['emit_builds']`).
    The report may name which package, never its hash. A package the worker wrote or changed is not handed on, so it is
    never graded. Returns (packages, not_handed_off, dropped)."""
    from ..configurator import _observed_packages
    builds, named, dropped = options.get('emit_builds') or {}, [], []
    artifacts = report.get('artifacts') if isinstance(report, dict) else None
    for artifact in artifacts if isinstance(artifacts, list) else []:
        raw = artifact.get('path') if isinstance(artifact, dict) else None
        if not isinstance(raw, str):
            continue
        path = (Path(raw) if Path(raw).is_absolute() else ws/raw).resolve()
        if path.is_relative_to(ws.resolve()):
            named.append(path)
        else:
            dropped.append({'path': raw, 'reason': 'Reported artifact is outside the assigned workspace'})
    packages, not_handed_off, gone = _observed_packages(named, builds, set(builds.values()),
                                                        'not built by emit_package in this assignment')
    return packages, not_handed_off, dropped + gone


def observe_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    """Observe a stage without truth: what it hands on and its checks as notes. No evaluator runs here, nothing is
    graded and no stage verdict is recorded. Only a changed sealed input or recorded hash stops (integrity); emit_check
    the host could not run raises (a resumable host stop). Probe and ground device evidence (the worker's probe records
    and the device diff) is the configurator's own; `options['observed_records']` names the records it hands on, and
    emit hands on only a package `options['emit_builds']` records an emit_package of the assignment building (`_emitted`)."""
    options = options or {}
    ws = Path(workspace)
    _, manifest = _inputs(case_id, run_dir)
    if stage == 'acquire':
        matches, unreadable = [], []
        for provenance in sorted(ws.rglob('provenance.json')):
            try:
                json.loads(provenance.read_text())
                for binary in sorted(provenance.parent.glob('*.bin')):
                    if hashlib.sha256(binary.read_bytes()).hexdigest() == manifest['images']['firmware.bin']:
                        matches.extend([binary, provenance])
            except ValueError:
                continue
            except OSError as error:
                unreadable.append({'path': str(provenance.parent), 'reason': 'Unreadable acquisition output: ' + type(error).__name__})
        reason = 'Unreadable acquisition output' if unreadable and not matches else 'No matching imported firmware'
        return _observed(matches, [{'name': 'imported_firmware_hash', 'passed': bool(matches),
                                    **({} if matches else {'reason': reason})}], unreadable)
    if stage == 'interpret':
        from ..toolkit import call_tool
        state_path, state = _state(run_dir)
        prepared = state['interpret']
        tools = str(Path(run_dir).resolve()/'benchmark/tools')
        result = call_tool('interpret_collect', {'run_dir': tools, 'attempt': state['interpret_attempt'],
            'expected_seal_sha256': prepared['seal_sha256'], 'seal_kind': 'instruction'})
        reason = result.get('reason') or result.get('error')
        if result.get('sealed_inputs_unchanged') is False or (not result.get('ok') and re.search(r'(?i)seal|changed|differ', str(result.get('reason') or ''))):
            return {'stop': {'category': 'integrity', 'reason': 'Sealed interpretation inputs changed or are unverifiable: ' + str(reason)}}
        out = Path(prepared['model_dir'])
        intact = bool(result.get('ok') or result.get('sealed_inputs_unchanged') is True and result.get('frozen_copy') is True)
        present = intact and (out/'model.json').is_file()
        validation = call_tool('model_validate', {'run_dir': tools, 'model_dir': str(out)}) if present else {}
        qualification = result.get('qualification') or {}
        qualified = qualification.get('status') == 'qualified' and not qualification.get('uncovered')
        state.update(model_dir=str(out), interpret_collect=result)
        _write(state_path, state)
        # A model behind an intact seal is handed on whether or not it validates or qualifies: both are notes.
        checks = [{'name': 'sealed_inputs', 'passed': bool(result.get('ok')), **({} if result.get('ok') else {'reason': str(reason)})},
                  {'name': 'executable_model', 'passed': bool(validation.get('ok')),
                   **({'defects': validation['defects']} if validation.get('defects') else {}),
                   **({} if present else {'reason': 'No model.json behind an intact seal'})},
                  {'name': 'predicted_reply_qualification', 'passed': bool(qualified),
                   **{key: qualification[key] for key in ('status', 'uncovered') if key in qualification}}]
        kept = [p for p in sorted(out.glob('*')) if intact and p.is_file() and p.name in ('model.json', 'NOTES.md', 'replies.json')]
        return _observed(kept, checks, scope='Sealed-input integrity, executable model contract and the candidate\'s '
                         'own predicted-reply qualification; meaning rests on the device')
    if stage in ('probe', 'ground'):
        from .candidate_context import remember
        from ..configurator import IntegrityError
        _, state = _state(run_dir)
        artifacts, checks, dropped, files, original = [], [], [], {}, None
        if state.get('adapted_model_dir') and stage == 'probe':
            model = Path(state['adapted_model_dir'])/'model.json'
            if hashlib.sha256(model.read_bytes()).hexdigest() != state['adaptation']['adapted_model_sha256']:
                raise IntegrityError('Probe changed the accepted model')
            artifacts += [path for path in (model, model.parent/'adaptation.json') if path.is_file()]
            files['model.json'], original = model, state['adaptation'].get('original_model_sha256')
        elif stage == 'ground' and (ws/'model/model.json').is_file():
            files['model.json'] = ws/'model/model.json'
            try:
                original = json.loads((ws/'model/adaptation.json').read_text(encoding='utf-8')).get('original_model_sha256')
            except (OSError, ValueError, AttributeError):
                original = None
        if stage == 'probe':
            capabilities, parsed, errors, reason = ws/'capabilities.json', False, None, None
            if capabilities.is_symlink() or capabilities.exists() and not capabilities.resolve().is_relative_to(ws.resolve()):
                reason = 'capabilities.json is a link or outside the workspace; it was not followed'
                dropped.append({'path': str(capabilities), 'reason': reason})
            elif not capabilities.is_file():
                reason = 'Missing capability mapping'
            else:
                try:
                    raw = capabilities.read_bytes()
                except OSError as error:
                    reason = 'capabilities.json is unreadable: ' + type(error).__name__
                    dropped.append({'path': str(capabilities), 'reason': reason})
                else:
                    artifacts.append(capabilities)
                    files['capabilities.json'] = capabilities
                    try:
                        mapping, parsed = json.loads(raw.decode('utf-8')), True
                    except (ValueError, UnicodeError):
                        reason = 'Capability file must contain valid UTF-8 JSON'
            if parsed and files.get('model.json'):
                try:
                    validation = _mapping_errors(case_id, mapping, json.loads(files['model.json'].read_text(encoding='utf-8')))
                except (KeyError, TypeError, AttributeError, IndexError, ValueError) as error:
                    # The mapping check reads the model as a valid one; a model that fails validation still reaches
                    # probe when the emulator can carry it, and its defects are the configurator's model_contract diff.
                    reason = 'The mapping cannot be checked against this model: ' + type(error).__name__ + ': ' + str(error)
                else:
                    errors = validation['errors'] if not validation['ok'] else None
                    reason = 'Invalid capability mapping' if errors else None
            elif parsed:
                reason = 'No adapted model to check the mapping against'
            checks.append({'name': 'capability_mapping', 'passed': reason is None,
                           **({'reason': reason} if reason else {}), **({'errors': errors} if errors else {})})
        live = Path(run_dir)/'benchmark'/f'live-observations-{state.get("revision", 0)}.jsonl'
        if live.is_file():
            artifacts.append(live)
            files['live-observations.jsonl'] = live
        # The replies and notes of the candidate whose model this stage ran, never of another revision.
        files.update(_candidate_files(accepted, original))
        files.update({f'probe-{i}.json': Path(p) for i, p in enumerate(options.get('observed_records', []), 1)})
        # The candidate's own files and the device evidence of its runs, kept for a later repair; nothing graded.
        remember(run_dir, stage, files, report, workspace=ws)
        return _observed(artifacts, checks, dropped, scope='The worker\'s mapping, the adapted model and the live '
                         'journal with instrument readings: device evidence, not a grade')
    if stage == 'emit':
        from ..toolkit import call_tool
        from ..configurator import digest, _emit_check_failure
        packages, not_handed_off, dropped = _emitted(ws, report, options)
        rejected, noted = [], {'not_handed_off': not_handed_off} if not_handed_off else {}
        for package in packages:
            manifest_path = package/'manifest.json'
            result = call_tool('emit_check', {'run_dir': str(Path(run_dir)/'benchmark/package-checks'),
                'package_dir': str(package), 'model_dir': str(ws/'model')})
            if _host(result):
                raise RuntimeError('emit_check could not run on this host for ' + package.name + ': ' + str(result.get('error')))
            if (result.get('ok') and result.get('integrity_ok') and result.get('runtime_current')
                    and result.get('replay_status') == 'passed'):
                path, state = _state(run_dir)
                state.update(package_sha256=digest(package), package_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
                _write(path, state)
                return _observed([package], [{'name': 'package_integrity_and_replay', 'passed': True,
                                              **({'rejected': rejected} if rejected else {}), **noted}], dropped)
            rejected.append({'package': package.name, 'reason': _emit_check_failure(result)})
        return _observed([], [{'name': 'package_integrity_and_replay', 'passed': False,
                               'reason': 'No package emit_package built in this assignment passed integrity and replay',
                               **({'rejected': rejected} if rejected else {}), **noted}],
                         dropped + [{'path': row['package'], 'reason': 'emit_check did not pass: ' + row['reason']} for row in rejected])
    if stage == 'reuse':
        _, state = _state(run_dir)
        events_path = Path(run_dir)/'benchmark/package-events.jsonl'
        events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
        events = [e for e in events if e['actor'] == 'worker' and e['stage'] == stage
                  and e['attempt_id'] == state.get('package_attempts', {}).get(stage) and e['revision'] == state.get('revision', 0)]
        try:
            mapping = json.loads(Path(state['capabilities_path']).read_text(encoding='utf-8')) if state.get('capabilities_path') else None
        except (OSError, ValueError) as error:
            mapping = error
        if not isinstance(mapping, dict):
            mission = {'name': 'fresh_worker_mission', 'passed': None, 'reason': 'No readable capability mapping was handed on'
                       + (': ' + type(mapping).__name__ if isinstance(mapping, Exception) else '')}
        else:
            mission = _mission(case_id, events, mapping)
        package = Path(state.get('package_copies', {}).get(stage) or ws/'missing')
        handed = [package] if package.is_dir() else []
        if case_id in RECORDED:
            # This assignment's package calls with the instrument readings after them, as the configurator recorded them:
            # the evaluator grades the frozen driver from this record after the run.
            record = Path(run_dir)/'benchmark/reuse'/str(options.get('assignment_id'))/REUSE_EVENTS
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(''.join(json.dumps(e, allow_nan=False) + '\n' for e in events
                                      if e.get('assignment_id') == options.get('assignment_id')), encoding='utf-8')
            handed.append(record)
        return _observed(handed, [mission],
                         scope='The fresh worker\'s package calls and the instrument readings after them; a note, not a grade')
    raise ValueError('Native stage is not implemented: ' + stage)


# A device stage of an emulated run without the device its bench harness provides waits for the operator.
NO_DEVICE = ('An emulated case runs only on the device its bench harness provides, and this run has none: start it with '
             '`benchmark run`, whose bench harness provides the device and grades the driver after the run')


def _binding(options):
    """The binding of the device the bench harness provides, as the configurator's durable record holds it now (a
    recovery changes it), or None: the configurator holds no emulator of its own, so a stage without one has no device."""
    device = options.get('device')
    if not isinstance(device, dict) or not isinstance(device.get('binding'), dict) or not device['binding']:
        return None
    return dict(device['binding'])


def package_call(case_id, run_dir, stage, arguments):
    """Check, before anything is dispatched, that the call runs the unchanged stage-assigned package copy, and return
    the call: it runs the package's own CLI over the assigned binding with the assigned grants."""
    from ..configurator import digest
    from .emulated_actions import package_invoker
    _, state = _state(run_dir)
    package = Path(arguments['package_dir']).resolve()
    if str(package) != state.get('package_copies', {}).get(stage) or digest(package) != state.get('package_sha256'):
        raise ValueError('Only the unchanged stage-assigned package may execute')
    invoke = package_invoker(package, arguments['binding'], arguments.get('allow_effects') or [])
    return lambda: invoke(arguments['operation'], arguments.get('parameters') or {})


def record_call(case_id, run_dir, stage, name, arguments, result, before, after, options=None):
    """Journal one live call with the instrument readings around it: a package call for fresh reuse's mission note, a
    model call in the live journal probe and ground hand on. Readings are device evidence; nothing here grades them."""
    path, state = _state(run_dir)
    if name == 'benchmark_package_execute':
        row = {'actor': 'worker', 'stage': stage, 'operation': arguments.get('operation'), 'revision': state.get('revision', 0),
               'attempt_id': state['package_attempts'][stage], 'assignment_id': (options or {}).get('assignment_id'),
               'package_sha256': state['package_sha256'], 'result': result, 'observation': after,
               'before_observation': before}
        with (Path(run_dir)/'benchmark/package-events.jsonl').open('a') as stream:
            stream.write(json.dumps(row, allow_nan=False)+'\n')
        return
    def measured(value):
        kept = {key: value[key] for key in ('ok', 'outputs', 'units', 'transcript', 'error', 'transport_open_attempted')
                if key in value}
        if isinstance(value.get('results'), list):
            kept['results'] = [measured(item) for item in value['results'] if isinstance(item, dict)]
        return kept
    journal = Path(run_dir)/'benchmark'/f'live-observations-{state.get("revision", 0)}.jsonl'
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'operation': arguments.get('operation'), 'parameters': arguments.get('parameters', {}),
            'result': measured(result), 'before_observation': before, 'observation': after}, allow_nan=False)+'\n')


def score(report, password_file=None, *, evidence_path=None):
    """Offline regrade requires an explicit evaluator sidecar; never guesses paths."""
    from .evidence import regrade_v2
    return regrade_v2(report, evidence_path, password_file)

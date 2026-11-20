"""The stages of a run on a supplied device, observed without truth: what each stage's worker gets, and what the stage
hands on with its checks as notes. The client supplies the device and the configurator reaches it through its device
handle; this module starts, holds and stops no device. It takes the firmware and the mission as data and never sees a
case's name."""
import hashlib
import re
import shutil
import tempfile
import json
from pathlib import Path

from ._files import accepted_artifact as _artifact, write_json as _write
from .candidate_context import _state
from ..device_client import offers_readings
from ..integrity import IntegrityError, digest

PAUSED = ('The provided emulator runs only inside each live call and is paused between calls, so device time advances '
          'during a call and not while you work between calls. ')
# Instrument readings are device evidence, as a real device's readings would be; workers see them, nothing grades them.
INSTRUMENT = ('Live interface_execute and probe_run results carry before_observation and observation: instrument '
              'readings of the emulated device with units. They are device evidence, as a real device\'s readings would '
              'be, never a grade. ' + PAUSED)


def _development_conditions(prepared, workspace, options):
    """`prepared` with the catalog of development conditions the device descriptor carries, as a file for the worker
    and guidance at the end of its objective, or `prepared` itself for a descriptor without one. The catalog holds the
    operator's public inputs; no control coordinates or evaluator answers."""
    catalog = ((options or {}).get('device') or {}).get('development_conditions')
    if not catalog:
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


def _required_tasks(mission):
    return tuple(mission['tasks'])


def _capability_guidance(workspace, mission):
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
    result = validate_capabilities(mapping, model, ''' + repr(_required_tasks(mission)) + ''')
print(json.dumps(result, allow_nan=False))
sys.exit(0 if result['ok'] else 1)
''', encoding='utf-8')
    guide = workspace/'CAPABILITY_FORMAT.md'
    guide.write_text('''# Capability mapping format

Declare schema `capabilities/2` and a `tasks` object. Every task has `operation`, `constants`, `inputs` and `outputs`; use empty objects when there are none. Operation, parameter and output names must refer to your candidate model. Constants and mapped inputs cover every operation parameter exactly once.

Copy an input with `{"parameter": "candidate_parameter", "kind": "copy"}`. Numeric conversion uses `{"parameter": "candidate_parameter", "kind": "affine", "scale": 1, "offset": 0}`. `mapping` is not the selector key. Affine conversion is canonical input * scale + offset, checked against the candidate type and bounds.

Map an output with `{"output": "candidate_output", "unit": "candidate_unit"}`. Always include `unit`; use JSON `null` when the candidate output has no unit. This field must agree with the candidate model. Canonical physical units still need to match the public task objective. An operation with only acknowledgment text can use empty outputs when no canonical output is requested.

Example shape, using placeholder names rather than inferred device facts:

```json
{"schema":"capabilities/2","tasks":{"task_name":{"operation":"candidate_operation","constants":{},"inputs":{"canonical_input":{"parameter":"candidate_parameter","kind":"copy"}},"outputs":{"canonical_output":{"output":"candidate_output","unit":null}}}}}
```

Run `python validate_capabilities.py <absolute-model-directory> <absolute-capabilities-json>` before submitting. This pure check uses the same binding rules as the controller, without a device or evaluator answer key. Correct capability-file errors here; report `needs_revision` if the immutable model itself needs repair. Do not edit the supplied validator or rules. A passed mapping check establishes structure, not functional behavior.
''', encoding='utf-8')
    return [str(helper), str(rules), str(guide)]


def _handoff_with(accepted, stages, name, sha256=None):
    """The latest accepted handoff of these stages that carries `name` (with that hash, when given), or None. A
    handoff is chosen first and its files after, so files of different candidates are never paired."""
    return next((h for h in reversed(accepted or []) if h.get('stage') in stages and _artifact(h, name, sha256)), None)


def _latest(accepted, stages, name):
    """(path, recorded sha256) of `name` in the latest accepted handoff of these stages that carries it, or None."""
    return _artifact(_handoff_with(accepted, stages, name), name)


def _verified(found, what):
    """The bytes of an accepted artifact, checked against the hash recorded when it was accepted."""
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


def adapt(run_dir, ws, accepted, options):
    """Probe's model: the latest interpreted model, hash-verified and wired to the supplied device's binding in
    `ws/model`, which the run state records; {'binding'} of the supplied device, or a stop for the operator without one.
    With no model, or one that binding cannot carry, the probe skips and no device is used: the candidate is kept for
    the repair its configurator diff drives."""
    from .evaluate import adapt_model
    found = _latest(accepted, ('interpret',), 'model.json')
    if found is None:
        return {'skip': 'Probe skipped: no interpreted model available'}
    _verified(found, 'interpretation')
    try:
        adaptation = adapt_model(found[0], ws/'model')
    except IntegrityError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        # A model the device's binding cannot carry is the candidate's, never a stop, and no device is used for it.
        # The configurator validates it and that diff drives repair, so the candidate is kept for the repair.
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


def prepare_stage(stage, run_dir, workspace, accepted=None, options=None, *, firmware, mission):
    """What the worker of `stage` gets: its objective, inputs, tools and context, written into `workspace`, or a
    `skip` or `blocked` reason. `firmware` is the supplied image, a dict with the `path` of its file and the `sha256`
    its bytes must have. `mission` is the supplied mission, a dict with `tasks`, `reuse_objective` and optionally
    `operating_conditions`. Both are data: no stage looks anything up by a case's name."""
    options = options or {}
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    readings = offers_readings(options.get('device'))
    if stage == 'acquire':
        source = Path(firmware['path'])
        if hashlib.sha256(source.read_bytes()).hexdigest() != firmware['sha256']:
            raise ValueError('Supplied firmware hash mismatch')
        target = ws/'description.bin'
        shutil.copyfile(source, target)
        return {'objective': 'Import the supplied firmware with acquire_firmware_artifact, verify its hash, and report artifact and provenance paths.',
                'inputs': [str(target)], 'allowed_tools': ['acquire_firmware_artifact'],
                'context': {'source_path': str(target), 'expected_sha256': firmware['sha256'],
                            'origin': 'provided_binary'}, 'boundary': 'provided artifact import'}
    if stage == 'probe':
        adapted = adapt(run_dir, ws, accepted, options)
        if adapted.get('skip') or adapted.get('blocked'):
            return adapted
        binding = adapted['binding']
        guidance = _capability_guidance(ws, mission)
        from .candidate_context import task_contract
        _write(ws/'TASKS.json', task_contract(mission, options.get('configured_effects', [])))
        guidance.append(str(ws/'TASKS.json'))
        objective = ('Test the supplied immutable model over the emulator binding. Write capabilities.json with '
            'schema capabilities/2 and tasks ' + ', '.join(_required_tasks(mission)) + '. '
            'Each task declares operation, constants, inputs and outputs. Map every requested input and output '
            'in TASKS.json, including its physical unit, to the candidate model. Report unsupported capabilities honestly.')
        objective += (' Read TASKS.json for required task semantics, outputs and effect grants. '
            'Read CAPABILITY_FORMAT.md and run validate_capabilities.py with the supplied model directory '
            'and your capabilities.json. Correct mapping errors before submission. '
            + (INSTRUMENT + 'Compare those readings and the observed effects' if readings
               else PAUSED + 'Compare the observed effects')
            + ' with your decoded outputs. Preserve capabilities.json even '
            'when incomplete; report needs_revision and the observed discrepancy if the immutable model needs repair. '
            'Do not invent unsupported units or change the model here. Your observed probe records, capabilities.json and '
            'notes are handed on; where the model fails on the device, that difference goes back to interpretation.')
        return _development_conditions({'objective': objective,
                'inputs': [str(ws/'model'), *guidance],
                'context': {'model_dir': str(ws/'model'), 'capabilities_output': str(ws/'capabilities.json')},
                'allowed_tools': ['interface_describe', 'interface_execute', 'probe_run', 'probe_diff'],
                'binding': binding, 'effects': ['write', 'actuate']}, ws, options)
    if stage == 'ground':
        from .ground import device_evidence
        prepared = device_evidence(accepted, ws, readings=readings)
        if prepared.get('skip'):
            return prepared
        if _binding(options) is None:
            return {'blocked': NO_DEVICE}
        from .candidate_context import task_contract
        tasks = ws/'TASKS.json'
        _write(tasks, task_contract(mission, options.get('configured_effects', [])))
        prepared['inputs'].append(str(tasks))
        prepared['objective'] += (' Read TASKS.json for the required observable behaviors and any public quantity '
                                  'comparisons.')
        return _development_conditions(
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
                            'inputs': [str(ws/'model'), str(ws/'probe.json')],
                            'context': {'model_dir': str(ws/'model'), 'probe': str(ws/'probe.json')},
                            'allowed_tools': ['emit_package', 'emit_check']}
        missing = 'No successful probe record of the model available'
        return {'objective': 'Use emit_package with the supplied immutable model, then emit_check. ' + missing +
                    '; emit_package needs one, so report what is missing.',
                'inputs': [str(ws/'model')], 'context': {'model_dir': str(ws/'model'), 'input_notes': [missing]},
                'allowed_tools': ['emit_package', 'emit_check'], 'input_notes': [missing]}
    if stage == 'reuse':
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
        _write(path, state)
        objective = mission['reuse_objective']
        if 'operating_conditions' in mission:
            objective += ('\nProvided operating conditions (public facts):\n'
                + '\n'.join('- ' + condition for condition in mission['operating_conditions']) + '\n')
        # The stage names no tool: the configurator assigns the tools generated from the package and names them.
        return {'objective': 'Use only the emitted package and this objective: ' + objective + ' '
                    + ('Each result also carries an instrument reading of the emulated device after the call. '
                       if readings else '')
                    + 'Report actual observations and limitations.',
                'inputs': [str(package)], 'allowed_tools': [],
                'context': {'package_dir': str(package)}, 'binding': binding,
                'boundary': 'fresh package-only mission'}
    if stage == 'interpret':
        from ..toolkit import call_tool
        from ..analysis import firmware_analysis_inputs
        state_path, state = _state(run_dir)
        state['revision'] = int(options.get('revision', state.get('revision', 0)))
        sources = {'image.bin': str(Path(firmware['path'])), **firmware_analysis_inputs(ws, options)}
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
        _write(ws/'TASKS.json', task_contract(mission, options.get('configured_effects', [])))
        sources['TASKS.json'] = str(ws/'TASKS.json')
        if options.get('feedback'):
            sources.update(repair_sources(run_dir, ws, options['feedback'], readings=readings))
        attempt = int(state.get('interpret_attempt', 0)) + 1
        prepared = call_tool('interpret_run', {'run_dir': str(Path(run_dir).resolve()/'supplied/tools'),
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


def _mapping_errors(mission, mapping, model):
    """The structural check of a probe's capability mapping against its model: {ok, errors}."""
    from .capabilities import validate_capabilities
    return validate_capabilities(mapping, model, _required_tasks(mission))


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


def observe_stage(stage, run_dir, workspace, report, accepted=None, options=None, *, firmware, mission):
    """Observe a stage without truth: what it hands on and its checks as notes. No evaluator runs here, nothing is
    graded and no stage verdict is recorded. Only a changed sealed input or recorded hash stops (integrity); emit_check
    the host could not run raises (a resumable host stop). Probe and ground device evidence (the worker's probe records
    and the device diff) is the configurator's own; `options['observed_records']` names the records it hands on, and
    emit hands on only a package `options['emit_builds']` records an emit_package of the assignment building
    (`_emitted`). Fresh reuse hands on its package copy with no check: its calls are recorded and not judged. `firmware`
    and `mission` are the supplied inputs, as `prepare_stage` takes them."""
    options = options or {}
    ws = Path(workspace)
    readings = offers_readings(options.get('device'))
    if stage == 'acquire':
        matches, unreadable = [], []
        for provenance in sorted(ws.rglob('provenance.json')):
            try:
                json.loads(provenance.read_text())
                for binary in sorted(provenance.parent.glob('*.bin')):
                    if hashlib.sha256(binary.read_bytes()).hexdigest() == firmware['sha256']:
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
        tools = str(Path(run_dir).resolve()/'supplied/tools')
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
        kept = [p for p in sorted(out.glob('*'))
                if intact and p.is_file() and p.name in ('model.json', 'NOTES.md', 'replies.json')]
        return _observed(kept, checks, scope='Sealed-input integrity, executable model contract and the candidate\'s '
                         'own predicted-reply qualification; meaning rests on the device')
    if stage in ('probe', 'ground'):
        from .candidate_context import remember
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
                    model = json.loads(files['model.json'].read_text(encoding='utf-8'))
                    validation = _mapping_errors(mission, mapping, model)
                except (KeyError, TypeError, AttributeError, IndexError, ValueError) as error:
                    # The mapping check reads the model as a valid one; a model that fails validation still reaches
                    # probe when the binding can carry it, and its defects are the configurator's model_contract diff.
                    reason = 'The mapping cannot be checked against this model: ' + type(error).__name__ + ': ' + str(error)
                else:
                    errors = validation['errors'] if not validation['ok'] else None
                    reason = 'Invalid capability mapping' if errors else None
            elif parsed:
                reason = 'No adapted model to check the mapping against'
            checks.append({'name': 'capability_mapping', 'passed': reason is None,
                           **({'reason': reason} if reason else {}), **({'errors': errors} if errors else {})})
        live = Path(run_dir)/'supplied'/f'live-observations-{state.get("revision", 0)}.jsonl'
        if live.is_file():
            artifacts.append(live)
            files['live-observations.jsonl'] = live
        # The replies and notes of the candidate whose model this stage ran, never of another revision.
        files.update(_candidate_files(accepted, original))
        files.update({f'probe-{i}.json': Path(p) for i, p in enumerate(options.get('observed_records', []), 1)})
        # The candidate's own files and the device evidence of its runs, kept for a later repair; nothing graded.
        remember(run_dir, stage, files, report, workspace=ws)
        return _observed(artifacts, checks, dropped, scope='The worker\'s mapping, the adapted model and the live '
                         'journal' + (' with instrument readings' if readings else '')
                         + ': device evidence, not a grade')
    if stage == 'emit':
        from ..toolkit import call_tool
        from ..configurator import _emit_check_failure
        packages, not_handed_off, dropped = _emitted(ws, report, options)
        rejected, noted = [], {'not_handed_off': not_handed_off} if not_handed_off else {}
        for package in packages:
            manifest_path = package/'manifest.json'
            result = call_tool('emit_check', {'run_dir': str(Path(run_dir)/'supplied/package-checks'),
                'package_dir': str(package), 'model_dir': str(ws/'model')})
            if _host(result):
                raise RuntimeError('emit_check could not run on this host for ' + package.name + ': ' + str(result.get('error')))
            if result.get('ok') and result.get('integrity_ok') and result.get('replay_status') == 'passed':
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
        package = Path(state.get('package_copies', {}).get(stage) or ws/'missing')
        handed = [package] if package.is_dir() else []
        return _observed(handed, [], scope='The fresh worker\'s package calls'
                         + (' and the instrument readings after them' if readings else '')
                         + ', recorded and not judged')
    raise ValueError('Native stage is not implemented: ' + stage)


# A device stage of a run without a supplied device waits for the operator.
NO_DEVICE = ('This stage needs the supplied device, and this run has none: start the run with a device, a firmware and '
             'a mission')


def _binding(options):
    """The binding of the supplied device, as the configurator's durable record holds it now (a recovery changes it),
    or None: the configurator holds no device of its own, so a stage without one has no device."""
    device = options.get('device')
    if not isinstance(device, dict) or not isinstance(device.get('binding'), dict) or not device['binding']:
        return None
    return dict(device['binding'])


def record_call(run_dir, stage, name, arguments, result, before, after, options=None):
    """Journal one live call with the instrument readings around it: a package call in the record of fresh reuse's
    calls, a model call in the live journal probe and ground hand on. Readings are device evidence; nothing here grades
    or judges them."""
    path, state = _state(run_dir)
    offered = offers_readings((options or {}).get('device'))
    readings = {'observation': after, 'before_observation': before} if offered else {}
    if name == 'package_execute':
        row = {'actor': 'worker', 'stage': stage, 'operation': arguments.get('operation'), 'revision': state.get('revision', 0),
               'attempt_id': state['package_attempts'][stage], 'assignment_id': (options or {}).get('assignment_id'),
               'package_sha256': state['package_sha256'], 'parameters': arguments.get('parameters', {}),
               **({'mcp': arguments['mcp']} if arguments.get('mcp') else {}),
               'result': result, **readings}
        with (Path(run_dir)/'supplied/package-events.jsonl').open('a') as stream:
            stream.write(json.dumps(row, allow_nan=False)+'\n')
        return
    def measured(value):
        kept = {key: value[key] for key in ('ok', 'outputs', 'units', 'transcript', 'error', 'transport_open_attempted')
                if key in value}
        if isinstance(value.get('results'), list):
            kept['results'] = [measured(item) for item in value['results'] if isinstance(item, dict)]
        return kept
    journal = Path(run_dir)/'supplied'/f'live-observations-{state.get("revision", 0)}.jsonl'
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'operation': arguments.get('operation'), 'parameters': arguments.get('parameters', {}),
            'result': measured(result), **({'before_observation': before, 'observation': after} if readings else {})},
            allow_nan=False)+'\n')

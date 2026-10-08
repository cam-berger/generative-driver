"""Native benchmark stages, observed without truth; the emulator stays in-process until the harness owns it."""
import hashlib
import re
import shutil
import tempfile
import json
from pathlib import Path

from .cases import _state, _write
from .registry import resolve_case
from .snapshots import read_snapshot

# Instrument readings are device evidence, as a real device's readings would be; workers see them, nothing grades them.
INSTRUMENT = ('Live interface_execute and probe_run results carry before_observation and observation: instrument '
              'readings of the emulated device with units. They are device evidence, as a real device\'s readings would '
              'be, never a grade. ')


def family_for(case_id):
    from . import tq9_v2, sampled_sensor, parameter_store
    families = {'tq9-v2': tq9_v2, 'sampled-sensor-v1': sampled_sensor,
                'parameter-store-v1': parameter_store}
    if case_id not in families:
        raise ValueError('Unknown native family')
    return families[case_id]


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
        session = _session(case_id, run_dir, options)
        path, state = _state(run_dir)
        state.update(adaptation=adaptation, adapted_model_dir=str(ws/'model'), revision=int(options.get('revision', state.get('revision', 0))))
        _write(path, state)
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
        return {'objective': objective,
                'inputs': [str(ws/'model'), *guidance],
                'context': {'model_dir': str(ws/'model'), 'capabilities_output': str(ws/'capabilities.json')},
                'allowed_tools': ['interface_describe', 'interface_execute', 'probe_run', 'probe_diff', 'model_validate'],
                'binding': session.binding, 'effects': ['write', 'actuate']}
    if stage == 'ground':
        from .ground import device_evidence
        prepared = device_evidence(accepted, ws)
        if prepared.get('skip'):
            return prepared
        session = _session(case_id, run_dir, options)
        return {**prepared, 'allowed_tools': ['interface_execute', 'probe_run', 'probe_diff', 'model_validate'],
                'binding': session.binding}
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
        state.setdefault('package_copies', {})[stage] = str(package)
        state.setdefault('package_attempts', {})[stage] = uuid.uuid4().hex
        capabilities = _emitted_mapping(accepted)
        if capabilities is not None:
            _verified(capabilities, 'probe')
        state['capabilities_path'] = str(capabilities[0]) if capabilities else None
        _write(path, state)
        session = _session(case_id, run_dir, options)
        mission = 'Read temperature, enable the device, request 37 percent output, then disarm.'
        if case_id == 'sampled-sensor-v1':
            mission = 'Acquire and read two fresh temperature samples. Report their measured sequence numbers.'
        elif case_id == 'parameter-store-v1':
            mission = 'Commit bank B slot 1 to -7. Stage bank B slot 1 to 9, abort that transaction, then read bank B slot 1. Report observations and whether other cells changed.'
        return {'objective': 'Use only the emitted package and this objective: ' + mission + ' Use benchmark_package_execute for each operation. Find names and arguments in the package reference. Each result also carries an instrument reading of the emulated device after the call. Report actual observations and limitations.',
                'inputs': [str(package)], 'allowed_tools': ['benchmark_package_execute'],
                'context': {'package_dir': str(package)}, 'binding': session.binding,
                'boundary': 'fresh package-only mission'}
    if stage == 'interpret':
        from ..toolkit import call_tool, resources_root
        state_path, state = _state(run_dir)
        state['revision'] = int(options.get('revision', state.get('revision', 0)))
        image = inputs/'firmware.bin'
        sources = {'image.bin': str(image),
                   'ghidra_run.py': str(resources_root()/'tools/workspace/ghidra_run.py'),
                   'ExportDecomp.java': str(resources_root()/'toolchain/skills/interpret-firmware-binary/ExportDecomp.java'),
                   'SeedCortexM.java': str(resources_root()/'toolchain/skills/interpret-firmware-binary/SeedCortexM.java')}
        config = ws/'ANALYSIS_TOOLS.json'
        _write(config, {**{k: str(options[k]) for k in ('ghidra_home', 'java_home') if options.get(k)},
            'optional_cortex_m_prescript': {
                'when': 'Use only when image evidence establishes a Cortex-M vector table at the start of the imported mapping and a Thumb reset entry inside executable bytes. This generic helper does not identify the architecture or infer a load map.',
                'usage': 'After independently establishing loader, processor and mapping, add --prescript SeedCortexM.java --script-path <absolute-workspace> to the supplied ghidra_run.py command. Keep the reasoning and native logs in this workspace.'}})
        sources['ANALYSIS_TOOLS.json'] = str(config)
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


def _host(result):
    """emit_check could not run on this host: the configurator's positive list, or an error that declares a host fault."""
    from ..configurator import _host_fault
    error = result.get('error')
    return _host_fault(result) or isinstance(error, dict) and error.get('fault') == 'host'


def observe_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    """Observe a stage without truth: what it hands on and its checks as notes. No evaluator runs here, nothing is
    graded and no stage verdict is recorded. Only a changed sealed input or recorded hash stops (integrity); emit_check
    the host could not run raises (a resumable host stop). Probe and ground device evidence (the worker's probe records
    and the device diff) is the configurator's own; `options['observed_records']` names the records it hands on."""
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
                from .behavior import validate_capabilities
                validation = validate_capabilities(mapping, json.loads(files['model.json'].read_text(encoding='utf-8')),
                                                   _required_tasks(case_id))
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
        rejected = []
        for manifest_path in sorted(ws.rglob('manifest.json')):
            package = manifest_path.parent
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
                                              **({'rejected': rejected} if rejected else {})}])
            rejected.append({'package': package.name, 'reason': _emit_check_failure(result)})
        return _observed([], [{'name': 'package_integrity_and_replay', 'passed': False,
                               'reason': 'No package passed integrity and replay', **({'rejected': rejected} if rejected else {})}],
                         [{'path': row['package'], 'reason': 'emit_check did not pass: ' + row['reason']} for row in rejected])
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
        return _observed([package] if package.is_dir() else [], [mission],
                         scope='The fresh worker\'s package calls and the instrument readings after them; a note, not a grade')
    raise ValueError('Native stage is not implemented: ' + stage)


def run_call(session, invoke, request):
    session.set_running(True)
    try:
        return invoke(request['operation'], request['parameters'])
    finally:
        session.set_running(False)


def reference_execute(pin, truth, model, capabilities, *, renode, image, output_dir,
                      phase='final', package=None, expected_package_sha256=None, package_final=True):
    """Measured evaluator-only seam using normal models and frozen packages."""
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    if phase not in ('diagnostic', 'final'):
        raise ValueError('Unknown behavior phase')
    from .native import NativeSession
    from .emulated_actions import execute_plan, model_invoker, canonical_package_invoker
    from .behavior import validate_records
    from .snapshots import canonical_digest
    from ..configurator import digest
    from ..toolkit import call_tool
    from interface_runtime.engine import validate_model
    candidate = {**model, 'channel': {'type': 'tcp'}}
    if not validate_model(candidate)['ok']:
        raise ValueError('Reference candidates must be structurally valid before native calibration')
    if package is not None and (not expected_package_sha256 or digest(package) != expected_package_sha256):
        raise ValueError('Frozen package changed before reference execution')
    family = family_for(pin.get('case_id', 'tq9-v2'))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    model_dir = output/'model'
    from .calibration import reference_model_bytes
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir/'model.json').write_bytes(reference_model_bytes(candidate))
    model_hash = hashlib.sha256((model_dir/'model.json').read_bytes()).hexdigest()
    probes = []
    session = NativeSession.start(renode=renode, image=image, recipe=truth['recipe'])
    try:
        frozen = None
        if phase == 'final' and package_final:
            if package is None:
                probed = run_call(session, lambda operation, parameters: call_tool('probe_run', {
                    'run_dir': str(output/'tools'), 'model_dir': str(model_dir), 'operation': operation,
                    'parameters': {}, 'binding': session.binding, 'n': 1}),
                    {'operation': candidate['identity']['operation'], 'parameters': {}})
                if not probed.get('ok') or not probed.get('probe'):
                    raise ValueError('Reference identity probe could not support package emission')
                probes.append(probed['probe'])
                emitted = call_tool('emit_package', {'run_dir': str(output/'tools'),
                    'model_dir': str(model_dir), 'probe': probed['probe']})
                if not emitted.get('ok'):raise ValueError('Reference package emission failed')
                package = Path(emitted['package_dir'])
                expected_package_sha256 = digest(package)
            checked = call_tool('emit_check', {'run_dir': str(output/'tools'),
                'model_dir': str(model_dir), 'package_dir': str(package)})
            if not all(checked.get(k) for k in ('ok', 'integrity_ok', 'runtime_current')):
                raise ValueError('Reference package integrity failed')
            frozen = digest(package)
            if frozen != expected_package_sha256:
                raise ValueError('Frozen package changed before final episode')
            invoke = canonical_package_invoker(package, capabilities, session.binding)
            runtime_root = Path(package)/'interface_runtime'
        else:
            invoke = model_invoker(model_dir, capabilities, session.binding, output/'tools', probes)
            import interface_runtime
            runtime_root = Path(interface_runtime.__file__).parent
        runtime_hash = canonical_digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in sorted(runtime_root.glob('*.py'))})
        local = {**truth, 'artifact_sha256': frozen or model_hash}
        contract = family.contract(pin, local, phase)
        records = execute_plan(session, invoke, family.build_plan(pin, local, phase), contract,
            output/'raw.json', family.observations, truth['contracts']['time_policy']['sample_settle_seconds'])
        if frozen and digest(package) != frozen:
            raise ValueError('Frozen package changed during final episode')
        result = {'execution': 'reference-calibration', 'backend': 'native-renode',
            'native_process_observed': session.process is not None and session.process.poll() is None,
            'process': session.info, 'contract': contract, 'records': records,
            'grade': validate_records(contract, records), 'probes': probes,
            'model_sha256': model_hash, 'capabilities_sha256': canonical_digest(capabilities), 'package_sha256': frozen,
            'artifact_execution': 'emitted-package' if frozen else 'model-runtime',
            'package_dir': str(package) if package is not None else None, 'runtime_sha256': runtime_hash}
        _write(output/'evaluation.json', result)
        return result
    finally:
        session.stop()


_OWNERS = {}


def _truth(case_id, run_dir, options):
    from ..benchmark import case_root
    from .emulator import truth_for_case
    _, manifest = _inputs(case_id, run_dir)
    truth = truth_for_case(case_root(), manifest, options)
    if case_id in ('sampled-sensor-v1', 'parameter-store-v1'):
        from .calibration import hydrate_truth
        return hydrate_truth(truth)
    return truth


def _session(case_id, run_dir, options):
    from .native import NativeSession
    key = str(Path(run_dir).resolve())
    path, state = _state(run_dir)
    if key in _OWNERS:
        return _OWNERS[key]
    if state.get('session'):
        raise RuntimeError('Native process ownership was lost; operator reconciliation is required')
    inputs, _ = _inputs(case_id, run_dir)
    session = NativeSession.start(renode=options.get('renode'),
        image=inputs/'firmware.bin', recipe=_truth(case_id, run_dir, options)['recipe'])
    import uuid
    session.info['session_id'] = uuid.uuid4().hex
    _OWNERS[key] = session
    state['session'] = session.info
    _write(path, state)
    from .emulated_recovery import observed_values
    try:
        observation = observed_values(session)
    except BaseException as failure:
        try:
            cleanup(case_id, run_dir, options)
        except BaseException as stop_error:
            raise RuntimeError(str(failure)+'; emulator cleanup unverified: '+str(stop_error)) from failure
        raise
    state['startup_observation'] = observation
    _write(path, state)
    return session


def cleanup(case_id, run_dir, options=None):
    key = str(Path(run_dir).resolve())
    path, state = _state(run_dir)
    session = _OWNERS.get(key)
    if session is None and state.get('session'):
        raise RuntimeError('Native process ownership was lost; operator reconciliation is required')
    if session is not None:
        session.stop()
        if session.process is not None and session.process.poll() is None:
            raise RuntimeError('Owned emulator stop was not confirmed')
        _OWNERS.pop(key, None)
        state['previous_session'] = state.pop('session', session.info)
        _write(path, state)
    return {'ok': True}


def public_observation(case_id, raw, result, parameters):
    """Instrument readings of the emulated device: projected values and units only, without monitor access or grading
    keys. Device evidence that workers see, as a real device's readings would be."""
    family = family_for(case_id)
    observed = family.observations(raw, result, parameters)
    if case_id == 'tq9-v2':
        return {'duty': observed['duty'], 'unit': 'permille',
                'temperature_reference': raw['values']['temperature'], 'temperature_unit': 'degC',
                'phase': 'diagnostic'}
    return {**{key: observed[key] for key in family.observations.monitor_units},
            'units': dict(family.observations.monitor_units), 'phase': 'diagnostic'}


def package_execute(case_id, run_dir, stage, package_dir, operation, parameters=None,
                    binding=None, allow_effects=None, options=None):
    """Run one operation of the reuse stage's package copy; the result carries the instrument reading after it."""
    from ..configurator import digest
    from .emulated_actions import package_invoker
    family = family_for(case_id)
    path, state = _state(run_dir)
    package = Path(package_dir).resolve()
    if str(package) != state.get('package_copies', {}).get(stage) or digest(package) != state['package_sha256']:
        raise ValueError('Only the unchanged stage-assigned package may execute')
    session = _session(case_id, run_dir, options or {})
    if binding != session.binding:
        raise ValueError('Package binding differs from the evaluator-owned emulator')
    before = public_observation(case_id, session.observe(), {}, parameters or {}) if case_id != 'tq9-v2' else {}
    result = run_call(session, package_invoker(package, binding, allow_effects or []),
                      {'operation': operation, 'parameters': parameters or {}})
    public = public_observation(case_id, session.observe(), result, parameters or {})
    events = Path(run_dir)/'benchmark/package-events.jsonl'
    row = {'actor': 'worker', 'stage': stage, 'operation': operation, 'revision': state.get('revision', 0),
           'attempt_id': state['package_attempts'][stage], 'assignment_id': (options or {}).get('assignment_id'),
           'package_sha256': state['package_sha256'], 'result': result, 'observation': public,
           'before_observation': before}
    with events.open('a') as stream:
        stream.write(json.dumps(row, allow_nan=False)+'\n')
    return {**result, 'observation': public}


def score(report, password_file=None, *, evidence_path=None):
    """Offline regrade requires an explicit evaluator sidecar; never guesses paths."""
    from .evidence import regrade_v2
    return regrade_v2(report, evidence_path, password_file)


def worker_tool(case_id, run_dir, name, arguments, options):
    """Execute a gateway-approved live call with the existing native owner."""
    from ..toolkit import call_tool
    if name not in ('interface_execute', 'probe_run'):
        raise ValueError('Unsupported native worker tool')
    session = _session(case_id, run_dir, options)
    if arguments.get('binding') != session.binding:
        raise ValueError('Worker binding differs from evaluator-owned emulator')
    parameters = arguments.get('parameters', {})
    before = public_observation(case_id, session.observe(), {}, parameters)
    result = run_call(session, lambda operation, parameters: call_tool(name, arguments),
                      {'operation': arguments.get('operation'), 'parameters': parameters})
    observed = public_observation(case_id, session.observe(), result, parameters)
    _, state = _state(run_dir)
    def measured(value):
        kept = {key: value[key] for key in ('ok', 'outputs', 'units', 'transcript', 'error', 'transport_open_attempted')
                if key in value}
        if isinstance(value.get('results'), list):
            kept['results'] = [measured(item) for item in value['results'] if isinstance(item, dict)]
        return kept
    journal = Path(run_dir)/'benchmark'/f'live-observations-{state.get("revision", 0)}.jsonl'
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'operation': arguments.get('operation'), 'parameters': parameters,
            'result': measured(result), 'before_observation': before, 'observation': observed}, allow_nan=False)+'\n')
    return {**result, 'before_observation': before, 'observation': observed}


def recover_emulator(case_id, run_dir, options, cancelled):
    """Replace the owned emulator; the stage's observation keeps the candidate and its device evidence for repair."""
    from .emulated_recovery import replace_owned
    return replace_owned(case_id, run_dir, options, cancelled)

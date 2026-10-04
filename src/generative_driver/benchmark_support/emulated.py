"""Evaluator-owned native benchmark stages, called by the configurator."""
import hashlib
import shutil
import tempfile
import json
from pathlib import Path

from .cases import _state, _write
from .registry import resolve_case
from .snapshots import read_snapshot
from .emulated_evidence import _private, _diagnose, _final


def family_for(case_id):
    from . import tq9_v2, sampled_sensor, parameter_store
    families = {'tq9-v2': tq9_v2, 'sampled-sensor-v1': sampled_sensor,
                'parameter-store-v1': parameter_store}
    if case_id not in families:
        raise ValueError('Unknown native family')
    return families[case_id]


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
    if stage == 'probe' and not any(h.get('stage') == 'interpret' for h in accepted or []):
        raise ValueError('Probe requires an accepted interpret handoff')
    if stage == 'probe':
        from .cases import _accepted_file
        from .evaluate import adapt_model
        from ..configurator import digest
        original = _accepted_file(accepted, 'interpret', 'model.json', ws/'missing')
        handoff = next(h for h in reversed(accepted) if h['stage'] == 'interpret')
        if not any(a['path'] == str(original) and a['sha256'] == digest(original) for a in handoff['artifacts']):
            raise ValueError('Accepted interpretation bytes changed')
        adaptation = adapt_model(original, ws/'model')
        session = _session(case_id, run_dir, options)
        path, state = _state(run_dir)
        state.update(adaptation=adaptation, adapted_model_dir=str(ws/'model'), revision=int(options.get('revision', state.get('revision', 0))))
        _write(path, state)
        objective = 'Test the supplied immutable model over the emulator binding. Write capabilities.json with schema benchmark-capabilities/2 and tasks temperature, arm, set_duty, disarm. Each task has operation, constants, inputs and outputs. Map canonical set_duty input duty (permille) with copy or affine parameter mapping. Map canonical temperature output to its candidate output and declared unit. Report unsupported capabilities honestly.'
        if case_id == 'sampled-sensor-v1':
            objective = 'Test the supplied model over the emulator binding. Write benchmark-capabilities/2 tasks measure and read, mapping temperature (degC) and sequence (count) outputs. Measure performs a new acquisition; read returns the current sample. Report unsupported capabilities honestly.'
        elif case_id == 'parameter-store-v1':
            objective = 'Test the supplied model over the emulator binding. Write benchmark-capabilities/2 tasks read, update, stage, commit and abort. Read takes bank and slot and maps value (configuration-unit). Update and stage take bank, slot and value; commit and abort have no inputs. Every task declares operation, constants, inputs and outputs.'
        return {'objective': objective,
                'inputs': [str(ws/'model/model.json'), str(ws/'model/adaptation.json')],
                'context': {'model_dir': str(ws/'model'), 'capabilities_output': str(ws/'capabilities.json')},
                'allowed_tools': ['interface_describe', 'interface_execute', 'probe_run', 'probe_diff', 'model_validate'],
                'binding': session.binding, 'effects': ['write', 'actuate']}
    if stage == 'ground':
        from .ground import prepare
        return prepare(accepted, ws)
    if stage == 'emit':
        from .cases import _accepted_file
        source = _accepted_file(accepted, 'probe', 'model.json', ws/'missing')
        (ws/'model').mkdir(exist_ok=True)
        shutil.copyfile(source, ws/'model/model.json')
        handoff = next(h for h in reversed(accepted or []) if h['stage'] == 'probe')
        for artifact in handoff['artifacts']:
            candidate = Path(artifact['path'])
            if candidate.suffix != '.json':
                continue
            probe = json.loads(candidate.read_text())
            if probe.get('schema') == 'interface-probe/1' and probe.get('ok'):
                if hashlib.sha256(candidate.read_bytes()).hexdigest() != artifact['sha256']:
                    raise ValueError('Accepted probe bytes changed')
                shutil.copyfile(candidate, ws/'probe.json')
                return {'objective': 'Use emit_package with the supplied immutable model and successful probe, then emit_check. Report the complete package directory.',
                        'inputs': [str(ws/'model/model.json'), str(ws/'probe.json')],
                        'context': {'model_dir': str(ws/'model'), 'probe': str(ws/'probe.json')},
                        'allowed_tools': ['emit_package', 'emit_check']}
        raise ValueError('No accepted successful executable probe')
    if stage == 'reuse':
        from .cases import _accepted_package, _accepted_file
        from ..configurator import digest
        import uuid
        source = _accepted_package(accepted, ws/'missing')
        package = ws/'package'
        shutil.copytree(source, package)
        path, state = _state(run_dir)
        if digest(package) != state['package_sha256']:
            raise ValueError('Accepted package identity changed')
        state.setdefault('package_copies', {})[stage] = str(package)
        state.setdefault('package_attempts', {})[stage] = uuid.uuid4().hex
        state['capabilities_path'] = str(_accepted_file(accepted, 'probe', 'capabilities.json', ws/'missing'))
        _write(path, state)
        session = _session(case_id, run_dir, options)
        mission = 'Read temperature, enable the device, request 37 percent output, then disarm.'
        if case_id == 'sampled-sensor-v1':
            mission = 'Acquire and read two fresh temperature samples. Report their measured sequence numbers.'
        elif case_id == 'parameter-store-v1':
            mission = 'Commit bank B slot 1 to -7. Stage bank B slot 1 to 9, abort that transaction, then read bank B slot 1. Report observations and whether other cells changed.'
        return {'objective': 'Use only the emitted package and this objective: ' + mission + ' Use benchmark_package_execute for each operation. Find names and arguments in the package reference. Report actual observations and limitations.',
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
        if options.get('feedback'):
            _write(ws/'DEFECTS.json', options['feedback'])
            sources['DEFECTS.json'] = str(ws/'DEFECTS.json')
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


def check_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    import json
    _, manifest = _inputs(case_id, run_dir)
    if stage == 'acquire':
        matches, unreadable = [], False
        for provenance in Path(workspace).rglob('provenance.json'):
            try:
                record = json.loads(provenance.read_text())
                for binary in provenance.parent.glob('*.bin'):
                    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
                    if digest == manifest['images']['firmware.bin']:
                        matches.extend([str(binary), str(provenance)])
            except ValueError:
                continue
            except OSError:
                unreadable = True
        ok = bool(matches)
        return {'ok': ok, 'fault': None if ok else 'host' if unreadable else 'model', 'checks': [{'name': 'imported_firmware_hash', 'passed': ok}],
                'artifacts': matches, 'reason': None if ok else 'No matching imported firmware'}
    if stage == 'probe':
        ws = Path(workspace)
        capabilities = ws/'capabilities.json'
        if not capabilities.is_file():
            return {'ok': False, 'fault': 'model', 'checks': [], 'artifacts': [], 'reason': 'Missing capability mapping'}
        _, state = _state(run_dir)
        model = Path(state['adapted_model_dir'])/'model.json'
        if hashlib.sha256(model.read_bytes()).hexdigest() != state['adaptation']['adapted_model_sha256']:
            raise ValueError('Probe changed the accepted model')
        grade, records, probes, observed = _diagnose(case_id, run_dir, model.parent, json.loads(capabilities.read_text()), options or {})
        from .behavior import project_feedback
        ok = grade['verdict'] == 'passed'
        return {'ok': ok, 'fault': None if ok else 'model', 'route': None if ok else 'interpret',
                'reason': None if ok else 'Independent diagnostic behavior failed', 'feedback': project_feedback(records),
                'checks': [{'name': c['id'], 'passed': c['passed']} for c in grade['checks']],
                'artifacts': [str(model), str(capabilities), str(observed), *probes], 'evaluator': grade}
    if stage == 'ground':
        _, state = _state(run_dir)
        ok = bool(state.get('diagnostic', {}).get('passed') and report.get('status') == 'completed')
        return {'ok': ok, 'fault': 'model' if not ok and type(state.get('diagnostic', {}).get('passed')) is bool else None, 'checks': [{'name': 'independent_diagnostic_grounding', 'passed': ok}],
                'artifacts': [str(Path(workspace)/'ground-evidence')]}
    if stage == 'emit':
        from ..toolkit import call_tool
        from ..configurator import digest
        failures = []
        for manifest_path in Path(workspace).rglob('manifest.json'):
            package = manifest_path.parent
            result = call_tool('emit_check', {'run_dir': str(Path(run_dir)/'benchmark/package-checks'),
                'package_dir': str(package), 'model_dir': str(Path(workspace)/'model')})
            failures.append(result)
            if (result.get('ok') and result.get('integrity_ok') and result.get('runtime_current')
                    and result.get('replay_status') == 'passed'):
                path, state = _state(run_dir)
                state.update(package_sha256=digest(package), package_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())
                _write(path, state)
                return {'ok': True, 'checks': [{'name': 'package_integrity_and_replay', 'passed': True}],
                        'artifacts': [str(package)], 'evaluator': {'verdict': 'passed'}}
        faults = [r.get('fault') or (r.get('error', {}).get('fault')
                  if isinstance(r.get('error'), dict) else None) for r in failures]
        fault = next((f for f in faults if f in ('host', 'operator')), None)
        if fault is None and (not failures or all(r.get('integrity_ok') is False
                or r.get('runtime_current') is False or r.get('replay_status') == 'failed' for r in failures)):
            fault = 'model'
        return {'ok': False, 'fault': fault, 'checks': [], 'artifacts': [], 'reason': 'No package passed integrity and replay'}
    if stage == 'reuse':
        _, state = _state(run_dir)
        events_path = Path(run_dir)/'benchmark/package-events.jsonl'
        events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
        events = [e for e in events if e['actor'] == 'worker' and e['stage'] == stage
                  and e['attempt_id'] == state['package_attempts'][stage] and e['revision'] == state.get('revision', 0)]
        capabilities = json.loads(Path(state['capabilities_path']).read_text())
        if case_id in ('sampled-sensor-v1', 'parameter-store-v1'):
            fresh = family_for(case_id).reuse_passed(events, capabilities)
        else:
            duties = [e['observation']['duty'] for e in events if e['result'].get('ok')]
            mapping = json.loads(Path(state['capabilities_path']).read_text())['tasks']['temperature']
            output = mapping['outputs']['temperature']['output']
            read = any(e.get('operation') == mapping['operation'] and e['result'].get('ok')
                       and type(e['result'].get('outputs', {}).get(output)) in (int, float) for e in events)
            fresh = read and any(abs(value - 370) <= 2 for value in duties) and bool(duties) and duties[-1] == 0
        if not fresh:
            failed = [e['result'] for e in events if e['result'].get('ok') is not True]
            faults = [r['error'].get('fault') if isinstance(r.get('error'), dict) else None for r in failed]
            # Untyped package errors can include host failures; mixed evidence is
            # not enough to attribute this incomplete mission to the candidate.
            default = None if any(f != 'model' for f in faults) else 'model'
            fault = next((f for f in faults if f in ('host', 'operator')), default)
            return {'ok': False, 'fault': fault, 'checks': [], 'artifacts': [], 'reason': 'Fresh worker mission lacks observed reading, requested effect or disarm'}
        package = Path(state['package_copies'][stage])
        decision = _final(case_id, run_dir, package, json.loads(Path(state['capabilities_path']).read_text()), options or {})
        return {**decision, 'checks': [{'name': 'fresh_worker_mission', 'passed': True},
                                     {'name': 'frozen_final_behavior', 'passed': decision['ok']}],
                'artifacts': [str(package)]}
    if stage == 'interpret':
        from ..toolkit import call_tool
        state_path, state = _state(run_dir)
        prepared = state['interpret']
        tools = str(Path(run_dir).resolve()/'benchmark/tools')
        result = call_tool('interpret_collect', {'run_dir': tools, 'attempt': state['interpret_attempt'],
            'expected_seal_sha256': prepared['seal_sha256'], 'seal_kind': 'instruction'})
        out = Path(prepared['model_dir'])
        validation = call_tool('model_validate', {'run_dir': tools, 'model_dir': str(out)}) if result.get('ok') else {'ok': False}
        qualification = result.get('qualification', {})
        qualified = qualification.get('status') == 'qualified' and not qualification.get('uncovered')
        ok = bool(result.get('ok') and validation.get('ok') and qualified)
        state.update(model_dir=str(out), interpret_collect=result)
        _write(state_path, state)
        model_failure = not ok and (result.get('ok') is True
            or result.get('sealed_inputs_unchanged') is False
            or result.get('sealed_inputs_unchanged') is True and result.get('frozen_copy') is True)
        return {'ok': ok, 'route': 'interpret' if not ok and result.get('ok') else None,
                'fault': 'model' if model_failure else None,
                'feedback': {'structural_defects': validation.get('defects', []), 'qualification': qualification},
                'checks': [{'name': 'sealed_inputs', 'passed': bool(result.get('ok'))},
                           {'name': 'executable_model', 'passed': bool(validation.get('ok'))},
                           {'name': 'predicted_reply_qualification', 'passed': bool(qualified)}],
                'artifacts': [str(p) for p in out.glob('*') if p.is_file() and p.name in ('model.json', 'NOTES.md', 'replies.json')]}
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
    _OWNERS[key] = session
    state['session'] = session.info
    _write(path, state)
    return session


def cleanup(case_id, run_dir, options=None):
    key = str(Path(run_dir).resolve())
    path, state = _state(run_dir)
    session = _OWNERS.pop(key, None)
    if session is None and state.get('session'):
        raise RuntimeError('Native process ownership was lost; operator reconciliation is required')
    if session is not None:
        session.stop()
        state['previous_session'] = state.pop('session', session.info)
        _write(path, state)
    return {'ok': True}


def package_execute(case_id, run_dir, stage, package_dir, operation, parameters=None,
                    binding=None, allow_effects=None, options=None):
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
    def public_observation(raw, result):
        observed = family.observations(raw, result, parameters or {})
        if case_id == 'tq9-v2':
            return {'duty': observed['duty'], 'unit': 'permille',
                    'temperature_reference': raw['values']['temperature'], 'temperature_unit': 'degC',
                    'phase': 'diagnostic'}
        return {**{key: observed[key] for key in family.observations.monitor_units},
                'units': dict(family.observations.monitor_units), 'phase': 'diagnostic'}
    before = public_observation(session.observe(), {}) if case_id != 'tq9-v2' else {}
    result = run_call(session, package_invoker(package, binding, allow_effects or []),
                      {'operation': operation, 'parameters': parameters or {}})
    public = public_observation(session.observe(), result)
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
    return run_call(session, lambda operation, parameters: call_tool(name, arguments),
                    {'operation': arguments.get('operation'), 'parameters': arguments.get('parameters', {})})

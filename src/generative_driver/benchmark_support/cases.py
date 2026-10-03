"""Benchmark-stage preparation and evaluator handoffs, owned by the configurator."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def _state(run_dir):
    path = Path(run_dir).resolve() / 'benchmark' / 'state.json'
    return path, json.loads(path.read_text()) if path.exists() else {}


def _accepted_file(accepted, stage, filename, fallback):
    for handoff in reversed(accepted or []):
        if handoff.get('stage') != stage:
            continue
        for artifact in handoff.get('artifacts', []):
            path = Path(artifact['path'] if isinstance(artifact, dict) else artifact)
            prefix, separator, original = path.name.partition('-')
            if (path.name == filename or separator and prefix.isdigit() and original == filename) and path.is_file():
                return path
        raise ValueError('Accepted ' + stage + ' handoff has no ' + filename)
    return Path(fallback)


def _accepted_package(accepted, fallback):
    for handoff in reversed(accepted or []):
        if handoff.get('stage') != 'emit':
            continue
        for artifact in handoff.get('artifacts', []):
            path = Path(artifact['path'] if isinstance(artifact, dict) else artifact)
            if path.is_dir() and (path/'manifest.json').is_file():
                return path
        raise ValueError('Accepted emit handoff has no package')
    return Path(fallback)


def _case_inputs(run_dir=None):
    from ..benchmark import case_root
    if run_dir is not None and (Path(run_dir) / 'benchmark/execution.json').is_file():
        from .snapshots import read_snapshot
        saved = read_snapshot(run_dir)
        from .registry import validate_pinned_workflow
        validate_pinned_workflow(saved['case_pin'])
        return Path(run_dir) / 'benchmark/inputs', saved['case_pin']['manifest']
    root = case_root() / 'cases/tq9'
    return root, json.loads((root / 'case.json').read_text())


def _load_case(options):
    from ..benchmark import case_root
    from .emulator import truth_for_case
    root = case_root()
    _, manifest = _case_inputs(options.get('_execution_run_dir'))
    return root, manifest, truth_for_case(root, manifest, options)


def _session(state, options):
    from .emulator import RenodeSession
    root, manifest, truth = _load_case(options)
    if not state.get('session'):
        inputs, _ = _case_inputs(options.get('_execution_run_dir'))
        image = inputs / 'firmware.bin'
        state['session'] = RenodeSession.start(options.get('renode'), image, truth).info
    return state['session'], truth


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    from ..benchmark import case_root
    from ..toolkit import call_tool, resources_root
    options = options or {}
    if case_id != 'tq9':
        raise ValueError('Only tq9 currently supplies an agent stage fixture')
    state_path, state = _state(run_dir)
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    inputs_root, manifest = _case_inputs(run_dir)
    image_name = 'firmware.bin'
    image = inputs_root / image_name
    if hashlib.sha256(image.read_bytes()).hexdigest() != manifest['images'][image_name]:
        raise ValueError('Benchmark firmware does not match the case hash')
    if stage == 'acquire':
        description = ws / 'description.bin'
        shutil.copy2(image, description)
        return {'objective': 'Import the supplied owned firmware artifact using acquire_firmware_artifact. '
                            'Verify its hash and report the returned artifact and provenance paths. '
                            'The provided binary is the description; no hardware capture is claimed.',
                'inputs': [str(description)], 'allowed_tools': ['acquire_firmware_artifact'],
                'context': {'source_path': str(description), 'expected_sha256': manifest['images']['firmware.bin'],
                            'origin': 'provided_binary'}, 'boundary': 'provided artifact import'}
    if stage == 'interpret':
        state['revision'] = int(options.get('revision', state.get('revision', 0)))
        inputs = {'image.bin': str(image),
                  'ghidra_run.py': str(resources_root() / 'tools/workspace/ghidra_run.py'),
                  'ExportDecomp.java': str(resources_root() / 'toolchain/skills/interpret-firmware-binary/ExportDecomp.java')}
        tool_config = ws / 'ANALYSIS_TOOLS.json'
        _write(tool_config, {k: str(options[k]) for k in ('ghidra_home', 'java_home') if options.get(k)})
        inputs['ANALYSIS_TOOLS.json'] = str(tool_config)
        if options.get('feedback'):
            defects = ws/'DEFECTS.json'
            _write(defects, options['feedback'])
            inputs['DEFECTS.json'] = str(defects)
        prepared = call_tool('interpret_run', {'run_dir': str(Path(run_dir).resolve() / 'benchmark/tools'),
            'front_end': 'interpret-interface', 'inputs': inputs, 'mode': 'prepare',
            'attempt': int(state.get('interpret_attempt', 0)) + 1,
            'workspace_root': tempfile.mkdtemp(prefix='gd-candidate-')})
        if not prepared.get('ok'):
            raise ValueError(prepared.get('reason', prepared.get('error', 'Cannot prepare interpret')))
        state['interpret_attempt'] = int(state.get('interpret_attempt', 0)) + 1
        state['interpret'] = prepared
        _write(state_path, state)
        return {'work_dir': prepared['workspace'], 'prompt': prepared['prompt_for_agent'],
                'report_required': False, 'objective': 'Recover the executable external interface from the supplied binary.',
                'inputs': [str(Path(prepared['workspace']) / name) for name in prepared['sealed_inputs']],
                'allowed_tools': [], 'boundary': 'instruction boundary; encrypted evaluator evidence; sealed input hashes'}
    if stage == 'probe':
        from .evaluate import adapt_model
        original = _accepted_file(accepted, 'interpret', 'model.json', Path(state['model_dir'])/'model.json')
        state['adaptation'] = adapt_model(original, ws / 'model')
        state['adapted_model_dir'] = str(ws / 'model')
        session, truth = _session(state, options)
        from .emulator import RenodeSession
        RenodeSession(session).monitor('start')
        _write(state_path, state)
        objective = ('Probe the recovered interface over the supplied emulator connection. The host explicitly adapted '
            'the recovered UART channel to TCP; original channel settings remain separately recorded. Do not change model.json. '
            'Use the shared interface/probe tools to test operations, then write capabilities.json mapping these tasks to '
            'the recovered operations: temperature (operation, parameters, output); arm (operation, parameters); '
            'set_duty (operation, parameters that request 65 percent output); disarm (operation, parameters). '
            'Each entry is an object; parameters defaults to {}. Report unsupported tasks honestly. '
            'A separate evaluator executes that mapping and observes the output through an independent channel.')
        return {'objective': objective, 'inputs': [str(ws/'model/model.json'), str(ws/'model/adaptation.json')],
                'context': {'model_dir': str(ws/'model'), 'capabilities_output': str(ws/'capabilities.json')},
                'allowed_tools': ['interface_describe', 'interface_execute', 'probe_run', 'probe_diff', 'model_validate'],
                'binding': session['binding'], 'effects': ['write', 'actuate']}
    if stage == 'ground':
        from .ground import prepare
        return prepare(accepted, ws)
    if stage == 'emit':
        model = ws/'model'
        source = _accepted_file(accepted, 'probe', 'model.json',
                                Path(state.get('adapted_model_dir', ws/'missing-model'))/'model.json')
        model.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, model/'model.json')
        handoff = next((h for h in reversed(accepted or []) if h.get('stage') == 'probe'), None)
        probe_paths = ([a['path'] if isinstance(a, dict) else a for a in handoff.get('artifacts', [])]
                       if handoff else state.get('probe_evaluation', {}).get('probes', []))
        successful = None
        for candidate in probe_paths:
            path = Path(candidate)
            if not path.is_file() or path.suffix != '.json':
                continue
            evidence = json.loads(path.read_text(encoding='utf-8'))
            if evidence.get('schema') == 'interface-probe/1' and evidence.get('ok'):
                successful = path
                break
        if successful is None:
            raise ValueError('Accepted probe handoff has no successful executable probe evidence')
        shutil.copy2(successful, ws/'probe.json')
        return {'objective': 'Use emit_package to package the supplied model and actual probe evidence, then emit_check. '
                'Do not repair the model during packaging. Report the package directory as an artifact and its '
                'manifest hash; state that the package uses emulator TCP wiring and evidence covers specific operations.',
                'inputs': [str(model/'model.json'), str(ws/'probe.json')],
                'context': {'model_dir': str(model), 'probe': str(ws/'probe.json')},
                'allowed_tools': ['emit_package', 'emit_check']}
    if stage == 'reuse':
        package = ws/'package'
        source = _accepted_package(accepted, state.get('package_dir', ws/'missing-package'))
        shutil.copytree(source, package, dirs_exist_ok=True)
        state.setdefault('package_copies', {})[stage] = str(package)
        state.setdefault('package_attempts', {})[stage] = __import__('uuid').uuid4().hex
        session, truth = _session(state, options)
        from .emulator import RenodeSession
        RenodeSession(session).monitor('pause')
        _write(state_path, state)
        if stage == 'reuse':
            objective = ('Use only the emitted package and this objective: read the temperature, enable the device if '
                'needed, request 37 percent output, and disarm it. Use benchmark_package_execute for each package operation; '
                'it executes the package and supplies an independent output observation. Find operation/argument names '
                'in package/REFERENCE.md or package/driver/model.json. Do not import development history. '
                'Report observed values, operations attempted and limitations.')
        return {'objective': objective, 'inputs': [str(package)], 'allowed_tools': ['benchmark_package_execute'],
                'context': {'package_dir': str(package)}, 'binding': session['binding'], 'effects': ['write', 'actuate'],
                'boundary': 'fresh package-only context; encrypted evaluator evidence'}
    raise ValueError('Benchmark stage is not implemented yet: ' + stage)


def check_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    from ..benchmark import case_root
    from ..toolkit import call_tool
    state_path, state = _state(run_dir)
    ws = Path(workspace)
    if case_id != 'tq9':
        raise ValueError('Unknown benchmark agent case: ' + case_id)
    if stage == 'acquire':
        _, manifest = _case_inputs(run_dir)
        # The import tool is independently observed through its provenance artifact.
        matches = []
        for provenance in ws.rglob('provenance.json'):
            try:
                item = json.loads(provenance.read_text())
                for binary in provenance.parent.glob('*.bin'):
                    if hashlib.sha256(binary.read_bytes()).hexdigest() == manifest['images']['firmware.bin']:
                        matches.extend([str(binary), str(provenance)])
            except (ValueError, OSError):
                continue
        ok = bool(matches)
        return {'ok': ok, 'reason': None if ok else 'No matching imported artifact and provenance found',
                'checks': [{'name': 'imported_firmware_hash', 'passed': ok}], 'artifacts': matches}
    if stage == 'interpret':
        prepared = state.get('interpret', {})
        result = call_tool('interpret_collect', {'run_dir': str(Path(run_dir).resolve() / 'benchmark/tools'),
            'attempt': state['interpret_attempt'],
            'expected_seal_sha256': prepared.get('seal_sha256'), 'seal_kind': 'instruction'})
        out = Path(prepared.get('model_dir', ws))
        validation = call_tool('model_validate', {'run_dir': str(Path(run_dir).resolve() / 'benchmark/tools'),
                                                 'model_dir': str(out)}) if result.get('ok') else {'ok': False}
        qualification = result.get('qualification', {})
        qualified = qualification.get('status') == 'qualified' and not qualification.get('uncovered')
        ok = bool(result.get('ok') and validation.get('ok') and qualified)
        state['model_dir'] = str(out)
        state['interpret_collect'] = result
        _write(state_path, state)
        artifacts = [str(p) for p in out.glob('*') if p.is_file() and p.name in ('model.json', 'NOTES.md', 'replies.json')]
        return {'ok': ok, 'route': 'interpret' if not ok and result.get('ok') else None,
                'fault': 'model' if not ok and result.get('ok') else None,
                'feedback': {'structural_defects': validation.get('defects', []), 'qualification': qualification},
                'reason': None if ok else result.get('reason', 'Model validation or predicted-reply qualification failed'),
                'checks': [{'name': 'sealed_inputs', 'passed': bool(result.get('ok'))},
                           {'name': 'executable_model', 'passed': bool(validation.get('ok'))},
                           {'name': 'predicted_reply_qualification', 'passed': qualified}],
                'artifacts': artifacts, 'evaluator': {'structural': validation, 'collection': result}}
    if stage == 'probe':
        from .evaluate import evaluate_model
        capabilities = ws/'capabilities.json'
        if not capabilities.is_file():
            return {'ok': False, 'reason': 'Probe did not supply executable task mapping capabilities.json', 'artifacts': [], 'checks': []}
        model = Path(state['adapted_model_dir'])/'model.json'
        if hashlib.sha256(model.read_bytes()).hexdigest() != state['adaptation']['adapted_model_sha256']:
            return {'ok': False, 'reason': 'Probe modified its handed-off model', 'artifacts': [], 'checks': []}
        session, truth = _session(state, options or {})
        output = Path(run_dir)/'benchmark'/f'evaluation-{state.get("revision", 0)}'
        try:
            evaluated = evaluate_model(state['adapted_model_dir'], json.loads(capabilities.read_text()), session, truth, output)
        except (KeyError, ValueError, OSError) as error:
            return {'ok': False, 'reason': 'Independent probe could not execute task mapping: '+str(error), 'checks': [], 'artifacts': []}
        state['probe_evaluation'] = evaluated
        _write(state_path, state)
        ok = evaluated['score']['verdict'] == 'passed'
        from ..benchmark import evaluation_fault
        fault = evaluation_fault(evaluated)
        model_fault = fault == 'model'
        return {'ok': ok, 'route': 'interpret' if model_fault else None, 'fault': fault,
                'feedback': {'defects': [{'task': row['id'], 'observed': row['observed'], 'reason': 'Observed behavior does not satisfy the assigned task'}
                             for row in evaluated['score']['checks'] if not row['passed']]},
                'reason': None if ok else 'Independent outputs/effects do not meet the case contract',
                'checks': [{'name': row['id'], 'passed': row['passed']} for row in evaluated['score']['checks']],
                'artifacts': [str(capabilities), str(output/'observations.json'), str(model), *evaluated['probes']], 'evaluator': evaluated['score']}
    if stage == 'ground':
        ok = state['probe_evaluation']['score']['verdict'] == 'passed' and report.get('status') == 'completed'
        return {'ok': ok, 'reason': None if ok else 'Grounding has missing or contradictory evidence',
                'checks': [{'name': 'independent_output_observation', 'passed': ok}],
                'artifacts': [str(ws/'ground-evidence')]}
    if stage == 'emit':
        manifests = list(ws.rglob('manifest.json'))
        for manifest_path in manifests:
            package = manifest_path.parent
            checked = call_tool('emit_check', {'run_dir': str(Path(run_dir).resolve()/'benchmark/tools'),
                                              'package_dir': str(package), 'model_dir': str(ws/'model')})
            if (checked.get('ok') and checked.get('integrity_ok') and checked.get('runtime_current')
                    and checked.get('replay_status') == 'passed'):
                state['package_dir'] = str(package)
                state['package_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                _write(state_path, state)
                return {'ok': True, 'checks': [{'name': 'package_integrity_and_replay', 'passed': True}],
                        'artifacts': [str(package)], 'evaluator': checked}
        return {'ok': False, 'reason': 'No emitted package passed the host package check', 'checks': [], 'artifacts': []}
    if stage == 'reuse':
        events_path = Path(run_dir)/'benchmark/package-events.jsonl'
        events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
        attempt = state.get('package_attempts', {}).get(stage)
        events = [e for e in events if attempt and e.get('attempt_id') == attempt
                  and e['stage'] == stage and e['revision'] == state.get('revision', 0)]
        if stage == 'reuse':
            _, _, truth = _load_case(options or {})
            contract = truth['reuse_goal']
            duties = [e['observation']['duty'] for e in events if e.get('ok') and e.get('observation')]
            reached = any(abs(d-contract['fraction']) <= contract['tolerance'] for d in duties)
            disarmed = bool(duties) and abs(duties[-1]-contract['final_fraction']) <= contract['tolerance']
            temperature_spec = state['probe_evaluation']['capabilities']['temperature']
            reference = next(c for c in truth['checks'] if c['id'] == 'temperature')
            readings = [e['result'].get('outputs', {}).get(temperature_spec['output']) for e in events
                        if e.get('ok') and e['operation'] == temperature_spec['operation']]
            read_ok = any(type(v) in (int, float) and abs(v-reference['expected']) <= reference['absolute_tolerance'] for v in readings)
            ok = reached and disarmed and read_ok
            return {'ok': ok, 'reason': None if ok else 'Fresh package execution did not establish requested output and disarm',
                    'checks': [{'name': 'fresh_package_temperature', 'passed': read_ok},
                               {'name': 'fresh_package_requested_output', 'passed': reached},
                               {'name': 'fresh_package_disarm', 'passed': disarmed}], 'artifacts': [str(events_path)]}
    raise ValueError('Benchmark stage is not implemented yet: ' + stage)

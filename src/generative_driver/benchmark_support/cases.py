"""Legacy tq9 stages, observed without truth. The bench harness provides the legacy emulator from the legacy recipe and
the configurator reaches it through its device handle, as for the emulated v2 cases, whose stages these share
(`emulated.py`); interpretation and the probe's legacy task mapping are tq9's own. Nothing here unlocks truth or grades:
the evaluator grades the recorded fresh reuse once, after the run. The state helpers are shared with the other adapters."""
import hashlib
import json
import tempfile
from pathlib import Path

# The evaluator uses these too, so they are defined in a module of its own and imported here under their old names.
from .case_assets import mapping_errors, write_json as _write


def _state(run_dir):
    path = Path(run_dir).resolve() / 'benchmark' / 'state.json'
    return path, json.loads(path.read_text()) if path.exists() else {}


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


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    from . import emulated
    from ..toolkit import call_tool, resources_root
    options = options or {}
    if case_id != 'tq9':
        raise ValueError('Only tq9 currently supplies an agent stage fixture')
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    # A device stage with something to run waits for the operator when the run has no device its bench harness
    # provides: the configurator never starts an emulator itself.
    if stage in ('acquire', 'ground', 'emit', 'reuse'):
        return emulated.prepare_stage(case_id, stage, run_dir, ws, accepted, options)
    state_path, state = _state(run_dir)
    inputs_root, manifest = _case_inputs(run_dir)
    image = inputs_root / 'firmware.bin'
    if hashlib.sha256(image.read_bytes()).hexdigest() != manifest['images']['firmware.bin']:
        raise ValueError('Benchmark firmware does not match the case hash')
    if stage == 'interpret':
        state['revision'] = int(options.get('revision', state.get('revision', 0)))
        inputs = {'image.bin': str(image),
                  'ghidra_run.py': str(resources_root() / 'tools/workspace/ghidra_run.py'),
                  'ExportDecomp.java': str(resources_root() / 'toolchain/skills/interpret-firmware-binary/ExportDecomp.java')}
        tool_config = ws / 'ANALYSIS_TOOLS.json'
        _write(tool_config, {k: str(options[k]) for k in ('ghidra_home', 'java_home') if options.get(k)})
        inputs['ANALYSIS_TOOLS.json'] = str(tool_config)
        if options.get('feedback'):
            # The repair input is the device diff: what the device did that the previous candidate did not predict.
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
                'allowed_tools': [], 'boundary': 'instruction boundary; sealed input hashes'}
    if stage == 'probe':
        adapted = emulated.adapt(run_dir, ws, accepted, options)
        if adapted.get('skip') or adapted.get('blocked'):
            return adapted
        objective = ('Probe the recovered interface over the supplied emulator connection. The host explicitly adapted '
            'the recovered UART channel to TCP; original channel settings remain separately recorded. Do not change model.json. '
            'Use the shared interface/probe tools to test operations, then write capabilities.json mapping these tasks to '
            'the recovered operations: temperature (operation, parameters, output); arm (operation, parameters); '
            'set_duty (operation, parameters that request 65 percent output); disarm (operation, parameters). '
            'Each entry is an object; parameters defaults to {}. Report unsupported tasks honestly. ' + emulated.INSTRUMENT +
            'Compare those readings and the observed effects with your decoded outputs. Your observed probe records, '
            'capabilities.json and notes are handed on; where the model fails on the device, that difference goes back '
            'to interpretation.')
        return {'objective': objective, 'inputs': [str(ws/'model')],
                'context': {'model_dir': str(ws/'model'), 'capabilities_output': str(ws/'capabilities.json')},
                'allowed_tools': ['interface_describe', 'interface_execute', 'probe_run', 'probe_diff'],
                'binding': adapted['binding'], 'effects': ['write', 'actuate']}
    raise ValueError('Benchmark stage is not implemented yet: ' + stage)


def observe_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    """Legacy tq9 stages observed without truth, as the emulated cases' are: what each hands on and its checks as notes.
    Reuse also hands on its recorded package calls, from which the evaluator grades the frozen driver after the run."""
    from . import emulated
    if case_id != 'tq9':
        raise ValueError('Unknown benchmark agent case: ' + case_id)
    return emulated.observe_stage(case_id, stage, run_dir, workspace, report, accepted, options)

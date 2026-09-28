"""Independent behavior grading through runtime/package public interfaces."""
import hashlib
import json
from pathlib import Path

from .emulator import RenodeSession


def adapt_model(original, destination):
    """Explicit UART-to-TCP bench wiring; preserve inferred serial settings separately."""
    original, destination = Path(original), Path(destination)
    model = json.loads(original.read_text())
    channel = model['channel']
    if channel.get('type') not in ('uart', 'tcp'):
        raise ValueError('This emulator connects the recovered host command interface over a UART socket')
    destination.mkdir(parents=True, exist_ok=True)
    model['channel'] = {'type': 'tcp'}
    (destination / 'model.json').write_text(json.dumps(model, indent=2) + '\n')
    record = {'kind': 'emulator UART-to-TCP wiring', 'original_model_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
              'original_channel': channel, 'adapted_model_sha256': hashlib.sha256((destination/'model.json').read_bytes()).hexdigest(),
              'limitation': 'TCP does not measure UART baud, parity or pin configuration'}
    (destination / 'adaptation.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def evaluate_model(model_dir, capabilities, session_info, truth, output_dir):
    from ..benchmark import score_observations
    from ..toolkit import call_tool
    model_dir, output_dir = Path(model_dir).resolve(), Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    session = RenodeSession(session_info)
    observations, calls, probes = {}, [], []
    adaptation = model_dir/'adaptation.json'
    if adaptation.exists():
        observations['inferred_baud'] = json.loads(adaptation.read_text())['original_channel'].get('baudrate')
    def execute(name, grants=('write', 'actuate')):
        spec = capabilities[name]
        result = call_tool('probe_run', {'run_dir': str(output_dir), 'model_dir': str(model_dir),
             'operation': spec['operation'], 'parameters': spec.get('parameters', {}), 'n': 1,
             'binding': session_info['binding'], 'allow_effects': list(grants)})
        calls.append({'capability': name, 'result': result})
        if result.get('probe'):
            probes.append(result['probe'])
        return result
    temperature = execute('temperature')
    if temperature.get('ok'):
        observations['temperature'] = temperature['results'][0]['outputs'].get(capabilities['temperature']['output'])
    refusal = execute('set_duty', ())
    refusal_results = refusal.get('results', [])
    observations['no_grant'] = bool(not refusal.get('ok') and refusal.get('fault') == 'operator'
                                    and refusal_results and all(not r.get('transcript') for r in refusal_results))
    enabled = execute('arm')
    if enabled.get('ok'):
        changed = execute('set_duty')
        if changed.get('ok'):
            observations['requested_duty'] = session.observe(truth)['duty']
    disabled = execute('disarm')
    if disabled.get('ok'):
        observations['disarmed_duty'] = session.observe(truth)['duty']
    score = score_observations(truth, observations)
    result = {'schema': 'benchmark-observation/1', 'execution': 'actual-agent-emulation',
              'observations': observations, 'score': score, 'calls': calls, 'probes': probes, 'capabilities': capabilities,
              'model_sha256': hashlib.sha256((model_dir/'model.json').read_bytes()).hexdigest(),
              'observation_channel': 'independent Renode monitor', 'physical': False}
    (output_dir/'observations.json').write_text(json.dumps(result, indent=2) + '\n')
    return result

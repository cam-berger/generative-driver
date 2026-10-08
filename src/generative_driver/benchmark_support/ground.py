"""Grounding handoff: accepted observations and candidate artifacts, without oracle answers."""
import hashlib
import json
from pathlib import Path


def _named(path, name):
    prefix, separator, original = path.name.partition('-')
    return path.name == name or bool(separator) and prefix.isdigit() and original == name


def device_evidence(accepted, workspace):
    """Ground inputs from the latest probe handoff that carries the adapted model: that model, as the assigned model
    directory, the worker's own probe records, its capability mapping and the live journal with the instrument
    readings of the emulated device. Device evidence only: no grade, expected value or evaluator record."""
    handoff = next((h for h in reversed(accepted or []) if h.get('stage') == 'probe'
                    and any(_named(Path(a['path']), 'model.json') for a in h.get('artifacts', []))), None)
    if handoff is None:
        return {'skip': 'Ground skipped: no interpreted model available'}
    ws = Path(workspace).resolve()
    model_dir, bundle = ws/'model', ws/'ground-evidence'
    model_dir.mkdir(parents=True, exist_ok=True); bundle.mkdir(parents=True, exist_ok=True)
    verified = []
    for artifact in handoff['artifacts']:
        path = Path(artifact['path'])
        if not path.is_file():
            continue
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.get('sha256'):
            raise ValueError('Accepted probe artifact hash changed: ' + path.name)
        verified.append((path, content, artifact['sha256']))
    # Copies of the verified bytes, so a later change cannot replace what ground receives.
    inputs, probes, mapping, live, model_sha = [str(model_dir)], [], None, None, None
    for path, content, sha in verified:
        if _named(path, 'model.json') or _named(path, 'adaptation.json'):
            # The probe's assigned model directory, byte for byte: records it wrote are compared with this model.
            (model_dir/('model.json' if _named(path, 'model.json') else 'adaptation.json')).write_bytes(content)
            model_sha = sha if _named(path, 'model.json') else model_sha
            continue
        if path.name.startswith('live-observations') or '-live-observations' in path.name:
            target = bundle/'live-observations.jsonl'; target.write_bytes(content)
            live = {'artifact': target.relative_to(ws).as_posix(), 'sha256': sha}
        elif _named(path, 'capabilities.json'):
            target = bundle/'capabilities.json'; target.write_bytes(content)
            mapping = {'artifact': target.relative_to(ws).as_posix(), 'sha256': sha}
        else:
            try:
                document = json.loads(content)
            except ValueError:
                continue
            if not isinstance(document, dict) or document.get('schema') != 'interface-probe/1':
                continue
            target = bundle/f'probe-{len(probes)+1}.json'; target.write_bytes(content)
            probes.append({'operation': document.get('operation'), 'ok': document.get('ok'),
                           'model_sha256': document.get('model_sha256'), 'artifact': target.relative_to(ws).as_posix(),
                           'sha256': sha})
        inputs.append(str(target))
    index = bundle/'device-observations.json'
    index.write_text(json.dumps({'schema': 'benchmark-ground-evidence/3', 'model': {'directory': 'model', 'sha256': model_sha},
        'capabilities': mapping, 'probes': probes, 'live_observations': live,
        'instrument': 'before_observation and observation are instrument readings of the emulated device with units: '
                      'device evidence, as a real device\'s readings would be, never a grade.'}, indent=1) + '\n', encoding='utf-8')
    notes = [note for note, missing in (('No probe record of the model available', not probes),
                                        ('No live journal available', live is None)) if missing]
    return {'objective': 'Test the meanings the supplied model gives the emulated device: units, scaling and the effects '
                'of its operations. Run the model in model/ with probe_run and interface_execute through the '
                'configurator-owned emulator connection. Live results carry before_observation and observation, '
                'instrument readings of the emulated device with units; they are device evidence, as a real device\'s '
                'readings would be, never a grade. Compare decoded outputs and effects with those readings, the supplied '
                'probe records, capability mapping and journal (ground-evidence/device-observations.json). Report what '
                'the device did that the model did not predict, and needs_revision for a specific contradiction.',
            'inputs': [str(index), *inputs], 'context': {'model_dir': str(model_dir), 'evidence': str(index)},
            **({'input_notes': notes} if notes else {})}


def prepare(accepted, workspace):
    """Legacy tq9 ground evidence from its accepted probe observations."""
    ws = Path(workspace)/'ground-evidence'
    handoff = next((item for item in reversed(accepted or []) if item.get('stage') == 'probe'), None)
    if not handoff:
        raise ValueError('Ground requires the accepted probe evidence')
    files, contents = {}, {}
    for artifact in handoff.get('artifacts', []):
        path = Path(artifact['path'])
        if not path.is_file():
            continue
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact.get('sha256'):
            raise ValueError('Accepted probe artifact hash changed: ' + path.name)
        files[digest] = path
        contents[digest] = content
    def named(name):
        for digest, path in files.items():
            prefix, separator, original = path.name.partition('-')
            if path.name == name or separator and prefix.isdigit() and original == name:
                return digest, path
        raise ValueError('Accepted probe handoff lacks ' + name)
    observed_hash, observed_path = named('observations.json')
    raw = json.loads(contents[observed_hash])
    model_hash, model = named('model.json')
    if model_hash != raw.get('model_sha256'):
        raise ValueError('Accepted observations and model hashes disagree')
    evidence_dir = ws/'evidence'; evidence_dir.mkdir(parents=True, exist_ok=True)
    model_copy = evidence_dir/'model.json'; model_copy.write_bytes(contents[model_hash])
    inputs, calls, probe_documents = [str(model_copy)], [], []
    for index, call in enumerate(raw.get('calls', []), 1):
        digest = call.get('probe_sha256')
        # Earlier captures retained the original filename but not this redundant
        # hash. Resolve only within the immutable accepted artifact set.
        if digest:
            path = files.get(digest)
            if path is None:
                raise ValueError('Accepted probe transaction artifact is missing')
        else:
            digest, path = named(Path(call['result']['probe']).name)
        probe = json.loads(contents[digest])
        if probe.get('model_sha256') != model_hash:
            raise ValueError('Probe transaction and accepted model hashes disagree')
        copied = evidence_dir/f'probe-{index}.json'; copied.write_bytes(contents[digest])
        inputs.append(str(copied))
        calls.append({'capability': call['capability'], 'grants': call.get('grants', []),
                      'artifact': copied.relative_to(ws).as_posix(), 'sha256': digest,
                      'fault': call.get('result', {}).get('fault')})
        probe_documents.append((call, probe))
    temperature_output = raw.get('capabilities', {}).get('temperature', {}).get('output')
    temperature_unit = None
    for call, probe in probe_documents:
        if call['capability'] == 'temperature' and probe.get('results'):
            temperature_unit = probe['results'][0].get('units', {}).get(temperature_output)
    values = raw.get('observations', {})
    meanings = {
        'temperature': ('Candidate-decoded temperature from the recorded live protocol reply; unit declared by the candidate model. This is not an independent physical temperature reference.', temperature_unit),
        'requested_duty': ('Measured actual PWM duty after the set-output operation, observed through the independent Renode register monitor. The legacy key names the task, not the requested input.', 'fraction'),
        'disarmed_duty': ('Measured actual PWM duty after the disarm operation, observed through the independent Renode register monitor.', 'fraction'),
        'no_grant': ('Host permission gate refused the operation before device I/O with an operator fault and no transactions. This is not a device refusal reply.', 'boolean'),
        'inferred_baud': ('UART baud declared in the original candidate model; TCP execution does not measure serial baud.', 'baud'),
    }
    monitor = raw.get('monitor_observations', {})
    limitations = ['Emulated register effects are not physical measurements.',
                   'Temperature decoding and units come from the candidate model; no private reference value is supplied.']
    if not monitor:
        limitations.append('Legacy capture retained independently observed duty scalars but not raw register counts or monitor responses. Those missing records cannot be reconstructed from the scalars.')
    public = {'schema': 'benchmark-ground-evidence/1', 'execution': raw['execution'],
        'physical': raw['physical'], 'observation_channel': raw.get('observation_channel'),
        'source_observations_sha256': observed_hash,
        'model': {'artifact': model_copy.relative_to(ws).as_posix(), 'sha256': model_hash},
        'image_sha256': raw.get('image_sha256'), 'capabilities': raw.get('capabilities', {}),
        'measurements': {key: {'value': values[key], 'meaning': meaning, 'unit': unit}
                         for key, (meaning, unit) in meanings.items() if key in values},
        'monitor_observations': monitor, 'calls': calls, 'limitations': limitations}
    evidence = ws/'independent-observations.json'
    evidence.write_text(json.dumps(public, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return {'objective': 'Assess the supplied recorded evidence for a temperature read, measured 65 percent output, '
        'host permission refusal before I/O, and disarm. Follow artifact hashes, candidate decoding/units, live TX/RX '
        'and independent monitor observations. The legacy requested_duty field denotes the measured actual duty '
        'after the requested operation. The no-grant task is a host policy gate; no device refusal reply is expected. '
        'Distinguish emulation from physical measurement and candidate decoding from an independent temperature '
        'reference. Report disagreement or missing evidence, including capture limitations. '
        'Cite ground-evidence/independent-observations.json and its supporting artifacts.',
        'inputs': [str(evidence), *inputs], 'allowed_tools': [],
        'context': {'independent_channel': 'Renode monitor for output effects; recorded runtime transactions for protocol behavior'}}

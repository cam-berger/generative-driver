"""Grounding handoff of an emulated case (v2 or legacy tq9): the probed model, the worker's probe records and the
instrument journal, without oracle answers."""
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

"""Public task semantics and sealed continuity for a candidate's own revisions."""
import hashlib
import json
import tempfile
from pathlib import Path

from .cases import _state, _write


def task_contract(case_id, effects):
    def task(meaning, inputs=None, outputs=None):
        return {'meaning': meaning, 'inputs': inputs or {}, 'outputs': outputs or {}}
    temperature = {'temperature': {'unit': 'degC'}}
    location = {'bank': {'type': 'string'}, 'slot': {'type': 'integer'}}
    change = {**location, 'value': {'type': 'integer', 'unit': 'configuration-unit'}}
    families = {
        'tq9-v2': {
            'temperature': task('Read the current temperature.', outputs=temperature),
            'arm': task('Enable the output so a later set_duty can take effect.'),
            'set_duty': task('Set the enabled output to the requested duty.',
                {'duty': {'type': 'integer', 'unit': 'permille', 'minimum': 0, 'maximum': 1000}}),
            'disarm': task('Disable the output and leave its measured duty at zero.')},
        'sampled-sensor-v1': {
            'measure': task('Perform a new acquisition, returning its temperature and sequence.',
                outputs={**temperature, 'sequence': {'unit': 'count'}}),
            'read': task('Return the current sample and sequence without acquiring another sample.',
                outputs={**temperature, 'sequence': {'unit': 'count'}})},
        'parameter-store-v1': {
            'read': task('Read the committed value of the selected bank and slot.', location,
                {'value': {'unit': 'configuration-unit'}}),
            'update': task('Commit the requested change, preserving all other cells and leaving no pending transaction.', change),
            'stage': task('Begin and stage the requested change, leaving the transaction pending and committed values unchanged.', change),
            'commit': task('Apply the pending transaction and close it.'),
            'abort': task('Discard the pending transaction, preserving committed values, and close it.')},
    }
    return {'schema': 'candidate-task-contract/1', 'tasks': families[case_id],
            'effect_grants': list(effects),
            'instructions': 'These are requested observable behaviors, not protocol facts or hidden test values. '
                'Recover command bytes, operation composition, bounds and decoders from the supplied evidence. '
                'One canonical task can require several exchange steps in a model operation. '
                'Effect labels describe actual behavior: acquisition and configuration changes are writes; '
                'actuation controls a physical output. Read is implicit; other effects require the listed grants. '
                'Do not relabel unsafe operations merely to bypass a grant. Report unsupported behavior.'}


def worker_claim(report):
    report = report or {}
    unresolved = report.get('unresolved', [])
    return {'summary': str(report.get('summary', ''))[:2000],
            'unresolved': [item[:1000] for item in unresolved if isinstance(item, str)][:20]
                          if isinstance(unresolved, list) else []}


def require_evidence_path(source, roots):
    source = Path(source)
    if source.is_symlink() or not any(source.resolve().is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError('Repair input outside assigned evidence roots')


def remember(run_dir, stage, files, feedback, report=None, workspace=None):
    """Capture bounded candidate files; callers select files, never report paths."""
    path, state = _state(run_dir)
    parent = Path(run_dir)/'benchmark/candidate-history'
    parent.mkdir(parents=True, exist_ok=True)
    content = {}
    roots = [Path(run_dir)] + ([Path(workspace)] if workspace else [])
    for name, source in files.items():
        source = Path(source)
        if source.is_file() or source.is_symlink():
            require_evidence_path(source, roots)
            content[name] = source.read_bytes()
    root = Path(tempfile.mkdtemp(prefix=f'{state.get("revision", 0)}-{stage}-', dir=parent))
    content['feedback.json'] = (json.dumps(feedback, allow_nan=False)+'\n').encode()
    content['worker-report.json'] = (json.dumps(worker_claim(report), allow_nan=False)+'\n').encode()
    hashes = {}
    for name, raw in content.items():
        destination = root/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    state.setdefault('candidate_history', []).append({'revision': state.get('revision', 0),
        'stage': stage, 'root': str(root.resolve()), 'hashes': hashes})
    _write(path, state)


def repair_sources(run_dir, workspace, feedback):
    """Reverify this run's saved evidence before interpretation seals its copy."""
    _, state = _state(run_dir)
    sources, history = {}, []
    for index, entry in enumerate(state.get('candidate_history', [])):
        files = {}
        for name, expected in entry['hashes'].items():
            source = Path(entry['root'])/name
            require_evidence_path(source, [Path(run_dir)/'benchmark/candidate-history'])
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('Candidate repair evidence hash changed')
            relative = f'previous/{index}-{entry["stage"]}/{name}'
            # Copy verified bytes, so a later change cannot replace the sealed input.
            copied = Path(workspace)/relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(raw)
            sources[relative] = str(copied)
            files[name] = relative
        history.append({'revision': entry['revision'], 'stage': entry['stage'], 'files': files})
    context = Path(workspace)/'REPAIR_CONTEXT.json'
    _write(context, {'schema': 'candidate-repair-context/1', 'mode': 'repair',
        'task_contract': 'TASKS.json', 'feedback': 'DEFECTS.json', 'history': history,
        'evidence_policy': 'Supplied diagnostic measurements, captures and this run\'s previous candidates '
            'are authorized evidence. Use them to correct the latest candidate. Worker reports are claims; '
            'compare them with the observed records. Do not access other runs, evaluator passwords, '
            'plaintext oracle files or hidden final answers. Preserve sealed inputs; write corrected outputs '
            'at the workspace root and explain each correction in NOTES.md.'})
    sources['REPAIR_CONTEXT.json'] = str(context)
    defects = Path(workspace)/'DEFECTS.json'; _write(defects, feedback)
    sources['DEFECTS.json'] = str(defects)
    return sources

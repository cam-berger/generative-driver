"""Public suite fixtures; these helpers never execute a runtime or native tools."""
import json


def pilot_manifest():
    return json.loads('''{
      "schema": "benchmark-suite/1",
      "id": "development-pilot",
      "version": "1",
      "entries": [
        {"case": "tq9-v2", "scenario": "semantic", "case_seed": 0},
        {"case": "tq9-v2", "scenario": "control", "case_seed": 0},
        {"case": "sampled-sensor-v1", "scenario": "semantic", "case_seed": 0},
        {"case": "sampled-sensor-v1", "scenario": "control", "case_seed": 0},
        {"case": "parameter-store-v1", "scenario": "semantic", "case_seed": 0},
        {"case": "parameter-store-v1", "scenario": "control", "case_seed": 0}
      ],
      "repetitions": 3,
      "child_budget_seconds": 10800,
      "suite_budget_seconds": 216000,
      "max_active_children": 1
    }''')


def legacy_manifest():
    manifest = pilot_manifest()
    manifest['entries'] = [{'case': 'tq9', 'scenario': 'identity', 'case_seed': 0}]
    return manifest


def public_case_fixture(root, case_id='tq9-v2', *, calibration=None, **changes):
    """Local registry inputs for pure freeze tests; never native qualification."""
    import hashlib
    case = root / 'cases' / case_id
    case.mkdir(parents=True)
    image = case / 'toy.bin'; image.write_bytes(b'public suite contract image')
    truth = root / 'groundtruth' / (case_id + '.enc')
    truth.parent.mkdir(exist_ok=True); truth.write_bytes(b'public suite contract opaque fixture')
    manifest = {
        'schema': 'benchmark-case/2', 'id': case_id, 'family': 'toy-family',
        'version': '1', 'evaluator_version': '2', 'execution': 'actual-agent-emulation',
        'evidence_track': 'firmware', 'scope': 'full-workflow', 'adapter_key': 'emulator-v2',
        'approval_scope': 'emulator', 'default_effects': ['write'], 'scenarios': ['semantic', 'control'],
        'required_stages': ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse', 'maintain'],
        'images': {'toy.bin': hashlib.sha256(image.read_bytes()).hexdigest()},
        'truth': {'path': 'groundtruth/' + case_id + '.enc', 'sha256': hashlib.sha256(truth.read_bytes()).hexdigest()},
        'calibration': {'status': 'passed'} if calibration is None else calibration,
        'time_policy': {'probe': 'toy virtual time'}, 'provenance': {'execution': 'scripted-contract-fixture'},
        'limitations': ['Pure suite freeze fixture; not native calibration'], **changes}
    (case / 'case.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest

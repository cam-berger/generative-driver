"""Pure evaluator helpers for family-specific observations and private phases."""
from copy import deepcopy


def integer(values, name):
    value = values[name] if name in values else None
    if type(value) is not int:
        raise ValueError("Unavailable integer observation: " + name)
    return value


def signed16(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("Unavailable unsigned 16-bit register")
    return value - 65536 if value & 32768 else value


def integer_vector(values, name, length):
    value = values.get(name)
    if not isinstance(value, list) or len(value) != length or any(type(x) is not int for x in value):
        raise ValueError("Unavailable integer vector: " + name)
    return list(value)


def private_phase(pin, truth, phase):
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    if phase not in ('diagnostic', 'final'):
        raise ValueError('Unknown behavior phase')
    if truth["family"] != pin["family"]:
        raise ValueError("Family evidence mismatch")
    phases = truth["phases"]
    result = deepcopy(phases[phase])
    result["contract"]["artifact_sha256"] = truth["artifact_sha256"]
    for check in result["contract"]["checks"]:
        check["revision"] = pin.get("revision", 0)
    seed = pin.get('case_seed', 0)
    if seed and result['actions'] and result['actions'][0]['kind'] == 'reset':
        episodes = []
        for action in result['actions']:
            if action['kind'] == 'reset':
                episodes.append([])
            episodes[-1].append(action)
        offset = seed % len(episodes)
        result['actions'] = [action for episode in episodes[offset:] + episodes[:offset] for action in episode]
    return result


def hydrate_family_truth(truth):
    """The family-truth inventory projected into the evaluator engine, by calibration, which only a grading path loads."""
    from .calibration import hydrate_truth
    return hydrate_truth(truth)


def admission_valid(pin, truth, record):
    """Whether `record` is the measured evidence of `truth`, the shared admission of the families qualified from an
    authenticated inventory: its digest, inputs, images, mutants, executed inputs and every run's contract must be the
    ones `truth` and the pinned manifest commit to, and `validate_calibration` must accept it."""
    from .calibration import apply_mutation, execution_input_identity, validate_calibration
    from .families import family_for
    from .snapshots import canonical_digest
    manifest = pin['manifest']
    expected = {r['id']: r['expected_failed_checks'] for r in truth['mutations']['mutants']}
    changes = {}
    mutant_inputs = {}
    for mutation in truth['mutations']['mutants']:
        source = truth['references'][mutation['reference']]
        changed = apply_mutation(source['model'], source['capabilities'], mutation)
        mutant_inputs[mutation['id']] = execution_input_identity(changed['model'], changed['capabilities'])
        changes[mutation['id']] = (changed['source_sha256'], changed['mutated_sha256'])
    valid = (truth.get('inventory_state') == 'qualified'
             and canonical_digest(record) == manifest['calibration']['evidence_sha256']
             and truth['input_hashes'] == record['input_hashes']
             and truth['images'] == manifest['images']
             and {r['id']: r['expected_failed_checks'] for r in record['mutants']} == expected
             and {r['id']: (r['source_sha256'], r['mutated_sha256']) for r in record['mutants']} == changes
             and {r['id']: r['model_sha256'] for r in record['references']} ==
                 {name: canonical_digest(ref['model']) for name, ref in truth['references'].items()}
             and validate_calibration(manifest, record)['ok'])
    family = family_for(pin['case_id'])
    runs = {r['id']: r for r in record['runs']}
    for reference in record['references']:
        source = truth['references'][reference['id']]
        for name in reference['runs']:
            runs[name]
            valid = (valid and reference['execution_inputs'][name] == execution_input_identity(source['model'], source['capabilities']))
    for row in record['mutants']:
        valid = valid and row['execution_input'] == mutant_inputs[row['id']]
    for run in runs.values():
        artifact = run['package_sha256'] or run['model_sha256']
        contract = family.contract({'family': manifest['family'], 'scenario_id': run['scenario'], 'case_seed': 0, 'revision': 0},
                                   {**truth, 'artifact_sha256': artifact}, run['phase'])
        valid = (valid and canonical_digest(contract) == run['contract_sha256']
                 and [c['id'] for c in contract['checks']] == run['check_ids'])
    rows = {r['id']: r for r in record['mutants']}
    for mutation in truth['mutations']['mutants']:
        run = runs[rows[mutation['id']]['run']]
        valid = valid and run['phase'] == mutation['phase'] and run['scenario'] == mutation.get('scenario', 'original')
    return valid

"""TQ9 behavior contracts and plans derived only from evaluator-owned truth, and its admission: the mutants a record must
cover, the input identities it must carry and its check."""
import copy

REQUIRED_MUTANTS = frozenset({'wrong-scale', 'constant-output', 'wrong-state', 'unsigned-temperature',
                              'wrong-temperature-scale', 'wrong-temperature-unit', 'wrong-temperature-byteorder',
                              'wrong-crc', 'wrong-duty-effect'})
CORE_MUTANTS = ('wrong-scale', 'constant-output', 'wrong-state')


def _scenario(pin, truth):
    scenarios = truth['contracts']['scenarios']
    if pin.get('scenario_id') != 'original' or 'original' not in scenarios:
        raise ValueError('Unknown case scenario')
    return scenarios['original']


def _definition(pin, truth, phase):
    if phase not in ('diagnostic', 'final'):
        raise ValueError('Unknown behavior phase')
    checks, plan = [], []
    revision = pin.get('revision', 0)
    units = truth['contracts']['units']
    episode = phase
    def add(step, suffix, expected, channel, unit, tolerance=0, kind='number'):
        identifier = f'{episode}/{step}/{suffix}'
        checks.append({'id': identifier, 'revision': revision, 'kind': kind,
                       'expected': expected, 'absolute_tolerance': tolerance, 'channel': channel, 'unit': unit})
        plan.append({'episode': episode, 'step': str(step), 'kind': 'observe', 'checks': [identifier]})
    def action(step, kind, **kwargs):
        plan.append({'episode': episode, 'step': str(step), 'kind': kind, **kwargs})
    scenario = _scenario(pin, truth)
    phases = scenario.get('phases', {})
    if phase not in phases:
        raise ValueError('TQ9 behavior requires a separate private phase inventory')
    inventory = phases[phase]
    all_vectors = inventory['temperature_vectors']
    effects = inventory.get('effect_vectors', truth['contracts'].get('effect_vectors', []))
    vectors = list(enumerate(all_vectors))
    if phase != 'diagnostic':
        offset = pin.get('case_seed', 0) % len(vectors)
        vectors = vectors[offset:] + vectors[:offset]
    for index, vector in vectors:
        step = 'temperature-' + str(index)
        action(step, 'reset', values={'temperature': vector['stimulus']})
        action(step, 'call', task='temperature', inputs={}, grants=[])
        add(step, 'temperature', vector['expected'], 'runtime-transcript', units['temperature'], vector['absolute_tolerance'])
    action('effects-initial', 'reset', values={'temperature': all_vectors[-1]['stimulus']})
    requested = next(v['inputs'] for v in effects if v['task'] == 'set_duty')
    action('denied', 'call', task='set_duty', inputs=requested, grants=[])
    add('denied', 'refused_without_io', True, 'runtime-transcript', 'boolean', kind='boolean')
    add('denied', 'duty', 0, 'independent-monitor', units['duty'])
    for index, vector in enumerate(effects):
        step = 'effect-' + str(index)
        action(step, 'call', task=vector['task'], inputs=vector['inputs'], grants=['write', 'actuate'])
        if vector['task'] in ('set_duty', 'disarm'):
            expected = vector['inputs']['duty'] if vector['task'] == 'set_duty' else 0
            add(step, 'duty', expected, 'independent-monitor', units['duty'], .002)
    return checks, plan


def contract(pin: dict, truth: dict, phase: str) -> dict:
    checks, _ = _definition(pin, truth, phase)
    return {'schema': 'benchmark-behavior/1', 'artifact_sha256': truth['artifact_sha256'], 'checks': checks}


def build_plan(pin: dict, truth: dict, phase: str) -> list[dict]:
    return _definition(pin, truth, phase)[1]


def observations(raw: dict, canonical_task_result: dict, inputs: dict) -> dict:
    values = raw['values']
    # A refusal of a model that fails validation carries a plain error string and no fault.
    error = canonical_task_result.get('error')
    fault = error.get('fault') if isinstance(error, dict) else None
    return {**canonical_task_result.get('outputs', {}),
            'refused_without_io': bool(not canonical_task_result.get('ok') and fault == 'operator' and not canonical_task_result.get('transcript')),
            'duty': 1000 * values['compare'] / (values['reload'] + 1)}


observations.monitor_units = {'duty': 'permille'}


def reuse_passed(events, capabilities):
    """Observe the public temperature, requested duty and final disarm mission."""
    duties = [event['observation']['duty'] for event in events if event['result'].get('ok')]
    mapping = capabilities['tasks']['temperature']
    output = mapping['outputs']['temperature']['output']
    read = any(event.get('operation') == mapping['operation'] and event['result'].get('ok')
               and type(event['result'].get('outputs', {}).get(output)) in (int, float) for event in events)
    return read and any(abs(value - 370) <= 2 for value in duties) and bool(duties) and duties[-1] == 0


def tq9_candidate(truth, reference_name, scenario, mutation=None):
    """Derive the declared legacy reference variant without executing it."""
    reference=truth['references'][reference_name]
    if scenario != 'original':raise ValueError('Unknown reference scenario')
    candidate=copy.deepcopy(reference['model'])
    caps=copy.deepcopy(reference['capabilities'])
    if isinstance(mutation,str):
        task=caps['tasks']['temperature']
        decoder=candidate['operations'][task['operation']]['outputs'][task['outputs']['temperature']['output']]
        if mutation=='wrong-scale':decoder['scale']=decoder.get('scale',1)*10
        elif mutation=='constant-output':decoder.update(scale=0,offset_value=0)
        elif mutation=='wrong-state':candidate['operations'][caps['tasks']['arm']['operation']]=copy.deepcopy(candidate['operations'][caps['tasks']['disarm']['operation']])
        else:raise ValueError('Unknown legacy mutation')
    elif mutation is not None:
        for patch in mutation['patch']:
            if patch['op']!='replace':raise ValueError('Unsupported reference mutant patch')
            parts=patch['path'].split('/')[1:];target=candidate
            for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
            target[int(parts[-1]) if isinstance(target,list) else parts[-1]]=copy.deepcopy(patch['value'])
        for mapping in caps['tasks'].values():
            for output_mapping in mapping['outputs'].values():
                output_mapping['unit']=candidate['operations'][mapping['operation']]['outputs'][output_mapping['output']].get('unit')
    return candidate,caps


def tq9_execution_inputs(truth):
    """Expected identities from authenticated reference and mutation definitions."""
    from .calibration import REQUIRED_SCENARIOS, execution_input_identity
    result={}
    for name in truth['references']:
        for phase in ('diagnostic','final'):
            result[name+'-'+phase+'-original']=execution_input_identity(*tq9_candidate(truth,name,'original'))
    for scenario in REQUIRED_SCENARIOS:
        for mutation in CORE_MUTANTS:
            result[mutation+'-'+scenario]=execution_input_identity(*tq9_candidate(truth,'reference-a',scenario,mutation))
    for mutation in truth['mutations']['mutations']:
        scenario='original'
        result[mutation['id']]=execution_input_identity(*tq9_candidate(truth,'reference-a',scenario,mutation))
    return result


def validate_record(manifest, record):
    from .calibration import validate_calibration
    checked=validate_calibration(manifest,record)
    try:
        measured={run['id']:{k:run[k] for k in ('model_sha256','capabilities_sha256')} for run in record['runs']}
        if len(measured)!=len(record['runs']) or measured!=record['execution_inputs']:
            checked['failures'].append('executed input identities')
    except (KeyError,TypeError):checked['failures'].append('missing executed input identities')
    return {'ok':not checked['failures'],'failures':checked['failures']}


def _admission_valid(pin, truth, record):
    """Whether `record` is the measured evidence of `truth`: its digest, inputs, images, mutants, executed inputs and every
    run's contract must be the ones `truth` and the pinned manifest commit to, and `validate_record` must accept it."""
    from .calibration import input_hashes
    from .snapshots import canonical_digest
    manifest = pin['manifest']
    valid = (canonical_digest(record) == manifest['calibration']['evidence_sha256']
             and input_hashes(truth) == record['input_hashes']
             and truth['images'] == manifest['images']
             and set(record['required_mutants']) == set(CORE_MUTANTS) | set(truth['mutations']['required'])
             and record['execution_inputs'] == tq9_execution_inputs(truth)
             and {r['id']: r['expected_failed_checks'] for r in record['mutants']} ==
                 truth['mutations']['expected_failed_checks']
             and validate_record(manifest, record)['ok'])
    for run in record['runs']:
        artifact = run['package_sha256'] or run['model_sha256']
        scoring = contract({'scenario_id': run['scenario'], 'case_seed': 0, 'revision': 0},
                           {**truth, 'artifact_sha256': artifact}, run['phase'])
        valid = (valid and canonical_digest(scoring) == run['contract_sha256']
                 and [c['id'] for c in scoring['checks']] == run['check_ids'])
    return valid


def _instrument_units():
    return {'duty': 'permille', 'temperature_reference': 'degC'}


def _public_observation(raw):
    return {'values': {'duty': observations(raw, {}, {})['duty'], 'temperature_reference': raw['values']['temperature']},
            'units': _instrument_units()}


HOOKS = {'contract': contract, 'build_plan': build_plan, 'observations': observations, 'reuse_passed': reuse_passed,
         'required_mutants': REQUIRED_MUTANTS, 'admission_valid': _admission_valid, 'instrument_units': _instrument_units,
         'public_observation': _public_observation,
         'input_commitments': frozenset({'source', 'contract', 'recipe', 'references', 'mutations', 'build', 'analysis'})}

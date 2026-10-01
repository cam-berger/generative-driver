"""TQ9 behavior contracts and plans derived only from evaluator-owned truth."""


def _scenario(pin, truth):
    scenarios = truth['contracts']['scenarios']
    return scenarios.get(pin['scenario_id'], scenarios['semantic'])


def _definition(pin, truth, phase):
    if phase not in ('diagnostic', 'final', 'maintenance'):
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
    all_vectors = _scenario(pin, truth)['temperature_vectors']
    vectors = list(enumerate(all_vectors))
    if phase == 'diagnostic':
        vectors = vectors[-1:]
    else:
        offset = pin.get('case_seed', 0) % len(vectors)
        vectors = vectors[offset:] + vectors[:offset]
    for index, vector in vectors:
        step = 'temperature-' + str(index)
        action(step, 'reset', values={'temperature': vector['stimulus']})
        action(step, 'call', task='temperature', inputs={}, grants=[])
        add(step, 'temperature', vector['expected'], 'runtime-transcript', units['temperature'], vector['absolute_tolerance'])
    action('effects-initial', 'reset', values={'temperature': all_vectors[-1]['stimulus']})
    requested = next(v['inputs'] for v in truth['contracts']['effect_vectors'] if v['task'] == 'set_duty')
    action('denied', 'call', task='set_duty', inputs=requested, grants=[])
    add('denied', 'refused_without_io', True, 'runtime-transcript', 'boolean', kind='boolean')
    add('denied', 'duty', 0, 'independent-monitor', units['duty'])
    for index, vector in enumerate(truth['contracts']['effect_vectors']):
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
    return {**canonical_task_result.get('outputs', {}),
            'refused_without_io': bool(not canonical_task_result.get('ok') and canonical_task_result.get('error', {}).get('fault') == 'operator' and not canonical_task_result.get('transcript')), 
            'duty': 1000 * values['compare'] / (values['reload'] + 1)}


observations.monitor_units = {'duty': 'permille'}

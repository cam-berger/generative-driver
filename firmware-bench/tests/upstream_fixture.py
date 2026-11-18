"""Unrelated scripted inventory for parser tests; no real upstream answer key."""
import copy
from benchmark_family_fixtures import toy_sample_model


def payload():
    from generative_driver.benchmark_support.missions import load_mission
    from generative_driver.benchmark_support.zephyr_modbus import REQUIRED_MUTANTS
    a = toy_sample_model()
    b = copy.deepcopy(a); b['device']['name'] = 'Second synthetic parser fixture'
    phases = {}
    for phase in ('diagnostic', 'final'):
        key = phase + '/toy/value'
        phases[phase] = {'contract': {'schema': 'benchmark-behavior/1', 'artifact_sha256': '0'*64,
            'checks': [{'id': key, 'revision': 0, 'kind': 'number', 'unit': 'degC',
                        'channel': 'runtime-transcript', 'expected': 5, 'absolute_tolerance': 0}]},
            'actions': [{'kind': 'reset', 'values': {}}, {'kind': 'call', 'task': 'sample',
                         'inputs': {}, 'grants': ['write']}, {'kind': 'observe', 'checks': [key]}]}
    return {'schema': 'benchmark-upstream-truth/1', 'case_id': 'zephyr-modbus-v1', 'family': 'zephyr-modbus',
        'inventory_state': 'pending', 'source': {'purpose': 'synthetic validation fixture'},
        'build': {}, 'images': {'firmware.bin': 'a'*64}, 'recipe': {},
        'references': {name: {'model': model, 'capabilities': {}} for name, model in (('a', a), ('b', b))},
        'phases': phases, 'mutations': {'mutants': [
            {'id': name, 'reference': 'a', 'phase': 'final', 'expected_failed_checks': ['final/toy/value'],
             'patches': [{'target': 'model', 'op': 'add', 'path': '/operations/sample/outputs/temperature/scale',
                          'value': index + 10}]} for index, name in enumerate(sorted(REQUIRED_MUTANTS))]},
        'public_mission': load_mission('zephyr-modbus-v1')}

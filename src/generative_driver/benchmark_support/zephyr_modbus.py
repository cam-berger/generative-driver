"""Zephyr's upstream RTU server: observe memory and GPIO independently of replies."""
from .family_support import integer, private_phase

REQUIRED_MUTANTS = frozenset({'constant-read', 'wrong-byteorder', 'wrong-register',
                              'wrong-coil', 'omit-write', 'wrong-request-crc'})


def contract(pin, truth, phase):
    return private_phase(pin, truth, phase)['contract']


def build_plan(pin, truth, phase):
    return private_phase(pin, truth, phase)['actions']


def observations(raw, canonical_task_result, inputs):
    values = raw['values']
    observed = {}
    for pair in range(4):
        word = integer(values, 'register_pair' + str(pair))
        if not 0 <= word <= 0xffffffff:
            raise ValueError('Invalid independent register observation')
        observed['register' + str(2 * pair)] = word & 0xffff
        observed['register' + str(2 * pair + 1)] = word >> 16
    gpio = integer(values, 'gpio_d')
    observed.update({'coil' + str(i): (gpio >> (12 + i)) & 1 for i in range(3)})
    result = canonical_task_result
    return {**observed, 'value': result.get('outputs', {}).get('value'),
            'operation_ok': result.get('ok') is True,
            # The public contract requires no false success and unchanged device state,
            # followed by a successful valid operation. State and recovery have separate
            # checks. An internal exception-code spelling is not an acceptance criterion.
            'operation_refused': result.get('ok') is False
                and result.get('error', {}).get('fault') in ('model', 'operator')}


observations.monitor_units = {**{'register' + str(i): 'register-value' for i in range(8)},
                              **{'coil' + str(i): 'boolean' for i in range(3)}}
observations.runtime_units = {'operation_refused': 'boolean'}


def reuse_passed(events, capabilities):
    if not events:
        return False
    mapping = capabilities['tasks']['read_register']
    reading = mapping['outputs']['value']['output']
    baseline = events[0]['before_observation']
    untouched = set(observations.monitor_units) - {'register3', 'coil0'}
    saw_read = saw_on = False
    for event in events:
        result, measured = event['result'], event['observation']
        if any(measured.get(key) != baseline.get(key) for key in untouched):
            return False
        if result.get('ok') is not True:
            # Harmless exploratory refusals do not negate a completed mission.
            # Do not accept unavailable device evidence or uncertain effects.
            if (result.get('error', {}).get('fault') not in ('model', 'operator')
                    or measured != event['before_observation']):
                return False
            continue
        if measured.get('register3') == 4660:
            saw_read |= (event.get('operation') == mapping['operation']
                         and result.get('outputs', {}).get(reading) == 4660
                         and result.get('units', {}).get(reading) == 'register-value')
        saw_on |= measured.get('coil0') == 1
    last = events[-1]['observation']
    return saw_read and saw_on and last.get('coil0') == 0 and last.get('register3') == 4660

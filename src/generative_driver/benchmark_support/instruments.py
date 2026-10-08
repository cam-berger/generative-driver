"""Instrument readings of an emulated device: monitor values projected to named quantities with units.

They are device evidence, as a real device's readings would be: workers see them and nothing grades them. A projection
carries no monitor command, address, raw read or response, and needs no truth."""

SCHEMA = 'instrument-observation/1'


def family_for(case_id):
    from . import tq9_v2, sampled_sensor, parameter_store
    families = {'tq9-v2': tq9_v2, 'sampled-sensor-v1': sampled_sensor,
                'parameter-store-v1': parameter_store}
    if case_id not in families:
        raise ValueError('Unknown native family')
    return families[case_id]


def units(case_id):
    """The instrument quantities of a case and their units."""
    if case_id == 'tq9-v2':
        return {'duty': 'permille', 'temperature_reference': 'degC'}
    return dict(family_for(case_id).observations.monitor_units)


def public_observation(case_id, raw):
    """{values, units}: the monitor's raw reading projected to the case's instrument quantities, nothing else."""
    observed, named = family_for(case_id).observations(raw, {}, {}), units(case_id)
    if case_id == 'tq9-v2':
        return {'values': {'duty': observed['duty'], 'temperature_reference': raw['values']['temperature']}, 'units': named}
    return {'values': {key: observed[key] for key in named}, 'units': named}


def observation(case_id, raw, session_id):
    """An `instrument-observation/1`: projected values and units of one device session, and when they were read."""
    return {'schema': SCHEMA, 'session_id': session_id, **public_observation(case_id, raw), 'time': raw.get('time')}


def reading(observed):
    """The reading a worker sees beside a live result: the values with their units, labelled as an instrument's."""
    return {**observed['values'], 'units': dict(observed['units']), 'source': 'instrument'}

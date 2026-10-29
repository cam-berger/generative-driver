"""Instrument readings of an emulated device: monitor values projected to named quantities with units.

They are device evidence, as a real device's readings would be: workers see them and nothing grades them. A projection
carries no monitor command, address, raw read or response, and needs no truth."""

from .registrations import registration_for


SCHEMA = 'instrument-observation/1'
# The legacy tq9 device's instrument: the PWM duty its timer registers give, as the fraction the legacy case reads.
LEGACY = {'tq9': {'duty': 'fraction'}}


def family_for(case_id):
    from .registrations import native_family_for
    return native_family_for(case_id)


def units(case_id):
    """The instrument quantities of a case and their units."""
    if case_id in LEGACY:
        return dict(LEGACY[case_id])
    if registration_for(case_id).family == 'tq9':
        return {'duty': 'permille', 'temperature_reference': 'degC'}
    return dict(family_for(case_id).observations.monitor_units)


def public_observation(case_id, raw):
    """{values, units}: the monitor's raw reading projected to the case's instrument quantities, nothing else."""
    named = units(case_id)
    if case_id == 'tq9':
        values = raw['values']
        return {'values': {'duty': values['compare'] / (values['reload'] + 1)}, 'units': named}
    observed = family_for(case_id).observations(raw, {}, {})
    if registration_for(case_id).family == 'tq9':
        return {'values': {'duty': observed['duty'], 'temperature_reference': raw['values']['temperature']}, 'units': named}
    return {'values': {key: observed[key] for key in named}, 'units': named}


def observation(case_id, raw, session_id):
    """An `instrument-observation/1`: projected values and units of one device session, and when they were read."""
    return {'schema': SCHEMA, 'session_id': session_id, **public_observation(case_id, raw), 'time': raw.get('time')}


def reading(observed):
    """The reading a worker sees beside a live result: the values with their units, labelled as an instrument's."""
    return {**observed['values'], 'units': dict(observed['units']), 'source': 'instrument'}

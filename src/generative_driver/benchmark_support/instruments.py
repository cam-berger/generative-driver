"""Instrument readings of an emulated device: monitor values projected to named quantities with units.

They are device evidence, as a real device's readings would be: workers see them and nothing grades them. A projection
carries no monitor command, address, raw read or response, and needs no truth."""

from . import families


SCHEMA = 'instrument-observation/1'


def units(case_id):
    """The instrument quantities of a case and their units."""
    return families.family_for(case_id).instrument_units()


def public_observation(case_id, raw):
    """{values, units}: the monitor's raw reading projected to the case's instrument quantities, nothing else."""
    return families.family_for(case_id).public_observation(raw)


def observation(case_id, raw, session_id):
    """An `instrument-observation/1`: projected values and units of one device session, and when they were read."""
    return {'schema': SCHEMA, 'session_id': session_id, **public_observation(case_id, raw), 'time': raw.get('time')}


def reading(observed):
    """The reading a worker sees beside a live result: the values with their units, labelled as an instrument's."""
    return {**observed['values'], 'units': dict(observed['units']), 'source': 'instrument'}

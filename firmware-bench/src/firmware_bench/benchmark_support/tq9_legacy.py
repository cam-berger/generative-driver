"""The legacy tq9 case's family: its instrument and its fresh-reuse mission. It is graded from the fresh reuse its run
recorded (`recorded_grading`), so it declares no contract or plan (Feature 01, family-seam)."""

# The public reuse mission requests 37 percent output, then disarm; the instrument reads the duty as a fraction.
_REQUESTED = .37


def _instrument_units():
    return {'duty': 'fraction'}


def _public_observation(raw):
    """The PWM duty the timer registers give, as the fraction the legacy case reads."""
    values = raw['values']
    return {'values': {'duty': values['compare'] / (values['reload'] + 1)}, 'units': _instrument_units()}


def _mission_done(events, capabilities):
    """Whether fresh reuse did the public legacy mission with the frozen package: a temperature read, the requested 37
    percent output and disarm, judged from the package results and the instrument's duty readings after them. A note on
    the reuse stage, never a grade."""
    spec = capabilities['temperature']
    duties = [e['observation']['duty'] for e in events
              if e['result'].get('ok') is True and isinstance(e.get('observation'), dict)]
    read = any(e.get('operation') == spec['operation'] and e['result'].get('ok') is True
               and type(e['result'].get('outputs', {}).get(spec['output'])) in (int, float) for e in events)
    return read and bool(duties) and any(abs(d - _REQUESTED) <= .002 for d in duties) and duties[-1] == 0


HOOKS = {'instrument_units': _instrument_units, 'public_observation': _public_observation,
         'reuse_passed': _mission_done}

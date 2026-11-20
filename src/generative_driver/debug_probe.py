"""The debug probe an operator declares with a run's device.

A run's `binding` may hold `debug_probe`: an ST-LINK probe, through which the acquire worker reads an STM32's firmware
with `acquire_stm32_flash`. The operator owns that connection: the program's command comes from the home's
configuration and the probe from the binding, never from a worker (Feature 01 of the stm32-acquire Run,
docs/features/stm32-acquire/01-stm32-flash-read.md).
"""
import re
from pathlib import Path

from .setup import STM32_PROGRAMMER, program_command

KEY = 'debug_probe'
_PROGRAM_KEY = 'debug_probe_program'
_MALFORMED = 'binding.debug_probe must be an object with kind "stlink" and an optional serial_number'
_BAD_SERIAL = 'binding.debug_probe.serial_number must be 1 to 64 letters and digits'
_NEEDS_PROGRAM = ('binding.debug_probe needs STM32CubeProgrammer: run generative-driver configure-program --program '
                  + STM32_PROGRAMMER + ' --command PATH')
_SUPPLIED = 'binding.debug_probe is for a run without a supplied device'
# The tool the acquire stage of a run that declares a probe is assigned after its other tools, and what its worker
# is told of the probe: that one exists, never which.
TOOL = 'acquire_stm32_flash'
CONTEXT = ('An ST-LINK debug probe is attached to this device. If the device is an STM32 and no firmware file is among '
           'the inputs, read its firmware with ' + TOOL + '. The configurator supplies the program and the probe. '
           'Without the reset grant the tool neither resets nor halts the chip; with it the chip is reset to connect '
           'and again after the read.')
# The arguments of the tool that only the configurator gives, beside the folder and the reset permission it owns for
# every tool, and what a worker that sends one is told.
ARGUMENTS = ('program', 'probe_serial_number')
ARGUMENTS_OWNED = 'The debug probe and its program are configurator-owned'
# The one lease the runs that declare a probe share: a row of the table of binding leases. A binding's own lease is
# a SHA-256 in hex, so this name is never one.
LEASE = 'debug-probe'
TAKEN = 'The debug probe is already owned by another run'


def refusal(binding, supplied, home):
    """Why a start with `binding` is refused for the probe it declares, as the text of the refusal, or None when it
    declares none or may have it. `supplied` says whether the start names a supplied device, which takes no probe;
    that is said before anything else. The program must be configured in `home` and its file must be there."""
    if not isinstance(binding, dict) or KEY not in binding:
        return None
    if supplied:
        return _SUPPLIED
    probe = binding[KEY]
    if not isinstance(probe, dict) or set(probe) - {'kind', 'serial_number'} or probe.get('kind') != 'stlink':
        return _MALFORMED
    if 'serial_number' in probe and not (isinstance(probe['serial_number'], str)
                                         and re.fullmatch('[0-9A-Za-z]{1,64}', probe['serial_number'])):
        return _BAD_SERIAL
    command = program_command(home, STM32_PROGRAMMER)
    if command is None or not Path(command[0]).is_file():
        return _NEEDS_PROGRAM
    return None


def settle(spec, home):
    """Settle the probe in `spec`, the record a start builds from its request: the declaration leaves the binding and
    is kept under `KEY` beside it, so that everything after the start sees the binding without it. The program's
    command, as `home` has it configured now, is copied under `_PROGRAM_KEY`, as the executor's configuration is
    copied: the run launches what it started with. Only a binding declares a probe: a record whose binding declares
    none holds nothing under either key, whatever the request held there."""
    binding = spec.get('binding')
    spec.pop(KEY, None)
    spec.pop(_PROGRAM_KEY, None)
    if isinstance(binding, dict) and KEY in binding:
        spec[KEY] = binding[KEY]
        spec[_PROGRAM_KEY] = program_command(home, STM32_PROGRAMMER)
        spec['binding'] = {key: value for key, value in binding.items() if key != KEY}


def arguments(spec, effects):
    """What the configurator supplies a call of the tool in the run of record `spec`: the program's command the run
    started with, the probe's serial number when the binding named one, and whether the operator granted a reset."""
    supplied = {'program': spec[_PROGRAM_KEY], 'allow_reset': 'reset' in effects}
    if 'serial_number' in spec[KEY]:
        supplied['probe_serial_number'] = spec[KEY]['serial_number']
    return supplied

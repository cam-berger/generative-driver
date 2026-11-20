"""A stand-in for `STM32_Programmer_CLI`, the command-line program of STM32CubeProgrammer.

Feature 01 of the stm32-acquire Run (docs/features/stm32-acquire/01-stm32-flash-read.md). The real program is the
operator's own installation and is on none of the machines the tests run on. A test gives a tool the command
`[sys.executable, <this file>]`, which starts the same way on macOS and Windows.

It reads no chip and no probe. It prints what the program is remembered to print: ST's page for version 2.23.0 shows
the output only as figures. So its text is no evidence of what the program prints, and nothing a test sees through it
is physical evidence.

The environment variable `STM32_STAND_IN_STATE` names a folder the test owns. Each launch appends one JSON line to
`launches.jsonl` there: its arguments, its working folder and its kind. The file `scenario.json` beside it steers the
launches; a key that is absent takes the value of `DEFAULTS`:

- `rdp`: the RDP option byte, as two hex digits. `rdp_line`: whether its line is printed.
- `flash_size`: the text after `Flash size`, or null for no such line. `device_name`: likewise.
- `exit`: the exit code of each kind of launch, `identify`, `read` or `return`.
- `write`: what a read leaves in its file: `image` (the bytes of `image`), `short` (one byte less), `blank` (every
  byte 0xFF) or `nothing` (no file).
- `in_the_way`: whether a read also leaves a folder named `interpret-input.bin` in the folder above its file's,
  which is where the tool is to move the image.
- `outlast`: for a kind of launch, the seconds it waits before it does anything else. While it waits it listens on a
  local port, which the record names, so that a test sees whether the launch was stopped.
- `escapes_inside`: whether escape sequences also stand inside the words a reader looks for.
- `shout`: whether every line is printed in capital letters.
- `line_end`: the end of each line printed.

This module holds no test. A test imports it for `COMMAND`, `steer`, `launches`, `image` and `plain`.
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

COMMAND = [sys.executable, str(Path(__file__).resolve())]
STATE = 'STM32_STAND_IN_STATE'
DEFAULTS = {'rdp': 'AA', 'rdp_line': True, 'flash_size': '64 KBytes',
            'device_name': 'STM32F101/F102/F103 Medium-density', 'exit': {}, 'write': 'image', 'outlast': {},
            'in_the_way': False, 'escapes_inside': False, 'shout': False, 'line_end': '\n'}
# The colours the program is remembered to wrap its lines in, and the byte of its progress bar, which is not UTF-8.
INFO, GOOD, BAD, PLAIN = '\x1b[36m\x1b[01m', '\x1b[00;32m', '\x1b[00;31m', '\x1b[39;49m\x1b[0m'
BAR = b'\xdb' * 20
REPLACED = '�' * 20


def steer(folder, **scenario):
    """Write the scenario of the launches that record in `folder`."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / 'scenario.json').write_text(json.dumps(scenario), encoding='utf-8')


def scenario_of(folder):
    path = Path(folder) / 'scenario.json'
    given = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    return {**DEFAULTS, **given}


def launches(folder):
    """The launches recorded in `folder`, in order, each as its record."""
    path = Path(folder) / 'launches.jsonl'
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def image(size):
    """The bytes a read of `size` bytes leaves: no two neighbours equal, so the image is never blank."""
    return bytes((index * 7 + index // 251) % 256 for index in range(size))


def kind_of(arguments):
    """Which launch `arguments` ask for: `identify`, `read`, `return`, or None for anything else."""
    if '--upload' in arguments:
        return 'read'
    if '-ob' in arguments:
        return 'identify'
    return 'return' if '-rst' in arguments else None


def _lines(arguments, scenario):
    """What a launch prints on its standard output, as pairs of a colour (or None) and a line. A line that is bytes
    is the progress bar."""
    kind = kind_of(arguments)
    code = scenario['exit'].get(kind, 0)
    serial = next((value[3:] for value in arguments if value.startswith('sn=')), '066DFF485550755187121723')
    mode = 'Hot Plug' if 'mode=HOTPLUG' in arguments else 'Normal'
    lines = [(None, '      -------------------------------------------------------------------'),
             (None, '                       STM32CubeProgrammer v2.23.0'),
             (None, '      -------------------------------------------------------------------'), (None, '')]
    if kind == 'identify' and code:
        return lines + [(BAD, 'Error: No STM32 target found!')]
    lines += [(INFO, 'ST-LINK SN  : ' + serial), (INFO, 'ST-LINK FW  : V2J45M31'), (INFO, 'Voltage     : 3.26V'),
              (INFO, 'SWD freq    : 4000 KHz'), (INFO, 'Connect mode: ' + mode),
              (INFO, 'Reset mode  : Software reset'), (INFO, 'Device ID   : 0x410')]
    if scenario['device_name'] is not None:
        lines.append((INFO, 'Device name : ' + scenario['device_name']))
    if scenario['flash_size'] is not None:
        lines.append((INFO, 'Flash size  : ' + scenario['flash_size']))
    lines += [(INFO, 'Device type : MCU'), (INFO, 'Device CPU  : Cortex-M3'), (None, '')]
    if kind == 'identify':
        lines += [(None, 'UPLOADING OPTION BYTES DATA ...'), (None, ''), (None, '  Bank          : 0x00'),
                  (None, '  Address       : 0x1ffff800'), (None, '  Size          : 16 Bytes'), (None, ''),
                  (None, BAR), (None, ''), (None, 'OPTION BYTES BANK: 0'), (None, ''),
                  (None, '   Read Out Protection:'), (None, '')]
        if scenario['rdp_line']:
            level = '(Level 0, no protection)' if scenario['rdp'].upper() == 'AA' else '(Level 1, read protection)'
            lines.append((None, '     RDP          : 0x' + scenario['rdp'] + ' ' + level))
        lines += [(None, ''), (None, '   User Configuration:'), (None, ''),
                  (None, '     nRST_STOP    : 0x1 (No reset generated when entering Stop mode)')]
    elif kind == 'read':
        at = arguments.index('--upload')
        lines += [(None, 'UPLOADING ...'), (None, '  Size          : ' + arguments[at + 2] + ' Bytes'),
                  (None, '  Address       : ' + arguments[at + 1]), (None, 'Read progress:'), (None, BAR)]
        lines.append((BAD, 'Error: Uploading failed') if code else (GOOD, 'Data read successfully'))
    if '-rst' in arguments and not code:
        lines += [(None, ''), (None, 'MCU Reset'), (None, ''), (GOOD, 'Software reset is performed')]
    if scenario['shout']:
        lines = [(colour, line if isinstance(line, bytes) else line.upper()) for colour, line in lines]
    return lines


def _error_line(arguments):
    """The one line every launch writes on its standard error."""
    return 'stand-in: ' + str(kind_of(arguments)) + ' launch'


def _inside(line):
    """`line` with an escape sequence inside each word a reader looks for."""
    for word, split in (('RDP', 'R\x1b[1mDP'), ('Flash size', 'Flash\x1b[22m size'),
                        ('Device name', 'Device\x1b[0m name'), ('KBytes', 'KB\x1b[4mytes'), ('0x', '0\x1b[24mx')):
        line = line.replace(word, split)
    return line


def printed(arguments, scenario):
    """The bytes a launch writes: its standard output and its standard error."""
    end = scenario['line_end'].encode('utf-8')
    out = b''
    for colour, line in _lines(arguments, scenario):
        if isinstance(line, bytes):
            out += line + b' 100%' + end
            continue
        if scenario['escapes_inside']:
            line = _inside(line)
        out += ((colour + line + PLAIN) if colour else line).encode('utf-8') + end
    return out, (BAD + _error_line(arguments) + PLAIN).encode('utf-8') + end


def plain(arguments, **scenario):
    """The text a launch with `arguments` leaves once its output is decoded as UTF-8 with replacement and its escape
    sequences are removed: its standard output, then its standard error. It is built from the lines themselves,
    never by removing a sequence."""
    scenario = {**DEFAULTS, **scenario}
    end = scenario['line_end']
    lines = [REPLACED + ' 100%' if isinstance(line, bytes) else line for _, line in _lines(arguments, scenario)]
    return ''.join(line + end for line in [*lines, _error_line(arguments)])


def main():
    arguments = sys.argv[1:]
    folder = os.environ.get(STATE)
    if not folder:
        sys.stderr.write('stand-in: ' + STATE + ' names no folder\n')
        return 97
    scenario, kind = scenario_of(folder), kind_of(arguments)
    record = {'arguments': arguments, 'cwd': os.getcwd(), 'kind': kind}
    wait = scenario['outlast'].get(kind)
    listener = None
    if wait:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        record['listening'] = listener.getsockname()[1]
    with open(Path(folder) / 'launches.jsonl', 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
    if wait:
        time.sleep(wait)
        listener.close()
    if kind is None:
        sys.stderr.write('stand-in: no launch it knows: ' + ' '.join(arguments) + '\n')
        return 2
    out, error = printed(arguments, scenario)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(error)
    sys.stderr.buffer.flush()
    code = scenario['exit'].get(kind, 0)
    if kind == 'read' and scenario['write'] != 'nothing':
        at = arguments.index('--upload')
        size = int(arguments[at + 2])
        data = {'image': image(size), 'short': image(size)[:-1], 'blank': b'\xff' * size}[scenario['write']]
        Path(arguments[at + 3]).write_bytes(data)
    if kind == 'read' and scenario['in_the_way']:
        (Path(arguments[arguments.index('--upload') + 3]).parent.parent / 'interpret-input.bin').mkdir()
    return code


if __name__ == '__main__':
    raise SystemExit(main())

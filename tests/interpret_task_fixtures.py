"""Fixtures for the kit analysis and the interpret tasks.

- A stand-in analyzer: a `ghidra_home` whose headless launcher writes a canned export, records each launch and can
  fail or hang, with a `java_home` the helper's checks accept. No Ghidra or Java runs.
- Canned firmware images: a raw Cortex-M image (with or without a header in front of its vector table), a raw image
  with no vector table and small ELF images.
- A scripted worker the configurator launches in place of Codex, which recognises each interpret task from the
  instructions in its workspace and does it from a script the test writes.
- In-process runs whose acquire stage hands on one image and whose device stages are skipped or scripted.

Temporary homes only; no model, no device."""
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
TIMEOUT = 120


# The canned export: a firmware whose dispatcher switches on the first byte of a text command, ID? or T.
FUNCTIONS = [
    ('08000100', 'Reset_Handler', 'void Reset_Handler(void)\n{\n  main();\n}\n'),
    ('08000140', 'main', 'void main(void)\n{\n  uart_init();\n  while (true) { dispatch(); }\n}\n'),
    ('08000200', 'dispatch', 'void dispatch(char *line)\n{\n  switch (line[0]) {\n'
                             '  case \'I\': handle_identify(); break;\n'
                             '  case \'T\': handle_measure(); break;\n  }\n}\n'),
    ('08000300', 'handle_identify', 'void handle_identify(void)\n{\n  uart_puts("DEMO-42");\n}\n'),
    ('08000340', 'handle_measure', 'void handle_measure(void)\n{\n  int t = read_sensor();\n'
                                   '  uart_printf("T:%d.%d", t / 10, t % 10);\n}\n'),
    ('08000400', 'uart_puts', 'void uart_puts(char *s)\n{\n  while (*s) uart_putc(*s++);\n  uart_putc(10);\n}\n'),
    ('08000440', 'uart_putc', 'void uart_putc(char c)\n{\n  UART->DR = c;\n}\n'),
    ('08000480', 'read_sensor', 'int read_sensor(void)\n{\n  return ADC->DR * 5 / 16;\n}\n'),
    ('080004c0', 'uart_init', 'void uart_init(void)\n{\n  UART->BRR = 0x45;\n}\n'),
    ('08000500', 'menu', 'void menu(int key)\n{\n  switch (key) {\n  case 0: uart_init(); break;\n'
                         '  case 1: read_sensor(); break;\n  }\n}\n'),
]
CALLS = [('08000100', '08000140'), ('08000140', '080004c0'), ('08000140', '08000200'), ('08000200', '08000300'),
         ('08000200', '08000340'), ('08000300', '08000400'), ('08000340', '08000480'), ('08000340', '08000400'),
         ('08000400', '08000440'), ('08000500', '080004c0'), ('08000500', '08000480')]
STRINGS = [('08000600', 'ID?', '08000200', ''), ('08000604', 'T', '08000200', ''),
           ('08000608', 'DEMO-42', '08000300', ''), ('08000610', 'T:%d.%d', '08000340', ''),
           ('08000618', 'Temperature sensor firmware v1', '', '08000020')]
SWITCHES = [('08000200', '08000210', '0x49', '08000220'), ('08000200', '08000210', '0x54', '08000240'),
            ('08000500', '08000510', '0x0', '08000520'), ('08000500', '08000510', '0x1', '08000530')]


def export_files(functions=FUNCTIONS, calls=CALLS, strings=STRINGS, switches=SWITCHES):
    """{file name: text} of an export as ExportDecomp.java writes it, with its navigation manifest."""
    files = {'functions.tsv': 'address\tname\tdecompilation_complete\n'
             + ''.join(f'{address}\t{name}\ttrue\n' for address, name, _ in functions)}
    for address, _, code in functions:
        files[f'function_{address}.c'] = f'// entry address: {address}\n{code}'
    files['disassembly.txt'] = ''.join(f'{address}\tpush {{r4, lr}}\n' for address, _, _ in functions)
    tables = {
        'references.tsv': ('from_address\tfrom_function\tto_address\tto_function\ttype',
                           [f'{caller[:-1]}8\t{caller}\t{callee}\t{callee}\tUNCONDITIONAL_CALL'
                            for caller, callee in calls]),
        'strings.tsv': ('address\tencoding\tlength\tclipped\tfunctions\trefs_outside_functions\tvalue',
                        [f'{address}\tUS-ASCII\t{len(value)}\tfalse\t{users}\t{outside}\t{value}'
                         for address, value, users, outside in strings]),
        'switches.tsv': ('function\tswitch_address\tcase_value\ttarget', ['\t'.join(row) for row in switches])}
    manifest = {'schema': 'ghidra-navigation/1', 'max_bytes': 16 << 20, 'note': 'canned', 'tables': {}}
    for name, (header, rows) in tables.items():
        files[name] = header + '\n' + ''.join(row + '\n' for row in rows)
        manifest['tables'][name] = {'rows': len(rows), 'rows_found': len(rows), 'truncated': False}
    files['navigation.json'] = json.dumps(manifest, indent=1) + '\n'
    return files


def many_functions(count):
    """A canned export with `count` small functions, each calling the next and referencing a string of its own."""
    functions = [(f'{0x08001000 + 16 * n:08x}', f'FUN_{n}', f'void FUN_{n}(void)\n{{\n  step({n});\n}}\n')
                 for n in range(count)]
    calls = [(functions[n][0], functions[n + 1][0]) for n in range(count - 1)]
    strings = [(f'{0x08100000 + 16 * n:08x}', f'message number {n} of the canned export', functions[n][0], '')
               for n in range(count)]
    return export_files(functions, calls, strings, [])


# The stand-in launcher. It records its arguments, then does what the home's mode.json says: write the canned export
# and report an import that succeeded, fail, or hang after recording its process id.
STAND_IN = r'''
import json, os, sys, time
from pathlib import Path
home = Path(__file__).resolve().parents[1]
argv = sys.argv[1:]
mode = json.loads((home / 'mode.json').read_text(encoding='utf-8'))['mode']
with open(home / 'launches.jsonl', 'a', encoding='utf-8') as log:
    log.write(json.dumps({'argv': argv, 'pid': os.getpid(), 'mode': mode}) + '\n')
if mode == 'hang':
    (home / 'hanging.pid').write_text(str(os.getpid()), encoding='utf-8')
    time.sleep(600)
if mode == 'fail':
    print('ERROR REPORT: Import failed for the canned image (ProgramLoader)')
    sys.exit(1)
export = Path(argv[argv.index('-postScript') + 2])
export.mkdir(parents=True, exist_ok=True)
for name, text in json.loads((home / 'export.json').read_text(encoding='utf-8')).items():
    (export / name).write_text(text, encoding='utf-8', newline='\n')
print('INFO  Import succeeded (HeadlessAnalyzer)')
print('INFO  Analysis succeeded (HeadlessAnalyzer)')
'''


def stand_in_ghidra(root, mode='ok', export=None):
    """A stand-in `ghidra_home` and `java_home` under `root`, as absolute strings."""
    ghidra, java = Path(root) / 'stand-in-ghidra', Path(root) / 'stand-in-jdk'
    (ghidra / 'support').mkdir(parents=True)
    (java / 'bin').mkdir(parents=True)
    script = ghidra / 'support' / 'stand_in.py'
    script.write_text(STAND_IN, encoding='utf-8')
    if os.name == 'nt':
        (ghidra / 'support' / 'analyzeHeadless.bat').write_text(
            f'@"{sys.executable}" "{script}" %*\r\n@exit /b %errorlevel%\r\n', encoding='utf-8')
        (java / 'bin' / 'java.exe').write_bytes(b'')
    else:
        launcher = ghidra / 'support' / 'analyzeHeadless'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
        (java / 'bin' / 'java').write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        (java / 'bin' / 'java').chmod(0o755)
    set_mode(str(ghidra), mode)
    set_export(str(ghidra), export or export_files())
    return str(ghidra.resolve()), str(java.resolve())


def set_mode(ghidra, mode):
    (Path(ghidra) / 'mode.json').write_text(json.dumps({'mode': mode}), encoding='utf-8')


def set_export(ghidra, files):
    (Path(ghidra) / 'export.json').write_text(json.dumps(files), encoding='utf-8')


def launches(ghidra):
    """Each launch of the stand-in analyzer: its arguments, process id and mode."""
    path = Path(ghidra) / 'launches.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


def option(argv, flag):
    """The value after `flag` in a launch's arguments, or None."""
    return argv[argv.index(flag) + 1] if flag in argv else None


def alive(pid):
    from generative_driver.client import _wait_for_exit
    return not _wait_for_exit(pid, 0)


def gone(pid, seconds=10):
    """Whether process `pid` has ended within `seconds`."""
    until = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() > until:
            return False
        time.sleep(.05)
    return True


def cortex_m_image(header=0):
    """A raw little-endian Cortex-M image whose vector table starts `header` bytes in and whose absolute literal
    pointers resolve to its strings only at load base 0x08000000 for the byte after the header."""
    body = bytearray(0x800)
    struct.pack_into('<8I', body, 0, 0x20001000, 0x08000101, 0x08000181, 0x08000181, 0, 0, 0, 0)
    body[0x100:0x140] = b'\x00\xbf' * 32
    pointers, offset = [], 0x400
    for text in (b'DEMO-42 firmware\x00', b'temperature reading\x00', b'command not known\x00'):
        body[offset:offset + len(text)] = text
        pointers.append(0x08000000 + offset)
        offset += (len(text) + 3) // 4 * 4
    struct.pack_into('<%dI' % len(pointers), body, 0x200, *pointers)
    prefix = (b'HDR\x00' + bytes(header))[:header] if header else b''
    return prefix + bytes(body)


def unmapped_image():
    """A raw image with no Cortex-M vector table anywhere in it."""
    return b'\x00\x01\x02\x03fixture firmware bytes ' * 16


def elf_image(machine, bits=32, little=True, vaddr=0x08000000):
    """A minimal executable ELF for `machine` with one loadable segment at `vaddr`."""
    order = '<' if little else '>'
    ident = b'\x7fELF' + bytes([1 if bits == 32 else 2, 1 if little else 2, 1]) + bytes(9)
    code = b'\x00\xbf' * 16
    if bits == 32:
        start = 52 + 32
        header = struct.pack(order + '16sHHIIIIIHHHHHH', ident, 2, machine, 1, vaddr + 1, 52, 0, 0, 52, 32, 1, 0, 0, 0)
        segment = struct.pack(order + 'IIIIIIII', 1, start, vaddr, vaddr, len(code), len(code), 5, 4)
    else:
        start = 64 + 56
        header = struct.pack(order + '16sHHIQQQIHHHHHH', ident, 2, machine, 1, vaddr, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        segment = struct.pack(order + 'IIQQQQQQ', 1, 5, start, vaddr, vaddr, len(code), len(code), 4)
    return header + segment + code


# The scripted worker. Its first argument is the script the test wrote; the Codex launch's arguments follow.
WORKER = r'''
import hashlib, json, os, sys, time
from pathlib import Path
script_path = Path(sys.argv[1])
script = json.loads(script_path.read_text(encoding='utf-8'))
arguments = sys.argv[2:]
workspace = Path(arguments[arguments.index('-C') + 1])
prompt = sys.stdin.buffer.read().decode('utf-8')
folder = script_path.parent

def read(name):
    path = workspace / name
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None

def files():
    return sorted(p.relative_to(workspace).as_posix() for p in workspace.rglob('*')
                  if p.is_file() and '_toolkit' not in p.parts and not p.name.startswith('_agent'))

def sha(name):
    path = workspace / name
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def sizes():
    return {name: (workspace / name).stat().st_size for name in ('DISPATCHERS.c', 'strings.tsv', 'CODE.c',
            'HANDLERS.c', 'STRINGS.json') if (workspace / name).is_file()}

def write(name, value):
    (workspace / name).write_text(value if isinstance(value, str) else json.dumps(value, indent=1), encoding='utf-8')

def seen(task, command=None, **more):
    record = {'task': task, 'command': command, 'workspace': str(workspace), 'files': files(), 'sizes': sizes(),
              'prompt': prompt, 'pid': os.getpid(), **more}
    for name in ('TASKS.json', 'TASK.json', 'COMMAND.json', 'IDENTITY.json', 'MODEL_FRAME.json', 'VALIDATION.json',
                 'DIFFERENCES.json', 'PREVIOUS_FRAGMENT.json', 'PREVIOUS_OPERATION.json', 'inventory.json',
                 'model.json', 'STRINGS.json'):
        if (workspace / name).is_file():
            record[name] = read(name)
    for name in ('DISPATCHERS.c', 'CODE.c', 'HANDLERS.c'):
        if (workspace / name).is_file():
            record[name] = (workspace / name).read_text(encoding='utf-8')
    record['index_sha256'] = sha('ANALYSIS_INDEX.json')
    with open(folder / 'seen.jsonl', 'a', encoding='utf-8') as log:
        log.write(json.dumps(record) + '\n')

def behave(key):
    if script.get('hang') == key:
        (folder / ('waiting-' + key.replace(':', '-'))).write_text(str(os.getpid()), encoding='utf-8')
        while not (folder / 'release').exists():
            time.sleep(.02)
    if script.get('exit_error') == key:
        sys.stderr.write('scripted failure in ' + key + '\n')
        raise SystemExit(1)
    if script.get('tamper') == key:
        write('TASKS.json', {'changed': 'by the worker'})
    if script.get('usage'):
        print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 0,
                                                              'output_tokens': 10, 'reasoning_output_tokens': 0}}))

if (workspace / 'INVENTORY.md').is_file():
    seen('inventory')
    behave('inventory')
    inventory = script.get('inventory')
    write('inventory.json', 'this is not JSON' if inventory == 'unreadable' else inventory)
    write('NOTES.md', 'Inventory notes: the dispatcher at 08000200 switches on the first byte.\n')
elif (workspace / 'HANDLER.md').is_file():
    command = (read('COMMAND.json') or {}).get('name')
    repair = (workspace / 'PREVIOUS_FRAGMENT.json').is_file()
    seen('handler', command)
    behave(('repair-handler:' if repair else 'handler:') + str(command))
    fragments = script.get('repair_fragments') if repair else None
    fragment = (fragments or script.get('fragments') or {}).get(command)
    if fragment == 'unusable':
        write('fragment.json', '{"not": "a fragment"')
    elif fragment is not None:
        write('fragment.json', fragment)
    write('NOTES.md', 'Handler notes for ' + str(command) + '.\n')
elif (workspace / 'CROSS_CHECK.md').is_file():
    seen('cross-check')
    behave('cross-check')
    action = script.get('cross_check', 'keep')
    if action == 'edit':
        model = read('model.json')
        model['device']['name'] = 'DEMO-42 (cross-checked)'
        write('model.json', model)
    elif action == 'break':
        write('model.json', 'not a model')
    write('NOTES.md', 'Cross-check notes.\n')
elif (workspace / 'BRIEF.md').is_file():
    seen('single')
    behave('single')
    if script.get('single'):
        write('model.json', script['single'])
else:
    task = json.loads(prompt.partition('\n')[2])
    seen(task['stage'])
    behave(task['stage'])
    report = {'status': 'completed', 'summary': 'Scripted ' + task['stage'] + '; no model ran', 'artifacts': [],
              'checks': [], 'unresolved': []}
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}}))
'''


def scripted_worker(folder, **script):
    """The command of a scripted worker that follows `script`, keeping its records in `folder`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'worker.py').write_text(WORKER, encoding='utf-8')
    (folder / 'script.json').write_text(json.dumps(script), encoding='utf-8')
    return [sys.executable, str(folder / 'worker.py'), str(folder / 'script.json')]


def seen(folder, task=None):
    """What the scripted worker saw in each workspace it ran in, in order; only `task`'s when given."""
    path = Path(folder) / 'seen.jsonl'
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []
    return [record for record in records if task is None or record['task'] == task]


def until(read, seconds=TIMEOUT, message='The run did not get there in time'):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError(message)


def events(controller, run, *kinds):
    """The run's events of `kinds` (every event when none is given), each as {kind, data, id}."""
    found, cursor = [], 0
    while True:
        page = controller.call('events', {**run, 'after': cursor})
        found += page['events']
        if not page['events']:
            break
        cursor = page['cursor']
    return [event for event in found if not kinds or event['kind'] in kinds]


def stopped(controller, run, seconds=TIMEOUT):
    """The run's result once it has stopped and nothing of it is left stopping."""
    def done():
        status = controller.call('status', run)
        return status['status'] not in ('queued', 'running') and not status['stopping'] and status
    until(done, seconds, 'The run did not stop in time: ' + json.dumps(controller.call('status', run)))
    return controller.call('result', run)


def interpret_notes(result, revision=None):
    """The inline notes of the latest interpret handoff (of `revision` when given)."""
    handoffs = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret'
                and (revision is None or h['revision'] == revision)]
    return handoffs[-1]['notes']['inline']


def scripted_stages(controller, image, name='firmware.bin', diffs=None, evidence=None):
    """Patch `controller` so that acquire hands on `image` (bytes, saved as `name`), with each document of `evidence`
    ({file name: JSON value}), and the device stages are skipped, except probe when `diffs(revision)` returns a device
    difference for that revision: probe then runs its worker and hands that difference back to interpretation.
    Interpretation is prepared, run and checked for real."""
    prepare, check = _scripts(controller._prepare, controller._check, image, name, diffs, evidence)
    return [patch.object(controller, '_prepare', side_effect=prepare),
            patch.object(controller, '_check', side_effect=check)]


def scripted_service_stages(image, name='firmware.bin', diffs=None, evidence=None):
    """The patches of `scripted_stages` made to the controller class, so that the controller of a service run in a
    thread of the test (`service_fixtures.serve`) has them too."""
    from generative_driver.configurator import Controller
    original_prepare, original_check = Controller._prepare, Controller._check

    def prepare(self, *arguments):
        return _scripts(lambda *a: original_prepare(self, *a), None, image, name, diffs, evidence)[0](*arguments)

    def check(self, *arguments):
        return _scripts(None, lambda *a: original_check(self, *a), image, name, diffs, evidence)[1](*arguments)

    return [patch.object(Controller, '_prepare', prepare), patch.object(Controller, '_check', check)]


def _scripts(original_prepare, original_check, image, name, diffs, evidence):
    def prepare(spec, stage, run_dir, workspace, accepted):
        if stage == 'interpret':
            return original_prepare(spec, stage, run_dir, workspace, accepted)
        if stage == 'acquire' or (stage == 'probe' and diffs and diffs(spec.get('_revision', 0))):
            return {'inputs': [], 'allowed_tools': []}
        return {'skip': 'Not part of this test'}

    def check(spec, stage, run_dir, workspace, report, accepted, assignment):
        if stage == 'interpret':
            return original_check(spec, stage, run_dir, workspace, report, accepted, assignment)
        if stage == 'acquire':
            path = Path(workspace) / name
            path.write_bytes(image)
            for document, value in (evidence or {}).items():
                (Path(workspace) / document).write_text(json.dumps(value), encoding='utf-8')
            return {'ok': True, 'artifacts': [str(path), *(str(Path(workspace) / d) for d in evidence or {})],
                    'checks': []}
        return {'ok': True, 'artifacts': [], 'checks': [], 'device_diff': diffs(assignment['revision'])}

    return prepare, check


class RunCase(unittest.TestCase):
    """In-process configurator runs in a temporary folder, closed when the test ends."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def controller(self, home='home', image=None, name='firmware.bin', diffs=None, evidence=None):
        from generative_driver.configurator import Controller
        controller = Controller(self.root / home)
        for scripted in scripted_stages(controller, image, name, diffs, evidence):
            scripted.start()
            self.addCleanup(scripted.stop)
        # Closed before its scripted stages are taken away.
        self.addCleanup(controller.close)
        return controller

    def start(self, image, *, name='firmware.bin', mode='ok', named=True, root=None, home='home', diffs=None,
              worker=None, script=None, export=None, evidence=None, **params):
        """A run whose acquisition hands on `image` (and `evidence`), with the stand-in analyzer named unless `named`
        is false, and the scripted worker following `script` (by default it writes SINGLE_MODEL as the single
        interpret worker). Returns the controller, the run, the stand-in's ghidra_home and the worker's folder."""
        root = Path(root or self.root)
        ghidra, java = stand_in_ghidra(root, mode, export) if named else (None, None)
        folder = root / 'worker'
        command = worker or scripted_worker(folder, **(script or {'single': SINGLE_MODEL}))
        controller = self.controller(home, image, name, diffs, evidence)
        run = controller.call('start', {'goal': 'Recover the interface of the canned firmware',
                                        'executor_config': {'command': command},
                                        **({'ghidra_home': ghidra, 'java_home': java} if named else {}), **params})
        return controller, run, ghidra, folder

    @staticmethod
    def run_dir(controller, run):
        return controller.home / 'runs' / run['run_id']

    def finished(self, controller, run):
        """The data of the run's kit_analysis.finished events."""
        return [event['data'] for event in events(controller, run, 'kit_analysis.finished')]


# Scripted interpret task outputs for the canned device: an identity operation and a temperature reading.
def _exchange(text, capture, **extra):
    return {'op': 'exchange', 'tx': [{'text': text}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128},
            'timeout_ms': 2000, 'capture': capture, **extra}


IDENTIFY = {'effect': 'read', 'parameters': {}, 'outputs': {},
            'steps': [_exchange('ID?\n', 'id', expect={'equals_hex': '44454d4f2d34320a'})]}
MEASURE = {'effect': 'read', 'parameters': {},
           'steps': [_exchange('T\n', 'reply')],
           'outputs': {'temperature': {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T:'}}}
STATUS = {'effect': 'read', 'parameters': {}, 'steps': [_exchange('S\n', 'status')],
          'outputs': {'status': {'from': 'status', 'kind': 'utf8', 'strip': True}}}


def fragment(name, operation, reply, source=None):
    outputs = {key: {'schema': {'type': 'number' if decoder['kind'] == 'ascii_number' else 'string'},
                     'description': 'The ' + key + ' the device reports.', 'unit': None,
                     'uncertainties': [{'code': 'unit_not_stated', 'message': 'The reply states no unit.',
                                        'evidence_refs': ['handler code']}]}
               for key, decoder in operation['outputs'].items()}
    return {'schema': 'interpret-fragment/1', 'operation': name, 'model': operation,
            'provenance': [{'item': 'operations.' + name, 'source': source or 'handler code of ' + name}],
            'replies': {'steps': [reply]},
            'contract': {'effect': operation['effect'], 'description': 'Operation ' + name + ' of the canned device.',
                         'uncertainties': [], 'parameters': {}, 'outputs': outputs}}


INVENTORY = {'schema': 'interface-model/3', 'device': {'name': 'DEMO-42'}, 'channel': {'type': 'tcp'},
             'framing': 'text lines ending in a newline', 'identity': 'identify',
             'commands': [{'name': 'measure', 'handlers': ['08000340'],
                           'evidence': ['dispatch case 0x54 at 08000240']},
                          {'name': 'identify', 'handlers': ['08000300'],
                           'evidence': ['dispatch case 0x49 at 08000220']}]}
FRAGMENTS = {'identify': fragment('identify', IDENTIFY, ['DEMO-42']),
             'measure': fragment('measure', MEASURE, ['T:21.5'])}
SINGLE_MODEL = {'schema': 'interface-model/3', 'device': {'name': 'DEMO-42'}, 'channel': {'type': 'tcp'},
                'identity': {'operation': 'identify'}, 'operations': {'identify': IDENTIFY},
                'provenance': [{'item': 'operations.identify', 'source': 'single worker'}]}

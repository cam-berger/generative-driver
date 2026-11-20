"""The kit analysis: the firmware analysis the configurator makes once per run, before interpretation.

An interpret worker that runs Ghidra itself spends its context on what the analyzer prints, and a repair's new worker
runs it all again. The configurator therefore decides how to load the acquired image, runs the existing Ghidra helper
and exporter (`ghidra_run`) in a child process it can stop, and keeps the export, the helper's log and the decision in
the run's folder for every interpret worker of the run. What it cannot do it says, and interpretation goes on as before
with the analysis kit supplied for the worker to run."""
import hashlib
import importlib.util
import json
import shutil
import struct
import tempfile
import time
from pathlib import Path

from . import analysis_index, tool_process
from .integrity import IntegrityError, digest

# The helper's own limit (`ghidra_run.run`'s default), passed to it so that a test may shorten it. The child process is
# stopped GRACE_SECONDS after it, should the helper not stop itself.
HELPER_TIMEOUT_SECONDS = 1800
GRACE_SECONDS = 60
# The run's folder holds one folder per analysis under this name.
FOLDER = 'kit-analysis'
# The index (06-B3), kept beside the export it was made from.
INDEX = 'ANALYSIS_INDEX.json'
# Ghidra languages for the ELF machine types the kit maps, by machine, class and byte order.
ELF_LANGUAGES = {(40, 32, '<'): 'ARM:LE:32:v8', (40, 32, '>'): 'ARM:BE:32:v8',
                 (183, 64, '<'): 'AARCH64:LE:64:v8A', (183, 64, '>'): 'AARCH64:BE:64:v8A',
                 (94, 32, '<'): 'Xtensa:LE:32:default', (94, 32, '>'): 'Xtensa:BE:32:default'}
CORTEX_M = 'ARM:LE:32:Cortex'
_SCRIPTS = ('tools/workspace/ghidra_run.py', 'toolchain/skills/interpret-firmware-binary/ExportDecomp.java',
            'toolchain/skills/interpret-firmware-binary/SeedCortexM.java')


def prepare(run_dir, image, options, *, stop, event, context):
    """The run's kit analysis of `image`, made now unless the run already made it: {made: True, folder, decision,
    export} with `folder` relative to `run_dir`, or {made: False, cause, reason, reported} saying why not.

    `options` names `ghidra_home` and `java_home`. `stop()` says why the helper must stop now, as a call's
    `tool_process.Limits.stop` does; a cancel, a shutdown or a spent budget raises `tool_process.Unfinished` and leaves
    nothing kept, so a resume makes the analysis again. `event(kind, payload)` records `kit_analysis.started` and
    `kit_analysis.finished`; `context` is the tool process's (`log`, `run_id`, `stage`)."""
    run_dir = Path(run_dir)
    if not (options.get('ghidra_home') and options.get('java_home')):
        return _not_made('not_named', 'Ghidra or Java is not named for this run')
    data = Path(image).read_bytes()
    decision, why = _decide(data)
    if decision is None:
        return _not_made('undecided', 'The loading of the image cannot be decided: ' + why)
    ghidra, java = (str(Path(options[key]).expanduser().resolve()) for key in ('ghidra_home', 'java_home'))
    key = hashlib.sha256(json.dumps({'image': hashlib.sha256(data).hexdigest(), 'ghidra_home': ghidra,
                                     'java_home': java, 'scripts': _scripts(), 'decision': decision},
                                    sort_keys=True).encode()).hexdigest()
    folder = run_dir / FOLDER / key[:16]
    relative = folder.relative_to(run_dir).as_posix()
    kept = _kept(folder, relative)
    if kept is not None:
        return kept
    shutil.rmtree(folder, ignore_errors=True)
    event('kit_analysis.started', {'folder': relative, 'decision': decision})
    started = time.monotonic()
    try:
        outcome = _make(folder, data, decision, ghidra, java, stop, context)
    except tool_process.Unfinished as unfinished:
        if unfinished.reason in ('cancelled', 'shutdown', 'budget'):
            shutil.rmtree(folder, ignore_errors=True)
            event('kit_analysis.finished', {'folder': relative, 'stopped': unfinished.reason,
                                            'seconds': round(time.monotonic() - started, 3)})
            raise
        outcome = _not_made('timeout' if unfinished.reason == 'timeout' else 'helper_failed', str(unfinished))
    (folder / 'outcome.json').write_text(json.dumps(outcome, indent=1, sort_keys=True), encoding='utf-8')
    event('kit_analysis.finished', {'folder': relative, 'seconds': round(time.monotonic() - started, 3),
                                    **{k: outcome[k] for k in ('made', 'cause', 'reason') if k in outcome}})
    return _kept(folder, relative)


def note(analysis):
    """What the interpret stage's notes say of `analysis`: how the image was loaded when it was made, else which cause
    applied and what the helper reported (06-B2)."""
    if not analysis['made']:
        return {key: analysis[key] for key in ('made', 'cause', 'reason', 'reported')}
    return {'made': True, 'folder': analysis['folder'], 'export_sha256': analysis['export_sha256'],
            'loading': {key: analysis['decision'][key] for key in ('processor', 'loader', 'load_address', 'why')}}


def sealed_inputs(analysis):
    """The single interpret worker's inputs of a made analysis (06-B4), by workspace name: the index and the export."""
    if not analysis['made']:
        return {}
    return {INDEX: analysis['index'], 'analysis': analysis['export']}


def task_entry(analysis):
    """TASKS.json's `kit_analysis` entry for a made analysis (06-B4), else None: that it is made, how the image was
    loaded, and when to rerun the analysis kit."""
    if not analysis['made']:
        return None
    return {'made': True, 'index': INDEX, 'export': 'analysis/',
            'loading': {key: analysis['decision'][key] for key in ('processor', 'loader', 'load_address', 'why')},
            'instructions': 'The configurator made the firmware analysis of image.bin before this stage, with the '
                'loading below. ' + INDEX + ' lists every function with its size, callers, callees, strings and '
                'switch tables, and ranks the likely command dispatchers with the evidence for each; analysis/ holds '
                'the export it was made from (function_<address>.c, functions.tsv, references.tsv, strings.tsv, '
                'switches.tsv, disassembly.txt). Read the index first and open only the functions it points to. '
                'Run the supplied analysis kit (ANALYSIS_TOOLS.json, ghidra_run.py) again only if the evidence '
                'contradicts that loading, and say in NOTES.md what contradicted it.'}


def _decide(data):
    """How to load an image's bytes: {format, processor, loader, load_address, prescript, why} and None, or None and
    why the loading cannot be decided."""
    if data[:4] == b'\x7fELF':
        return _elf(data)
    return _raw(data)


def _elf(data):
    bits = {1: 32, 2: 64}.get(data[4]) if len(data) > 5 else None
    order = {1: '<', 2: '>'}.get(data[5]) if len(data) > 5 else None
    if bits is None or order is None or len(data) < (52 if bits == 32 else 64):
        return None, 'the ELF header is cut short or names no known class and byte order'
    machine = struct.unpack_from(order + 'H', data, 18)[0]
    endian = 'little' if order == '<' else 'big'
    language = ELF_LANGUAGES.get((machine, bits, order))
    if language is None:
        return None, ('the kit maps no Ghidra language to ELF machine type %d (%d-bit, %s-endian)'
                      % (machine, bits, endian))
    # The program headers' table offset, entry size and count, and where p_vaddr sits in an entry, by class.
    table, sizes, vaddr = (('I', 28), 42, ('I', 8)) if bits == 32 else (('Q', 32), 54, ('Q', 16))
    loads = []
    try:
        offset = struct.unpack_from(order + table[0], data, table[1])[0]
        size, count = struct.unpack_from(order + 'HH', data, sizes)
        for index in range(count):
            at = offset + index * size
            if struct.unpack_from(order + 'I', data, at)[0] == 1:  # PT_LOAD
                loads.append(struct.unpack_from(order + vaddr[0], data, at + vaddr[1])[0])
    except struct.error:
        return None, 'the ELF program headers are cut short'
    load = min(loads) if loads else 0
    return {'format': 'elf', 'processor': language, 'loader': 'ElfLoader', 'load_address': '0x%08x' % load,
            'prescript': None,
            'why': 'An ELF of machine type %d (%d-bit, %s-endian), which the kit maps to %s; Ghidra\'s ELF loader '
                   'places its segments at their own addresses, the lowest at 0x%08x.'
                   % (machine, bits, endian, language, load)}, None


def _raw(data):
    if len(data) < 64:
        return None, 'the image is too small to hold a Cortex-M vector table'
    report = _map_check().check_image(data)
    best = report['best']
    if best is None:
        return None, 'the Cortex-M map check found no vector table'
    if not report['decisive']:
        return None, ('the Cortex-M map check found no decisive mapping (best coherence %s at header %s, base %s)'
                      % (best['coherence'], best['header_offset'], best['load_base']))
    if int(best['header_offset'], 16) or int(best['vector_file_offset'], 16):
        return None, ('the decisive mapping puts the vector table at file offset %s after a header of %s bytes, not at '
                      'the first byte' % (best['vector_file_offset'], best['header_offset']))
    runner = report['runner_up']
    return {'format': 'raw', 'processor': CORTEX_M, 'loader': 'BinaryLoader', 'load_address': best['load_base'],
            'prescript': 'SeedCortexM.java',
            'why': 'A raw image: the Cortex-M map check found a decisive mapping with the vector table at the first '
                   'byte, loaded at %s; %d of its %d literal pointers in range resolve to strings (coherence %s%s).'
                   % (best['load_base'], best['string_pointers'], best['pointers'], best['coherence'],
                      ', runner-up %s' % runner['coherence'] if runner else ', no other mapping')}, None


def _map_check():
    from .toolkit import resources_root
    spec = importlib.util.spec_from_file_location('image_map_check',
                                                  resources_root() / 'tools/workspace/image_map_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scripts():
    from .toolkit import resources_root
    return {name: hashlib.sha256((resources_root() / name).read_bytes()).hexdigest() for name in _SCRIPTS}


def _not_made(cause, reason, reported=()):
    return {'made': False, 'cause': cause, 'reason': reason, 'reported': list(reported)}


def _make(folder, data, decision, ghidra, java, stop, context):
    """Run the helper on a copy of the image in a fresh folder under the system temporary folder, whose path has no
    whitespace as the helper requires, and keep its export and log in `folder`."""
    folder.mkdir(parents=True)
    (folder / 'decision.json').write_text(json.dumps(decision, indent=1, sort_keys=True), encoding='utf-8')
    work = Path(tempfile.mkdtemp(prefix='gd-kit-analysis-')).resolve()
    try:
        (work / 'image.bin').write_bytes(data)
        arguments = {'run_dir': str(folder / 'tool'), 'workspace': str(work / 'ghidra'),
                     'image': str(work / 'image.bin'), 'base_address': decision['load_address'],
                     'processor': decision['processor'],
                     'ghidra_path': ghidra, 'java_home': java, 'export_dir': str(work / 'export'),
                     'loader': decision['loader'], 'timeout_s': HELPER_TIMEOUT_SECONDS,
                     **({'prescripts': [decision['prescript']]} if decision.get('prescript') else {})}
        try:
            result = tool_process.run('ghidra_run', arguments, directory=folder / 'calls', stop=stop, context=context)
        finally:
            if (work / 'ghidra' / 'logs').is_dir():
                shutil.copytree(work / 'ghidra' / 'logs', folder / 'helper-log', dirs_exist_ok=True)
        (folder / 'helper-result.json').write_text(json.dumps(result, indent=1, default=str), encoding='utf-8')
        if not result.get('ok'):
            reported = result.get('reasons') or [str(result.get('error') or 'the helper gave no reason')]
            timed_out = result.get('timed_out')
            return _not_made('timeout' if timed_out else 'helper_failed',
                             'The Ghidra helper ' + ('ran out of time' if timed_out else 'failed'), reported)
        shutil.copytree(work / 'export', folder / 'export')
        index = analysis_index.Export(folder / 'export').index()
        (folder / INDEX).write_bytes(index)
        return {'made': True, 'decision': decision, 'export_sha256': digest(folder / 'export'),
                'index_sha256': hashlib.sha256(index).hexdigest()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _kept(folder, relative):
    """The analysis kept in `folder`, with its paths, or None when there is none. Its export must still be the bytes it
    was made with."""
    try:
        outcome = json.loads((folder / 'outcome.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not outcome.get('made'):
        return {**outcome, 'folder': relative}
    export, index = folder / 'export', folder / INDEX
    if digest(export) != outcome['export_sha256'] or digest(index) != outcome['index_sha256']:
        raise IntegrityError('The kit analysis changed after it was made: ' + str(folder))
    return {**outcome, 'folder': relative, 'export': str(export), 'index': str(index)}

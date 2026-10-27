"""Reproduce an upstream MCU image without inventing a benchmark protocol."""
import hashlib
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

CASE = 'zephyr-modbus-v1'
SOURCE_COMMIT = '413b789deb391d3a37d06b463288a5fe765ee57e'
MODULES = {'cmsis': '512cc7e895e8491696b61f7ba8066b4a182569b8',
           'cmsis_6': '06d952b6713a2ca41c9224a62075e4059402a151',
           'stm32': '1e753266ddfb4b07a8a0b1ec566e9637ea45d5ef'}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True,
                                   stderr=subprocess.PIPE, timeout=120).strip()


def verify_source(source, *, native=False):
    source = Path(source).resolve()
    try:
        if _git(source, 'rev-parse', 'HEAD') != SOURCE_COMMIT or _git(source, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Require the clean pinned Zephyr source commit')
        for module, commit in ({} if native else MODULES).items():
            path = source.parent / 'modules/hal' / module
            if _git(path, 'rev-parse', 'HEAD') != commit or _git(path, 'status', '--porcelain', '--untracked-files=all'):
                raise ValueError('Require clean pinned Zephyr module: ' + module)
    except (OSError, subprocess.SubprocessError) as error:
        if native:
            raise ValueError('The clean pinned Zephyr source is required before native building') from error
        raise ValueError('Pinned Zephyr source and west modules are required before building') from error
    if native:
        return {'repository': 'https://github.com/zephyrproject-rtos/zephyr', 'commit': SOURCE_COMMIT,
                'path': 'samples/subsys/modbus/rtu_server', 'modules': {}, 'board': 'native_sim/native/64',
                'transformations': ['Native TTY UART connected to a host PTY; GPIO emulator replaces STM32 GPIO.',
                                    'Application handlers, Modbus framing, CRC and exception behavior are unchanged.']}
    return {'repository': 'https://github.com/zephyrproject-rtos/zephyr', 'commit': SOURCE_COMMIT,
            'path': 'samples/subsys/modbus/rtu_server', 'modules': dict(MODULES), 'board': 'stm32f4_disco',
            'transformations': ['Board wiring selects USART2 for Modbus and disables console/log traffic on that UART.',
                                'Application handlers, Modbus framing, CRC and exception behavior are unchanged.']}


def build(source, compiler, west, output, *, backend='renode'):
    if backend == 'native-sim':
        return build_native(source, compiler, west, output)
    if backend != 'renode':
        raise ValueError('Unknown upstream execution backend')
    from .authoring import evaluator_directory
    source = Path(source).expanduser().resolve()
    provenance = verify_source(source)
    compiler, west = Path(compiler).expanduser().resolve(), Path(west).expanduser().resolve()
    objcopy = compiler.with_name(compiler.name.replace('gcc', 'objcopy'))
    if any(not p.is_file() for p in (compiler, objcopy, west)) or compiler == objcopy:
        raise ValueError('Explicit compiler, adjacent objcopy and west executables are required')
    output = evaluator_directory(output)
    if output.is_relative_to(source) or source.is_relative_to(output) or output.exists() and any(output.iterdir()):
        raise ValueError('Build output must be empty and disjoint from the source')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    resources = Path(__file__).resolve().parents[1] / 'resources/bench/platforms'
    overlay, conf = resources/'zephyr-modbus.overlay', resources/'zephyr-modbus.conf'
    tools = {name: subprocess.check_output([str(path), '--version'], text=True, timeout=30).splitlines()[0]
             for name, path in (('compiler', compiler), ('objcopy', objcopy))}
    env = {**os.environ, 'PATH': str(west.parent) + os.pathsep + os.environ.get('PATH', ''),
           'ZEPHYR_BASE': str(source), 'ZEPHYR_TOOLCHAIN_VARIANT': 'cross-compile',
           'CROSS_COMPILE': str(compiler)[:-len('gcc.exe' if compiler.name.endswith('.exe') else 'gcc')]}
    commands, images = [], []
    for index in (1, 2):
        directory = output / ('build-' + str(index))
        command = [str(west), 'build', '-b', 'stm32f4_disco', str(source/'samples/subsys/modbus/rtu_server'),
                   '-d', str(directory), '--', '-DDTC_OVERLAY_FILE=' + str(overlay),
                   '-DEXTRA_CONF_FILE=' + str(conf), '-DBUILD_VERSION=v4.2.0']
        commands.append(command)
        with (output / ('build-' + str(index) + '.log')).open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=source.parent, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=600)
        if completed.returncode:
            raise ValueError('Upstream build failed; inspect the retained build log')
        images.append(_sha(directory/'zephyr/zephyr.bin'))
    if len(set(images)) != 1:
        raise ValueError('Two clean upstream builds produced different firmware bytes')
    if verify_source(source) != provenance:
        raise ValueError('Upstream source changed during the build')
    for suffix in ('bin', 'elf'):
        shutil.copyfile(output/('build-1/zephyr/zephyr.' + suffix), output/('firmware.' + suffix))
    record = {'schema': 'benchmark-upstream-build/1', 'case': CASE, 'execution': 'reference-build',
              'model_benchmark': False, 'ok': True, 'source': provenance, 'tool_versions': tools,
              'tool_hashes': {'compiler': _sha(compiler), 'objcopy': _sha(objcopy)},
              'images': {'firmware.bin': images[0]}, 'rebuild_hashes': images, 'commands': commands,
              'configuration_hashes': {p.name: _sha(p) for p in (overlay, conf)},
              'generated_configuration_sha256': _sha(output/'build-1/zephyr/.config')}
    (output/'build-report.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


def build_native(source, compiler, west, output):
    """Build the unchanged RTU application with host UART/GPIO drivers, outside its source tree."""
    from .authoring import evaluator_directory
    source = Path(source).expanduser().resolve()
    provenance = verify_source(source, native=True)
    compiler, west = Path(compiler).expanduser().resolve(), Path(west).expanduser().resolve()
    if not compiler.is_file() or not west.is_file():
        raise ValueError('Explicit native compiler and west executables are required')
    output = evaluator_directory(output)
    if output.is_relative_to(source) or source.is_relative_to(output) or output.exists() and any(output.iterdir()):
        raise ValueError('Build output must be empty and disjoint from the source')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    resources = Path(__file__).resolve().parents[1] / 'resources/bench/platforms'
    overlay, conf = resources/'zephyr-modbus-native.overlay', resources/'zephyr-modbus-native.conf'
    runner_config = resources/'zephyr-modbus-native.cmake'
    env = {**os.environ, 'PATH': str(west.parent) + os.pathsep + os.environ.get('PATH', ''),
           'ZEPHYR_BASE': str(source), 'ZEPHYR_TOOLCHAIN_VARIANT': 'host', 'CC': str(compiler)}
    commands, hashes = [], []
    for index in (1, 2):
        directory = output / ('build-' + str(index))
        command = [str(west), 'build', '-b', 'native_sim/native/64', str(source/'samples/subsys/modbus/rtu_server'),
                   '-d', str(directory), '--', '-DDTC_OVERLAY_FILE=' + str(overlay),
                   '-DEXTRA_CONF_FILE=' + str(conf), '-DBUILD_VERSION=v4.2.0', '-DZEPHYR_MODULES=',
                   '-DCMAKE_C_COMPILER=' + str(compiler),
                   '-DCMAKE_PROJECT_modbus-rtu-server_INCLUDE=' + str(runner_config),
                   '-DEXTRA_CFLAGS=-ffile-prefix-map=' + str(directory) + '=/build']
        commands.append(command)
        with (output/('build-' + str(index) + '.log')).open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=source.parent, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=600)
        if completed.returncode:
            raise ValueError('Native upstream build failed; inspect the retained build log')
        # zephyr.elf is the embedded library; zephyr.exe is the actual host executable ELF.
        hashes.append(_sha(directory/'zephyr/zephyr.exe'))
    if len(set(hashes)) != 1:
        raise ValueError('Two clean native builds produced different executable bytes')
    if verify_source(source, native=True) != provenance:
        raise ValueError('Upstream source changed during the native build')
    image = output/'firmware.elf'
    shutil.copy2(output/'build-1/zephyr/zephyr.exe', image)
    record = {'schema': 'benchmark-upstream-build/1', 'case': CASE, 'execution': 'native-source-build',
              'model_benchmark': False, 'ok': True, 'source': provenance,
              'tool_versions': {'compiler': subprocess.check_output([str(compiler), '--version'], text=True,
                                                                   timeout=30).splitlines()[0]},
              'tool_hashes': {'compiler': _sha(compiler)}, 'images': {'firmware.elf': hashes[0]},
              'rebuild_hashes': hashes, 'commands': commands,
              'configuration_hashes': {p.name: _sha(p) for p in (overlay, conf, runner_config)},
              'generated_configuration_sha256': _sha(output/'build-1/zephyr/.config'),
              'time_policy': {'probe': 'native simulator paced to the host clock',
                  'package_calls': 'host-clock-with-process-pauses',
                  'clock': 'Host time continues during SIGSTOP; simulated time catches up after SIGCONT.',
                  'limitation': 'Thinking time is not excluded from native clock pacing; no timing-equivalence claim.'},
              'execution_setup': {'method': 'native-sim', 'artifact': 'firmware.elf',
                  'substitutions': provenance['transformations'], 'validated_behavior': [],
                  'hardware_gaps': ['MCU execution, physical UART/RS-485 timing and electrical behavior are untested.',
                                    'The host clock continues during process pauses; native clock pacing catches up on resume.']}}
    (output/'build-report.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


def native_recipe(image):
    """Derive evaluator memory coordinates from the actual linked ELF, without changing its code."""
    try:
        from elftools.elf.elffile import ELFFile
    except ImportError as error:
        raise ValueError('Native case authoring requires the optional pyelftools build dependency') from error
    with Path(image).open('rb') as stream:
        elf = ELFFile(stream)
        if elf.header['e_type'] != 'ET_EXEC' or elf.elfclass != 64 or not elf.little_endian:
            raise ValueError('Native observations require a non-PIE 64-bit little-endian executable ELF')
        symbols = elf.get_section_by_name('.symtab')
        registers = symbols.get_symbol_by_name('holding_reg') if symbols else None
        gpio = symbols.get_symbol_by_name('gpio_emul_data_0') if symbols else None
        if not registers or len(registers) != 1 or registers[0]['st_size'] != 16 or not gpio or len(gpio) != 1:
            raise ValueError('Pinned Modbus register and GPIO symbols are missing or ambiguous')
        layouts = [die for cu in elf.get_dwarf_info().iter_CUs() for die in cu.iter_DIEs()
            if die.tag == 'DW_TAG_structure_type' and die.attributes.get('DW_AT_name')
            and die.attributes['DW_AT_name'].value == b'gpio_emul_data']
        offsets = {member.attributes['DW_AT_data_member_location'].value for die in layouts
            for member in die.iter_children() if member.tag == 'DW_TAG_member'
            and member.attributes['DW_AT_name'].value == b'output_vals'}
        if len(offsets) != 1:
            raise ValueError('Native GPIO output layout is missing or ambiguous')
        return {'backend': 'native-sim', 'uart_args': ['--rt', '--native_uart_port={tty}'], 'stimuli': {},
            'observations': {**{'register_pair'+str(i): {'address': registers[0]['st_value']+4*i,
                'width': 32, 'count': 1} for i in range(4)},
                'gpio_d': {'address': gpio[0]['st_value']+offsets.pop(), 'width': 32, 'count': 1}}}


def prepare_execution(build_dir, password_file, output, *, native_record=None, reference_record=None):
    """Author an isolated native variant under the same registered case and runner; never modify installed resources."""
    from ..benchmark import case_root
    from .authoring import evaluator_directory
    from .registry import resolve_case
    from .execution import artifact_name
    from .emulator import truth_for_case
    from .truth import seal
    from .rehosting import compare_executions
    build_dir = Path(build_dir).resolve()
    record = json.loads((build_dir/'build-report.json').read_text(encoding='utf-8'))
    image = build_dir/'firmware.elf'
    if (record.get('schema') != 'benchmark-upstream-build/1' or record.get('case') != CASE
            or record.get('execution') != 'native-source-build' or record.get('ok') is not True
            or record.get('source', {}).get('commit') != SOURCE_COMMIT
            or record.get('images') != {'firmware.elf': _sha(image)}
            or record.get('rebuild_hashes') != [record['images']['firmware.elf']]*2):
        raise ValueError('Native case requires matching pinned build evidence and two identical executable builds')
    recipe = native_recipe(image)
    definition = resolve_case(CASE)
    if definition.manifest.get('execution_setup', {}).get('method', 'renode') != 'renode':
        raise ValueError('Prepare the variant from the original registered Renode case root')
    payload = truth_for_case(case_root(), definition.manifest, {'evaluator_password_file': password_file})
    manifest = copy.deepcopy(definition.manifest)
    setup = copy.deepcopy(record['execution_setup'])
    setup['validated_behavior'] = []
    if bool(native_record) != bool(reference_record):
        raise ValueError('Supply both native and reference observation records')
    if native_record:
        native = json.loads(Path(native_record).read_text(encoding='utf-8'))
        reference = json.loads(Path(reference_record).read_text(encoding='utf-8'))
        compared = compare_executions(native, reference)
        if (compared['verdict'] != 'passed' or compared['execution'] != 'firmware-rehosting-validation'
                or native['image_sha256'] != record['images']['firmware.elf']
                or reference['image_sha256'] != manifest['images'][artifact_name(manifest)]):
            raise ValueError('Execution comparison failed or names different registered/build images')
        record['execution_validation'] = {'native': native, 'renode': reference,
            'reference_image_sha256': manifest['images'][artifact_name(manifest)]}
        setup['validated_behavior'] = [row['id'] for row in native['records']]
    output = evaluator_directory(output)
    source_root = case_root().resolve()
    if (output.is_relative_to(source_root) or source_root.is_relative_to(output)
            or output.exists() and any(output.iterdir())):
        raise ValueError('Native resources must be empty and disjoint from the installed case root')
    # No plaintext answer files or password handles are written into this root.
    shutil.copytree(source_root, output, dirs_exist_ok=True)
    case_dir = output/'cases'/CASE
    (case_dir/artifact_name(manifest)).unlink()
    shutil.copy2(image, case_dir/'firmware.elf')
    manifest.update(images=record['images'], execution_setup=setup, calibration={'status': 'pending'},
        time_policy={'probe': 'native simulator paced to host clock', 'package_calls': 'host-clock-with-process-pauses',
            'clock': 'Host clock continues during SIGSTOP; simulated time catches up after SIGCONT.',
            'limitation': 'Thinking time is not excluded from native clock pacing; no timing-equivalence claim.'})
    manifest['provenance'].update(source=record['source'], build_execution='native-source-build',
        build_tools=record['tool_versions'], build='Two byte-identical native executable ELF builds',
        uart_bridge='Zephyr native TTY UART via a host PTY and a protocol-independent TCP bridge.')
    manifest['limitations'] = ['Native host ELF recovery uses a different executable from the ARM case.',
        *setup['hardware_gaps']]
    payload.update(images=record['images'], source=record['source'], build=record, recipe=recipe, inventory_state='pending')
    payload.pop('calibration', None)
    manifest['truth']['sha256'] = seal(payload, output/manifest['truth']['path'],
        Path(password_file).read_text(encoding='utf-8').strip())
    (case_dir/'case.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return {'ok': True, 'case': CASE, 'execution': 'native-case-authoring', 'model_benchmark': False,
            'resources_root': str(output), 'images': record['images'], 'calibration': 'pending',
            'comparison_included': bool(native_record)}

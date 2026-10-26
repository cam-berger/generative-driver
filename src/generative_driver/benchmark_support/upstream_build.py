"""Reproduce an upstream MCU image without inventing a benchmark protocol."""
import hashlib
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


def verify_source(source):
    source = Path(source).resolve()
    try:
        if _git(source, 'rev-parse', 'HEAD') != SOURCE_COMMIT or _git(source, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Require the clean pinned Zephyr source commit')
        for module, commit in MODULES.items():
            path = source.parent / 'modules/hal' / module
            if _git(path, 'rev-parse', 'HEAD') != commit or _git(path, 'status', '--porcelain', '--untracked-files=all'):
                raise ValueError('Require clean pinned Zephyr module: ' + module)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('Pinned Zephyr source and west modules are required before building') from error
    return {'repository': 'https://github.com/zephyrproject-rtos/zephyr', 'commit': SOURCE_COMMIT,
            'path': 'samples/subsys/modbus/rtu_server', 'modules': dict(MODULES), 'board': 'stm32f4_disco',
            'transformations': ['Board wiring selects USART2 for Modbus and disables console/log traffic on that UART.',
                                'Application handlers, Modbus framing, CRC and exception behavior are unchanged.']}


def build(source, compiler, west, output):
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

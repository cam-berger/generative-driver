"""Build and seal evaluator-owned families without exposing their authoring inputs."""
from pathlib import Path
import base64
import hashlib
import json
import shutil
import sysconfig
import tempfile
from . import authoring, truth

GCC_VERSION = 'arm-none-eabi-gcc (Arm GNU Toolchain 14.2.Rel1 (Build arm-14.52)) 14.2.1 20241119'
OBJCOPY_VERSION = 'GNU objcopy (Arm GNU Toolchain 14.2.Rel1 (Build arm-14.52)) 2.43.1.20241119'


def require_private_root(root, forbidden_roots):
    candidate = Path(root).expanduser().resolve()
    libraries = sysconfig.get_paths()
    forbidden = [*forbidden_roots, *(libraries[key] for key in ('purelib', 'platlib') if libraries.get(key))]
    if any(candidate.is_relative_to(Path(base).expanduser().resolve()) for base in forbidden):
        raise ValueError('Evaluator authoring must be outside repository and candidate roots')
    from .authoring import evaluator_directory
    return evaluator_directory(candidate)


def require_toolchain(version_line, objcopy_line):
    if version_line != GCC_VERSION or objcopy_line != OBJCOPY_VERSION:
        raise ValueError('Expected exact recorded Arm GNU Toolchain compiler and objcopy versions')


def build_argv(compiler, source_dir, elf, flags):
    source = Path(source_dir)
    return [str(compiler), *flags,
            '-ffile-prefix-map=' + str(source.resolve()) + '=.',
            '-fdebug-prefix-map=' + str(source.resolve()) + '=.',
            '-Wl,--build-id=none', '-T', str(source / 'linker.ld'),
            str(source / 'startup.c'), str(source / 'board_uart.c'),
            str(source / 'application.c'), '-lgcc', '-o', str(elf)]


BUILD_FILES = {'build/build-report.json', 'build/firmware.elf', 'build/firmware.bin'}
_INPUT_FILES = {'AUTHORING.json', 'LICENSE', 'source/startup.c', 'source/board_uart.c',
    'source/application.c', 'source/linker.ld', 'build.py', 'build-recipe.json',
    'native/platform.repl', 'native/device.resc', 'native/observation-map.json',
    'diagnostic/episodes.json', 'final/episodes.json', 'oracle/vectors.json', 'mutants/manifest.json',
    *{f'reference-{ref}/{name}.json' for ref in ('a', 'b') for name in ('model', 'capabilities', 'replies')}}
_FAMILIES = {'sampled-sensor', 'parameter-store'}


def inventory(family, *, qualified=False):
    if family not in _FAMILIES:
        raise ValueError('Unsupported family')
    return (_INPUT_FILES | BUILD_FILES | ({'native/SampleSource.cs'} if family == 'sampled-sensor' else set())
            | ({'calibration/calibration.json'} if qualified else set()))


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _files(root):
    if root.is_symlink():
        raise ValueError('Inventory symlinks are forbidden')
    paths = list(root.rglob('*'))
    if any(path.is_symlink() or not (path.is_dir() or path.is_file()) for path in paths):
        raise ValueError('Inventory requires regular files and directories without symlinks')
    return {path.relative_to(root).as_posix() for path in paths if path.is_file()}


def _author(root, *, complete):
    root = require_private_root(root, [])
    metadata = _json(root / 'AUTHORING.json')
    family = metadata.get('family')
    if (metadata.get('schema') != 'benchmark-authoring/1' or family not in _FAMILIES
            or metadata.get('id') != family + '-v1'
            or metadata.get('execution') not in ('reference-build', 'scripted-contract-fixture')):
        raise ValueError('Invalid family authoring metadata')
    expected = inventory(family)
    actual = _files(root)
    if actual != expected and (complete or actual != expected - BUILD_FILES):
        raise ValueError('Authoring inventory differs from the exact pending inventory')
    return root, metadata


def _disjoint(left, right):
    if left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError('Authoring and output roots must be disjoint')


def build_family(authoring_root, compiler, output_dir):
    """Execute two private builds with the shared runner; commit only identical images."""
    root, metadata = _author(authoring_root, complete=False)
    output = require_private_root(output_dir, [])
    _disjoint(root, output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Build output must be empty')
    compiler = Path(compiler).expanduser()
    if not compiler.is_absolute() or not compiler.is_file():
        raise ValueError('Require an absolute native compiler file')
    compiler = compiler.resolve()
    objcopy_name = 'arm-none-eabi-objcopy' + ('.exe' if compiler.suffix.lower() == '.exe' else '')
    if not (compiler.parent / objcopy_name).is_file():
        raise ValueError('Require adjacent native objcopy')
    recipe = _json(root / 'build-recipe.json')
    if (recipe.get('schema') != 'benchmark-family-build/1' or not isinstance(recipe.get('flags'), list)
            or set(recipe.get('variants', {})) != {'firmware'}
            or any(not isinstance(v, list) for v in recipe['variants'].values())):
        raise ValueError('Invalid family build recipe')
    inputs = inventory(metadata['family']) - BUILD_FILES
    source_hashes = {name: _hash(root / name) for name in sorted(inputs)}
    steps = []
    for variant in ('firmware',):
        argv = build_argv(compiler, root / 'source', Path('{output}') / (variant + '.elf'),
                          recipe['flags'] + recipe['variants'][variant])
        converted = ['{compiler}']
        for arg in argv[1:]:
            if arg.startswith(('-ffile-prefix-map=', '-fdebug-prefix-map=')):
                converted.append(arg.split('=', 1)[0] + '={authoring}/source=.')
            elif arg.startswith(str(root / 'source') + '/') or arg.startswith(str(root / 'source') + '\\'):
                converted.append('{authoring}/source/' + Path(arg).name)
            else:
                converted.append(arg.replace('\\', '/') if arg.startswith('{output}') else arg)
        steps.append(converted)
        steps.append(['{objcopy}', '-O', 'binary', '{output}/' + variant + '.elf', '{output}/' + variant + '.bin'])
    manifest = {'schema': 'benchmark-build/1', 'tools': {'compiler': {'version': GCC_VERSION},
        'objcopy': {'filename': objcopy_name, 'version': OBJCOPY_VERSION}},
        'source_hashes': source_hashes, 'outputs': ['firmware.elf', 'firmware.bin'],
        'steps': steps}
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    reports = []
    with tempfile.TemporaryDirectory(prefix='family-staging-', dir=output) as temporary:
        staging = Path(temporary)
        for name in sorted(inputs):
            target = staging / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(root / name, target)
        _write(staging / 'build.json', manifest)
        for index in (1, 2):
            report = authoring.build_case(staging, compiler, output / ('build-' + str(index)))
            require_toolchain(report['tool_versions']['compiler'], report['tool_versions']['objcopy'])
            report['execution'] = metadata['execution']
            _write(output / ('build-' + str(index)) / 'build-report.json', report)
            reports.append(report)
    binaries = ('firmware.bin',)
    if any(reports[0]['images'][name] != reports[1]['images'][name] for name in binaries):
        raise ValueError('Native images are not reproducible; canonical assets were preserved')
    for name in binaries:
        pinned = root / 'build' / name
        if pinned.exists() and _hash(pinned) != reports[0]['images'][name]:
            raise ValueError('Rebuild differs from pinned images; canonical assets were preserved')
    result = {**reports[0], 'schema': 'benchmark-family-build-report/1', 'case_id': metadata['id'],
              'source_hashes': source_hashes, 'reproducibility': {'builds': 2, 'identical': True,
              'reports': reports}, 'inventory_state': 'pending'}
    (root / 'build').mkdir(mode=0o700, exist_ok=True)
    for name in manifest['outputs']:
        shutil.copyfile(output / 'build-1' / name, root / 'build' / name)
    _write(root / 'build/build-report.json', result)
    return result


def assemble_release(authoring_root, build_report, password_file, output_dir):
    """Seal exact pending inventory and publish only images plus allowlisted metadata."""
    root, metadata = _author(authoring_root, complete=True)
    output = require_private_root(output_dir, [])
    _disjoint(root, output)
    report = _json(root / 'build/build-report.json')
    if (report != build_report or report.get('schema') != 'benchmark-family-build-report/1'
            or report.get('case_id') != metadata['id'] or report.get('ok') is not True
            or report.get('reproducibility', {}).get('identical') is not True
            or report.get('reproducibility', {}).get('builds') != 2):
        raise ValueError('Release requires the successful reproducible private build report')
    require_toolchain(report['tool_versions']['compiler'], report['tool_versions']['objcopy'])
    for name, digest in report['source_hashes'].items():
        if name not in inventory(metadata['family']) - BUILD_FILES or _hash(root / name) != digest:
            raise ValueError('Authoring changed after native build')
    if set(report['source_hashes']) != inventory(metadata['family']) - BUILD_FILES:
        raise ValueError('Incomplete source inventory')
    for name in ('firmware.bin', 'firmware.elf'):
        if _hash(root / 'build' / name) != report['images'][name]:
            raise ValueError('Built asset changed after native build')
    case_id = metadata['id']
    allowed = {f'cases/{case_id}/{name}' for name in ('case.json', 'firmware.bin')} | {f'groundtruth/{case_id}.enc'}
    if output.exists() and (not output.is_dir() or _files(output) - allowed):
        raise ValueError('Unexpected plaintext or unrelated files under release output')
    for name in ('firmware.bin',):
        existing = output / 'cases' / case_id / name
        if existing.exists() and _hash(existing) != report['images'][name]:
            raise ValueError('Release would replace a different pinned image')
    entries = {name: {'sha256': _hash(root / name), 'base64': base64.b64encode((root / name).read_bytes()).decode('ascii')}
               for name in sorted(inventory(metadata['family']))}
    hashes = {name: entry['sha256'] for name, entry in entries.items()}
    input_hashes = {'source': _canonical({name: value for name, value in hashes.items() if name.startswith('source/')}),
        'contract': _canonical({name: value for name, value in hashes.items() if name.startswith(('diagnostic/', 'final/', 'oracle/'))})}
    payload = {'schema': 'benchmark-family-truth/1', 'case_id': case_id, 'family': metadata['family'],
               'inventory_state': 'pending', 'inventory': entries, 'inventory_sha256': _canonical(hashes),
               'input_hashes': input_hashes}
    password = Path(password_file).read_text(encoding='utf-8').strip()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    encrypted = output / 'groundtruth' / (case_id + '.enc')
    digest = truth.seal(payload, encrypted, password)
    if truth.unlock(encrypted, password, digest) != payload:
        raise ValueError('Sealed inventory round-trip failed')
    manifest = {'schema': 'benchmark-case/2', 'id': case_id, 'family': metadata['family'],
        'version': '3', 'evaluator_version': '3', 'adapter_key': 'emulator-v2',
        'execution': 'actual-agent-emulation', 'evidence_track': 'firmware', 'scope': 'full-workflow',
        'scenarios': ['original'], 'scenario_descriptors': {'original': {'kind': 'stable'}},
        'required_stages': ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'],
        'images': {name: report['images'][name] for name in ('firmware.bin',)},
        'truth': {'path': 'groundtruth/' + case_id + '.enc', 'sha256': digest},
        'default_effects': ['write'], 'approval_scope': 'emulator',
        'calibration': {'status': 'pending', 'input_hashes': input_hashes},
        'provenance': {'source': 'Independently authored family firmware', 'source_archive': 'encrypted evaluator bundle',
            'license': 'Apache-2.0', 'build_execution': report['execution'], 'build_tools': report['tool_versions'],
            'build': ('Two byte-identical native binary builds' if report['execution'] == 'reference-build'
                      else 'Two byte-identical scripted contract fixture builds')},
        'time_policy': {'probe': 'continuous emulator virtual time', 'package_calls': 'paused between calls',
            'limitation': 'Thinking time excluded; not a real-time control benchmark'},
        'limitations': ['Reference calibration pending', 'UART-over-TCP does not prove wire baud correctness',
            'Independent emulator observation is not physical measurement']}
    case = output / 'cases' / case_id
    case.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in manifest['images']:
        shutil.copyfile(root / 'build' / name, case / name)
    _write(case / 'case.json', manifest)
    return {'status': 'pending', 'case_id': case_id, 'images': manifest['images'], 'ciphertext_sha256': digest}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('authoring-root', 'compiler', 'password-file', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    # Build evidence is kept beside the release, outside its exact public inventory.
    try:
        report = build_family(args.authoring_root, args.compiler, args.output.with_name(args.output.name + '-build'))
        result = assemble_release(args.authoring_root, report, args.password_file, args.output)
    except (ValueError, OSError, RuntimeError):
        parser.exit(1, 'Family build/release failed; inspect private evaluator diagnostics.\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

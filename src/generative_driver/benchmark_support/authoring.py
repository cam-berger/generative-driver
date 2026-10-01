"""Private evaluator authoring runners; never configure or invoke a model runtime."""
import hashlib
import json
import re
import subprocess
from pathlib import Path


def evaluator_directory(value):
    """Exclude known worker roots; this is placement validation, not OS isolation."""
    from ..setup import home_path
    from ..client import default_home
    path = Path(value).expanduser().resolve()
    package = Path(__file__).resolve().parents[1]
    checkout = next((p for p in package.parents if (p / '.git').exists()), None)
    forbidden = [package, Path(home_path()).resolve() / 'runs', Path(default_home()).resolve() / 'runs']
    if checkout:
        forbidden.append(checkout)
    parents = (path, *path.parents)
    if (any(path.is_relative_to(root) for root in forbidden)
            or any((p / '_report_schema.json').exists() for p in parents)
            or any(p.name == 'work' and p.parent.parent.name == 'runs' for p in parents)):
        raise ValueError('Evaluator directory must be outside repository, installed package and known candidate roots')
    return path


def _within(root, relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or Path(relative).is_absolute() or any(p in ('.', '..') for p in relative.split('/'))):
        raise ValueError('Invalid build path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Build path escapes directory')
    return path


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_case(authoring_dir: Path, compiler: Path, output_dir: Path) -> dict:
    author = evaluator_directory(authoring_dir)
    output = evaluator_directory(output_dir)
    if author.is_relative_to(output) or output.is_relative_to(author):
        raise ValueError('Authoring and output directories must be disjoint')
    if not author.is_dir() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ValueError('Require an authoring directory and empty output directory')
    manifest_path = _within(author, 'build.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('schema') != 'benchmark-build/1':
        raise ValueError('Unsupported build schema')
    tools = manifest.get('tools')
    if (not isinstance(tools, dict) or any(not isinstance(tools.get(k), dict) for k in ('compiler', 'objcopy'))):
        raise ValueError('Invalid build tools')
    compiler = Path(compiler).expanduser().resolve()
    objcopy_name = tools['objcopy'].get('filename')
    if not isinstance(objcopy_name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', objcopy_name):
        raise ValueError('Invalid adjacent objcopy filename')
    objcopy = _within(compiler.parent, objcopy_name)
    paths = {'compiler': compiler, 'objcopy': objcopy}
    for name in paths:
        if not paths[name].is_file() or not isinstance(tools[name].get('version'), str) or not tools[name]['version']:
            raise ValueError('Missing native tool or exact version pin')
    sources = manifest.get('source_hashes')
    outputs = manifest.get('outputs')
    steps = manifest.get('steps')
    if not isinstance(sources, dict) or not sources or not isinstance(outputs, list) or not outputs:
        raise ValueError('Build requires source hashes and outputs')
    for name, expected in sources.items():
        path = _within(author, name)
        if not path.is_file() or _hash(path) != expected:
            raise ValueError('Build source hash mismatch: ' + name)
    if any(not isinstance(name, str) or Path(name).name != name for name in outputs) or len(set(outputs)) != len(outputs):
        raise ValueError('Invalid expected output filenames')
    for name in outputs:
        _within(output, name)
    if not isinstance(steps, list) or not steps:
        raise ValueError('Build requires ordered argv lists')
    commands = []
    for step in steps:
        if not isinstance(step, list) or not step or step[0] not in ('{compiler}', '{objcopy}'):
            raise ValueError('Build steps must invoke only pinned tools')
        argv = [str(paths[step[0][1:-1]])]
        for index, arg in enumerate(step[1:], 1):
            if not isinstance(arg, str) or not arg or any(c in arg for c in '\n\r\x00'):
                raise ValueError('Invalid build argument')
            if arg.startswith('{authoring}/') or arg.startswith('{output}/'):
                token, relative = arg.split('/', 1)
                if '{' in relative or '}' in relative:
                    raise ValueError('Invalid build substitution')
                if token == '{authoring}' and relative not in sources:
                    raise ValueError('Build source argument is absent from hash inventory')
                argv.append(str(_within(author if token == '{authoring}' else output, relative)))
            elif any(c in arg for c in '{}\\/') or arg in ('.', '..'):
                raise ValueError('Only validated authoring/output paths may be substituted')
            else:
                # Literal arguments are a small native-tool vocabulary, never implicit
                # filenames, response files, include paths, specs, plugins or scripts.
                compiler_flags = {'-mthumb', '-Wall', '-Wextra', '-Werror',
                    '-ffunction-sections', '-fdata-sections', '-ffreestanding',
                    '-fno-common', '-nostdlib', '-nostartfiles', '-c', '-lgcc',
                    '-Wl,--gc-sections', '-Wl,--build-id=none'}
                compiler_value = re.fullmatch(
                    r'(?:-O[0123sgz]|-g[0-3]|-mcpu=cortex-m[0-9]+(?:plus)?|'
                    r'-mfloat-abi=(?:soft|softfp|hard)|'
                    r'-D[A-Za-z_][A-Za-z0-9_]*(?:=(?:-?[0-9]+|0[xX][0-9a-fA-F]+))?)', arg)
                next_arg = step[index + 1] if index + 1 < len(step) else None
                next_prefix = {'-o': '{output}/', '-T': '{authoring}/'}.get(arg)
                supported = (step[0] == '{compiler}' and
                             (arg in compiler_flags or compiler_value is not None or
                              (next_prefix is not None and isinstance(next_arg, str) and next_arg.startswith(next_prefix))))
                supported = supported or (step[0] == '{objcopy}' and
                             ((arg == '-O' and next_arg == 'binary') or
                              (arg == 'binary' and step[index - 1] == '-O')))
                if not supported:
                    raise ValueError('Unsupported literal build argument; file inputs require inventoried placeholders')
                argv.append(arg)
        commands.append(argv)
    versions = {}
    try:
        for name, tool in paths.items():
            result = subprocess.run([str(tool), '--version'], cwd=author, check=True,
                                    timeout=120, capture_output=True, text=True)
            versions[name] = result.stdout.strip().splitlines()[0]
            if versions[name] != tools[name]['version']:
                raise ValueError('Native tool version does not match pin: ' + name)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        for argv in commands:
            subprocess.run(argv, cwd=author, check=True, timeout=120, capture_output=True, text=True)
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError('Native reference build failed; no successful build report was written') from error
    output_paths = {name: _within(output, name) for name in outputs}
    if any(not path.is_file() for path in output_paths.values()):
        raise RuntimeError('Native build did not produce all expected outputs')
    result = {'schema': 'benchmark-build-report/1', 'ok': True, 'execution': 'reference-build',
              'model_benchmark': False, 'images': {name: _hash(path) for name, path in output_paths.items()},
              'input_hashes': {**sources, 'build.json': _hash(manifest_path)},
              'tool_versions': versions, 'commands': commands}
    (output / 'build-report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def calibrate(case_id: str, options: dict) -> dict:
    from .reference_calibration import calibrate as native_calibration
    return native_calibration(case_id, options)

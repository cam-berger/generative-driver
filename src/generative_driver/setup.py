"""Non-invasive installation checks and explicit runtime configuration.

A generated Codex plugin carries a plugin build that names exactly what was generated, because Codex reinstalls a
plugin from a local marketplace only when its version changes; `doctor` compares it with what `setup` would write now
(docs/features/kit-fixes/02-plugin-freshness.md)."""
import contextlib
import hashlib
import os
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile

from . import __version__

SUPPORTED_EXECUTORS = ("codex", "replay")


def home_path(home=None):
    from .client import default_home
    return Path(home or default_home()).expanduser().resolve()


def resolve_executor(executor=None):
    """Resolve an optional executor before starting or freezing a run."""
    if executor is None:
        return "codex"
    if executor not in SUPPORTED_EXECUTORS:
        raise ValueError(f"Unsupported executor {executor!r}. Choose codex or replay.")
    return executor


def configure(executor, command, model=None, provider=None, home=None, reasoning_effort=None):
    """Persist an explicit runtime selection without touching runtime credentials."""
    if executor != "codex" or not command or not all(isinstance(s, str) and s for s in command):
        raise ValueError("Choose the codex executor and a nonempty executable argument list")
    root = home_path(home)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "config.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    entry = {"command": command}
    if model:
        entry["model"] = model
    if provider:
        entry["provider"] = provider
    if reasoning_effort:
        entry["reasoning_effort"] = reasoning_effort
    config.setdefault("executors", {})[executor] = entry
    fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"configured": executor, "configuration": entry, "path": str(path)}


# What a generated plugin holds besides its build. The skill folder is a module name so that a test can point the
# generator at a copy with other text; nothing else changes it.
_SKILLS = Path(__file__).parent / 'resources/client/skills'
_MANIFEST = {'name': 'generative-driver', 'version': None, 'license': 'Apache-2.0',
             'description': 'Generate device interfaces with checked firmware and experiment evidence.',
             'author': {'name': 'Generative Driver contributors'},
             'skills': './skills/', 'mcpServers': './.mcp.json',
             'interface': {'displayName': 'Generative Driver',
                           'shortDescription': 'Generate and verify device interfaces.',
                           'longDescription': 'Run the six-stage workflow through a local background configurator.',
                           'developerName': 'Generative Driver contributors', 'category': 'Developer Tools',
                           'capabilities': ['Read', 'Write'],
                           'defaultPrompt': ['Check my setup and help me generate a device interface.']}}
_MARKETPLACE = {'name': 'generative-driver-local', 'interface': {'displayName': 'Generative Driver'},
                'plugins': [{'name': 'generative-driver', 'source': {
                    'source': 'local', 'path': './plugins/generative-driver'},
                    'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
                    'category': 'Developer Tools'}]}
_PLUGIN = 'plugins/generative-driver'
_MARKETPLACE_FILE = '.agents/plugins/marketplace.json'
_SERVER_ARGUMENTS = ('-m', 'generative_driver.mcp')
# The server environment variable that carries the plugin build to the process a generated plugin starts.
_PLUGIN_BUILD = 'GENERATIVE_DRIVER_PLUGIN_BUILD'
_RECORD = 'setup.json'  # in the home: what the last setup generated, for doctor


def _plugin_files(command, arguments, home, build):
    """The content of each file of a generated plugin, as bytes by its path relative to the output folder, with
    `build` written as the plugin build."""
    server = {'command': command, 'args': arguments,
              'env': {'GENERATIVE_DRIVER_HOME': str(home), _PLUGIN_BUILD: build}}
    files = {f'{_PLUGIN}/.codex-plugin/plugin.json': json.dumps({**_MANIFEST, 'version': build}, indent=2),
             f'{_PLUGIN}/.mcp.json': json.dumps({'mcpServers': {'generative-driver': server}}, indent=2),
             _MARKETPLACE_FILE: json.dumps(_MARKETPLACE, indent=2)}
    files = {path: text.encode('utf-8') for path, text in files.items()}
    for path in sorted(_SKILLS.rglob('*')):
        if path.is_file():
            files[f'{_PLUGIN}/skills/{path.relative_to(_SKILLS).as_posix()}'] = path.read_bytes()
    return files


def _build(command, arguments, home):
    """The plugin build `setup` writes for `command`, `arguments` and `home`: the kit version, `+`, and 12 hex digits
    of a digest of every file it writes with the build left empty, so that they change exactly when the content of a
    file other than the build changes."""
    digest = hashlib.sha256()
    for path, content in sorted(_plugin_files(command, arguments, home, '').items()):
        digest.update(b'%s\0%d\0' % (path.encode('utf-8'), len(content)) + content)
    return f'{__version__}+{digest.hexdigest()[:12]}'


def _json_field(path, *keys):
    """The value at `keys` in the JSON file at `path`, or None when the file cannot be read or holds no such value."""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        for key in keys:
            value = value[key]
        return value
    except (OSError, ValueError, LookupError, TypeError):
        return None


def _earlier_plugin(target):
    """Whether `target` holds a plugin `setup` generated, and the plugin build its `.mcp.json` names, or None.

    A plugin `setup` generated has its own name beside a marketplace file with the local marketplace's name, as output
    from before plugin builds has too. Anything else at either path raises `ValueError` naming that path, or naming
    the one that is there when the other is missing."""
    plugin, marketplace = target / _PLUGIN, target / _MARKETPLACE_FILE
    present = [path for path in (plugin, marketplace) if os.path.lexists(path)]
    if not present:
        return False, None
    named = {plugin: _json_field(plugin / '.codex-plugin/plugin.json', 'name') == _MANIFEST['name'],
             marketplace: _json_field(marketplace, 'name') == _MARKETPLACE['name']}
    if not all(named.values()):
        path = next((path for path in present if not named[path]), present[0])
        raise ValueError(f'Setup replaces only a plugin and marketplace file it generated, and this is not one: {path}')
    build = _json_field(plugin / '.mcp.json', 'mcpServers', 'generative-driver', 'env', _PLUGIN_BUILD)
    return True, build if isinstance(build, str) else None


def _make_folder(folder, made):
    """Makes `folder` and any missing parent, appending each folder it makes to `made`, outermost first."""
    missing = []
    while not os.path.lexists(folder) and folder.parent != folder:
        missing.append(folder)
        folder = folder.parent
    for path in reversed(missing):
        path.mkdir()
        made.append(path)


@contextlib.contextmanager
def _placed(target, files):
    """Places the generated `files` in `target` for the duration of the block, replacing the plugin folder and the
    marketplace file there.

    The new plugin is written in full to a temporary folder in `target` before anything there moves. A failure, while
    placing it or in the block, moves back what was there before, removes the folders it made and the temporary
    folder, and raises, so `target` is left as it was."""
    made, moved, temporary = [], [], None
    try:
        _make_folder(target, made)
        temporary = Path(tempfile.mkdtemp(prefix='.setup-', dir=target))
        for path, content in files.items():
            (temporary / 'new' / path).parent.mkdir(parents=True, exist_ok=True)
            (temporary / 'new' / path).write_bytes(content)
        for name in (_PLUGIN, _MARKETPLACE_FILE):
            placed, earlier = target / name, temporary / 'earlier' / name
            if os.path.lexists(placed):
                earlier.parent.mkdir(parents=True, exist_ok=True)
                os.replace(placed, earlier)
                moved.append((placed, earlier))
            _make_folder(placed.parent, made)
            os.replace(temporary / 'new' / name, placed)
            moved.append((temporary / 'new' / name, placed))
        yield
    except BaseException:
        for source, destination in reversed(moved):
            try:
                os.replace(destination, source)
            except OSError:
                pass
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        for folder in reversed(made):
            try:
                folder.rmdir()
            except OSError:
                pass
        raise
    shutil.rmtree(temporary, ignore_errors=True)


def _record(root, record):
    """Replaces the record of the last `setup` in the home `root` with `record`: the output folder, the plugin build,
    the interpreter and the server arguments it used. A failed write leaves the earlier record."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='setup-', suffix='.json', dir=root)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / _RECORD)
    finally:
        Path(temporary).unlink(missing_ok=True)


def integration_assets(host, output, *, command=None, arguments=None, home=None):
    """What `setup` generated: a Codex plugin and its local marketplace in `output`, pinned to `command` (this Python
    executable unless given), the server `arguments` and the home, with its plugin build.

    A plugin it generated there before is replaced in place, so the marketplace Codex already references holds the
    new build; `replaced` and `replaced_build` say whether there was one and which build it had. Anything else at the
    plugin's or the marketplace file's path raises `ValueError`. The record in the home of what was generated is
    replaced too, and a failure leaves `output` as it was."""
    if host != "codex":
        raise ValueError("Choose the codex host")
    target = Path(output).expanduser().resolve()
    command = command or sys.executable
    arguments = list(_SERVER_ARGUMENTS) if arguments is None else arguments
    root = home_path(home)
    build = _build(command, arguments, root)
    plugin, marketplace = target / _PLUGIN, target / _MARKETPLACE_FILE
    replaced, replaced_build = _earlier_plugin(target)
    with _placed(target, _plugin_files(command, arguments, root, build)):
        _record(root, {'output': str(target), 'build': build, 'command': command, 'args': arguments})
    return {"host": host, "plugin": str(plugin), "marketplace": str(marketplace),
            "marketplace_root": str(target), "command": command, "args": arguments, "build": build,
            "replaced": replaced, "replaced_build": replaced_build}


def _codex_selection(config_path):
    """The Codex entry of the configuration at `config_path`, or None when there is none. A file a start could not
    read raises `OSError` or `ValueError`."""
    if not config_path.is_file():
        return None
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('A configuration is a JSON object: ' + _json_type(config))
    executors = config.get('executors', {})
    if not isinstance(executors, dict):
        raise ValueError('A configuration holds its executors in a JSON object: ' + _json_type(executors))
    return executors.get('codex')


def _json_type(value):
    """The JSON name of the type of `value`, a value `json.loads` returned. It names what a file holds without
    quoting the file."""
    return {dict: 'object', list: 'array', str: 'string', bool: 'boolean', int: 'number', float: 'number',
            type(None): 'null'}[type(value)]


def _codex(home):
    """The `dependencies.codex` entry: what a start would find, which is only the command the operator configured."""
    from .agents import codex_identity
    entry = {'available': False, 'path': None, 'required_for': 'Codex workers', 'source': None,
             'configured': False, 'version': None, 'reason': None, 'on_path': None}
    config_path = home_path(home) / 'config.json'
    configure = 'generative-driver configure --executor codex --command <path>'

    def unconfigured(reason):
        # A start never looks on PATH, and what has the name there may be another program: name it, never run it.
        return {**entry, 'on_path': shutil.which('codex'), 'reason': reason}

    try:
        selection = _codex_selection(config_path)
    except (OSError, ValueError) as error:
        return unconfigured(f'{config_path} is not a configuration a start can read: {error}. '
                            f'Fix or remove it, then run `{configure}`.')
    if selection is None:
        return unconfigured(f'No Codex command is configured. Run `{configure}`.')
    try:
        identity = codex_identity(selection)
    except ValueError:
        return unconfigured(f'The Codex entry in {config_path} has no command a start would accept. '
                            f'Run `{configure}`.')
    return {**entry, **identity, 'configured': True, 'available': identity['version'] is not None,
            'source': 'configuration', 'model': selection.get('model'), 'provider': selection.get('provider')}


# Optional packages by distribution name: the install extra that provides each, and what needs it.
_EXTRAS = {'pyserial': ('hardware', 'UART devices and listing serial ports'),
           'pyusb': ('hardware', 'USB devices'),
           'pyftdi': ('hardware', 'I2C and SPI devices through an FTDI adapter'),
           'esptool': ('hardware', 'reading firmware from ESP devices'),
           'pypdf': ('docs', 'reading PDF datasheets')}


def _extras():
    """The `extras` entries: whether each optional package is installed and at which version, read from the installed
    metadata so that none is imported to find out."""
    from importlib import metadata
    entries = {}
    for name, (extra, purpose) in _EXTRAS.items():
        try:
            installed, version = True, metadata.distribution(name).metadata.get('Version')
        except metadata.PackageNotFoundError:
            installed, version = False, None
        entries[name] = {'installed': installed, 'version': version, 'required_for': purpose, 'extra': extra}
    return entries


def _setup_command(root, output):
    """The command, for the platform's shell, that runs `setup` again with this interpreter and the home `root`, into
    `output` or into a placeholder when it is None."""
    if os.name == 'nt':
        # PowerShell takes a single-quoted string literally, and runs a quoted command only after `&`.
        quote = lambda path: "'" + path.replace("'", "''") + "'"
        start = '& ' + quote(sys.executable)
    else:
        quote = lambda path: "'" + path.replace("'", "'\"'\"'") + "'"
        start = quote(sys.executable)
    target = '<directory>' if output is None else quote(output)
    return f'{start} -m generative_driver --home {quote(str(root))} setup --host codex --output {target}'


def _setup_record(path):
    """What the last `setup` recorded at `path`, or None when nothing is there. A record doctor cannot use raises
    `OSError` or `ValueError`."""
    if not os.path.lexists(path):
        return None
    record = json.loads(path.read_text(encoding='utf-8'))
    if not (isinstance(record, dict) and all(isinstance(record.get(key), str) for key in ('output', 'build', 'command'))
            and isinstance(record.get('args'), list) and all(isinstance(argument, str) for argument in record['args'])):
        raise ValueError(f'A setup record holds the output, build, command and arguments it used: {path}')
    return record


def _plugin_freshness(home):
    """The `plugin` entry: the plugin build `setup` would write now with this interpreter, the default server
    arguments and `home`; the build in this process's environment, which the plugin that started it passed on; what
    the last `setup` recorded; and whether the plugin in use is `current`, `stale` or `unchecked`, with the reason."""
    root = home_path(home)
    current = _build(sys.executable, list(_SERVER_ARGUMENTS), root)
    launched_by = os.environ.get(_PLUGIN_BUILD) or None
    generated, rewritten, unreadable = None, None, None
    try:
        record = _setup_record(root / _RECORD)
    except (OSError, ValueError):
        record, unreadable = None, f'The record of the last setup cannot be read: {root / _RECORD}.'
    if record is not None:
        rewritten = _build(record['command'], record['args'], root)
        generated = {'output': record['output'], 'build': record['build'], 'up_to_date': rewritten == record['build']}
    if launched_by == current:
        state, reason = 'current', None
    elif launched_by is not None or (generated is not None and not generated['up_to_date']):
        state = 'stale'
        found = (f'The plugin that started this process has build {launched_by}, and this kit writes {current}.'
                 if launched_by is not None else
                 f'The plugin setup last generated in {generated["output"]} has build {generated["build"]}, and '
                 f'setup would now write {rewritten}.')
        command = _setup_command(root, None if generated is None else generated['output'])
        reason = f'{found} Run `{command}`, then reinstall or refresh Generative Driver in Codex and start a new chat.'
    else:
        state = 'unchecked'
        reason = ('No plugin build reached this process, so it was started from the command line, by an MCP-only '
                  'setup or by a plugin generated before builds were recorded. Call driver_doctor from Codex to '
                  'check the plugin in use.')
    reason = ' '.join(part for part in (reason, unreadable) if part) or None
    return {'current': current, 'launched_by': launched_by, 'generated': generated, 'state': state, 'reason': reason}


def doctor(home=None):
    """The installation report: core Python, the home, the worker runtime and optional tools, the optional packages,
    the USB serial ports and whether the Codex plugin in use is current.

    It is a diagnosis: it changes no configuration, asks the configured worker runtime only for its version, reads
    only the kit's own files and environment, never Codex's, and never fails because something optional is
    missing."""
    purposes = {"renode": "emulated STM32 benchmark", "java": "firmware analysis",
                "arm-none-eabi-gcc": "rebuilding STM32 firmware",
                "podman": "optional analysis containers"}
    dependencies = {"codex": _codex(home),
                    **{name: {"available": bool(shutil.which(name)), "path": shutil.which(name),
                              "required_for": purpose, "source": "PATH"}
                       for name, purpose in purposes.items()}}
    try:
        from serial.tools import list_ports
        ports = list_ports.comports()
        serial_ports = [{"device": p.device, "vendor_id": p.vid, "product_id": p.pid,
                         "serial_number": p.serial_number, "description": p.description}
                        for p in ports if p.vid is not None]
        ports_available = True
    except (ImportError, OSError):
        serial_ports, ports_available = [], False
    return {"core": {"ok": sys.version_info >= (3, 11), "python": platform.python_version(),
                     "platform": platform.platform(), "executable": sys.executable},
            "home": str(home_path(home)),
            "dependencies": dependencies, "extras": _extras(),
            "serial_ports": serial_ports, "serial_ports_available": ports_available,
            "plugin": _plugin_freshness(home)}

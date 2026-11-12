"""Non-invasive installation checks and explicit runtime configuration."""
import os
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile

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


def integration_assets(host, output, *, command=None, arguments=None, home=None):
    """Generate relocatable Codex assets pinned to this Python executable."""
    if host != "codex":
        raise ValueError("Choose the codex host")
    target = Path(output).expanduser().resolve()
    command = command or sys.executable
    arguments = ["-m", "generative_driver.mcp"] if arguments is None else arguments
    environment = {"GENERATIVE_DRIVER_HOME": str(home_path(home))}
    source = Path(__file__).parent / "resources/client/skills"
    target.mkdir(parents=True, exist_ok=True)
    plugin = target / "plugins/generative-driver"
    plugin.mkdir(parents=True, exist_ok=False)
    (plugin / ".codex-plugin").mkdir()
    manifest = {"name": "generative-driver", "version": "0.1.0", "license": "Apache-2.0",
                "description": "Generate device interfaces with checked firmware and experiment evidence.",
                "author": {"name": "Generative Driver contributors"},
                "skills": "./skills/", "mcpServers": "./.mcp.json",
                "interface": {"displayName": "Generative Driver",
                              "shortDescription": "Generate and verify device interfaces.",
                              "longDescription": "Run the six-stage workflow through a local background configurator.",
                              "developerName": "Generative Driver contributors", "category": "Developer Tools",
                              "capabilities": ["Read", "Write"],
                              "defaultPrompt": ["Check my setup and help me generate a device interface."]}}
    (plugin / ".codex-plugin/plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {"generative-driver": {
        "command": command, "args": arguments, "env": environment}}}, indent=2), encoding="utf-8")
    shutil.copytree(source, plugin / "skills")
    marketplace = target / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    with marketplace.open("x", encoding="utf-8") as stream:
        json.dump({"name": "generative-driver-local", "interface": {"displayName": "Generative Driver"},
                   "plugins": [{"name": "generative-driver", "source": {
                       "source": "local", "path": "./plugins/generative-driver"},
                       "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                       "category": "Developer Tools"}]}, stream, indent=2)
    return {"host": host, "plugin": str(plugin), "marketplace": str(marketplace),
            "marketplace_root": str(target), "command": command, "args": arguments}


def _codex_selection(config_path):
    """The Codex entry of the configuration at `config_path`, or None when there is none. A file a start could not
    read raises `OSError` or `ValueError`."""
    if not config_path.is_file():
        return None
    config = json.loads(config_path.read_text(encoding='utf-8'))
    executors = config.get('executors', {}) if isinstance(config, dict) else None
    if not isinstance(executors, dict):
        raise ValueError('Expected a JSON object holding an object of executors')
    return executors.get('codex')


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


def doctor(home=None):
    """The installation report: core Python, the home, the worker runtime and optional tools, the optional packages
    and the USB serial ports.

    It is a diagnosis: it changes no configuration, asks the configured worker runtime only for its version, and never
    fails because something optional is missing."""
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
            "serial_ports": serial_ports, "serial_ports_available": ports_available}

"""Non-invasive installation checks and explicit runtime configuration."""
import os
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile


def home_path(home=None):
    from .client import default_home
    return Path(home or default_home()).expanduser().resolve()


def configure(executor, command, model=None, provider=None, home=None, reasoning_effort=None):
    """Persist an explicit runtime selection without touching runtime credentials."""
    if executor not in ("codex", "goose") or not command or not all(isinstance(s, str) and s for s in command):
        raise ValueError("Choose codex/goose and a nonempty executable argument list")
    if reasoning_effort and executor != "codex":
        raise ValueError("reasoning_effort is supported by the Codex adapter")
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


def integration_assets(host, output, *, command=None, arguments=None):
    """Generate relocatable host assets; installed setup pins this Python executable."""
    if host not in ("codex", "goose"):
        raise ValueError("Choose codex or goose")
    target = Path(output).expanduser().resolve()
    command = command or sys.executable
    arguments = ["-m", "generative_driver.mcp"] if arguments is None else arguments
    source = Path(__file__).parent / "resources/client/skills"
    target.mkdir(parents=True, exist_ok=True)
    if host == "codex":
        plugin = target / "plugins/generative-driver"
        plugin.mkdir(parents=True, exist_ok=False)
        (plugin / ".codex-plugin").mkdir()
        manifest = {"name": "generative-driver", "version": "0.1.0", "license": "Apache-2.0",
                    "description": "Generate device interfaces with checked firmware and experiment evidence.",
                    "author": {"name": "Generative Driver contributors"},
                    "skills": "./skills/", "mcpServers": "./.mcp.json",
                    "interface": {"displayName": "Generative Driver",
                                  "shortDescription": "Generate and verify device interfaces.",
                                  "longDescription": "Run the seven-stage workflow through a local background configurator.",
                                  "developerName": "Generative Driver contributors", "category": "Developer Tools",
                                  "capabilities": ["Read", "Write"],
                                  "defaultPrompt": ["Check my setup and help me generate a device interface."]}}
        (plugin / ".codex-plugin/plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {"generative-driver": {
            "command": command, "args": arguments}}}, indent=2), encoding="utf-8")
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
    recipe = {"version": "1.0.0", "title": "Generative Driver", "description": "Generate an evidence-backed device interface.",
              "instructions": (source / "generative-driver/SKILL.md").read_text(encoding="utf-8"),
              "prompt": "Check the Generative Driver installation and help me start or reconnect to my workflow.",
              "extensions": [{"type": "stdio", "name": "generative-driver", "cmd": command,
                              "args": arguments, "timeout": 120, "description": "Local driver configurator"}]}
    path = target / "generative-driver.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(recipe, stream, indent=2)
    shutil.copytree(source, target / "skills")
    return {"host": host, "recipe": str(path), "command": command, "args": arguments}


def doctor(home=None):
    purposes = {"codex": "Codex workers", "goose": "Goose workers",
                "renode": "emulated STM32 benchmark", "java": "firmware analysis",
                "arm-none-eabi-gcc": "rebuilding STM32 firmware",
                "docker": "optional analysis containers"}
    dependencies = {name: {"available": bool(shutil.which(name)), "path": shutil.which(name),
                            "required_for": purpose, "source": "PATH"}
                    for name, purpose in purposes.items()}
    config_path = home_path(home) / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for name, selection in config.get("executors", {}).items():
            if name not in ("codex", "goose"):
                continue
            command = selection.get("command") or [name]
            executable = command[0] if isinstance(command, list) else command
            located = shutil.which(executable)
            dependencies[name].update(available=bool(located), path=located, source="configuration",
                                      model=selection.get("model"), provider=selection.get("provider"),
                                      version=selection.get("version"))
    return {"core": {"ok": sys.version_info >= (3, 11), "python": platform.python_version(),
                     "platform": platform.platform(), "executable": sys.executable},
            "home": str(home_path(home)),
            "dependencies": dependencies}

"""The scripted worker: a program the configurator launches in place of the Codex CLI, which does one stage's work by
a fixed script with no model (docs/features/kit-fixes/03-service-smoke.md). `generative-driver smoke` configures it
as the run's Codex command, so each stage launches it the way it would launch Codex.

It takes only what that launch gives a Codex worker: the arguments the configurator passes Codex (its workspace and
the stage's tool gateway among them), the prompt on its input, and the gateway, which it starts with the command and
arguments the launch names and speaks MCP to over standard input and output. It prints its stage report as the event
Codex prints for its final message. Its runs show that the kit's machinery works, never a model's ability.

Only the smoke's tests give it arguments of its own, before the Codex ones: `--idle STAGE` does no work in that stage
and reports it completed, and `--hang STAGE` waits without end in that stage with its gateway open."""
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

# A gateway call may take as long as a Codex worker's stage tool may.
_CALL_TIMEOUT = 600


# What the worker records of its launch in its workspace, for whoever checks the smoke.
_RECORD = '_scripted_worker.json'


def _launch(arguments):
    """What the Codex launch gave: its workspace, the stage gateway's command and arguments (None without one) and the
    tools it enables."""
    workspace, overrides = None, {}
    for flag, value in zip(arguments, arguments[1:]):
        if flag == '-C':
            workspace = Path(value)
        elif flag == '-c' and value.startswith('mcp_servers.stage.'):
            key, _, setting = value[len('mcp_servers.stage.'):].partition('=')
            overrides[key] = setting
    gateway = None
    if 'command' in overrides:
        gateway = {'command': json.loads(overrides['command']), 'args': json.loads(overrides.get('args', '[]'))}
    return workspace, gateway, json.loads(overrides.get('enabled_tools', '[]'))


def _task(prompt):
    """The assignment of a stage prompt: its JSON body after the instruction line. The interpret stage's prompt has
    none, and its workspace holds the brief instead."""
    try:
        task = json.loads(prompt.partition('\n')[2])
    except ValueError:
        return None
    return task if isinstance(task, dict) and isinstance(task.get('stage'), str) else None


def _sha256(path):
    from .benchmark_support.integrity import digest
    return digest(path)


def _artifact(path, kind):
    return {'path': str(path), 'sha256': _sha256(path), 'kind': kind}


class _StepFailed(Exception):
    """A step of the script that did not do its work."""


class _Gateway:
    """One MCP session with the stage's tool gateway, which calls only the tools the launch enables and keeps the
    `listed` and `called` tool names for the launch record."""

    def __init__(self, session, enabled, record):
        self.session, self.enabled, self.record = session, enabled, record

    async def list(self):
        self.record['listed_tools'] = [tool.name for tool in (await self.session.list_tools()).tools]

    async def call(self, name, arguments):
        """The tool's result, judged as the configurator judges it; a tool that did not succeed fails the step."""
        if name not in self.enabled:
            raise _StepFailed('the launch does not enable ' + name)
        self.record['called_tools'].append(name)
        reply = await self.session.call_tool(name, arguments)
        result = json.loads(reply.content[0].text)
        if result.get('_exit', 0) != 0 or not result.get('ok', result.get('available', True)):
            error = result.get('error')
            raise _StepFailed(name + ' failed: ' + str(result.get('error_message') or result.get('reason')
                                                      or (error.get('message') if isinstance(error, dict) else error)))
        return result


@contextlib.asynccontextmanager
async def _gateway(gateway, enabled, record):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async with stdio_client(StdioServerParameters(command=gateway['command'], args=gateway['args'])) as streams:
        async with ClientSession(*streams, read_timeout_seconds=_CALL_TIMEOUT) as session:
            await session.initialize()
            tools = _Gateway(session, enabled, record)
            await tools.list()
            yield tools


async def _acquire(tools, task, workspace):
    source = Path(task['inputs'][0])
    result = await tools.call('acquire_firmware_artifact', {
        'source_path': str(source), 'origin': 'provided_binary',
        'expected_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    return 'Imported the firmware stand-in', [_artifact(result['artifact'], 'firmware'),
                                               _artifact(result['provenance'], 'provenance')]


async def _probe(tools, task, workspace):
    model = next(Path(path) for path in task['inputs'] if (Path(path) / 'model.json').is_file())
    operations = json.loads((model / 'model.json').read_text(encoding='utf-8'))['operations']
    records = []
    for operation in operations:
        result = await tools.call('probe_run', {'model_dir': str(model), 'operation': operation, 'n': 1})
        records.append(_artifact(result['probe'], 'probe'))
    return 'Ran each operation of the model on the stand-in device', records


async def _emit(tools, task, workspace):
    model = next(Path(path) for path in task['inputs'] if (Path(path) / 'model.json').is_file())
    record = next(Path(path) for path in task['inputs'] if Path(path).suffix == '.json')
    result = await tools.call('emit_package', {'model_dir': str(model), 'probe_json': str(record)})
    return 'Built the package', [_artifact(result['package_dir'], 'package')]


async def _reuse(tools, task, workspace):
    package = next(Path(path) for path in task['inputs'] if (Path(path) / 'manifest.json').is_file())
    catalog = json.loads((package / 'mcp-tools.json').read_text(encoding='utf-8'))
    identity = json.loads((package / 'driver/contract.json').read_text(encoding='utf-8'))['identity']['operation']
    name = sorted(tool for tool, operation in catalog['operations'].items() if operation != identity)[0]
    await tools.call(name, {})
    return 'Called ' + name + ' through the generated catalog', []


_STAGE_WORK = {'acquire': _acquire, 'probe': _probe, 'ground': _probe, 'emit': _emit, 'reuse': _reuse}


def _interpret(workspace):
    from .toolkit import resources_root
    model = workspace / 'model.json'
    shutil.copyfile(resources_root() / 'smoke/model.json', model)
    return 'Wrote the stand-in model', [_artifact(model, 'model')]


async def _work(stage, task, workspace, gateway, enabled, record, hang=False):
    if gateway is None:
        raise _StepFailed('the launch names no tool gateway')
    async with _gateway(gateway, enabled, record) as tools:
        while hang:
            await asyncio.sleep(3600)
        try:
            return await _STAGE_WORK[stage](tools, task, workspace)
        except _StepFailed as failure:
            # Raised outside the session, which would otherwise wrap it in an exception group.
            return failure


def main(argv=None):
    given = list(sys.argv[1:] if argv is None else argv)
    # The Codex launch's arguments start at `exec`; the configured command's own come before them.
    start = given.index('exec') if 'exec' in given else len(given)
    seams, arguments = dict(zip(given[:start:2], given[1:start:2])), given[start:]
    workspace, gateway, enabled = _launch(arguments)
    from . import __file__ as package
    # Which package the worker found, and whether its launch gave it a PYTHONPATH to find it by.
    record = {'arguments': arguments, 'gateway': gateway, 'listed_tools': [], 'called_tools': [], 'package': package,
              'pythonpath': os.environ.get('PYTHONPATH')}
    # The configurator writes the prompt as UTF-8; a pipe's platform encoding (Windows' ANSI code page) is unreliable.
    task = _task(sys.stdin.buffer.read().decode('utf-8'))
    stage = 'interpret' if task is None else task['stage']
    try:
        if seams.get('--idle') == stage:
            done = 'Did no work, as a test of the smoke asked', []
        elif task is None:
            done = _interpret(workspace)
        else:
            done = asyncio.run(_work(stage, task, workspace, gateway, enabled, record, seams.get('--hang') == stage))
        if isinstance(done, _StepFailed):
            raise done
        summary, artifacts = done
    except _StepFailed as failure:
        sys.stderr.write('scripted worker: ' + stage + ': ' + str(failure) + '\n')
        raise SystemExit(1)
    except Exception:
        sys.stderr.write('scripted worker: ' + stage + ': ' + traceback.format_exc())
        raise SystemExit(1)
    finally:
        (workspace / _RECORD).write_text(json.dumps(record, indent=1), encoding='utf-8')
    report = {'status': 'completed', 'summary': 'Scripted ' + stage + ': ' + summary + '; no model ran',
              'artifacts': artifacts, 'checks': [], 'unresolved': []}
    # The event Codex prints for its final message, which the configurator reads for every runtime.
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}}),
          flush=True)


if __name__ == '__main__':
    main()

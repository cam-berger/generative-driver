"""The replay executor: a saved candidate's stage work, replayed through the stage gateway without a language model.

A candidate is one directory: replay.json (schema replay-candidate/1) and the files it names, which earlier workers
produced, such as an interpretation's model.json and replies.json or a probe's capabilities.json. replay.json lists each
stage's steps in order. `{"write": name}` copies the candidate file `name` into the stage workspace and never replaces a
file there. `{"call": tool, "arguments": {...}}` calls an assigned gateway tool; an argument `{"$context": key}` is the
assignment context's value and `{"$result": key}` that key of the latest earlier result of the stage that has it.

The worker reads nothing but the candidate and its assignment: no groundtruth, evaluator record or case source. It
reaches the toolchain and the device only through the gateway, which the configurator observes and records. Its results
are replay, never model runs."""
import argparse
import asyncio
import contextlib
import json
import shutil
import sys
from pathlib import Path

RUNTIME = 'replay'
SCHEMA = 'replay-candidate/1'
SCRIPT = 'replay.json'
STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
# The stages whose workers write their outputs themselves (a model and replies, a capability mapping). Every other stage
# acts through its tools, so a replay never places a package or a handoff behind the gateway.
WRITING = ('interpret', 'probe')
# A gateway call may take as long as a Codex worker's stage tool may.
CALL_TIMEOUT = 600
# Result paths a tool hands back, which the report names as artifacts.
RESULT_PATHS = ('artifact', 'provenance', 'probe', 'package_dir')


def load_candidate(directory):
    """The checked replay.json of the candidate directory: a script a replay can follow, naming files of the candidate
    only. Raises ValueError otherwise."""
    from .toolkit import resources_root
    root = Path(directory)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError('A replay candidate is an absolute directory path: ' + str(directory))
    if root.resolve().is_relative_to(resources_root().resolve()):
        raise ValueError('A replay candidate is saved worker output, never the installed case sources or groundtruth')
    script = root / SCRIPT
    if script.is_symlink() or not script.is_file():
        raise ValueError('A replay candidate holds ' + SCRIPT + ': ' + str(root))
    try:
        value = json.loads(script.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError) as error:
        raise ValueError(SCRIPT + ' is not UTF-8 JSON: ' + str(error)) from None
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA or set(value) - {'schema', 'description', 'stages'}
            or not isinstance(value.get('stages'), dict) or set(value['stages']) - set(STAGES)):
        raise ValueError(SCRIPT + ' must be a ' + SCHEMA + ' object whose stages name only ' + ', '.join(STAGES))
    for stage, steps in value['stages'].items():
        if not isinstance(steps, list):
            raise ValueError('The steps of ' + stage + ' are a list')
        for step in steps:
            if isinstance(step, dict) and set(step) == {'write'}:
                if stage not in WRITING:
                    raise ValueError('Only interpret and probe write files in a replay; ' + stage + ' acts through its tools')
                name = step['write']
                if (not isinstance(name, str) or name in ('', '.', '..', SCRIPT) or Path(name).name != name
                        or (root / name).is_symlink() or not (root / name).is_file()):
                    raise ValueError('A write step names a file of the candidate directory: ' + repr(name))
            elif not (isinstance(step, dict) and set(step) == {'call', 'arguments'} and isinstance(step['call'], str)
                      and step['call'] and isinstance(step['arguments'], dict)):
                raise ValueError('A replay step is {"write": name} or {"call": tool, "arguments": {...}}: ' + repr(step))
    return value


def candidate_config(config, *, bench=None):
    """The replay runtime's configuration: the candidate's absolute path and the sha256 of its files, checked against
    one recorded before. It names no command: the installed replay worker runs every replay. A candidate never lies in
    the bench home `bench`, which holds evaluator records."""
    from .configurator import digest
    if not isinstance(config, dict) or set(config) - {'runtime', 'candidate', 'candidate_sha256'}:
        raise ValueError('The replay executor takes only its saved candidate: executor_config {"candidate": <directory>}')
    if not isinstance(config.get('candidate'), str) or not config['candidate']:
        raise ValueError('The replay executor needs a saved candidate: executor_config {"candidate": <directory>}')
    root = Path(config['candidate'])
    if bench is not None and root.is_absolute() and root.resolve().is_relative_to(Path(bench).resolve()):
        raise ValueError('A replay candidate never lies in the bench home, which holds evaluator records: ' + str(root))
    load_candidate(root)
    sha256 = digest(root)
    if config.get('candidate_sha256') not in (None, sha256):
        raise ValueError('The replay candidate changed since its hash was recorded: ' + str(root))
    return {'runtime': RUNTIME, 'candidate': str(root.resolve()), 'candidate_sha256': sha256}


def execution_label(case_execution, runtime):
    """How a benchmark run executed: a candidate replayed on an emulated case is `replay-emulation`, since no model ran
    it, and never an actual-agent result."""
    return 'replay-emulation' if runtime == RUNTIME and case_execution == 'actual-agent-emulation' else case_execution


def _context(prompt):
    """The assignment context of a stage prompt: its JSON body after the instruction line, or none."""
    try:
        task = json.loads(prompt.partition('\n')[2])
    except ValueError:
        return {}
    context = task.get('context') if isinstance(task, dict) else None
    return context if isinstance(context, dict) else {}


def _resolve(value, context, results):
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ('$context', '$result'):
        (kind, key), = value.items()
        found = (context.get(key) if kind == '$context' else
                 next((result[key] for result in reversed(results) if key in result), None))
        if found is None:
            raise LookupError(kind + ' ' + str(key))
        return found
    if isinstance(value, dict):
        return {key: _resolve(item, context, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, context, results) for item in value]
    return value


async def _replay(root, steps, context, gateway, workspace):
    """Each step's (status, name, reason, produced paths), in order."""
    outcomes, results = [], []
    async with contextlib.AsyncExitStack() as stack:
        session, listed, unavailable = None, set(), 'No gateway tools are assigned to this stage'
        if gateway and any('call' in step for step in steps):
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            try:
                streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=gateway['command'], args=list(gateway.get('args') or []))))
                session = await stack.enter_async_context(ClientSession(*streams, read_timeout_seconds=CALL_TIMEOUT))
                await session.initialize()
                listed = {tool.name for tool in (await session.list_tools()).tools}
            except Exception as error:
                session, unavailable = None, 'The stage gateway is unavailable: ' + type(error).__name__ + ': ' + str(error)
        for step in steps:
            if 'write' in step:
                name, target = step['write'], workspace / step['write']
                if target.exists() or target.is_symlink():
                    outcomes.append(('fail', 'write ' + name, 'The workspace already holds ' + name
                                     + '; a replay never replaces a file there', []))
                else:
                    shutil.copyfile(root / name, target)
                    outcomes.append(('pass', 'write ' + name, None, [target]))
                continue
            name = step['call']
            if session is None or name not in listed:
                outcomes.append(('fail', name, unavailable if session is None else 'Tool is not assigned to this stage', []))
                continue
            try:
                arguments = _resolve(step['arguments'], context, results)
            except LookupError as missing:
                outcomes.append(('not_run', name, 'No value for ' + str(missing), []))
                continue
            try:
                reply = await session.call_tool(name, arguments)
                result = json.loads(reply.content[0].text)
                if not isinstance(result, dict):
                    raise ValueError('The gateway reply is not a JSON object')
            except Exception as error:
                outcomes.append(('fail', name, type(error).__name__ + ': ' + str(error), []))
                continue
            results.append(result)
            ok = result.get('_exit', 0) == 0 and bool(result.get('ok', result.get('available', True)))
            produced = [Path(result[key]) for key in RESULT_PATHS if isinstance(result.get(key), str)
                        and Path(result[key]).is_absolute() and Path(result[key]).resolve().is_relative_to(workspace)
                        and Path(result[key]).exists()]
            outcomes.append(('pass' if ok else 'fail', name,
                             None if ok else str(result.get('reason') or result.get('error') or 'The tool reported failure'),
                             produced))
    return outcomes


def replay_stage(candidate, candidate_sha256, stage, prompt, gateway, workspace):
    """Replay one stage of the candidate in `workspace` and return its stage report: a claim, as every report is."""
    from .configurator import digest
    workspace = Path(workspace).resolve()
    root = Path(candidate)
    try:
        script = load_candidate(root)
        if digest(root) != candidate_sha256:
            raise ValueError('The replay candidate changed since the run recorded its hash')
    except (OSError, ValueError) as error:
        return {'status': 'failed', 'summary': 'Replay candidate unusable; no language model ran', 'artifacts': [],
                'checks': [], 'unresolved': [str(error)]}
    steps = script['stages'].get(stage, [])
    outcomes = asyncio.run(_replay(root, steps, _context(prompt), gateway, workspace))
    produced = list(dict.fromkeys(path for outcome in outcomes for path in outcome[3]))
    passed = sum(outcome[0] == 'pass' for outcome in outcomes)
    return {'status': 'completed' if passed == len(outcomes) else 'failed',
            'summary': 'Replay of saved candidate ' + candidate_sha256[:12] + ': ' + str(passed) + ' of '
                       + str(len(outcomes)) + ' ' + stage + ' steps passed; no language model ran',
            'artifacts': [{'path': str(path), 'sha256': digest(path), 'kind': 'evidence'} for path in produced],
            'checks': [{'name': 'replay step ' + str(index) + ': ' + name, 'status': status,
                        'evidence': [reason] if reason else [str(path) for path in paths]}
                       for index, (status, name, reason, paths) in enumerate(outcomes, 1)],
            'unresolved': [name + ': ' + reason for status, name, reason, _ in outcomes if reason]}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='generative-driver replay worker')
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--candidate-sha256', required=True)
    parser.add_argument('--stage', required=True, choices=STAGES)
    parser.add_argument('--gateway', help='JSON {"command", "args"} of the stage gateway')
    args = parser.parse_args(argv)
    report = replay_stage(args.candidate, args.candidate_sha256, args.stage, sys.stdin.read(),
                          json.loads(args.gateway) if args.gateway else None, Path.cwd())
    # The event Codex prints for its final message, which the configurator reads for every runtime.
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(report)}}), flush=True)


if __name__ == '__main__':
    main()

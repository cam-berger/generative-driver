"""Configured external agent runtimes. No provider keys are read from host stores."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
import sys


REPORT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'status': {'type': 'string', 'enum': ['completed', 'blocked', 'needs_revision', 'failed']},
        'summary': {'type': 'string'},
        'artifacts': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'properties': {'path': {'type': 'string'}, 'sha256': {'type': 'string'}, 'kind': {'type': 'string'}},
            'required': ['path', 'sha256', 'kind']}},
        'checks': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'properties': {'name': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['pass', 'fail', 'not_run']},
                           'evidence': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['name', 'status', 'evidence']}},
        'unresolved': {'type': 'array', 'items': {'type': 'string'}},
    }, 'required': ['status', 'summary', 'artifacts', 'checks', 'unresolved'],
}

STAGE_BOUNDARY = ('Use only the assigned workspace, supplied inputs, and assigned tool gateway. '
    'Supplied diagnostic feedback, independent observations and sealed previous candidates from this same run '
    'are authorized task evidence. Use them for requested repairs and grounding; worker reports remain claims '
    'to check against the measurements. Do not discard supplied diagnostics as hidden evaluator data. '
    'Do not read personal skills, home-directory instructions, other repositories, other runs, evaluator '
    'passwords, plaintext oracle files or hidden final answers. '
    'Do not search outside the assigned workspace for task evidence or examples. '
    'Installed runtime libraries and explicitly supplied analysis executables may run normally. '
    'Use the assigned gateway for all device or emulator interactions; never bypass a denied tool through the shell. '
    'If a required input or capability is absent, report the blocker.')


@dataclass
class StageRequest:
    stage: str
    workspace: Path
    prompt: str
    budget_seconds: float = 10800
    report_required: bool = True
    allowed_tools: list = field(default_factory=list)
    gateway: dict | None = None
    approve_scoped_tools: bool = False


def _environment(config):
    # Preserve runtime-managed auth location; never inspect auth files. Discard
    # unrelated variables, particularly evaluator credentials and UI plugins.
    keep = {'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT', 'WINDIR',
            'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'LOGNAME',
            'CODEX_HOME', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    keep.update(config.get('env_keys', []))
    env = {k: v for k, v in os.environ.items() if k in keep and 'GROUNDTRUTH' not in k.upper() and 'EVALUATOR' not in k.upper()}
    return env


def _skill_overrides(work):
    roots = {Path.home()/'.agents/skills', Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'skills',
             Path('/etc/codex/skills')}
    roots.update(parent/'.agents/skills' for parent in (work,*work.parents))
    paths, visited = [], set()
    for root in roots:
        for directory, children, files in os.walk(root,followlinks=True):
            resolved=Path(directory).resolve()
            if resolved in visited:
                children[:]=[]
                continue
            visited.add(resolved)
            if 'SKILL.md' in files:
                # Codex matches the instruction-file path, not its containing directory.
                paths.append(str((resolved/'SKILL.md').resolve()))
    return 'skills.config=[' + ','.join('{path=' + json.dumps(p) + ',enabled=false}' for p in sorted(set(paths))) + ']'


def validate_report(value):
    """Validate the small report contract without trusting runtime schema promises."""
    def check(item, schema):
        kind = schema.get('type')
        if kind == 'object':
            if not isinstance(item,dict) or set(item) != set(schema['required']):
                return False
            return all(check(item[k],s) for k,s in schema['properties'].items())
        if kind == 'array':
            return isinstance(item,list) and all(check(v,schema['items']) for v in item)
        if kind == 'string':
            return isinstance(item,str) and ('enum' not in schema or item in schema['enum'])
        return False
    return check(value,REPORT_SCHEMA)


def _launch(request, config):
    runtime = config.get('runtime', 'codex')
    command = config.get('command') or [runtime]
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
        raise ValueError('executor command must be a nonempty argument array')
    command = list(command)
    work = Path(request.workspace).resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = _environment(config)
    model = config.get('model')
    if runtime == 'codex':
        if config.get('provider') not in (None,'openai'):
            raise ValueError('Codex adapter currently supports its built-in openai provider; custom provider definitions are not inherited')
        command += ['exec', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                    '--skip-git-repo-check', '--sandbox', 'workspace-write', '-C', str(work),
                    '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
                    '-c', 'features.plugins=false', '-c', 'features.memories=false',
                    '-c', 'agents.enabled=false', '-c', 'project_doc_max_bytes=0',
                    '-c', 'shell_environment_policy.inherit="core"', '-c', _skill_overrides(work),
                    '-c', 'developer_instructions=' + json.dumps(STAGE_BOUNDARY)]
        if request.report_required:
            schema = work / '_report_schema.json'
            schema.write_text(json.dumps(REPORT_SCHEMA), encoding='utf-8')
            command += ['--output-schema', str(schema)]
        if model:
            command += ['--model', model]
        if config.get('provider'):
            command += ['-c', 'model_provider=' + json.dumps(config['provider'])]
        if config.get('reasoning_effort'):
            command += ['-c', 'model_reasoning_effort=' + json.dumps(config['reasoning_effort'])]
        if request.gateway:
            gateway = request.gateway
            for name, value in (('command', gateway['command']), ('args', gateway.get('args', []))):
                command += ['-c', 'mcp_servers.stage.' + name + '=' + json.dumps(value)]
            command += ['-c', 'mcp_servers.stage.tool_timeout_sec=600']
            command += ['-c', 'mcp_servers.stage.enabled_tools=' + json.dumps(request.allowed_tools)]
            if request.approve_scoped_tools:
                # CLI dotted override keys split on dots without parsing quoted
                # TOML keys. Put tool names inside the parsed table value.
                policies=','.join(json.dumps(tool)+'={approval_mode="approve"}' for tool in request.allowed_tools)
                command += ['-c', 'mcp_servers.stage.tools={' + policies + '}']
        command += ['-']
        stdin = request.prompt
    elif runtime == 'goose':
        # Explicit recipe extensions replace inherited extension selection.
        recipe = {'version': '1.0.0', 'title': 'Generative Driver ' + request.stage,
                  'description': 'One configurator-owned stage', 'prompt': request.prompt,
                  'instructions': STAGE_BOUNDARY,
                  'extensions': [{'type': 'builtin', 'name': 'developer'}],
                  'settings': {'max_turns': int(config.get('max_turns', 100))}}
        if request.report_required:
            recipe['response'] = {'json_schema': REPORT_SCHEMA}
        if model:
            recipe['settings']['goose_model'] = model
        if config.get('provider'):
            recipe['settings']['goose_provider'] = config['provider']
        if request.gateway:
            recipe['extensions'].append({'type': 'stdio', 'name': 'stage', 'cmd': request.gateway['command'],
                                         'args': request.gateway.get('args', []), 'timeout': 600,
                                         'available_tools': request.allowed_tools})
        recipe_path = work / '_stage_recipe.json'
        recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
        command += ['run', '--no-profile', '--no-session', '--recipe', str(recipe_path), '--output-format', 'stream-json']
        env['GOOSE_DISABLE_SESSION_NAMING'] = 'true'
        env['CONTEXT_FILE_NAMES'] = '[]'
        stdin = ''
    else:
        raise ValueError('executor must be codex or goose')
    return command, env, stdin


def _terminate(proc):
    try:
        if os.name == 'nt':
            if proc.poll() is None:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    finally:
        if os.name != 'nt':
            # The group can outlive its leader and keep inherited output pipes
            # open. Cancellation owns the entire stage process group.
            try:
                os.killpg(proc.pid,signal.SIGKILL)
            except OSError:
                pass


def execute(request, config, cancel_event=None, on_event=None):
    """Execute exactly one fresh external session and retain its actual output."""
    command, env, prompt = _launch(request, config)
    work = Path(request.workspace).resolve()
    transcript = work / '_agent_events.jsonl'
    stderr_path = work / '_agent_stderr.txt'
    runtime = config.get('runtime', 'codex')
    result = {'runtime': runtime, 'runtime_version':config.get('version'),
              'provider': config.get('provider') or ('openai' if runtime=='codex' else None), 'model': config.get('model'),
              'settings': {k: config[k] for k in ('reasoning_effort', 'max_turns') if k in config},
              'report': None, 'usage': None, 'transcript': str(transcript), 'stderr': str(stderr_path),
              'boundary': 'fresh session; explicit workspace and configured tools; filesystem read and direct-device isolation unverified'}
    started = time.monotonic()
    if os.name == 'nt':
        command = [sys.executable,str(Path(__file__).with_name('_windows_worker.py')),*command]
    launch_options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    try:
        proc = subprocess.Popen(command, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', **launch_options)
    except OSError as exc:
        return {**result, 'status': 'blocked', 'reason': 'Cannot start configured runtime: ' + str(exc), 'elapsed_seconds': 0}
    messages = queue.Queue()
    def reader(stream, channel):
        for line in stream:
            messages.put((channel, line))
        messages.put((channel, None))
    for channel, stream in (('out', proc.stdout), ('err', proc.stderr)):
        threading.Thread(target=reader, args=(stream, channel), daemon=True).start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    text_parts = []
    done = set()
    outcome = None
    launch_error = None
    size = 0
    with transcript.open('w', encoding='utf-8') as transcript_file, stderr_path.open('w', encoding='utf-8') as stderr_file:
        while len(done) < 2 or proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                outcome = ('cancelled', 'Run cancelled; external worker stopped')
                _terminate(proc)
            elif time.monotonic() - started > request.budget_seconds:
                outcome = ('blocked', 'Stage exhausted the remaining run time budget')
                _terminate(proc)
            try:
                channel, line = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                done.add(channel)
                continue
            size += len(line)
            if size > 32 * 1024 * 1024:
                outcome = ('failed', 'External worker output exceeded 32 MiB')
                _terminate(proc)
                continue
            if channel == 'err':
                stderr_file.write(line)
                stderr_file.flush()
                continue
            try:
                event = json.loads(line)
            except ValueError:
                event = {'type': 'unparsed_stdout', 'text': line.rstrip()}
            transcript_file.write(json.dumps(event) + '\n')
            transcript_file.flush()
            if on_event:
                on_event(event)
            if event.get('type') == 'generative_driver.runtime_start_failed' and isinstance(event.get('error'), str):
                launch_error = event['error']
            if event.get('type') == 'turn.completed' and isinstance(event.get('usage'), dict):
                usage = event['usage']
                result['usage'] = {key: usage.get(key) for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')}
            item = event.get('item', {})
            if item.get('type') == 'agent_message' and event.get('type') == 'item.completed':
                text_parts.append(item.get('text', ''))
            # Goose versions expose either a final message or a completed result.
            if runtime == 'goose':
                if event.get('type') == 'complete' and type(event.get('total_tokens')) is int:
                    # Older Goose builds emitted only a last-context total. Newer
                    # builds expose accumulated input/output fields explicitly.
                    if type(event.get('input_tokens')) is int and type(event.get('output_tokens')) is int:
                        result['usage']={'input_tokens':event['input_tokens'],'output_tokens':event['output_tokens'],
                            'cached_input_tokens':event.get('cache_read_input_tokens'),'cache_write_input_tokens':event.get('cache_write_input_tokens'),
                            'reasoning_output_tokens':None,'total_tokens':event['total_tokens']}
                        result['reported_cost_usd']=event.get('cost_usd')
                    else:
                        result['runtime_usage'] = {'reported_total_tokens':event['total_tokens'], 'cumulative':None}
                if isinstance(event.get('response'), str):
                    text_parts.append(event['response'])
                if isinstance(event.get('message'), dict):
                    for block in event['message'].get('content', []):
                        if block.get('type') == 'text':
                            text_parts.append(block.get('text', ''))
                if event.get('status') in ('completed', 'blocked', 'failed', 'needs_revision') and 'artifacts' in event:
                    result['report'] = event
    result.update(exit_code=proc.wait(), elapsed_seconds=time.monotonic() - started)
    proc.stdout.close()
    proc.stderr.close()
    if outcome:
        return {**result, 'status': outcome[0], 'reason': outcome[1]}
    if launch_error is not None:
        return {**result, 'status': 'blocked', 'reason': 'Cannot start configured runtime: ' + launch_error}
    for text in reversed(text_parts):
        try:
            candidate = json.loads(text)
            if isinstance(candidate, dict) and candidate.get('status') in ('completed', 'blocked', 'failed', 'needs_revision'):
                result['report'] = candidate
                break
        except ValueError:
            pass
    result['final_text'] = text_parts[-1] if text_parts else ''
    if result['exit_code']:
        return {**result, 'status': 'failed', 'reason': 'Configured runtime exited with an error; inspect retained stderr'}
    if request.report_required and result['report'] is None:
        return {**result, 'status': 'failed', 'reason': 'Runtime did not produce a structured stage report'}
    if request.report_required and not validate_report(result['report']):
        return {**result, 'status':'failed','reason':'Runtime report does not match the stage report schema'}
    return {**result, 'status': 'completed'}

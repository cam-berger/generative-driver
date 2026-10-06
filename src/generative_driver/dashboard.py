"""Local run monitor; the configurator remains the owner of all execution."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from importlib.resources import files
from pathlib import Path
import secrets
import threading
import time
import webbrowser
from urllib.parse import urlsplit

from .client import call, default_home
from .configurator import STAGES


def project_run(result, *, now=None, assignment_id=None):
    """Expose display fields and accepted gates for the current revision only."""
    now = time.time() if now is None else now
    progress = result.get('progress') or {}
    revision = progress.get('revision', 0)
    accepted = {h['stage'] for h in result.get('accepted_handoffs', [])
                if h.get('revision', 0) == (0 if h['stage'] == 'acquire' else revision)}
    states = []
    for stage in STAGES:
        state = 'accepted' if stage in accepted else 'pending'
        if stage == result.get('stage') and state != 'accepted':
            state = result['status'] if result['status'] != 'queued' else 'pending'
            if state == 'running' and any(r['stage'] == stage and r.get('revision', 0) == revision
                                          and (assignment_id is None or r.get('assignment_id') == assignment_id)
                                          and r['status'] == 'completed' for r in result.get('worker_reports', [])):
                state = 'checking'
        states.append({'name': stage, 'status': state})
    end = result['updated'] if result['status'] in ('completed', 'blocked', 'failed', 'cancelled') else now
    return {'ok': True, 'run_id': result['run_id'], 'status': result['status'],
            'stage': result.get('stage'), 'reason': result.get('reason'),
            'uncertain_effect': result.get('uncertain_effect', False),
            'stopping': result.get('stopping', False), 'stages': states,
            'accepted_count': len(accepted), 'revision': revision,
            'repairs': progress.get('repairs', 0),
            'elapsed_seconds': max(0, end - result['created']), 'observed_at': now,
            'case': result.get('case'),
            'agent': {key: result.get('agent', {}).get(key) for key in ('runtime', 'model')},
            'reasoning_effort': result.get('agent', {}).get('settings', {}).get('reasoning_effort')}


def _event_line(event):
    data, kind = event['data'], event['kind']
    stage = data.get('stage') or ''
    if kind in ('tool.started', 'tool.finished'):
        outcome = 'started' if kind == 'tool.started' else ('finished' if data.get('ok') else 'failed')
        message = f"{stage}: {data.get('name', 'tool')} {outcome}"
    elif kind == 'run.revision':
        message = f"Repair {data.get('repairs', data.get('revision', 0))}: {data.get('reason', 'revision requested')}"
    elif kind.startswith('stage.') or kind == 'worker.finished':
        message = f"{stage}: {kind.replace('.', ' ')}"
    else:
        message = kind.replace('.', ' ') + (': ' + str(data['reason']) if data.get('reason') else '')
    return {'id': event['id'], 'time': event['time'], 'message': message[:1000]}


def read_activity(workspace):
    """Read the last 64 KiB of the assigned worker's structured progress log."""
    empty = {'entries': [], 'age_seconds': None}
    root = Path(workspace)
    path = root / '_agent_events.jsonl'
    if root.resolve() != root or path.is_symlink():
        return empty
    try:
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 65536))
            tail = stream.read(65536)
        if size > 65536:
            tail = tail.partition(b'\n')[2]
        modified = path.stat().st_mtime
    except OSError:
        return empty
    entries = []
    for line in tail.decode('utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get('message')
        texts = [block['text'] for block in message.get('content', [])
                 if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)
                 ] if isinstance(message, dict) and message.get('role') == 'assistant' else []
        if isinstance(event.get('response'), str):
            texts.append(event['response'])
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        completed = event.get('type') == 'item.completed'
        if item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            texts.append(item['text'])
        for text in texts:
            try:
                report = json.loads(text)
                if isinstance(report, dict) and 'status' in report:
                    text = str(report.get('summary', report['status']))
            except ValueError:
                pass
            entries.append(text[:1000])
        if item.get('type') == 'command_execution':
            entries.append(f"Shell command finished (exit {item.get('exit_code')})" if completed
                           else 'Shell command started')
        elif item.get('type') == 'mcp_tool_call':
            entries.append(f"Tool {item.get('tool', 'call')} {'finished' if completed else 'started'}")
        elif item.get('type') == 'file_change' and completed:
            entries.append('Worker files updated')
    return {'entries': entries[-12:], 'age_seconds': max(0, time.time() - modified)}


class RunReader:
    def __init__(self, run_id, home):
        self.run_id = run_id
        self.home = Path(home or default_home()).expanduser().resolve()
        self.cursor = 0
        self.logs = deque(maxlen=200)
        self.assignment = None
        self.lock = threading.Lock()

    def snapshot(self):
        if not self.lock.acquire(blocking=False):
            return {'ok': False, 'reason': 'Waiting for a fresh configurator update.', 'observed_at': time.time()}
        try:
            if not (self.home / 'service.json').is_file():
                return {'ok': False, 'reason': 'Configurator is not reachable. Start the service, then reconnect.',
                        'observed_at': time.time()}
            try:
                result = call('result', {'run_id': self.run_id}, home=self.home, autostart=False, read_timeout=2)
                if not result.get('ok'):
                    return {'ok': False, 'reason': result.get('reason', 'Run is unavailable'), 'observed_at': time.time()}
                # Bounded catch-up also supports attaching midway through a long run.
                for _ in range(4):
                    batch = call('events', {'run_id': self.run_id, 'after': self.cursor},
                                 home=self.home, autostart=False, read_timeout=2)
                    if not batch.get('ok'):
                        return {'ok': False, 'reason': batch.get('reason', 'Event log is unavailable'),
                                'observed_at': time.time()}
                    for event in batch['events']:
                        self.logs.append(_event_line(event))
                        if event['kind'] == 'stage.assigned':
                            self.assignment = event['data']
                    self.cursor = batch['cursor']
                    if len(batch['events']) < 500:
                        break
                activity = {'entries': [], 'age_seconds': None}
                if (result['status'] == 'running' and self.assignment
                        and self.assignment['stage'] == result['stage']
                        and self.assignment.get('revision', 0) == (result.get('progress') or {}).get('revision', 0)):
                    activity = read_activity(self.assignment['workspace'])
                return {**project_run(result, assignment_id=self.assignment['id'] if self.assignment else None),
                        'logs': list(self.logs), 'activity': activity}
            except (OSError, EOFError, ValueError, TimeoutError):
                return {'ok': False, 'reason': 'Connection lost. Waiting for the configurator.',
                        'observed_at': time.time()}
        finally:
            self.lock.release()


def create_server(run_id, *, home=None, port=0):
    """Create a loopback monitor with an unguessable URL and GET-only routes."""
    reader = RunReader(run_id, home)
    prefix = '/' + secrets.token_urlsafe(24) + '/'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Keep the private URL out of request logs.

        def do_GET(self):
            origins = {f'http://127.0.0.1:{self.server.server_port}',
                       f'http://localhost:{self.server.server_port}'}
            if 'http://' + self.headers.get('Host', '') not in origins or (
                    self.headers.get('Origin') and self.headers['Origin'] not in origins):
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            assets = {'': ('index.html', 'text/html'), 'app.js': ('app.js', 'text/javascript'),
                      'style.css': ('style.css', 'text/css')}
            if path == prefix + 'api/run':
                body = json.dumps(reader.snapshot(), ensure_ascii=False, allow_nan=False).encode('utf-8')
                content_type = 'application/json'
            elif path.startswith(prefix) and path[len(prefix):] in assets:
                name, content_type = assets[path[len(prefix):]]
                body = files('generative_driver').joinpath('resources', 'dashboard', name).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(405)

        do_PUT = do_DELETE = do_PATCH = do_POST

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.url = f'http://127.0.0.1:{server.server_port}' + prefix
    return server


def watch(run_id, *, home=None, port=0, open_browser=True):
    server = create_server(run_id, home=home, port=port)
    print(server.url, flush=True)
    try:
        if open_browser:
            webbrowser.open(server.url)
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

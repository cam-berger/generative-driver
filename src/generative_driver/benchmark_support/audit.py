"""The transcript audit: the second layer behind an unmounted answer vault.

Reporting code, outside the grader's identity: it reads the Codex transcripts a run's workers left and grades nothing.
The audit sees only what a command's text shows; it cannot see a path a command builds or finds for itself."""
import json
from pathlib import Path

TRANSCRIPT = '_agent_events.jsonl'


def forbidden_paths(forbidden: tuple[str, ...]) -> tuple[str, ...]:
    """The paths an audit looks for, as given. An empty path is an error: it is in every command and would flag them all."""
    forbidden = tuple(forbidden)
    if any(not isinstance(path, str) or not path.strip() for path in forbidden):
        raise ValueError('An audit path must not be empty')
    return forbidden


def _normal(text: str) -> str:
    """A path or command as it is compared: backslashes as slashes, case folded, as on Windows."""
    return text.replace('\\', '/').casefold()


def _completed_command(event) -> str | None:
    """The command of a completed command execution, a list joined with spaces; None for any other record."""
    if not isinstance(event, dict) or event.get('type') != 'item.completed':
        return None
    item = event.get('item')
    if not isinstance(item, dict) or item.get('type') != 'command_execution':
        return None
    command = item.get('command')
    if isinstance(command, list):
        return ' '.join(map(str, command))
    return command if isinstance(command, str) else None


def audit_commands(run_dir: Path, forbidden: tuple[str, ...]) -> list[dict]:
    """Every command a worker completed that names a forbidden path, as a finding: the transcript's path in the run and
    the command's first 300 characters. Every `_agent_events.jsonl` under `run_dir` is read (the stage workspaces, and
    the preparation and tool runs of interpretation), in the order of their paths and then of their lines. A line that
    is not a completed command is skipped, whether or not it is JSON."""
    run_dir = Path(run_dir)
    paths = [_normal(path) for path in forbidden_paths(forbidden)]
    findings = []
    for transcript in sorted(run_dir.rglob(TRANSCRIPT)):
        if not transcript.is_file():
            # Not a transcript, and opening a named pipe would wait for a writer.
            continue
        with transcript.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    command = _completed_command(json.loads(line))
                except ValueError:
                    continue
                if command is not None and any(path in _normal(command) for path in paths):
                    findings.append({'transcript': transcript.relative_to(run_dir).as_posix(), 'command': command[:300]})
    return findings

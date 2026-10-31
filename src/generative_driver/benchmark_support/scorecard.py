"""The metrics of one trial, computed afterwards from the records its run kept.

Reporting code, outside the grader's identity: it measures what a run recorded and grades nothing. The system under test
records nothing for it (spec decision 12), so a stored run can be measured again. Its sources are the configurator's
database, opened read-only and never through a `Controller`, which writes on start; the Codex transcripts under the run's
directory; and, when the trial's directory is given, the device provider's journal.

Counts come from events and transcripts, so a stage nothing happened in counts zero. A time or token figure comes from
the workers' reports and is None unless every attempt of the stage left one: a worker that died leaves no report, and
a sum over the others would pass for the whole. A transcript is read only where a run keeps it: an interpretation attempt
that stopped before its outputs were collected leaves its transcript in a workspace outside the run."""
import bisect
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

from .audit import TRANSCRIPT
from .case_assets import STAGES

SCHEMA = 'benchmark-trial-metrics/1'
# The suffix of the id of a run's final verdict in the configurator's `verdicts` table.
FINAL = ':final'
ACTIONS = ('command_execution', 'mcp_tool_call', 'file_change')
DEVICE = ('exchanges', 'writes', 'refusals', 'resets')
# The transcript entries that are exchanges with the device.
EXCHANGES = ('exchange', 'usb_control')
# Each token counter and its key in a worker's usage.
COUNTERS = (('input', 'input_tokens'), ('cached_input', 'cached_input_tokens'), ('output', 'output_tokens'),
            ('reasoning_output', 'reasoning_output_tokens'))


def _decoded(payload):
    """A stored payload as a dict; JSON that is anything else is taken as empty."""
    value = json.loads(payload)
    return value if isinstance(value, dict) else {}


def _records(home: Path, run_id: str):
    """A run's rows, decoded, as the configurator's `result` assembles them: its events as (seq, kind, data, created),
    its worker reports as (stage, payload), its accepted handoffs, its final verdict and its progress. The verdict and
    progress are None when the run saved none. A home with no database, or a database that holds no such run, is an
    error: a run that was never recorded is not one that did nothing. Even a read-only connection to a WAL-mode database
    creates SQLite's `-shm` file beside it, so a home on media that cannot be written cannot be read."""
    database = home / 'runs.sqlite3'
    if not database.is_file():
        raise FileNotFoundError('No configurator database at ' + str(database))
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone() is None:
            raise ValueError('Unknown run_id')
        events = [(seq, kind, _decoded(payload), created) for seq, kind, payload, created in
                  db.execute('SELECT seq,kind,payload,created FROM events WHERE run_id=? ORDER BY seq', (run_id,))]
        reports = [(stage, _decoded(payload)) for stage, payload in
                   db.execute('SELECT stage,payload FROM reports WHERE run_id=? ORDER BY created', (run_id,))]
        handoffs = [_decoded(payload) for (payload,) in
                    db.execute('SELECT payload FROM handoffs WHERE run_id=? ORDER BY created,rowid', (run_id,))]
        final = db.execute('SELECT payload FROM verdicts WHERE id=?', (run_id + FINAL,)).fetchone()
        progress = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
    return (events, reports, handoffs, _decoded(final[0]) if final else None, _decoded(progress[0]) if progress else None)


def _entries(value):
    """The transcript entries inside a tool result: those of its own `transcript`, of each `results[]` item and of
    anything else it nests, such as the preconditions a package call checked."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'transcript' and isinstance(item, list):
                yield from (entry for entry in item if isinstance(entry, dict))
            else:
                yield from _entries(item)
    elif isinstance(value, list):
        for item in value:
            yield from _entries(item)


def _exchanges(result):
    """How many exchanges with the device a tool result's transcripts show completed, and how many entries show the
    device rejecting its reply. A rejected reply is also a completed exchange."""
    entries = list(_entries(result))
    return (sum(entry.get('op') in EXCHANGES and entry.get('status') == 'completed' for entry in entries),
            sum(bool(entry.get('device_rejected')) for entry in entries))


def _actions(run_dir: Path, stage: str) -> dict:
    """The actions a stage's workers completed, by type, counted in their Codex transcripts: those of the stage's work
    directories and, for interpretation, those frozen into its preparation and tool runs. A worker that left no
    transcript adds nothing, and a line that is not a completed action is skipped, whether or not it is JSON."""
    places = [f'work/{stage}/*/{TRANSCRIPT}']
    if stage == 'interpret':
        places += [f'preparation/*/interpret/attempt*/{TRANSCRIPT}', f'benchmark/tools/interpret/attempt*/{TRANSCRIPT}']
    counts = dict.fromkeys(ACTIONS, 0)
    for place in places:
        for transcript in run_dir.glob(place):
            if not transcript.is_file():
                continue
            with transcript.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    item = event.get('item') if isinstance(event, dict) and event.get('type') == 'item.completed' else None
                    if isinstance(item, dict) and item.get('type') in counts:
                        counts[item['type']] += 1
    return counts


def _wall(begun, ended):
    """Seconds from a stage's first assignment to the last time it was accepted or skipped, or None when it was never
    assigned or nothing ended it after that."""
    ended = [time for time in ended if begun is not None and time >= begun]
    return max(ended) - begun if ended else None


def _reported(rows, attempts):
    """Whether every attempt of a stage left a report: there is at least one, and as many as there were attempts."""
    return bool(rows) and len(rows) >= attempts


def _worker_seconds(rows, attempts):
    """The seconds the stage's workers ran, from their reports, or None unless every attempt reported them."""
    seconds = [row.get('elapsed_seconds') for row in rows]
    known = _reported(rows, attempts) and all(type(value) in (int, float) and math.isfinite(value) for value in seconds)
    return sum(seconds) if known else None


def _tokens(rows, attempts):
    """The tokens the stage's workers used, summed over their reports, with `total` the input plus the output (cached
    input and reasoning output are subsets of those). Every counter is None unless every attempt reported usage, that is
    input and output tokens, and one the runtime does not count is None too."""
    usages = [row.get('usage') for row in rows]
    counted = lambda key: all(isinstance(usage, dict) and type(usage.get(key)) is int for usage in usages)
    reported = _reported(rows, attempts) and counted('input_tokens') and counted('output_tokens')
    tokens = {name: sum(usage[key] for usage in usages) if reported and counted(key) else None for name, key in COUNTERS}
    return {**tokens, 'total': tokens['input'] + tokens['output'] if reported else None}


def _session(receipt):
    """The session a recovery receipt says replaced the emulator, or None."""
    session = receipt.get('new_session_id') if isinstance(receipt, dict) else None
    return session if isinstance(session, str) and session else None


def _new_reset(session, counted):
    """Whether a replacement is one not counted yet, and note it in `counted`. The configurator's `emulator.recovered`
    event and the provider's journal row of the same replacement name the same new session; one that names none cannot
    be matched and counts alone."""
    if session in counted:
        return False
    if session is not None:
        counted.add(session)
    return True


def _replacements(trial_dir):
    """The replacements the device provider's journal holds, as (time, new session or None): its `replaced` rows that
    have a time. A trial with no journal, a line that is not JSON and a row of another kind add nothing."""
    path = None if trial_dir is None else Path(trial_dir) / 'device' / 'journal.jsonl'
    if path is None or not path.is_file():
        return
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get('kind') == 'replaced' and type(row.get('time')) in (int, float):
                proof = row.get('proof')
                yield row['time'], _session(proof.get('receipt') if isinstance(proof, dict) else None)


def _active_at(active, last, moment):
    """The stage active at a time: the one assigned or skipped most recently by then, until the run's last event at
    `last`. Before the run's first assignment and after its last event no stage is active."""
    index = bisect.bisect_right([time for time, _ in active], moment) - 1
    return active[index][1] if index >= 0 and moment <= last else None


def _claim(handoffs, reports, final):
    """The fresh-reuse worker's claim of success beside the mission result of the evaluator's verdict, which the claim
    never mixes with: the worker's report and the verdict stay as each recorded them.

    `claimed_success` is true when the report of the latest accepted reuse handoff says `completed` and every one of
    its checks `pass` (a report with no list of checks has none, as the configurator's notes read it), false for any
    other report, and None when there is none: reuse did not run, ran no worker or got no report back. `mission_passed`
    is the verdict's `equivalence.mission.passed`, None when the run has no verdict or its verdict no mission result.
    `agrees` is None unless both are known."""
    reuse = [handoff.get('assignment_id') for handoff in handoffs if handoff.get('stage') == 'reuse']
    saved = {payload.get('assignment_id'): payload for _, payload in reports}
    report = saved.get(reuse[-1], {}).get('report') if reuse else None
    claimed = None
    if isinstance(report, dict) and isinstance(report.get('status'), str):
        checks = report.get('checks') if isinstance(report.get('checks'), list) else []
        claimed = report['status'] == 'completed' and all(isinstance(check, dict) and check.get('status') == 'pass'
                                                          for check in checks)
    equivalence = (final or {}).get('equivalence')
    mission = equivalence.get('mission') if isinstance(equivalence, dict) else None
    passed = mission.get('passed') if isinstance(mission, dict) else None
    passed = passed if type(passed) is bool else None
    return {'claimed_success': claimed, 'mission_passed': passed,
            'agrees': None if claimed is None or passed is None else claimed == passed}


def trial_metrics(home: Path, run_id: str, *, trial_dir: Path | None = None) -> dict:
    """The metrics of one run, schema `benchmark-trial-metrics/1`: its `repairs`, its `claim` and, for each stage in
    order:

    - `attempts`: the `stage.assigned` events. `wall_seconds`: from the first of them to the last `stage.accepted` or
      `stage.skipped` event. `worker_seconds` and `tokens`: the workers' reports summed.
    - `managed_calls`: the `tool.started` events, by tool. `worker_actions`: the completed actions in the workers'
      transcripts, by type.
    - `device`: the `exchanges` that completed in the transcripts of tool results; the `writes` dispatched (effectful
      `tool.started` events); the `refusals` (`tool.refused` events, calls the runtime refused before it opened the
      device, and replies the device rejected); and the `resets`, each emulator replacement after an uncertain write,
      from the configurator's `emulator.recovered` events and, when `trial_dir` is given, the provider's journal. A
      replacement in both counts once, and a journal row belongs to the stage active at its time.

    A call is its `tool.started` event and its `tool.finished` event, paired by `dispatch_id`; one that never finished,
    as when its tool raised, still counts as a call, and one finished twice counts once.

    Raises FileNotFoundError for a `home` with no database and ValueError for a `run_id` the database does not hold."""
    home = Path(home)
    events, reports, handoffs, final, progress = _records(home, run_id)
    attempts, begun, ended = dict.fromkeys(STAGES, 0), {}, {stage: [] for stage in STAGES}
    managed, device = {stage: {} for stage in STAGES}, {stage: dict.fromkeys(DEVICE, 0) for stage in STAGES}
    active, counted = [], set()  # (time, stage) of each assignment or skip; the sessions already counted as resets
    calls = {}
    for seq, kind, data, created in events:
        stage = data.get('stage')
        if stage in STAGES:
            if kind in ('stage.assigned', 'stage.skipped'):
                active.append((created, stage))
            if kind == 'stage.assigned':
                attempts[stage] += 1
                begun.setdefault(stage, created)
            elif kind in ('stage.accepted', 'stage.skipped'):
                ended[stage].append(created)
            elif kind == 'tool.refused':
                device[stage]['refusals'] += 1
        if kind == 'emulator.recovered':
            if active and _new_reset(_session(data.get('receipt')), counted):
                device[active[-1][1]]['resets'] += 1
        elif kind == 'tool.started':
            calls.setdefault(str(seq), {})['started'] = data
        elif kind == 'tool.finished':
            calls.setdefault(str(data.get('dispatch_id')), {})['finished'] = data
    for call in calls.values():
        started, finished = call.get('started'), call.get('finished')
        stage = (finished if started is None else started).get('stage')
        if stage not in STAGES:
            continue
        if started is not None:
            if isinstance(started.get('name'), str):
                managed[stage][started['name']] = managed[stage].get(started['name'], 0) + 1
            device[stage]['writes'] += bool(started.get('effectful'))
        if finished is not None:
            exchanged, rejected = _exchanges(finished.get('result'))
            device[stage]['exchanges'] += exchanged
            device[stage]['refusals'] += rejected + bool(finished.get('refused_before_open'))
    last = max((created for _, _, _, created in events), default=None)
    for moment, session in _replacements(trial_dir):
        stage = _active_at(active, last, moment)
        if stage and _new_reset(session, counted):
            device[stage]['resets'] += 1
    stages = {}
    for stage in STAGES:
        rows = [payload for name, payload in reports if name == stage]
        stages[stage] = {'attempts': attempts[stage], 'wall_seconds': _wall(begun.get(stage), ended[stage]),
                         'worker_seconds': _worker_seconds(rows, attempts[stage]),
                         'managed_calls': dict(sorted(managed[stage].items())),
                         'worker_actions': _actions(home / 'runs' / run_id, stage), 'device': device[stage],
                         'tokens': _tokens(rows, attempts[stage])}
    repairs = (progress or {}).get('repairs')
    return {'schema': SCHEMA, 'run_id': run_id, 'repairs': repairs if type(repairs) is int else None,
            'claim': _claim(handoffs, reports, final), 'stages': stages}

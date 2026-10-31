"""The metrics of one trial, computed afterwards from the records its run kept, and the scorecard that adds a suite's
trials up.

Reporting code, outside the grader's identity: it measures what a run recorded and grades nothing. The system under test
records nothing for it (spec decision 12), so a stored run can be measured again. Its sources are the configurator's
database, opened read-only and never through a `Controller`, which writes on start; the Codex transcripts under the run's
directory; and, when the trial's directory is given, the device provider's journal.

What the events record is counted exactly, so a stage in which nothing happened counts zero. What the workers left is
not: their time, their token counters (from their reports) and their action counts (from their transcripts) are None
unless every attempt of the stage left them. A worker that died leaves no report, one that left no transcript is not
one that did nothing, and a sum over the others would pass for the whole. Only a Codex transcript kept in the run is
read: an interpretation attempt that stopped before its outputs were collected leaves its transcript in a workspace
outside the run, and the events of another runtime say nothing of Codex actions. The exchanges of a package's live test
are in a record file that the events only name: a stage whose record the run no longer holds has its exchanges None, too,
as has one with a device call that never finished or a live-test call that left no result (it may have reached the device).

A suite's scorecard keeps that rule: a figure that some trials could not measure is summed over the others and counted
as measured by fewer, never passed off as the whole. The scorecard of a suite that ran a scoring set shows aggregates
only, since no detail of one scoring trial leaves the bench harness (spec decisions 4 and 21)."""
import bisect
import json
import math
import re
import sqlite3
import statistics
from collections import namedtuple
from contextlib import closing
from pathlib import Path

from .audit import TRANSCRIPT
from .case_assets import STAGES
from .outcomes import pass_rate, pass_rate_line

SCHEMA = 'benchmark-trial-metrics/1'
SUITE_SCHEMA = 'benchmark-scorecard/1'
# The suffix of the id of a run's final verdict in the configurator's `verdicts` table.
FINAL = ':final'
ACTIONS = ('command_execution', 'mcp_tool_call', 'file_change')
DEVICE = ('exchanges', 'writes', 'refusals', 'resets')
# What the configurator names an attempt: a path component it may be joined to.
NAME = re.compile('[0-9A-Za-z_-]+')
# The events a Codex session prints start with these: `thread.started`, `turn.completed`, `item.completed` and so on.
CODEX = ('thread.', 'turn.', 'item.')
# The transcript entries that are exchanges with the device.
EXCHANGES = ('exchange', 'usb_control')
# The tools that connect to the device, whose finished results (or, for `emit_test_live`, live records) hold the exchanges.
DEVICE_TOOLS = ('probe_run', 'interface_execute', 'emit_test_live', 'benchmark_package_execute')
# The code the toolchain gives a live-test call whose process could not be started: it reached nothing.
NOT_STARTED = 'open'
# Each token counter and its key in a worker's usage.
COUNTERS = (('input', 'input_tokens'), ('cached_input', 'cached_input_tokens'), ('output', 'output_tokens'),
            ('reasoning_output', 'reasoning_output_tokens'))
# The time figures of a stage that can be unknown, and its token figures: the counters, then their total.
TIMES = ('wall_seconds', 'worker_seconds')
TOKENS = (*(name for name, _ in COUNTERS), 'total')
# The yes/no figures of a run's claim, each with its words in the Markdown.
CLAIM = {'claimed_success': 'Fresh-reuse worker claimed success', 'mission_passed': 'Fresh mission passed (evaluator)',
         'agrees': 'Claim agrees with the mission result'}
# The keys of a suite's trial rows that its pass rate is also given by, and the labels of a suite report that say how
# its trials executed (a replay suite's say that no model ran).
GROUPS = ('family', 'level', 'framing')
LABELS = ('execution', 'model_benchmark')


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
    device rejecting its reply. A rejected reply is also a completed exchange. A result that says its `evidence_kind` is
    `replay` (`probe_diff` and `emit_check` give one) re-reports exchanges recorded earlier without contacting the
    device, so it shows none."""
    if isinstance(result, dict) and result.get('evidence_kind') == 'replay':
        return 0, 0
    entries = list(_entries(result))
    return (sum(entry.get('op') in EXCHANGES and entry.get('status') == 'completed' for entry in entries),
            sum(bool(entry.get('device_rejected')) for entry in entries))


def _inside(run_dir: Path, run_id: str, named) -> Path | None:
    """The file a path recorded in a result names, found inside the run's directory, or None. The path was recorded where
    the run was then, which may not be where it is now (a copied home, a run scored on another host, a Windows path), so
    it is read by its place in the run: what follows `runs/<run_id>/`. A path with no such place, one that climbs out of
    the run or leads out of it through a link (the file found must lie inside the run once resolved), and one that is no
    file there name nothing."""
    parts = named.replace('\\', '/').split('/') if isinstance(named, str) else []
    places = [index for index in range(len(parts) - 2) if parts[index] == 'runs' and parts[index + 1] == run_id]
    relative = parts[places[-1] + 2:] if places else []
    if not relative:
        return None
    try:
        path = run_dir.joinpath(*relative).resolve()
        return path if path.is_relative_to(run_dir.resolve()) and path.is_file() else None
    except (OSError, ValueError):
        return None


def _live_exchanges(run_dir: Path, run_id: str, result) -> int | None:
    """The completed exchanges of one `emit_test_live` call. The tool keeps only a summary in its result and the output of
    each package call in the live record it names (`live_record`, `emit/live-N.json` in the stage's tool folder), under
    `results[].stdout_json.transcript`, so they are counted there (`_inside` the run). A result that names no record
    reached no device (the tool names one only once it has called the package) and shows none. None when the record cannot
    be used: it names no usable path, the file is missing or unreadable, or it holds no list of results; or when one of
    its calls left no interface-result to count (`stdout_json` is no object: the package printed no JSON, its process timed
    out, or the host failed around the call), which may already have reached the device. The exception is a call whose
    process could not be started (`error_code` `open`), which reached none and counts zero."""
    if not isinstance(result, dict) or 'live_record' not in result:
        return 0
    path = _inside(run_dir, run_id, result['live_record'])
    try:
        record = json.loads(path.read_text(encoding='utf-8')) if path else None
        results = record.get('results') if isinstance(record, dict) else None
        if not isinstance(results, list):
            return None
        total = 0
        for item in results:
            output = item.get('stdout_json') if isinstance(item, dict) else None
            if isinstance(output, dict):
                total += _exchanges(output)[0]
            elif not (isinstance(item, dict) and item.get('error_code') == NOT_STARTED):
                return None
        return total
    except (OSError, ValueError, RecursionError):
        return None


def _kept(run_dir: Path, stage: str, assignment: dict):
    """Where a run keeps the transcript of one attempt, in the order they are tried. A worker leaves it in its work
    directory, `work/<stage>/<assignment>`. An interpretation works in a sealed workspace outside the run, named
    `attempt<N>`, and the outputs it collects, the transcript among them, are frozen into a record of the attempt:
    `preparation/<assignment>/interpret/attempt*` when each interpretation is prepared on its own, and
    `benchmark/tools/interpret/attempt<N>` in a benchmark run, where the attempts share one tool run and the index in the
    workspace's name says which attempt it is."""
    identity = assignment.get('id')
    if not isinstance(identity, str) or not NAME.fullmatch(identity):
        return
    yield run_dir / 'work' / stage / identity / TRANSCRIPT
    if stage == 'interpret':
        record = run_dir / 'preparation' / identity / 'interpret'
        if record.is_dir():
            yield from (folder / TRANSCRIPT for folder in sorted(record.iterdir()) if folder.name.startswith('attempt'))
        sealed = str(assignment.get('workspace')).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
        if re.fullmatch('attempt[0-9]+', sealed):
            yield run_dir / 'benchmark' / 'tools' / 'interpret' / sealed / TRANSCRIPT


def _read_actions(transcript: Path):
    """The actions completed in one transcript, by type, or None when it cannot be read as a Codex stream: it is no file,
    or no line of it is a Codex event (`thread.*`, `turn.*` or `item.*`), as with an empty file, the events of another
    runtime or text the runtime printed. A line that is not a completed action is skipped, whether or not it is JSON (a
    line nested too deeply for `json` to read is skipped as one that is not), and so is an item whose type is no name."""
    if not transcript.is_file():
        return None
    counts, codex = dict.fromkeys(ACTIONS, 0), False
    with transcript.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except (ValueError, RecursionError):
                # Not JSON, or nested too deeply for `json` to read: a line with no action to count.
                continue
            kind = event.get('type') if isinstance(event, dict) else None
            codex = codex or isinstance(kind, str) and kind.startswith(CODEX)
            item = event.get('item') if kind == 'item.completed' else None
            action = item.get('type') if isinstance(item, dict) else None
            # A type that is no name (a list or an object) cannot be looked up, and is no action.
            if isinstance(action, str) and action in counts:
                counts[action] += 1
    return counts if codex else None


def _actions(run_dir: Path, stage: str, assignments: list) -> dict:
    """The actions a stage's workers completed, by type, counted in the transcripts of its attempts. Every count is None,
    as the token counters are, unless every attempt left a transcript in the run: a stage that ran no worker, an attempt
    with no transcript, and one that stopped before its outputs were collected, whose transcript stays in a workspace
    outside the run, are unknown, not zero. A transcript that no attempt of the stage owns is not counted."""
    total = dict.fromkeys(ACTIONS, 0)
    for assignment in assignments:
        counts = next((found for found in map(_read_actions, _kept(run_dir, stage, assignment)) if found is not None), None)
        if counts is None:
            return dict.fromkeys(ACTIONS)
        for action in ACTIONS:
            total[action] += counts[action]
    return total if assignments else dict.fromkeys(ACTIONS)


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
    - `managed_calls`: the `tool.started` events, by tool. `worker_actions`: the completed actions in the attempts'
      transcripts, by type, each count None unless every attempt left a readable one.
    - `device`: the `exchanges` the device made, each once; the `writes` dispatched (effectful `tool.started` events);
      the `refusals` (`tool.refused` events, calls the runtime refused before it opened the device, and replies the
      device rejected); and the `resets`, each emulator replacement after an uncertain write, from the configurator's
      `emulator.recovered` events and, when `trial_dir` is given, the provider's journal. A replacement in both counts
      once, and a journal row belongs to the stage active at its time.

    The exchanges are the ones completed in the transcripts of tool results. A result whose `evidence_kind` is `replay`
    (`probe_diff`, and `emit_check` when it replays) re-reports recorded exchanges without contacting the device, so it
    counts none. An `emit_test_live` result keeps only a summary, so its exchanges are counted in the live record it
    names (`live_record`, `results[].stdout_json.transcript`), found inside the run's directory by its place there
    whatever home it was recorded in; a result that names no record reached no device.

    A stage's `exchanges` are None (unknown), as the worker figures are, not a lower bound shown as exact, when
    - the record of a live test cannot be used: its path leads out of the run, the file is missing or unreadable, or it
      holds no list of results;
    - a call in that record left no interface-result to count (the package printed no JSON object, its process timed
      out, or the host failed around the call), since it may already have reached the device. A call whose process could
      not be started (`error_code` `open`, in the toolchain's `_live_call`) reached none and counts zero;
    - a call to a tool that connects to the device (`DEVICE_TOOLS`, unless its start says it did not connect) has a
      `tool.started` event and no `tool.finished` event, as when its tool raised after the dispatch, since it may have
      exchanged what no result recorded.

    Two limits remain, and the exchanges of such a stage are exact only apart from them. A `benchmark_package_execute`
    result is counted where it holds a transcript, as the package's own result does on the device the bench harness
    provides; on the physical case (bme280) it is a measurement row with none, so those exchanges are not counted, and the
    stage reads a number that is lower than what the device exchanged. And the replies a package's live test saw the device
    reject are in its record and are not counted as `refusals` (the other tools' rejected replies are).

    A call is its `tool.started` event and its `tool.finished` event, paired by `dispatch_id`; one that never finished,
    as when its tool raised, still counts as a call, and one finished twice counts once.

    Raises FileNotFoundError for a `home` with no database and ValueError for a `run_id` the database does not hold."""
    home = Path(home)
    run_dir = home / 'runs' / run_id
    events, reports, handoffs, final, progress = _records(home, run_id)
    assigned, begun, ended = {stage: [] for stage in STAGES}, {}, {stage: [] for stage in STAGES}
    managed, device = {stage: {} for stage in STAGES}, {stage: dict.fromkeys(DEVICE, 0) for stage in STAGES}
    active, counted = [], set()  # (time, stage) of each assignment or skip; the sessions already counted as resets
    unknown = set()  # the stages whose exchanges cannot be told (see the docstring)
    calls = {}
    for seq, kind, data, created in events:
        stage = data.get('stage')
        if stage in STAGES:
            if kind in ('stage.assigned', 'stage.skipped'):
                active.append((created, stage))
            if kind == 'stage.assigned':
                assigned[stage].append(data)
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
            if finished is None and started.get('name') in DEVICE_TOOLS and started.get('connected') is not False:
                # It never finished, as when its tool raised, so it may have reached the device and nothing says what it
                # exchanged. A replay (`connected` false) never connects.
                unknown.add(stage)
        if finished is not None:
            exchanged, rejected = _exchanges(finished.get('result'))
            if finished.get('name') == 'emit_test_live':
                exchanged = _live_exchanges(run_dir, run_id, finished.get('result'))
                if exchanged is None:
                    unknown.add(stage)
                    exchanged = 0
            device[stage]['exchanges'] += exchanged
            device[stage]['refusals'] += rejected + bool(finished.get('refused_before_open'))
    last = max((created for _, _, _, created in events), default=None)
    for moment, session in _replacements(trial_dir):
        stage = _active_at(active, last, moment)
        if stage and _new_reset(session, counted):
            device[stage]['resets'] += 1
    for stage in unknown:
        device[stage]['exchanges'] = None
    stages = {}
    for stage in STAGES:
        rows = [payload for name, payload in reports if name == stage]
        stages[stage] = {'attempts': len(assigned[stage]), 'wall_seconds': _wall(begun.get(stage), ended[stage]),
                         'worker_seconds': _worker_seconds(rows, len(assigned[stage])),
                         'managed_calls': dict(sorted(managed[stage].items())),
                         'worker_actions': _actions(run_dir, stage, assigned[stage]),
                         'device': device[stage], 'tokens': _tokens(rows, len(assigned[stage]))}
    repairs = (progress or {}).get('repairs')
    return {'schema': SCHEMA, 'run_id': run_id, 'repairs': repairs if type(repairs) is int else None,
            'claim': _claim(handoffs, reports, final), 'stages': stages}


_Figure = namedtuple('_Figure', 'total median measured')


def _figure(values) -> _Figure:
    """The sum and the median of the values that are known, and how many are. A value that is None was not measured and
    is skipped, so a figure no trial gave is None (not zero) with 0 measured, and a sum over some of the trials is told
    from the whole by that count."""
    known = [value for value in values if value is not None]
    return _Figure(sum(known) if known else None, statistics.median(known) if known else None, len(known))


def _stage_figures(rows):
    """One stage's figures over the trials, from `rows`, the stage's entry in each trial's metrics. Each figure sits at
    the key path it has in a trial. The counts that are never unknown (`attempts`, `managed_calls` by tool, and the
    `device` counts but the exchanges) are plain sums. The others, the exchanges among them, are summed over the trials
    that gave them (`_figure`), with the medians of the times and tokens under `median` and the number of trials that
    gave each figure under `measured`."""
    times = {key: _figure(row[key] for row in rows) for key in TIMES}
    tokens = {key: _figure(row['tokens'][key] for row in rows) for key in TOKENS}
    actions = {key: _figure(row['worker_actions'][key] for row in rows) for key in ACTIONS}
    exchanges = _figure(row['device']['exchanges'] for row in rows)
    counts = {key: sum(row['device'][key] for row in rows) for key in DEVICE if key != 'exchanges'}
    calls = {}
    for row in rows:
        for tool, count in row['managed_calls'].items():
            calls[tool] = calls.get(tool, 0) + count
    return {'attempts': sum(row['attempts'] for row in rows), **{key: times[key].total for key in TIMES},
            'managed_calls': dict(sorted(calls.items())), 'worker_actions': {key: actions[key].total for key in ACTIONS},
            'device': {'exchanges': exchanges.total, **counts}, 'tokens': {key: tokens[key].total for key in TOKENS},
            'median': {**{key: times[key].median for key in TIMES}, 'tokens': {key: tokens[key].median for key in TOKENS}},
            'measured': {**{key: times[key].measured for key in TIMES},
                         'tokens': {key: tokens[key].measured for key in TOKENS},
                         'worker_actions': {key: actions[key].measured for key in ACTIONS},
                         'device': {'exchanges': exchanges.measured}}}


def _run_figures(metrics):
    """The run-level figures over the trials: the `repairs` summed over those that gave them, with `measured` their
    number, and each yes/no figure of the `claim` as how many trials answered true, false and unknown (None)."""
    repairs = _figure(trial['repairs'] for trial in metrics)
    claim = {}
    for key in CLAIM:
        answers = [trial['claim'][key] for trial in metrics]
        claim[key] = {'true': sum(answer is True for answer in answers),
                      'false': sum(answer is False for answer in answers),
                      'unknown': sum(answer is None for answer in answers)}
    return {'repairs': repairs.total, 'claim': claim, 'measured': {'repairs': repairs.measured}}


def bench_trial_dirs(report: dict, home: Path) -> dict[str, Path]:
    """Where the bench harness kept the directory of each started trial of a suite report, by trial key: the trial
    directory, under any suite of the bench home beside `home`, whose `trial.json` names the trial's run, found as the
    harness finds a trial for a run. A started trial with no directory there is left out, and its resets are then those
    the configurator recorded, a lower bound."""
    from .harness import _trial_of, bench_home
    suites = bench_home(home) / 'suites'
    found = {row['trial_key']: _trial_of(suites, row['run_id']) for row in report['trials'] if row.get('run_id')}
    return {key: directory for key, directory in found.items() if directory is not None}


def _groups(rows):
    """The pass rate of each value of each grouping key (`GROUPS`) that the trial rows carry, `{key: {value: rate}}`,
    each value named by its text. A row with no value for a key is in none of that key's groups, and a key no row
    carries has no entry."""
    groups = {}
    for key in GROUPS:
        carried = [row for row in rows if row.get(key) is not None]
        if carried:
            groups[key] = {value: pass_rate([row['scored'] for row in carried if str(row[key]) == value])
                           for value in sorted({str(row[key]) for row in carried})}
    return groups


def suite_scorecard(report: dict, *, home: Path, trial_dirs: dict[str, Path] | None = None) -> dict:
    """The scorecard of a suite, schema `benchmark-scorecard/1`, from its `benchmark-suite-report/3` and the records of
    the runs its trials started. It says what it measures: the `suite_id`; whether the suite is still under way or was
    stopped before its slots settled (the report's `provisional` flag, the suite's `status` and its `reason`, None for a
    report that gives none; a scoring suite's reason names a trial and why it stopped, so its card has none); the
    `system`, the report's `protocol` and its experiment's dimensions (runtime, model and so on) but for
    `replay_candidates`, which map cases to candidates; and the `conditions` it ran under, from the experiment's
    comparison identity (its `scoring_set` when it has one, the evaluator revision, execution, evidence track and scope,
    the effective child budget and the number of its overrides, which name trial keys, the repair limit, the tool
    approval) and the report's qualification. None is a per-trial detail.
    Then come the `pass_rate` of the report's stored `scored` outcomes and its `pass_line`; the `joint_verdict`, the
    report's `equivalent_and_mission` count of equivalent drivers with a successful fresh mission that earlier studies
    reported, when it has one; the `groups`, that pass rate by each value of each of `family`, `level` and `framing`
    that the trial rows carry; the report's `execution` and `model_benchmark` when it has them, which say that no model
    ran a replay suite's trials; `measured_trials`; and the figures of `trial_metrics` added up over the trials, under
    `run` (repairs and claim) and `stages` (each stage).

    The two `execution` fields differ for a replay suite: the card's own says how its trials were run
    (`replay-emulation`, from the report), and `conditions.execution` is what the frozen experiment declared
    (`actual-agent-emulation`), the field pairing compares.

    Metrics come from every trial with a run that is not still running, whatever its outcome, read in `home` and, for a
    trial whose key `trial_dirs` names, its trial directory (`bench_trial_dirs`). A trial with no run counts in the pass
    rate alone, and so does one scored `running`: its records are still moving, so they are not read or summed, and it is
    not among the `measured_trials`. A figure sits at the key path it has in a trial's metrics, summed over the trials
    that gave it: a None is skipped, a figure no trial gave is None, and `measured` counts the trials that gave it, so a
    sum over some trials is never taken for the whole. The medians of times and tokens are under `median`. Counts that
    are never unknown (attempts, managed calls, device) are plain sums. In `run` the repairs are summed the same way, and
    each yes/no figure of the claim is how many trials answered true, false and unknown.

    A report that carries a scoring set gives aggregates only: no `trials`, and no case, run id or trial key. Any other
    adds `trials`, a `{trial_key, scored, equivalent_and_mission, metrics}` row for each planned trial (the flag where
    the report's row has one), its metrics None without a run or while it runs. Audit findings quote raw worker commands
    and are in no scorecard.

    Raises ValueError for a report that is not a `benchmark-suite-report/3`, which alone stores each trial's outcome,
    and what `trial_metrics` raises for a run that `home` does not hold."""
    if report.get('schema') != 'benchmark-suite-report/3':
        raise ValueError("A scorecard needs a benchmark-suite-report/3, which stores each trial's scored outcome")
    rows, trial_dirs = report['trials'], trial_dirs or {}
    identity, dimensions = report['experiment']['comparison_identity'], report['experiment']['dimensions']
    budget, policy = identity['budgets']['effective'], identity['intervention_policy']
    rate = pass_rate([row.get('scored') for row in rows])
    metrics = {row['trial_key']: trial_metrics(home, row['run_id'], trial_dir=trial_dirs.get(row['trial_key']))
               for row in rows if row.get('run_id') and row.get('scored') != 'running'}
    measured = list(metrics.values())
    stages = {stage: _stage_figures([trial['stages'][stage] for trial in measured]) for stage in STAGES}
    system = {'protocol': report['protocol'],
              **{key: value for key, value in dimensions.items() if key != 'replay_candidates'}}
    conditions = {**({'scoring_set': dict(identity['scoring_set'])} if 'scoring_set' in identity else {}),
                  **{key: identity[key] for key in ('evaluator_revision', 'execution', 'evidence_track', 'scope')},
                  'child_budget_seconds': budget['child_budget_seconds'],
                  'child_budget_overrides': len(budget['child_budget_overrides']),
                  'max_model_repairs': policy['max_model_repairs'],
                  'scoped_tool_approval': policy['scoped_tool_approval'], 'qualification': report['qualification']}
    joint = {'joint_verdict': dict(report['equivalent_and_mission'])} if 'equivalent_and_mission' in report else {}
    # A scoring suite's reason names a trial and why it stopped: a detail of one scoring trial, which stays inside.
    reason = None if 'scoring_set' in identity else report.get('reason')
    card = {'schema': SUITE_SCHEMA, 'suite_id': report['suite_id'], 'provisional': report.get('provisional'),
            'status': report.get('status'), 'reason': reason, 'system': system, 'conditions': conditions,
            **{key: report[key] for key in LABELS if key in report}, 'pass_rate': rate, 'pass_line': pass_rate_line(rate),
            **joint, 'groups': _groups(rows), 'measured_trials': len(measured), 'run': _run_figures(measured),
            'stages': stages}
    if 'scoring_set' not in identity:
        flag = lambda row: ({'equivalent_and_mission': row['equivalent_and_mission']}
                            if 'equivalent_and_mission' in row else {})
        card['trials'] = [{'trial_key': row['trial_key'], 'scored': row['scored'], **flag(row),
                           'metrics': metrics.get(row['trial_key'])} for row in rows]
    return card


def _cell(figure, measured, trials):
    """A sum or median over the trials as table text: `unknown` when no trial measured it, else the figure, with how many
    measured it (`n/N`) when that is not all N of the trials measured at all. A whole number has no decimal point and any
    other has two places."""
    if not measured:
        return 'unknown'
    text = str(int(figure)) if float(figure).is_integer() else f'{figure:.2f}'
    return text + (f' ({measured}/{trials})' if measured < trials else '')


def _row(*cells):
    return '| ' + ' | '.join(cells) + ' |'


def scorecard_markdown(card: dict) -> str:
    """A scorecard as Markdown, aggregates only: it names no trial. The first line is the pass line. A suite that is still
    under way, or stopped before its slots settled, says so on the second (`Provisional: suite <status>`, then its reason
    when the card has one), and a suite no model ran says so on the next (`model_benchmark` is False). Under them, one
    line names the system (protocol, runtime and version, model and reasoning effort, and how it executed and on what
    evidence: the card's own `execution` when it has one, else the conditions', then the evidence track), one the scoring
    set (its registration and the first 12 characters of its manifest hash) of a suite that ran one, and one gives the
    joint verdict of earlier studies. Then come the pass line of each group, the run figures, a row of sums and one of
    medians for each stage, and the managed calls by tool. A figure that no trial measured reads `unknown`, and one that
    only some did is marked with how many (`n/N`)."""
    trials, stages, run = card['measured_trials'], card['stages'], card['run']
    lines = [card['pass_line']]
    if card.get('provisional'):
        reason = ' '.join(str(card['reason']).split()) if card.get('reason') else ''
        lines.append(f"Provisional: suite {card.get('status') or 'status unknown'}" + (f': {reason}' if reason else ''))
    if card.get('model_benchmark') is False:
        label = f" ({card['execution']})" if card.get('execution') else ''
        lines.append(f'No model ran these trials{label}: none of these figures is model performance.')
    named = [('protocol', card['system'].get('protocol')),
             ('runtime', ' '.join(str(card['system'][key]) for key in ('runtime', 'version') if card['system'].get(key))),
             ('model', card['system'].get('model')), ('reasoning effort', card['system'].get('reasoning_effort')),
             # How the trials were run (a replay suite's own label) before what the frozen experiment declared.
             ('execution', card.get('execution') or card['conditions'].get('execution')),
             ('evidence track', card['conditions'].get('evidence_track'))]
    lines.append('System: ' + ', '.join(f'{label} {value}' for label, value in named if value))
    if 'scoring_set' in card['conditions']:
        scoring = card['conditions']['scoring_set']
        lines.append(f"Scoring set: {scoring['registration']}, manifest {scoring['manifest_sha256'][:12]}")
    if 'joint_verdict' in card:
        joint = card['joint_verdict']
        lines += ['', "Earlier studies' joint verdict (an equivalent driver and a successful fresh mission): "
                      f"{joint['passed']} of {joint['denominator']} planned trials."]
    still = ' that is not still running' if card['pass_rate']['running'] else ''
    lines += ['', f"Figures cover {trials} of {card['pass_rate']['planned']} planned trials, those with a run{still}. A sum or "
              'median that fewer of them measured shows how many (`n/N`); `unknown` means none did.', '']
    if card['groups']:
        lines += ['## Groups', '', *(f'- {key} `{value}`: {pass_rate_line(rate)}'
                                     for key, values in card['groups'].items() for value, rate in values.items()), '']
    answers = lambda key: ', '.join(f'{run["claim"][key][answer]} {answer}' for answer in ('true', 'false', 'unknown'))
    lines += ['## Run', '', f"- Repairs: {_cell(run['repairs'], run['measured']['repairs'], trials)}",
              *(f'- {label}: {answers(key)}' for key, label in CLAIM.items()), '']
    lines += ['## Stages', '', _row('Stage', 'Attempts', 'Wall seconds', 'Worker seconds', 'Tokens', 'Shell commands',
                                    'MCP calls', 'File edits', 'Exchanges', 'Writes', 'Refusals', 'Resets'),
              '|---|' + '---:|' * 11]
    for name, stage in stages.items():
        measured = stage['measured']
        lines.append(_row(name, str(stage['attempts']), _cell(stage['wall_seconds'], measured['wall_seconds'], trials),
                          _cell(stage['worker_seconds'], measured['worker_seconds'], trials),
                          _cell(stage['tokens']['total'], measured['tokens']['total'], trials),
                          *(_cell(stage['worker_actions'][key], measured['worker_actions'][key], trials)
                            for key in ACTIONS),
                          _cell(stage['device']['exchanges'], measured['device']['exchanges'], trials),
                          *(str(stage['device'][key]) for key in DEVICE if key != 'exchanges')))
    lines += ['', '## Medians', '', _row('Stage', 'Wall seconds', 'Worker seconds', 'Tokens'), '|---|---:|---:|---:|']
    for name, stage in stages.items():
        measured, median = stage['measured'], stage['median']
        lines.append(_row(name, _cell(median['wall_seconds'], measured['wall_seconds'], trials),
                          _cell(median['worker_seconds'], measured['worker_seconds'], trials),
                          _cell(median['tokens']['total'], measured['tokens']['total'], trials)))
    calls = [f'- {name}: ' + ', '.join(f'{tool} {count}' for tool, count in stage['managed_calls'].items())
             for name, stage in stages.items() if stage['managed_calls']]
    return '\n'.join([*lines, '', '## Managed calls', '', *(calls or ['none'])]) + '\n'


def write_scorecard(card: dict, output: Path) -> None:
    """Write `scorecard.json` and `scorecard.md` into `output`, creating it if needed, as bytes so that they are the
    same on every host. Both are encoded before anything is written."""
    files = {'scorecard.json': json.dumps(card, indent=2, allow_nan=False) + '\n', 'scorecard.md': scorecard_markdown(card)}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (output / name).write_bytes(text.encode('utf-8'))

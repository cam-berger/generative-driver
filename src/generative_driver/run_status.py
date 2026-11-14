"""What a run's `status` adds for whoever watches the run (docs/features/kit-fixes/04-service-diagnostics.md, 04-B3 and
04-B5): where the service's log is, when the budget runs out, the repairs used and allowed, and the running stage
worker's last sign of life.

Every time here is one that was stored, never an age or a remaining duration, so a stopped run reads the same twice.
Reading them never fails: a run saved by an earlier version gives null where it holds no value."""

import json
import math
import threading
import time

# The keys `added` gives. Suite replies stay as they were, so a child's status passes into one without them.
_ADDED = ('deadline', 'repairs', 'worker', 'log')


def _saved(text):
    """A saved JSON object, or an empty one when the text holds none."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _repair_budget(spec):
    """The run's repair budget, as the run reads it (`max_revisions`, 2 unless the run set one)."""
    try:
        return int(spec.get('max_revisions', 2))
    except (TypeError, ValueError):
        return None


class Workers:
    """The stage workers running now and each one's last sign of life. They are kept in memory, since a worker lives no
    longer than the service that launched it; each time is taken when the sign is seen and kept, never computed when
    it is read."""

    def __init__(self):
        self._lock = threading.Lock()
        self._running = {}

    def began(self, run_id, assignment_id, stage, at):
        """A stage worker of `run_id` is launched for its assignment, which began `at`."""
        with self._lock:
            self._running[run_id] = {'assignment_id': assignment_id, 'stage': stage, 'started': at,
                                     'last_activity': at, 'last_activity_kind': 'started'}

    def ended(self, run_id):
        """The run's stage worker has stopped."""
        with self._lock:
            self._running.pop(run_id, None)

    def _seen(self, run_id, kind, assignment_id=None):
        now = time.time()
        with self._lock:
            worker = self._running.get(run_id)
            if (worker is not None and assignment_id in (None, worker['assignment_id'])
                    and now >= worker['last_activity']):
                worker.update(last_activity=now, last_activity_kind=kind)

    def printed(self, run_id):
        """The run's stage worker printed a line on its output."""
        self._seen(run_id, 'output')

    def called(self, run_id, assignment_id):
        """A gateway call of the assignment `assignment_id` started or ended; a call of any other assignment is not
        the running worker's."""
        self._seen(run_id, 'tool', assignment_id)

    def of(self, run_id):
        """The run's running stage worker as `status` gives it, or None when none runs."""
        with self._lock:
            worker = self._running.get(run_id)
            return None if worker is None else {key: worker[key] for key in
                                                ('stage', 'started', 'last_activity', 'last_activity_kind')}


def without(reply):
    """A suite's reply as it was before `added`: one that passes its child's status on drops the added fields."""
    if isinstance(reply, dict) and any(key in reply for key in _ADDED):
        return {key: value for key, value in reply.items() if key not in _ADDED}
    return reply


def added(row, progress, worker, log):
    """The fields a run's `status` adds, from the run's saved `row` and its saved `progress` text (None when it has
    none): `deadline`, when its budget runs out (its start plus its budget, which a resume's extension rewrites);
    `repairs`, the repairs made and the repair budget, or None when the progress records no count; `worker`, as
    `Workers.of` gives the running stage worker; and `log`, the path of the service's log."""
    spec = _saved(row['spec'])
    budget, used = spec.get('budget_seconds'), _saved(progress).get('repairs')
    return {'deadline': row['created'] + budget if _number(budget) and _number(row['created']) else None,
            'repairs': {'used': used, 'budget': _repair_budget(spec)} if type(used) is int else None,
            'worker': worker, 'log': str(log)}

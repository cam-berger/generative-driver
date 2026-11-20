"""The record of every dispatch and the one owner of uncertain effects.

A dispatch is one tool call the configurator runs for a worker or a client. The worker gateway, the generated tools
of a run and device sessions all record theirs here, so the rule for what leaves an effect uncertain is written once
(docs/adr/0011-what-leaves-an-effect-uncertain.md).
"""
import json
from contextlib import contextmanager

_MARKING = frozenset({'write', 'actuate'})
# For each kind of owner: the table that holds it, the status in which it is active, and the refusal of a dispatch
# for one that is not.
_OWNERS = {'run': ('runs', 'running', 'Worker assignment became inactive before dispatch'),
           'session': ('device_sessions', 'open', 'Device operation requires active ownership')}


def marks(effects):
    """Whether a call is marked: its declared `effects` hold `write` or `actuate`. Only a marked call can leave an
    uncertain effect; a read, a reset and any other name mark nothing."""
    return not _MARKING.isdisjoint(effects)


class DispatchRecord:
    """The journal of the dispatches of runs and device sessions, with their pending effects.

    `connect()` gives a database connection that commits when its block ends, `lock` is the configurator's re-entrant
    lock and `write_event(owner_id, kind, payload, db)` writes one event and returns its id. An owner is a run or a
    device session, as `kind` says (`run` or `session`)."""

    def __init__(self, connect, lock, write_event):
        self._connect, self._lock, self._write_event = connect, lock, write_event
        with connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS pending_effects (dispatch_id INTEGER PRIMARY KEY, '
                       'run_id TEXT NOT NULL, assignment_id TEXT, actor TEXT NOT NULL)')

    def begin(self, owner_id, *, kind, name, actor, effects, stage=None, assignment_id=None, facts=None,
              condition=False, before_mark=None, after_start=None):
        """The id of the dispatch, recorded as `tool.started` before its tool runs, or None when the caller gave it
        up. `facts` are the caller's own fields of the event; a field of the record wins over a fact of the same name.

        The caller's own steps run inside the record's lock and transaction. `before_mark(db)` may raise to refuse
        the dispatch or return `False` to give it up without an error; either way nothing is recorded.
        `after_start(db, dispatch_id)` writes what the caller keeps with the start; if it raises, the start is not
        recorded either.

        A dispatch starts only for an owner that is active: a run that is `running`, a session that is `open`, and
        neither cancelled. A run's dispatch names its assignment, which must be the run's current one. While the owner
        is uncertain a read still starts; a marked call and one that selects a condition of a supplied device
        (`condition`) are refused."""
        table, active, inactive = _OWNERS[kind]
        marked = marks(effects)
        with self._lock, self._connect() as db:
            status, cancelled, uncertain = db.execute(
                f'SELECT status,cancelled,uncertain FROM {table} WHERE id=?', (owner_id,)).fetchone() or (None,) * 3
            if status != active or cancelled or kind == 'run' and not _current(db, owner_id, stage, assignment_id):
                raise ValueError(inactive)
            if before_mark is not None and before_mark(db) is False:
                return None
            if (marked or condition) and uncertain:
                raise ValueError('Outstanding effect is uncertain; nothing that would change the device is sent until '
                                 'the operator resolves it')
            # The flag is stored with the start, before the tool runs: neither a crash nor a cancellation can turn
            # an outstanding effect into a retry.
            if marked:
                db.execute(f'UPDATE {table} SET uncertain=1 WHERE id=?', (owner_id,))
            dispatch_id = self._write_event(owner_id, 'tool.started', {
                **(facts or {}), 'stage': stage, 'assignment_id': assignment_id, 'name': name, 'actor': actor,
                'effectful': marked}, db)
            if marked:
                db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)', (dispatch_id, owner_id, assignment_id, actor))
            if after_start is not None:
                after_start(db, dispatch_id)
            return dispatch_id

    def abandon(self, owner_id, dispatch_id, *, name, actor, reason, stage=None, assignment_id=None, error=None):
        """Whether the dispatch has a pending effect, once it is recorded as `tool.abandoned`: it ended without a
        result. Nothing says what such a call did, so its pending effect stays. `reason` says what ended the call
        and `error`, a type name and a message, what it raised."""
        with self._connect() as db:
            self._write_event(owner_id, 'tool.abandoned', {
                'stage': stage, 'assignment_id': assignment_id, 'name': name, 'dispatch_id': str(dispatch_id),
                'reason': reason, **({'type': error[0], 'message': error[1]} if error else {}), 'actor': actor}, db)
            return db.execute('SELECT 1 FROM pending_effects WHERE dispatch_id=?',
                              (dispatch_id,)).fetchone() is not None

    def uncertain(self, owner_id, *, kind, db=None):
        """Whether the owner is uncertain: its flag is set. An owner that is not stored is not uncertain."""
        with self._transaction(db) as db:
            flag = db.execute(f'SELECT uncertain FROM {_OWNERS[kind][0]} WHERE id=?', (owner_id,)).fetchone()
        return bool(flag and flag[0])

    def pending(self, owner_id, db=None):
        """The owner's pending effects in the order of their dispatches, each with `dispatch_id` as it is stored,
        `assignment_id` and `actor`."""
        with self._transaction(db) as db:
            rows = db.execute('SELECT dispatch_id,assignment_id,actor FROM pending_effects WHERE run_id=? '
                              'ORDER BY dispatch_id', (owner_id,)).fetchall()
        return [{'dispatch_id': row[0], 'assignment_id': row[1], 'actor': row[2]} for row in rows]

    def resolve_by_operator(self, owner_id, *, kind, effect_resolution, observation, db=None):
        """The dispatches whose pending effects the operator's statement resolved, as the text of their ids. The
        owner is no longer uncertain. The statement is complete when `effect_resolution` is `confirmed_safe` and
        `observation` is an object that holds `prior_execution_stopped` true and a `source` and a `message` that are
        text and not blank; an incomplete one is refused and changes nothing."""
        if not (effect_resolution == 'confirmed_safe' and isinstance(observation, dict)
                and observation.get('prior_execution_stopped') is True
                and all(isinstance(observation.get(key), str) and observation[key].strip()
                        for key in ('source', 'message'))):
            raise ValueError('Resolving an uncertain effect requires effect_resolution "confirmed_safe" and an '
                             'observation with source, message and prior_execution_stopped true')
        with self._transaction(db) as db:
            return self._resolve(db, owner_id, kind)

    def resolve_by_replacement(self, owner_id, db):
        """The dispatches whose pending effects the verified replacement of the run's supplied device resolved, as
        the text of their ids. The run is no longer uncertain. It needs no statement and commits with the caller's
        transaction."""
        return self._resolve(db, owner_id, 'run')

    def _resolve(self, db, owner_id, kind):
        resolved = [str(effect['dispatch_id']) for effect in self.pending(owner_id, db)]
        db.execute('DELETE FROM pending_effects WHERE run_id=?', (owner_id,))
        db.execute(f'UPDATE {_OWNERS[kind][0]} SET uncertain=0 WHERE id=?', (owner_id,))
        return resolved

    @contextmanager
    def _transaction(self, db):
        """The caller's transaction when it gave one, which takes no lock, else the lock and a transaction of the
        record's own."""
        if db is not None:
            yield db
        else:
            with self._lock, self._connect() as own:
                yield own


def _current(db, run_id, stage, assignment_id):
    """Whether the assignment is the run's current one: `active`, and of the run's stage and revision. A run with no
    saved progress is at revision 0, and so is an assignment that names none."""
    assignment = db.execute('SELECT state,payload FROM assignments WHERE id=? AND run_id=?',
                            (assignment_id, run_id)).fetchone()
    if assignment is None or assignment[0] != 'active':
        return False
    progress = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
    return (stage == db.execute('SELECT stage FROM runs WHERE id=?', (run_id,)).fetchone()[0]
            and _revision(assignment[1]) == (_revision(progress[0]) if progress else 0))


def _revision(payload):
    return json.loads(payload).get('revision', 0)

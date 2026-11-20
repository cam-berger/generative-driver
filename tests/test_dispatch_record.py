"""The dispatch record: one journal of every dispatch and the one owner of uncertain effects (Spec 01 of the
dispatch-outcome Run, 01-B5 to 01-B16).

The record runs on a temporary SQLite database that holds only what it reads, with a lock and an event writer of the
test. No configurator, no worker, no model run and no device.
"""
import itertools
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from generative_driver.dispatch_record import DispatchRecord, marks

# What the record reads, the journal the test's writer keeps, and one table of a caller's own for the hooks.
SCHEMA = '''
    CREATE TABLE runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT, cancelled INTEGER DEFAULT 0,
                       uncertain INTEGER DEFAULT 0);
    CREATE TABLE device_sessions (id TEXT PRIMARY KEY, status TEXT NOT NULL, cancelled INTEGER DEFAULT 0,
                                  uncertain INTEGER DEFAULT 0);
    CREATE TABLE assignments (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE progress (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
                         payload TEXT NOT NULL);
    CREATE TABLE invocations (dispatch_id INTEGER PRIMARY KEY, request_id TEXT NOT NULL);
'''
# The pending effects of a database that was written before this record existed: two of the run, one of the session.
STORED_BEFORE = '''
    CREATE TABLE pending_effects (dispatch_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, assignment_id TEXT,
                                  actor TEXT NOT NULL);
    INSERT INTO pending_effects VALUES(9,'run','probe-1','worker');
    INSERT INTO pending_effects VALUES(8,'session',NULL,'client');
    INSERT INTO pending_effects VALUES(7,'run','probe-0','worker');
    UPDATE runs SET uncertain=1;
    UPDATE device_sessions SET uncertain=1;
'''
RUN = {'kind': 'run', 'name': 'interface_execute', 'actor': 'worker', 'stage': 'probe', 'assignment_id': 'probe-1'}
SESSION = {'kind': 'session', 'name': 'measure', 'actor': 'client'}
OWNERS = (('run', RUN, 'runs'), ('session', SESSION, 'device_sessions'))
STATEMENT = {'source': 'operator at the bench', 'message': 'The earlier worker has stopped and the output reads off',
             'prior_execution_stopped': True}
INCOMPLETE_STATEMENT = ('Resolving an uncertain effect requires effect_resolution "confirmed_safe" and an observation '
                        'with source, message and prior_execution_stopped true')
REFUSED_WHILE_UNCERTAIN = ('Outstanding effect is uncertain; nothing that would change the device is sent until the '
                           'operator resolves it')


class MarksTests(unittest.TestCase):
    def test_01_B6_only_a_write_or_an_actuation_marks(self):
        for effects, marked in ((['read'], False), ({'read', 'reset'}, False), ([], False), (['erase'], False),
                                (['write'], True), (('read', 'actuate'), True), ({'reset', 'write', 'actuate'}, True),
                                (iter(['read', 'write']), True)):
            with self.subTest(effects=effects):
                self.assertIs(marks(effects), marked)


class RecordCase(unittest.TestCase):
    """A record on a temporary database that holds the run `run`, running at probe with its current assignment
    `probe-1` at revision 2, and the open device session `session`. The journal already holds events, so no event
    of a test has the id 1."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory, self.databases = Path(temporary.name), itertools.count(1)
        self.lock = threading.RLock()
        self.fresh()

    def fresh(self, stored=''):
        """Start over on a new database. A test that goes through several cases gives each its own. `stored` is
        what the database holds beyond the fixture before the record is built on it."""
        self.path = self.directory / f'records-{next(self.databases)}.sqlite3'
        with self.connect() as db:
            db.executescript(SCHEMA)
        with self.connect() as db:
            db.execute("INSERT INTO runs(id,status,stage) VALUES('run','running','probe')")
            db.execute("INSERT INTO device_sessions(id,status) VALUES('session','open')")
            db.execute("INSERT INTO assignments VALUES('probe-1','run','active',?)", (json.dumps({'revision': 2}),))
            db.execute("INSERT INTO progress VALUES('run',?)", (json.dumps({'revision': 2}),))
            for owner in ('run', 'session', 'run'):
                self.write_event(owner, 'owner.noted', {}, db)
        with self.connect() as db:
            db.executescript(stored)
        self.record = DispatchRecord(self.connect, self.lock, self.write_event)

    @contextmanager
    def connect(self):
        """A connection that commits when its block ends and rolls back when the block raises, as the
        configurator's does. It gives plain rows."""
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def write_event(owner_id, kind, payload, db):
        return db.execute('INSERT INTO events(owner_id,kind,payload) VALUES(?,?,?)',
                          (owner_id, kind, json.dumps(payload))).lastrowid

    def events(self, owner_id, *kinds):
        """The owner's events of `kinds` in order, each as its id and its payload."""
        with self.connect() as db:
            rows = db.execute('SELECT seq,kind,payload FROM events WHERE owner_id=? ORDER BY seq',
                              (owner_id,)).fetchall()
        return [(seq, json.loads(payload)) for seq, kind, payload in rows if kind in kinds]

    def journal(self):
        """Every event a test's dispatches wrote, as owner and kind in order."""
        with self.connect() as db:
            return [tuple(row) for row in db.execute(
                "SELECT owner_id,kind FROM events WHERE kind!='owner.noted' ORDER BY seq")]

    def rows(self, sql):
        with self.connect() as db:
            return [tuple(row) for row in db.execute(sql)]

    def change(self, sql):
        """Change what the configurator stores about an owner, as its own code does between two dispatches."""
        with self.connect() as db:
            db.execute(sql)

    def pending_ids(self, owner_id):
        return [effect['dispatch_id'] for effect in self.record.pending(owner_id)]

    def state(self, owner_id, kind):
        """What the record holds for an owner: the dispatches of its pending effects, and whether it is uncertain."""
        return self.pending_ids(owner_id), self.record.uncertain(owner_id, kind=kind)


class StartTests(RecordCase):
    def test_01_B5_a_dispatch_of_a_run_is_recorded_as_started_with_its_fields_and_the_callers_facts(self):
        facts = {'connected': True, 'argument_sha256': {'model_dir': 'a' * 64}}
        dispatch = self.record.begin('run', **RUN, effects=['read'], facts=facts)
        [(event, payload)] = self.events('run', 'tool.started')
        self.assertEqual(dispatch, event)
        self.assertEqual(payload, {'stage': 'probe', 'assignment_id': 'probe-1', 'name': 'interface_execute',
                                   'actor': 'worker', 'effectful': False, 'connected': True,
                                   'argument_sha256': {'model_dir': 'a' * 64}})

    def test_01_B5_a_dispatch_of_a_session_is_recorded_in_the_same_journal_with_no_stage_and_no_assignment(self):
        dispatch = self.record.begin('session', **SESSION, effects=['read'])
        [(event, payload)] = self.events('session', 'tool.started')
        self.assertEqual(dispatch, event)
        self.assertEqual(payload, {'stage': None, 'assignment_id': None, 'name': 'measure', 'actor': 'client',
                                   'effectful': False})
        self.assertEqual(self.events('run', 'tool.started'), [])

    def test_01_B5_a_fact_never_replaces_a_field_of_the_record(self):
        self.record.begin('run', **RUN, effects=['read'],
                          facts={'name': 'another_tool', 'effectful': True, 'operation': 'measure'})
        [(_, payload)] = self.events('run', 'tool.started')
        self.assertEqual((payload['name'], payload['effectful'], payload['operation']),
                         ('interface_execute', False, 'measure'))

    def test_01_B5_what_the_caller_writes_after_the_start_commits_with_it(self):
        seen = []

        def after_start(db, dispatch_id):
            seen.append(dispatch_id)
            db.execute('INSERT INTO invocations VALUES(?,?)', (dispatch_id, 'request-1'))
        dispatch = self.record.begin('session', **SESSION, effects=['read'], after_start=after_start)
        self.assertEqual(seen, [dispatch])
        self.assertEqual(self.rows('SELECT dispatch_id,request_id FROM invocations'), [(dispatch, 'request-1')])

    def test_01_B5_a_start_whose_caller_fails_after_it_records_nothing(self):
        def after_start(db, dispatch_id):
            db.execute('INSERT INTO invocations VALUES(?,?)', (dispatch_id, 'request-1'))
            raise RuntimeError('scripted failure of the caller')
        with self.assertRaisesRegex(RuntimeError, 'scripted failure of the caller'):
            self.record.begin('session', **SESSION, effects=['read'], after_start=after_start)
        self.assertEqual(self.journal(), [])
        self.assertEqual(self.rows('SELECT * FROM invocations'), [])

    def test_01_B5_before_the_mark_the_caller_may_refuse_the_dispatch_or_give_it_up(self):
        def refuse(db):
            raise ValueError("The call's deadline passed before it was dispatched; nothing was dispatched")
        with self.assertRaisesRegex(ValueError, 'deadline passed before it was dispatched'):
            self.record.begin('run', **RUN, effects=['read'], before_mark=refuse)
        self.assertEqual(self.journal(), [])
        # Only False gives the dispatch up; a check that returns nothing lets it go on.
        self.assertIsNone(self.record.begin('run', **RUN, effects=['read'], before_mark=lambda db: False))
        self.assertEqual(self.journal(), [])
        asked = []
        dispatch = self.record.begin('run', **RUN, effects=['read'], before_mark=lambda db: asked.append(
            db.execute("SELECT status FROM runs WHERE id='run'").fetchone()[0]))
        self.assertEqual(asked, ['running'])
        self.assertEqual([event for event, _ in self.events('run', 'tool.started')], [dispatch])


class ActiveTests(RecordCase):
    """Before anything else `begin` refuses an owner that is not active and an assignment that is not current."""

    def refused(self, text, owner_id, **call):
        asked = []
        with self.assertRaises(ValueError) as refusal:
            self.record.begin(owner_id, **call, effects=['read'], before_mark=asked.append)
        self.assertEqual(str(refusal.exception), text)
        self.assertEqual(asked, [], 'the caller is asked only about a dispatch that may start')
        self.assertEqual(self.journal(), [])

    def test_01_B5_a_dispatch_of_a_run_that_is_not_active_is_refused_and_records_nothing(self):
        for state in ("status='queued'", "status='blocked'", "status='succeeded'", 'cancelled=1'):
            with self.subTest(run=state):
                self.fresh()
                self.change(f'UPDATE runs SET {state}')
                self.refused('Worker assignment became inactive before dispatch', 'run', **RUN)
        with self.subTest(run='not stored'):
            self.refused('Worker assignment became inactive before dispatch', 'another-run', **RUN)

    def test_01_B5_a_dispatch_whose_assignment_is_not_current_is_refused_and_records_nothing(self):
        for label, change, call in (
                ('accepted', "UPDATE assignments SET state='accepted'", RUN),
                ('superseded', "UPDATE assignments SET state='superseded'", RUN),
                ('of an earlier revision', """UPDATE progress SET payload='{"revision": 3}'""", RUN),
                ('of a stage the run has left', "UPDATE runs SET stage='ground'", RUN),
                ('of another run', "UPDATE assignments SET run_id='another-run'", RUN),
                ('not stored', 'SELECT 1', {**RUN, 'assignment_id': 'probe-9'}),
                ('not named', 'SELECT 1', {**RUN, 'assignment_id': None})):
            with self.subTest(assignment=label):
                self.fresh()
                self.change(change)
                self.refused('Worker assignment became inactive before dispatch', 'run', **call)

    def test_01_B5_a_run_without_saved_progress_is_at_revision_zero(self):
        self.change('DELETE FROM progress')
        self.refused('Worker assignment became inactive before dispatch', 'run', **RUN)
        self.change("UPDATE assignments SET payload='{}'")
        dispatch = self.record.begin('run', **RUN, effects=['read'])
        self.assertEqual([event for event, _ in self.events('run', 'tool.started')], [dispatch])

    def test_01_B5_a_dispatch_of_a_session_that_is_not_active_is_refused_and_records_nothing(self):
        for state in ("status='closed'", "status='blocked'", 'cancelled=1'):
            with self.subTest(session=state):
                self.fresh()
                self.change(f'UPDATE device_sessions SET {state}')
                self.refused('Device operation requires active ownership', 'session', **SESSION)
        with self.subTest(session='not stored'):
            self.refused('Device operation requires active ownership', 'another-session', **SESSION)


class MarkTests(RecordCase):
    def test_01_B6_a_write_or_an_actuation_of_a_run_is_marked_when_it_starts(self):
        for effects in (['write'], ['actuate'], ['read', 'write', 'reset']):
            with self.subTest(effects=effects):
                self.fresh()
                dispatch = self.record.begin('run', **RUN, effects=effects)
                [(_, payload)] = self.events('run', 'tool.started')
                self.assertIs(payload['effectful'], True)
                self.assertEqual(self.record.pending('run'),
                                 [{'dispatch_id': dispatch, 'assignment_id': 'probe-1', 'actor': 'worker'}])
                self.assertIs(self.record.uncertain('run', kind='run'), True)
                self.assertEqual(self.record.pending('session'), [])
                self.assertIs(self.record.uncertain('session', kind='session'), False)

    def test_01_B6_a_marked_dispatch_of_a_session_names_no_assignment(self):
        dispatch = self.record.begin('session', **SESSION, effects=['actuate'])
        [(_, payload)] = self.events('session', 'tool.started')
        self.assertIs(payload['effectful'], True)
        self.assertEqual(self.record.pending('session'),
                         [{'dispatch_id': dispatch, 'assignment_id': None, 'actor': 'client'}])
        self.assertIs(self.record.uncertain('session', kind='session'), True)
        self.assertEqual(self.record.pending('run'), [])
        self.assertIs(self.record.uncertain('run', kind='run'), False)

    def test_01_B6_a_read_marks_nothing_also_over_a_channel_that_resets_the_device(self):
        for owner_id, call in (('run', RUN), ('session', SESSION)):
            for effects in (['read'], ['read', 'reset'], []):
                with self.subTest(owner=owner_id, effects=effects):
                    self.fresh()
                    self.record.begin(owner_id, **call, effects=effects)
                    [(_, payload)] = self.events(owner_id, 'tool.started')
                    self.assertIs(payload['effectful'], False)
                    self.assertEqual(self.record.pending(owner_id), [])
                    self.assertIs(self.record.uncertain(owner_id, kind=call['kind']), False)

    def test_01_B6_an_owner_that_is_not_stored_is_not_uncertain_and_has_no_pending_effect(self):
        for kind in ('run', 'session'):
            with self.subTest(kind=kind):
                self.assertIs(self.record.uncertain('never-stored', kind=kind), False)
                self.assertEqual(self.record.pending('never-stored'), [])

    def test_01_B6_the_caller_sees_the_mark_in_its_own_transaction_and_a_failed_start_keeps_none(self):
        seen = []

        def after_start(db, dispatch_id):
            seen.append((self.record.uncertain('run', kind='run', db=db), self.record.pending('run', db)))
            raise RuntimeError('scripted failure of the caller')
        with self.assertRaisesRegex(RuntimeError, 'scripted failure of the caller'):
            self.record.begin('run', **RUN, effects=['write'], after_start=after_start)
        [(uncertain, [pending])] = seen
        self.assertIs(uncertain, True)
        self.assertEqual((pending['assignment_id'], pending['actor']), ('probe-1', 'worker'))
        self.assertIs(self.record.uncertain('run', kind='run'), False)
        self.assertEqual(self.record.pending('run'), [])


class UncertainTests(RecordCase):
    def refused(self, owner_id, **call):
        with self.assertRaises(ValueError) as refusal:
            self.record.begin(owner_id, **call)
        self.assertEqual(str(refusal.exception), REFUSED_WHILE_UNCERTAIN)

    def test_01_B11_while_an_owner_is_uncertain_reads_go_on_and_what_would_change_the_device_is_refused(self):
        for owner_id, call, _ in OWNERS:
            with self.subTest(owner=owner_id):
                self.fresh()
                write = self.record.begin(owner_id, **call, effects=['write'])
                for change in ({'effects': ['write']}, {'effects': ['actuate']}, {'effects': ['read', 'actuate']},
                               {'effects': ['read'], 'condition': True}):
                    self.refused(owner_id, **call, **change)
                read = self.record.begin(owner_id, **call, effects=['read', 'reset'])
                self.assertEqual([event for event, _ in self.events(owner_id, 'tool.started')], [write, read])
                self.assertEqual(self.journal(), [(owner_id, 'tool.started')] * 2)
                self.assertEqual([effect['dispatch_id'] for effect in self.record.pending(owner_id)], [write])
                self.assertIs(self.record.uncertain(owner_id, kind=call['kind']), True)

    def test_01_B11_an_uncertain_owner_does_not_stop_another_owner(self):
        self.record.begin('run', **RUN, effects=['write'])
        write = self.record.begin('session', **SESSION, effects=['write'])
        self.assertEqual([effect['dispatch_id'] for effect in self.record.pending('session')], [write])

    def test_01_B11_a_condition_call_of_an_owner_that_is_not_uncertain_goes_on_and_marks_nothing(self):
        dispatch = self.record.begin('run', **RUN, effects=['read'], condition=True)
        self.assertEqual([event for event, _ in self.events('run', 'tool.started')], [dispatch])
        self.assertEqual(self.record.pending('run'), [])
        self.assertIs(self.record.uncertain('run', kind='run'), False)

    def test_01_B11_the_callers_own_check_comes_before_the_refusal(self):
        self.record.begin('run', **RUN, effects=['write'])

        def late(db):
            raise ValueError("The run's budget ran out before it was dispatched; nothing was dispatched")
        with self.assertRaisesRegex(ValueError, 'budget ran out before it was dispatched'):
            self.record.begin('run', **RUN, effects=['write'], before_mark=late)
        self.assertIsNone(self.record.begin('run', **RUN, effects=['write'], before_mark=lambda db: False))
        self.refused('run', **RUN, effects=['write'], before_mark=lambda db: None)
        self.assertEqual(self.journal(), [('run', 'tool.started')])

    def test_01_B16_a_flag_with_no_pending_effect_refuses_a_write_and_the_start_of_a_read_leaves_it(self):
        for owner_id, call, table in OWNERS:
            with self.subTest(owner=owner_id):
                self.fresh()
                self.change(f'UPDATE {table} SET uncertain=1')
                self.assertEqual(self.record.pending(owner_id), [])
                self.refused(owner_id, **call, effects=['write'])
                self.refused(owner_id, **call, effects=['read'], condition=True)
                self.record.begin(owner_id, **call, effects=['read'])
                self.assertIs(self.record.uncertain(owner_id, kind=call['kind']), True)
                self.assertEqual(self.record.pending(owner_id), [])


class AbandonTests(RecordCase):
    def test_01_B10_a_write_of_a_run_that_ends_without_a_result_is_recorded_as_abandoned_and_stays_pending(self):
        dispatch = self.record.begin('run', **RUN, effects=['write'])
        kept = self.record.abandon('run', dispatch, name='interface_execute', actor='worker', reason='raised',
                                   stage='probe', assignment_id='probe-1',
                                   error=('ToolProcessExit', 'exit status 1'))
        self.assertIs(kept, True)
        [(_, payload)] = self.events('run', 'tool.abandoned')
        self.assertEqual(payload, {'stage': 'probe', 'assignment_id': 'probe-1', 'name': 'interface_execute',
                                   'dispatch_id': str(dispatch), 'reason': 'raised', 'type': 'ToolProcessExit',
                                   'message': 'exit status 1', 'actor': 'worker'})
        self.assertEqual(self.journal(), [('run', 'tool.started'), ('run', 'tool.abandoned')])
        self.assertEqual([effect['dispatch_id'] for effect in self.record.pending('run')], [dispatch])
        self.assertIs(self.record.uncertain('run', kind='run'), True)

    def test_01_B10_an_abandoned_actuation_of_a_session_is_recorded_too_with_no_stage_and_no_assignment(self):
        dispatch = self.record.begin('session', **SESSION, effects=['actuate'])
        kept = self.record.abandon('session', dispatch, name='measure', actor='client', reason='raised',
                                   error=('OSError', 'The checked copy of the package could not be read back'))
        self.assertIs(kept, True)
        [(_, payload)] = self.events('session', 'tool.abandoned')
        self.assertEqual(payload, {'stage': None, 'assignment_id': None, 'name': 'measure',
                                   'dispatch_id': str(dispatch), 'reason': 'raised', 'type': 'OSError',
                                   'message': 'The checked copy of the package could not be read back',
                                   'actor': 'client'})
        self.assertEqual(self.journal(), [('session', 'tool.started'), ('session', 'tool.abandoned')])
        self.assertEqual([effect['dispatch_id'] for effect in self.record.pending('session')], [dispatch])
        self.assertIs(self.record.uncertain('session', kind='session'), True)

    def test_01_B10_a_call_the_service_stopped_is_recorded_with_its_reason_alone(self):
        dispatch = self.record.begin('run', **RUN, effects=['write'])
        self.record.abandon('run', dispatch, name='interface_execute', actor='worker', reason='timeout',
                            stage='probe', assignment_id='probe-1')
        [(_, payload)] = self.events('run', 'tool.abandoned')
        self.assertEqual(payload, {'stage': 'probe', 'assignment_id': 'probe-1', 'name': 'interface_execute',
                                   'dispatch_id': str(dispatch), 'reason': 'timeout', 'actor': 'worker'})

    def test_01_B6_an_abandoned_read_has_no_pending_effect(self):
        for owner_id, call, _ in OWNERS:
            with self.subTest(owner=owner_id):
                self.fresh()
                dispatch = self.record.begin(owner_id, **call, effects=['read', 'reset'])
                kept = self.record.abandon(owner_id, dispatch, name=call['name'], actor=call['actor'],
                                           reason='cancelled', stage=call.get('stage'),
                                           assignment_id=call.get('assignment_id'))
                self.assertIs(kept, False)
                self.assertEqual(self.journal(), [(owner_id, 'tool.started'), (owner_id, 'tool.abandoned')])
                self.assertEqual(self.record.pending(owner_id), [])
                self.assertIs(self.record.uncertain(owner_id, kind=call['kind']), False)

    def test_01_B6_only_the_abandoned_dispatch_itself_counts_not_another_pending_effect_of_its_owner(self):
        self.record.begin('run', **RUN, effects=['write'])
        read = self.record.begin('run', **RUN, effects=['read'])
        kept = self.record.abandon('run', read, name='interface_execute', actor='worker', reason='cancelled',
                                   stage='probe', assignment_id='probe-1')
        self.assertIs(kept, False)

    def test_01_B16_an_abandoned_read_leaves_a_flag_that_has_no_pending_effect(self):
        self.change('UPDATE device_sessions SET uncertain=1')
        dispatch = self.record.begin('session', **SESSION, effects=['read'])
        kept = self.record.abandon('session', dispatch, name='measure', actor='client', reason='shutdown')
        self.assertIs(kept, False)
        self.assertIs(self.record.uncertain('session', kind='session'), True)


class StatementTests(RecordCase):
    def resolve(self, owner_id, kind, effect_resolution='confirmed_safe', observation=STATEMENT, **more):
        return self.record.resolve_by_operator(owner_id, kind=kind, effect_resolution=effect_resolution,
                                               observation=observation, **more)

    def test_01_B14_one_complete_statement_resolves_the_uncertain_effect_of_a_run_and_of_a_session(self):
        for owner_id, call, _ in OWNERS:
            with self.subTest(owner=owner_id):
                self.fresh()
                write = self.record.begin(owner_id, **call, effects=['write'])
                self.assertEqual(self.resolve(owner_id, call['kind']), [str(write)])
                self.assertEqual(self.state(owner_id, call['kind']), ([], False))
                again = self.record.begin(owner_id, **call, effects=['actuate'])
                self.assertEqual(self.state(owner_id, call['kind']), ([again], True))

    def test_01_B14_a_statement_resolves_only_the_owner_it_names(self):
        self.record.begin('run', **RUN, effects=['write'])
        held = self.record.begin('session', **SESSION, effects=['write'])
        self.resolve('run', 'run')
        self.assertEqual(self.state('session', 'session'), ([held], True))

    def test_01_B14_an_incomplete_statement_is_refused_with_one_text_and_changes_nothing(self):
        def without(key):
            return {name: value for name, value in STATEMENT.items() if name != key}
        for label, resolution, observation in (
                ('no resolution', None, STATEMENT),
                ('another resolution', 'retry', STATEMENT),
                ('the resolution in other letters', 'Confirmed_Safe', STATEMENT),
                ('no observation', 'confirmed_safe', None),
                ('an observation that is text', 'confirmed_safe', json.dumps(STATEMENT)),
                ('an observation that is a list', 'confirmed_safe', [STATEMENT]),
                ('no source', 'confirmed_safe', without('source')),
                ('an empty source', 'confirmed_safe', {**STATEMENT, 'source': ''}),
                ('a blank source', 'confirmed_safe', {**STATEMENT, 'source': ' \t\n'}),
                ('a source that is a number', 'confirmed_safe', {**STATEMENT, 'source': 7}),
                ('a source that is a list', 'confirmed_safe', {**STATEMENT, 'source': ['operator']}),
                ('a source that is true', 'confirmed_safe', {**STATEMENT, 'source': True}),
                ('no message', 'confirmed_safe', without('message')),
                ('a blank message', 'confirmed_safe', {**STATEMENT, 'message': '   '}),
                ('a message that is an object', 'confirmed_safe', {**STATEMENT, 'message': {'text': 'off'}}),
                ('no word that the earlier execution stopped', 'confirmed_safe', without('prior_execution_stopped')),
                ('an earlier execution that did not stop', 'confirmed_safe',
                 {**STATEMENT, 'prior_execution_stopped': False}),
                ('true as text', 'confirmed_safe', {**STATEMENT, 'prior_execution_stopped': 'true'}),
                ('a number for true', 'confirmed_safe', {**STATEMENT, 'prior_execution_stopped': 1})):
            for owner_id, call, _ in OWNERS:
                with self.subTest(statement=label, owner=owner_id):
                    self.fresh()
                    write = self.record.begin(owner_id, **call, effects=['write'])
                    with self.assertRaises(ValueError) as refusal:
                        self.resolve(owner_id, call['kind'], resolution, observation)
                    self.assertEqual(str(refusal.exception), INCOMPLETE_STATEMENT)
                    self.assertEqual(self.state(owner_id, call['kind']), ([write], True))

    def test_01_B14_a_complete_statement_for_an_owner_that_is_not_uncertain_lists_no_dispatch(self):
        for owner_id, call, _ in OWNERS:
            with self.subTest(owner=owner_id):
                self.assertEqual(self.resolve(owner_id, call['kind']), [])
                self.assertEqual(self.state(owner_id, call['kind']), ([], False))

    def test_01_B14_an_incomplete_statement_is_refused_also_for_an_owner_that_is_not_uncertain(self):
        with self.assertRaises(ValueError) as refusal:
            self.resolve('run', 'run', observation={**STATEMENT, 'source': ''})
        self.assertEqual(str(refusal.exception), INCOMPLETE_STATEMENT)

    def test_01_B14_in_the_callers_transaction_the_resolution_is_kept_or_dropped_with_the_callers_event(self):
        write = self.record.begin('run', **RUN, effects=['write'])
        with self.assertRaisesRegex(RuntimeError, 'scripted failure of the caller'):
            with self.connect() as db:
                self.write_event('run', 'operator.response',
                                 {'resolved_dispatch_ids': self.resolve('run', 'run', db=db)}, db)
                raise RuntimeError('scripted failure of the caller')
        self.assertEqual(self.state('run', 'run'), ([write], True))
        self.assertEqual(self.events('run', 'operator.response'), [])
        with self.connect() as db:
            self.write_event('run', 'operator.response',
                             {'resolved_dispatch_ids': self.resolve('run', 'run', db=db)}, db)
            self.assertEqual(self.pending_ids('run'), [write], 'nothing is resolved before the caller commits')
        self.assertEqual(self.state('run', 'run'), ([], False))
        [(_, payload)] = self.events('run', 'operator.response')
        self.assertEqual(payload, {'resolved_dispatch_ids': [str(write)]})

    def test_01_B16_a_flag_with_no_pending_effect_stays_until_the_statement_which_lists_no_dispatch(self):
        for owner_id, call, table in OWNERS:
            with self.subTest(owner=owner_id):
                self.fresh()
                self.change(f'UPDATE {table} SET uncertain=1')
                with self.assertRaises(ValueError):
                    self.resolve(owner_id, call['kind'], observation={**STATEMENT, 'prior_execution_stopped': False})
                self.assertEqual(self.state(owner_id, call['kind']), ([], True))
                self.assertEqual(self.resolve(owner_id, call['kind']), [])
                self.assertEqual(self.state(owner_id, call['kind']), ([], False))
                self.record.begin(owner_id, **call, effects=['write'])


class ReplacementTests(RecordCase):
    def test_01_B15_a_verified_replacement_resolves_the_runs_pending_effects_with_the_callers_handoff(self):
        write = self.record.begin('run', **RUN, effects=['write'])
        held = self.record.begin('session', **SESSION, effects=['write'])
        with self.connect() as db:
            resolved = self.record.resolve_by_replacement('run', db)
            self.write_event('run', 'stage.accepted', {}, db)
            self.assertEqual(self.pending_ids('run'), [write], 'nothing is resolved before the caller commits')
        self.assertEqual(resolved, [str(write)])
        self.assertEqual(self.state('run', 'run'), ([], False))
        self.assertEqual(self.state('session', 'session'), ([held], True))
        self.assertEqual(self.journal()[-1], ('run', 'stage.accepted'))
        self.record.begin('run', **RUN, effects=['write'])

    def test_01_B15_a_replacement_whose_handoff_is_not_stored_resolves_nothing(self):
        write = self.record.begin('run', **RUN, effects=['write'])
        with self.assertRaisesRegex(RuntimeError, 'scripted failure of the caller'):
            with self.connect() as db:
                self.record.resolve_by_replacement('run', db)
                raise RuntimeError('scripted failure of the caller')
        self.assertEqual(self.state('run', 'run'), ([write], True))


class LockTests(RecordCase):
    """The record takes the configurator's lock for what it does in a transaction of its own, and none inside the
    caller's transaction."""

    def returns_while_locked(self, call):
        """Whether `call` returns while another thread holds the lock. Once the lock is free it must return."""
        held, release, returned = threading.Event(), threading.Event(), threading.Event()

        def hold():
            with self.lock:
                held.set()
                release.wait(30)

        def run():
            call()
            returned.set()
        holder, caller = threading.Thread(target=hold), threading.Thread(target=run)
        holder.start()
        try:
            self.assertTrue(held.wait(30))
            caller.start()
            early = returned.wait(0.3)
        finally:
            release.set()
            holder.join(30)
        caller.join(30)
        self.assertTrue(returned.is_set(), 'the call returns once the lock is free')
        return early

    def test_01_B11_a_dispatch_starts_only_under_the_configurators_lock(self):
        self.assertFalse(self.returns_while_locked(lambda: self.record.begin('run', **RUN, effects=['write'])))
        self.assertEqual(self.journal(), [('run', 'tool.started')])

    def test_01_B14_in_a_transaction_of_its_own_the_record_waits_for_the_lock(self):
        for method, call in (
                ('uncertain', lambda: self.record.uncertain('run', kind='run')),
                ('pending', lambda: self.record.pending('run')),
                ('resolve_by_operator', lambda: self.record.resolve_by_operator(
                    'run', kind='run', effect_resolution='confirmed_safe', observation=STATEMENT))):
            with self.subTest(method=method):
                self.assertFalse(self.returns_while_locked(call))

    def test_01_B14_in_the_callers_transaction_the_record_takes_no_lock(self):
        def in_a_transaction(method):
            def call():
                with self.connect() as db:
                    method(db)
            return call
        for method, call in (
                ('uncertain', lambda db: self.record.uncertain('run', kind='run', db=db)),
                ('pending', lambda db: self.record.pending('run', db)),
                ('resolve_by_operator', lambda db: self.record.resolve_by_operator(
                    'run', kind='run', effect_resolution='confirmed_safe', observation=STATEMENT, db=db)),
                ('resolve_by_replacement', lambda db: self.record.resolve_by_replacement('run', db))):
            with self.subTest(method=method):
                self.assertTrue(self.returns_while_locked(in_a_transaction(call)))


class StoredTests(RecordCase):
    def test_01_B5_a_record_built_again_on_the_same_database_keeps_what_the_first_stored(self):
        write = self.record.begin('run', **RUN, effects=['write'])
        again = DispatchRecord(self.connect, self.lock, self.write_event)
        self.assertEqual(again.pending('run'), [{'dispatch_id': write, 'assignment_id': 'probe-1', 'actor': 'worker'}])
        self.assertIs(again.uncertain('run', kind='run'), True)

    def test_01_B14_pending_effects_a_database_already_holds_are_read_in_dispatch_order_and_resolved_together(self):
        self.fresh(STORED_BEFORE)
        self.assertEqual(self.record.pending('run'), [
            {'dispatch_id': 7, 'assignment_id': 'probe-0', 'actor': 'worker'},
            {'dispatch_id': 9, 'assignment_id': 'probe-1', 'actor': 'worker'}])
        self.assertEqual(self.record.pending('session'), [{'dispatch_id': 8, 'assignment_id': None, 'actor': 'client'}])
        resolved = self.record.resolve_by_operator('run', kind='run', effect_resolution='confirmed_safe',
                                                   observation=STATEMENT)
        self.assertEqual(resolved, ['7', '9'])
        self.assertEqual(self.state('run', 'run'), ([], False))
        self.assertEqual(self.state('session', 'session'), ([8], True))


if __name__ == '__main__':
    unittest.main()

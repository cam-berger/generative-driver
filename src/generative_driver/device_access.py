"""Configurator-owned leases and dispatch records for stages and package sessions.

What an operation of a package does is read from the package's interface model, the file of its driver under
`drivers/`, the one statement of that driver (docs/adr/0010-a-package-is-data-in-one-format.md, decision 3).
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid


class DeviceAccess:
    """One owner of binding claims and finalized-package use. Its dispatches go through the configurator's dispatch
    record, which owns the pending effects."""

    def __init__(self, owner):
        self.owner = owner
        with owner._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS device_sessions (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE, spec TEXT NOT NULL,
                status TEXT NOT NULL, cancelled INTEGER DEFAULT 0, uncertain INTEGER DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS device_invocations (
                session_id TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL,
                dispatch_id INTEGER NOT NULL, result TEXT, created REAL NOT NULL,
                PRIMARY KEY(session_id,request_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS device_cancellations (
                owner_id TEXT NOT NULL, request_id TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(owner_id, request_id))''')
            unfinished = db.execute('''SELECT session_id,dispatch_id,uncertain FROM device_invocations
                                       JOIN device_sessions ON device_sessions.id=session_id
                                       WHERE result IS NULL AND status!='closed' ''').fetchall()
            abandoned = {(event['run_id'], json.loads(event['payload']).get('dispatch_id')) for event in db.execute(
                "SELECT run_id,payload FROM events WHERE kind='tool.abandoned'")} if unfinished else set()
            # An invocation was in flight when the owner stopped if it has no result and its dispatch was not
            # recorded as abandoned. Its session is blocked, and is as uncertain as it was.
            interrupted = {invocation['session_id']: invocation['uncertain'] for invocation in unfinished
                           if (invocation['session_id'], str(invocation['dispatch_id'])) not in abandoned}
            for session_id, uncertain in interrupted.items():
                db.execute("UPDATE device_sessions SET status='blocked',updated=? WHERE id=?",
                           (time.time(), session_id))
                owner._event(session_id, 'device.recovered', {
                    'reason': 'Owner restarted with an unfinished invocation; confirm prior execution has stopped before reconciliation',
                    'uncertain_effect': bool(uncertain)}, db)

    @contextmanager
    def lease(self, owner_id):
        owner = self.owner
        with owner._lock:
            operation_lock = owner._operations.setdefault(owner_id, threading.RLock())
        with operation_lock, owner._active_operation(owner_id):
            yield

    def claim_binding(self, binding, owner_id, db):
        if not binding:
            return
        from .configurator import binding_identity, _json
        identity = hashlib.sha256(_json(binding_identity(binding)).encode()).hexdigest()
        self._claim(identity, owner_id, db, 'Device binding is already owned by another run or session')

    def claim_probe(self, owner_id, db):
        """Claim the one lease that the runs which declare a debug probe share."""
        from .debug_probe import LEASE, TAKEN
        self._claim(LEASE, owner_id, db, TAKEN)

    def _claim(self, identity, owner_id, db, refusal):
        """Give `owner_id` the lease `identity`, or raise `refusal` while another owner that is still on holds it."""
        from .configurator import SafetyError
        prior = db.execute('SELECT run_id FROM bindings WHERE identity=?', (identity,)).fetchone()
        if prior and prior['run_id'] != owner_id:
            key = prior['run_id']
            row = db.execute('SELECT status,uncertain FROM runs WHERE id=?', (key,)).fetchone()
            on = ('queued', 'running', 'open')
            if row is None:
                row = db.execute('SELECT status,uncertain FROM device_sessions WHERE id=?', (key,)).fetchone()
                # The call that blocked a session may still run in a child of an earlier owner: the session, and
                # only a session, holds its lease until it is closed.
                on += ('blocked',)
            if row and (row['status'] in on or row['uncertain']
                        or key in self.owner._workers or key in self.owner._inflight):
                raise SafetyError(refusal)
        db.execute('INSERT OR REPLACE INTO bindings VALUES(?,?)', (identity, owner_id))

    @staticmethod
    def _effectful(effect, channel, grants):
        """Refuse an operation whose effect the operator has not granted. Opening a transport that resets the device
        needs the `reset` grant even for a read, and nothing more: a reset marks nothing."""
        resets = channel.get('resets_on_open') is True
        if (effect != 'read' and effect not in grants) or (resets and 'reset' not in grants):
            raise ValueError('Operation or transport opening requires an effect grant the operator has not supplied')

    def _unstopped(self, owner_id, request_id):
        """What the dispatch record asks before it marks the dispatch of an invocation: refuse it while the
        configurator is stopping, and give it up without an error when its request was cancelled while it was queued."""
        def before_mark(db):
            if self.owner._closing:
                raise ValueError('Device operation requires active ownership')
            return db.execute('SELECT 1 FROM device_cancellations WHERE owner_id=? AND request_id=?',
                              (owner_id, request_id)).fetchone() is None
        return before_mark

    @staticmethod
    def _invocation_row(owner_id, request_id, payload):
        """What the dispatch record writes with the start of a dispatch: the row of its invocation, with no result."""
        def after_start(db, dispatch_id):
            db.execute('INSERT INTO device_invocations VALUES(?,?,?,?,NULL,?)',
                       (owner_id, request_id, payload, dispatch_id, time.time()))
        return after_start

    @staticmethod
    def _saved_result(owner_id, request_id, dispatch_id, result):
        """What the dispatch record writes with the end of a dispatch: the result of its invocation, with its ids."""
        def after_finish(db, evidence_id):
            saved = {**result, 'session_id': owner_id, 'request_id': request_id,
                     'dispatch_id': str(dispatch_id), 'evidence_id': str(evidence_id)}
            db.execute('UPDATE device_invocations SET result=? WHERE session_id=? AND request_id=?',
                       (json.dumps(saved, allow_nan=False), owner_id, request_id))
        return after_finish

    def _session(self, session_id):
        with self.owner._db() as db:
            row = db.execute('SELECT * FROM device_sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown device session')
        return row, json.loads(row['spec'])

    def open(self, params):
        from .configurator import _json
        from ._toolchain.interface import checked_copy, refusal_text
        from interface_runtime.package import digest, the_driver
        from interface_runtime.transports import validate_binding
        package = Path(params['package_dir']).expanduser().resolve()
        grants = params.get('effects', ['read'])
        if type(grants) is not list or any(grant not in ('read', 'write', 'actuate', 'reset') for grant in grants):
            raise ValueError('Session effects must be explicit read/write/actuate/reset grants')
        binding = params.get('binding')
        pin = params.get('expected_manifest_sha256') or digest(package/'manifest.json')
        configuration = {'source': str(package), 'manifest_sha256': pin, 'binding': binding, 'effects': grants}
        request_id = params.get('request_id') or uuid.uuid4().hex
        with self.owner._lock:
            with self.owner._db() as db:
                previous = db.execute('SELECT * FROM device_sessions WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                saved = json.loads(previous['spec'])
                if saved['configuration'] != configuration:
                    raise ValueError('Device session request ID conflicts with its saved configuration')
                return {'ok': True, 'session_id': previous['id'], 'manifest_sha256': pin}
            session_id = uuid.uuid4().hex
            storage = self.owner.home/'devices'/session_id
            storage.mkdir(parents=True)
            try:
                # The session keeps a checked copy of the package, so a package changed later does not change it.
                snapshot, checked, refused = checked_copy(package, None, pin, storage)
                if refused is not None:
                    raise ValueError(refusal_text(refused))
                validate_binding(the_driver(checked)[1]['channel'], binding)
                spec = {'configuration': configuration, 'package_dir': str(snapshot),
                        'manifest_sha256': pin, 'binding': binding, 'effects': grants}
                with self.owner._db() as db:
                    db.execute('INSERT INTO device_sessions(id,request_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)',
                               (session_id, request_id, _json(spec), 'open', time.time(), time.time()))
                    self.claim_binding(binding, session_id, db)
            except Exception:
                shutil.rmtree(storage)
                raise
        return {'ok': True, 'session_id': session_id, 'manifest_sha256': pin}

    def _invocation(self, owner_id, request_id, operation, parameters, uncertain):
        if type(request_id) is not str or not 1 <= len(request_id) <= 128:
            raise ValueError('Device execution requires a bounded unique request_id')
        payload = json.dumps({'operation': operation, 'parameters': parameters}, sort_keys=True, allow_nan=False)
        with self.owner._db() as db:
            previous = db.execute('SELECT * FROM device_invocations WHERE session_id=? AND request_id=?',
                                  (owner_id, request_id)).fetchone()
        if previous is None:
            with self.owner._db() as db:
                cancelled = db.execute('SELECT result FROM device_cancellations WHERE owner_id=? AND request_id=?', (owner_id, request_id)).fetchone()
                if cancelled:
                    result = {**json.loads(cancelled['result']), 'operation': operation}
                    db.execute('UPDATE device_cancellations SET result=? WHERE owner_id=? AND request_id=?', (json.dumps(result), owner_id, request_id))
                    return payload, result
            return payload, None
        if previous['payload'] != payload:
            raise ValueError('Invocation request ID conflicts with its saved operation or parameters')
        if previous['result'] is not None:
            return payload, json.loads(previous['result'])
        return payload, {'schema': 'driver-result/1', 'operation': operation, 'ok': False,
            'outputs': {}, 'warnings': [], 'session_id': owner_id, 'request_id': request_id,
            'dispatch_id': str(previous['dispatch_id']), 'uncertain_effect': bool(uncertain),
            'error': {'code': 'invocation_pending', 'fault': 'host',
                      'message': 'Invocation has no durable result; inspect its status instead of resending a command.'}}

    def execute(self, params):
        from interface_runtime.package import the_driver
        session_id = params['session_id']
        request_id = params.get('request_id')
        with self.lease(session_id):
            row, spec = self._session(session_id)
            if params.get('expected_manifest_sha256', spec['manifest_sha256']) != spec['manifest_sha256']:
                raise ValueError('Client package identity differs from the managed session package')
            request_payload, previous = self._invocation(session_id, request_id, params['operation'],
                                                         params.get('parameters', {}), row['uncertain'])
            if previous is not None:
                return previous
            package = Path(spec['package_dir'])
            model = the_driver(self._checked(package, spec['manifest_sha256']))[1]
            operation = params['operation']
            if operation not in model['operations']:
                raise ValueError('Unknown public operation')
            effect = model['operations'][operation]['effect']
            self._effectful(effect, model['channel'], spec['effects'])
            dispatch = self.owner.dispatch_record.begin(
                session_id, kind='session', name=operation, actor='client', effects=[effect],
                before_mark=self._unstopped(session_id, request_id),
                after_start=self._invocation_row(session_id, request_id, request_payload))
            if dispatch is None:
                return self._invocation(session_id, request_id, operation, params.get('parameters', {}), row['uncertain'])[1]
            try:
                result = self.invoke_package(package, spec['manifest_sha256'], operation,
                                             params.get('parameters', {}), spec['binding'], spec['effects'])
            except Exception as error:
                # The call ended without a result: it is recorded as abandoned, and the client still gets the error.
                self.owner.dispatch_record.abandon(session_id, dispatch, name=operation, actor='client',
                                                   reason='raised', error=(type(error).__name__, str(error)))
                raise
            evidence = self.owner.dispatch_record.finish(
                session_id, dispatch, result, kind='session', name=operation, actor='client', generated=True,
                after_finish=self._saved_result(session_id, request_id, dispatch, result))
            return {**result, 'session_id': session_id, 'request_id': request_id,
                    'dispatch_id': str(dispatch), 'evidence_id': str(evidence)}

    @staticmethod
    def _checked(package, pin):
        """The check of the package a session or an assignment holds, whose manifest must still have the hash `pin`."""
        from ._toolchain.interface import refusal_text
        from interface_runtime.package import verify_package
        checked = verify_package(package, expected_manifest_sha256=pin)
        if not checked['ok']:
            raise ValueError(refusal_text(checked))
        return checked

    def invoke_package(self, package, pin, operation, parameters, binding, effects):
        """One operation of the package on the installed runtime, in a child process over a checked copy of the
        package's data; callers hold the owner lease and journal dispatch."""
        from ._toolchain.interface import checked_copy, refusal_text, run_child
        with tempfile.TemporaryDirectory(dir=self.owner.home) as temporary:
            copy, _, refused = checked_copy(package, None, pin, temporary)
            if refused is not None:
                raise ValueError(refusal_text(refused))
            completed = run_child(copy, operation, parameters, binding, [grant for grant in effects if grant != 'read'])
            result = completed['stdout_json']
            if type(result) is not dict:
                result = {'schema': 'driver-result/1', 'operation': operation, 'ok': False,
                          'outputs': {}, 'warnings': [], 'error': {'code': completed['error_code'],
                          'message': completed['error_message'], 'fault': 'host'}}
            return result

    def worker_tool(self, method, params, run, assignment, marker=None):
        """Generated tool Adapter joins the run's existing lease and independent observer."""
        from interface_runtime.catalog import render_mcp
        from interface_runtime.package import verify_package, the_driver
        configured = assignment['device_mcp']
        package = Path(configured['package_dir'])
        self.owner._verify_inputs(assignment['inputs'])
        checked = verify_package(package, expected_manifest_sha256=configured['manifest_sha256'])
        if not checked['ok']:
            raise ValueError('Assigned generated MCP package has changed')
        catalog = render_mcp(checked['drivers'])
        if method in ('tools', 'mcp_tools'):
            if method == 'mcp_tools':
                self.owner._event(run['id'], 'mcp.catalog_served', {
                    'assignment_id': assignment['id'], **configured})
            return {'ok': True, 'tools': catalog['tools'], 'server': catalog['server']}
        if method != 'mcp_tool':
            raise ValueError('Generated package operations require the assigned MCP gateway')
        name = params.get('name')
        if name not in catalog['operations']:
            raise ValueError('Tool is not assigned to this worker')
        operation = catalog['operations'][name]['operation']
        arguments = params.get('arguments', {})
        model = the_driver(checked)[1]
        effect = model['operations'][operation]['effect']
        if effect != 'read' and effect not in assignment['effects']:
            raise ValueError('Operation requires an effect grant the operator has not supplied')
        request_id = params.get('request_id')
        request_payload, previous = self._invocation(run['id'], request_id, operation, arguments, run['uncertain'])
        if previous is not None:
            return previous
        from .configurator import _is_serial_binding
        channel = model['channel']
        self._effectful(effect, channel, assignment['effects'])
        binding = assignment.get('binding')
        if not binding:
            raise ValueError('No operator-owned device binding for this stage')
        if _is_serial_binding(binding):
            state = self.owner._binding_state(run['id'])
            usb = channel.get('type') == 'usb' and 'port' not in binding
            if 'reset' not in assignment['effects'] and (state['resets_on_open'] or not usb and not state['listened']):
                raise ValueError('Listen to the bound serial device first, or supply a reset grant')
            if state['resets_on_open'] and not channel.get('resets_on_open'):
                raise ValueError('The model omits resets_on_open observed on this device')
        spec = json.loads(run['spec'])
        progress = self.owner._workflow_progress(run['id']) if spec.get('device') else {}
        if spec.get('device') and binding != (progress.get('device') or {}).get('binding'):
            raise ValueError('Assigned binding differs from the supplied device')
        facts = {'connected': True, 'argument_sha256': {'package_dir': assignment['inputs'][str(package)]},
                 'operation': operation, 'parameters': arguments,
                 **({'device_session_id': (progress.get('device') or {}).get('session_id')} if spec.get('device') else {})}
        dispatch = self.owner.dispatch_record.begin(
            run['id'], kind='run', name=name, actor='worker', effects=[effect], stage=assignment['stage'],
            assignment_id=assignment['id'], facts=facts, before_mark=self._unstopped(run['id'], request_id),
            after_start=self._invocation_row(run['id'], request_id, request_payload))
        if dispatch is None:
            return self._invocation(run['id'], request_id, operation, arguments, run['uncertain'])[1]
        if marker is not None:
            marker.update(dispatched=dispatch)
        proof = {'schema': 'driver-mcp-call/1', 'tool_name': name,
                 'catalog_sha256': configured['catalog_sha256'], 'manifest_sha256': configured['manifest_sha256'],
                 'session_id': run['id'],
                 'package_sha256': assignment['inputs'][str(package)],
                 'assignment_id': assignment['id'], 'dispatch_id': str(dispatch),
                 'request_id': params.get('request_id')}
        invoke = lambda: self.invoke_package(package, configured['manifest_sha256'], operation, arguments, binding, assignment['effects'])
        undispatched = False
        if spec.get('device'):
            from .device_client import open_device
            result, undispatched = self.owner._device_call(run, assignment, 'package_execute',
                {'package_dir': str(package), 'operation': operation, 'parameters': arguments, 'mcp': proof},
                open_device(spec['device']), invoke, expected_session_id=(progress.get('device') or {}).get('session_id'))
        else:
            result = invoke()
        result = {**result, 'mcp': proof}
        evidence = self.owner.dispatch_record.finish(
            run['id'], dispatch, result, kind='run', name=name, actor='worker', generated=True,
            undispatched=undispatched, stage=assignment['stage'], assignment_id=assignment['id'], facts=facts,
            after_finish=self._saved_result(run['id'], request_id, dispatch, result))
        return {**result, 'session_id': run['id'], 'request_id': request_id,
                'dispatch_id': str(dispatch), 'evidence_id': str(evidence)}

    def status(self, params):
        session_id = params['session_id']
        with self.owner._lock:
            row, spec = self._session(session_id)
            response = {'ok': True, 'schema': 'device-session/1', 'session_id': session_id,
                        'status': row['status'], 'cancelled': bool(row['cancelled']),
                        'uncertain_effect': bool(row['uncertain']),
                        'in_flight': session_id in self.owner._inflight,
                        'manifest_sha256': spec['manifest_sha256'], 'effects': spec['effects']}
            if 'request_id' in params:
                with self.owner._db() as db:
                    invocation = db.execute('SELECT * FROM device_invocations WHERE session_id=? AND request_id=?',
                                            (session_id, params['request_id'])).fetchone()
                    cancelled = db.execute('SELECT result FROM device_cancellations WHERE owner_id=? AND request_id=?', (session_id, params['request_id'])).fetchone()
                response.update(request_id=params['request_id'],
                    invocation_status=('finished' if invocation['result'] is not None else
                                       'pending' if response['in_flight'] else 'interrupted') if invocation else 'finished' if cancelled else 'unknown',
                    result=json.loads(invocation['result']) if invocation and invocation['result'] else json.loads(cancelled['result']) if cancelled and not invocation else None)
            return response

    def describe(self, params):
        from interface_runtime.catalog import render_mcp
        from interface_runtime.contracts import contract_view
        from interface_runtime.package import verify_package, the_driver
        _, spec = self._session(params['session_id'])
        package = Path(spec['package_dir'])
        checked = verify_package(package, expected_manifest_sha256=spec['manifest_sha256'])
        if not checked['ok']:
            raise ValueError('Managed session package has changed')
        return {**self.status(params), 'contract': contract_view(the_driver(checked)[1]),
                'catalog': render_mcp(checked['drivers'])}

    def stop(self, params, *, close=False):
        session_id = params['session_id']
        with self.owner._lock, self.owner._db() as db:
            row = db.execute('SELECT * FROM device_sessions WHERE id=?', (session_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown device session')
            if close and (row['uncertain'] or session_id in self.owner._inflight):
                raise ValueError('Cannot close a session with an active invocation or uncertain effect')
            # A cancelled session that is blocked stays blocked, so that it keeps its lease until it is closed, and one
            # that is closed stays closed, so that no restart blocks it again.
            db.execute('UPDATE device_sessions SET status=?,cancelled=1,updated=? WHERE id=?',
                       ('closed' if close else row['status'] if row['status'] in ('blocked', 'closed') else 'cancelled',
                        time.time(), session_id))
            if close:
                db.execute('DELETE FROM bindings WHERE run_id=?', (session_id,))
            self.owner._event(session_id, 'device.closed' if close else 'device.cancelled', {}, db)
        return self.status(params)

    def reconcile(self, params):
        session_id = params['session_id']
        observation = params.get('observation')
        with self.owner._lock, self.owner._db() as db:
            row, spec = self._session(session_id)
            if row['status'] == 'closed' or not row['uncertain']:
                raise ValueError('Only an owned session with an uncertain outcome can be reconciled')
            if session_id in self.owner._inflight:
                raise ValueError('Prior invocation is still running; reconcile only after it stops')
            resolved = self.owner.dispatch_record.resolve_by_operator(
                session_id, kind='session', effect_resolution=params.get('effect_resolution'), observation=observation,
                db=db)
            evidence = self.owner._event(session_id, 'device.reconciled', {
                'observation': observation, 'effect_resolution': 'confirmed_safe',
                'resolved_dispatch_ids': resolved}, db)
            interrupted = db.execute('SELECT * FROM device_invocations WHERE session_id=? AND result IS NULL',
                                     (session_id,)).fetchall()
            for invocation in interrupted:
                result = {'schema': 'driver-result/1', 'operation': json.loads(invocation['payload'])['operation'],
                    'ok': False, 'outputs': {}, 'warnings': [], 'session_id': session_id,
                    'request_id': invocation['request_id'], 'dispatch_id': str(invocation['dispatch_id']),
                    'evidence_id': str(evidence), 'error': {'code': 'invocation_interrupted', 'fault': 'host',
                        'message': 'Original outcome remains unknown; operator confirmed execution stopped and inspected the device.'}}
                db.execute('UPDATE device_invocations SET result=? WHERE session_id=? AND request_id=?',
                           (json.dumps(result), session_id, invocation['request_id']))
            db.execute('UPDATE device_sessions SET status=?,updated=? WHERE id=?',
                       ('cancelled' if row['cancelled'] else 'open', time.time(), session_id))
        return self.status(params)

    def cancel_invocation(self, params, *, assignment=False):
        owner_id = params['run_id'] if assignment else params['session_id']
        request_id = params.get('request_id')
        if type(request_id) is not str or not 1 <= len(request_id) <= 128:
            raise ValueError('Cancellation requires a bounded request_id')
        with self.owner._lock, self.owner._db() as db:
            if assignment:
                current = db.execute('SELECT state,stage,payload FROM assignments WHERE id=? AND run_id=?',
                    (params.get('assignment_id'), owner_id)).fetchone()
                run = db.execute('SELECT stage,status,cancelled FROM runs WHERE id=?', (owner_id,)).fetchone()
                if (not current or not run or current['state'] != 'active' or run['status'] != 'running'
                        or run['cancelled'] or current['stage'] != run['stage']
                        or not json.loads(current['payload']).get('device_mcp')):
                    raise ValueError('Worker assignment is inactive or unknown')
            elif db.execute('SELECT id FROM device_sessions WHERE id=?', (owner_id,)).fetchone() is None:
                raise ValueError('Unknown device session')
            prior = db.execute('SELECT * FROM device_invocations WHERE session_id=? AND request_id=?',
                (owner_id, request_id)).fetchone()
            if prior and prior['result']:
                return json.loads(prior['result'])
            result = {'schema': 'driver-result/1', 'operation': json.loads(prior['payload'])['operation'] if prior else None,
                'ok': False, 'outputs': {}, 'warnings': [], 'session_id': owner_id, 'request_id': request_id,
                'error': {'code': 'cancelled', 'fault': 'operator', 'message': 'Invocation cancelled; inspect its durable status before any new intent.'}}
            if not prior:
                result.update(requested_command_dispatched=False, transport_open_attempted=False)
            else:
                pending = self.owner.dispatch_record.pending(owner_id, db)
                result.update(dispatch_id=str(prior['dispatch_id']), uncertain_effect=any(
                    effect['dispatch_id'] == prior['dispatch_id'] for effect in pending))
            db.execute('INSERT OR IGNORE INTO device_cancellations VALUES(?,?,?)', (owner_id, request_id, json.dumps(result)))
            self.owner._event(owner_id, 'device.invocation_cancelled', {'request_id': request_id,
                'assignment_id': params.get('assignment_id'), 'dispatch_id': str(prior['dispatch_id']) if prior else None}, db)
            return result

    def control(self, method, params):
        if method == 'device_cancel_invocation':
            return self.cancel_invocation(params)
        if method == 'device_open':
            return self.open(params)
        if method == 'device_execute':
            return self.execute(params)
        if method == 'device_describe':
            return self.describe(params)
        if method == 'device_status':
            return self.status(params)
        if method in ('device_cancel', 'device_close'):
            return self.stop(params, close=method == 'device_close')
        if method == 'device_reconcile':
            return self.reconcile(params)
        raise ValueError('Unknown device session action')

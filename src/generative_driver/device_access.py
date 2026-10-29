"""Configurator-owned leases and dispatch records for stages and package sessions."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import uuid


class DeviceAccess:
    """One owner of binding claims, pending effects and finalized-package use."""

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
            interrupted = db.execute('''SELECT DISTINCT session_id FROM device_invocations
                                        JOIN device_sessions ON device_sessions.id=session_id
                                        WHERE result IS NULL''').fetchall()
            for invocation in interrupted:
                session_id = invocation['session_id']
                db.execute("UPDATE device_sessions SET status='blocked',uncertain=1,updated=? WHERE id=?",
                           (time.time(), session_id))
                owner._event(session_id, 'device.recovered', {
                    'reason': 'Owner restarted with an unfinished invocation; confirm prior execution has stopped before reconciliation',
                    'uncertain_effect': True}, db)

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
        from .configurator import binding_identity, _json, SafetyError
        identity = hashlib.sha256(_json(binding_identity(binding)).encode()).hexdigest()
        prior = db.execute('SELECT run_id FROM bindings WHERE identity=?', (identity,)).fetchone()
        if prior and prior['run_id'] != owner_id:
            key = prior['run_id']
            row = db.execute('SELECT status,uncertain FROM runs WHERE id=?', (key,)).fetchone()
            if row is None:
                row = db.execute('SELECT status,uncertain FROM device_sessions WHERE id=?', (key,)).fetchone()
            if row and (row['status'] in ('queued', 'running', 'open') or row['uncertain']
                        or key in self.owner._workers or key in self.owner._inflight):
                raise SafetyError('Device binding is already owned by another run or session')
        db.execute('INSERT OR REPLACE INTO bindings VALUES(?,?)', (identity, owner_id))

    @staticmethod
    def _table(kind):
        return {'run': 'runs', 'session': 'device_sessions'}[kind]

    @staticmethod
    def _effectful(effect, channel, grants):
        """Opening a resetting transport is an effect even for a public read operation."""
        resets = channel.get('resets_on_open') is True
        if (effect != 'read' and effect not in grants) or (resets and 'reset' not in grants):
            raise ValueError('Operation or transport opening requires an effect grant the operator has not supplied')
        return effect != 'read' or resets

    def begin(self, owner_id, *, kind='run', stage=None, assignment_id=None,
              actor='worker', name, effectful=False, request_id=None, request_payload=None, facts=None):
        owner = self.owner
        table = self._table(kind)
        with owner._lock, owner._db() as db:
            row = db.execute(f'SELECT status,cancelled,uncertain FROM {table} WHERE id=?', (owner_id,)).fetchone()
            if owner._closing or row is None or row['cancelled'] or row['status'] not in ('running', 'open'):
                raise ValueError('Device operation requires active ownership')
            if kind == 'run' and assignment_id is not None:
                current = db.execute('SELECT state,payload FROM assignments WHERE id=? AND run_id=?', (assignment_id, owner_id)).fetchone()
                active = db.execute('SELECT stage FROM runs WHERE id=?', (owner_id,)).fetchone()
                saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (owner_id,)).fetchone()
                revision = json.loads(saved['payload']).get('revision', 0) if saved else 0
                if not current or current['state'] != 'active' or active['stage'] != stage or json.loads(current['payload']).get('revision', 0) != revision:
                    raise ValueError('Worker assignment became inactive before dispatch')
            if request_id is not None and db.execute('SELECT 1 FROM device_cancellations WHERE owner_id=? AND request_id=?', (owner_id, request_id)).fetchone():
                return None
            if effectful and row['uncertain']:
                raise ValueError('Outstanding effect is uncertain; reconcile it before another effectful operation')
            if effectful:
                db.execute(f'UPDATE {table} SET uncertain=1 WHERE id=?', (owner_id,))
            dispatch = owner._event(owner_id, 'tool.started', {'stage': stage,
                'assignment_id': assignment_id, 'actor': actor, 'name': name,
                'effectful': effectful, **(facts or {})}, db)
            if effectful:
                db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)',
                           (dispatch, owner_id, assignment_id, actor))
            if request_id is not None:
                db.execute('INSERT INTO device_invocations VALUES(?,?,?,?,NULL,?)',
                           (owner_id, request_id, request_payload, dispatch, time.time()))
            return dispatch

    def finish(self, owner_id, dispatch, result, *, kind='run', stage=None,
               assignment_id=None, actor='worker', name, effectful=False, artifacts=None, request_id=None, facts=None):
        owner = self.owner
        table = self._table(kind)
        success = result.get('_exit', 0) == 0 and bool(result.get('ok', result.get('available', True)))
        executions = result.get('results') if name == 'probe_run' else [result]
        refused_before_open = (type(executions) is list and bool(executions) and all(
            type(item) is dict and item.get('schema') in ('interface-result/1', 'driver-result/1')
            and item.get('transport_open_attempted') is False for item in executions))
        refused = (type(executions) is list and bool(executions) and all(
            type(item) is dict and item.get('schema') in ('interface-result/1', 'driver-result/1')
            and (item.get('transport_open_attempted') is False
                 or (item.get('schema') == 'driver-result/1'
                     and item.get('requested_command_dispatched') is False
                     and (item.get('error') or {}).get('code') == 'precondition_failed'
                     and (item.get('error') or {}).get('fault') == 'operator'
                     and item.get('observations') and all(observation.get('result', {}).get('ok') is True
                         for observation in item['observations']))) for item in executions))
        with owner._lock, owner._db() as db:
            row = db.execute(f'SELECT cancelled FROM {table} WHERE id=?', (owner_id,)).fetchone()
            acknowledged = result.get('fault') != 'host' if actor == 'evaluator' else success or refused
            reconciled = bool(effectful and not row['cancelled'] and acknowledged)
            if reconciled:
                db.execute('DELETE FROM pending_effects WHERE dispatch_id=?', (dispatch,))
                db.execute(f'UPDATE {table} SET uncertain=EXISTS(SELECT 1 FROM pending_effects WHERE run_id=?) WHERE id=?',
                           (owner_id, owner_id))
            payload = {'stage': stage, 'assignment_id': assignment_id, 'actor': actor,
                       'name': name, 'ok': success, 'dispatch_id': str(dispatch),
                       'effectful': effectful, 'effects_reconciled': reconciled, **(facts or {}),
                       'refused_before_open': refused_before_open}
            if actor == 'evaluator':
                payload['evaluation_event_id'] = str(dispatch)
            else:
                payload.update(result=result, artifacts=artifacts or {})
            evidence = owner._event(owner_id, 'tool.finished', payload, db)
            if request_id is not None:
                saved = {**result, 'session_id': owner_id, 'request_id': request_id,
                         'dispatch_id': str(dispatch), 'evidence_id': str(evidence)}
                db.execute('UPDATE device_invocations SET result=? WHERE session_id=? AND request_id=?',
                           (json.dumps(saved, allow_nan=False), owner_id, request_id))
            return evidence

    def _session(self, session_id):
        with self.owner._db() as db:
            row = db.execute('SELECT * FROM device_sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown device session')
        return row, json.loads(row['spec'])

    def open(self, params):
        from .configurator import _json
        from ._toolchain.interface import _snapshot
        from interface_runtime.evidence import read_json
        from interface_runtime.package import digest
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
                snapshot, manifest, refused = _snapshot(package, None, pin, storage)
                if refused is not None:
                    raise ValueError(str(refused))
                if manifest['schema'] != 'interface-package/2':
                    raise ValueError('Managed package use requires a driver-contract package')
                contract = read_json(snapshot/'driver/contract.json')
                protocol = read_json(snapshot/'driver/protocol.json')
                validate_binding(protocol['channel'], binding)
                spec = {'configuration': configuration, 'package_dir': str(snapshot),
                        'manifest_sha256': pin, 'contract_sha256': manifest['contract_sha256'],
                        'binding': binding, 'effects': grants}
                with self.owner._db() as db:
                    db.execute('INSERT INTO device_sessions(id,request_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)',
                               (session_id, request_id, _json(spec), 'open', time.time(), time.time()))
                    self.claim_binding(binding, session_id, db)
            except Exception:
                shutil.rmtree(storage)
                raise
        return {'ok': True, 'session_id': session_id, 'manifest_sha256': pin,
                'contract_sha256': manifest['contract_sha256']}

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
        from interface_runtime.evidence import read_json
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
            contract = read_json(package/'driver/contract.json')
            operation = params['operation']
            if operation not in contract['operations']:
                raise ValueError('Unknown public operation')
            effect = contract['operations'][operation]['effect']
            effectful = self._effectful(effect, read_json(package/'driver/protocol.json')['channel'], spec['effects'])
            dispatch = self.begin(session_id, kind='session', actor='client',
                                  name=operation, effectful=effectful,
                                  request_id=request_id, request_payload=request_payload)
            if dispatch is None:
                return self._invocation(session_id, request_id, operation, params.get('parameters', {}), row['uncertain'])[1]
            result = self.invoke_package(package, spec['manifest_sha256'], operation,
                                         params.get('parameters', {}), spec['binding'], spec['effects'])
            evidence = self.finish(session_id, dispatch, result, kind='session', actor='client',
                                   name=operation, effectful=effectful, request_id=request_id)
            return {**result, 'session_id': session_id, 'request_id': request_id,
                    'dispatch_id': str(dispatch), 'evidence_id': str(evidence)}

    def invoke_package(self, package, pin, operation, parameters, binding, effects):
        """Trusted package subprocess; callers hold the owner lease and journal dispatch."""
        from ._toolchain.interface import _snapshot, _live_call, BOOT
        with tempfile.TemporaryDirectory(dir=self.owner.home) as temporary:
            snapshot, manifest, refused = _snapshot(package, None, pin, temporary)
            if refused is not None:
                raise ValueError(str(refused))
            command = [sys.executable, '-I', '-S', '-B', '-c', BOOT, str(snapshot),
                       'execute', operation, '--parameters', json.dumps(parameters, allow_nan=False),
                       '--binding', json.dumps(binding, allow_nan=False)]
            for grant in effects:
                if grant != 'read':
                    command += ['--allow-effect', grant]
            completed = _live_call(operation, command)
            result = completed['stdout_json']
            if type(result) is not dict:
                result = {'schema': 'driver-result/1', 'operation': operation, 'ok': False,
                          'outputs': {}, 'warnings': [], 'error': {'code': completed['error_code'],
                          'message': completed['error_message'], 'fault': 'host'}}
            return result

    def worker_tool(self, method, params, run, assignment, marker=None):
        """Generated tool Adapter joins the run's existing lease and independent observer."""
        from .configurator import _emulated
        from interface_runtime.package import verify_package, digest
        from interface_runtime.evidence import read_json
        configured = assignment['device_mcp']
        package = Path(configured['package_dir'])
        self.owner._verify_inputs(assignment['inputs'])
        checked = verify_package(package)
        if not checked['ok'] or digest(package/'manifest.json') != configured['manifest_sha256']:
            raise ValueError('Assigned generated MCP package has changed')
        catalog = read_json(package/'mcp-tools.json')
        if method in ('tools', 'mcp_tools'):
            if method == 'mcp_tools':
                self.owner._event(run['id'], 'mcp.catalog_served', {
                    'assignment_id': assignment['id'], **configured})
            return {'ok': True, 'tools': catalog['tools']}
        if method != 'mcp_tool':
            raise ValueError('Generated package operations require the assigned MCP gateway')
        name = params.get('name')
        if name not in catalog['operations']:
            raise ValueError('Tool is not assigned to this worker')
        operation = catalog['operations'][name]
        arguments = params.get('arguments', {})
        effect = read_json(package/'driver/contract.json')['operations'][operation]['effect']
        if effect != 'read' and effect not in assignment['effects']:
            raise ValueError('Operation requires an effect grant the operator has not supplied')
        request_id = params.get('request_id')
        request_payload, previous = self._invocation(run['id'], request_id, operation, arguments, run['uncertain'])
        if previous is not None:
            return previous
        from .configurator import _is_serial_binding
        channel = read_json(package/'driver/protocol.json')['channel']
        effectful = self._effectful(effect, channel, assignment['effects'])
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
        if _emulated(spec) and not spec.get('device'):
            raise ValueError('An emulated case runs only on the device the bench harness provides')
        progress = self.owner._workflow_progress(run['id']) if spec.get('device') else {}
        if spec.get('device') and binding != (progress.get('device') or {}).get('binding'):
            raise ValueError('Assigned binding differs from the provided device')
        facts = {'connected': True, 'argument_sha256': {'package_dir': assignment['inputs'][str(package)]},
                 'operation': operation, 'parameters': arguments,
                 **({'device_session_id': (progress.get('device') or {}).get('session_id')} if spec.get('device') else {})}
        dispatch = self.begin(run['id'], stage=assignment['stage'], assignment_id=assignment['id'],
                              name=name, effectful=effectful, request_id=request_id, request_payload=request_payload, facts=facts)
        if dispatch is None:
            return self._invocation(run['id'], request_id, operation, arguments, run['uncertain'])[1]
        if marker is not None:
            marker['dispatched'] = dispatch
        proof = {'schema': 'driver-mcp-call/1', 'tool_name': name,
                 'catalog_sha256': configured['catalog_sha256'], 'manifest_sha256': configured['manifest_sha256'],
                 'contract_sha256': configured['contract_sha256'], 'session_id': run['id'],
                 'package_sha256': assignment['inputs'][str(package)],
                 'assignment_id': assignment['id'], 'dispatch_id': str(dispatch),
                 'request_id': params.get('request_id')}
        invoke = lambda: self.invoke_package(package, configured['manifest_sha256'], operation, arguments, binding, assignment['effects'])
        if spec.get('device'):
            from .device_client import open_device
            result = self.owner._device_call(run, assignment, 'benchmark_package_execute',
                {'package_dir': str(package), 'operation': operation, 'parameters': arguments, 'mcp': proof},
                open_device(spec['device']), invoke, expected_session_id=(progress.get('device') or {}).get('session_id'))
        else:
            result = invoke()
        result = {**result, 'mcp': proof}
        evidence = self.finish(run['id'], dispatch, result, stage=assignment['stage'],
            assignment_id=assignment['id'], name=name, effectful=effectful, request_id=request_id, facts=facts)
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
                        'manifest_sha256': spec['manifest_sha256'],
                        'contract_sha256': spec['contract_sha256'], 'effects': spec['effects']}
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
        from interface_runtime.evidence import read_json
        from interface_runtime.package import verify_package, digest
        _, spec = self._session(params['session_id'])
        package = Path(spec['package_dir'])
        checked = verify_package(package)
        if not checked['ok'] or digest(package/'manifest.json') != spec['manifest_sha256']:
            raise ValueError('Managed session package has changed')
        return {**self.status(params), 'contract': read_json(package/'driver/contract.json'),
                'catalog': read_json(package/'mcp-tools.json')}

    def stop(self, params, *, close=False):
        session_id = params['session_id']
        with self.owner._lock, self.owner._db() as db:
            row = db.execute('SELECT * FROM device_sessions WHERE id=?', (session_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown device session')
            if close and (row['uncertain'] or session_id in self.owner._inflight):
                raise ValueError('Cannot close a session with an active invocation or uncertain effect')
            db.execute('UPDATE device_sessions SET status=?,cancelled=1,updated=? WHERE id=?',
                       ('closed' if close else 'cancelled', time.time(), session_id))
            if close:
                db.execute('DELETE FROM bindings WHERE run_id=?', (session_id,))
            self.owner._event(session_id, 'device.closed' if close else 'device.cancelled', {}, db)
        return self.status(params)

    def reconcile(self, params):
        session_id = params['session_id']
        observation = params.get('observation')
        if (params.get('effect_resolution') != 'confirmed_safe' or type(observation) is not dict
                or observation.get('prior_execution_stopped') is not True
                or not isinstance(observation.get('source'), str) or not observation['source'].strip()
                or not isinstance(observation.get('message'), str) or not observation['message'].strip()):
            raise ValueError('Reconciliation requires confirmed_safe and a sourced observation confirming prior execution stopped')
        with self.owner._lock, self.owner._db() as db:
            row, spec = self._session(session_id)
            if row['status'] == 'closed' or not row['uncertain']:
                raise ValueError('Only an owned session with an uncertain outcome can be reconciled')
            if session_id in self.owner._inflight:
                raise ValueError('Prior invocation is still running; reconcile only after it stops')
            pending = db.execute('SELECT dispatch_id FROM pending_effects WHERE run_id=?', (session_id,)).fetchall()
            evidence = self.owner._event(session_id, 'device.reconciled', {
                'observation': observation, 'effect_resolution': 'confirmed_safe',
                'resolved_dispatch_ids': [str(item['dispatch_id']) for item in pending]}, db)
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
            db.execute('DELETE FROM pending_effects WHERE run_id=?', (session_id,))
            db.execute('UPDATE device_sessions SET uncertain=0,status=?,updated=? WHERE id=?',
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
                result.update(dispatch_id=str(prior['dispatch_id']), uncertain_effect=True)
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

"""Durable configurator. Worker statements, accepted evidence and verdicts stay distinct."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid
import sys
import copy
import ipaddress
import tempfile
from contextlib import contextmanager

from .agents import StageRequest, execute

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse', 'maintain')
TERMINAL = {'completed', 'failed', 'cancelled', 'blocked'}
STAGE_TOOLS = {
    'acquire': ['acquire_firmware_artifact','acquire_datasheet','acquire_protocol_spec','acquire_classify'],
    'interpret': ['firmware_prepare','firmware_collect','firmware_validate','model_validate','model_provenance_audit','ghidra_run','image_map_check'],
    'probe': ['model_validate','model_qualify','probe_run','probe_diff','interface_describe','interface_execute'],
    'ground': ['probe_observe','probe_diff','model_validate'],
    'emit': ['emit_package','emit_check'],
    'reuse': ['interface_describe','interface_execute','emit_check','emit_test_live'],
    'maintain': ['probe_run','probe_diff','emit_check','emit_test_live','model_validate'],
}


def digest(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Artifact symlinks are not admissible')
    if path.is_file():
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        return hasher.hexdigest()
    if path.is_dir():
        entries = []
        for item in sorted(path.rglob('*')):
            if item.is_symlink():
                raise ValueError('Artifact symlinks are not admissible')
            if item.is_file():
                entries.append([item.relative_to(path).as_posix(), digest(item)])
        return hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()
    raise ValueError('Artifact does not exist: ' + str(path))


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def validate_scoped_approval(spec):
    scope=spec.get('scoped_tool_approval')
    if scope is None:
        return
    case=spec.get('case')
    case_id=case.get('id') if isinstance(case,dict) else case
    if scope=='bound-device':
        if not spec.get('binding'):
            raise ValueError('Bound-device approval requires an explicit operator binding')
        binding_identity(spec['binding'])
        if not spec.get('effects'):
            raise ValueError('Bound-device approval requires nonempty operator effect grants')
        return
    if scope!='emulator' or case_id!='tq9':
        raise ValueError('scoped_tool_approval supports only the tq9 emulator profile')
    if spec.get('binding') or spec.get('case_options',{}).get('binding') or (isinstance(case,dict) and case.get('options',{}).get('binding')):
        raise ValueError('Emulator tool approval cannot authorize an operator-supplied device binding')


def binding_identity(binding):
    """Lease physical endpoints, ignoring tuning knobs and permission flags.

    USB and FTDI selectors can omit a serial number, so the first release takes
    a conservative product-wide lease instead of risking overlapping selectors.
    """
    if not isinstance(binding,dict):
        raise ValueError('binding must be an operator-supplied object')
    if isinstance(binding.get('host'),str) and type(binding.get('port')) is int:
        host = binding['host'].strip().lower().rstrip('.')
        try:
            address = ipaddress.ip_address(host)
            host = 'loopback' if address.is_loopback else address.compressed
        except ValueError:
            if host == 'localhost': host = 'loopback'
        return ['tcp',host,binding['port']]
    if isinstance(binding.get('port'),str) and binding['port'].strip():
        port = binding['port'].strip()
        if port.upper().startswith(('COM','\\\\.\\COM')):
            port = port.upper().removeprefix('\\\\.\\')
        else:
            port = str(Path(port).expanduser().resolve())
        return ['serial',port]
    if isinstance(binding.get('url'),str) and binding['url'].lower().startswith('ftdi://'):
        # A selector may be a serial, bus address or index for the same adapter.
        # All FTDI connections share a lease until selector resolution is added.
        return ['ftdi']
    if type(binding.get('vendor_id')) is int and type(binding.get('product_id')) is int:
        return ['usb',binding['vendor_id'],binding['product_id']]
    raise ValueError('Unsupported binding shape; use host/port, serial port, FTDI url, or USB vendor_id/product_id')


def operation_effects(name, arguments):
    """Read declared operation effects before opening an operator-owned binding."""
    model_dir=arguments.get('model_dir')
    if model_dir:
        model_path=Path(model_dir)/'model.json'
    else:
        model_path=Path(arguments['package_dir'])/'driver/model.json'
    model=json.loads(model_path.read_text(encoding='utf-8'))
    operations=model.get('operations',{})
    if model.get('schema')=='interface-model/1':
        steps=[step for group in operations.values() for step in group]+model.get('state',[])
        return {'write'} if any(step.get('op') in ('reg_write','raw_write') for step in steps) else {'read'}
    names=arguments.get('operations') if name=='emit_test_live' else [arguments.get('operation')]
    if names is None:
        names=list(operations)
    if not names or any(item not in operations for item in names):
        raise ValueError('Select existing named operations before device dispatch')
    return {operations[item].get('effect','actuate') for item in names}


class Controller:
    """Public request interface; the daemon owns this object, clients do not."""
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.home / 'runs.sqlite3'
        self._lock = threading.RLock()
        self._workers = {}
        self._operations = {}
        self._inflight = set()
        self._closing = False
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE, spec TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT, reason TEXT, created REAL NOT NULL,
                    updated REAL NOT NULL, cancelled INTEGER DEFAULT 0, uncertain INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS handoffs (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS verdicts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings (
                    identity TEXT PRIMARY KEY, run_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS progress (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
            for row in db.execute("SELECT * FROM runs WHERE status IN ('queued','running')").fetchall():
                spec = json.loads(row['spec'])
                uncertain = row['uncertain'] or bool(set(spec.get('effects',[])) & {'write','actuate'})
                db.execute("UPDATE runs SET status='blocked',reason=?,uncertain=?,updated=? WHERE id=?",
                           ('Configurator restarted; explicit resume required', int(uncertain),time.time(),row['id']))
                db.execute("UPDATE assignments SET state='interrupted' WHERE run_id=? AND state='active'", (row['id'],))
                self._event(row['id'],'run.recovered',{'uncertain_effect':bool(uncertain)},db)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=20000')
            with db:
                yield db
        finally:
            db.close()

    def _event(self, run_id, kind, payload, db=None):
        if db is None:
            with self._db() as own:
                return self._event(run_id, kind, payload, own)
        db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                   (run_id, kind, _json(payload), time.time()))

    def _state(self, run_id, status, reason=None, stage=None):
        with self._db() as db:
            changed = db.execute('UPDATE runs SET status=?,reason=?,stage=COALESCE(?,stage),updated=? WHERE id=? AND (cancelled=0 OR ?=\'cancelled\')',
                       (status, reason, stage, time.time(), run_id,status))
            if changed.rowcount:
                if status in TERMINAL:
                    db.execute("UPDATE assignments SET state=? WHERE run_id=? AND state='active'",(status,run_id))
                self._event(run_id, 'run.' + status, {'stage': stage, 'reason': reason}, db)

    def _row(self, run_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown run_id')
        return row

    def call(self, method, params=None):
        params = params or {}
        if method == 'start':
            return self.start(params)
        run_id = params.get('run_id')
        row = self._row(run_id)
        if method in ('tool', 'tools'):
            with self._lock:
                operation_lock = self._operations.setdefault(run_id,threading.RLock())
            with operation_lock:
                if method == 'tools':
                    return self._tool(method,params,self._row(run_id))
                with self._active_operation(run_id):
                    return self._tool(method, params, self._row(run_id))
        if method == 'status':
            return {'ok': True, **{k: row[k] for k in ('status', 'stage', 'reason', 'created', 'updated')},
                    'run_id': run_id, 'uncertain_effect': bool(row['uncertain'])}
        if method == 'events':
            with self._db() as db:
                events = db.execute('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500',
                                    (run_id, int(params.get('after', 0)))).fetchall()
            return {'ok': True, 'run_id': run_id,
                    'events': [{'id': e['seq'], 'kind': e['kind'], 'data': json.loads(e['payload']), 'time': e['created']} for e in events],
                    'cursor': events[-1]['seq'] if events else int(params.get('after', 0))}
        if method == 'result':
            out = self.call('status', {'run_id': run_id})
            spec=json.loads(row['spec'])
            config=spec['executor_config']
            case=spec.get('case')
            out.update(case=case.get('id') if isinstance(case,dict) else case,
                scoped_tool_approval=spec.get('scoped_tool_approval'),
                budget_seconds=spec['budget_seconds'],limits={'max_model_repairs':spec.get('max_revisions',2),'max_maintenance_cycles':1},
                agent={'runtime':spec['executor'],'model':config.get('model'),
                    'provider':config.get('provider') or ('openai' if spec['executor']=='codex' else None),
                    'version':config.get('version'),'settings':{k:config[k] for k in ('reasoning_effort','max_turns') if k in config}})
            with self._db() as db:
                for table, key in (('reports', 'worker_reports'), ('handoffs', 'accepted_handoffs'), ('verdicts', 'evaluator_verdicts')):
                    out[key] = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM ' + table + ' WHERE run_id=? ORDER BY created', (run_id,))]
                saved = db.execute('SELECT payload FROM progress WHERE run_id=?',(run_id,)).fetchone()
                out['progress'] = json.loads(saved['payload']) if saved else None
            return out
        if method == 'cancel':
            with self._lock:
                with self._db() as db:
                    db.execute('UPDATE runs SET cancelled=1 WHERE id=?', (run_id,))
                if run_id in self._workers:
                    self._workers[run_id][0].set()
                self._state(run_id, 'cancelled', 'Cancellation requested; retain outstanding-effect uncertainty')
            return self.call('status', {'run_id': run_id})
        if method == 'respond':
            observation = params.get('observation')
            message = params.get('message') or (_json(observation) if isinstance(observation,dict) and observation else None)
            resolution = params.get('effect_resolution') or (observation.get('effect_resolution') if isinstance(observation,dict) else None)
            if not isinstance(message,str) or not message.strip():
                raise ValueError('respond requires an operator message')
            if row['status'] not in TERMINAL or row['status'] == 'completed':
                raise ValueError('Respond only while the run is stopped')
            with self._lock:
                if resolution is not None and (run_id in self._inflight or run_id in self._workers):
                    raise ValueError('Previous worker or operation is still stopping; reconcile after it finishes')
            with self._db() as db:
                if resolution is not None:
                    if resolution != 'confirmed_safe':
                        raise ValueError('effect_resolution must be confirmed_safe after checking the device')
                    db.execute('UPDATE runs SET uncertain=0 WHERE id=?',(run_id,))
                self._event(run_id,'operator.response',{'message':message,'observation':observation,'effect_resolution':resolution},db)
            return self.call('status', {'run_id': run_id})
        if method == 'resume':
            with self._lock:
                if self._closing:
                    raise ValueError('Configurator is stopping')
                if run_id in self._workers or run_id in self._inflight:
                    raise ValueError('Previous worker is still stopping')
                if row['status'] not in ('blocked','failed','cancelled'):
                    raise ValueError('Only a stopped incomplete run can resume')
                if row['uncertain']:
                    raise ValueError('Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation')
                spec = json.loads(row['spec'])
                if params.get('scoped_tool_approval') is not None:
                    spec['scoped_tool_approval']=params['scoped_tool_approval']
                    validate_scoped_approval(spec)
                if time.time() >= row['created'] + spec['budget_seconds']:
                    raise ValueError('Run budget exhausted; start a new run')
                for handoff in self.call('result',{'run_id':run_id})['accepted_handoffs']:
                    self._verify_inputs({a['path']:a['sha256'] for a in handoff['artifacts']})
                with self._db() as db:
                    self._claim_binding(spec.get('binding'),run_id,db)
                    if spec!=json.loads(row['spec']):
                        db.execute('UPDATE runs SET spec=? WHERE id=?',(_json(spec),run_id))
                        self._event(run_id,'run.authorization',{'scoped_tool_approval':spec['scoped_tool_approval'],
                            'scope':'Assigned tools only; saved binding and effect grants apply'},db)
                    db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'",(run_id,))
                    db.execute("UPDATE runs SET cancelled=0,status='queued',reason=NULL,updated=? WHERE id=?",(time.time(),run_id))
                    self._event(run_id,'run.resumed',{},db)
                self._spawn(run_id)
            return self.call('status',{'run_id':run_id})
        raise ValueError('Unknown method: ' + str(method))

    def start(self, params):
        goal = params.get('goal')
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError('start requires a nonempty goal')
        executor = params.get('executor', 'codex')
        if executor not in ('codex', 'goose'):
            raise ValueError('executor must be codex or goose')
        budget = params.get('budget_seconds', 10800)
        if type(budget) not in (int, float) or not 0 < budget <= 7 * 86400:
            raise ValueError('budget_seconds must be positive and no more than seven days')
        effects = params.get('effects', ['read'])
        if not isinstance(effects, list) or not set(effects) <= {'read', 'write', 'actuate'}:
            raise ValueError('effects must contain only read, write, actuate')
        config = {}
        config_path = self.home / 'config.json'
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding='utf-8')).get('executors', {}).get(executor, {})
        config = {**config, **params.get('executor_config', {}), 'runtime': executor}
        spec = {**params, 'executor': executor, 'executor_config': config, 'budget_seconds': budget, 'effects': effects}
        case=spec.get('case')
        option_bindings=[spec.get('case_options',{}).get('binding')]
        if isinstance(case,dict): option_bindings.append(case.get('options',{}).get('binding'))
        if spec.get('binding') and any(value and value!=spec['binding'] for value in option_bindings):
            raise ValueError('Operator and case options contain conflicting device bindings')
        validate_scoped_approval(spec)
        encoded = _json(spec)
        with self._lock:
            if self._closing:
                raise ValueError('Configurator is stopping')
            with self._db() as db:
                if params.get('request_id'):
                    found = db.execute('SELECT id,spec FROM runs WHERE request_id=?', (params['request_id'],)).fetchone()
                    if found:
                        if found['spec'] != encoded:
                            raise ValueError('request_id was already used with different inputs')
                        return {'ok': True, 'run_id': found['id'], 'duplicate': True}
                run_id = 'run-' + uuid.uuid4().hex[:16]
                self._claim_binding(spec.get('binding'),run_id,db)
                now = time.time()
                db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                           (run_id, params.get('request_id'), encoded, 'queued', 'acquire', now, now))
                db.execute('INSERT INTO progress VALUES(?,?)',(run_id,_json({'revision':0,'next_stage':'acquire','repairs':0,'maintenance_cycles':0,'feedback':None})))
                self._event(run_id, 'run.started', {'goal': goal, 'executor': executor, 'budget_seconds': budget}, db)
            self._spawn(run_id)
        return {'ok': True, 'run_id': run_id, 'duplicate': False}

    def _spawn(self, run_id):
        cancel = threading.Event()
        thread = threading.Thread(target=self._run, args=(run_id, cancel), daemon=True)
        self._workers[run_id] = (cancel, thread)
        thread.start()

    def _run(self, run_id, cancel):
        spec = None
        run_dir = self.home / 'runs' / run_id
        try:
            row = self._row(run_id)
            spec = json.loads(row['spec'])
            accepted = self.call('result', {'run_id': run_id})['accepted_handoffs']
            progress = self.call('result', {'run_id':run_id}).get('progress') or {'revision':0,'next_stage':row['stage'],'repairs':0,'maintenance_cycles':0,'feedback':None}
            revision = progress['revision']
            queue = list(STAGES[STAGES.index(progress['next_stage']):]) if progress['next_stage'] else []
            while queue:
                stage = queue.pop(0)
                if cancel.is_set():
                    return
                remaining = row['created'] + spec['budget_seconds'] - time.time()
                if remaining <= 0:
                    self._state(run_id, 'blocked', 'Run exhausted its wall-clock budget')
                    return
                attempt = uuid.uuid4().hex
                workspace = run_dir / 'work' / stage / attempt
                workspace.mkdir(parents=True, exist_ok=True)
                for prior in accepted:
                    self._verify_inputs({a['path']:a['sha256'] for a in prior['artifacts']})
                current_spec = {**spec, '_revision':revision, '_feedback':progress.get('feedback')}
                with self._db() as db:
                    responses=db.execute("SELECT payload FROM events WHERE run_id=? AND kind='operator.response' ORDER BY seq",(run_id,)).fetchall()
                current_spec['_operator_observations']=[value.get('observation') or {'message':value['message']} for value in (json.loads(r['payload']) for r in responses)]
                prepared = self._prepare(current_spec, stage, run_dir, workspace, accepted)
                if prepared.get('blocked'):
                    self._state(run_id,'blocked',prepared['blocked'],stage)
                    return
                workspace = Path(prepared.get('work_dir', workspace)).resolve()
                workspace.mkdir(parents=True, exist_ok=True)
                input_hashes = {str(Path(p).resolve()): digest(p) for p in prepared.get('inputs', [])}
                assignment = {'id': attempt, 'stage': stage, 'workspace': str(workspace), 'inputs': input_hashes,
                              'allowed_tools': prepared.get('allowed_tools', []), 'report_required': prepared.get('report_required', True),
                              'binding': prepared.get('binding', spec.get('binding')), 'effects': spec['effects'], 'revision': revision}
                if prepared.get('collection'):
                    assignment['collection'] = prepared['collection']
                with self._db() as db:
                    self._claim_binding(assignment.get('binding'),run_id,db)
                    db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                               (attempt, run_id, stage, 'active', _json(assignment), time.time()))
                    self._event(run_id, 'stage.assigned', assignment, db)
                self._state(run_id, 'running', stage=stage)
                prompt = prepared.get('prompt')
                if prompt is None:
                    prompt = ('Execute exactly the assigned stage. Do not start another stage. Return a JSON stage report. '
                              'Write report.json and include artifact paths and actual SHA-256 hashes. '
                              'Report checks honestly; use blocked if evidence or permission is missing.\n' + _json({
                                  'stage': stage, 'objective': prepared.get('objective', spec['goal']),
                                  'inputs': list(input_hashes), 'context': prepared.get('context', {}),
                                  'allowed_tools': assignment['allowed_tools'], 'effect_grants': spec['effects'],
                                  'report_schema': __import__('generative_driver.agents', fromlist=['REPORT_SCHEMA']).REPORT_SCHEMA}))
                gateway = {'command': sys.executable, 'args': ['-c',
                    'import sys; sys.path.insert(0,' + repr(str(Path(__file__).resolve().parents[1])) + '); from generative_driver.worker_tools import main; main()',
                    '--home',str(self.home),'--run',run_id,'--assignment',attempt]}
                result = execute(StageRequest(stage=stage, workspace=workspace, prompt=prompt,
                                budget_seconds=max(0.01, row['created'] + spec['budget_seconds'] - time.time()),
                                report_required=assignment['report_required'], allowed_tools=assignment['allowed_tools'],
                                gateway=gateway if assignment['allowed_tools'] else None,
                                approve_scoped_tools=spec.get('scoped_tool_approval') in ('emulator','bound-device')),
                                spec['executor_config'], cancel)
                record = {'assignment_id': attempt, 'stage': stage, 'revision':revision,'workspace': str(workspace), **result}
                with self._db() as db:
                    db.execute('INSERT INTO reports VALUES(?,?,?,?,?)', (attempt, run_id, stage, _json(record), time.time()))
                    self._event(run_id, 'worker.finished', {'stage':stage, 'runtime':result['runtime'], 'status':result['status'], 'usage':result.get('usage')}, db)
                if cancel.is_set():
                    return
                if result['status'] != 'completed':
                    self._state(run_id, result['status'], result.get('reason'))
                    return
                if self._row(run_id)['uncertain']:
                    self._state(run_id,'blocked','Outstanding effect is uncertain; operator reconciliation is required before further execution')
                    return
                report = result.get('report')
                if assignment['report_required'] and report.get('status') != 'completed' and not (spec.get('case') and report.get('status') == 'needs_revision'):
                    self._state(run_id, 'blocked', report.get('summary', 'Worker could not complete stage'))
                    return
                self._verify_inputs(input_hashes)
                checked = self._check(current_spec, stage, run_dir, workspace, report, accepted, assignment)
                if checked.get('evaluator') is not None:
                    with self._db() as db:
                        db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)', (attempt, run_id, stage,
                            _json({'stage':stage, 'assignment_id':attempt, **checked['evaluator']}), time.time()))
                if not checked.get('ok'):
                    if checked.get('route') == 'interpret' and checked.get('fault') == 'model' and stage in ('interpret','probe','ground'):
                        if progress['repairs'] >= int(spec.get('max_revisions',2)):
                            self._state(run_id,'blocked','Model repair budget exhausted: '+checked.get('reason','checks failed'))
                            return
                        progress.update(revision=revision+1,next_stage='interpret',repairs=progress['repairs']+1,feedback=checked.get('feedback'))
                        self._route(run_id,progress,checked.get('reason'))
                        revision = progress['revision']
                        queue = list(STAGES[1:])
                        continue
                    self._state(run_id, 'blocked', checked.get('reason', 'Independent stage checks failed'))
                    return
                route = checked.get('route')
                if route:
                    if stage != 'maintain' or route != 'interpret':
                        raise ValueError('Unsupported evaluator revision route')
                    if progress['maintenance_cycles'] >= 1:
                        self._state(run_id, 'blocked', 'Maintenance cycle limit reached')
                        return
                    revision += 1
                    progress.update(revision=revision,next_stage=route,maintenance_cycles=progress['maintenance_cycles']+1,feedback=checked.get('feedback'))
                    queue = list(STAGES[STAGES.index(route):])
                else:
                    progress['next_stage'] = queue[0] if queue else None
                handoff = self._accept(run_id, assignment, checked, run_dir, workspace, progress)
                accepted.append(handoff)
                if route:
                    self._event(run_id,'run.revision',{**progress,'reason':checked.get('reason')})
            self._state(run_id, 'completed', 'Seven stages accepted')
        except Exception as exc:
            self._state(run_id, 'failed', type(exc).__name__ + ': ' + str(exc))
        finally:
            if spec and spec.get('case'):
                try:
                    from .benchmark import cleanup
                    case=spec['case']
                    options=spec.get('case_options',{})
                    if isinstance(case,dict): options={**case.get('options',{}),**options}
                    cleanup(case['id'] if isinstance(case,dict) else case,run_dir,options)
                except Exception as exc:
                    self._event(run_id,'cleanup.failed',{'reason':type(exc).__name__+': '+str(exc)})
            with self._lock:
                with self._db() as db:
                    row = db.execute('SELECT uncertain FROM runs WHERE id=?',(run_id,)).fetchone()
                    if row and not row['uncertain'] and run_id not in self._inflight:
                        db.execute('DELETE FROM bindings WHERE run_id=?',(run_id,))
                self._workers.pop(run_id, None)

    @contextmanager
    def _active_operation(self,run_id):
        with self._lock:
            self._inflight.add(run_id)
        try:
            yield
        finally:
            with self._lock:
                self._inflight.discard(run_id)
                with self._db() as db:
                    row=db.execute('SELECT status,uncertain FROM runs WHERE id=?',(run_id,)).fetchone()
                    if row and row['status'] in TERMINAL and not row['uncertain'] and run_id not in self._workers:
                        db.execute('DELETE FROM bindings WHERE run_id=?',(run_id,))

    def _route(self, run_id, progress, reason):
        with self._db() as db:
            db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'",(run_id,))
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(run_id,_json(progress)))
            db.execute('UPDATE runs SET stage=? WHERE id=?',(progress['next_stage'],run_id))
            self._event(run_id,'run.revision', {**progress,'reason':reason},db)

    def _claim_binding(self, binding, run_id, db):
        if not binding:
            return
        identity = hashlib.sha256(_json(binding_identity(binding)).encode()).hexdigest()
        prior = db.execute('SELECT run_id FROM bindings WHERE identity=?',(identity,)).fetchone()
        if prior and prior['run_id'] != run_id:
            owner = db.execute('SELECT status,uncertain FROM runs WHERE id=?',(prior['run_id'],)).fetchone()
            if owner and (owner['status'] in ('queued','running') or owner['uncertain'] or prior['run_id'] in self._workers or prior['run_id'] in self._inflight):
                raise ValueError('Device binding is already owned by another run')
        db.execute('INSERT OR REPLACE INTO bindings VALUES(?,?)',(identity,run_id))

    @staticmethod
    def _verify_inputs(inputs):
        for path, expected in inputs.items():
            if digest(path) != expected:
                raise ValueError('Stale handoff: an assigned input was altered: ' + path)

    def _prepare(self, spec, stage, run_dir, workspace, accepted):
        case = spec.get('case')
        if case:
            from .benchmark import prepare_stage
            case_id = case['id'] if isinstance(case, dict) else case
            options = spec.get('case_options', {})
            if isinstance(case, dict):
                options = {**case.get('options', {}), **options}
            options = {**options,'revision':spec.get('_revision',0),'feedback':spec.get('_feedback'),
                       'operator_observations':spec.get('_operator_observations',[]),'configured_effects':spec['effects']}
            return prepare_stage(case_id, stage, run_dir, workspace, accepted, options)
        if stage in ('ground','maintain'):
            return {'blocked':stage + ' requires an independent observation/evaluator adapter; none is configured for this device'}
        if stage == 'interpret':
            from .toolkit import call_tool
            acquisition = next((h for h in reversed(accepted) if h['stage']=='acquire'),None)
            images = [Path(a['path']) for a in acquisition['artifacts'] if Path(a['path']).suffix.lower() in ('.bin','.elf')] if acquisition else []
            if len(images) != 1 or spec.get('front_end','interpret-interface') != 'interpret-interface':
                return {'blocked':'Generic blind interpretation currently requires one acquired binary and front_end=interpret-interface'}
            tool_run = run_dir / 'preparation' / workspace.name
            prepared = call_tool('interpret_run',{'run_dir':str(tool_run),'front_end':'interpret-interface',
                'inputs':{'image.bin':str(images[0])},'mode':'prepare','workspace_root':tempfile.mkdtemp(prefix='gd-candidate-')})
            if not prepared.get('ok'):
                return {'blocked':'Could not prepare a sealed interpretation workspace: '+prepared.get('reason',prepared.get('error','unknown preparation error'))}
            return {'work_dir':prepared['workspace'],'prompt':prepared['prompt_for_agent'],'report_required':False,
                'inputs':[str(Path(prepared['workspace'])/name) for name in prepared['sealed_inputs']], 'allowed_tools':[],
                'collection':{'run_dir':str(tool_run),'expected_seal_sha256':prepared['seal_sha256'],'model_dir':prepared['model_dir']}}
        # Reuse receives only the emitted package; other stages get the immediately
        # preceding accepted artifacts. Neither receives the prior conversation.
        previous = next((h for h in reversed(accepted) if h['stage'] == 'emit'), None) if stage == 'reuse' else (accepted[-1] if accepted else None)
        source = [a['path'] for a in previous['artifacts']] if previous else list(spec.get('inputs', {}).values())
        inputs = []
        for index, raw in enumerate(source):
            path = Path(raw).expanduser().resolve()
            digest(path)
            target = workspace / 'inputs' / (str(index) + '-' + path.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(path, target) if path.is_dir() else shutil.copy2(path, target)
            inputs.append(str(target))
        return {'objective': spec['goal'], 'inputs': inputs, 'allowed_tools': STAGE_TOOLS[stage],
                'context': {'stage': stage, 'acceptance': 'Report evidence for this stage; missing evidence blocks acceptance.'}}

    def _check(self, spec, stage, run_dir, workspace, report, accepted, assignment):
        if spec.get('case'):
            from .benchmark import check_stage
            case = spec['case']
            options = spec.get('case_options', {})
            if isinstance(case, dict):
                options = {**case.get('options', {}), **options}
            options = {**options,'revision':spec.get('_revision',0),'feedback':spec.get('_feedback'),
                       'operator_observations':spec.get('_operator_observations',[]),'configured_effects':spec['effects']}
            return check_stage(case['id'] if isinstance(case, dict) else case, stage, run_dir, workspace, report, accepted, options)
        if stage == 'interpret':
            from .toolkit import call_tool
            prepared = assignment.get('collection')
            if not prepared:
                return {'ok':False,'reason':'No pinned interpretation preparation record'}
            result = call_tool('interpret_collect',{'run_dir':prepared['run_dir'],
                'expected_seal_sha256':prepared['expected_seal_sha256'],'seal_kind':'instruction'})
            model = Path(prepared['model_dir'])
            validation = call_tool('model_validate',{'run_dir':str(workspace/'checks'),'model_dir':str(model)}) if result.get('ok') else {}
            good = bool(result.get('ok') and validation.get('ok'))
            return {'ok':good,'reason':None if good else 'Sealed model collection or executable validation failed',
                'artifacts':[str(model)] if good else [],'checks':[{'name':'sealed_model_collection','passed':bool(result.get('ok'))},
                {'name':'executable_model_contract','passed':bool(validation.get('ok'))}],
                'validation_scope':'Sealed-input integrity and executable model contract; meaning requires probe and independent ground evidence'}
        if not report or not report.get('checks') or not all(c.get('status') == 'pass' and c.get('evidence') for c in report['checks']):
            return {'ok': False, 'reason': 'Stage requires passing evidence-bearing checks'}
        if not report.get('artifacts'):
            return {'ok': False, 'reason': 'Stage requires produced artifacts'}
        paths = []
        for artifact in report['artifacts']:
            path = Path(artifact['path'])
            if not path.is_absolute():
                path = workspace / path
            if not path.resolve().is_relative_to(workspace):
                return {'ok':False,'reason':'Reported artifact is outside the assigned workspace'}
            if digest(path) != artifact.get('sha256'):
                return {'ok':False, 'reason':'Worker artifact hash disagrees with observed bytes'}
            artifact['path'] = str(path.resolve())
            paths.append(path.resolve())
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='tool.finished'", (run_dir.name,)).fetchall()
        observed = [json.loads(row['payload']) for row in rows]
        observed = [event for event in observed if event.get('assignment_id') == assignment['id'] and event.get('ok')]
        checks, selected = [], []
        if stage == 'acquire':
            calls = [e for e in observed if e['name'].startswith('acquire_') and e.get('result',{}).get('available')]
            for call in calls:
                files = call.get('artifacts',{})
                for raw, expected in files.items():
                    path = Path(raw).resolve()
                    if not path.is_relative_to(workspace) or digest(path) != expected:
                        return {'ok':False,'reason':'Observed acquisition artifact changed after the tool returned'}
                    selected.append(str(path))
            if not selected:
                return {'ok':False,'reason':'No observed acquisition with verifiable output artifacts'}
            checks.append({'name':'observed_acquisition_hashes','passed':True,'evidence':selected})
        elif stage == 'probe':
            calls = [e for e in observed if e['name']=='probe_run' and e.get('result',{}).get('ok')]
            for call in calls:
                probe = call['result'].get('probe')
                if probe and Path(probe).is_file():
                    if digest(probe) != call.get('artifacts',{}).get(str(Path(probe).resolve())):
                        return {'ok':False,'reason':'Observed probe artifact changed after the tool returned'}
                    json.loads(Path(probe).read_text())
                    selected.append(probe)
            if not selected:
                return {'ok':False,'reason':'No observed successful probe through the owned connection'}
            checks.append({'name':'observed_probe_execution','passed':True,'evidence':selected})
        elif stage in ('ground','maintain'):
            return {'ok':False,'reason':stage + ' requires an independent observation/evaluator adapter; none is configured for this device'}
        elif stage == 'emit':
            from .toolkit import call_tool
            packages = [p for p in paths if p.is_dir() and (p/'manifest.json').is_file()]
            for package in packages:
                checked = call_tool('emit_check',{'package_dir':str(package),'run_dir':str(workspace/'checks')})
                replayed=checked.get('replay_status')=='passed' or checked.get('seeds_run',0)>0
                if checked.get('ok') and checked.get('_exit',0)==0 and checked.get('runtime_current') and replayed:
                    selected.append(str(package))
            if not selected:
                return {'ok':False,'reason':'No independently checked emitted package'}
            checks.append({'name':'package_integrity_and_replay','passed':True,'evidence':selected})
        elif stage == 'reuse':
            calls = [e for e in observed if e['name']=='emit_test_live' and e.get('result',{}).get('ok')]
            if not calls:
                return {'ok':False,'reason':'No observed successful execution of the emitted package'}
            selected = [str(p) for p in paths]
            checks.append({'name':'fresh_package_execution','passed':True,'evidence':selected})
        return {'ok':True,'artifacts':list(dict.fromkeys(selected)), 'checks':checks,
                'validation_scope':'Observed tool outputs and executable artifact contracts; physical meaning requires independent ground evidence'}

    def _accept(self, run_id, assignment, checked, run_dir, workspace, progress):
        artifacts = []
        for index, raw in enumerate(checked.get('artifacts', [])):
            source = Path(raw).resolve()
            if not source.is_relative_to(workspace) and not source.is_relative_to(run_dir):
                raise ValueError('Artifact is outside the assigned workspace and run directory')
            expected = digest(source)
            target = run_dir / 'accepted' / str(assignment.get('revision',0)) / assignment['stage'] / assignment['id'] / (str(index) + '-' + source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
            if digest(target) != expected:
                raise ValueError('Artifact changed while copying accepted evidence')
            artifacts.append({'path': str(target), 'sha256': expected, 'kind': 'directory' if target.is_dir() else 'file'})
        handoff = {'stage': assignment['stage'], 'assignment_id': assignment['id'], 'revision':assignment.get('revision',0),
                   'route':checked.get('route'), 'artifacts': artifacts,
                   'checks': checked.get('checks', []), 'validation_scope': checked.get('validation_scope', 'Case evaluator'),
                   'accepted_at': time.time()}
        with self._db() as db:
            active = db.execute('SELECT state FROM assignments WHERE id=? AND run_id=?', (assignment['id'],run_id)).fetchone()
            prior = db.execute('SELECT payload FROM handoffs WHERE run_id=? AND stage=?', (run_id,assignment['stage'])).fetchall()
            duplicate = any(json.loads(p['payload']).get('revision',0)==assignment.get('revision',0) for p in prior)
            if not active or active['state'] != 'active' or duplicate:
                raise ValueError('Stale or duplicate handoff rejected')
            if db.execute('SELECT cancelled FROM runs WHERE id=?',(run_id,)).fetchone()['cancelled']:
                raise ValueError('Cancelled assignment cannot produce an accepted handoff')
            db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)', (assignment['id'],run_id,assignment['stage'],_json(handoff),time.time()))
            db.execute('UPDATE assignments SET state=? WHERE id=?', ('accepted', assignment['id']))
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(run_id,_json(progress)))
            self._event(run_id, 'stage.accepted', handoff, db)
        return handoff

    def _tool(self, method, params, run):
        from .toolkit import list_tools, call_tool
        with self._db() as db:
            assigned = db.execute('SELECT * FROM assignments WHERE id=? AND run_id=?',
                                  (params.get('assignment_id'), run['id'])).fetchone()
        if not assigned or assigned['state'] != 'active' or run['status'] != 'running' or run['cancelled']:
            raise ValueError('Worker assignment is inactive or unknown')
        assignment = json.loads(assigned['payload'])
        with self._db() as db:
            saved=db.execute('SELECT payload FROM progress WHERE run_id=?',(run['id'],)).fetchone()
        progress=json.loads(saved['payload']) if saved else {}
        if assignment['stage'] != run['stage'] or assignment.get('revision',0) != progress.get('revision',0):
            raise ValueError('Worker assignment is inactive after stage or revision change')
        contracts = [copy.deepcopy(t) for t in list_tools() if t['name'] in assignment['allowed_tools']]
        if 'benchmark_package_execute' in assignment['allowed_tools']:
            contracts.append({'name':'benchmark_package_execute','description':'Execute a packaged driver operation with configurator-owned binding and immediately capture the evaluator observation.',
                'inputSchema':{'type':'object','required':['package_dir','operation'],'additionalProperties':False,
                    'properties':{'package_dir':{'type':'string'},'operation':{'type':'string'},'parameters':{'type':'object'}}}})
        for contract in contracts:
            schema = contract['inputSchema']
            for bound in ('run_dir','binding','allow_effects','bus_url'):
                schema.get('properties', {}).pop(bound, None)
                if bound in schema.get('required', []):
                    schema['required'].remove(bound)
        if method == 'tools':
            return {'ok': True, 'tools': contracts}
        name = params.get('name')
        if name not in [c['name'] for c in contracts]:
            raise ValueError('Tool is not assigned to this worker')
        arguments = dict(params.get('arguments', {}))
        if {'run_dir','binding','allow_effects','bus_url'} & arguments.keys():
            raise ValueError('Connection, output directory and effect grants are configurator-owned')
        workspace = Path(assignment['workspace'])
        from .toolkit import _PATH_ARGUMENTS
        for key in _PATH_ARGUMENTS & arguments.keys():
            value = arguments[key]
            if value is None:
                continue
            path = Path(value).resolve()
            if not path.is_relative_to(workspace):
                raise ValueError('Tool path is outside this stage workspace: ' + key)
        if 'inputs' in arguments:
            if not isinstance(arguments['inputs'],dict) or any(not isinstance(p,str) or not Path(p).is_absolute() or not Path(p).resolve().is_relative_to(workspace) for p in arguments['inputs'].values()):
                raise ValueError('Every tool input must be inside this stage workspace')
        arguments['run_dir'] = str(workspace / 'tools')
        connected = name in ('probe_run','interface_execute','emit_test_live','benchmark_package_execute') and not arguments.get('replay')
        if connected:
            if not assignment.get('binding'):
                raise ValueError('No operator-owned device binding for this stage')
            arguments['binding'] = assignment['binding']
            arguments['allow_effects'] = assignment['effects']
        effects=operation_effects(name,arguments) if connected else set()
        if effects-{'read'}-set(assignment['effects']):
            raise ValueError('Operation requires an effect grant the operator has not supplied')
        effectful=bool(effects & {'write','actuate'})
        # The uncertain flag is persisted before dispatch. A crash or cancellation
        # cannot turn an outstanding physical effect into an automatic retry.
        with self._lock:
            with self._db() as db:
                active = db.execute('SELECT status,cancelled,uncertain FROM runs WHERE id=?',(run['id'],)).fetchone()
                if active['cancelled'] or active['status'] != 'running':
                    raise ValueError('Worker assignment became inactive before dispatch')
                if effectful and active['uncertain']:
                    raise ValueError('Outstanding effect is uncertain; another effectful operation is refused until operator reconciliation')
                if effectful:
                    db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run['id'],))
                self._event(run['id'], 'tool.started', {'stage':assignment['stage'],'assignment_id':assignment['id'],'name':name,'effectful':effectful}, db)
        if name == 'benchmark_package_execute':
            from .benchmark import package_execute
            spec = json.loads(run['spec'])
            case = spec.get('case')
            if not case:
                raise ValueError('Benchmark package execution requires an assigned case')
            options = spec.get('case_options', {})
            if isinstance(case, dict):
                options = {**case.get('options', {}), **options}
            arguments['run_dir'] = str(self.home / 'runs' / run['id'])
            result = package_execute(case['id'] if isinstance(case,dict) else case,
                                     stage=assignment['stage'],options=options,**arguments)
        else:
            result = call_tool(name, arguments)
        observed_artifacts = {}
        for key in ('artifact','provenance','pages','pages_dir','manifest','package_dir','probe','model_dir','transcript'):
            raw = result.get(key)
            if isinstance(raw,str) and Path(raw).is_absolute():
                path = Path(raw).resolve()
                if path.is_relative_to(workspace) and path.exists():
                    observed_artifacts[str(path)] = digest(path)
        with self._db() as db:
            cancelled = db.execute('SELECT cancelled FROM runs WHERE id=?',(run['id'],)).fetchone()['cancelled']
            success = result.get('_exit',0)==0 and bool(result.get('ok',result.get('available',True)))
            if effectful and not cancelled and success:
                db.execute('UPDATE runs SET uncertain=0 WHERE id=?',(run['id'],))
            self._event(run['id'], 'tool.finished', {'stage':assignment['stage'],'assignment_id':assignment['id'],'name':name,'ok':success,'result':result,'artifacts':observed_artifacts}, db)
        return result

    def close(self):
        with self._lock:
            self._closing = True
        for cancel, thread in list(self._workers.values()):
            cancel.set()
            thread.join(timeout=10)

"""Durable suite slots in the existing controller database; no execution owner."""
import json
import time
import uuid


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _slot(row):
    return {**json.loads(row['trial_json']),
        **{key:row[key] for key in ('child_request_id','run_id','status','outcome_category')},
        'result':json.loads(row['result_json']) if row['result_json'] else None}


class SuiteStore:
    def __init__(self,open_db):
        self.open_db=open_db
        with self.open_db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS suites (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE, request_json TEXT NOT NULL,
                    frozen_json TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    budget_seconds REAL NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS suite_trials (
                    suite_id TEXT NOT NULL, ordinal INTEGER NOT NULL, trial_json TEXT NOT NULL,
                    child_request_id TEXT NOT NULL UNIQUE, run_id TEXT UNIQUE,
                    status TEXT NOT NULL, outcome_category TEXT NOT NULL DEFAULT 'unknown',
                    result_json TEXT, PRIMARY KEY(suite_id,ordinal));
                CREATE TABLE IF NOT EXISTS suite_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, suite_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            ''')

    def _event(self,db,suite_id,kind,payload):
        db.execute('INSERT INTO suite_events(suite_id,kind,payload,created) VALUES(?,?,?,?)',
                   (suite_id,kind,_json(payload),time.time()))

    def _get(self,db,suite_id):
        row=db.execute('SELECT * FROM suites WHERE id=?',(suite_id,)).fetchone()
        if row is None:raise ValueError('Unknown suite_id')
        return {'suite_id':row['id'],**{key:row[key] for key in ('status','reason','created','updated','budget_seconds')},
            'cancelled':bool(row['cancelled']),'frozen':json.loads(row['frozen_json']),
            'trials':[_slot(trial) for trial in db.execute('SELECT * FROM suite_trials WHERE suite_id=? ORDER BY ordinal',(suite_id,))]}

    def get(self,suite_id):
        with self.open_db() as db:return self._get(db,suite_id)

    def _find_request(self,db,request_id,canonical_request):
        if request_id is None:return None
        row=db.execute('SELECT id,request_json FROM suites WHERE request_id=?',(request_id,)).fetchone()
        if row is None:return None
        if row['request_json']!=_json(canonical_request):
            raise ValueError('request_id was already used with different suite inputs')
        return self._get(db,row['id'])

    def find_request(self,request_id,canonical_request):
        with self.open_db() as db:return self._find_request(db,request_id,canonical_request)

    def create(self,frozen,request_id=None):
        suite_id='suite-'+uuid.uuid4().hex[:16]
        with self.open_db() as db:
            db.execute('BEGIN IMMEDIATE')
            saved=self._find_request(db,request_id,frozen['request'])
            if saved is not None:return saved
            now=time.time()
            db.execute('INSERT INTO suites(id,request_id,request_json,frozen_json,status,created,updated,budget_seconds) VALUES(?,?,?,?,?,?,?,?)',
                (suite_id,request_id,_json(frozen['request']),_json(frozen),'queued',now,now,frozen['manifest']['suite_budget_seconds']))
            for trial in frozen['trials']:
                db.execute('INSERT INTO suite_trials(suite_id,ordinal,trial_json,child_request_id,status) VALUES(?,?,?,?,?)',
                    (suite_id,trial['ordinal'],_json(trial),f"suite:{suite_id}:trial:{trial['ordinal']}",'pending'))
            self._event(db,suite_id,'suite.started',{'planned_trials':len(frozen['trials'])})
            return self._get(db,suite_id)

    def set_state(self,suite_id,status,reason=None):
        with self.open_db() as db:
            self._get(db,suite_id)
            db.execute('UPDATE suites SET status=?,reason=?,updated=?,cancelled=? WHERE id=?',
                (status,reason,time.time(),int(status=='cancelled'),suite_id))
            self._event(db,suite_id,'suite.'+status,{})

    def reserve(self,suite_id):
        # Keep an interrupted dispatch visible even while its parent is blocked.
        with self.open_db() as db:
            db.execute('BEGIN IMMEDIATE')
            suite=self._get(db,suite_id)
            row=db.execute("SELECT * FROM suite_trials WHERE suite_id=? AND status IN ('launching','running') ORDER BY ordinal LIMIT 1",(suite_id,)).fetchone()
            if row is not None:return _slot(row)
            if suite['cancelled'] or suite['status']!='running':return None
            row=db.execute("SELECT * FROM suite_trials WHERE suite_id=? AND status='pending' ORDER BY ordinal LIMIT 1",(suite_id,)).fetchone()
            if row is None:return None
            db.execute("UPDATE suite_trials SET status='launching' WHERE suite_id=? AND ordinal=?",(suite_id,row['ordinal']))
            self._event(db,suite_id,'trial.launching',{'ordinal':row['ordinal']})
            return {**_slot(row),'status':'launching'}

    def attach(self,suite_id,ordinal,child_request_id,run_id):
        with self.open_db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM suite_trials WHERE suite_id=? AND ordinal=?',(suite_id,ordinal)).fetchone()
            if row is None or row['child_request_id']!=child_request_id:raise ValueError('Unknown or stale suite slot')
            if row['run_id'] not in (None,run_id):raise ValueError('Suite slot already owns a different child')
            if row['run_id']==run_id:return
            db.execute("UPDATE suite_trials SET run_id=?,status='running' WHERE suite_id=? AND ordinal=?",(run_id,suite_id,ordinal))
            self._event(db,suite_id,'trial.started',{'ordinal':ordinal,'run_id':run_id})

    def trials(self,suite_id,offset=0,limit=50):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Invalid suite trial pagination")
        with self.open_db() as db:
            self._get(db,suite_id)
            rows=db.execute('SELECT * FROM suite_trials WHERE suite_id=? ORDER BY ordinal LIMIT ? OFFSET ?',(suite_id,limit+1,offset)).fetchall()
        fields=('ordinal','trial_key','case','scenario','case_seed','repeat_index','run_id','status','outcome_category')
        return {'suite_id':suite_id,'trials':[{key:_slot(row)[key] for key in fields} for row in rows[:limit]],
            'next_offset':offset+limit if len(rows)>limit else None}

    def events(self,suite_id,after=0):
        if type(after) is not int or after<0:raise ValueError('Invalid suite event cursor')
        with self.open_db() as db:
            self._get(db,suite_id)
            rows=db.execute('SELECT * FROM suite_events WHERE suite_id=? AND seq>? ORDER BY seq LIMIT 500',(suite_id,after)).fetchall()
        return {'suite_id':suite_id,'events':[{'id':row['seq'],'kind':row['kind'],
            'data':json.loads(row['payload']),'time':row['created']} for row in rows],
            'cursor':rows[-1]['seq'] if rows else after}

    def settle(self,suite_id,ordinal,result):
        if (result.get('stopping') is not False or result.get('uncertain_effect') is not False
                or result.get('status') not in ('completed','failed','cancelled','blocked')):
            raise ValueError('Child must be settled before its slot')
        category=result.get('outcome_category','unknown')
        if category not in ('completed','model','host','operator','cancelled','unknown'):
            raise ValueError('Invalid child outcome category')
        public={'run_id':result.get('run_id'),'status':result['status'],'outcome_category':category}
        with self.open_db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM suite_trials WHERE suite_id=? AND ordinal=?',(suite_id,ordinal)).fetchone()
            if row is None or not row['run_id'] or row['run_id']!=public['run_id']:
                raise ValueError('Settlement does not match attached child')
            if row['result_json'] is not None:
                if row['result_json']!=_json(public):raise ValueError('Child slot already has a different settlement')
                return
            db.execute("UPDATE suite_trials SET status='finished',outcome_category=?,result_json=? WHERE suite_id=? AND ordinal=?",
                (category,_json(public),suite_id,ordinal))
            self._event(db,suite_id,'trial.finished',{'ordinal':ordinal,**public})

    def recover(self):
        with self.open_db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT id FROM suites WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                db.execute("UPDATE suites SET status='blocked',reason=?,updated=? WHERE id=?",
                    ('Configurator restarted; explicit resume required',time.time(),row['id']))
                self._event(db,row['id'],'suite.recovered',{})

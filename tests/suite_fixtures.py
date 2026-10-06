"""Public suite data and scripted contract helpers; never native tools or inference."""
import json


def pilot_manifest():
    return {'schema':'benchmark-suite/1','id':'development-pilot','version':'2',
        'entries':[{'case':case,'scenario':'original','case_seed':0}
                   for case in ('tq9-v2','sampled-sensor-v1','parameter-store-v1')],
        'repetitions':3,'child_budget_seconds':10800,'suite_budget_seconds':108000,'max_active_children':1}


def legacy_manifest(repetitions=1):
    return {'schema':'benchmark-suite/1','id':'scripted-contract','version':'1',
        'entries':[{'case':'tq9','scenario':'original','case_seed':0}],
        'repetitions':repetitions,'child_budget_seconds':120,
        'suite_budget_seconds':120,'max_active_children':1}


def sqlite_context(path):
    from contextlib import contextmanager
    import sqlite3
    @contextmanager
    def open_db():
        db=sqlite3.connect(path,timeout=20)
        try:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=20000')
            with db:yield db
        finally:db.close()
    return open_db


def public_case_fixture(root, case_id='tq9-v2', *, calibration=None, **changes):
    """Local registry inputs for pure freeze tests; never native qualification."""
    import hashlib
    case = root / 'cases' / case_id
    case.mkdir(parents=True)
    image = case / 'firmware.bin'; image.write_bytes(b'public suite contract image')
    truth = root / 'groundtruth' / (case_id + '.enc')
    truth.parent.mkdir(exist_ok=True); truth.write_bytes(b'public suite contract opaque fixture')
    manifest = {
        'schema': 'benchmark-case/2', 'id': case_id, 'family': 'toy-family',
        'version': '5', 'evaluator_version': '5', 'execution': 'actual-agent-emulation',
        'evidence_track': 'firmware', 'scope': 'full-workflow', 'adapter_key': 'emulator-v2',
        'approval_scope': 'emulator', 'default_effects': ['write'], 'scenarios': ['original'],
        'required_stages': ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'],
        'images': {'firmware.bin': hashlib.sha256(image.read_bytes()).hexdigest()},
        'truth': {'path': 'groundtruth/' + case_id + '.enc', 'sha256': hashlib.sha256(truth.read_bytes()).hexdigest()},
        'calibration': {'status': 'passed'} if calibration is None else calibration,
        'time_policy': {'probe': 'toy virtual time'}, 'provenance': {'execution': 'scripted-contract-fixture'},
        'limitations': ['Pure suite freeze fixture; not native calibration'], **changes}
    (case / 'case.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


def write_scripted_case_worker(directory, *, hold_on_repair=False):
    """Write a subprocess contract fixture rejected by the real legacy model gate."""
    import sys
    from pathlib import Path
    import generative_driver
    path = Path(directory)/'scripted_suite_worker.py'
    source = '''import sys,json,pathlib,hashlib,shutil,time
sys.path.insert(0,PACKAGE_PARENT)
from generative_driver.client import call
from generative_driver.toolkit import resources_root
prompt=sys.stdin.read()
if prompt.startswith('Execute exactly'):
    task=json.loads(prompt.split('\\n',1)[1])
    args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
    identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
    image=pathlib.Path(task['inputs'][0])
    out=call('tool',{**identity,'name':'acquire_firmware_artifact','arguments':{'source_path':str(image),'expected_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'origin':'provided_binary'}},args[args.index('--home')+1])
    assert out.get('available'),out
    report={'status':'completed','summary':'scripted import','artifacts':[{'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'} for k in ('artifact','provenance')],'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
else:
    # An intentionally unrelated, unqualified model. The real case gate rejects it.
    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')
'''
    if hold_on_repair:
        source = source.replace('else:\n', "else:\n    if pathlib.Path('DEFECTS.json').exists(): time.sleep(20)\n", 1)
    source = source.replace('PACKAGE_PARENT', repr(str(Path(generative_driver.__file__).resolve().parents[1])))
    path.write_text(source, encoding='utf-8')
    return [sys.executable, str(path)]


def wait_suite(call, suite_id, home, predicate):
    import time
    hard_stop, idle_stop, cursor = time.monotonic()+60, time.monotonic()+10, 0
    last_event, child_cursors = None, {}
    while time.monotonic() < min(hard_stop, idle_stop):
        state = call('suite_status', {'suite_id':suite_id}, home)
        page = call('suite_events', {'suite_id':suite_id,'after':cursor}, home)
        if page['cursor'] > cursor:
            cursor, idle_stop = page['cursor'], time.monotonic()+10
            last_event = page['events'][-1]
        child = state.get('active_child_id')
        if child:
            child_page = call('events', {'run_id':child,'after':child_cursors.get(child,0)}, home)
            if child_page['cursor'] > child_cursors.get(child,0):
                child_cursors[child] = child_page['cursor']
                idle_stop, last_event = time.monotonic()+10, child_page['events'][-1]
        if predicate(state):
            return state
        if state['status'] in ('failed','blocked','cancelled','completed'):
            raise AssertionError({'unexpected_terminal':state,'last_event':last_event})
        time.sleep(.02)
    raise AssertionError({'wait_expired':state,'last_event':last_event})


def wait_owner_suite(owner, suite_id, predicate):
    return wait_suite(lambda method,params,home:owner.call(method,params),suite_id,None,predicate)

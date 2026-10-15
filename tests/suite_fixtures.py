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
    """Write a subprocess contract fixture for legacy tq9 stages: it imports the supplied firmware at acquire, writes an
    unrelated setup-smoke model at interpret (the real stage observation notes it unqualified) and reports every later
    stage blocked, with nothing to hand on. `hold_on_repair` makes the first interpretation one the emulator binding
    cannot carry, so probe skips and the configurator's model diff starts a repair, which it then holds."""
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
    if task['stage']=='acquire':
        args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
        identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
        image=pathlib.Path(task['inputs'][0])
        out=call('tool',{**identity,'name':'acquire_firmware_artifact','arguments':{'source_path':str(image),'expected_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'origin':'provided_binary'}},args[args.index('--home')+1])
        assert out.get('available'),out
        report={'status':'completed','summary':'scripted import','artifacts':[{'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'} for k in ('artifact','provenance')],'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
    else:
        report={'status':'blocked','summary':'scripted contract: nothing to do in '+task['stage'],'artifacts':[],'checks':[],'unresolved':[]}
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
else:
    # An intentionally unrelated, unqualified model; the real stage observation notes it.
    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')
'''
    if hold_on_repair:
        source = source.replace("    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')\n",
            "    if pathlib.Path('DEFECTS.json').exists(): time.sleep(20)\n"
            "    model=json.loads((resources_root()/'bench/cases/setup-smoke/model.json').read_text())\n"
            "    model['channel']={'type':'usb'}\n"
            "    pathlib.Path('model.json').write_text(json.dumps(model))\n", 1)
    source = source.replace('PACKAGE_PARENT', repr(str(Path(generative_driver.__file__).resolve().parents[1])))
    path.write_text(source, encoding='utf-8')
    return [sys.executable, str(path)]


LEGACY_DEVICE_FIXTURE = '''
# Tests only: the bench harness's legacy tq9 device, a synthetic TCP peer per emulator session, and a toy legacy truth
# for the device recipe and the grade. A spawned harness loads this through GD_TEST_HARNESS_FIXTURE.
import sys
sys.path.insert(0, TESTS)
from suite_fixtures import legacy_device_patches
for patcher in legacy_device_patches(PRIVATE)[0]:
    patcher.start()
'''


def legacy_device_patches(private):
    """([patchers], peers): a bench harness's legacy tq9 emulator sessions become synthetic TCP peers and its tq9 truth a
    toy one; other cases' truth is unlocked as before."""
    from pathlib import Path
    from unittest.mock import patch
    from tq9_workflow_fixture import Silicon, legacy_truth
    from generative_driver.benchmark_support import evaluator
    real, peers = evaluator.case_truth, []
    def start(**kwargs):
        peer = Silicon(kwargs['image']); peers.append(peer)
        session = peer.session(kwargs['recipe'], Path(kwargs.get('private_dir') or Path(private) / ('native-' + str(len(peers)))))
        class Process:
            def poll(self):
                return 0 if getattr(peer, 'closed', False) else None
        session.process = Process()
        return session
    def case_truth(case_id, run_dir, options):
        return legacy_truth() if case_id == 'tq9' else real(case_id, run_dir, options)
    return ([patch('generative_driver.benchmark_support.evaluator.case_truth', side_effect=case_truth),
             patch('generative_driver.benchmark_support.native.NativeSession.start', side_effect=start)], peers)


def legacy_devices(test, root):
    """Have every bench harness of a test provide legacy tq9 devices of synthetic peers: patches in this process, for an
    in-process harness, and a fixture file that a spawned harness loads. Returns the harness's secrets for the suite:
    {'password_files': {'tq9': a synthetic credential file}, 'renode': an emulator path}, which `options()` turns into
    suite start options."""
    import os
    from pathlib import Path
    from unittest.mock import patch
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    patchers, peers = legacy_device_patches(root / 'native')
    for patcher in patchers:
        patcher.start(); test.addCleanup(patcher.stop)
    test.addCleanup(lambda: [peer.close() for peer in peers])
    test.legacy_peers = peers  # The peers of devices an in-process harness provides.
    fixture = root / 'legacy_harness_fixture.py'
    fixture.write_text(LEGACY_DEVICE_FIXTURE.replace('TESTS', repr(str(Path(__file__).resolve().parent)))
                       .replace('PRIVATE', repr(str(root / 'native'))), encoding='utf-8')
    environment = patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)})
    environment.start(); test.addCleanup(environment.stop)
    password = root / 'legacy-password-handle'; password.write_text('synthetic fixture credential')
    return {'password_files': {'tq9': str(password)}, 'renode': str(root / 'renode')}


def options(secrets, **extra):
    """Suite start or resume options carrying the harness's secrets from `legacy_devices`."""
    return {'evaluator_password_files': dict(secrets['password_files']), 'renode': secrets['renode'], **extra}


def wait_suite(call, suite_id, home, predicate):
    import time
    hard_stop, idle_stop, cursor = time.monotonic()+180, time.monotonic()+30, 0
    last_event, child_cursors = None, {}
    while time.monotonic() < min(hard_stop, idle_stop):
        state = call('suite_status', {'suite_id':suite_id}, home)
        page = call('suite_events', {'suite_id':suite_id,'after':cursor}, home)
        if page['cursor'] > cursor:
            cursor, idle_stop = page['cursor'], time.monotonic()+30
            last_event = page['events'][-1]
        child = state.get('active_child_id')
        if child:
            child_page = call('events', {'run_id':child,'after':child_cursors.get(child,0)}, home)
            if child_page['cursor'] > child_cursors.get(child,0):
                child_cursors[child] = child_page['cursor']
                idle_stop, last_event = time.monotonic()+30, child_page['events'][-1]
        if predicate(state):
            return state
        # A stopped suite whose bench harness has not let go of it yet is still settling.
        if state['status'] in ('failed','blocked','cancelled','completed') and not state['stopping']:
            raise AssertionError({'unexpected_terminal':state,'last_event':last_event})
        time.sleep(.02)
    raise AssertionError({'wait_expired':state,'last_event':last_event})


def wait_owner_suite(owner, suite_id, predicate):
    return wait_suite(lambda method,params,home:owner.call(method,params),suite_id,None,predicate)


def settled_suite(call, suite_id, home=None, seconds=60):
    """The suite's status once nothing of it is stopping: its child has stopped and its bench harness let go."""
    import time
    deadline = time.monotonic() + seconds
    while True:
        state = call('suite_status', {'suite_id': suite_id}, home)
        if not state['stopping']:
            return state
        if time.monotonic() > deadline:
            raise AssertionError({'suite_did_not_settle':state})
        time.sleep(.02)


def drive_suite(owner, bench, request, *, wait=True, calls=None, stop=None, intercept=None):
    """Play the bench harness for an in-process owner: the harness's suite loop runs in a thread and makes its
    configurator requests to `owner`. With `wait`, returns the harness's record once it has finished; otherwise the
    record once the configurator took the suite on or refused it. A refusal raises ValueError with its reason.
    `intercept(method, params, forward)` may stand between the harness and the owner: it returns the reply, and
    `forward(method, params)` makes the request."""
    import threading
    import uuid
    from pathlib import Path
    from generative_driver.benchmark_support import harness
    def forward(method, params=None):
        try:
            return owner.call(method, params or {})
        except Exception as error:
            return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
    def configurator(method, params=None):
        if calls is not None:
            calls.append((method, params))
        if method == 'ping':
            return {'ok': True}
        return intercept(method, params, forward) if intercept else forward(method, params)
    directory = Path(bench) / ('suite-' + uuid.uuid4().hex[:8])
    done = {}
    thread = threading.Thread(target=lambda: done.update(record=harness.run_suite(
        directory, {'home': str(owner.home), **request}, call=configurator, stop=stop)), daemon=True)
    thread.start()
    owner._test_harness_threads = getattr(owner, '_test_harness_threads', []) + [thread]
    if wait:
        thread.join(60)
        if thread.is_alive():
            raise AssertionError('The suite harness did not finish: ' + str(harness.read_suite(directory)))
        record = done['record']
    else:
        import time
        deadline = time.monotonic() + 30
        while True:
            record = harness.read_suite(directory) or {}
            if record.get('accepted') or record.get('status') in ('failed', 'finished') or not thread.is_alive():
                break
            if time.monotonic() > deadline:
                raise AssertionError('The suite harness never took the suite on: ' + str(record))
            time.sleep(.02)
    if not record.get('accepted'):
        raise ValueError(record.get('reason') or 'The suite harness stopped before the configurator took the suite on')
    return record



def close_owner(owner, timeout=30):
    """Close an in-process owner, then wait for the bench harness threads `drive_suite` started for it. A harness that
    has let go of its suite still writes its record and closes its lock file, and Windows will not remove a test's
    temporary directory while that file is open. Waiting never raises, so a test's own failure is the one reported."""
    try:
        owner.close()
    finally:
        for thread in getattr(owner, '_test_harness_threads', []):
            thread.join(timeout)


def shutdown_owner(home):
    """Cancel test suites, await their detached harness processes, then stop the owner.

    Suite detach records scheduling completion before the interpreter has exited;
    only process exit releases harness.log and prevents a late post restarting the owner.
    """
    from generative_driver.client import call
    from generative_driver.benchmark_support.harness import bench_home, read_suite
    bench = bench_home(home)
    for path in (bench / 'suites').glob('*/suite.json'):
        record = read_suite(path.parent) or {}
        if record.get('suite_id'):
            reply = call('suite_cancel', {'suite_id':record['suite_id']}, home, autostart=False)
            if not reply.get('ok'):
                raise AssertionError(reply)
    wait_harnesses(home)
    reply = call('shutdown', {}, home)
    if not reply.get('stopped'):
        raise AssertionError(reply)


def wait_harnesses(home):
    """Await process exit, including the final log handles after scheduling detaches."""
    from generative_driver.client import _wait_for_exit
    from generative_driver.benchmark_support.harness import bench_home, read_suite, read_trial
    bench = bench_home(home)
    for pattern, read in (('suites/*/suite.json',read_suite), ('trials/*/trial.json',read_trial)):
        for path in bench.glob(pattern):
            record = read(path.parent) or {}
            pid = record.get('pid')
            if pid and not _wait_for_exit(pid, 60):
                raise AssertionError({'harness_did_not_exit':str(path), 'record':record})

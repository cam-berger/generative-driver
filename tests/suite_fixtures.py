"""Public suite data and scripted contract helpers; never native tools or inference."""
import json


def service(owner):
    """The in-process configurator `owner` as the callable a suite book or a bench harness sends its requests to.
    `ping` answers with a process id of the owner's own, which differs for each `Controller` object
    (`owner_ipc_fixture.service_pid`), so that a second one on the same home is a restart of the service. A refusal
    comes back in the form the service gives it."""
    from owner_ipc_fixture import service_pid
    def send(method, params=None):
        if method == 'ping':
            return {'ok': True, 'pid': service_pid(owner)}
        try:
            return owner.call(method, params or {})
        except Exception as error:
            return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}
    return send


def book(owner):
    """The suite book of the in-process configurator `owner`: the book of its home, whose requests go to `owner`. Its
    `store` is the book's `SuiteStore`, and `store.open_db()` its database (Feature 07 of the untangle Run,
    docs/features/untangle/07-suites.md)."""
    from generative_driver.benchmark_support.suite_book import SuiteBook
    return SuiteBook(owner.home, service(owner))


def suite_call(owner, method, params=None):
    """What the suite book of the in-process configurator `owner` answers the suite call `method`: the one way a test
    sends a suite call, since the configurator serves none. A refusal raises, as it did when the configurator served
    the call: the book's own, and one of the configurator's that ended the call. A reply that never came is returned
    as the client gives it."""
    from generative_driver.integrity import IntegrityError
    reply = book(owner).call(method, params)
    if isinstance(reply, dict) and reply.get('ok') is False and 'error' in reply:
        raise {'IntegrityError': IntegrityError, 'ValueError': ValueError}.get(reply['error'], RuntimeError)(
            reply.get('reason'))
    return reply


def owner_call(owner):
    """`(method, params)` for an in-process configurator `owner`, as a client's one callable: a suite call goes to
    the owner's book (`suite_call`) and every other method to the owner."""
    return lambda method, params=None: (suite_call(owner, method, params) if method.startswith('suite_')
                                        else owner.call(method, params))


def client_call(method, params=None, home=None, **options):
    """`generative_driver.client.call` for a test of a configurator process, with a suite call sent to the suite book
    of the same home in this process, as a command of the benchmark sends it."""
    from generative_driver import client
    from generative_driver.benchmark_support.suite_book import suite_call as asked
    return (asked if method.startswith('suite_') else client.call)(method, params, home, **options)


def standing_in(call=None, **mock):
    """Replace `generative_driver.client.call`, and with it the suite book's `suite_call`, by one stand-in: `call`,
    or a mock made with `mock` (`side_effect=...` or `return_value=...`). For a test that stands in for the
    service's answers, among them the suite answers the service gave before the book kept suites: a reader of the
    benchmark asks the book for those now. Entered, it gives the stand-in."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch
    stand_in = call if call is not None else MagicMock(**mock)
    @contextmanager
    def replaced():
        with patch('generative_driver.client.call', stand_in), \
                patch('generative_driver.benchmark_support.suite_book.suite_call', stand_in):
            yield stand_in
    return replaced()


def pilot_manifest():
    return {'schema':'benchmark-suite/1','id':'development-pilot','version':'2',
        'entries':[{'case':case,'scenario':'original','case_seed':0}
                   for case in ('tq9-v2','sampled-sensor-v1','parameter-store-v1')],
        'repetitions':3,'child_budget_seconds':10800,'suite_budget_seconds':108000,'max_active_children':1}


def scripted_manifest(repetitions=1):
    """A one-entry suite of the current case `tq9-v2` for scripted contract tests; its budgets are short."""
    return {'schema':'benchmark-suite/1','id':'scripted-contract','version':'1',
        'entries':[{'case':'tq9-v2','scenario':'original','case_seed':0}],
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
    import shutil
    from generative_driver.toolkit import resources_root
    from generative_driver.benchmark_support.registrations import registration_for
    registration = registration_for(case_id)
    # Public missions are data resources now, just like images and manifests.
    # Keep fixture asset trees complete without bringing evaluator answers in.
    shutil.copytree(resources_root() / 'bench/missions', root / 'missions', dirs_exist_ok=True)
    case = root / 'cases' / case_id
    case.mkdir(parents=True)
    image = case / 'firmware.bin'; image.write_bytes(b'public suite contract image')
    truth = root / 'groundtruth' / (case_id + '.enc')
    truth.parent.mkdir(exist_ok=True); truth.write_bytes(b'public suite contract opaque fixture')
    manifest = {
        'schema': 'benchmark-case/2', 'id': case_id, 'family': registration.family,
        'version': registration.version, 'evaluator_version': registration.version, 'execution': 'actual-agent-emulation',
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


def file_changes(truth_file, password, outside):
    """The changes a worker could make to a trial's files, as (name, change, undo): both deleted, both replaced, and the
    truth file swapped for a symlink to `outside`, for which a test first asks `symlinks_refused`."""
    from pathlib import Path
    truth_file, password, outside = Path(truth_file), Path(password), Path(outside)
    kept, secret = truth_file.read_bytes(), password.read_bytes()
    outside.write_bytes(b'not the answer key')
    def undo():
        truth_file.unlink(missing_ok=True); truth_file.write_bytes(kept); password.write_bytes(secret)
    def deleted():
        truth_file.unlink(); password.unlink()
    def replaced():
        truth_file.write_bytes(b'a worker changed this'); password.write_text('another credential')
    def symlink():
        truth_file.unlink(); truth_file.symlink_to(outside)
    return (('deleted', deleted, undo), ('replaced', replaced, undo), ('symlink', symlink, undo))


def symlinks_refused(directory):
    """The class name of the OSError this system raises for a symlink in `directory`, or None. A test asks in its own
    thread: a skip raised in a stage's worker would reach the configurator as that stage's failure."""
    from pathlib import Path
    probe = Path(directory) / 'symlink-probe'
    try:
        probe.symlink_to(Path(directory))
    except OSError as error:
        return type(error).__name__
    probe.unlink()
    return None


def write_scripted_case_worker(directory, *, hold_on_repair=False):
    """Write a subprocess contract fixture for a case's stages: it imports the supplied firmware at acquire, writes an
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


DEVICE_FIXTURE = '''
# Tests only: the bench harness's device of the current case tq9-v2, a synthetic TCP peer per emulator session, and a
# toy answer key for its admission and its device recipe. A spawned harness loads this through
# GD_TEST_HARNESS_FIXTURE.
import sys
sys.path.insert(0, TESTS)
from suite_fixtures import device_patches
for patcher in device_patches(PRIVATE)[0]:
    patcher.start()
'''


def device_patches(private):
    """([patchers], peers): a bench harness's emulator sessions become synthetic TCP peers, and its admission of
    `tq9-v2` hands out a toy answer key whose recipe those peers serve, under the calibration evidence the case's public
    pin names, so that nothing decrypts the installed answer file. Other cases are admitted as before."""
    from pathlib import Path
    from unittest.mock import patch
    from test_device_provider import RECIPE
    from tq9_workflow_fixture import Silicon
    from generative_driver.benchmark_support import registry
    from generative_driver.benchmark_support.truth import UnlockedTruth
    real, peers = registry.admitted_truth, []
    def start(**kwargs):
        peer = Silicon(kwargs['image']); peers.append(peer)
        session = peer.session(kwargs['recipe'],
                               Path(kwargs.get('private_dir') or Path(private) / ('native-' + str(len(peers)))))
        class Process:
            def poll(self):
                return 0 if getattr(peer, 'closed', False) else None
        session.process = Process()
        return session
    def admitted_truth(pin, password):
        if pin['case_id'] == 'tq9-v2':
            return (UnlockedTruth({'recipe': RECIPE}, pin['truth_sha256']),
                    {'ok': True, 'evidence_sha256': pin['calibration']['evidence_sha256']})
        return real(pin, password)
    return ([patch('generative_driver.benchmark_support.registry.admitted_truth', side_effect=admitted_truth),
             patch('generative_driver.benchmark_support.native.NativeSession.start', side_effect=start)], peers)


def harness_devices(test, root):
    """Have every bench harness of a test provide `tq9-v2` devices of synthetic peers: patches in this process, for an
    in-process harness, and a fixture file that a spawned harness loads. Returns the harness's secrets for the suite:
    {'password_files': {'tq9-v2': a synthetic credential file}, 'renode': an emulator path}, which `options()` turns
    into suite start options."""
    import os
    from pathlib import Path
    from unittest.mock import patch
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    patchers, peers = device_patches(root / 'native')
    for patcher in patchers:
        patcher.start(); test.addCleanup(patcher.stop)
    test.addCleanup(lambda: [peer.close() for peer in peers])
    test.device_peers = peers  # The peers of devices an in-process harness provides.
    fixture = root / 'harness_fixture.py'
    fixture.write_text(DEVICE_FIXTURE.replace('TESTS', repr(str(Path(__file__).resolve().parent)))
                       .replace('PRIVATE', repr(str(root / 'native'))), encoding='utf-8')
    environment = patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)})
    environment.start(); test.addCleanup(environment.stop)
    password = root / 'password-handle'; password.write_text('synthetic fixture credential')
    return {'password_files': {'tq9-v2': str(password)}, 'renode': str(root / 'renode')}


# The evaluation token of the bench harness that `supplied_start` plays: a run holds only its sha256.
HARNESS_TOKEN = 'scripted harness token'


def device_descriptor(test, root, image=None):
    """The descriptor of another device of synthetic peers under `root`, started from `image`: by default the
    installed `tq9-v2` image. A run that stopped has released its device, so a test attaches another one, with
    `HARNESS_TOKEN`, before it resumes the run."""
    from pathlib import Path
    from test_harness import Device
    from generative_driver.benchmark_support.registry import resolve_case
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    device = Device(test, image or resolve_case('tq9-v2').root / 'firmware.bin',
                    root / ('device-' + str(len(list(root.glob('device-*'))))))
    return device.server.descriptor


class SuppliedStart(dict):
    """The `start` the bench harness sends for a run of a case, for a test that starts the run itself. `trial_dir` is
    the folder the case was frozen in, as a harness's trial folder holds it."""
    trial_dir = None

    def keep(self, home, run):
        """Copy the snapshot into the folder of `run` under the configurator `home`, as the harness does once `start`
        has answered; returns `run`. The grader and the reports look for it there."""
        from pathlib import Path
        from generative_driver.benchmark_support.harness import keep_snapshot
        keep_snapshot(self.trial_dir, Path(home) / 'runs' / (run['run_id'] if isinstance(run, dict) else run))
        return run


def supplied_start(test, root, *, case='tq9-v2', descriptor=None, home=None, case_options=None, pin=None,
                   token=HARNESS_TOKEN, trial_dir=None, **request):
    """What the bench harness sends to start a run of `case`, for a test that starts the run itself: a
    `SuppliedStart`. As the harness does, it pins the case (unless `pin` gives the pin), settles the executor
    configuration from the `config.json` of the configurator `home`, freezes the case in a new trial folder under
    `root` (or in `trial_dir`), and builds the device descriptor, the firmware, the mission, the sha256 of `token`
    and the client record. The device is one of synthetic peers started from the installed `tq9-v2` image, unless
    `descriptor` gives one. `request` holds what a harness's request holds: `goal`, `effects`, `executor`,
    `executor_config`, `budget_seconds`, `request_id` and `scoped_tool_approval`. `case_options` holds the scenario,
    the seed and the analysis tools."""
    from pathlib import Path
    from generative_driver.benchmark_support import harness
    from generative_driver.benchmark_support.registry import pin_case
    options = dict(case_options or {})
    pin = pin or pin_case(case, options.get('scenario_id'), options.get('case_seed', 0))
    executor, config = harness.settled_executor(home or Path(root) / 'no-home', request)
    added = harness_keys(test, root, pin, config, descriptor=descriptor, case_options=options, token=token,
                         trial_dir=trial_dir)
    start = SuppliedStart({**request, 'executor': executor, 'executor_config': config,
                           **{key: options[key] for key in ('ghidra_home', 'java_home') if key in options}, **added})
    start.trial_dir = added.trial_dir
    return start


def harness_keys(test, root, pin, config, *, descriptor=None, case_options=None, token=HARNESS_TOKEN,
                 trial_dir=None):
    """The five keys the bench harness adds to a start, or to a suite child's frozen request, for the case pinned as
    `pin` under the executor configuration `config`: a `SuppliedStart` with only those keys
    (`suites.HARNESS_KEYS`). The case is frozen in a new trial folder under `root`, or in `trial_dir` when the test
    has its own trial folder. The device is one of synthetic peers started from the installed `tq9-v2` image, unless
    `descriptor` gives one."""
    import hashlib
    from pathlib import Path
    from generative_driver.benchmark_support import harness
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if trial_dir is None:
        trial_dir = root / ('trial-' + str(len(list(root.glob('trial-*')))))
    Path(trial_dir).mkdir(parents=True, exist_ok=True)
    added = SuppliedStart(harness.supplied_start(
        trial_dir, pin, descriptor or device_descriptor(test, root), config,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(), options=case_options))
    added.trial_dir = trial_dir
    return added


def outside_start(request):
    """A suite child's request as a plain `start` takes it: without the case, its pin and its options, which `start`
    refuses. The suite book starts a child with the same plain start, so a run started so, outside its suite, is the
    slot's child when its request is the frozen slot's and its client record names the slot's case and pin."""
    return {key: value for key, value in request.items() if key not in ('case', 'case_pin', 'case_options')}


def options(secrets, **extra):
    """Suite start or resume options carrying the harness's secrets from `harness_devices`."""
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
    asked = owner_call(owner)
    return wait_suite(lambda method,params,home:asked(method,params),suite_id,None,predicate)


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
    requests as a harness does, a suite call to the suite book of the owner's home and every other method to `owner`
    (`service`). With `wait`, returns the harness's record once it has finished; otherwise the record once the suite
    was taken on or refused. A refusal raises ValueError with its reason. `intercept(method, params, forward)` may
    stand between the harness and its requests, by method name: it returns the reply, and `forward(method, params)`
    makes the request."""
    import threading
    import uuid
    from pathlib import Path
    from generative_driver.benchmark_support import harness
    from generative_driver.benchmark_support.suite_book import answered
    asked, kept = service(owner), book(owner)
    def forward(method, params=None):
        if method.startswith('suite_'):
            return answered(kept, method, params or {})
        return asked(method, params or {})
    def configurator(method, params=None):
        if calls is not None:
            calls.append((method, params))
        if method == 'ping':
            return asked('ping')
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
            reply = client_call('suite_cancel', {'suite_id':record['suite_id']}, home, autostart=False)
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

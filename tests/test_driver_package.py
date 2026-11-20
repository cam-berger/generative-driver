"""A driver package runs on the installed runtime over its own data.

Packages are emitted here from invented models with replayed evidence, or copied from `tests/fixtures/earlier-package`,
which an earlier kit emitted; the device is the smoke's stand-in device on a localhost port. Nothing here is physical
evidence."""
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_package import example_model, example_replay

EARLIER = Path(__file__).resolve().parent / 'fixtures' / 'earlier-package' / 'package'


def emit(root, model=None, contract=None):
    """A package the installed kit emits from `model` with replayed evidence of `measure`; `contract`, when given, is
    authored beside the model with the protocol the kit derives from it."""
    from generative_driver.toolkit import call_tool
    from interface_runtime.contracts import from_model
    model = model or example_model()
    source = root / 'model'
    source.mkdir(parents=True)
    (source / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    if contract is not None:
        (source / 'contract.json').write_text(json.dumps(contract), encoding='utf-8')
        (source / 'protocol.json').write_text(json.dumps(from_model(model)[1]), encoding='utf-8')
    replay = root / 'replay.json'
    replay.write_text(json.dumps(example_replay()), encoding='utf-8')
    probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source), 'operation': 'measure',
                                    'n': 1, 'replay': str(replay)})
    assert probe.get('ok'), probe
    emitted = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                         'probe': probe['probe']})
    assert emitted.get('ok'), emitted
    return Path(emitted['package_dir'])


def without_unit(model):
    """`model` with its temperature decoder stating no unit."""
    model = copy.deepcopy(model)
    del model['operations']['measure']['outputs']['temperature']['unit']
    return model


def earlier(root):
    """A copy of the package an earlier kit emitted, which tests may change."""
    return Path(shutil.copytree(EARLIER, root / 'earlier-package'))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remanifest(package, relative=None):
    """Rewrite `package`'s manifest for its current files, as an emitting kit would, after a change to `relative`."""
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    manifest['files'] = {path.relative_to(package).as_posix(): sha256(path) for path in sorted(package.rglob('*'))
                         if path.is_file() and path.name not in ('manifest.json', 'manifest.sha256')}
    key = {'driver/model.json': 'model', 'probe.json': 'probe', 'driver/contract.json': 'contract',
           'driver/protocol.json': 'protocol', 'mcp-tools.json': 'mcp_catalog'}.get(relative)
    if key and key + '_sha256' in manifest:
        manifest[key + '_sha256'] = manifest['files'][relative]
    (package / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (package / 'manifest.sha256').write_text(sha256(package / 'manifest.json') + '\n', encoding='utf-8')
    return package


def rewrite(package, relative, change):
    """Apply `change` to the JSON document `relative` of `package` and rewrite its manifest to match."""
    path = package / relative
    document = json.loads(path.read_text(encoding='utf-8'))
    change(document)
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    return remanifest(package, relative)


def live_model():
    """The invented model on TCP, which the stand-in device answers."""
    model = example_model()
    model['channel'] = {'type': 'tcp'}
    return model


# What a bundled copy runs if anything imports it.
POISON = "\nraise RuntimeError('the package carries this copy; the kit must not import it')\n"


def other_runtime(package):
    """`package` as a kit with another runtime would have emitted it: its bundled Python differs from the installed
    kit's, names a module the installed kit lacks and lacks one it has, and fails if anything imports it."""
    for path in [*(package / 'driver').glob('*.py'), *(package / 'interface_runtime').glob('*.py')]:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(POISON)
    (package / 'interface_runtime/legacy.py').write_text(POISON, encoding='utf-8')
    (package / 'interface_runtime/qualify.py').unlink()
    return remanifest(package)


def commented(package):
    """`package` as a kit whose runtime differs by a comment would have emitted it."""
    with (package / 'interface_runtime/engine.py').open('a', encoding='utf-8') as stream:
        stream.write('\n# A later comment in the runtime.\n')
    return remanifest(package)


def defects(result):
    return ' '.join(str(defect.get('what')) for defect in result.get('defects') or [])


def check(package, root):
    from generative_driver.toolkit import call_tool
    return call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package)})


def another_kit(root):
    """A copy of the installed kit whose runtime differs by a comment, as after an upgrade."""
    import generative_driver
    import interface_runtime
    kit = root / 'later-kit'
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
    shutil.copytree(Path(generative_driver.__file__).resolve().parent, kit / 'generative_driver', ignore=ignore)
    shutil.copytree(Path(interface_runtime.__file__).resolve().parent, kit / 'interface_runtime', ignore=ignore)
    with (kit / 'interface_runtime/engine.py').open('a', encoding='utf-8') as stream:
        stream.write('\n# A comment a later kit added to its runtime.\n')
    return kit


def later_kit_call(kit, home, method, params, autostart=True):
    """`method`'s reply from the service of `kit`, asked by `kit`'s own client in a child process: a client never
    drives a service running other code. The child imports `kit` alone, which it reports."""
    import os
    import subprocess
    import sys
    script = ('import json, sys\nimport generative_driver\nfrom generative_driver.client import call\n'
              'reply = call(sys.argv[2], json.loads(sys.argv[3]), sys.argv[1], autostart=sys.argv[4] == "1")\n'
              'print(json.dumps({"kit": generative_driver.__file__, "reply": reply}))\n')
    env = {**os.environ, 'PYTHONPATH': str(kit)}
    done = subprocess.run([sys.executable, '-c', script, str(home), method, json.dumps(params),
                           '1' if autostart else '0'],
                          env=env, cwd=str(kit.parent), capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert Path(answer['kit']).resolve().is_relative_to(kit.resolve()), answer
    return answer['reply']


def fresh_reuse(root, package, binding):
    """A generic run whose emit stage hands on `package` and whose reuse worker, through the configurator's generated
    MCP gateway, lists its tools and calls `device_measure`: (what the worker saw, the run's events). Stages before
    emit are skipped; the run's own preparation and gateway serve reuse."""
    from generative_driver.configurator import Controller
    from test_configurator import scripted_stages, gateway_identity, task_of, wait_stopped
    seen = {}
    controller = Controller(root / 'home')

    def prepare(original, spec, stage, run_dir, workspace, accepted):
        if stage == 'reuse':
            return original(spec, stage, run_dir, workspace, accepted)
        return {'inputs': [], 'allowed_tools': []} if stage == 'emit' else {'skip': 'Not part of this test'}

    def check(spec, stage, run_dir, workspace, report, accepted, assignment):
        return {'ok': True, 'artifacts': [str(workspace / package.name)] if stage == 'emit' else [], 'checks': []}

    def work(request):
        if request.stage == 'emit':
            shutil.copytree(package, Path(request.workspace) / package.name)
        if request.stage != 'reuse':
            return
        identity = gateway_identity(request)
        seen['inputs'] = [Path(path) for path in task_of(request.prompt)['inputs']]
        seen['tools'] = controller.call('mcp_tools', identity)['tools']
        try:
            seen['result'] = controller.call('mcp_tool', {**identity, 'name': 'device_measure', 'arguments': {},
                                                          'request_id': 'fresh-reuse-measure'})
        except Exception as error:
            seen['result'] = {'raised': type(error).__name__ + ': ' + str(error)}
    try:
        with scripted_stages(controller, check=check, prepare=prepare, work=work):
            run = controller.call('start', {'goal': 'Read the temperature through the emitted driver',
                                            'binding': binding, 'executor_config': {'command': ['never-launched']}})
            wait_stopped(controller, run)
        events = controller.call('events', run)['events']
    finally:
        controller.close()
    return seen, events


def catalog_tool(package, name):
    catalog = json.loads((package / 'mcp-tools.json').read_text(encoding='utf-8'))
    return catalog, next(tool for tool in catalog['tools'] if tool['name'] == name)


def output_description(tool, output):
    return tool['outputSchema']['properties']['outputs']['properties'][output]['description']


class RendererTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_05_B9_new_catalogs_say_a_unit_is_not_stated_by_the_device(self):
        from interface_runtime.contracts import from_model
        model = without_unit(example_model())
        contract = from_model(model)[0]
        contract['operations']['measure']['outputs']['temperature']['uncertainties'] = [{
            'code': 'unit_not_stated', 'message': 'The reply T:21.5 carries no unit, nor does its handler.',
            'evidence_refs': ['replay.json transaction 2']}]
        catalog, tool = catalog_tool(emit(self.root, model, contract), 'device_measure')
        self.assertEqual(catalog['renderer_version'], '3')
        self.assertEqual(output_description(tool, 'temperature').split('\n'), [
            'Decoded output temperature.', 'Unit: not stated by the device.',
            'Warning: The reply T:21.5 carries no unit, nor does its handler.'])

    def test_05_B9_a_null_unit_without_unit_not_stated_still_reads_unknown(self):
        catalog, tool = catalog_tool(emit(self.root, without_unit(example_model())), 'device_measure')
        self.assertEqual(catalog['renderer_version'], '3')
        self.assertEqual(output_description(tool, 'temperature').split('\n'), [
            'Decoded output temperature.', 'Unit: unknown.',
            'Warning: Output temperature meaning and operating assumptions are not documented.'])
        # A stated unit renders as before.
        _, tool = catalog_tool(emit(self.root / 'stated', example_model()), 'device_measure')
        self.assertEqual(output_description(tool, 'temperature').split('\n'),
                         ['Decoded output temperature.', 'Unit: degC.'])


class FormatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_05_B4_an_earlier_package_verifies_under_the_renderer_it_names(self):
        from interface_runtime.package import verify_package
        package = earlier(self.root)
        catalog = json.loads((package / 'mcp-tools.json').read_text(encoding='utf-8'))
        self.assertEqual(catalog['renderer_version'], '2')
        self.assertEqual(verify_package(package)['defects'], [])
        checked = check(package, self.root)
        self.assertIs(checked['integrity_ok'], True, checked)

    def test_05_B4_a_package_naming_a_format_the_kit_does_not_know_is_refused_naming_it(self):
        changes = {
            'renderer 9': ('mcp-tools.json', lambda catalog: catalog.update(renderer_version='9'), "'9'"),
            'catalog format': ('mcp-tools.json', lambda catalog: catalog.update(schema='driver-mcp/9'), 'driver-mcp/9'),
            'contract format': ('driver/contract.json', lambda contract: contract.update(schema='driver-contract/9'),
                                'driver-contract/9'),
            'protocol format': ('driver/protocol.json', lambda protocol: protocol.update(schema='driver-protocol/9'),
                                'driver-protocol/9'),
            'model format': ('driver/protocol.json',
                             lambda protocol: protocol.update(runtime_schema='interface-model/9'),
                             'interface-model/9'),
            'package format': ('manifest.json', None, 'interface-package/9'),
        }
        for label, (relative, change, named) in changes.items():
            with self.subTest(label):
                package = earlier(self.root / label.replace(' ', '-'))
                if relative == 'manifest.json':
                    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
                    manifest['schema'] = 'interface-package/9'
                    (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                    (package / 'manifest.sha256').write_text(sha256(package / 'manifest.json'), encoding='utf-8')
                else:
                    rewrite(package, relative, change)
                checked = check(package, self.root / label.replace(' ', '-'))
                self.assertEqual((checked['ok'], checked.get('integrity_ok')), (False, False), checked)
                self.assertIn(named, defects(checked))


class PackageCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def session(self, package, binding, home=None):
        """A managed session the configurator opens over `package`, and its execution of `measure`."""
        from generative_driver.configurator import Controller
        controller = Controller(home or self.root / 'owner')
        try:
            opened = controller.call('device_open', {'package_dir': str(package), 'binding': binding,
                                                     'effects': ['read']})
            executed = controller.call('device_execute', {'session_id': opened['session_id'], 'operation': 'measure',
                                                          'parameters': {}, 'request_id': 'measure-once'})
        finally:
            controller.close()
        return opened, executed


class InstalledRuntimeTests(PackageCase):
    def test_05_B1_emit_test_live_runs_the_installed_runtime_over_the_package_data(self):
        from generative_driver import stand_in_device
        from generative_driver.toolkit import call_tool
        package = other_runtime(emit(self.root, live_model()))
        with stand_in_device.serving() as binding:
            tested = call_tool('emit_test_live', {'run_dir': str(self.root / 'live'), 'package_dir': str(package),
                                                  'binding': binding, 'operations': ['identify', 'measure']})
        self.assertEqual((tested['ok'], tested['verdict']), (True, 'pass'), tested)
        self.assertEqual([(r['operation'], r['ok'], r.get('outputs')) for r in tested['results']],
                         [('identify', True, {}), ('measure', True, {'temperature': 21.5})])
        # The result names the package that was checked and ran, not the kit's own copy.
        self.assertEqual(tested['manifest_sha256'], sha256(package / 'manifest.json'))

    def test_05_B1_the_replay_in_emit_check_runs_the_installed_runtime(self):
        checked = check(other_runtime(emit(self.root)), self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok'], checked['runtime_current'],
                          checked['replay_status']), (True, True, False, 'passed'), checked)

    def test_05_B1_a_managed_session_opens_and_executes_on_the_installed_runtime(self):
        from generative_driver import stand_in_device
        package = other_runtime(emit(self.root, live_model()))
        with stand_in_device.serving() as binding:
            opened, executed = self.session(package, binding)
        self.assertEqual(opened['manifest_sha256'], sha256(package / 'manifest.json'))
        self.assertEqual((executed['ok'], executed['outputs']), (True, {'temperature': 21.5}), executed)

    def test_05_B1_a_session_opened_before_the_kit_changed_keeps_executing(self):
        from generative_driver import stand_in_device
        package = emit(self.root, live_model())
        home = self.root / 'owner'
        kit = another_kit(self.root)
        with stand_in_device.serving() as binding:
            opened, before = self.session(package, binding, home)
            self.assertTrue(before['ok'], before)
            try:
                after = later_kit_call(kit, home, 'device_execute', {
                    'session_id': opened['session_id'], 'operation': 'measure', 'parameters': {},
                    'request_id': 'after-the-upgrade'})
            finally:
                later_kit_call(kit, home, 'shutdown', {}, autostart=False)
        self.assertEqual((after.get('ok'), after.get('outputs')), (True, {'temperature': 21.5}), after)

    def test_05_B1_a_generated_tool_call_in_fresh_reuse_runs_the_installed_runtime(self):
        from generative_driver import stand_in_device
        package = other_runtime(emit(self.root / 'emitted', live_model()))
        with stand_in_device.serving() as binding:
            seen, _ = fresh_reuse(self.root, package, binding)
        self.assertEqual((seen['result'].get('ok'), seen['result'].get('outputs')), (True, {'temperature': 21.5}),
                         seen['result'])


class IntegrityTests(PackageCase):
    def test_05_B2_a_bundled_copy_altered_after_emission_is_refused(self):
        from generative_driver import stand_in_device
        from generative_driver.configurator import Controller
        from generative_driver.toolkit import call_tool
        package = emit(self.root, live_model())
        with (package / 'interface_runtime/engine.py').open('a', encoding='utf-8') as stream:
            stream.write('\n# Altered after emission.\n')
        checked = check(package, self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok']), (False, False), checked)
        self.assertIn('changed: interface_runtime/engine.py', defects(checked))
        with stand_in_device.serving() as binding:
            tested = call_tool('emit_test_live', {'run_dir': str(self.root / 'live'), 'package_dir': str(package),
                                                  'binding': binding})
            self.assertEqual((tested['ok'], tested['transport_open_attempted']), (False, False), tested)
            self.assertIn('changed: interface_runtime/engine.py', defects(tested))
            controller = Controller(self.root / 'owner')
            try:
                with self.assertRaisesRegex(ValueError, 'changed: interface_runtime/engine.py'):
                    controller.call('device_open', {'package_dir': str(package), 'binding': binding})
            finally:
                controller.close()

    def test_05_B2_a_session_refuses_its_kept_copy_once_altered(self):
        from generative_driver import stand_in_device
        from generative_driver.configurator import Controller
        package = emit(self.root, live_model())
        controller = Controller(self.root / 'owner')
        try:
            with stand_in_device.serving() as binding:
                opened = controller.call('device_open', {'package_dir': str(package), 'binding': binding})
                [kept] = (self.root / 'owner' / 'devices').glob('*/package/interface_runtime/engine.py')
                with kept.open('a', encoding='utf-8') as stream:
                    stream.write('\n# Altered in the session.\n')
                with self.assertRaisesRegex(ValueError, 'changed: interface_runtime/engine.py'):
                    controller.call('device_execute', {'session_id': opened['session_id'], 'operation': 'measure',
                                                       'parameters': {}, 'request_id': 'after-alteration'})
        finally:
            controller.close()

    def test_05_B2_the_manifest_must_match_its_checksum_and_any_pin(self):
        from generative_driver.toolkit import call_tool
        package = emit(self.root)
        pinned = call_tool('emit_check', {'run_dir': str(self.root / 'check'), 'package_dir': str(package),
                                          'expected_manifest_sha256': '0' * 64})
        self.assertEqual((pinned['ok'], pinned['integrity_ok']), (False, False), pinned)
        self.assertIn('manifest checksum/pin mismatch', defects(pinned))
        (package / 'manifest.sha256').write_text('0' * 64 + '\n', encoding='utf-8')
        checked = check(package, self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok']), (False, False), checked)
        self.assertIn('manifest checksum/pin mismatch', defects(checked))


class ReplayTests(PackageCase):
    def test_05_B3_emit_check_replays_a_package_whose_runtime_differs_by_a_comment(self):
        checked = check(commented(emit(self.root)), self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok'], checked['runtime_current'],
                          checked['replay_status']), (True, True, False, 'passed'), checked)
        self.assertEqual(checked['replay_reason'], 'The replay ran on the installed runtime, because the package '
                                                   'carries another copy of the runtime')

    def test_05_B3_a_replay_that_fails_on_the_installed_runtime_fails_the_check(self):
        package = commented(emit(self.root))
        rewrite(package, 'probe.json', lambda probe: probe['results'][0]['outputs'].update(temperature=99.0))
        checked = check(package, self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok'], checked['runtime_current'],
                          checked['replay_status']), (False, True, False, 'failed'), checked)
        self.assertIn('installed runtime', checked['replay_reason'])

    def test_05_B3_the_generic_emit_gate_rejects_a_package_with_another_runtime_saying_so(self):
        from generative_driver.configurator import Controller, digest
        from test_configurator import scripted_stages, gateway_identity, wait_stopped
        source = commented(emit(self.root / 'emitted'))
        controller = Controller(self.root / 'home')

        def prepare(original, spec, stage, run_dir, workspace, accepted):
            return ({'inputs': [], 'allowed_tools': ['emit_package', 'emit_check']} if stage == 'emit'
                    else {'skip': 'Not part of this test'})

        def work(request):
            if request.stage == 'emit':
                package = shutil.copytree(source, Path(request.workspace) / 'package')
                controller.call('tool', {**gateway_identity(request), 'name': 'emit_check',
                                         'arguments': {'package_dir': str(package)}})

        def report(request):
            package = Path(request.workspace) / 'package'
            return {'status': 'completed', 'summary': 'Checked a package', 'checks': [], 'unresolved': [],
                    'artifacts': [{'path': str(package), 'sha256': digest(package), 'kind': 'package'}]}
        try:
            with scripted_stages(controller, check=controller._check, prepare=prepare, work=work, report=report):
                run = controller.call('start', {'goal': 'Emit the driver', 'executor_config': {'command': ['none']}})
                result = wait_stopped(controller, run)
        finally:
            controller.close()
        [emitted] = [h for h in result['accepted_handoffs'] if h['stage'] == 'emit']
        [gate] = [c for c in emitted['notes']['inline']['configurator']['checks']
                  if c['name'] == 'package_integrity_and_replay']
        self.assertEqual((emitted['artifacts'], gate['passed']), ([], False), gate)
        self.assertEqual(gate['rejected'], [{'package': 'package',
                                             'reason': "the package carries a runtime other than this kit's"}])


class EarlierPackageTests(PackageCase):
    def test_05_B5_emit_check_replays_the_earlier_package_on_the_installed_runtime(self):
        checked = check(earlier(self.root), self.root)
        self.assertEqual((checked['integrity_ok'], checked['runtime_current'], checked['replay_status']),
                         (True, False, 'passed'), checked)

    def test_05_B5_a_managed_session_over_the_earlier_package_measures_the_stand_in_device(self):
        from generative_driver import stand_in_device
        with stand_in_device.serving() as binding:
            opened, executed = self.session(earlier(self.root), binding)
        self.assertTrue(opened['ok'], opened)
        self.assertEqual((executed['ok'], executed['outputs']), (True, {'temperature': 21.5}), executed)

    def test_05_B5_fresh_reuse_is_served_the_earlier_catalog_unchanged_and_a_generated_tool_runs(self):
        from generative_driver import stand_in_device
        with stand_in_device.serving() as binding:
            seen, events = fresh_reuse(self.root, earlier(self.root), binding)
        catalog = json.loads((EARLIER / 'mcp-tools.json').read_text(encoding='utf-8'))
        self.assertEqual(seen['tools'], catalog['tools'])
        [package] = seen['inputs']
        self.assertEqual((package / 'mcp-tools.json').read_bytes(), (EARLIER / 'mcp-tools.json').read_bytes())
        served = [event['data'] for event in events if event['kind'] == 'mcp.catalog_served']
        self.assertEqual([entry['catalog_sha256'] for entry in served], [sha256(EARLIER / 'mcp-tools.json')])
        self.assertEqual((seen['result'].get('ok'), seen['result'].get('outputs')), (True, {'temperature': 21.5}),
                         seen['result'])


if __name__ == '__main__':
    unittest.main()

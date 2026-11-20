"""Every use of a package runs the installed runtime over the package's data, in a child process started one way
(docs/features/package-format/03-package.md, Behaviour 03-B4).

The serial port here is a stand-in module named `serial` on the import path of the test process, which starts the
child: it records each process that imports it and answers the invented model's two requests. No port is opened and
nothing here is physical evidence."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from test_package import example_model
from test_package_data import emitted, samp1, sha256

STAND_IN = '''"""A stand-in for the optional serial package: it records the process that imports it and answers two
requests."""
import os

with open(MARKER, 'a', encoding='utf-8') as stream:
    stream.write(str(os.getpid()) + '\\n')

REPLIES = {b'ID?\\n': b'DEMO-42\\n', b'T\\n': b'T:21.5\\n'}


class Serial:
    def __init__(self):
        self._waiting = b''

    def open(self):
        pass

    def close(self):
        pass

    def reset_input_buffer(self):
        self._waiting = b''

    def write(self, data):
        self._waiting += REPLIES[bytes(data)]
        return len(data)

    @property
    def in_waiting(self):
        return len(self._waiting)

    def read(self, size):
        data, self._waiting = self._waiting[:size], self._waiting[size:]
        return data
'''
PORT = {'port': 'stand-in-port'}


def serial_model():
    """The invented model of SAMP1 on a serial channel."""
    model = example_model()
    model['device'] = {'name': 'SAMP1'}
    return model


def call_measure(controller, identity, seen):
    """A fresh reuse worker that calls the generated tool of `measure` once."""
    [measure] = [tool['name'] for tool in seen['tools'] if tool['name'].endswith('measure')]
    seen['result'] = controller.call('mcp_tool', {**identity, 'name': measure, 'arguments': {},
                                                  'request_id': 'fresh-reuse-measure'})


def fresh_reuse(root, package, binding, effects=('read',), work=call_measure):
    """A generic run whose emit stage hands on `package` and whose reuse worker lists its generated tools and then
    does `work(controller, identity, seen)` through the configurator's gateway. Returns what the worker saw: its
    `task`, the `tools` it was served, what `work` kept, and the run's `events`. Stages before emit are skipped; the
    run's own preparation and gateway serve reuse."""
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

    def script(request):
        if request.stage == 'emit':
            shutil.copytree(package, Path(request.workspace) / package.name)
        if request.stage != 'reuse':
            return
        identity = gateway_identity(request)
        seen['task'] = task_of(request.prompt)
        try:
            seen['tools'] = controller.call('mcp_tools', identity)['tools']
            work(controller, identity, seen)
        except Exception as error:
            seen['result'] = {'raised': type(error).__name__ + ': ' + str(error)}
    try:
        with scripted_stages(controller, check=check, prepare=prepare, work=script):
            run = controller.call('start', {'goal': 'Read the temperature through the emitted driver',
                                            'binding': binding, 'effects': list(effects),
                                            'executor_config': {'command': ['never-launched']}})
            wait_stopped(controller, run)
        seen['events'] = controller.call('events', run)['events']
    finally:
        controller.close()
    return seen


class ChildCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def package(self, model, label='emitted'):
        return Path(emitted(self.root / label, model)[0]['package_dir'])


class StartedOneWayTests(ChildCase):
    """On each path a module on the import path of the process that starts the child is imported by the child."""

    def setUp(self):
        super().setUp()
        folder = self.root / 'stand-in'
        (folder / 'serial').mkdir(parents=True)
        self.marker = self.root / 'imported.txt'
        (folder / 'serial' / '__init__.py').write_text(STAND_IN.replace('MARKER', repr(str(self.marker))),
                                                       encoding='utf-8')
        sys.path.insert(0, str(folder))
        self.addCleanup(sys.path.remove, str(folder))
        self.addCleanup(sys.modules.pop, 'serial', None)

    def assertRanInAChild(self, result):
        """The operation gave the stand-in port's reading, and a process other than this one imported the stand-in."""
        self.assertEqual((result.get('ok'), result.get('outputs')), (True, {'temperature': 21.5}), result)
        importers = [int(line) for line in self.marker.read_text(encoding='utf-8').split()] \
            if self.marker.exists() else []
        self.assertTrue(importers, 'no process imported the stand-in module')
        self.assertNotIn(os.getpid(), importers)
        self.assertNotIn('serial', sys.modules)

    def test_03_B4_emit_test_live_runs_in_a_child_that_imports_what_the_tool_can_import(self):
        from generative_driver.toolkit import call_tool
        package = self.package(serial_model())
        tested = call_tool('emit_test_live', {'run_dir': str(self.root / 'live'), 'package_dir': str(package),
                                              'binding': PORT, 'operations': ['identify', 'measure']})
        self.assertEqual((tested['ok'], tested['verdict'], tested['manifest_sha256']),
                         (True, 'pass', sha256(package / 'manifest.json')), tested)
        self.assertEqual([(r['operation'], r['ok'], r.get('identity_verified')) for r in tested['results']],
                         [('identify', True, True), ('measure', True, True)])
        self.assertRanInAChild(tested['results'][1])
        record = json.loads(Path(tested['live_record']).read_text(encoding='utf-8'))
        self.assertEqual((record['schema'], record['manifest_sha256'], record['model_sha256']),
                         ('interface-live-test/1', tested['manifest_sha256'], sha256(package / 'drivers/samp1.json')))

    def test_03_B4_a_managed_session_runs_in_a_child_that_imports_what_the_tool_can_import(self):
        from generative_driver.configurator import Controller
        package = self.package(serial_model())
        controller = Controller(self.root / 'owner')
        try:
            opened = controller.call('device_open', {'package_dir': str(package), 'binding': PORT,
                                                     'effects': ['read']})
            executed = controller.call('device_execute', {'session_id': opened['session_id'], 'operation': 'measure',
                                                          'parameters': {}, 'request_id': 'measure-once'})
        finally:
            controller.close()
        self.assertEqual((executed['schema'], executed['model_sha256']),
                         ('driver-result/1', sha256(package / 'drivers/samp1.json')), executed)
        self.assertRanInAChild(executed)

    def test_03_B4_a_generated_tool_call_in_fresh_reuse_runs_in_a_child_that_imports_what_the_tool_can_import(self):
        package = self.package(serial_model())
        seen = fresh_reuse(self.root, package, PORT, effects=('read', 'reset'))
        self.assertEqual(seen['result'].get('schema'), 'driver-result/1', seen['result'])
        self.assertRanInAChild(seen['result'])


class SessionCopyTests(ChildCase):
    def session(self, package, binding):
        from generative_driver.configurator import Controller
        controller = Controller(self.root / 'owner')
        self.addCleanup(controller.close)
        opened = controller.call('device_open', {'package_dir': str(package), 'binding': binding})

        def execute(request_id):
            return controller.call('device_execute', {'session_id': opened['session_id'], 'operation': 'measure',
                                                      'parameters': {}, 'request_id': request_id})
        return execute

    def test_03_B4_a_package_changed_after_device_open_does_not_change_the_session(self):
        from generative_driver import stand_in_device
        package = self.package(samp1())
        with stand_in_device.serving() as binding:
            execute = self.session(package, binding)
            before = execute('before')
            shutil.rmtree(package)
            after = execute('after')
        self.assertEqual((before['ok'], before['outputs']), (True, {'temperature': 21.5}), before)
        self.assertEqual((after['ok'], after['outputs']), (True, {'temperature': 21.5}), after)

    def test_03_B4_a_session_refuses_its_kept_copy_once_that_copy_is_altered(self):
        from generative_driver import stand_in_device
        package = self.package(samp1())
        with stand_in_device.serving() as binding:
            execute = self.session(package, binding)
            [kept] = (self.root / 'owner' / 'devices').glob('*/package/README.md')
            with kept.open('a', encoding='utf-8') as stream:
                stream.write('Altered in the session.\n')
            with self.assertRaisesRegex(ValueError, 'changed: README.md'):
                execute('after-alteration')


if __name__ == '__main__':
    unittest.main()

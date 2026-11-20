"""A driver package runs on the installed runtime over its own data.

Packages are emitted here from invented models with replayed evidence; the device is the smoke's stand-in device on a
localhost port. Nothing here is physical evidence. The tests of a package's own copy of the runtime went with that
copy: a package holds data and no code (docs/features/package-format/03-package.md), and what its uses run on is
shown in `tests/test_package_child.py`."""
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_package import example_model, example_replay


def emit(root, model=None):
    """A package the installed kit emits from `model` with replayed evidence of `measure`."""
    from generative_driver.toolkit import call_tool
    model = model or example_model()
    source = root / 'model'
    source.mkdir(parents=True)
    (source / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    replay = root / 'replay.json'
    replay.write_text(json.dumps(example_replay()), encoding='utf-8')
    probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source), 'operation': 'measure',
                                    'n': 1, 'replay': str(replay)})
    assert probe.get('ok'), probe
    emitted = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                         'probe': probe['probe']})
    assert emitted.get('ok'), emitted
    return Path(emitted['package_dir'])


DESCRIBED = 'Temperature of the probe.'


def described(model):
    """`model` with its temperature described, so that a rendered description shows what the model says of the unit."""
    model = copy.deepcopy(model)
    model['operations']['measure']['outputs']['temperature']['description'] = DESCRIBED
    return model


def without_unit(model):
    """`model` with its temperature described and its decoder stating no unit."""
    model = described(model)
    del model['operations']['measure']['outputs']['temperature']['unit']
    return model


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def live_model():
    """The invented model on TCP, which the stand-in device answers."""
    model = example_model()
    model['channel'] = {'type': 'tcp'}
    return model


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


def catalog_tool(package, name):
    catalog = json.loads((package / 'mcp.json').read_text(encoding='utf-8'))
    return catalog, next(tool for tool in catalog['tools'] if tool['name'] == name)


# The tool of `measure`, named after the device of the example model.
MEASURE = 'fictional_demonstration_measure'


def output_description(tool, output):
    return tool['outputSchema']['properties']['outputs']['properties'][output]['description']


class RendererTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_05_B9_new_catalogs_say_a_unit_is_not_stated_by_the_device(self):
        model = without_unit(example_model())
        model['operations']['measure']['outputs']['temperature']['uncertainties'] = [{
            'code': 'unit_not_stated', 'message': 'The reply T:21.5 carries no unit, nor does its handler.',
            'evidence_refs': ['replay.json transaction 2']}]
        _, tool = catalog_tool(emit(self.root, model), MEASURE)
        self.assertEqual(output_description(tool, 'temperature').split('\n'), [
            DESCRIBED, 'Unit: not stated by the device.',
            'Warning: The reply T:21.5 carries no unit, nor does its handler.'])

    def test_05_B9_a_null_unit_without_unit_not_stated_still_reads_unknown(self):
        _, tool = catalog_tool(emit(self.root, without_unit(example_model())), MEASURE)
        self.assertEqual(output_description(tool, 'temperature').split('\n'), [DESCRIBED, 'Unit: unknown.'])
        # A stated unit renders as before.
        _, tool = catalog_tool(emit(self.root / 'stated', described(example_model())), MEASURE)
        self.assertEqual(output_description(tool, 'temperature').split('\n'), [DESCRIBED, 'Unit: degC.'])


class FormatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_05_B4_a_package_naming_a_format_the_kit_does_not_know_is_refused_naming_it(self):
        package = emit(self.root)
        manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
        manifest['schema'] = 'interface-package/9'
        (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        checked = check(package, self.root)
        self.assertEqual((checked['ok'], checked.get('integrity_ok')), (False, False), checked)
        self.assertIn('interface-package/9', defects(checked))


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


class IntegrityTests(PackageCase):
    def test_05_B2_the_manifest_must_match_a_pin(self):
        from generative_driver.toolkit import call_tool
        package = emit(self.root)
        pinned = call_tool('emit_check', {'run_dir': str(self.root / 'check'), 'package_dir': str(package),
                                          'expected_manifest_sha256': '0' * 64})
        self.assertEqual((pinned['ok'], pinned['integrity_ok']), (False, False), pinned)
        self.assertIn('manifest pin mismatch', defects(pinned))
        matching = call_tool('emit_check', {'run_dir': str(self.root / 'check'), 'package_dir': str(package),
                                            'expected_manifest_sha256': sha256(package / 'manifest.json')})
        self.assertEqual((matching['ok'], matching['integrity_ok']), (True, True), matching)


if __name__ == '__main__':
    unittest.main()

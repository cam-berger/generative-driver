"""A package is five files of data, and the package check refuses everything else
(docs/features/package-format/03-package.md, Behaviours 03-B1, 03-B2, 03-B3 and 03-B15).

Packages are emitted here from invented models with replayed evidence; the device is the smoke's stand-in device on a
localhost port. Nothing here is physical evidence."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_package import example_model, example_replay

FILES = ['README.md', 'drivers/samp1.json', 'evidence/samp1.probe.json', 'manifest.json', 'mcp.json']


def samp1():
    """An invented model of a device named SAMP1 on TCP, with the operations `identify` and `measure`."""
    model = example_model()
    model['device'] = {'name': 'SAMP1'}
    model['channel'] = {'type': 'tcp'}
    return model


def emitted(root, model=None, operation='measure', grants=(), **arguments):
    """What `emit_package` gives for `model` with replayed evidence of `operation`, which is `measure` or the identity:
    its result, the model file and the probe record it was given. The replay is granted `grants` beside the read."""
    from generative_driver.toolkit import call_tool
    source = root / 'model'
    source.mkdir(parents=True)
    (source / 'model.json').write_text(json.dumps(model or samp1(), indent=1), encoding='utf-8')
    replay = root / 'replay.json'
    recorded = example_replay()
    if operation != 'measure':
        recorded['transactions'] = recorded['transactions'][:1]
    replay.write_text(json.dumps(recorded), encoding='utf-8')
    probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source), 'operation': operation,
                                    'n': 1, 'replay': str(replay), 'allow_effects': list(grants)})
    assert probe.get('ok'), probe
    result = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                        'probe': probe['probe'], **arguments})
    assert result.get('ok'), result
    return result, source / 'model.json', Path(probe['probe'])


def files_of(package):
    return sorted(path.relative_to(package).as_posix() for path in Path(package).rglob('*') if not path.is_dir())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PackageDataCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()


class EmittedFilesTests(PackageDataCase):
    def test_03_B1_emit_package_writes_the_five_files_of_a_package_and_nothing_else(self):
        result, model, probe = emitted(self.root)
        package = Path(result['package_dir'])
        self.assertEqual(files_of(package), FILES)
        self.assertEqual(sorted(path.name for path in package.iterdir()),
                         ['README.md', 'drivers', 'evidence', 'manifest.json', 'mcp.json'])
        # The driver is the model and the evidence is the probe record, byte for byte.
        self.assertEqual((package / 'drivers/samp1.json').read_bytes(), model.read_bytes())
        self.assertEqual((package / 'evidence/samp1.probe.json').read_bytes(), probe.read_bytes())

    def test_03_B1_the_manifest_names_the_format_the_driver_and_a_hash_of_every_other_file(self):
        result, _, _ = emitted(self.root)
        package = Path(result['package_dir'])
        manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(manifest), ['drivers', 'files', 'name', 'schema', 'version'])
        self.assertEqual((manifest['schema'], manifest['name'], manifest['version']),
                         ('interface-package/3', 'samp1', '1.0.0'))
        self.assertEqual(manifest['drivers'], [{'name': 'samp1', 'model': 'drivers/samp1.json',
                                                'evidence': 'evidence/samp1.probe.json', 'evidence_kind': 'replay'}])
        self.assertEqual(manifest['files'], {name: sha256(package / name) for name in FILES if name != 'manifest.json'})

    def test_03_B1_the_result_names_the_folder_the_pin_and_the_kind_of_evidence(self):
        result, _, _ = emitted(self.root)
        package = Path(result['package_dir'])
        self.assertEqual({key: value for key, value in result.items() if not key.startswith('_')},
                         {'ok': True, 'package_dir': str(package), 'manifest_sha256': sha256(package / 'manifest.json'),
                          'evidence_kind': 'replay'})

    def test_03_B1_a_name_given_to_emit_package_is_the_name_of_the_package_and_not_of_its_driver(self):
        result, _, _ = emitted(self.root, name='bench thermometer', version='2.1.0')
        package = Path(result['package_dir'])
        manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual((manifest['name'], manifest['version']), ('bench thermometer', '2.1.0'))
        self.assertEqual(files_of(package), FILES)


def remanifest(package, change=None):
    """Write the manifest of `package` again for the files the folder now holds, as someone who edits a package would,
    after `change` to the manifest."""
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['files'] = {name: sha256(package / name) for name in files_of(package) if name != 'manifest.json'}
    if change is not None:
        change(manifest)
    path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return package


def rewrite(package, relative, change):
    """Apply `change` to the JSON file `relative` of `package` and write the manifest again to match."""
    path = package / relative
    document = json.loads(path.read_text(encoding='utf-8'))
    change(document)
    path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    return remanifest(package)


def defects(result):
    return ' '.join(str(defect.get('what')) for defect in result.get('defects') or [])


# A binding of the right form that no test opens: every use below is refused before a transport is opened.
UNOPENED = {'host': '127.0.0.1', 'port': 9}


class PackageCheckCase(PackageDataCase):
    def package(self, label='emitted', model=None):
        result, _, _ = emitted(self.root / label, model)
        return Path(result['package_dir'])

    def controller(self):
        from generative_driver.configurator import Controller
        controller = Controller(self.root / 'owner')
        self.addCleanup(controller.close)
        return controller

    def refusals(self, package):
        """Why each user of a package refuses `package`: the runtime's check, `emit_check`, `emit_test_live` and
        `device_open`. None of them contacts a device, writes a live record or leaves a session behind."""
        from generative_driver.toolkit import call_tool
        from interface_runtime.package import verify_package
        reasons = {}
        checked = verify_package(package)
        self.assertEqual((checked['ok'], checked.get('drivers')), (False, {}), checked)
        reasons['verify_package'] = defects(checked)
        run = self.root / ('run-' + str(len(list(self.root.glob('run-*')))))
        checked = call_tool('emit_check', {'run_dir': str(run), 'package_dir': str(package)})
        self.assertEqual((checked['ok'], checked.get('integrity_ok'), checked.get('replay_status'), checked['_exit']),
                         (False, False, 'not_run', 1), checked)
        reasons['emit_check'] = defects(checked)
        tested = call_tool('emit_test_live', {'run_dir': str(run), 'package_dir': str(package), 'binding': UNOPENED})
        self.assertEqual((tested['ok'], tested.get('transport_open_attempted'), tested['_exit']), (False, False, 1),
                         tested)
        self.assertEqual(list(run.rglob('live-*.json')), [])
        reasons['emit_test_live'] = defects(tested)
        controller = getattr(self, '_controller', None) or self.controller()
        self._controller = controller
        with self.assertRaises(ValueError) as refused:
            controller.call('device_open', {'package_dir': str(package), 'binding': UNOPENED})
        reasons['device_open'] = str(refused.exception)
        sessions = self.root / 'owner' / 'devices'
        self.assertEqual(list(sessions.iterdir()) if sessions.is_dir() else [], [])
        return reasons

    def assertRefused(self, package, *named):
        """Every user of a package refuses `package` with a reason that holds each text of `named`."""
        reasons = self.refusals(package)
        for user, reason in reasons.items():
            for text in named:
                self.assertIn(text, reason, user)
        return reasons


def earlier_layout(root, schema):
    """A folder laid out as a package of an earlier format was: its driver in `driver/model.json`, the probe record
    beside the manifest, and a second manifest file with the first one's hash."""
    package = root / 'earlier'
    (package / 'driver').mkdir(parents=True)
    (package / 'driver/model.json').write_text(json.dumps(samp1()), encoding='utf-8')
    (package / 'probe.json').write_text('{}', encoding='utf-8')
    manifest = {'schema': schema, 'name': 'device-interface', 'version': '1.0.0',
                'model_sha256': sha256(package / 'driver/model.json'), 'probe_sha256': sha256(package / 'probe.json'),
                'source_evidence_kind': 'replay',
                'files': {name: sha256(package / name) for name in ('driver/model.json', 'probe.json')}}
    (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (package / 'manifest.sha256').write_text(sha256(package / 'manifest.json') + '\n', encoding='utf-8')
    return package


class PackageCheckTests(PackageCheckCase):
    def test_03_B2_the_check_accepts_what_emit_package_wrote_and_gives_the_manifest_and_the_model(self):
        from generative_driver.toolkit import call_tool
        from interface_runtime.package import verify_package
        result, model, _ = emitted(self.root)
        package = Path(result['package_dir'])
        checked = verify_package(package)
        self.assertEqual((checked['ok'], checked['defects']), (True, []), checked)
        self.assertEqual(checked['manifest'], json.loads((package / 'manifest.json').read_text(encoding='utf-8')))
        self.assertEqual(checked.get('manifest_sha256'), result['manifest_sha256'])
        self.assertEqual(checked.get('drivers'), {'samp1': json.loads(model.read_text(encoding='utf-8'))})
        tool = call_tool('emit_check', {'run_dir': str(self.root / 'check'), 'package_dir': str(package),
                                        'expected_manifest_sha256': result['manifest_sha256']})
        self.assertEqual((tool['ok'], tool['integrity_ok'], tool['replay_status'], tool['_exit']),
                         (True, True, 'passed', 0), tool)
        opened = self.controller().call('device_open', {'package_dir': str(package), 'binding': UNOPENED})
        self.assertEqual((opened['ok'], opened['manifest_sha256']), (True, result['manifest_sha256']), opened)

    def test_03_B2_emit_package_refuses_a_name_or_a_version_the_check_refuses_and_writes_nothing(self):
        """What `emit_package` writes, the check accepts. A name or a version the check refuses in a manifest is refused
        when the package is asked for, by the argument's name, and no folder is made. At the check's bounds the package
        is written and accepted. An empty name is no name, so the package has its driver's."""
        from generative_driver.toolkit import call_tool
        from interface_runtime.package import verify_package
        _, model, probe = emitted(self.root / 'source')

        def emit(label, **arguments):
            run = self.root / label
            return run, call_tool('emit_package', {'run_dir': str(run), 'model_dir': str(model.parent),
                                                   'probe': str(probe), **arguments})
        for label, argument, value in (('an empty version', 'version', ''),
                                       ('a version of 65 characters', 'version', 'v' * 65),
                                       ('a version that is a number', 'version', 2),
                                       ('a name of 257 characters', 'name', 'n' * 257),
                                       ('a name that is a number', 'name', 7)):
            with self.subTest(label):
                run, result = emit(label, **{argument: value})
                if result.get('ok'):
                    checked = verify_package(result['package_dir'])
                    self.assertTrue(checked['ok'],
                                    'emit_package wrote a package the check refuses: ' + defects(checked))
                self.assertEqual((result.get('ok'), result['_exit'], [d['where'] for d in result['defects']]),
                                 (False, 1, [argument]), result)
                self.assertIn(argument + ' must be a text of ', defects(result))
                self.assertEqual(list(run.rglob('interface-package-*')), [])
        for label, arguments, name in (
                ('the longest name and version', {'name': 'n' * 256, 'version': 'v' * 64}, 'n' * 256),
                ('an empty name', {'name': ''}, 'samp1')):
            with self.subTest(label):
                _, result = emit(label, **arguments)
                self.assertTrue(result.get('ok'), result)
                checked = verify_package(result['package_dir'])
                self.assertEqual((checked['ok'], checked['manifest']['name']), (True, name), checked)

    def test_03_B2_a_package_of_an_earlier_format_is_refused_naming_the_format_found_and_the_one_read(self):
        for schema in ('interface-package/1', 'interface-package/2'):
            with self.subTest(schema, layout='of today'):
                package = remanifest(self.package(schema[-1] + '-today'),
                                     lambda manifest: manifest.update(schema=schema))
                self.assertRefused(package, "package format is not one this kit knows: '" + schema + "'",
                                   'it reads interface-package/3')
            with self.subTest(schema, layout='earlier'):
                package = earlier_layout(self.root / (schema[-1] + '-earlier'), schema)
                self.assertRefused(package, "package format is not one this kit knows: '" + schema + "'",
                                   'it reads interface-package/3')

    def test_03_B2_a_manifest_that_names_no_format_is_refused(self):
        package = remanifest(self.package(), lambda manifest: manifest.pop('schema'))
        self.assertRefused(package, 'package names no format', 'interface-package/3')

    def test_03_B2_a_changed_a_missing_and_an_unlisted_file_are_each_named(self):
        changed = self.package('changed')
        with (changed / 'README.md').open('a', encoding='utf-8') as stream:
            stream.write('A line added after the package was made.\n')
        self.assertRefused(changed, 'changed: README.md')
        missing = self.package('missing')
        (missing / 'evidence/samp1.probe.json').unlink()
        self.assertRefused(missing, 'missing: evidence/samp1.probe.json')
        unlisted = self.package('unlisted')
        (unlisted / 'requirements.txt').write_text('pyserial\n', encoding='utf-8')
        self.assertRefused(unlisted, 'unexpected: requirements.txt')
        # A file the manifest lists beside the four of a package is refused as well.
        listed = remanifest(unlisted)
        self.assertRefused(listed, 'requirements.txt')
        folder = self.package('folder')
        (folder / 'driver').mkdir()
        self.assertRefused(folder, 'unexpected: driver/')

    def test_03_B2_a_link_in_place_of_a_file_or_a_folder_is_refused(self):
        from test_shared_helpers import link
        file = self.package('file')
        kept = self.root / 'README.md'
        (file / 'README.md').replace(kept)
        link(self, file / 'README.md', kept)
        self.assertRefused(file, 'package may not contain links: README.md')
        folder = self.package('folder')
        moved = self.root / 'drivers'
        (folder / 'drivers').replace(moved)
        link(self, folder / 'drivers', moved)
        self.assertRefused(folder, 'package may not contain links: drivers')
        manifest = self.package('manifest')
        other = self.root / 'manifest.json'
        (manifest / 'manifest.json').replace(other)
        link(self, manifest / 'manifest.json', other)
        self.assertRefused(manifest, 'package may not contain links: manifest.json')

    def test_03_B2_a_driver_that_is_no_valid_model_is_refused_with_the_reason_of_the_driver_format(self):
        earlier = rewrite(self.package('earlier'), 'drivers/samp1.json',
                          lambda model: model.update(schema='interface-model/6'))
        self.assertRefused(earlier, "model format is not one this kit knows: 'interface-model/6'; "
                                    'it reads interface-model/7')
        invalid = rewrite(self.package('invalid'), 'drivers/samp1.json',
                          lambda model: model['operations']['measure'].update(effect='observe'))
        self.assertRefused(invalid, 'drivers/samp1.json is not a valid interface model', 'operations.measure')

    def test_03_B2_a_manifest_that_lists_no_driver_or_several_is_refused(self):
        none = remanifest(self.package('none'), lambda manifest: manifest.update(drivers=[]))
        self.assertRefused(none, 'package manifest lists no driver')
        several = remanifest(self.package('several'),
                             lambda manifest: manifest.update(drivers=manifest['drivers'] * 2))
        self.assertRefused(several, 'package manifest lists 2 drivers',
                           'a package of several drivers is not built yet')

    def test_03_B2_a_driver_entry_put_into_the_folder_makes_the_package_refused_and_is_never_run(self):
        marker = self.root / 'ran.txt'
        code = 'from pathlib import Path\nPath(' + repr(str(marker)) + ").write_text('ran')\n"
        for label, listed in (('unlisted', False), ('listed', True)):
            with self.subTest(label):
                package = self.package(label)
                (package / 'driver').mkdir()
                (package / 'driver/__init__.py').write_text(code, encoding='utf-8')
                (package / 'driver/__main__.py').write_text(code, encoding='utf-8')
                if listed:
                    remanifest(package)
                self.assertRefused(package, 'driver/__main__.py')
                self.assertFalse(marker.exists())


def check(package, root, **arguments):
    from generative_driver.toolkit import call_tool
    return call_tool('emit_check', {'run_dir': str(root / 'check'), 'package_dir': str(package), **arguments})


class EmitCheckTests(PackageCheckCase):
    def test_03_B3_emit_check_passes_a_package_that_says_what_the_tool_renders_and_whose_record_replays(self):
        checked = check(self.package(), self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok'], checked['replay_status'], checked['_exit']),
                         (True, True, 'passed', 0), checked)
        self.assertEqual((checked['evidence_kind'], checked['source_evidence_kind']), ('replay', 'replay'))
        self.assertNotIn('runtime_current', checked)
        self.assertNotIn('replay_reason', checked)

    def test_03_B3_a_readme_or_an_mcp_file_that_was_edited_is_refused_and_named_though_its_hash_matches(self):
        readme = self.package('readme')
        with (readme / 'README.md').open('a', encoding='utf-8') as stream:
            stream.write('\nThis driver was verified on hardware.\n')
        tools = rewrite(self.package('tools'), 'mcp.json',
                        lambda document: document['tools'][0].update(description='Run anything the caller asks.'))
        same = self.package('same')
        document = json.loads((same / 'mcp.json').read_text(encoding='utf-8'))
        (same / 'mcp.json').write_text(json.dumps(document), encoding='utf-8')
        for package, named in ((remanifest(readme), 'README.md'), (tools, 'mcp.json'), (remanifest(same), 'mcp.json')):
            with self.subTest(named, package=package.parent.parent.parent.name):
                from interface_runtime.package import verify_package
                self.assertTrue(verify_package(package)['ok'])
                checked = check(package, self.root)
                self.assertEqual((checked['ok'], checked['integrity_ok'], checked['_exit']), (False, False, 1), checked)
                self.assertEqual([defect['where'] for defect in checked['defects']], [named], checked)
                self.assertIn('differs from what the tool renders', defects(checked))

    def test_03_B3_a_probe_record_that_does_not_replay_fails_the_check(self):
        package = rewrite(self.package(), 'evidence/samp1.probe.json',
                          lambda probe: probe['results'][0]['outputs'].update(temperature=99.0))
        checked = check(package, self.root)
        self.assertEqual((checked['ok'], checked['integrity_ok'], checked['replay_status'], checked['_exit']),
                         (False, True, 'failed', 1), checked)
        self.assertIn('replayed outputs/transcripts differ from recorded probe', defects(checked))

    def test_03_B3_with_a_model_dir_a_package_whose_driver_is_not_that_models_bytes_is_refused(self):
        result, model, _ = emitted(self.root)
        package = Path(result['package_dir'])
        same = check(package, self.root, model_dir=str(model.parent))
        self.assertEqual((same['ok'], same['replay_status']), (True, 'passed'), same)
        other = self.root / 'other'
        other.mkdir()
        (other / 'model.json').write_text(json.dumps(samp1()), encoding='utf-8')
        refused = check(package, self.root, model_dir=str(other))
        self.assertEqual((refused['ok'], refused['integrity_ok'], refused['replay_status']), (False, False, 'not_run'),
                         refused)
        self.assertIn('package belongs to different model bytes', defects(refused))
        missing = check(package, self.root, model_dir=str(self.root / 'no-model'))
        self.assertEqual((missing['ok'], [defect['where'] for defect in missing['defects']]), (False, ['model_dir']))


class EmitGateTests(PackageCheckCase):
    """The emit stage hands on a package that `emit_check` passes, and no other."""

    def generic_gate(self, source):
        """The configurator's check `package_integrity_and_replay` of a generic run whose emit worker hands in a copy
        of the package `source`, with the artifacts the stage handed on."""
        import shutil
        from generative_driver.configurator import Controller, digest
        from test_configurator import scripted_stages, gateway_identity, wait_stopped
        controller = Controller(self.root / ('home-' + source.parent.parent.parent.name))

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
        [handoff] = [h for h in result['accepted_handoffs'] if h['stage'] == 'emit']
        [gate] = [c for c in handoff['notes']['inline']['configurator']['checks']
                  if c['name'] == 'package_integrity_and_replay']
        return gate, handoff['artifacts']

    def test_03_B3_the_emit_stage_of_a_generic_run_accepts_a_package_on_ok_integrity_and_a_passed_replay(self):
        gate, artifacts = self.generic_gate(self.package())
        self.assertEqual((gate['passed'], gate.get('rejected')), (True, None), gate)
        [handed] = artifacts
        self.assertEqual(files_of(handed['path']), FILES)

    def test_03_B3_the_emit_stage_of_a_generic_run_rejects_a_package_whose_readme_was_edited(self):
        package = self.package()
        with (package / 'README.md').open('a', encoding='utf-8') as stream:
            stream.write('\nThis driver was verified on hardware.\n')
        gate, artifacts = self.generic_gate(remanifest(package))
        self.assertEqual((gate['passed'], artifacts), (False, []), gate)
        self.assertEqual(gate['rejected'], [{'package': 'package', 'reason': 'package integrity failed'}])

    def supplied_gate(self, source, model):
        """The check `package_integrity_and_replay` of the emit stage of a run on a supplied device whose worker hands
        in a copy of the package `source`, with the artifacts the stage handed on; `model` is the accepted model."""
        import shutil
        from generative_driver.configurator import digest
        from test_supplied_stage_files import observe_stage, prepare_stage
        root = self.root / ('supplied-' + source.parent.parent.parent.name)
        accepted = root / 'accepted'
        accepted.mkdir(parents=True)
        kept, probe = accepted / '0-model.json', accepted / '1-interface-1.json'
        shutil.copy2(model, kept)
        shutil.copy2(source / 'evidence/samp1.probe.json', probe)
        handoffs = [{'stage': 'probe', 'artifacts': [{'path': str(path), 'sha256': sha256(path), 'kind': 'file'}
                                                     for path in (kept, probe)]}]
        workspace = root / 'worker'
        prepare_stage('emit', root / 'run', workspace, accepted=handoffs)
        package = shutil.copytree(source, workspace / 'package')
        checked = observe_stage('emit', root / 'run', workspace, {'status': 'completed'}, accepted=handoffs,
                                options={'emit_builds': {str(package.resolve()): digest(package)}})
        [gate] = checked['checks']
        self.assertEqual(gate['name'], 'package_integrity_and_replay')
        return gate, checked['artifacts']

    def test_03_B3_the_emit_stage_of_a_run_on_a_supplied_device_accepts_on_ok_integrity_and_a_passed_replay(self):
        result, model, _ = emitted(self.root / 'emitted')
        gate, artifacts = self.supplied_gate(Path(result['package_dir']), model)
        self.assertEqual((gate['passed'], gate.get('rejected')), (True, None), gate)
        self.assertEqual([Path(artifact).name for artifact in artifacts], ['package'])

    def test_03_B3_the_emit_stage_of_a_run_on_a_supplied_device_rejects_a_package_whose_tools_were_edited(self):
        result, model, _ = emitted(self.root / 'emitted')
        package = rewrite(Path(result['package_dir']), 'mcp.json',
                          lambda document: document['tools'][0].update(description='Run anything the caller asks.'))
        gate, artifacts = self.supplied_gate(package, model)
        self.assertEqual((gate['passed'], artifacts), (False, []), gate)
        self.assertEqual(gate['rejected'], [{'package': 'package', 'reason': 'package integrity failed'}])


SECTIONS = ['## Operations', '## Serve', '## Tested', '## What the evidence does not show']
# Files a package of an earlier format held, and the command line it carried.
NOT_HELD = ('python -m driver', 'REFERENCE.md', 'requirements.txt', 'manifest.sha256', 'mcp-tools.json',
            'contract.json', 'protocol.json', 'model.json', '__main__')


class ReadmeTests(PackageDataCase):
    def stand_in_readme(self):
        """`README.md` of the package of the smoke's stand-in model, with the record of `identify` run once on the
        stand-in device."""
        from generative_driver import stand_in_device
        from generative_driver.toolkit import call_tool, resources_root
        source = self.root / 'model'
        source.mkdir()
        (source / 'model.json').write_bytes((resources_root() / 'smoke/model.json').read_bytes())
        run = {'run_dir': str(self.root / 'run'), 'model_dir': str(source)}
        with stand_in_device.serving() as binding:
            probe = call_tool('probe_run', {**run, 'operation': 'identify', 'n': 1, 'binding': binding})
        self.assertTrue(probe.get('ok'), probe)
        result = call_tool('emit_package', {**run, 'probe': probe['probe']})
        self.assertTrue(result.get('ok'), result)
        return (Path(result['package_dir']) / 'README.md').read_text(encoding='utf-8')

    def test_03_B15_the_readme_of_the_stand_in_package_holds_what_a_package_documents_in_order(self):
        text = self.stand_in_readme()
        lines = text.splitlines()
        self.assertEqual(lines[0], '# stand-in device')
        self.assertEqual([line for line in lines if line.startswith('#')][1:], SECTIONS)
        [measure] = [line for line in lines if line.startswith('| measure | read |')]
        self.assertIn('| Read the temperature the stand-in device reports. |', measure)
        self.assertIn('`temperature`: The temperature the stand-in device reports.<br>'
                      'Unit: not stated by the device.<br>'
                      "Warning: The stand-in device's reply T:21.5 states no unit.", measure)
        self.assertIn('generative-driver serve PACKAGE --binding '
                      """'{"host": "<host name or address>", "port": <int>}'""", lines)
        self.assertIn('| identify | tested | 1 / 1 / 0 | live |', text)
        self.assertIn('| measure | untested | 0 / 0 / 0 | none |', text)
        self.assertLess(lines.index('## Serve'), lines.index("""generative-driver serve PACKAGE --binding """
                                                             """'{"host": "<host name or address>", "port": <int>}'"""))
        self.assertIn('Every operation is a read, and a read needs no flag.', text)
        self.assertIn('needs no optional install', text)
        self.assertNotIn('generative-driver[hardware]', text)
        for name in NOT_HELD:
            self.assertNotIn(name, text)

    def test_03_B15_the_readme_names_the_flag_each_effect_needs_and_the_install_the_connection_needs(self):
        import copy
        model = example_model()
        model['channel']['resets_on_open'] = True
        for effect in ('write', 'actuate'):
            model['operations'][effect + '_level'] = {**copy.deepcopy(model['operations']['measure']), 'effect': effect}
            model['provenance'].append({'item': 'operations.' + effect + '_level', 'source': 'invented'})
        from generative_driver.toolkit import call_tool
        source = self.root / 'model'
        source.mkdir()
        (source / 'model.json').write_text(json.dumps(model), encoding='utf-8')
        (self.root / 'replay.json').write_text(json.dumps(example_replay()), encoding='utf-8')
        run = {'run_dir': str(self.root / 'run'), 'model_dir': str(source)}
        probe = call_tool('probe_run', {**run, 'operation': 'measure', 'n': 1, 'allow_effects': ['reset'],
                                        'replay': str(self.root / 'replay.json')})
        self.assertTrue(probe.get('ok'), probe)
        result = call_tool('emit_package', {**run, 'probe': probe['probe']})
        self.assertTrue(result.get('ok'), result)
        text = (Path(result['package_dir']) / 'README.md').read_text(encoding='utf-8')
        lines = text.splitlines()
        self.assertIn("""generative-driver serve PACKAGE --binding '{"port": "<serial device path>"}'""", lines)
        self.assertIn('- `--allow-effect write` for write_level', lines)
        self.assertIn('- `--allow-effect actuate` for actuate_level', lines)
        self.assertIn('- `--allow-effect reset` for every operation, because this channel resets the device when it '
                      'opens', lines)
        self.assertIn('generative-driver[hardware]', text)
        self.assertIn('| measure | tested | 1 / 1 / 0 | replay |', text)
        self.assertIn('| write_level | untested | 0 / 0 / 0 | none |', text)

    def test_03_B15_the_readme_says_what_the_evidence_does_not_show(self):
        result, _, _ = emitted(self.root)
        text = (Path(result['package_dir']) / 'README.md').read_text(encoding='utf-8')
        after = text.split('## What the evidence does not show\n', 1)[1]
        self.assertIn('Replay shows that the driver is consistent with the recorded exchanges, not that it is '
                      'physically correct.', after)
        self.assertIn('No accuracy and no full coverage of the device is implied.', after)
        for name in NOT_HELD:
            self.assertNotIn(name, text)


if __name__ == '__main__':
    unittest.main()

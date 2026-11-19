"""A generic run's interpret worker is taught to write the driver contract, and emit packages what of it matches the
model.

Runs here are scripted through the configurator's own stage preparation: no model, worker runtime or device runs."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from package_env import package_env
from test_package import example_model, example_replay

FIRMWARE = b'\xe9 scripted image, not firmware'
# The `TASKS.json` the stage package wrote for the hand-written mission of `tests/test_supplied_stages.py` at commit
# 6bbdcae, before contract authoring, the kit analysis and documents arrived: it says no word of any of them.
STAGE_PACKAGE_TASKS = {
    'schema': 'candidate-task-contract/1',
    'tasks': {
        'read_level': {'meaning': 'Read the fill level.', 'inputs': {}, 'outputs': {'level': {'unit': 'percent'}}},
        'read_status': {'meaning': 'Read the status code.', 'inputs': {}, 'outputs': {'status': {'unit': 'code'}}}},
    'effect_grants': [],
    'instructions': (
        'These are requested observable behaviors, not protocol facts or hidden test values. Recover command bytes, '
        'operation composition, bounds and decoders from the supplied evidence. One canonical task can require several '
        'exchange steps in a model operation. Effect labels describe actual behavior: acquisition and configuration '
        'changes are writes; actuation controls a physical output. Read is implicit; other effects require the listed '
        'grants. Do not relabel unsafe operations merely to bypass a grant. Report unsupported behavior. The '
        'operating_conditions are provided operating facts. The tasks remain requested observable behaviors, not '
        'protocol facts or hidden test values.'),
    'operating_conditions': ['The device answers one request at a time.']}


def thermometer():
    """An invented model whose temperature decoder states no unit, as a firmware reply `T:21.5` gives none."""
    model = example_model()
    del model['operations']['measure']['outputs']['temperature']['unit']
    return model


def cleanup_workspaces(test, workspaces):
    """Interpret workspaces live outside the run, as the configurator prepares them; remove them after the test."""
    for workspace in workspaces:
        test.addCleanup(shutil.rmtree, Path(workspace).parent, True)


def interpret_attempts(test, root, *, repair=False):
    """The interpret attempts of a generic run whose acquire hands on a firmware image, each as (workspace, its
    assignment); with `repair`, a probe difference sends the run back to interpret once. Later stages are skipped."""
    from generative_driver.configurator import Controller
    from test_configurator import scripted_stages, wait_stopped
    controller = Controller(root / 'home')

    def prepare(original, spec, stage, run_dir, workspace, accepted):
        if stage == 'interpret':
            return original(spec, stage, run_dir, workspace, accepted)
        if stage in ('acquire', 'probe'):
            return {'inputs': [], 'allowed_tools': []}
        return {'skip': 'Not part of this test'}

    def check(spec, stage, run_dir, workspace, report, accepted, assignment):
        if stage == 'acquire':
            image = workspace / 'image.bin'
            image.write_bytes(FIRMWARE)
            return {'ok': True, 'artifacts': [str(image)], 'checks': []}
        if stage == 'interpret':
            model = workspace / 'model'
            model.mkdir()
            (model / 'model.json').write_text(json.dumps(thermometer()), encoding='utf-8')
            return {'ok': True, 'artifacts': [str(model)], 'checks': []}
        out = {'ok': True, 'artifacts': [], 'checks': []}
        if repair and assignment['revision'] == 0:
            out['device_diff'] = [{'source': 'probe_diff', 'defects': [{'where': 'operations.measure',
                                                                         'what': 'scripted difference'}]}]
        return out
    try:
        with scripted_stages(controller, check=check, prepare=prepare):
            run = controller.call('start', {'goal': 'Read the temperature', 'executor_config': {'command': ['none']}})
            wait_stopped(controller, run)
        assigned = [event['data'] for event in controller.call('events', run)['events']
                    if event['kind'] == 'stage.assigned' and event['data']['stage'] == 'interpret']
    finally:
        controller.close()
    cleanup_workspaces(test, [assignment['workspace'] for assignment in assigned])
    return [(Path(assignment['workspace']), assignment) for assignment in assigned]


def sealed(workspace, assignment):
    return sorted(Path(path).relative_to(workspace).as_posix() for path in assignment['inputs'])


class InterpretWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_05_B6_a_generic_interpret_workspace_holds_the_contract_guide_and_tool_as_sealed_inputs(self):
        [(workspace, assignment)] = interpret_attempts(self, self.root)
        for name in ('CONTRACT.md', 'contract.py'):
            self.assertTrue((workspace / name).is_file(), name)
            self.assertIn(name, sealed(workspace, assignment))
            self.assertIn(name, json.loads((workspace / 'INPUT_HASHES.json').read_text(encoding='utf-8')))
        instructions = json.loads((workspace / 'TASKS.json').read_text(encoding='utf-8'))['instructions']
        self.assertIn('Write contract.json as CONTRACT.md describes', instructions)

    def test_05_B6_the_guide_teaches_how_to_write_the_contract(self):
        [(workspace, _)] = interpret_attempts(self, self.root)
        guide = ' '.join((workspace / 'CONTRACT.md').read_text(encoding='utf-8').split())
        for taught in ('all a fresh agent reads about the driver', 'python3 contract.py draft', 'placeholder',
                       'observable meaning', 'caveat', 'UCUM', '`Cel`', '`hPa`', '`%`', '`V`',
                       '`1` for a count or ratio',
                       "must equal its decoder's `unit` in `model.json`", 'projected into the model', '`null`',
                       '`unit_not_stated`', 'where you searched the evidence', 'Never guess a unit',
                       'python3 contract.py check'):
            self.assertIn(taught, guide)

    def test_05_B6_a_repair_keeps_contract_json_in_step_with_the_revised_model(self):
        first, (workspace, assignment) = interpret_attempts(self, self.root, repair=True)
        context = json.loads((workspace / 'REPAIR_CONTEXT.json').read_text(encoding='utf-8'))
        self.assertIn('Keep contract.json in step with the revised model.json', context['instructions'])
        for name in ('CONTRACT.md', 'contract.py'):
            self.assertIn(name, sealed(workspace, assignment))
            self.assertIn(name, context['sealed_inputs'])

    def test_05_B6_benchmark_interpret_workspaces_get_neither_file(self):
        # A run on a supplied device interprets on the stage package, so the test asks that package, with a
        # hand-written firmware and mission.
        from generative_driver.supplied.stages import prepare_stage
        from generative_driver.toolkit import resources_root
        from test_supplied_stages import MISSION, hand_written_firmware
        prepared = prepare_stage('interpret', self.root / 'run', self.root / 'worker',
                                 firmware=hand_written_firmware(self.root), mission=MISSION)
        cleanup_workspaces(self, [prepared['work_dir']])
        workspace = Path(prepared['work_dir'])
        for name in ('CONTRACT.md', 'contract.py', 'contract.json'):
            self.assertFalse((workspace / name).exists(), name)
            self.assertNotIn(name, prepared['prompt'])
        toolchain = resources_root() / 'toolchain'
        self.assertEqual((workspace / 'BRIEF.md').read_bytes(),
                         (toolchain / 'skills/interpret-interface/SKILL.md').read_bytes())
        self.assertEqual((workspace / 'validate.py').read_bytes(),
                         (toolchain / 'template/interface_validate.py').read_bytes())
        self.assertEqual(json.loads((workspace / 'TASKS.json').read_text(encoding='utf-8')), STAGE_PACKAGE_TASKS)


def contract_tool(workspace, *arguments):
    """Exit status and JSON report of `contract.py` run in `workspace` by a Python that sees nothing else: isolated,
    with no site packages, so the kit is not importable."""
    done = subprocess.run([sys.executable, '-I', '-S', 'contract.py', *arguments], cwd=workspace, env=package_env(),
                          capture_output=True, text=True, timeout=60)
    try:
        return done.returncode, json.loads(done.stdout)
    except ValueError:
        raise AssertionError('contract.py printed no JSON report: ' + done.stdout + done.stderr) from None


class ContractToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        [(self.workspace, _)] = interpret_attempts(self, self.root)

    def write(self, name, document):
        (self.workspace / name).write_text(json.dumps(document, indent=1), encoding='utf-8')

    def contract(self):
        return json.loads((self.workspace / 'contract.json').read_text(encoding='utf-8'))

    def test_05_B7_draft_writes_the_contract_the_kit_imports_from_the_model(self):
        from test_driver_package import emit
        self.write('model.json', example_model())
        code, report = contract_tool(self.workspace, 'draft')
        self.assertEqual(code, 0, report)
        drafted = self.contract()
        measure = drafted['operations']['measure']
        self.assertEqual(measure['description'], 'Execute public operation measure.')
        self.assertEqual([caveat['code'] for caveat in measure['uncertainties']], ['semantics_unknown'])
        self.assertEqual(measure['outputs']['temperature']['unit'], 'degC')
        # What emit packages today for a model folder that holds no contract.
        package = emit(self.root / 'emitted', example_model())
        self.assertEqual(drafted, json.loads((package / 'driver/contract.json').read_text(encoding='utf-8')))

    def test_05_B7_draft_never_overwrites_contract_json(self):
        self.write('model.json', example_model())
        self.write('contract.json', {'written': 'by the worker'})
        code, report = contract_tool(self.workspace, 'draft')
        self.assertNotEqual(code, 0, report)
        self.assertEqual(self.contract(), {'written': 'by the worker'})

    def test_05_B7_check_names_each_operation_that_does_not_match_and_its_first_difference(self):
        self.write('model.json', example_model())
        self.assertEqual(contract_tool(self.workspace, 'draft')[0], 0)
        contract = self.contract()
        contract['operations']['measure']['outputs']['temperature']['unit'] = 'Cel'
        contract['operations']['identify']['preconditions'] = [{
            'id': 'ready', 'description': 'The device is ready.', 'operator': 'eq', 'expected': 1,
            'observation': {'operation': 'measure', 'parameters': {}, 'output': 'ready'}}]
        self.write('contract.json', contract)
        code, report = contract_tool(self.workspace, 'check')
        self.assertEqual(code, 1, report)
        self.assertIs(report['describes_model'], False)
        mismatches = {entry['where']: entry['difference'] for entry in report['mismatches']}
        self.assertEqual(set(mismatches), {'operations.measure', 'operations.identify.preconditions'}, report)
        self.assertEqual(mismatches['operations.measure'], "operations.measure.outputs.temperature.unit: the "
                                                           "contract gives \"Cel\" where model.json has \"degC\"")
        self.assertIn('Preconditions require a declared unguarded read observation',
                      mismatches['operations.identify.preconditions'])

    def test_05_B7_check_advises_on_null_units_and_draft_descriptions_without_failing(self):
        self.write('model.json', thermometer())
        self.assertEqual(contract_tool(self.workspace, 'draft')[0], 0)
        code, report = contract_tool(self.workspace, 'check')
        self.assertEqual(code, 0, report)
        self.assertEqual((report['describes_model'], report['mismatches']), (True, []))
        self.assertEqual(sorted(report['advice']), sorted([
            'operations.identify.description is still the draft placeholder',
            'operations.measure.description is still the draft placeholder',
            'operations.measure.outputs.temperature.description is still the draft placeholder',
            'operations.measure.outputs.temperature.unit is null with no unit_not_stated uncertainty: write the unit '
            'the evidence states, or add one that says where you searched']))

    def test_05_B7_check_passes_a_completed_contract_with_no_advice(self):
        self.write('model.json', thermometer())
        self.assertEqual(contract_tool(self.workspace, 'draft')[0], 0)
        contract = self.contract()
        contract['operations']['identify']['description'] = 'Read the device identity line, DEMO-42.'
        measure = contract['operations']['measure']
        measure['description'] = 'Read the temperature the device reports.'
        measure['outputs']['temperature'].update(description='The temperature reading.', uncertainties=[{
            'code': 'unit_not_stated', 'message': 'The reply T:21.5 and its handler give no unit.',
            'evidence_refs': ['image.bin strings']}])
        self.write('contract.json', contract)
        code, report = contract_tool(self.workspace, 'check')
        self.assertEqual((code, report['describes_model'], report['mismatches'], report['advice']), (0, True, [], []))

    def test_05_B7_neither_command_needs_anything_outside_the_workspace(self):
        moved = self.root / 'moved workspace'
        moved.mkdir()
        for name in ('contract.py', '_toolkit'):
            source = self.workspace / name
            (shutil.copytree if source.is_dir() else shutil.copyfile)(source, moved / name)
        (moved / 'model.json').write_text(json.dumps(example_model()), encoding='utf-8')
        self.assertEqual(contract_tool(moved, 'draft')[0], 0)
        self.assertEqual(contract_tool(moved, 'check')[0], 0)


def emitted(root, model, contract=None):
    """A generic run, scripted up to emit: interpret hands on a model folder holding `model` and, unless None, the
    worker's `contract` (a dict written as JSON, or text written as it is); probe hands on replayed evidence of
    `measure` bound to that model; the configurator prepares emit, whose worker builds the package with emit_package
    through the gateway. Returns (the package's contract, the emit stage's notes)."""
    from generative_driver.configurator import Controller, digest
    from generative_driver.toolkit import call_tool
    from test_configurator import scripted_stages, gateway_identity, task_of, wait_stopped
    source = root / 'source'
    source.mkdir()
    (source / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    (root / 'replay.json').write_text(json.dumps(example_replay()), encoding='utf-8')
    probe = call_tool('probe_run', {'run_dir': str(root / 'probe-run'), 'model_dir': str(source),
                                    'operation': 'measure', 'n': 1, 'replay': str(root / 'replay.json')})
    assert probe['ok'], probe
    seen = {}
    controller = Controller(root / 'home')

    def prepare(original, spec, stage, run_dir, workspace, accepted):
        if stage in ('probe', 'emit'):
            return original(spec, stage, run_dir, workspace, accepted)
        return {'inputs': [], 'allowed_tools': []} if stage == 'interpret' else {'skip': 'Not part of this test'}

    def check(spec, stage, run_dir, workspace, report, accepted, assignment):
        if stage == 'interpret':
            folder = workspace / 'model'
            folder.mkdir()
            shutil.copyfile(source / 'model.json', folder / 'model.json')
            if contract is not None:
                (folder / 'contract.json').write_text(contract if isinstance(contract, str) else json.dumps(contract),
                                                      encoding='utf-8')
            return {'ok': True, 'artifacts': [str(folder)], 'checks': []}
        if stage == 'probe':
            record = workspace / 'interface-1.json'
            shutil.copyfile(probe['probe'], record)
            bound = [{'name': record.name, 'sha256': digest(record), 'model_sha256': sha}
                     for sha in assignment['inputs'].values()]
            return {'ok': True, 'artifacts': [str(record)], 'checks': [], 'notes': {'records': bound}}
        return {'ok': True, 'artifacts': [], 'checks': []}

    def work(request):
        if request.stage == 'emit':
            inputs = [Path(path) for path in task_of(request.prompt)['inputs']]
            folder = next(path for path in inputs if (path / 'model.json').is_file())
            record = next(path for path in inputs if path.suffix == '.json')
            seen['built'] = controller.call('tool', {**gateway_identity(request), 'name': 'emit_package',
                                                     'arguments': {'model_dir': str(folder), 'probe': str(record)}})
    try:
        with scripted_stages(controller, check=check, prepare=prepare, work=work):
            run = controller.call('start', {'goal': 'Read the temperature', 'executor_config': {'command': ['none']}})
            result = wait_stopped(controller, run)
    finally:
        controller.close()
    assert seen.get('built', {}).get('ok'), seen
    [handoff] = [h for h in result['accepted_handoffs'] if h['stage'] == 'emit']
    package = Path(seen['built']['package_dir'])
    return json.loads((package / 'driver/contract.json').read_text(encoding='utf-8')), handoff['notes']['inline']


def drafted(model):
    from interface_runtime.contracts import from_model
    return from_model(model)[0]


def guarded():
    """An invented model with a read whose output a write's precondition can observe."""
    from test_driver_contract import controlled_model
    return controlled_model()


class EmitContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def contract_notes(self, notes):
        return notes['configurator'].get('driver_contract', [])

    def test_05_B8_a_contract_that_matches_entirely_is_packaged_as_the_same_value(self):
        contract = drafted(thermometer())
        contract['operations']['measure']['description'] = 'Read the temperature the device reports.'
        contract['operations']['measure']['outputs']['temperature']['uncertainties'] = [{
            'code': 'unit_not_stated', 'message': 'The reply T:21.5 and its handler give no unit.',
            'evidence_refs': ['image.bin strings']}]
        packaged, notes = emitted(self.root, thermometer(), contract)
        self.assertEqual(packaged, contract)
        self.assertEqual(self.contract_notes(notes), [])

    def test_05_B8_an_operation_that_does_not_match_gets_the_imported_entry_and_the_rest_keep_theirs(self):
        contract = drafted(thermometer())
        contract['operations']['identify']['description'] = 'Read the identity line, DEMO-42.'
        contract['operations']['identify']['uncertainties'] = []
        contract['operations']['measure']['description'] = 'Read the temperature in degrees Celsius.'
        contract['operations']['measure']['outputs']['temperature']['unit'] = 'Cel'
        packaged, notes = emitted(self.root, thermometer(), contract)
        self.assertEqual(packaged['operations']['identify'], contract['operations']['identify'])
        self.assertEqual(packaged['operations']['measure'], drafted(thermometer())['operations']['measure'])
        [note] = self.contract_notes(notes)
        self.assertIn('measure', note)
        self.assertIn('operations.measure.outputs.temperature.unit: the contract gives "Cel" where model.json has '
                      'nothing', note)

    def test_05_B8_an_operation_the_contract_leaves_out_gets_the_imported_entry(self):
        contract = drafted(thermometer())
        contract['operations']['identify']['description'] = 'Read the identity line, DEMO-42.'
        del contract['operations']['measure']
        packaged, notes = emitted(self.root, thermometer(), contract)
        self.assertEqual(packaged['operations']['identify']['description'], 'Read the identity line, DEMO-42.')
        self.assertEqual(packaged['operations']['measure'], drafted(thermometer())['operations']['measure'])
        [note] = self.contract_notes(notes)
        self.assertIn('measure', note)
        self.assertIn('contract.json leaves it out', note)

    def test_05_B8_an_operation_whose_precondition_no_longer_holds_gets_the_imported_entry_too(self):
        contract = drafted(guarded())
        armed = contract['operations']['read_armed']
        armed['description'] = 'Read whether the device is armed.'
        armed['outputs']['armed']['schema'] = {'type': 'string', 'enum': ['armed', 'safe']}
        level = contract['operations']['set_level']
        level['description'] = 'Set the output level.'
        level['preconditions'] = [{'id': 'armed', 'description': 'The device is armed.', 'operator': 'eq',
                                   'expected': 'armed',
                                   'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'}}]
        contract['operations']['measure']['description'] = 'Read the temperature.'
        packaged, notes = emitted(self.root, guarded(), contract)
        imported = drafted(guarded())['operations']
        self.assertEqual(packaged['operations']['read_armed'], imported['read_armed'])
        self.assertEqual(packaged['operations']['set_level'], imported['set_level'])
        self.assertEqual(packaged['operations']['measure']['description'], 'Read the temperature.')
        why = {name: next(note for note in self.contract_notes(notes) if 'operation ' + name + ' ' in note)
               for name in ('read_armed', 'set_level')}
        self.assertEqual(len(self.contract_notes(notes)), 2, notes)
        self.assertIn('Public output type is incompatible with its decoder: read_armed.armed', why['read_armed'])
        self.assertIn('precondition', why['set_level'])

    def test_05_B8_an_entry_that_keeps_its_description_keeps_its_preconditions(self):
        contract = drafted(guarded())
        contract['operations']['read_armed']['description'] = 'Read whether the device is armed: 1 when armed.'
        level = contract['operations']['set_level']
        level['description'] = 'Set the output level, 0 to 100.'
        level['preconditions'] = [{'id': 'armed', 'description': 'The device is armed.', 'operator': 'eq',
                                   'expected': 1,
                                   'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'}}]
        packaged, notes = emitted(self.root, guarded(), contract)
        self.assertEqual(packaged['operations']['set_level'], level)
        self.assertEqual(self.contract_notes(notes), [])

    def test_05_B8_device_identity_and_provenance_always_come_from_the_model(self):
        contract = drafted(thermometer())
        contract['device'] = {'name': 'renamed by the worker'}
        contract['provenance'] = [{'item': 'operations.measure', 'source': 'the worker'}]
        contract['operations']['measure']['description'] = 'Read the temperature.'
        packaged, notes = emitted(self.root, thermometer(), contract)
        model = thermometer()
        self.assertEqual((packaged['device'], packaged['identity'], packaged['provenance']),
                         (model['device'], model['identity'], model['provenance']))
        self.assertEqual(packaged['operations']['measure']['description'], 'Read the temperature.')

    def test_05_B8_a_contract_json_that_cannot_be_read_gives_every_operation_the_imported_entry(self):
        packaged, notes = emitted(self.root, thermometer(), '{"operations": not a contract')
        self.assertEqual(packaged, drafted(thermometer()))
        [note] = self.contract_notes(notes)
        self.assertIn('contract.json cannot be read as a contract', note)

    def test_05_B8_the_notes_say_when_the_model_came_with_no_contract_json(self):
        packaged, notes = emitted(self.root, thermometer())
        self.assertEqual(packaged, drafted(thermometer()))
        [note] = self.contract_notes(notes)
        self.assertIn('came with no contract.json', note)

    def test_05_B8_emit_package_keeps_refusing_an_authored_pair_that_does_not_compile_to_the_model(self):
        from generative_driver.toolkit import call_tool
        from interface_runtime.contracts import from_model
        from test_driver_package import emit
        package = emit(self.root / 'emitted', thermometer())
        contract, protocol = from_model(thermometer())
        contract['operations']['measure']['outputs']['temperature']['unit'] = 'Cel'
        folder = self.root / 'emitted' / 'model'
        (folder / 'contract.json').write_text(json.dumps(contract), encoding='utf-8')
        (folder / 'protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
        refused = call_tool('emit_package', {'run_dir': str(self.root / 'again'), 'model_dir': str(folder),
                                             'probe': str(package / 'probe.json')})
        self.assertEqual((refused.get('ok'), refused['_exit']), (None, 1), refused)
        self.assertIn('Authored contract/protocol differ from the probed model projection', refused['error'])


if __name__ == '__main__':
    unittest.main()

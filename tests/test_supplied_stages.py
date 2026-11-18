"""The tool's stage package: the six stages of a run on a supplied device, from a firmware and a mission given as data.

Feature 05 of the untangle Run (docs/features/untangle/05-supplied-device-stages.md): the first point of 05-B1, 05-B2,
05-B5, the first five points of 05-B4 and the first two of 05-B6. This module imports nothing from the benchmark
package and uses no file under `resources/bench`, so it also runs where the benchmark is gone. The benchmark's side of
the Feature is in `firmware-bench/tests/test_benchmark_supplied_stages.py`.

Temporary directories only; no model, no hardware and no device.
"""
import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.integrity import digest
from package_env import package_env
from test_shared_helpers import benchmark_imports, tool_modules

PACKAGE = 'generative_driver.supplied'
STAGE_FUNCTIONS = ('prepare_stage', 'observe_stage', 'package_call', 'record_call')
# The SHA-256 of the bytes `supplied firmware`.
FIRMWARE_SHA256 = '43b4574c479a13842705bb2799c05a94d6055996e56f6fe59e0595059eb8e021'
# The hand-written mission. It names no registered case.
MISSION = {
    'tasks': {
        'read_level': {'meaning': 'Read the fill level.', 'inputs': {}, 'outputs': {'level': {'unit': 'percent'}}},
        'read_status': {'meaning': 'Read the status code.', 'inputs': {}, 'outputs': {'status': {'unit': 'code'}}}},
    'reuse_objective': 'Read the level twice and report both readings.',
    'operating_conditions': ['The device answers one request at a time.']}
# A model the probe stage can wire to a TCP binding, with one operation for each task of the hand-written mission.
MODEL = {'channel': {'type': 'uart'},
         'operations': {'level': {'parameters': {}, 'outputs': {'level': {'unit': 'percent'}}},
                        'status': {'parameters': {}, 'outputs': {'status': {'unit': 'code'}}}}}
MAPPING = {'schema': 'capabilities/2', 'tasks': {
    'read_level': {'operation': 'level', 'constants': {}, 'inputs': {},
                   'outputs': {'level': {'output': 'level', 'unit': 'percent'}}},
    'read_status': {'operation': 'status', 'constants': {}, 'inputs': {},
                    'outputs': {'status': {'output': 'status', 'unit': 'code'}}}}}
BINDING = {'host': '127.0.0.1', 'port': 1}
# A catalog of development conditions, as a device descriptor carries one.
CATALOG = {'schema': 'development-conditions/1', 'conditions': [
    {'id': 'low', 'reference': {'value': -12.0, 'unit': 'degC'}},
    {'id': 'high', 'reference': {'value': 32.0, 'unit': 'degC'}}]}
# A mission whose one task states how it samples, and whose one output names the instrument reading it is held to.
SAMPLED_OUTPUT = {'temperature': {'unit': 'degC', 'instrument': 'reference_temperature', 'absolute_tolerance': 0.5}}
SAMPLED = {'tasks': {'measure': {'meaning': 'Take a new sample and return its temperature.', 'inputs': {},
                                 'outputs': SAMPLED_OUTPUT, 'sampling': 'acquire'}},
           'reuse_objective': 'Take two new samples and report both temperatures.'}
SAMPLE_MODEL = {'channel': {'type': 'tcp'}, 'operations': {'sample': {'parameters': {}, 'outputs': {
    'temperature': {'unit': 'degC'}, 'humidity': {'unit': 'percent'}}}}}
# The command line of a hand-written package: it prints the arguments it was started with as its result.
ECHO = ('import json, sys\n'
        "print(json.dumps({'ok': True, 'outputs': {'arguments': sys.argv[1:]}, 'units': {}, 'transcript': []}))\n")


def stage_module(test, name):
    """The module `name` of the stage package. `test` fails, saying which module the tool lacks, where there is
    none."""
    dotted = f'{PACKAGE}.{name}'
    try:
        found = importlib.util.find_spec(dotted)
    except ModuleNotFoundError:
        found = None
    test.assertIsNotNone(found, f'The tool has no module {dotted}')
    return importlib.import_module(dotted)


def stage_package_files(test):
    """The Python files of the stage package, sorted. `test` fails where the tool has no such package."""
    try:
        found = importlib.util.find_spec(PACKAGE)
    except ModuleNotFoundError:
        found = None
    test.assertIsNotNone(found, f'The tool has no package {PACKAGE}')
    return sorted(Path(found.origin).parent.rglob('*.py'))


def hand_written_firmware(root):
    """The hand-written firmware: a file under `root` that holds the bytes `supplied firmware`, with its SHA-256."""
    path = Path(root) / 'supplied' / 'firmware.bin'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'supplied firmware')
    return {'path': str(path), 'sha256': FIRMWARE_SHA256}


def write_state(run_dir, state):
    """Write `state` as the stage state file of the run at `run_dir`."""
    path = Path(run_dir) / 'supplied' / 'state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding='utf-8')


def read_state(run_dir):
    """What the stage state file of the run at `run_dir` holds."""
    return json.loads((Path(run_dir) / 'supplied' / 'state.json').read_text(encoding='utf-8'))


def hand_written_package(root, body):
    """A package at `root/package` whose command line is the Python `body`: the path of the package."""
    package = Path(root) / 'package'
    (package / 'driver').mkdir(parents=True)
    (package / 'driver' / '__init__.py').write_text('', encoding='utf-8')
    (package / 'driver' / '__main__.py').write_text(body, encoding='utf-8')
    return package


class TemporaryDirectoryCase(unittest.TestCase):
    def directory(self):
        """A new empty directory, removed when the test ends."""
        temporary = tempfile.TemporaryDirectory(prefix='supplied stages ')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name).resolve()


class PackageTests(unittest.TestCase):
    def test_05_B1_the_stage_package_and_its_named_modules_can_be_imported(self):
        for name in ('stages', 'instrument_evidence', 'capabilities', 'package_runner'):
            with self.subTest(module=name):
                stage_module(self, name)
        stages = stage_module(self, 'stages')
        for name in STAGE_FUNCTIONS:
            self.assertTrue(callable(getattr(stages, name, None)), f'stages has no function {name}')
        self.assertTrue(callable(getattr(stage_module(self, 'instrument_evidence'), 'discrepancies', None)))

    def test_05_B1_the_import_check_sees_a_benchmark_import_of_each_form_at_any_depth(self):
        directory = tempfile.TemporaryDirectory(prefix='supplied stages ')
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'module.py'
        for line in ('from ..benchmark_support.case_assets import write_json', 'from .. import benchmark',
                     'def prepare():\n    from ..benchmark_support import missions',
                     'import generative_driver.benchmark_support.execution',
                     'def score():\n    from generative_driver.benchmark import supplied_inputs'):
            path.write_text(line + '\n', encoding='utf-8')
            with self.subTest(line=line):
                self.assertTrue(benchmark_imports(path, PACKAGE))
        for line in ('from ..integrity import digest', 'from .capabilities import bind_task', 'import json',
                     'def call():\n    from ..configurator import _host_fault',
                     "NAME = 'generative_driver.benchmark_support.case_assets'"):
            path.write_text(line + '\n', encoding='utf-8')
            with self.subTest(line=line):
                self.assertEqual(benchmark_imports(path, PACKAGE), [])

    def test_05_B1_no_module_of_the_stage_package_imports_the_benchmark_package(self):
        files = stage_package_files(self)
        for name in ('__init__.py', 'stages.py', 'instrument_evidence.py', 'capabilities.py', 'package_runner.py'):
            self.assertIn(name, [path.name for path in files])
        found = [f'{path.name} imports {name or "the module"} from {module}'
                 for path in files for module, name in benchmark_imports(path, PACKAGE)]
        self.assertEqual(len(found), 0, '\n'.join(found))


class StageCase(TemporaryDirectoryCase):
    """The stage functions on the hand-written firmware and mission, in a run folder of their own."""

    def setUp(self):
        self.stages = stage_module(self, 'stages')
        self.root = self.directory()
        self.run = self.root / 'run'
        self.firmware = hand_written_firmware(self.root)

    def prepare(self, stage, accepted=None, options=None, *, firmware=None, mission=MISSION):
        """What preparing `stage` returns, in the workspace `self.root / stage`."""
        return self.stages.prepare_stage(stage, self.run, self.root / stage, accepted, options,
                                         firmware=firmware or self.firmware, mission=mission)

    def observe(self, stage, accepted=None, options=None, *, mission=MISSION):
        """What observing `stage` returns for a worker that reported it completed."""
        return self.stages.observe_stage(stage, self.run, self.root / stage, {'status': 'completed'}, accepted,
                                         options, firmware=self.firmware, mission=mission)

    def handoff(self, stage, files):
        """An accepted handoff of `stage` whose artifacts are `files`, each a name and its text, written as the
        configurator names accepted copies: `<index>-<name>`."""
        self.handoffs = getattr(self, 'handoffs', 0) + 1
        folder = self.root / 'accepted' / f'{self.handoffs}-{stage}'
        folder.mkdir(parents=True)
        artifacts = []
        for index, (name, text) in enumerate(files.items()):
            path = folder / f'{index}-{name}'
            path.write_text(text, encoding='utf-8')
            artifacts.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return {'stage': stage, 'artifacts': artifacts}

    def interpreted(self):
        """The accepted handoffs of an interpretation whose model the probe stage can wire."""
        return [self.handoff('interpret', {'model.json': json.dumps(MODEL)})]

    def probed(self, **files):
        """The accepted handoffs of a probe of that model, with `files` beside it."""
        return [self.handoff('probe', {'model.json': json.dumps({**MODEL, 'channel': {'type': 'tcp'}}), **files})]

    def tasks_file(self, prepared):
        """The `TASKS.json` among the inputs of a prepared stage, parsed."""
        path = next(Path(name) for name in prepared['inputs'] if Path(name).name == 'TASKS.json')
        return json.loads(path.read_text(encoding='utf-8'))


class StageInputTests(StageCase):
    def test_05_B2_the_stage_functions_take_the_firmware_and_the_mission_and_no_case_id(self):
        signatures = {name: str(inspect.signature(getattr(self.stages, name))) for name in STAGE_FUNCTIONS}
        self.assertEqual(signatures, {
            'prepare_stage': '(stage, run_dir, workspace, accepted=None, options=None, *, firmware, mission)',
            'observe_stage': '(stage, run_dir, workspace, report, accepted=None, options=None, *, firmware, mission)',
            'package_call': '(run_dir, stage, arguments)',
            'record_call': '(run_dir, stage, name, arguments, result, before, after, options=None)'})

    def test_05_B2_preparing_acquire_copies_the_firmware_and_gives_its_hash(self):
        prepared = self.prepare('acquire')
        copy = self.root / 'acquire' / 'description.bin'
        self.assertEqual(copy.read_bytes(), b'supplied firmware')
        self.assertEqual(prepared['inputs'], [str(copy)])
        self.assertEqual(prepared['context'], {'source_path': str(copy), 'expected_sha256': FIRMWARE_SHA256,
                                               'origin': 'provided_binary'})
        self.assertEqual(prepared['allowed_tools'], ['acquire_firmware_artifact'])

    def test_05_B2_preparing_acquire_refuses_a_firmware_whose_file_does_not_hash_to_its_sha256(self):
        with self.assertRaisesRegex(ValueError, '^Supplied firmware hash mismatch$'):
            self.prepare('acquire', firmware={**self.firmware, 'sha256': 'a' * 64})
        Path(self.firmware['path']).write_bytes(b'another firmware')
        with self.assertRaisesRegex(ValueError, '^Supplied firmware hash mismatch$'):
            self.prepare('acquire')
        self.assertFalse((self.root / 'acquire' / 'description.bin').exists())

    def test_05_B2_observing_acquire_checks_for_an_imported_bin_with_the_firmwares_hash_beside_its_provenance(self):
        workspace = self.root / 'acquire'
        workspace.mkdir()
        nothing = self.observe('acquire')
        self.assertEqual(nothing['artifacts'], [])
        self.assertEqual(nothing['checks'], [{'name': 'imported_firmware_hash', 'passed': False,
                                              'reason': 'No matching imported firmware'}])
        imported = workspace / 'tools' / 'artifacts'
        imported.mkdir(parents=True)
        (imported / 'provenance.json').write_text('{}', encoding='utf-8')
        (imported / 'firmware.bin').write_bytes(b'another firmware')
        self.assertEqual(self.observe('acquire')['checks'], nothing['checks'])
        (imported / 'firmware.bin').write_bytes(b'supplied firmware')
        found = self.observe('acquire')
        self.assertEqual(found['checks'], [{'name': 'imported_firmware_hash', 'passed': True}])
        self.assertEqual(found['artifacts'], [str(imported / 'firmware.bin'), str(imported / 'provenance.json')])
        self.assertNotIn('stop', found)

    def test_05_B2_preparing_interpret_hands_the_firmwares_file_to_interpret_run_as_image_bin(self):
        handed = []

        def interpret_run(name, arguments):
            """Stands in for the kit's `interpret_run`: it keeps what it was handed and seals nothing."""
            handed.append((name, arguments))
            return {'ok': True, 'workspace': str(self.root / 'sealed'), 'prompt_for_agent': 'Scripted prepare only.',
                    'sealed_inputs': sorted(arguments['inputs'])}
        with patch('generative_driver.toolkit.call_tool', side_effect=interpret_run):
            prepared = self.prepare('interpret', options={'configured_effects': ['write']})
        self.assertEqual([name for name, _ in handed], ['interpret_run'])
        arguments = handed[0][1]
        shutil.rmtree(arguments['workspace_root'], ignore_errors=True)
        self.assertEqual(arguments['inputs']['image.bin'], self.firmware['path'])
        self.assertEqual(Path(arguments['inputs']['image.bin']).read_bytes(), b'supplied firmware')
        self.assertEqual((arguments['mode'], arguments['front_end'], arguments['attempt']),
                         ('prepare', 'interpret-interface', 1))
        self.assertEqual(prepared['work_dir'], str(self.root / 'sealed'))
        self.assertIn(str(self.root / 'sealed' / 'image.bin'), prepared['inputs'])

    def test_05_B2_interpret_probe_and_ground_give_their_worker_the_missions_tasks_and_operating_conditions(self):
        options = {'device': {'session_id': 'supplied', 'binding': BINDING}, 'configured_effects': ['write']}

        def sealed(name, arguments):
            """Stands in for the kit's `interpret_run`: the worker's inputs are the files it was handed."""
            self.addCleanup(shutil.rmtree, arguments['workspace_root'], True)
            return {'ok': True, 'workspace': str(Path(arguments['inputs']['TASKS.json']).parent),
                    'prompt_for_agent': 'Scripted prepare only.', 'sealed_inputs': ['TASKS.json']}
        for stage, accepted in (('interpret', []), ('probe', self.interpreted()), ('ground', self.probed())):
            with self.subTest(stage=stage), patch('generative_driver.toolkit.call_tool', side_effect=sealed):
                contract = self.tasks_file(self.prepare(stage, accepted, options))
                self.assertEqual(list(contract['tasks']), ['read_level', 'read_status'])
                self.assertEqual(contract['tasks'], MISSION['tasks'])
                self.assertEqual(contract['operating_conditions'], ['The device answers one request at a time.'])
                self.assertEqual(contract['effect_grants'], ['write'])
        plain = {key: MISSION[key] for key in ('tasks', 'reuse_objective')}
        with patch('generative_driver.toolkit.call_tool', side_effect=sealed):
            prepared = self.stages.prepare_stage('interpret', self.run, self.root / 'plain', [], options,
                                                 firmware=self.firmware, mission=plain)
        self.assertNotIn('operating_conditions', self.tasks_file(prepared))

    def test_05_B2_the_probe_objective_and_its_validator_name_the_missions_tasks(self):
        prepared = self.prepare('probe', self.interpreted(), {'device': {'session_id': 'supplied', 'binding': BINDING}})
        self.assertIn('Write capabilities.json with schema capabilities/2 and tasks read_level, read_status. '
                      'Each task declares operation, constants, inputs and outputs.', prepared['objective'])
        self.assertEqual(prepared['binding'], BINDING)
        workspace = self.root / 'probe'
        helper = workspace / 'validate_capabilities.py'
        self.assertIn(str(helper), prepared['inputs'])
        self.assertIn("('read_level', 'read_status')", helper.read_text(encoding='utf-8'))
        # The worker runs the helper as its own process, on the model it was given and the mapping it wrote.
        mapping = workspace / 'capabilities.json'
        for tasks, status, paths in (({}, 1, ['tasks.read_level', 'tasks.read_status']), (MAPPING['tasks'], 0, [])):
            mapping.write_text(json.dumps({**MAPPING, 'tasks': tasks}), encoding='utf-8')
            checked = subprocess.run([sys.executable, str(helper), prepared['context']['model_dir'], str(mapping)],
                                     capture_output=True, text=True, timeout=60, env={**os.environ, **package_env()})
            with self.subTest(tasks=sorted(tasks)):
                self.assertEqual(checked.returncode, status, checked.stderr)
                self.assertEqual([error['path'] for error in json.loads(checked.stdout)['errors']], paths)

    def test_05_B2_the_reuse_objective_holds_the_missions_reuse_objective_and_its_operating_condition(self):
        package = self.root / 'emitted' / 'package'
        package.mkdir(parents=True)
        (package / 'manifest.json').write_text('{}', encoding='utf-8')
        write_state(self.run, {'package_sha256': digest(package)})
        emitted = [{'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}]
        prepared = self.prepare('reuse', emitted, {'device': {'session_id': 'supplied', 'binding': BINDING}})
        self.assertEqual(prepared['objective'],
                         'Use only the emitted package and this objective: Read the level twice and report both '
                         'readings.\nProvided operating conditions (public facts):\n- The device answers one request '
                         'at a time.\n Use package_execute for each operation. Find names and arguments in '
                         'the package reference. Each result also carries an instrument reading of the emulated device '
                         'after the call. Report actual observations and limitations.')
        self.assertEqual(prepared['inputs'], [str(self.root / 'reuse' / 'package')])
        self.assertEqual(prepared['allowed_tools'], ['package_execute'])


class PackageCallTests(StageCase):
    def test_05_B2_package_call_runs_only_the_unchanged_stage_assigned_package(self):
        package = hand_written_package(self.root, ECHO)
        write_state(self.run, {'package_copies': {'reuse': str(package)}, 'package_sha256': digest(package)})
        arguments = {'package_dir': str(package), 'operation': 'level', 'parameters': {'samples': 1},
                     'binding': BINDING, 'allow_effects': ['read', 'write']}
        call = self.stages.package_call(self.run, 'reuse', arguments)
        with patch.dict(os.environ, package_env()):
            result = call()
        self.assertEqual(result['outputs']['arguments'], [
            'execute', 'level', '--parameters', '{"samples": 1}', '--binding', '{"host": "127.0.0.1", "port": 1}',
            '--allow-effect', 'write'])
        other = hand_written_package(self.root / 'other', ECHO)
        with self.assertRaisesRegex(ValueError, '^Only the unchanged stage-assigned package may execute$'):
            self.stages.package_call(self.run, 'reuse', {**arguments, 'package_dir': str(other)})
        (package / 'driver' / '__main__.py').write_text(ECHO + '# changed\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '^Only the unchanged stage-assigned package may execute$'):
            self.stages.package_call(self.run, 'reuse', arguments)


class FreshReuseTests(StageCase):
    """Fresh reuse is recorded and not judged."""
    READINGS = {'session_id': 'supplied', 'binding': BINDING}
    NO_READINGS = {**READINGS, 'instrument': {'units': {}, 'label': 'none'}}

    def emitted(self):
        """The accepted handoff of an emit stage, whose package the stage state file names by its hash."""
        package = self.root / 'emitted' / 'package'
        package.mkdir(parents=True)
        (package / 'manifest.json').write_text('{}', encoding='utf-8')
        write_state(self.run, {'package_sha256': digest(package), 'revision': 2})
        return {'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}

    def mapped(self):
        """The accepted handoff of a probe that carries the model and a capability mapping of it."""
        return self.probed(**{'capabilities.json': json.dumps(MAPPING)})[0]

    def record(self, result, device):
        """Record one package call of fresh reuse that gave `result`, as the service does after the call."""
        self.stages.record_call(self.run, 'reuse', 'package_execute',
                                {'operation': 'level', 'parameters': {'samples': 1}}, result, {'level': 39},
                                {'level': 40}, {'assignment_id': 'reuse-1', 'device': device})

    def rows(self):
        """The rows of the run's record of package calls."""
        path = self.run / 'supplied' / 'package-events.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def test_05_B4_observing_reuse_hands_on_the_package_copy_with_no_check(self):
        scopes = ((self.READINGS, "The fresh worker's package calls and the instrument readings after them, recorded "
                                  'and not judged'),
                  (self.NO_READINGS, "The fresh worker's package calls, recorded and not judged"))
        accepted = [self.emitted()]
        self.prepare('reuse', accepted, {'device': self.READINGS})
        copy = self.root / 'reuse' / 'package'
        self.assertTrue((copy / 'manifest.json').is_file())
        for device, scope in scopes:
            with self.subTest(scope=scope):
                self.assertEqual(self.observe('reuse', accepted, {'device': device}), {
                    'artifacts': [str(copy)], 'checks': [], 'validation_scope': scope,
                    'notes': {'checks': [], 'dropped_artifacts': []}})

    def test_05_B4_observing_reuse_gives_that_result_whatever_was_recorded_and_with_or_without_a_mapping(self):
        emitted = self.emitted()
        with_mapping, without_mapping = [self.mapped(), emitted], [emitted]
        self.prepare('reuse', with_mapping, {'device': self.READINGS})
        expected = {'artifacts': [str(self.root / 'reuse' / 'package')], 'checks': [],
                    'validation_scope': "The fresh worker's package calls and the instrument readings after them, "
                                        'recorded and not judged',
                    'notes': {'checks': [], 'dropped_artifacts': []}}
        results = (None, {'ok': True, 'outputs': {'level': 40}}, {'ok': False, 'error': {'fault': 'model'}},
                   {'ok': False, 'error': {'fault': 'host'}}, {'ok': False})
        for count, result in enumerate(results):
            if result is not None:
                self.record(result, self.READINGS)
                self.assertEqual(len(self.rows()), count)
            for name, accepted in (('a mapping', with_mapping), ('no mapping', without_mapping)):
                with self.subTest(recorded=count, accepted=name):
                    self.assertEqual(self.observe('reuse', accepted, {'device': self.READINGS}), expected)

    def test_05_B4_preparing_reuse_does_not_read_the_accepted_capability_mapping(self):
        mapped, emitted = self.mapped(), self.emitted()
        mapping = next(Path(artifact['path']) for artifact in mapped['artifacts']
                       if artifact['path'].endswith('capabilities.json'))
        # Bytes that no longer have the recorded hash, then no file at all: a stage that read the mapping would stop.
        for change in (lambda: mapping.write_text('{"changed": true}', encoding='utf-8'), mapping.unlink):
            change()
            workspace = self.root / f'reuse-{mapping.exists()}'
            prepared = self.stages.prepare_stage('reuse', self.run, workspace, [mapped, emitted],
                                                 {'device': self.READINGS}, firmware=self.firmware, mission=MISSION)
            self.assertEqual(prepared['context'], {'package_dir': str(workspace / 'package')})
            state = read_state(self.run)
            self.assertNotIn('capabilities_path', state)
            self.assertEqual(sorted(state), ['package_attempts', 'package_copies', 'package_sha256', 'revision'])
            self.assertEqual(state['package_copies'], {'reuse': str(workspace / 'package')})

    def test_05_B4_record_call_appends_one_row_for_a_package_call_with_the_readings_a_device_offers(self):
        write_state(self.run, {'package_sha256': 'a' * 64, 'package_attempts': {'reuse': 'attempt-1'}, 'revision': 2})
        row = {'actor': 'worker', 'stage': 'reuse', 'operation': 'level', 'revision': 2, 'attempt_id': 'attempt-1',
               'assignment_id': 'reuse-1', 'package_sha256': 'a' * 64, 'parameters': {'samples': 1},
               'result': {'ok': True, 'outputs': {'level': 40}}}
        self.record(row['result'], self.READINGS)
        self.assertEqual(self.rows(), [{**row, 'observation': {'level': 40}, 'before_observation': {'level': 39}}])
        self.record(row['result'], self.NO_READINGS)
        self.assertEqual(self.rows()[1:], [row])
        self.assertEqual(list(self.rows()[0]), ['actor', 'stage', 'operation', 'revision', 'attempt_id',
                                                'assignment_id', 'package_sha256', 'parameters', 'result',
                                                'observation', 'before_observation'])

    def test_05_B4_no_module_of_the_stage_package_holds_the_mission_check_or_the_graders_judge(self):
        files = stage_package_files(self)
        self.assertIn('stages.py', [path.name for path in files])
        found = [f'{path.name} holds {word}' for path in files for word in ('fresh_worker_mission', 'reuse_mission')
                 if word in path.read_text(encoding='utf-8')]
        self.assertEqual(found, [])


class DevelopmentConditionTests(StageCase):
    def device(self, **descriptor):
        return {'device': {'session_id': 'supplied', 'binding': BINDING, **descriptor}}

    def test_05_B5_a_catalog_in_the_device_descriptor_gives_probe_and_ground_the_development_conditions(self):
        for stage, accepted in (('probe', self.interpreted()), ('ground', self.probed())):
            with self.subTest(stage=stage):
                prepared = self.prepare(stage, accepted, self.device(development_conditions=CATALOG))
                path = self.root / stage / 'DEVELOPMENT_CONDITIONS.json'
                self.assertTrue(path.is_file(), f'The {stage} worker got no DEVELOPMENT_CONDITIONS.json')
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), CATALOG)
                self.assertIn(str(path), prepared['inputs'])
                self.assertEqual(prepared['context']['development_conditions'], str(path))
                self.assertIn(' Read DEVELOPMENT_CONDITIONS.json. On interface_execute or probe_run, condition_id '
                              'selects a fixed public source condition before that live call; omit it to keep the '
                              'source.', prepared['objective'])
                self.assertTrue(prepared['objective'].endswith(
                    'The requested reference is an experimental input, not proof of a new measurement. Preserve the '
                    'observed discrepancies in your notes; final evaluation is separate.'), prepared['objective'])

    def test_05_B5_without_a_catalog_probe_and_ground_get_no_development_conditions(self):
        for name, descriptor in (('no key', {}), ('an empty catalog', {'development_conditions': {}}),
                                 ('no catalog', {'development_conditions': None})):
            for stage, accepted in (('probe', self.interpreted()), ('ground', self.probed())):
                with self.subTest(descriptor=name, stage=stage):
                    workspace = self.root / f'{stage} with {name}'
                    prepared = self.stages.prepare_stage(stage, self.run, workspace, accepted,
                                                         self.device(**descriptor), firmware=self.firmware,
                                                         mission=MISSION)
                    self.assertFalse((workspace / 'DEVELOPMENT_CONDITIONS.json').exists())
                    self.assertNotIn('development_conditions', prepared['context'])
                    self.assertNotIn('condition_id', prepared['objective'])
                    self.assertEqual([name for name in prepared['inputs'] if 'CONDITIONS' in name], [])

    def test_05_B5_no_tool_module_holds_the_name_of_the_sampled_sensor_case(self):
        modules = tool_modules()
        names = {path.name for path, _ in modules}
        for name in ('configurator.py', 'stages.py', 'instrument_evidence.py', 'device_client.py'):
            self.assertIn(name, names)
        found = [f'{package}/{path.name}'.replace('.', '/', package.count('.')) for path, package in modules
                 if b'sampled-sensor-v1' in path.read_bytes()]
        self.assertEqual(found, [])


class DiscrepancyTests(TemporaryDirectoryCase):
    """`discrepancies` on a hand-written mission that states how its one task samples."""

    def setUp(self):
        self.discrepancies = stage_module(self, 'instrument_evidence').discrepancies
        self.workspace = self.directory()
        self.model = self.workspace / 'model'
        self.model.mkdir()
        (self.model / 'model.json').write_text(json.dumps(SAMPLE_MODEL), encoding='utf-8')
        self.write_mapping(self.workspace / 'capabilities.json', ('measure',))
        self.assignment = {'stage': 'probe', 'workspace': str(self.workspace),
                           'inputs': {str(self.model): digest(self.model)}}

    def write_mapping(self, path, tasks):
        """Write a capability mapping at `path` that maps each of `tasks` to the model's one operation."""
        entry = {'operation': 'sample', 'constants': {}, 'inputs': {},
                 'outputs': {name: {'output': name, 'unit': unit}
                             for name, unit in (('temperature', 'degC'), ('humidity', 'percent'))}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema': 'capabilities/2', 'tasks': dict.fromkeys(tasks, entry)}),
                        encoding='utf-8')

    def live_call(self, temperature, *, sequence, reference=20.0, humidity=40.0):
        """The recorded event of one successful live call of the mapped operation that returned `temperature`,
        between two instrument readings whose `monitor_sequence` are `sequence` and whose temperature is
        `reference`."""
        def reading(count):
            return {'source': 'instrument', 'session_id': 'supplied', 'monitor_sequence': count,
                    'reference_temperature': reference, 'reference_humidity': 10.0,
                    'units': {'monitor_sequence': 'count', 'reference_temperature': 'degC',
                              'reference_humidity': 'percent'}}
        return {'name': 'interface_execute', 'ok': True, 'connected': True, 'dispatch_id': '7',
                'device_session_id': 'supplied', 'operation': 'sample', 'parameters': {},
                'argument_sha256': {'model_dir': digest(self.model)},
                'result': {'ok': True, 'outputs': {'temperature': temperature, 'humidity': humidity},
                           'units': {'temperature': 'degC', 'humidity': 'percent'},
                           'before_observation': reading(sequence[0]), 'observation': reading(sequence[1])}}

    def test_05_B5_readings_whose_sequence_did_not_advance_give_one_entry_for_no_new_acquisition(self):
        found = self.discrepancies(SAMPLED, self.assignment, [self.live_call(20.0, sequence=(3, 3))])
        self.assertEqual([(entry['task'], entry['quantity'], entry['reason']) for entry in found],
                         [('measure', 'sequence', 'no new acquisition')])
        self.assertEqual(found[0]['sequence'], {'before': 3, 'after': 3})

    def test_05_B5_an_output_further_from_the_reading_than_the_tolerance_gives_one_value_mismatch(self):
        found = self.discrepancies(SAMPLED, self.assignment, [self.live_call(20.75, sequence=(3, 4))])
        self.assertEqual([(entry['task'], entry['quantity'], entry['reason']) for entry in found],
                         [('measure', 'temperature', 'value mismatch')])
        self.assertEqual(found[0]['candidate'], {'output': 'temperature', 'value': 20.75, 'unit': 'degC'})
        self.assertEqual(found[0]['instrument'], {'quantity': 'reference_temperature', 'value': 20.0, 'unit': 'degC'})
        self.assertEqual(found[0]['absolute_tolerance'], 0.5)

    def test_05_B5_an_output_within_the_tolerance_gives_no_entry(self):
        for temperature in (20.0, 20.5, 19.5):
            with self.subTest(temperature=temperature):
                self.assertEqual(
                    self.discrepancies(SAMPLED, self.assignment, [self.live_call(temperature, sequence=(3, 4))]), [])

    def test_05_B5_only_tasks_with_sampling_and_outputs_with_an_instrument_are_compared(self):
        # Both tasks map to the operation that ran. `describe` states no sampling, and `humidity` names no instrument,
        # though the device's reading of each differs from what the model returned.
        mission = {**SAMPLED, 'tasks': {
            'describe': {'meaning': 'Describe the sample.', 'inputs': {}, 'outputs': SAMPLED_OUTPUT},
            'measure': {**SAMPLED['tasks']['measure'],
                        'outputs': {**SAMPLED_OUTPUT, 'humidity': {'unit': 'percent'}}}}}
        self.write_mapping(self.workspace / 'capabilities.json', ('describe', 'measure'))
        found = self.discrepancies(mission, self.assignment, [self.live_call(25.0, sequence=(3, 4))])
        self.assertEqual([(entry['task'], entry['quantity'], entry['reason']) for entry in found],
                         [('measure', 'temperature', 'value mismatch')])

    def test_05_B5_a_mission_none_of_whose_tasks_has_sampling_gives_nothing_and_its_mapping_is_not_read(self):
        from generative_driver.integrity import IntegrityError
        call = self.live_call(25.0, sequence=(3, 3))
        self.assertEqual(self.discrepancies(MISSION, self.assignment, [call]), [])
        # A ground assignment whose mapping no longer has its recorded hash stops a mission that is compared, and only
        # that one.
        mapping = self.workspace / 'ground-evidence' / 'capabilities.json'
        self.write_mapping(mapping, ('measure',))
        ground = {**self.assignment, 'stage': 'ground',
                  'inputs': {**self.assignment['inputs'], str(mapping): 'f' * 64}}
        self.assertEqual(self.discrepancies(MISSION, ground, [call]), [])
        with self.assertRaisesRegex(IntegrityError, 'Assigned capability mapping differs from its recorded hash'):
            self.discrepancies(SAMPLED, ground, [call])


class OwnCopiesTests(TemporaryDirectoryCase):
    """What the stage code calls and the grader also has: the tool's own capability rules and package runner."""

    def test_05_B6_the_capability_rules_check_a_mapping_and_bind_a_task(self):
        rules = stage_module(self, 'capabilities')
        tasks = ('read_level', 'read_status')
        self.assertEqual(rules.validate_capabilities(MAPPING, MODEL, tasks), {'ok': True, 'errors': []})
        checked = rules.validate_capabilities({**MAPPING, 'tasks': {}}, MODEL, tasks)
        self.assertIs(checked['ok'], False)
        self.assertEqual([error['path'] for error in checked['errors']], ['tasks.read_level', 'tasks.read_status'])
        self.assertEqual(rules.bind_task(MAPPING, 'read_status', {}, MODEL), {'operation': 'status', 'parameters': {}})
        with self.assertRaisesRegex(ValueError, 'Invalid capability mapping or candidate model'):
            rules.bind_task(MAPPING, 'read_pressure', {}, MODEL)

    def test_05_B6_the_capability_rules_import_only_the_standard_library(self):
        rules = stage_module(self, 'capabilities')
        imported = []
        for node in ast.walk(ast.parse(Path(rules.__file__).read_bytes())):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # A relative import would take a module of the tool, which a worker's copy of the file lacks.
                imported.append('.' * node.level + (node.module or ''))
        self.assertEqual(imported, ['math'])
        self.assertEqual([name for name in imported if name.split('.')[0] not in sys.stdlib_module_names], [])

    def test_05_B6_the_rules_file_a_probe_worker_gets_holds_the_bytes_of_the_tools_capability_rules(self):
        stages, rules = stage_module(self, 'stages'), stage_module(self, 'capabilities')
        root = self.directory()
        model = root / 'accepted' / '0-model.json'
        model.parent.mkdir()
        model.write_text(json.dumps(MODEL), encoding='utf-8')
        accepted = [{'stage': 'interpret', 'artifacts': [{'path': str(model), 'sha256': digest(model)}]}]
        prepared = stages.prepare_stage('probe', root / 'run', root / 'probe', accepted,
                                        {'device': {'session_id': 'supplied', 'binding': BINDING}},
                                        firmware=hand_written_firmware(root), mission=MISSION)
        given = root / 'probe' / '_capability_rules.py'
        self.assertIn(str(given), prepared['inputs'])
        self.assertEqual(given.read_bytes(), Path(rules.__file__).read_bytes())
        self.assertEqual(Path(rules.__file__).parent.name, 'supplied')

    def test_05_B6_the_package_runner_offers_package_invoker_for_a_package_a_binding_and_grants(self):
        runner = stage_module(self, 'package_runner')
        self.assertEqual(list(inspect.signature(runner.package_invoker).parameters), ['package', 'binding', 'grants'])

    def test_05_B6_the_package_runner_runs_the_packages_command_line_with_the_binding_and_the_grants(self):
        runner = stage_module(self, 'package_runner')
        package = hand_written_package(self.directory(), ECHO)
        with patch.dict(os.environ, package_env()):
            result = runner.package_invoker(package, BINDING, ['read', 'write'])('read_level', {'samples': 2})
        self.assertEqual(result, {'ok': True, 'units': {}, 'transcript': [], 'outputs': {'arguments': [
            'execute', 'read_level', '--parameters', '{"samples": 2}', '--binding', '{"host": "127.0.0.1", "port": 1}',
            '--allow-effect', 'write']}})

    def test_05_B6_the_package_runner_reports_a_package_that_prints_no_result_as_a_host_fault(self):
        runner = stage_module(self, 'package_runner')
        package = hand_written_package(self.directory(), "import sys\nprint('not a result')\nsys.exit(3)\n")
        with patch.dict(os.environ, package_env()):
            result = runner.package_invoker(package, BINDING, [])('read_level', {})
        self.assertEqual(result, {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [], 'error': {
            'fault': 'host', 'source': 'package', 'code': 'no_result',
            'message': 'The package returned no runtime result (exit 3)'}})


if __name__ == '__main__':
    unittest.main()

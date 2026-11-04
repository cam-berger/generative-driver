"""The family seam: the grading core reaches a family only through `families`, so it names none, and each family's code
moves only the identities of the cases that use it."""
import ast
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import generative_driver
from benchmark_family_fixtures import toy_sample_model, toy_sensor_phase
from package_env import package_env
from test_grader_identity import PackageCopy
from generative_driver.benchmark_support import (instruments, parameter_store, registrations, registry, sampled_sensor,
                                                  tq9_legacy, tq9_v2)
from generative_driver.benchmark_support.calibration import inventory, validate_calibration
from generative_driver.benchmark_support.case_assets import reuse_mission
from generative_driver.benchmark_support.emulated_actions import execute_plan
from generative_driver.benchmark_support.evaluator import method_for
from generative_driver.benchmark_support.families import Family, family_for, family_from
from generative_driver.benchmark_support.grader_identity import grader_closure, grader_identity
from generative_driver.benchmark_support.registrations import registered_case_ids, registration_for
from generative_driver.benchmark_support.registry import case_descriptors, case_truth, pin_case, require_calibration, resolve_case
from generative_driver.benchmark_support.snapshots import canonical_digest

HOOKS = ('contract', 'observations', 'reuse_passed', 'required_mutants', 'execute', 'build_plan', 'hydrate_truth',
         'admission_valid', 'validate_phase', 'required_scenarios', 'mutant_checks_may_exceed_expected',
         'input_commitments', 'inventory_files', 'instrument_units', 'public_observation', 'development_conditions',
         'start_session', 'validate_execution', 'current_pin')
REQUIRED = ('contract', 'observations', 'reuse_passed', 'required_mutants')
INVENTORY_SET = frozenset({'source', 'contract', 'recipe', 'references', 'mutations', 'build', 'inventory'})


def observe(raw, canonical_task_result, inputs):
    return {'pressure': raw['values']['pressure'], 'hidden': 1}


observe.monitor_units = {'pressure': 'kPa'}


def complete_hooks(**changes):
    """Every required hook of a graded case, with a plan: what `family_from` accepts."""
    return {'contract': lambda pin, truth, phase: {'checks': []}, 'observations': observe,
            'reuse_passed': lambda events, capabilities: True, 'required_mutants': frozenset({'constant'}),
            'build_plan': lambda pin, truth, phase: [], **changes}


def graded_case(**changes):
    return replace(registration_for('sampled-sensor-v1'), id='toy-v1', family='toy', native_module='toy', **changes)


def ungraded_case():
    return replace(graded_case(), adapter_key='legacy-emulator')


class FamilyInterfaceTests(unittest.TestCase):
    def test_01_B1_every_registered_case_has_a_family_with_one_attribute_per_hook(self):
        self.assertEqual({field.name for field in dataclasses.fields(Family)},
                         (set(HOOKS) - {'required_mutants'}) | {'_required_mutants'})
        self.assertIsInstance(Family.required_mutants, property)
        for case_id in registered_case_ids():
            with self.subTest(case_id):
                self.assertIsInstance(family_for(case_id), Family)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            family_for('tq9-v2').contract = None
        for unregistered in ('unregistered-v1', None):
            with self.subTest(unregistered), self.assertRaisesRegex(ValueError, 'Unknown benchmark case'):
                family_for(unregistered)

    def test_01_B2_a_malformed_family_is_refused_naming_the_case_and_the_hook(self):
        self.assertIsInstance(family_from(graded_case(), complete_hooks()), Family)
        hooks = complete_hooks()
        del hooks['build_plan']
        self.assertIsInstance(family_from(graded_case(), {**hooks, 'execute': print}), Family)
        for name, malformed, named in (
                ('unknown', complete_hooks(hydrate=print), 'hydrate'),
                ('both', complete_hooks(execute=print), 'execute'),
                ('both', complete_hooks(execute=print), 'build_plan'),
                ('neither', hooks, 'execute'),
                ('neither', hooks, 'build_plan'),
                *[('required', {key: value for key, value in complete_hooks().items() if key != hook}, hook)
                  for hook in REQUIRED]):
            with self.subTest(name, hook=named), self.assertRaisesRegex(ValueError, 'toy-v1') as raised:
                family_from(graded_case(), malformed)
            self.assertIn(named, str(raised.exception))
        # Only a graded case must declare these; an unknown hook is refused on any case.
        self.assertIsInstance(family_from(ungraded_case(), {}), Family)
        with self.assertRaisesRegex(ValueError, 'toy-v1.*hydrate'):
            family_from(ungraded_case(), {'hydrate': print})

    def test_01_B3_a_family_that_omits_an_optional_hook_gets_the_default(self):
        family = family_from(graded_case(), complete_hooks())
        truth = {'schema': 'unlocked'}
        self.assertIs(family.hydrate_truth(truth), truth)
        self.assertIs(family.admission_valid({}, truth, {}), False)
        self.assertIsNone(family.validate_phase({}))
        self.assertEqual(family.required_scenarios, frozenset())
        self.assertIs(family.mutant_checks_may_exceed_expected, False)
        self.assertEqual(family.input_commitments, INVENTORY_SET)
        self.assertEqual(family.inventory_files, frozenset())
        self.assertEqual(family.instrument_units(), {'pressure': 'kPa'})
        self.assertEqual(family.public_observation({'values': {'pressure': 101}}),
                         {'values': {'pressure': 101}, 'units': {'pressure': 'kPa'}})
        self.assertIsNone(family.development_conditions({'stimuli': {'source_q4': {}}}))
        self.assertIsNone(family.validate_execution({'execution_setup': {'method': 'renode'}}, truth))
        self.assertIsNone(family.validate_execution({}, truth))

    def required_calls(self, family):
        """Each required hook of `family`, its plan and its step executor, as a call."""
        return {'contract': lambda: family.contract({}, {}, 'final'),
                'observations': lambda: family.observations({}, {}, {}),
                'reuse_passed': lambda: family.reuse_passed([], {}),
                'required_mutants': lambda: family.required_mutants,
                'build_plan': lambda: family.build_plan({}, {}, 'final'),
                'execute': lambda: family.execute(None, None, {}, {}, {'contracts': {}}, 'raw.json', phase='final',
                                                  healthy_device_failures=False)}

    def test_01_B4_a_case_without_a_module_or_a_hook_is_refused_when_the_hook_is_used(self):
        for case_id in ('bme280', 'setup-smoke'):
            family = family_for(case_id)
            for hook, call in self.required_calls(family).items():
                with self.subTest(case_id, hook=hook), self.assertRaisesRegex(ValueError, case_id):
                    call()
            self.assertIs(family.admission_valid({}, {}, {}), False)
            self.assertEqual(family.inventory_files, frozenset())
            truth = {'schema': 'benchmark-family-truth/1'}
            self.assertIs(family.hydrate_truth(truth), truth)
            for call in (family.instrument_units, lambda: family.public_observation({})):
                with self.subTest(case_id, hook='instrument'), self.assertRaisesRegex(ValueError, case_id):
                    call()
            mission = reuse_mission(case_id, [], {})
            self.assertIsNone(mission['passed'])
            self.assertIn(case_id, mission['reason'])
        legacy = self.required_calls(family_for('tq9'))
        del legacy['reuse_passed']  # the one required hook the legacy module declares
        for hook, call in legacy.items():
            with self.subTest('tq9', hook=hook), self.assertRaisesRegex(ValueError, 'tq9'):
                call()


class ToySession:
    """An emulator session of a fixture device: what `execute_plan` and the evaluator ask of one, with no wire."""
    process, info, binding, stopped = None, {'fixture': True}, {}, False

    def reset(self, values):
        return {}

    def set_running(self, value):
        pass

    def observe(self):
        return {'values': {'latched_q4': 65456, 'acquisition_counter': 3}}

    def stop(self):
        self.stopped = True


def toy_invoke(calls):
    """A package call that answers as the toy sensor's correct driver would, keeping each task in `calls`."""
    def invoke(task, inputs, grants):
        calls.append(task)
        return {'ok': True, 'outputs': {'temperature': -5, 'sequence': 3},
                'units': {'temperature': 'degC', 'sequence': 'count'}}
    return invoke


def toy_plan_case():
    """(pin, truth) of the toy sampled sensor, whose two phases call different tasks."""
    pin, truth = toy_sensor_phase()
    pin.update(case_id='sampled-sensor-v1', revision=0)
    contract = truth['phases']['diagnostic']['contract']
    checks = [check['id'] for check in contract['checks']]
    truth['phases'] = {phase: {'contract': json.loads(json.dumps(contract)), 'actions': [
        {'kind': 'reset', 'values': {}}, {'kind': 'call', 'task': phase + '-task', 'inputs': {}, 'grants': ['write']},
        {'kind': 'observe', 'checks': checks}]} for phase in ('diagnostic', 'final')}
    truth['contracts'], truth['recipe'] = {'time_policy': {'sample_settle_seconds': 0}}, {}
    return pin, truth


def grade_frozen(root, pin, truth, calls):
    """`reference_execute` of a frozen package under `root` on a toy session, its package calls kept in `calls`."""
    from generative_driver.benchmark_support.evaluator import reference_execute
    from generative_driver.benchmark_support.integrity import digest
    package = root / 'package'
    (package / 'driver').mkdir(parents=True)
    (package / 'interface_runtime').mkdir()
    (package / 'driver' / 'model.json').write_text('{}\n', encoding='utf-8')
    (package / 'interface_runtime' / 'engine.py').write_text('# runtime\n', encoding='utf-8')
    with patch('generative_driver.benchmark_support.native.NativeSession.start', return_value=ToySession()), \
            patch('generative_driver.benchmark_support.emulated_actions.canonical_package_invoker',
                  return_value=toy_invoke(calls)):
        return reference_execute(pin, truth, {}, renode=Path('/toy'), image=Path('/toy'), output_dir=root / 'final',
                                 package=package, expected_package_sha256=digest(package), healthy_device_failures=True)


class FamilyExecutionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def own_steps(self, seen):
        """The hooks of the toy sensor's family with a step executor of its own in place of its plan, which keeps each
        call's arguments in `seen`."""
        def execute(session, invoke, pin, contract, truth, evidence_path, *, phase, healthy_device_failures):
            seen.append({'pin': pin, 'phase': phase, 'healthy_device_failures': healthy_device_failures,
                         'artifact': truth['artifact_sha256'], 'contract': contract})
            return [{'own': phase}]
        hooks = {key: value for key, value in sampled_sensor.HOOKS.items() if key != 'build_plan'}
        return patch.dict(sampled_sensor.HOOKS, {**hooks, 'execute': execute}, clear=True)

    def grade_frozen(self, pin, truth, calls):
        return grade_frozen(self.root, pin, truth, calls)

    def test_01_B5_a_plan_family_runs_its_plan_for_the_phase_through_execute_plan(self):
        pin, truth = toy_plan_case()
        truth['artifact_sha256'] = 'a' * 64
        family = family_for('sampled-sensor-v1')
        for phase in ('diagnostic', 'final'):
            with self.subTest(phase):
                contract = family.contract(pin, truth, phase)
                calls, direct = [], []
                records = family.execute(ToySession(), toy_invoke(calls), pin, contract, truth, self.root / 'raw.json',
                                         phase=phase, healthy_device_failures=False)
                expected = execute_plan(ToySession(), toy_invoke(direct), family.build_plan(pin, truth, phase),
                                        contract, self.root / 'direct.json', family.observations, 0)
                self.assertEqual(records, expected)
                self.assertEqual((calls, direct), ([phase + '-task'], [phase + '-task']))

    def test_01_B5_a_family_that_declares_execute_runs_its_own(self):
        own = lambda *call, **options: 'its own'
        hooks = {key: value for key, value in complete_hooks().items() if key != 'build_plan'}
        family = family_from(graded_case(), {**hooks, 'execute': own})
        self.assertIs(family.execute, own)

    def test_01_B5_grading_and_the_diagnostic_path_have_one_call_path(self):
        from generative_driver.benchmark_support import evaluator, reference_calibration
        from generative_driver.benchmark_support.reference_calibration import run_reference
        for module in (evaluator, reference_calibration):
            tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
            calls = [node.func.id for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
            self.assertNotIn('hasattr', calls)
        pin, truth = toy_plan_case()
        seen = []
        with self.own_steps(seen):
            graded = self.grade_frozen(pin, truth, [])
            with patch('generative_driver.benchmark_support.native.NativeSession.start', return_value=ToySession()), \
                    patch('generative_driver.benchmark_support.reference_calibration.model_invoker',
                          return_value=toy_invoke([])):
                diagnostic = run_reference(pin, truth, toy_sample_model(), {}, renode=Path('/toy'), image=Path('/toy'),
                                           output_dir=self.root / 'diagnostic', phase='diagnostic')
        self.assertEqual([(row['phase'], row['healthy_device_failures'], row['pin']) for row in seen],
                         [('final', True, pin), ('diagnostic', False, pin)])
        self.assertEqual((graded['records'], diagnostic['records']), ([{'own': 'final'}], [{'own': 'diagnostic'}]))
        self.assertEqual(seen[0]['contract']['artifact_sha256'], graded['package_sha256'])

    def test_01_B5_a_plan_familys_records_contract_and_grade_are_unchanged(self):
        pin, truth = toy_plan_case()
        calls = []
        graded = self.grade_frozen(pin, truth, calls)
        contract = {**truth['phases']['final']['contract'], 'artifact_sha256': graded['package_sha256']}
        self.assertEqual(graded['contract'], contract)
        self.assertEqual(graded['grade']['verdict'], 'passed')
        self.assertEqual(calls, ['final-task'])
        self.assertEqual({record['id']: (record['value'], record['unit']) for record in graded['records']},
                         {'toy/measure/temperature': (-5, 'degC'), 'toy/measure/reference_temperature': (-5.0, 'degC'),
                          'toy/measure/sequence': (3, 'count'), 'toy/measure/monitor_sequence': (3, 'count'),
                          'toy/measure/operation_ok': (True, 'boolean')})
        self.assertEqual(json.loads((self.root / 'final' / 'raw.json').read_text(encoding='utf-8'))['schema'],
                         'benchmark-action-evidence/1')


NATIVE_MANIFEST = {'execution_setup': {'method': 'native-sim', 'artifact': 'firmware.elf', 'substitutions': [],
                                       'validated_behavior': [], 'hardware_gaps': []}}


class FamilySessionTests(unittest.TestCase):
    SESSION_CLASSES = ('generative_driver.benchmark_support.native.NativeSession.start',
                       'generative_driver.benchmark_support.native_firmware.NativeFirmwareSession.start')
    PLACE = {'image': Path('/toy'), 'renode': None, 'private_dir': None}

    def started(self, case_id, recipe):
        """Which session class `case_id`'s family starts for `recipe`, with both mocked, since native sessions need
        Linux."""
        with patch(self.SESSION_CLASSES[0], return_value='renode') as renode, \
                patch(self.SESSION_CLASSES[1], return_value='native') as native:
            session = family_for(case_id).start_session(recipe=recipe, **self.PLACE)
        called = renode if session == 'renode' else native
        self.assertEqual(called.call_args.kwargs, {'recipe': recipe, **self.PLACE})
        return session

    def test_01_B16_the_default_starts_renode_and_refuses_any_other_backend(self):
        for recipe in ({}, {'backend': 'renode'}):
            self.assertEqual(self.started('sampled-sensor-v1', recipe), 'renode')
        for backend in ('native-sim', 'elsewhere'):
            with self.subTest(backend), self.assertRaisesRegex(ValueError, backend):
                family_for('sampled-sensor-v1').start_session(recipe={'backend': backend}, **self.PLACE)

    def test_01_B16_the_default_validate_execution_refuses_a_native_sim_manifest(self):
        family = family_for('sampled-sensor-v1')
        with self.assertRaisesRegex(ValueError, 'sampled-sensor-v1'):
            family.validate_execution(NATIVE_MANIFEST, {})
        self.assertIsNone(family.validate_execution({'execution_setup': {'method': 'renode'}}, {}))

    def test_01_B16_the_modbus_case_with_a_native_sim_recipe_starts_the_native_firmware_session(self):
        self.assertEqual(self.started('zephyr-modbus-v1', {'backend': 'native-sim'}), 'native')
        self.assertEqual(self.started('zephyr-modbus-v1', {}), 'renode')
        with self.assertRaisesRegex(ValueError, 'elsewhere'):
            family_for('zephyr-modbus-v1').start_session(recipe={'backend': 'elsewhere'}, **self.PLACE)
        with self.assertRaisesRegex(ValueError, 'native/Renode comparison'):
            family_for('zephyr-modbus-v1').validate_execution(NATIVE_MANIFEST, {})
        self.assertIsNone(family_for('zephyr-modbus-v1').validate_execution({}, {}))

    def test_01_B16_each_caller_starts_its_session_through_the_family(self):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        from generative_driver.benchmark_support.execution import validate_setup
        from generative_driver.benchmark_support.reference_calibration import run_reference
        from test_device_provider import Raw
        recipe = {'observations': {'latched_q4': {'command': 'private-latched', 'width': 16, 'count': 1},
                                   'acquisition_counter': {'command': 'private-counter', 'width': 32, 'count': 1}}}
        values = {'latched_q4': 400, 'acquisition_counter': 2}
        pin, truth = toy_plan_case()
        starts = []

        def start(*, recipe, **arguments):
            starts.append(sorted(arguments))
            return Raw(recipe, values) if recipe.get('observations') else ToySession()

        with tempfile.TemporaryDirectory() as temp, patch.dict(sampled_sensor.HOOKS, {'start_session': start}), \
                patch('generative_driver.benchmark_support.reference_calibration.model_invoker',
                      return_value=toy_invoke([])):
            root = Path(temp)
            (root / 'image').write_bytes(b'image')
            provider = DeviceProvider('sampled-sensor-v1', root / 'image', recipe, None, root / 'device')
            provider.start()
            provider.stop()
            run_reference(pin, truth, toy_sample_model(), {}, renode=Path('/toy'), image=Path('/toy'),
                          output_dir=root / 'diagnostic', phase='diagnostic')
            grade_frozen(root, pin, truth, [])
        self.assertEqual(starts, [['image', 'private_dir', 'renode']] * 3)
        # `validate_setup` asks the case's family about a native-sim method, after what any method must satisfy.
        with self.assertRaisesRegex(ValueError, 'sampled-sensor-v1'):
            validate_setup({'id': 'sampled-sensor-v1', **NATIVE_MANIFEST}, {'recipe': {'backend': 'native-sim'}})


class InstrumentTests(unittest.TestCase):
    TQ9_RAW = {'values': {'compare': 37, 'reload': 99, 'temperature': 21.5}}

    def test_01_B11_units_and_public_observations_are_todays(self):
        self.assertEqual(instruments.units('tq9'), {'duty': 'fraction'})
        for case_id in ('tq9-v2', 'tq9-v3'):
            self.assertEqual(instruments.units(case_id), {'duty': 'permille', 'temperature_reference': 'degC'})
        self.assertEqual(instruments.units('sampled-sensor-v1'),
                         {'reference_temperature': 'degC', 'monitor_sequence': 'count'})
        self.assertEqual(instruments.public_observation('tq9', self.TQ9_RAW),
                         {'values': {'duty': 0.37}, 'units': {'duty': 'fraction'}})
        self.assertEqual(instruments.public_observation('tq9-v2', self.TQ9_RAW),
                         {'values': {'duty': 370.0, 'temperature_reference': 21.5},
                          'units': {'duty': 'permille', 'temperature_reference': 'degC'}})
        self.assertEqual(instruments.public_observation('sampled-sensor-v1',
                                                        {'values': {'latched_q4': 400, 'acquisition_counter': 2}}),
                         {'values': {'reference_temperature': 25.0, 'monitor_sequence': 2},
                          'units': {'reference_temperature': 'degC', 'monitor_sequence': 'count'}})

    def test_01_B11_the_units_and_the_observation_come_from_the_familys_hooks(self):
        for module in (tq9_legacy, tq9_v2):
            case_id = 'tq9' if module is tq9_legacy else 'tq9-v2'
            projected = {'values': {'toy': 1}, 'units': {'toy': 'unit'}}
            with patch.dict(module.HOOKS, {'instrument_units': lambda: {'toy': 'unit'},
                                           'public_observation': lambda raw: projected}):
                self.assertEqual(instruments.units(case_id), {'toy': 'unit'})
                self.assertEqual(instruments.public_observation(case_id, {}), projected)

    def test_01_B11_reuse_missions_are_todays(self):
        passed = {'name': 'fresh_worker_mission', 'passed': True}
        failed = {'name': 'fresh_worker_mission', 'passed': False, 'fault': 'model',
                  'reason': 'Fresh worker mission lacks observed reading, requested effect or disarm'}
        legacy = {'temperature': {'operation': 'read', 'output': 't'}}
        duty = [{'operation': 'read', 'result': {'ok': True, 'outputs': {'t': 25}}, 'observation': {'duty': .37}},
                {'operation': 'off', 'result': {'ok': True}, 'observation': {'duty': 0}}]
        self.assertEqual(reuse_mission('tq9', duty, legacy), passed)
        self.assertEqual(reuse_mission('tq9', duty[:1], legacy), failed)
        current = {'tasks': {'temperature': {'operation': 'read', 'outputs': {'temperature': {'output': 'reading'}}}}}
        permille = [{'operation': 'read', 'result': {'ok': True, 'outputs': {'reading': 25}},
                     'observation': {'duty': value}} for value in (370, 0)]
        self.assertEqual(reuse_mission('tq9-v2', permille, current), passed)
        self.assertEqual(reuse_mission('tq9-v2', permille[:1], current), failed)
        sensor = {'tasks': {'measure': {'operation': 'm', 'outputs': {'temperature': {'output': 't'},
                                                                       'sequence': {'output': 's'}}}}}
        samples = [{'operation': 'm', 'result': {'ok': True, 'outputs': {'t': 25, 's': value}},
                    'observation': {'reference_temperature': 25, 'monitor_sequence': value}} for value in (1, 2)]
        self.assertEqual(reuse_mission('sampled-sensor-v1', samples, sensor), passed)
        self.assertEqual(reuse_mission('sampled-sensor-v1', samples[:1], sensor), failed)
        self.assertEqual(reuse_mission('bme280', duty, legacy),
                         {'name': 'fresh_worker_mission', 'passed': None,
                          'reason': 'The capability mapping cannot judge the mission: ValueError: '
                                    'Unknown native family: bme280'})

    def test_01_B11_the_reuse_mission_comes_from_the_familys_hook(self):
        for module, case_id in ((tq9_legacy, 'tq9'), (tq9_v2, 'tq9-v2'), (sampled_sensor, 'sampled-sensor-v1')):
            with self.subTest(case_id), patch.dict(module.HOOKS, {'reuse_passed': lambda events, capabilities: True}):
                self.assertIs(reuse_mission(case_id, [], {})['passed'], True)


class DevelopmentConditionTests(unittest.TestCase):
    CATALOG = {'schema': 'development-conditions/1', 'conditions': [
        {'id': name, 'reference': {'value': value, 'unit': 'degC'}}
        for name, value in (('low', -12.0), ('high', 32.0), ('zero', 0.0), ('fractional', 7.5),
                            ('fine_fraction', 18.5625))]}
    STIMULI = {'stimuli': {'source_q4': {'command': 'c', 'minimum': -8192, 'maximum': 8191}}}

    def provider(self, case_id, recipe):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        return DeviceProvider(case_id, Path('/toy'), recipe, None, Path('/toy'))

    def test_01_B12_only_the_sensor_with_a_source_stimulus_offers_development_conditions(self):
        self.assertEqual(self.provider('sampled-sensor-v1', self.STIMULI).development_conditions(), self.CATALOG)
        self.assertIsNone(self.provider('sampled-sensor-v1', {'stimuli': {}}).development_conditions())
        self.assertIsNone(self.provider('sampled-sensor-v1', {}).development_conditions())
        for case_id in registered_case_ids():
            if case_id not in ('sampled-sensor-v1', 'bme280', 'setup-smoke'):
                with self.subTest(case_id):
                    self.assertIsNone(self.provider(case_id, self.STIMULI).development_conditions())

    def test_01_B12_the_catalog_comes_from_the_familys_hook(self):
        with patch.dict(sampled_sensor.HOOKS, {'development_conditions': lambda recipe: 'catalog of the hook'}):
            self.assertEqual(self.provider('sampled-sensor-v1', {}).development_conditions(), 'catalog of the hook')


TQ9_TIME_POLICY = {'probe': 'continuous emulator virtual time',
                   'package_calls': 'paused between calls, running inside each call; model timing must hold inside '
                                    'each operation',
                   'clock': 'firmware reset-clock SysTick frequency configured by encrypted reference recipe',
                   'limitation': 'Agent thinking time is excluded from device watchdog time during reuse; this is '
                                 'not a real-time control benchmark'}
TQ9_IMAGE = 'c1e9ab72e497c13d1e8037a9773c8b229adf6eb63665c3a8f750894c65d92d51'
DESCRIBED = {'availability_basis': 'public-metadata', 'authenticated_admission': 'not-checked', 'status': 'available'}


class UnbranchedCaseTests(unittest.TestCase):
    """What the core branched on by a legacy case's name comes from its registration row, with today's values."""

    def assert_definition(self, case_id, **expected):
        definition = resolve_case(case_id)
        self.assertEqual({key: getattr(definition, key) for key in expected}, expected)

    def test_01_B10_resolve_case_returns_todays_definitions(self):
        self.assert_definition('tq9', id='tq9', family='tq9', version='2', evaluator_version='2',
                               execution='actual-agent-emulation', evidence_track='firmware',
                               adapter_key='legacy-emulator', approval_scope='emulator',
                               default_effects=('write', 'actuate'),
                               manifest_sha256='fa1d971100f646cc950e7af5eb5b96b82ff4a85950ecbbd7121305603a3c74b1')
        self.assert_definition('bme280', id='bme280', family='bme280', version='3', evaluator_version='3',
                               execution='actual-agent-physical', evidence_track='physical',
                               adapter_key='legacy-physical', approval_scope='bound-device', default_effects=('write',),
                               manifest_sha256='8b7b2abfb883e6b61d64f8ffbcb4201084d30c5b263691fbadfde39eaaa2b4d3')
        self.assert_definition('setup-smoke', id='setup-smoke', family='setup-smoke', version='1',
                               evaluator_version='1', execution='scripted-replay', evidence_track='recorded-replay',
                               adapter_key='smoke',
                               approval_scope=None, default_effects=(), manifest={}, manifest_sha256='')

    def test_01_B10_pin_case_returns_todays_pins(self):
        common = {'schema': 'benchmark-case-pin/1', 'case_seed': 0, 'calibration': {'status': 'legacy-not-required'}}
        expected = {
            'tq9': {**common, 'case': 'tq9', 'case_id': 'tq9', 'case_version': '2', 'evaluator_version': '2',
                    'family': 'tq9', 'scenario_id': 'original',
                    'manifest_sha256': 'fa1d971100f646cc950e7af5eb5b96b82ff4a85950ecbbd7121305603a3c74b1',
                    'image_hashes': {'firmware.bin': TQ9_IMAGE},
                    'truth_sha256': '4de3c5c251f68ead6567c9b140b41654c6dbc49f1ec85eb943eba00693792b2d',
                    'execution': 'actual-agent-emulation', 'evidence_track': 'firmware', 'scope': 'full-workflow',
                    'time_policy': TQ9_TIME_POLICY},
            'bme280': {**common, 'case': 'bme280', 'case_id': 'bme280', 'case_version': '3', 'evaluator_version': '3',
                       'family': 'bme280', 'scenario_id': 'original',
                       'manifest_sha256': '8b7b2abfb883e6b61d64f8ffbcb4201084d30c5b263691fbadfde39eaaa2b4d3',
                       'image_hashes': {},
                       'truth_sha256': '0eaaca4a7dcd6ffe7ee1eb638857966e3152d1c7be420b4aa691e2e76844f82c',
                       'execution': 'actual-agent-physical', 'evidence_track': 'physical', 'scope': 'full-workflow',
                       'time_policy': {'physical': 'Real elapsed device time; no timing pause'}},
            'setup-smoke': {**common, 'case': 'setup-smoke', 'case_id': 'setup-smoke', 'case_version': '1',
                            'evaluator_version': '1', 'family': 'setup-smoke', 'scenario_id': None,
                            'manifest_sha256': '',
                            'image_hashes': {}, 'truth_sha256': None, 'execution': 'scripted-replay',
                            'evidence_track': 'recorded-replay', 'scope': 'installation', 'time_policy': {}}}
        for case_id, pin in expected.items():
            with self.subTest(case_id):
                pinned = pin_case(case_id, None, 0)
                self.assertEqual({key: value for key, value in pinned.items() if key != 'manifest'}, pin)
                with self.assertRaisesRegex(ValueError, 'Legacy case does not support'):
                    pin_case(case_id, None, 1)

    def test_01_B10_method_for_and_the_catalog_rows_are_todays(self):
        self.assertEqual({case_id: method_for(case_id) for case_id in ('tq9', 'bme280', 'setup-smoke', 'tq9-v2', None)},
                         {'tq9': 'recorded-reuse', 'bme280': 'recorded-reuse', 'setup-smoke': 'fresh-final',
                          'tq9-v2': 'fresh-final', None: 'fresh-final'})
        rows = {row['id']: row for row in case_descriptors()}
        self.assertEqual(rows['tq9'], {**DESCRIBED, 'id': 'tq9', 'family': 'tq9', 'version': '2',
                                       'execution': 'actual-agent-emulation', 'evidence_track': 'firmware',
                                       'scenarios': ['original']})
        self.assertEqual(rows['bme280'], {**DESCRIBED, 'id': 'bme280', 'family': 'bme280', 'version': '3',
                                          'execution': 'actual-agent-physical', 'evidence_track': 'physical',
                                          'scenarios': ['original']})
        self.assertEqual(rows['setup-smoke'], {**DESCRIBED, 'id': 'setup-smoke', 'family': 'setup-smoke',
                                               'version': '1', 'execution': 'scripted-replay',
                                               'evidence_track': 'recorded-replay',
                                               'scenarios': []})

    def register_like(self, source, case_id):
        """Register `case_id` with the row of `source`, its family and version too, until the test ends."""
        row = replace(registration_for(source), id=case_id)
        for patcher in (patch.object(registrations, '_REGISTRATIONS',
                                     MappingProxyType({**registrations._REGISTRATIONS, case_id: row})),
                        patch.dict(registry.CURRENT_VERSIONS, {case_id: row.version})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def setUp(self):
        self.installed = {case_id: resolve_case(case_id).manifest for case_id in ('tq9', 'bme280')}

    def case_file(self, source, case_id, **changes):
        """A public case tree whose only case is `case_id`, with `source`'s manifest and `changes`; the case root until
        the test ends."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        manifest = {**self.installed[source], 'id': case_id, 'truth': {}, 'images': {}, **changes}
        (root / 'cases' / case_id).mkdir(parents=True)
        (root / 'cases' / case_id / 'case.json').write_text(json.dumps(manifest), encoding='utf-8')
        patcher = patch('generative_driver.benchmark_support.case_assets.case_root', return_value=root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_01_B10_those_values_come_from_the_row_not_the_case_id(self):
        self.register_like('bme280', 'another-physical')
        self.case_file('bme280', 'another-physical')
        self.assertEqual(method_for('another-physical'), 'recorded-reuse')
        self.assert_definition('another-physical', family='bme280', evidence_track='physical',
                               approval_scope='bound-device', default_effects=('write',))
        self.assertEqual(pin_case('another-physical', None, 0)['calibration'], {'status': 'legacy-not-required'})
        self.register_like('tq9', 'another-emulated')
        self.case_file('tq9', 'another-emulated')
        with self.assertRaisesRegex(ValueError, 'one original firmware image'):
            resolve_case('another-emulated')
        self.register_like('setup-smoke', 'another-smoke')
        self.assert_definition('another-smoke', family='setup-smoke', execution='scripted-replay',
                               evidence_track='recorded-replay', approval_scope=None, default_effects=(), manifest={})
        self.assertEqual(pin_case('another-smoke', None, 0)['scope'], 'installation')
        self.assertEqual(method_for('another-smoke'), 'fresh-final')


class HydrationTests(unittest.TestCase):
    FAMILY_TRUTH = {'schema': 'benchmark-family-truth/1', 'recipe': {}}

    def unlocked(self, truth):
        return patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value=truth)

    def never_hydrated(self):
        return patch('generative_driver.benchmark_support.calibration.hydrate_truth',
                     side_effect=AssertionError('hydrated by the schema the truth names'))

    def test_01_B13_a_family_truth_of_a_case_with_no_hydrate_truth_comes_back_unlocked(self):
        with self.unlocked(self.FAMILY_TRUTH), self.never_hydrated(), \
                patch('generative_driver.benchmark_support.case_assets.case_inputs',
                      return_value=(Path('inputs'), {'truth': {'sha256': 'a' * 64}})):
            self.assertIs(case_truth('tq9-v2', Path('run'), None).payload, self.FAMILY_TRUTH)

    def test_01_B13_admission_hydrates_from_the_family_never_from_the_truths_schema(self):
        pin = pin_case('tq9-v2', 'original', 0)
        pin['manifest'].update(calibration={'status': 'passed'})
        pin['calibration'] = {'status': 'passed'}
        with self.unlocked(self.FAMILY_TRUTH), self.never_hydrated(), \
                patch('generative_driver.benchmark_support.registry.pin_case', return_value=pin), \
                self.assertRaisesRegex(ValueError, 'missing, stale, or incomplete'):
            require_calibration(pin, {})

    def test_01_B13_a_case_hydrates_with_its_familys_hook(self):
        hydrated = {'hydrated': True}
        with self.unlocked(self.FAMILY_TRUTH), patch.dict(tq9_v2.HOOKS, {'hydrate_truth': lambda truth: hydrated}), \
                patch('generative_driver.benchmark_support.case_assets.case_inputs',
                      return_value=(Path('inputs'), {'truth': {'sha256': 'a' * 64}})):
            self.assertIs(case_truth('tq9-v2', Path('run'), None).payload, hydrated)


class AdmissionTests(unittest.TestCase):
    REFUSED = 'missing, stale, or incomplete'

    def admit(self, pin, truth):
        """Admission of `pin` when its sealed truth unlocks and hydrates to `truth`."""
        with patch('generative_driver.benchmark_support.registry.pin_case', return_value=pin), \
                patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value=truth), \
                patch('generative_driver.benchmark_support.calibration.hydrate_truth',
                      side_effect=lambda unlocked: unlocked):
            return require_calibration(pin, {})

    def test_01_B14_a_tq9_shaped_record_is_admitted_and_refused_as_today(self):
        from test_benchmark_tq9_v2 import TQ9V2Tests
        manifest, record, truth, pin = TQ9V2Tests().tq9_admission_fixture()
        self.assertEqual(self.admit(pin, truth), {'ok': True, 'evidence_sha256': canonical_digest(record)})
        with patch.dict(tq9_v2.HOOKS, {'admission_valid': lambda pin, truth, record: False}), \
                self.assertRaisesRegex(ValueError, self.REFUSED):
            self.admit(pin, truth)
        record['execution_inputs'] = {}
        manifest['calibration']['evidence_sha256'] = canonical_digest(record)
        with self.assertRaisesRegex(ValueError, self.REFUSED):
            self.admit(pin, truth)

    def test_01_B14_a_family_truth_record_is_admitted_and_refused_as_today(self):
        from test_benchmark_family_calibration import FamilyAdmissionBoundaryTests
        manifest, record, truth, pin, _ = FamilyAdmissionBoundaryTests().family_admission_fixture()
        self.assertEqual(self.admit(pin, truth), {'ok': True, 'evidence_sha256': canonical_digest(record)})
        with patch.dict(sampled_sensor.HOOKS, {'admission_valid': lambda pin, truth, record: False}), \
                self.assertRaisesRegex(ValueError, self.REFUSED):
            self.admit(pin, truth)
        record['mutants'][0]['source_sha256'] = 'e' * 64
        manifest['calibration']['evidence_sha256'] = canonical_digest(record)
        with self.assertRaisesRegex(ValueError, self.REFUSED):
            self.admit(pin, truth)

    def test_01_B14_a_family_with_the_default_admission_is_refused(self):
        from test_benchmark_family_calibration import FamilyAdmissionBoundaryTests
        _, _, truth, pin, _ = FamilyAdmissionBoundaryTests().family_admission_fixture()
        hooks = {key: value for key, value in sampled_sensor.HOOKS.items() if key != 'admission_valid'}
        with patch.dict(sampled_sensor.HOOKS, hooks, clear=True), self.assertRaisesRegex(ValueError, self.REFUSED):
            self.admit(pin, truth)


class CalibrationRuleTests(unittest.TestCase):
    def fixture(self, case_id, family='a family name no rule reads'):
        """A record and manifest of `case_id`. The manifest names another family: only its case's hooks can apply."""
        from test_benchmark_family_calibration import AdmissionTests as Fixture
        manifest, record = Fixture().fixture(case_id)
        return {**manifest, 'family': family}, record

    def failures(self, manifest, record):
        return validate_calibration(manifest, record)['failures']

    def test_01_B15_required_scenarios_come_from_the_hook(self):
        coverage = 'transaction scenario coverage:a-diagnostic-original'
        self.assertIn(coverage, self.failures(*self.fixture('parameter-store-v1')))
        self.assertNotIn(coverage, self.failures(*self.fixture('sampled-sensor-v1', 'parameter-store')))
        with patch.dict(sampled_sensor.HOOKS, {'required_scenarios': frozenset({'another'})}):
            self.assertIn(coverage, self.failures(*self.fixture('sampled-sensor-v1')))

    def test_01_B15_a_mutants_extra_failed_checks_are_allowed_by_the_hook(self):
        def exceeding():
            manifest, record = self.fixture('sampled-sensor-v1')
            run = next(row for row in record['runs'] if row['id'] == 'constant')
            run['check_ids'] = ['toy/constant', 'toy/extra']
            run['checks'].append({'id': 'toy/extra', 'passed': False, 'reason': 'value mismatch'})
            record['mutants'][0]['failed_checks'] = ['toy/constant', 'toy/extra']
            return manifest, record
        self.assertIn('mutant evidence:constant', self.failures(*exceeding()))
        with patch.dict(sampled_sensor.HOOKS, {'mutant_checks_may_exceed_expected': True}):
            self.assertNotIn('mutant evidence:constant', self.failures(*exceeding()))
        self.assertIs(family_for('zephyr-modbus-v1').mutant_checks_may_exceed_expected, True)

    def test_01_B15_input_commitments_come_from_the_hook(self):
        commitment = 'input commitment inventory'
        manifest, record = self.fixture('tq9-v2')
        self.assertIn(commitment, self.failures(manifest, record))
        names = {'source', 'contract', 'recipe', 'references', 'mutations', 'build', 'analysis'}
        record['input_hashes'] = {name: 'a' * 64 for name in names}
        manifest['calibration'] = {'input_hashes': record['input_hashes']}
        self.assertNotIn(commitment, self.failures(manifest, record))
        sensor, measured = self.fixture('sampled-sensor-v1')
        self.assertIn(commitment, self.failures({**sensor, 'calibration': manifest['calibration']},
                                                {**measured, 'input_hashes': record['input_hashes']}))

    def test_01_B15_the_extra_inventory_file_comes_from_the_hook(self):
        extra = 'native/SampleSource.cs'
        self.assertIn(extra, inventory('sampled-sensor-v1'))
        self.assertNotIn(extra, inventory('parameter-store-v1'))
        with patch.dict(parameter_store.HOOKS, {'inventory_files': frozenset({'native/Another.cs'})}):
            self.assertIn('native/Another.cs', inventory('parameter-store-v1'))
        with self.assertRaisesRegex(ValueError, 'Unsupported family'):
            inventory('tq9-v2')

    def test_01_B15_required_mutants_come_from_the_hook(self):
        with patch.dict(sampled_sensor.HOOKS, {'required_mutants': frozenset({'alpha'})}):
            manifest, record = self.fixture('sampled-sensor-v1')
            self.assertIn('family mutant inventory', self.failures(manifest, record))
            record['required_mutants'] = ['alpha']
            self.assertNotIn('family mutant inventory', self.failures(manifest, record))


# Backends, not names, stay allowed; `generated` is an ordinary word in this codebase, so it matches by equality only.
EQUALITY_ONLY = {'generated'}


def core_names():
    """Every registered case's and family's name and native module, each also in its underscore form."""
    names = set()
    for case_id in registered_case_ids():
        row = registration_for(case_id)
        names |= {row.id, row.family} | ({row.native_module} if row.native_module else set())
    return names | {name.replace('-', '_') for name in names}


def docstrings(tree):
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)}


def identifiers(node):
    """The names `node` itself binds or reads, which are not string constants: every string among its fields."""
    for field in node._fields:
        value = getattr(node, field, None)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                yield item


def named_in(source, names):
    """Each string constant or identifier of `source` that names one of `names`: equal to it, or holding it unless it
    matches by equality only. Docstrings are not read."""
    tree = ast.parse(source)
    skipped = docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            texts = [node.value] if isinstance(node.value, str) and id(node) not in skipped else []
        else:
            texts = list(identifiers(node))
        found += [text for text in texts for name in names
                  if text == name or (name not in EQUALITY_ONLY and name in text)]
    return found


def module_source(root, name):
    path = root.joinpath(*name.split('.'))
    return path.with_suffix('.py') if path.with_suffix('.py').is_file() else path / '__init__.py'


class GradingCoreTests(PackageCopy, unittest.TestCase):
    PACKAGE = Path(generative_driver.__file__).parent

    def identities(self, root):
        return {case_id: grader_identity(root, case_id=case_id)['sha256'] for case_id in registered_case_ids()}

    def changed_by_editing(self, *modules):
        """The cases whose identity moves when `modules` of a copied package are edited."""
        root = self.copy_package()
        before = self.identities(root)
        for module in modules:
            path = root / 'benchmark_support' / module
            path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        after = self.identities(root)
        return {case_id for case_id in before if before[case_id] != after[case_id]}

    def test_01_B6_the_check_finds_a_name_in_constants_and_identifiers_but_not_docstrings(self):
        names = {'tq9', 'tq9_v2', 'generated', 'zephyr-modbus-v1'}
        planted = {"x = 'tq9'": ['tq9'], "x = 'the tq9 device'": ['the tq9 device'], "tq9_helper = 1": ['tq9_helper'],
                   "x.tq9_attribute": ['tq9_attribute'], "def f(tq9_argument): pass": ['tq9_argument'],
                   "from .tq9_v2 import y": ['tq9_v2', 'tq9_v2'], "x = 'generated'": ['generated'],
                   "generated = 1": ['generated'], "generated_mcp_reuse = 'generated_reuse'": [],
                   '"""A docstring of tq9."""': [], 'def f():\n    """Of tq9."""\n': []}
        for source, expected in planted.items():
            with self.subTest(source):
                self.assertEqual(sorted(set(named_in(source, names))), sorted(set(expected)))

    def test_01_B6_no_module_in_the_core_names_a_registered_case_or_family(self):
        names = core_names()
        self.assertLessEqual({'tq9', 'tq9-v2', 'tq9_v2', 'bme280', 'setup-smoke', 'setup_smoke', 'sampled-sensor-v1',
                              'sampled_sensor', 'parameter-store', 'zephyr_modbus', 'tq9_legacy', 'generated'}, names)
        offenders = {name: named_in(module_source(self.PACKAGE, name).read_text(encoding='utf-8'), names)
                     for name in grader_closure(families=()) if not name.startswith('interface_runtime')}
        self.assertEqual({name: found for name, found in offenders.items() if found}, {})

    def test_01_B7_upstream_and_native_code_lie_outside_the_core_and_move_only_the_modbus_identity(self):
        outside = ('upstream.py', 'native_firmware.py', 'native_comparison.py')
        core = grader_closure(families=())
        for module in outside:
            with self.subTest(module):
                self.assertNotIn('benchmark_support.' + module.removesuffix('.py'), core)
                self.assertEqual(self.changed_by_editing(module), {'zephyr-modbus-v1'})

    def test_01_B8_the_legacy_module_moves_only_its_case_and_the_seam_moves_every_case(self):
        self.assertEqual(self.changed_by_editing('tq9_legacy.py'), {'tq9'})
        self.assertEqual(self.changed_by_editing('families.py'), set(registered_case_ids()))

    def test_01_B9_only_the_seam_loads_code_by_name(self):
        loaders = {}
        for name in grader_closure():
            if name.startswith('interface_runtime'):
                continue
            tree = ast.parse(module_source(self.PACKAGE, name).read_text(encoding='utf-8'))
            calls = [node.func.id if isinstance(node.func, ast.Name) else node.func.attr for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))]
            if {'import_module', '__import__'} & set(calls):
                loaders[name] = sorted({'import_module', '__import__'} & set(calls))
        self.assertEqual(loaders, {'benchmark_support.families': ['import_module']})


class ProviderTests(unittest.TestCase):
    def test_01_B17_the_provider_and_every_family_load_nothing_that_decrypts(self):
        from test_device_provider import DECRYPTING_MODULES
        script = textwrap.dedent("""\
            import json, sys
            import generative_driver.benchmark_support.device_provider
            from generative_driver.benchmark_support.families import family_for
            from generative_driver.benchmark_support.registrations import registered_case_ids
            for case_id in registered_case_ids():
                family_for(case_id)
            print(json.dumps(sorted(name for name in sys.modules if name.startswith('generative_driver'))))
        """)
        loaded = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True, timeout=60,
                                env={**os.environ, **package_env()})
        modules = {name.rsplit('.', 1)[-1] for name in json.loads(loaded.stdout)}
        self.assertLessEqual({'tq9_v2', 'tq9_legacy', 'sampled_sensor', 'parameter_store', 'zephyr_modbus',
                              'generated'}, modules)
        self.assertEqual(modules & DECRYPTING_MODULES, set())


if __name__ == '__main__':
    unittest.main()

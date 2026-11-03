"""The family seam: the grading core reaches a family only through `families`, so it names none, and each family's code
moves only the identities of the cases that use it."""
import ast
import dataclasses
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import generative_driver
from benchmark_family_fixtures import toy_sample_model, toy_sensor_phase
from generative_driver.benchmark_support import sampled_sensor
from generative_driver.benchmark_support.case_assets import reuse_mission
from generative_driver.benchmark_support.emulated_actions import execute_plan
from generative_driver.benchmark_support.families import Family, family_for, family_from
from generative_driver.benchmark_support.registrations import registered_case_ids, registration_for

HOOKS = ('contract', 'observations', 'reuse_passed', 'required_mutants', 'execute', 'build_plan', 'hydrate_truth',
         'admission_valid', 'validate_phase', 'required_scenarios', 'mutant_checks_may_exceed_expected',
         'input_commitments', 'inventory_files', 'instrument_units', 'public_observation', 'development_conditions',
         'start_session', 'validate_execution')
REQUIRED = ('contract', 'observations', 'reuse_passed', 'required_mutants')
SOURCE = Path(generative_driver.__file__).parent / 'benchmark_support'
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
        """Each required hook of `family` as a call, and its plan and step executor, for what a case without them does."""
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
    """An emulator session of a fixture device: what `execute_plan` and the evaluator ask of one, and nothing on a wire."""
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
        return {'ok': True, 'outputs': {'temperature': -5, 'sequence': 3}, 'units': {'temperature': 'degC', 'sequence': 'count'}}
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

    def frozen_package(self):
        package = self.root / 'package'
        (package / 'driver').mkdir(parents=True)
        (package / 'interface_runtime').mkdir()
        (package / 'driver' / 'model.json').write_text('{}\n', encoding='utf-8')
        (package / 'interface_runtime' / 'engine.py').write_text('# runtime\n', encoding='utf-8')
        return package

    def grade_frozen(self, pin, truth, calls):
        from generative_driver.benchmark_support.evaluator import reference_execute
        from generative_driver.benchmark_support.integrity import digest
        package = self.frozen_package()
        with patch('generative_driver.benchmark_support.native.NativeSession.start', return_value=ToySession()), \
                patch('generative_driver.benchmark_support.emulated_actions.canonical_package_invoker',
                      return_value=toy_invoke(calls)):
            return reference_execute(pin, truth, {}, renode=Path('/toy'), image=Path('/toy'), output_dir=self.root / 'final',
                                     package=package, expected_package_sha256=digest(package),
                                     healthy_device_failures=True)

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
                expected = execute_plan(ToySession(), toy_invoke(direct), family.build_plan(pin, truth, phase), contract,
                                        self.root / 'direct.json', family.observations, 0)
                self.assertEqual(records, expected)
                self.assertEqual((calls, direct), ([phase + '-task'], [phase + '-task']))

    def test_01_B5_a_family_that_declares_execute_runs_its_own(self):
        own = lambda *call, **options: 'its own'
        family = family_from(graded_case(), {**{key: value for key, value in complete_hooks().items() if key != 'build_plan'},
                                             'execute': own})
        self.assertIs(family.execute, own)

    def test_01_B5_grading_and_the_diagnostic_path_have_one_call_path(self):
        from generative_driver.benchmark_support import evaluator, reference_calibration
        from generative_driver.benchmark_support.reference_calibration import run_reference
        for module in (evaluator, reference_calibration):
            tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
            calls = [node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
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


if __name__ == '__main__':
    unittest.main()

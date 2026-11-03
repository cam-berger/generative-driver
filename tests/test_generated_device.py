"""What a valid generated device is (docs/generated-device-interface.md), checked through `validate_device` on the
hand-written fixture device, never generator output."""
import copy
import unittest

from generated_fixtures import FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, fixture_mission
from generative_driver.benchmark_support.generated_device import validate_device

DEVICE = {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}


class ValidDeviceTests(unittest.TestCase):
    def assert_refused(self, rows):
        """Each row (name, edit of the answer key, pattern) makes the fixture device invalid, for the reason `pattern`
        names. The unedited device is valid first, so a refusal comes from the row's edit alone."""
        mission = fixture_mission(DEVICE['id'])
        validate_device(DEVICE, image=FIXTURE_IMAGE, mission=mission, key=FIXTURE_ANSWER_KEY)
        for name, edit, pattern in rows:
            key = copy.deepcopy(FIXTURE_ANSWER_KEY)
            edit(key)
            with self.subTest(name), self.assertRaisesRegex(ValueError, pattern):
                validate_device(DEVICE, image=FIXTURE_IMAGE, mission=mission, key=key)

    def assert_mission_refused(self, rows):
        """Each row (name, edit of the public mission, pattern) makes the fixture device's inputs invalid, for the reason
        `pattern` names, whether or not its answer key is checked too."""
        validate_device(DEVICE, image=FIXTURE_IMAGE, mission=fixture_mission(DEVICE['id']))
        for name, edit, pattern in rows:
            mission = fixture_mission(DEVICE['id'])
            edit(mission)
            for key in (None, FIXTURE_ANSWER_KEY):
                with self.subTest(name, key=key is not None), self.assertRaisesRegex(ValueError, pattern):
                    validate_device(DEVICE, image=FIXTURE_IMAGE, mission=mission, key=key)

    def test_a_generated_mission_states_effects_only(self):
        task = lambda mission, name='set_mode': mission['tasks'][name]
        self.assert_mission_refused([
            ('another case', lambda m: m.update(case_id='tq9-v2'), 'mission identity'),
            ('an input range', lambda m: task(m)['inputs']['mode'].update(minimum=0), 'input mode'),
            # Capability binding takes no bytes, and takes a string only from a set the driver declares.
            ('a bytes input', lambda m: task(m)['inputs']['mode'].update(type='bytes'), 'input mode'),
            ('a string input', lambda m: task(m)['inputs']['mode'].update(type='string'), 'input mode'),
            ('an output without a unit', lambda m: task(m, 'get_mode')['outputs']['mode'].update(unit=None), 'output mode'),
            ('an output with a range', lambda m: task(m, 'get_mode')['outputs']['mode'].update(maximum=3), 'output mode'),
            ('operating conditions', lambda m: m.update(operating_conditions=['Keep it cool.']), 'operating conditions'),
            ('a sampling requirement', lambda m: task(m, 'get_mode').update(sampling='acquire'), 'sampling')])

    def test_the_answer_key_agrees_with_the_mission(self):
        step = lambda key, sequence=0, index=0: key['sequences'][sequence]['steps'][index]
        mapping = lambda candidate: candidate['capabilities']['tasks']

        def without_output(key):
            # The mutants expect its check to fail, and a key's mutants expect only checks it has.
            step(key, 0, 1).pop('outputs')
            for mutant in key['mutants']:
                mutant['expected_failed_checks'] = [check for check in mutant['expected_failed_checks']
                                                    if check != 's1/2/out:mode']
        self.assert_refused([
            ('a step that calls a task the mission lacks', lambda key: step(key, 0, 1).update(task='reboot'),
             'steps call'),
            # The fixture's two ping steps now read the mode, so no step calls ping.
            ('a mission task that no step calls',
             lambda key: [step(key, 1, index).update(task='get_mode') for index in (0, 3)], 'steps call'),
            ('an input the task does not declare', lambda key: step(key).update(inputs={'mode': 9, 'speed': 1}),
             'declared inputs'),
            ('a missing input', lambda key: step(key).update(inputs={}), 'declared inputs'),
            ('a number for an integer input', lambda key: step(key).update(inputs={'mode': 9.5}), 'declared inputs'),
            ('a string input', lambda key: step(key).update(inputs={'mode': '9'}), 'declared inputs'),
            ('an output the task does not declare',
             lambda key: step(key, 0, 1)['outputs'].update(speed={'value': 1, 'tolerance': 0, 'unit': '1'}),
             'declared outputs'),
            ('a value step without its task\'s output', without_output, 'declared outputs'),
            ('an output in another unit', lambda key: step(key, 0, 1)['outputs']['mode'].update(unit='count'),
             'declared outputs'),
            ('a reference that maps fewer tasks', lambda key: mapping(key['references']['b']).pop('ping'),
             'maps exactly'),
            ('a mutant that maps another task',
             lambda key: mapping(key['mutants'][0]).update(reboot=mapping(key['mutants'][0])['ping']), 'maps exactly')])

    def test_a_tasks_steps_share_grants_and_a_refusal_or_silence_has_a_value_beside_it(self):
        step = lambda key, sequence=0, index=0: key['sequences'][sequence]['steps'][index]

        def refusals_only(key):
            # Every set_mode step a refusal: no value step proves its grants.
            for sequence, index in ((0, 2), (1, 1)):
                step(key, sequence, index).update(answer='refusal', refusal_rx_hex=b'ERR\n'.hex())
        self.assert_refused([
            # The runtime refuses an ungranted effect before sending, which grades as a matching refusal: without its
            # task's grant, this refusal step would pass any driver.
            ('a refusal step without its task\'s grant', lambda key: step(key).update(grants=[]), 'same grants'),
            ('steps of one task with other grants', lambda key: step(key, 0, 2).update(grants=['write', 'actuate']),
             'same grants'),
            ('a task that only refuses', refusals_only, 'value step'),
            ('a task that is only silent', lambda key: step(key, 1, 3).update(answer='no_reply'), 'value step')])

    def test_cells_and_sequences_carry_no_design_in_their_names(self):
        # A verdict publishes check ids built from these names, so they are numbered.
        def swap(key):
            key['sequences'][0]['id'], key['sequences'][1]['id'] = 's2', 's1'
        self.assert_refused([
            ('a cell named for its meaning', lambda key: key['state'].update(mode=key['state'].pop('c1')), 'c1 to c1'),
            ('cells numbered from 0', lambda key: key['state'].update(c0=key['state'].pop('c1')), 'c1 to c1'),
            ('cells that skip a number', lambda key: key['state'].update(c3=dict(key['state']['c1'])), 'c1 to c2'),
            ('a sequence named for its meaning', lambda key: key['sequences'][0].update(id='overflow'), 'named s1'),
            ('sequences out of order', swap, 'named s1')])


if __name__ == '__main__':
    unittest.main()

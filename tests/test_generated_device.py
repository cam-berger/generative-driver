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

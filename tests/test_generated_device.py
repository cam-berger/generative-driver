"""What a valid generated device is (docs/generated-device-interface.md), checked through `validate_device` on the
hand-written fixture device, never generator output."""
import copy
import unittest

from generated_fixtures import FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, fixture_mission
from generative_driver.benchmark_support.generated_device import validate_device

DEVICE = {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}


def retake(key, identifier, **changes):
    """Change the task or answer of transition `identifier` of `key` and of every step that takes it alike, so that only
    the rule a test aims at breaks. A step that becomes a refusal records the fixture's refusal, and one that stops
    being a refusal drops it."""
    next(transition for transition in key['machine']['transitions'] if transition['id'] == identifier).update(changes)
    for sequence in key['sequences']:
        for step in sequence['steps']:
            if step['transition'] == identifier:
                step.update(changes)
                if step['answer'] == 'refusal':
                    step.setdefault('refusal_rx_hex', b'MODE ERR\n'.hex())
                else:
                    step.pop('refusal_rx_hex', None)


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
            ('a step that calls a task the mission lacks', lambda key: retake(key, 't2', task='reboot'), 'steps call'),
            # The fixture's two ping transitions now read the mode, so no step calls ping.
            ('a mission task that no step calls',
             lambda key: [retake(key, identifier, task='get_mode') for identifier in ('t5', 't8')], 'steps call'),
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
        # Every set_mode transition a refusal: no value step proves its grants.
        refusals_only = lambda key: [retake(key, identifier, answer='refusal') for identifier in ('t3', 't6')]
        self.assert_refused([
            # The runtime refuses an ungranted effect before sending, which grades as a matching refusal: without its
            # task's grant, this refusal step would pass any driver.
            ('a refusal step without its task\'s grant', lambda key: step(key).update(grants=[]), 'same grants'),
            ('steps of one task with other grants', lambda key: step(key, 0, 2).update(grants=['write', 'actuate']),
             'same grants'),
            ('a task that only refuses', refusals_only, 'value step'),
            ('a task that is only silent', lambda key: retake(key, 't8', answer='no_reply'), 'value step')])

    def test_the_hidden_sequences_walk_every_transition_of_the_declared_machine(self):
        step = lambda key, sequence=0, index=0: key['sequences'][sequence]['steps'][index]
        machine = lambda key: key['machine']
        transition = lambda key, identifier: next(t for t in key['machine']['transitions'] if t['id'] == identifier)

        def starts_away(key):
            # s2 starts with a silent ping, but from the state that mode 3 holds.
            machine(key)['transitions'].append({'id': 't9', 'from': 'm3', 'task': 'ping', 'answer': 'no_reply',
                                                'to': 'm3'})
            step(key, 1, 0).update(transition='t9')
        self.assert_refused([
            ('version 1', lambda key: key.update(schema='generated-answer-key/1'), 'schema'),
            ('no machine', lambda key: key.pop('machine'), 'fields of the answer key'),
            ('a step that names no transition', lambda key: step(key).pop('transition'), 'fields of the step s1/1'),
            ('a step that names an unknown transition', lambda key: step(key).update(transition='t0'),
             'takes a declared transition'),
            ('an initial state that is not the reset', lambda key: machine(key).update(initial='m1'),
             'initial state'),
            ('two states with the same cells', lambda key: machine(key)['states'].update(m4={'c1': 2}),
             'distinct'),
            ('a transition to an unknown state', lambda key: transition(key, 't3').update(to='m9'), 'among the states'),
            ('a transition with a duplicate id', lambda key: machine(key)['transitions'].append(
                dict(transition(key, 't3'))), 'distinct ids'),
            ('a step whose task is not its transition\'s', lambda key: step(key, 0, 1).update(transition='t1'),
             'task and answer'),
            ('a sequence that starts away from the initial state', starts_away, 'starts where'),
            ('a step whose transition starts elsewhere', lambda key: step(key, 0, 3).update(transition='t7'),
             'starts where'),
            ('a step that ends in another state', lambda key: step(key, 0, 2).update(state={'c1': 3}), 'end state'),
            ('a transition that no step takes', lambda key: machine(key)['transitions'].append(
                {'id': 't9', 'from': 'm1', 'task': 'set_mode', 'answer': 'value', 'to': 'm2'}), 'every transition')])

    def test_a_read_task_never_changes_the_state(self):
        # The runtime runs a driver's identity operation, a read, before every call: a read that changed the state would
        # shift every later one.
        def reading_moves(key):
            # get_mode, a read task, now takes mode 1 to mode 2.
            next(t for t in key['machine']['transitions'] if t['id'] == 't4').update(to='m2')
            key['sequences'][0]['steps'][3]['state'] = {'c1': 2}
        self.assert_refused([('a read that moves the device', reading_moves, 'same state')])

    def test_a_device_refuses_something_and_a_refusal_changes_nothing(self):
        def refusing_moves(key):
            # A last set_mode in s1 takes mode 1 back to mode 2, though the device refuses it.
            key['machine']['transitions'].append({'id': 't9', 'from': 'm1', 'task': 'set_mode', 'answer': 'refusal',
                                                  'to': 'm2'})
            key['sequences'][0]['steps'].append({'task': 'set_mode', 'inputs': {'mode': 9}, 'grants': ['write'],
                                                 'answer': 'refusal', 'state': {'c1': 2}, 'transition': 't9',
                                                 'refusal_rx_hex': b'MODE ERR\n'.hex()})
        no_line_feed = lambda key: key['sequences'][0]['steps'][0].update(refusal_rx_hex=b'MODE ERR'.hex())
        self.assert_refused([
            # Hidden sequences include out-of-range values, so passing on the device's refusals is graded.
            ('no refusal step', lambda key: retake(key, 't1', answer='value'), 'refusal step'),
            # A refusal matches whether the device or the driver made it, and a driver's own refusal sends nothing.
            ('a refusal that changes the state', refusing_moves, 'refusal transition t9'),
            # Every correct driver must capture the same bytes, so a reply is self-delimiting.
            ('a text refusal without its line feed', no_line_feed, 'line feed')])
        key = copy.deepcopy(FIXTURE_ANSWER_KEY)
        no_line_feed(key)  # only text lines end with a line feed
        validate_device({**DEVICE, 'framing': 'binary-frames'}, image=FIXTURE_IMAGE,
                        mission=fixture_mission(DEVICE['id']), key=key)

    def test_the_references_forward_refused_requests_and_differ_in_identity_and_capture(self):
        model = lambda key, name: key['references'][name]['model']

        def b_declines(key):
            # B's own bounds refuse mode 9, so it would never show what the device sends back.
            model(key, 'b')['operations']['select']['parameters']['mode']['maximum'] = 3

        def one_receive_mode(key):
            model(key, 'b')['operations']['select']['steps'][0]['rx'] = {'mode': 'until', 'delimiter_hex': '0a',
                                                                         'max_bytes': 32}
        self.assert_refused([
            ('a reference that declines a refused request', b_declines, 'forwards'),
            # B identifies the device with the read A identifies it with, so only one read command is shown harmless.
            ('one identity command', lambda key: model(key, 'b')['identity'].update(operation='query'), 'identity'),
            ('two references alike', lambda key: key['references'].update(b=copy.deepcopy(key['references']['a'])),
             'identity'),
            ('one receive mode for a refused request', one_receive_mode, 'until and with lines')])
        key = copy.deepcopy(FIXTURE_ANSWER_KEY)
        one_receive_mode(key)  # a correct driver must read a whole binary reply anyway
        validate_device({**DEVICE, 'framing': 'binary-frames'}, image=FIXTURE_IMAGE,
                        mission=fixture_mission(DEVICE['id']), key=key)

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

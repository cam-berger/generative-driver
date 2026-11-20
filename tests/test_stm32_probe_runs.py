"""Runs whose binding declares an ST-LINK debug probe.

Feature 01 of the stm32-acquire Run (docs/features/stm32-acquire/01-stm32-flash-read.md), 01-B3 to 01-B8 and 01-B15,
at the configurator. The operator declares the probe as `debug_probe` in a run's binding. The configurator takes it
out of the binding, leases it, assigns the acquire worker the tool that reads an STM32's flash, and supplies that tool
the program, the probe and the reset permission.

In-process runs with the scripted workers and the stand-in serial device of `tests/input_path_fixtures.py`, and the
stand-in program of `tests/stm32_programmer_stand_in.py`. One test builds its configurator as the service builds one,
so that the tool runs in a process the configurator can stop. Temporary homes only; no model, no hardware and no
program of ST's. Nothing here is physical evidence.
"""
import contextlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import stm32_programmer_stand_in as stand_in
from input_path_fixtures import RunCase, Worker
from test_stm32_flash_tool import outside_the_list

TOOL = 'acquire_stm32_flash'
STLINK = {'kind': 'stlink'}
SERIAL_PORT = '/dev/stand-in-serial'
GOAL = 'Recover the interface from the acquired evidence'
MALFORMED = 'binding.debug_probe must be an object with kind "stlink" and an optional serial_number'
BAD_SERIAL = 'binding.debug_probe.serial_number must be 1 to 64 letters and digits'
NEEDS_PROGRAM = ('binding.debug_probe needs STM32CubeProgrammer: run generative-driver configure-program --program '
                 'stm32-programmer --command PATH')
SUPPLIED = 'binding.debug_probe is for a run without a supplied device'


class ProbeCase(RunCase):
    """Generic runs in temporary homes, with the stand-in program recording its launches in `self.state`. When a
    test ends, every launch it made is held to the list of 01-B11."""

    def setUp(self):
        super().setUp()
        self.state = self.root / 'stand-in'
        self.state.mkdir()
        environment = patch.dict(os.environ, {stand_in.STATE: str(self.state)})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.hold_every_launch_to_the_list)

    def hold_every_launch_to_the_list(self):
        for launch in stand_in.launches(self.state):
            self.assertEqual(outside_the_list(launch['arguments'], Path(launch['cwd']) / 'flash.bin'), [], launch)

    def next_home(self):
        """The home the next `run_with` gives its run."""
        return self.root / ('home-' + str(len(self.homes)))

    def configure(self, command=None, home=None):
        """Save `command`, the stand-in unless given, as the program of `home`, the next run's unless given."""
        from generative_driver.setup import configure_program
        configure_program('stm32-programmer', command or stand_in.COMMAND, home or self.next_home())

    @contextlib.contextmanager
    def workers(self, controller, script=None):
        """While the block runs, the stage workers of every run of `controller` are those of `script`, which maps a
        stage to `work(worker)`, as `run_with` scripts one run. A stage without one does nothing and reports
        completed. A worker has, as `cancel`, the event that is set when its run is to stop."""
        def execute(request, config, cancel):
            work = (script or {}).get(request.stage)
            worker = Worker(controller, request)
            worker.cancel = cancel
            try:
                report = work(worker) if work else None
            except Exception as error:  # The test fails on it in `no_worker_raised`, not on the run it stopped.
                self.worker_errors.append((request.stage, error))
                raise
            return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0, 'usage': None,
                    'report': report or {'status': 'completed', 'summary': 'scripted ' + request.stage,
                                         'artifacts': [], 'checks': [], 'unresolved': []}}
        with patch('generative_driver.configurator.execute', execute):
            yield

    def configurator(self, home, **options):
        """A configurator of `home`, closed when the test ends."""
        from generative_driver.configurator import Controller
        made = Controller(home, **options)
        self.addCleanup(made.close)
        return made

    @staticmethod
    def start(controller, **params):
        """Start a generic run of `params` on `controller`."""
        return controller.call('start', {'goal': GOAL, 'executor_config': {'command': ['never-launched']}, **params})


class StartTests(ProbeCase):
    """A start that declares a probe is checked before a run exists (01-B3)."""

    def refused(self, controller, text, **params):
        """Hold a start of `params` to the refusal `text`, with no run made."""
        self.requests = getattr(self, 'requests', 0) + 1
        request = 'request-' + str(self.requests)
        with self.assertRaises(ValueError) as refusal:
            self.start(controller, request_id=request, **params)
        self.assertEqual(str(refusal.exception), text)
        with self.assertRaises(ValueError) as unknown:
            controller.call('status', {'request_id': request})
        self.assertEqual(str(unknown.exception), 'Unknown request_id')

    def test_01_B3_stm32_a_malformed_declaration_is_refused_before_a_run_exists(self):
        home = self.root / 'home'
        self.configure(home=home)
        controller = self.configurator(home)
        malformed = ('stlink', ['stlink'], None, 7, {}, {'serial_number': '0670FF3632'}, {'kind': 'jlink'},
                     {'kind': 'STLINK'}, {**STLINK, 'index': 0}, {**STLINK, 'serial_number': 'A1', 'port': 'SWD'})
        serials = ('', 'A B', 'A-1', 'a' * 65, 'É1', 5, None, ['A1'])
        with self.workers(controller):
            for declared in malformed:
                with self.subTest(debug_probe=declared):
                    self.refused(controller, MALFORMED, binding={'port': SERIAL_PORT, 'debug_probe': declared})
            for serial in serials:
                with self.subTest(serial_number=serial):
                    self.refused(controller, BAD_SERIAL,
                                 binding={'port': SERIAL_PORT, 'debug_probe': {**STLINK, 'serial_number': serial}})
            # A refused start holds nothing: the same binding without the probe starts at once.
            started = self.start(controller, binding={'port': SERIAL_PORT})
            self.assertIs(started['ok'], True)
            self.stopped(controller, started)

    def test_01_B3_stm32_a_declaration_needs_the_configured_program_and_its_file(self):
        binding = {'port': SERIAL_PORT, 'debug_probe': STLINK}
        missing = str(self.root / 'STM32CubeProgrammer' / 'bin' / 'STM32_Programmer_CLI')
        homes = {'no configuration': None, 'a program whose file is missing': [missing],
                 'a folder at the path': [str(self.root)]}
        for label, command in homes.items():
            with self.subTest(label):
                home = self.root / ('home ' + label)
                if command:
                    self.configure(command, home)
                controller = self.configurator(home)
                with self.workers(controller):
                    self.refused(controller, NEEDS_PROGRAM, binding=binding)
                    # A run without a declared probe needs no program.
                    started = self.start(controller, binding={'port': SERIAL_PORT})
                    self.assertIs(started['ok'], True)
                    self.stopped(controller, started)
                    # Once the program is configured and its file is there, the same start is accepted.
                    self.configure(home=home)
                    for declared in (STLINK, {**STLINK, 'serial_number': '0670FF3632'},
                                     {**STLINK, 'serial_number': 'a' * 64}):
                        started = self.start(controller, binding={'port': SERIAL_PORT, 'debug_probe': declared})
                        self.assertEqual((started['ok'], started['duplicate']), (True, False))
                        result = self.stopped(controller, started)
                        self.assertEqual(result['request']['binding'], {'port': SERIAL_PORT, 'debug_probe': declared})

    def test_01_B3_stm32_a_supplied_device_takes_no_probe_whatever_else_is_wrong_with_the_declaration(self):
        controller = self.configurator(self.root / 'home')
        supplied = {'device': {}, 'firmware': {}, 'mission': {}}
        with self.workers(controller):
            for declared in (STLINK, 'stlink', {**STLINK, 'serial_number': 'A B'}):
                with self.subTest(debug_probe=declared):
                    self.refused(controller, SUPPLIED, binding={'debug_probe': declared}, **supplied)


OWNED_BINDING = 'Device binding is already owned by another run or session'


class BindingTests(ProbeCase):
    """After the start, everything sees the binding without the probe (01-B5)."""

    def outline(self):
        """What the run's stages did, by the kind and reason of each stage event."""
        return [(event['kind'], event['data'].get('stage'), event['data'].get('reason'))
                for event in self.events('stage.assigned', 'stage.accepted', 'stage.skipped', 'run.completed',
                                         'run.blocked', 'run.failed')]

    def serial_run(self, binding, **params):
        """A run on a stand-in serial device that resets when opened: the acquire worker listens, and the interpret
        worker, device first, runs a model's read on the device. Returns what the workers saw, the run's result, the
        binding of each assignment and the outline of the stages."""
        from input_path_fixtures import fake_serial
        from test_input_paths import serial_model
        seen = {}

        def acquire(worker):
            if seen:
                return  # A worker of a run this one started, below.
            seen['run'] = worker.identity['run_id']
            seen['listen'] = worker.tool('acquire_serial_listen', seconds=0.3)
            # The device's lease is held under the binding without the probe.
            for port in (SERIAL_PORT, '/dev/another-serial'):
                try:
                    seen[port] = self.start(worker.controller, binding={'port': port})
                    self.stopped(worker.controller, seen[port])
                except ValueError as refused:
                    seen[port] = str(refused)

        def interpret(worker):
            if (worker.identity or {}).get('run_id') != seen['run']:
                return
            worker.write('model.json', serial_model())
            seen['execute'] = worker.tool('interface_execute', model_dir=str(worker.workspace), operation='identify')
            seen['approved'] = worker.request.approve_scoped_tools
        self.configure()
        device = fake_serial(boot=b'DEMO-42 booting\r\n', replies={b'ID?': b'DEMO-42\n'})
        with patch.dict(sys.modules, {'serial': device}):
            result = self.run_with({'acquire': acquire, 'interpret': interpret}, binding=binding,
                                   effects=['read', 'reset'], **params)
        bindings = [event['data']['binding'] for event in self.events('stage.assigned')]
        return seen, result, bindings, self.outline()

    def test_01_B5_stm32_listening_a_device_call_and_bound_device_approval_work_as_without_a_probe(self):
        for approval in ({}, {'scoped_tool_approval': 'bound-device'}):
            with self.subTest(approval=approval):
                plain, plain_result, plain_bindings, plain_outline = self.serial_run({'port': SERIAL_PORT}, **approval)
                declared = {'port': SERIAL_PORT, 'debug_probe': {**STLINK, 'serial_number': '0670FF3632'}}
                seen, result, bindings, outline = self.serial_run(declared, **approval)
                for observed in (plain, seen):
                    self.assertIs(observed['listen'].get('available'), True, observed['listen'])
                    self.assertIsInstance(observed['execute'], dict, observed['execute'])
                    self.assertIs(observed['execute'].get('ok'), True, observed['execute'])
                    self.assertEqual(observed['approved'], bool(approval))
                    self.assertEqual(observed[SERIAL_PORT], OWNED_BINDING)
                    self.assertIs(observed['/dev/another-serial']['ok'], True)
                # Every assignment holds the binding without the probe, and the stages go as they go without one.
                self.assertEqual(len(bindings), len(plain_bindings))
                self.assertEqual(bindings, [{'port': SERIAL_PORT}] * len(bindings))
                self.assertEqual(outline, plain_outline)
                self.assertEqual((result['status'], result['reason']), (plain_result['status'], plain_result['reason']))
                # The request is given back as it was sent.
                self.assertEqual(result['request']['binding'], declared)

    def test_01_B5_stm32_a_binding_that_holds_only_a_probe_is_a_run_with_no_device_binding(self):
        self.run_with({})
        plain_outline = self.outline()
        self.configure()
        result = self.run_with({}, binding={'debug_probe': STLINK})
        self.assertEqual([event['data']['binding'] for event in self.events('stage.assigned')],
                         [{}] * len(self.events('stage.assigned')))
        self.assertTrue(self.events('stage.assigned'))
        # No device binding: interpretation is skipped for want of an image, never run device first.
        self.assertEqual(self.outline(), plain_outline)
        self.assertEqual(result['request']['binding'], {'debug_probe': STLINK})

    def test_01_B5_stm32_the_runtime_refuses_the_key_as_it_refuses_any_binding_key_it_does_not_know(self):
        # A device session checks its binding with this check of the runtime's, which does not change.
        from interface_runtime.transports import TransportError, validate_binding
        channel = {'type': 'uart', 'baudrate': 115200}
        validate_binding(channel, {'port': SERIAL_PORT})
        for key in ('debug_probe', 'some_other_key'):
            with self.subTest(key=key), self.assertRaises(TransportError) as refused:
                validate_binding(channel, {'port': SERIAL_PORT, key: STLINK})
            self.assertEqual((refused.exception.code, str(refused.exception)),
                             ('binding', 'binding has unknown or missing fields'))
        # So the binding a probe run's device call is given holds no such key.
        self.configure()
        self.run_with({}, binding={'port': SERIAL_PORT, 'debug_probe': STLINK})
        assigned = [event['data']['binding'] for event in self.events('stage.assigned')]
        self.assertEqual(assigned, [{'port': SERIAL_PORT}] * len(assigned))
        for binding in assigned:
            validate_binding(channel, binding)


TAKEN = 'The debug probe is already owned by another run'


class LeaseTests(ProbeCase):
    """Runs that declare a probe share one lease (01-B6)."""

    def setUp(self):
        super().setUp()
        self.home = self.root / 'home'
        self.configure(home=self.home)
        self.configurator_ = self.configurator(self.home)

    def test_01_B6_stm32_a_second_probe_run_is_refused_while_one_is_on_and_starts_once_it_ended(self):
        controller, seen = self.configurator_, {}
        others = ({'debug_probe': STLINK}, {'debug_probe': {**STLINK, 'serial_number': 'ANOTHER1'}},
                  {'port': SERIAL_PORT, 'debug_probe': STLINK})

        def acquire(worker):
            if seen:
                return  # A worker of a run started below.
            seen['refusals'] = []
            for binding in others:
                try:
                    seen['refusals'].append(self.start(controller, binding=binding))
                except ValueError as refused:
                    seen['refusals'].append(str(refused))
            # A run without a declared probe is not refused by the lease.
            seen['plain'] = [self.start(controller), self.start(controller, binding={'port': SERIAL_PORT})]
            for run in seen['plain']:
                self.stopped(controller, run)
        with self.workers(controller, {'acquire': acquire}):
            self.stopped(controller, self.start(controller, binding={'debug_probe': STLINK}))
            self.assertEqual(seen['refusals'], [TAKEN] * 3)
            self.assertEqual([run['ok'] for run in seen['plain']], [True, True])
            # When the first has ended, a second starts, and a third once the second has ended.
            for binding in others:
                started = self.start(controller, binding=binding)
                self.assertIs(started['ok'], True)
                self.stopped(controller, started)
        self.assertEqual(self.worker_errors, [])

    def test_01_B6_stm32_a_run_without_a_probe_takes_no_lease_and_a_resumed_probe_run_claims_it_again(self):
        import threading
        controller, order, first = self.configurator_, [], threading.Lock()
        gates = {name: threading.Event() for name in ('other', 'resumed', 'release')}

        def acquire(worker):
            with first:
                if not order:
                    # While this run without a probe is on, a probe run starts: the first took no probe lease.
                    order.append(worker.identity['run_id'])
                    order.append(self.start(controller, binding={'debug_probe': STLINK}))
                    return
            if worker.identity['run_id'] != order[1]['run_id']:
                gates['other'].wait(60)
            elif gates['resumed'].is_set():
                gates['release'].wait(60)
            else:
                worker.cancel.wait(60)  # That probe run is cancelled inside this stage, so a resume repeats it.
        with self.workers(controller, {'acquire': acquire}):
            try:
                self.stopped(controller, self.start(controller, binding={'port': SERIAL_PORT}))
                stopped = order[1]
                self.assertIs(stopped['ok'], True)
                controller.call('cancel', stopped)
                self.assertEqual(self.stopped(controller, stopped)['status'], 'cancelled')
                # Another probe run is on: the stopped one cannot resume until it has ended.
                other = self.start(controller, binding={'debug_probe': STLINK})
                with self.assertRaises(ValueError) as refused:
                    controller.call('resume', stopped)
                self.assertEqual(str(refused.exception), TAKEN)
                self.assertEqual(controller.call('status', stopped)['status'], 'cancelled')
                gates['other'].set()
                self.stopped(controller, other)
                gates['resumed'].set()
                self.assertIn(controller.call('resume', stopped)['status'], ('queued', 'running'))
                # Resumed, it holds the lease again, while its acquire worker is on.
                with self.assertRaises(ValueError) as refused:
                    self.start(controller, binding={'debug_probe': STLINK})
                self.assertEqual(str(refused.exception), TAKEN)
            finally:
                for gate in gates.values():
                    gate.set()
            self.stopped(controller, stopped)
        self.assertEqual(self.worker_errors, [])

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
# What the acquire stage of a generic run was assigned and told before this Feature.
ACQUIRE_TOOLS = ['acquire_serial_listen', 'acquire_firmware_artifact', 'acquire_datasheet', 'acquire_protocol_spec',
                 'acquire_classify', 'acquire_document']
ACQUIRE_CONTEXT = {'stage': 'acquire', 'acceptance': (
    'Report evidence and checks for this stage honestly. Observed tool evidence is handed on; every report is kept '
    'as notes and the run continues.')}
SERIAL_CONTEXT = {**ACQUIRE_CONTEXT, 'device': (
    'An operator-owned device binding is attached. The configurator supplies its connection to device tools; do not '
    'pass a port or binding.'), 'serial': (
    'Listen first with acquire_serial_listen. If it reports unsolicited output the device resets on open, and every '
    'later connection needs the operator reset grant. Granted effects: read.')}
PROBE_CONTEXT = (
    'An ST-LINK debug probe is attached to this device. If the device is an STM32 and no firmware file is among the '
    'inputs, read its firmware with acquire_stm32_flash. The configurator supplies the program and the probe. Without '
    'the reset grant the tool neither resets nor halts the chip; with it the chip is reset to connect and again after '
    'the read.')


class AssignmentTests(ProbeCase):
    """The acquire stage of a probe run is assigned the tool and told of the probe (01-B7)."""

    def stages(self, **params):
        """What each stage of a run of `params` was assigned and told, in a home whose program is configured: a
        stage's name maps to the tools of its first assignment and to the task its first worker read, None for a
        worker without one."""
        import shutil
        tasks = {}

        def note(worker):
            if worker.stage == 'interpret' and params.get('device'):
                # The stage package seals a supplied run's interpret workspace outside the run.
                self.addCleanup(shutil.rmtree, worker.workspace.parent, True)
            tasks.setdefault(worker.stage, worker.task)
        self.configure()
        self.run_with({stage: note for stage in STAGES}, **params)
        assigned = {}
        for event in self.events('stage.assigned'):
            assigned.setdefault(event['data']['stage'], event['data']['allowed_tools'])
        self.assertIn('acquire', tasks)
        return {stage: (tools, tasks.get(stage)) for stage, tools in assigned.items()}

    def assert_unchanged(self, stages, context):
        """Hold the acquire stage of `stages` to what it was assigned and told before this Feature."""
        tools, task = stages['acquire']
        self.assertEqual((tools, task['allowed_tools'], task['context']), (ACQUIRE_TOOLS, ACQUIRE_TOOLS, context))

    def assert_gained(self, stages, plain):
        """Hold `stages`, those of a run that declares a probe, to those of the same run without one, `plain`: the
        acquire stage has the tool after its tools and the one more entry of context, and nothing else differs."""
        import json
        tools, task = stages['acquire']
        self.assertEqual((tools, task['allowed_tools']), ([*ACQUIRE_TOOLS, TOOL], [*ACQUIRE_TOOLS, TOOL]))
        self.assertEqual(task['context'], {**plain['acquire'][1]['context'], 'debug_probe': PROBE_CONTEXT})
        self.assertEqual({**task, 'context': None, 'allowed_tools': None, 'inputs': None},
                         {**plain['acquire'][1], 'context': None, 'allowed_tools': None, 'inputs': None})
        self.assertEqual(list(stages), list(plain))
        for stage in stages:
            if stage != 'acquire':
                self.assertEqual(stages[stage][0], plain[stage][0], stage)
                self.assertNotIn('debug_probe', json.dumps(stages[stage][1]), stage)

    def test_01_B7_stm32_the_acquire_stage_of_a_probe_run_is_assigned_the_tool_and_told_of_the_probe(self):
        import json
        from generative_driver import configurator
        plain = self.stages(binding={'port': SERIAL_PORT})
        self.assert_unchanged(plain, SERIAL_CONTEXT)
        declared = self.stages(binding={'port': SERIAL_PORT, 'debug_probe': {**STLINK, 'serial_number': '0670FF3632'}})
        self.assert_gained(declared, plain)
        # The worker is told that a probe exists, never which.
        self.assertNotIn('0670FF3632', json.dumps(declared['acquire'][1]))
        self.assertEqual(configurator.STAGE_TOOLS['acquire'], ACQUIRE_TOOLS)

    def test_01_B7_stm32_a_run_without_a_declared_probe_and_a_supplied_run_are_assigned_what_they_were(self):
        import copy
        from supplied_device import SuppliedDevice
        from test_supplied_runs import GAUGE, TOKEN_SHA256
        from test_supplied_stages import MISSION, hand_written_firmware
        plain = self.stages()
        self.assert_unchanged(plain, ACQUIRE_CONTEXT)
        # Only a binding declares a probe: the same key beside the binding declares none.
        self.assert_unchanged(self.stages(debug_probe=STLINK), ACQUIRE_CONTEXT)
        self.assert_unchanged(self.stages(binding={'port': SERIAL_PORT}, debug_probe=STLINK), SERIAL_CONTEXT)
        # A supplied run takes no probe (01-B3), and the stage package prepares its acquire stage as before.
        device = SuppliedDevice(self.root / 'device', replies=GAUGE)
        self.addCleanup(device.close)
        tools, task = self.stages(device=device.descriptor, firmware=hand_written_firmware(self.root),
                                  mission=copy.deepcopy(MISSION), evaluation_token_sha256=TOKEN_SHA256)['acquire']
        self.assertEqual((tools, task['allowed_tools']), (['acquire_firmware_artifact'], ['acquire_firmware_artifact']))
        self.assertEqual(sorted(task['context']), ['expected_sha256', 'origin', 'source_path'])
        # A binding that holds only a probe gains the same as one that holds a device as well.
        self.assert_gained(self.stages(binding={'debug_probe': STLINK}), plain)


OWNED = 'Connection, output directory and effect grants are configurator-owned'
PROBE_OWNED = 'The debug probe and its program are configurator-owned'
SERIAL_NUMBER = '0670FF3632'


class GatewayTests(ProbeCase):
    """Through the gateway a worker gives the size alone; the configurator supplies the rest (01-B8)."""

    def probe_run(self, work, probe=STLINK, **params):
        """A run whose binding declares `probe` alone, with `work(worker)` as its acquire worker. Returns the launches
        the stand-in program recorded for it."""
        before = len(stand_in.launches(self.state))
        self.configure()
        self.run_with({'acquire': work}, binding={'debug_probe': probe}, **params)
        return stand_in.launches(self.state)[before:]

    def test_01_B8_stm32_the_workers_schema_of_the_tool_shows_the_size_alone(self):
        from generative_driver.toolkit import list_tools
        seen = {}

        def acquire(worker):
            seen['tools'] = worker.controller.call('tools', worker.identity)['tools']
        launches = self.probe_run(acquire, {**STLINK, 'serial_number': SERIAL_NUMBER}, effects=['read', 'reset'])
        # The gateway lists the assigned tools in the catalogue's order, in which the tool is the last.
        names = [tool['name'] for tool in seen['tools']]
        self.assertEqual((names[-1], sorted(names)), (TOOL, sorted([*ACQUIRE_TOOLS, TOOL])))
        schema = seen['tools'][-1]['inputSchema']
        self.assertEqual((list(schema['properties']), schema.get('required', [])), (['size_kb'], []))
        self.assertEqual(launches, [])
        # The catalogue describes what the configurator supplies as well.
        whole = [tool for tool in list_tools() if tool['name'] == TOOL][0]['inputSchema']
        self.assertEqual((sorted(whole['properties']), whole['required']),
                         (['allow_reset', 'probe_serial_number', 'program', 'run_dir', 'size_kb'],
                          ['run_dir', 'program']))

    def test_01_B8_stm32_the_configurator_supplies_the_program_the_probe_and_the_reset_permission(self):
        hotplug = ['-c', 'port=SWD', 'mode=HOTPLUG']
        normal = ['-c', 'port=SWD', 'mode=NORMAL', 'reset=SWrst']
        cases = {
            'a named probe and no reset grant': (
                {**STLINK, 'serial_number': SERIAL_NUMBER}, {}, {},
                [[*hotplug, 'sn=' + SERIAL_NUMBER, '-ob', 'displ'],
                 [*hotplug, 'sn=' + SERIAL_NUMBER, '--upload', '0x08000000', '65536', 'flash.bin']],
                {'connection': 'hotplug', 'size': 65536, 'returned_to_application': None}),
            'an unnamed probe, the reset grant and a size': (
                STLINK, {'effects': ['read', 'reset']}, {'size_kb': 16},
                [[*normal, '-ob', 'displ'], [*normal, '--upload', '0x08000000', '16384', 'flash.bin', '-rst']],
                {'connection': 'normal', 'size': 16384, 'returned_to_application': True}),
            'a grant that is not the reset grant': (
                STLINK, {'effects': ['read', 'write']}, {},
                [[*hotplug, '-ob', 'displ'], [*hotplug, '--upload', '0x08000000', '65536', 'flash.bin']],
                {'connection': 'hotplug', 'size': 65536, 'returned_to_application': None}),
        }
        for label, (probe, params, sent, expected, result) in cases.items():
            with self.subTest(label):
                seen = {}

                def acquire(worker):
                    seen['folder'] = worker.workspace / 'tools'
                    seen['result'] = worker.tool(TOOL, **sent)
                launches = self.probe_run(acquire, probe, **params)
                self.assertIsInstance(seen['result'], dict, seen['result'])
                self.assertIs(seen['result'].get('available'), True, seen['result'])
                self.assertEqual({key: seen['result'].get(key) for key in result}, result)
                # The image is under the folder the configurator gave the tool, in the stage's workspace.
                image = seen['folder'] / 'acquire' / 'interpret-input.bin'
                self.assertEqual((Path(seen['result']['artifact']), image.is_file()), (image, True))
                # The read writes under that folder too; the file's path stands here as its name.
                flash = seen['folder'] / 'acquire' / 'stm32' / 'flash.bin'
                self.assertEqual([['flash.bin' if Path(value) == flash else value for value in launch['arguments']]
                                  for launch in launches], expected)

    def test_01_B8_stm32_a_worker_cannot_send_what_the_configurator_owns(self):
        seen = {}

        def acquire(worker):
            seen['refused'] = {
                'run_dir': worker.tool(TOOL, run_dir=str(worker.workspace / 'elsewhere')),
                'allow_reset': worker.tool(TOOL, allow_reset=True),
                'allow_reset, false': worker.tool(TOOL, size_kb=16, allow_reset=False),
                'program': worker.tool(TOOL, program=[sys.executable, '-c', 'pass']),
                'probe_serial_number': worker.tool(TOOL, size_kb=16, probe_serial_number='A1'),
                'allow_reset and program': worker.tool(TOOL, allow_reset=True, program=stand_in.COMMAND),
            }
            seen['files'] = sorted(path.name for path in worker.workspace.rglob('*') if path.is_file())

        def plain(worker):
            seen['unassigned'] = worker.tool(TOOL)
        launches = self.probe_run(acquire, effects=['read', 'reset'])
        self.assertEqual({sent: str(answer) for sent, answer in seen['refused'].items()}, {
            'run_dir': OWNED, 'allow_reset': OWNED, 'allow_reset, false': OWNED, 'program': PROBE_OWNED,
            'probe_serial_number': PROBE_OWNED, 'allow_reset and program': OWNED})
        self.assertEqual([type(answer) for answer in seen['refused'].values()], [ValueError] * 6)
        # A refused call launches nothing and leaves no file.
        self.assertEqual((launches, seen['files']), ([], []))
        # A run without a declared probe is not assigned the tool, whatever its home has configured.
        self.configure()
        self.run_with({'acquire': plain})
        self.assertEqual(str(seen['unassigned']), 'Tool is not assigned to this worker')


class ProgramKeptTests(ProbeCase):
    """A run launches the program's command it started with (01-B4)."""

    def test_01_B4_stm32_a_configure_program_after_the_start_does_not_change_what_the_run_launches(self):
        # Another program: it leaves a mark that it was started, and answers as a probe without a controller.
        other = self.root / 'another program.py'
        other.write_text('import sys\nfrom pathlib import Path\n'
                         "Path(__file__).with_name('started').write_text('started', encoding='utf-8')\n"
                         'sys.exit(1)\n', encoding='utf-8')
        mark = other.with_name('started')
        seen = {}

        def reconfigure_then_read(worker):
            self.configure([sys.executable, str(other)], self.homes[-1])
            seen['first'] = worker.tool(TOOL)

        def read(worker):
            seen['second'] = worker.tool(TOOL)
        self.configure()
        self.run_with({'acquire': reconfigure_then_read}, binding={'debug_probe': STLINK})
        self.assertIs(seen['first'].get('available'), True, seen['first'])
        self.assertEqual([launch['kind'] for launch in stand_in.launches(self.state)], ['identify', 'read'])
        self.assertFalse(mark.exists())
        # The configuration did change: a run started after it launches the other program.
        self.configure([sys.executable, str(other)])
        self.run_with({'acquire': read}, binding={'debug_probe': STLINK})
        self.assertEqual((seen['second'].get('available'), seen['second'].get('reason')),
                         (False, 'no controller answered through the debug probe'))
        self.assertTrue(mark.exists())
        self.assertEqual(len(stand_in.launches(self.state)), 2)


class WholeRunTests(ProbeCase):
    """A whole run reads the flash and interprets exactly the image read (01-B15)."""

    def test_01_B15_stm32_the_acquire_handoff_carries_the_image_and_interpret_receives_exactly_it(self):
        import hashlib
        seen = {}

        def acquire(worker):
            seen['read'] = worker.tool(TOOL)

        def interpret(worker):
            seen['files'] = sorted(path.relative_to(worker.workspace).as_posix()
                                   for path in worker.workspace.rglob('*') if path.is_file())
            seen['image'] = (worker.workspace / 'image.bin').read_bytes()
        self.configure()
        result = self.run_with({'acquire': acquire, 'interpret': interpret},
                               binding={'debug_probe': {**STLINK, 'serial_number': SERIAL_NUMBER}})
        image = stand_in.image(65536)
        sha256 = hashlib.sha256(image).hexdigest()
        self.assertIs(seen['read'].get('available'), True, seen['read'])
        # The gateway hashed the four files when the tool returned. The handoff carries its own copy of each, named
        # with its place before the file's name, with that hash.
        [finished] = [event['data'] for event in self.events('tool.finished') if event['data']['name'] == TOOL]
        observed = {Path(path).name: digest for path, digest in finished['artifacts'].items()}
        self.assertEqual(sorted(observed), ['identify.txt', 'interpret-input.bin', 'provenance.json', 'read.txt'])
        self.assertEqual(observed['interpret-input.bin'], sha256)
        [handoff] = self.handoffs(result, 'acquire')
        self.assertEqual({Path(artifact['path']).name.partition('-')[2]: artifact['sha256']
                          for artifact in handoff['artifacts']}, observed)
        [carried] = [Path(artifact['path']) for artifact in handoff['artifacts'] if artifact['sha256'] == sha256]
        self.assertEqual(carried.read_bytes(), image)
        self.assertEqual([check['passed'] for check in handoff['checks']
                          if check['name'] == 'observed_acquisition_hashes'], [True])
        # Interpretation is assigned that image, and no other firmware, and its worker reads the bytes read.
        [assignment] = self.assigned('interpret')
        sealed = {Path(path).relative_to(assignment['workspace']).as_posix(): digest
                  for path, digest in assignment['inputs'].items()}
        self.assertEqual({name: digest for name, digest in sealed.items() if name.endswith(('.bin', '.elf'))},
                         {'image.bin': sha256})
        self.assertEqual([name for name in seen['files'] if name.endswith(('.bin', '.elf'))], ['image.bin'])
        self.assertEqual(seen['image'], image)
        self.assertFalse([event for event in self.events('stage.skipped') if event['data']['stage'] == 'interpret'])


class StoppedCallTests(ProbeCase):
    """A read the configurator stops makes no return launch, and the program ends with the call (01-B10). The
    configurator is built as the service builds one, so the tool runs in a process of its own."""

    def test_01_B10_stm32_a_call_the_configurator_stops_makes_no_return_launch(self):
        import socket
        import time
        home = self.root / 'home'
        self.configure(home=home)
        controller = self.configurator(home, for_service=True)
        stand_in.steer(self.state, outlast={'read': 120})
        seen = {}

        def acquire(worker):
            seen['folder'] = worker.workspace / 'tools' / 'acquire'
            seen['reply'] = worker.tool(TOOL)
        with self.workers(controller, {'acquire': acquire}):
            run = self.start(controller, binding={'debug_probe': STLINK}, effects=['read', 'reset'])
            until = time.monotonic() + 60
            while [launch['kind'] for launch in stand_in.launches(self.state)] != ['identify', 'read']:
                self.assertLess(time.monotonic(), until, stand_in.launches(self.state))
                time.sleep(.05)
            self.assertIn('mode=NORMAL', stand_in.launches(self.state)[-1]['arguments'])
            controller.call('cancel', run)
            result = self.stopped(controller, run)
        self.assertEqual(self.worker_errors, [])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual((seen['reply'].get('ok'), seen['reply'].get('abandoned')), (False, 'cancelled'), seen['reply'])
        # No launch returns the chip to its application: under the grant it can stay halted until it is reset.
        launches = stand_in.launches(self.state)
        self.assertEqual([launch['kind'] for launch in launches], ['identify', 'read'])
        # The program was stopped with the call: nothing listens on the port its read launch held.
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', launches[-1]['listening']), timeout=5).close()
        self.assertFalse((seen['folder'] / 'interpret-input.bin').exists())


if __name__ == '__main__':
    unittest.main()

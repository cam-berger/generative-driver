"""The acquire tool that reads an STM32's flash through STM32CubeProgrammer's command-line program.

Feature 01 of the stm32-acquire Run (docs/features/stm32-acquire/01-stm32-flash-read.md), 01-B9 to 01-B14, by
`toolkit.call_tool`. The program is the stand-in of `tests/stm32_programmer_stand_in.py`: it reads no chip, and what
it prints is remembered, not observed. So these tests show how the tool starts the program and what it does with the
program's files and exit codes. They give no physical evidence.

Temporary folders only; no model, no hardware and no program of ST's.
"""
import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import stm32_programmer_stand_in as stand_in

TOOL = 'acquire_stm32_flash'
HOTPLUG = ['-c', 'port=SWD', 'mode=HOTPLUG']
NORMAL = ['-c', 'port=SWD', 'mode=NORMAL', 'reset=SWrst']
# What a launch may hold after the program's own command (01-B11), besides a probe's serial number, a decimal byte
# count and the output file's path.
LISTED = ('-c', 'port=SWD', 'mode=HOTPLUG', 'mode=NORMAL', 'reset=SWrst', '-ob', 'displ', '--upload', '0x08000000',
          '-rst')


def outside_the_list(arguments, output_file):
    """The arguments of one launch that are not drawn from the list of 01-B11, where `output_file` is the path the
    read is to write."""
    return [argument for argument in arguments
            if not (argument in LISTED or re.fullmatch(r'sn=[0-9A-Za-z]{1,64}', argument)
                    or re.fullmatch(r'[0-9]+', argument) or Path(argument) == Path(output_file))]


class FlashToolCase(unittest.TestCase):
    """Run folders in a temporary folder, each with a stand-in program of its own record. A test begins with one, and
    `fresh` begins another. When a test ends, every launch it made is held to the list of 01-B11."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='stm32 flash tool ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.scenarios = []
        self.addCleanup(self.hold_every_launch_to_the_list)
        self.fresh()

    def fresh(self, **scenario):
        """Begin a scenario: a new run folder, and a new record for the stand-in, steered by `scenario`."""
        number = str(len(self.scenarios))
        self.state = self.root / ('stand-in-' + number)
        stand_in.steer(self.state, **scenario)
        os.environ[stand_in.STATE] = str(self.state)
        self.run_dir = self.root / ('run-' + number)
        self.work = self.run_dir / 'acquire' / 'stm32'
        self.flash = self.work / 'flash.bin'
        self.image = self.run_dir / 'acquire' / 'interpret-input.bin'
        self.scenarios.append((self.state, self.flash))

    def hold_every_launch_to_the_list(self):
        for state, flash in self.scenarios:
            for launch in stand_in.launches(state):
                self.assertEqual(outside_the_list(launch['arguments'], flash), [], launch)

    def read(self, **arguments):
        """The result of the tool for `arguments`, with the stand-in as the program unless another is given."""
        from generative_driver.toolkit import call_tool
        return call_tool(TOOL, {'run_dir': str(self.run_dir), 'program': stand_in.COMMAND, **arguments})

    def launches(self):
        """The arguments of each launch so far, after the program's own command."""
        return [launch['arguments'] for launch in stand_in.launches(self.state)]


class HotplugReadTests(FlashToolCase):
    """Without the reset grant the tool connects without a halt or a reset (01-B9)."""

    def test_01_B9_stm32_without_the_reset_grant_two_hotplug_launches_read_the_image(self):
        result = self.read(size_kb=4)
        self.assertIs(result.get('available'), True, result)
        identify, read = self.launches()
        self.assertEqual(identify, [*HOTPLUG, '-ob', 'displ'])
        self.assertEqual(read, [*HOTPLUG, '--upload', '0x08000000', '4096', str(self.flash)])
        self.assertNotIn('-rst', identify + read)
        # Each launch runs in the tool's folder for the program.
        self.assertEqual([Path(launch['cwd']).resolve() for launch in stand_in.launches(self.state)],
                         [self.work, self.work])
        self.assertEqual(Path(result['artifact']), self.image)
        self.assertEqual(self.image.read_bytes(), stand_in.image(4096))
        self.assertEqual(result['connection'], 'hotplug')
        self.assertNotIn('returned_to_application', result)

    def test_01_B9_stm32_a_probe_that_names_a_serial_number_is_selected_by_it(self):
        result = self.read(size_kb=2, probe_serial_number='066DFF48A1b2')
        self.assertIs(result.get('available'), True, result)
        identify, read = self.launches()
        self.assertEqual(identify, [*HOTPLUG, 'sn=066DFF48A1b2', '-ob', 'displ'])
        self.assertEqual(read, [*HOTPLUG, 'sn=066DFF48A1b2', '--upload', '0x08000000', '2048', str(self.flash)])
        self.assertEqual(self.image.read_bytes(), stand_in.image(2048))
        self.assertEqual(result['connection'], 'hotplug')
        self.assertNotIn('returned_to_application', result)


class ResetGrantTests(FlashToolCase):
    """With the reset grant the chip is reset to connect and again after the read (01-B10)."""

    def test_01_B10_stm32_with_the_reset_grant_both_launches_connect_under_reset_and_the_read_ends_with_a_reset(self):
        result = self.read(size_kb=4, allow_reset=True)
        self.assertIs(result.get('available'), True, result)
        self.assertEqual(self.launches(), [[*NORMAL, '-ob', 'displ'],
                                           [*NORMAL, '--upload', '0x08000000', '4096', str(self.flash), '-rst']])
        self.assertEqual((result['connection'], result.get('returned_to_application')), ('normal', True))
        self.assertEqual(self.image.read_bytes(), stand_in.image(4096))
        self.fresh()
        result = self.read(size_kb=4, allow_reset=True, probe_serial_number='0670FF3632')
        self.assertIs(result.get('available'), True, result)
        self.assertEqual(self.launches(), [[*NORMAL, 'sn=0670FF3632', '-ob', 'displ'],
                                           [*NORMAL, 'sn=0670FF3632', '--upload', '0x08000000', '4096',
                                            str(self.flash), '-rst']])
        self.assertEqual((result['connection'], result.get('returned_to_application')), ('normal', True))

    def test_01_B10_stm32_a_call_that_ends_without_a_successful_read_returns_the_chip_to_its_application(self):
        identify = [*NORMAL, '-ob', 'displ']
        back = [*HOTPLUG, '-rst']
        cases = (('no controller answers', {'exit': {'identify': 1}}, {'size_kb': 4}, [identify, back]),
                 ('read protection is set', {'rdp': 'BB'}, {'size_kb': 4}, [identify, back]),
                 ('no size is known', {'flash_size': None}, {}, [identify, back]),
                 ('the read launch exits nonzero', {'exit': {'read': 1}}, {'size_kb': 4}, [identify, 'read', back]),
                 ('the read leaves no file', {'write': 'nothing'}, {'size_kb': 4}, [identify, 'read', back]),
                 ('the read leaves a short file', {'write': 'short'}, {'size_kb': 4}, [identify, 'read', back]),
                 ('the dump is blank', {'write': 'blank'}, {'size_kb': 4}, [identify, 'read', back]))
        for label, scenario, arguments, expected in cases:
            with self.subTest(label):
                self.fresh(**scenario)
                result = self.read(allow_reset=True, **arguments)
                read = [*NORMAL, '--upload', '0x08000000', '4096', str(self.flash), '-rst']
                self.assertEqual(self.launches(), [read if launch == 'read' else launch for launch in expected])
                self.assertEqual((result.get('available'), result.get('returned_to_application')), (False, True),
                                 result)
        # The return launch names the probe as the others do, and its exit code is what the result says.
        self.fresh(exit={'identify': 1, 'return': 1})
        result = self.read(allow_reset=True, size_kb=4, probe_serial_number='0670FF3632')
        self.assertEqual(self.launches(), [[*NORMAL, 'sn=0670FF3632', '-ob', 'displ'],
                                           [*HOTPLUG, 'sn=0670FF3632', '-rst']])
        self.assertEqual((result.get('available'), result.get('returned_to_application')), (False, False), result)
        # No return launch is made without a launch before it, or without the grant.
        self.fresh()
        result = self.read(allow_reset=True, size_kb=0)
        self.assertEqual((self.launches(), result.get('available')), ([], False), result)
        self.assertNotIn('returned_to_application', result)
        self.fresh()
        result = self.read(allow_reset=True, size_kb=4, program=[str(self.root / 'no such program')])
        self.assertEqual((self.launches(), result.get('available')), ([], False), result)
        self.assertNotIn('returned_to_application', result)
        for scenario, launched in (({'exit': {'identify': 1}}, 1), ({'rdp': 'BB'}, 1), ({'exit': {'read': 1}}, 2),
                                   ({'write': 'blank'}, 2)):
            with self.subTest(scenario=scenario):
                self.fresh(**scenario)
                result = self.read(size_kb=4)
                self.assertEqual((len(self.launches()), result.get('available')), (launched, False), result)
                self.assertNotIn('-rst', [argument for launch in self.launches() for argument in launch])
                self.assertNotIn('returned_to_application', result)


UNFINISHED = 'the read could not be finished'


class RaisedTests(FlashToolCase):
    """A call that an exception ends after a launch ends as any failed call does: under the reset grant the chip is
    let run again (01-B10), and no image and no read file is left (01-B12)."""

    def stand(self, name):
        """Put a folder where the tool is to write the file `name` of its work folder, so that writing it raises."""
        (self.work / name).mkdir(parents=True)

    @contextlib.contextmanager
    def unstartable(self, refuse):
        """While the block runs, the system refuses to start a process whose command `refuse` picks, as it refuses a
        file it cannot run. Gives the commands it refused."""
        start, refused = subprocess.Popen, []

        def popen(command, *more, **named):
            if refuse(command):
                refused.append(list(command))
                raise OSError('The system cannot start the program')
            return start(command, *more, **named)
        with patch('subprocess.Popen', popen):
            yield refused

    def test_01_B10_stm32_a_call_that_an_exception_ends_after_a_launch_returns_the_chip_to_its_application(self):
        identify, back = [*NORMAL, '-ob', 'displ'], [*HOTPLUG, '-rst']
        cases = (('identify.txt', [identify, back]), ('flash.bin', [identify, back]),
                 ('read.txt', [identify, 'read', back]), ('provenance.json', [identify, 'read', back]))
        for name, expected in cases:
            with self.subTest(cannot_be_written=name):
                self.fresh()
                self.stand(name)
                result = self.read(allow_reset=True, size_kb=4)
                read = [*NORMAL, '--upload', '0x08000000', '4096', str(self.flash), '-rst']
                self.assertEqual(self.launches(), [read if launch == 'read' else launch for launch in expected])
                self.assertEqual(
                    (result.get('available'), result.get('reason'), result.get('returned_to_application')),
                    (False, UNFINISHED, True), result)
        # The return launch names the probe, and its exit code is what the result says.
        self.fresh(exit={'return': 1})
        self.stand('read.txt')
        result = self.read(allow_reset=True, size_kb=4, probe_serial_number='0670FF3632')
        self.assertEqual(self.launches()[2:], [[*HOTPLUG, 'sn=0670FF3632', '-rst']])
        self.assertEqual((result.get('available'), result.get('returned_to_application')), (False, False), result)
        # Without the grant no launch follows, and the result does not say where the chip is.
        for name, launched in (('identify.txt', 1), ('read.txt', 2)):
            with self.subTest(without_the_grant=name):
                self.fresh()
                self.stand(name)
                result = self.read(size_kb=4)
                self.assertEqual((len(self.launches()), result.get('available'), result.get('reason')),
                                 (launched, False, UNFINISHED), result)
                self.assertNotIn('-rst', [argument for launch in self.launches() for argument in launch])
                self.assertNotIn('returned_to_application', result)

    def test_01_B10_stm32_a_launch_the_system_cannot_start_is_no_launch_and_the_call_ends_as_a_failed_one(self):
        identify = [*NORMAL, '-ob', 'displ']
        # The read cannot be started after the first launch reset the chip: one more launch lets it run again.
        self.fresh()
        with self.unstartable(lambda command: '--upload' in command) as refused:
            result = self.read(allow_reset=True, size_kb=4)
        self.assertEqual((self.launches(), len(refused)), ([identify, [*HOTPLUG, '-rst']], 1))
        self.assertEqual((result.get('available'), result.get('reason'), result.get('returned_to_application')),
                         (False, UNFINISHED, True), result)
        # The return launch cannot be started: the chip is not said to be back, and the result keeps its reason.
        self.fresh(exit={'identify': 1})
        with self.unstartable(lambda command: 'mode=HOTPLUG' in command) as refused:
            result = self.read(allow_reset=True, size_kb=4)
        self.assertEqual((self.launches(), len(refused)), ([identify], 1))
        self.assertEqual((result.get('available'), result.get('reason'), result.get('returned_to_application')),
                         (False, NO_CONTROLLER, False), result)
        # The first launch cannot be started: nothing reset the chip, so no launch follows.
        self.fresh()
        with self.unstartable(lambda command: '-ob' in command) as refused:
            result = self.read(allow_reset=True, size_kb=4)
        self.assertEqual((self.launches(), len(refused)), ([], 1))
        self.assertEqual((result.get('available'), result.get('reason')), (False, UNFINISHED), result)
        self.assertNotIn('returned_to_application', result)

    def test_01_B12_stm32_a_call_that_an_exception_ends_makes_no_image_and_leaves_no_read_file(self):
        for name, kept in (('read.txt', ['identify.txt']), ('provenance.json', ['identify.txt', 'read.txt'])):
            with self.subTest(cannot_be_written=name):
                self.fresh()
                self.stand(name)
                result = self.read(size_kb=4)
                self.assertEqual(len(self.launches()), 2)
                self.assertEqual((self.image.exists(), self.flash.exists()), (False, False), result)
                self.assertEqual((result.get('available'), result.get('reason'), result.get('_exit')),
                                 (False, UNFINISHED, 1), result)
                # The result says what was raised, and names the texts that were kept.
                self.assertIn(name, result.get('cause', ''))
                self.assertEqual(result.get('launch_texts'), [str(self.work / text) for text in kept])
                # Nothing of the failed call stands in the way of the next: with the folder gone, the same run reads.
                (self.work / name).rmdir()
                again = self.read(size_kb=4)
                self.assertIs(again.get('available'), True, again)
                self.assertEqual(self.image.read_bytes(), stand_in.image(4096))

    def test_01_B12_stm32_a_move_that_fails_leaves_no_image_no_read_file_and_no_record(self):
        # While the read runs, a folder comes to stand where the image is to go, so the tool's last step fails.
        self.fresh(in_the_way=True)
        result = self.read(size_kb=4, allow_reset=True)
        self.assertEqual(len(self.launches()), 3)
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ['identify.txt', 'read.txt'])
        # What stood there is left as it was, and the chip is let run again.
        self.assertEqual((self.image.is_dir(), list(self.image.iterdir())), (True, []))
        self.assertEqual((result.get('available'), result.get('reason'), result.get('returned_to_application')),
                         (False, UNFINISHED, True), result)
        self.assertEqual(self.launches()[2], [*HOTPLUG, '-rst'])
        self.assertEqual(result.get('launch_texts'), [str(self.work / 'identify.txt'), str(self.work / 'read.txt')])


DEVICE_NAME = 'STM32F101/F102/F103 Medium-density'


class OutputReadTests(FlashToolCase):
    """What the tool reads from the program's output (01-B13)."""

    def read_count(self):
        """The byte count of the one read launch."""
        [read] = [launch for launch in self.launches() if '--upload' in launch]
        return read[read.index('--upload') + 2]

    def test_01_B13_stm32_the_size_is_the_callers_or_else_the_one_on_the_flash_size_line(self):
        sizes = (({'flash_size': '64 KBytes'}, {}, 65536), ({'flash_size': '1 MBytes'}, {}, 1048576),
                 ({'flash_size': '32 kbytes'}, {}, 32768), ({'flash_size': '8 KBytes', 'shout': True}, {}, 8192),
                 ({'flash_size': '64 KBytes'}, {'size_kb': 4}, 4096), ({'flash_size': None}, {'size_kb': 2}, 2048))
        for scenario, arguments, size in sizes:
            with self.subTest(scenario=scenario, arguments=arguments):
                self.fresh(**scenario)
                result = self.read(**arguments)
                self.assertIs(result.get('available'), True, result)
                self.assertEqual(self.read_count(), str(size))
                self.assertEqual((result.get('size'), self.image.stat().st_size), (size, size))
                self.assertEqual(self.image.read_bytes(), stand_in.image(size))

    def test_01_B13_stm32_escape_sequences_are_removed_before_any_line_is_read(self):
        for scenario in ({}, {'escapes_inside': True}, {'line_end': '\r\n'}, {'escapes_inside': True, 'shout': True}):
            with self.subTest(scenario=scenario):
                self.fresh(**scenario)
                result = self.read()
                self.assertIs(result.get('available'), True, result)
                self.assertEqual(self.read_count(), '65536')
                self.assertEqual(result.get('chip'), DEVICE_NAME.upper() if scenario.get('shout') else DEVICE_NAME)
                self.assertEqual(result.get('readout_protection'), 'level 0')

    def test_01_B13_stm32_without_an_rdp_line_the_protection_is_unknown_and_the_tool_reads(self):
        self.fresh(rdp_line=False)
        result = self.read()
        self.assertIs(result.get('available'), True, result)
        self.assertEqual(result.get('readout_protection'), 'unknown')
        self.assertEqual(self.image.read_bytes(), stand_in.image(65536))
        # A device the program does not name is read too.
        self.fresh(device_name=None)
        result = self.read()
        self.assertIs(result.get('available'), True, result)
        self.assertEqual((result.get('chip', 'absent'), result.get('readout_protection')), (None, 'level 0'))


EXISTS = 'An acquired image already exists; use a fresh run'
BAD_SIZE = 'size_kb must be a whole number from 1 to 16384'
NOT_CONFIGURED = 'The debug probe or its program is not configured'
NO_CONTROLLER = 'no controller answered through the debug probe'
PROTECTED = 'readout protection is set; class 1 unavailable'
NO_SIZE = 'The program did not report the flash size; pass size_kb'
DUMP_FAILED = 'dump failed'
BLANK = 'the dump is blank'


class UnavailableTests(FlashToolCase):
    """The cases in which the tool reads no image, each with its reason (01-B12)."""

    def refused(self, reason, result, launched, code=1):
        """Hold `result` to a refusal with `reason` after `launched` launches, with no image made."""
        self.assertEqual((result.get('available'), result.get('reason'), result.get('_exit')), (False, reason, code),
                         result)
        self.assertEqual(len(self.launches()), launched, self.launches())
        self.assertFalse(self.image.exists())
        self.assertFalse(self.flash.exists())

    def test_01_B12_stm32_nothing_is_launched_for_an_earlier_image_a_bad_size_or_a_missing_configuration(self):
        for name in ('interpret-input.bin', 'interpret-input.elf', 'interpret-input.hex'):
            with self.subTest(earlier=name):
                self.fresh()
                earlier = self.run_dir / 'acquire' / name
                earlier.parent.mkdir(parents=True)
                earlier.write_bytes(b'an earlier image')
                result = self.read(size_kb=4)
                self.assertEqual((result.get('available'), result.get('reason'), result.get('_exit')),
                                 (False, EXISTS, 1), result)
                self.assertEqual(self.launches(), [])
                self.assertEqual(sorted(path.name for path in earlier.parent.iterdir() if path.is_file()), [name])
                self.assertEqual(earlier.read_bytes(), b'an earlier image')
        for size in (0, -4, 16385, 1.5, '64', True, [4]):
            with self.subTest(size_kb=size):
                self.fresh()
                self.refused(BAD_SIZE, self.read(size_kb=size), launched=0)
        missing = str(self.root / 'no such folder' / 'STM32_Programmer_CLI')
        try:
            relative = os.path.relpath(sys.executable)
        except ValueError:  # On Windows no relative path leads to another drive.
            relative = 'STM32_Programmer_CLI'
        # On Windows a path without its drive is not absolute, though it may name a file on the current drive.
        driveless = os.path.splitdrive(sys.executable)[1]
        programs = (sys.executable, [], [''], [sys.executable, ''], [sys.executable, 5], [missing], [str(self.root)],
                    [relative], None, *([[driveless]] if driveless != sys.executable else []))
        for program in programs:
            with self.subTest(program=program):
                self.fresh()
                self.refused(NOT_CONFIGURED, self.read(size_kb=4, program=program), launched=0)
        for serial in ('', 'A B', 'A-1', 'A' * 65, '\u00c91', 5):
            with self.subTest(probe_serial_number=serial):
                self.fresh()
                self.refused(NOT_CONFIGURED, self.read(size_kb=4, probe_serial_number=serial), launched=0)
        # The bounds themselves are taken: the largest size and the longest serial number reach the first launch.
        self.fresh(exit={'identify': 1})
        self.refused(NO_CONTROLLER, self.read(size_kb=16384, probe_serial_number='9' * 64), launched=1)
        self.fresh()
        self.assertIs(self.read(size_kb=1).get('available'), True)

    def test_01_B12_stm32_no_read_is_launched_without_an_answer_under_read_protection_or_without_a_size(self):
        self.fresh(exit={'identify': 1})
        self.refused(NO_CONTROLLER, self.read(size_kb=4), launched=1)
        for rdp in ('BB', 'CC', '00', 'bb', '55'):
            with self.subTest(rdp=rdp):
                self.fresh(rdp=rdp)
                # Exit code 0, as `acquire_flash` gives for a locked ESP32.
                self.refused(PROTECTED, self.read(size_kb=4), launched=1, code=0)
        self.fresh(flash_size=None)
        self.refused(NO_SIZE, self.read(), launched=1)
        self.fresh(flash_size='0 KBytes')
        self.refused(NO_SIZE, self.read(), launched=1)

    def test_01_B12_stm32_a_read_that_fails_or_leaves_a_blank_image_makes_no_image(self):
        cases = (('the read launch exits nonzero', {'exit': {'read': 1}}, DUMP_FAILED,
                  'the read launch exited nonzero'),
                 ('its file is missing', {'write': 'nothing'}, DUMP_FAILED, 'the read launch left no file'),
                 ('its file is short', {'write': 'short'}, DUMP_FAILED, 'the file is not the size asked for'),
                 ('every byte is equal', {'write': 'blank'}, BLANK, None))
        for label, scenario, reason, cause in cases:
            with self.subTest(label):
                self.fresh(**scenario)
                result = self.read(size_kb=4)
                self.refused(reason, result, launched=2)
                self.assertEqual(result.get('cause'), cause)
        # A file an earlier call left behind is no read: the tool removes it before the launch.
        self.fresh(write='nothing')
        self.work.mkdir(parents=True)
        self.flash.write_bytes(stand_in.image(4096))
        result = self.read(size_kb=4)
        self.refused(DUMP_FAILED, result, launched=2)
        self.assertEqual(result.get('cause'), 'the read launch left no file')

    def stopped(self, launch):
        """Whether the launch the stand-in recorded as `launch` runs no more: nothing listens on its port."""
        try:
            socket.create_connection(('127.0.0.1', launch['listening']), timeout=5).close()
        except OSError:
            return True
        return False

    def patched_limit(self):
        """The seconds a launch that is to outlast its limit is given here: enough on this machine for the stand-in to
        start and record the launch before it is stopped, however slow the machine is. That is the time of ten
        launches, as a plain read of two launches measures it now, and 3 seconds at least."""
        self.fresh()
        began = time.monotonic()
        self.assertIs(self.read(size_kb=4).get('available'), True)
        return max(3, 5 * (time.monotonic() - began))

    def test_01_B12_stm32_a_launch_past_its_limit_is_stopped_and_counts_as_failed(self):
        from generative_driver._toolchain import acquire
        self.assertEqual((acquire.STM32_IDENTIFY_SECONDS, acquire.STM32_READ_SECONDS), (60, 300))
        seconds = self.patched_limit()
        cases = (('identify', 'STM32_IDENTIFY_SECONDS', NO_CONTROLLER, 1),
                 ('read', 'STM32_READ_SECONDS', DUMP_FAILED, 2))
        for kind, constant, reason, launched in cases:
            with self.subTest(kind=kind):
                self.fresh(outlast={kind: 600})
                began = time.monotonic()
                with patch.object(acquire, constant, seconds):
                    result = self.read(size_kb=4)
                # The tool did not wait for the launch to end by itself.
                self.assertLess(time.monotonic() - began, seconds + 120)
                self.refused(reason, result, launched=launched)
                last = stand_in.launches(self.state)[-1]
                self.assertEqual(last['kind'], kind)
                self.assertTrue(self.stopped(last))

    def test_01_B12_stm32_a_return_launch_past_its_limit_is_stopped_and_the_chip_is_not_said_to_be_back(self):
        from generative_driver._toolchain import acquire
        self.assertEqual(acquire.STM32_RETURN_SECONDS, 30)
        seconds = self.patched_limit()
        self.fresh(exit={'identify': 1}, outlast={'return': 600})
        began = time.monotonic()
        with patch.object(acquire, 'STM32_RETURN_SECONDS', seconds):
            result = self.read(size_kb=4, allow_reset=True)
        self.assertLess(time.monotonic() - began, seconds + 120)
        self.refused(NO_CONTROLLER, result, launched=2)
        self.assertIs(result.get('returned_to_application'), False)
        last = stand_in.launches(self.state)[-1]
        self.assertEqual(last['kind'], 'return')
        self.assertTrue(self.stopped(last))

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class EvidenceTests(FlashToolCase):
    """What a read leaves under the run's tool folder, and what its event records (01-B14)."""

    def files(self):
        """The files under the run's `acquire/` folder, by their paths from it."""
        folder = self.run_dir / 'acquire'
        return sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*') if path.is_file())

    def event(self):
        """The tool's last event in the run's `events.jsonl`."""
        lines = (self.run_dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()
        return json.loads(lines[-1])

    def text(self, name):
        return (self.work / name).read_bytes().decode('utf-8')

    def test_01_B14_stm32_a_read_leaves_the_image_both_texts_and_a_record_and_its_event_hashes_the_four(self):
        result = self.read(probe_serial_number='0670FF3632')
        self.assertIs(result.get('available'), True, result)
        identify, read, record = self.work / 'identify.txt', self.work / 'read.txt', self.work / 'provenance.json'
        self.assertEqual(self.files(), ['interpret-input.bin', 'stm32/identify.txt', 'stm32/provenance.json',
                                        'stm32/read.txt'])
        connect = [*HOTPLUG, 'sn=0670FF3632']
        self.assertEqual(self.text('identify.txt'), stand_in.plain([*connect, '-ob', 'displ']))
        self.assertEqual(self.text('read.txt'),
                         stand_in.plain([*connect, '--upload', '0x08000000', '65536', str(self.flash)]))
        self.assertEqual(self.image.read_bytes(), stand_in.image(65536))
        digest = hashlib.sha256(stand_in.image(65536)).hexdigest()
        fields = {'schema': 'firmware-readout/1', 'origin': 'device_dump', 'origin_basis': 'debug_probe_read',
                  'program': Path(sys.executable).name, 'probe': 'stlink', 'probe_serial_number': '0670FF3632',
                  'connection': 'hotplug', 'reset_granted': False, 'start_address': '0x08000000', 'size': 65536,
                  'sha256': digest, 'device_name': DEVICE_NAME, 'readout_protection': 'level 0',
                  'flash_performed': False, 'bit_identical_to_device': 'not_verified'}
        written = json.loads(record.read_text(encoding='utf-8'))
        self.assertEqual(written, fields)
        self.assertEqual(list(written), list(fields))
        four = [str(self.image), str(identify), str(read), str(record)]
        event = self.event()
        self.assertEqual((event['tool'], event['exit'], event['stage']), (TOOL, 0, 'acquire'))
        self.assertEqual(event['files'], {path: sha256(path) for path in four})
        self.assertEqual(event['files'][str(self.image)], digest)
        # The result, as `toolkit.call_tool` returns it.
        self.assertEqual(result, {
            'available': True, 'rung': 'flash', 'chip': DEVICE_NAME, 'artifact': str(self.image),
            'interpret_input': str(self.image), 'artifacts': four, 'provenance': str(record), 'sha256': digest,
            'size': 65536, 'connection': 'hotplug', 'readout_protection': 'level 0',
            'front_end': 'interpret-firmware-binary', 'inputs_for_interpret': {'image.bin': str(self.image)},
            '_event': event['seq'], '_exit': 0})

    def test_01_B14_stm32_the_record_says_what_was_granted_and_what_the_program_did_not_say(self):
        self.fresh(rdp_line=False, device_name=None)
        result = self.read(size_kb=4, allow_reset=True)
        self.assertIs(result.get('available'), True, result)
        written = json.loads((self.work / 'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual({key: written[key] for key in ('probe_serial_number', 'connection', 'reset_granted', 'size',
                                                        'device_name', 'readout_protection')},
                         {'probe_serial_number': None, 'connection': 'normal', 'reset_granted': True, 'size': 4096,
                          'device_name': None, 'readout_protection': 'unknown'})
        self.assertEqual(len(written), 15)
        self.assertEqual((result['chip'], result['returned_to_application'], result['sha256']),
                         (None, True, written['sha256']))
        self.assertEqual(self.text('read.txt'), stand_in.plain(
            [*NORMAL, '--upload', '0x08000000', '4096', str(self.flash), '-rst'], rdp_line=False, device_name=None))

    def test_01_B14_stm32_the_texts_are_kept_on_a_failure_and_the_result_names_them(self):
        identify, read = self.work / 'identify.txt', self.work / 'read.txt'
        cases = (('no controller answers', {'exit': {'identify': 1}}, ['stm32/identify.txt'], [identify]),
                 ('read protection is set', {'rdp': 'BB', 'line_end': '\r\n'}, ['stm32/identify.txt'], [identify]),
                 ('the read launch exits nonzero', {'exit': {'read': 1}}, ['stm32/identify.txt', 'stm32/read.txt'],
                  [identify, read]),
                 ('the dump is blank', {'write': 'blank', 'escapes_inside': True},
                  ['stm32/identify.txt', 'stm32/read.txt'], [identify, read]))
        for label, scenario, files, named in cases:
            with self.subTest(label):
                self.fresh(**scenario)
                result = self.read(size_kb=4)
                self.assertIs(result.get('available'), False, result)
                self.assertEqual(self.files(), files)
                work = self.work
                self.assertEqual(result.get('launch_texts'), [str(work / path.name) for path in named])
                self.assertEqual(self.text('identify.txt'), stand_in.plain([*HOTPLUG, '-ob', 'displ'], **scenario))
                if len(named) == 2:
                    self.assertEqual(self.text('read.txt'), stand_in.plain(
                        [*HOTPLUG, '--upload', '0x08000000', '4096', str(self.flash)], **scenario))
                self.assertEqual(self.event()['files'], {str(work / path.name): sha256(work / path.name)
                                                         for path in named})
                self.assertNotIn('\x1b', self.text('identify.txt'))


class LaunchArgumentTests(FlashToolCase):
    """Every launch is drawn from one short list (01-B11). `FlashToolCase` holds every scenario of this module to
    it; this test adds what a worker could send."""

    def test_01_B11_stm32_nothing_a_caller_sends_reaches_a_launch_but_as_the_byte_count(self):
        # The check itself sees what the list leaves out.
        strays = ['mode=UR', '-e', 'all', '-rdu', '--readunprotect', '-w', '-d', '-ob', 'RDP=0xAA', '0x08000001',
                  'sn=A1 -rdu', 'sn=', '4096.0', '-4096', str(self.root / 'elsewhere.bin'), '-hardRst', 'port=USB1']
        self.assertEqual(outside_the_list([*HOTPLUG, *strays, str(self.flash)], self.flash),
                         [stray for stray in strays if stray != '-ob'])
        elsewhere = str(self.root / 'elsewhere.bin')
        sent = (
            {'size_kb': 1.5}, {'size_kb': '4 -e all'}, {'size_kb': '0x10'}, {'size_kb': [4, '-e', 'all']},
            {'size_kb': 2, 'probe_serial_number': 'A1 -rdu'},
            {'size_kb': 2, 'probe_serial_number': 'A1 -w ' + elsewhere + ' 0x08000000'},
            {'size_kb': 2, 'probe_serial_number': ['A1', '-e', 'all']},
            {'size_kb': 2, 'erase': 'all', 'arguments': ['-e', 'all'], 'mode': 'UR', 'start_address': '0x1FFF7800',
             'file': elsewhere, 'write': elsewhere, 'readunprotect': True, 'option_bytes': 'RDP=0xAA',
             'connect': 'port=USB1', 'extra': '-rdu'})
        for arguments in sent:
            self.fresh()
            self.read(**arguments)
            for launch in self.launches():
                with self.subTest(sent=arguments, launch=launch):
                    self.assertEqual(outside_the_list(launch, self.flash), [])
        # What the tool does not know it ignores: the last call read 2 KB, and the byte count is all it took.
        self.assertEqual(self.launches(), [[*HOTPLUG, '-ob', 'displ'],
                                           [*HOTPLUG, '--upload', '0x08000000', '2048', str(self.flash)]])
        self.assertFalse(Path(elsewhere).exists())


if __name__ == '__main__':
    unittest.main()

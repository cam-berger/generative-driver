"""Generated device sets: registered by their manifest hash, opened one device at a time."""
import base64
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generated_fixtures import (FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, PASSWORD, fixture_mission, registrations_table,
                                write_fixture_set)
from generative_driver.benchmark_support import generated_sets
from generative_driver.benchmark_support.generated_device import FRAMINGS
from generative_driver.benchmark_support.generated_sets import (is_scoring, load_set, open_device, seal_device,
                                                                set_registrations, write_manifest)
from generative_driver.benchmark_support.snapshots import canonical_digest
from generative_driver.benchmark_support.truth import EvaluatorPassword

SET = 'fixture-v1'


class RegistrationTableTests(unittest.TestCase):
    """Set registrations are code-owned data, read strictly."""

    def write(self, text):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'sets.json'
        path.write_text(text, encoding='utf-8')
        return path

    def test_the_shipped_table_registers_no_set_yet(self):
        # A deliberate tripwire: the first real registration updates this test.
        shipped = Path(generated_sets.__file__).resolve().parents[1] / 'resources' / 'registrations' / 'sets.json'
        self.assertEqual(json.loads(shipped.read_text(encoding='utf-8')),
                         {'schema': 'generated-set-registrations/2', 'sets': {}})
        self.assertEqual(set_registrations(), {})

    def test_04_B1_the_table_gives_each_sets_hash_and_its_mark(self):
        path = self.write(json.dumps({
            'schema': 'generated-set-registrations/2',
            'sets': {'scoring-v1': {'manifest_sha256': 'a' * 64, 'scoring': True},
                     'development-v1': {'manifest_sha256': 'b' * 64, 'scoring': False}}}))
        self.assertEqual(set_registrations(path), {'scoring-v1': 'a' * 64, 'development-v1': 'b' * 64})
        self.assertIs(is_scoring('scoring-v1', path), True)
        self.assertIs(is_scoring('development-v1', path), False)

    def test_04_B1_is_scoring_reads_the_code_owned_table_by_default(self):
        path = self.write(registrations_table({'scoring-v1': 'a' * 64}, scoring=True))
        with patch.object(generated_sets, '_SETS_FILE', path):
            self.assertIs(is_scoring('scoring-v1'), True)

    def test_04_B1_an_unregistered_name_has_no_mark(self):
        path = self.write(registrations_table({'scoring-v1': 'a' * 64}))
        with self.assertRaisesRegex(ValueError, '^Generated set not registered: other-v1$'):
            is_scoring('other-v1', path)

    def test_04_B1_a_malformed_table_is_refused(self):
        pin = 'a' * 64
        entry = {'manifest_sha256': pin, 'scoring': False}

        def table(sets=None, schema='generated-set-registrations/2', **more):
            return json.dumps({'schema': schema, 'sets': {SET: entry} if sets is None else sets, **more})

        self.assertEqual(set_registrations(self.write(table())), {SET: pin})
        self.assertIs(is_scoring(SET, self.write(table())), False)
        # A JSON reader that keeps the last of two equal keys would register the second pin for the set.
        one = json.dumps(entry)
        same_set_twice = ('{"schema": "generated-set-registrations/2", "sets": {"%s": %s, "%s": %s}}'
                          % (SET, one, SET, one))
        same_field_twice = table().replace('"sets"', '"schema": "other", "sets"', 1)
        unsupported = 'Unsupported generated set registrations'
        pinned = '^A generated set registration holds its manifest_sha256.*: ' + SET + '$'
        rows = [('a set registered twice', same_set_twice, 'Duplicate key'),
                ('a field given twice', same_field_twice, 'Duplicate key'),
                ('another schema', table(schema='generated-set-registrations/1'), unsupported),
                ('the earlier form', json.dumps({'schema': 'generated-set-registrations/1', 'sets': {SET: pin}}),
                 unsupported),
                ('an unknown field', table(extra=1), unsupported),
                ('a missing field', json.dumps({'schema': 'generated-set-registrations/2'}), unsupported),
                ('sets as a list', table(sets=[SET]), unsupported), ('no object', '[]', unsupported),
                ('truncated JSON', '{', 'Cannot read'),
                ('a bare hash under the new schema', table(sets={SET: pin}), pinned),
                ('an entry with no mark', table(sets={SET: {'manifest_sha256': pin}}), pinned),
                ('an entry with no hash', table(sets={SET: {'scoring': True}}), pinned),
                ('an unknown field of an entry', table(sets={SET: {**entry, 'level': 1}}), pinned)]
        # A pin is 64 lowercase hexadecimal characters, and a trailing newline does not make it one.
        rows += [('the pin ' + repr(bad), table(sets={SET: {**entry, 'manifest_sha256': bad}}), pinned)
                 for bad in ('A' * 64, 'a' * 63, 'a' * 65, 'g' * 64, pin + '\n', '', 5, None)]
        # A mark is True or False, never something that reads like one.
        rows += [('the mark ' + repr(bad), table(sets={SET: {**entry, 'scoring': bad}}), pinned)
                 for bad in (1, 0, 'true', None, [])]
        for name, text, pattern in rows:
            path = self.write(text)
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, pattern):
                    set_registrations(path)
                with self.assertRaisesRegex(ValueError, pattern):
                    is_scoring(SET, path)

    def test_a_case_root_override_never_replaces_the_registrations(self):
        # The table is read beside the code, not through case_root(), which an environment variable redirects.
        forged = registrations_table({'forged-v1': 'a' * 64})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in (root / 'registrations', root / 'bench' / 'registrations'):
                folder.mkdir(parents=True)
                (folder / 'sets.json').write_text(forged, encoding='utf-8')
            for name in ('GENERATIVE_DRIVER_BENCH_ROOT', 'GENERATIVE_DRIVER_CASE_ROOT'):
                with self.subTest(name), patch.dict(os.environ, {name: str(root / 'bench')}):
                    self.assertEqual(set_registrations(), {})


class SetBundleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'set'
        self.root.mkdir()

    def write(self):
        """Write the fixture set under the root: the registrations that pin its manifest."""
        sha = write_fixture_set(self.root)
        self.pristine = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
        return {SET: sha}

    def pin(self, data):
        """Replace the manifest by `data`: the registrations that pin those bytes."""
        (self.root / 'manifest.json').write_bytes(data)
        return {SET: hashlib.sha256(data).hexdigest()}

    def alter(self, change):
        """Replace the written manifest by a copy of the original that `change` altered: the registrations pinning it."""
        manifest = copy.deepcopy(self.pristine)
        change(manifest)
        return self.pin((json.dumps(manifest, indent=2) + '\n').encode('utf-8'))

    def assert_refused(self, rows):
        """Each row (name, change, pattern) must make load_set refuse the written set, for the reason `pattern` names.
        A change that alters nothing loads first, so that a refusal comes from the row's change alone."""
        self.assertEqual(load_set(self.root, SET, registrations=self.alter(lambda manifest: None))['set'], SET)
        for name, change, pattern in rows:
            with self.subTest(name), self.assertRaisesRegex(ValueError, pattern):
                load_set(self.root, SET, registrations=self.alter(change))

    def flip_a_byte(self, name):
        path = self.root / name
        data = bytearray(path.read_bytes())
        data[len(data) // 2] ^= 1
        path.write_bytes(bytes(data))

    def test_a_set_opens_only_under_its_registered_manifest_hash(self):
        sha = write_fixture_set(self.root)
        manifest = load_set(self.root, SET, registrations={SET: sha})
        self.assertEqual(manifest['manifest_sha256'], sha)
        self.assertEqual((manifest['set'], [device['id'] for device in manifest['devices']]), (SET, ['0000d001', '0000d002']))
        with self.assertRaisesRegex(ValueError, 'not the registered manifest'):
            load_set(self.root, SET, registrations={SET: '0' * 64})

    def test_a_manifest_changed_after_registration_is_refused_whatever_its_meaning(self):
        registrations = self.write()
        path = self.root / 'manifest.json'
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))  # the same JSON value in other bytes
        with self.assertRaisesRegex(ValueError, 'not the registered manifest'):
            load_set(self.root, SET, registrations=registrations)

    def test_a_set_that_is_not_registered_is_refused(self):
        sha = write_fixture_set(self.root)
        for registrations in ({}, {'another-v1': sha}):
            with self.subTest(registrations), self.assertRaisesRegex(ValueError, 'not registered'):
                load_set(self.root, SET, registrations=registrations)
        with self.assertRaisesRegex(ValueError, 'not registered'):
            load_set(self.root, SET)  # the code-owned table registers no set yet

    def test_the_code_owned_registrations_are_used_unless_a_table_is_passed(self):
        sha = write_fixture_set(self.root)
        with patch.object(generated_sets, 'set_registrations', return_value={SET: sha}):
            self.assertEqual(load_set(self.root, SET)['manifest_sha256'], sha)
            with self.assertRaisesRegex(ValueError, 'not registered'):
                load_set(self.root, SET, registrations={})  # a table passed empty is not the code-owned one

    def test_a_manifest_that_is_not_well_formed_is_refused(self):
        self.write()
        device = lambda manifest: manifest['devices'][0]
        self.assert_refused([
            ('another schema', lambda m: m.update(schema='generated-set-manifest/2'), 'Unsupported'),
            ('another set name', lambda m: m.update(set='another-v1'), 'names set'),
            ('an unknown field', lambda m: m.update(note='x'), 'Unsupported'),
            ('a missing field', lambda m: m.pop('devices'), 'Unsupported'),
            ('devices that are no list', lambda m: m.update(devices={}), 'Unsupported'),
            ('a duplicate id', lambda m: m['devices'][1].update(id='0000d001'), 'Duplicate device id'),
            ('an unknown device field', lambda m: device(m).update(note='x'), 'device fields'),
            ('a missing device field', lambda m: device(m).pop('framing'), 'device fields'),
            ('a device that is no object', lambda m: m['devices'].append('0000d003'), 'device fields'),
            ('an uppercase content hash', lambda m: device(m).update(image_sha256='F' * 64), 'content hash'),
            ('a short content hash', lambda m: device(m).update(mission_sha256='a' * 63), 'content hash'),
            ('a file hash that is no hash', lambda m: device(m)['answer'].update(sha256='f' * 65), 'answer file'),
            ('an unknown file field', lambda m: device(m)['inputs'].update(note='x'), 'inputs file'),
            ('a file that is no object', lambda m: device(m).update(inputs='devices/0000d001.inputs.enc'), 'inputs file')])

    def test_a_set_without_a_device_is_refused(self):
        # It would qualify vacuously, whatever its mutants.
        self.write()
        self.assert_refused([('a manifest that lists no device', lambda m: m.update(devices=[]), 'lists no device')])
        (self.root / 'manifest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'lists no device'):
            write_manifest(self.root, SET, [])
        self.assertFalse((self.root / 'manifest.json').exists())

    def test_a_device_id_a_level_and_a_framing_are_checked(self):
        self.write()
        self.assertEqual(FRAMINGS, ('text-lines', 'binary-frames', 'register-access'))
        # An id is eight lowercase hexadecimal digits, so it carries no design and never names a Windows device.
        accepted = ([{'id': value} for value in ('0a1b2c3d', 'ffffffff', '00000000')] + [{'level': 5}]
                    + [{'framing': framing} for framing in FRAMINGS])
        for changes in accepted:
            with self.subTest(changes):
                load_set(self.root, SET, registrations=self.alter(lambda m: m['devices'][0].update(changes)))
        # A trailing newline does not make an id; a bool, a float and a string are no level.
        rows = ([('the id ' + repr(value), {'id': value}, 'device id')
                 for value in ('d001', '0A1B2C3D', '0a1b2c3', '0a1b2c3d0', 'g0000000', '0a1b-c3d', 'l4-frame', 'nul',
                               '0a1b2c3d\n', '', '../0a1b2', 7, None)]
                + [('the level ' + repr(value), {'level': value}, 'level') for value in (0, 6, -1, True, 1.0, '1', None)]
                + [('the framing ' + repr(value), {'framing': value}, 'framing')
                   for value in ('morse', 'TEXT-LINES', '', None, ['text-lines'])])
        self.assert_refused([(name, lambda m, c=changes: m['devices'][0].update(c), pattern)
                             for name, changes, pattern in rows])

    def test_a_manifest_with_a_duplicate_key_or_unreadable_text_is_refused(self):
        self.write()
        text = (self.root / 'manifest.json').read_text(encoding='utf-8')
        # A JSON reader that keeps the last of two equal keys would load this manifest, since the later schema is right.
        twice = text.replace('"schema"', '"schema": "other", "schema"', 1)
        self.assertNotEqual(twice, text)
        for name, data, pattern in (('a duplicate key', twice.encode('utf-8'), 'Duplicate key'),
                                    ('truncated JSON', b'{', 'Cannot read'), ('no object', b'[]', 'Unsupported'),
                                    ('text that is not UTF-8', b'\xff\xfe{}', 'Cannot read')):
            with self.subTest(name), self.assertRaisesRegex(ValueError, pattern):
                load_set(self.root, SET, registrations=self.pin(data))

    def test_a_path_that_leaves_the_set_is_refused(self):
        self.write()
        paths = ['../0000d001.inputs.enc', '/etc/hosts', 'devices\\0000d001.inputs.enc', 'C:/Windows/win.ini', 'C:win.ini',
                 '//server/share/x', 'devices/0000d001.inputs.enc:stream', 'devices/../../0000d001.inputs.enc',
                 './devices/0000d001.inputs.enc', 'devices//0000d001.inputs.enc', 'devices/', '', 'devices/0000d001.inputs.enc\x00',
                 5, None]
        self.assert_refused([(name + ' ' + repr(path), lambda m, n=name, p=path: m['devices'][0][n].update(path=p), 'set path')
                             for path in paths for name in ('inputs', 'answer')])

    def test_a_path_changed_after_loading_is_refused_when_the_device_opens(self):
        manifest = load_set(self.root, SET, registrations=self.write())
        for name in ('inputs', 'answer'):
            changed = copy.deepcopy(manifest)
            changed['devices'][0][name]['path'] = '../0000d001.' + name + '.enc'
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'set path'):
                open_device(self.root, changed, '0000d001', PASSWORD, answer=True)

    def test_a_symlink_that_leads_out_of_the_set_is_refused(self):
        registrations = self.write()
        manifest = load_set(self.root, SET, registrations=registrations)
        (self.root / 'devices').rename(self.base / 'elsewhere')  # the same files, now beyond the set
        try:
            (self.root / 'devices').symlink_to(self.base / 'elsewhere', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('This system cannot create a symbolic link')
        with self.assertRaisesRegex(ValueError, 'set path'):
            load_set(self.root, SET, registrations=registrations)
        with self.assertRaisesRegex(ValueError, 'set path'):  # a link made after the set loaded
            open_device(self.root, manifest, '0000d001', PASSWORD)

    def test_a_device_opens_alone_and_its_answer_key_only_when_asked(self):
        manifest = load_set(self.root, SET, registrations=self.write())
        device = open_device(self.root, manifest, '0000d001', PASSWORD)
        self.assertEqual((device['image'], sorted(device)), (FIXTURE_IMAGE, ['image', 'mission']))
        self.assertIn('answer_key', open_device(self.root, manifest, '0000d001', PASSWORD, answer=True))
        # Each device has its own content, and the key is the one that was sealed.
        self.assertEqual(device['mission'], fixture_mission('0000d001'))
        self.assertEqual(open_device(self.root, manifest, '0000d002', PASSWORD)['mission'], fixture_mission('0000d002'))
        self.assertEqual(open_device(self.root, manifest, '0000d002', PASSWORD, answer=True),
                         {'image': FIXTURE_IMAGE, 'mission': fixture_mission('0000d002'), 'answer_key': FIXTURE_ANSWER_KEY})

    def test_a_device_opens_without_its_answer_file(self):
        # Starting a trial needs the inputs alone, so the answer file is never read: it can be gone.
        manifest = load_set(self.root, SET, registrations=self.write())
        (self.root / 'devices' / '0000d001.answer.enc').unlink()
        self.assertEqual(sorted(open_device(self.root, manifest, '0000d001', PASSWORD)), ['image', 'mission'])

    def test_a_file_that_cannot_be_read_is_an_os_error_not_a_refusal(self):
        # ValueError says the bytes are wrong. OSError says they could not be read, as when a volume is not mounted, and
        # the harness and the qualification treat that as a problem of the host.
        registrations = self.write()
        manifest = load_set(self.root, SET, registrations=registrations)
        for name in ('devices/0000d001.answer.enc', 'devices/0000d002.inputs.enc', 'manifest.json'):
            (self.root / name).unlink()
        for name, read in (('the manifest', lambda: load_set(self.root, SET, registrations=registrations)),
                           ('an answer file', lambda: open_device(self.root, manifest, '0000d001', PASSWORD, answer=True)),
                           ('an inputs file', lambda: open_device(self.root, manifest, '0000d002', PASSWORD))):
            with self.subTest(name), self.assertRaises(OSError):
                read()

    def test_a_device_the_manifest_does_not_list_is_refused(self):
        manifest = load_set(self.root, SET, registrations=self.write())
        for device_id in ('0000d003', '', None):
            with self.subTest(device_id), self.assertRaisesRegex(ValueError, 'Unknown generated device'):
                open_device(self.root, manifest, device_id, PASSWORD)

    def test_a_changed_file_or_the_wrong_password_is_refused_before_plaintext(self):
        manifest = load_set(self.root, SET, registrations=self.write())
        self.flip_a_byte('devices/0000d002.inputs.enc')
        with self.assertRaisesRegex(ValueError, 'pinned hash'):
            open_device(self.root, manifest, '0000d002', PASSWORD)
        self.assertEqual(open_device(self.root, manifest, '0000d001', PASSWORD)['image'], FIXTURE_IMAGE)  # 0000d001 is untouched
        for password in (EvaluatorPassword('wrong'),):
            for answer in (False, True):
                with self.subTest(password=password, answer=answer), self.assertRaisesRegex(ValueError, 'password'):
                    open_device(self.root, manifest, '0000d001', password, answer=answer)
        self.flip_a_byte('devices/0000d001.answer.enc')
        with self.assertRaisesRegex(ValueError, 'pinned hash'):  # nothing is returned, not even the inputs that were fine
            open_device(self.root, manifest, '0000d001', PASSWORD, answer=True)

    def test_a_file_sealed_again_is_not_the_pinned_file(self):
        manifest = load_set(self.root, SET, registrations=self.write())
        inputs = {'image_base64': base64.b64encode(FIXTURE_IMAGE).decode('ascii'), 'mission': fixture_mission('0000d001')}
        self.assertEqual(open_device(self.root, manifest, '0000d001', PASSWORD)['mission'], inputs['mission'])
        PASSWORD.seal(inputs, self.root / 'devices' / '0000d001.inputs.enc')  # the same content, another salt and nonce
        with self.assertRaisesRegex(ValueError, 'pinned hash'):
            open_device(self.root, manifest, '0000d001', PASSWORD)

    def test_content_that_differs_from_the_manifest_is_refused(self):
        self.write()
        for field, pattern in (('image_sha256', 'image of device 0000d001'), ('mission_sha256', 'mission of device 0000d001'),
                               ('answer_key_sha256', 'answer key of device 0000d001')):
            with self.subTest(field):
                manifest = load_set(self.root, SET, registrations=self.alter(lambda m: m['devices'][0].update({field: '0' * 64})))
                with self.assertRaisesRegex(ValueError, pattern):
                    open_device(self.root, manifest, '0000d001', PASSWORD, answer=True)
                if field == 'answer_key_sha256':  # the key is not decrypted, so not compared, unless it is asked for
                    self.assertEqual(sorted(open_device(self.root, manifest, '0000d001', PASSWORD)), ['image', 'mission'])

    def test_sealing_a_device_writes_two_encrypted_files_and_returns_its_manifest_entry(self):
        root = self.base / 'new' / 'set'  # neither it nor its devices folder exists
        image, mission, answer_key = b'\x00image bytes', fixture_mission('0000d007'), FIXTURE_ANSWER_KEY
        entry = seal_device(root, {'id': '0000d007', 'level': 3, 'framing': 'register-access'}, image=image,
                            mission=mission, answer_key=answer_key, password=PASSWORD)
        files = {name: (root / 'devices' / ('0000d007.' + name + '.enc')).read_bytes() for name in ('inputs', 'answer')}
        self.assertEqual(sorted(path.name for path in (root / 'devices').iterdir()), ['0000d007.answer.enc', '0000d007.inputs.enc'])
        self.assertEqual(entry, {
            'id': '0000d007', 'level': 3, 'framing': 'register-access', 'image_sha256': hashlib.sha256(image).hexdigest(),
            'mission_sha256': canonical_digest(mission), 'answer_key_sha256': canonical_digest(answer_key),
            'inputs': {'path': 'devices/0000d007.inputs.enc', 'sha256': hashlib.sha256(files['inputs']).hexdigest()},
            'answer': {'path': 'devices/0000d007.answer.enc', 'sha256': hashlib.sha256(files['answer']).hexdigest()}})
        for name, secret in (('inputs', b'reuse_objective'), ('answer', answer_key['nonce'].encode())):
            self.assertNotIn(secret, files[name])
        sha = write_manifest(root, SET, [entry])  # a sealed device opens under its own manifest entry
        opened = open_device(root, load_set(root, SET, registrations={SET: sha}), '0000d007', PASSWORD, answer=True)
        self.assertEqual(opened, {'image': image, 'mission': mission, 'answer_key': answer_key})

    def test_a_device_that_a_manifest_could_not_list_is_not_sealed(self):
        good = {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}
        content = {'image': FIXTURE_IMAGE, 'mission': fixture_mission('0000d001'), 'answer_key': FIXTURE_ANSWER_KEY,
                   'password': PASSWORD}
        rows = [('a path for an id', {**good, 'id': '../escape'}, {}, 'Invalid device id'),
                ('an uppercase id', {**good, 'id': '0000D001'}, {}, 'Invalid device id'),
                ('an id that is no eight hexadecimal digits', {**good, 'id': 'nul'}, {}, 'Invalid device id'),
                ('no id', {'level': 1, 'framing': 'text-lines'}, {}, 'Invalid device id'),
                ('level 0', {**good, 'level': 0}, {}, 'level'), ('a bool level', {**good, 'level': True}, {}, 'level'),
                ('level 6', {**good, 'level': 6}, {}, 'level'),
                ('a framing that does not exist', {**good, 'framing': 'morse'}, {}, 'framing'),
                ('a text image', good, {'image': 'text'}, 'image'), ('a list mission', good, {'mission': []}, 'mission'),
                ('no answer key', good, {'answer_key': None}, 'answer key')]
        for name, device, changes, pattern in rows:
            with self.subTest(name), self.assertRaisesRegex(ValueError, pattern):
                seal_device(self.root, device, **{**content, **changes})
        self.assertEqual(list(self.root.rglob('*')), [])  # nothing was written

    def test_a_device_whose_answer_key_breaks_the_interface_is_not_sealed(self):
        key = {**copy.deepcopy(FIXTURE_ANSWER_KEY), 'note': 'a field the interface does not define'}
        with self.assertRaisesRegex(ValueError, 'Invalid generated answer key'):
            seal_device(self.root, {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}, image=FIXTURE_IMAGE,
                        mission=fixture_mission('0000d001'), answer_key=key, password=PASSWORD)
        self.assertEqual(list(self.root.rglob('*')), [])  # nothing was written

    def test_a_device_whose_answer_key_breaks_the_interface_does_not_open(self):
        with patch.object(generated_sets, 'validate_device'):  # a set written without the validator
            entry = seal_device(self.root, {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}, image=FIXTURE_IMAGE,
                                mission=fixture_mission('0000d001'),
                                answer_key={**FIXTURE_ANSWER_KEY, 'note': 'a field the interface does not define'},
                                password=PASSWORD)
        manifest = load_set(self.root, SET, registrations={SET: write_manifest(self.root, SET, [entry])})
        with self.assertRaisesRegex(ValueError, 'Invalid generated answer key'):
            open_device(self.root, manifest, '0000d001', PASSWORD, answer=True)

    def test_content_that_json_cannot_carry_is_refused_before_anything_is_written(self):
        good = {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}
        content = {'image': FIXTURE_IMAGE, 'mission': {}, 'answer_key': {}, 'password': PASSWORD}
        for name, changes in (('a set in the mission', {'mission': {'tasks': {1, 2}}}),
                              ('a set in the answer key', {'answer_key': {'cells': {1, 2}}})):
            with self.subTest(name), self.assertRaises(TypeError):
                seal_device(self.root, good, **{**content, **changes})
        self.assertEqual(list(self.root.rglob('*')), [])

    def test_the_manifest_is_written_with_a_trailing_newline_and_its_hash_returned(self):
        sha = write_fixture_set(self.root)
        data = (self.root / 'manifest.json').read_bytes()
        self.assertEqual(sha, hashlib.sha256(data).hexdigest())
        self.assertTrue(data.endswith(b'}\n'))
        manifest = json.loads(data)
        self.assertEqual((manifest['schema'], manifest['set'], [device['id'] for device in manifest['devices']]),
                         ('generated-set-manifest/1', SET, ['0000d001', '0000d002']))

    def test_a_manifest_that_load_set_would_refuse_is_not_written(self):
        entry = seal_device(self.root, {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}, image=FIXTURE_IMAGE,
                            mission=fixture_mission('0000d001'), answer_key=FIXTURE_ANSWER_KEY, password=PASSWORD)
        for name, devices, pattern in (('a duplicate id', [entry, entry], 'Duplicate device id'),
                                       ('a path out of the set',
                                        [{**entry, 'inputs': {**entry['inputs'], 'path': '../0000d001.inputs.enc'}}], 'set path')):
            with self.subTest(name), self.assertRaisesRegex(ValueError, pattern):
                write_manifest(self.root, SET, devices)
            self.assertFalse((self.root / 'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()

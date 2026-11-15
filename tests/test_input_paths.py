"""The input paths (docs/features/kit-fixes/07-input-paths.md): documents acquired beside the firmware reach
interpretation, and a generic run with no single image but a device binding interprets device first.

In-process runs with scripted workers that call the real tools through the configurator's gateway; temporary homes
only, no model, no real hardware and no network."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from input_path_fixtures import RunCase, have_pypdf, pdf_bytes, sha256


def digest(path):
    from generative_driver.benchmark_support.integrity import digest as tree_digest
    return tree_digest(path)


class DocumentImportTests(RunCase):
    """07-B1: a generic acquire worker imports a document the operator passed with the run."""

    def acquire(self, files, calls):
        """The acquire worker's tool list and the results of `calls(worker)` in a run passed `files` ({name: bytes}),
        with the run's result."""
        inputs = {}
        for name, data in files.items():
            (self.root / name).write_bytes(data)
            inputs[name] = str(self.root / name)
        seen = {}

        def acquire(worker):
            seen['tools'] = worker.tools()
            seen['given'] = [Path(path).name for path in worker.task['inputs']]
            seen['results'] = calls(worker)
        result = self.run_with({'acquire': acquire}, inputs=inputs)
        return seen, result

    @unittest.skipUnless(have_pypdf(), 'pypdf is not installed')
    def test_07_B1_a_pdf_becomes_page_text_with_an_index_and_the_acquire_handoff_carries_both(self):
        data = pdf_bytes(['ID? answers DEMO-42', 'T answers the temperature in tenths of a degree'])
        seen, result = self.acquire({'manual.pdf': data}, lambda worker: [
            worker.tool('acquire_document', name=Path(worker.task['inputs'][0]).name)])
        self.assertIn('acquire_document', seen['tools'])
        [imported] = seen['results']
        self.assertIs(imported['available'], True, imported)
        self.assertEqual((imported['source'], imported['sha256'], imported['page_count'], imported['format']),
                         (seen['given'][0], sha256(data), 2, 'pdf'))
        pages = Path(imported['pages_dir'])
        self.assertEqual([(pages / name).read_text(encoding='utf-8').strip() for name in ('001.txt', '002.txt')],
                         ['ID? answers DEMO-42', 'T answers the temperature in tenths of a degree'])
        index = json.loads((pages / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual([(entry['page'], entry['first_line']) for entry in index],
                         [(1, 'ID? answers DEMO-42'), (2, 'T answers the temperature in tenths of a degree')])
        self.assertEqual(Path(imported['artifact']).read_bytes(), data)
        # The original and its pages are hashed and recorded, so the acquire handoff carries them.
        [handoff] = self.handoffs(result, 'acquire')
        carried = {artifact['sha256'] for artifact in handoff['artifacts']}
        self.assertLessEqual({sha256(data), digest(pages)}, carried)

    def test_07_B1_a_txt_or_md_file_in_utf8_becomes_one_page(self):
        text = '# Line protocol\n\nSend `ID?`; the device answers `DEMO-42`.\nTemperatures are in °C.\n'
        files = {'notes.md': text.encode('utf-8'), 'readme.txt': 'T replies T:21.5\n'.encode('utf-8')}
        seen, result = self.acquire(files, lambda worker: [worker.tool('acquire_document', name=name)
                                                           for name in sorted(Path(p).name for p in
                                                                              worker.task['inputs'])])
        for imported in seen['results']:
            with self.subTest(imported=str(imported)[:80]):
                self.assertIsInstance(imported, dict, imported)
                self.assertIs(imported['available'], True, imported)
                self.assertEqual((imported['page_count'], imported['format']), (1, 'text'))
                original = files[imported['source'].split('-', 1)[1]]
                self.assertEqual(imported['sha256'], sha256(original))
                self.assertEqual((Path(imported['pages_dir']) / '001.txt').read_bytes(), original)
        [handoff] = self.handoffs(result, 'acquire')
        self.assertEqual(len(handoff['artifacts']), 4)

    def test_07_B1_any_other_path_or_file_is_refused_with_the_reason(self):
        files = {'firmware.bin': b'\x00\x01\x02', 'latin.txt': 'Temp\xe9rature'.encode('latin-1'),
                 'ok.md': b'fine\n'}

        def calls(worker):
            inside = {Path(p).name.split('-', 1)[1]: Path(p).name for p in worker.task['inputs']}
            outside = self.root / 'outside.md'
            outside.write_text('not passed with the run\n', encoding='utf-8')
            names = ['../inputs/' + inside['ok.md'], str(worker.workspace / 'inputs' / inside['ok.md']),
                     'tools/' + inside['ok.md'], str(outside), '', 'never-passed.md', inside['firmware.bin'],
                     inside['latin.txt']]
            return ([(name, worker.tool('acquire_document', name=name)) for name in names]
                    + [('inputs_dir', worker.tool('acquire_document', name=inside['ok.md'],
                                                  inputs_dir=str(self.root)))])
        seen, result = self.acquire(files, calls)
        refused = dict(seen['results'])
        gateway = refused.pop('inputs_dir')
        self.assertIsInstance(gateway, ValueError)
        self.assertIn('configurator', str(gateway))
        for name, answer in refused.items():
            with self.subTest(name=name):
                self.assertIsInstance(answer, dict, answer)
                self.assertIs(answer['available'], False, answer)
                self.assertEqual(answer['_exit'], 1)
                self.assertTrue(answer['reason'])
        self.assertIn('.bin', refused[[n for n in refused if n.endswith('firmware.bin')][0]]['reason'])
        self.assertIn('UTF-8', refused[[n for n in refused if n.endswith('latin.txt')][0]]['reason'])
        # Nothing was imported, so the acquire handoff carries nothing.
        [handoff] = self.handoffs(result, 'acquire')
        self.assertEqual(handoff['artifacts'], [])

    def test_07_B1_without_pypdf_a_pdf_is_answered_unavailable_with_the_reason(self):
        with patch.dict(sys.modules, {'pypdf': None}):
            seen, result = self.acquire({'manual.pdf': pdf_bytes(['one page'])},
                                        lambda worker: [worker.tool('acquire_document',
                                                                    name=Path(worker.task['inputs'][0]).name)])
        [answer] = seen['results']
        self.assertIsInstance(answer, dict, answer)
        self.assertIs(answer['available'], False, answer)
        self.assertIn('pypdf', answer['reason'])
        self.assertEqual(self.handoffs(result, 'acquire')[0]['artifacts'], [])

    def test_07_B1_the_acquire_worker_is_told_how_to_import_documents_passed_with_the_run(self):
        seen, _ = self.acquire({'notes.md': b'one\n'}, lambda worker: [worker.task['context']])
        [context] = seen['results']
        self.assertIn('acquire_document', context.get('documents', ''))
        seen, _ = self.acquire({}, lambda worker: [worker.task['context']])
        self.assertNotIn('documents', seen['results'][0])

    def test_07_B1_the_inputs_folder_is_the_configurators_to_name(self):
        seen, _ = self.acquire({'notes.md': b'one\n'}, lambda worker: [
            next(t for t in worker.controller.call('tools', worker.identity)['tools']
                 if t['name'] == 'acquire_document')])
        [listed] = seen['results']
        self.assertEqual(listed['inputSchema']['required'], ['name'])
        self.assertNotIn('inputs_dir', listed['inputSchema']['properties'])
        self.assertNotIn('run_dir', listed['inputSchema']['properties'])

    def test_07_B1_an_existing_tool_given_a_stray_inputs_dir_ignores_it_as_before(self):
        from generative_driver.toolkit import call_tool
        firmware = b'\x00firmware stand-in\xff'

        def imported(worker):
            return worker.tool('acquire_firmware_artifact', source_path=worker.task['inputs'][0],
                               origin='provided_binary', expected_sha256=sha256(firmware), inputs_dir=str(self.root))
        # Through the gateway: a folder outside the workspace, which only acquire_document's inputs_dir is checked for.
        seen, result = self.acquire({'firmware.bin': firmware}, lambda worker: [imported(worker)])
        [answer] = seen['results']
        self.assertIsInstance(answer, dict, answer)
        self.assertIs(answer.get('available'), True, answer)
        self.assertEqual({a['sha256'] for a in self.handoffs(result, 'acquire')[0]['artifacts']} & {sha256(firmware)},
                         {sha256(firmware)})
        # Through call_tool: a relative path, which only acquire_document's inputs_dir must not be.
        (self.root / 'direct.bin').write_bytes(firmware)
        arguments = {'run_dir': str(self.root / 'direct'), 'inputs_dir': 'relative/inputs'}
        direct = call_tool('acquire_firmware_artifact', {
            **arguments, 'source_path': str(self.root / 'direct.bin'), 'origin': 'provided_binary',
            'expected_sha256': sha256(firmware)})
        self.assertIs(direct.get('available'), True, direct)
        refused = call_tool('acquire_document', {**arguments, 'name': 'notes.md'})
        self.assertEqual(refused.get('error'), 'inputs_dir must be an absolute path', refused)


SPLIT_SPEC = ('# Demo line protocol\n\nIntroduction.\n\n## Identify\n\nSend `ID?`; the device answers its name.\n\n'
              '## Measure\n\nSend `T`; the reply is `T:` and the temperature in degrees Celsius.\n').encode('utf-8')
PLAIN_SPEC = 'Framing: each request and reply is one line ending in a line feed.\n'.encode('utf-8')


class ProtocolSpecTests(RunCase):
    """07-B2: a protocol specification acquire_protocol_spec fetched is recorded and handed on."""

    def test_07_B2_its_source_files_and_sections_are_recorded_and_the_acquire_handoff_carries_them(self):
        from input_path_fixtures import protocol_registry
        seen = {}
        with protocol_registry(self.root / 'registry', {'demo-line': [
                {'name': 'protocol.md', 'data': SPLIT_SPEC, 'split': '\n## '},
                {'name': 'framing.txt', 'data': PLAIN_SPEC}]}):
            result = self.run_with({'acquire': lambda worker: seen.update(
                fetched=worker.tool('acquire_protocol_spec', key='demo-line'))})
        fetched = seen['fetched']
        self.assertIs(fetched['available'], True, fetched)
        sections = Path(fetched['sections_dir'])
        self.assertEqual(fetched['section_count'], 3)
        [handoff] = self.handoffs(result, 'acquire')
        carried = {artifact['sha256']: Path(artifact['path']) for artifact in handoff['artifacts']}
        self.assertLessEqual({sha256(SPLIT_SPEC), sha256(PLAIN_SPEC), digest(sections)}, set(carried), handoff)
        self.assertEqual(carried[sha256(PLAIN_SPEC)].read_bytes(), PLAIN_SPEC)
        self.assertEqual(sorted(p.name for p in carried[digest(sections)].iterdir()),
                         ['000.txt', '001.txt', '002.txt', 'index.json'])
        # The configurator recorded each file when the tool returned, as it records a datasheet's pages.
        [call] = [e['data'] for e in self.events('tool.finished') if e['data']['name'] == 'acquire_protocol_spec']
        self.assertEqual(sorted(call['artifacts'].values()),
                         sorted([sha256(SPLIT_SPEC), sha256(PLAIN_SPEC), digest(sections)]))


FIRMWARE = b'\x00\x01\x02\x03 canned firmware bytes for the input paths'
DATASHEET_URL = 'https://vendor.example/demo-42-datasheet.pdf'
DATASHEET_PAGES = ['DEMO-42 datasheet: overview', 'Commands: ID? returns the part name', 'T returns the temperature']
NOT_TEXT = b'%PDF-1.4\n\xe2\xe3\xcf\xd3 a protocol source that is neither split nor UTF-8\n'


def model(name='DEMO-42', expect='DEMO-42\n'):
    """A line model of the canned device: identify, and measure the temperature."""
    from interpret_task_fixtures import IDENTIFY, MEASURE
    identify = json.loads(json.dumps(IDENTIFY))
    identify['steps'][0]['expect'] = {'equals_hex': expect.encode().hex()}
    return {'schema': 'interface-model/3', 'device': {'name': name}, 'channel': {'type': 'tcp'},
            'identity': {'operation': 'identify'}, 'operations': {'identify': identify, 'measure': MEASURE},
            'provenance': [{'item': 'operations.identify', 'source': 'documents/1-datasheet/002.txt'},
                           {'item': 'operations.measure', 'source': 'documents/1-datasheet/003.txt'}]}


class DocumentsTests(RunCase):
    """07-B3: every interpret worker of a generic run gets the supplementary documents of the latest acquire handoff."""

    def files(self, names):
        inputs = {}
        for name, data in names.items():
            (self.root / name).write_bytes(data)
            inputs[name] = str(self.root / name)
        return inputs

    def acquire(self, results):
        """An acquire worker that imports each input: the firmware, and each other file as a document; then fetches the
        stand-in datasheet and the stand-in protocol specification. Each result is kept in `results`."""
        def work(worker):
            for path in worker.task['inputs']:
                if path.endswith('.bin'):
                    results.append(worker.tool('acquire_firmware_artifact', source_path=path, origin='provided_binary',
                                               expected_sha256=sha256(Path(path).read_bytes())))
                else:
                    results.append(worker.tool('acquire_document', name=Path(path).name))
            results.append(worker.tool('acquire_datasheet', url=DATASHEET_URL,
                                       expected_sha256=sha256(pdf_bytes(DATASHEET_PAGES))))
            results.append(worker.tool('acquire_protocol_spec', key='demo-line'))
        return work

    @staticmethod
    def interpret(seen, written=None):
        """An interpret worker that keeps what its workspace held and writes `written` (the canned model) as
        model.json."""
        def work(worker):
            listing = worker.workspace / 'DOCUMENTS.json'
            seen.append({'workspace': worker.workspace,
                         'files': sorted(p.relative_to(worker.workspace).as_posix()
                                         for p in worker.workspace.rglob('*') if p.is_file()),
                         'documents': json.loads(listing.read_text(encoding='utf-8')) if listing.is_file() else None,
                         'tasks': json.loads((worker.workspace / 'TASKS.json').read_text(encoding='utf-8')),
                         'text': {p.relative_to(worker.workspace).as_posix(): p.read_bytes()
                                  for p in (worker.workspace / 'documents').rglob('*') if p.is_file()}})
            worker.write('model.json', written or model())
        return work

    @unittest.skipUnless(have_pypdf(), 'pypdf is not installed')
    def test_07_B3_every_interpret_worker_gets_the_documents_listed_with_kind_source_hash_pages_and_sizes(self):
        from input_path_fixtures import network, protocol_registry
        from line_device import line_device
        manual = pdf_bytes(['Manual page one: wiring', 'Manual page two: the reply T:21.5 is in degrees Celsius'])
        notes = 'Operator notes: the device answers within 50 ms.\n'.encode('utf-8')
        acquired, seen = [], []

        def probe(worker):
            given = next(Path(p) for p in worker.task['inputs'] if (Path(p) / 'model.json').is_file())
            worker.tool('probe_run', model_dir=str(given), operation='identify', n=1)
        with network({DATASHEET_URL: pdf_bytes(DATASHEET_PAGES)}), \
                protocol_registry(self.root / 'registry', {'demo-line': [
                    {'name': 'protocol.md', 'data': SPLIT_SPEC, 'split': '\n## '},
                    {'name': 'framing.txt', 'data': PLAIN_SPEC}, {'name': 'reference.pdf', 'data': NOT_TEXT}]}), \
                line_device({b'ID?': b'OTHER-7\n', b'T': b'T:21.5\n'}) as binding:
            result = self.run_with({'acquire': self.acquire(acquired), 'interpret': self.interpret(seen),
                                    'probe': probe}, binding=binding, max_revisions=1,
                                   inputs=self.files({'firmware.bin': FIRMWARE, 'manual.pdf': manual,
                                                      'notes.md': notes}))
        self.assertTrue(all(isinstance(r, dict) and r.get('available') for r in acquired), acquired)
        # The first attempt and the repair the device's different identity sent the run back to.
        self.assertEqual([h['revision'] for h in self.handoffs(result, 'interpret')], [0, 1], result['reason'])
        self.assertEqual(len(seen), 2)
        listing = seen[0]['documents']
        self.assertIsNotNone(listing, seen[0]['files'])
        self.assertEqual(seen[1]['documents'], listing)
        documents = listing['documents']
        self.assertEqual([(d['kind'], d['source']) for d in documents], [
            ('supplied', '1-manual.pdf'), ('supplied', '2-notes.md'), ('datasheet', DATASHEET_URL),
            ('protocol-spec', 'https://specs.example/demo-line/protocol.md'),
            ('protocol-spec', 'https://specs.example/demo-line/framing.txt'),
            ('protocol-spec', 'https://specs.example/demo-line/reference.pdf')])
        self.assertEqual([d['sha256'] for d in documents],
                         [sha256(manual), sha256(notes), sha256(pdf_bytes(DATASHEET_PAGES)), sha256(SPLIT_SPEC),
                          sha256(PLAIN_SPEC), sha256(NOT_TEXT)])
        self.assertEqual([len(d['pages']) for d in documents], [2, 1, 3, 3, 1, 0])
        for attempt in seen:
            text = attempt['text']
            for document in documents:
                for page in document['pages']:
                    with self.subTest(path=page['path']):
                        self.assertTrue(page['path'].startswith('documents/'))
                        self.assertEqual(len(text[page['path']]), page['bytes'])
            # Text only, never a PDF, and nothing else under documents/.
            self.assertEqual(sorted(text), sorted(p['path'] for d in documents for p in d['pages']))
            self.assertFalse([name for name in text if not name.endswith('.txt')])
            self.assertIn('DOCUMENTS.json', attempt['files'])
        pages = {p['path']: p for d in documents for p in d['pages']}
        text = seen[0]['text']
        self.assertIn(b'degrees Celsius', text[documents[0]['pages'][1]['path']])
        self.assertEqual(text[documents[1]['pages'][0]['path']], notes)
        self.assertIn(b'ID? returns the part name', text[documents[2]['pages'][1]['path']])
        self.assertIn(b'## Measure', text[documents[3]['pages'][2]['path']])
        self.assertEqual(text[documents[4]['pages'][0]['path']], PLAIN_SPEC)
        self.assertEqual(sum(page['bytes'] for page in pages.values()), listing['forwarded_bytes'])
        # The source that is neither split nor UTF-8 is listed as left out.
        self.assertEqual([(entry['document'], 'page' in entry) for entry in listing['left_out']],
                         [(documents[5]['document'], False)])
        self.assertIn('UTF-8', listing['left_out'][0]['reason'])
        # TASKS.json says what the documents are evidence of, that firmware and device win, and how to cite a page.
        entry = seen[0]['tasks']['documents']
        self.assertEqual((entry['index'], entry['folder']), ('DOCUMENTS.json', 'documents/'))
        for words in ('meanings, units and framing', 'firmware', 'device', 'NOTES.md', 'uncertaint', 'provenance',
                      'path'):
            self.assertIn(words, entry['instructions'])
        # Documents are sealed inputs: none becomes part of the frozen output.
        for handoff in self.handoffs(result, 'interpret'):
            [frozen] = [Path(a['path']) for a in handoff['artifacts']]
            self.assertTrue((frozen / 'model.json').is_file())
            self.assertFalse((frozen / 'documents').exists() or (frozen / 'DOCUMENTS.json').exists(),
                             sorted(p.name for p in frozen.iterdir()))

    def test_07_B3_over_the_bound_pages_are_taken_round_robin_and_each_page_left_out_is_named(self):
        from input_path_fixtures import protocol_registry
        # Three documents: sections of 100, 100 and 10 bytes; one page of 100; sections of 100 and 300.
        first = b'a' * 100 + b'\n## ' + b'b' * 96 + b'\n## ' + b'c' * 6
        second = b'd' * 100
        third = b'e' * 100 + b'\n## ' + b'f' * 296
        seen = []
        with patch('generative_driver.supplementary_documents.BOUND_BYTES', 450), \
                protocol_registry(self.root / 'registry', {'demo-line': [
                    {'name': 'first.md', 'data': first, 'split': '\n## '}, {'name': 'second.txt', 'data': second},
                    {'name': 'third.md', 'data': third, 'split': '\n## '}]}):
            self.run_with({'acquire': lambda worker: [
                worker.tool('acquire_firmware_artifact', source_path=worker.task['inputs'][0],
                            origin='provided_binary', expected_sha256=sha256(FIRMWARE)),
                worker.tool('acquire_protocol_spec', key='demo-line')], 'interpret': self.interpret(seen)},
                inputs=self.files({'firmware.bin': FIRMWARE}))
        listing = seen[0]['documents']
        self.assertIsNotNone(listing, seen[0]['files'])
        # Round-robin: first 1, second 1, third 1, first 2 (400 bytes); third 2 would pass 450, so the taking stops
        # there and first 3 is left out too, though it would fit: each document loses pages only from its end.
        self.assertEqual([[(p['page'], p['bytes']) for p in d['pages']] for d in listing['documents']],
                         [[(1, 100), (2, 100)], [(1, 100)], [(1, 100)]])
        self.assertEqual((listing['bound_bytes'], listing['forwarded_bytes']), (450, 400))
        named = [(listing['documents'][e['document'] - 1]['source'].rsplit('/', 1)[1], e['page'], e['bytes'])
                 for e in listing['left_out']]
        self.assertEqual(named, [('third.md', 2, 300), ('first.md', 3, 10)])
        self.assertEqual(sum(len(data) for data in seen[0]['text'].values()), 400)


class TaskDocumentsTests(RunCase):
    """07-B4: in task mode the inventory, each handler task and the cross-check get the documents they need, bounded."""

    def test_07_B4_each_task_gets_documents_json_and_its_pages_within_the_bound_and_ignores_what_names_none(self):
        from input_path_fixtures import protocol_registry
        from interpret_task_fixtures import FRAGMENTS, INVENTORY, cortex_m_image, stand_in_ghidra
        ghidra, java = stand_in_ghidra(self.root)
        image = cortex_m_image()
        # Two one-page documents (50 and 60 bytes) and a specification of three 100-byte sections.
        spec = b's' * 100 + b'\n## ' + b't' * 96 + b'\n## ' + b'u' * 96
        section = lambda number: 'documents/3-protocol-spec/%03d.txt' % number
        inventory = json.loads(json.dumps(INVENTORY))
        commands = {command['name']: command for command in inventory['commands']}
        commands['identify']['evidence'] += [section(2)]
        commands['measure']['evidence'] += [section(1), section(2), section(3), 'documents/7-supplied/001.txt']
        seen = {}

        def interpret(worker):
            task = json.loads((worker.workspace / 'TASK.json').read_text(encoding='utf-8')) \
                if (worker.workspace / 'TASK.json').is_file() else {}
            key = task.get('kind', 'single') + ':' + str(task.get('command'))
            seen[key] = {'task': task, 'documents': sorted(p.relative_to(worker.workspace).as_posix() for p in
                                                           (worker.workspace / 'documents').rglob('*.txt')),
                         'listing': (worker.workspace / 'DOCUMENTS.json').is_file()}
            if task.get('kind') == 'inventory':
                worker.write('inventory.json', inventory)
            elif task.get('kind') == 'handler':
                worker.write('fragment.json', FRAGMENTS[task['command']])
        files = {'firmware.bin': image, 'a.md': b'a' * 50, 'b.md': b'b' * 60}
        for name, data in files.items():
            (self.root / name).write_bytes(data)
        with patch('generative_driver.supplementary_documents.TASK_BOUND_BYTES', 260), \
                protocol_registry(self.root / 'registry', {'demo-line': [
                    {'name': 'spec.md', 'data': spec, 'split': '\n## '}]}):
            result = self.run_with({'acquire': lambda worker: [
                worker.tool('acquire_firmware_artifact', source_path=path, origin='provided_binary',
                            expected_sha256=sha256(image)) if path.endswith('.bin') else
                worker.tool('acquire_document', name=Path(path).name) for path in worker.task['inputs']] + [
                worker.tool('acquire_protocol_spec', key='demo-line')], 'interpret': interpret},
                inputs={name: str(self.root / name) for name in files}, interpretation='tasks',
                ghidra_home=ghidra, java_home=java)
        notes = self.handoffs(result, 'interpret')[-1]['notes']['inline']['configurator']['interpretation']
        self.assertEqual(notes['mode'], 'tasks', notes)
        self.assertEqual(sorted(seen), ['cross-check:None', 'handler:identify', 'handler:measure', 'inventory:None'])
        self.assertTrue(all(record['listing'] for record in seen.values()), seen)
        # The inventory: every document's first page, then the second ones, until the next would pass 260 bytes.
        inventory_seen = seen['inventory:None']
        self.assertEqual(inventory_seen['documents'], ['documents/1-supplied/001.txt', 'documents/2-supplied/001.txt',
                                                       section(1)])
        self.assertEqual((inventory_seen['task']['documents']['bound_bytes'],
                          inventory_seen['task']['documents']['left_out']), (260, [section(2), section(3)]))
        # A handler task: the pages its command cites, within the bound; one page that names nothing is ignored.
        self.assertEqual(seen['handler:identify']['documents'], [section(2)])
        self.assertEqual(seen['handler:measure']['documents'], [section(1), section(2)])
        self.assertEqual(seen['handler:measure']['task']['documents']['left_out'], [section(3)])
        # The cross-check: every page any command cites, within the bound.
        self.assertEqual(seen['cross-check:None']['documents'], [section(1), section(2)])
        self.assertEqual(seen['cross-check:None']['task']['documents']['left_out'], [section(3)])
        ignored = [note for note in notes['notes'] if 'documents/7-supplied/001.txt' in note]
        self.assertEqual(len(ignored), 1, notes['notes'])
        self.assertIn('ignored', ignored[0])


DEVICE = {b'ID?': b'DEMO-42\n', b'T': b'T:21.5\n'}
DEVICE_FIRST_TOOLS = ['model_qualify', 'interface_describe', 'interface_execute', 'probe_run']


def interpretation(result, revision=None):
    handoffs = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret'
                and (revision is None or h['revision'] == revision)]
    return handoffs[-1]['notes']['inline']['configurator'].get('interpretation')


class DeviceFirstChoiceTests(RunCase):
    """07-B5: a generic run with no single image and a device binding interprets device first instead of skipping."""

    def device_first_run(self, *, binding=True, script=None, **params):
        from line_device import line_device
        seen = []

        def interpret(worker):
            seen.append({'tools': worker.request.allowed_tools, 'files': sorted(
                p.relative_to(worker.workspace).as_posix() for p in worker.workspace.rglob('*')
                if p.is_file() and '_toolkit' not in p.parts)})
            worker.write('model.json', model())
        with line_device(DEVICE) as served:
            result = self.run_with({'interpret': interpret, **(script or {})},
                                   **({'binding': served} if binding else {}), **params)
        return result, seen

    def test_07_B5_no_image_and_a_binding_interprets_device_first_and_its_notes_say_how(self):
        result, seen = self.device_first_run()
        self.assertEqual(len(seen), 1, result['reason'])
        self.assertEqual(sorted(seen[0]['tools']), sorted(DEVICE_FIRST_TOOLS))
        self.assertNotIn('image.bin', seen[0]['files'])
        notes = interpretation(result)
        self.assertEqual({key: notes[key] for key in ('requested', 'mode', 'candidate_images')},
                         {'requested': 'single', 'mode': 'device', 'candidate_images': 0})
        self.assertEqual((notes['kit_analysis']['made'], notes['kit_analysis']['cause']), (False, 'no_single_image'))
        self.assertIn('no single firmware image', notes['kit_analysis']['reason'].lower())
        self.assertFalse([e for e in self.events('stage.skipped') if e['data']['stage'] == 'interpret'])

    def test_07_B5_without_a_binding_interpretation_is_skipped_as_today(self):
        result, seen = self.device_first_run(binding=False)
        self.assertEqual(seen, [])
        [skipped] = [e['data'] for e in self.events('stage.skipped') if e['data']['stage'] == 'interpret']
        self.assertEqual(skipped['reason'],
                         'Interpretation skipped: no single acquired binary to interpret (0 candidates)')

    def test_07_B5_with_several_images_none_is_given_to_the_worker_and_the_notes_say_so(self):
        from input_path_fixtures import protocol_registry
        with protocol_registry(self.root / 'registry', {'two-images': [
                {'name': 'first.bin', 'data': b'\x00first image\xff'},
                {'name': 'second.elf', 'data': b'\x7fELF\xfe'}]}):
            result, seen = self.device_first_run(script={'acquire': lambda worker: worker.tool(
                'acquire_protocol_spec', key='two-images')})
        self.assertEqual(len(seen), 1, result['reason'])
        self.assertFalse([name for name in seen[0]['files'] if name.endswith(('.bin', '.elf'))], seen[0]['files'])
        notes = interpretation(result)
        self.assertEqual((notes['mode'], notes['candidate_images']), ('device', 2))
        self.assertTrue([note for note in notes['notes'] if '2 firmware images' in note and 'none' in note], notes)

    def test_07_B5_a_run_started_for_tasks_interprets_device_first_as_one_worker_with_a_note(self):
        result, seen = self.device_first_run(interpretation='tasks')
        self.assertEqual(len(seen), 1, result['reason'])
        notes = interpretation(result)
        self.assertEqual((notes['requested'], notes['mode']), ('tasks', 'device'))
        self.assertTrue([note for note in notes['notes'] if 'tasks' in note and 'one worker' in note], notes)
        self.assertEqual(self.events('task.started'), [])


def contract_for(written):
    """The contract a worker writes for `written` with the workspace's contract.py, as CONTRACT.md asks."""
    from interface_runtime.contracts import from_model
    return from_model(written)[0]


class DeviceFirstWorkerTests(RunCase):
    """07-B6: the device-first worker's sealed inputs, tools, instructions and output."""

    def test_07_B6_its_sealed_inputs_tools_and_prompt_and_its_output_handed_off_as_an_interpret_workers(self):
        from line_device import line_device
        seen = {}

        def interpret(worker):
            seen.update(prompt=worker.request.prompt, tools=list(worker.request.allowed_tools),
                        tasks=json.loads((worker.workspace / 'TASKS.json').read_text(encoding='utf-8')),
                        instructions=(worker.workspace / 'DEVICE_FIRST.md').read_text(encoding='utf-8')
                        if (worker.workspace / 'DEVICE_FIRST.md').is_file() else None)
            worker.write('model.json', model())
            worker.write('replies.json', {'schema': 'interface-replies/1', 'operations': {
                'identify': {'steps': [['DEMO-42']]}, 'measure': {'steps': [['T:21.5']]}}})
            worker.write('contract.json', contract_for(model()))
            worker.write('NOTES.md', 'Ran identify and measure on the device.\n')
        (self.root / 'notes.md').write_text('The temperature reply is in degrees Celsius.\n', encoding='utf-8')
        with line_device(DEVICE) as served:
            result = self.run_with({'acquire': lambda worker: worker.tool('acquire_document', name=Path(
                worker.task['inputs'][0]).name), 'interpret': interpret}, binding=served, effects=['read', 'write'],
                inputs={'notes.md': str(self.root / 'notes.md')})
        [assignment] = self.assigned('interpret')
        workspace = Path(assignment['workspace'])
        sealed = {Path(path).relative_to(workspace).as_posix() for path in assignment['inputs']}
        for name in ('DEVICE_FIRST.md', 'TASKS.json', 'INTERFACE_MODEL.md', 'CONTRACT.md', 'contract.py',
                     'validate.py', 'qualify.py', 'DOCUMENTS.json', 'documents/1-supplied/001.txt'):
            self.assertIn(name, sealed)
        for name in ('image.bin', 'ANALYSIS_TOOLS.json', 'ghidra_run.py', 'ExportDecomp.java', 'SeedCortexM.java',
                     'image_map_check.py', 'ANALYSIS_INDEX.json'):
            self.assertNotIn(name, sealed)
        self.assertEqual(sorted(seen['tools']), sorted(DEVICE_FIRST_TOOLS))
        # TASKS.json: no firmware, the model from the device, reads only, and probe and ground under the run's grants.
        first = seen['tasks']['device_first']
        self.assertIsNone(first['firmware'])
        for words in ('no firmware', 'from the device', 'only operations you declare read', 'Probe and ground',
                      "the run's grants (read, write)", 'not_exercised'):
            self.assertIn(words, first['instructions'])
        self.assertEqual(seen['tasks']['effect_grants'], ['read', 'write'])
        self.assertIn("The device's replies are the truth", seen['tasks']['documents']['instructions'])
        # Its own instructions and prompt, never the interpret prompt that says the worker has no device access.
        self.assertIsNotNone(seen['instructions'])
        for name in ('model.json', 'replies.json', 'NOTES.md', 'contract.json', 'interface_execute', 'not_exercised'):
            self.assertIn(name, seen['instructions'])
        self.assertIn('DEVICE_FIRST.md', seen['prompt'])
        self.assertNotIn('no access to the device', seen['prompt'])
        # Collected and checked as an interpret worker's output, and handed off as the stage's one handoff.
        [handoff] = self.handoffs(result, 'interpret')
        [frozen] = [Path(artifact['path']) for artifact in handoff['artifacts']]
        self.assertLessEqual({'model.json', 'replies.json', 'contract.json', 'NOTES.md'},
                             {path.name for path in frozen.iterdir()})
        checks = {check['name']: check['passed'] for check in handoff['checks']}
        self.assertEqual(checks, {'sealed_model_collection': True, 'executable_model_contract': True})
        [probe] = self.assigned('probe')
        self.assertEqual(list(probe['inputs'].values()), [handoff['artifacts'][0]['sha256']])

    def test_07_B6_it_gets_the_listen_evidence_and_binding_context_it_would_get_today(self):
        from input_path_fixtures import fake_serial
        seen = {}

        def interpret(worker):
            for name in ('serial-listen.json', 'BINDING_CONTEXT.json'):
                path = worker.workspace / name
                seen[name] = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
            worker.write('model.json', model())
        with patch.dict(sys.modules, {'serial': fake_serial(boot=b'DEMO-42 booting\r\n')}):
            self.run_with({'acquire': lambda worker: worker.tool('acquire_serial_listen', seconds=0.3),
                           'interpret': interpret}, binding={'port': '/dev/stand-in-serial'})
        self.assertEqual((seen['serial-listen.json'] or {}).get('schema'), 'serial-listen/1', seen)
        self.assertEqual(seen['BINDING_CONTEXT.json']['runtime_channel'],
                         {'type': 'uart', 'baudrate': 115200, 'resets_on_open': True})
        self.assertEqual(interpretation(self.controller.call('result', self.run))['mode'], 'device')


def effectful_model(no_effect=False):
    """The canned line model with operations the device-first worker may declare but not run: a write and an
    actuation, and with `no_effect` one that declares no effect (which the runtime would refuse as invalid)."""
    from interpret_task_fixtures import _exchange
    written = model()
    written['operations'].update({
        'set_rate': {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [_exchange('R=1\n', 'ack')]},
        'beep': {'effect': 'actuate', 'parameters': {}, 'outputs': {}, 'steps': [_exchange('B\n', 'ack')]}})
    if no_effect:
        written['operations']['mystery'] = {'parameters': {}, 'outputs': {}, 'steps': [_exchange('M\n', 'ack')]}
    written['provenance'] += [{'item': 'operations.' + name, 'source': 'documents/1-supplied/001.txt'}
                              for name in written['operations'] if name not in ('identify', 'measure')]
    return written


def serial_model():
    """The canned model on a UART that resets when opened and prints a boot line first."""
    return {**model(), 'channel': {'type': 'uart', 'baudrate': 115200, 'resets_on_open': True,
                                   'boot': {'line_prefix': 'DEMO-42 booting', 'timeout_ms': 1000}}}


class DeviceFirstLimitTests(RunCase):
    """07-B7: the device-first worker runs only read operations; its calls are recorded and listed, never handed on."""

    def calls_on(self, written, calls, device=None, **params):
        """(the results of `calls(worker)` by the interpret worker after it wrote `written`, the request lines the line
        device saw before each call and after the last, the run's result)."""
        from line_device import line_device
        seen, lines = {}, []

        def interpret(worker):
            worker.write('model.json', written)
            seen['results'], seen['lines'] = [], []
            for call in calls:
                seen['lines'].append(list(lines))
                seen['results'].append(call(worker))
            seen['lines'].append(list(lines))
        with line_device(device or DEVICE, lines) as served:
            result = self.run_with({'interpret': interpret}, binding=served, **params)
        return seen['results'], seen['lines'], result

    def test_07_B7_an_operation_declared_write_or_actuate_or_with_no_effect_is_refused_before_any_device_io(self):
        def run(tool, operation, written=None):
            def call(worker):
                if written:
                    worker.write('model.json', written)
                return worker.tool(tool, model_dir=str(worker.workspace), operation=operation)
            return call
        device = {**DEVICE, b'R=1': b'OK\n', b'B': b'OK\n', b'M': b'OK\n'}
        calls = [run('interface_execute', 'set_rate'), run('interface_execute', 'beep'),
                 run('interface_execute', 'mystery', effectful_model(no_effect=True)),
                 run('probe_run', 'set_rate', effectful_model()), run('interface_execute', 'identify')]
        results, lines, result = self.calls_on(effectful_model(), calls, device, effects=['read', 'write', 'actuate'])
        # A ValueError in sentence case, with no final period and the operation and its effect after a colon.
        for refused, offending in zip(results[:4], ("'set_rate' declares 'write'", "'beep' declares 'actuate'",
                                                    "'mystery' declares no effect", "'set_rate' declares 'write'")):
            with self.subTest(offending=offending):
                self.assertIsInstance(refused, ValueError, refused)
                self.assertEqual(str(refused), "Device-first interpretation runs only operations declared read, "
                                               "whatever the run's grants; nothing was dispatched: " + offending)
        # Nothing reached the device until the read operation ran, and the run's grants did not change that.
        self.assertEqual(lines[4], [])
        self.assertIs(results[4]['ok'], True, results[4])
        self.assertEqual(lines[5], [b'ID?'])
        refusals = [e['data'] for e in self.events('tool.refused') if e['data']['stage'] == 'interpret']
        self.assertEqual([r['name'] for r in refusals], ['interface_execute'] * 3 + ['probe_run'])
        self.assertEqual([e['data']['name'] for e in self.events('tool.started') if e['data']['stage'] == 'interpret'],
                         ['interface_execute'])

    def test_07_B7_its_effects_are_read_plus_reset_when_the_run_grants_it(self):
        for grants, expected in ((['read', 'write', 'actuate'], ['read']),
                                 (['read', 'write', 'reset'], ['read', 'reset'])):
            with self.subTest(grants=grants):
                self.calls_on(model(), [], effects=grants)
                self.assertEqual(self.assigned('interpret')[0]['effects'], expected)
                self.assertEqual(self.assigned('probe')[0]['effects'], grants)

    def test_07_B7_a_serial_device_opens_after_a_listen_or_the_reset_grant_and_one_resetting_on_open_needs_it(self):
        from input_path_fixtures import fake_serial
        execute = lambda worker: worker.tool('interface_execute', model_dir=str(worker.workspace), operation='identify')
        listen = lambda worker: worker.tool('acquire_serial_listen', seconds=0.3)
        seen = []

        def interpret(worker):
            worker.write('model.json', serial_model())
            seen.append(execute(worker))
        cases = (({}, ['read', 'write'], 'Listen to the bound serial device first, or supply a reset grant'),
                 ({'acquire': listen}, ['read', 'write'], 'resets when opened; this connection requires a reset grant'),
                 ({'acquire': listen}, ['read', 'write', 'reset'], None))
        for script, grants, refusal in cases:
            with self.subTest(grants=grants, listened=bool(script)), patch.dict(sys.modules, {'serial': fake_serial(
                    boot=b'DEMO-42 booting\r\n', replies={b'ID?': b'DEMO-42\n'})}):
                self.run_with({**script, 'interpret': interpret}, binding={'port': '/dev/stand-in-serial'},
                              effects=grants)
                if refusal:
                    self.assertIsInstance(seen[-1], ValueError, seen[-1])
                    self.assertIn(refusal, str(seen[-1]))
                else:
                    self.assertIs(seen[-1].get('ok'), True, seen[-1])
                    self.assertEqual(self.assigned('interpret')[0]['effects'], ['read', 'reset'])

    def test_07_B7_its_calls_are_recorded_as_a_probe_stages_and_the_notes_list_dispatch_ids_and_record_hashes(self):
        import hashlib
        calls = [lambda worker: worker.tool('interface_execute', model_dir=str(worker.workspace), operation='identify'),
                 lambda worker: worker.tool('probe_run', model_dir=str(worker.workspace), operation='measure', n=2),
                 lambda worker: worker.tool('interface_describe', model_dir=str(worker.workspace))]
        results, _, result = self.calls_on(model(), calls)
        self.assertTrue(all(isinstance(r, dict) and r.get('ok') for r in results), results)
        started = {str(e['id']): e['data'] for e in self.events('tool.started') if e['data']['stage'] == 'interpret'}
        finished = [e['data'] for e in self.events('tool.finished') if e['data']['stage'] == 'interpret']
        self.assertEqual([started[f['dispatch_id']]['connected'] for f in finished], [True, True, False])
        # The probe record is hashed into the call's record, as a probe stage's is.
        probe = finished[1]
        self.assertIn(str(Path(probe['result']['probe']).resolve()), probe['artifacts'])
        record = lambda payload: hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                           allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(interpretation(result).get('device_calls'), [
            {'dispatch_id': f['dispatch_id'], 'name': f['name'], 'operation': operation, 'ok': True,
             'record_sha256': record(f)} for f, operation in zip(finished[:2], ('identify', 'measure'))])

    def test_07_B7_its_records_stay_out_of_the_handoff_so_probe_and_ground_receive_only_the_model(self):
        calls = [lambda worker: worker.tool('interface_execute', model_dir=str(worker.workspace), operation='identify'),
                 lambda worker: worker.tool('probe_run', model_dir=str(worker.workspace), operation='measure', n=2)]
        results, _, result = self.calls_on(model(), calls)
        self.assertTrue(all(isinstance(r, dict) and r.get('ok') for r in results), results)
        [handoff] = self.handoffs(result, 'interpret')
        [artifact] = handoff['artifacts']
        frozen = sorted(p.relative_to(artifact['path']).as_posix() for p in Path(artifact['path']).rglob('*')
                        if p.is_file())
        self.assertEqual(frozen, ['model.json'])
        recorded = {sha for e in self.events('tool.finished') if e['data']['stage'] == 'interpret'
                    for sha in e['data']['artifacts'].values()}
        self.assertTrue(recorded)
        for stage in ('probe', 'ground'):
            self.assertEqual(list(self.assigned(stage)[0]['inputs'].values()), [artifact['sha256']])


def given_model(worker):
    """The model folder among a stage worker's inputs, or None."""
    return next((Path(path) for path in worker.task['inputs'] if (Path(path) / 'model.json').is_file()), None)


def probe_each(worker):
    """A probe or ground worker that runs each operation of its assigned model once on the device; with no model it
    does nothing."""
    model_dir = given_model(worker)
    operations = json.loads((model_dir / 'model.json').read_text(encoding='utf-8'))['operations'] if model_dir else ()
    for operation in operations:
        worker.tool('probe_run', model_dir=str(model_dir), operation=operation, n=1)


def emit(worker):
    """An emit worker that packages its model with a probe record and reports the package; with neither it does
    nothing."""
    from generative_driver.benchmark_support.integrity import digest as tree_digest
    record = next((Path(path) for path in worker.task['inputs'] if Path(path).suffix == '.json'), None)
    if given_model(worker) is None or record is None:
        return None
    built = worker.tool('emit_package', model_dir=str(given_model(worker)), probe_json=str(record))
    if not isinstance(built, dict) or not built.get('ok'):
        raise AssertionError('emit_package failed: ' + str(built)[:400])
    return {'status': 'completed', 'summary': 'scripted emit', 'checks': [], 'unresolved': [],
            'artifacts': [{'path': built['package_dir'], 'sha256': tree_digest(built['package_dir']),
                           'kind': 'package'}]}


def reuse(worker):
    """A fresh reuse worker that calls the generated tool of each operation but identity."""
    tools = worker.controller.call('mcp_tools', worker.identity)['tools']
    for tool in tools:
        if not tool['name'].endswith('identify'):
            worker.controller.call('mcp_tool', {**worker.identity, 'name': tool['name'], 'arguments': {},
                                                'request_id': tool['name']})


class DeviceFirstRunTests(RunCase):
    """07-B8: after device-first interpretation the run goes on as today, to an emitted package and a verdict."""

    def test_07_B8_a_device_difference_sends_the_run_back_to_a_device_first_repair_with_the_previous_model(self):
        from line_device import line_device
        first, seen = model(expect='DEMO-41\n'), []

        def interpret(worker):
            files = {p.relative_to(worker.workspace).as_posix() for p in worker.workspace.rglob('*') if p.is_file()}
            read = lambda name: json.loads((worker.workspace / name).read_text(encoding='utf-8')) \
                if name in files else None
            seen.append({'tools': sorted(worker.request.allowed_tools), 'files': files,
                         'previous': read('previous-model/model.json'), 'context': read('REPAIR_CONTEXT.json'),
                         'defects': read('defects.json')})
            worker.write('model.json', first if len(seen) == 1 else model())
        with line_device(DEVICE) as served:
            result = self.run_with({'interpret': interpret, 'probe': probe_each, 'ground': probe_each, 'emit': emit,
                                    'reuse': reuse}, binding=served, max_revisions=2)
        self.assertEqual([h['revision'] for h in self.handoffs(result, 'interpret')], [0, 1], result['reason'])
        self.assertEqual([interpretation(result, revision)['mode'] for revision in (0, 1)], ['device', 'device'])
        # The repair is the device-first worker again, with the model the device contradicted and the differences.
        self.assertEqual(len(seen), 2)
        repair = seen[1]
        self.assertEqual(repair['tools'], sorted(DEVICE_FIRST_TOOLS))
        self.assertIn('DEVICE_FIRST.md', repair['files'])
        self.assertEqual(repair['previous'], first)
        self.assertEqual((repair['context'] or {}).get('previous_candidate'), 'previous-model/')
        self.assertEqual((repair['context'] or {}).get('feedback'), 'defects.json')
        self.assertTrue(repair['defects'], repair['files'])
        self.assertIn('identify', json.dumps(repair['defects']))
        self.assertIsNone(seen[0]['previous'])
        # Probe tested the repaired model and the run went on.
        probes = self.assigned('probe')
        self.assertEqual(len(probes), 2)
        self.assertEqual(list(probes[1]['inputs'].values()),
                         [self.handoffs(result, 'interpret')[1]['artifacts'][0]['sha256']])

    def test_07_B8_a_run_with_a_binding_and_no_firmware_ends_with_an_emitted_package_and_a_self_evidence_verdict(self):
        from line_device import line_device

        def interpret(worker):
            worker.write('model.json', model())
            worker.write('contract.json', contract_for(model()))
        with line_device(DEVICE) as served:
            result = self.run_with({'interpret': interpret, 'probe': probe_each, 'ground': probe_each, 'emit': emit,
                                    'reuse': reuse}, binding=served)
        equivalence = result['equivalence']
        self.assertEqual((result['status'], result['reason']), ('completed', 'Final verdict: equivalent'), equivalence)
        self.assertEqual((equivalence['equivalent'], equivalence['basis']), (True, 'self-evidence'))
        [handoff] = self.handoffs(result, 'interpret')
        # Emit took the model from the interpret handoff, and fresh reuse ran the package it froze.
        [emitted] = self.assigned('emit')
        [given] = [Path(path) for path in emitted['inputs'] if (Path(path) / 'model.json').is_file()]
        self.assertEqual((given / 'model.json').read_bytes(),
                         (Path(handoff['artifacts'][0]['path']) / 'model.json').read_bytes())
        [package] = [a for h in self.handoffs(result, 'emit') for a in h['artifacts']]
        self.assertEqual(json.loads((Path(package['path']) / 'driver' / 'model.json').read_text(encoding='utf-8')),
                         model())
        [reused] = self.assigned('reuse')
        self.assertEqual(list(reused['inputs'].values()), [package['sha256']])


# The sha256 of every tool description but acquire_document's, as the base commit of Feature 07 listed them.
BASE_TOOLS_SHA256 = '16b20a898c1024e0c77601ab9c14eba3dd99f707eb7d111d0cce8531be02d7d6'
SENSOR_BINDING = {'url': 'ftdi://scripted/1'}


class BenchmarkUnchangedTests(RunCase):
    """07-B9: benchmark runs acquire, prepare, run and check interpretation exactly as before Feature 07."""

    def sensor_run(self, acquire):
        """The interpret worker's launch and workspace files (None when none launched) and the run's result, for a
        physical benchmark run whose acquire worker does `acquire(worker)`: it names a binding and acquires no image."""
        seen = {}

        def interpret(worker):
            seen.update(tools=list(worker.request.allowed_tools), files=sorted(
                p.relative_to(worker.workspace).as_posix() for p in worker.workspace.rglob('*') if p.is_file()))
        result = self.run_with({'acquire': acquire, 'interpret': interpret}, case='bme280', binding=SENSOR_BINDING,
                               effects=['read', 'write'])
        return seen or None, result

    @unittest.skipUnless(have_pypdf(), 'pypdf is not installed')
    def test_07_B9_a_benchmark_run_gets_no_new_acquire_tool_and_its_interpreter_no_documents(self):
        from input_path_fixtures import network
        acquired = {}

        def acquire(worker):
            acquired.update(tools=worker.tools(), document=worker.tool('acquire_document', name='datasheet.pdf'),
                            datasheet=worker.tool('acquire_datasheet', url=DATASHEET_URL,
                                                  expected_sha256=sha256(pdf_bytes(DATASHEET_PAGES))))
        # The stand-in datasheet is the one the benchmark's registry pins, for this run.
        with network({DATASHEET_URL: pdf_bytes(DATASHEET_PAGES)}), \
                patch('generative_driver.benchmark_support.physical._datasheet_sha256',
                      lambda: sha256(pdf_bytes(DATASHEET_PAGES))):
            seen, result = self.sensor_run(acquire)
        self.assertEqual(acquired['tools'], ['acquire_datasheet'])
        self.assertIsInstance(acquired['document'], ValueError)
        self.assertIn('not assigned', str(acquired['document']))
        self.assertIs(acquired['datasheet'].get('available'), True, acquired['datasheet'])
        # The benchmark's own datasheet interpreter, without documents or device-first instructions.
        self.assertIsNotNone(seen, result['reason'])
        self.assertEqual(seen['tools'], [])
        for name in ('DOCUMENTS.json', 'DEVICE_FIRST.md', 'TASKS.json'):
            self.assertNotIn(name, seen['files'])
        self.assertFalse([name for name in seen['files'] if name.startswith('documents/')])
        [assignment] = self.assigned('interpret')
        self.assertEqual(assignment['effects'], ['read', 'write'])
        self.assertNotIn('device_first', assignment)
        self.assertIsNone(interpretation(result))

    def test_07_B9_a_benchmark_run_that_names_a_binding_and_acquires_no_image_never_interprets_device_first(self):
        seen, result = self.sensor_run(lambda worker: None)
        self.assertIsNone(seen)
        [skipped] = [e['data'] for e in self.events('stage.skipped') if e['data']['stage'] == 'interpret']
        self.assertIn('no acquired official datasheet', json.dumps(skipped))

    def test_07_B9_an_emulated_benchmark_acquire_worker_gets_its_one_tool_as_before(self):
        acquired = {}

        def acquire(worker):
            acquired['tools'] = worker.tools()
        self.run_with({'acquire': acquire}, case='tq9')
        self.assertEqual(acquired['tools'], ['acquire_firmware_artifact'])

    def test_07_B9_every_existing_tool_description_is_unchanged(self):
        import hashlib
        from generative_driver.toolkit import list_tools
        existing = [tool for tool in list_tools() if tool['name'] != 'acquire_document']
        canonical = json.dumps(existing, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        self.assertEqual(hashlib.sha256(canonical.encode('utf-8')).hexdigest(), BASE_TOOLS_SHA256)


if __name__ == '__main__':
    unittest.main()

"""The kit analysis: the firmware analysis the configurator makes once per run before interpretation, its fallback, its
index and what the single interpret worker gets of it (docs/features/kit-fixes/06-interpret-tasks.md, 06-B1 to 06-B4).

A stand-in analyzer, canned images and scripted workers in temporary homes; no Ghidra, Java, model or device."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interpret_task_fixtures import (FUNCTIONS, SINGLE_MODEL, RunCase, cortex_m_image, elf_image, events, export_files,
                                     gone, interpret_notes, launches, many_functions, option, scripted_worker, seen,
                                     set_mode, stopped, until, unmapped_image)


class KitAnalysisCase(RunCase):
    """A run of the kit analysis, in a temporary home."""


class MadeTests(KitAnalysisCase):
    def test_06_B1_makes_the_analysis_of_a_raw_cortex_m_image_before_the_first_interpret_worker(self):
        controller, run, ghidra, worker = self.start(cortex_m_image())
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        order = [event['kind'] for event in events(controller, run, 'kit_analysis.started', 'kit_analysis.finished',
                                                     'stage.assigned')
                 if event['kind'] != 'stage.assigned' or event['data']['stage'] == 'interpret']
        self.assertEqual(order, ['kit_analysis.started', 'kit_analysis.finished', 'stage.assigned'])
        # The existing helper ran the stand-in once, with the loading the map check decided.
        launch, = launches(ghidra)
        argv = launch['argv']
        self.assertEqual((option(argv, '-processor'), option(argv, '-loader'), option(argv, '-loader-baseAddr'),
                          option(argv, '-preScript'), option(argv, '-postScript')),
                         ('ARM:LE:32:Cortex', 'BinaryLoader', '08000000', 'SeedCortexM.java', 'ExportDecomp.java'))
        # The helper's working folders are a fresh folder under the system temporary folder.
        self.assertTrue(Path(argv[0]).is_relative_to(Path(tempfile.gettempdir()).resolve()), argv[0])
        finished, = self.finished(controller, run)
        self.assertTrue(finished['made'], finished)
        # The export, the helper's log and the decision are kept in the run's folder, outside every workspace.
        folder = self.run_dir(controller, run) / finished['folder']
        decision = json.loads((folder / 'decision.json').read_text(encoding='utf-8'))
        self.assertEqual({key: decision[key] for key in ('processor', 'loader', 'load_address')},
                         {'processor': 'ARM:LE:32:Cortex', 'loader': 'BinaryLoader', 'load_address': '0x08000000'})
        self.assertIn('vector table', decision['why'])
        self.assertTrue((folder / 'export' / 'functions.tsv').is_file())
        self.assertIn('Import succeeded',
                      (folder / 'helper-log' / 'analyzeHeadless.stdout').read_text(encoding='utf-8'))
        workspaces = [Path(event['data']['workspace']) for event in events(controller, run, 'stage.assigned')]
        self.assertFalse(any(folder.is_relative_to(w) or w.is_relative_to(folder) for w in workspaces))

    def test_06_B1_loads_an_elf_whose_machine_type_the_kit_maps_with_ghidras_elf_loader(self):
        for index, (image, processor) in enumerate([(elf_image(40), 'ARM:LE:32:v8'),
                                                    (elf_image(183, bits=64, vaddr=0x40000000), 'AARCH64:LE:64:v8A'),
                                                    (elf_image(94, vaddr=0x40080000), 'Xtensa:LE:32:default')]):
            with self.subTest(processor=processor):
                root = self.root / str(index)
                controller, run, ghidra, _ = self.start(image, name='firmware.elf', root=root, home=root / 'home')
                until(lambda: self.finished(controller, run))
                controller.call('cancel', run)
                finished, = self.finished(controller, run)
                self.assertTrue(finished['made'], finished)
                launch, = launches(ghidra)
                self.assertEqual((option(launch['argv'], '-processor'), option(launch['argv'], '-loader')),
                                 (processor, 'ElfLoader'))
                decision = json.loads((self.run_dir(controller, run) / finished['folder'] / 'decision.json')
                                      .read_text(encoding='utf-8'))
                self.assertEqual((decision['processor'], decision['loader']), (processor, 'ElfLoader'))
                self.assertIn('ELF', decision['why'])

    def test_06_B1_a_home_whose_path_has_a_space_still_gets_the_analysis(self):
        controller, run, ghidra, _ = self.start(cortex_m_image(), home='Application Support/home')
        self.assertIn(' ', str(controller.home))
        until(lambda: self.finished(controller, run))
        finished, = self.finished(controller, run)
        self.assertTrue(finished['made'], finished)
        launch, = launches(ghidra)
        self.assertNotRegex(launch['argv'][0], r'\s')

    def test_06_B1_every_later_interpret_worker_reuses_the_one_analysis_on_resume_and_repair(self):
        image = cortex_m_image()
        folder = self.root / 'worker'
        # The first interpret worker exits with an error, which stops the run after the analysis was made.
        command = scripted_worker(folder, single=SINGLE_MODEL, exit_error='single')
        diff = [{'source': 'probe_diff', 'dispatch_id': '1', 'defects': [{'where': 'operations.identify.steps.0',
                                                                          'what': 'the device answered DEMO-43'}]}]
        controller, run, ghidra, _ = self.start(image, worker=command,
                                                diffs=lambda revision: diff if revision == 0 else None)
        self.assertEqual(stopped(controller, run)['status'], 'failed')
        scripted_worker(folder, single=SINGLE_MODEL)
        controller.call('resume', run)
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        # A failed attempt, its resume and the repair after probe: three interpret workers, one analysis.
        self.assertEqual(len(seen(folder, 'single')), 3)
        self.assertEqual(len(launches(ghidra)), 1)
        self.assertEqual(len(events(controller, run, 'kit_analysis.started')), 1)
        self.assertEqual([h['revision'] for h in result['accepted_handoffs'] if h['stage'] == 'interpret'], [0, 1])


class StopTests(KitAnalysisCase):
    def hanging(self, controller, run, ghidra):
        """The stand-in analyzer's process id once it hangs."""
        pid = until(lambda: (Path(ghidra) / 'hanging.pid').is_file() and (Path(ghidra) / 'hanging.pid').read_text())
        return int(pid)

    def test_06_B1_a_cancel_stops_the_analysis_and_every_process_it_started_and_a_resume_makes_it_again(self):
        controller, run, ghidra, worker = self.start(cortex_m_image(), mode='hang')
        pid = self.hanging(controller, run, ghidra)
        controller.call('cancel', run)
        self.assertTrue(gone(pid), 'the stand-in analyzer outlived the cancel')
        status = stopped(controller, run)
        self.assertEqual(status['status'], 'cancelled')
        # No fallback: no interpret worker started.
        self.assertFalse([e for e in events(controller, run, 'stage.assigned') if e['data']['stage'] == 'interpret'])
        self.assertEqual([f.get('stopped') for f in self.finished(controller, run)], ['cancelled'])
        (Path(ghidra) / 'hanging.pid').unlink()
        set_mode(ghidra, 'ok')
        controller.call('resume', run)
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(launches(ghidra)), 2)
        self.assertTrue(self.finished(controller, run)[-1]['made'])

    def test_06_B1_a_shutdown_stops_the_analysis_and_every_process_it_started_without_falling_back(self):
        image = cortex_m_image()
        controller, run, ghidra, worker = self.start(image, mode='hang')
        pid = self.hanging(controller, run, ghidra)
        self.assertTrue(controller.close())
        self.assertTrue(gone(pid), 'the stand-in analyzer outlived the shutdown')
        self.assertFalse([e for e in events(controller, run, 'stage.assigned') if e['data']['stage'] == 'interpret'])
        self.assertEqual([f.get('stopped') for f in self.finished(controller, run)], ['shutdown'])
        # The next service finds the run stopped by the shutdown; a resume makes the analysis again.
        set_mode(ghidra, 'ok')
        restarted = self.controller(image=image)
        self.assertEqual(restarted.call('status', run)['status'], 'blocked')
        restarted.call('resume', run)
        result = stopped(restarted, run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(launches(ghidra)), 2)

    def test_06_B1_the_analysis_stops_when_the_runs_budget_runs_out(self):
        controller, run, ghidra, worker = self.start(cortex_m_image(), mode='hang', budget_seconds=6)
        pid = self.hanging(controller, run, ghidra)
        status = stopped(controller, run, seconds=30)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'operator'), status)
        self.assertIn('budget', status['reason'])
        self.assertTrue(gone(pid), 'the stand-in analyzer outlived the budget')
        self.assertFalse([e for e in events(controller, run, 'stage.assigned') if e['data']['stage'] == 'interpret'])


class FallbackTests(KitAnalysisCase):
    def fell_back(self, result, folder, cause, revision=None):
        """The interpret notes' account of the analysis, after checking that it was not made for `cause` and that the
        single worker got the analysis kit and no index."""
        self.assertEqual(result['status'], 'completed', result)
        kit = interpret_notes(result, revision)['configurator']['interpretation']['kit_analysis']
        self.assertEqual((kit['made'], kit['cause']), (False, cause), kit)
        single = seen(folder, 'single')[-1]
        self.assertTrue({'ghidra_run.py', 'ExportDecomp.java', 'SeedCortexM.java', 'ANALYSIS_TOOLS.json'}
                        <= set(single['files']), single['files'])
        self.assertNotIn('ANALYSIS_INDEX.json', single['files'])
        self.assertNotIn('kit_analysis', single['TASKS.json'])
        return kit

    def test_06_B2_without_ghidra_or_java_named_interpretation_goes_on_with_the_analysis_kit(self):
        controller, run, _, folder = self.start(cortex_m_image(), named=False)
        kit = self.fell_back(stopped(controller, run), folder, 'not_named')
        self.assertIn('not named', kit['reason'])
        self.assertFalse(events(controller, run, 'kit_analysis.started'))

    def test_06_B2_an_image_whose_loading_cannot_be_decided_is_interpreted_without_the_analysis(self):
        cases = [('firmware.bin', unmapped_image(), 'no vector table'),
                 ('firmware.bin', cortex_m_image(header=0x200), 'not at the first byte'),
                 ('firmware.elf', elf_image(62, bits=64), 'machine type 62')]
        for index, (name, image, why) in enumerate(cases):
            with self.subTest(why=why):
                root = self.root / str(index)
                controller, run, ghidra, folder = self.start(image, name=name, root=root, home=root / 'home')
                kit = self.fell_back(stopped(controller, run), folder, 'undecided')
                self.assertIn(why, kit['reason'])
                self.assertEqual(launches(ghidra), [])

    def test_06_B2_a_helper_failure_is_recorded_once_and_every_later_attempt_falls_back_with_its_cause(self):
        diff = [{'source': 'probe_diff', 'dispatch_id': '1', 'defects': [{'where': 'operations.identify.steps.0',
                                                                          'what': 'the device answered DEMO-43'}]}]
        controller, run, ghidra, folder = self.start(cortex_m_image(), mode='fail',
                                                     diffs=lambda revision: diff if revision == 0 else None)
        result = stopped(controller, run)
        for revision in (0, 1):
            kit = self.fell_back(result, folder, 'helper_failed', revision)
            self.assertTrue(any('Import failed' in line for line in kit['reported']), kit)
        self.assertEqual(len(seen(folder, 'single')), 2)
        self.assertEqual(len(launches(ghidra)), 1)

    def test_06_B2_a_helper_that_runs_out_of_time_is_stopped_and_interpretation_goes_on(self):
        with patch('generative_driver.kit_analysis.HELPER_TIMEOUT_SECONDS', 2):
            controller, run, ghidra, folder = self.start(cortex_m_image(), mode='hang')
            pid = int(until(lambda: (Path(ghidra) / 'hanging.pid').is_file()
                            and (Path(ghidra) / 'hanging.pid').read_text()))
            result = stopped(controller, run)
        self.assertTrue(gone(pid), 'the stand-in analyzer outlived its limit')
        kit = self.fell_back(result, folder, 'timeout')
        self.assertTrue(any('timed out' in line for line in kit['reported']), kit)


class IndexTests(KitAnalysisCase):
    def index_of(self, controller, run):
        """The bytes of the run's ANALYSIS_INDEX.json, kept beside its export."""
        finished = until(lambda: self.finished(controller, run))[0]
        self.assertTrue(finished['made'], finished)
        return (self.run_dir(controller, run) / finished['folder'] / 'ANALYSIS_INDEX.json').read_bytes()

    def test_06_B3_the_index_lists_each_function_and_ranks_dispatcher_candidates_with_their_evidence(self):
        controller, run, _, _ = self.start(cortex_m_image())
        raw = self.index_of(controller, run)
        index = json.loads(raw)
        functions = {entry['address']: entry for entry in index['functions']}
        self.assertEqual(sorted(functions), sorted(address for address, _, _ in FUNCTIONS))
        dispatch = functions['08000200']
        self.assertEqual((dispatch['name'], dispatch['callers'], dispatch['callees']),
                         ('dispatch', ['08000140'], ['08000300', '08000340']))
        self.assertEqual(dispatch['decompiled_bytes'], len(export_files()['function_08000200.c'].encode('utf-8')))
        self.assertEqual([string['value'] for string in dispatch['strings']], ['ID?', 'T'])
        self.assertEqual(dispatch['switch_tables'], [{'switch_address': '08000210', 'cases': [
            {'value': '0x49', 'target': '08000220'}, {'value': '0x54', 'target': '08000240'}]}])
        self.assertEqual(functions['08000400']['callers'], ['08000300', '08000340'])
        # Ranked, each with the evidence that ranked it: the dispatcher's two cases and two commands before the menu's
        # two cases.
        ranked = index['dispatcher_candidates']
        self.assertEqual([candidate['function'] for candidate in ranked], ['08000200', '08000500'])
        evidence = ranked[0]['evidence']
        self.assertEqual([(s['value'], s['functions']) for s in evidence['command_strings']],
                         [('ID?', ['08000200']), ('T', ['08000200'])])
        self.assertEqual(evidence['switch_tables'], dispatch['switch_tables'])
        self.assertEqual(ranked[1]['evidence']['command_strings'], [])
        self.assertIsNone(index['omitted'])
        # The same export always gives the same bytes.
        again = self.root / 'again'
        controller, run, _, _ = self.start(cortex_m_image(), root=again, home=again / 'home')
        self.assertEqual(self.index_of(controller, run), raw)

    def test_06_B3_the_index_stays_within_its_bound_and_says_what_it_left_out(self):
        with patch('generative_driver.analysis_index.INDEX_MAX_BYTES', 20000):
            controller, run, _, _ = self.start(cortex_m_image(), export=many_functions(400))
            raw = self.index_of(controller, run)
        self.assertLessEqual(len(raw), 20000)
        index = json.loads(raw)
        self.assertTrue(0 < len(index['functions']) < 400, len(index['functions']))
        omitted = index['omitted']
        self.assertEqual(omitted['functions'], 400 - len(index['functions']), omitted)
        self.assertIn('20000 bytes', omitted['rule'])


class SingleWorkerTests(KitAnalysisCase):
    def test_06_B4_the_single_worker_gets_the_analysis_as_sealed_inputs_and_how_the_image_was_loaded(self):
        controller, run, ghidra, folder = self.start(cortex_m_image())
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        single, = seen(folder, 'single')
        assigned, = [e['data'] for e in events(controller, run, 'stage.assigned') if e['data']['stage'] == 'interpret']
        sealed = {Path(path).relative_to(assigned['workspace']).as_posix() for path in assigned['inputs']}
        # The index and the export, sealed, and the analysis kit still supplied.
        self.assertTrue({'ANALYSIS_INDEX.json', 'analysis/functions.tsv', 'analysis/function_08000200.c',
                         'analysis/strings.tsv', 'ghidra_run.py', 'ANALYSIS_TOOLS.json'} <= sealed, sealed)
        self.assertTrue({'ANALYSIS_INDEX.json', 'analysis/functions.tsv'} <= set(single['files']))
        finished, = self.finished(controller, run)
        kept = self.run_dir(controller, run) / finished['folder'] / 'ANALYSIS_INDEX.json'
        self.assertEqual(single['index_sha256'], hashlib.sha256(kept.read_bytes()).hexdigest())
        # TASKS.json says that the analysis is made, how the image was loaded and when to rerun the kit.
        entry = single['TASKS.json']['kit_analysis']
        self.assertEqual({key: entry['loading'][key] for key in ('processor', 'loader', 'load_address')},
                         {'processor': 'ARM:LE:32:Cortex', 'loader': 'BinaryLoader', 'load_address': '0x08000000'})
        for words in ('ANALYSIS_INDEX.json', 'analysis/', 'only if', 'contradicts'):
            self.assertIn(words, entry['instructions'])
        # The export is not part of the worker's frozen output.
        handoff, = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret']
        frozen = Path(handoff['artifacts'][0]['path'])
        self.assertTrue((frozen / 'model.json').is_file())
        self.assertEqual([p.name for p in frozen.iterdir() if p.name in ('analysis', 'ANALYSIS_INDEX.json')], [])


if __name__ == '__main__':
    unittest.main()

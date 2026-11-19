"""The benchmark's side of version 4 of the client interface: the run report is the benchmark's, and the benchmark
takes from the tool only what the interface lists.

Feature 08 of the untangle Run (docs/features/untangle/08-reports.md): 08-B1 to 08-B3, 08-B6 and 08-B12 to 08-B15.
This file is a benchmark item: it imports the benchmark and runs a registered case. The tool's side of the Feature
is in `tests/test_client_surface.py`, which stays with the tool.

Reports are exported from canned answers of a service, with stand-ins for the machine and for the hashes of the
installed trees. The scripted trials and the scripted suite are those of `test_benchmark_runs.py` and
`test_benchmark_suite_book.py`, on the installed `tq9-v2` against an in-process configurator that records the
methods it receives. The benchmark's source is read as a syntax tree. No model, no emulator and no real password.
"""
import ast
import contextlib
import copy
import hashlib
import importlib
import inspect
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import firmware_bench
import generative_driver
import interface_runtime
from firmware_bench import benchmark
from generative_driver import client, client_interface, integrity, replay
from firmware_bench.benchmark_support import grader_identity, harness, trial_store
from test_benchmark_runs import CASE, SnapshotCase, TrialCase
from test_benchmark_suite_book import ScriptedSuiteCase

# The benchmark's package and the tool's, each found through its import, so the tests also run on installed
# packages (Feature 12 of this Run, docs/features/untangle/12-firmware-bench.md).
PACKAGE = Path(firmware_bench.__file__).resolve().parent
TOOL = Path(generative_driver.__file__).resolve().parent
RUNTIME = Path(interface_runtime.__file__).resolve().parent
SUPPORT = PACKAGE / 'benchmark_support'
REPORT_NAMES = ('summarize', 'markdown', 'report_run', '_usage', '_interventions', '_public', '_awaiting', '_why',
                '_tree_hash')
STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
# A case pin and the provenance of an execution as a snapshot holds them, with fixed values in place of hashes.
PIN = {'execution': 'actual-agent-emulation', 'case_id': 'surface-case', 'case_version': '5',
       'scenario_id': 'original', 'case_seed': 7, 'manifest_sha256': 'c' * 64, 'truth_sha256': 'd' * 64,
       'image_hashes': {'firmware.bin': 'e' * 64}, 'time_policy': {'kind': 'emulated'}, 'evaluator_version': '5',
       'manifest': {'schema': 'benchmark-case/2', 'id': 'surface-case', 'version': '5', 'evaluator_version': '5',
                    'execution': 'actual-agent-emulation', 'scenarios': ['original'],
                    'images': {'firmware.bin': 'e' * 64}, 'required_stages': list(STAGES),
                    'limitations': ['A canned run, never a model run'],
                    'execution_setup': {'method': 'emulation', 'artifact': 'firmware.bin',
                                        'substitutions': ['a stand-in clock'], 'validated_behavior': ['the level'],
                                        'hardware_gaps': ['no sensor']}}}
EXECUTED = {'runtime_configuration': {'runtime': 'codex', 'model': 'canned-model', 'reasoning_effort': 'high'},
            'evaluator_revision': 'a' * 64, 'toolchain_revision': 'b' * 64, 'skills_revision': 'f' * 64,
            'environment': {'system': 'CannedOS', 'release': '1', 'architecture': 'canned', 'python': '3.11.0'}}
GRADED = {'schema': 'equivalence/2', 'status': 'graded', 'equivalent': True, 'basis': 'evaluator',
          'checks': [{'id': 'reads the level', 'passed': True}], 'diff': [], 'passed': 1, 'total': 1,
          'mission': {'name': 'fresh reuse', 'passed': True}, 'frozen_artifact_sha256': '1' * 64,
          'evidence_sha256': '2' * 64, 'protocol': 'assistive/1'}
# The run's formats: a run with no snapshot, a run of the earlier gated protocol, and three assistive benchmark runs
# (graded, awaiting its grade after an unscored one, and stopped).
FORMS = {'1': 'benchmark-report/1', '2': 'benchmark-report/2', '3': 'benchmark-report/3',
         '3-unscored': 'benchmark-report/3', '3-stopped': 'benchmark-report/3'}
# The SHA-256 of the `report.json` and of the `report.md` the tool's report module wrote for each canned run before
# it moved (commit 023324f, `src/generative_driver/reporting.py`), over their text as UTF-8. The digest of the
# `report.json` of form `1` was computed anew for Feature 12 of this Run (12-B2): its `toolchain_revision` joins three
# tree hashes, of the tool, of its runtime and of the benchmark, where it joined two.
BEFORE = {
    '1': ['1be3c71d7baae7ecbb073302b30580cc0c4a9e0bda12da2bdcd40f31e90f8627',
          'f7af4591de008e30bd4f33a419cd22cdf64ee04226af829646d880e2972dcf82'],
    '2': ['a0898f6b6693fbde1b176cd63b25598a85956e8c4cb86d7e1d60d68f0c824299',
          '3701903e5815ab64a5baef4c5ddef273058c2d7e32a7caf71c869e2f9d155f1f'],
    '3': ['f490b8236c0e290321c986a94ae33dae94cd98487b72e0488d5e250b02bacdd1',
          'a3765d7d355ba0aed37b67ec04a5ef7146e3c5adf15c52404f5a04d1a62f9c04'],
    '3-unscored': ['28aa7122966435531818d4f7c9a998786625f624c487bf27e9800842bb9f8d3e',
                   'fd8b349ff6de12535a9ca99fb25772232928c6bc80ea49385d7ad9d928f96d4a'],
    '3-stopped': ['1acf20e07963dea631dcdd53b6f0b13c4c6d28f56e099ec18e46b443f96139a6',
                  '07c65f3028ffe1ce74de33b6ff133cd5db5eb328622718a00ceafeb2ce965275']}


def report_module():
    """The benchmark's module of the run report."""
    return importlib.import_module('firmware_bench.benchmark_support.run_reporting')


def snapshot():
    """An execution snapshot of the canned case, with its hash."""
    from firmware_bench.benchmark_support.snapshots import canonical_digest
    body = {'schema': 'benchmark-execution-snapshot/1', 'case_pin': PIN, 'public_files': {}, 'executed': EXECUTED}
    return {**body, 'snapshot_sha256': canonical_digest(body)}


def canned(home, run_id, form):
    """What a service answers for the finished run `run_id` of `form`, as its `result` and its `events`, after
    writing the files such a run has in its folder under `home`."""
    folder = home / 'runs' / run_id
    (folder / 'supplied').mkdir(parents=True)
    usage = {'input_tokens': 100, 'cached_input_tokens': 10, 'output_tokens': 20, 'reasoning_output_tokens': 5}
    workers = [{'stage': stage, 'assignment_id': stage + '-0', 'status': 'completed', 'elapsed_seconds': 2.5,
                'usage': usage if stage != 'ground' else None, 'runtime': 'codex', 'provider': 'openai',
                'model': 'canned-model', 'version': '1.0', 'settings': {'reasoning_effort': 'high'},
                'report': {'status': 'completed', 'summary': 'a worker claim that no report holds'}}
               for stage in STAGES]
    handoffs = [{'stage': stage, 'assignment_id': stage + '-0', 'revision': 0,
                 'artifacts': [{'path': str(folder / 'accepted' / stage / 'artifact.json'), 'sha256': '1' * 64,
                                'kind': 'evidence'}]} for stage in STAGES]
    events = [
        {'kind': 'run.started', 'time': 100.0, 'data': {'budget_seconds': 600}},
        {'kind': 'tool.started', 'time': 110.0, 'data': {'stage': 'probe', 'name': 'probe_run'}},
        {'kind': 'tool.finished', 'time': 113.5, 'data': {'stage': 'probe', 'name': 'probe_run'}},
        {'kind': 'tool.started', 'time': 120.0, 'data': {'stage': 'reuse', 'name': 'package_execute',
                                                          'actor': 'evaluator'}},
        {'kind': 'tool.finished', 'time': 121.0, 'data': {'stage': 'reuse', 'name': 'package_execute',
                                                           'actor': 'evaluator'}},
        {'kind': 'operator.response', 'time': 130.0, 'data': {'effect_resolution': 'confirmed',
                                                               'observation': {'said': 'never exported'}}},
        {'kind': 'run.budget_extended', 'time': 140.0,
         'data': {'old_budget_seconds': 600, 'new_budget_seconds': 900, 'extension_seconds': 300,
                  'old_deadline': 700.0, 'new_deadline': 1000.0, 'reason': 'A longer look'}},
        {'kind': 'tool.started', 'time': 150.0, 'data': {'stage': 'emit', 'name': 'emit_package'}}]
    result = {'ok': True, 'run_id': run_id, 'status': 'completed', 'stage': 'reuse', 'created': 100.0,
              'updated': 400.0, 'outcome_category': 'completed', 'reason': 'Final verdict: equivalent',
              'worker_reports': workers, 'accepted_handoffs': handoffs, 'evaluator_verdicts': [],
              'budget_seconds': 900, 'limits': {'max_model_repairs': 2, 'max_maintenance_cycles': 1},
              'agent': {'runtime': 'codex', 'model': 'canned-model', 'provider': 'openai', 'version': None,
                        'settings': {'reasoning_effort': 'high'}},
              'progress': {'protocol': 'assistive/1', 'equivalence': 'graded', 'revision': 0, 'repairs': 0},
              'equivalence': copy.deepcopy(GRADED)}
    state = {'stage_verdicts': {stage: {'status': 'passed', 'checks': [{'name': 'accepted', 'passed': True}]}
                                for stage in STAGES},
             'probe_evaluation': {'observations': {'level': 42}, 'expected': 'never exported'},
             'time_policy': {'kind': 'wall'}}
    if form == '1':
        result.update(reason='Final verdict: not equivalent: The package did not replay; kept in ' + str(home),
                      equivalence={'equivalent': False, 'basis': 'self-evidence', 'checks': [],
                                   'diff': [{'id': 'emitted_package_replay', 'reason': 'The package did not replay'}]})
    else:
        saved = snapshot()
        (folder / 'benchmark').mkdir()
        (folder / 'benchmark' / 'execution.json').write_text(json.dumps(saved), encoding='utf-8')
        result['client_record'] = {'case': 'surface-case', 'case_pin': PIN,
                                   'snapshot_sha256': saved['snapshot_sha256']}
    if form == '2':
        gate = {'stage': 'reuse', 'revision': 1, 'assignment_id': 'reuse-0', 'artifact_sha256': '1' * 64,
                'verdict': 'failed', 'passed': 0, 'total': 1, 'checks': ['never exported']}
        state.update(accepted_gates=[gate], final_evaluation={'verdict': 'failed', 'passed': 0, 'total': 1,
                                                              'evidence_sha256': 'f' * 64},
                     evaluations=[{'phase': 'final', 'revision': 1, 'frozen_artifact_sha256': '1' * 64,
                                   'verdict': 'failed', 'passed': 0, 'total': 1, 'records': ['never exported']}])
        result.update(status='failed', reason='Frozen final evaluation failure', equivalence=None,
                      progress={'revision': 1, 'repairs': 1},
                      evaluator_verdicts=[{'assignment_id': 'probe-0', 'stage': 'probe', 'revision': 0,
                                           'verdict': 'failed', 'passed': 0, 'total': 1,
                                           'records': ['never exported']}])
    if form == '3-unscored':
        result.update(reason='Frozen driver awaits independent evaluation', equivalence=None,
                      progress={**result['progress'], 'equivalence': 'pending'})
        events.append({'kind': 'evaluation.unscored', 'time': 390.0, 'data': {'diff': [
            {'id': 'evaluator', 'reason': 'The evaluator host stopped in ' + str(home)}]}})
    if form == '3-stopped':
        result.update(status='blocked', stage='probe', outcome_category='operator', equivalence=None,
                      reason='DeviceUnavailable: no device answered in ' + str(home),
                      progress={'protocol': 'assistive/1', 'revision': 0, 'repairs': 0})
    (folder / 'supplied' / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    return result, [{'id': number, **event} for number, event in enumerate(events, 1)]


def exported(module, form, home, command=None):
    """The report of the canned run of `form` kept under `home`, as `module.report_run` gives it, or as the
    command line `command(run_id)` prints it: (the report, the text of the `report.json` it wrote, the text of the
    Markdown file beside it, the folders whose trees it hashed). The machine and the trees are stand-ins, so the
    report is the same wherever it is exported."""
    run_id = 'run-form-' + form
    result, events = canned(home, run_id, form)
    hashed = []

    def call(method, params, home=None, autostart=True):
        if method == 'events':
            page = [event for event in events if event['id'] > params['after']]
            return {'ok': True, 'run_id': run_id, 'events': copy.deepcopy(page),
                    'cursor': page[-1]['id'] if page else params['after']}
        return copy.deepcopy(result)

    def tree(folder):
        hashed.append(Path(folder))
        return 'tree of ' + Path(folder).name
    machine = {'system': 'CannedOS', 'release': '2', 'machine': 'canned', 'python_version': '3.12.0'}
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch('generative_driver.client.call', side_effect=call))
        stack.enter_context(patch.object(module, '_tree_hash', tree))
        for name, value in machine.items():
            stack.enter_context(patch('platform.' + name, return_value=value))
        report = command(run_id) if command else module.report_run(run_id, home=home)
    folder = home / 'runs' / run_id
    return (report, (folder / 'report.json').read_text(encoding='utf-8'),
            (folder / 'report.md').read_text(encoding='utf-8'), hashed)


def digests(*texts):
    return [hashlib.sha256(text.encode('utf-8')).hexdigest() for text in texts]


class TemporaryHomeCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve() / 'home'


class RunReportTests(TemporaryHomeCase):
    """The run report's code is the benchmark's (08-B1)."""

    def test_08_B1_the_benchmarks_module_holds_the_nine_names_of_the_run_report(self):
        module = report_module()
        self.assertEqual(Path(module.__file__), SUPPORT / 'run_reporting.py')
        for name in REPORT_NAMES:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(module, name, None)), name)
        from firmware_bench.benchmark_support import integrity
        self.assertIs(module._tree_hash, integrity.tree_hash)

    def test_08_B1_report_run_writes_the_same_report_and_markdown_as_before_in_each_format(self):
        module = report_module()
        self.assertEqual(sorted(BEFORE), sorted(FORMS))
        for form, schema in FORMS.items():
            with self.subTest(form=form):
                report, saved, text, _ = exported(module, form, self.home)
                self.assertEqual(report['schema'], schema)
                self.assertEqual(json.loads(saved), report)
                self.assertEqual(digests(saved, text), BEFORE[form])

    def test_08_B1_a_run_without_a_snapshot_names_the_revisions_of_the_folders_12_B2_gives(self):
        report, _, _, hashed = exported(report_module(), '1', self.home)
        skills = TOOL / 'resources' / 'toolchain' / 'skills'
        # The tool's package, its runtime and the benchmark's package, each where it is installed, then the tool's
        # skills: the files the two revisions covered while the three lay in one source tree.
        self.assertEqual(hashed, [TOOL, RUNTIME, PACKAGE, skills])
        self.assertEqual([folder.name for folder in hashed],
                         ['generative_driver', 'interface_runtime', 'firmware_bench', 'skills'])
        self.assertTrue(all(folder.is_dir() for folder in hashed))
        three = hashlib.sha256(b'tree of generative_drivertree of interface_runtimetree of firmware_bench').hexdigest()
        self.assertEqual((report['toolchain_revision'], report['skills_revision']), (three, 'tree of skills'))
        # A run with a snapshot takes both from the snapshot, and hashes nothing.
        report, _, _, hashed = exported(report_module(), '3', self.home)
        self.assertEqual(hashed, [])
        self.assertEqual((report['toolchain_revision'], report['skills_revision']), ('b' * 64, 'f' * 64))


class ReportCommandTests(TemporaryHomeCase):
    """`firmware-bench report` gives what `generative-driver report` gave (08-B2). The command is the benchmark's
    since Feature 09 of this Run (09-B3 of docs/features/untangle/09-commands.md)."""

    def run_command(self, *arguments):
        """What the benchmark's command line prints for `arguments`, as JSON, once it has ended with status 0. The
        home it sets for the process is put back as it was."""
        printed = io.StringIO()
        with patch.dict(os.environ), contextlib.redirect_stdout(printed):
            status = benchmark.main(['--home', str(self.home), *arguments])
        self.assertEqual(status, 0)
        return json.loads(printed.getvalue())

    def test_08_B2_report_prints_the_report_and_writes_both_files_in_the_runs_folder(self):
        module = report_module()
        report, saved, text, _ = exported(module, '1', self.home, lambda run_id: self.run_command('report', run_id))
        self.assertEqual(report['schema'], 'benchmark-report/1')
        self.assertEqual(json.loads(saved), report)
        self.assertEqual(digests(saved, text), BEFORE['1'])
        self.assertEqual(sorted(path.name for path in (self.home / 'runs' / 'run-form-1').iterdir()),
                         ['report.json', 'report.md', 'supplied'])

    def test_08_B2_with_output_it_writes_the_report_and_its_markdown_sibling_there(self):
        module = report_module()
        output = self.home.parent / 'elsewhere' / 'trial.json'
        printed = []

        def command(run_id):
            printed.append(self.run_command('report', run_id, '--output', str(output)))
            # Nothing is written in the run's folder; the files there are written for the comparison below.
            self.assertEqual(sorted(path.name for path in (self.home / 'runs' / run_id).iterdir()),
                             ['benchmark', 'supplied'])
            return module.report_run(run_id, home=self.home)
        report, saved, text, _ = exported(module, '3', self.home, command)
        self.assertEqual(printed, [report])
        self.assertEqual(sorted(path.name for path in output.parent.iterdir()), ['trial.json', 'trial.md'])
        self.assertEqual((output.read_text(encoding='utf-8'), output.with_suffix('.md').read_text(encoding='utf-8')),
                         (saved, text))
        self.assertEqual(digests(saved, text), BEFORE['3'])


IN_BENCH = 'A replay candidate never lies in the bench home, which holds evaluator records: '
NEEDS = 'The replay executor needs a saved candidate: executor_config {"candidate": <directory>}'


def saved_candidate(directory):
    """A saved replay candidate with no step at `directory`, as text of its path."""
    directory.mkdir(parents=True)
    (directory / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {}}), encoding='utf-8')
    return str(directory)


class ReplayCandidateTests(TemporaryHomeCase):
    """The benchmark itself refuses a replay candidate that lies in the bench home (08-B6)."""

    def setUp(self):
        super().setUp()
        self.root = self.home.parent
        self.bench = self.root / 'home-bench'
        self.bench.mkdir()

    def refused(self, candidate, bench=None):
        with self.assertRaises(ValueError) as refusal:
            trial_store.replay_candidate(candidate, self.bench if bench is None else bench)
        self.assertIs(type(refusal.exception), ValueError)
        return str(refusal.exception)

    def no_harness(self):
        """Fail the test if a harness is started."""
        guard = patch.object(harness, '_spawn', side_effect=AssertionError('a bench harness started'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_08_B6_replay_candidate_gives_what_the_tools_candidate_config_gives(self):
        candidate = saved_candidate(self.root / 'candidate')
        given = trial_store.replay_candidate(candidate, self.bench)
        self.assertEqual(given, replay.candidate_config({'candidate': candidate}))
        self.assertEqual(given, {'runtime': 'replay', 'candidate': candidate,
                                 'candidate_sha256': integrity.digest(candidate)})

    def test_08_B6_it_first_refuses_a_candidate_that_lies_in_the_bench_home(self):
        inside = saved_candidate(self.bench / 'held' / 'candidate')
        self.assertEqual(self.refused(inside), IN_BENCH + inside)
        # Before the tool looks at it: a folder that is no candidate, and one that is not there, are refused the same.
        (self.bench / 'empty').mkdir()
        for path in (self.bench / 'empty', self.bench / 'absent', self.bench):
            with self.subTest(path=path.name):
                self.assertEqual(self.refused(str(path)), IN_BENCH + str(path))

    def test_08_B6_it_tells_by_file_identity_so_another_spelling_of_the_path_is_refused_too(self):
        inside = saved_candidate(self.bench / 'candidate')
        # The bench home named through a link is the same folder.
        try:
            (self.root / 'linked').symlink_to(self.bench, target_is_directory=True)
        except (OSError, NotImplementedError) as unsupported:
            self.skipTest(f'This platform cannot make a symlink: {unsupported}')
        self.assertEqual(self.refused(inside, bench=self.root / 'linked'), IN_BENCH + inside)
        spelled = self.root / 'HOME-BENCH' / 'candidate'
        if spelled.is_dir():
            # A file system that ignores letter case: the other spelling names the same folder.
            self.assertEqual(self.refused(str(spelled)), IN_BENCH + str(spelled))
        else:
            self.assertEqual(self.refused(str(spelled)), 'A replay candidate is an absolute directory path: '
                             + str(spelled))

    def test_08_B6_the_benchmarks_rule_of_file_identity_gives_the_tools_answers(self):
        inside = Path(saved_candidate(self.bench / 'candidate'))
        outside = Path(saved_candidate(self.root / 'candidate'))
        cases = [(inside, self.bench), (self.bench, self.bench), (outside, self.bench), (self.root, self.bench),
                 (self.bench / 'absent' / 'deeper', self.bench), (inside, self.root / 'no such folder'),
                 (self.root / 'HOME-BENCH' / 'candidate', self.bench), (inside / 'replay.json', self.bench)]
        answers = [trial_store._inside(path, folder) for path, folder in cases]
        self.assertEqual(answers, [replay._inside(path, folder) for path, folder in cases])
        self.assertEqual(answers[:6], [True, True, False, False, True, False])

    def test_08_B6_a_candidate_that_is_no_text_or_is_empty_is_refused_in_the_tools_words(self):
        for candidate in (None, '', 7, ['a'], Path(self.bench / 'held')):
            with self.subTest(candidate=candidate):
                self.assertEqual(self.refused(candidate), NEEDS)
        # A path that is not absolute is the tool's to refuse, too. It names the path as this system spells it.
        self.assertEqual(self.refused('home-bench/candidate'),
                         'A replay candidate is an absolute directory path: ' + str(Path('home-bench/candidate')))

    def test_08_B6_launch_refuses_such_a_candidate_before_a_harness_starts(self):
        self.no_harness()
        inside = saved_candidate(self.bench / 'candidate')
        with self.assertRaises(ValueError) as refusal:
            harness.launch('tq9-v2', executor='replay', options={'home': str(self.home), 'replay_candidate': inside})
        self.assertEqual(str(refusal.exception), IN_BENCH + inside)
        self.assertEqual(sorted(path.name for path in self.bench.iterdir()), ['candidate'])
        self.assertFalse(self.home.exists())

    def test_08_B6_a_trial_with_another_bench_home_holds_its_candidate_out_of_that_one_alone(self):
        other = self.root / 'other-bench'
        beside, elsewhere = saved_candidate(self.bench / 'candidate'), saved_candidate(other / 'candidate')
        options = {'home': str(self.home), 'bench_home': str(other)}
        launched = []
        with patch.object(harness, '_spawn', side_effect=lambda folder, arguments, payload: launched.append(payload)), \
                patch.object(harness, '_follow', return_value={'trial_id': 'followed'}):
            with self.assertRaises(ValueError) as refusal:
                harness.launch('tq9-v2', executor='replay', options={**options, 'replay_candidate': elsewhere})
            self.assertEqual(str(refusal.exception), IN_BENCH + elsewhere)
            self.assertEqual(launched, [])
            harness.launch('tq9-v2', executor='replay', options={**options, 'replay_candidate': beside})
        [payload] = launched
        self.assertEqual(payload['executor_config'], replay.candidate_config({'candidate': beside}))

    def test_08_B6_suite_candidates_refuses_it_and_so_does_the_suite_call_before_anything_is_stored(self):
        from firmware_bench.benchmark_support import suite_book
        from suite_fixtures import scripted_manifest
        inside = saved_candidate(self.bench / 'candidate')
        with self.assertRaises(ValueError) as refusal:
            suite_book.suite_candidates({'tq9-v2'}, {'tq9-v2': inside}, bench=self.bench)
        self.assertEqual(str(refusal.exception), IN_BENCH + inside)
        outside = saved_candidate(self.root / 'candidate')
        self.assertEqual(suite_book.suite_candidates({'tq9-v2'}, {'tq9-v2': outside}, bench=self.bench),
                         {'tq9-v2': replay.candidate_config({'candidate': outside})})
        book = suite_book.SuiteBook(self.home, lambda method, params=None: {'ok': True, 'pid': 4001})
        with self.assertRaises(ValueError) as refusal:
            book.call('suite_start', {'manifest': scripted_manifest(2), 'executor': 'replay',
                                      'options': {'replay_candidates': {'tq9-v2': inside}},
                                      'request_id': 'replay-suite', 'harness_token_sha256': 'a' * 64,
                                      'harness_pid': 4002})
        self.assertEqual(str(refusal.exception), IN_BENCH + inside)
        self.assertFalse((self.home / 'replay-candidates').exists())
        with contextlib.closing(sqlite3.connect(trial_store.suite_store_path(self.home))) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM suites').fetchone()[0], 0)

    def test_08_B6_start_suite_refuses_it_before_a_harness_starts(self):
        from firmware_bench.benchmark_support import suites
        from suite_fixtures import scripted_manifest
        self.no_harness()
        inside = saved_candidate(self.bench / 'candidate')
        with patch('generative_driver.client.call', side_effect=AssertionError('the service was asked')):
            with self.assertRaises(ValueError) as refusal:
                suites.start_suite(scripted_manifest(2), 'replay', {'replay_candidates': {'tq9-v2': inside}},
                                   home=self.home)
        self.assertEqual(str(refusal.exception), IN_BENCH + inside)
        self.assertEqual(sorted(path.name for path in self.bench.iterdir()), ['candidate'])

    def test_08_B6_the_launch_and_the_suite_book_use_it_and_no_longer_hand_the_tool_a_bench_home(self):
        for name, function in (('harness', 'launch'), ('suite_book', 'suite_candidates')):
            tree = ast.parse((SUPPORT / (name + '.py')).read_bytes())
            [found] = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function]
            called = [ast.unparse(node.func) for node in ast.walk(found) if isinstance(node, ast.Call)]
            with self.subTest(function=name + '.' + function):
                self.assertIn('trial_store.replay_candidate', called)
                self.assertNotIn('candidate_config', called)
        keywords = [keyword.arg for path in sorted(SUPPORT.glob('*.py'))
                    for node in ast.walk(ast.parse(path.read_bytes())) if isinstance(node, ast.Call)
                    and ast.unparse(node.func).endswith('candidate_config') for keyword in node.keywords]
        self.assertEqual(keywords, [])


def benchmark_sources():
    """The benchmark's code: `benchmark.py` and every module under `benchmark_support/`, each as its file and the
    dotted name of its package."""
    return [(PACKAGE / 'benchmark.py', 'firmware_bench'),
            *((path, 'firmware_bench.benchmark_support') for path in sorted(SUPPORT.rglob('*.py')))]


# The folder that holds each of the three packages.
HOLDERS = {'firmware_bench': PACKAGE.parent, 'generative_driver': TOOL.parent, 'interface_runtime': RUNTIME.parent}


def is_module(name):
    """Whether the dotted `name` is a module or a package of the benchmark or of the tool."""
    holder = HOLDERS.get(name.split('.')[0])
    if holder is None:
        return False
    base = holder.joinpath(*name.split('.'))
    return base.with_suffix('.py').is_file() or (base / '__init__.py').is_file()


def is_tool(name):
    """Whether the dotted `name` is a tool module: `interface_runtime` or `generative_driver`, or a module of either.
    The benchmark is the package `firmware_bench`."""
    return name.split('.')[0] in ('interface_runtime', 'generative_driver')


def tool_imports(source, package):
    """What the benchmark source `source` of the package `package` imports from the tool, in import statements at
    any depth: (the tool module, the name taken from it, the name it is bound to). The name taken is None where the
    module itself is imported, which `import a.b` does for `a` and for `a.b`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split('.')
                reached = ['.'.join(parts[:count]) for count in range(1, len(parts) + 1)]
                found += [(alias.name, None, alias.asname)] if alias.asname else [(name, None, name)
                                                                                  for name in reached]
        elif isinstance(node, ast.ImportFrom):
            parts = package.split('.')
            base = parts[:len(parts) - node.level + 1] if node.level else []
            module = '.'.join([*base, *([node.module] if node.module else [])])
            for alias in node.names:
                if is_module(module + '.' + alias.name):
                    found.append((module + '.' + alias.name, None, alias.asname or alias.name))
                else:
                    found.append((module, alias.name, alias.asname or alias.name))
    return [entry for entry in found if is_tool(entry[0])]


def unlisted(source, package):
    """Each way the benchmark source imports or reads a name of the tool that `IMPORTS` does not list, as a
    sentence."""
    listed, problems = client_interface.IMPORTS, []
    tree = ast.parse(source)
    taken = tool_imports(source, package)
    whole = {bound for _, name, bound in taken if name is None}
    for module, name, bound in taken:
        if module not in listed:
            problems.append(f'imports {name or "the module"} of {module}, a module the interface does not list')
        elif name is not None and name not in listed[module]:
            problems.append(f'takes {name} from {module}')
        elif name is None:
            read = sorted({node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                           and ast.unparse(node.value) == bound and bound + '.' + node.attr not in whole})
            problems += [f'reads {attribute} of {module}' for attribute in read
                         if attribute not in listed[module] and attribute != '__file__']
    return problems


def lock_module():
    """The benchmark's module of lock helpers."""
    return importlib.import_module('firmware_bench.benchmark_support.process_lock')


def body(function):
    """The statements of `function` without its doc, as text: what it does, whatever it is called."""
    [found] = ast.parse(textwrap.dedent(inspect.getsource(function))).body
    return ast.dump(ast.Module(body=found.body[1:], type_ignores=[]))


class LockHelperTests(TemporaryHomeCase):
    """The bench harness tells whether a harness runs with lock helpers of its own (08-B13)."""

    def setUp(self):
        super().setUp()
        self.home.mkdir()
        self.lock = self.home / 'harness.lock'

    def held(self):
        """What the benchmark's helper and the tool's say of the lock, which must be the same."""
        mine, tools = lock_module().process_lock_held(self.lock), client.process_lock_held(self.lock)
        self.assertEqual(mine, tools)
        return mine

    def test_08_B13_the_benchmark_has_the_three_helpers_and_they_are_copies_of_the_tools(self):
        module = lock_module()
        self.assertEqual(Path(module.__file__), SUPPORT / 'process_lock.py')
        pairs = ((module.hold_process_lock, client.hold_process_lock),
                 (module.process_lock_held, client.process_lock_held), (module.wait_for_exit, client._wait_for_exit))
        for mine, tools in pairs:
            with self.subTest(helper=mine.__name__):
                self.assertIsNot(mine, tools)
                self.assertEqual(mine.__module__, module.__name__)
                # Statement for statement the tool's, so the branch for Windows is the tool's too.
                self.assertEqual(body(mine), body(tools))
                self.assertEqual(inspect.signature(mine), inspect.signature(tools))
        self.assertEqual(tool_imports((SUPPORT / 'process_lock.py').read_text(encoding='utf-8'),
                                      'firmware_bench.benchmark_support'), [])

    def test_08_B13_a_lock_one_side_holds_is_one_the_other_sees_held(self):
        for holder in (lock_module().hold_process_lock, client.hold_process_lock):
            with self.subTest(holder=holder.__module__):
                stream = holder(self.lock)
                self.assertIsNotNone(stream)
                try:
                    self.assertIs(self.held(), True)
                    # Neither side takes a lock the other holds.
                    self.assertIsNone(lock_module().hold_process_lock(self.lock))
                    self.assertIsNone(client.hold_process_lock(self.lock))
                finally:
                    stream.close()
                self.assertIs(self.held(), False)

    def test_08_B13_both_say_the_same_of_a_lock_nobody_holds_and_of_a_file_that_is_missing(self):
        self.assertFalse(self.lock.exists())
        self.assertIs(self.held(), False)
        self.assertFalse(self.lock.exists(), 'asking made the file')
        self.lock.write_bytes(b'')
        self.assertIs(self.held(), False)
        self.lock.unlink()
        self.lock.mkdir()
        self.held()

    def test_08_B13_both_say_the_same_of_a_process_that_runs_and_of_one_that_has_ended(self):
        module = lock_module()
        process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        try:
            self.assertEqual((module.wait_for_exit(process.pid, 0), client._wait_for_exit(process.pid, 0)),
                             (False, False))
            # Windows counts the wait in timer ticks of 1/64 s, and a wait may end up to one tick early. perf_counter
            # reads a fine clock everywhere; before Python 3.13, time.monotonic on Windows moves by whole ticks.
            early = 1 / 64 if os.name == 'nt' else 0
            started = time.perf_counter()
            self.assertIs(module.wait_for_exit(process.pid, .2), False)
            self.assertGreaterEqual(time.perf_counter() - started, .2 - early)
        finally:
            process.stdin.close()
            process.wait(30)
        self.assertEqual((module.wait_for_exit(process.pid, 0), client._wait_for_exit(process.pid, 0)), (True, True))
        self.assertIs(module.wait_for_exit(process.pid, 5), True)

    def test_08_B13_the_bench_harness_and_the_suite_book_use_the_benchmarks_helpers(self):
        helpers = {'hold_process_lock', 'process_lock_held', 'wait_for_exit', '_wait_for_exit'}
        taken = {}
        for name in ('harness', 'suite_book'):
            tree = ast.parse((SUPPORT / (name + '.py')).read_bytes())
            taken[name] = sorted({(node.level, node.module, alias.name) for node in ast.walk(tree)
                                  if isinstance(node, ast.ImportFrom) for alias in node.names
                                  if alias.name in helpers})
        self.assertEqual(taken, {
            'harness': [(1, 'process_lock', 'hold_process_lock'), (1, 'process_lock', 'process_lock_held'),
                        (1, 'process_lock', 'wait_for_exit')],
            'suite_book': [(1, 'process_lock', 'process_lock_held'), (1, 'process_lock', 'wait_for_exit')]})

    def test_08_B13_a_harness_is_told_to_run_by_the_lock_it_holds_and_else_by_its_process(self):
        from firmware_bench.benchmark_support.suite_book import SuiteBook
        folder = self.home / 'trial'
        folder.mkdir()
        mine = {'pid': os.getpid()}
        # No lock file: the process id decides, and this process runs.
        self.assertIs(harness._harness_exited(None, mine, folder), False)
        self.assertIs(SuiteBook._harness_alive({'harness': {'attached': True, 'pid': os.getpid(), 'lock': None}}),
                      True)
        stream = lock_module().hold_process_lock(folder / harness.HARNESS_LOCK)
        try:
            # A held lock decides, whatever process the record names.
            self.assertIs(harness._harness_exited(None, {'pid': 2 ** 22 + 1}, folder), False)
            attached = {'harness': {'attached': True, 'pid': 2 ** 22 + 1, 'lock': str(folder / harness.HARNESS_LOCK)}}
            self.assertIs(SuiteBook._harness_alive(attached), True)
        finally:
            stream.close()
        # The lock let go: the harness has ended, though the record names a process that runs.
        self.assertIs(harness._harness_exited(None, mine, folder), True)
        self.assertIs(SuiteBook._harness_alive({'harness': {'attached': True, 'pid': os.getpid(),
                                                           'lock': str(folder / harness.HARNESS_LOCK)}}), False)


SUPPORT_PACKAGE = 'firmware_bench.benchmark_support'
# What the scan of 08-B12 says of each form of an import, for a source of `benchmark_support/`.
FORMS_OF_IMPORT = {
    'from generative_driver.configurator import PROTOCOL':
        ['imports PROTOCOL of generative_driver.configurator, a module the interface does not list'],
    'from generative_driver import configurator':
        ['imports the module of generative_driver.configurator, a module the interface does not list'],
    'from generative_driver import __version__':
        ['imports __version__ of generative_driver, a module the interface does not list'],
    'def launch():\n    from generative_driver.client import _wait_for_exit, call':
        ['takes _wait_for_exit from generative_driver.client'],
    'from generative_driver.integrity import IntegrityError, digest':
        ['takes IntegrityError from generative_driver.integrity'],
    'from generative_driver.replay import RUNTIME, candidate_config': ['takes RUNTIME from generative_driver.replay'],
    'from generative_driver.setup import *': ['takes * from generative_driver.setup'],
    'from generative_driver import client\nclient.call("ping")\nclient._request':
        ['reads _request of generative_driver.client'],
    'from generative_driver import client as tool\ntool.default_home()\ntool.SERVICE_LOCK':
        ['reads SERVICE_LOCK of generative_driver.client'],
    'import generative_driver.setup as chosen\nchosen.home_path()': ['reads home_path of generative_driver.setup'],
    'import interface_runtime.engine\ninterface_runtime.engine.validate_model\ninterface_runtime.engine.execute':
        ['reads execute of interface_runtime.engine'],
    'from interface_runtime.package import digest':
        ['imports digest of interface_runtime.package, a module the interface does not list'],
    'import interface_runtime\ninterface_runtime.__file__\ninterface_runtime.engine':
        ['reads engine of interface_runtime'],
    # What the interface lists, and the benchmark's own modules, are no fault.
    'import interface_runtime\ninterface_runtime.__file__': [],
    'from interface_runtime.engine import validate_model\nfrom interface_runtime import engine\n'
    'engine.validate_model': [],
    'from generative_driver import client, replay\nclient.call\nclient.default_home\nclient.__file__\n'
    'replay.candidate_config': [],
    'from generative_driver.client_interface import CALLS, IMPORTS, PROTOCOL, verdict_errors\n'
    'from generative_driver.toolkit import call_tool': [],
    'from .integrity import IntegrityError\nfrom . import trial_store\nfrom ..benchmark import STAGES\nimport json': [],
}
# The tool names the benchmark's code took at the base and takes no more, each as (module, name).
GONE = [('generative_driver.configurator', 'IntegrityError'), ('generative_driver.configurator', 'PROTOCOL'),
        ('generative_driver.configurator', 'digest'), ('generative_driver.configurator', 'equivalence_label'),
        ('generative_driver.integrity', 'IntegrityError'), ('generative_driver.setup', 'home_path'),
        ('generative_driver.replay', 'RUNTIME'), ('generative_driver.client', 'hold_process_lock'),
        ('generative_driver.client', 'process_lock_held'), ('generative_driver.client', '_wait_for_exit')]


def imported_in(module, function):
    """What the function `function` of the benchmark module `module` imports, as (level, module, name)."""
    tree = ast.parse((SUPPORT / (module + '.py')).read_bytes())
    [found] = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function]
    return sorted((node.level, node.module or '', alias.name) for node in ast.walk(found)
                  if isinstance(node, ast.ImportFrom) for alias in node.names)


class ToolImportTests(TrialCase):
    """The benchmark's code imports from the tool only what `IMPORTS` lists (08-B12)."""

    def taken(self):
        """Every (tool module, name) the benchmark's code imports, with the files that do."""
        found = {}
        for path, package in benchmark_sources():
            for module, name, _ in tool_imports(path.read_text(encoding='utf-8'), package):
                found.setdefault((module, name), []).append(path.name)
        return found

    def test_08_B12_the_benchmarks_code_imports_and_reads_only_names_the_interface_lists(self):
        problems = [f'{path.name}: {problem}' for path, package in benchmark_sources()
                    for problem in unlisted(path.read_text(encoding='utf-8'), package)]
        self.assertEqual(problems, [])
        # The scan saw the benchmark's code, with the imports that stay.
        self.assertGreater(len(benchmark_sources()), 60)
        taken = self.taken()
        for kept in (('generative_driver.client', 'call'), ('generative_driver.client', None),
                     ('generative_driver.client_interface', 'verdict_errors'),
                     ('generative_driver.integrity', 'digest'), ('generative_driver.replay', 'candidate_config'),
                     ('generative_driver.toolkit', 'call_tool'), ('interface_runtime', None),
                     ('interface_runtime.engine', 'validate_model')):
            with self.subTest(kept=kept):
                self.assertIn(kept, taken)

    def test_08_B12_the_scan_sees_each_form_of_an_import_and_each_read_of_an_imported_module(self):
        for source, problems in FORMS_OF_IMPORT.items():
            with self.subTest(source=source):
                self.assertEqual(unlisted(source, SUPPORT_PACKAGE), problems)
        # A source of the package itself, as `benchmark.py` is, names the tool's modules the same way.
        self.assertEqual(unlisted('from generative_driver.setup import home_path, resolve_executor', 'firmware_bench'),
                         ['takes home_path from generative_driver.setup'])
        self.assertEqual(unlisted('from .benchmark_support import harness\nfrom generative_driver.client import call',
                                  'firmware_bench'), [])

    def test_08_B12_what_the_benchmark_took_outside_the_list_is_gone(self):
        taken = self.taken()
        self.assertEqual({entry: taken[entry] for entry in GONE if entry in taken}, {})
        modules = {module for module, _ in taken}
        self.assertEqual(sorted(modules - set(client_interface.IMPORTS)), [])
        self.assertNotIn('generative_driver.configurator', modules)
        self.assertNotIn('generative_driver.reporting', modules)

    def test_08_B12_each_name_comes_from_its_new_place(self):
        own_error = (1, 'integrity', 'IntegrityError')
        self.assertIn(own_error, imported_in('harness', 'check_snapshot'))
        self.assertIn(own_error, imported_in('harness', 'conclude'))
        self.assertEqual([entry for entry in imported_in('harness', 'conclude') if entry[2] == 'IntegrityError'],
                         [own_error])
        listed_protocol = (0, 'generative_driver.client_interface', 'PROTOCOL')
        self.assertIn(listed_protocol, imported_in('suite_reporting', '_recorded_equivalence'))
        self.assertIn(listed_protocol, imported_in('run_reporting', 'markdown'))
        self.assertIn((1, 'labels', 'equivalence_label'), imported_in('run_reporting', 'markdown'))
        self.assertEqual(imported_in('authoring', 'evaluator_directory'),
                         [(0, 'generative_driver.client', 'default_home')])
        self.assertEqual(imported_in('suite_book', 'stored_candidate'), [(0, 'generative_driver.integrity', 'digest')])

    def test_08_B12_the_benchmarks_integrity_error_is_its_own_class_and_a_value_error(self):
        from firmware_bench.benchmark_support import integrity as own
        self.assertIsNot(own.IntegrityError, integrity.IntegrityError)
        self.assertEqual(own.IntegrityError.__name__, 'IntegrityError')
        self.assertTrue(issubclass(own.IntegrityError, ValueError))
        self.assertFalse(issubclass(own.IntegrityError, integrity.IntegrityError))

    def test_08_B12_the_check_of_a_snapshot_raises_the_benchmarks_integrity_error_with_the_same_texts(self):
        from firmware_bench.benchmark_support import integrity as own
        folder, record = self.trial()
        result, run_dir = self.result(record), self.run_dir(record)
        self.assertEqual(harness.check_snapshot(folder, run_dir, result)['case_pin']['case_id'], CASE)
        changed = {**result, 'client_record': {**result['client_record'], 'snapshot_sha256': 'f' * 64}}
        with self.assertRaises(ValueError) as refusal:
            harness.check_snapshot(folder, run_dir, changed)
        self.assertIs(type(refusal.exception), own.IntegrityError)
        self.assertEqual(str(refusal.exception), 'Execution snapshot hash differs from saved run')
        other = {**harness.read_trial(folder)['executor_config'], 'model': 'another-model'}
        with self.assertRaises(ValueError) as refusal:
            harness.check_snapshot(folder, run_dir, result, config=other)
        self.assertIs(type(refusal.exception), own.IntegrityError)
        self.assertEqual(str(refusal.exception),
                         'Execution snapshot implementation or runtime environment changed; start a new run')

    def unscored_for(self, name, error):
        """The reason of the unscored record the harness posts for a trial whose grading raises `error`."""
        self.calls.clear()
        self.trial(name, outcome=error)
        [record] = [params['equivalence'] for params in self.sent('record_evaluation')]
        self.assertEqual(record['status'], 'unscored')
        [difference] = record['diff']
        return difference['reason']

    def test_08_B12_conclude_counts_only_the_benchmarks_integrity_error_as_an_integrity_stop(self):
        from firmware_bench.benchmark_support import integrity as own
        self.assertEqual(self.unscored_for('own', own.IntegrityError('A hash differs')), 'integrity: A hash differs')
        # The tool's class is any other fault of the evaluator's host: nothing the harness calls while it grades
        # raises it.
        self.assertEqual(self.unscored_for('tools', integrity.IntegrityError('A hash differs')),
                         'evaluator host fault: IntegrityError')

    def test_08_B12_a_stored_candidate_keeps_the_runtime_of_the_configuration_the_tool_gave(self):
        from firmware_bench.benchmark_support import suite_book
        candidate = saved_candidate(self.root / 'candidate')
        checked = replay.candidate_config({'candidate': candidate})
        store = self.root / 'store'
        stored = suite_book.stored_candidate(checked, store)
        self.assertEqual(stored, {'runtime': 'replay', 'candidate': str(store / checked['candidate_sha256']),
                                  'candidate_sha256': checked['candidate_sha256']})
        self.assertEqual(stored['runtime'], checked['runtime'])
        self.assertEqual(integrity.digest(stored['candidate']), checked['candidate_sha256'])

    def test_08_B12_an_evaluator_folder_under_the_homes_runs_is_refused_as_before(self):
        from firmware_bench.benchmark_support import authoring
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.home)}):
            inside = self.home / 'runs' / 'run-1' / 'evaluator'
            with self.assertRaises(ValueError) as refusal:
                authoring.evaluator_directory(str(inside))
            outside = self.root / 'private-evaluator'
            self.assertEqual(authoring.evaluator_directory(str(outside)), outside)
        self.assertNotEqual(str(refusal.exception), '')


# The callables through which the benchmark's code sends a request, each with the place of the method among its
# arguments: the client's `call` under its names, the harness's one callable for its requests, the suite book's
# `_ask`, and the two helpers that take the callable first.
SENDERS = {'call': 0, 'client_call': 0, 'configurator': 0, 'asked': 0, '_ask': 0, '_reply': 1, '_post': 1}
# Where a method is handed on as it came: the wrappers through which the sends above pass.
FORWARDING = [('harness.py', '_configurator.configurator'), ('harness.py', '_post'), ('harness.py', 'resume'),
              ('suite_book.py', 'answered'), ('suite_book.py', 'suite_call.configurator'), ('trial_store.py', '_reply')]


def sent_methods(source):
    """Each request the benchmark source `source` sends, as (the function it lies in, the method). The method is
    None where a variable names it. A callable a function defines itself with `def` is no sender: `benchmark.py`
    calls a developer tool through one named `call`."""
    found = []

    def visit(node, functions, defined):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                own = {inner.name for inner in child.body if isinstance(inner, ast.FunctionDef)}
                visit(child, [*functions, child.name], defined | own)
                continue
            if isinstance(child, ast.Call):
                callee = child.func
                name = (callee.id if isinstance(callee, ast.Name) else
                        callee.attr if isinstance(callee, ast.Attribute) else None)
                local = isinstance(callee, ast.Name) and name in defined
                if name in SENDERS and not local and len(child.args) > SENDERS[name]:
                    method = child.args[SENDERS[name]]
                    found.append(('.'.join(functions), method.value if isinstance(method, ast.Constant)
                                  and isinstance(method.value, str) else None))
            visit(child, functions, defined)
    visit(ast.parse(source), [], set())
    return found


class Recording:
    """Keeps every method the service of a test receives, whoever sends it."""

    def setUp(self):
        super().setUp()
        self.received = []
        real = self.controller.call

        def call(method, params=None):
            self.received.append(method)
            return real(method, params)
        self.controller.call = call


class SentMethodTests(unittest.TestCase):
    """The benchmark's code sends the service only calls the interface lists (08-B14), by its source. The launch,
    the one exception it had, went with Feature 09 of this Run (09-B5 of docs/features/untangle/09-commands.md)."""

    @classmethod
    def setUpClass(cls):
        cls.sent = [(path.name, function, method) for path, _ in benchmark_sources()
                    for function, method in sent_methods(path.read_text(encoding='utf-8'))]

    def test_08_B14_every_method_the_benchmarks_code_sends_the_service_is_a_listed_call(self):
        named = {method for _, _, method in self.sent if method is not None}
        # A suite call is served by the suite book in the benchmark's own process, and never reaches the service.
        to_service = {method for method in named if not method.startswith('suite_')}
        self.assertEqual(sorted(to_service - set(client_interface.CALLS)), [])
        self.assertEqual(sorted(set(client_interface.CALLS) - to_service), [])
        self.assertGreater(len(named - to_service), 10)

    def test_08_B14_a_method_is_handed_on_unnamed_only_by_the_wrappers_the_named_sends_pass_through(self):
        self.assertEqual(sorted({(file, function) for file, function, method in self.sent if method is None}),
                         FORWARDING)

    def test_08_B14_the_scan_sees_a_send_through_each_callable_and_leaves_out_a_callable_defined_in_place(self):
        source = '\n'.join((
            'def harness(configurator, call):',
            "    configurator('ping', {})",
            "    self._ask('seal', {'run_id': run_id})",
            "    _post(call, 'record_evaluation', params)",
            "    client.call('tool', {})",
            '    call(method, params)',
            'def smoke():',
            '    def call(name, **arguments):',
            '        return name',
            "    call('probe_run')",
            "    values.get('status')"))
        self.assertEqual(sent_methods(source), [('harness', 'ping'), ('harness', 'seal'),
                                                ('harness', 'record_evaluation'), ('harness', 'tool'),
                                                ('harness', None)])


class SentByATrialTests(Recording, SnapshotCase):
    """Every request the benchmark's code sends during a scripted trial is a listed call (08-B14)."""

    def test_08_B14_during_scripted_trials_the_service_receives_only_listed_calls(self):
        folder, record = self.trial('graded')
        self.assertEqual(record['status'], 'evaluated', record)
        stopped, _ = self.stopped_trial('stopped')
        self.assertEqual(self.again(harness.reattach, stopped)['status'], 'evaluated')
        ungraded, _ = self.ungraded_trial('ungraded')
        self.assertEqual(self.again(harness.resume, ungraded)['status'], 'evaluated')
        sent = {method for method, _ in self.calls}
        self.assertEqual(sorted(sent - set(client_interface.CALLS)), [])
        self.assertEqual(sorted(set(self.received) - set(client_interface.CALLS)), [])
        # The trials were started, followed, continued on a new device and graded through those calls.
        self.assertEqual(sent, {'ping', 'start', 'status', 'result', 'attach_device', 'resume', 'record_evaluation'})
        self.assertLessEqual(sent - {'ping'}, set(self.received))


class SentByASuiteTests(Recording, ScriptedSuiteCase):
    """Every request the benchmark's code sends during a scripted suite is a listed call (08-B14)."""

    def test_08_B14_during_a_scripted_suite_the_service_receives_only_listed_calls(self):
        self.assertEqual((self.record['status'], self.record['accepted']), ('finished', True), self.record)
        sent = {method for method, _ in self.during}
        received = set(self.fixture.received)
        self.assertEqual(sorted(sent - set(client_interface.CALLS)), [])
        self.assertEqual(sorted(received - set(client_interface.CALLS)), [])
        self.assertEqual(sent, {'ping', 'start', 'status', 'result', 'record_evaluation'})
        self.assertLessEqual(sent - {'ping'}, received)
        # A reader of the suite asks the book, whose own requests are listed calls too.
        before = len(self.fixture.received)
        for method in ('suite_status', 'suite_events', 'suite_result'):
            self.assertTrue(self.asked(method, suite_id=self.suite_id)['ok'])
        self.assertEqual(sorted(set(self.fixture.received[before:]) - set(client_interface.CALLS)), [])


# The tool's interface document, which lies two folders above these tests while the benchmark is in the tool's
# repository. The tests that read it are skipped where it is not there.
DOCUMENT = Path(__file__).resolve().parents[2] / 'docs' / 'client-interface.md'
# The names in SQL that are no table and no column.
SQL_WORDS = {'SELECT', 'FROM', 'WHERE', 'ORDER', 'BY', 'AND'}
# The places where the benchmark's code opens the service's database, and where it reads the operator's settings.
DATABASE_READERS = [('mcp_reuse.py', 'owner_evidence'), ('scorecard.py', '_records'),
                    ('trial_store.py', '_is_run_of_suite')]
SETTINGS_READERS = [('harness.py', 'settled_executor'), ('suite_book.py', '_configured_executors')]


def records_section():
    """The lines of the interface document's section `Records on disk`."""
    if not DOCUMENT.is_file():
        raise unittest.SkipTest(f'The tool\'s interface document is not two folders above these tests: {DOCUMENT}')
    lines = DOCUMENT.read_text(encoding='utf-8').splitlines()
    start = lines.index('## Records on disk') + 1
    end = next(number for number in range(start, len(lines)) if lines[number].startswith('## '))
    return lines[start:end]


def named_columns():
    """The tables the section names, each with its columns; `rowid` is a column of `handoffs` when the section
    says rows of that table are read by it."""
    rows = [re.findall(r'`([^`]+)`', line) for line in records_section() if line.startswith('| `')]
    named = {table: set(columns) for table, *columns in rows}
    if any('`handoffs`' in line and '`rowid`' in line for line in records_section()):
        named['handoffs'].add('rowid')
    return named


def code_texts(function):
    """The texts in the code of the syntax node `function`, leaving out docs."""
    docs = {id(node.value) for node in ast.walk(function) if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)}
    return [node.value for node in ast.walk(function) if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and id(node) not in docs]


def functions_holding(text):
    """The innermost functions of the benchmark's code that hold `text` in a text of their code, as (file, name)."""
    found = []
    for path, _ in benchmark_sources():
        tree = ast.parse(path.read_bytes())
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        holding = [node for node in functions if any(text in held for held in code_texts(node))]
        inner = [node for node in holding
                 if not any(other is not node and other in ast.walk(node) for other in holding)]
        found += [(path.name, node.name) for node in inner]
        if not holding and any(text in held for held in code_texts(tree)):
            found.append((path.name, None))
    return sorted(found)


def function_of(file, name):
    tree = ast.parse((SUPPORT / file).read_bytes())
    [found] = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
    return found


class HomeReadTests(unittest.TestCase):
    """The benchmark's code reads in the home only what `Records on disk` names (08-B15), by its source."""

    def test_08_B15_it_opens_the_database_at_three_places_and_read_only(self):
        self.assertEqual(functions_holding('runs.sqlite3'), DATABASE_READERS)
        for file, name in DATABASE_READERS:
            with self.subTest(place=file + ':' + name):
                opened = [node for node in ast.walk(function_of(file, name)) if isinstance(node, ast.Call)
                          and ast.unparse(node.func) == 'sqlite3.connect']
                self.assertEqual(len(opened), 1)
                self.assertIn("+ '?mode=ro'", ast.unparse(opened[0].args[0]))
                self.assertEqual([(keyword.arg, ast.unparse(keyword.value)) for keyword in opened[0].keywords],
                                 [('uri', 'True')])

    def test_08_B15_every_statement_there_names_only_tables_and_columns_the_section_names(self):
        named = named_columns()
        self.assertEqual(sorted(named), ['events', 'handoffs', 'progress', 'reports', 'runs', 'verdicts'])
        read = {}
        for file, name in DATABASE_READERS:
            statements = [node.args[0].value for node in ast.walk(function_of(file, name))
                          if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                          and node.func.attr == 'execute']
            self.assertTrue(statements, file)
            for statement in statements:
                words = re.findall(r'[A-Za-z_][A-Za-z_0-9]*', statement)
                table = words[words.index('FROM') + 1]
                columns = {word for word in words if word not in SQL_WORDS and word != table}
                with self.subTest(statement=statement):
                    self.assertTrue(statement.startswith('SELECT '))
                    self.assertIn(table, named)
                    self.assertEqual(sorted(columns - named[table]), [])
                read.setdefault(table, set()).update(columns)
        # Together the three places read every table the section names.
        self.assertEqual(sorted(read), sorted(named))

    def test_08_B15_the_scan_of_a_statement_sees_a_table_or_a_column_the_section_does_not_name(self):
        named = named_columns()
        for statement, table, unnamed in (('SELECT spec FROM runs WHERE id=?', 'runs', ['spec']),
                                          ('SELECT payload FROM handoffs ORDER BY created,rowid', 'handoffs', []),
                                          ('SELECT payload FROM events ORDER BY rowid', 'events', ['rowid']),
                                          ('SELECT state FROM assignments WHERE run_id=?', 'assignments', None)):
            words = re.findall(r'[A-Za-z_][A-Za-z_0-9]*', statement)
            self.assertEqual(words[words.index('FROM') + 1], table)
            columns = {word for word in words if word not in SQL_WORDS and word != table}
            with self.subTest(statement=statement):
                self.assertEqual(sorted(columns - named[table]) if table in named else None, unnamed)

    def test_08_B15_it_reads_the_operators_settings_at_two_places_and_takes_only_the_executors(self):
        self.assertEqual(functions_holding('config.json'), SETTINGS_READERS)
        for file, name in SETTINGS_READERS:
            with self.subTest(place=file + ':' + name):
                code = ast.unparse(function_of(file, name))
                self.assertEqual(code.count('json.loads('), 1)
                self.assertIn("json.loads(path.read_text(encoding='utf-8')).get('executors', {})", code)

    def test_08_B15_it_names_no_other_file_of_the_home(self):
        from generative_driver import service_log
        kept_by_the_service = ('service.json', client.SERVICE_LOCK, 'startup.lock', service_log.LOG_NAME)
        self.assertEqual(kept_by_the_service, ('service.json', 'service.lock', 'startup.lock', 'daemon.log'))
        for name in kept_by_the_service:
            with self.subTest(name=name):
                self.assertEqual(functions_holding(name), [])
        # The folder of saved candidates is named where a replay suite stores its own.
        self.assertEqual(functions_holding('replay-candidates'), [('suite_book.py', '_replay_suite_config')])


class GraderListTests(unittest.TestCase):
    """The grader's list of modules that are not grading code follows the move (08-B3)."""

    def test_08_B3_the_list_names_the_two_new_modules_and_no_longer_the_tools_report_module(self):
        listed = grader_identity.SYSTEM_MODULES
        self.assertIn('benchmark_support.run_reporting', listed)
        self.assertIn('benchmark_support.process_lock', listed)
        self.assertNotIn('reporting', listed)
        self.assertEqual(len(listed), len(set(listed)))
        for name in ('benchmark_support.run_reporting', 'benchmark_support.process_lock'):
            with self.subTest(module=name):
                self.assertTrue((PACKAGE / (name.replace('.', '/') + '.py')).is_file())

    def test_08_B3_grading_code_imports_neither_of_the_two_new_modules(self):
        closure = grader_identity.grader_closure()
        self.assertIn('benchmark_support.evidence', closure)
        self.assertEqual([name for name in closure if name.endswith(('.run_reporting', '.process_lock'))], [])
        # Nor any module of the tool, by the name of either of its packages (12-B2 of Feature 12 of this Run).
        outside = ('generative_driver', 'interface_runtime', *grader_identity.SYSTEM_MODULES)
        beyond = [name for name in closure
                  if any(name == listed or name.startswith(listed + '.') for listed in outside)]
        self.assertEqual(beyond, [])


if __name__ == '__main__':
    unittest.main()

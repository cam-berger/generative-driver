"""No text a worker gets names what a package no longer holds, and the tools table says what the three emit tools do
now (docs/features/package-format/03-package.md, Behaviour 03-B16).

The prompts are those of scripted runs: no model runs and no device is touched."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_supplied_runs import FullRunCase
from worker_texts import earlier_names, gone_names, texts_of

# Words of the three emit tools' descriptions that told of a package with code of its own.
OUTDATED = ('driver entry', 'copy of the runtime', 'runtime currency', 'standalone', 'archived runtime', 'bundled',
            'replay process', 'command line', 'a reference')


def described(name):
    """The description of the tool `name` in the tools table, as one line."""
    from generative_driver.toolkit import list_tools
    [tool] = [tool for tool in list_tools() if tool['name'] == name]
    return ' '.join(tool['description'].split())


class ToolsTableTests(unittest.TestCase):
    def test_03_B16_no_tool_description_names_what_a_package_no_longer_holds(self):
        from generative_driver.toolkit import list_tools
        tools = {tool['name']: json.dumps(tool) for tool in list_tools()}
        self.assertEqual(gone_names(tools), [])
        self.assertEqual(earlier_names(tools), [])

    def test_03_B16_the_tools_table_says_what_the_three_emit_tools_do_now(self):
        package = described('emit_package')
        for held in ('README.md', 'manifest.json', 'mcp.json', 'drivers/', 'evidence/'):
            self.assertIn(held, package)
        self.assertIn('no code', package)
        check = described('emit_check')
        self.assertIn('README.md and mcp.json', check)
        self.assertIn('what the tool renders', check)
        self.assertIn('replays its probe record in process on the installed runtime', check)
        live = described('emit_test_live')
        self.assertIn('in a child process on the installed runtime', live)
        for name in ('emit_package', 'emit_check', 'emit_test_live'):
            for words in OUTDATED:
                with self.subTest(tool=name, words=words):
                    self.assertNotIn(words, described(name))


class WorkerTextTests(unittest.TestCase):
    def test_03_B16_no_skill_or_document_of_the_tool_names_what_a_package_no_longer_holds(self):
        from generative_driver.toolkit import resources_root
        texts = texts_of(resources_root())
        # The skills, the model document and the briefs of the stages are among them.
        self.assertTrue({'docs/INTERFACE_MODEL.md', 'interpret_tasks/HANDLER.md'} <= set(texts), sorted(texts)[:20])
        self.assertTrue(any(name.startswith('toolchain/skills/') for name in texts))
        self.assertEqual(gone_names(texts), [])

    def test_03_B16_no_prompt_of_a_generic_run_names_what_a_package_no_longer_holds(self):
        from generative_driver.configurator import Controller
        from test_configurator import probed, scripted_stages, wait_stopped
        from test_package_data import emitted
        prompts = []

        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'interpret':
                model = workspace / 'model'
                model.mkdir()
                (model / 'model.json').write_text('{"schema": "interface-model/7"}', encoding='utf-8')
                return {'ok': True, 'artifacts': [str(model)], 'checks': []}
            if stage == 'probe':
                return probed(workspace, assignment)
            if stage == 'emit':
                package = shutil.copytree(emitted(Path(run_dir) / 'emitted-for-the-test')[0]['package_dir'],
                                          workspace / 'package')
                return {'ok': True, 'artifacts': [str(package)], 'checks': []}
            return {'ok': True, 'artifacts': [], 'checks': []}

        def prepare(original, spec, stage, run_dir, workspace, accepted):
            # Probe, emit and reuse are prepared as a generic run prepares them; the other stages need inputs of
            # their own and get the prompt the configurator writes around an empty preparation.
            if stage in ('probe', 'emit', 'reuse'):
                return original(spec, stage, run_dir, workspace, accepted)
            return {'inputs': [], 'allowed_tools': []}
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory).resolve() / 'home')
            try:
                with scripted_stages(controller, check=check, prepare=prepare, prompts=prompts):
                    run = controller.call('start', {'goal': 'Read the temperature through the emitted driver',
                                                    'executor_config': {'command': ['never-launched']}})
                    result = wait_stopped(controller, run)
            finally:
                controller.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([stage for stage, _ in prompts], ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
        self.assertEqual(gone_names(dict(prompts)), [])


class SuppliedWorkerTextTests(FullRunCase):
    def test_03_B16_no_text_of_a_run_on_a_supplied_device_names_what_a_package_no_longer_holds(self):
        self.assertEqual([call['stage'] for call in self.prepared],
                         ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
        texts = {'what the stage package prepared for ' + call['stage']: json.dumps(call['prepared'])
                 for call in self.prepared}
        for worker in self.fixture.seen:
            texts['the task of ' + worker.stage] = json.dumps(worker.task)
            for name, text in texts_of(worker.workspace).items():
                texts[worker.stage + ': ' + name] = text
        self.assertEqual(gone_names(texts), [])


if __name__ == '__main__':
    unittest.main()

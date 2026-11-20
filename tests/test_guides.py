"""The guides show what is built, and what they show can be run (Spec 03 of the package-format Run, 03-B16).

The guides are the front page and the documents under `docs/`, read as UTF-8 text from the checkout. A command line
of a guide is given to the parser that `generative_driver.cli.main` builds, and the command is stopped right after the
parse, so nothing of it runs. The package is emitted from an invented model with replayed evidence.

No model run and no device.
"""
import argparse
import ast
import contextlib
import inspect
import io
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
# The guides whose code blocks show command lines.
COMMAND_GUIDES = ('README.md', 'docs/setup.md', 'docs/extending-drivers-and-mcp.md', 'docs/support.md',
                  'docs/codex.md')
# The documents that name nothing a package no longer holds and no format the tool no longer reads.
CURRENT_DOCUMENTS = ('README.md', 'CONTEXT.md', 'docs/setup.md', 'docs/support.md', 'docs/codex.md',
                     'docs/dashboard.md', 'docs/extending-drivers-and-mcp.md', 'docs/client-interface.md')
GONE = ('python -m driver', 'REFERENCE.md', 'mcp-tools.json', 'contract.json', 'protocol.json', 'manifest.sha256',
        'interface-package/1', 'interface-package/2', 'interface-model/3', 'interface-model/4', 'interface-model/5',
        'interface-model/6', 'runtime_current', 'package_execute', 'device_mcp --package', 'register model', 'Model 1')
# The ways a guide writes the command: its installed name, or the module, each also behind `uv run`.
COMMAND = re.compile(r'(?:uv run )?(?:generative-driver|(?:\S*[\\/])?python[\d.]*(?:\.exe)? -m generative_driver'
                     r'(?:\.cli)?)(?=\s|$)')
DATED = re.compile(r'\d{4}-\d{2}-\d{2}')


def text_of(name):
    """The text of the document `name` of the checkout."""
    return (REPO / name).read_text(encoding='utf-8')


def code_blocks(text):
    """The fenced code blocks of the Markdown `text`, each as the number of its first line and its lines."""
    blocks, inside = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith('```'):
            if inside is None:
                inside = (number + 1, [])
            else:
                blocks.append(inside)
                inside = None
        elif inside is not None:
            inside[1].append(line)
    return blocks


def command_arguments(line):
    """The arguments `line` gives the command, or None for a line that does not run the command."""
    line = line.strip()
    found = COMMAND.match(line)
    return shlex.split(line[found.end():]) if found else None


def command_lines(names=COMMAND_GUIDES):
    """Every line of a code block of the guides `names` that runs the command, as its place, the line and the
    arguments it gives the command."""
    return [(f'{name}:{first + offset}', line.strip(), command_arguments(line))
            for name in names for first, lines in code_blocks(text_of(name))
            for offset, line in enumerate(lines) if command_arguments(line) is not None]


class _Parsed(Exception):
    """The command's parser accepted its arguments; raised in place of running the command."""


def refusal(arguments):
    """Why the command's own parser refuses `arguments`, or None when it accepts them. The parser is the one
    `generative_driver.cli.main` builds, and the command is stopped right after the parse."""
    from generative_driver import cli
    parse = argparse.ArgumentParser.parse_args

    def parse_and_stop(parser, args=None, namespace=None):
        parse(parser, args, namespace)
        raise _Parsed()
    said = io.StringIO()
    with patch.object(argparse.ArgumentParser, 'parse_args', parse_and_stop), \
            contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
        try:
            cli.main(list(arguments))
        except _Parsed:
            return None
        except SystemExit as stopped:
            lines = said.getvalue().strip().splitlines()
            return lines[-1] if lines else 'the command exited with ' + str(stopped.code)
    raise AssertionError('The command ran without parsing its arguments: ' + ' '.join(arguments))


def sections(text):
    """The second-level sections of the Markdown `text`, as pairs of a heading and the lines under it. The lines
    before the first such heading are under the heading ''."""
    found = [('', [])]
    for line in text.splitlines():
        if line.startswith('## '):
            found.append((line[3:].strip(), []))
        else:
            found[-1][1].append(line)
    return found


def paragraphs(lines):
    """The paragraphs of `lines`, each as one line of text."""
    return [' '.join(part.split()) for part in re.split(r'\n\s*\n', '\n'.join(lines)) if part.strip()]


def exchange(text, capture, expected=None):
    """One line sent to the invented device and one line read back."""
    step = {'op': 'exchange', 'tx': [{'text': text}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128},
            'timeout_ms': 500, 'capture': capture}
    return {**step, 'expect': {'equals_hex': expected}} if expected else step


# An invented device that answers `ID?` with `SAMP1` and `T` with a temperature.
IDENTIFY = {'effect': 'read', 'parameters': {}, 'outputs': {}, 'steps': [exchange('ID?\n', 'id', '53414d50310a')]}
MEASURE = {'effect': 'read', 'parameters': {}, 'description': 'Read the temperature of the probe.',
           'steps': [exchange('T\n', 'reply')],
           'outputs': {'temperature': {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T:', 'unit': 'Cel',
                                       'description': 'Temperature of the probe.'}}}
REPLAY = {'schema': 'interface-replay/1', 'transactions': [
    {'tx_hex': '49443f0a', 'rx_hex': '53414d50310a', 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128},
     'timeout_ms': 500},
    {'tx_hex': '540a', 'rx_hex': '543a32312e350a', 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128},
     'timeout_ms': 500}]}


def model_of(**operations):
    """An interface model of the invented device `SAMP1` with `operations`, of which `identify` is its identity."""
    return {'schema': 'interface-model/7', 'device': {'name': 'SAMP1'}, 'channel': {'type': 'tcp'},
            'identity': {'operation': 'identify'}, 'operations': operations,
            'provenance': [{'item': 'operations.' + name, 'source': 'invented for this test'} for name in operations]}


def emitted_files(root):
    """The files of a package `emit_package` writes for the invented device, by their paths from its folder."""
    from generative_driver.toolkit import call_tool
    source = root / 'model'
    source.mkdir()
    (source / 'model.json').write_text(json.dumps(model_of(identify=IDENTIFY, measure=MEASURE)), encoding='utf-8')
    (root / 'replay.json').write_text(json.dumps(REPLAY), encoding='utf-8')
    probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(source), 'operation': 'measure',
                                    'n': 1, 'replay': str(root / 'replay.json')})
    assert probe.get('ok'), probe
    emitted = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(source),
                                         'probe': probe['probe']})
    assert emitted.get('ok'), emitted
    package = Path(emitted['package_dir'])
    return sorted(path.relative_to(package).as_posix() for path in package.rglob('*') if path.is_file())


class CommandLineTests(unittest.TestCase):
    """Every command line a guide shows is one the command takes (03-B16)."""

    def test_03_B16_guides_every_command_line_of_a_code_block_is_accepted_by_the_parser(self):
        shown = command_lines()
        refused = [f'{place}: {line}\n    {why}' for place, line, arguments in shown
                   for why in [refusal(arguments)] if why]
        self.assertEqual(refused, [], '\n'.join(refused))
        # What is built is among what is shown: the one command that serves a package, with its flags.
        serving = [arguments for _, _, arguments in shown if 'serve' in arguments]
        self.assertTrue(any('--binding' in arguments for arguments in serving), 'No guide shows `serve --binding`')
        self.assertTrue(any('--allow-effect' in arguments for arguments in serving),
                        'No guide shows `serve` with `--allow-effect`')
        self.assertGreater(len(shown), 20)

    def test_03_B16_guides_the_scan_reads_each_way_the_command_is_written_and_the_parser_can_refuse(self):
        for line, arguments in (
                ('generative-driver doctor', ['doctor']),
                ('uv run generative-driver smoke', ['smoke']),
                ('python -m generative_driver dashboard RUN_ID', ['dashboard', 'RUN_ID']),
                ('.venv/bin/python -m generative_driver.cli tool emit_check --arguments check.json',
                 ['tool', 'emit_check', '--arguments', 'check.json']),
                ('.\\.venv\\Scripts\\python.exe -m generative_driver service start', ['service', 'start']),
                ('python -m generative_driver --home "path to state" dashboard RUN_ID',
                 ['--home', 'path to state', 'dashboard', 'RUN_ID']),
                ('generative-driver serve PACKAGE --binding \'{"host": "127.0.0.1", "port": 12345}\'',
                 ['serve', 'PACKAGE', '--binding', '{"host": "127.0.0.1", "port": 12345}'])):
            with self.subTest(line=line):
                self.assertEqual(command_arguments(line), arguments)
        # Another program, another module of the tool and a line of data run no command of the tool.
        for line in ('python3 -m venv .venv', 'codex mcp add generative-driver -- /absolute/path/to/python -m '
                     'generative_driver.mcp', 'python -m generative_driver.examples.measurement_average --package P',
                     '{"command": "generative-driver", "args": ["serve"]}', 'uv sync --extra dev'):
            with self.subTest(line=line):
                self.assertIsNone(command_arguments(line))
        self.assertIsNone(refusal(['dashboard', 'RUN_ID', '--no-open']))
        self.assertIn("invalid choice: 'no-such-command'", refusal(['no-such-command']))
        self.assertIn('--arguments', refusal(['tool', 'emit_check']))


class PackageListingTests(unittest.TestCase):
    """The guide lists the files a package holds (03-B16)."""

    def test_03_B16_guides_the_package_listing_equals_the_files_emit_package_writes(self):
        listings = [lines for _, lines in code_blocks(text_of('docs/extending-drivers-and-mcp.md'))
                    if lines and lines[0].strip() == '<package>/']
        self.assertEqual(len(listings), 1, 'The guide has one listing of a package, under `<package>/`')
        listed = sorted(line.split()[0].replace('<driver>', 'samp1') for line in listings[0][1:] if line.strip())
        with tempfile.TemporaryDirectory(prefix='guide package ') as temporary:
            self.assertEqual(listed, emitted_files(Path(temporary).resolve()))
        # The listing names a driver by a placeholder, which this package fills with the name of its device.
        self.assertEqual(listed, ['README.md', 'drivers/samp1.json', 'evidence/samp1.probe.json', 'manifest.json',
                                  'mcp.json'])


class CurrentTextTests(unittest.TestCase):
    """The guides, the design and the decision records describe what is built (03-B16)."""

    def test_03_B16_guides_name_nothing_a_package_no_longer_holds_and_no_earlier_format(self):
        named = [f'{name} names `{gone}`' for name in CURRENT_DOCUMENTS for gone in GONE if gone in text_of(name)]
        self.assertEqual(named, [], '\n'.join(named))

    def test_03_B16_guides_the_design_gets_a_section_of_2026_11_19_and_keeps_its_dated_sections(self):
        design = sections(text_of('docs/design.md'))
        headings = [heading for heading, _ in design]
        for kept in ('Original design — 2026-10-03', 'Assistive protocol amendment — 2026-10-07',
                     'Driver contracts and generated MCP — 2026-10-28'):
            self.assertIn(kept, headings)
        new = [(heading, ' '.join(' '.join(lines).split())) for heading, lines in design if '2026-11-19' in heading]
        self.assertEqual(len(new), 1, 'The design has one section dated 2026-11-19')
        heading, section = new[0]
        # What a package holds, how it runs and how it is served.
        for told in ('`interface-package/3`', '`README.md`', '`manifest.json`', '`mcp.json`', '`drivers/<driver>.json`',
                     '`evidence/<driver>.probe.json`', 'no code', 'installed runtime', '`generative-driver serve'):
            self.assertIn(told, section)
        # The section of 2026-10-28 says at its head that the new section replaces its packaging.
        anchor = '#' + re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        head = paragraphs(dict(design)['Driver contracts and generated MCP — 2026-10-28'])[0]
        self.assertIn('](' + anchor + ')', head)
        self.assertIn('replaces', head)
        self.assertIn('packaging', head)
        # Outside the dated sections the design describes no register model.
        for heading, lines in design:
            if not DATED.search(heading):
                with self.subTest(section=heading or 'the head of the document'):
                    self.assertNotIn('register', ' '.join(lines).lower())

    def test_03_B16_guides_the_decision_records_say_what_adr_0010_replaced(self):
        self.assertNotIn('Not built yet', text_of('docs/adr/0010-a-package-is-data-in-one-format.md'))
        for record in ('docs/adr/0002-driver-contract-and-generated-mcp.md',
                       'docs/adr/0008-run-the-installed-runtime-over-package-data.md'):
            with self.subTest(record=record):
                parts = paragraphs(text_of(record).splitlines())
                status = next(number for number, part in enumerate(parts) if part.startswith('Status:'))
                under = parts[status + 1]
                self.assertIn('](0010-a-package-is-data-in-one-format.md)', under)
                self.assertIn('replaced', under)

    def test_03_B16_guides_show_the_install_with_uv_first(self):
        for name in ('README.md', 'docs/setup.md'):
            with self.subTest(guide=name):
                lines = [line for _, block in code_blocks(text_of(name)) for line in block]
                installs = [line for line in lines if 'uv sync' in line or 'pip install' in line]
                self.assertTrue(installs, 'The guide shows an install')
                self.assertIn('uv sync', installs[0])


class WorkerDocumentTests(unittest.TestCase):
    """What a worker document shows can be used as it stands (03-B16)."""

    def test_03_B16_guides_the_handler_fragment_validates_when_its_operation_is_put_into_a_model(self):
        from generative_driver.toolkit import resources_root
        from interface_runtime.engine import validate_model
        guide = (resources_root() / 'interpret_tasks' / 'HANDLER.md').read_text(encoding='utf-8')
        [shown] = [lines for _, lines in code_blocks(guide) if '"interpret-fragment/1"' in ''.join(lines)]
        fragment = json.loads('\n'.join(shown))
        model = model_of(identify=IDENTIFY)
        model['operations'][fragment['operation']] = fragment['model']
        model['provenance'] += fragment['provenance']
        report = validate_model(model)
        self.assertEqual((report['ok'], report['defects']), (True, []))


class SessionLeftOpenTests(unittest.TestCase):
    """The setup guide says how an operator frees a session that a killed `serve` left open (03-B16)."""

    def test_03_B16_guides_setup_frees_a_session_a_killed_serve_left_open_and_reads_a_saved_result(self):
        from generative_driver.sdk import ManagedDriver
        told = [lines for _, lines in sections(text_of('docs/setup.md')) if 'ManagedDriver' in '\n'.join(lines)]
        self.assertEqual(len(told), 1, 'One section of the setup guide frees a session through `ManagedDriver`')
        lines = told[0]
        section = ' '.join(' '.join(lines).split())
        # Where the session's id is found, and that the command is the one that left the session.
        self.assertIn('generative-driver serve PACKAGE --binding', section)
        self.assertIn('standard error', section)
        [shown] = [block for _, block in code_blocks('\n'.join(lines)) if 'ManagedDriver' in '\n'.join(block)]
        tree = ast.parse('\n'.join(shown))
        # The code names the session through the SDK, by the package and the session's id.
        [(session, made)] = [(node.targets[0].id, node.value) for node in ast.walk(tree)
                             if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                             and isinstance(node.value.func, ast.Name) and node.value.func.id == 'ManagedDriver']
        inspect.signature(ManagedDriver).bind(*made.args, **{keyword.arg: keyword.value for keyword in made.keywords})
        self.assertEqual(len(made.args), 2)
        called = {node.func.attr: node for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name) and node.func.value.id == session}
        # `status` with the id of a call reads its saved result, and `close` frees the binding.
        self.assertLessEqual({'status', 'close'}, set(called))
        for name, call in called.items():
            with self.subTest(method=name):
                inspect.signature(getattr(ManagedDriver, name)).bind(None, *call.args, **{
                    keyword.arg: keyword.value for keyword in call.keywords})
        self.assertEqual(len(called['status'].args) + len(called['status'].keywords), 1)
        self.assertEqual(len(called['close'].args) + len(called['close'].keywords), 0)


if __name__ == '__main__':
    unittest.main()

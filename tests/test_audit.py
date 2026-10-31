"""The transcript audit: a worker command that touches a forbidden path is found in a run's Codex transcripts."""
import json
import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark_support.audit import audit_commands


def command(text, kind='item.completed'):
    """The transcript line of a command the worker ran: `kind` says whether it started or completed."""
    return {'type': kind, 'item': {'type': 'command_execution', 'command': text}}


class AuditTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name) / 'run'

    def write(self, path, events, extra_line=None):
        """A transcript of `events`, one JSON line each, after a line that is not JSON when `extra_line` is given."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ([] if extra_line is None else [extra_line]) + [json.dumps(event) for event in events]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def test_flags_a_command_reading_the_vault(self):
        run = self.run_dir
        self.write(run / 'work/probe/a1/_agent_events.jsonl', [
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'cat "/Volumes/Answer Vault/evaluator/x.password"'}},
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'ls workspace'}}], extra_line='not json')
        self.assertEqual([f['transcript'] for f in audit_commands(run, ('/Volumes/Answer Vault',))], ['work/probe/a1/_agent_events.jsonl'])

    def test_windows_paths_and_case_still_match(self):
        run = self.run_dir
        self.write(run / 'work/ground/a2/_agent_events.jsonl', [
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': ['type', 'C:\\Vault\\GD\\key.txt']}}])
        self.assertEqual(len(audit_commands(run, ('c:/vault/gd',))), 1)

    def test_a_path_is_normalized_like_the_command(self):
        # Catches a path compared as it was given: one written with backslashes or capitals must match a command that
        # spells it with slashes or lower case, as an operator on Windows may write either.
        run = self.run_dir
        self.write(run / 'work/ground/a2/_agent_events.jsonl', [command('type C:/Vault/GD/key.txt'), command('type c:\\vault\\gd\\key.txt')])
        self.assertEqual(len(audit_commands(run, ('C:\\Vault\\GD',))), 2)

    def test_a_command_naming_any_one_of_the_paths_is_flagged(self):
        # Catches an audit that needs a command to name every forbidden path, or only the last one it is given.
        run = self.run_dir
        self.write(run / 'work/probe/a1/_agent_events.jsonl', [
            command('cat /vault/key'), command('cat /generator/seed'), command('ls workspace')])
        self.assertEqual([found['command'] for found in audit_commands(run, ('/vault', '/generator'))],
                         ['cat /vault/key', 'cat /generator/seed'])

    def test_records_that_are_not_commands_are_skipped_not_fatal(self):
        # Catches an audit that stops on a line that is not an event or on an event that is not a command, so that
        # the flagged command after it goes unseen.
        run = self.run_dir
        path = run / 'work/probe/a1/_agent_events.jsonl'
        odd = ['[1, 2]', '"text"', 'null', '7', '{', json.dumps({'type': 'item.completed'}),
               json.dumps({'type': 'item.completed', 'item': 'cat /vault/key'}),
               json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution'}}),
               json.dumps(command({'cat': '/vault/key'})), json.dumps(command(None)),
               json.dumps({'type': 'unparsed_stdout', 'text': 'cat /vault/key'})]
        path.parent.mkdir(parents=True)
        # A line that is not valid UTF-8 is as unreadable as a line that is not JSON.
        path.write_bytes(b'\xff\xfe cat /vault/key\n' + '\n'.join(odd + [json.dumps(command('cat /vault/key'))]).encode('utf-8') + b'\n')
        self.assertEqual(audit_commands(run, ('/vault',)),
                         [{'transcript': 'work/probe/a1/_agent_events.jsonl', 'command': 'cat /vault/key'}])

    def test_an_empty_path_is_refused_rather_than_matching_every_command(self):
        # Catches an audit path that is empty (an unset shell variable, say) flagging every command of every run: no
        # path is no audit, and an empty one is an error.
        run = self.run_dir
        self.write(run / 'work/probe/a1/_agent_events.jsonl', [command('ls workspace')])
        for forbidden in (('',), ('/vault', ' '), (None,)):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                audit_commands(run, forbidden)
        self.assertEqual(audit_commands(run, ()), [])

    def test_a_command_is_one_finding_and_talk_about_a_path_is_none(self):
        # Catches a command reported twice (a transcript holds it when it starts and when it completes), a message,
        # file change or other item that only names a forbidden path reported as a command, and a command that names
        # another path.
        run = self.run_dir
        self.write(run / 'work/probe/a1/_agent_events.jsonl', [
            command('cat /vault/key', 'item.started'), command('cat /vault/key'),
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'I will not read /vault/key'}},
            {'type': 'item.completed', 'item': {'type': 'file_change', 'changes': [{'path': '/vault/key', 'kind': 'update'}]}},
            {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'command': 'cat /vault/key'}},
            command('cat /elsewhere/key')])
        self.assertEqual([found['command'] for found in audit_commands(run, ('/vault',))], ['cat /vault/key'])

    def test_a_finding_keeps_the_first_300_characters_of_the_command_as_the_worker_wrote_it(self):
        # Catches a finding that carries the command as it was compared (case folded, backslashes turned into slashes)
        # or all of a long command.
        run = self.run_dir
        words = ['type', 'C:\\Vault\\GD\\' + 'Key' * 200]
        self.write(run / 'work/ground/a2/_agent_events.jsonl', [command(words)])
        self.assertEqual(audit_commands(run, ('c:/vault/gd',)),
                         [{'transcript': 'work/ground/a2/_agent_events.jsonl', 'command': ' '.join(words)[:300]}])

    def test_every_transcript_of_the_run_is_scanned_in_order_and_named_by_its_path_in_the_run(self):
        # Catches an audit that reads the stage workspaces alone (the interpret attempts that preparation and the
        # benchmark's tools ran have transcripts too), findings that come in a filesystem's order, a path that is not
        # relative to the run, and a file that is not a transcript read as one.
        run = self.run_dir
        places = ['work/probe/a1', 'work/acquire/a0', 'preparation/a2/interpret/attempt1', 'benchmark/tools/interpret/attempt1']
        for place in places:
            self.write(run / place / '_agent_events.jsonl', [command('cat /vault/one'), command('ls'), command('cat /vault/two')])
        self.write(run / 'work/probe/a1/events.jsonl', [command('cat /vault/other')])
        self.write(run / 'work/ground/a3/_agent_events.jsonl', [command('ls')])
        (run / 'work/emit/a4').mkdir(parents=True)
        found = audit_commands(run, ('/vault',))
        expected = [(place + '/_agent_events.jsonl', text) for place in sorted(places) for text in ('cat /vault/one', 'cat /vault/two')]
        self.assertEqual([(f['transcript'], f['command']) for f in found], expected)

    def test_only_a_file_is_read_as_a_transcript(self):
        # Catches an audit that stops, or waits for a reader, on something a worker left under its workspace under a
        # transcript's name: a directory here, a named pipe elsewhere.
        run = self.run_dir
        (run / 'work/probe/a1/notes/_agent_events.jsonl').mkdir(parents=True)
        self.write(run / 'work/probe/a1/_agent_events.jsonl', [command('cat /vault/key')])
        self.assertEqual([found['transcript'] for found in audit_commands(run, ('/vault',))], ['work/probe/a1/_agent_events.jsonl'])


if __name__ == '__main__':
    unittest.main()

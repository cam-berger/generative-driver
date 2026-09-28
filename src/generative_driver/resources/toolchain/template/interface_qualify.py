"""Relocatable offline qualifier: python3 qualify.py <model_dir> <replies.json>.

Drives the sealed runtime beside this file over the supplied replies, complete and fragmented,
under LF and CRLF framing (or one BINARY framing for frame replies, or whole transfers under one
USB framing for a usb channel) and synthetic faults, with the standard library only. Writes
<model_dir>/qualification/result.json, with the SHA-256 of the model and replies it read, and exits 0
only when every operation that has replies qualifies. Replies are the caller's assumptions; a passing result
proves framing and guard robustness against them, not device behaviour.
"""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent/'_toolkit'))
from interface_runtime.evidence import read_json_hashed
from interface_runtime.qualify import QualifyError, qualify, summary

if len(sys.argv) != 3:
    print('usage: python3 qualify.py <model_dir> <replies.json>')
    raise SystemExit(2)
model_dir = Path(sys.argv[1]).resolve()
replies_path = Path(sys.argv[2]).resolve()
hashes = {}
try:
    model, hashes['model_sha256'] = read_json_hashed(model_dir/'model.json', 1048576)
    replies, hashes['replies_sha256'] = read_json_hashed(replies_path, 4194304)
    report = qualify(model, replies)
except (OSError, ValueError, TypeError, KeyError, QualifyError) as exc:
    report = {'schema': 'interface-qualification/1', 'ok': False, 'error': str(exc),
              'all_supplied_qualify': False, 'operations': {}}
report = {**report, **hashes}
output = model_dir/'qualification'
output.mkdir(exist_ok=True)
(output/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding="utf-8")
print(summary(report) if report.get('operations') else 'qualification error: ' + str(report.get('error')))
raise SystemExit(0 if report.get('all_supplied_qualify') else 1)

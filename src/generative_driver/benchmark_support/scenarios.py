"""Pure maintenance verdicts and evaluator-owned scenario effect journal."""
import json
import os
from pathlib import Path
import tempfile
import hashlib
import re


def _require_sha256(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('Expected a lowercase SHA-256 digest')


def maintain_objective(scenario: str) -> str:
    if scenario not in ('control', 'semantic', 'identity'):
        raise ValueError('Unknown maintenance scenario')
    return ('Check the supplied package against the current device using assigned tools. '
            'Write maintenance.json with schema benchmark-maintenance-claim/1, claim unchanged, drift, or unknown, '
            'and evidence_ids naming this assignment\'s observed tool events. Include its actual SHA-256 '
            'in the stage report artifacts. Report completed for unchanged or unknown, or needs_revision for drift. '
            'Do not repair the package in this stage.')


def read_maintenance_claim(workspace: Path, report: dict, assignment_id: str, events: list[dict]) -> dict:
    """Read a claim only when its bytes and cited event IDs belong to this assignment."""
    workspace = Path(workspace).resolve()
    target = workspace / 'maintenance.json'
    if target.is_symlink() or not target.is_file():
        raise ValueError('maintenance.json missing from assigned workspace')
    claimed = [row for row in report.get('artifacts', [])
               if (workspace / row.get('path', '')).resolve() == target]
    actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    if len(claimed) != 1 or claimed[0].get('sha256') != actual_hash:
        raise ValueError('maintenance.json report artifact hash mismatch')
    value = json.loads(target.read_text(encoding='utf-8'))
    if value.get('schema') != 'benchmark-maintenance-claim/1' or value.get('claim') not in ('unchanged', 'drift', 'unknown'):
        raise ValueError('Invalid maintenance claim schema or value')
    expected_status = 'needs_revision' if value['claim'] == 'drift' else 'completed'
    if report.get('status') != expected_status:
        raise ValueError('Maintenance report status disagrees with claim')
    ids = value.get('evidence_ids')
    current_ids = {str(event['id']) for event in events if event.get('assignment_id') == assignment_id}
    if (not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item or item not in current_ids for item in ids)):
        raise ValueError('Maintenance evidence IDs must name current assignment events')
    return value


def maintenance_decision(*, scenario: str, claim: str, diagnostic: dict, repaired: bool) -> dict:
    """Combine a worker claim with separately obtained evaluator observations."""
    fault = diagnostic.get('fault')
    if not diagnostic.get('evaluable') or fault in ('host', 'operator'):
        return {'ok': False, 'fault': fault or 'host', 'route': None,
                'reason': 'Independent maintenance observation is unavailable',
                'maintenance': {'false_alarm': False}}
    evidence = diagnostic.get('evidence_ids')
    if (claim not in ('unchanged', 'drift') or not isinstance(evidence, list) or
            not evidence or any(not isinstance(item, str) or not item for item in evidence)):
        return {'ok': False, 'fault': None, 'route': None,
                'reason': 'Maintenance claim lacks evidence', 'maintenance': {'false_alarm': False}}
    if repaired:
        ok = claim == 'unchanged' and not diagnostic.get('contradiction') and bool(diagnostic.get('requalified')) and bool(diagnostic.get('fresh_reuse_passed'))
        return {'ok': ok, 'fault': None if ok else 'model', 'route': None,
                'reason': None if ok else 'Repair needs requalification and fresh reuse',
                'maintenance': {'false_alarm': False}}
    if scenario == 'control':
        ok = claim == 'unchanged' and not diagnostic.get('contradiction') and bool(diagnostic.get('fresh_reuse_passed'))
        return {'ok': ok, 'fault': None if ok else 'model', 'route': None,
                'reason': None if ok else 'Control maintenance claim failed',
                'maintenance': {'false_alarm': claim == 'drift' and not diagnostic.get('contradiction')}}
    if scenario not in ('semantic', 'identity'):
        raise ValueError('Unknown maintenance scenario')
    ok = claim == 'drift' and bool(diagnostic.get('contradiction'))
    return {'ok': ok, 'fault': None if ok else 'model', 'route': 'interpret' if ok else None,
            'reason': 'Evidenced semantic drift' if ok else 'Drift claim lacks independent contradiction',
            'maintenance': {'false_alarm': claim == 'drift' and not diagnostic.get('contradiction')}}


def next_action_state(current, nonce, action_sha256):
    _require_sha256(action_sha256)
    if current.get('status') == 'applying':
        raise ValueError('Scenario effect requires reconciliation')
    if current.get('status') in ('applied', 'observed'):
        if (current['nonce'], current['action_sha256']) != (nonce, action_sha256):
            raise ValueError('Scenario action identity changed')
        return current
    return {'status': 'applying', 'nonce': nonce, 'action_sha256': action_sha256}


def maintenance_history(scenario, entries, *, requalified, fresh, transition):
    """One acceptance rule for live attempts and authenticated retained history."""
    initial = entries.get('initial', {})
    attempts = entries.get('attempts', [dict(e, phase=phase) for phase, e in
        (('initial', initial), ('repaired', entries.get('repaired'))) if e])
    decisions = [maintenance_decision(scenario=scenario, claim=e.get('claim'),
        diagnostic=e.get('diagnostic', {}), repaired=e.get('phase') == 'repaired') for e in attempts]
    false_alarm = any(d['maintenance']['false_alarm'] for d in decisions)
    model_failure = any(d.get('fault') == 'model' and not d['ok'] for d in decisions)
    observed = [e for e in attempts if e.get('diagnostic', {}).get('evaluable') is True
                and e['diagnostic'].get('fault') not in ('host', 'operator')]
    detection = initial or (observed[-1] if observed else attempts[-1] if attempts else {})
    diagnostic = detection.get('diagnostic', {})
    first = maintenance_decision(scenario=scenario, claim=initial.get('claim'),
        diagnostic=initial.get('diagnostic', {}), repaired=False)
    needs_repair = scenario != 'control'
    last = entries.get('repaired', {}) if needs_repair else initial
    second = maintenance_decision(scenario=scenario, claim=last.get('claim'),
        diagnostic={**last.get('diagnostic', {}), 'fresh_reuse_passed': fresh, 'requalified': requalified},
        repaired=needs_repair)
    completed = bool(needs_repair and transition and first['ok'] and second['ok'])
    fields = {'evaluable': bool(observed), 'drift_claimed': detection.get('claim') == 'drift',
        'drift_observed': diagnostic.get('evaluable') is True and diagnostic.get('contradiction') is True,
        'false_alarm': false_alarm, 'repair_completed': completed,
        'requalified': bool(needs_repair and transition and requalified), 'fresh_reuse_passed': bool(fresh)}
    ok = bool(first['ok'] and second['ok'] and not model_failure and (transition if needs_repair else not entries.get('repaired')))
    return fields, ok


def maintenance_summary_ok(value):
    return (value.get('false_alarm') is False and type(value.get('drift_claimed')) is bool
        and value.get('drift_claimed') == value.get('drift_observed') and value.get('fresh_reuse_passed') is True
        and (value.get('repair_completed') is True and value.get('requalified') is True
             if value['drift_observed'] else value.get('repair_completed') is False and value.get('requalified') is False))


class ScenarioJournal:
    """One action per evaluator run; applying state is durable before external I/O."""
    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / 'scenario-journal.json'

    def state(self) -> dict:
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.scenario-', suffix='.json', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return state

    def begin(self, nonce: str, action_sha256: str) -> dict:
        current = self.state()
        state = next_action_state(current, nonce, action_sha256)
        return current if state is current else self._save(state)

    def applied(self, nonce: str, observation: dict) -> dict:
        """Commit a Task 7 producer's explicit success wrapper, not raw device fields.

        The producer must establish success and bind the native response to this
        stimulus before constructing the wrapper.
        """
        current = self.state()
        if current.get('status') != 'applying' or current.get('nonce') != nonce:
            raise ValueError('No matching applying scenario action')
        native = observation.get('native_acknowledgement') if isinstance(observation, dict) else None
        if (not isinstance(observation, dict) or observation.get('success') is not True or
                observation.get('action_sha256') != current['action_sha256'] or
                not isinstance(native, dict) or
                not isinstance(native.get('response'), str) or not native['response'].strip()):
            raise ValueError('Scenario action requires a verifiable acknowledgement')
        return self._save({**current, 'status': 'applied', 'observation': observation})

    def observed(self, nonce: str, evidence_sha256: str) -> dict:
        _require_sha256(evidence_sha256)
        current = self.state()
        if current.get('nonce') != nonce or current.get('status') not in ('applied', 'observed'):
            raise ValueError('No matching applied scenario action')
        if current['status'] == 'observed':
            if current['evidence_sha256'] != evidence_sha256:
                raise ValueError('Scenario independent evidence changed')
            return current
        return self._save({**current, 'status': 'observed', 'evidence_sha256': evidence_sha256})

"""Sealed evaluator records and explicitly allowlisted public summaries."""
from pathlib import Path


COUNTS = ('verdict', 'passed', 'total')
USAGE = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens',
         'total_tokens', 'observed_total_tokens', 'reported_attempts', 'attempts', 'coverage', 'counting')
PIN = ('case_id', 'case_version', 'manifest_sha256', 'scenario_id', 'scenario_sha256',
       'case_seed', 'truth_sha256', 'evaluator_version', 'family', 'variant', 'execution', 'evidence_track', 'scope')


def _scalars(value, fields):
    return {key: value[key] for key in fields if isinstance(value, dict) and key in value
            and (value[key] is None or type(value[key]) in (str, int, float, bool))}


def public_pin(value):
    result = _scalars(value, PIN)
    from .snapshots import canonical_digest
    from .behavior import _sha
    saved = value.get('pin_sha256')
    result['pin_sha256'] = saved if _sha(saved) else canonical_digest(value)
    result['image_hashes'] = _hashes(value.get('image_hashes', {}))
    return result


def _hashes(value):
    return {key: item for key, item in value.items() if isinstance(key, str)
            and isinstance(item, str) and len(item) == 64 and all(c in '0123456789abcdef' for c in item)} if isinstance(value, dict) else {}


def _evaluation(value):
    return _scalars(value, ('phase', 'revision', 'frozen_artifact_sha256', *COUNTS))


def _executed(value):
    result = _scalars(value, ('toolchain_revision', 'skills_revision', 'evaluator_revision'))
    result['environment'] = _scalars(value.get('environment', {}), ('system', 'release', 'architecture', 'python'))
    result['runtime_configuration'] = _scalars(value.get('runtime_configuration', {}),
        ('runtime', 'model', 'provider', 'version', 'reasoning_effort', 'max_turns'))
    return result


def public_v2_report(payload: dict) -> dict:
    """Unknown fields, including inside known containers, never cross this boundary."""
    result = _scalars(payload, ('run_id', 'case', 'case_version', 'evaluator_version', 'scenario_id',
        'case_seed', 'execution', 'model_benchmark', 'workflow_status', 'verdict', 'snapshot_sha256',
        'toolchain_revision', 'skills_revision', 'evaluator_revision', 'elapsed_seconds', 'worker_seconds',
        'tool_seconds', 'tool_calls', 'worker_tool_seconds', 'evaluator_tool_seconds',
        'additional_attempts', 'unfinished_tool_calls', 'human_inputs', 'original_budget_seconds', 'effective_budget_seconds'))
    result['schema'] = 'benchmark-report/2'
    result['case_pin'] = public_pin(payload.get('case_pin', {}))
    result['final_evaluation'] = _scalars(payload.get('final_evaluation', {}), (*COUNTS, 'evidence_sha256'))
    result['evaluations'] = [_evaluation(row) for row in payload.get('evaluations', [])]
    result['accepted_gates'] = [_scalars(row, ('stage', 'phase', 'revision', 'assignment_id', 'artifact_sha256', *COUNTS))
                                for row in payload.get('accepted_gates', [])]
    result['evaluator_verdicts'] = [_scalars(row, ('stage', 'assignment_id', 'revision', 'fault', 'final_evaluation', *COUNTS))
                                    for row in payload.get('evaluator_verdicts', [])]
    result['progress'] = _scalars(payload.get('progress', {}), ('revision', 'repairs', 'terminal_final_failure'))
    result['attempt_limits'] = _scalars(payload.get('attempt_limits', {}), ('max_model_repairs',))
    result['usage'] = _scalars(payload.get('usage', {}), USAGE)
    result['executed'] = _executed(payload.get('executed', {}))
    result['stages'] = {}
    from ..benchmark import STAGES
    for stage in STAGES:
        if stage not in payload.get('stages', {}):
            continue
        value = payload['stages'][stage]
        row = _scalars(value, ('attempt_count', 'workflow_status', 'evaluator_status', 'worker_seconds',
                              'tool_seconds', 'tool_calls', 'worker_tool_seconds', 'evaluator_tool_seconds'))
        row['usage'] = _scalars(value.get('usage', {}), USAGE)
        row['attempts'] = [{**_scalars(attempt, ('assignment_id', 'status', 'worker_status', 'accepted',
                           'elapsed_seconds', 'revision')),
                           'usage': _scalars(attempt.get('usage', {}), USAGE)} for attempt in value.get('attempts', [])]
        result['stages'][stage] = row
    result['interventions'] = [_scalars(row, ('event_id', 'kind', 'time', 'effect_resolution',
        'scoped_tool_approval', 'old_budget_seconds', 'new_budget_seconds', 'extension_seconds',
        'old_deadline', 'new_deadline', 'stage', 'uncertain_effect', 'has_observation'))
        for row in payload.get('interventions', [])]
    return result


def _validate_payload(payload):
    from .snapshots import canonical_digest
    from .behavior import validate_records, _sha
    if payload.get('schema') != 'benchmark-run-evidence/1':
        raise ValueError('Unsupported run evidence schema')
    snapshot = payload['execution_snapshot']
    if (snapshot.get('schema') != 'benchmark-execution-snapshot/1' or
            canonical_digest({k: v for k, v in snapshot.items() if k != 'snapshot_sha256'}) != snapshot.get('snapshot_sha256') or
            snapshot['case_pin'] != payload['case_pin']):
        raise ValueError('Execution snapshot identity mismatch')
    if payload['case_pin']['manifest'].get('schema') != 'benchmark-case/2':
        raise ValueError('Run evidence requires a v2 case')
    from .registry import validate_pinned_workflow
    validate_pinned_workflow(payload['case_pin'])
    seen = set()
    for evaluation in payload['evaluations']:
        key = (evaluation['phase'], evaluation['revision'])
        digest = evaluation['frozen_artifact_sha256']
        if (key in seen or key[0] not in ('diagnostic', 'final') or type(key[1]) is not int or key[1] < 0 or
                not _sha(digest) or evaluation['contract']['artifact_sha256'] != digest):
            raise ValueError('Evaluation artifact or revision identity mismatch')
        seen.add(key)
        if any(check.get('revision') != key[1] for check in evaluation['contract']['checks']):
            raise ValueError('Contract revision mismatch')
        for record in evaluation['records']:
            if record.get('artifact_sha256') != digest or record.get('revision') != key[1]:
                raise ValueError('Record artifact or revision mismatch')
        validate_records(evaluation['contract'], evaluation['records'])
        if key[0] == 'final':
            emit = [row for row in payload.get('accepted_artifacts', [])
                    if row.get('stage') == 'emit' and row.get('revision') == key[1]]
            if emit and not any(row.get('sha256') == digest for row in emit):
                raise ValueError('Frozen final artifact differs from accepted emit package')


def seal_run_evidence(payload: dict, path: Path, password_file: Path) -> dict:
    from .truth import seal
    _validate_payload(payload)
    from ..configurator import digest as artifact_digest
    for artifact in payload.get('accepted_artifacts', []):
        if 'path' in artifact and artifact_digest(Path(artifact['path'])) != artifact['sha256']:
            raise ValueError('Accepted artifact bytes changed before sealing')
    digest = seal(payload, path, Path(password_file).read_text(encoding='utf-8').strip())
    return {'path': Path(path).name, 'sha256': digest}


def _gate_results(payload, evaluations):
    """Sealed evaluator checks establish structural gates; referenced behavior is regraded."""
    from ..benchmark import STAGES
    from .behavior import _sha
    artifacts = payload.get('accepted_artifacts', [])
    identities = {(row.get('stage'), row.get('revision'), row.get('assignment_id'), row.get('sha256'))
                  for row in artifacts}
    final = {row['revision']: row for row in evaluations if row['phase'] == 'final'}
    rows, seen = [], set()
    for gate in payload['accepted_gates']:
        key = (gate.get('stage'), gate.get('revision'))
        identity = (*key, gate.get('assignment_id'), gate.get('artifact_sha256'))
        checks = gate.get('checks', [])
        refs = gate.get('evaluation_refs', [])
        ok = (key not in seen and key[0] in STAGES and type(key[1]) is int and key[1] >= 0
              and isinstance(identity[2], str) and bool(identity[2]) and _sha(identity[3])
              and identity in identities and bool(checks)
              and len({row.get('id') for row in checks}) == len(checks)
              and all(isinstance(row.get('id'), str) and row['id'] and row.get('passed') is True for row in checks))
        seen.add(key)
        for ref in refs:
            matches = [row for row in evaluations if (row['phase'], row['revision']) == (ref.get('phase'), ref.get('revision'))]
            ok = ok and ref.get('revision') == key[1] and len(matches) == 1 and matches[0]['verdict'] == 'passed'
        if key[0] in ('emit', 'reuse') and key[1] in final:
            ok = ok and identity[3] == final[key[1]]['frozen_artifact_sha256']
        if key[0] == 'reuse':
            ok = (ok and {'phase': 'final', 'revision': key[1]} in refs
                  and {'fresh_worker_mission', 'frozen_final_behavior'} <= {c.get('id') for c in checks})
        rows.append({**_scalars(gate, ('stage', 'revision', 'assignment_id', 'artifact_sha256')),
                     'verdict': 'passed' if ok else 'failed', 'passed': int(bool(ok)), 'total': 1})
    required = payload['case_pin']['manifest'].get('required_stages', [])
    inventory_ok = required == list(STAGES) and len(final) == 1
    for revision in final:
        for stage in required:
            target = 0 if stage == 'acquire' else revision
            matches = [row for row in rows if (row['stage'], row['revision']) == (stage, target)]
            inventory_ok = inventory_ok and len(matches) == 1 and matches[0]['verdict'] == 'passed'
    return rows, bool(inventory_ok and all(row['verdict'] == 'passed' for row in rows))


def regrade_v2(report: dict, evidence_path: Path, password_file: Path) -> dict:
    from .truth import unlock
    from .behavior import validate_records
    from ..reporting import _tree_hash
    if not evidence_path or not password_file:
        raise ValueError('V2 scoring requires evidence and password-file paths')
    payload = unlock(evidence_path, Path(password_file).read_text(encoding='utf-8').strip(),
                     report['final_evaluation']['evidence_sha256'])
    _validate_payload(payload)
    snapshot = payload['execution_snapshot']
    if snapshot['executed']['evaluator_revision'] != _tree_hash(Path(__file__).parent):
        raise ValueError('Installed evaluator implementation hash mismatch')
    if (report.get('schema') != 'benchmark-report/2' or report.get('case_pin') != public_pin(payload['case_pin']) or
            report.get('snapshot_sha256') != snapshot['snapshot_sha256'] or
            report.get('executed') != _executed(snapshot['executed'])):
        raise ValueError('Report and sealed execution identity mismatch')
    expected_aliases = {key: payload['case_pin'].get(key) for key in
                        ('case_version', 'evaluator_version', 'scenario_id', 'case_seed')}
    execution = payload['case_pin'].get('execution', 'unclassified')
    expected_aliases.update(case=payload['case_pin']['case_id'], execution=execution,
        model_benchmark=execution in ('actual-agent-emulation', 'actual-agent-physical'),
        **{key: snapshot['executed'][key] for key in
           ('evaluator_revision', 'toolchain_revision', 'skills_revision')})
    if any(key in report and report[key] != value for key, value in expected_aliases.items()):
        raise ValueError('Conflicting public execution identity')
    identity_fields = ('phase', 'revision', 'frozen_artifact_sha256')
    if ([_scalars(row, identity_fields) for row in report.get('evaluations', [])] !=
            [_scalars(row, identity_fields) for row in payload['evaluations']]):
        raise ValueError('Report evaluation artifact identity mismatch')
    evaluations = [{**_scalars(row, identity_fields), **_scalars(validate_records(row['contract'], row['records']), COUNTS)}
                   for row in payload['evaluations']]
    gates, gates_ok = _gate_results(payload, evaluations)
    finals = [row for row in evaluations if row['phase'] == 'final']
    passed, total = sum(row['passed'] for row in finals), sum(row['total'] for row in finals)
    ok = bool(finals) and all(row['verdict'] == 'passed' for row in finals) and gates_ok
    return public_v2_report({**report, 'evaluations': evaluations, 'accepted_gates': gates,
        'verdict': 'passed' if ok else 'failed',
        'final_evaluation': {'verdict': 'passed' if finals and passed == total else 'failed',
            'passed': passed, 'total': total, 'evidence_sha256': report['final_evaluation']['evidence_sha256']}})


def frozen_final_decision(contract: dict, records: list[dict]) -> dict:
    """Terminal evaluator result; private check IDs and values never become feedback."""
    from .behavior import validate_records
    grade = validate_records(contract, records)
    ok = grade['verdict'] == 'passed'
    summary = _scalars(grade, COUNTS)
    return {'ok': ok, 'fault': None if ok else 'model', 'final_evaluation': True,
            'reason': None if ok else 'Frozen final evaluation failed',
            'evaluator': {**summary, 'fault': None if ok else 'model', 'final_evaluation': True}}

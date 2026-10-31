"""Source-traceable corpus selection, including cases that cannot yet run.

A catalog is intake data, never an executable adapter or calibration authority.
Only registered, qualified cases can enter the existing six-stage suite runner.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

AREAS = ('robotic-control', 'iot', 'sensors', 'usb-peripherals', 'instruments',
         'controllers', 'industrial-controllers')
STRATA = ('units-measurement', 'fresh-streamed-acquisition', 'bounded-actuation',
          'transactions-persistence', 'framing-bulk-transfer', 'sessions-errors-reset')
STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
BASES = ('observable-behavior', 'compatibility', 'safety-integrity')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('Incomplete or unknown ' + label + ' fields')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing ' + label)


def _strings(value, label):
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError('Invalid ' + label)
    if len(value) != len(set(value)):
        raise ValueError('Duplicate ' + label)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate catalog key: ' + key)
        value[key] = item
    return value


def load_catalog(path=None):
    from ..benchmark import case_root
    path = Path(path) if path else case_root() / 'corpora/seven-area-pilot.json'
    return validate_catalog(json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique))


def validate_catalog(catalog):
    _object(catalog, 'schema id version development_cases lineages', 'corpus')
    if catalog['schema'] != 'benchmark-corpus/1':
        raise ValueError('Unsupported corpus schema')
    for name in ('id', 'version'):
        _text(catalog[name], name)
    _strings(catalog['development_cases'], 'development cases')
    if not isinstance(catalog['lineages'], list) or not catalog['lineages']:
        raise ValueError('Corpus requires upstream lineages')
    identities, origins, missions = set(), {}, set()
    for row in catalog['lineages']:
        _object(row, 'id area origin_group split source architecture system protocols strata mission case blockers limitations',
                'lineage')
        for key in ('id', 'origin_group', 'architecture', 'system'):
            _text(row[key], key)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', row['id']) or row['id'] in identities:
            raise ValueError('Invalid or duplicate lineage ID')
        identities.add(row['id'])
        if row['area'] not in AREAS or row['split'] not in ('development', 'validation', 'test'):
            raise ValueError('Unknown area or split')
        prior = origins.setdefault(row['origin_group'], row['split'])
        if prior != row['split']:
            raise ValueError('Related lineages cannot cross dataset splits')
        _strings(row['protocols'], 'protocols')
        _strings(row['strata'], 'behavior strata')
        if not set(row['strata']) <= set(STRATA):
            raise ValueError('Unknown behavior stratum')
        _strings(row['limitations'], 'limitations')
        source = row['source']
        _object(source, 'repository commit path license evidence', 'source provenance')
        if not isinstance(source['repository'], str) or not re.fullmatch(
                r'https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', source['repository']):
            raise ValueError('Source requires an upstream repository URL')
        if not isinstance(source['commit'], str) or not re.fullmatch('[0-9a-f]{40}', source['commit']):
            raise ValueError('Source requires an immutable commit, not a branch or tag')
        for name in ('path', 'license'):
            _text(source[name], 'source ' + name)
        _strings(source['evidence'], 'source evidence')
        mission = row['mission']
        _object(mission, 'id objective requirements', 'mission')
        _text(mission['id'], 'mission ID')
        _text(mission['objective'], 'mission objective')
        identity = (row['id'], mission['id'])
        if identity in missions:
            raise ValueError('Duplicate mission identity')
        missions.add(identity)
        requirements = mission['requirements']
        if not isinstance(requirements, list) or not requirements:
            raise ValueError('Mission requires observable acceptance requirements')
        names = set()
        for requirement in requirements:
            _object(requirement, 'id basis behavior evidence', 'acceptance requirement')
            for key in ('id', 'behavior', 'evidence'):
                _text(requirement[key], 'requirement ' + key)
            if requirement['basis'] not in BASES or requirement['id'] in names:
                raise ValueError('Invalid acceptance basis or duplicate requirement')
            names.add(requirement['id'])
        if row['case'] is not None:
            _text(row['case'], 'registered case')
            if row['case'] in catalog['development_cases']:
                raise ValueError('Synthetic development cases cannot substitute for upstream lineages')
        blockers = row['blockers']
        if not isinstance(blockers, list):
            raise ValueError('Blockers must be a list')
        for blocker in blockers:
            _object(blocker, 'kind reason evidence', 'blocker')
            if blocker['kind'] not in ('source', 'license', 'build', 'runtime', 'backend', 'oracle', 'qualification'):
                raise ValueError('Unknown admission blocker')
            for key in ('reason', 'evidence'):
                _text(blocker[key], 'blocker ' + key)
    return copy.deepcopy(catalog)


def lineage_identity(row):
    return {'lineage': row['id'], 'origin_group': row['origin_group'], 'mission': row['mission']['id'],
            'source_commit': row['source']['commit']}


def _availability(row):
    """Public preflight only. The harness still authenticates calibration before inference."""
    from .registry import pin_case, resolve_case
    blockers = copy.deepcopy(row['blockers'])
    pin = None
    if row['case'] is None:
        blockers.append({'kind': 'qualification', 'reason': 'No admitted executable case is registered.',
                         'evidence': 'benchmark case registry'})
    else:
        try:
            definition = resolve_case(row['case'])
            pin = pin_case(row['case'], 'original', 0)
            if (definition.execution != 'actual-agent-emulation' or definition.evidence_track != 'firmware'
                    or pin.get('scope') != 'full-workflow'):
                raise ValueError('Case does not execute a full MCU firmware workflow')
            if definition.manifest.get('corpus_identity') != lineage_identity(row):
                raise ValueError('Registered case provenance differs from the selected lineage and mission')
            if pin.get('calibration', {}).get('status') != 'passed':
                raise ValueError('Reference and mutant qualification is pending')
        except (OSError, ValueError) as error:
            blockers.append({'kind': 'qualification', 'reason': str(error), 'evidence': 'benchmark case registry'})
            pin = None
    return {'lineage': row['id'], 'status': 'blocked' if blockers else 'ready', 'blockers': blockers,
            'case_pin': pin}


def _legacy_plan(catalog, repetitions, availability):
    from .suites import normalize_manifest
    if type(repetitions) is not int or not 1 <= repetitions <= 20:
        raise ValueError('Corpus repetitions must be an integer from 1 through 20')
    if len(availability) != len(catalog['lineages']):
        raise ValueError('Incomplete frozen availability inventory')
    for row, available in zip(catalog['lineages'], availability):
        _object(available, 'lineage status blockers case_pin', 'availability')
        if (available['lineage'] != row['id'] or available['status'] not in ('ready', 'blocked')
                or not isinstance(available['blockers'], list)
                or (available['status'] == 'blocked') != bool(available['blockers'])):
            raise ValueError('Inconsistent frozen case availability')
        pin = available['case_pin']
        if available['status'] == 'ready' and (
                not isinstance(pin, dict) or pin.get('case_id') != row['case']
                or pin.get('manifest', {}).get('corpus_identity') != lineage_identity(row)
                or pin.get('calibration', {}).get('status') != 'passed'):
            raise ValueError('Ready case lacks frozen matching qualification')
    slots = []
    for repeat in range(repetitions):
        for row, available in zip(catalog['lineages'], availability):
            slots.append({'slot': len(slots), 'repeat': repeat, 'lineage': row['id'], 'area': row['area'],
                          'origin_group': row['origin_group'], 'split': row['split'],
                          'mission': row['mission']['id'], 'case': row['case'],
                          'required_stages': list(STAGES), 'status': available['status'],
                          'blockers': copy.deepcopy(available['blockers'])})
    ready = [row for row, available in zip(catalog['lineages'], availability) if available['status'] == 'ready']
    suite = None
    if ready:
        suite = normalize_manifest({'schema': 'benchmark-suite/1', 'id': catalog['id'], 'version': catalog['version'],
            'entries': [{'case': row['case'], 'scenario': 'original', 'case_seed': 0} for row in ready],
            'repetitions': repetitions, 'child_budget_seconds': 10800,
            'suite_budget_seconds': min(604800, 10800 * repetitions * len(ready)), 'max_active_children': 1})
    body = {'schema': 'benchmark-corpus-plan/1', 'model_benchmark': False,
            'catalog': copy.deepcopy(catalog), 'catalog_sha256': _digest(catalog), 'repetitions': repetitions,
            'availability': copy.deepcopy(availability), 'slots': slots, 'runnable_suite': suite,
            'coverage': {area: {'lineages': sum(r['area'] == area for r in catalog['lineages']),
                               'ready': sum(r['area'] == area for r in ready)} for area in AREAS},
            'qualification': 'Intake and public preflight only; not device or model performance. '
                             'The harness must authenticate reference qualification before any agent starts.'}
    return {**body, 'plan_sha256': _digest(body)}


def _selection(catalog, include=None, exclude=None):
    """Resolve operator choices without deleting anything from the intake inventory."""
    names = {row['id'] for row in catalog['lineages']}
    if include is not None:
        if not isinstance(include, (list, tuple)) or any(not isinstance(v, str) for v in include):
            raise ValueError('Include must contain lineage IDs')
        if len(include) != len(set(include)) or not set(include) <= names:
            raise ValueError('Duplicate or unknown included lineage')
    if exclude is None:
        exclude = []
    elif isinstance(exclude, dict):
        exclude = list(exclude.items())
    if not isinstance(exclude, (list, tuple)):
        raise ValueError('Exclude must contain lineage IDs and reasons')
    excluded = {}
    for entry in exclude:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError('Each excluded lineage requires a reason')
        name, reason = entry
        if not isinstance(name, str) or name not in names or name in excluded:
            raise ValueError('Duplicate or unknown excluded lineage')
        _text(reason, 'exclusion reason')
        excluded[name] = reason
    if include is not None and set(include) & set(excluded):
        raise ValueError('A lineage cannot be both included and excluded')
    selected = names if include is None else set(include)
    return [{'lineage': row['id'], 'selected': row['id'] in selected and row['id'] not in excluded,
             'reason': excluded.get(row['id']) or ('Selected by default.' if include is None
                 else 'Explicitly included.' if row['id'] in selected
                 else 'Not included in the explicit selection.')}
            for row in catalog['lineages']]


def _plan(catalog, repetitions, availability, selection=None):
    from .suites import normalize_manifest
    legacy = _legacy_plan(catalog, repetitions, availability)
    selection = _selection(catalog) if selection is None else copy.deepcopy(selection)
    if not isinstance(selection, list) or len(selection) != len(catalog['lineages']):
        raise ValueError('Incomplete frozen lineage selection')
    for row, decision in zip(catalog['lineages'], selection):
        _object(decision, 'lineage selected reason', 'selection')
        if decision['lineage'] != row['id'] or type(decision['selected']) is not bool:
            raise ValueError('Inconsistent frozen lineage selection')
        _text(decision['reason'], 'selection reason')
    choices = {row['lineage']: row for row in selection}
    ready = [row for row, available in zip(catalog['lineages'], availability)
             if choices[row['id']]['selected'] and available['status'] == 'ready']
    context = {'catalog': catalog, 'availability': availability, 'selection': selection,
               'repetitions': repetitions}
    context_sha256 = _digest(context)
    suite = None
    if ready:
        suite = normalize_manifest({'schema': 'benchmark-suite/1',
            'id': catalog['id'] + ':' + context_sha256, 'version': catalog['version'],
            'entries': [{'case': row['case'], 'scenario': 'original', 'case_seed': 0} for row in ready],
            'repetitions': repetitions, 'child_budget_seconds': 10800,
            'suite_budget_seconds': min(604800, 10800 * repetitions * len(ready)), 'max_active_children': 1})
    slots = []
    for slot in legacy['slots']:
        choice = choices[slot['lineage']]
        slots.append({**slot, 'selected': choice['selected'], 'selection_reason': choice['reason'],
                      'availability_status': slot['status'],
                      'status': slot['status'] if choice['selected'] else 'excluded'})
    body = {key: value for key, value in legacy.items() if key != 'plan_sha256'}
    body.update(schema='benchmark-corpus-plan/2', selection=selection, context_sha256=context_sha256,
                slots=slots, runnable_suite=suite,
                coverage={area: {'lineages': sum(r['area'] == area for r in slots) // repetitions,
                    'selected': sum(r['area'] == area and r['selected'] for r in slots) // repetitions,
                    'excluded': sum(r['area'] == area and not r['selected'] for r in slots) // repetitions,
                    'ready': sum(r['area'] == area and r['status'] == 'ready' for r in slots) // repetitions,
                    'blocked': sum(r['area'] == area and r['status'] == 'blocked' for r in slots) // repetitions}
                    for area in AREAS})
    return {**body, 'plan_sha256': _digest(body)}


def plan_corpus(catalog, repetitions=3, *, include=None, exclude=None):
    catalog = validate_catalog(catalog)
    selection = _selection(catalog, include, exclude)
    return _plan(catalog, repetitions, [_availability(row) for row in catalog['lineages']], selection)


def validate_plan(plan, *, current=False):
    """Validate frozen inputs, optionally requiring the entire public inventory to remain current."""
    try:
        catalog = validate_catalog(plan['catalog'])
        if plan['schema'] == 'benchmark-corpus-plan/1':
            expected = _legacy_plan(catalog, plan['repetitions'], plan['availability'])
        elif plan['schema'] == 'benchmark-corpus-plan/2':
            expected = _plan(catalog, plan['repetitions'], plan['availability'], plan['selection'])
        else:
            raise ValueError('Unsupported frozen corpus plan schema')
    except (KeyError, TypeError) as error:
        raise ValueError('Malformed frozen corpus plan') from error
    if plan != expected:
        raise ValueError('Frozen corpus plan changed')
    if current:
        availability = [_availability(row) for row in catalog['lineages']]
        expected = (_legacy_plan(catalog, plan['repetitions'], availability)
                    if plan['schema'] == 'benchmark-corpus-plan/1' else
                    _plan(catalog, plan['repetitions'], availability, plan['selection']))
        if plan != expected:
            raise ValueError('Case availability or identity changed; freeze a new corpus plan')
    return copy.deepcopy(plan)


def start_corpus(plan, executor=None, options=None, request_id=None, *, home=None):
    """Freeze intake separately; dispatch only admitted software cases through the existing harness."""
    from .suites import start_suite
    plan = validate_plan(plan)
    if plan['runnable_suite'] is None:
        raise ValueError('Corpus has no runnable, qualified cases')
    if executor == 'replay' or (options or {}).get('replay_candidates') is not None:
        raise ValueError('Corpus model measurements require an actual agent runtime')
    plan = validate_plan(plan, current=True)
    return {**start_suite(plan['runnable_suite'], executor, options, request_id, home=home),
            'corpus_plan_sha256': plan['plan_sha256']}


def report_corpus(plan, suite=None):
    """Count frozen slots without treating unavailable devices as model failures.

    A saved suite is trusted configured-owner evidence, not independent authentication.
    Its manifest and complete case pins must match this plan before any result counts.
    A trial passes or fails as its suite export scored it (the row's `scored` outcome); fresh reuse is reported beside
    a pass.
    """
    from .outcomes import STOPPED, classify
    from .suite_reporting import _saved_report
    plan = validate_plan(plan)
    trials, qualification = {}, None
    modern = plan['schema'] == 'benchmark-corpus-plan/2'
    selected = {row['lineage'] for row in plan['selection'] if row['selected']} if modern else {
        row['id'] for row in plan['catalog']['lineages']}
    ready = [a for a in plan['availability'] if a['status'] == 'ready' and a['lineage'] in selected]
    if suite is not None:
        try:
            suite = _saved_report(suite)
            identity = suite['experiment']['comparison_identity']
            if (identity['manifest'] != plan['runnable_suite'] or identity.get('protocol') != 'assistive/1'
                    or identity['execution'] != 'actual-agent-emulation' or identity['evidence_track'] != 'firmware'
                    or identity['scope'] != 'full-workflow'
                    or suite['experiment']['dimensions'].get('runtime') not in ('codex', 'goose')
                    or [p['pin_sha256'] for p in identity['entry_pins']] != [_digest(a['case_pin']) for a in ready]):
                raise ValueError('Suite does not match the frozen corpus and actual-agent execution')
            trials = {(ready[t['entry_index']]['lineage'], t['repeat_index']): t for t in suite['trials']}
            qualification = suite['qualification']
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError('Malformed saved corpus suite') from error
    rows = []
    for slot in plan['slots']:
        row = {**copy.deepcopy(slot), 'status': (slot['status'] if slot['status'] in ('blocked', 'excluded') else 'not_run'),
               'run_id': None, 'passed': False, 'reason': None}
        trial = trials.get((slot['lineage'], slot['repeat']))
        if trial and not trial.get('run_id') and (
                trial.get('status') in STOPPED
                or trial.get('status') == 'finished' and trial.get('outcome_category') != 'completed'):
            row.update(status='stopped', reason=trial.get('reason'), trial_key=trial['trial_key'],
                       stopped_stage=trial.get('stopped_stage'))
        if trial and trial.get('run_id'):
            # The outcome the suite export stored for the trial. A benchmark-suite-report/2 row stored none, so it is
            # classified as exported, which shows no spent budget.
            scored = trial.get('scored') or classify(trial, trial)
            # A counted equivalent is a pass of a trial the owner completed.
            equivalent = scored == 'pass' and trial.get('outcome_category') == 'completed'
            grade = trial.get('equivalence')
            mission_record = grade.get('mission') if isinstance(grade, dict) else None
            mission = mission_record.get('passed') if isinstance(mission_record, dict) else None
            mission_fault = mission_record.get('fault') if isinstance(mission_record, dict) else None
            mission_reason = mission_record.get('reason') if isinstance(mission_record, dict) else None
            mission_fault = mission_fault if isinstance(mission_fault, str) else None
            mission_reason = mission_reason if isinstance(mission_reason, str) else None
            if equivalent:
                status = 'passed'
            elif scored == 'failure':
                status = 'failed'
            elif scored == 'running':
                status = 'running'
            elif (trial.get('status') in STOPPED
                    or trial.get('outcome') == 'stopped'
                    or trial.get('status') == 'finished' and trial.get('outcome_category') != 'completed'):
                status = 'stopped'
            elif trial.get('verdict') in ('equivalent', 'unqualified'):
                status = 'unqualified'
            else:
                status = 'unscored'
            row.update(status=status, run_id=trial['run_id'], passed=status == 'passed',
                       reason=trial.get('reason'),
                       trial_key=trial['trial_key'], equivalent=equivalent, fresh_mission=mission,
                       fresh_mission_fault=mission_fault, fresh_mission_reason=mission_reason,
                       stopped_stage=trial.get('stopped_stage'), repairs=trial.get('repairs'),
                       elapsed_seconds=trial.get('elapsed_seconds'), usage=trial.get('usage'))
        rows.append(row)
    def summary(selected):
        counts = {s: sum(r['status'] == s for r in selected) for s in
                  ('blocked', 'not_run', 'passed', 'failed', 'unscored', 'unqualified', 'stopped', 'running')}
        counts.update(planned=len(selected), started=sum(r['run_id'] is not None for r in selected))
        if modern:
            counts.update(selected=sum(r['selected'] for r in selected),
                          excluded=sum(r['status'] == 'excluded' for r in selected))
        measured = counts['passed'] + counts['failed']
        result = {'counts': counts, 'measured_success': {'passed': counts['passed'], 'denominator': measured,
                'rate': counts['passed']/measured if measured else None},
                'demonstrated_coverage': {'passed': counts['passed'], 'denominator': len(selected),
                'rate': counts['passed']/len(selected) if selected else None}}
        if modern:
            denominator = counts['selected']
            result['selected_coverage'] = {'passed': counts['passed'], 'denominator': denominator,
                                          'rate': counts['passed']/denominator if denominator else None}
        return result
    lineages = {r['id']: summary([s for s in rows if s['lineage'] == r['id']]) for r in plan['catalog']['lineages']}
    groups = {}
    for origin in sorted({r['origin_group'] for r in rows}):
        names = {r['lineage'] for r in rows if r['origin_group'] == origin}
        rates = [lineages[name]['measured_success']['rate'] for name in names
                 if lineages[name]['measured_success']['rate'] is not None]
        groups[origin] = {'lineages': sorted(names), 'measured_lineages': len(rates),
                          'mean_lineage_success': sum(rates)/len(rates) if rates else None}
    rates = [g['mean_lineage_success'] for g in groups.values() if g['mean_lineage_success'] is not None]
    return {'schema': 'benchmark-corpus-report/2' if modern else 'benchmark-corpus-report/1',
            'plan_sha256': plan['plan_sha256'],
            'catalog_sha256': plan['catalog_sha256'], 'model_benchmark': any(r['run_id'] for r in rows),
            'qualification': qualification, **summary(rows), 'slots': rows, 'lineages': lineages,
            'areas': {area: summary([r for r in rows if r['area'] == area]) for area in AREAS},
            'origin_groups': groups, 'measured_origin_macro_success': sum(rates)/len(rates) if rates else None,
            'limitations': [
                'Blocked and unrun slots remain in demonstrated coverage, not measured model failures.',
                'Measured success counts a pass when the evaluator grades the frozen driver equivalent; '
                'fresh reuse is reported beside it.',
                'Host and operator stops and missing grades are reported separately; '
                'a spent budget or a stop the system caused is a failure.',
                'Origin means weight measured lineages equally; unavailable groups have no measured rate.',
                'Software execution does not establish physical transport, timing or actuation behavior.',
                'Saved owner reports and hashes provide provenance, not cryptographic authentication.']}

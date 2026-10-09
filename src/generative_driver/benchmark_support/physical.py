"""Optional physical profile (bme280), observed without truth. The operator owns the bound device and supplies the
independent reference through driver_respond; the configurator reaches the device itself, under the operator's binding
and grants. Nothing here unlocks truth or grades: after the run the evaluator grades the recorded fresh measurement
against the operator reference and the truth's limits, once, and never touches the board."""
import hashlib
import json
import math
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from .cases import _state, _write

OUTPUTS = ('temperature', 'humidity', 'pressure')
EVENTS = 'physical-package-events.jsonl'
REUSE_EVENTS = 'reuse-events.jsonl'
REFERENCE = 'reference.json'


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _reference_request(reason):
    return {'blocked': reason + '. Supply a fresh independent reference through driver_respond, then resume. '
            'Observation requires stage="reuse", channel, observed_at, evidence_path and reference values with absolute_tolerance '
            'for temperature (Celsius), humidity (percent RH) and pressure (pascals). Take it while fresh reuse measures: after '
            'the run the evaluator compares that measurement with the reference and its own limits.'}


def reference_errors(observed):
    """Why an operator reference is not a complete one, or None. Its age and the breadth of its tolerances are judged
    against the evaluator's limits after the run: nothing here knows them."""
    if not isinstance(observed, dict) or not isinstance(observed.get('channel'), str) or not observed['channel'].strip():
        return 'Reference channel missing'
    if not _finite(observed.get('observed_at')):
        return 'Reference timestamp missing'
    if not isinstance(observed.get('evidence_path'), str) or not Path(observed['evidence_path']).is_file():
        return 'Independent photo/log evidence file missing'
    refs = observed.get('reference')
    if not isinstance(refs, dict) or set(refs) != set(OUTPUTS):
        return 'All three independent reference values are required'
    if any(not isinstance(row, dict) or not _finite(row.get('value')) or not _finite(row.get('absolute_tolerance'))
           or row['absolute_tolerance'] <= 0 for row in refs.values()):
        return 'Reference values and positive tolerances are required'
    return None


def mapping_errors(mapping):
    """The structural errors of a bme280 capability mapping, or []: temperature, humidity and pressure each name an
    output."""
    if (not isinstance(mapping, dict) or set(mapping) != set(OUTPUTS)
            or any(not isinstance(value, str) or not value for value in mapping.values())):
        return [{'path': 'capabilities.json', 'message': 'Map temperature, humidity and pressure each to an output name'}]
    return []


def valid_rows(rows, mapping):
    """The package measurements of fresh reuse that hold a finite value of every mapped output at a finite time."""
    outputs = list(mapping.values()) if isinstance(mapping, dict) and not mapping_errors(mapping) else []
    return [row for row in rows if isinstance(row, dict) and row.get('ok') is True and outputs and _finite(row.get('time'))
            and isinstance(row.get('values'), dict) and all(_finite(row['values'].get(name)) for name in outputs)]


def _datasheet_sha256():
    """The official datasheet's hash, as the public datasheet registry pins it."""
    from ..toolkit import resources_root
    return json.loads((resources_root()/'toolchain/datasheets.json').read_text())['parts']['bme280']['sha256']


def _model(accepted, stages, ws):
    """The register model and converter of the latest handoff of these stages that carries both, hash-verified and
    copied to `ws/model`; None when there is none."""
    from .emulated import _artifact, _verified
    for handoff in reversed(accepted or []):
        if handoff.get('stage') not in stages:
            continue
        found = [_artifact(handoff, name) for name in ('model.json', 'convert.py')]
        if None in found:
            continue
        (ws/'model').mkdir(parents=True, exist_ok=True)
        for (path, sha), name in zip(found, ('model.json', 'convert.py')):
            (ws/'model'/name).write_bytes(_verified((path, sha), handoff['stage']))
        return ws/'model'
    return None


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    from ..toolkit import call_tool
    from .emulated import _artifact, _handoff_with, _verified
    options = options or {}
    binding = options.get('binding')
    if not isinstance(binding, dict) or set(binding) != {'url'} or not str(binding['url']).startswith('ftdi://'):
        return {'blocked':'The physical profile requires an explicit binding {"url":"ftdi://selected-adapter/1"}, a connected BME280, and the hardware extra.'}
    state_path,state = _state(run_dir)
    ws=Path(workspace).resolve(); ws.mkdir(parents=True,exist_ok=True)
    if 'write' not in options.get('configured_effects', []):
        return {'blocked':'The physical profile requires the operator-configured write effect grant for sensor initialization.'}
    if stage=='acquire':
        return {'objective':'Acquire the official BME280 datasheet with acquire_datasheet(part_hint="bme280"). Report the hash-checked PDF and extracted pages.',
                'inputs':[],'allowed_tools':['acquire_datasheet'],'context':{'part_hint':'bme280'}}
    if stage=='interpret':
        handoff = _handoff_with(accepted, ('acquire',), 'datasheet.pdf')
        pages = next((Path(a['path']) for a in (handoff or {}).get('artifacts', [])
                      if Path(a['path']).is_dir() and Path(a['path']).name.split('-', 1)[-1] == 'pages'), None)
        if handoff is None or pages is None:
            return {'skip': 'Interpretation skipped: no acquired official datasheet with extracted pages'}
        pdf = _artifact(handoff, 'datasheet.pdf')
        _verified(pdf, 'acquisition')
        prepared=call_tool('interpret_run',{'run_dir':str(Path(run_dir)/'benchmark/tools'),'front_end':'interpret-datasheet',
            'inputs':{'datasheet.pdf':str(pdf[0]),'pages':str(pages)},'mode':'prepare','attempt':state.get('interpret_attempt',0)+1,
            'workspace_root':tempfile.mkdtemp(prefix='gd-sensor-candidate-')})
        if not prepared.get('ok'):raise ValueError(str(prepared))
        state['interpret_attempt']=state.get('interpret_attempt',0)+1;state['interpret']=prepared;_write(state_path,state)
        return {'work_dir':prepared['workspace'],'prompt':prepared['prompt_for_agent'],'report_required':False,
                'objective':'Recover the sensor register interface and conversion from supplied datasheet.',
                'inputs':[str(Path(prepared['workspace'])/n) for n in prepared['sealed_inputs']], 'allowed_tools':[],
                'boundary':'Sealed supplied datasheet; instruction isolation'}
    if stage=='probe':
        model = _model(accepted, ('interpret',), ws)
        if model is None:
            return {'skip': 'Probe skipped: no interpreted register model and converter available'}
        state['physical_model_dir']=str(model);_write(state_path,state)
        return {'objective':'Run probe_run on the supplied model with n=3. Write capabilities.json mapping temperature, humidity, pressure to the actual output names; units must be Celsius, percent RH and pascals. Report identity and observed finite values; no absolute calibration is established yet.',
                'inputs':[str(model)],'allowed_tools':['probe_run','probe_diff'],
                'context':{'model_dir':str(model)},'binding':binding,'effects':['write']}
    if stage == 'ground':
        model = _model(accepted, ('probe',), ws)
        if model is None:
            return {'skip': 'Ground skipped: no probed register model available'}
        observations=[o for o in options.get('operator_observations',[]) if isinstance(o,dict) and o.get('stage')==stage]
        inputs, notes = [str(model)], []
        if observations:
            evidence=ws/'operator-observations.json';_write(evidence,observations);inputs.insert(0,str(evidence))
        else:
            notes.append('No operator observation for ground: an independent reading supplied through driver_respond with '
                         'stage "ground" is compared where given')
        return {'objective':'Test the meanings the supplied register model gives the sensor: units, scaling and conversion. '
                'Run probe_run on the supplied model over the operator-owned binding and compare its finite values with the '
                'operator observations where supplied (operator-observations.json). Report what the device did that the model '
                'did not predict, needs_revision for a specific contradiction, and distinguish observed agreement from absolute '
                'calibration.', 'inputs':inputs,'allowed_tools':['probe_run','probe_diff'],'context':{'model_dir':str(model)},
                'binding':binding,'effects':['write'], **({'input_notes': notes} if notes else {})}
    if stage=='emit':
        model = _model(accepted, ('probe',), ws)
        if model is None:
            return {'skip': 'Emit skipped: no probed register model available'}
        state['physical_model_dir']=str(model);_write(state_path,state)
        # The latest probe record a worker's run left in the probe or ground handoff that read the expected identity.
        for handoff in reversed(accepted or []):
            if handoff.get('stage') not in ('probe', 'ground'):
                continue
            found = _artifact(handoff, 'probe.json')
            if found is None:
                continue
            raw = _verified(found, handoff['stage'])
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get('identity_ok') is True:
                (ws/'probe.json').write_bytes(raw)
                return {'objective':'Emit the supplied register model with actual probe evidence, then check the package; report its directory.',
                        'inputs':[str(model),str(ws/'probe.json')],'allowed_tools':['emit_package','emit_check'],
                        'context':{'model_dir':str(model),'probe':str(ws/'probe.json')}}
        missing = 'No probe record of the model that read its identity is available'
        return {'objective':'Emit the supplied register model, then check the package; report its directory. ' + missing +
                    '; emit_package needs one, so report what is missing.',
                'inputs':[str(model)],'allowed_tools':['emit_package','emit_check'],
                'context':{'model_dir':str(model),'input_notes':[missing]},'input_notes':[missing]}
    if stage=='reuse':
        from ..configurator import IntegrityError, digest
        source = next(((Path(a['path']), a['sha256']) for h in reversed(accepted or []) if h.get('stage') == 'emit'
                       for a in h.get('artifacts', []) if (Path(a['path'])/'manifest.json').is_file()), None)
        if source is None:
            return {'skip': 'Fresh reuse skipped: no accepted emitted package to reuse'}
        # The operator's own reference, never one a worker claims: the latest supplied for reuse, complete.
        supplied=[o for o in options.get('operator_observations',[]) if isinstance(o,dict) and o.get('stage')=='reuse']
        observed = supplied[-1] if supplied else None
        reason = reference_errors(observed) if observed else 'Fresh independent reference missing for this reuse'
        if reason:
            return _reference_request(reason)
        attempt=uuid.uuid4().hex
        reference_copy=Path(run_dir)/('benchmark/reference-reuse-'+attempt+Path(observed['evidence_path']).suffix)
        shutil.copy2(observed['evidence_path'],reference_copy)
        state['physical_reuse_reference']={**observed,'evidence_path':str(reference_copy),
                                           'evidence_sha256':hashlib.sha256(reference_copy.read_bytes()).hexdigest()}
        package=ws/'package';shutil.copytree(source[0],package)
        if digest(package) != source[1]:
            raise IntegrityError('Accepted package identity changed')
        state.setdefault('package_copies',{})[stage]=str(package)
        state.setdefault('package_attempts',{})[stage]=attempt
        state['physical_reuse_reference_attempt']=attempt
        _write(state_path,state)
        return {'objective':'Using only this package, take a fresh measurement via benchmark_package_execute(operation="measure"). Report all values with units and identify any unsupported output.',
                'inputs':[str(package)],'allowed_tools':['benchmark_package_execute'],'context':{'package_dir':str(package)},
                'binding':binding,'effects':['write'],'boundary':'Fresh package-only context; physical sensor'}
    raise ValueError('Unknown physical stage '+stage)


def observe_stage(case_id, stage, run_dir, workspace, report, accepted=None, options=None):
    """Observe a physical stage without truth: what it hands on, with its checks as notes. Only a changed sealed input
    stops (integrity); emit_check the host could not run raises (a resumable host stop). Fresh reuse hands on its
    recorded measurement and the operator reference it was taken against, for the evaluator after the run."""
    import re
    from ..toolkit import call_tool
    from .emulated import _emitted_mapping, _host, _observed
    options=options or {};path,state=_state(run_dir);ws=Path(workspace)
    if stage=='acquire':
        official=_datasheet_sha256()
        pdf=next((p for p in sorted(ws.rglob('datasheet.pdf')) if hashlib.sha256(p.read_bytes()).hexdigest()==official
                  and (p.parent/'pages/index.json').is_file()),None)
        if pdf:
            state['datasheet_dir']=str(pdf.parent);_write(path,state)
        return _observed([pdf,pdf.parent/'pages'] if pdf else [],[{'name':'official_datasheet_hash','passed':bool(pdf),
                         **({} if pdf else {'reason':'No hash-checked official datasheet and pages'})}])
    if stage=='interpret':
        prepared=state['interpret'];tools=str(Path(run_dir)/'benchmark/tools')
        result=call_tool('interpret_collect',{'run_dir':tools,'attempt':state['interpret_attempt'],
            'expected_seal_sha256':prepared['seal_sha256'],'seal_kind':'instruction'})
        reason=result.get('reason') or result.get('error')
        if result.get('sealed_inputs_unchanged') is False or (not result.get('ok') and re.search(r'(?i)seal|changed|differ', str(result.get('reason') or ''))):
            return {'stop':{'category':'integrity','reason':'Sealed interpretation inputs changed or are unverifiable: '+str(reason)}}
        model=Path(prepared['model_dir'])
        intact=bool(result.get('ok') or result.get('sealed_inputs_unchanged') is True and result.get('frozen_copy') is True)
        present=intact and (model/'model.json').is_file()
        validated=call_tool('model_validate',{'run_dir':tools,'model_dir':str(model)}) if present else {}
        converter=intact and (model/'convert.py').is_file()
        state['model_dir']=str(model);_write(path,state)
        checks=[{'name':'sealed_inputs','passed':bool(result.get('ok')),**({} if result.get('ok') else {'reason':str(reason)})},
                {'name':'executable_model','passed':bool(validated.get('ok')),**({'defects':validated['defects']} if validated.get('defects') else {}),
                 **({} if present else {'reason':'No model.json behind an intact seal'})},
                {'name':'register_converter','passed':converter,**({} if converter else {'reason':'No convert.py behind an intact seal'})}]
        kept=[model/name for name in ('model.json','convert.py') if intact and (model/name).is_file()]
        return _observed(kept,checks,scope='Sealed-input integrity, the register model\'s structure and its converter; meaning rests on the device')
    if stage=='probe':
        mapping_path,dropped,artifacts,reason,errors=ws/'capabilities.json',[],[],None,None
        if mapping_path.is_symlink() or mapping_path.exists() and not mapping_path.resolve().is_relative_to(ws.resolve()):
            reason='capabilities.json is a link or outside the workspace; it was not followed'
            dropped.append({'path':str(mapping_path),'reason':reason})
        elif not mapping_path.is_file():
            reason='Missing capability mapping'
        else:
            try:
                mapping=json.loads(mapping_path.read_text(encoding='utf-8'))
            except (OSError,ValueError,UnicodeError) as error:
                reason='capabilities.json is not readable JSON: '+type(error).__name__
            else:
                artifacts.append(mapping_path)
                errors=mapping_errors(mapping) or None
                reason='Invalid capability mapping' if errors else None
        model=Path(state.get('physical_model_dir') or ws/'model')
        artifacts+=[model/name for name in ('model.json','convert.py') if (model/name).is_file()]
        return _observed(artifacts,[{'name':'capability_mapping','passed':reason is None,**({'reason':reason} if reason else {}),
                                     **({'errors':errors} if errors else {})}],dropped,
                         scope='The worker\'s mapping and the probed register model: device evidence, not a grade')
    if stage=='ground':
        return _observed([],[],scope='The configurator\'s own device tests of the model and their differences; physical '
                         'meaning rests on operator observations where given')
    if stage=='emit':
        rejected=[]
        for manifest in sorted(ws.rglob('manifest.json')):
            checked=call_tool('emit_check',{'run_dir':str(Path(run_dir)/'benchmark/tools'),'package_dir':str(manifest.parent),'model_dir':str(ws/'model')})
            if _host(checked):
                raise RuntimeError('emit_check could not run on this host for '+manifest.parent.name+': '+str(checked.get('error')))
            if checked.get('ok') and checked.get('integrity_ok') and checked.get('runtime_current') and checked.get('seeds_run',0)>0:
                state.update(package_dir=str(manifest.parent),package_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
                             physical_model_dir=str(ws/'model'));_write(path,state)
                return _observed([manifest.parent],[{'name':'register_package_integrity_and_replay','passed':True,
                                                     **({'rejected':rejected} if rejected else {})}])
            rejected.append({'package':manifest.parent.name,'reason':'emit_check did not pass'})
        return _observed([],[{'name':'register_package_integrity_and_replay','passed':False,
                              'reason':'No package passed integrity and seeded replay',**({'rejected':rejected} if rejected else {})}],
                         [{'path':row['package'],'reason':row['reason']} for row in rejected])
    if stage=='reuse':
        attempt=state.get('package_attempts',{}).get(stage)
        events=Path(run_dir)/'benchmark'/EVENTS
        rows=[json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()] if events.is_file() else []
        mine=[row for row in rows if attempt and row.get('attempt_id')==attempt and row.get('stage')=='reuse'
              and row.get('assignment_id')==options.get('assignment_id')]
        folder=Path(run_dir)/'benchmark/reuse'/str(options.get('assignment_id'));folder.mkdir(parents=True,exist_ok=True)
        # This assignment's measurements as the configurator recorded them, and the operator reference they were taken
        # against: the evaluator grades the frozen driver from these after the run.
        record=folder/REUSE_EVENTS
        record.write_text(''.join(json.dumps(row)+'\n' for row in mine),encoding='utf-8')
        handed=[Path(state['package_copies'][stage])] if Path(state.get('package_copies',{}).get(stage) or ws/'missing').is_dir() else []
        handed.append(record)
        reference=state.get('physical_reuse_reference') if attempt and state.get('physical_reuse_reference_attempt')==attempt else None
        if reference:
            saved=folder/REFERENCE
            _write(saved,{key:reference.get(key) for key in ('channel','observed_at','reference','evidence_sha256')})
            handed+=[saved,Path(reference['evidence_path'])]
        found=_emitted_mapping(accepted)
        try:
            mapping=json.loads(found[0].read_text(encoding='utf-8')) if found else None
        except (OSError,ValueError,UnicodeError):
            mapping=None
        measured=bool(valid_rows(mine,mapping))
        mission={'name':'fresh_physical_measurement','passed':measured,
                 **({} if measured else {'reason':'No finite measurement of every mapped output by the frozen package'})}
        return _observed(handed,[mission,{'name':'operator_reference','passed':bool(reference),
                                          **({} if reference else {'reason':'No operator reference for this reuse'})}],
                         scope='The fresh measurement of the frozen package and the operator reference; a note, not a grade')
    raise ValueError('Unknown physical stage '+stage)


def _measure(package, binding):
    """One measurement by the package's own CLI on the operator's bound device: (exit status, stdout, stderr)."""
    import subprocess
    import sys
    result=subprocess.run([sys.executable,'-I','-B','-c',
        'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
        str(package),'--url',binding['url'],'--allow-write','--json','measure','--n','1'],
        cwd=package.parent,capture_output=True,text=True,timeout=60)
    return result.returncode,result.stdout,result.stderr


def package_execute(case_id,run_dir,stage,package_dir,operation,parameters=None,binding=None,allow_effects=None,options=None):
    from ..toolkit import call_tool
    _,state=_state(run_dir);options=options or {};package=Path(package_dir).resolve()
    if package != Path(state.get('package_copies',{}).get(stage,'')).resolve() or binding!=options.get('binding'):
        return {'ok':False,'error':{'fault':'operator','code':'binding','message':'Use the assigned package and physical binding'}}
    if operation!='measure' or parameters or 'write' not in (allow_effects or []):
        return {'ok':False,'error':{'fault':'operator','code':'permission','message':'This physical benchmark allows a granted measurement only'}}
    checked=call_tool('emit_check',{'run_dir':str(Path(run_dir)/'benchmark/package-check'),'package_dir':str(package),
        'model_dir':state['physical_model_dir'],'expected_manifest_sha256':state['package_sha256']})
    if not checked.get('ok') or not checked.get('integrity_ok') or not checked.get('runtime_current'):
        return {'ok':False,'error':{'fault':'host','code':'package','message':'Package check failed'}}
    status,stdout,stderr=_measure(package,binding)
    try:row=json.loads(stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):row={'status':'ERR','reason':stderr[-1000:]}
    row.update(ok=status==0 and row.get('status')=='OK',stage=stage,attempt_id=state['package_attempts'][stage],
               assignment_id=options.get('assignment_id'),execution='actual-physical-package',time=time.time(),
               package_sha256=state['package_sha256'])
    evidence=Path(run_dir)/'benchmark'/EVENTS
    with evidence.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,allow_nan=False)+'\n')
    return {**row,'evidence':str(evidence)}

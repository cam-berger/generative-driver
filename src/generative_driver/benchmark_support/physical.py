"""Optional physical profile; reference readings arrive only from the operator channel."""
import hashlib
import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from .cases import _state, _write, _accepted_file


def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    from ..toolkit import call_tool, resources_root
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
        source=Path(state['datasheet_dir'])
        inputs={'datasheet.pdf':str(source/'datasheet.pdf'),'pages':str(source/'pages')}
        prepared=call_tool('interpret_run',{'run_dir':str(Path(run_dir)/'benchmark/tools'),'front_end':'interpret-datasheet','inputs':inputs,
            'mode':'prepare','attempt':state.get('interpret_attempt',0)+1,'workspace_root':tempfile.mkdtemp(prefix='gd-sensor-candidate-')})
        if not prepared.get('ok'):raise ValueError(str(prepared))
        state['interpret_attempt']=state.get('interpret_attempt',0)+1;state['interpret']=prepared;_write(state_path,state)
        return {'work_dir':prepared['workspace'],'prompt':prepared['prompt_for_agent'],'report_required':False,
                'objective':'Recover the sensor register interface and conversion from supplied datasheet.',
                'inputs':[str(Path(prepared['workspace'])/n) for n in prepared['sealed_inputs']], 'allowed_tools':[],
                'boundary':'Sealed supplied datasheet; encrypted evaluator evidence; instruction isolation'}
    if stage=='probe':
        model=_accepted_file(accepted,'interpret','model.json',Path(state['model_dir'])/'model.json')
        converter=_accepted_file(accepted,'interpret','convert.py',Path(state['model_dir'])/'convert.py')
        (ws/'model').mkdir(exist_ok=True)
        shutil.copy2(model,ws/'model/model.json');shutil.copy2(converter,ws/'model/convert.py')
        state['physical_model_dir']=str(ws/'model');_write(state_path,state)
        return {'objective':'Run probe_run on the supplied model with n=3. Write capabilities.json mapping temperature, humidity, pressure to the actual output names; units must be Celsius, percent RH and pascals. Report identity and observed finite values; no absolute calibration is established yet.',
                'inputs':[str(ws/'model')],'allowed_tools':['probe_run','probe_diff','model_validate'],
                'context':{'model_dir':str(ws/'model')},'binding':binding,'effects':['write']}
    if stage in ('ground','maintain'):
        observations=[o for o in options.get('operator_observations',[]) if isinstance(o,dict) and o.get('stage')==stage]
        if not observations:
            return {'blocked':'Supply an independent physical reference through driver_respond, then resume. Observation: {"stage":"'+stage+'","channel":"independent meter and identifier","observed_at":UNIX_SECONDS,"reference":{"temperature":{"value":NUMBER,"absolute_tolerance":NUMBER},"humidity":{"value":NUMBER,"absolute_tolerance":NUMBER},"pressure":{"value":NUMBER,"absolute_tolerance":NUMBER}},"evidence_path":"absolute photo/log path"}. Units: Celsius, percent RH, pascals. Do not use candidate output as the reference.'}
        evidence=ws/'physical-observation.json';_write(evidence,observations[-1])
        return {'objective':'Assess the supplied independent physical reference and its scope. The evaluator will acquire a fresh sample and compare canonical temperature/humidity/pressure with that reference. Report missing evidence and distinguish observed agreement from absolute calibration.',
                'inputs':[str(evidence)],'allowed_tools':[], 'binding':binding, 'effects':['write']}
    if stage=='emit':
        model=ws/'model';model.mkdir(exist_ok=True)
        for name in ('model.json','convert.py'):
            source=_accepted_file(accepted,'probe',name,Path(state['physical_model_dir'])/name)
            shutil.copy2(source,model/name)
        probe=_accepted_file(accepted,'probe','probe.json',state['physical_probe']['probe']);shutil.copy2(probe,ws/'probe.json')
        return {'objective':'Emit the supplied register model with actual probe evidence, then check the package; report its directory.',
                'inputs':[str(model),str(ws/'probe.json')],'allowed_tools':['emit_package','emit_check'],
                'context':{'model_dir':str(model),'probe':str(ws/'probe.json')}}
    if stage=='reuse':
        source=Path(state['package_dir'])
        for h in reversed(accepted or []):
            if h.get('stage')=='emit':
                source=next((Path(a['path']) for a in h['artifacts'] if Path(a['path']).is_dir()),source);break
        package=ws/'package';shutil.copytree(source,package)
        state.setdefault('package_copies',{})[stage]=str(package);_write(state_path,state)
        return {'objective':'Using only this package, take a fresh measurement via benchmark_package_execute(operation="measure"). Report all values with units and identify any unsupported output.',
                'inputs':[str(package)],'allowed_tools':['benchmark_package_execute'],'context':{'package_dir':str(package)},
                'binding':binding,'effects':['write'],'boundary':'Fresh package-only context; physical sensor'}
    raise ValueError('Unknown physical stage '+stage)


def check_stage(case_id,stage,run_dir,workspace,report,accepted=None,options=None):
    from ..toolkit import call_tool, resources_root
    options=options or {};path,state=_state(run_dir);ws=Path(workspace)
    def answer(ok,name,artifacts=(),reason=None,**extra):
        return {'ok':bool(ok),'checks':[{'name':name,'passed':bool(ok)}],'artifacts':[str(p) for p in artifacts], 'reason':reason,**extra}
    if stage=='acquire':
        registry=json.loads((resources_root()/'toolchain/datasheets.json').read_text())['parts']['bme280']
        pdf=next((p for p in ws.rglob('datasheet.pdf') if hashlib.sha256(p.read_bytes()).hexdigest()==registry['sha256'] and (p.parent/'pages/index.json').is_file()),None)
        if not pdf:return answer(False,'official_datasheet_hash',reason='No hash-checked official datasheet and pages')
        state['datasheet_dir']=str(pdf.parent);_write(path,state)
        return answer(True,'official_datasheet_hash',[pdf,pdf.parent/'pages'])
    if stage=='interpret':
        prepared=state['interpret'];result=call_tool('interpret_collect',{'run_dir':str(Path(run_dir)/'benchmark/tools'),
            'attempt':state['interpret_attempt'],'expected_seal_sha256':prepared['seal_sha256'],'seal_kind':'instruction'})
        model=Path(prepared['model_dir']);validated=call_tool('model_validate',{'run_dir':str(Path(run_dir)/'benchmark/tools'),'model_dir':str(model)})
        ok=result.get('ok') and validated.get('ok') and (model/'convert.py').is_file()
        state['model_dir']=str(model);_write(path,state)
        return answer(ok,'sealed_register_model',[model/'model.json',model/'convert.py'] if ok else [],evaluator=validated)
    if stage in ('ground','maintain') and not any(isinstance(o,dict) and o.get('stage')==stage for o in options.get('operator_observations',[])):
        return answer(False,'independent_reference',reason='Operator reference missing')
    if stage in ('probe','ground','maintain') and ('write' not in options.get('configured_effects', []) or not callable(options.get('managed_probe'))):
        return answer(False,'managed_physical_evaluator',reason='Physical evaluation requires an operator-granted managed probe hook; no device action was issued')
    if stage=='probe':
        mapping=ws/'capabilities.json'
        if not mapping.is_file():return answer(False,'output_mapping',reason='capabilities.json missing')
        caps=json.loads(mapping.read_text())
        if set(caps)!={'temperature','humidity','pressure'} or any(not isinstance(v,str) for v in caps.values()):return answer(False,'output_mapping')
        probe=options['managed_probe'](state['physical_model_dir'],3)
        state['physical_probe']=probe;state['capabilities']=caps;_write(path,state)
        good=probe.get('ok') and all(type(s.get(output)) in (int,float) and math.isfinite(s[output]) for s in probe.get('samples',[]) for output in caps.values()) and bool(probe.get('samples'))
        return answer(good,'live_identity_and_finite_samples',[mapping,Path(state['physical_model_dir'])/'model.json',Path(state['physical_model_dir'])/'convert.py',probe['probe']] if good else [],evaluator=probe)
    if stage in ('ground','maintain'):
        observations=[o for o in options.get('operator_observations',[]) if isinstance(o,dict) and o.get('stage')==stage]
        if not observations:return answer(False,'independent_reference',reason='Operator reference missing')
        observed=observations[-1]
        from .emulator import truth_for_case
        from ..benchmark import case_root
        manifest=json.loads((case_root()/'cases/bme280/case.json').read_text());truth=truth_for_case(case_root(),manifest,options)
        validated=grade_physical_reference(observed,truth)
        if not validated['ok']:return answer(False,'independent_reference',reason=validated['reason'])
        probe=options['managed_probe'](state['physical_model_dir'],3)
        if not probe.get('ok'):return answer(False,'fresh_physical_measurement',evaluator=probe)
        from ..benchmark import score_observations
        values={k:sum(sample[output] for sample in probe['samples'])/len(probe['samples']) for k,output in state['capabilities'].items()}
        contract={'checks':[{'id':k,'expected':v['value'],'absolute_tolerance':v['absolute_tolerance']} for k,v in observed['reference'].items()]}
        grade=score_observations(contract,values)
        copy=Path(run_dir)/('benchmark/reference-'+stage+Path(observed['evidence_path']).suffix)
        shutil.copy2(observed['evidence_path'],copy)
        observed={**observed,'evidence_path':str(copy),'evidence_sha256':validated['evidence_sha256']}
        state['physical_grounding']={'observations':values,'reference':observed,'score':grade,'physical':True,'probe':probe['probe']};_write(path,state)
        evidence=Path(run_dir)/('benchmark/physical-'+stage+'.json');_write(evidence,state['physical_grounding'])
        return answer(grade['verdict']=='passed','independent_physical_agreement',[evidence,copy],evaluator=grade)
    if stage=='emit':
        for manifest in ws.rglob('manifest.json'):
            checked=call_tool('emit_check',{'run_dir':str(Path(run_dir)/'benchmark/tools'),'package_dir':str(manifest.parent),'model_dir':str(ws/'model')})
            if checked.get('ok') and checked.get('integrity_ok') and checked.get('runtime_current') and checked.get('seeds_run',0)>0:
                state.update(package_dir=str(manifest.parent),package_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest());_write(path,state)
                return answer(True,'register_package_integrity_and_replay',[manifest.parent],evaluator=checked)
        return answer(False,'register_package_integrity_and_replay')
    if stage=='reuse':
        events=Path(run_dir)/'benchmark/physical-package-events.jsonl'
        rows=[json.loads(x) for x in events.read_text().splitlines()] if events.exists() else []
        valid=[r for r in rows if r.get('ok') and r.get('stage')=='reuse' and r.get('values')]
        return answer(bool(valid),'fresh_physical_package_measurement',[events] if events.exists() else [])
    raise ValueError('Unknown physical stage '+stage)


def grade_physical_reference(observed, truth):
    if not observed.get('channel') or not isinstance(observed.get('observed_at'),(int,float)) or abs(time.time()-observed['observed_at'])>truth['max_reference_age_seconds']:
        return {'ok':False,'reason':'Reference channel/timestamp missing or stale'}
    path=Path(observed.get('evidence_path',''))
    if not path.is_file():return {'ok':False,'reason':'Independent photo/log evidence file missing'}
    refs=observed.get('reference',{})
    if set(refs)!=set(truth['max_tolerances']):return {'ok':False,'reason':'All three independent reference values are required'}
    for key,maximum in truth['max_tolerances'].items():
        row=refs[key]
        if not all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) for k in ('value','absolute_tolerance')) or not 0<row['absolute_tolerance']<=maximum:
            return {'ok':False,'reason':'Reference values/tolerances are invalid or too broad'}
    return {'ok':True,'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def package_execute(case_id,run_dir,stage,package_dir,operation,parameters=None,binding=None,allow_effects=None,options=None):
    import subprocess
    import sys
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
    result=subprocess.run([sys.executable,'-I','-B','-c',
        'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("driver",run_name="__main__")',
        str(package),'--url',binding['url'],'--allow-write','--json','measure','--n','1'],
        cwd=package.parent,capture_output=True,text=True,timeout=60)
    try:row=json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):row={'status':'ERR','reason':result.stderr[-1000:]}
    row.update(ok=result.returncode==0 and row.get('status')=='OK',stage=stage,execution='actual-physical-package',time=time.time(),package_sha256=state['package_sha256'])
    evidence=Path(run_dir)/'benchmark/physical-package-events.jsonl'
    with evidence.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,allow_nan=False)+'\n')
    return {**row,'evidence':str(evidence)}

"""A worker script for the tool's tests that run stages as real worker processes, with no model.

`test_rejection_framing.py`, `test_reuse_controller.py` and `test_service_repair.py` use it. The script it writes takes
the model of the tool's own smoke.
"""


def write_scripted_worker(directory, *, hold_on_repair=False):
    """The command of a worker script written into `directory`: it imports the supplied firmware at acquire, writes
    the unrelated model of the tool's smoke at interpret (the real stage observation notes it unqualified) and reports
    every later stage blocked, with nothing to hand on. `hold_on_repair` makes the first interpretation one a TCP
    binding cannot carry, so probe skips and the configurator's model diff starts a repair, which it then holds."""
    import sys
    from pathlib import Path
    import generative_driver
    path = Path(directory)/'scripted_stage_worker.py'
    source = '''import sys,json,pathlib,hashlib,shutil,time
sys.path.insert(0,PACKAGE_PARENT)
from generative_driver.client import call
from generative_driver.toolkit import resources_root
prompt=sys.stdin.read()
if prompt.startswith('Execute exactly'):
    task=json.loads(prompt.split('\\n',1)[1])
    if task['stage']=='acquire':
        args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
        identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
        image=pathlib.Path(task['inputs'][0])
        out=call('tool',{**identity,'name':'acquire_firmware_artifact','arguments':{'source_path':str(image),'expected_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'origin':'provided_binary'}},args[args.index('--home')+1])
        assert out.get('available'),out
        report={'status':'completed','summary':'scripted import','artifacts':[{'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'} for k in ('artifact','provenance')],'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
    else:
        report={'status':'blocked','summary':'scripted contract: nothing to do in '+task['stage'],'artifacts':[],'checks':[],'unresolved':[]}
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
else:
    # An intentionally unrelated, unqualified model; the real stage observation notes it.
    shutil.copy2(resources_root()/'smoke/model.json','model.json')
'''
    if hold_on_repair:
        source = source.replace("    shutil.copy2(resources_root()/'smoke/model.json','model.json')\n",
            "    if pathlib.Path('DEFECTS.json').exists(): time.sleep(20)\n"
            "    model=json.loads((resources_root()/'smoke/model.json').read_text())\n"
            "    model['channel']={'type':'usb'}\n"
            "    pathlib.Path('model.json').write_text(json.dumps(model))\n", 1)
    source = source.replace('PACKAGE_PARENT', repr(str(Path(generative_driver.__file__).resolve().parents[1])))
    path.write_text(source, encoding='utf-8')
    return [sys.executable, str(path)]

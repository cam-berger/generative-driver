"""Scripted model defects exercise case routing without inference or an emulator."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import generative_driver


class RepairServiceTests(unittest.TestCase):
    def test_old_gateway_is_invalidated_when_a_model_defect_starts_a_new_revision(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as home:
            script=Path(home)/'scripted_agent.py'
            script.write_text('''import sys,json,pathlib,hashlib,shutil,time
sys.path.insert(0,PACKAGE_PARENT)
from generative_driver.client import call
from generative_driver.toolkit import resources_root
prompt=sys.stdin.read()
if prompt.startswith('Execute exactly'):
    task=json.loads(prompt.split('\\n',1)[1])
    args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
    identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
    image=pathlib.Path(task['inputs'][0])
    out=call('tool',{**identity,'name':'acquire_firmware_artifact','arguments':{'source_path':str(image),'expected_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'origin':'provided_binary'}},args[args.index('--home')+1])
    assert out.get('available'),out
    report={'status':'completed','summary':'scripted import','artifacts':[{'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'} for k in ('artifact','provenance')],'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
else:
    if pathlib.Path('DEFECTS.json').exists(): time.sleep(20)
    # An intentionally unrelated, unqualified model. The real case gate rejects it.
    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')
'''.replace('PACKAGE_PARENT',repr(str(Path(generative_driver.__file__).resolve().parents[1]))))
            try:
                run=call('start',{'goal':'Scripted revision contract, not a benchmark result','case':'tq9',
                    'executor_config':{'command':[sys.executable,str(script)]}},home)
                assignments=[]
                # Wait for each durable transition, not a total runtime guess
                # spanning several external workers and their case checks.
                for count,stage in enumerate(('acquire','interpret','interpret'),1):
                    until=time.monotonic()+10
                    while True:
                        events=call('events',run,home)['events']
                        assignments=[e['data'] for e in events if e['kind']=='stage.assigned']
                        if len(assignments)>=count: break
                        state=call('status',run,home)
                        if state['status'] in ('failed','blocked','cancelled','completed') or time.monotonic()>=until:
                            self.fail(f'Waiting for assignment {count} ({stage}); '
                                      f'last event: {events[-1] if events else None}; '
                                      f'result: {call("result",run,home)}')
                        time.sleep(.03)
                self.assertEqual([a['stage'] for a in assignments[:3]],['acquire','interpret','interpret'],call('result',run,home))
                old=call('tools',{**run,'assignment_id':assignments[1]['id']},home)
                self.assertFalse(old['ok'])
                self.assertIn('inactive',old['reason'])
                result=call('result',run,home)
                self.assertEqual(result['progress']['repairs'],1)
                self.assertEqual(result['progress']['maintenance_cycles'],0)
                self.assertEqual(result['progress']['revision'],1)
            finally:
                call('cancel',run,home)
                call('shutdown',{},home)


if __name__=='__main__':
    unittest.main()

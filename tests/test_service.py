import tempfile
import time
import unittest
import threading
import socketserver
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path
import generative_driver


class ServiceTests(unittest.TestCase):
    def test_uncertain_effect_refuses_another_write_but_allows_read_only_observation(self):
        """A localhost scripted device exercises real managed TCP dispatch, no hardware."""
        from generative_driver.client import call
        received=[]
        class Device(socketserver.StreamRequestHandler):
            def handle(self):
                while True:
                    command=self.rfile.readline()
                    if not command:return
                    received.append(command)
                    if command==b'ID?\n': self.wfile.write(b'DEMO-42\n');self.wfile.flush()
                    else:return  # A write may have happened, but its reply was lost.
        server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Device)
        server.daemon_threads=True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with tempfile.TemporaryDirectory() as home:
            source=Path(home)/'owned.bin';source.write_bytes(b'contract fixture')
            fake=Path(home)/'uncertain_agent.py'
            fake.write_text('''import json,sys,pathlib,hashlib
sys.path.insert(0,PACKAGE_PARENT)
from generative_driver.client import call
from generative_driver.toolkit import resources_root
prompt=sys.stdin.read()
if not prompt.startswith('Execute exactly'):
 model=json.loads((resources_root()/'bench/cases/setup-smoke/model.json').read_text())
 model['channel']={'type':'tcp'};model['operations']['measure']['effect']='write'
 pathlib.Path('model.json').write_text(json.dumps(model))
 sys.exit(0)
task=json.loads(prompt.split('\\n',1)[1])
args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
home=args[args.index('--home')+1]
identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
def invoke(name,arguments):return call('tool',{**identity,'name':name,'arguments':arguments},home)
if task['stage']=='acquire':
 image=pathlib.Path(task['inputs'][0])
 out=invoke('acquire_firmware_artifact',{'source_path':str(image),'expected_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'origin':'provided_binary'})
 r={'status':'completed','summary':'Scripted import','artifacts':[{'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'} for k in ('artifact','provenance')],'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
else:
 model=next(str(p.parent) for p in pathlib.Path('.').rglob('model.json'))
 first=invoke('interface_execute',{'model_dir':str(pathlib.Path(model).resolve()),'operation':'measure'})
 second=invoke('interface_execute',{'model_dir':str(pathlib.Path(model).resolve()),'operation':'measure'})
 observation=invoke('interface_execute',{'model_dir':str(pathlib.Path(model).resolve()),'operation':'identify'})
 print(json.dumps({'type':'fixture.results','first':first,'second':second,'observation':observation}))
 r={'status':'blocked','summary':'Uncertain scripted device response','artifacts':[],'checks':[],'unresolved':['operator must reconcile']}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(r)}}))
'''.replace('PACKAGE_PARENT',repr(str(Path(generative_driver.__file__).resolve().parents[1]))))
            try:
                run=call('start',{'goal':'Managed uncertainty contract','inputs':{'image.bin':str(source)},
                    'binding':{'host':'127.0.0.1','port':server.server_address[1]},'effects':['write'],
                    'executor_config':{'command':[sys.executable,str(fake)]}},home)
                until=time.monotonic()+15
                while time.monotonic()<until:
                    result=call('result',run,home)
                    if result['status'] in ('failed','blocked'):break
                    time.sleep(.03)
                self.assertEqual(result['stage'],'probe',result)
                self.assertTrue(result['uncertain_effect'],result)
                rows=[json.loads(line) for record in result['worker_reports'] for line in Path(record['transcript']).read_text().splitlines()]
                fixture=next(row for row in rows if row.get('type')=='fixture.results')
                self.assertFalse(fixture['first']['ok'])
                self.assertIn('uncertain',fixture['second']['reason'])
                self.assertTrue(fixture['observation']['ok'],fixture)
                self.assertEqual(received.count(b'T\n'),1,received)
            finally:
                call('shutdown',{},home)
                server.shutdown();server.server_close()

    def test_observed_acquisition_is_accepted_but_altered_evidence_cannot_resume(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as home:
            source = Path(home)/'source.bin'
            source.write_bytes(b'owned scripted contract image')
            fake = Path(home)/'agent.py'
            fake.write_text('''import json,sys,pathlib,hashlib
sys.path.insert(0,SOURCE_ROOT)
from generative_driver.client import call
prompt=sys.stdin.read()
print(json.dumps({'type':'fixture.prompt','prompt':prompt}))
task=json.loads(prompt.split('\\n',1)[1]) if prompt.startswith('Execute exactly') else {'stage':'interpret'}
r={'status':'blocked','summary':'Scripted interpretation stop','artifacts':[],'checks':[],'unresolved':['no actual model inference']}
if task['stage']=='acquire':
    encoded=next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args='))
    args=json.loads(encoded)
    home=args[args.index('--home')+1]; run=args[args.index('--run')+1]; assignment=args[args.index('--assignment')+1]
    image=pathlib.Path(task['inputs'][0]); sha=hashlib.sha256(image.read_bytes()).hexdigest()
    out=call('tool',{'run_id':run,'assignment_id':assignment,'name':'acquire_firmware_artifact','arguments':{'source_path':str(image),'expected_sha256':sha,'origin':'provided_binary'}},home)
    assert out.get('available'),out
    r={'status':'completed','summary':'Imported the supplied bytes','artifacts':[{'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'} for k in ('artifact','provenance')],'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(r)}}))
'''.replace('SOURCE_ROOT',repr(str(Path(generative_driver.__file__).resolve().parents[1]))))
            try:
                goal='Import a scripted firmware fixture: PRIVATE-OPERATOR-GOAL'
                run=call('start',{'goal':goal,'inputs':{'image.bin':str(source)},
                    'executor_config':{'command':[sys.executable,str(fake)]}},home)
                until=time.monotonic()+10
                while time.monotonic()<until:
                    result=call('result',run,home)
                    if result['status'] in ('failed','blocked'):break
                    time.sleep(.03)
                details = [Path(r['stderr']).read_text() for r in result['worker_reports'] if Path(r['stderr']).exists()]
                self.assertEqual(result['status'],'blocked',details or result)
                self.assertEqual([h['stage'] for h in result['accepted_handoffs']],['acquire'])
                last=result['worker_reports'][-1]
                self.assertEqual(last['stage'],'interpret',result['reason'])
                prompt=next(__import__('json').loads(line)['prompt'] for line in Path(last['transcript']).read_text().splitlines() if __import__('json').loads(line).get('type')=='fixture.prompt')
                self.assertNotIn('PRIVATE-OPERATOR-GOAL',prompt)
                self.assertNotIn('provenance.json',prompt)
                artifact=Path(result['accepted_handoffs'][0]['artifacts'][0]['path'])
                artifact.write_bytes(b'changed after acceptance')
                resumed=call('resume',run,home)
                self.assertFalse(resumed['ok'])
                self.assertIn('Stale handoff',resumed['reason'])
            finally:
                call('shutdown',{},home)

    def test_cancellation_remains_responsive_during_a_slow_external_tool(self):
        from generative_driver.client import call
        entered, release = threading.Event(), threading.Event()
        class Source(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(10)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'scripted download contract')
            def log_message(self,*args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Source)
        server_thread = threading.Thread(target=server.serve_forever,daemon=True)
        server_thread.start()
        with tempfile.TemporaryDirectory() as home:
            worker = None
            try:
                run = call('start', {'goal':'Scripted slow download contract',
                    'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}},home=home)
                until = time.monotonic()+5
                while time.monotonic()<until:
                    events = call('events',run,home=home)['events']
                    assignments = [e['data'] for e in events if e['kind']=='stage.assigned']
                    if assignments: break
                    time.sleep(.02)
                worker = threading.Thread(target=call,args=('tool',{**run,'assignment_id':assignments[0]['id'],
                    'name':'acquire_datasheet','arguments':{'url':f'http://127.0.0.1:{server.server_port}/data','expected_sha256':'0'*64}},home),daemon=True)
                worker.start()
                self.assertTrue(entered.wait(5),'Tool did not reach the external scripted source')
                started = time.monotonic()
                self.assertEqual(call('cancel',run,home=home)['status'],'cancelled')
                self.assertLess(time.monotonic()-started,2)
                reconciliation=call('respond',{**run,'observation':{'source':'scripted test','effect_resolution':'confirmed_safe'}},home)
                self.assertFalse(reconciliation['ok'])
                self.assertIn('stopping',reconciliation['reason'])
            finally:
                release.set()
                if worker: worker.join(10)
                call('shutdown',{},home=home)
                server.shutdown()
                server.server_close()

    def test_background_run_is_visible_after_the_start_client_disconnects(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as home:
            try:
                started = call('start', {'goal':'Classify an unavailable input', 'executor':'codex',
                    'executor_config':{'command':['no-such-runtime']}}, home=home)
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    status = call('status', {'run_id':started['run_id']}, home=home)
                    if status['status'] == 'blocked':
                        break
                    time.sleep(.03)
                self.assertEqual(status['status'], 'blocked')
                self.assertEqual(call('result', {'run_id':started['run_id']}, home=home)['run_id'], started['run_id'])
            finally:
                call('shutdown', {}, home=home)


if __name__ == '__main__':
    unittest.main()

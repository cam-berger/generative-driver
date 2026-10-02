"""Synthetic TCP silicon and external scripted worker; never native calibration."""
import copy
import json
from pathlib import Path
import socketserver
import threading


def model(semantic=False):
    from generative_driver.benchmark import case_root
    result = json.loads((case_root()/'cases/setup-smoke/model.json').read_text())
    result['schema'] = 'interface-model/4'
    result['operations']['measure']['outputs']['temperature'] = {'from':'reply','kind':'kv_number','key':'T:', 'scale':.1 if semantic else 1, 'unit':'degC'}
    for name, command, effect in [('arm','A','write'),('disarm','D','write'),('set_duty','W ','actuate')]:
        result['operations'][name] = {'effect':effect,'parameters':{},'outputs':{},'steps':[{
            'op':'exchange','tx':[{'text':command+'\n'}],
            'rx':{'mode':'until','delimiter_hex':'0a','max_bytes':32},'timeout_ms':500,
            'capture':'reply','expect':{'equals_hex':'4f4b0a'}}]}
    result['operations']['set_duty']['parameters']={'duty':{'type':'integer','minimum':0,'maximum':1000}}
    result['operations']['set_duty']['steps'][0]['tx']=[{'text':'W '},{'arg':'duty','format':'ascii'},{'text':'\n'}]
    for name, operation in result['operations'].items():
        if name != 'measure':
            capture=operation['steps'][0]['capture']
            operation['steps'][0]['expect']={'final_line_equals':'DEMO-42' if name=='identify' else 'OK','reject_line_prefix':['ERR']}
            operation['outputs']={'ack':{'from':capture,'kind':'utf8','strip':True}}
    result['provenance'] = [{'item':'operations.'+name,'source':'synthetic scripted fixture'} for name in result['operations']]
    return result


def capabilities():
    return {'schema':'benchmark-capabilities/2','tasks':{
        task:{'operation':operation,'constants':{},'inputs':{'duty':{'parameter':'duty','kind':'copy'}} if task=='set_duty' else {},
              'outputs':{'temperature':{'output':'temperature','unit':'degC'}} if task=='temperature' else {}}
        for task,operation in [('temperature','measure'),('arm','arm'),('set_duty','set_duty'),('disarm','disarm')]}}


def replies():
    return {'schema':'interface-replies/1','operations':{
        name:{'steps':[[reply]]} for name,reply in [('identify','DEMO-42'),('measure','T:20'),('arm','OK'),('set_duty','OK'),('disarm','OK')]}}


class Silicon:
    def __init__(self, image):
        self.semantic = b'semantic' in Path(image).read_bytes()
        self.temperature, self.duty, self.armed = 20, 0, False
        self.monitor_available = True
        self.running = False
        self.commands = []
        self.paused_requests = []
        owner = self
        class UART(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    command=line.decode().strip()
                    if not owner.running:
                        owner.paused_requests.append(command)
                        return
                    if command=='ID?': reply='DEMO-42'
                    elif command=='T': reply='T:'+str(42 if getattr(owner,'wrong_hidden',False) and owner.temperature == -7 else owner.temperature*(10 if owner.semantic else 1))
                    elif command=='A': owner.armed=True; reply='OK'
                    elif command=='D': owner.armed=False; owner.duty=0; reply='OK'
                    elif command.startswith('W '):
                        owner.duty=int(command[2:]) if owner.armed else 0
                        reply='OK' if owner.armed else 'ERR'
                    else: reply='ERR'
                    self.wfile.write((reply+'\n').encode());self.wfile.flush()
        class Monitor(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'(device) ');self.wfile.flush()
                line=self.rfile.readline().decode(errors='ignore').lstrip('\x00\x01\x03').strip()
                if not owner.monitor_available: return
                owner.commands.append(line)
                if line in ('start','pause'): owner.running = line == 'start'
                if line=='reset': owner.duty=0;owner.armed=False
                if line.startswith('temp '): owner.temperature=int(line.split()[1])
                value=getattr(owner,'monitor_error',None) or {'compare':owner.duty,'reload':999,'temperature':owner.temperature}.get(line,'')
                self.wfile.write((line+'\r\n'+str(value)+'\r\n(device) ').encode());self.wfile.flush()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address=True
            daemon_threads=True
        self.servers=[Server(('127.0.0.1',0),handler) for handler in (UART,Monitor)]
        for server in self.servers: threading.Thread(target=server.serve_forever,daemon=True).start()
    def close(self):
        if getattr(self,'closed',False):return
        self.closed=True
        for server in self.servers:server.shutdown();server.server_close()
    def session(self, recipe, private):
        from generative_driver.benchmark_support.native import NativeSession
        Path(private).mkdir(parents=True,exist_ok=True)
        result=NativeSession.attach({'binding':{'host':'127.0.0.1','port':self.servers[0].server_address[1]},
            'monitor_port':self.servers[1].server_address[1],'private_dir':str(private)},recipe)
        result.stop=self.close
        return result


WORKER = r'''
import hashlib,json,pathlib,sys,urllib.request
sys.path[:0]=[PACKAGE_PARENT,FIXTURE_PARENT]
from tq9_workflow_fixture import model,capabilities,replies
prompt=sys.stdin.read()
if not prompt.startswith('Execute exactly'):
    pathlib.Path('model.json').write_text(json.dumps(model(b'semantic' in pathlib.Path('image.bin').read_bytes())))
    pathlib.Path('replies.json').write_text(json.dumps(replies()))
    sys.exit(0)
task=json.loads(prompt.split('\n',1)[1]);stage=task['stage']
args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args='))) if task['allowed_tools'] else []
identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]} if args else {}
def call(name,arguments):
    body=json.dumps({'method':'tool','params':{**identity,'name':name,'arguments':arguments}}).encode()
    result=json.load(urllib.request.urlopen(urllib.request.Request(SERVICE_URL,data=body,headers={'Content-Type':'application/json'})))
    if 'fixture_error' in result:raise RuntimeError(result['fixture_error'])
    return result
artifacts=[];status='completed'
if stage=='acquire':
    result=call('acquire_firmware_artifact',task['context']);artifacts=[result['artifact'],result['provenance']]
elif stage=='probe':
    model_dir=task['context']['model_dir']
    rejected=call('interface_execute',{'model_dir':model_dir,'operation':'measure','parameters':{'unexpected':1}})
    assert not rejected['ok']
    measured=call('interface_execute',{'model_dir':model_dir,'operation':'measure','parameters':{}})
    assert measured['ok'], 'live interface_execute failed'
    probed=call('probe_run',{'model_dir':model_dir,'operation':'measure','parameters':{},'n':1})
    assert probed['ok'], 'live probe_run failed'
    pathlib.Path('capabilities.json').write_text(json.dumps(capabilities()));artifacts=['capabilities.json']
elif stage=='emit':
    result=call('emit_package',task['context']);assert result['ok'];artifacts=[result['package_dir']]
elif stage in ('reuse','maintain'):
    package=task['inputs'][0]
    sequence=[('measure',{}),('arm',{}),('set_duty',{'duty':370}),('disarm',{})] if stage=='reuse' else [('measure',{})]
    results=[call('benchmark_package_execute',{'package_dir':package,'operation':op,'parameters':params}) for op,params in sequence]
    if stage=='maintain':
        result=results[0]
        drift=not result['ok'] or abs(result['outputs']['temperature']-result['observation']['temperature_reference'])>.01
        claim={'schema':'benchmark-maintenance-claim/1','claim':'drift' if drift else 'unchanged','evidence_ids':[str(result['evidence_id'])]}
        pathlib.Path('maintenance.json').write_text(json.dumps(claim));artifacts=['maintenance.json'];status='needs_revision' if drift else 'completed'
def digest(path):
    path=pathlib.Path(path)
    if path.is_file():return hashlib.sha256(path.read_bytes()).hexdigest()
    from generative_driver.configurator import digest
    return digest(path)
report={'status':status,'summary':'scripted-contract-fixture','artifacts':[{'path':str(p),'sha256':digest(p),'kind':'evidence'} for p in artifacts], 'checks':[],'unresolved':[]}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
'''

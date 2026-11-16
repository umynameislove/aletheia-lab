import concurrent.futures
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

root=Path.cwd(); ev=root/'delivery/kien-r418/evidence'; stage=ev/'k14-staging'; dst=ev/'k14-staging-closeout'; dst.mkdir(exist_ok=True)
state=json.loads((stage/'state-public.json').read_text()); owner=state['run_id']; tool=root/'delivery/kien-r418/staging/k14-local'
report=dst/sys.argv[1];report.mkdir(); done=threading.Event()

def docker(*args):
    r=subprocess.run(['docker',*args],capture_output=True,timeout=28)
    assert r.returncode==0,(args[0],r.stderr.decode(errors='replace')[-400:]);return r.stdout

def owned(role):
    cid=state['containers'][role]; item=json.loads(docker('inspect',cid))[0]
    assert item['Name']=='/'+state['project']+'-'+role and item['Config']['Labels']['com.aletheia.k14.owner']==owner
    assert item['State']['Running'],role
    return cid,item

ids={role:owned(role)[0] for role in state['containers']}
assert json.loads(docker('network','inspect',state['network']))[0]['Internal']
cmd=['docker','run','--rm','--pull=never','--label','com.aletheia.k14.owner='+owner,'--name',state['project']+'-'+report.name,'--network',state['network'],'--memory','384m','--cpus','0.75','--pids-limit','64','--ulimit','nofile=256:256','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--tmpfs','/tmp:rw,size=67108864','--mount',f'type=bind,src={tool},dst=/opt/k14,readonly','--mount',f'type=bind,src={ev},dst=/evidence','--entrypoint','/opt/venv/bin/python']
for role,d in [('configs','/run/config'),('clients','/run/load'),('metrics','/run/metrics')]:
    v=state['volumes'][role];assert json.loads(docker('volume','inspect',v))[0]['Labels']['com.aletheia.k14.owner']==owner
    cmd.extend(['--mount',f'type=volume,src={v},dst={d},readonly'])
script='''printf '%s\\n' "$$"
i=0
while [ "$i" -lt 1000 ]; do
 read -r uptime _ < /proc/uptime
 read -r cpu < /sys/fs/cgroup/cpuacct/cpuacct.usage
 read -r ram < /sys/fs/cgroup/memory/memory.usage_in_bytes
 read -r pids < /sys/fs/cgroup/pids/pids.current
 printf '%s %s %s %s\n' "$uptime" "$cpu" "$ram" "$pids"
 sleep .25
 i=$((i+1))
done'''

def monitor(role):
    p=subprocess.Popen(['docker','exec','--user','0',ids[role],'sh','-c',script],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    pid=None
    try:
        pid=int(p.stdout.readline().strip())
        with (report/(role+'-resource.jsonl')).open('w',encoding='utf-8') as f:
            while not done.is_set():
                line=p.stdout.readline()
                if not line:break
                t,cpu,ram,pids=line.split(); f.write(json.dumps({'monotonic':float(t),'epoch':time.time(),'cpu':{'usage_usec':int(cpu)//1000},'memory_current':int(ram),'pids':int(pids)})+'\n');f.flush()
    finally:
        if pid is not None:
            guard='test "$1" -gt 1 && tr "\\000" " " < "/proc/$1/cmdline" | grep -q "cpuacct.usage" && kill -TERM "$1"'
            subprocess.run(['docker','exec','--user','0',ids[role],'sh','-c',guard,'guard',str(pid)],capture_output=True,timeout=6)
        p.terminate();p.wait(5)

def restart(process):
    limit=time.monotonic()+65
    while not (report/'sessions-ready').exists():
        if process.poll() is not None:return
        if time.monotonic()>limit: raise TimeoutError('sessions-ready wait')
        time.sleep(.25)
    cid,before=owned('api0'); t=time.time();docker('restart','--time','12',cid); _,after=owned('api0')
    assert before['State']['StartedAt']!=after['State']['StartedAt']
    (report/'api0-restarted.json').write_text(json.dumps({'only_api0_restarted':True,'container':cid,'start_before':before['State']['StartedAt'],'start_after':after['State']['StartedAt'],'restart_wall_seconds':time.time()-t}),encoding='utf-8')

(report/'inputs.json').write_text(json.dumps({'source_identity':state['release_identity'],'head':state['head'],'image_id':state['image_id'],'tools':{x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in tool.glob('*.py')},'container_ids':ids,'generator_cpu_quota':.75,'generator_memory_bytes':384*1024**2,'generator_fd_soft_limit':256,'tested_on_internal_network':True},indent=2),encoding='utf-8')
with concurrent.futures.ThreadPoolExecutor(3) as pool,(report/'console.log').open('wb') as log:
    polls=[pool.submit(monitor,k) for k in ('pg','idp')]
    time.sleep(.8)
    previous=['transactions' if sys.argv[2]=='transactions' else '/evidence/k14-staging-closeout/'+sys.argv[2]+'/result.json'] if len(sys.argv)==3 else []
    with subprocess.Popen([*cmd,state['image_id'],'-B','/opt/k14/verify_remaining.py','/evidence/k14-staging-closeout/'+report.name,*previous],stdout=log,stderr=subprocess.STDOUT) as p:
        watcher=pool.submit(restart,p) if not previous else None
        try:
            code=p.wait(230)
            if watcher:watcher.result()
        finally:
            done.set()
        for f in polls:f.result()
(report/'exit.json').write_text(json.dumps({'exit_code':code}),encoding='utf-8');print('exit',code,flush=True);raise SystemExit(code)

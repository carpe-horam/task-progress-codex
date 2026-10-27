import subprocess,json,datetime,sys,re
from pathlib import Path
out=Path('/tmp/task-progress-process-leak-audit')
rows={}
for line in subprocess.check_output(['ps','-axo','pid=,ppid=,stat=,%cpu=,rss=,time=,lstart=,command='],text=True).splitlines():
 s=line.split(None,11)
 if len(s)==12 and 'python' in s[11].split()[0].lower() and 'server.py' in s[11] and ' -c ' not in s[11]:
  rows[int(s[0])]={'pid':int(s[0]),'ppid':int(s[1]),'state':s[2],'cpu_percent':float(s[3]),'rss_kib':int(s[4]),'cpu_time':s[5],'started_at':' '.join(s[6:11])}
pids=[5314,*rows]
p=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',','.join(map(str,pids)),'-FpcftDn'],capture_output=True,text=True,timeout=20)
records=[];pid=None;record=None
for line in p.stdout.splitlines():
 if not line: continue
 k,v=line[0],line[1:]
 if k=='p': pid=int(v);record=None
 elif k=='f': record={'pid':pid,'fd':v};records.append(record)
 elif record is not None and k in ('t','D','n'):record[{'t':'type','D':'device','n':'name'}[k]]=v
plain=subprocess.check_output(['/usr/sbin/lsof','-nP','-a','-p',','.join(map(str,pids))],text=True)
for line in plain.splitlines():
 m=re.match(r'^\S+\s+(\d+)\s+\S+\s+(\S+)\s+PIPE\s+(0x[0-9a-f]+)\s+\d+\s+.*?(->0x[0-9a-f]+)',line)
 if m:
  match=next((r for r in records if r['pid']==int(m[1]) and r['fd']==m[2]),None)
  if match is not None: match['device']=m[3]
cwd={r['pid']:r.get('name','') for r in records if r['fd']=='cwd'}
selected=[]
for pid,row in rows.items():
 if '/plugins/cache/task-progress-codex/task-progress/' not in cwd.get(pid,''):continue
 pipes=[r for r in records if r['pid']==pid and r.get('type')=='PIPE' and r['fd'] in ('0','1','2','0r','1w','2w')]
 for pipe in pipes:
  peer=(pipe.get('name') or '').removeprefix('->')
  pipe['peer_fds']=[{'pid':r['pid'],'fd':r['fd']} for r in records if r.get('type')=='PIPE' and r.get('device')==peer]
 selected.append({**row,'cwd':cwd[pid],'stdio':pipes})
result={'captured_at':datetime.datetime.now().astimezone().isoformat(),'processes':selected,'count':len(selected),'rss_mib':round(sum(r['rss_kib'] for r in selected)/1024,2),'cpu_percent':sum(r['cpu_percent'] for r in selected),'all_three_stdio_connected_to_parent':all(len(r['stdio'])==3 and all(any(p['pid']==r['ppid'] for p in fd['peer_fds']) for fd in r['stdio']) for r in selected)}
(out/(sys.argv[1]+'.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='processes'},indent=2))
print(json.dumps([{'pid':r['pid'],'state':r['state'],'rss_kib':r['rss_kib'],'cpu_time':r['cpu_time'],'parent_connected_fds':sum(any(p['pid']==r['ppid'] for p in fd['peer_fds']) for fd in r['stdio'])} for r in selected],indent=2))

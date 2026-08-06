#!/usr/bin/env python3
"""N3 read-only preflight and 60-second zero-command witness."""
from __future__ import annotations
import json, shutil, signal, subprocess, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS, bounded_tag_read
from fusion_session import parse_fields, resolve_fusion_port, u32_delta
from listener_array_run import wait_listener_preflight

ROOT=Path(__file__).resolve().parents[2]
COLLECTOR=ROOT/'B306_Part/host/listener_array_collector.py'
def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
def dump(p,x): p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def rate(rows,key):
    if len(rows)<2:return None
    dt=(int(rows[-1][1][key],0)-int(rows[0][1][key],0))/1e6
    return (len(rows)-1)/dt if dt>0 else None

def main():
    root=Path(sys.argv[1]); root.mkdir(exist_ok=False)
    ld=root/'listener_array'; clog=(root/'listener.stdout.log').open('x',encoding='utf-8',buffering=1)
    cdc=(root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1)
    proc=subprocess.Popen([sys.executable,str(COLLECTOR),'--out-dir',str(ld),'--duration','420',
        '--baud','460800','--require-kind','LSTAT','--require-kind','LPD','--require-kind','LRD'],
        cwd=ROOT,stdout=clog,stderr=subprocess.STDOUT,text=True)
    ch=None; out={'status':'IN_PROGRESS','started':wall(),'nodes':{}}
    try:
        out['listener_preflight']=wait_listener_preflight(ld,proc,30)
        ch=ThreadedLineChannel(resolve_fusion_port(None),cdc,'FUSION',decoded_queue_records=1048576,
            backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2)
        ch.transport_mode='binary'; ch.text_pending.clear(); out['decode_guard']=decode_guard(ch,15)
        ch.send('MASTER STATUS'); dl=time.monotonic()+5
        while time.monotonic()<dl:
            master=ch.read(dl)
            if master and master.startswith('FUSION_MASTER_STATUS '):break
        else: raise RuntimeError('MASTER STATUS timeout')
        mf=parse_fields(master)
        if mf.get('marker')!='dk-fusion-imu-relay-v30' or mf.get('count')!='10' or mf.get('ready')!='10':
            raise RuntimeError(f'master/fleet mismatch: {master}')
        out['master']=master
        for node,slot in SLOTS.items():
            cfg=bounded_tag_read(ch,node,'CFG_STATUS','CFG '); cf=parse_fields(cfg['reply']['text'])
            beacon=bounded_tag_read(ch,node,'BEACON_STATUS','BEACON '); bf=parse_fields(beacon['reply']['text'])
            imu=b306_command(ch,node,'IMU STATUS','IMU '); im=parse_fields(imu['text'])
            expected={'slot':f'{slot}/12','period':'10','sync':'1','stored':'1','pslot':f'{slot}/12','pperiod':'10','psync':'1'}
            bad={k:[cf.get(k),v] for k,v in expected.items() if cf.get(k)!=v}
            if bad or bf.get('lock')!='1' or any(im.get(k)!=v for k,v in {'active':'1','rate':'200','batch':'10'}.items()):
                raise RuntimeError(f'{node} contract failure cfg={bad} beacon={bf} imu={im}')
            out['nodes'][node]={'cfg':cf,'beacon':bf,'imu':im}
        out['witness_open']={'monotonic':time.monotonic(),'wall':wall()}; print('=== N3 ZERO-COMMAND WITNESS OPEN — 60 s ===',flush=True)
        start=out['witness_open']['monotonic']; end=start+60; rows=[]
        while time.monotonic()<end:
            line=ch.read(min(end,time.monotonic()+.5))
            if line: rows.append((time.monotonic(),line))
        out['witness_close']={'monotonic':time.monotonic(),'wall':wall()}; print('=== N3 ZERO-COMMAND WITNESS CLOSED ===',flush=True)
        by=defaultdict(lambda:defaultdict(list))
        for t,line in rows:
            f=parse_fields(line); n=f.get('name')
            if n not in SLOTS:continue
            for kind in ('UWB','IMU','QUEUE'):
                if line.startswith('FUSION_'+kind+' '):by[n][kind].append((t,f))
        metrics={}
        for n in SLOTS:
            u=by[n]['UWB']; i=by[n]['IMU']; q=by[n]['QUEUE']
            samples=sum(int(f.get('n','0'),0) for _,f in i)
            irate=None
            if len(i)>=2:
                first=int(i[0][1]['base_us'],0); lf=i[-1][1]
                offs=[int(x.split(',',1)[0],0) for x in lf.get('samples','').split(';') if x]
                last=int(lf['base_us'],0)+max(offs or [0]); span=(last-first)/1e6
                irate=(samples-1)/span if span>0 else None
            qd={k:(u32_delta(int(q[0][1].get(k,'0'),0),int(q[-1][1].get(k,'0'),0)) if len(q)>=2 else None)
                for k in ('q_drop_imu','q_drop_uwb','q_drop_ctl')}
            sf=sum(f.get('sf_valid')=='1' for _,f in u); hist=Counter(str(int(f.get('valid','0'),0).bit_count()) for _,f in u)
            metrics[n]={'uwb_records':len(u),'uwb_rate_hz':rate(u,'frame_us'),'imu_samples':samples,
                'imu_rate_samples_s':irate,'sf_valid':{'numerator':sf,'denominator':len(u),'value':sf/len(u) if u else None},
                'valid_link_histogram':dict(sorted(hist.items())),'q_drop_delta':qd,
                'queue_start':({k:int(q[0][1].get(k,'0'),0) for k in ('q_drop_imu','q_drop_uwb','q_drop_ctl')} if q else None)}
            if len(u)<2 or len(i)<2: raise RuntimeError(f'{n} zero/insufficient witness progress')
        out['witness']=metrics; out['disk']={'free_bytes':shutil.disk_usage(root).free,'projected_5h_bytes':22500000000}
        out['host_health']=ch.health_snapshot()
        if out['host_health']['decoded_queue_drops'] or out['host_health']['log_queue_drops'] or out['host_health']['red_markers']:
            raise RuntimeError(f'host drain failure {out["host_health"]}')
        out['status']='PASS'; return 0
    except Exception as e:
        out['status']='FAIL'; out['error']=f'{type(e).__name__}: {e}'; return 2
    finally:
        if ch: out.setdefault('host_health',ch.health_snapshot()); ch.close()
        if proc.poll() is None:proc.send_signal(signal.SIGINT)
        try:out['listener_rc']=proc.wait(timeout=30)
        except subprocess.TimeoutExpired:proc.terminate();out['listener_rc']=proc.wait(timeout=10)
        sp=ld/'summary.json'
        if sp.exists():out['listener_summary']=json.loads(sp.read_text())
        out['ended']=wall(); dump(root/'result.json',out); cdc.close();clog.close()
        print(json.dumps({'status':out['status'],'error':out.get('error'),'witness':out.get('witness'),'host_health':out.get('host_health')},indent=2),flush=True)
if __name__=='__main__':raise SystemExit(main())

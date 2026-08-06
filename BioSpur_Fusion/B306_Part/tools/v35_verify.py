#!/usr/bin/env python3
from __future__ import annotations
import json, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS
from fusion_session import parse_fields, parse_reply, resolve_fusion_port
from delivered_rate import delivered_rate

def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
def dump(p,x): p.write_text(json.dumps(x,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def main():
 root=Path(__import__('sys').argv[1]);root.mkdir(exist_ok=False)
 log=(root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1)
 out={'status':'RUNNING','started':wall(),'nodes':{},'queries':[],'silent_events':[]}
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=1048576,
  backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,15)
  counts=defaultdict(lambda:defaultdict(int));first={};last={};last_u={n:time.monotonic() for n in SLOTS};last_i=dict(last_u);silent=set()
  commands=[]
  for n in SLOTS:
   commands += [(n,'PING','PONG '),(n,'BOOT CONFIRM STATUS','BOOT CONFIRM STATUS '),(n,'IMU STATUS','IMU '),(n,'STALL STATUS','STALL '),(n,'STACKS','STACKS ')]
   commands += [(n,f'QUEUE PUB HIST={p}',f'QUEUE PUB HIST p={p} ') for p in range(4)]
   commands += [(n,'COUNTERS','CTR1 ')]
  pending=[];start=time.monotonic();end=start+600;next_send=start;next_cycle=start+120
  print(f'=== V35 P8 WINDOW OPEN — 600 s === mono={start:.6f} wall={wall()}',flush=True)
  while time.monotonic()<end:
   now=time.monotonic()
   if now>=next_cycle:
    for n in SLOTS:
     commands += [(n,'STALL STATUS','STALL '),(n,'COUNTERS','CTR1 ')]
     commands += [(n,f'QUEUE PUB HIST={p}',f'QUEUE PUB HIST p={p} ') for p in range(4)]
    next_cycle+=120
   if commands and now>=next_send:
    n,cmd,prefix=commands.pop(0);ch.send(f'{n} {cmd}')
    pending.append({'node':n,'command':cmd,'prefix':prefix,'sent':now,'deadline':now+8});next_send=now+.15
   line=ch.read(min(end,time.monotonic()+.2));now=time.monotonic()
   if line:
    f=parse_fields(line);n=f.get('name')
    if n in SLOTS:
     if line.startswith('FUSION_UWB '):
      counts[n]['uwb']+=1;last_u[n]=now;silent.discard(n);ts=int(f['frame_us'],0);first.setdefault((n,'u'),ts);last[(n,'u')]=ts
     elif line.startswith('FUSION_IMU '):
      k=int(f.get('n','0'),0);counts[n]['imu']+=k;last_i[n]=now;silent.discard(n);base=int(f['base_us'],0);offs=[int(x.split(',',1)[0],0) for x in f.get('samples','').split(';') if x];ts=base+max(offs or [0]);first.setdefault((n,'i'),base);last[(n,'i')]=ts
    rep=parse_reply(line)
    if rep:
     for q in list(pending):
      if q['node']==n and rep.text.startswith(q['prefix']):q['reply']=rep.text;q['received']=now;out['queries'].append(q);pending.remove(q);break
   for q in list(pending):
    if now>=q['deadline']:q['timeout']=True;out['queries'].append(q);pending.remove(q)
   for n in SLOTS:
    if now-last_u[n]>2 and now-last_i[n]>2 and n not in silent:
     silent.add(n);e={'node':n,'at':now,'uwb_s':now-last_u[n],'imu_s':now-last_i[n]};out['silent_events'].append(e);print(f'DATA_PLANE_SILENT node={n} threshold_s=2.0',flush=True)
   elapsed=int(now-start)
   if elapsed and elapsed%60==0 and out.get('last_tick')!=elapsed:
    out['last_tick']=elapsed;print(f'V35 P8 T+{elapsed:03d}/600 uwb={sum(counts[n]["uwb"]>0 for n in SLOTS)}/10 imu={sum(counts[n]["imu"]>0 for n in SLOTS)}/10',flush=True)
  host_span_s=end-start
  for n in SLOTS:
   # Delivery is a host-window quantity. Device timestamps are retained only
   # as cadence diagnostics; a stale attachment record must not stretch the
   # formal observation window (O3, 2026-08-04).
   ur=delivered_rate(counts[n]['uwb'],host_span_s,[],stream='uwb',max_rate_hz=1000/120)
   ir=delivered_rate(counts[n]['imu'],host_span_s,[],stream='imu',max_rate_hz=200)
   out['nodes'][n]={'uwb_records':counts[n]['uwb'],'uwb_rate_hz':ur.delivered_rate_hz,
                    'imu_samples':counts[n]['imu'],'imu_rate_hz':ir.delivered_rate_hz,
                    'rate_flags':list(ur.flags+ir.flags)}
  out['host_health']=ch.health_snapshot();out['status']='PASS' if all(v['uwb_records']>1 and v['imu_samples']>1 for v in out['nodes'].values()) else 'FAIL'
  print(f'=== V35 P8 WINDOW CLOSED === mono={time.monotonic():.6f} wall={wall()}',flush=True)
 except KeyboardInterrupt:
  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'
 except BaseException as exc:
  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise
 finally:
  # The writer runs on every exit path, so a status left at RUNNING would make
  # a stopped run look live. No exit path may leave RUNNING on disk.
  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')
  out['ended']=wall();dump(root/'result.json',out);ch.close();log.close()
 return 0 if out['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())

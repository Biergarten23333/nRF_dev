#!/usr/bin/env python3
import json,sys,time
from collections import defaultdict
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields,resolve_fusion_port
NODES=('BSF3C79','BSF44AD')
root=Path(sys.argv[1]);root.mkdir(exist_ok=False);out={'duration_s':60,'nodes':{}}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=262144,
  backlog_red_records=32768,raw_backlog_red_bytes=32768,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();start=time.monotonic();end=start+60
  stats=defaultdict(lambda:defaultdict(lambda:{'count':0,'first':None,'last':None}))
  while time.monotonic()<end:
   line=ch.read(end)
   if not line:continue
   f=parse_fields(line);n=f.get('name');kind=None
   if n in NODES and line.startswith('FUSION_UWB '):kind='uwb'
   elif n in NODES and line.startswith('FUSION_IMU '):kind='imu'
   if kind:
    now=time.monotonic();s=stats[n][kind];s['count']+=1;s['first']=now if s['first'] is None else s['first'];s['last']=now
  for n in NODES:
   out['nodes'][n]={}
   for kind in ('uwb','imu'):
    s=stats[n][kind];span=(s['last']-s['first']) if s['count']>1 else None
    out['nodes'][n][kind]={**s,'span_s':span,'rate_hz':((s['count']-1)/span if span else 0)}
  out['status']='COMPLETE';out['started_monotonic']=start;out['ended_monotonic']=time.monotonic()
 finally:
  out['health']=ch.health_snapshot();ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

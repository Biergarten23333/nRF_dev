#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS
from fusion_session import parse_fields, resolve_fusion_port

root=Path(sys.argv[1]); root.mkdir(exist_ok=False)
out={"status":"RUNNING","nodes":{}}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=131072,
  backlog_red_records=16384,raw_backlog_red_bytes=16384,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,20)
  ch.send('SPACING ON');deadline=time.monotonic()+45
  while time.monotonic()<deadline:
   line=ch.read(deadline)
   if line and line.startswith('FUSION_SPACING '):
    f=parse_fields(line)
    if f.get('state')=='APPLIED' and f.get('mode')=='ON' and f.get('applied_us')=='5000':
     out['spacing']=f;break
  for node in SLOTS:
   row={};out['nodes'][node]=row
   try: row['ping']=b306_command(ch,node,'PING','PONG ')
   except Exception as e: row['ping_error']=f'{type(e).__name__}: {e}'
   try: row['imgstat']=b306_command(ch,node,'BOOT CONFIRM STATUS','BOOT CONFIRM STATUS ')
   except Exception as e: row['imgstat_error']=f'{type(e).__name__}: {e}'
   ch.send(f'{node} STALL READ');deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    line=ch.read(deadline)
    if line and line.startswith(f'FUSION_STALL_READ name={node} '):
     row['stall_read']=parse_fields(line);break
   if 'stall_read' not in row: row['stall_read_error']='timeout'
  out['status']='COMPLETE'
 except KeyboardInterrupt:
  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'
 except BaseException as exc:
  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise
 finally:
  # The writer runs on every exit path, so a status left at RUNNING would make
  # a stopped run look live. No exit path may leave RUNNING on disk.
  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')
  out['host_health']=ch.health_snapshot();ch.close()
  (root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

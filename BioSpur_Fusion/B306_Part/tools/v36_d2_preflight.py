#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port

root=Path(sys.argv[1]);root.mkdir(exist_ok=False);out={'status':'RUNNING','started':time.time()}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=131072,backlog_red_records=16384,raw_backlog_red_bytes=16384,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,20)
  deadline=time.monotonic()+120;last_send=0;ready=None
  while time.monotonic()<deadline:
   now=time.monotonic()
   if now-last_send>=5:ch.send('MASTER STATUS');ch.send('LIST');last_send=now
   line=ch.read(min(deadline,now+1))
   if not line:continue
   if line.startswith('FUSION_MASTER_STATUS '):
    f=parse_fields(line);out['last_master']=f
    if f.get('marker')=='dk-fusion-imu-relay-v31' and int(f.get('ready','0'))==10:ready=f;break
  if ready is None:raise RuntimeError(f'fleet did not establish 10/10: {out.get("last_master")}')
  ch.send('SPACING ON');deadline=time.monotonic()+45;status=None;last_send=0
  while time.monotonic()<deadline:
   now=time.monotonic()
   if now-last_send>=2:ch.send('SPACING STATUS');last_send=now
   line=ch.read(min(deadline,now+1))
   if line and line.startswith(('FUSION_SPACING_STATUS ', 'FUSION_SPACING ')):
    f=parse_fields(line);out['spacing_attempt']=f
    spacing_us=f.get('spacing_us', f.get('applied_us'))
    applied=f.get('state') in (None, 'APPLIED')
    if f.get('mode')=='ON' and spacing_us=='5000' and applied and int(f.get('generation','0'))>0:
     status=f;break
  if status is None:raise RuntimeError(f'spacing contract failed: {out.get("spacing_attempt")}')
  out['status']='PASS';out['elapsed_s']=time.time()-out['started'];out['health']=ch.health_snapshot()
 except KeyboardInterrupt:
  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'
 except BaseException as exc:
  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise
 finally:
  # The writer runs on every exit path, so a status left at RUNNING would make
  # a stopped run look live. A raised contract failure above must read FAILED,
  # never RUNNING. No exit path may leave RUNNING on disk.
  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')
  out.setdefault('elapsed_s',time.time()-out['started'])
  ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps(out,sort_keys=True))

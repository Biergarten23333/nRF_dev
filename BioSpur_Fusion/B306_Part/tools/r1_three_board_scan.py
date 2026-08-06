#!/usr/bin/env python3
import json,sys,time
from datetime import datetime,timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields,resolve_fusion_port
TARGETS=('BSF3C79','BSF44AD','BSFAA61')
def wall():return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
out={'status':'RUNNING','duration_s':180,'targets':{n:{'events':[],'reads':[]} for n in TARGETS},'started':wall()}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=262144,
  backlog_red_records=32768,raw_backlog_red_bytes=32768,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,20)
  ch.send('LIST');end=time.monotonic()+180;pending={};last_read={}
  while time.monotonic()<end:
   now=time.monotonic()
   for n in TARGETS:
    if len(out['targets'][n]['reads'])<3 and out['targets'][n].get('bridge_ready') and n not in pending and now-last_read.get(n,0)>=5:
     ch.send(f'{n} STALL READ');pending[n]=now
   line=ch.read(min(end,now+1));now=time.monotonic()
   if not line:continue
   if line.startswith('FUSION_PEER '):
    f=parse_fields(line);n=f.get('name')
    if n in TARGETS:out['targets'][n]['initial_peer']=f
   for n in TARGETS:
    if line.startswith((f'FUSION_CONNECTED name={n} ',f'FUSION_BRIDGE_READY name={n} ',f'FUSION_DISCONNECTED name={n} ')):
     out['targets'][n]['events'].append({'monotonic':now,'wall':wall(),'line':line})
     if line.startswith('FUSION_CONNECTED '):out['targets'][n]['connected']=True
     if line.startswith('FUSION_BRIDGE_READY '):
      f=parse_fields(line);out['targets'][n]['bridge_ready']=True;out['targets'][n]['rssi']=f.get('rssi')
    if line.startswith(f'FUSION_STALL_READ name={n} '):
     out['targets'][n]['reads'].append({'monotonic':now,'wall':wall(),'fields':parse_fields(line),'raw':line});pending.pop(n,None);last_read[n]=now
    if line.startswith('FUSION_COMMAND_REJECT ') and f'target={n}' in line:pending.pop(n,None)
  for n,row in out['targets'].items():
   row['advertising_observed']=bool(row.get('connected') or row.get('bridge_ready'))
   row['advertising_interval']='UNAVAILABLE_DK_V31'
  out['status']='COMPLETE'
 except KeyboardInterrupt:
  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'
 except BaseException as exc:
  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise
 finally:
  # The writer runs on every exit path, so a status left at RUNNING would make
  # a stopped run look live. No exit path may leave RUNNING on disk.
  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')
  out['ended']=wall();out['health']=ch.health_snapshot();ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

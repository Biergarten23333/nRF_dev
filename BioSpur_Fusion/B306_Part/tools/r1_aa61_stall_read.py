#!/usr/bin/env python3
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port

def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
out={'status':'WAITING','started':wall(),'reconnect_observed':False,'reads':[],'events':[]}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=262144,
  backlog_red_records=32768,raw_backlog_red_bytes=32768,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,20)
  deadline=time.monotonic()+120;next_try=time.monotonic();pending=False;last_read=None
  while time.monotonic()<deadline and len(out['reads'])<3:
   now=time.monotonic()
   if not pending and now>=next_try and (last_read is None or now-last_read>=5):
    ch.send('BSFAA61 STALL READ');pending=True;next_try=now+5
   line=ch.read(min(deadline,now+1));now=time.monotonic()
   if not line: continue
   if line.startswith(('FUSION_CONNECTED name=BSFAA61 ',
                       'FUSION_BRIDGE_READY name=BSFAA61 ',
                       'FUSION_DISCONNECTED name=BSFAA61 ')):
    out['events'].append({'monotonic':now,'wall':wall(),'line':line})
    if line.startswith('FUSION_CONNECTED '):out['reconnect_observed']=True
   if line.startswith('FUSION_COMMAND_REJECT ') and 'target=BSFAA61' in line:
    pending=False
   if line.startswith('FUSION_STALL_READ name=BSFAA61 '):
    fields=parse_fields(line);out['reads'].append({'monotonic':now,'wall':wall(),
     'fields':fields,'raw':line});pending=False;last_read=now
  out['status']='PASS' if len(out['reads'])>=3 else 'UNREACHABLE'
 finally:
  out['ended']=wall();out['health']=ch.health_snapshot();ch.close()
  (root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps({'status':out['status'],'reads':len(out['reads']),
 'reconnect_observed':out['reconnect_observed']},sort_keys=True))

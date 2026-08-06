#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields,resolve_fusion_port
NODES=('BSF3C79','BSF44AD')
root=Path(sys.argv[1]);root.mkdir(exist_ok=False);out={'nodes':{n:[] for n in NODES}}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=131072,
  backlog_red_records=16384,raw_backlog_red_bytes=16384,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,20)
  for round_no in range(3):
   if round_no:time.sleep(5)
   for node in NODES:
    sent=time.monotonic();ch.send(f'{node} STALL READ');deadline=sent+8
    while time.monotonic()<deadline:
     line=ch.read(deadline)
     if line and line.startswith(f'FUSION_STALL_READ name={node} '):
      out['nodes'][node].append({'round':round_no+1,'sent':sent,'received':time.monotonic(),'fields':parse_fields(line),'raw':line});break
     if line and line.startswith('FUSION_COMMAND_REJECT ') and f'target={node}' in line:
      out['nodes'][node].append({'round':round_no+1,'sent':sent,'raw':line});break
  out['status']='COMPLETE'
 finally:
  out['health']=ch.health_snapshot();ch.close();(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

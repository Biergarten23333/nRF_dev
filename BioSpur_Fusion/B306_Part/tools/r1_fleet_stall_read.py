#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS
from fusion_session import parse_fields, resolve_fusion_port

root=Path(sys.argv[1]);root.mkdir(exist_ok=False);out={'status':'RUNNING','nodes':{}}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=131072,
  backlog_red_records=16384,raw_backlog_red_bytes=16384,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,20)
  for node in SLOTS:
   ch.send(f'{node} STALL READ');deadline=time.monotonic()+8;row=None
   while time.monotonic()<deadline:
    line=ch.read(deadline)
    if line and line.startswith(f'FUSION_STALL_READ name={node} '):
     row=parse_fields(line);row['raw']=line;break
    # dk-v32 ends a failed transaction with FUSION_STALL_READ_DONE carrying a
    # terminal reason. Matching only the value line reported that as
    # 'unreachable_or_not_ready', i.e. as silence — but the Master did answer,
    # and terminal=submit_error means the read never reached the air. Recording
    # the terminal reason keeps a diagnosable failure from reading as a dead board.
    if line and line.startswith(f'FUSION_STALL_READ_DONE name={node} '):
     f=parse_fields(line)
     if f.get('terminal')!='callback':
      row={'error':'stall_read_terminal','raw':line,**f};break
    if line and line.startswith('FUSION_COMMAND_REJECT ') and f'target={node}' in line:break
   out['nodes'][node]=row if row is not None else {'error':'no_reply_within_bound'}
  out['status']='COMPLETE'
 except KeyboardInterrupt:
  out['status']='INTERRUPTED';out['stop_reason']='KeyboardInterrupt'
 except BaseException as exc:
  out['status']='FAILED';out['stop_reason']=f'{type(exc).__name__}: {exc}';raise
 finally:
  # The writer runs on every exit path, so a status left at RUNNING would make
  # a stopped run look live. No exit path may leave RUNNING on disk.
  if out['status']=='RUNNING':out['status']='INTERRUPTED';out.setdefault('stop_reason','closeout reached without a terminal status')
  out['health']=ch.health_snapshot();ch.close()
  (root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

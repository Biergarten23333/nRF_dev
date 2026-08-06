#!/usr/bin/env python3
import json,time,sys
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields,resolve_fusion_port

root=Path(sys.argv[1]);root.mkdir(exist_ok=False);node='BSFB165';out={}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=65536,backlog_red_records=8192,raw_backlog_red_bytes=8192,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,15)
  out['before']=b306_command(ch,node,'STALL STATUS','STALL ')
  out['write']=b306_command(ch,node,'STALL LATCH TEST=35A5C0DE','STALL LATCH TEST OK ')
  out['written_readback']=b306_command(ch,node,'STALL STATUS','STALL ')
  out['reboot']=b306_command(ch,node,'REBOOT','REBOOT QUEUED ')
  deadline=time.monotonic()+60;after=None
  while time.monotonic()<deadline:
   try:
    after=b306_command(ch,node,'STALL STATUS','STALL ');break
   except Exception:time.sleep(2)
  if after is None:raise RuntimeError('post-reboot STALL STATUS timeout')
  out['after']=after
  if parse_fields(after['text']).get('test')!='35A5C0DE':raise RuntimeError('retained value lost')
  out['imu']=b306_command(ch,node,'IMU STATUS','IMU ')
  out['status']='PASS';out['host']=ch.health_snapshot()
 finally:
  ch.close()
(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

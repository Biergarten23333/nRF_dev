#!/usr/bin/env python3
import json,sys
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields,resolve_fusion_port
root=Path(sys.argv[1]);root.mkdir(exist_ok=False);out={};node='BSFB165'
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=65536,backlog_red_records=8192,raw_backlog_red_bytes=8192,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,15)
  out['stall']=b306_command(ch,node,'STALL STATUS','STALL ');out['imu']=b306_command(ch,node,'IMU STATUS','IMU ')
  out['ping']=b306_command(ch,node,'PING','PONG ');f=parse_fields(out['stall']['text']);im=parse_fields(out['imu']['text'])
  out['status']='PASS' if f.get('test')=='35A5C0DE' and im.get('active')=='1' else 'FAIL';out['host']=ch.health_snapshot()
 finally:ch.close()
(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
raise SystemExit(0 if out['status']=='PASS' else 2)

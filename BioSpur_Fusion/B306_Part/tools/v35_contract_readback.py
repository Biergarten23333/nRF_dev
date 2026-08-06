#!/usr/bin/env python3
import json,sys
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from d1_blind_disturbance import SLOTS,bounded_tag_read
from fusion_session import parse_fields,resolve_fusion_port
root=Path(sys.argv[1]);root.mkdir(exist_ok=False);out={'nodes':{}}
with (root/'fusion_cdc.log').open('x',encoding='utf-8',buffering=1) as log:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,'FUSION',decoded_queue_records=65536,backlog_red_records=8192,raw_backlog_red_bytes=8192,stall_red_s=2)
 try:
  ch.transport_mode='binary';ch.text_pending.clear();out['guard']=decode_guard(ch,15);out['master']=wait_master_status(ch)
  for n,slot in SLOTS.items():
   cfg=bounded_tag_read(ch,n,'CFG_STATUS','CFG ');beacon=bounded_tag_read(ch,n,'BEACON_STATUS','BEACON ');cf=parse_fields(cfg['reply']['text']);bf=parse_fields(beacon['reply']['text'])
   out['nodes'][n]={'slot':slot,'cfg':cf,'beacon':bf}
   assert cf.get('slot')==f'{slot}/12' and cf.get('period')=='10' and cf.get('sync')=='1' and bf.get('lock')=='1'
  out['status']='PASS';out['host']=ch.health_snapshot()
 finally:ch.close()
(root/'result.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

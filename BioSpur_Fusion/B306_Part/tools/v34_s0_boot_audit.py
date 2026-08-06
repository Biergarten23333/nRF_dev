#!/usr/bin/env python3
from __future__ import annotations
import json,time
from datetime import datetime,timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields,resolve_fusion_port
from relay8_3_t567_provision import tag_read
from relay8_tag_control import wait_master_status

BASE={"BSF3C79":7,"BSFC2CC":7,"BSF44AD":7,"BSF6C53":5,"BSF8BC4":5,"BSF1120":7,"BSF31CC":5,"BSFAA61":6,"BSFEC35":5,"BSFB165":5}
DETECTED={"BSF3C79":1,"BSFC2CC":1,"BSF44AD":1,"BSF6C53":2,"BSF8BC4":0,"BSF1120":1,"BSF31CC":0,"BSFAA61":2,"BSFEC35":1,"BSFB165":1}
def now(): return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
def read(ch,n):
 errs=[]
 for a in range(1,4):
  try:
   r=tag_read(ch,n,'CFG_STATUS','CFG ');return a,r
  except Exception as e:
   errs.append(f'{type(e).__name__}: {e}')
   if a<3:time.sleep(5)
 raise RuntimeError(f'{n} exhausted: {errs}')
def main():
 out=Path('B306_Part/logs/b306_v34_20260803/S0_BOOT_BASELINE.json'); log=out.with_suffix('.cdc.log')
 result={'started':now(),'pre_d1_source':'relay8.3 FIX2_G5 post-POR table','pre_d1':BASE,'detected_events':DETECTED,'rows':{}}
 ch=None; fh=log.open('x',encoding='utf-8',buffering=1)
 try:
  ch=ThreadedLineChannel(resolve_fusion_port(None),fh,'FUSION',decoded_queue_records=131072)
  ch.transport_mode='binary';ch.text_pending.clear();result['decode_guard']=decode_guard(ch,15);result['master']=wait_master_status(ch)
  for n in BASE:
   a,r=read(ch,n);f=parse_fields(r['reply']['text']); boot=int(f['boot'],0); delta=boot-BASE[n]; detected=DETECTED[n]
   result['rows'][n]={'attempt':a,'boot_pre_d1':BASE[n],'boot_post_d1':boot,'boot_delta':delta,'detected_events':detected,
    'miss_by_one_sided_rule':delta>0 and detected==0,'delta_exceeds_detected':delta>detected,'cfg_status':r}
  result['missed_detection_boards']=[n for n,r in result['rows'].items() if r['miss_by_one_sided_rule']]
  result['ended']=now();result['status']='PASS'
 finally:
  if ch:ch.close()
  fh.close();out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
 print(json.dumps(result['rows'],indent=2,sort_keys=True));print('MISSED',result.get('missed_detection_boards'))
if __name__=='__main__':main()

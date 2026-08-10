#!/usr/bin/env python3
"""Recovery B: reset the production Master in place; never flash firmware."""
from __future__ import annotations
import argparse, json, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import parse_fields, resolve_fusion_port
from qualify_ota_confirmation_timing import collect_list, read_ping, NODES

SNR="683234364"; TARGET="BSF1120"; MARKER="dk-fusion-imu-relay-v36"

def main():
 p=argparse.ArgumentParser();p.add_argument("--out-dir",required=True,type=Path);a=p.parse_args()
 a.out_dir.mkdir(parents=True,exist_ok=False);result={"schema":"biospur-master-restart-recovery-v1","started":datetime.now(timezone.utc).astimezone().isoformat()}
 command=["JLinkExe","-device","NRF52840_XXAA","-if","SWD","-speed","4000","-SelectEmuBySN",SNR,"-autoconnect","1"]
 with (a.out_dir/"jlink_reset.log").open("xb") as log:
  cp=subprocess.run(command,input=b"r\ng\nq\n",stdout=log,stderr=subprocess.STDOUT,check=False,timeout=30)
 result["jlink_rc"]=cp.returncode
 if cp.returncode!=0: raise SystemExit(2)
 time.sleep(3);ch=None
 with (a.out_dir/"fusion_cdc.log").open("x",encoding="utf-8",buffering=1) as log:
  try:
   ch=ThreadedLineChannel(resolve_fusion_port(None),log,"FUSION",decoded_queue_records=65536,backlog_red_records=8192,raw_backlog_red_bytes=8192,stall_red_s=1)
   ch.transport_mode="binary";ch.text_pending.clear();result["guard"]=decode_guard(ch,15)
   master=wait_master_status(ch);result["master_status"]=master
   result["marker_ok"]=parse_fields(master).get("marker")==MARKER
   deadline=time.monotonic()+180;target_seen=False
   while time.monotonic()<deadline:
    aggregate,peer_lines=collect_list(ch); peers={parse_fields(x).get("name"):parse_fields(x) for x in peer_lines}
    result["last_list"]={"aggregate":aggregate,"peers":peer_lines}
    if TARGET in peers and peers[TARGET].get("connected")=="1" and peers[TARGET].get("subscribed")=="1":target_seen=True;break
    time.sleep(1)
   result["target_reconnected"]=target_seen
   result["target_pings"]=[read_ping(ch,TARGET) for _ in range(3)] if target_seen else []
   result["all_peer_pings"]={n:read_ping(ch,n) for n in NODES}
  finally:
   if ch:ch.close()
 result["ended"]=datetime.now(timezone.utc).astimezone().isoformat();(a.out_dir/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
 return 0 if result.get("target_reconnected") and all(parse_fields(x.get("text"," ")).get("name")==TARGET for x in result["target_pings"]) else 2
if __name__=="__main__":raise SystemExit(main())

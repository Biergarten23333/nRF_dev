#!/usr/bin/env python3
"""Zero-TX passive BSFC2CC visibility diagnostic."""
import argparse, json, time
from collections import Counter
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, resolve_fusion_port

ap=argparse.ArgumentParser();ap.add_argument("--out-dir",type=Path,required=True);ap.add_argument("--duration",type=float,default=60);a=ap.parse_args()
a.out_dir.mkdir(parents=True,exist_ok=False);counts=Counter();first=None;last=None;nodes=set()
with (a.out_dir/"fusion_cdc.log").open("x",buffering=1) as log,(a.out_dir/"fusion_host_raw.cobs.bin").open("xb",buffering=0) as raw:
 ch=ThreadedLineChannel(resolve_fusion_port(None),log,"FUSION",decoded_queue_records=1048576,backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2,raw_file=raw);ch.transport_mode="binary";ch.text_pending.clear();start=time.monotonic();end=start+a.duration
 try:
  while time.monotonic()<end:
   line=ch.read(end)
   if not line:continue
   f=parse_fields(line);name=f.get("name")
   if name and name!="-":nodes.add(name)
   if name=="BSFC2CC":
    kind=line.split(" ",1)[0];counts[kind]+=1;first=time.monotonic() if first is None else first;last=time.monotonic()
 finally:
  health=ch.health_snapshot();ch.close()
result={"schema":"biospur-c2cc-passive-visibility-v1","duration_s":a.duration,"transmitted_commands":[],"counts":dict(sorted(counts.items())),"observed_nodes":sorted(nodes),"first_c2cc_monotonic":first,"last_c2cc_monotonic":last,"health":health}
(a.out_dir/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n");print(json.dumps(result,indent=2,sort_keys=True))

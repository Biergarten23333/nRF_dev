#!/usr/bin/env python3
"""Passive FM CDC/J-Link enumeration witness; transmits nothing."""
from __future__ import annotations
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

CDC=Path('/dev/serial/by-id/usb-BioSpur_BioSpur_Fusion_Master_8D3AC42D4D90FAE8-if00')
JLINK=Path('/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00')
def wall():return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')
def state():
 def one(p):
  try:return {'exists':True,'target':os.readlink(p),'resolved':str(p.resolve(strict=True))}
  except (FileNotFoundError,OSError):return {'exists':False,'target':None,'resolved':None}
 return {'cdc':one(CDC),'jlink':one(JLINK)}
def main():
 out=Path(sys.argv[1]);parent=int(sys.argv[2]);last=None
 with out.open('x',encoding='utf-8',buffering=1) as f:
  while Path(f'/proc/{parent}').exists():
   cur=state()
   if cur!=last:
    row={'host_monotonic':time.monotonic(),'wall':wall(),**cur}
    f.write(json.dumps(row,sort_keys=True)+'\n');last=cur
   time.sleep(.1)
  f.write(json.dumps({'host_monotonic':time.monotonic(),'wall':wall(),'event':'capture_parent_exited',**state()},sort_keys=True)+'\n')
 return 0
if __name__=='__main__':raise SystemExit(main())

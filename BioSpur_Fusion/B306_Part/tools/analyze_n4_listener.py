#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from pathlib import Path

def main():
    src=Path(sys.argv[1]); out=Path(sys.argv[2]); start=float(sys.argv[3]); end=float(sys.argv[4])
    counts=Counter(); first={}; last={}; hourly=defaultdict(Counter)
    with src.open(errors='replace') as f:
        for line in f:
            if '"kind":"LPD"' not in line: continue
            try:r=json.loads(line); t=int(r['arrival_monotonic_ns'])/1e9; s=int(r['src'])
            except (ValueError,KeyError,TypeError):continue
            if not start<=t<=end:continue
            counts[s]+=1;first.setdefault(s,t);last[s]=t;hourly[min(4,int((t-start)//3600)+1)][s]+=1
    result={'window':{'start':start,'end':end},'sources':{f'0x{s:04X}':{'count':counts[s],
      'first_relative_s':first[s]-start,'last_relative_s':last[s]-start,
      'hours':{str(h):hourly[h][s] for h in sorted(hourly)}} for s in sorted(counts)}}
    out.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
if __name__=='__main__':main()

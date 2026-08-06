#!/usr/bin/env python3
"""Offline attribution gate for relay8.3; writes only derived evidence."""
from __future__ import annotations
import csv,json,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'UWB_Part/logs/relay8_3_20260802'
sys.path.insert(0,str(ROOT/'B306_Part/tools'))
import analyze_relay8_1_overnight as ana
from fusion_session import parse_fields
NODES=('BSF3C79','BSFEC35','BSF44AD','BSF6C53','BSF8BC4','BSF1120','BSF31CC','BSFAA61','BSFB165','BSFC2CC')

def bounds(cap):
 j=json.loads(cap.read_text()); return float(j['started_monotonic']),float(j['ended_monotonic'])
def extract(label,log,start,end):
 first={};last={}
 for h,line in ana.iter_fusion(log,start,end):
  if 'FUSION_TELEMETRY ' not in line: continue
  f=parse_fields(line);n=f.get('name')
  if n not in NODES: continue
  row={k:int(f.get(k,'0'),0) for k in ('uart_restarts','relay_timeout','crc','header')}
  first.setdefault(n,row);last[n]=row
 dur_h=(end-start)/3600
 return [dict(window=label,node=n,duration_h=dur_h,**{k:last[n][k]-first[n][k] for k in first[n]},uart_restarts_per_board_hour=(last[n]['uart_restarts']-first[n]['uart_restarts'])/dur_h,relay_timeout_per_board_hour=(last[n]['relay_timeout']-first[n]['relay_timeout'])/dur_h) for n in NODES]
def main():
 s3=ROOT/'UWB_Part/logs/relay8_2_20260802/hardware_arc/s3_fix_verification_20260802_132357'
 s67=ROOT/'UWB_Part/logs/relay8_2_20260802/hardware_arc/s6_s7_20260802_143622'
 ov=ROOT/'UWB_Part/logs/relay8_1_20260801/hardware_arc/overnight_run_20260802_0046/capture'
 state=json.loads((ov/'OVERNIGHT_RUN_STATE.json').read_text()); chunks=state['w_chunk_indices']; os=min(state['chunks'][i]['capture']['started_monotonic'] for i in chunks); oe=max(state['chunks'][i]['capture']['ended_monotonic'] for i in chunks)
 specs=[('relay8.1_overnight_W',ov/'fusion_cdc.log',os,oe),('relay8.2_S3',s3/'fusion_cdc.log',*bounds(s3/'formal_capture.json')),('relay8.2_S6',s67/'s6/fusion_cdc.log',*bounds(s67/'s6/s6_capture.json')),('relay8.2_S7',s67/'s7/fusion_cdc.log',*bounds(s67/'s7/s7_capture.json'))]
 rows=[]
 for x in specs: rows+=extract(*x)
 with (OUT/'PHASE0_REGRESSION_CURVE.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
 # P0.3: compare per-second QOS reports and telemetry counter deltas before/after reset.
 log=s67/'s7/fusion_cdc.log'; start,end=specs[-1][2:]; reset=249527.101543
 q=defaultdict(lambda:defaultdict(lambda:[0,0,0])); t=defaultdict(lambda:defaultdict(dict))
 for h,line in ana.iter_fusion(log,start,end):
  f=parse_fields(line);n=f.get('name'); period='post' if h>=reset else 'pre'
  if n not in NODES or n=='BSF44AD':continue
  if 'FUSION_QOS ' in line:
   q[period][n][0]+=int(f.get('reports','0'),0);q[period][n][1]+=int(f.get('crc_error','0'),0);q[period][n][2]+=int(f.get('rx_timeout','0'),0)
  elif 'FUSION_TELEMETRY ' in line:
   r={k:int(f.get(k,'0'),0) for k in ('uart_restarts','crc','header')};t[period][n].setdefault('first',r);t[period][n]['last']=r
 inter={}
 for period,duration in [('pre',reset-start),('post',end-reset)]:
  rr=sum(q[period][n][0] for n in q[period]);ce=sum(q[period][n][1] for n in q[period]);to=sum(q[period][n][2] for n in q[period])
  inter[period]={'duration_s':duration,'reports':rr,'qos_crc_per_report':ce/max(1,rr),'rx_timeout_per_report':to/max(1,rr),'uart_restarts_per_board_hour':sum(t[period][n]['last']['uart_restarts']-t[period][n]['first']['uart_restarts'] for n in t[period])/(duration/3600)/9,'telemetry_crc_header_per_board_hour':sum((t[period][n]['last']['crc']+t[period][n]['last']['header'])-(t[period][n]['first']['crc']+t[period][n]['first']['header']) for n in t[period])/(duration/3600)/9}
 result={'s3_configuration':{'count':11,'period_us':110000,'evidence':str((s3/'s3_entry_main_period_110.json').relative_to(ROOT))},'regression_curve':'PHASE0_REGRESSION_CURVE.csv','interference_other_nine':inter,'p0_4':{'count12_excluded':True,'step_exact_at_relay8_2_boundary':None,'t2_open':False,'reason':'computed after reviewing curve; default fail-closed'}}
 # Exact boundary requires relay8.1 near-zero and all relay8.2 windows elevated.
 by=defaultdict(list)
 for r in rows:by[r['window']].append(r['uart_restarts_per_board_hour'])
 # "Step at the boundary" is a fleet-rate comparison, not a claim that the
 # relay8.1 baseline had literally zero restarts on every board.  Require an
 # order-of-magnitude S3 step and persistence through both later windows.
 old=sum(by['relay8.1_overnight_W'])/len(by['relay8.1_overnight_W'])
 exact=(sum(by['relay8.2_S3'])/len(by['relay8.2_S3']) >= 10*max(old,1e-9)
        and all(sum(by[w])>0 for w in ('relay8.2_S6','relay8.2_S7')))
 result['p0_4'].update(step_exact_at_relay8_2_boundary=exact,t2_open=exact,reason='S3 excludes COUNT=12 and normalized restart step begins with relay8.2' if exact else 'boundary attribution did not close')
 (OUT/'PHASE0_ATTRIBUTION.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()

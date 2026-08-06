#!/usr/bin/env python3
from __future__ import annotations
import json,re,sys
from collections import Counter,defaultdict
from pathlib import Path
from delivered_rate import delivered_rate
NODES=('BSF3C79','BSFC2CC','BSF44AD','BSF6C53','BSF8BC4','BSF1120','BSF31CC','BSFAA61','BSFEC35','BSFB165')
KV=re.compile(r'(?:^| )([A-Za-z0-9_]+)=([^ ]+)')
def fields(s):return dict(KV.findall(s))
def ratio(n,d):return {'numerator':n,'denominator':d,'value':n/d if d else None}
def u16(a,b):return (b-a)&0xffff
def main():
 root=Path(sys.argv[1]); output=Path(sys.argv[2]) if len(sys.argv)>2 else root/'n3_core_analysis.json'
 meta=json.loads((root/'state.json').read_text());start=meta['window_open']['monotonic'];end=meta['window_close']['monotonic']
 hours=int((end-start+3599.999)//3600); data=defaultdict(lambda:[defaultdict(int) for _ in range(hours)])
 hist=defaultdict(lambda:[Counter() for _ in range(hours)]); qfirst={};qlast={};qprev={};qsum=defaultdict(Counter);last_seen={};first_seen={};last_seq={};last_vec={};back=[]
 slot_uwb=defaultdict(lambda:[set() for _ in range(hours)])
 slot_imu=defaultdict(lambda:[set() for _ in range(hours)])
 con=[]; total_lines=0
 with (root/'fusion_cdc.log').open(errors='replace') as fh:
  for raw in fh:
   p=raw.split(' ',3)
   if len(p)<4:continue
   try:t=float(p[1])
   except ValueError:continue
   if not start<=t<=end:continue
   rec=p[3].strip();f=fields(rec);n=f.get('name')
   if n not in NODES:continue
   h=min(hours-1,int((t-start)//3600));d=data[n][h];total_lines+=1
   first_seen.setdefault(n,t);last_seen[n]=t
   if rec.startswith(('FUSION_CONNECTED ','FUSION_DISCONNECTED ')):
    con.append({'mono':t,'wall_epoch':float(p[0]),'line':rec})
    last_seq.pop((n,'imu'),None);last_seq.pop((n,'imu_n'),None);last_seq.pop((n,'sweep'),None);last_vec.pop(n,None);qprev.pop(n,None)
    continue
   if rec.startswith('FUSION_UWB '):
    d['uwb']+=1;valid=int(f.get('valid','0'),0).bit_count();hist[n][h][valid]+=1
    if valid>=8:d['ge8']+=1
    if valid>=7:d['ge7']+=1
    if f.get('sf_valid')=='1':d['sf']+=1
    if valid>=4:d['position_solvable']+=1
    if valid>=4 and f.get('sf_valid')=='1':
     s=int((t-(start+h*3600))/.12);slot_uwb[n][h].add(s)
    sw=int(f.get('sweep','0'),0)
    key=(n,'sweep')
    if key in last_seq and sw<last_seq[key]:back.append({'node':n,'mono':t,'previous':last_seq[key],'current':sw})
    last_seq[key]=sw
   elif rec.startswith('FUSION_IMU '):
    seq=int(f.get('seq','0'),0);count=int(f.get('n','0'),0);d['imu']+=count
    key=(n,'imu')
    if key in last_seq:
     delta=u16(last_seq[key],seq)
     expected=last_seq[(n,'imu_n')]
     if delta>32768:d['imu_seq_epoch_reset']+=1
     elif delta!=expected:
      missing=max(0,delta-expected)
      # The CDC decoder may drain a small pre-attachment block before the
      # current block.  O3 established this fleet-wide boundary within the
      # first 0.2 s; account it separately, never as formal-window loss.
      if t-start < .2:
       d['imu_attachment_boundary_events']+=1;d['imu_attachment_boundary_missing']+=missing
      else:d['imu_gap_events']+=1;d['imu_seq_lost']+=missing
    last_seq[key]=seq;last_seq[(n,'imu_n')]=count
    for tok in f.get('samples','').split(';'):
     if not tok:continue
     vals=tok.split(',');vec=tuple(vals[1:7])
     if last_vec.get(n)==vec:d['duplicates']+=1
     else:d['fresh']+=1
     last_vec[n]=vec;s=int((t-(start+h*3600))/.12);slot_imu[n][h].add(s)
   elif rec.startswith('FUSION_QUEUE '):
    vals={k:int(f.get(k,'0'),0) for k in ('q_drop_imu','q_drop_uwb','q_drop_ctl')}
    qfirst.setdefault(n,vals);qlast[n]=vals
    if n in qprev:
     for k,v in vals.items():
      if v>=qprev[n][k]:qsum[n][k]+=v-qprev[n][k]
    qprev[n]=vals
 out={'window':{'start':start,'end':end,'duration_s':end-start,'hours':hours},'nodes':{},'connection_events':con,'sweep_backwards':back,'total_parsed_node_records':total_lines}
 for n in NODES:
  hrs=[]
  for h,d in enumerate(data[n]):
   dur=min(3600,end-(start+h*3600));fusion=len(slot_uwb[n][h] & slot_imu[n][h])
   possible=int(dur//.12)
   uwb_rate=delivered_rate(d['uwb'],dur,(),stream='uwb',max_rate_hz=1000/120)
   imu_rate=delivered_rate(d['imu'],dur,(),stream='imu',max_rate_hz=200)
   hrs.append({'hour':h+1,'duration_s':dur,'uwb_delivered':d['uwb'],'uwb_8of8':ratio(d['ge8'],d['uwb']),
    'uwb_7plus':ratio(d['ge7'],d['uwb']),'sf_valid':ratio(d['sf'],d['uwb']),'position_solvable':ratio(d['position_solvable'],d['uwb']),
    'valid_link_histogram':dict(sorted(hist[n][h].items())),'imu_delivered':d['imu'],'imu_seq_lost':d['imu_seq_lost'],
    'imu_gap_events':d['imu_gap_events'],'imu_seq_epoch_resets':d['imu_seq_epoch_reset'],
    'imu_attachment_boundary_events':d['imu_attachment_boundary_events'],
    'imu_attachment_boundary_missing':d['imu_attachment_boundary_missing'],
    'fresh':ratio(d['fresh'],d['imu']),'duplicates':d['duplicates'],
    'placeable':ratio(d['fresh'],d['imu']),'fused_usable_120ms':ratio(fusion,possible),
    'uwb_delivery_rate':uwb_rate.json(),'imu_delivery_rate':imu_rate.json(),
    'rate_flags':list(uwb_rate.flags+imu_rate.flags)})
  qd={k:qsum[n][k] for k in ('q_drop_imu','q_drop_uwb','q_drop_ctl')}
  out['nodes'][n]={'first_mono':first_seen.get(n),'last_mono':last_seen.get(n),'last_relative_s':last_seen.get(n,0)-start,
   'queue_first':qfirst.get(n),'queue_last':qlast.get(n),'queue_delta':qd,'hours':hrs}
 output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
 print(json.dumps({'window':out['window'],'deaths':{n:out['nodes'][n]['last_relative_s'] for n in NODES},'events':len(con),'backwards':back,
  'totals':{n:{'uwb':sum(h['uwb_delivered'] for h in out['nodes'][n]['hours']),'imu':sum(h['imu_delivered'] for h in out['nodes'][n]['hours']),
  'seq_lost':sum(h['imu_seq_lost'] for h in out['nodes'][n]['hours']),'dup':sum(h['duplicates'] for h in out['nodes'][n]['hours']),'q':out['nodes'][n]['queue_delta']} for n in NODES}},indent=2))
if __name__=='__main__':main()

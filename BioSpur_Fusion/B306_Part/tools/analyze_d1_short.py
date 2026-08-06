#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

NODES = ("BSF3C79","BSFC2CC","BSF44AD","BSF6C53","BSF8BC4","BSF1120","BSF31CC","BSFAA61","BSFEC35","BSFB165")
KV = re.compile(r"(?:^| )([A-Za-z0-9_]+)=([^ ]+)")

def fields(s): return dict(KV.findall(s))
def ratio(n,d): return {"numerator":n,"denominator":d,"value": n/d if d else None, "label": f"{n}/{d}" if d else "INSUFFICIENT"}

root=Path(sys.argv[1]); meta=json.loads((root/'d1_capture.json').read_text()); log=root/'d1_capture.cdc.log'
start=float(meta['window_open']['monotonic']); end=start+600.0
uwb=defaultdict(list); imu=defaultdict(list); queue=defaultdict(list); con=[]
for line in log.open(errors='replace'):
    p=line.split(' ',3)
    if len(p)<4: continue
    try: t=float(p[1])
    except: continue
    if not (start <= t <= end): continue
    rec=p[3].strip(); f=fields(rec); n=f.get('name')
    if n not in NODES: continue
    if rec.startswith('FUSION_UWB '): uwb[n].append((t,f))
    elif rec.startswith('FUSION_IMU '): imu[n].append((t,f))
    elif rec.startswith('FUSION_QUEUE '): queue[n].append((t,f))
    elif rec.startswith(('FUSION_CONNECTED ','FUSION_DISCONNECTED ')): con.append((t,rec,f))

rejoins=[]
for n,rows in uwb.items():
    for (ta,a),(tb,b) in zip(rows,rows[1:]):
        if int(b['sweep'],0) < int(a['sweep'],0):
            rejoins.append({"node":n,"stream_stop_mono":ta,"first_uwb_mono":tb,"outage_s":tb-ta,
                            "last_pre_sweep":int(a['sweep'],0),"first_post_sweep":int(b['sweep'],0),
                            "first_post_sf_valid":int(b.get('sf_valid','0'),0)})
rejoins.sort(key=lambda x:x['first_uwb_mono'])
ints=meta.get('interventions',[])
for e in rejoins:
    cand=[x for x in ints if x['node']==e['node'] and x['sent_mono']>=e['first_uwb_mono'] and x['sent_mono']<=e['first_uwb_mono']+20]
    e['imu_autonomous']=cand[0]['autonomous_imu_seen'] if cand else None
    e['imu_intervention_mono']=cand[0]['sent_mono'] if cand else None
    e['imu_intervention_delay_s']=(cand[0]['sent_mono']-e['first_uwb_mono']) if cand else None
    disc=[(t,r) for t,r,f in con if f.get('name')==e['node'] and r.startswith('FUSION_DISCONNECTED') and e['stream_stop_mono']-5<=t<=e['first_uwb_mono']]
    conn=[(t,r) for t,r,f in con if f.get('name')==e['node'] and r.startswith('FUSION_CONNECTED') and e['stream_stop_mono']<=t<=e['first_uwb_mono']+2]
    e['disconnect_mono']=disc[-1][0] if disc else None; e['connect_mono']=conn[-1][0] if conn else None
    e['connect_to_first_uwb_s']=e['first_uwb_mono']-e['connect_mono'] if e['connect_mono'] else None

def metrics(node,a,b):
    us=[(t,f) for t,f in uwb[node] if a<=t<b]; ims=[(t,f) for t,f in imu[node] if a<=t<b]
    dur=max(1e-9,b-a); hist=Counter(int(f.get('valid','0'),0).bit_count() for _,f in us)
    sf=sum(f.get('sf_valid')=='1' for _,f in us)
    samples=sum(int(f.get('n','0'),0) for _,f in ims)
    gaps=0; pairs=0
    for (_,x),(_,y) in zip(ims,ims[1:]):
        pairs+=1
        if ((int(y['seq'],0)-int(x['seq'],0))&0xffffffff) != int(x.get('n','0'),0): gaps+=1
    qs=[f for t,f in queue[node] if a<=t<b]
    qd={}
    for k in ('q_drop_imu','q_drop_uwb'):
        qd[k]=((int(qs[-1][k],0)-int(qs[0][k],0))&0xffffffff) if len(qs)>=2 else None
    return {"duration_s":dur,"uwb_records":len(us),"uwb_rate_hz":len(us)/dur,
            "sf_valid":ratio(sf,len(us)),"valid_link_count_histogram":{str(k):v for k,v in sorted(hist.items())},
            "imu_samples":samples,"imu_rate_hz":samples/dur,"imu_sequence_gap_pairs":ratio(pairs-gaps,pairs),
            "imu_gap_count":gaps,**qd}

effects=[]
for i,e in enumerate(rejoins,1):
    a=e['stream_stop_mono']; b=e['first_uwb_mono']; width=max(10.0,min(30.0,b-a))
    rows={}
    for n in NODES:
        if n==e['node']: continue
        rows[n]={"before":metrics(n,max(start,a-width),a),"during":metrics(n,a,b),"after":metrics(n,b,min(end,b+width))}
    effects.append({"event":i,"disturbed_node":e['node'],"windows":{"before":[max(start,a-width),a],"during":[a,b],"after":[b,min(end,b+width)]},"undisturbed":rows})

minutes=[]
for m in range(10):
    a=start+60*m; b=a+60; per={}
    for n in NODES:
        us=[f for t,f in uwb[n] if a<=t<b]; ims=[f for t,f in imu[n] if a<=t<b]
        full=sum(int(f.get('valid','0'),0).bit_count()==8 for f in us); sf=sum(f.get('sf_valid')=='1' for f in us)
        samples=sum(int(f.get('n','0'),0) for f in ims)
        per[n]={"uwb_delivered":len(us),"eight_of_eight_links":ratio(full,len(us)),
                "usable_absolute_epoch_label":ratio(sf,len(us)),"imu_samples_delivered":samples,
                "position_solved":"INSUFFICIENT: no solver output in capture",
                "imu_fresh_placeable":"INSUFFICIENT: offline axis aligner not run",
                "fused_usable_epochs":"INSUFFICIENT: requires solver and axis-placement products"}
    minutes.append({"minute":m+1,"nodes":per})

summary={"classification":"OPERATOR_REQUESTED_EARLY_STOP","window":{"open_mono":start,"close_mono":end,"duration_s":600.0},
         "events":rejoins,"connection_events":con,"undisturbed_effect_windows":effects,"yield_ladder_per_minute":minutes,
         "host_health":meta.get('host_health'),"limitations":[
             "The planned 1800 s D1 was stopped at 600 s by operator instruction; this is not a complete D1 qualification.",
             "No listener-array capture was started, so the air-direct H4 no-transmit-before-lock check is unavailable.",
             "RESETREAS and boot counter were not queried after the early stop; no values are inferred.",
             "The capture contains no position-solver or final axis-placement products; bottom-rung and fused-usable metrics are INSUFFICIENT."]}
(root/'d1_10min_analysis.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
print(json.dumps({"events":rejoins,"minute_counts":[{"minute":m['minute'],"uwb_nodes":sum(v['uwb_delivered']>0 for v in m['nodes'].values()),"imu_nodes":sum(v['imu_samples_delivered']>0 for v in m['nodes'].values())} for m in minutes]},indent=2))

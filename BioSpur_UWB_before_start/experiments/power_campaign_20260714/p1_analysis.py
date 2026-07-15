import csv, json, statistics as st, os
PRE="SS-TWR/alt-SS-TWR/broadcast/logs/ge7_test_20260704_032041_20260704_032041/range_diag_joined.csv"
POST="SS-TWR/alt-SS-TWR/broadcast/logs/p0txrf_verify_bs9336_v5_20260714_20260714_175051/range_diag_joined.csv"
def load(p):
    d={}
    with open(p) as f:
        for x in csv.DictReader(f):
            if x.get('valid') not in ('1','True','true'): continue
            try: rng=float(x['range_mm'])
            except: continue
            k=(x.get('peer_name',''), x.get('anchor_id',''))
            d.setdefault(k,[]).append(rng)
    return d
pre, post = load(PRE), load(POST)
tags=sorted(set(k[0] for k in pre)|set(k[0] for k in post))
anchors=sorted(set(k[1] for k in pre)|set(k[1] for k in post), key=lambda a:int(a))
rows=[]; deltas=[]
for t in tags:
    for a in anchors:
        pv=pre.get((t,a),[]); qv=post.get((t,a),[])
        r={"tag":t,"anchor":a,
           "pre_n":len(pv),"post_n":len(qv),
           "pre_mean_mm":round(st.mean(pv),1) if pv else None,
           "post_mean_mm":round(st.mean(qv),1) if qv else None,
           "pre_std_mm":round(st.pstdev(pv),1) if len(pv)>1 else None,
           "post_std_mm":round(st.pstdev(qv),1) if len(qv)>1 else None}
        if pv and qv:
            r["delta_mm"]=round(r["post_mean_mm"]-r["pre_mean_mm"],1); deltas.append(r["delta_mm"])
        else: r["delta_mm"]=None
        rows.append(r)
summ={"n_links_compared":len(deltas),
      "delta_mean_mm":round(st.mean(deltas),1) if deltas else None,
      "delta_median_mm":round(st.median(deltas),1) if deltas else None,
      "delta_std_mm":round(st.pstdev(deltas),1) if len(deltas)>1 else None,
      "delta_min_mm":round(min(deltas),1) if deltas else None,
      "delta_max_mm":round(max(deltas),1) if deltas else None}
out={"pre_capture":PRE,"post_capture":POST,
     "pre_power":"POR-era (old fw, TX~0x0E080222)","post_power":"P0-fixed (TX=0x25456585, +4.5dB)",
     "caveat":"PRE valid~96.6%, POST valid~48.8% => different wand geometry; range delta confounded by position, not purely the 4.5dB power change",
     "per_link":rows,"summary":summ}
os.makedirs("experiments/power_campaign_20260714",exist_ok=True)
json.dump(out,open("experiments/power_campaign_20260714/p1_results.json","w"),indent=2)
print("=== P1 pre/post per-link (mm) ===")
print(f"{'tag':7}{'anc':4}{'pre_mean':>9}{'post_mean':>10}{'delta':>7}{'pre_std':>8}{'post_std':>9}")
for r in rows:
    if r['delta_mm'] is None: continue
    print(f"{r['tag']:7}{r['anchor']:4}{r['pre_mean_mm']:>9}{r['post_mean_mm']:>10}{r['delta_mm']:>7}{r['pre_std_mm'] if r['pre_std_mm'] else 0:>8}{r['post_std_mm'] if r['post_std_mm'] else 0:>9}")
print("\n=== SUMMARY ===")
for k,v in summ.items(): print(f"  {k}: {v}")

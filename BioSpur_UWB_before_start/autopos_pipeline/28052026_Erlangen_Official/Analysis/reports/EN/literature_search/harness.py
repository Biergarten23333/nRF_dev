import sys, csv, glob, json, time, importlib.util
from pathlib import Path
from types import SimpleNamespace
from collections import defaultdict
import numpy as np
from scipy.optimize import least_squares
sys.path.insert(0, sys.argv[1])  # scratch
from eval_lib import load_truth, load_layout, report, ANCH

ROOT=Path("/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start")
ERL=ROOT/"autopos_pipeline/28052026_Erlangen_Official"
spec=importlib.util.spec_from_file_location("sv4",ROOT/"autopos_pipeline/solve_v4_fusion/solve_v4.py")
sv4=importlib.util.module_from_spec(spec); spec.loader.exec_module(sv4)
A2I={c:i for i,c in enumerate(ANCH)}
TRUTH=load_truth(ERL/"Analysis/AutoPos_simulation/phase0_solver_headroom/data/erlangen_anchor_truth_all8_v4io.json")
INIT=ERL/"solver/work/field_dataset_staged/FULL-COMPARE-1000-production-T4-real/v4-io/layout.json"
PAIRS=ERL/"solver/work/field_dataset_staged/sweep1000/pairs_all.csv"

def fuse_pairs():
    g=defaultdict(list)
    with open(PAIRS) as f:
        for r in csv.DictReader(f):
            if r.get('ok')!='1': continue
            a=r['a'].strip().upper(); b=r['b'].strip().upper()
            if a not in A2I or b not in A2I or a==b: continue
            i,j=sorted((A2I[a],A2I[b]))
            try: d=float(r['dist_mm'])
            except: continue
            if d>0: g[(i,j)].append(d)
    inter=[]
    for (i,j),v in g.items():
        v=np.asarray(v); n=max(1,int(np.ceil(v.size*0.2)))
        est=float(np.mean(np.partition(v,n-1)[:n])) if v.size>=50 else float(np.median(v))
        inter.append({"i":i,"j":j,"range_mm":est})
    return inter
INTER=fuse_pairs()

def cap_dir(name):
    hits=sorted(glob.glob(str(ERL/f"captures/erlangen_20260528_optitrack/{name}*")))
    return hits[0] if hits else None
def tr_csv(capdir):
    h=glob.glob(f"{capdir}/tag_capture_*/tr_all.csv"); return h[0] if h else None

def load_cap(capdir, source_id, keep):
    f=tr_csv(capdir)
    if not f: return []
    rows=[]
    with open(f) as fh:
        for r in csv.DictReader(fh):
            if r.get('valid')!='1': continue
            tag=(r.get('peer_name') or '').strip()
            try: aid=int(r['anchor_id']); rng=float(r['range_mm']); swp=int(r['sweep'])
            except: continue
            if not(0<=aid<8) or rng<=0 or not tag: continue
            rows.append({"tag":tag,"source":source_id,"sweep":swp,"anchor":aid,"range_mm":rng})
    sw=sorted(set((r['tag'],r['sweep']) for r in rows))
    step=max(1,len(sw)//keep) if keep else 1
    ksel=set(sw[::step])
    return [r for r in rows if (r['tag'],r['sweep']) in ksel]

def solve(tag_rows):
    names=sv4.tag_names_from_ranges(tag_rows)
    t2i={n:i for i,n in enumerate(names)}
    fk,fmap,tr=sv4.build_tag_frame_map(tag_rows,t2i,1)
    init=sv4.load_layout(INIT)
    tp=sv4.initial_tag_positions(fk,fmap,tr,[],t2i,init)
    x0=sv4.pack_variables(init,np.zeros(8),np.zeros(3),tp)
    cfg=SimpleNamespace(sigma_inter=30.0,sigma_tag=80.0,sigma_gauge=1.0,sigma_delay_prior=10.0,n_tag_frames=len(fk))
    fun=lambda x: sv4.residuals(x,INTER,tr,fmap,cfg)
    lo,hi=sv4.variable_bounds(len(fk),30.0)
    res=least_squares(fun,x0,loss='huber',f_scale=1.0,bounds=(lo,hi),
        jac_sparsity=sv4.build_jac_sparsity(INTER,tr,fmap,len(fk)),max_nfev=200,verbose=0)
    axyz,da,dt,tpos=sv4.unpack_variables(res.x,len(fk))
    return axyz,da,dt,tpos,fk,fmap,res

def tilt_of(tpos,fk,tagname,t2i):
    idx=[fmap_i for k,fmap_i in [( (t2i.get(tagname),),0)]]  # placeholder
    return None

if __name__=="__main__":
    mode=sys.argv[2] if len(sys.argv)>2 else "smoke"
    STATIC=[f"static_ID{n:02d}" for n in range(1,25)]
    t0=time.time()
    print(f"INTER pairs: {len(INTER)}")
    # baseline: inter + static(subsampled)
    base_rows=[]
    for n in STATIC[:6 if mode=='smoke' else 24]:
        d=cap_dir(n);  base_rows+=load_cap(d,n,8 if mode=='smoke' else 6) if d else []
    print(f"baseline static frames rows={len(base_rows)}  building... t={time.time()-t0:.1f}s")
    axyz,da,dt,tpos,fk,fmap,res=solve(base_rows)
    print(f"  solved baseline: nfev={res.nfev} cost={res.cost:.1f} frames={len(fk)} t={time.time()-t0:.1f}s  d_tag={np.round(dt,1)}")
    np.save(f"{sys.argv[1]}/base_axyz.npy",axyz)
    report("BASELINE inter+static (no roto)", axyz, TRUTH)
    # + two roto
    roto2=base_rows[:]
    for n in ["roto_R01_","roto_R09_"]:
        d=cap_dir(n); roto2+=load_cap(d,n,25) if d else []
    print(f"\n+2 roto rows={len(roto2)} solving... t={time.time()-t0:.1f}s")
    axyz2,da2,dt2,tpos2,fk2,fmap2,res2=solve(roto2)
    print(f"  solved +2roto: nfev={res2.nfev} frames={len(fk2)} t={time.time()-t0:.1f}s d_tag={np.round(dt2,1)}")
    report("BASELINE + 2 roto (R01,R09)", axyz2, TRUTH)

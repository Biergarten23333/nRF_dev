import json, numpy as np, itertools
ANCH="ABCDEFGH"
def load_truth(p):
    d=json.load(open(p))["anchors"]
    return np.array([[d[L]["x_mm"],d[L]["y_mm"],d[L]["z_mm"]] for L in ANCH])
def load_layout(p):
    raw=json.load(open(p)); X=np.zeros((8,3))
    ents = raw.get("anchors")
    if ents and isinstance(ents,list):
        for e in ents:
            i=ANCH.index(str(e.get("label",ANCH[int(e.get("id",0))])).upper())
            X[i]=[e["x_mm"],e["y_mm"],e["z_mm"]]
    elif "layout" in raw:
        for k,v in raw["layout"].items(): X[int(k)]=v[:3]
    return X
def _align_once(src,dst,scale,allow_reflect):
    sc=src.mean(0); dc=dst.mean(0); S=src-sc; D=dst-dc
    H=S.T@D; U,s,Vt=np.linalg.svd(H); d=np.sign(np.linalg.det(Vt.T@U.T))
    best=None
    cands=[1.0,-1.0] if allow_reflect else [d]  # try proper & improper, pick best
    for sgn in cands:
        Dg=np.diag([1,1,sgn]); R=Vt.T@Dg@U.T
        if scale:
            a=(np.sum(s*np.array([1,1,sgn])))/ (S**2).sum()
        else: a=1.0
        aligned=(a*(S@R.T))+dc
        rmse=np.sqrt((np.linalg.norm(aligned-dst,axis=1)**2).mean())
        if best is None or rmse<best[0]: best=(rmse,aligned,a)
    return best[1],best[2]
def report(name,X,T,allow_reflect=True):
    out={}
    for scale in (False,True):
        A,a=_align_once(X,T,scale,allow_reflect)
        err=A-T; e3=np.linalg.norm(err,axis=1)
        vy=np.abs(err[:,1]); hxz=np.linalg.norm(err[:,[0,2]],axis=1)
        rmse=float(np.sqrt((e3**2).mean())); med=float(np.median(e3))
        vR=float(np.sqrt((vy**2).mean())); hR=float(np.sqrt((hxz**2).mean()))
        out['rigid' if not scale else 'sim3']=dict(rmse=rmse,med=med,vertY=vR,horizXZ=hR,scale=float(a))
        print(f"  [{name}] {'Sim3' if scale else 'rigid'}: 3D_RMSE={rmse:7.2f}  3D_med={med:7.2f}  vertY_RMSE={vR:7.2f}  horizXZ_RMSE={hR:7.2f}  scale={a:.4f}")
    return out

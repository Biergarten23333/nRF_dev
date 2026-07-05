#!/usr/bin/env python3
"""Room image from the wand cluster treated as a multistatic radar at the volume center.
Geometry (per user): 3 wand tags + their 3 co-located listeners are ALL on a <=500mm rod at
the volume center -> treat as ONE point C (they sit inside one range-resolution cell anyway).
3 anchor-side listeners are at anchors B/E/F (AutoPos, mm-accurate).

Two products:
  (A) Monostatic range profile from C: each wand tag heard by its OWN co-located listener.
      First-path = self-coupling (range~0); tail tap k = room echo at one-way range
      r = k*SAMP_NS*c/2. This is a radar looking outward from the volume center.
  (B) 2D multistatic back-projection: mono spheres (C->C) + bistatic ellipsoids (C->B/E/F).

Usage: python3 wand_radar_image.py <capture_base_dir> [cx cy cz]"""
import csv, glob, os, sys, json, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
BASE=sys.argv[1]
MM_PER_NS=299.792458; SAMP_NS=1.0016
NSAMP=1016; PRE=8; POST=120
RANGE_PER_TAP=SAMP_NS*MM_PER_NS/2.0        # mm of one-way range per tap (monostatic)

lay=json.load(open('SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']],float) for a in lay['anchors']}
xs_a=[p[0] for p in A.values()]; ys_a=[p[1] for p in A.values()]; zs_a=[p[2] for p in A.values()]
# volume center (default = anchor bounding-box center); overridable on cmdline
if len(sys.argv)>=5: C=np.array([float(sys.argv[2]),float(sys.argv[3]),float(sys.argv[4])])
else: C=np.array([(min(xs_a)+max(xs_a))/2,(min(ys_a)+max(ys_a))/2, 900.0])
print(f"volume center C = {C.round().tolist()}  (room x[{min(xs_a):.0f},{max(xs_a):.0f}] y[{min(ys_a):.0f},{max(ys_a):.0f}])")

# tag_id after 2026-07-05 power-cycle
TAGS=[('4','BSCCF4','LCCF4'),('6','BS9336','L9336'),('10','BS955A','L955A')]
RX={'LB':A['B'],'LE':A['E'],'LF':A['F'],'LCCF4':C,'L9336':C,'L955A':C}

def mean_cir(probe,tid):
    D=glob.glob(f'{BASE}/{probe}/listener_*')
    if not D: return None,0
    meta={}
    try:
        for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
            if r['tag_id']!=tid: continue
            try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']))
            except: pass
        segs={}
        for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
            if r['accepted_polls'] in meta: segs.setdefault(r['accepted_polls'],[]).append((int(r['offset']),r['hex']))
    except FileNotFoundError: return None,0
    acc=np.zeros(PRE+POST); n=0
    for ap,seg in segs.items():
        fps,rp=meta[ap]; seg.sort(); h=''.join(x for _,x in seg)
        try: iq=np.frombuffer(bytes.fromhex(h),dtype='<i2').astype(float)
        except: continue
        if iq.size<2*NSAMP: continue
        mag=np.hypot(iq[0:2*NSAMP:2],iq[1:2*NSAMP:2])
        if rp>0: mag/=rp
        fp=int(round(fps))
        if fp-PRE<0 or fp+POST>NSAMP: continue
        acc+=mag[fp-PRE:fp+POST]; n+=1
    return (acc/n if n>0 else None),n

# ---------- (A) monostatic range profile from center ----------
rng=np.arange(POST)*RANGE_PER_TAP/1000.0   # meters, tap 0..POST at index PRE..
mono={}
for tid,tname,lname in TAGS:
    m,n=mean_cir(lname,tid)
    if m is None: continue
    pk=m[PRE] if m[PRE]>0 else m.max()
    mono[tname]=20*np.log10(np.maximum(m[PRE:PRE+POST],1e-9)/pk)
# expected wall/floor distances from C (nearest surface along each axis)
walls={'x- wall':C[0]-min(xs_a),'x+ wall':max(xs_a)-C[0],'y- wall':C[1]-min(ys_a),
       'y+ wall':max(ys_a)-C[1],'floor':C[2]-0,'ceil':max(zs_a)-C[2]}

# ---------- (B) 2D multistatic back-projection ----------
links=[]
for tid,tname,lname in TAGS:
    for rx in RX:
        m,n=mean_cir(rx,tid)
        if m is None: continue
        pk=m[PRE] if m[PRE]>0 else m.max(); tail=m/pk
        base=np.linalg.norm(C-RX[rx])
        links.append((C,RX[rx],tail,base,f'{tname}->{rx}',rx in ('LCCF4','L9336','L955A')))
print(f"{len(links)} links ({sum(1 for l in links if l[5])} monostatic-from-center, {sum(1 for l in links if not l[5])} bistatic)")

z0=C[2]
xs=np.arange(-300,4800,35); ys=np.arange(-400,3600,35)
GX,GY=np.meshgrid(xs,ys)
img=np.zeros(GX.shape); hits=np.zeros(GX.shape)
for Tx,Rx,tail,base,name,ismono in links:
    dT=np.sqrt((GX-Tx[0])**2+(GY-Tx[1])**2+(z0-Tx[2])**2)
    dR=np.sqrt((GX-Rx[0])**2+(GY-Rx[1])**2+(z0-Rx[2])**2)
    excess=dT+dR-base
    tap=PRE+np.round(excess/MM_PER_NS/SAMP_NS).astype(int)
    valid=(tap>=PRE+3)&(tap<PRE+POST)
    amp=np.zeros(GX.shape); amp[valid]=tail[tap[valid]]
    img+=amp; hits+=valid
img=img/np.maximum(hits,1)

# ---------- plot ----------
fig=plt.figure(figsize=(19,7))
axA=fig.add_subplot(1,2,1)
for tname,prof in mono.items(): axA.plot(rng,prof,lw=2,label=f'{tname} (mono)')
for lab,d in walls.items():
    axA.axvline(d/1000.0,color='0.6',ls=':',lw=1); axA.annotate(lab,(d/1000.0,2),rotation=90,va='top',ha='right',fontsize=7,color='0.4')
axA.set_xlim(0,4.5); axA.set_ylim(-40,3); axA.grid(alpha=0.25)
axA.set_xlabel('one-way range from volume center (m)'); axA.set_ylabel('echo power (dB, rel. self-coupling)')
axA.set_title('(A) Radar range profile FROM volume center (3 wand monostatic links)'); axA.legend(fontsize=9)

axB=fig.add_subplot(1,2,2)
im=axB.pcolormesh(xs,ys,img,shading='auto',cmap='hot'); fig.colorbar(im,ax=axB,label='back-projected echo')
# room boundary + nodes
bx=[min(xs_a),max(xs_a),max(xs_a),min(xs_a),min(xs_a)]; by=[min(ys_a),min(ys_a),max(ys_a),max(ys_a),min(ys_a)]
axB.plot(bx,by,'c-',lw=1.2,alpha=0.7,label='anchor bounding box (walls)')
for lab,p in A.items(): axB.plot(p[0],p[1],'s',ms=8,color='cyan'); axB.annotate(lab,(p[0],p[1]),color='cyan',fontsize=8,xytext=(3,3),textcoords='offset points')
axB.plot(C[0],C[1],'*',ms=20,color='lime',label='wand cluster (volume center)')
axB.set_aspect('equal'); axB.set_xlabel('x (mm)'); axB.set_ylabel('y (mm)')
axB.set_title(f'(B) Multistatic back-projection ({len(links)} links, z={z0:.0f}mm)'); axB.legend(fontsize=8,loc='upper left')
fig.suptitle('Room image from wand cluster @ volume center — mono radar (A) + multistatic map (B). Static room baseline.',fontsize=12)
plt.tight_layout(rect=[0,0,1,0.95])
out=os.path.join(os.path.dirname(__file__),'figs_20260704','wand_radar_image.png'); os.makedirs(os.path.dirname(out),exist_ok=True)
plt.savefig(out,dpi=120); print('saved',out)

# print the monostatic peaks (echo bumps) as a quick text readout
print("\nMonostatic echo bumps (range m : dB) above -25dB, past 0.6m:")
for tname,prof in mono.items():
    idx=[i for i in range(2,POST) if prof[i]>-25 and rng[i]>0.6 and prof[i]>=prof[i-1] and prof[i]>=prof[i-1]]
    peaks=sorted([(rng[i],prof[i]) for i in range(3,POST-1) if prof[i]>-25 and rng[i]>0.6 and prof[i]>prof[i-1] and prof[i]>prof[i+1]],key=lambda t:-t[1])[:5]
    print(f"  {tname}: "+", ".join(f"{r:.2f}m:{d:.0f}dB" for r,d in peaks))

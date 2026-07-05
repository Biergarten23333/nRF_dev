#!/usr/bin/env python3
"""Multistatic CIR back-projection -> room reflector map (point-cloud-like image).
For each (tag Tx, listener Rx) link, the mean CIR tail taps are echoes. A reflector at X puts
energy at excess delay (|Tx-X|+|X-Rx|-|Tx-Rx|)/c. Back-project every link's tail onto a room
grid and accumulate -> reflectors (walls, desk) light up where many ellipsoids agree.
Ground-truth check: the desk/monitor/PC on the A-F line is a known strong reflector."""
import csv, glob, os, sys, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
BASE=sys.argv[1] if len(sys.argv)>1 else 'SS-TWR/alt-SS-TWR/broadcast/logs/overnight_soak_v2_20260704_032348/chunk6_20260704_071845'
C=0.299792458  # mm/ps ... use mm per ns: 299.79 mm/ns
MM_PER_NS=299.792458
SAMP_NS=1.0016
NSAMP=1016; PRE=6; POST=110

# --- anchor positions (AutoPos) ---
import json
lay=json.load(open('SS-TWR/alt-SS-TWR/broadcast/logs/autopos_v3box_noref_20260704_030908/solve_v3_box/anchor_layout_v3_box.json'))
A={a['label']:np.array([a['x_mm'],a['y_mm'],a['z_mm']]) for a in lay['anchors']}
# 6 CIR probes: 3 anchor-side (LB/LE/LF at their anchors) + 3 wand-side (co-located with each wand tag).
# Wand cluster ESTIMATED near volume center; wand-side probe co-located with its tag.
# tag_id after 2026-07-05 power-cycle: BSCCF4=4, BS9336=6, BS955A=10 (src 0xb1f4/0xb136/0xb15a).
TX={'BSCCF4':np.array([2600,400,800]),'BS9336':np.array([1400,400,800]),'BS955A':np.array([3500,400,800])}
RX={'LB':A['B'],'LE':A['E'],'LF':A['F'],
    'LCCF4':TX['BSCCF4'].copy(),'L9336':TX['BS9336'].copy(),'L955A':TX['BS955A'].copy()}
TAGID={'BSCCF4':'4','BS9336':'6','BS955A':'10'}
print("NOTE: tag + wand-side listener (LCCF4/L9336/L955A) positions are ESTIMATES. Anchors LB/LE/LF from AutoPos.")

def mean_cir(probe,tid):
    D=glob.glob(f'{BASE}/{probe}/listener_*')
    if not D: return None
    meta={}
    for r in csv.DictReader(open(f'{D[0]}/lcirm.csv')):
        if r['tag_id']!=tid: continue
        try: meta[r['accepted_polls']]=(float(r['fp_index'])/64.0,float(r['rxpacc']))
        except: pass
    segs={}
    for r in csv.DictReader(open(f'{D[0]}/lcird.csv')):
        if r['accepted_polls'] in meta: segs.setdefault(r['accepted_polls'],[]).append((int(r['offset']),r['hex']))
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
    return acc/n if n>20 else None

# build links: aligned mean CIR (index PRE = first path), normalized to FP peak; tail = index>PRE+3
links=[]
for tag,tid in TAGID.items():
    for rx in RX:
        m=mean_cir(rx,tid)
        if m is None: continue
        pk=m[PRE] if m[PRE]>0 else m.max()
        tail=m/pk
        base=np.linalg.norm(TX[tag]-RX[rx])
        # skip near-monostatic (baseline<0.3m) -> excess-delay unreliable
        if base<300: continue
        links.append((TX[tag],RX[rx],tail,base,f'{tag}->{rx}'))
print(f"{len(links)} usable bistatic links")

# grid (x,y) at torso/desk height z0
z0=900
xs=np.arange(-200,4700,40); ys=np.arange(-300,3400,40)
GX,GY=np.meshgrid(xs,ys)
img=np.zeros(GX.shape); hits=np.zeros(GX.shape)
for Tx,Rx,tail,base,name in links:
    dT=np.sqrt((GX-Tx[0])**2+(GY-Tx[1])**2+(z0-Tx[2])**2)
    dR=np.sqrt((GX-Rx[0])**2+(GY-Rx[1])**2+(z0-Rx[2])**2)
    excess=dT+dR-base                      # mm
    tap=PRE+np.round(excess/MM_PER_NS/SAMP_NS).astype(int)
    valid=(tap>=PRE+3)&(tap<PRE+POST)
    amp=np.zeros(GX.shape)
    amp[valid]=tail[tap[valid]]
    img+=amp; hits+=valid
img=img/np.maximum(hits,1)

# plot
fig,ax=plt.subplots(1,2,figsize=(17,6.5))
for a in ax:
    for lab,p in A.items(): a.plot(p[0],p[1],'s',ms=9,color='k'); a.annotate(lab,(p[0],p[1]),color='k',fontsize=9,xytext=(4,4),textcoords='offset points')
    for t,p in TX.items(): a.plot(p[0],p[1],'^',ms=11,color='blue')
    a.plot([A['A'][0],A['F'][0]],[A['A'][1],A['F'][1]],'r--',lw=1.5,label='A-F line (desk/PC occluder)')
    a.set_xlabel('x (mm)'); a.set_ylabel('y (mm)'); a.set_aspect('equal')
im0=ax[0].pcolormesh(xs,ys,img,shading='auto',cmap='hot'); fig.colorbar(im0,ax=ax[0])
ax[0].set_title('Back-projected reflector map (sum of link tails)')
# sharpened: emphasize where value is high AND many links agree
sharp=img*np.log1p(hits)
im1=ax[1].pcolormesh(xs,ys,sharp,shading='auto',cmap='hot'); fig.colorbar(im1,ax=ax[1])
ax[1].set_title('Sharpened (x link-count)')
ax[0].legend(fontsize=8,loc='upper left')
fig.suptitle(f'Multistatic CIR room image ({len(links)} links, static room, z={z0}mm). ▲=tags(EST) ■=anchors. Do walls/desk appear?',fontsize=11)
plt.tight_layout(rect=[0,0,1,0.95])
out=os.path.join(os.path.dirname(__file__),'figs_20260704','room_image.png'); os.makedirs(os.path.dirname(out),exist_ok=True)
plt.savefig(out,dpi=120); print('saved',out)

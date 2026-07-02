#!/usr/bin/env bash
# ANCHOR-B OCCLUSION (pivot 2026-07-02): pork wall standing 10-20cm in front of Anchor B,
# hood over the Anchor-B + Listener-B ~20cm pair, facing the tag zone. Fresnel ellipsoids of
# ALL tag->B links neck down at the B end => one hood covers the neck => occludes every link.
#   anchor-side EXP:  Anchor B (self-read all tags) vs L-B (lpd all tags)   [paper core]
#   anchor-side LOS ctrl: Anchor E vs L-E, Anchor F vs L-F                  [not occluded]
#   tag-side LOS ctrl:    L-9336 (near clear-LOS tag BS9336) vs anchor self-read of BS9336
# 4-layer verdict. Runs ONLY on explicit go: preflight -> 45s capture -> analysis. No reflash.
set -u
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start || exit 9
SP=/tmp/claude-1000/-home-zekaixiao-Documents-nRF-dev-BioSpur-UWB-before-start/dc7ef140-5e45-46bd-91a9-eafbab0d9d66/scratchpad
MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
declare -A PT=( [L955A]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00 \
                [L9336]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186071-if00 \
                [LB]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184545-if00 \
                [LE]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00 \
                [LF]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184964-if00 )
DUR=45; LDUR=160; PREROLL=10   # LDUR spans master-tag reset warmup (~60-90s, precedes recv --duration) + 45s ranging + margin, so listeners actually cover the ranging window
STAMP=$(date +%Y%m%d_%H%M%S); BASE=SS-TWR/alt-SS-TWR/broadcast/logs/anchorB_pork_${STAMP}
mkdir -p "$BASE"; ST="$BASE/STATUS.txt"; say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$ST"; }
for L in L955A L9336 LB LE LF; do test -e "${PT[$L]}" || { say "FATAL port missing $L ${PT[$L]}"; exit 2; }; done
say "BASE=$BASE  (pork hood on Anchor B; anchor-side proxy core)"
# ---- 1. PREFLIGHT (LSTAT heartbeat liveness; correct for self-heal fw, no traffic needed) ----
say "PREFLIGHT: 5 listeners must be alive (LSTAT heartbeat) ..."
bash "$SP/listener_preflight_lstat.sh" "$BASE/preflight" 2>&1 | tee -a "$ST"; pf=${PIPESTATUS[0]}
[ "$pf" -eq 0 ] || { say "ABORT: preflight FAILED (rc=$pf) - power-cycle listed boards, rerun"; exit 3; }
# ---- 2. countdown ----
say "5 listeners LIVE; countdown then RECORD ${DUR}s (pork static; you are clear of the ray zone)"
for i in $(seq $PREROLL -1 1); do echo "  T-$i"; sleep 1; done
say "RECORDING ${DUR}s"
# ---- 3. capture (5 listeners + recv) ----
declare -A cp
for L in L955A L9336 LB LE LF; do
  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py --port "${PT[$L]}" --baud 460800 --duration $LDUR --out-dir "$BASE/$L" > "$BASE/$L.console.log" 2>&1 & cp[$L]=$!
done
sleep 3
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py --port "$MT" \
  --targets BS9336,BS955A,BSCCF4 --duration $DUR --tr-hz 10 --controller-reset-snr 1050070698 \
  --skip-anchor-preflight --legacy-skip-link-ready-wait --no-silence-non-target-tags \
  --out-dir "$BASE/recv" > "$BASE/recv.console.log" 2>&1
say "recv rc=$?"; for L in L955A L9336 LB LE LF; do wait ${cp[$L]}; say "$L rc=$?"; done
grep -E "M1 anchor coverage" "$BASE/recv.console.log" | tee -a "$ST"
RECV=$(ls -d "$BASE"/recv_* 2>/dev/null | head -1); say "RECV=$RECV"
# ---- 4. FOUR-LAYER ANALYSIS ----
python3 - "$RECV/tag_rf_diag.csv" "$BASE/LB" "$BASE/LE" "$BASE/LF" "$BASE/L9336" <<'PY' 2>&1 | tee -a "$ST"
import sys,glob,csv,math
from collections import defaultdict
import numpy as np
trf,dLB,dLE,dLF,dL9336=sys.argv[1:6]
ANCH="ABCDEFGH"; TAG={2:"BS9336",3:"BS955A",4:"BSCCF4"}; TAGC=[2,3,4]
def dP(c,f1,f2,f3):
    s=f1*f1+f2*f2+f3*f3; return 10*math.log10(c*(2**17)/s) if (c>0 and s>0) else None
# anchor self-read: anc[anchor_id][tag_id] -> list of dP
anc=defaultdict(lambda: defaultdict(list))
for r in csv.DictReader(open(trf)):
    if str(r.get("anchor_diag_valid","0")) not in ("1","True","true"): continue
    try:
        a=int(r["anchor_id"]); t=int(r["tag_id"]); c=float(r["anchor_cir_pwr"])
        d=dP(c,float(r["anchor_fp1"]),float(r["anchor_fp2"]),float(r["anchor_fp3"]))
    except: continue
    if d is not None: anc[a][t].append(d)
def L(dirp,which):  # listener lpd (by tag_id) or lrd (by anchor_id)
    m=defaultdict(list); f=glob.glob(f"{dirp}/**/{which}.csv",recursive=True)
    if not f: return {}
    key="anchor_id" if which=="lrd" else "tag_id"
    for r in csv.DictReader(open(f[0])):
        try: k=int(r[key]); d=dP(float(r["cir_pwr"]),float(r["fp1"]),float(r["fp2"]),float(r["fp3"]))
        except: continue
        if d is not None: m[k].append(d)
    return {k:(float(np.median(v)),len(v)) for k,v in m.items()}
def ma(a,t):  # anchor a self-read of tag t -> (median dP, n)
    v=anc[a].get(t); return (float(np.median(v)),len(v)) if v and len(v)>=5 else (None,len(v) if v else 0)
def NL(x): return "NLOS" if (x is not None and x>=10) else "LOS "
def f(x): return ("%.1f"%x) if x is not None else "-"
def dd(a,b): return abs(a-b) if (a is not None and b is not None) else None
LB=L(dLB,'lpd'); LE=L(dLE,'lpd'); LF=L(dLF,'lpd'); L9=L(dL9336,'lrd')
Bid,Eid,Fid,Gid,Did = 1,4,5,6,3

print("### LAYER 1 — OCCLUSION VERIFY: every tag->B must be NLOS (>=10) ###")
any_nlos=False
for t in TAGC:
    dm,n=ma(Bid,t)
    if dm is not None and dm>=10: any_nlos=True
    print(f"  {TAG[t]:7s} -> B : dP={f(dm)} n={n}  {NL(dm)}")
print(f"  => occlusion {'OK (>=1 tag NLOS)' if any_nlos else 'NOT ACHIEVED — adjust pork'}")

print("\n### LAYER 2 — CORE anchor-side proxy at B (Anchor-B self-read vs L-B lpd; both behind pork) ###")
for t in TAGC:
    a=ma(Bid,t); l=LB.get(t,(None,0))
    print(f"  {TAG[t]:7s}: B-selfread={f(a[0])}({NL(a[0])} n{a[1]})  L-B-lpd={f(l[0])}({NL(l[0])} n{l[1]})  |Δ|={f(dd(a[0],l[0]))}")
print("  PASS if both NLOS AND |Δ| small (proxy faithful in NLOS).")

print("\n### LAYER 3 — LOS controls (expect LOS both sides, |Δ| small) ###")
print(" anchor-side E (Anchor-E self-read vs L-E lpd):")
for t in TAGC:
    a=ma(Eid,t); l=LE.get(t,(None,0))
    print(f"   {TAG[t]:7s}: E-selfread={f(a[0])}({NL(a[0])} n{a[1]})  L-E-lpd={f(l[0])}({NL(l[0])} n{l[1]})  |Δ|={f(dd(a[0],l[0]))}")
print(" anchor-side F (Anchor-F self-read vs L-F lpd):")
for t in TAGC:
    a=ma(Fid,t); l=LF.get(t,(None,0))
    print(f"   {TAG[t]:7s}: F-selfread={f(a[0])}({NL(a[0])} n{a[1]})  L-F-lpd={f(l[0])}({NL(l[0])} n{l[1]})  |Δ|={f(dd(a[0],l[0]))}")
print(" tag-side BS9336 (Anchor self-read of BS9336 vs L-9336 lrd; exclude G=intrinsic NLOS):")
for a in range(8):
    if a==Gid: continue
    am=ma(a,2); lr=L9.get(a,(None,0))
    if am[0] is None and lr[0] is None: continue
    print(f"   {ANCH[a]} : anc-selfread(BS9336)={f(am[0])}({NL(am[0])} n{am[1]})  L9336-lrd={f(lr[0])}({NL(lr[0])} n{lr[1]})  |Δ|={f(dd(am[0],lr[0]))}")

print("\n### LAYER 4 — ENVIRONMENT BASELINE ###")
g=ma(Gid,2); print(f"  BS9336->G (known intrinsic NLOS ~10.8): dP={f(g[0])} n={g[1]}  -> EXCLUDE from LOS reads")
for a,nm in ((Did,'D'),(Eid,'E')):
    v=ma(a,3); print(f"  BS955A->{nm} (you absent => clean): dP={f(v[0])} n={v[1]}  {NL(v[0])}")
print("\nVERDICT KEYS: Layer1 gate (>=1 tag->B NLOS) THEN Layer2 core |Δ| small w/ both NLOS,")
print("Layer3 LOS pairs |Δ| small (scale comparable, no stray), Layer4 confirms clean surroundings.")
PY
say "ANCHORB_PORK_DONE $BASE"

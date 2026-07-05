#!/usr/bin/env python3
"""Prompt-1 phase-stability audit (rigorous). Decides if COHERENT SAR + RESPIRATION are
physically possible on this rig, using ONLY existing static-room data.

Improvements over the earlier step0_audit.py (which rounded FP and got a crude 15 deg):
  - RF config READ FROM FIRMWARE (Ch5 -> lambda), not assumed.
  - Complex CIR reassembled with byte order VERIFIED against firmware accumulator dump:
      firmware dwt_readaccdata(chunk,len+1,off) reads ACC_MEM raw, discards dummy chunk[0]
      (uses chunk[1+i]), prints bytes MSnibble-first => bytes.fromhex recovers exact stream.
      DW1000 ACC_MEM = 1016 complex samples, each = int16 real, int16 imag, little-endian.
      (real/imag swap = i*conj => FP-referenced circular-std is INVARIANT, so sigma_phi robust.)
  - FRACTIONAL first-path alignment via FFT phase-ramp (fp=fp_index/64, keep frac); FP -> tap K.
  - FP-reference z[n] <- z[n]/z[K] cancels per-frame carrier/clock phase (clock-independent).
  - Circular statistics (mean resultant length), NOT linear std of unwrapped angle.
  - Time structure: sigma_phi vs averaging window (1/10/100/1000 s), overnight drift, coherence.

Usage: python3 phase_audit.py [n_chunks_for_full_night]  (default all)
"""
import csv, glob, os, sys, collections, numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ---------------- RF config (from firmware, verified) ----------------
C_MMPS   = 299792458.0 * 1e-9 * 1000.0   # mm per ns  (~299.79 mm/ns)
CH5_FC   = 6489.6e6                       # Channel 5 centre freq (Hz)  [APP_UWB_CHANNEL=5U]
LAMBDA_MM= 299792458.0 / CH5_FC * 1000.0  # ~46.2 mm
SAMP_NS  = 1.0016                         # DW1000 accumulator sample period (ns/tap) @64MHz PRF
NSAMP    = 1016                           # ACC_MEM = 1016 complex samples (acc_len=4064 bytes)
# carrier_integrator -> ppm  (DW1000 API constants, chan 5)
FREQ_OFFSET_MULT = 998.4e6/2.0/1024.0/131072.0        # Hz per LSB  (~3.719)
HZ_TO_PPM_CH5    = -1.0e6/CH5_FC                       # ppm per Hz (~ -1.541e-4)
CI_TO_PPM        = FREQ_OFFSET_MULT * HZ_TO_PPM_CH5    # ppm per LSB

BASE   = 'logs/overnight_soak_v2_20260704_032348'
OUTDIR = 'handoff_scripts_20260704/figs_20260704'
os.makedirs(OUTDIR, exist_ok=True)
NCHUNK = int(sys.argv[1]) if len(sys.argv) > 1 else 99
# soak tag_id map (from recv CFG assigned): 2=BS9336 3=BS955A 4=BSCCF4
TAGNAME = {'2':'BS9336','3':'BS955A','4':'BSCCF4'}
LISTENERS = ['LCCF4','LB','LE','LF']       # full-CIR that session (L9336/L955A scalar-only)
PRIMARY = [('LCCF4','BSCCF4'), ('LB','BSCCF4')]   # quasi-monostatic, bistatic
K = 48                                      # target FP tap inside aligned window
WSTART_BACK = 48; WLEN = 192                # window = round(fp)-48 .. +144  (fits fp up to ~841)

def circ_R(u):            # mean resultant length of unit phasors (u already unit or complex)
    return np.abs(np.mean(u/np.abs(u), axis=0))
def circ_std_deg(R):
    R = np.clip(R, 1e-12, 1.0)
    return np.degrees(np.sqrt(np.maximum(-2.0*np.log(R), 0.0)))

def frac_align(c, fp):
    """Extract WLEN window and FFT-fractional-shift so first path lands exactly at tap K."""
    start = int(round(fp)) - WSTART_BACK
    if start < 0 or start + WLEN > NSAMP:
        return None
    w = c[start:start+WLEN].astype(complex)
    frac = fp - start - K                    # current FP position = K + frac; shift by -frac
    k = np.fft.fftfreq(WLEN) * WLEN
    w = np.fft.ifft(np.fft.fft(w) * np.exp(1j*2*np.pi*k*frac/WLEN))  # delay by -frac
    return w

def iter_frames(listener, chunks):
    """Yield (tag_id, host_epoch_s, carrier_integrator, rxpacc, fp, cir_pwr, complexCIR192@K)."""
    cdirs = sorted(glob.glob(f'{BASE}/chunk*'))[:chunks]
    for cd in cdirs:
        for ld in sorted(glob.glob(f'{cd}/{listener}/listener_*')):
            mfile = f'{ld}/lcirm.csv'; dfile = f'{ld}/lcird.csv'
            if not (os.path.exists(mfile) and os.path.exists(dfile)):
                continue
            meta = {}
            for r in csv.DictReader(open(mfile)):
                try:
                    meta[r['accepted_polls']] = (
                        r['tag_id'], float(r['host_epoch_s']), float(r['carrier_integrator']),
                        float(r['rxpacc']), float(r['fp_index'])/64.0, float(r['cir_pwr']))
                except (ValueError, KeyError):
                    pass
            # stream lcird, flush a frame when a new frame starts (offset resets to 0)
            buf = {}; cur = None
            def emit(ap, slices):
                if ap not in meta: return None
                if not slices: return None
                try:
                    tot = b''.join(bytes.fromhex(slices[o]) for o in sorted(slices)
                                   if len(slices[o]) % 2 == 0)
                except ValueError:
                    return None                 # serial glitch -> skip frame
                if len(tot) != NSAMP*4: return None
                iq = np.frombuffer(tot, dtype='<i2').astype(np.float64)
                c = iq[0::2] + 1j*iq[1::2]      # real, imag interleaved LE (firmware-verified)
                tag, ep, ci, rx, fp, pw = meta[ap]
                w = frac_align(c, fp)
                if w is None: return None
                return (tag, ep, ci, rx, fp, pw, w)
            for r in csv.DictReader(open(dfile)):
                try:
                    ap = r['accepted_polls']; off = int(r['offset'])
                except (ValueError, KeyError):
                    continue
                if off == 0 and cur is not None:
                    out = emit(cur, buf)
                    if out is not None: yield out
                    buf = {}
                cur = ap; buf[off] = r['hex']
            if cur is not None:
                out = emit(cur, buf)
                if out is not None: yield out

def collect(listener, tagname, chunks):
    Z=[]; EP=[]; CI=[]; RX=[]; FP=[]; PW=[]
    want = [k for k,v in TAGNAME.items() if v==tagname]
    for tag, ep, ci, rx, fp, pw, w in iter_frames(listener, chunks):
        if tag not in want: continue
        Z.append(w); EP.append(ep); CI.append(ci); RX.append(rx); FP.append(fp); PW.append(pw)
    if not Z: return None
    Z=np.array(Z); EP=np.array(EP); CI=np.array(CI); RX=np.array(RX); FP=np.array(FP); PW=np.array(PW)
    # quality filter: rxpacc>=0.7*median, fp within link median +-3 tap
    rxmed=np.median(RX); fpmed=np.median(FP)
    ok = (RX>=0.7*rxmed) & (np.abs(FP-fpmed)<=3.0)
    Z,EP,CI,RX,FP,PW = Z[ok],EP[ok],CI[ok],RX[ok],FP[ok],PW[ok]
    if len(Z)<30: return None
    order=np.argsort(EP)
    return dict(Z=Z[order],EP=EP[order],CI=CI[order],RX=RX[order],FP=FP[order],PW=PW[order],
                listener=listener,tag=tagname)

def analyze(d):
    Z=d['Z']; ref=Z[:,K:K+1]
    Zr = Z/ref                                   # FP-referenced (per-frame clock removed)
    mag = np.abs(Z).mean(0)
    noise = np.median(mag[K-16:K-6])             # pre-FP noise floor
    taps = np.arange(K+3, K+65)
    R = circ_R(Zr[:,taps])
    sig = circ_std_deg(R)
    snr = 20*np.log10(np.maximum(mag[taps],1e-9)/max(noise,1e-9))
    magmean=np.abs(Z[:,taps]).mean(0); magstd=np.abs(Z[:,taps]).std(0)
    cv = magstd/np.maximum(magmean,1e-9)
    excess = (taps-K)*SAMP_NS
    d.update(sig=sig,snr=snr,cv=cv,excess=excess,taps=taps,magall=mag,noise=noise,Zr=Zr)
    return d

def sigma_vs_window(d, tap, windows=(1,10,100,1000)):
    """circular std of FP-ref phase at a tap, computed within time windows of length T then
    median over windows. Small T -> short-term jitter; large T -> includes drift."""
    ph = np.angle(d['Zr'][:,tap]); ep=d['EP']-d['EP'][0]; out={}
    for T in windows:
        sig=[]
        b0=0
        # contiguous time windows
        edges=np.arange(0, ep[-1]+T, T)
        idx=np.digitize(ep, edges)
        for g in np.unique(idx):
            sel=idx==g
            if sel.sum()>=8:
                R=np.abs(np.mean(np.exp(1j*ph[sel])))
                sig.append(circ_std_deg(R))
        out[T]=np.median(sig) if sig else np.nan
    return out

# ============================ RUN ============================
print("="*76)
print("RF CONFIG (from firmware SS-TWR/.../broadcast: APP_UWB_CHANNEL=5U, dwt_config):")
print(f"  Channel 5  fc={CH5_FC/1e6:.1f} MHz  BW 499.2 MHz  lambda={LAMBDA_MM:.1f} mm")
print(f"  PRF 64MHz, PLEN128, PAC8, code9, non-std SFD, 6.8Mbps, PHR STD, SFD-TO 129")
print(f"  ACC sample={SAMP_NS} ns/tap (={SAMP_NS*C_MMPS:.1f} mm path/tap), NSAMP={NSAMP}")
print(f"  carrier_integrator -> ppm: 1 LSB = {FREQ_OFFSET_MULT:.4f} Hz = {CI_TO_PPM*1e3:.4f} milli-ppm")
print("="*76)

# ---- all 12 links: sigma table (use 2 chunks for speed; primary links get full night below)
rows=[]
LINKS = [(L,T) for L in LISTENERS for T in ('BS9336','BS955A','BSCCF4')]
results={}
for L,T in LINKS:
    ch = NCHUNK if (L,T) in PRIMARY else min(NCHUNK,2)
    d = collect(L,T,ch)
    if d is None:
        print(f"  {L:6s}<-{T:7s}: no usable frames"); continue
    d = analyze(d)
    results[(L,T)] = d
    # median sigma over echo taps with SNR>10
    m = d['snr']>10
    sig10 = np.median(d['sig'][m]) if m.any() else np.nan
    ci_ppm = d['CI']*CI_TO_PPM
    rows.append((L,T,len(d['Z']),d['FP'].mean(),d['noise'],sig10,
                 float(np.nanmin(d['sig'][m])) if m.any() else np.nan,
                 ci_ppm.mean(), ci_ppm.std()))
    print(f"  {L:6s}<-{T:7s}: N={len(d['Z']):5d} FPtap={d['FP'].mean():6.1f} "
          f"medSNRtail={np.median(d['snr']):4.1f}dB  sigma_phi(SNR>10)={sig10:5.1f}deg "
          f"min={rows[-1][6]:4.1f}  CFO={ci_ppm.mean():+.2f}+-{ci_ppm.std():.2f}ppm")

# ---- primary-link detailed tables + figures
print("\n"+"="*76+"\nPER-TAP TABLE (primary links, full night)\n"+"="*76)
for L,T in PRIMARY:
    if (L,T) not in results: continue
    d=results[(L,T)]
    print(f"\n  {L} <- {T}   N={len(d['Z'])} frames   noise floor={d['noise']:.2f}")
    print(f"  {'tap':>4} {'excess_ns':>9} {'excess_mm':>9} {'SNRdB':>6} {'sigma_phi':>9} {'Rbar':>6} {'magCV':>6}")
    for i,tp in enumerate(d['taps']):
        if d['snr'][i] < 3: continue
        R=circ_R(d['Zr'][:,tp:tp+1])[0]
        print(f"  {tp-K:4d} {d['excess'][i]:9.2f} {d['excess'][i]*C_MMPS:9.1f} "
              f"{d['snr'][i]:6.1f} {d['sig'][i]:9.1f} {R:6.3f} {d['cv'][i]:6.3f}")

# ---- figure 1: sigma_phi vs excess delay (all links)
plt.figure(figsize=(9,6))
for (L,T),d in results.items():
    m=d['snr']>6
    plt.plot(d['excess'][m], d['sig'][m], marker='.', label=f'{L}<-{T}', alpha=0.7)
plt.axhline(40,color='r',ls='--',label='40 deg coherent threshold')
plt.axhline(10,color='g',ls='--',label='10 deg respiration threshold')
plt.xlabel('excess delay past first path (ns)'); plt.ylabel('sigma_phi (deg, FP-referenced)')
plt.title('Phase stability vs excess delay (static room)'); plt.legend(fontsize=7); plt.grid(alpha=0.3)
plt.ylim(0,180); plt.tight_layout(); plt.savefig(f'{OUTDIR}/phase_sigma_vs_excess.png',dpi=110); plt.close()

# ---- figures 2-4 on the quasi-monostatic primary link
if ('LCCF4','BSCCF4') in results:
    d=results[('LCCF4','BSCCF4')]
    # dominant tail bumps: local maxima in mag beyond K+3 with SNR>10
    tail=d['magall'].copy(); tail[:K+3]=0
    cand=[t for t in range(K+3,K+80) if tail[t]==max(tail[max(t-2,0):t+3]) and
          20*np.log10(tail[t]/d['noise'])>10]
    bumps=sorted(cand, key=lambda t:-tail[t])[:3]
    print(f"\n  dominant tail bumps (LCCF4<-BSCCF4) at taps {[(b-K) for b in bumps]} "
          f"= {[round((b-K)*SAMP_NS*C_MMPS) for b in bumps]} mm path excess")
    t0=d['EP'][0]; tmin=(d['EP']-t0)/60.0
    # fig2: phase vs time
    plt.figure(figsize=(10,5))
    for b in bumps:
        ph=np.unwrap(np.angle(d['Zr'][:,b]))
        plt.plot(tmin, np.degrees(ph-ph.mean()), '.', ms=2, label=f'tap +{b-K} ({round((b-K)*SAMP_NS*C_MMPS)}mm)')
    plt.xlabel('time (min)'); plt.ylabel('FP-ref phase - mean (deg)')
    plt.title('LCCF4<-BSCCF4 tail-tap phase over night'); plt.legend(); plt.grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(f'{OUTDIR}/phase_vs_time.png',dpi=110); plt.close()
    # fig3: sigma vs averaging window
    plt.figure(figsize=(8,5))
    for b in bumps:
        sw=sigma_vs_window(d,b); ks=sorted(sw);
        plt.plot(ks,[sw[k] for k in ks],'o-',label=f'tap +{b-K}')
    plt.axhline(10,color='g',ls='--'); plt.axhline(40,color='r',ls='--')
    plt.xscale('log'); plt.xlabel('averaging window T (s)'); plt.ylabel('sigma_phi (deg)')
    plt.title('LCCF4<-BSCCF4 phase std vs window (short=jitter, long=+drift)')
    plt.legend(); plt.grid(alpha=0.3); plt.tight_layout()
    plt.savefig(f'{OUTDIR}/phase_sigma_vs_window.png',dpi=110); plt.close()
    print("\n  sigma_phi vs averaging window (LCCF4<-BSCCF4):")
    for b in bumps:
        sw=sigma_vs_window(d,b)
        print(f"    tap +{b-K}: " + "  ".join(f"{k}s={sw[k]:.1f}deg" for k in sorted(sw)))
    # carrier_integrator drift
    ci_ppm=d['CI']*CI_TO_PPM
    print(f"\n  CFO (carrier_integrator) LCCF4<-BSCCF4: {ci_ppm.mean():+.3f} ppm, "
          f"std {ci_ppm.std():.3f}, overnight drift {ci_ppm[-100:].mean()-ci_ppm[:100].mean():+.3f} ppm")

# ---- VERDICT
print("\n"+"="*76+"\nVERDICT\n"+"="*76)
all_sig=[]; short_sig=[]
for (L,T),d in results.items():
    m=d['snr']>10
    if m.any(): all_sig.append(np.median(d['sig'][m]))
if ('LCCF4','BSCCF4') in results:
    d=results[('LCCF4','BSCCF4')]
    tail=d['magall'].copy(); tail[:K+3]=0
    b=int(np.argmax(tail))
    sw=sigma_vs_window(d,b); short_sig=[sw[1],sw[10]]
med=np.nanmedian(all_sig) if all_sig else np.nan
print(f"  median sigma_phi over echo taps (SNR>10dB), across links = {med:.1f} deg")
if short_sig: print(f"  short-term jitter (1s / 10s window) on strongest bump = {short_sig[0]:.1f} / {short_sig[1]:.1f} deg")
print(f"  -> COHERENT SAR: {'VIABLE' if med<40 else 'DEAD'} (threshold 40 deg)")
if short_sig:
    print(f"  -> RESPIRATION (mm displacement): {'VIABLE' if short_sig[1]<10 else 'MARGINAL/DEAD'} (10s jitter vs 10 deg)")
print("  figures in", OUTDIR)

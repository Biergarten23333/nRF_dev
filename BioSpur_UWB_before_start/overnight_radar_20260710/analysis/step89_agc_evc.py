#!/usr/bin/env python3
"""STEP 8 (AGC confirmation) + STEP 9 (EVC health summary).
EVC counters are 12-bit wrapping (mod 4096); diff consecutive LSTAT snapshots."""
import os, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(HERE, "parsed")
OUT = os.path.join(HERE, "health"); os.makedirs(OUT, exist_ok=True)
LISTENERS = ["LB", "LE", "LF", "L9336", "L955A", "LCCF4"]
CLEAN = {"LB", "LE", "LF", "L9336", "L955A"}
# LSTAT cols: 0 lid,1 near,2 good_frames,3 accepted_polls,4 ignored_nonpoll,
# 5 ignored_poll_mask,6 bad_header,7 too_long,8 rx_errors,9 full_cir_captures,
# 10 ring_drops,11 self_recover,12 rx_enable_failures,13 fps,
# 14 evc_fcg,15 evc_fce,16 evc_ovr,17 evc_sto
EVC = {"fcg": 14, "fce": 15, "ovr": 16, "sto": 17}


def wrapped_sum(series, mod=4096):
    s = series.astype(np.int64)
    d = np.diff(s)
    d = np.mod(d, mod)          # handle 12-bit wrap
    return int(d.sum()), d


# ---- STEP 8: AGC confirmation ----
print("=== STEP 8 — AGC (EDG1) confirmation (expected 0 on all frames) ===")
agc_report = {}
for L in LISTENERS:
    z = np.load(os.path.join(PARSED, f"{L}_scalar.npz"))
    d = z["data"]; cols = list(z["cols"]); ci = {c: i for i, c in enumerate(cols)}
    agc = d[:, ci["agc"]]
    nz = int((agc != 0).sum())
    agc_report[L] = {"rows": int(agc.size), "agc_nonzero": nz,
                     "max_agc": int(agc.max()) if agc.size else 0}
    print(f"  {L:<7} rows={agc.size:>9,}  agc!=0: {nz}  (max={agc_report[L]['max_agc']})")
all_zero = all(v["agc_nonzero"] == 0 for v in agc_report.values())
print(f"  VERDICT: {'CONFIRMED agc==0 on every frame, all listeners' if all_zero else 'ANOMALY — nonzero AGC present'}")

# ---- STEP 9: EVC health ----
print("\n=== STEP 9 — EVC hardware health (night totals via wrap-aware deltas) ===")
evc_report = {}
print(f"  {'listener':<8}{'good(fcg)':>12}{'crc_err(fce)':>13}{'rx_ovr':>9}{'sfd_to(sto)':>12}"
      f"{'crc_rate':>10}{'sto_rate':>10}{'spikes':>8}")
for L in LISTENERS:
    z = np.load(os.path.join(PARSED, f"{L}_lstat.npz")); d = z["data"]
    if d.shape[0] < 2:
        evc_report[L] = {"lstat_rows": int(d.shape[0]), "note": "too few LSTAT"}
        print(f"  {L:<8}  (only {d.shape[0]} LSTAT rows)")
        continue
    tot = {}; deltas = {}
    for name, col in EVC.items():
        tot[name], deltas[name] = wrapped_sum(d[:, col])
    # firmware good_frames (col 2) is a non-wrapping uint32 -> cleaner night total
    gf_total = int(d[:, 2].max() - d[:, 2].min())
    fcg = max(tot["fcg"], 1)
    crc_rate = tot["fce"] / fcg
    sto_rate = tot["sto"] / fcg
    # anomalous spikes: intervals where fce delta > mean+5*std
    fce_d = deltas["fce"]
    spike = int((fce_d > (fce_d.mean() + 5 * fce_d.std() + 3)).sum()) if fce_d.size else 0
    evc_report[L] = {"lstat_rows": int(d.shape[0]), "good_frames_fw": gf_total,
                     "good_fcg_hw": tot["fcg"], "crc_err_fce": tot["fce"],
                     "rx_ovr": tot["ovr"], "sfd_to_sto": tot["sto"],
                     "crc_rate": crc_rate, "sto_rate": sto_rate, "fce_spikes": spike}
    print(f"  {L:<8}{gf_total:>12,}{tot['fce']:>13,}{tot['ovr']:>9,}{tot['sto']:>12,}"
          f"{crc_rate:>10.2e}{sto_rate:>10.2e}{spike:>8}")

with open(os.path.join(OUT, "step89_agc_evc.json"), "w") as f:
    json.dump({"agc": agc_report, "agc_all_zero": all_zero, "evc": evc_report}, f, indent=2)
print(f"\n[Step8/9] wrote {OUT}/step89_agc_evc.json")

#!/usr/bin/env python3
"""Phase 4 power-sweep analysis.
Per (tag x anchor x level): range mean/std, valid rate (miss proxy),
anchor_fp1 (anchor RX of the swept tag poll = power-sensitive), tag_fp1,
std_noise; per-level tag-side tr_lde_thresh/tr_agc_stat1 from RFD lines.
Listener fp1 time series with cell-boundary overlay + within-cell jump flag.
Outputs results.json + figures."""
import csv, json, os, glob, statistics as st, re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

EXP = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/experiments/power_campaign_20260714"
BCAST = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast"
FIG = os.path.join(EXP, "figures"); os.makedirs(FIG, exist_ok=True)
# dB above register floor on operative P250 byte (for x-axis ordering)
DB = {"MAX": 8.5, "M3": 5.5, "POR": 4.0, "M6": 2.5, "M12": 0.0}

def fnum(x):
    try: return float(x)
    except: return None

def load_cell_ranges(csv_path):
    """-> {(tag,anchor): {range:[], afp1:[], tfp1:[], astd:[], tstd:[], n_total, n_valid}}"""
    d = {}
    tot = {}
    with open(csv_path) as f:
        for x in csv.DictReader(f):
            tag = x.get("peer_name", ""); anc = x.get("anchor_id", "")
            k = (tag, anc)
            tot[k] = tot.get(k, 0) + 1
            if x.get("valid") not in ("1", "True", "true"):
                continue
            e = d.setdefault(k, {"range": [], "afp1": [], "tfp1": [], "astd": [], "tstd": []})
            for col, key in [("range_mm", "range"), ("anchor_fp1", "afp1"),
                             ("tag_fp1", "tfp1"), ("anchor_std_noise", "astd"),
                             ("tag_std_noise", "tstd")]:
                v = fnum(x.get(col))
                if v is not None: e[key].append(v)
    for k in d:
        d[k]["n_total"] = tot.get(k, 0)
        d[k]["n_valid"] = len(d[k]["range"])
    return d, tot

RFD_RE = re.compile(r"\[RECV\]\s+(BS\w+)\s+notify:\s*RFD;([0-9;]+)")
def load_cell_rfd(rawlog):
    """tag-side lde_thresh/agc_stat1 per tag (fields 28,29 after RFD split)."""
    out = {}
    if not os.path.exists(rawlog): return out
    with open(rawlog, errors="replace") as f:
        for ln in f:
            m = RFD_RE.search(ln)
            if not m: continue
            tag = m.group(1); parts = ("RFD;" + m.group(2)).split(";")
            if len(parts) < 30: continue
            lt = fnum(parts[28]); ag = fnum(parts[29])
            e = out.setdefault(tag, {"lde": [], "agc": [], "afp1": []})
            if lt is not None: e["lde"].append(lt)
            if ag is not None: e["agc"].append(ag)
            af = fnum(parts[10])  # ap_fp_ampl1
            if af is not None: e["afp1"].append(af)
    return out

def load_listener_lpd(lpd_path):
    rows = []
    if not os.path.exists(lpd_path): return rows
    with open(lpd_path) as f:
        for x in csv.DictReader(f):
            t = fnum(x.get("host_epoch") or x.get("host_epoch_s"))
            tms = fnum(x.get("listener_t_ms"))
            fp1 = fnum(x.get("fp1")); std = fnum(x.get("std_noise"))
            rows.append({"host": t, "tms": tms, "fp1": fp1, "std": std,
                         "tag": x.get("tag_id", "")})
    return rows

def mean(a): return round(st.mean(a), 1) if a else None
def pstd(a): return round(st.pstdev(a), 1) if len(a) > 1 else None

def main():
    meta = json.load(open(os.path.join(EXP, "sweep", "sweep_meta.json")))
    cells = meta["cells"]
    # ---- per-cell range/diag ----
    per_level = {}
    for c in cells:
        lvl = c["level"]; d = os.path.join(BCAST, c["out_dir"]) if not c["out_dir"].startswith("/") else c["out_dir"]
        jc = os.path.join(d, "range_diag_joined.csv"); rl = os.path.join(d, "raw.log")
        links, tot = (load_cell_ranges(jc) if os.path.exists(jc) else ({}, {}))
        rfd = load_cell_rfd(rl)
        per_level[lvl] = {"dir": d, "links": links, "rfd": rfd,
                          "cap_start": c["cap_start_epoch"], "cap_end": c["cap_end_epoch"],
                          "ack": c.get("ack")}
    # ---- per (tag,anchor,level) table ----
    tags = sorted({k[0] for lv in per_level.values() for k in lv["links"]})
    anchors = sorted({k[1] for lv in per_level.values() for k in lv["links"]}, key=lambda a: int(a) if a.isdigit() else 99)
    order = meta["order"]
    table = []
    for t in tags:
        for a in anchors:
            row = {"tag": t, "anchor": a, "by_level": {}}
            for lvl in order:
                e = per_level[lvl]["links"].get((t, a))
                if e:
                    row["by_level"][lvl] = {
                        "range_mean": mean(e["range"]), "range_std": pstd(e["range"]),
                        "anchor_fp1_mean": mean(e["afp1"]), "tag_fp1_mean": mean(e["tfp1"]),
                        "anchor_std_mean": mean(e["astd"]), "tag_std_mean": mean(e["tstd"]),
                        "n_valid": e["n_valid"], "n_total": e.get("n_total", 0),
                        "valid_rate": round(e["n_valid"]/e["n_total"], 3) if e.get("n_total") else None}
                else:
                    row["by_level"][lvl] = None
            # bias shift vs MAX
            base = row["by_level"].get("MAX")
            if base and base["range_mean"] is not None:
                for lvl in order:
                    bl = row["by_level"][lvl]
                    if bl and bl["range_mean"] is not None:
                        bl["bias_vs_MAX_mm"] = round(bl["range_mean"] - base["range_mean"], 1)
            table.append(row)
    # ---- per-level summary ----
    lvl_summ = {}
    for lvl in order:
        allv = [v for k in per_level[lvl]["links"] for v in [per_level[lvl]["links"][k]]]
        nt = sum(e.get("n_total", 0) for e in allv); nv = sum(e["n_valid"] for e in allv)
        afp1 = [x for e in allv for x in e["afp1"]]
        rfd = per_level[lvl]["rfd"]
        lde = [x for t in rfd for x in rfd[t]["lde"]]; agc = [x for t in rfd for x in rfd[t]["agc"]]
        rfd_afp1 = [x for t in rfd for x in rfd[t]["afp1"]]
        ratio = (mean(afp1)/mean(lde)) if (afp1 and lde and mean(lde)) else None
        lvl_summ[lvl] = {"db_above_floor": DB[lvl], "n_total": nt, "n_valid": nv,
                         "valid_rate": round(nv/nt, 3) if nt else None,
                         "miss_rate": round(1-nv/nt, 3) if nt else None,
                         "anchor_fp1_mean": mean(afp1), "tr_lde_thresh_mean": mean(lde),
                         "tr_agc_stat1_mean": mean(agc),
                         "anchor_fp1_over_lde_ratio": round(ratio, 3) if ratio else None}
    # ---- listeners ----
    listeners = {}
    for lp in sorted(glob.glob(os.path.join(EXP, "listener", "**", "lpd.csv"), recursive=True)):
        name = os.path.relpath(lp, os.path.join(EXP, "listener")).split(os.sep)[0]
        rows = load_listener_lpd(lp)
        # assign to cells by host epoch
        per_cell = {}
        for r in rows:
            if r["host"] is None: continue
            for c in cells:
                if c["cap_start_epoch"] <= r["host"] <= c["cap_end_epoch"]:
                    per_cell.setdefault(c["level"], []).append(r["fp1"])
                    break
        cell_stats = {lvl: {"fp1_mean": mean([v for v in per_cell.get(lvl, []) if v is not None]),
                            "fp1_std": pstd([v for v in per_cell.get(lvl, []) if v is not None]),
                            "n": len(per_cell.get(lvl, []))} for lvl in order}
        # within-cell jump flag: fp1_std/fp1_mean > 0.5 => flag contamination
        for lvl in order:
            cs = cell_stats[lvl]
            cs["contaminated"] = bool(cs["fp1_mean"] and cs["fp1_std"] and cs["fp1_std"] > 0.5*cs["fp1_mean"])
        listeners[name] = {"n_rows": len(rows), "by_level": cell_stats}
    out = {"order": order, "level_db": DB, "level_summary": lvl_summ,
           "per_link": table, "listeners": listeners,
           "note": "Tag sweeps POLL power; power-sensitive first-path amplitude = anchor_fp1 (anchor RX of poll) and listener fp1. tag-side tr_lde/tr_agc reflect fixed-power anchor responses. anchor-side lde/agc not carried over-air (=0)."}
    json.dump(out, open(os.path.join(EXP, "results.json"), "w"), indent=2)

    # ---- figures ----
    xs = [DB[l] for l in order]
    # 1. bias vs power (per link, vs MAX)
    plt.figure(figsize=(8, 5))
    for row in table:
        ys = [row["by_level"][l].get("bias_vs_MAX_mm") if row["by_level"][l] else None for l in order]
        if any(y is not None for y in ys):
            plt.plot([x for x, y in zip(xs, ys) if y is not None],
                     [y for y in ys if y is not None], "o-", alpha=0.5, label=f"{row['tag']}-a{row['anchor']}")
    plt.axhline(0, color="k", lw=0.5); plt.xlabel("TX power (dB above reg floor, P250)"); plt.ylabel("range bias vs MAX (mm)")
    plt.title("Range bias vs TX power (per tag-anchor link)"); plt.grid(alpha=0.3)
    plt.legend(fontsize=6, ncol=2); plt.tight_layout(); plt.savefig(os.path.join(FIG, "bias_vs_power.png"), dpi=110); plt.close()
    # 2. anchor_fp1 + valid_rate vs power
    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    ax[0].plot(xs, [lvl_summ[l]["anchor_fp1_mean"] for l in order], "o-", label="anchor_fp1 (poll RX)")
    ax[0].set_xlabel("TX power (dB above floor)"); ax[0].set_ylabel("mean anchor_fp1"); ax[0].set_title("First-path amplitude vs power"); ax[0].grid(alpha=0.3); ax[0].legend()
    ax[1].plot(xs, [lvl_summ[l]["valid_rate"] for l in order], "s-", color="crimson")
    ax[1].set_xlabel("TX power (dB above floor)"); ax[1].set_ylabel("valid (non-miss) rate"); ax[1].set_title("Link success vs power (frame drop-off)"); ax[1].grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(os.path.join(FIG, "fp1_and_validrate_vs_power.png"), dpi=110); plt.close()
    # 3. std vs power (per level mean range std)
    plt.figure(figsize=(8, 5))
    std_by_lvl = {l: mean([row["by_level"][l]["range_std"] for row in table if row["by_level"][l] and row["by_level"][l].get("range_std") is not None]) for l in order}
    plt.plot(xs, [std_by_lvl[l] for l in order], "o-", color="darkgreen")
    plt.xlabel("TX power (dB above floor)"); plt.ylabel("mean per-link range std (mm)"); plt.title("Range noise (std) vs power"); plt.grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(os.path.join(FIG, "std_vs_power.png"), dpi=110); plt.close()
    # 4. agc/lde ratio vs power
    plt.figure(figsize=(8, 5))
    plt.plot(xs, [lvl_summ[l]["tr_agc_stat1_mean"] for l in order], "o-", label="tr_agc_stat1 (tag-side)")
    plt.plot(xs, [lvl_summ[l]["anchor_fp1_over_lde_ratio"] for l in order], "s-", label="anchor_fp1/tr_lde ratio")
    plt.xlabel("TX power (dB above floor)"); plt.ylabel("value"); plt.title("AGC / first-path margin vs power"); plt.grid(alpha=0.3); plt.legend()
    plt.tight_layout(); plt.savefig(os.path.join(FIG, "agc_fpratio_vs_power.png"), dpi=110); plt.close()
    # 5. listener fp1 timeseries with cell boundaries
    if listeners:
        plt.figure(figsize=(12, 5))
        t0 = meta.get("sweep_start_epoch") or cells[0]["cap_start_epoch"]
        for lp in sorted(glob.glob(os.path.join(EXP, "listener", "**", "lpd.csv"), recursive=True)):
            name = os.path.relpath(lp, os.path.join(EXP, "listener")).split(os.sep)[0]; rows = load_listener_lpd(lp)
            ts = [(r["host"]-t0) for r in rows if r["host"] and r["fp1"] is not None]
            fp = [r["fp1"] for r in rows if r["host"] and r["fp1"] is not None]
            if ts: plt.plot(ts, fp, ".", ms=2, alpha=0.5, label=name)
        for c in cells:
            plt.axvspan(c["cap_start_epoch"]-t0, c["cap_end_epoch"]-t0, alpha=0.08)
            plt.text((c["cap_start_epoch"]+c["cap_end_epoch"])/2-t0, plt.ylim()[1]*0.95, c["level"], ha="center", fontsize=8)
        plt.xlabel("time since sweep start (s)"); plt.ylabel("listener fp1"); plt.title("Listener first-path amplitude across sweep (cells shaded)"); plt.grid(alpha=0.3); plt.legend()
        plt.tight_layout(); plt.savefig(os.path.join(FIG, "listener_fp1_timeseries.png"), dpi=110); plt.close()

    print("=== LEVEL SUMMARY ===")
    print(f"{'lvl':5}{'dB':>5}{'valid%':>8}{'miss%':>7}{'anc_fp1':>9}{'tr_lde':>8}{'tr_agc':>10}")
    for l in order:
        s = lvl_summ[l]
        print(f"{l:5}{s['db_above_floor']:>5}{(s['valid_rate'] or 0)*100:>8.1f}{(s['miss_rate'] or 0)*100:>7.1f}{str(s['anchor_fp1_mean']):>9}{str(s['tr_lde_thresh_mean']):>8}{str(s['tr_agc_stat1_mean']):>10}")
    print("\n=== LISTENERS (per-cell fp1) ===")
    for name, L in listeners.items():
        print(f" {name}: rows={L['n_rows']}")
        for l in order:
            b = L["by_level"][l]; print(f"   {l:5} fp1_mean={b['fp1_mean']} std={b['fp1_std']} n={b['n']} contaminated={b['contaminated']}")
    print("\nfigures ->", FIG)

if __name__ == "__main__":
    main()

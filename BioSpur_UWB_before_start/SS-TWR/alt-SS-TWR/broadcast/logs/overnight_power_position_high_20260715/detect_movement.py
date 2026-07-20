#!/usr/bin/env python3
"""Movement detector for the position_high run — the operator walked in-room during capture.
Uses the 7-listener fleet as a passive motion sensor: a real person perturbs SEVERAL
listeners' received power (cir_pwr) coincidentally in time, whereas per-link noise is
independent. Method: 1 s cir_pwr time series per listener -> rolling-median baseline ->
robust excursion flag (|resid| > K*MAD) -> cross-listener coincidence (>= COINC listeners
in the same second) = a movement event. Events are merged and mapped to power cells so the
downstream analysis can mark movement-contaminated cells instead of assuming a clean room."""
import csv, glob, os, json, re, datetime, statistics
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]
BIN_S = 1.0
BASE_WIN = 31        # rolling-median baseline window (bins ~ seconds)
K_MAD = 4.0          # excursion threshold in robust sigmas
COINC = 3            # >= this many listeners excurse in the same bin -> movement

# power-cell windows
cells = []
for d in glob.glob(os.path.join(ROOT, "round_*", "*_3min_*")):
    m = re.search(r"/(MAX|M3|M6|M12|POR)_3min_(\d{8})_(\d{6})$", d)
    if m:
        ts = datetime.datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S").timestamp()
        cells.append((ts, ts + 200.0, m.group(1), os.path.basename(d)))
cells.sort()


def cell_of(ep):
    for s, e, lvl, name in cells:
        if s <= ep <= e:
            return name, lvl
    return None, None


def load_series(name):
    eps, cir = [], []
    for lpd in sorted(glob.glob(os.path.join(ROOT, "listener", name, "listener_*", "lpd.csv"))):
        with open(lpd) as fh:
            for r in csv.DictReader(fh):
                try:
                    e = float(r["host_epoch_s"]); c = float(r["cir_pwr"])
                except Exception:
                    continue
                if c > 0:
                    eps.append(e); cir.append(c)
    return np.array(eps), np.array(cir)


def rolling_median(x, w):
    n = len(x); h = w // 2; out = np.empty(n)
    for i in range(n):
        out[i] = np.median(x[max(0, i - h):min(n, i + h + 1)])
    return out


def main():
    if not cells:
        print("no cells; abort"); return
    t0 = min(c[0] for c in cells); t1 = max(c[1] for c in cells)
    nb = int((t1 - t0) / BIN_S) + 1
    grid = t0 + np.arange(nb) * BIN_S
    excur = np.zeros((len(LISTENERS), nb), dtype=bool)
    resid_store = {}
    active = []
    for li, name in enumerate(LISTENERS):
        eps, cir = load_series(name)
        if len(cir) < BASE_WIN:
            continue
        active.append(name)
        # bin to grid (median cir per bin)
        idx = np.clip(((eps - t0) / BIN_S).astype(int), 0, nb - 1)
        binned = np.full(nb, np.nan)
        for b in range(nb):
            v = cir[idx == b]
            if len(v):
                binned[b] = np.median(v)
        # fill gaps by forward/back fill for baseline stability
        ok = ~np.isnan(binned)
        if ok.sum() < BASE_WIN:
            continue
        filled = binned.copy()
        filled[~ok] = np.interp(np.flatnonzero(~ok), np.flatnonzero(ok), binned[ok])
        base = rolling_median(filled, BASE_WIN)
        resid = filled - base
        mad = np.median(np.abs(resid - np.median(resid))) or 1.0
        thr = K_MAD * 1.4826 * mad
        ex = ok & (np.abs(resid) > thr)
        excur[li] = ex
        resid_store[name] = {"mad": round(float(1.4826 * mad), 1), "thr": round(float(thr), 1),
                             "n_excursion_bins": int(ex.sum())}
    coinc = excur.sum(axis=0)   # how many listeners excurse per bin
    is_evt = coinc >= COINC
    # merge adjacent event bins
    events = []
    b = 0
    while b < nb:
        if is_evt[b]:
            s = b
            while b < nb and is_evt[b]:
                b += 1
            e = b - 1
            mid = grid[(s + e) // 2]
            cname, clvl = cell_of(mid)
            nl = int(coinc[s:e + 1].max())
            who = [LISTENERS[i] for i in range(len(LISTENERS)) if excur[i, s:e + 1].any()]
            events.append({"start_iso": datetime.datetime.fromtimestamp(grid[s]).strftime("%H:%M:%S"),
                           "dur_s": round((e - s + 1) * BIN_S, 1), "peak_listeners": nl,
                           "listeners": who, "cell": cname, "power": clvl})
        else:
            b += 1
    # per-cell movement fraction
    cell_frac = {}
    for s, e, lvl, name in cells:
        bs = int((s - t0) / BIN_S); be = int((e - t0) / BIN_S)
        seg = is_evt[max(0, bs):min(nb, be + 1)]
        cell_frac[name] = {"power": lvl, "movement_bins": int(seg.sum()),
                           "total_bins": int(len(seg)),
                           "movement_frac": round(float(seg.mean()), 3) if len(seg) else 0.0}
    out = {"active_listeners": active, "coinc_threshold": COINC, "k_mad": K_MAD,
           "n_events": len(events), "total_movement_seconds": round(float(is_evt.sum() * BIN_S), 1),
           "span_seconds": round(float(t1 - t0), 1),
           "movement_duty_pct": round(100 * float(is_evt.mean()), 1),
           "per_listener": resid_store, "events": events, "per_cell_movement": cell_frac}
    json.dump(out, open(os.path.join(ROOT, "movement_events.json"), "w"), indent=1)

    print(f"active listeners: {len(active)}/7 {active}")
    print(f"movement events: {len(events)}  duty={out['movement_duty_pct']}% of "
          f"{out['span_seconds']:.0f}s  ({out['total_movement_seconds']:.0f}s moving)")
    print("\nmost-contaminated cells (by movement fraction):")
    for name, d in sorted(cell_frac.items(), key=lambda kv: -kv[1]["movement_frac"])[:8]:
        print(f"  {name:>26} {d['power']:>4}  movement_frac={d['movement_frac']:.3f}")
    print("\nfirst 15 events:")
    for ev in events[:15]:
        print(f"  {ev['start_iso']}  {ev['dur_s']:>4}s  {ev['peak_listeners']}L "
              f"{ev['power'] or '-':>4} {ev['cell'] or '-'}  {'/'.join(ev['listeners'])}")
    print("->", os.path.join(ROOT, "movement_events.json"))


if __name__ == "__main__":
    main()

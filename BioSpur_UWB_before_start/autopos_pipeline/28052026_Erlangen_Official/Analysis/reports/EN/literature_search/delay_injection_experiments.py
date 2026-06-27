"""
Two experiments confirming the per-tag delay -> vertical (GPS clock->altitude) mechanism.

EXP 1 (main, non-circular, REAL Vicon truth): localize each static tag from real
ranges against truth anchors under 3 delay treatments and compare vertical vs
horizontal error to truth:
    (a) no delay handling (b=0)
    (b) single global fixed delay (one calibration constant for all tags)
    (c) per-tag estimated delay (4-param solve)
Prediction: vertical error drops a->b->c; horizontal essentially flat.

EXP 2 (supporting, closed-form sanity): inject a known common-mode Delta_b into all
of a tag's ranges, re-localize with b unmodelled, sweep Delta_b, measure the
vertical/horizontal shift slope. Prediction: vertical slope ~ 1.576, horiz ~ 0.23.
"""
import sys, csv
SCR = "/tmp/claude-1000/-home-zekaixiao-Documents-nRF-dev-BioSpur-UWB-before-start/bf909886-ad48-495f-b091-a809435e28fc/scratchpad"
sys.argv = ["t", SCR]
import numpy as np
from collections import defaultdict
import harness as H
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
np.set_printoptions(precision=3, suppress=True)

A = H.TRUTH.astype(float)            # truth anchors, vertical = col 1 (Y)
ERL = H.ERL
TRUTH_CSV = ERL / "Analysis/official_extra_analysis/FULL_AutoPos_one_baseline_scale_correction/tables/static_abs_errors_per_session.csv"

# ---- truth tag positions per static ID --------------------------------------
truth = {}
with open(TRUTH_CSV) as f:
    for r in csv.DictReader(f):
        ID = r["ID"]
        if ID in truth:
            continue
        try:
            truth[ID] = np.array([float(r["truth_x_mm"]), float(r["truth_y_vertical_mm"]), float(r["truth_z_mm"])])
        except (ValueError, KeyError):
            pass
print(f"truth tag positions loaded: {len(truth)} static IDs")

# ---- real ranges per static ID: median range per anchor ---------------------
def ranges_for(ID):
    d = H.cap_dir(f"static_{ID}_")
    if not d:
        return None
    rows = H.load_cap(d, ID, 0)               # all sweeps
    per = defaultdict(list)
    for r in rows:
        per[r["anchor"]].append(r["range_mm"])
    return {a: float(np.median(v)) for a, v in per.items() if len(v) >= 5}

# ---- localizers --------------------------------------------------------------
def loc3(meas, b=0.0):                          # 3-param trilateration, fixed delay b
    aids = list(meas); p = A[aids].mean(0).copy()
    for _ in range(60):
        Jr, res = [], []
        for a in aids:
            d = p - A[a]; nd = max(np.linalg.norm(d), 1e-6)
            Jr.append(d / nd); res.append(nd + b - meas[a])
        dp = np.linalg.lstsq(np.array(Jr), -np.array(res), rcond=None)[0]
        p += dp
        if np.linalg.norm(dp) < 1e-5: break
    return p

def loc4(meas):                                 # 4-param: estimate position + delay b
    aids = list(meas); p = A[aids].mean(0).copy(); b = 0.0
    for _ in range(60):
        Jr, res = [], []
        for a in aids:
            d = p - A[a]; nd = max(np.linalg.norm(d), 1e-6)
            Jr.append(np.r_[d / nd, 1.0]); res.append(nd + b - meas[a])
        step = np.linalg.lstsq(np.array(Jr), -np.array(res), rcond=None)[0]
        p += step[:3]; b += step[3]
        if np.linalg.norm(step) < 1e-5: break
    return p, b

# gather valid IDs with truth + ranges
data = {}
for ID, tp in truth.items():
    mr = ranges_for(ID)
    if mr and len(mr) >= 4:
        data[ID] = (tp, mr)
print(f"usable static IDs (truth + >=4 anchors): {len(data)}")

# per-tag estimated delays -> global constant
b_est = {ID: loc4(mr)[1] for ID, (tp, mr) in data.items()}
b_global = float(np.median(list(b_est.values())))
print(f"per-tag estimated delay: median={b_global:.1f} mm  "
      f"(range {min(b_est.values()):.0f}..{max(b_est.values()):.0f})")

def err(p, tp):
    e = p - tp
    return abs(e[1]), float(np.hypot(e[0], e[2]))   # vertical(Y), horizontal(XZ)

# ---- EXP 1: three treatments vs real truth ----------------------------------
rows = {"(a) no delay (b=0)": [], "(b) global fixed delay": [], "(c) per-tag estimated": []}
for ID, (tp, mr) in data.items():
    rows["(a) no delay (b=0)"].append(err(loc3(mr, 0.0), tp))
    rows["(b) global fixed delay"].append(err(loc3(mr, b_global), tp))
    rows["(c) per-tag estimated"].append(err(loc4(mr)[0], tp))

print("\n==================== EXP 1: delay treatment vs REAL Vicon truth ====================")
print(f"{'treatment':28s} {'vertical(Y) med':>16s} {'horizontal(XZ) med':>20s}")
base_v = None
for k, v in rows.items():
    arr = np.array(v); mv, mh = np.median(arr[:, 0]), np.median(arr[:, 1])
    if base_v is None: base_v = mv
    print(f"{k:28s} {mv:>13.1f} mm {mh:>17.1f} mm   (vert vs (a): {mv-base_v:+.1f})")

# ---- EXP 2: synthetic common-mode delay injection, slope ---------------------
dbs = np.arange(-60, 61, 10.0)
dy, dh = [], []
for db in dbs:
    sy, sh = [], []
    for ID, (tp, mr) in data.items():
        p0 = loc3(mr, 0.0)
        pdb = loc3({a: r + db for a, r in mr.items()}, 0.0)   # inject db, don't model it
        sy.append(pdb[1] - p0[1]); sh.append(np.hypot(pdb[0] - p0[0], pdb[2] - p0[2]))
    dy.append(np.median(sy)); dh.append(np.median(sh))
dy, dh = np.array(dy), np.array(dh)
sv = np.polyfit(dbs, dy, 1)[0]
# horizontal magnitude vs |db|
sh_slope = np.polyfit(np.abs(dbs), dh, 1)[0]
print("\n==================== EXP 2: synthetic Delta_b injection (b unmodelled) ====================")
print(f"vertical shift slope  dZ/d(db)  = {sv:+.3f}   (theory |w_vert|  = 1.576)")
print(f"horizontal |shift| slope /|db|  = {sh_slope:+.3f}   (theory |w_horiz| = 0.229)")

fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
ax[0].plot(dbs, dy, "o-", label=f"measured (slope {sv:.2f})")
ax[0].plot(dbs, 1.576 * dbs, "--", color="gray", label="theory 1.576")
ax[0].set_xlabel("injected common delay $\\Delta b$ (mm)"); ax[0].set_ylabel("vertical shift $\\Delta Z$ (mm)")
ax[0].set_title("Vertical: common delay aliases ~1.6x into Z"); ax[0].legend(); ax[0].grid(alpha=.3)
ax[1].plot(np.abs(dbs), dh, "s-", color="C1", label=f"measured (slope {sh_slope:.2f})")
ax[1].plot(np.abs(dbs), 0.229 * np.abs(dbs), "--", color="gray", label="theory 0.229")
ax[1].set_xlabel("|injected common delay| (mm)"); ax[1].set_ylabel("horizontal |shift| (mm)")
ax[1].set_title("Horizontal: common delay barely moves XZ"); ax[1].legend(); ax[1].grid(alpha=.3)
plt.tight_layout()
out = ERL / "Analysis/reports/EN/literature_search/delay_injection_sweep.png"
plt.savefig(out, dpi=130)
print(f"\nplot saved: {out}")

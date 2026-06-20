# Source excerpt from run_full_evaluation_same_pipeline_20260513.py
# V3-lite: median/MAD directional fusion, no anchor delays.

def mad_sigma(vals, floor=0.1):
    arr = np.asarray(list(vals), dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    return max(float(floor), 1.4826 * mad)

def fuse_v3(ab, ba, allv):
    med_ab = float(np.median(ab if ab.size else allv))
    med_ba = float(np.median(ba if ba.size else allv))
    sig_ab = mad_sigma(ab if ab.size else allv, 0.1)
    sig_ba = mad_sigma(ba if ba.size else allv, 0.1)
    return float((sig_ba**2 * med_ab + sig_ab**2 * med_ba) / (sig_ab**2 + sig_ba**2))

def solve_v3_lite(pair_dists, anchor_ids):
    return solve_autopos_v1(pair_dists, anchor_ids)

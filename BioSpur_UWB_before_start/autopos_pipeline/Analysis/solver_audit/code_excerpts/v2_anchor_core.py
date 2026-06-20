# Source excerpt from run_full_evaluation_same_pipeline_20260513.py
# V2: inverse-variance directional fusion + weak no-delay NLS regularization.

def fuse_v2(ab, ba, allv):
    mean_ab = float(np.mean(ab if ab.size else allv))
    mean_ba = float(np.mean(ba if ba.size else allv))
    var_ab = max(1.0, float(np.var(ab if ab.size > 1 else allv, ddof=1)))
    var_ba = max(1.0, float(np.var(ba if ba.size > 1 else allv, ddof=1)))
    return float((var_ba * mean_ab + var_ab * mean_ba) / (var_ab + var_ba))

def solve_autopos_v2(pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    x = mds_init(lp, len(anchor_ids))
    result = None
    lam = 0.01
    for _ in range(3):
        x, result = nls_refine(x, lp, lam=lam)
        lam *= 0.5
    return x, result

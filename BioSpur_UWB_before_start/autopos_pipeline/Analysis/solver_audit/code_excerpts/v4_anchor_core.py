# Source excerpt from run_full_evaluation_same_pipeline_20260513.py
# V4/v4-io: joint bounded independent anchor delays, Huber loss, physical priors.

def solve_v4(pair_dists, anchor_ids, x_init=None):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = mds_init(lp, n)
    pmap = pos_param_map(n)
    def unpack(v):
        x = unpack_pos(v[:len(pmap)], n)
        d = np.zeros(n)
        if n > 1:
            d[1:] = v[len(pmap):]
        return x, d
    def fun(v):
        x, dly = unpack(v)
        out = [(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0 for (i, j), dist in lp.items()]
        if n > 1:
            out.extend((dly[1:] / 20.0).tolist())
        out.extend(physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out)
    x0 = np.r_[pack_pos(x_init), np.zeros(max(0, n - 1))]
    lo = np.r_[np.full(len(pmap), -np.inf), np.full(max(0, n - 1), -60.0)]
    hi = np.r_[np.full(len(pmap), np.inf), np.full(max(0, n - 1), 60.0)]
    result = least_squares(fun, x0, loss="huber", f_scale=2.0, bounds=(lo, hi), max_nfev=5000)
    x, dly = unpack(result.x)
    return gauge_align_local(x), dly, result

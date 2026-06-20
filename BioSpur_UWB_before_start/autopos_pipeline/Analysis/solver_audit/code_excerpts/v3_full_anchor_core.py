# Source excerpt from run_full_evaluation_same_pipeline_20260513.py
# V3-full: Tukey IRLS plus alternating median per-anchor delay updates.

def solve_v3_full(pair_dists, anchor_ids):
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    x = mds_init(lp, n)
    dly = np.zeros(n)
    for it in range(50):
        resids = np.asarray([np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - d for (i, j), d in lp.items()])
        sigma = max(mad_sigma(resids, 0.0), 5.0)
        c_t = 4.685 * sigma
        weights = np.asarray([(1 - (r / c_t) ** 2) ** 2 if abs(r) <= c_t else 0.0 for r in resids])
        def fun(v):
            cur = unpack_pos(v, n)
            return np.asarray([
                math.sqrt(max(0.0, weights[idx])) * (np.linalg.norm(cur[i] - cur[j]) + dly[i] + dly[j] - dist)
                for idx, ((i, j), dist) in enumerate(lp.items())
            ])
        result = least_squares(fun, pack_pos(x), loss="linear", method="trf", max_nfev=1000)
        x = gauge_align_local(unpack_pos(result.x, n))
        for i in range(1, n):
            est = []
            for (a, b), dist in lp.items():
                other = b if a == i else a if b == i else None
                if other is not None:
                    est.append(dist - np.linalg.norm(x[i] - x[other]) - dly[other])
            if est:
                dly[i] = float(np.median(est))
    return x, dly, result

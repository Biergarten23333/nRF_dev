# Source excerpt from run_full_evaluation_same_pipeline_20260513.py
# V5/common-mode: d_i = c + e_i. Existing V5 artifact used e_reg=20; current code can disable e_i.

def solve_v4_common_mode(pair_dists, anchor_ids, x_init=None, *, c_init=0.0, e_init=None,
                         e_reg_scale_mm=0.0, use_per_anchor_ei=None,
                         loss="huber", f_scale_mm=30.0, residual_sigma_mm=15.0, max_nfev=5000):
    if use_per_anchor_ei is None:
        use_per_anchor_ei = bool(e_init is not None or (np.isfinite(e_reg_scale_mm) and e_reg_scale_mm > 0.0))
    lp, _g2l, _l2g = local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = mds_init(lp, n)
    pmap = pos_param_map(n)
    def unpack(v):
        x = unpack_pos(v[:len(pmap)], n)
        c = float(v[len(pmap)])
        e = np.asarray(v[len(pmap)+1:len(pmap)+1+n], dtype=float) if use_per_anchor_ei else np.zeros(n)
        return x, c, e
    def residual(v):
        x, c, e = unpack(v)
        dly = c + e
        out = [(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / residual_sigma_mm for (i, j), dist in lp.items()]
        if use_per_anchor_ei:
            out.extend((e / e_reg_scale_mm).tolist())
            out.append(float(np.mean(e) / 1.0))
        out.extend(physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)
    # x0/bounds include c and optional e_i. least_squares uses normalized f_scale = f_scale_mm/residual_sigma_mm.

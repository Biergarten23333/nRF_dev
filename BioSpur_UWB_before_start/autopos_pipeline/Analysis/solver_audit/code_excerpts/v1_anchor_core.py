# Source excerpts from run_clean_full_compare.py and run_full_evaluation_same_pipeline_20260513.py
# V1/v1-old: bidirectional mean pair fusion; classical MDS baseline / helper MDS+NLS.

def fuse_v1(all_directed_values):
    return float(np.mean(all_directed_values))

def solve_v1_old(mod, pair_dists, anchor_ids):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    x = mod.mds_init(lp, len(anchor_ids))
    dly = np.zeros(len(anchor_ids), dtype=float)
    return x, dly, {"implementation": "archive_v1_classical_mds_only", "delay_aware": False}

def solve_autopos_v1(pair_dists, anchor_ids):
    return solve_mds_nls(pair_dists, anchor_ids)

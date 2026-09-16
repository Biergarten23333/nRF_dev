"""Read-only saved-range geometry audit; no fusion replay or estimator edits."""
import argparse
import json
from pathlib import Path

import numpy as np


def audit(source):
    with np.load(source, allow_pickle=False) as f:
        z = {k: f[k] for k in f.files}
    a = z['anchors_world_m']
    ranges = z['source_range_mm'].astype(float) / 1000
    valid = ((z['source_valid_mask'][:, None].astype(int) >> np.arange(8)) & 1).astype(bool)
    valid &= (ranges > 0) & (ranges < 65.535)
    selected = ((z['selected_valid_mask'][:, None].astype(int) >> np.arange(8)) & 1).astype(bool) & valid
    t = z['uwb_time_s']
    idx = np.clip(np.searchsorted(z['time_s'], t, side='right') - 1, 0, len(z['time_s']) - 1)
    from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
    ji = {str(n): i for i, n in enumerate(z['joint_names'])}
    pi = np.array([ji[NODE_TO_PROXY_POINT[str(n)]] for n in z['uwb_node']])
    tag = z['roots_b_posterior'][idx] + z['joints_relative'][idx, pi]
    offset = z['joints_relative'][idx, pi]
    mirrored_tag = z['roots_b_posterior'][idx] + offset * [-1, 1, 1]
    bias = z['persistent_range_bias_prior_m']
    residual = ranges - bias - np.linalg.norm(tag[:, None] - a, axis=2)
    mirror_residual = ranges - bias - np.linalg.norm(mirrored_tag[:, None] - a, axis=2)
    # Exact squared-range difference for near-vertical pairs, conditioned on
    # candidate XY only. Also retain full-XY-box possible height intervals.
    dz = a[4:, 2] - a[:4, 2]
    da = a[4:] - a[:4]
    s = ranges[:, :4]**2 - ranges[:, 4:]**2 + (a[4:]**2).sum(1) - (a[:4]**2).sum(1)
    pair_z = (s - 2 * (tag[:, None, :2] * da[None, :, :2]).sum(2)) / (2 * dz)
    pair_valid = valid[:, :4] & valid[:, 4:]
    corners = np.array([[x, y] for x in (a[:, 0].min(), a[:, 0].max()) for y in (a[:, 1].min(), a[:, 1].max())])
    terms = corners @ da[:, :2].T / dz
    zlo = s/(2*dz) - terms.max(0)
    zhi = s/(2*dz) - terms.min(0)
    # An interval intersection failure means no XY anywhere in the anchor
    # horizontal bounding box can reconcile all those vertical pairs.
    lo = np.max(np.where(pair_valid, zlo, -np.inf), axis=1)
    hi = np.min(np.where(pair_valid, zhi, np.inf), axis=1)
    pair_gap = np.where(pair_valid.sum(1) >= 2, np.maximum(lo-hi, 0), np.nan)
    excesses, kept_excesses = [], []
    for i in range(8):
        for j in range(i+1, 8):
            d = np.linalg.norm(a[i]-a[j])
            excess = np.maximum(np.abs(ranges[:, i]-ranges[:, j])-d, d-ranges[:, i]-ranges[:, j])
            excesses.append(np.where(valid[:, i] & valid[:, j], excess, np.nan))
            kept_excesses.append(np.where(selected[:, i] & selected[:, j], excess, np.nan))
    ex = np.nanmax(np.stack(excesses), axis=0)
    with np.errstate(invalid='ignore'):
        kept = np.stack(kept_excesses)
        ex_kept = np.max(np.where(np.isfinite(kept), kept, -np.inf), axis=0)
    rows = []
    masks = [('full', np.ones(len(t), bool)), ('initial_5_30s', (t-t[0]>=5)&(t-t[0]<=30))]
    for scope, mask in masks:
        for node in np.unique(z['uwb_node']):
            m = mask & (z['uwb_node']==node)
            pz = np.where(pair_valid[m], pair_z[m], np.nan)
            use = selected & m[:, None]
            rows.append(dict(scope=scope,node=str(node),sweeps=int(m.sum()),
                fixed_root_retained_abs_residual_median_m=float(np.median(np.abs(residual[use]))),
                reflected_x_retained_abs_residual_median_m=float(np.median(np.abs(mirror_residual[use]))),
                triangle_excess_over_10cm=int(np.sum(ex[m]>.1)),
                retained_triangle_excess_over_10cm=int(np.sum(ex_kept[m]>.1)),
                triangle_excess_p95_m=float(np.nanpercentile(ex[m],95)),
                vertical_pair_gap_median_m=float(np.nanmedian(pair_gap[m])),
                vertical_pair_gap_p95_m=float(np.nanpercentile(pair_gap[m],95)),
                pair_z_median_m=np.nanmedian(pz,axis=0).tolist(),
                model_tag_z_median_m=float(np.median(tag[m,2]))))
    return dict(source=str(source),role='GEOMETRY_COMPATIBILITY_NOT_POSITION_TRUTH',
        note='Pair heights use candidate XY; interval gaps permit any XY within anchor horizontal box. Measurements have slightly different epochs; small violations can include motion. Reflection is a fixed-old-root/mask/bias counterfactual using previous IMU sample, not a corrected estimator or physical truth. No thresholds control estimator.',
        anchors_m=a.tolist(), rows=rows)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('source',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    result=audit(args.source)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(result,indent=2))

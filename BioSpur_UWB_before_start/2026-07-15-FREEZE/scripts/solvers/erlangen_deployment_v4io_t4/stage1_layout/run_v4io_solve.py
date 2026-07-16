#!/usr/bin/env python3
"""Run the PRODUCTION v4-io anchor-layout solver on a fresh inter-anchor sweep.

This imports the outdoor `run_clean_full_compare.py` module (the same solver
`run_v4io_field_check.py` drives) and calls its real solve path for version
"v4-io":  init = solve_autopos_v1(fused v3) ; solve_v4(fused v3, init).

We drive ONLY the 8-anchor layout solve (not the roto/wand/tag-capture field
evaluation, which needs fresh tag captures we do not have). The layout math is
byte-for-byte the production code.

Usage: run_v4io_solve.py <pairs_all.csv> <out_layout.json>
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start")
FC_PATH = REPO / "autopos_pipeline" / "outdoor_20260513" / "run_clean_full_compare.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # required so @dataclass in the module resolves (py3.12)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    pairs_csv = Path(sys.argv[1]).resolve()
    out_json = Path(sys.argv[2]).resolve()

    fc = load_module(FC_PATH, "fc_v4io")
    # Point the production module at the fresh sweep.
    fc.SWEEP_CSV = pairs_csv
    # EVAL solver impl stays the production one (fc.EVAL untouched).
    mod = fc.load_eval_module()

    anchor_ids = list(range(8))
    raw = fc.load_sweep_grouped()          # directed medians from pairs_all.csv
    present = sorted({a for k in raw for a in k})
    fused = fc.fuse_all(mod, raw, anchor_ids)
    layout = fc.solve_version(mod, "v4-io", fused, anchor_ids)

    # Per-pair inter-anchor residual stats for the layout (fit quality).
    import numpy as np
    res = []
    for i in range(8):
        for j in range(i + 1, 8):
            d = fused["v3"].get((i, j)) or fused["v3"].get((j, i))
            if d is None:
                continue
            pred = float(np.linalg.norm(layout.x[i] - layout.x[j]) + layout.dly[i] + layout.dly[j])
            res.append(pred - float(d))
    rms = float(np.sqrt(np.mean(np.square(res)))) if res else float("nan")
    stats = {
        "inter_anchor_pair_rms_mm": round(rms, 2),
        "n_pairs": len(res),
        "anchors_present": "".join(fc.ANCHORS[i] for i in present),
        "solver": "v4-io (production run_clean_full_compare.solve_version)",
        "source_pairs_csv": str(pairs_csv),
    }
    fc.save_layout(out_json, layout, anchor_ids, stats=stats)
    print(f"[v4-io] solved 8-anchor layout -> {out_json}")
    print(f"[v4-io] inter-anchor pair RMS = {rms:.1f} mm over {len(res)} pairs; anchors={stats['anchors_present']}")
    for li, gi in enumerate(anchor_ids):
        x = layout.x[li]
        print(f"    {fc.ANCHORS[gi]}: x={x[0]:8.1f} y={x[1]:8.1f} z={x[2]:8.1f}  d_anchor={layout.dly[li]:+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE / "make_w05_data_driven_activity_region.py"


def load_module():
    spec = importlib.util.spec_from_file_location("activity_region", SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_module()
    original_margin = mod.EDGE_MARGIN_MM
    original_thresh = mod.QUALITY_THRESH_MM
    try:
        mod.QUALITY_THRESH_MM = 90.0
        for margin in [150.0, 250.0, 350.0]:
            mod.EDGE_MARGIN_MM = margin
            rows = [mod.plot_solver("V3"), mod.plot_solver("V4")]
            # Rename the freshly overwritten outputs so every variant stays visible.
            for solver in ["V3", "V4"]:
                outdir = mod.BASE / f"{solver}_solver"
                src = outdir / f"fig_w05_{solver.lower()}_data_driven_activity_region_xy.png"
                dst = outdir / f"fig_w05_{solver.lower()}_data_driven_activity_region_margin{int(margin)}_xy.png"
                src.replace(dst)
            mod.write_csv(mod.BASE / f"w05_data_driven_activity_region_summary_margin{int(margin)}.csv", rows)
        print("Wrote margin variants: 150, 250, 350 mm")
    finally:
        mod.EDGE_MARGIN_MM = original_margin
        mod.QUALITY_THRESH_MM = original_thresh


if __name__ == "__main__":
    main()

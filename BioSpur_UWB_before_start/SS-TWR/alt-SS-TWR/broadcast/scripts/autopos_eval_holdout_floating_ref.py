#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


ANCHORS = tuple("ABCDEFGH")


def load_anchor_layout(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_anchor_map(layout: dict[str, Any]) -> dict[str, list[float]]:
    """
    Accept common layout formats:
    - solve_anchor_layout*.py: {"anchors": {"A":[x,y,z], ...}, "units":"m|mm"}
    - v3_full: {"anchors":[{"label":"A","x_mm":..,"y_mm":..,"z_mm":..}, ...], "units":"mm"}
    """
    anchors = layout.get("anchors")
    units = str(layout.get("units") or "m").lower()
    scale = 0.001 if units == "mm" else 1.0

    out: dict[str, list[float]] = {}
    if isinstance(anchors, dict):
        for k in ANCHORS:
            v = anchors.get(k)
            if isinstance(v, list) and len(v) >= 3:
                out[k] = [float(v[0]) * scale, float(v[1]) * scale, float(v[2]) * scale]
    elif isinstance(anchors, list):
        for e in anchors:
            if not isinstance(e, dict):
                continue
            lbl = str(e.get("label") or "").strip().upper()
            if lbl not in ANCHORS:
                continue
            if "x_mm" in e:
                out[lbl] = [float(e["x_mm"]) * 0.001, float(e["y_mm"]) * 0.001, float(e.get("z_mm", 0.0)) * 0.001]
            else:
                out[lbl] = [float(e.get("x", 0.0)) * scale, float(e.get("y", 0.0)) * scale, float(e.get("z", 0.0)) * scale]
    else:
        raise ValueError("layout missing anchors")

    if len(out) != len(ANCHORS):
        raise ValueError("layout missing some anchors")
    return out


def find_solved_reference_m(layout: dict[str, Any], session_dir: Path | None) -> list[float] | None:
    # v1/v2/v3-lite format
    fr = layout.get("floating_reference_constraints")
    if isinstance(fr, list) and fr:
        if session_dir is None:
            if len(fr) == 1 and isinstance(fr[0], dict):
                v = fr[0].get("solved_reference_m")
                if isinstance(v, list) and len(v) == 3:
                    return [float(v[0]), float(v[1]), float(v[2])]
            return None

        want = str(session_dir.resolve())
        for entry in fr:
            if not isinstance(entry, dict):
                continue
            sd = entry.get("session_dir")
            if not isinstance(sd, str):
                continue
            try:
                got = str((Path(sd)).resolve())
            except Exception:
                got = sd
            if got == want:
                v = entry.get("solved_reference_m")
                if isinstance(v, list) and len(v) == 3:
                    return [float(v[0]), float(v[1]), float(v[2])]
        return None

    # v3_full format
    fr2 = layout.get("floating_reference")
    if not isinstance(fr2, list) or not fr2:
        return None
    if session_dir is None:
        if len(fr2) != 1 or not isinstance(fr2[0], dict):
            return None
        rp = fr2[0].get("ref_point_mm") or fr2[0].get("ref_point_m")
        if isinstance(rp, dict) and all(k in rp for k in ("x", "y", "z")):
            scale = 0.001 if "ref_point_mm" in fr2[0] else 1.0
            return [float(rp["x"]) * scale, float(rp["y"]) * scale, float(rp["z"]) * scale]
        return None

    want = str(session_dir.resolve())
    for entry in fr2:
        if not isinstance(entry, dict):
            continue
        sd = entry.get("session_dir")
        if not isinstance(sd, str):
            continue
        try:
            got = str((Path(sd)).resolve())
        except Exception:
            got = sd
        if got != want:
            continue
        rp = entry.get("ref_point_mm") or entry.get("ref_point_m")
        if isinstance(rp, dict) and all(k in rp for k in ("x", "y", "z")):
            scale = 0.001 if "ref_point_mm" in entry else 1.0
            return [float(rp["x"]) * scale, float(rp["y"]) * scale, float(rp["z"]) * scale]
    return None


def percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if p <= 0:
        return sorted_vals[0]
    if p >= 100:
        return sorted_vals[-1]
    idx = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    w = idx - lo
    return sorted_vals[lo] * (1.0 - w) + sorted_vals[hi] * w


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate a solved anchor layout against holdout floating-reference ranges.csv."
    )
    ap.add_argument("--layout", required=True, help="anchor_layout_*.json containing anchors{} and solved_reference_m")
    ap.add_argument("--holdout-session", required=True, help="Directory containing ranges.csv (holdout)")
    ap.add_argument(
        "--train-session",
        default=None,
        help="Optional train session dir to select the corresponding solved_reference_m inside layout json.",
    )
    ap.add_argument("--out", required=True, help="Output markdown report path")
    ap.add_argument("--min-quality", type=int, default=0, help="Ignore samples with quality_percent < this")
    args = ap.parse_args()

    layout_path = Path(args.layout)
    holdout_dir = Path(args.holdout_session)
    train_dir = Path(args.train_session) if args.train_session else None
    out_path = Path(args.out)

    layout = load_anchor_layout(layout_path)
    anchors = extract_anchor_map(layout)
    ref = find_solved_reference_m(layout, train_dir)
    if ref is None:
        # fallback: allow layout to contain exactly one floating ref
        ref = find_solved_reference_m(layout, None)
    if ref is None:
        raise SystemExit("[error] layout json missing solved floating reference point")

    ranges_csv = holdout_dir / "ranges.csv"
    if not ranges_csv.exists():
        raise SystemExit(f"[error] missing {ranges_csv}")

    residuals_mm: list[float] = []
    abs_mm: list[float] = []
    by_anchor: dict[int, list[float]] = {}
    used = 0
    skipped = 0

    with ranges_csv.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                ok = int(row.get("ok") or "0")
                if ok != 1:
                    skipped += 1
                    continue
                q = int(row.get("quality_percent") or "0")
                if q < args.min_quality:
                    skipped += 1
                    continue
                anchor_id = int(row["anchor_id"])
                if not (0 <= anchor_id < 8):
                    skipped += 1
                    continue
                dist_mm = float(row.get("filt_mm") or "0")
                if dist_mm <= 0:
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue

            label = ANCHORS[anchor_id]
            ax, ay, az = anchors[label]
            rx, ry, rz = ref
            pred_m = math.sqrt((ax - rx) ** 2 + (ay - ry) ** 2 + (az - rz) ** 2)
            pred_mm = pred_m * 1000.0
            res = pred_mm - dist_mm
            residuals_mm.append(res)
            abs_mm.append(abs(res))
            by_anchor.setdefault(anchor_id, []).append(res)
            used += 1

    residuals_mm_sorted = sorted(residuals_mm)
    abs_mm_sorted = sorted(abs_mm)

    def rms(vals: list[float]) -> float | None:
        if not vals:
            return None
        return math.sqrt(sum(v * v for v in vals) / len(vals))

    report = []
    report.append("# Holdout Evaluation (Floating Reference)")
    report.append("")
    report.append(f"- layout: `{layout_path.resolve()}`")
    report.append(f"- train_session (for solved_reference_m): `{train_dir.resolve() if train_dir else '-'}`")
    report.append(f"- holdout_session: `{holdout_dir.resolve()}`")
    report.append(f"- solved_reference_m: `{ref[0]:.6f}, {ref[1]:.6f}, {ref[2]:.6f}`")
    report.append(f"- used_samples: `{used}` (skipped `{skipped}`)")
    report.append("")

    report.append("## Overall Residual Stats (mm)")
    report.append("")
    report.append("| metric | value |")
    report.append("|---|---:|")
    if used == 0:
        report.append("| rms | n/a |")
    else:
        report.append(f"| mean | {sum(residuals_mm)/len(residuals_mm):.3f} |")
        report.append(f"| rms | {rms(residuals_mm):.3f} |")
        report.append(f"| p50(abs) | {percentile(abs_mm_sorted, 50):.3f} |")
        report.append(f"| p95(abs) | {percentile(abs_mm_sorted, 95):.3f} |")
        report.append(f"| max(abs) | {abs_mm_sorted[-1]:.3f} |")
    report.append("")

    report.append("## Per-Anchor Residual RMS (mm)")
    report.append("")
    report.append("| anchor | n | mean | rms | p95(abs) |")
    report.append("|---|---:|---:|---:|---:|")
    for anchor_id in range(8):
        vals = by_anchor.get(anchor_id, [])
        if not vals:
            report.append(f"| {ANCHORS[anchor_id]} | 0 | n/a | n/a | n/a |")
            continue
        abs_sorted = sorted(abs(v) for v in vals)
        report.append(
            f"| {ANCHORS[anchor_id]} | {len(vals)} | {sum(vals)/len(vals):.3f} | {rms(vals):.3f} | {percentile(abs_sorted, 95):.3f} |"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

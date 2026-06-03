#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BROADCAST_DIR = SCRIPT_DIR.parent


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=BROADCAST_DIR, check=True)


def load_layout_summary(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    quality = raw.get("quality", {})
    source = raw.get("source", {})
    edge_fit = quality.get("edge_fit", {})
    return {
        "layout_json": str(path.resolve()),
        "rms_edges_mm": quality.get("rms_edges_mm"),
        "rms_inlier_mm": edge_fit.get("rms_inlier_mm"),
        "outlier_count": edge_fit.get("outlier_count"),
        "cir_pair_weight_count": source.get("cir_pair_weight_count", 0),
        "cir_pair_weights": source.get("cir_pair_weights"),
        "top_outliers": edge_fit.get("top_outliers", []),
    }


def fmt_float(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def write_report(out_dir: Path, summary: dict[str, Any]) -> None:
    report = out_dir / "cir_weighted_layout_comparison.md"
    lines: list[str] = [
        "# CIR Weighted Layout Comparison",
        "",
        f"- Sweep dir: `{summary['sweep_dir']}`",
        f"- CIR pair weights: `{summary.get('cir_pair_weights') or '-'}`",
        f"- Pairs CSV: `{summary['pairs_csv']}`",
        "",
        "| solve | RMS edges mm | RMS inlier mm | outliers | CIR pairs | layout |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name in ("baseline", "cir_weighted"):
        item = summary[name]
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    fmt_float(item.get("rms_edges_mm")),
                    fmt_float(item.get("rms_inlier_mm")),
                    str(item.get("outlier_count", "-")),
                    str(item.get("cir_pair_weight_count", 0)),
                    f"`{item['layout_json']}`",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Notes", ""])
    lines.append(
        "- This report only proves the same SW matrix can be solved with and without CIR-derived pair weights."
    )
    lines.append(
        "- Position repeatability must be evaluated in a separate BSF66F static-tag capture using these two layouts."
    )
    lines.append(
        "- FULL CIR waveform captures are for calibration/inspection; the solver consumes compact/full-derived pair weights."
    )
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run baseline and CIR-weighted V3-box layout solves for the same AutoPos sweep."
    )
    ap.add_argument("--sweep-dir", required=True, help="Sweep directory containing summary.json")
    ap.add_argument("--cir-pair-weights", required=True, help="CIR-derived pair weights JSON")
    ap.add_argument("--out-dir", default=None, help="Output dir; default: <sweep-dir>/cir_weighted_layout_compare")
    ap.add_argument("--min-dir-samples", type=int, default=20)
    ap.add_argument("--min-sigma-mm", type=float, default=3.0)
    ap.add_argument("--max-iters", type=int, default=15)
    ap.add_argument("--verbose", type=int, default=0)
    args = ap.parse_args()

    sweep_dir = Path(args.sweep_dir).resolve()
    summary_json = sweep_dir / "summary.json"
    if not summary_json.exists():
        raise SystemExit(f"[error] missing {summary_json}")
    weight_json = Path(args.cir_pair_weights).resolve()
    if not weight_json.exists():
        raise SystemExit(f"[error] missing {weight_json}")

    out_dir = Path(args.out_dir).resolve() if args.out_dir else sweep_dir / "cir_weighted_layout_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs_csv = out_dir / "pairs_all.csv"

    run(
        [
            "python3",
            str(SCRIPT_DIR / "autopos_extract_pairs_from_sweep_summary.py"),
            "--summary-json",
            str(summary_json),
            "--out-csv",
            str(pairs_csv),
        ]
    )

    base_dir = out_dir / "baseline_v3_box"
    weighted_dir = out_dir / "cir_weighted_v3_box"
    common = [
        "--pairs-csv",
        str(pairs_csv),
        "--min-dir-samples",
        str(args.min_dir_samples),
        "--min-sigma-mm",
        str(args.min_sigma_mm),
        "--max-iters",
        str(args.max_iters),
        "--verbose",
        str(args.verbose),
    ]
    run(["python3", str(SCRIPT_DIR / "prepare_autopos_v3_box.py"), *common, "--out-dir", str(base_dir)])
    run(
        [
            "python3",
            str(SCRIPT_DIR / "prepare_autopos_v3_box.py"),
            *common,
            "--out-dir",
            str(weighted_dir),
            "--cir-pair-weights",
            str(weight_json),
        ]
    )

    summary = {
        "sweep_dir": str(sweep_dir),
        "summary_json": str(summary_json),
        "pairs_csv": str(pairs_csv.resolve()),
        "cir_pair_weights": str(weight_json),
        "baseline": load_layout_summary(base_dir / "anchor_layout_v3_box.json"),
        "cir_weighted": load_layout_summary(weighted_dir / "anchor_layout_v3_box.json"),
    }
    (out_dir / "cir_weighted_layout_comparison.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    write_report(out_dir, summary)
    print(f"[ok] wrote {out_dir / 'cir_weighted_layout_comparison.md'}")
    print(f"[ok] wrote {out_dir / 'cir_weighted_layout_comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

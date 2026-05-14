#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = "A:BSCCF4,B:BS9336,C:BS955A"


def normalize_bs(value: str) -> str:
    name = value.strip().upper()
    if not name.startswith("BS") or len(name) != 6:
        raise argparse.ArgumentTypeError(f"invalid BS name: {value!r}")
    int(name[2:], 16)
    return name


def parse_targets(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in value.replace(" ", ",").split(","):
        if not raw.strip():
            continue
        if ":" in raw:
            label, bs = raw.split(":", 1)
        elif "=" in raw:
            label, bs = raw.split("=", 1)
        else:
            raise argparse.ArgumentTypeError(
                "targets must look like A:BSCCF4,B:BS9336,C:BS955A"
            )
        label = label.strip().upper()
        if not label:
            raise argparse.ArgumentTypeError("empty Wand label")
        if label in out:
            raise argparse.ArgumentTypeError(f"duplicate Wand label: {label}")
        out[label] = normalize_bs(bs)
    if len(out) != 3:
        raise argparse.ArgumentTypeError("internal Wand sweep requires exactly 3 Wand Tags")
    if len(set(out.values())) != 3:
        raise argparse.ArgumentTypeError("Wand Tag BS names must be unique")
    return out


def parse_truth(value: str | None) -> dict[str, float]:
    if not value:
        return {}
    out: dict[str, float] = {}
    for raw in value.replace(" ", ",").split(","):
        if not raw.strip():
            continue
        if "=" not in raw:
            raise argparse.ArgumentTypeError("truth must look like AB=500,AC=600,BC=700")
        key, val = raw.split("=", 1)
        key = "".join(sorted(key.strip().upper()))
        if len(key) != 2:
            raise argparse.ArgumentTypeError(f"bad truth pair: {raw!r}")
        out[key] = float(val)
    return out


def read_truth_csv(path: Path) -> dict[str, float]:
    truth: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return truth
        cols = {name.strip().lower(): name for name in reader.fieldnames}
        pair_col = cols.get("pair")
        value_col = (
            cols.get("truth_mm")
            or cols.get("measured_mm")
            or cols.get("distance_mm")
            or cols.get("length_mm")
        )
        if pair_col and value_col:
            for row in reader:
                pair = "".join(sorted((row.get(pair_col) or "").strip().upper()))
                raw = (row.get(value_col) or "").strip()
                if len(pair) == 2 and raw:
                    truth[pair] = float(raw)
            return truth

        for row in reader:
            for pair in ("AB", "AC", "BC"):
                for suffix in ("", "_mm", "_truth_mm", "_measured_mm"):
                    col = cols.get((pair + suffix).lower())
                    if col and (row.get(col) or "").strip():
                        truth[pair] = float(row[col])
            if truth:
                break
    return truth


def write_truth_template(path: Path, targets: dict[str, str]) -> None:
    labels = sorted(targets)
    rows = []
    for a, b in [(labels[0], labels[1]), (labels[0], labels[2]), (labels[1], labels[2])]:
        rows.append(
            {
                "pair": a + b,
                "tag_a_label": a,
                "tag_a_bs": targets[a],
                "tag_b_label": b,
                "tag_b_bs": targets[b],
                "truth_mm": "",
                "notes": "",
            }
        )
    write_csv(
        path,
        ["pair", "tag_a_label", "tag_a_bs", "tag_b_label", "tag_b_bs", "truth_mm", "notes"],
        rows,
    )


def find_latest(path: Path, pattern: str) -> Path | None:
    candidates = sorted(path.glob(pattern))
    return candidates[-1] if candidates else None


def capture_positions(args: argparse.Namespace, targets: dict[str, str], out_dir: Path) -> Path:
    target_csv = ",".join(targets[label] for label in sorted(targets))
    cmd = [
        sys.executable,
        "scripts/run_caliwand_capture.py",
        "--targets",
        target_csv,
        "--duration",
        str(args.duration),
        "--out-dir",
        str(out_dir / "capture"),
    ]
    if args.with_listener:
        cmd.append("--with-listener")
    if args.skip_anchor_preflight:
        cmd.append("--skip-anchor-preflight")
    print("[WAND_SWEEP] capture: " + " ".join(cmd), flush=True)
    if args.dry_run:
        return out_dir / "capture_<dry_run>" / "recv_<dry_run>" / "positions_all.csv"
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    summary_path = find_latest(out_dir, "**/caliwand_summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path else {}
    positions_ref = summary.get("positions_all")
    if positions_ref:
        p = Path(positions_ref)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if p.exists():
            return p

    positions_csv = find_latest(out_dir, "**/positions_all.csv")
    if positions_csv is None:
        raise SystemExit(f"[error] no positions_all.csv found under {out_dir}")
    return positions_csv


def read_positions(path: Path, bs_to_label: dict[str, str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bs = (row.get("peer_name") or "").upper()
            label = bs_to_label.get(bs)
            if not label:
                continue
            try:
                sweep = int(row["sweep"])
                point = (
                    float(row["x_mm"]),
                    float(row["y_mm"]),
                    float(row["z_mm"]),
                )
            except Exception:
                continue
            grouped.setdefault(label, []).append({
                "sweep": sweep,
                "bs": bs,
                "point": point,
                "host_elapsed_s": float(row.get("host_elapsed_s") or 0.0),
                "rms_mm": float(row.get("rms_mm") or 0.0),
                "quality_flag_percent": row.get("quality_flag_percent") or "",
            })
    for rows in grouped.values():
        rows.sort(key=lambda item: item["host_elapsed_s"])
    return grouped


def nearest_by_time(rows: list[dict], t_ref: float) -> dict | None:
    if not rows:
        return None
    lo = 0
    hi = len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if rows[mid]["host_elapsed_s"] < t_ref:
            lo = mid + 1
        else:
            hi = mid
    candidates = []
    if lo < len(rows):
        candidates.append(rows[lo])
    if lo > 0:
        candidates.append(rows[lo - 1])
    return min(candidates, key=lambda item: abs(item["host_elapsed_s"] - t_ref)) if candidates else None


def median_abs_dev(values: list[float]) -> float:
    if not values:
        return float("nan")
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    vals = sorted(values)
    idx = (len(vals) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - idx) + vals[hi] * (idx - lo)


def summarize(values: list[float], truth_mm: float | None = None) -> dict:
    if not values:
        return {
            "n": 0,
            "mean_mm": None,
            "median_mm": None,
            "std_mm": None,
            "mad_mm": None,
            "p05_mm": None,
            "p95_mm": None,
            "min_mm": None,
            "max_mm": None,
            "truth_mm": truth_mm,
            "mean_error_mm": None,
        }
    mean = statistics.fmean(values)
    return {
        "n": len(values),
        "mean_mm": mean,
        "median_mm": statistics.median(values),
        "std_mm": statistics.pstdev(values) if len(values) > 1 else 0.0,
        "mad_mm": median_abs_dev(values),
        "p05_mm": percentile(values, 0.05),
        "p95_mm": percentile(values, 0.95),
        "min_mm": min(values),
        "max_mm": max(values),
        "truth_mm": truth_mm,
        "mean_error_mm": (mean - truth_mm) if truth_mm is not None else None,
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def analyze_positions(
    positions_csv: Path,
    targets: dict[str, str],
    truth: dict[str, float],
    out_dir: Path,
    match_window_s: float,
    reference_label: str,
) -> dict:
    bs_to_label = {bs: label for label, bs in targets.items()}
    by_label = read_positions(positions_csv, bs_to_label)
    labels = sorted(targets)
    if reference_label not in labels:
        reference_label = labels[0]
    pairs = [(labels[0], labels[1]), (labels[0], labels[2]), (labels[1], labels[2])]
    pair_keys = ["".join(pair) for pair in pairs]

    sweep_rows: list[dict] = []
    per_pair_values: dict[str, list[float]] = {key: [] for key in pair_keys}
    complete_epochs = 0
    partial_epochs = 0

    ref_rows = by_label.get(reference_label, [])
    for epoch_idx, ref_item in enumerate(ref_rows):
        t_ref = float(ref_item["host_elapsed_s"])
        epoch: dict[str, dict] = {reference_label: ref_item}
        max_abs_dt = 0.0
        for label in labels:
            if label == reference_label:
                continue
            item = nearest_by_time(by_label.get(label, []), t_ref)
            if item is None:
                continue
            dt = float(item["host_elapsed_s"]) - t_ref
            if abs(dt) <= match_window_s:
                epoch[label] = item
                max_abs_dt = max(max_abs_dt, abs(dt))

        row: dict[str, object] = {
            "epoch": epoch_idx,
            "ref_label": reference_label,
            "ref_sweep": ref_item["sweep"],
            "ref_host_elapsed_s": round(t_ref, 6),
            "max_abs_dt_s": round(max_abs_dt, 6),
            "n_tags": len(epoch),
        }
        for label in labels:
            item = epoch.get(label)
            if item:
                x, y, z = item["point"]
                row[f"{label}_bs"] = item["bs"]
                row[f"{label}_sweep"] = item["sweep"]
                row[f"{label}_dt_s"] = round(float(item["host_elapsed_s"]) - t_ref, 6)
                row[f"{label}_x_mm"] = round(x, 3)
                row[f"{label}_y_mm"] = round(y, 3)
                row[f"{label}_z_mm"] = round(z, 3)
                row[f"{label}_rms_mm"] = round(float(item["rms_mm"]), 3)
            else:
                row[f"{label}_bs"] = targets[label]
                row[f"{label}_sweep"] = ""
                row[f"{label}_dt_s"] = ""
                row[f"{label}_x_mm"] = ""
                row[f"{label}_y_mm"] = ""
                row[f"{label}_z_mm"] = ""
                row[f"{label}_rms_mm"] = ""
        if len(epoch) == 3:
            complete_epochs += 1
            for a, b in pairs:
                key = a + b
                d = math.dist(epoch[a]["point"], epoch[b]["point"])
                per_pair_values[key].append(d)
                row[f"d_{key}_mm"] = round(d, 3)
        else:
            partial_epochs += 1
            for key in pair_keys:
                row[f"d_{key}_mm"] = ""
        sweep_rows.append(row)

    fields = ["epoch", "ref_label", "ref_sweep", "ref_host_elapsed_s", "max_abs_dt_s", "n_tags"]
    for label in labels:
        fields.extend(
            [
                f"{label}_bs",
                f"{label}_sweep",
                f"{label}_dt_s",
                f"{label}_x_mm",
                f"{label}_y_mm",
                f"{label}_z_mm",
                f"{label}_rms_mm",
            ]
        )
    fields.extend([f"d_{key}_mm" for key in pair_keys])
    write_csv(out_dir / "wand_internal_sweep.csv", fields, sweep_rows)

    stats = {
        key: summarize(values, truth.get("".join(sorted(key))))
        for key, values in per_pair_values.items()
    }
    stats_rows = []
    for key, item in stats.items():
        row = {"pair": key, **item}
        stats_rows.append(row)
    write_csv(
        out_dir / "wand_internal_pair_stats.csv",
        [
            "pair",
            "n",
            "mean_mm",
            "median_mm",
            "std_mm",
            "mad_mm",
            "p05_mm",
            "p95_mm",
            "min_mm",
            "max_mm",
            "truth_mm",
            "mean_error_mm",
        ],
        stats_rows,
    )
    truth_template = out_dir / "wand_truth_template.csv"
    write_truth_template(truth_template, targets)

    summary = {
        "mode": "host_side_wand_internal_sweep_from_positions",
        "note": (
            "This prototype derives Wand side lengths from simultaneous Tag positions. "
            "It does not yet perform direct Tag-to-Tag UWB ranging in firmware."
        ),
        "positions_csv": str(positions_csv.resolve()),
        "targets": targets,
        "truth_mm": truth,
        "match_window_s": match_window_s,
        "reference_label": reference_label,
        "position_rows_by_label": {label: len(by_label.get(label, [])) for label in labels},
        "epochs_total": len(ref_rows),
        "complete_3tag_epochs": complete_epochs,
        "partial_epochs": partial_epochs,
        "pair_stats": stats,
        "outputs": {
            "sweep_csv": str((out_dir / "wand_internal_sweep.csv").resolve()),
            "pair_stats_csv": str((out_dir / "wand_internal_pair_stats.csv").resolve()),
            "truth_template_csv": str(truth_template.resolve()),
            "summary_json": str((out_dir / "wand_internal_summary.json").resolve()),
            "report_md": str((out_dir / "wand_internal_report.md").resolve()),
        },
    }
    (out_dir / "wand_internal_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(out_dir / "wand_internal_report.md", summary)
    return summary


def fmt(value: object, digits: int = 1) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def write_report(path: Path, summary: dict) -> None:
    lines = [
        "# Wand Internal Sweep",
        "",
        f"- positions_csv: `{summary['positions_csv']}`",
        f"- reference_label: `{summary['reference_label']}`",
        f"- match_window_s: `{summary['match_window_s']}`",
        f"- truth_template_csv: `{summary['outputs']['truth_template_csv']}`",
        f"- complete_3tag_epochs: `{summary['complete_3tag_epochs']}`",
        f"- partial_epochs: `{summary['partial_epochs']}`",
        "",
        "This is the first host-side implementation: it computes the three Wand side lengths from simultaneous UWB position fixes. Direct Tag-to-Tag internal ranging still needs firmware support.",
        "",
        "Fill `truth_template_csv` with measured AB/AC/BC distances in millimeters, then rerun with `--truth-csv <that file>` to get mean error columns.",
        "",
        "## Pair Statistics",
        "",
        "| Pair | N | Mean | Median | Std | MAD | P05 | P95 | Truth | Mean error |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for pair, item in summary["pair_stats"].items():
        lines.append(
            "| "
            + " | ".join(
                [
                    pair,
                    str(item["n"]),
                    fmt(item["mean_mm"]),
                    fmt(item["median_mm"]),
                    fmt(item["std_mm"]),
                    fmt(item["mad_mm"]),
                    fmt(item["p05_mm"]),
                    fmt(item["p95_mm"]),
                    fmt(item["truth_mm"]),
                    fmt(item["mean_error_mm"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- sweep CSV: `{summary['outputs']['sweep_csv']}`",
            f"- pair stats CSV: `{summary['outputs']['pair_stats_csv']}`",
            f"- truth template CSV: `{summary['outputs']['truth_template_csv']}`",
            f"- summary JSON: `{summary['outputs']['summary_json']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Calibration Wand internal sweep prototype. Capture or analyze three Wand "
            "Tags, then output AB/AC/BC side-length statistics."
        )
    )
    p.add_argument("--targets", type=parse_targets, default=parse_targets(DEFAULT_TARGETS))
    p.add_argument("--duration", type=float, default=120.0)
    p.add_argument("--positions-csv", help="Analyze an existing positions_all.csv instead of capturing.")
    p.add_argument("--capture-dir", help="Analyze latest positions_all.csv under this capture directory.")
    p.add_argument("--truth", type=parse_truth, default={}, help="Optional measured distances, e.g. AB=500,AC=600,BC=700")
    p.add_argument(
        "--truth-csv",
        help=(
            "Optional measured distances CSV. Accepts columns pair,truth_mm "
            "or one-row AB/AC/BC columns. CLI --truth overrides duplicates."
        ),
    )
    p.add_argument(
        "--write-truth-template",
        action="store_true",
        help="Only write a wand_truth_template.csv and exit.",
    )
    p.add_argument(
        "--match-window-s",
        type=float,
        default=0.2,
        help="Maximum timestamp difference when pairing the other two Wand Tags to the reference Tag.",
    )
    p.add_argument("--reference-label", default="A", help="Reference Wand label for time matching.")
    p.add_argument("--out-dir", default="logs/wand_internal_sweep")
    p.add_argument("--with-listener", action="store_true")
    p.add_argument("--skip-anchor-preflight", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(f"{args.out_dir}_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    truth = {}
    if args.truth_csv:
        truth.update(read_truth_csv(Path(args.truth_csv)))
    truth.update(args.truth)

    if args.write_truth_template:
        template = out_dir / "wand_truth_template.csv"
        write_truth_template(template, args.targets)
        print(f"[ok] truth template: {template}", flush=True)
        return 0

    if args.positions_csv:
        positions_csv = Path(args.positions_csv)
    elif args.capture_dir:
        positions_csv = find_latest(Path(args.capture_dir), "**/positions_all.csv")
        if positions_csv is None:
            raise SystemExit(f"[error] no positions_all.csv found under {args.capture_dir}")
    else:
        positions_csv = capture_positions(args, args.targets, out_dir)

    if args.dry_run:
        print(f"[WAND_SWEEP] dry-run positions_csv={positions_csv}", flush=True)
        return 0
    if not positions_csv.exists():
        raise SystemExit(f"[error] positions CSV does not exist: {positions_csv}")

    summary = analyze_positions(
        positions_csv,
        args.targets,
        truth,
        out_dir,
        float(args.match_window_s),
        str(args.reference_label).strip().upper(),
    )
    print(json.dumps(summary, indent=2), flush=True)
    print(f"[ok] report: {out_dir / 'wand_internal_report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

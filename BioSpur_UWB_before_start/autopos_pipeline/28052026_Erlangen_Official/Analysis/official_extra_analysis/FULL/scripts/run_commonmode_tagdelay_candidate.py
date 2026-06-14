#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SCRIPT_DIR = THIS.parent
REPO_ROOT = THIS.parents[6]
AUTOPOS_ROOT = REPO_ROOT / "autopos_pipeline"
OFFICIAL_ROOT = AUTOPOS_ROOT / "28052026_Erlangen_Official"
FIELD_ROOT = AUTOPOS_ROOT / "erlangen_20260528_mocap"
OUTDOOR_RUN = (
    REPO_ROOT
    / "biospur_tag_positioning_offline_solver"
    / "reference_current_implementations"
    / "official_report_field_solver_13052026"
    / "run_clean_full_compare.py"
)
EVAL_SCRIPT = AUTOPOS_ROOT / "outdoor_20260513" / "analysis_20260513_182053" / "run_full_evaluation_same_pipeline_20260513.py"
STATIC_RAW = SCRIPT_DIR / "static_tag_raw_replay_matrix.py"
STATIC_ABS = SCRIPT_DIR / "static_tag_absolute_accuracy.py"
LAYOUT_BASE = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"
FROZEN_LAYOUT = LAYOUT_BASE / "v4-io" / "layout.json"
COMMON_VERSION = "v4-io-commonmode"
COMMON_LAYOUT = LAYOUT_BASE / COMMON_VERSION / "layout.json"
STATIC_TAG = "BSF66F"
ORACLE_STANDIN_TAG_DELAY_MM = 91.153
TAG_DELAY_CASES = [0.0, 80.0, 95.0, ORACLE_STANDIN_TAG_DELAY_MM]
EXPECTED_SOLVE_V4_SHA256 = "8a31606645c94837ce58b411b05fae8824a662b8cb0a0a0cd5e582728a06b1ef"
EXPECTED_FROZEN_LAYOUT_GIT_SHA1 = "71420fd90a491433bc608b5fab4ccca62f5f1658"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_hash(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT)
    result = subprocess.run(
        ["git", "hash-object", str(rel)],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def solve_v4_slice_sha256() -> str:
    lines = EVAL_SCRIPT.read_text(encoding="utf-8").splitlines(keepends=True)
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.startswith("def solve_v4("):
            start = i
        elif start is not None and i > start and line.startswith("def solve_v4_common_mode("):
            end = i
            break
        elif start is not None and i > start and line.startswith("def inter_rms_local("):
            end = i
            break
    if start is None or end is None:
        raise RuntimeError("could not isolate solve_v4 block")
    while end > start and not lines[end - 1].strip():
        end -= 1
    payload = "".join(lines[start:end]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_cmd(cmd: list[str]) -> None:
    print("[cmd] " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def fmt_delay(delay: float) -> str:
    return f"{delay:07.3f}".replace(".", "p")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def generate_common_layout(out_root: Path, *, replace_common_layout: bool) -> Path:
    if COMMON_LAYOUT.exists() and not replace_common_layout:
        print(f"[layout] reusing existing new common-mode layout: {COMMON_LAYOUT}", flush=True)
        return COMMON_LAYOUT

    staged = OFFICIAL_ROOT / "solver" / "work" / "field_dataset_staged"
    sweep = staged / "sweep1000" / "pairs_all.csv"
    if not sweep.exists():
        raise FileNotFoundError(f"missing staged sweep pairs: {sweep}")

    fc = load_module(OUTDOOR_RUN, "commonmode_full_compare")
    fc.DATA = staged
    fc.SWEEP_CSV = sweep
    fc.VERSIONS = [
        (COMMON_VERSION, "V4-io common-mode", "V4-io with d_i = c + e_i common-mode anchor delays")
    ]
    generation_out = out_root / "layout_generation"
    print(f"[layout] generating common-mode layout into {generation_out}", flush=True)
    fc.run_single("1000", str(generation_out))
    generated = generation_out / COMMON_VERSION / "layout.json"
    if not generated.exists():
        raise FileNotFoundError(f"common-mode generation did not write {generated}")

    COMMON_LAYOUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(generated, COMMON_LAYOUT)
    action = "replaced" if replace_common_layout and COMMON_LAYOUT.exists() else "wrote"
    print(f"[layout] {action} new layout artifact: {COMMON_LAYOUT}", flush=True)
    return COMMON_LAYOUT


def make_static_abs_input(raw_session_csv: Path, out_csv: Path, version: str) -> None:
    rows: list[dict] = []
    with raw_session_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                {
                    "version": version,
                    "ID": row["ID"],
                    "status": row.get("status", "ok"),
                    "location": row.get("location", ""),
                    "height": row.get("height", ""),
                    "facing": row.get("facing", ""),
                    "mean_x": row["x_mm"],
                    "mean_y": row["y_mm"],
                    "mean_z": row["z_mm"],
                    "N_frames": row.get("frames_solved", ""),
                    "pct_ge8": row.get("pct_solved_ge8", ""),
                    "path": row.get("source_tr_all", ""),
                }
            )
    write_csv(out_csv, rows)


def read_summary(abs_out: Path) -> dict:
    path = abs_out / "tables" / "tag_accuracy_summary.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    if len(rows) != 1:
        raise RuntimeError(f"expected one static summary row in {path}, found {len(rows)}")
    row = rows[0]
    return {
        "version": row["version"],
        "eval_set": row["eval_set"],
        "n_sessions": int(row["n"]),
        "err_3d_median_mm": float(row["err_3d_median_mm"]),
        "err_3d_p95_mm": float(row["err_3d_p95_mm"]),
        "err_3d_rms_mm": float(row["err_3d_rms_mm"]),
        "err_horizontal_median_mm": float(row["err_horizontal_median_mm"]),
        "err_vertical_median_mm": float(row["err_vertical_median_mm"]),
    }


def vertical_regression(abs_out: Path) -> dict:
    df = pd.read_csv(abs_out / "tables" / "tag_abs_errors_per_session.csv")
    x = df["truth_y_vertical_mm"].to_numpy(dtype=float)
    y = df["err_y_vertical_mm"].to_numpy(dtype=float)
    slope_mm_per_mm, intercept = np.polyfit(x, y, 1)
    pred = slope_mm_per_mm * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")
    return {
        "signed_vertical_slope_mm_per_m": float(slope_mm_per_mm * 1000.0),
        "signed_vertical_intercept_mm": float(intercept),
        "signed_vertical_r2": r2,
    }


def run_static_case(out_root: Path, version: str, delay_mm: float) -> dict:
    case = f"tag_delay_{fmt_delay(delay_mm)}"
    case_dir = out_root / case
    raw_out = case_dir / "raw_replay"
    abs_in = case_dir / "static_session_mean_for_tag_abs.csv"
    abs_out = case_dir / "static_abs"
    run_cmd(
        [
            sys.executable,
            str(STATIC_RAW),
            "--official-root",
            str(OFFICIAL_ROOT),
            "--out-dir",
            str(raw_out),
            "--layout-dir",
            str(LAYOUT_BASE),
            "--static-csv",
            str(LAYOUT_BASE / "tables" / "static_all_captures.csv"),
            "--layout-versions",
            version,
            "--tag-methods",
            "T4",
            "--point-estimator",
            "mean",
            "--tag-delay-by-tag",
            f"{STATIC_TAG}={delay_mm:.6f}",
        ]
    )
    make_static_abs_input(raw_out / "tables" / "tag_raw_replay_abs_errors_per_session.csv", abs_in, version)
    run_cmd(
        [
            sys.executable,
            str(STATIC_ABS),
            "--official-root",
            str(OFFICIAL_ROOT),
            "--out-dir",
            str(abs_out),
            "--layout-dir",
            str(LAYOUT_BASE),
            "--static-csv",
            str(abs_in),
            "--eval-sets",
            "all8",
        ]
    )
    summary = read_summary(abs_out)
    reg = vertical_regression(abs_out)
    return {
        "case": case,
        "version": version,
        "tag_method": "T4",
        "point_estimator": "mean",
        "fixed_tag_delay_mm": delay_mm,
        "tag_delay_label": "ORACLE STAND-IN" if abs(delay_mm - ORACLE_STANDIN_TAG_DELAY_MM) < 1e-9 else "port-faithfulness/check",
        **summary,
        **reg,
        "raw_replay_out": str(raw_out),
        "static_abs_out": str(abs_out),
        "static_abs_input_csv": str(abs_in),
    }


def load_static_abs_module():
    sys.path.insert(0, str(SCRIPT_DIR))
    return load_module(STATIC_ABS, "static_abs_for_commonmode_validation")


def layout_metrics(layout_path: Path) -> dict:
    static_abs = load_static_abs_module()
    anchor_truth, _tag_truth, _tag_truth_meta, _correction_rows = static_abs.load_corrected_static_truth(
        OFFICIAL_ROOT / "opti_captures" / "full",
        static_abs.ANCHORS,
        static_abs.PRIMARY_IDS,
    )
    labels, coords = static_abs.load_layout(layout_path)
    idx = [labels.index(a) for a in "ABCDEFGH"]
    src = coords[idx]
    dst = np.array([anchor_truth[a] for a in "ABCDEFGH"], dtype=float)
    r, t, scale, det = static_abs.fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    aligned = static_abs.apply_transform(src, r, t, scale)
    err3 = np.linalg.norm(aligned - dst, axis=1)
    _sr, _st, sim3_scale, _sdet = static_abs.fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
    return {
        "layout_json": str(layout_path),
        "anchor_rigid_median_mm": float(np.nanmedian(err3)),
        "anchor_rigid_p95_mm": float(np.nanpercentile(err3, 95)),
        "anchor_rigid_rmse_mm": float(np.sqrt(np.nanmean(err3 * err3))),
        "anchor_sim3_scale_autopos_to_vicon": float(sim3_scale),
        "anchor_rigid_det": float(det),
    }


def pass_fail(common_rows: list[dict], frozen_row: dict, metrics: dict) -> list[dict]:
    by_delay = {round(float(r["fixed_tag_delay_mm"]), 6): r for r in common_rows}
    row0 = by_delay[0.0]
    row80 = by_delay[80.0]
    row95 = by_delay[95.0]
    row91 = by_delay[round(ORACLE_STANDIN_TAG_DELAY_MM, 6)]
    return [
        {
            "criterion": "tag_delay=0 median near audited 109.515",
            "actual": row0["err_3d_median_mm"],
            "target": 109.515,
            "delta": row0["err_3d_median_mm"] - 109.515,
            "pass": abs(row0["err_3d_median_mm"] - 109.515) <= 3.0,
        },
        {
            "criterion": "tag_delay=80 median near 60.1",
            "actual": row80["err_3d_median_mm"],
            "target": 60.1,
            "delta": row80["err_3d_median_mm"] - 60.1,
            "pass": abs(row80["err_3d_median_mm"] - 60.1) <= 3.0,
        },
        {
            "criterion": "tag_delay=95 median near 60.7",
            "actual": row95["err_3d_median_mm"],
            "target": 60.7,
            "delta": row95["err_3d_median_mm"] - 60.7,
            "pass": abs(row95["err_3d_median_mm"] - 60.7) <= 3.0,
        },
        {
            "criterion": "91.153 mm ORACLE STAND-IN median beats frozen and lands 58-61",
            "actual": row91["err_3d_median_mm"],
            "target": 59.5,
            "delta": row91["err_3d_median_mm"] - 59.5,
            "pass": row91["err_3d_median_mm"] < frozen_row["err_3d_median_mm"] and 58.0 <= row91["err_3d_median_mm"] <= 61.0,
        },
        {
            "criterion": "91.153 mm ORACLE STAND-IN vertical slope collapses",
            "actual": row91["signed_vertical_slope_mm_per_m"],
            "target": -5.0,
            "delta": row91["signed_vertical_slope_mm_per_m"] - (-5.0),
            "pass": abs(row91["signed_vertical_slope_mm_per_m"]) <= 20.0 and row91["signed_vertical_r2"] <= 0.05,
        },
        {
            "criterion": "common-mode Sim(3) scale near 1.0",
            "actual": metrics["anchor_sim3_scale_autopos_to_vicon"],
            "target": 1.0,
            "delta": metrics["anchor_sim3_scale_autopos_to_vicon"] - 1.0,
            "pass": 0.99 <= metrics["anchor_sim3_scale_autopos_to_vicon"] <= 1.03,
        },
        {
            "criterion": "common-mode anchor rigid RMSE near 63 mm",
            "actual": metrics["anchor_rigid_rmse_mm"],
            "target": 63.0,
            "delta": metrics["anchor_rigid_rmse_mm"] - 63.0,
            "pass": 55.0 <= metrics["anchor_rigid_rmse_mm"] <= 70.0,
        },
    ]


def write_report(out_root: Path, rows: list[dict], frozen_row: dict, metrics: dict, checks: list[dict]) -> None:
    lines = [
        "# Common-Mode + Tag-Delay Stand-In Validation\n",
        "",
        f"- Common-mode layout: `{COMMON_LAYOUT}`",
        f"- Validation output: `{out_root}`",
        f"- `{ORACLE_STANDIN_TAG_DELAY_MM:.3f}` mm is an ORACLE STAND-IN fixed tag-side delay, not a measured calibration.",
        f"- Frozen v4-io layout: `{FROZEN_LAYOUT}`",
        "",
        "## Static Cases",
        "",
        "| fixed_tag_delay_mm | label | median_3d_mm | p95_3d_mm | rmse_3d_mm | horiz_med_mm | vert_med_mm | vertical_slope_mm_per_m | vertical_r2 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['fixed_tag_delay_mm']:.3f} | {row['tag_delay_label']} | "
            f"{row['err_3d_median_mm']:.3f} | {row['err_3d_p95_mm']:.3f} | {row['err_3d_rms_mm']:.3f} | "
            f"{row['err_horizontal_median_mm']:.3f} | {row['err_vertical_median_mm']:.3f} | "
            f"{row['signed_vertical_slope_mm_per_m']:.3f} | {row['signed_vertical_r2']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Frozen Recheck",
            "",
            f"- Frozen v4-io/T4/session-mean static median re-run: `{frozen_row['err_3d_median_mm']:.3f}` mm.",
            f"- P95/RMSE: `{frozen_row['err_3d_p95_mm']:.3f}` / `{frozen_row['err_3d_rms_mm']:.3f}` mm.",
            "",
            "## Layout Metrics",
            "",
            f"- Rigid anchor RMSE: `{metrics['anchor_rigid_rmse_mm']:.3f}` mm.",
            f"- Rigid anchor median/P95: `{metrics['anchor_rigid_median_mm']:.3f}` / `{metrics['anchor_rigid_p95_mm']:.3f}` mm.",
            f"- Sim(3) scale AutoPos-to-Vicon: `{metrics['anchor_sim3_scale_autopos_to_vicon']:.6f}`.",
            "",
            "## Pass/Fail",
            "",
            "| criterion | actual | target | delta | pass |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for check in checks:
        lines.append(
            f"| {check['criterion']} | {float(check['actual']):.6f} | {float(check['target']):.6f} | "
            f"{float(check['delta']):+.6f} | {check['pass']} |"
        )
    out = out_root / "COMMONMODE_TAGDELAY_VALIDATION.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate v4-io common-mode layout plus fixed tag-delay stand-in.")
    default_out = (
        OFFICIAL_ROOT
        / "Analysis"
        / "official_extra_analysis"
        / "FULL"
        / f"commonmode_tagdelay_candidate_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    ap.add_argument("--out-root", default=str(default_out))
    ap.add_argument(
        "--replace-common-layout",
        action="store_true",
        help="Replace the non-frozen v4-io-commonmode layout artifact if it already exists.",
    )
    args = ap.parse_args()

    out_root = Path(args.out_root).resolve()
    if out_root.exists():
        raise SystemExit(f"refusing to overwrite existing validation output: {out_root}")
    out_root.mkdir(parents=True)

    solve_v4_before = solve_v4_slice_sha256()
    frozen_blob_before = git_blob_hash(FROZEN_LAYOUT)
    common_layout = generate_common_layout(out_root, replace_common_layout=args.replace_common_layout)
    common_metrics = layout_metrics(common_layout)

    rows = [run_static_case(out_root, COMMON_VERSION, delay) for delay in TAG_DELAY_CASES]
    frozen_row = run_static_case(out_root / "frozen_recheck", "v4-io", 0.0)

    solve_v4_after = solve_v4_slice_sha256()
    frozen_blob_after = git_blob_hash(FROZEN_LAYOUT)
    integrity = {
        "solve_v4_sha256_before": solve_v4_before,
        "solve_v4_sha256_after": solve_v4_after,
        "solve_v4_sha256_expected": EXPECTED_SOLVE_V4_SHA256,
        "solve_v4_unchanged": solve_v4_before == solve_v4_after == EXPECTED_SOLVE_V4_SHA256,
        "frozen_layout_git_sha1_before": frozen_blob_before,
        "frozen_layout_git_sha1_after": frozen_blob_after,
        "frozen_layout_git_sha1_expected": EXPECTED_FROZEN_LAYOUT_GIT_SHA1,
        "frozen_layout_unchanged": frozen_blob_before == frozen_blob_after == EXPECTED_FROZEN_LAYOUT_GIT_SHA1,
        "common_layout_sha256": sha256_file(common_layout),
        "note": "91.153 mm is an ORACLE STAND-IN fixed tag-side delay and is not a measured calibration.",
    }
    checks = pass_fail(rows, frozen_row, common_metrics)
    write_csv(out_root / "tables" / "commonmode_static_validation_summary.csv", rows)
    write_csv(out_root / "tables" / "frozen_v4io_recheck_summary.csv", [frozen_row])
    write_csv(out_root / "tables" / "commonmode_layout_metrics.csv", [common_metrics])
    write_csv(out_root / "tables" / "pass_fail_checks.csv", checks)
    (out_root / "tables" / "integrity_checks.json").write_text(
        json.dumps(integrity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    created = [
        str(common_layout),
        str(out_root),
        str(out_root / "layout_generation"),
        *(str(out_root / f"tag_delay_{fmt_delay(delay)}") for delay in TAG_DELAY_CASES),
        str(out_root / "frozen_recheck"),
    ]
    (out_root / "CREATED_PATHS.txt").write_text("\n".join(created) + "\n", encoding="utf-8")
    write_report(out_root, rows, frozen_row, common_metrics, checks)

    print(json.dumps({"rows": rows, "frozen": frozen_row, "layout": common_metrics, "integrity": integrity, "checks": checks}, indent=2), flush=True)
    if not all(bool(c["pass"]) for c in checks):
        return 2
    if not integrity["solve_v4_unchanged"] or not integrity["frozen_layout_unchanged"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

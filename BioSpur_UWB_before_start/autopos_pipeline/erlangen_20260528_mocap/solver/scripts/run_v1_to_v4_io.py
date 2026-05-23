#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
FIELD_ROOT = REPO / "autopos_pipeline" / "erlangen_20260528_mocap"
OUTDOOR_RUN = REPO / "autopos_pipeline" / "outdoor_20260513" / "run_clean_full_compare.py"
US_HEIGHT_SCRIPT = FIELD_ROOT / "solver" / "scripts" / "apply_ultrasound_height_to_layout.py"

VERSIONS = [
    ("v1-old", "V1", "simple bidirectional mean, no delay"),
    ("v2", "V2", "weighted/IVW pair fusion, no delay"),
    ("v3-lite", "V3-lite", "MAD/MVUE robust pair fusion, no delay"),
    ("v3-full", "V3-full", "robust pair fusion plus per-anchor delay"),
    ("v4-io", "V4-io", "current production inter-anchor solver"),
]


def progress(step: int, total: int, message: str, started: float | None = None) -> None:
    width = 20
    done = int(round(width * step / total))
    bar = "#" * done + "." * (width - done)
    elapsed = ""
    if started is not None:
        elapsed = f" elapsed={time.monotonic() - started:.1f}s"
    print(f"[V1_TO_V4IO] [{bar}] {step}/{total} {message}{elapsed}", flush=True)


def estimated_progress(
    start_step: int,
    end_step: int,
    total: int,
    message: str,
    started: float,
    expected_s: float,
):
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(5.0):
            frac = min(0.96, (time.monotonic() - started) / max(expected_s, 1.0))
            step = start_step + int(round((end_step - start_step) * frac))
            step = min(max(step, start_step), end_step - 1)
            progress(step, total, message, started)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return stop


def load_full_compare():
    spec = importlib.util.spec_from_file_location("field_full_compare", OUTDOOR_RUN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {OUTDOOR_RUN}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compact_report(out_dir: Path) -> str:
    version_summary = read_csv_rows(out_dir / "tables" / "version_summary.csv")
    quality = read_csv_rows(out_dir / "tables" / "autopos_quality_summary.csv")
    delay = read_csv_rows(out_dir / "tables" / "delay_sanity.csv")

    lines = ["# Erlangen V1 To V4-io Field Check\n"]
    lines.append(f"- output: `{out_dir}`")
    lines.append("- policy: V1/V2/V3-lite/V3-full/V4-io use sweep only for layout; static/roto/wand are validation only.")

    if version_summary:
        lines.append("\n## Version Summary")
        lines.append("| version | label | AutoPos RMS | AutoPos p95 | static med | static p95 | roto dR RMS | turn-center med |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for r in version_summary:
            lines.append(
                f"| {r.get('version', '')} | {r.get('label', '')} | {r.get('autopos_rms', '')} | "
                f"{r.get('autopos_p95', '')} | {r.get('static_median', '')} | {r.get('static_p95', '')} | "
                f"{r.get('roto_deltaR_rms', '')} | {r.get('roto_turn_center_median', '')} |"
            )

    if quality:
        lines.append("\n## Anchor Layout Quality")
        lines.append("| version | eval set | RMS mm | p95 abs mm | max abs mm |")
        lines.append("|---|---|---:|---:|---:|")
        for r in quality:
            lines.append(
                f"| {r.get('version', '')} | {r.get('eval_set', '')} | {r.get('rms', '')} | "
                f"{r.get('p95_abs', '')} | {r.get('max_abs', '')} |"
            )

    if delay:
        lines.append("\n## Delay Sanity")
        lines.append("| version | delay min | delay max | delay L2 | near bounds |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in delay:
            if r.get("version") not in {v[0] for v in VERSIONS}:
                continue
            lines.append(
                f"| {r.get('version', '')} | {r.get('delay_min', '')} | {r.get('delay_max', '')} | "
                f"{r.get('delay_l2', '')} | {r.get('near_bound_count', '')} |"
            )

    lines.append("\n## Main Files")
    lines.append("- `tables/version_summary.csv`")
    lines.append("- `tables/autopos_quality_summary.csv`")
    lines.append("- `tables/delay_sanity.csv`")
    for version, _label, _meaning in VERSIONS:
        lines.append(f"- `{version}/layout.json`")
        us_layout = out_dir / version / "layout_us_height.json"
        if us_layout.exists():
            lines.append(f"- `{version}/layout_us_height.json`")

    if (out_dir / "v4-io" / "layout_us_height.json").exists():
        lines.append("\n## Ultrasound Height-Aligned Layout")
        lines.append("- `layout_us_height.json` files use Anchor H ultrasound median antenna-center height as the z-up frame reference.")
        lines.append("- This is a coordinate-frame post-process; it does not change inter-anchor solve residuals.")

    lines.append("\n## Important Caveat")
    lines.append("This is a field sanity check. Final OptiTrack alignment and metadata review still happen later.")
    return "\n".join(lines) + "\n"


def apply_ultrasound_height_layouts(out_dir: Path, staged: Path) -> None:
    for version, _label, _meaning in VERSIONS:
        layout = out_dir / version / "layout.json"
        if not layout.exists():
            print(f"[V1_TO_V4IO] ultrasound height skip: missing {layout}", flush=True)
            continue
        cmd = [
            sys.executable,
            str(US_HEIGHT_SCRIPT),
            "--layout",
            str(layout),
            "--staged",
            str(staged),
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"[V1_TO_V4IO] ultrasound height warning: {version} rc={exc.returncode}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Erlangen field solver progression: V1, V2, V3-lite, V3-full, V4-io.")
    ap.add_argument("--staged", default=str(FIELD_ROOT / "solver" / "work" / "field_dataset_staged"))
    ap.add_argument("--out", default=str(FIELD_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"))
    args = ap.parse_args()

    staged = Path(args.staged)
    out_dir = Path(args.out)
    if not staged.is_absolute():
        staged = FIELD_ROOT / "solver" / "work" / staged
    if not out_dir.is_absolute():
        out_dir = FIELD_ROOT / "solver" / "outputs" / out_dir
    pairs_csv = staged / "sweep1000" / "pairs_all.csv"
    if not pairs_csv.exists():
        raise SystemExit(f"missing staged sweep pairs: {pairs_csv}")

    started = time.monotonic()
    total_steps = 100
    progress(1, total_steps, f"staged dataset ready: {staged}", started)
    progress(7, total_steps, f"using sweep pairs: {pairs_csv}", started)
    progress(12, total_steps, "load solver module", started)

    fc = load_full_compare()
    fc.DATA = staged
    fc.SWEEP_CSV = pairs_csv
    fc.VERSIONS = VERSIONS

    for idx, (version, label, _meaning) in enumerate(VERSIONS, start=0):
        progress(16 + idx * 4, total_steps, f"queued {version} ({label})", started)

    progress(40, total_steps, "run progression and evaluate static/roto/wand", started)
    heartbeat = estimated_progress(
        40,
        90,
        total_steps,
        "estimated: solve/evaluate V1..V4-io",
        started,
        expected_s=260.0,
    )
    try:
        fc.run_single("1000", str(out_dir))
    finally:
        heartbeat.set()

    progress(90, total_steps, "apply ultrasound height and write compact report", started)
    apply_ultrasound_height_layouts(out_dir, staged)
    report = compact_report(out_dir)
    report_path = out_dir / "FIELD_V1_TO_V4_IO.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    progress(100, total_steps, f"done: {report_path}", started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

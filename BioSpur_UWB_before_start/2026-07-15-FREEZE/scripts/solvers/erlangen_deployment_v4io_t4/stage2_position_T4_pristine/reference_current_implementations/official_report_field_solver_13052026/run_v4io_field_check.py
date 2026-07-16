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


def progress(step: int, total: int, message: str, started: float | None = None) -> None:
    width = 20
    done = int(round(width * step / total))
    bar = "#" * done + "." * (width - done)
    elapsed = ""
    if started is not None:
        elapsed = f" elapsed={time.monotonic() - started:.1f}s"
    print(f"[FIELD_CHECK] [{bar}] {step}/{total} {message}{elapsed}", flush=True)


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
    static_rows = read_csv_rows(out_dir / "v4-io" / "static_all_captures.csv")
    roto_phys = read_csv_rows(out_dir / "tables" / "roto_physical_consistency_summary.csv")
    wand_rows = read_csv_rows(out_dir / "v4-io" / "wand_static_summary.csv")

    lines = ["# V4-io Field Check\n"]
    lines.append(f"- output: `{out_dir}`")

    if version_summary:
        r = version_summary[0]
        lines.append("\n## Summary")
        lines.append(f"- layout residual RMS: `{r.get('autopos_rms', '')}` mm")
        lines.append(f"- layout residual p95 abs: `{r.get('autopos_p95', '')}` mm")
        lines.append(f"- static captures used: `{r.get('static_n', '')}`")
        lines.append(f"- static 3D median: `{r.get('static_median', '')}` mm")
        lines.append(f"- static 3D p95: `{r.get('static_p95', '')}` mm")
        lines.append(f"- roto dR RMS: `{r.get('roto_deltaR_rms', '')}` mm")
        lines.append(f"- roto turn-center median: `{r.get('roto_turn_center_median', '')}` mm")

    if quality:
        lines.append("\n## Anchor Layout Quality")
        lines.append("| eval set | RMS mm | p95 abs mm | max abs mm |")
        lines.append("|---|---:|---:|---:|")
        for r in quality:
            lines.append(f"| {r.get('eval_set', '')} | {r.get('rms', '')} | {r.get('p95_abs', '')} | {r.get('max_abs', '')} |")

    ok_static = [r for r in static_rows if r.get("status") == "ok"]
    if ok_static:
        lines.append("\n## Static Captures")
        lines.append("| ID | X std | Y std | Z std | 3D std |")
        lines.append("|---|---:|---:|---:|---:|")
        for r in ok_static[:40]:
            lines.append(f"| {r.get('capture', '')} | {r.get('X_std', '')} | {r.get('Y_std', '')} | {r.get('Z_std', '')} | {r.get('D3_std', '')} |")

    if roto_phys:
        lines.append("\n## Roto Physical Consistency")
        lines.append("| captures | dR mean | dR RMS | turn-center median |")
        lines.append("|---:|---:|---:|---:|")
        for r in roto_phys:
            lines.append(
                f"| {r.get('capture_pairs', '')} | {r.get('deltaR_error_mean_mm', '')} | "
                f"{r.get('deltaR_error_rms_mm', '')} | {r.get('turn_center_rms_median_mm', '')} |"
            )

    ok_wand = [r for r in wand_rows if r.get("status") == "ok"]
    if ok_wand:
        lines.append("\n## Wand Static Summary")
        lines.append("| ID | pair bias RMS mm | max abs bias mm |")
        lines.append("|---|---:|---:|")
        for r in ok_wand[:20]:
            lines.append(f"| {r.get('capture', '')} | {r.get('pair_bias_rms_mm', '')} | {r.get('pair_bias_max_abs_mm', '')} |")

    lines.append("\n## Important Caveat")
    lines.append("This is a field sanity check. Without OptiTrack alignment and final metadata review, do not treat these numbers as final absolute accuracy.")
    us_layout = out_dir / "v4-io" / "layout_us_height.json"
    if us_layout.exists():
        lines.append("\n## Ultrasound Height-Aligned Layout")
        lines.append(f"- `{us_layout}`")
        lines.append("- Uses F/G/H ultrasound antenna-center heights for a best-fit rigid z alignment.")
        lines.append("- `layout.json` remains the pure UWB AutoPos result; `layout_us_height.json` reports residual PASS/FAIL.")
    return "\n".join(lines) + "\n"


def apply_ultrasound_height_layout(out_dir: Path, staged: Path) -> None:
    layout = out_dir / "v4-io" / "layout.json"
    if not layout.exists():
        print(f"[FIELD_CHECK] ultrasound height skip: missing {layout}", flush=True)
        return
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
        print(f"[FIELD_CHECK] ultrasound height warning: rc={exc.returncode}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a minimal V4-io field solver check on a staged Erlangen dataset.")
    ap.add_argument("--staged", default=str(FIELD_ROOT / "solver" / "work" / "field_dataset_staged"))
    ap.add_argument("--out", default=str(FIELD_ROOT / "solver" / "outputs" / "v4io_field_check"))
    args = ap.parse_args()

    staged = Path(args.staged)
    out_dir = Path(args.out)
    if not staged.is_absolute():
        staged = FIELD_ROOT / "solver" / "work" / staged
    if not out_dir.is_absolute():
        out_dir = FIELD_ROOT / "solver" / "outputs" / out_dir
    if not (staged / "sweep1000" / "pairs_all.csv").exists():
        raise SystemExit(f"missing staged sweep pairs: {staged / 'sweep1000' / 'pairs_all.csv'}")

    started = time.monotonic()
    total_steps = 100
    progress(1, total_steps, f"staged dataset ready: {staged}", started)
    progress(8, total_steps, f"using sweep pairs: {staged / 'sweep1000' / 'pairs_all.csv'}", started)
    progress(15, total_steps, "load solver module", started)
    fc = load_full_compare()
    fc.DATA = staged
    fc.SWEEP_CSV = staged / "sweep1000" / "pairs_all.csv"
    fc.VERSIONS = [("v4-io", "V4-io", "Huber bounded-delay inter-anchor")]
    progress(25, total_steps, "solve V4-io anchor layout and evaluate captures", started)
    heartbeat = estimated_progress(
        25,
        90,
        total_steps,
        "estimated: solve/evaluate V4-io",
        started,
        expected_s=110.0,
    )
    try:
        fc.run_single("1000", str(out_dir))
    finally:
        heartbeat.set()

    progress(90, total_steps, "write compact report", started)
    apply_ultrasound_height_layout(out_dir, staged)
    report = compact_report(out_dir)
    (out_dir / "FIELD_V4IO_CHECK.md").write_text(report, encoding="utf-8")
    progress(100, total_steps, f"done: {out_dir / 'FIELD_V4IO_CHECK.md'}", started)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

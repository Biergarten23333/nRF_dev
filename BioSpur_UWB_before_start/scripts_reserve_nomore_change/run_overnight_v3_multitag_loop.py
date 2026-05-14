#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], *, env: dict[str, str] | None = None, log_path: Path | None = None) -> None:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    merged_env.update({"PYTHONUNBUFFERED": "1"})
    if log_path is None:
        subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=merged_env)
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write("$ " + " ".join(cmd) + "\n")
        logf.flush()
        subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            check=True,
            env=merged_env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            text=True,
        )


def find_latest_subdir(base: Path) -> Path:
    dirs = [p for p in base.iterdir() if p.is_dir()]
    if not dirs:
        raise FileNotFoundError(f"no subdir under {base}")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for k in row.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def write_markdown(path: Path, rows: list[dict[str, Any]], started_at: datetime, ends_at: datetime) -> None:
    lines = [
        "# Overnight V3 Multitag Loop",
        "",
        f"- started_at: `{started_at.isoformat()}`",
        f"- planned_end_at: `{ends_at.isoformat()}`",
        f"- cycles_recorded: `{len(rows)}`",
        "",
        "| Cycle | Status | Stage | Layout RMS (mm) | BSF66F RMS (mm) | BS2DCE radius (mm) | BSDC91 radius (mm) | Notes |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {cycle} | {status} | {stage} | {layout_rms_edges_mm} | {bsf66f_static_rms_mm} | {bs2dce_radius_mm} | {bsdc91_radius_mm} | {notes} |".format(
                cycle=row.get("cycle", "-"),
                status=row.get("status", "-"),
                stage=row.get("stage", "-"),
                layout_rms_edges_mm=row.get("layout_rms_edges_mm", "-"),
                bsf66f_static_rms_mm=row.get("bsf66f_static_rms_mm", "-"),
                bs2dce_radius_mm=row.get("bs2dce_radius_mm", "-"),
                bsdc91_radius_mm=row.get("bsdc91_radius_mm", "-"),
                notes=str(row.get("notes", "-")).replace("\n", " "),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Overnight loop: fresh sweep + V3 full + multitag capture + analysis.")
    ap.add_argument("--port", default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--max-cycles", type=int, default=99)
    ap.add_argument("--sw-sets", type=int, default=100)
    ap.add_argument("--recv-duration", type=int, default=600)
    ap.add_argument("--order", default="ABCDEFGH")
    ap.add_argument("--tag-name", default="BSF66F")
    ap.add_argument("--quiet-tag-name", default="-")
    ap.add_argument("--out-dir", default=None)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now()
    ends_at = started_at + timedelta(hours=float(args.hours))
    base_dir = Path(args.out_dir or f"logs/overnight_v3_multitag_{started_at.strftime('%Y%m%d_%H%M%S')}")
    base_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    cycle = 1
    while cycle <= int(args.max_cycles) and datetime.now() < ends_at:
        cycle_dir = base_dir / f"cycle_{cycle:02d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "cycle": cycle,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "status": "running",
            "stage": "init",
            "layout_rms_edges_mm": "-",
            "bsf66f_static_rms_mm": "-",
            "bs2dce_radius_mm": "-",
            "bsdc91_radius_mm": "-",
            "notes": "-",
        }
        rows.append(row)
        try:
            row["stage"] = "precheck"
            precheck_path = cycle_dir / "anchorstatus_before.txt"
            with precheck_path.open("w", encoding="utf-8") as f:
                subprocess.run(
                    ["python3", "scripts/scan_and_map.py", "--timeout-s", "8"],
                    cwd=REPO_ROOT,
                    check=True,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

            row["stage"] = "workflow"
            workflow_dir = cycle_dir / "workflow"
            env = {
                "PORT": args.port,
                "ORDER": args.order,
                "SW_SETS": str(args.sw_sets),
                "TIMEOUT_S": "7200",
                "QUIET_TAG_NAME": args.quiet_tag_name,
                "CAPTURE_TAG115": "1",
                "TAG_NAME": args.tag_name,
                "CM_LINES": "80",
                "OUT_DIR": str(workflow_dir),
            }
            run(
                ["bash", "scripts/run_v3_box_100set_workflow.sh"],
                env=env,
                log_path=cycle_dir / "workflow_driver.log",
            )

            layout_json = workflow_dir / "solve_v3_box" / "anchor_layout_v3_box.json"
            layout = load_json(layout_json)
            row["layout_rms_edges_mm"] = f"{layout['quality']['rms_edges_mm']:.3f}"

            row["stage"] = "recv_capture"
            recv_base = cycle_dir / "recv_tdma_capture"
            run(
                [
                    "python3",
                    "scripts/run_recv_tdma_capture.py",
                    "--port",
                    args.port,
                    "--duration",
                    str(args.recv_duration),
                    "--out-dir",
                    str(recv_base),
                ],
                log_path=cycle_dir / "recv_driver.log",
            )
            recv_session = find_latest_subdir(recv_base)

            row["stage"] = "analysis"
            analysis_json = recv_session / "analysis_fresh_layout.json"
            analysis_md = recv_session / "analysis_fresh_layout.md"
            run(
                [
                    "python3",
                    "scripts/analyze_recv_tdma_session.py",
                    "--session-dir",
                    str(recv_session),
                    "--layout-json",
                    str(layout_json),
                    "--static-tag",
                    "BSF66F",
                    "--roto-tag",
                    "BS2DCE",
                    "--roto-tag",
                    "BSDC91",
                    "--out-json",
                    str(analysis_json),
                    "--out-md",
                    str(analysis_md),
                ],
                log_path=cycle_dir / "analysis_driver.log",
            )
            analysis = load_json(analysis_json)
            results = analysis["results"]
            row["bsf66f_static_rms_mm"] = f"{results['BSF66F']['static_rms_mm']:.3f}"
            row["bs2dce_radius_mm"] = f"{results['BS2DCE']['radius_mm']:.3f}"
            row["bsdc91_radius_mm"] = f"{results['BSDC91']['radius_mm']:.3f}"
            row["notes"] = f"workflow={workflow_dir.name} recv={recv_session.name}"
            row["status"] = "ok"
            row["stage"] = "done"
        except subprocess.CalledProcessError as exc:
            row["status"] = "fail"
            row["notes"] = f"calledprocesserror rc={exc.returncode}"
        except Exception as exc:  # pragma: no cover
            row["status"] = "fail"
            row["notes"] = f"{type(exc).__name__}: {exc}"
        finally:
            row["finished_at"] = datetime.now().isoformat(timespec="seconds")
            write_csv(base_dir / "overnight_summary.csv", rows)
            (base_dir / "overnight_summary.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
            write_markdown(base_dir / "overnight_summary.md", rows, started_at, ends_at)

        cycle += 1

    print(base_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

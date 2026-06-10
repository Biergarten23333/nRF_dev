#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 1 diagnostics and assemble reports/01_diagnostics.md.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    return parser.parse_args()


def run_step(script: str, data_dir: Path, out_dir: Path) -> None:
    cmd = [sys.executable, script, "--data-dir", str(data_dir), "--out-dir", str(out_dir)]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for script in [
        "verify_anchor_mapping.py",
        "asymmetry.py",
        "pair_bias_vs_distance.py",
        "tag_link_bias.py",
    ]:
        run_step(script, data_dir, out_dir)

    fragments_dir = out_dir / "fragments"
    parts = [
        "# Phase 1 Range-Level Diagnostics",
        "",
        f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Data dir: `{data_dir}`",
        "- Ground-truth terminology: `Vicon`",
        "- Scope: diagnostics only; no solver changes were made.",
        "",
    ]
    for fragment in [
        "00_prerequisites.md",
        "01_asymmetry.md",
        "02_pair_bias.md",
        "03_tag_link_bias.md",
    ]:
        parts.append((fragments_dir / fragment).read_text())
        parts.append("")
    parts.append("STOP: Phase 1 diagnostics only. Do not proceed to solver work until this report is reviewed.")
    parts.append("")
    report_path = out_dir / "01_diagnostics.md"
    report_path.write_text("\n".join(parts))
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.audit_helpers import (
    discover_inputs,
    list_capture_dirs,
    load_anchor_truth,
    load_sweep_pairs,
    load_tag_capture_frame,
    load_tag_truth,
    load_workspace,
    motive_export_info,
    relpath,
    render_report,
    session_notes_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 0 data audit for range-level AutoPos analysis.")
    parser.add_argument("--data-dir", type=Path, default=Path("."), help="Project data directory.")
    parser.add_argument("--out-dir", type=Path, default=Path("reports"), help="Report output directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    workspace = load_workspace(data_dir)
    inputs = discover_inputs(data_dir)

    session_notes, session_note_rows = session_notes_summary(inputs)
    sweep_df, sweep_source = load_sweep_pairs(inputs)

    static_dirs = list_capture_dirs(inputs.capture_root, "static")
    roto_dirs = list_capture_dirs(inputs.capture_root, "roto")
    static_df, static_inventory = load_tag_capture_frame(static_dirs, "static")
    roto_df, roto_inventory = load_tag_capture_frame(roto_dirs, "roto")

    anchor_by_file, anchor_truth = load_anchor_truth(inputs)
    tag_truth = load_tag_truth(inputs)

    opti_examples = []
    for path in [
        inputs.opti_root / "static" / "ID01.trc",
        inputs.opti_root / "full" / "ID01.csv",
        inputs.opti_root / "full" / "R01.csv",
    ]:
        info = motive_export_info(path)
        info["path"] = relpath(path, data_dir)
        opti_examples.append(info)

    report = render_report(
        inputs=inputs,
        workspace=workspace,
        session_notes_rows=session_note_rows,
        sweep_df=sweep_df,
        sweep_source=sweep_source,
        static_df=static_df,
        static_inventory=static_inventory,
        roto_df=roto_df,
        roto_inventory=roto_inventory,
        anchor_by_file=anchor_by_file,
        anchor_truth=anchor_truth,
        tag_truth=tag_truth,
        opti_infos=opti_examples,
    )

    report_path = out_dir / "00_audit.md"
    report_path.write_text(report)
    print(f"Wrote {report_path}")
    print(f"Data dir: {data_dir}")
    print(f"Capture root: {inputs.capture_root}")
    print(f"Session notes rows: {len(session_notes)}")
    print(f"Sweep rows: {len(sweep_df)} from {sweep_source}")
    print(f"Static rows: {len(static_df)} across {len(static_inventory)} captures")
    print(f"Roto rows: {len(roto_df)} across {len(roto_inventory)} captures")
    print("STOP: Phase 0 audit only. Review reports/00_audit.md before Phase 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

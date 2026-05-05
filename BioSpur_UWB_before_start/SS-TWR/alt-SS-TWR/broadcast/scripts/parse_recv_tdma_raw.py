#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import run_recv_tdma_capture as cap


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Parse an existing run_recv_tdma_capture raw.log.")
    ap.add_argument("raw_log", help="Path to raw.log")
    ap.add_argument(
        "--out-dir",
        default="",
        help="Output directory. Defaults to the raw.log parent directory.",
    )
    return ap.parse_args()


def write_rows(path: Path, fields: list[str], rows: list[dict]) -> None:
    if path.suffix == ".csv" and path.name[:2] in {"cm", "cs", "cr", "cf"}:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    raw_path = Path(args.raw_log)
    out_dir = Path(args.out_dir) if args.out_dir else raw_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    positions: list[dict] = []
    tr_rows: list[dict] = []
    cm_rows: list[dict] = []
    cs_rows: list[dict] = []
    cr_rows: list[dict] = []
    cf_rows: list[dict] = []

    for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
        peer_name = cap.extract_bs_name(line)

        for m in cap.iter_tag_summary_matches(line):
            x = m.group("x") or m.group("x2")
            y = m.group("y") or m.group("y2")
            z = m.group("z") or m.group("z2")
            positions.append(
                {
                    "sweep": int(m.group("sweep")),
                    "peer_name": peer_name,
                    "plan": m.group("plan"),
                    "pmode": m.groupdict().get("pmode") or "",
                    "plan_label": m.groupdict().get("plan_label") or "",
                    "quality_flag_percent": m.groupdict().get("qf") or "",
                    "x_mm": int(x),
                    "y_mm": int(y),
                    "z_mm": int(z),
                    "rms_mm": int(m.group("rms")),
                    "max_mm": int(m.group("max")),
                    "anchors": m.group("anchors") or "",
                    "motion_dt_ms": int(m.group("motion_dt") or 0),
                }
            )

        for m in cap.iter_cm_matches(line):
            cm_rows.append(
                {
                    "sweep": int(m.group("sweep")),
                    "peer_name": peer_name,
                    "anchor_id": int(m.group("anchor")),
                    "status": m.group("status"),
                    "raw_mm": int(m.group("raw")),
                    "filt_mm": int(m.group("filt")),
                    "quality_percent": int(m.group("q")),
                    "ok_count": int(m.group("ok")),
                    "fail_count": int(m.group("fail")),
                }
            )

        for tr in cap.iter_tr_records(line):
            tr_rows.append(
                {
                    "sweep": int(tr["sweep"]),
                    "peer_name": peer_name,
                    "plan": tr["plan"],
                    "pmode": int(tr["pmode"]),
                    "anchor_id": int(tr["anchor_id"]),
                    "raw_mm": int(tr["raw_mm"]),
                    "range_mm": int(tr["range_mm"]),
                    "quality_percent": int(tr["quality_percent"]),
                    "valid": int(tr["valid"]),
                    "status": tr["status"],
                    "quality_flag_percent": int(tr.get("quality_flag_percent") or 0),
                    "first_to_last_us": int(tr.get("first_to_last_us") or 0),
                    "frame_us": int(tr.get("frame_us") or 0),
                    "poll_count": int(tr.get("poll_count") or 0),
                }
            )

        for m in cap.iter_cs_matches(line):
            cs_rows.append(
                {
                    "sweep": int(m.group("sweep")),
                    "peer_name": peer_name,
                    "plan": m.group("plan"),
                    "pmode": int(m.group("pmode")),
                    "quality_flag_percent": int(m.group("qf")),
                    "targets": m.group("targets"),
                    "statuses": m.group("statuses"),
                    "qualities": m.group("qualities"),
                }
            )

        for m in cap.iter_cr_matches(line):
            cr_rows.append(
                {
                    "sweep": int(m.group("sweep")),
                    "peer_name": peer_name,
                    "plan": m.group("plan"),
                    "pmode": int(m.group("pmode")),
                    "anchor_label": m.group("anchor"),
                    "status": m.group("status"),
                    "reason": m.group("reason"),
                    "raw_mm": int(m.group("raw")),
                    "filt_mm": int(m.group("filt")),
                    "pred_mm": int(m.group("pred")),
                    "resid_mm": int(m.group("resid")),
                    "tracker_quality_percent": int(m.group("tracker_q")),
                    "solve_quality_percent": int(m.group("solve_q")),
                }
            )

        for m in cap.iter_cf_matches(line):
            cf_rows.append(
                {
                    "sweep": int(m.group("sweep")),
                    "peer_name": peer_name,
                    "plan": m.group("plan"),
                    "pmode": int(m.group("pmode")),
                    "solve_reason": m.group("solve_reason"),
                    "quality_flag_percent": int(m.group("qf")),
                    "active_anchor_count": int(m.group("active")),
                    "valid_anchor_count": int(m.group("valid")),
                    "rms_mm": int(m.group("rms")),
                    "max_mm": int(m.group("max")),
                    "step_mm": int(m.group("step")),
                }
            )

    write_rows(out_dir / "positions_all.csv", list(positions[0].keys()) if positions else ["sweep"], positions)
    write_rows(out_dir / "tr_all.csv", list(tr_rows[0].keys()) if tr_rows else ["sweep"], tr_rows)
    write_rows(out_dir / "cm_all.csv", list(cm_rows[0].keys()) if cm_rows else ["sweep"], cm_rows)
    write_rows(out_dir / "cs_all.csv", list(cs_rows[0].keys()) if cs_rows else ["sweep"], cs_rows)
    write_rows(out_dir / "cr_all.csv", list(cr_rows[0].keys()) if cr_rows else ["sweep"], cr_rows)
    write_rows(out_dir / "cf_all.csv", list(cf_rows[0].keys()) if cf_rows else ["sweep"], cf_rows)

    per_tag = {}
    for name in sorted({r["peer_name"] for r in tr_rows + cm_rows + cs_rows + cr_rows + cf_rows + positions if r.get("peer_name")}):
        tr = [r for r in tr_rows if r["peer_name"] == name]
        cm = [r for r in cm_rows if r["peer_name"] == name]
        cs = [r for r in cs_rows if r["peer_name"] == name]
        cr = [r for r in cr_rows if r["peer_name"] == name]
        cf = [r for r in cf_rows if r["peer_name"] == name]
        per_tag[name] = {
            "positions": sum(1 for r in positions if r["peer_name"] == name),
            "tr": len(tr),
            "cm": len(cm),
            "cs": len(cs),
            "cr": len(cr),
            "cf": len(cf),
            "cm_status_counts": dict(Counter(r["status"] for r in cm)),
            "cr_reason_counts": dict(Counter(r["reason"] for r in cr)),
            "cf_valid_anchor_count": dict(Counter(str(r["valid_anchor_count"]) for r in cf)),
            "cs_target_sets": dict(Counter(r["targets"] for r in cs).most_common(12)),
        }

    summary = {
        "success": True,
        "raw_log": str(raw_path),
        "positions_all": len(positions),
        "tr_all": len(tr_rows),
        "cm_all": len(cm_rows),
        "cs_all": len(cs_rows),
        "cr_all": len(cr_rows),
        "cf_all": len(cf_rows),
        "per_tag": per_tag,
        "positions_all_csv": str(out_dir / "positions_all.csv"),
        "tr_all_csv": str(out_dir / "tr_all.csv"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def counter_to_dict(counter: Counter) -> dict[str, int]:
    return {str(k): int(v) for k, v in counter.most_common()}


def top_counter_rows(counter: Counter, limit: int = 10) -> list[dict[str, Any]]:
    return [{"key": str(k), "count": int(v)} for k, v in counter.most_common(limit)]


def analyze_tag(tag: str,
                cm_rows: list[dict[str, str]],
                cs_rows: list[dict[str, str]],
                cr_rows: list[dict[str, str]],
                cf_rows: list[dict[str, str]]) -> dict[str, Any]:
    status_counts = Counter(row["status"] for row in cm_rows)
    target_counter = Counter(row["targets"] for row in cs_rows)
    frame_ok_counter = Counter()
    frame_partial_counter = Counter()
    frame_solve_counter = Counter(row["solve_reason"] for row in cf_rows)
    reason_counter = Counter(row["reason"] for row in cr_rows)
    per_anchor_reason: dict[str, Counter] = defaultdict(Counter)
    per_anchor_status: dict[str, Counter] = defaultdict(Counter)

    for row in cs_rows:
        statuses = [s for s in row["statuses"].split(",") if s]
        ok_count = sum(1 for s in statuses if s == "ok")
        frame_ok_counter[str(ok_count)] += 1
        if ok_count != len(statuses):
            frame_partial_counter[row["targets"]] += 1

    for row in cr_rows:
        per_anchor_reason[row["anchor_label"]][row["reason"]] += 1
        per_anchor_status[row["anchor_label"]][row["status"]] += 1

    dominant_bd_reason = Counter()
    for anchor in ("B", "D"):
        dominant_bd_reason.update(per_anchor_reason.get(anchor, Counter()))

    qfs = [int(row["quality_flag_percent"]) for row in cs_rows if row.get("quality_flag_percent")]
    return {
        "tag": tag,
        "cm_status_counts": counter_to_dict(status_counts),
        "target_set_frequency": top_counter_rows(target_counter, limit=12),
        "frame_ok_distribution": counter_to_dict(frame_ok_counter),
        "worst_partial_target_sets": top_counter_rows(frame_partial_counter, limit=12),
        "solve_reason_counts": counter_to_dict(frame_solve_counter),
        "reject_reason_counts": counter_to_dict(reason_counter),
        "bd_reason_counts": counter_to_dict(dominant_bd_reason),
        "qf": {
            "min": min(qfs) if qfs else None,
            "max": max(qfs) if qfs else None,
            "avg": round(sum(qfs) / len(qfs), 2) if qfs else None,
        },
        "per_anchor_reason_counts": {
            anchor: counter_to_dict(counter)
            for anchor, counter in sorted(per_anchor_reason.items())
        },
        "per_anchor_status_counts": {
            anchor: counter_to_dict(counter)
            for anchor, counter in sorted(per_anchor_status.items())
        },
        "latest_cs": cs_rows[-1] if cs_rows else None,
        "latest_cf": cf_rows[-1] if cf_rows else None,
        "latest_cr": cr_rows[-1] if cr_rows else None,
    }


def build_conclusion(results: dict[str, Any]) -> dict[str, Any]:
    focus = {}
    dominant_global = Counter()
    dominant_non_ok_global = Counter()
    for tag in ("BS2DCE", "BSDC91"):
        tag_result = results.get(tag, {})
        bd_counts = Counter(tag_result.get("bd_reason_counts", {}))
        dominant_global.update(bd_counts)
        for reason, count in bd_counts.items():
            if reason != "ok":
                dominant_non_ok_global[reason] += count
        focus[tag] = {
            "dominant_bd_reason": bd_counts.most_common(1)[0][0] if bd_counts else None,
            "dominant_bd_reason_count": bd_counts.most_common(1)[0][1] if bd_counts else 0,
            "frame_ok_distribution": tag_result.get("frame_ok_distribution", {}),
        }

    dominant = dominant_global.most_common(1)
    dominant_non_ok = dominant_non_ok_global.most_common(1)
    return {
        "dominant_bd_reason_global": dominant[0][0] if dominant else None,
        "dominant_bd_reason_global_count": dominant[0][1] if dominant else 0,
        "dominant_bd_non_ok_reason_global": dominant_non_ok[0][0] if dominant_non_ok else None,
        "dominant_bd_non_ok_reason_global_count": dominant_non_ok[0][1] if dominant_non_ok else 0,
        "per_tag_focus": focus,
        "patch_recommended": bool(dominant_non_ok and dominant_non_ok[0][1] >= 10),
    }


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Recv TDMA Reject Root-Cause Summary",
        "",
        f"- session_dir: `{summary['session_dir']}`",
        f"- patch_recommended: `{summary['conclusion']['patch_recommended']}`",
        f"- dominant_bd_reason_global: `{summary['conclusion']['dominant_bd_reason_global']}`",
        f"- dominant_bd_non_ok_reason_global: `{summary['conclusion']['dominant_bd_non_ok_reason_global']}`",
        "",
    ]

    for tag in ("BSF66F", "BS2DCE", "BSDC91"):
        result = summary["results"].get(tag)
        if not result:
            continue
        lines.extend(
            [
                f"## {tag}",
                "",
                f"- qf: `{result['qf']}`",
                f"- cm_status_counts: `{result['cm_status_counts']}`",
                f"- frame_ok_distribution: `{result['frame_ok_distribution']}`",
                f"- solve_reason_counts: `{result['solve_reason_counts']}`",
                f"- reject_reason_counts: `{result['reject_reason_counts']}`",
                f"- bd_reason_counts: `{result['bd_reason_counts']}`",
                f"- top_target_sets: `{result['target_set_frequency'][:5]}`",
                f"- worst_partial_target_sets: `{result['worst_partial_target_sets'][:5]}`",
                "",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze CR/CF/CS diagnostics from run_recv_tdma_capture.py")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    session_dir = Path(args.session_dir)
    summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
    cm_rows = load_rows(session_dir / "cm_all.csv")
    cs_rows = load_rows(session_dir / "cs_all.csv")
    cr_rows = load_rows(session_dir / "cr_all.csv")
    cf_rows = load_rows(session_dir / "cf_all.csv")

    results = {}
    for tag in summary.get("targets", []):
        results[tag] = analyze_tag(
            tag,
            [row for row in cm_rows if row["peer_name"] == tag],
            [row for row in cs_rows if row["peer_name"] == tag],
            [row for row in cr_rows if row["peer_name"] == tag],
            [row for row in cf_rows if row["peer_name"] == tag],
        )

    out = {
        "session_dir": str(session_dir),
        "summary_json": str((session_dir / "summary.json").resolve()),
        "results": results,
        "conclusion": build_conclusion(results),
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    write_markdown(out_md, out)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

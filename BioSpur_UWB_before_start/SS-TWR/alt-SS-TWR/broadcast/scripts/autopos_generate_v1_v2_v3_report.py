#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def cm_notify_lines(run_log: Path, tag_name: str) -> int:
    if not run_log.exists():
        return 0
    text = run_log.read_text(encoding="utf-8", errors="ignore")
    return sum(1 for line in text.splitlines() if f"{tag_name} notify: CM;" in line)


def summarize_sweep(summary_json: Path) -> dict[str, Any]:
    if not summary_json.exists():
        return {"exists": False}
    s = load_json(summary_json)
    rounds = s.get("rounds") or {}
    ok = sum(1 for r in rounds.values() if r.get("success"))
    sw_total = sum(int(r.get("sw_count") or 0) for r in rounds.values())
    return {
        "exists": True,
        "sw_sets": s.get("sw_sets"),
        "timeout_s": s.get("timeout_s"),
        "round_success": f"{ok}/{len(rounds)}",
        "sw_total_lines": sw_total,
        "path": str(summary_json),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a compact V1/V2/V3 run report from latest_run_manifest.json files.")
    ap.add_argument("--v1-manifest", required=True)
    ap.add_argument("--v2-manifest", required=True)
    ap.add_argument("--v3-manifest", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    m1 = load_json(Path(args.v1_manifest))
    m2 = load_json(Path(args.v2_manifest))
    m3 = load_json(Path(args.v3_manifest))

    tag = m1.get("tag_name") or m2.get("tag_name") or m3.get("tag_name") or "BSF66F"

    def entry(m: dict[str, Any], label: str) -> dict[str, Any]:
        capture_dir = Path(m["capture_dir"])
        solve_dir = Path(m["solve_dir"])
        sweep_summary = Path(m["sweep_summary_json"])
        tag_run_log = Path(m["tag_run_log"])

        # Layout outputs vary by version.
        v = m["version"]
        layout = None
        if v == "v1":
            layout = pick_first_existing(
                [
                    solve_dir / "v1" / "anchor_coords_v1.json",
                ]
            )
        elif v == "v2":
            layout = pick_first_existing(
                [
                    solve_dir / "v2" / "v2_fused" / "anchor_layout_v2_iterative.json",
                    solve_dir / "v2" / "v2_fused" / "anchor_layout_v2_iterative.json",
                ]
            )
        elif v == "v3":
            layout = pick_first_existing(
                [
                    solve_dir / "v3_lite" / "v3_fused" / "anchor_layout_v3_lite_iterative.json",
                    solve_dir / "v3_lite" / "v3_fused" / "anchor_layout_v3_lite_iterative.json",
                ]
            )

        return {
            "label": label,
            "version": v,
            "capture_dir": str(capture_dir),
            "solve_dir": str(solve_dir),
            "sweep": summarize_sweep(sweep_summary),
            "tag_cm_notify_lines": cm_notify_lines(tag_run_log, tag),
            "layout_path": str(layout) if layout else None,
        }

    e1 = entry(m1, "V1")
    e2 = entry(m2, "V2")
    e3 = entry(m3, "V3-lite")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# AutoPos V1 / V2 / V3-lite Overnight Report")
    lines.append("")
    lines.append(f"- Generated: {out.name}")
    lines.append(f"- Tag target: `{tag}`")
    lines.append("")

    def render(e: dict[str, Any]) -> None:
        lines.append(f"## {e['label']}")
        lines.append(f"- Version: `{e['version']}`")
        lines.append(f"- Capture dir: `{e['capture_dir']}`")
        lines.append(f"- Solve dir: `{e['solve_dir']}`")
        sw = e["sweep"]
        if sw.get("exists"):
            lines.append(
                f"- Sweep: success rounds {sw['round_success']}, sw_sets={sw.get('sw_sets')}, sw_total_lines={sw.get('sw_total_lines')}"
            )
            lines.append(f"- Sweep summary: `{sw['path']}`")
        else:
            lines.append("- Sweep: missing summary.json")
        lines.append(f"- Tag CM notify lines: `{e['tag_cm_notify_lines']}`")
        lines.append(f"- Layout output: `{e['layout_path']}`")
        lines.append("")

    render(e1)
    render(e2)
    render(e3)

    lines.append("## Notes")
    lines.append("- Each version captured fresh data: Anchor sweep (default 100 sets) + Tag115 CM (default 100 aggregated notify lines).")
    lines.append("- V3-lite is not the full V3 from the design doc; it reuses existing fusion with tighter parameters and a more aggressive solve.")
    lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


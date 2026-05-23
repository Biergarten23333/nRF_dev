#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from pathlib import Path


REPO = Path(__file__).resolve().parents[4]
FIELD_ROOT = REPO / "autopos_pipeline" / "erlangen_20260528_mocap"


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def newest(paths: list[Path]) -> Path | None:
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def ensure_link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src.resolve(), dst)
    except OSError:
        shutil.copy2(src, dst)


def find_tr_csv(capture_dir: Path) -> Path | None:
    direct = capture_dir / "tr_all.csv"
    if direct.exists():
        return direct
    matches = sorted(capture_dir.glob("tag_capture*/tr_all.csv"))
    return newest(matches)


def is_complete_sweep_summary(path: Path) -> bool:
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    rounds = summary.get("rounds")
    if not isinstance(rounds, dict):
        return False
    order = summary.get("order") or list("ABCDEFGH")
    return all(
        isinstance(rounds.get(label), dict)
        and rounds[label].get("success")
        and int(rounds[label].get("sw_count") or 0) > 0
        for label in order
    )


def parse_sw_line(line: str) -> tuple[str, list[tuple[str, int, int]]]:
    s = line.strip()
    if "SW-" not in s:
        raise ValueError("not a SW line")
    s = s.split("SW-", 1)[1]
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        raise ValueError("empty SW payload")
    master = parts[0].upper()
    if len(master) != 1 or not master.isalpha():
        raise ValueError(f"bad master: {master!r}")

    triples: list[tuple[str, int, int]] = []
    i = 1
    while i + 2 < len(parts):
        other = parts[i].upper()
        dist_mm = int(float(parts[i + 1]))
        quality = int(float(parts[i + 2]))
        if len(other) == 1 and other.isalpha():
            triples.append((other, dist_mm, quality))
        i += 3
    return master, triples


def extract_pairs_from_summary(summary_json: Path, out_csv: Path) -> int:
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    rounds = summary.get("rounds") or {}
    fieldnames = ["a", "b", "master", "dist_mm", "quality_percent", "raw_mm", "ok", "fail"]
    rows = 0
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rounds.values():
            if not isinstance(r, dict):
                continue
            for line in r.get("sw_lines") or []:
                master, triples = parse_sw_line(line)
                for other, dist_mm, quality in triples:
                    if dist_mm <= 0 or quality <= 0:
                        continue
                    w.writerow({
                        "a": min(master, other),
                        "b": max(master, other),
                        "master": master,
                        "dist_mm": dist_mm,
                        "quality_percent": quality,
                        "raw_mm": dist_mm,
                        "ok": 1,
                        "fail": 0,
                    })
                    rows += 1
    return rows


def capture_id(name: str, kind: str) -> str | None:
    patterns = {
        "static": r"^static_(ID\d+)_",
        "roto": r"^roto_(R\d+)_",
        "wand": r"^wand3_(W\d+)_",
    }
    m = re.match(patterns[kind], name)
    return m.group(1) if m else None


def roto_field_to_old_id(rid: str) -> str:
    # Existing outdoor solver metadata uses ID25.. for Roto captures.
    # R01 -> ID25, R02 -> ID26, ...
    n = int(rid[1:])
    return f"ID{24 + n:02d}"


def stage_capture_group(session: Path, staged: Path, kind: str) -> list[dict]:
    rows: list[dict] = []
    out_sub = {
        "static": "Static_Test",
        "roto": "Roto_Test",
        "wand": "Wand_Test",
    }[kind]
    for d in sorted(session.iterdir()):
        if not d.is_dir():
            continue
        cid = capture_id(d.name, kind)
        if not cid:
            continue
        tr = find_tr_csv(d)
        if not tr:
            rows.append({"kind": kind, "id": cid, "source": rel(d), "status": "missing_tr_all_csv"})
            continue
        solver_id = roto_field_to_old_id(cid) if kind == "roto" else cid
        dst_dir = staged / out_sub / f"{solver_id}_{d.name}"
        ensure_link_or_copy(tr, dst_dir / "tr_all.csv")
        summary = newest(sorted(d.glob("summary.json")) + sorted(d.glob("tag_capture*/summary.json")))
        if summary:
            ensure_link_or_copy(summary, dst_dir / "summary.json")
        rows.append({
            "kind": kind,
            "field_id": cid,
            "solver_id": solver_id,
            "source": rel(d),
            "tr_all_csv": rel(tr),
            "staged_dir": rel(dst_dir),
            "status": "ok",
        })
    return rows


def resolve_sweep_dir(session: Path, sweep: str | None) -> Path | None:
    if not sweep:
        return None
    p = Path(sweep)
    if p.is_absolute():
        return p if p.exists() else None
    direct = session / sweep
    if direct.exists():
        return direct
    matches = [d for d in session.glob(f"*{sweep}*") if d.is_dir()]
    return newest(matches)


def stage_sweep(session: Path, staged: Path, sweep: str | None = None) -> dict:
    sweep_dir = resolve_sweep_dir(session, sweep)
    search_roots = [sweep_dir] if sweep_dir else [session]
    candidates = []
    summaries = []
    for root in search_roots:
        if root is None:
            continue
        candidates.extend(root.glob("sweep1000/pairs_all.csv"))
        candidates.extend(root.glob("**/pairs_all.csv"))
        candidates.extend(root.glob("autopos*/**/pairs_all.csv"))
        summaries.extend(root.glob("sweep1000/summary.json"))
        summaries.extend(root.glob("**/summary.json"))
    pairs = newest(sorted(set(candidates)))
    if not pairs:
        complete = [p for p in summaries if is_complete_sweep_summary(p)]
        summary = newest(complete)
        if not summary:
            return {
                "status": "missing_pairs_all_csv_or_complete_summary",
                "requested_sweep": sweep,
                "requested_sweep_dir": rel(sweep_dir) if sweep_dir else None,
            }
        dst = staged / "sweep1000" / "pairs_all.csv"
        rows = extract_pairs_from_summary(summary, dst)
        ensure_link_or_copy(summary, staged / "sweep1000" / "summary.json")
        return {
            "status": "ok",
            "source": rel(summary),
            "staged": rel(dst),
            "requested_sweep": sweep,
            "selected_sweep_dir": rel(summary.parents[1]) if summary.parent.name.startswith("sweep") else rel(summary.parent),
            "derived_from_summary": True,
            "rows": rows,
        }
    dst = staged / "sweep1000" / "pairs_all.csv"
    ensure_link_or_copy(pairs, dst)
    summary = newest(sorted(pairs.parent.glob("summary.json")) + sorted(pairs.parent.glob("*.json")))
    if summary:
        ensure_link_or_copy(summary, staged / "sweep1000" / summary.name)
    return {
        "status": "ok",
        "source": rel(pairs),
        "staged": rel(dst),
        "requested_sweep": sweep,
        "selected_sweep_dir": rel(pairs.parents[1]) if pairs.parent.name.startswith("sweep") else rel(pairs.parent),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage Erlangen field captures into the outdoor solver directory shape.")
    ap.add_argument("--session", required=True, help="Capture session directory under captures/, or absolute path.")
    ap.add_argument("--out", default=str(FIELD_ROOT / "solver" / "work" / "field_dataset_staged"))
    ap.add_argument("--sweep", default=None, help="Optional sweep directory/name/id to stage instead of newest complete sweep.")
    args = ap.parse_args()

    session = Path(args.session)
    if not session.is_absolute():
        session = FIELD_ROOT / "captures" / session
    staged = Path(args.out)
    if not staged.is_absolute():
        staged = FIELD_ROOT / "solver" / "work" / staged

    if not session.exists():
        raise SystemExit(f"session not found: {session}")
    if staged.exists():
        shutil.rmtree(staged)
    for sub in ["sweep1000", "Static_Test", "Roto_Test", "Wand_Test"]:
        (staged / sub).mkdir(parents=True, exist_ok=True)

    manifest = {
        "session": str(session.resolve()),
        "staged": str(staged.resolve()),
        "sweep": stage_sweep(session, staged, args.sweep),
        "captures": [],
    }
    for kind in ["static", "roto", "wand"]:
        manifest["captures"].extend(stage_capture_group(session, staged, kind))

    (staged / "stage_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    if manifest["sweep"].get("status") != "ok":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

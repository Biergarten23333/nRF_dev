#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def stage_sweep(session: Path, staged: Path) -> dict:
    candidates = []
    candidates.extend(session.glob("sweep*/sweep1000/pairs_all.csv"))
    candidates.extend(session.glob("sweep*/**/pairs_all.csv"))
    candidates.extend(session.glob("autopos*/**/pairs_all.csv"))
    pairs = newest(sorted(set(candidates)))
    if not pairs:
        return {"status": "missing_pairs_all_csv"}
    dst = staged / "sweep1000" / "pairs_all.csv"
    ensure_link_or_copy(pairs, dst)
    summary = newest(sorted(pairs.parent.glob("summary.json")) + sorted(pairs.parent.glob("*.json")))
    if summary:
        ensure_link_or_copy(summary, staged / "sweep1000" / summary.name)
    return {"status": "ok", "source": rel(pairs), "staged": rel(dst)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage Erlangen field captures into the outdoor solver directory shape.")
    ap.add_argument("--session", required=True, help="Capture session directory under captures/, or absolute path.")
    ap.add_argument("--out", default=str(FIELD_ROOT / "solver" / "work" / "field_dataset_staged"))
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
        "sweep": stage_sweep(session, staged),
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

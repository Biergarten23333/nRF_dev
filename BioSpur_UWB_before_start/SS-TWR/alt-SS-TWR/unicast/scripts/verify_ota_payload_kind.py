#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ANCHOR_MARKERS = (
    b"ANCHOR: unified",
    b"Anchor app ready",
    b"ANCHOR_VERSION",
    b"ANCHOR-%c-%s",
)

TAG_MARKERS = (
    b"Tag BLE",
    b"VERSION fw=",
    b"CFG_OK TAG=",
    b"TDMA_SET_OK",
)


def load_inc_bytes(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8", errors="replace")
    values = [int(m.group(1), 16) for m in re.finditer(r"0x([0-9a-fA-F]{2})", text)]
    if not values:
        raise SystemExit(f"no byte literals found in generated OTA image: {path}")
    return bytes(values)


def manifest_kind(repo_root: Path, expected: str) -> tuple[str | None, Path | None]:
    manifest = repo_root / "apps" / "master_ota" / "generated" / f"{expected}_ota_manifest.json"
    if not manifest.exists():
        return None, manifest
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"failed to parse manifest {manifest}: {exc}") from exc
    return str(data.get("kind") or ""), manifest


def contains_any(data: bytes, markers: tuple[bytes, ...]) -> list[str]:
    return [m.decode("ascii", "replace") for m in markers if m in data]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refuse deploying a stale/wrong embedded OTA payload kind."
    )
    parser.add_argument("--expected", required=True, choices=("anchor", "tag"))
    parser.add_argument(
        "--inc",
        type=Path,
        default=Path("apps/master_ota/generated/ota_image.inc"),
        help="Generated C include containing the embedded signed OTA image.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    inc = args.inc if args.inc.is_absolute() else repo_root / args.inc
    if not inc.exists():
        raise SystemExit(f"OTA payload include does not exist: {inc}")

    data = load_inc_bytes(inc)
    anchor_hits = contains_any(data, ANCHOR_MARKERS)
    tag_hits = contains_any(data, TAG_MARKERS)
    m_kind, m_path = manifest_kind(repo_root, args.expected)

    errors: list[str] = []
    if args.expected == "anchor":
        if not anchor_hits:
            errors.append("embedded image does not contain anchor markers")
        if tag_hits:
            errors.append(f"embedded image contains tag markers: {tag_hits}")
        if m_kind and m_kind != "anchor_ota_bundle":
            errors.append(f"anchor manifest kind mismatch: {m_path} kind={m_kind}")
    else:
        if not tag_hits:
            errors.append("embedded image does not contain tag markers")
        if anchor_hits:
            errors.append(f"embedded image contains anchor markers: {anchor_hits}")
        if m_kind and m_kind != "tag_ota_bundle":
            errors.append(f"tag manifest kind mismatch: {m_path} kind={m_kind}")

    payload = {
        "expected": args.expected,
        "inc": str(inc),
        "size": len(data),
        "anchor_markers": anchor_hits,
        "tag_markers": tag_hits,
        "manifest": str(m_path) if m_path else None,
        "manifest_kind": m_kind,
        "ok": not errors,
        "errors": errors,
    }
    print(json.dumps(payload, indent=2))
    if errors:
        raise SystemExit(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

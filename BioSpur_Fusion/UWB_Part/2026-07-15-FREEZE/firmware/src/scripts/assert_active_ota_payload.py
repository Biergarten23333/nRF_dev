#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return repo_root / path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refuse building a Master carrier with a stale or wrong OTA payload."
    )
    parser.add_argument("--expected", required=True, choices=("anchor", "tag"))
    parser.add_argument("--marker", default="")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    generated = repo_root / "apps" / "master_ota" / "generated"
    active_path = generated / "active_ota_payload.json"
    if not active_path.exists():
        raise SystemExit(
            f"missing active OTA payload lock: {active_path}; run prepare_alt_ota_payload.py first"
        )

    data = json.loads(active_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if data.get("kind") != args.expected:
        errors.append(f"active payload kind={data.get('kind')} expected={args.expected}")
    if args.marker and data.get("fw_marker") != args.marker:
        errors.append(f"active payload marker={data.get('fw_marker')} expected={args.marker}")

    for key, sha_key in (
        ("signed_bin", "signed_bin_sha256"),
        ("dfu_zip", "dfu_zip_sha256"),
        ("ota_image_inc", "ota_image_inc_sha256"),
    ):
        path_value = data.get(key)
        expected_sha = data.get(sha_key)
        if not path_value or not expected_sha:
            errors.append(f"active payload lock missing {key}/{sha_key}")
            continue
        path = resolve_path(repo_root, str(path_value))
        if not path.exists():
            errors.append(f"active payload file missing: {path}")
            continue
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha:
            errors.append(f"{key} sha mismatch: active={expected_sha} actual={actual_sha}")

    verify = subprocess.run(
        [
            "python3",
            str(repo_root / "scripts" / "verify_ota_payload_kind.py"),
            "--repo-root",
            str(repo_root),
            "--expected",
            args.expected,
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if verify.returncode != 0:
        errors.append("embedded OTA payload kind verification failed")

    result = {
        "expected": args.expected,
        "marker": args.marker or None,
        "active_payload": str(active_path),
        "active_kind": data.get("kind"),
        "active_marker": data.get("fw_marker"),
        "verify_payload_kind_rc": verify.returncode,
        "ok": not errors,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    if verify.stdout:
        print(verify.stdout, end="")
    if verify.stderr:
        print(verify.stderr, end="")
    if errors:
        raise SystemExit(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

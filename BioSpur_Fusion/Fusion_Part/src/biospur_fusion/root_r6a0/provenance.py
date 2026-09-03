"""Read-only provenance, immutability, disk, and write-boundary audits."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Iterable


TARGET_PREFIXES = (
    Path("src/biospur_fusion/root_r6a0"),
    Path("tests/root_r6a0"),
    Path("config/root_r6a0"),
)
EXCLUDED_NAMES = frozenset({"__pycache__", ".pytest_cache"})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(16 << 20):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_payload_sha256(value: dict, omitted_key: str = "manifest_payload_sha256") -> str:
    payload = dict(value)
    payload.pop(omitted_key, None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_target(relative: Path) -> bool:
    return any(relative == target or target in relative.parents for target in TARGET_PREFIXES)


def protected_tree_digest(fusion_root: Path, scope: str) -> dict:
    root = Path(fusion_root).resolve()
    base = root / scope
    digest = hashlib.sha256()
    regular = links = total = 0
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(root)
        if _is_target(relative) or any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.is_symlink():
            content = os.readlink(path).encode("utf-8", "surrogateescape")
            mode = path.lstat().st_mode
            size = len(content)
            links += 1
        elif path.is_file():
            content = bytes.fromhex(sha256(path))
            mode = path.stat().st_mode
            size = path.stat().st_size
            regular += 1
            total += size
        else:
            continue
        name = relative.as_posix().encode("utf-8", "surrogateescape")
        digest.update(len(name).to_bytes(4, "little"))
        digest.update(name)
        digest.update((mode & 0o7777).to_bytes(4, "little"))
        digest.update(size.to_bytes(8, "little"))
        digest.update(content)
    return {
        "scope": scope,
        "regular_files": regular,
        "symlinks": links,
        "bytes": total,
        "sha256_tree_v1": digest.hexdigest(),
    }


def verify_protected_baseline(fusion_root: Path, baseline_path: Path) -> dict:
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    rows = []
    for expected in baseline["scopes"]:
        actual = protected_tree_digest(fusion_root, str(expected["scope"]))
        rows.append({
            "scope": expected["scope"],
            "expected": expected,
            "actual": actual,
            "byte_for_byte_unchanged": actual == expected,
        })
    return {
        "schema": "biospur.root_r6a0.protected_tree_verification.v1",
        "baseline_path": str(Path(baseline_path).resolve()),
        "baseline_sha256": sha256(Path(baseline_path)),
        "rows": rows,
        "pass": all(row["byte_for_byte_unchanged"] for row in rows),
    }


def root_r4_verification(root_r4: Path, expected_payload_sha256: str) -> dict:
    root = Path(root_r4).resolve()
    manifest_path = root / "REPRODUCIBILITY_MANIFEST.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    computed = canonical_json_payload_sha256(value)
    tree_digest = hashlib.sha256()
    regular = total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or any(part in EXCLUDED_NAMES for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = bytes.fromhex(sha256(path))
        tree_digest.update(len(relative).to_bytes(4, "little"))
        tree_digest.update(relative)
        tree_digest.update((path.stat().st_mode & 0o7777).to_bytes(4, "little"))
        tree_digest.update(path.stat().st_size.to_bytes(8, "little"))
        tree_digest.update(content)
        regular += 1; total += path.stat().st_size
    return {
        "path": str(root),
        "manifest_path": str(manifest_path),
        "recorded_manifest_payload_sha256": value.get("manifest_payload_sha256"),
        "recomputed_manifest_payload_sha256": computed,
        "expected_manifest_payload_sha256": expected_payload_sha256,
        "manifest_exact": computed == expected_payload_sha256 and value.get("manifest_payload_sha256") == computed,
        "tree": {"regular_files": regular, "bytes": total, "sha256_tree_v1": tree_digest.hexdigest()},
    }


def git_snapshot(git_root: Path, fusion_relative: str) -> dict:
    root = Path(git_root).resolve()
    def output(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    status = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=normal"],
    )
    target_status = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain=v1", "--untracked-files=normal", "--", fusion_relative],
        text=True,
    ).splitlines()
    return {
        "resolved_git_root": output("rev-parse", "--show-toplevel"),
        "resolved_git_dir": output("rev-parse", "--absolute-git-dir"),
        "branch": output("branch", "--show-current"),
        "head": output("rev-parse", "HEAD"),
        "status_line_count": status.count(b"\n"),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "fusion_status_lines": target_status,
    }


def disk_gate(fusion_root: Path) -> dict:
    root_usage = shutil.disk_usage("/")
    fusion_usage = shutil.disk_usage(Path(fusion_root))
    return {
        "root_free_bytes": root_usage.free,
        "root_free_gib": root_usage.free / (1024 ** 3),
        "fusion_filesystem_free_bytes": fusion_usage.free,
        "fusion_filesystem_free_gib": fusion_usage.free / (1024 ** 3),
        "projected_growth_gib": 0.25,
        "root_minimum_gib": 40,
        "fusion_minimum_gib": 100,
        "projected_growth_maximum_gib": 5,
        "pass": root_usage.free >= 40 * 1024 ** 3 and fusion_usage.free >= 100 * 1024 ** 3,
    }


def file_inventory(roots: Iterable[Path], base: Path) -> list[dict]:
    base = Path(base).resolve()
    rows = []
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if not path.is_file() or any(part in EXCLUDED_NAMES for part in path.parts):
                continue
            rows.append({
                "path": path.resolve().relative_to(base).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    return rows

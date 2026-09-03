"""Exact Root-R4 resolution and immutable-input provenance utilities."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Iterable

from .constants import ROOT_R4_MANIFEST_PAYLOAD_SHA256


REPOSITORY = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
GIT_ROOT = REPOSITORY.parent
ROOT_R4_CANDIDATE = Path("/tmp/biospur_c1_uwb_imu_root_r4_20260824T100721Z")
ARCHITECTURE_TITLE = "BioSpur Fusion Frozen Architecture Contract"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(16 << 20):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_payload_sha256(value: dict, omitted_key: str = "manifest_payload_sha256") -> str:
    payload = dict(value); payload.pop(omitted_key, None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def resolve_root_r4() -> tuple[Path, dict]:
    candidates: list[Path] = []
    if ROOT_R4_CANDIDATE.is_dir():
        candidates.append(ROOT_R4_CANDIDATE)
    for manifest in (REPOSITORY / "Fusion_Part/logs").glob("**/REPRODUCIBILITY_MANIFEST.json"):
        candidates.append(manifest.parent)
    matches = []
    for root in sorted(set(candidates)):
        manifest_path = root / "REPRODUCIBILITY_MANIFEST.json"
        if not manifest_path.is_file():
            continue
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        computed = canonical_json_payload_sha256(value)
        if computed == ROOT_R4_MANIFEST_PAYLOAD_SHA256 and value.get("manifest_payload_sha256") == computed:
            matches.append((root, value))
    if len(matches) != 1:
        raise RuntimeError(f"BLOCKED_ROOT_R4_PROVENANCE_NOT_RESOLVED: matches={len(matches)}")
    return matches[0]


def file_record(path: Path, role: str, lineage: str = "direct immutable input") -> dict:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path),
            "role": role, "lineage": lineage, "read_only": True}


def tree_digest(root: Path, excluded_names: Iterable[str] = ("__pycache__", ".pytest_cache")) -> dict:
    excluded = set(excluded_names); digest = hashlib.sha256(); regular = links = total = 0
    for base, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in excluded)
        for name in sorted(files):
            path = Path(base) / name; relative = path.relative_to(root).as_posix(); status = path.lstat()
            relative_bytes = relative.encode("utf-8", "surrogateescape")
            if stat.S_ISLNK(status.st_mode):
                content = hashlib.sha256(os.readlink(path).encode("utf-8", "surrogateescape")).digest(); links += 1
            elif stat.S_ISREG(status.st_mode):
                content = bytes.fromhex(sha256(path)); regular += 1; total += status.st_size
            else:
                continue
            digest.update(len(relative_bytes).to_bytes(4, "little")); digest.update(relative_bytes)
            digest.update((status.st_mode & 0o7777).to_bytes(4, "little")); digest.update(status.st_size.to_bytes(8, "little")); digest.update(content)
    return {"root": str(root.resolve()), "regular_files": regular, "symlinks": links,
            "bytes": total, "sha256_tree_v1": digest.hexdigest()}


def git_snapshot() -> dict:
    raw = subprocess.check_output(["git", "-C", str(GIT_ROOT), "status", "--porcelain=v1", "--untracked-files=all"])
    return {
        "branch": subprocess.check_output(["git", "-C", str(GIT_ROOT), "branch", "--show-current"], text=True).strip(),
        "head": subprocess.check_output(["git", "-C", str(GIT_ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "status_lines": raw.count(b"\n"), "status_sha256": hashlib.sha256(raw).hexdigest(),
    }


def architecture_contract_search() -> dict:
    # Deliberately excludes logs so a generated report cannot become doctrine.
    candidates = []
    for path in (REPOSITORY / "Fusion_Part").rglob("*"):
        if not path.is_file() or "logs" in path.relative_to(REPOSITORY / "Fusion_Part").parts:
            continue
        if path.suffix.lower() not in (".md", ".json", ".txt"):
            continue
        try:
            if ARCHITECTURE_TITLE in path.read_text(encoding="utf-8", errors="ignore"):
                candidates.append(file_record(path, "frozen architecture doctrine"))
        except OSError:
            continue
    return {"title": ARCHITECTURE_TITLE, "local_document_present": bool(candidates), "matches": candidates,
            "fallback": "prompt-reproduced invariants enforced; no local identity fabricated" if not candidates else None}

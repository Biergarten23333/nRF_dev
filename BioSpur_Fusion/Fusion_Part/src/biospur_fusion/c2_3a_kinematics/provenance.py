"""Fail-closed provenance gate for the frozen Capture2 3A interface."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


WORKSPACE = Path(__file__).resolve().parents[3]
FORMAL_SEAL = Path(
    "logs/c2_imu_19plus2_formal_freeze_20260901_082039/FORMAL_FREEZE_SEAL.json"
)
FORMAL_MANIFEST = Path(
    "logs/c2_imu_19plus2_formal_freeze_20260901_082039/FORMAL_FREEZE_MANIFEST.json"
)
PRIMARY_TRAJECTORY = Path(
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/POSE_RESET_QMT_TRAJECTORY.npz"
)
DIAGNOSTIC_REPORT = Path(
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/POSE_RESET_QMT_DIAGNOSTIC.json"
)
FROZEN_REPLAY_CALIBRATION = Path(
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/FROZEN_C2_AVATAR_REPLAY_CALIBRATION.npz"
)
HOLDOUT_TRAJECTORY = Path(
    "logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz"
)
HOLDOUT_REPORT = Path(
    "logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_REPORT.json"
)
BASE_CONFIG = Path("config/c2_coupled_progressive_v1/config.json")
EFFECTIVE_AMENDMENT = Path(
    "config/c2_coupled_progressive_v1/AMENDMENT_001_REMOVE_TORSO_SCALAR.json"
)
DIRECT_FK_OWNER = Path("src/biospur_fusion/c2_coupled_progressive/renderer.py")
TOPOLOGY_OWNER = Path("src/biospur_fusion/c2_coupled_progressive/contracts.py")
OUTPUT_COORDINATE_OWNER = Path(
    "src/biospur_fusion/c2_coupled_progressive/output_coordinates.py"
)

EXPECTED_SEAL_SHA256 = (
    "f41317208851eb3b0037b1463dd45aa3935d258161756f9549203603ef885534"
)
EXPECTED_MANIFEST_SHA256 = (
    "e4edfa682daa6c3002212d8c3a8e0992e0c4cb562938434d4d2f75ad43f87acb"
)
EXPECTED_BOUND_FILE_COUNT = 132
EXPECTED_BOUND_TOTAL_BYTES = 1_261_349_621
BOUND_COLLECTIONS = (
    "accepted_artifacts",
    "canonical_payload",
    "capture_metadata",
    "effective_configuration",
    "implementation",
)

REQUIRED_BINDINGS = MappingProxyType(
    {
        str(PRIMARY_TRAJECTORY): (
            "0f3ce2f9765508829de66d6681d17b433af6b54167c83619d9ddaffa13fbccdd"
        ),
        str(DIAGNOSTIC_REPORT): (
            "0e1d1dac79efebd8ddbcc928d5aa3ba56e52bfec4a040bb3301176c4b84b9aaa"
        ),
        str(FROZEN_REPLAY_CALIBRATION): (
            "ddc25eef63dce56065478dc331d667f3ec85502193c6e11f3cee1e83abd2431d"
        ),
        str(HOLDOUT_TRAJECTORY): (
            "da0855cb3b440cfbc565d60c4aedc0dbbe855fb3caff1e53ec7350c91929d639"
        ),
        str(HOLDOUT_REPORT): (
            "196e6e50a19c13290652ddae64ed8cfa8b9bed00093f0415910a33b314ec0cdc"
        ),
        str(BASE_CONFIG): (
            "d675c963a75a4e5b9dd103278401ac2bc93d24244dba4ffc1988a04a96d343e3"
        ),
        str(EFFECTIVE_AMENDMENT): (
            "d7a0554321c77b59a3dac8e91bdaab4dc68f2e3c06ba3faeba529ae1834a8170"
        ),
        str(DIRECT_FK_OWNER): (
            "aa62192f0e4afe9c129331650def3993581f59d3d371f44329c51028973ef66f"
        ),
        str(TOPOLOGY_OWNER): (
            "8823bb6ec93829388c4e3f978eed17dafdb2d8ce50a92d5940ff0fe3094d9361"
        ),
        str(OUTPUT_COORDINATE_OWNER): (
            "49834491a1adb21156928c81c4ab51ee685134c4cf6f65c5c3cfc969dde1eeb5"
        ),
    }
)


class FrozenC2ProvenanceError(RuntimeError):
    """Raised when any formally bound Capture2 input fails closed."""


@dataclass(frozen=True)
class FrozenC2Verification:
    """Immutable result of rehashing the complete formal freeze."""

    seal_sha256: str
    manifest_sha256: str
    bound_file_count: int
    bound_total_bytes: int
    bound_sha256: Mapping[str, str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenC2ProvenanceError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise FrozenC2ProvenanceError(f"JSON object required: {path}")
    return value


def _require_digest(path: Path, expected: str) -> str:
    if not path.is_file():
        raise FrozenC2ProvenanceError(f"missing frozen artifact: {path}")
    actual = sha256_file(path)
    if not hmac.compare_digest(actual, expected):
        raise FrozenC2ProvenanceError(
            f"frozen SHA-256 mismatch for {path}: {actual} != {expected}"
        )
    return actual


def _bound_path(workspace: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        raise FrozenC2ProvenanceError(f"absolute bound path is forbidden: {relative}")
    resolved_workspace = workspace.resolve()
    resolved = (resolved_workspace / path).resolve()
    try:
        resolved.relative_to(resolved_workspace)
    except ValueError as exc:
        raise FrozenC2ProvenanceError(
            f"bound path escapes the canonical workspace: {relative}"
        ) from exc
    return resolved


def verify_frozen_c2(
    workspace: Path = WORKSPACE,
) -> FrozenC2Verification:
    """Rehash the seal, manifest, and all 132 formally bound files.

    This gate deliberately runs before any trajectory array is loaded. New 3A
    code is not part of the historical seal; the hash-bound artifacts remain
    the sole owners of orientations, display geometry, FK, and output axes.
    """

    workspace = workspace.resolve()
    seal_path = workspace / FORMAL_SEAL
    manifest_path = workspace / FORMAL_MANIFEST
    seal_sha = _require_digest(seal_path, EXPECTED_SEAL_SHA256)
    seal = _json_object(seal_path)
    if (
        seal.get("schema") != "biospur-capture2-avatar-formal-freeze-seal-v1"
        or seal.get("status") != "FORMAL_FREEZE_SEALED"
        or seal.get("manifest") != str(FORMAL_MANIFEST)
        or seal.get("verified_bound_file_count") != EXPECTED_BOUND_FILE_COUNT
    ):
        raise FrozenC2ProvenanceError("formal seal identity or status changed")
    if seal.get("verification_mismatches") != []:
        raise FrozenC2ProvenanceError("formal seal records verification mismatches")
    if seal.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256:
        raise FrozenC2ProvenanceError("formal seal manifest binding changed")

    manifest_sha = _require_digest(manifest_path, EXPECTED_MANIFEST_SHA256)
    manifest = _json_object(manifest_path)
    records: list[dict[str, Any]] = []
    for collection in BOUND_COLLECTIONS:
        rows = manifest.get(collection)
        if not isinstance(rows, list):
            raise FrozenC2ProvenanceError(
                f"formal manifest collection is missing: {collection}"
            )
        for row in rows:
            if not isinstance(row, dict):
                raise FrozenC2ProvenanceError(
                    f"non-object row in formal manifest: {collection}"
                )
            records.append(row)
    if len(records) != EXPECTED_BOUND_FILE_COUNT:
        raise FrozenC2ProvenanceError(
            f"formal bound-file count changed: {len(records)}"
        )

    verified: dict[str, str] = {}
    total_bytes = 0
    for row in records:
        relative = row.get("path")
        expected_sha = row.get("sha256")
        expected_bytes = row.get("bytes")
        if (
            not isinstance(relative, str)
            or not isinstance(expected_sha, str)
            or not isinstance(expected_bytes, int)
        ):
            raise FrozenC2ProvenanceError("malformed formal manifest row")
        if relative in verified:
            raise FrozenC2ProvenanceError(f"duplicate bound path: {relative}")
        path = _bound_path(workspace, relative)
        if not path.is_file() or path.stat().st_size != expected_bytes:
            raise FrozenC2ProvenanceError(
                f"frozen byte-size mismatch for {relative}"
            )
        verified[relative] = _require_digest(path, expected_sha)
        total_bytes += expected_bytes

    if total_bytes != EXPECTED_BOUND_TOTAL_BYTES:
        raise FrozenC2ProvenanceError(
            f"formal bound-byte total changed: {total_bytes}"
        )
    for relative, expected_sha in REQUIRED_BINDINGS.items():
        if verified.get(relative) != expected_sha:
            raise FrozenC2ProvenanceError(
                f"required 3A owner is not formally bound: {relative}"
            )

    return FrozenC2Verification(
        seal_sha256=seal_sha,
        manifest_sha256=manifest_sha,
        bound_file_count=len(verified),
        bound_total_bytes=total_bytes,
        bound_sha256=MappingProxyType(verified),
    )


__all__ = [
    "BASE_CONFIG",
    "DIAGNOSTIC_REPORT",
    "DIRECT_FK_OWNER",
    "EFFECTIVE_AMENDMENT",
    "FORMAL_MANIFEST",
    "FORMAL_SEAL",
    "FROZEN_REPLAY_CALIBRATION",
    "FrozenC2ProvenanceError",
    "FrozenC2Verification",
    "HOLDOUT_REPORT",
    "HOLDOUT_TRAJECTORY",
    "OUTPUT_COORDINATE_OWNER",
    "PRIMARY_TRAJECTORY",
    "REQUIRED_BINDINGS",
    "TOPOLOGY_OWNER",
    "WORKSPACE",
    "sha256_file",
    "verify_frozen_c2",
]

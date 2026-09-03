"""Verified read-only access to the sealed, continuous C2 orientation frontend."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from .contracts import EPISODES, NODE_TO_SEGMENT, ROOT


ARCHIVE_REL = Path(
    "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/"
    "C2_NONHINGE_TRAINING_REPLAY_001/FRONTEND_RECONSTRUCTION_INPUTS.npz"
)
MANIFEST_REL = ARCHIVE_REL.with_suffix(".json")
CONTINUITY_REL = Path(
    "logs/c2_basis_progressive_20260829T102836Z/P1_FRONTEND/P1_CAPTURE_WIDE_STATE_AUDIT.json"
)
EXPECTED_ARCHIVE_SHA256 = "58f88f9fb59d64a20c9c3c1f29db2309eb2a38e6fb3bd62d982969b51bf54cd7"
EXPECTED_MANIFEST_SHA256 = "db1ed458cbc80ad07cd1a885d5ac42498ab6057ea560d6535524ae244b5e7a22"
ALLOWED_FIELDS = frozenset({
    "acc_mps2", "gyro_rads", "time_us", "derived_boot_epoch",
    "contiguous_span_id", "gap_only_covariance_rad2", "quat_world_sensor_wxyz",
})
KEY_RE = re.compile(r"^orientation/(?P<episode>\d{2})/(?P<node>BSF[0-9A-F]{4})/(?P<field>[a-z0-9_]+)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(header + array.tobytes()).hexdigest()


@dataclass(frozen=True)
class NodeSeries:
    time_us: np.ndarray
    derived_boot_epoch: np.ndarray
    acc_mps2: np.ndarray
    gyro_rads: np.ndarray
    quat_world_sensor_wxyz: np.ndarray
    contiguous_span_id: np.ndarray
    gap_covariance_rad2: np.ndarray

    def __post_init__(self) -> None:
        n = len(self.time_us)
        if self.derived_boot_epoch.shape != (n,):
            raise ValueError("frontend boot-epoch shape mismatch")
        if self.acc_mps2.shape != (n, 3) or self.gyro_rads.shape != (n, 3):
            raise ValueError("frontend vector shape mismatch")
        if self.quat_world_sensor_wxyz.shape != (n, 4):
            raise ValueError("frontend quaternion shape mismatch")
        if np.any(np.diff(self.time_us) <= 0) or not np.all(np.isfinite(self.acc_mps2)):
            raise ValueError("frontend time/finite invariant failed")
        norm = np.linalg.norm(self.quat_world_sensor_wxyz, axis=1)
        if not np.allclose(norm, 1.0, atol=2e-6):
            raise ValueError("frontend quaternion normalization failed")


@dataclass(frozen=True)
class EpisodeFrontend:
    chronological_index: int
    qa_label: str
    nodes: dict[str, NodeSeries]
    pair_alignment_reports: dict[str, Any]


class VerifiedFrontendArchive:
    """Only reads whitelisted `orientation/*` arrays; old replay/fit keys are never consumed."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.archive_path = self.root / ARCHIVE_REL
        self.manifest_path = self.root / MANIFEST_REL
        self.continuity_path = self.root / CONTINUITY_REL
        self._manifest: dict[str, Any] | None = None
        self._consumed_keys: list[str] = []

    def verify_seal_and_semantics(self) -> dict[str, Any]:
        if _sha256(self.archive_path) != EXPECTED_ARCHIVE_SHA256:
            raise RuntimeError("sealed frontend archive hash mismatch")
        if _sha256(self.manifest_path) != EXPECTED_MANIFEST_SHA256:
            raise RuntimeError("sealed frontend manifest hash mismatch")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        continuity = json.loads(self.continuity_path.read_text(encoding="utf-8"))
        if manifest.get("heldout_opened") is not False or manifest.get("fit_geometry_or_progressive_recomputed") is not False:
            raise RuntimeError("frontend archive includes forbidden fit/heldout ownership")
        if manifest.get("frontend_reconstruction_applies_frozen_final_calibration_posterior_backwards") is not False:
            raise RuntimeError("frontend archive applies an old fit backwards")
        if continuity.get("node_state_count") != 10 or continuity.get("states_created_per_node") != 1:
            raise RuntimeError("frontend is not one state per node")
        if continuity.get("episode_reset_count") != 0 or continuity.get("gaps_concatenated") is not False:
            raise RuntimeError("frontend reset or gap concatenation detected")
        if continuity.get("gap_policy") != "NO_UPDATE_WITH_COVARIANCE_GROWTH":
            raise RuntimeError("frontend gap semantics changed")
        if manifest.get("npz", {}).get("sha256") != EXPECTED_ARCHIVE_SHA256:
            raise RuntimeError("frontend manifest does not bind archive")
        self._manifest = manifest
        return {
            "archive": str(ARCHIVE_REL), "archive_sha256": EXPECTED_ARCHIVE_SHA256,
            "manifest": str(MANIFEST_REL), "manifest_sha256": EXPECTED_MANIFEST_SHA256,
            "continuous_state_per_node": 1, "episode_reset_count": 0,
            "allowed_prefix": "orientation/", "allowed_fields": sorted(ALLOWED_FIELDS),
            "replay_input_consumed": False, "old_joint_heading_mount_fit_consumed": False,
        }

    def alignment_reports_for_episode(self, index: int) -> dict[str, Any]:
        if self._manifest is None:
            self.verify_seal_and_semantics()
        reports = self._manifest["pair_clock_and_alignment_audits"][index]["pair_alignment_reports"]
        return json.loads(json.dumps(reports))

    def _read(self, archive: Any, key: str) -> np.ndarray:
        match = KEY_RE.fullmatch(key)
        if match is None or match.group("field") not in ALLOWED_FIELDS:
            raise RuntimeError(f"frontend whitelist rejected key: {key}")
        if match.group("node") not in NODE_TO_SEGMENT:
            raise RuntimeError(f"frontend whitelist rejected node: {key}")
        value = np.array(archive[key], copy=True)
        expected = self._manifest["array_bindings"][key]
        if list(value.shape) != expected["shape"] or str(value.dtype) != expected["dtype"]:
            raise RuntimeError(f"frontend array schema mismatch: {key}")
        if _array_sha256(value) != expected["sha256"]:
            raise RuntimeError(f"frontend array content mismatch: {key}")
        self._consumed_keys.append(key)
        return value

    def episodes(self) -> Iterator[EpisodeFrontend]:
        if self._manifest is None:
            self.verify_seal_and_semantics()
        with np.load(self.archive_path, allow_pickle=False) as archive:
            for index, qa_label in enumerate(EPISODES):
                prefix = f"orientation/{index:02d}"
                nodes: dict[str, NodeSeries] = {}
                for node in NODE_TO_SEGMENT:
                    base = f"{prefix}/{node}"
                    nodes[node] = NodeSeries(
                        time_us=self._read(archive, f"{base}/time_us"),
                        derived_boot_epoch=self._read(archive, f"{base}/derived_boot_epoch"),
                        acc_mps2=self._read(archive, f"{base}/acc_mps2"),
                        gyro_rads=self._read(archive, f"{base}/gyro_rads"),
                        quat_world_sensor_wxyz=self._read(archive, f"{base}/quat_world_sensor_wxyz"),
                        contiguous_span_id=self._read(archive, f"{base}/contiguous_span_id"),
                        gap_covariance_rad2=self._read(archive, f"{base}/gap_only_covariance_rad2"),
                    )
                yield EpisodeFrontend(index, qa_label, nodes, self.alignment_reports_for_episode(index))

    def access_audit(self) -> dict[str, Any]:
        bad = [key for key in self._consumed_keys if not key.startswith("orientation/")]
        return {
            "consumed_key_count": len(self._consumed_keys),
            "consumed_keys_sha256": hashlib.sha256("\n".join(self._consumed_keys).encode()).hexdigest(),
            "all_consumed_keys_whitelisted": not bad,
            "bad_keys": bad,
            "replay_input_consumed": any(key.startswith("replay_input/") for key in self._consumed_keys),
        }

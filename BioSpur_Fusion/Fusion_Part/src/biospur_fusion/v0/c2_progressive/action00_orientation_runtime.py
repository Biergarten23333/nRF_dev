"""Role-scoped Action00 orientation owner for engineering tilt diagnostics.

This owner deliberately does not instantiate the historical progressive
calibration runtime.  It validates one pinned, append-only source authority
anchored to the existing prefit/nonhinge chain, then delegates all orientation
math to the existing :class:`ContinuousVQFState`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Any, Mapping

from .architecture_guard import C2ExecutionGuard
from .orientation import (
    OrientedAction,
    _continuous_vqf_state_from_validated_action00_authority,
)
from .range_reader import DecodedAction


AUTHORITY_RELATIVE = Path(
    "logs/c2_action00_orientation_source_authority_20260908T201000Z/"
    "SOURCE_AUTHORITY.json"
)
EXPECTED_SOURCE_PATHS = frozenset({
    "src/biospur_fusion/__init__.py",
    "src/biospur_fusion/v0/__init__.py",
    "src/biospur_fusion/v0/contracts.py",
    "src/biospur_fusion/v0/c2_progressive/__init__.py",
    "src/biospur_fusion/v0/c2_progressive/action00_orientation_runtime.py",
    "src/biospur_fusion/v0/c2_progressive/architecture_guard.py",
    "src/biospur_fusion/v0/c2_progressive/calibration_posterior.py",
    "src/biospur_fusion/v0/c2_progressive/orientation.py",
    "src/biospur_fusion/v0/c2_progressive/range_reader.py",
})
STAGE_AUTHORIZED_CHANGED_PATHS = frozenset({
    "src/biospur_fusion/v0/c2_progressive/action00_orientation_runtime.py",
    "src/biospur_fusion/v0/c2_progressive/orientation.py",
})
PREFIT_INHERITED_SOURCE_PATHS = frozenset({
    "src/biospur_fusion/v0/c2_progressive/__init__.py",
    "src/biospur_fusion/v0/c2_progressive/architecture_guard.py",
    "src/biospur_fusion/v0/c2_progressive/range_reader.py",
})
NONHINGE_INHERITED_SOURCE_PATHS = frozenset({
    "src/biospur_fusion/v0/c2_progressive/architecture_guard.py",
    "src/biospur_fusion/v0/c2_progressive/calibration_posterior.py",
    "src/biospur_fusion/v0/c2_progressive/range_reader.py",
})
# These loader modules were not members of the historical prefit closure.  They
# are unchanged support code, pinned here and independently bound together with
# this validator by the one-shot PRE_RUN manifest.
EXPECTED_LOADER_SOURCE_HASHES = MappingProxyType({
    "src/biospur_fusion/__init__.py": "e882125ac7d4de2dbe6de6893f3bcb8c299820c52fe06e6e210fcfd3e7b7937d",
    "src/biospur_fusion/v0/__init__.py": "89622bd50145b097b64b1349a4cb9872fc3ea1100348b4fdb886ba02f7bdb015",
    "src/biospur_fusion/v0/contracts.py": "fb196bfb99b1c34e6a2f3d92d11843612aea48f16c300b1d81ddd4e103c423a9",
})


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _regular_immutable(path: Path) -> None:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or value.st_mode & 0o222:
        raise RuntimeError(f"Action00 authority file is absent, mutable, or a symlink: {path}")


def _regular(path: Path) -> None:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise RuntimeError(f"Action00 source is absent or a symlink: {path}")


class Action00OrientationRuntime:
    """One-shot, Action00-only facade over the existing continuous VQF owner."""

    def __init__(self, *, root: Path, expected_authority_sha256: str,
                 settings: Mapping[str, Any],
                 initial_stochastic_state: Mapping[str, Any]) -> None:
        self.root = Path(root).resolve(strict=True)
        if (
            len(expected_authority_sha256) != 64
            or any(character not in "0123456789abcdef"
                   for character in expected_authority_sha256)
        ):
            raise ValueError("expected Action00 authority identity is not SHA-256")
        authority_path = (self.root / AUTHORITY_RELATIVE).resolve(strict=True)
        authority_path.relative_to(self.root)
        _regular_immutable(authority_path)
        if _sha(authority_path) != expected_authority_sha256:
            raise RuntimeError("Action00 orientation source authority is not approved")
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        if (
            authority.get("schema") != "biospur.c2.action00_orientation_source_authority.v1"
            or authority.get("role") != "ACTION00_ENGINEERING_POLICY"
            or authority.get("product_ready") is not False
            or authority.get("scientific_pass") is not False
        ):
            raise RuntimeError("foreign Action00 orientation authority role")
        if {row["path"] for row in authority["source_files"]} != EXPECTED_SOURCE_PATHS:
            raise RuntimeError("Action00 orientation source closure path set differs")
        if frozenset(authority.get("stage_authorized_changed_paths", ())) != STAGE_AUTHORIZED_CHANGED_PATHS:
            raise RuntimeError("Action00 authority changed-path scope differs")
        declared_sources = {row["path"]: row["sha256"] for row in authority["source_files"]}
        if any(declared_sources.get(path) != digest
               for path, digest in EXPECTED_LOADER_SOURCE_HASHES.items()):
            raise RuntimeError("Action00 loader source lineage differs")
        for binding in authority["source_files"]:
            path = (self.root / binding["path"]).resolve(strict=True)
            path.relative_to(self.root)
            _regular(path)
            if _sha(path) != binding["sha256"]:
                raise RuntimeError(f"Action00 orientation source closure changed: {binding['path']}")
        for binding in authority["parent_chain"] + authority["focused_gates"]:
            path = (self.root / binding["path"]).resolve(strict=True)
            path.relative_to(self.root)
            _regular_immutable(path)
            if _sha(path) != binding["sha256"]:
                raise RuntimeError(f"Action00 orientation source closure changed: {binding['path']}")
        prefit_binding = authority["required_parent_prefit_seal"]
        prefit_path = (self.root / prefit_binding["path"]).resolve(strict=True)
        prefit = json.loads(prefit_path.read_text(encoding="utf-8"))
        prefit_sources = prefit.get("qualified_source_hashes", {})
        if (
            _sha(prefit_path) != prefit_binding["sha256"]
            or prefit.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
            or any(prefit_sources.get(path) != declared_sources[path]
                   for path in PREFIT_INHERITED_SOURCE_PATHS)
        ):
            raise RuntimeError("Action00 inherited prefit source lineage differs")

        delta_binding = authority["nonhinge_source_delta"]
        delta_path = (self.root / delta_binding["path"]).resolve(strict=True)
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
        if (
            _sha(delta_path) != delta_binding["sha256"]
            or delta.get("schema") != "biospur-c2-nonhinge-training-replay-source-delta-v1"
            or delta.get("direct_user_authority_thread_id") != delta_binding["direct_user_authority_thread_id"]
            or delta.get("relay_and_independent_monitor_thread_id") != delta_binding["monitor_thread_id"]
            or delta.get("parent_prefit_seal") != authority["required_parent_prefit_seal"]
            or delta.get("parent_diagnostic_activation") != authority["required_parent_activation"]
            or delta.get("parent_authorized_source_delta") != authority["required_parent_source_delta"]
            or delta.get("focused_owner_test") != authority["required_focused_owner_test"]
            or any(delta.get("effective_source_hashes", {}).get(path)
                   != declared_sources[path] for path in NONHINGE_INHERITED_SOURCE_PATHS)
        ):
            raise RuntimeError("Action00 authority does not descend from the authorized nonhinge chain")
        settings_semantic = _semantic(settings)
        if (
            settings_semantic != authority["settings_semantic_sha256"]
            or settings_semantic != delta["settings_semantic_sha256"]
        ):
            raise RuntimeError("Action00 runtime settings differ from source authority")
        initial_raw = json.dumps(
            initial_stochastic_state, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()
        initial_semantic = hashlib.sha256(initial_raw).hexdigest()
        initial_binding = authority["initial_stochastic_state"]
        initial_path = (self.root / initial_binding["path"]).resolve(strict=True)
        _regular_immutable(initial_path)
        initial_stat = initial_path.stat()
        parsed = json.loads(initial_path.read_text(encoding="utf-8"))
        if (
            _sha(initial_path) != initial_binding["file_sha256"]
            or _semantic(parsed) != initial_binding["semantic_sha256"]
            or initial_semantic != initial_binding["semantic_sha256"]
            or [initial_stat.st_dev, initial_stat.st_ino, initial_stat.st_size,
                initial_stat.st_mtime_ns] != initial_binding["stat_identity"]
            or str(settings["execution_contract"]["initial_stochastic_state_relative_path"])
            != initial_binding["path"]
        ):
            raise RuntimeError("Action00 initial stochastic-state authority differs")
        guard = C2ExecutionGuard(settings)
        guard.begin_capture("C2")
        source_closure = {row["path"]: row["sha256"] for row in authority["source_files"]}
        provenance_authority = MappingProxyType({
            "prefit_seal_sha256": authority["required_parent_prefit_seal"]["sha256"],
            "qualified_source_closure_digest": _semantic(source_closure),
            "initial_stochastic_state_semantic_sha256": initial_semantic,
            "initial_stochastic_state_source_status": "SEALED_SETTINGS_PATH_AND_SEMANTIC_BOUND",
            "initial_stochastic_state_source_sha256": initial_binding["file_sha256"],
            "initial_stochastic_state_source_relative_path": initial_binding["path"],
            "initial_stochastic_state_source_size": initial_stat.st_size,
            "initial_stochastic_state_source_mtime_ns": initial_stat.st_mtime_ns,
            "settings_semantic_sha256": settings_semantic,
            "timer_domain": "B306_TIMER2_US_NODE_LOCAL",
            "vqf_version": authority["vqf_version"],
            "action00_source_authority_sha256": expected_authority_sha256,
            "action00_source_authority_role": authority["role"],
        })
        self._owner = _continuous_vqf_state_from_validated_action00_authority(
            initial_stochastic_state, execution_guard=guard,
            sample_period_s=float(settings["orientation"]["sample_period_s"]),
            unknown_boot_orientation_sigma_rad=float(
                settings["orientation"]["unknown_boot_orientation_sigma_rad"]
            ),
            unknown_unusable_episode_orientation_sigma_rad=float(
                settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
            ),
            calibration_settings=settings["calibration_posterior"],
            validated_runtime_authority=provenance_authority,
        )
        self._consumed = False
        self.authority_digest = expected_authority_sha256

    def process(self, action: DecodedAction) -> OrientedAction:
        if self._consumed:
            raise RuntimeError("Action00 orientation runtime is one-shot")
        if type(action) is not DecodedAction or action.chronological_index != 0 or action.action != "00_initial_still":
            raise ValueError("Action00 orientation runtime rejects foreign action")
        self._consumed = True
        return self._owner.process(action)

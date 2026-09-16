"""Sealed static inputs for the non-promotable C2 root diagnostic."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Mapping

import numpy as np

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.root_r3.estimator import RootFilterConfig
from .run_calibration import NODES, _clock_models_document

_ROOT = Path(__file__).resolve().parents[3]
_LAYOUT = _ROOT.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
_CLOCK = _ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
_EXPECTED = MappingProxyType({
    _LAYOUT: "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1",
    _CLOCK: "b3c18d2d0ece3826498d2adc3cd41f3e4412794557f8525adc2f73bfa4ae3a66",
})
_SOURCE_HASHES = MappingProxyType({
    "beacon_clock.py": "c3f307f49f7175739006f18dc298797c004c41e686240e4270f5d1faf412a228",
    "run_calibration.py": "679e049cee17fad2890c0a27ef6e85af8cf2b0060399ba8650f2020f753c22df",
    "direct_body_shadow_ab.py": "341a0956b4ea7363564c9cb0e9813a26878d8d8d1b681a6d9adb46bc29e42168",
    "root_r3/estimator.py": "516e41b9ab62df00dd3486cf4aa944e5055e800a84c1b0159458f824cf29fa23",
})
_CONSTRUCTOR = object()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_json(path: Path, expected_sha256: str) -> tuple[dict, tuple[int, int, int, int]]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("sealed static artifact is not a regular file")
        chunks = []
        while block := os.read(descriptor, 1 << 20):
            chunks.append(block)
        after = os.fstat(descriptor)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        if identity(before) != identity(after):
            raise ValueError("sealed static artifact changed while reading")
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if _sha(payload) != expected_sha256:
        raise ValueError("sealed static artifact SHA-256 mismatch")
    document = json.loads(payload)
    if type(document) is not dict:
        raise ValueError("sealed static artifact is not a JSON object")
    return document, identity(after)


@dataclass(frozen=True)
class DiagnosticUncalibratedRangeModel:
    status: str = "DIAGNOSTIC_ONLY_UNCALIBRATED"
    product_ready: bool = False
    scientific_pass: bool = False


@dataclass(frozen=True, slots=True, init=False)
class DiagnosticC2StaticOwner:
    """Private-construction static owner; never a production reference owner."""
    anchors_m: np.ndarray
    anchor_delay_m: np.ndarray
    tag_delay_m: float
    clocks: Mapping[str, DirectNodeLinkClock]
    root_config: RootFilterConfig
    range_model: DiagnosticUncalibratedRangeModel
    artifact_sha256: Mapping[str, str]
    artifact_stat: Mapping[str, tuple[int, int, int, int]]
    source_hashes: Mapping[str, str]
    digest: str
    qualification = "DIAGNOSTIC_ROOT_ONLY_NON_PROMOTABLE"

    def __init__(self, capability: object, *, anchors_m: np.ndarray,
                 anchor_delay_m: np.ndarray, tag_delay_m: float,
                 clocks: Mapping[str, DirectNodeLinkClock], artifact_sha256: Mapping[str, str],
                 artifact_stat: Mapping[str, tuple[int, int, int, int]]) -> None:
        if capability is not _CONSTRUCTOR:
            raise RuntimeError("DiagnosticC2StaticOwner has a private constructor")
        anchors = np.asarray(anchors_m, float).reshape(8, 3).copy()
        delays = np.asarray(anchor_delay_m, float).reshape(8).copy()
        if (not np.isfinite(anchors).all() or not np.isfinite(delays).all()
                or np.linalg.matrix_rank(anchors - anchors.mean(0)) != 3
                or not np.isfinite(tag_delay_m)):
            raise ValueError("invalid sealed SI anchor layout")
        if set(clocks) != set(NODES) or any(type(v) is not DirectNodeLinkClock for v in clocks.values()):
            raise ValueError("sealed direct-clock node inventory mismatch")
        anchors.setflags(write=False); delays.setflags(write=False)
        object.__setattr__(self, "anchors_m", anchors)
        object.__setattr__(self, "anchor_delay_m", delays)
        object.__setattr__(self, "tag_delay_m", float(tag_delay_m))
        object.__setattr__(self, "clocks", MappingProxyType(dict(sorted(clocks.items()))))
        object.__setattr__(self, "root_config", RootFilterConfig())
        object.__setattr__(self, "range_model", DiagnosticUncalibratedRangeModel())
        object.__setattr__(self, "artifact_sha256", MappingProxyType(dict(artifact_sha256)))
        object.__setattr__(self, "artifact_stat", MappingProxyType(dict(artifact_stat)))
        object.__setattr__(self, "source_hashes", _SOURCE_HASHES)
        object.__setattr__(self, "digest", self._computed_digest())

    def _computed_digest(self) -> str:
        return _sha(json.dumps({
            "schema": "biospur.c2.diagnostic_static_owner.v1",
            "qualification": self.qualification,
            "anchors_m": self.anchors_m.tolist(),
            "anchor_delay_m": self.anchor_delay_m.tolist(),
            "tag_delay_m": self.tag_delay_m,
            "clocks": [asdict(self.clocks[node]) for node in sorted(self.clocks)],
            "root_config": asdict(self.root_config),
            "range_model": asdict(self.range_model),
            "artifacts": dict(self.artifact_sha256),
            "sources": dict(self.source_hashes),
        }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())

    def validate_integrity(self) -> None:
        if self.digest != self._computed_digest():
            raise ValueError("diagnostic static owner digest mismatch")

    @classmethod
    def from_sealed_archives(cls) -> "DiagnosticC2StaticOwner":
        for relative, expected in _SOURCE_HASHES.items():
            path = (_ROOT / "src/biospur_fusion/c2_uwb_root_world" / relative
                    if "/" not in relative and relative != "direct_body_shadow_ab.py"
                    else _ROOT / "src/biospur_fusion" / ("c2_uwb_calibration/direct_body_shadow_ab.py" if relative == "direct_body_shadow_ab.py" else relative))
            if _sha(path.read_bytes()) != expected:
                raise ValueError(f"diagnostic static source changed: {relative}")
        layout, layout_stat = _stable_json(_LAYOUT, _EXPECTED[_LAYOUT])
        clock_doc, clock_stat = _stable_json(_CLOCK, _EXPECTED[_CLOCK])
        validated = _clock_models_document(
            clock_doc, source_sha256=_SOURCE_HASHES["beacon_clock.py"],
        )
        models = clock_doc.get("models")
        if type(models) is not dict or set(models) != set(NODES) or set(validated) != set(NODES):
            raise ValueError("clock table node inventory mismatch")
        clocks = {}
        for node in NODES:
            row = models[node]; checked = validated[node]
            if (row.get("node_id") != node or int(row["boot_epoch"]) != checked.boot_epoch
                    or float(row["a_ns_per_us"]) != checked.a_ns_per_us
                    or float(row["b_ns"]) != checked.b_ns):
                raise ValueError("clock validator/parsed owner mismatch")
            clocks[node] = DirectNodeLinkClock(
                node, checked.a_ns_per_us, checked.b_ns, checked.boot_epoch,
                int(row["first_timer_us"]), int(row["last_timer_us"]),
            )
        if layout.get("anchor_ids") != list(range(8)):
            raise ValueError("layout anchor inventory mismatch")
        rows = layout.get("anchors")
        if type(rows) is not list or [row.get("id") for row in rows] != list(range(8)):
            raise ValueError("layout anchor row identity mismatch")
        anchors = np.asarray([[row[k] for k in ("x_mm", "y_mm", "z_mm")] for row in rows], float) / 1000.0
        delays = np.asarray([row["d_anchor_mm"] for row in rows], float) / 1000.0
        return cls(_CONSTRUCTOR, anchors_m=anchors, anchor_delay_m=delays,
                   tag_delay_m=float(layout["tag_delay_mm"]) / 1000.0, clocks=clocks,
                   artifact_sha256={str(_LAYOUT): _EXPECTED[_LAYOUT], str(_CLOCK): _EXPECTED[_CLOCK]},
                   artifact_stat={str(_LAYOUT): layout_stat, str(_CLOCK): clock_stat})

    @property
    def product_ready(self) -> bool: return False

    @property
    def scientific_pass(self) -> bool: return False

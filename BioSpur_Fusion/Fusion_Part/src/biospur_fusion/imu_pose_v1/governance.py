from __future__ import annotations

import hashlib
from pathlib import Path

from biospur_fusion.calibration_v2.phase2r.governance import AccessClass, DataAccessBroker, DataAccessViolation, Rule


class Phase3RDatasetBroker(DataAccessBroker):
    """Exact-path Phase3-R broker; prior holdouts are contaminated diagnostics."""

    @classmethod
    def bootstrap(cls, dataset_root: Path, ledger_path: Path, worker: str) -> "Phase3RDatasetBroker":
        broker = super().bootstrap(dataset_root, ledger_path, worker)
        broker.stage = "PHASE3R_OPERATOR_MAPPED_IMU_ONLY"
        return broker

    def register_literal_selection(self, selection: dict, source_plan_payload: bytes) -> tuple[list[dict], list[dict]]:
        if hashlib.sha256(source_plan_payload).hexdigest() != selection["source_plan_sha256"]:
            raise DataAccessViolation("capture plan identity mismatch")
        development = []
        for row in selection["development_windows"]:
            manifest = Path(row["manifest"]); raw = Path(row["raw"])
            if manifest != manifest.resolve(strict=True) or raw != raw.resolve(strict=True):
                raise DataAccessViolation("non-exact development path")
            self.add_exact_rules((
                Rule(manifest, AccessClass.PRETRUTH_METADATA, ("promoted_manifest",), False, row["manifest_sha256"]),
                Rule(raw, AccessClass.PRETRUTH_MEASUREMENT, ("common_header", "imu_projection_only", "TIMER2"), True, row["raw_opaque_sha256"]),
            ))
            development.append(dict(row))
        if len(development) != 19 or selection.get("invalid_redo_deleted_numeric_count") != 0:
            raise DataAccessViolation("exact 19-window promoted selection required")
        retrospective = []
        expected = {"H00_walk", "H01_boxing", "H02_golf"}
        for source in selection["holdouts"]:
            row = dict(source)
            if row["action_id"] not in expected:
                raise DataAccessViolation("unexpected retrospective route")
            manifest = Path(row["manifest"]); raw = Path(row["raw"])
            if manifest != manifest.resolve(strict=True) or raw != raw.resolve(strict=True):
                raise DataAccessViolation("non-exact retrospective path")
            self.rules[manifest] = Rule(manifest, AccessClass.PRETRUTH_METADATA,
                                        ("contaminated_retrospective_manifest",), False, row["manifest_sha256"])
            # Explicit Phase3-R reclassification; no claim of pristine holdout status.
            self.rules[raw] = Rule(raw, AccessClass.PRETRUTH_MEASUREMENT,
                                   ("common_header", "imu_projection_only", "TIMER2", "CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC"),
                                   True, row["raw_opaque_sha256"])
            row["classification"] = "CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC"
            row["independent_validation"] = False
            retrospective.append(row)
        if {x["action_id"] for x in retrospective} != expected:
            raise DataAccessViolation("exact H00/H01/H02 retrospective set required")
        return development, retrospective

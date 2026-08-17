from __future__ import annotations

import hashlib
import json
from pathlib import Path

from biospur_fusion.calibration_v2.phase2r.governance import AccessClass, DataAccessBroker, DataAccessViolation, Rule


class Phase3DatasetBroker(DataAccessBroker):
    """Phase 3 extension: metadata-safe holdout routing and one-shot IMU release."""

    def __init__(self, dataset_root: Path, ledger_path: Path, worker: str):
        super().__init__(dataset_root, ledger_path, worker)
        self.stage = "PHASE3_PREHOLDOUT"
        self.holdout_routes: dict[str, dict] = {}
        self._release_identity: str | None = None
        self._consumed: set[str] = set()

    @classmethod
    def bootstrap(cls, dataset_root: Path, ledger_path: Path, worker: str):
        broker = cls(dataset_root, ledger_path, worker)
        policy = broker.root / cls.POLICY_NAME
        if policy.is_symlink() or policy.resolve(strict=True) != policy:
            raise DataAccessViolation("base policy must be exact")
        payload = policy.read_bytes()
        broker.policy = json.loads(payload)
        if broker.policy.get("schema") != "biospur-phase2r-data-access-v1":
            raise DataAccessViolation("unexpected base policy schema")
        broker.rules[policy] = Rule(policy, AccessClass.POLICY, ("governance",), False)
        broker._record(requested=policy, resolved=policy, purpose="bootstrap_base_policy", allowed=True, rule=broker.rules[policy], payload=payload, numeric=0, arrays=0, factors=0)
        broker.counts["allowed_reads"] += 1; broker.counts["payload_bytes_read"] += len(payload)
        for relative in cls.BOOTSTRAP_METADATA:
            target = broker.root / relative
            broker.rules[target] = Rule(target, AccessClass.PRETRUTH_METADATA, ("metadata",), False)
        return broker

    def register_phase3_routes(self, plan: dict) -> tuple[list[dict], list[dict]]:
        development = self.register_promoted_phase2_windows(plan)
        holdouts = []
        expected = {"H00_walk", "H01_boxing", "H02_golf"}
        for action in plan.get("actions", []):
            if action.get("data_role") != "SEALED_PHASE3_REGRESSION":
                continue
            relative = Path(action["relative_dir"])
            if relative.is_absolute() or ".." in relative.parts:
                raise DataAccessViolation("invalid holdout route")
            manifest = (self.root / relative / "rep_01/manifest/CAPTURE_MANIFEST.json").resolve(strict=True)
            raw = (self.root / relative / "rep_01/raw/fusion_host_raw.cobs.bin").resolve(strict=True)
            self.add_exact_rules((Rule(manifest, AccessClass.PRETRUTH_METADATA, ("holdout_routing_metadata",), False),))
            row = {
                "action_id": action["action_id"], "relative_dir": relative.as_posix(), "manifest": str(manifest), "raw": str(raw),
                "preparation_s": action["pre_s"], "formal_s": action["action_duration_s"], "recovery_s": action["post_s"],
                "classification": "IN_SCOPE_GATE" if action["action_id"] == "H00_walk" else "STRESS_PROBE_ONLY",
                "numeric_status": "NUMERICALLY_UNOPENED_FOR_THIS_PHASE3_RELEASE",
            }
            self.holdout_routes[action["action_id"]] = row
            holdouts.append(row)
        if {x["action_id"] for x in holdouts} != expected:
            raise DataAccessViolation("exact H00/H01/H02 routes required")
        return development, holdouts

    def bind_holdout_manifest(self, action_id: str, manifest: dict, manifest_sha256: str) -> dict:
        row = self.holdout_routes[action_id]
        if manifest.get("status") != "ACCEPTED" or manifest.get("data_role") != "SEALED_PHASE3_REGRESSION":
            raise DataAccessViolation("holdout manifest not accepted/sealed")
        result = dict(row)
        result["manifest_sha256"] = manifest_sha256
        result["raw_opaque_sha256"] = manifest["continuous_range"]["slice_sha256"]
        result["promoted_attempt_id"] = manifest["attempt_id"]
        self.holdout_routes[action_id] = result
        return result

    def enable_one_shot_holdouts(self, envelope: Path) -> None:
        payload = Path(envelope).read_bytes()
        data = json.loads(payload)
        if not data.get("pre_holdout_release_eligible") or data.get("holdout_numeric_counters_before_release") != {"H00_walk":0,"H01_boxing":0,"H02_golf":0}:
            raise DataAccessViolation("release envelope not eligible")
        self._release_identity = hashlib.sha256(payload).hexdigest()
        self.stage = "PHASE3_ONE_SHOT_HOLDOUT"
        for action_id, row in self.holdout_routes.items():
            raw = Path(row["raw"])
            self.rules[raw] = Rule(raw, AccessClass.SEALED_HOLDOUT, ("mixed_container", "IMU_projection_only", "TIMER2"), True, row["raw_opaque_sha256"])

    def read_holdout_once(self, action_id: str) -> bytes:
        if self._release_identity is None or action_id in self._consumed:
            raise DataAccessViolation("holdout unavailable or already consumed")
        self._consumed.add(action_id)
        return self.read_bytes(Path(self.holdout_routes[action_id]["raw"]), purpose=f"one-shot selective IMU container {action_id}")

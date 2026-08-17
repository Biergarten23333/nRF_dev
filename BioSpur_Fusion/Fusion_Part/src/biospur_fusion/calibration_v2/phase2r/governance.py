"""Fail-closed dataset access for the Phase 2-R blind workflow.

The broker is intentionally the only production component that opens files in
the capture dataset.  It rejects a request before ``open`` when a path is not an
exact realpath allowlist member or belongs to a sealed class.  Every decision is
recorded in a repository-external JSONL ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


class DataAccessViolation(RuntimeError):
    """Raised before opening a path that is outside the active contract."""


class AccessClass(StrEnum):
    POLICY = "POLICY_BOOTSTRAP"
    PRETRUTH_METADATA = "PRETRUTH_ALLOWED_METADATA"
    PRETRUTH_MEASUREMENT = "PRETRUTH_ALLOWED_MEASUREMENT"
    PRETRUTH_NON_ROLE_PRIOR = "PRETRUTH_ALLOWED_NON_ROLE_OPERATOR_PRIOR"
    SEALED_MAPPING = "SEALED_MAPPING_TRUTH"
    SEALED_HOLDOUT = "SEALED_PHASE3_HOLDOUT_NUMERIC"


@dataclass(frozen=True)
class Rule:
    realpath: Path
    access_class: AccessClass
    decoded_field_classes: tuple[str, ...]
    numeric_allowed: bool
    expected_sha256: str | None = None
    allow_external: bool = False


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class DataAccessBroker:
    """Exact-realpath access broker with pre-open denial and auditable counts."""

    POLICY_NAME = "DATA_ACCESS_POLICY.json"
    FORBIDDEN_MAPPING_PATHS = (
        "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json",
        "identity/CONFIRMED_MAPPING_INPUT.json",
        "identity/DONNING_MANIFEST.json",
        "identity/OPERATOR_DONNING_AND_MAPPING_INPUT.json",
        "identity/NODE_TO_BODY_GROUND_TRUTH_COMMITMENT.json",
    )
    BOOTSTRAP_METADATA = (
        "CAPTURE_PLAN_FINAL.json",
        "subject/ACTUAL_ACTION_EXECUTION_TABLE.md",
        "subject/OPERATOR_CAPTURE_NOTICE_POST_SESSION.json",
        "subject/HUMAN_EXECUTION_POLICY.json",
        "qc/ACTION_COMPLETENESS.json",
        "system/readiness/SYSTEM_READINESS_REPORT.json",
        "system/fusion_continuous/READINESS_LATEST.json",
        "system/NORMAL_CAPTURE_END.json",
        "checksums/SHA256SUMS.txt",
        "NEXT_PHASE_HANDOFF.md",
        "DATA_ACCESS_POLICY_ADDENDUM_003.json",
    )

    def __init__(self, dataset_root: Path, ledger_path: Path, worker: str):
        root = Path(dataset_root)
        if root.is_symlink():
            raise DataAccessViolation("dataset root may not be a symlink")
        self.root = root.resolve(strict=True)
        self.ledger = Path(ledger_path).resolve()
        self.worker = worker
        self.stage = "PHASE2R_PRETRUTH"
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        if self.root == self.ledger or self.root in self.ledger.parents:
            raise DataAccessViolation("ledger must be repository-external to dataset")
        self.rules: dict[Path, Rule] = {}
        self.policy: dict[str, Any] | None = None
        self.counts = {
            "allowed_reads": 0,
            "denied_before_open": 0,
            "payload_bytes_read": 0,
            "numeric_measurement_decode_count": 0,
            "array_materialization_count": 0,
            "estimator_factor_consumption_count": 0,
            "mapping_revealing_bytes_read_pretruth": 0,
            "holdout_numeric_bytes_read": 0,
        }

    @classmethod
    def bootstrap(cls, dataset_root: Path, ledger_path: Path, worker: str) -> "DataAccessBroker":
        """Open exactly the base policy, then install the initial metadata rules."""
        broker = cls(dataset_root, ledger_path, worker)
        policy = broker.root / cls.POLICY_NAME
        if policy.is_symlink() or policy.resolve(strict=True) != policy:
            raise DataAccessViolation("base policy must be an exact non-symlink realpath")
        payload = policy.read_bytes()
        broker.policy = json.loads(payload)
        if broker.policy.get("schema") != "biospur-phase2r-data-access-v1":
            raise DataAccessViolation("unexpected base policy schema")
        broker.rules[policy] = Rule(policy, AccessClass.POLICY, ("governance",), False)
        broker._record(
            requested=policy,
            resolved=policy,
            purpose="bootstrap_base_policy",
            allowed=True,
            rule=broker.rules[policy],
            payload=payload,
            numeric=0,
            arrays=0,
            factors=0,
        )
        broker.counts["allowed_reads"] += 1
        broker.counts["payload_bytes_read"] += len(payload)
        for relative in cls.BOOTSTRAP_METADATA:
            target = broker.root / relative
            broker.rules[target] = Rule(target, AccessClass.PRETRUTH_METADATA, ("metadata",), False)
        return broker

    def _resolve_request(self, path: Path) -> tuple[Path, Path | None]:
        requested = Path(path)
        if not requested.is_absolute():
            requested = self.root / requested
        # lexical normalization is not authorization; exact spelling is required.
        try:
            resolved = requested.resolve(strict=True)
        except FileNotFoundError:
            return requested, None
        return requested, resolved

    def _record(
        self,
        *,
        requested: Path,
        resolved: Path | None,
        purpose: str,
        allowed: bool,
        rule: Rule | None,
        payload: bytes | None,
        numeric: int,
        arrays: int,
        factors: int,
        policy_recorded_opaque_sha256: str | None = None,
        reason: str | None = None,
    ) -> None:
        row = {
            "utc": _utc(),
            "stage": self.stage,
            "requested_path": str(requested),
            "resolved_realpath": str(resolved) if resolved else None,
            "purpose": purpose,
            "allowed": allowed,
            "reason": reason,
            "access_class": rule.access_class if rule else None,
            "decoded_field_classes": list(rule.decoded_field_classes) if rule else [],
            "observed_sha256": _sha_bytes(payload) if payload is not None else None,
            "policy_recorded_opaque_sha256": policy_recorded_opaque_sha256,
            "byte_count": len(payload) if payload is not None else 0,
            "payload_bytes_read": len(payload) if payload is not None else 0,
            "numeric_measurement_decode_count": numeric,
            "array_materialization_count": arrays,
            "estimator_factor_consumption_count": factors,
            "worker_process_identity": self.worker,
        }
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _deny(self, requested: Path, resolved: Path | None, purpose: str, reason: str) -> None:
        self.counts["denied_before_open"] += 1
        if resolved and any(resolved == self.root / p for p in self.FORBIDDEN_MAPPING_PATHS):
            access_class = AccessClass.SEALED_MAPPING
        elif resolved and "holdout" in resolved.relative_to(self.root).parts:
            access_class = AccessClass.SEALED_HOLDOUT
        else:
            access_class = None
        rule = Rule(resolved, access_class, (), False) if resolved and access_class else None
        self._record(
            requested=requested,
            resolved=resolved,
            purpose=purpose,
            allowed=False,
            rule=rule,
            payload=None,
            numeric=0,
            arrays=0,
            factors=0,
            reason=reason,
        )
        raise DataAccessViolation(reason)

    def add_exact_rules(self, rules: Iterable[Rule]) -> None:
        for rule in rules:
            target = rule.realpath
            if not target.is_absolute() or target != target.resolve(strict=True) or target.is_symlink():
                raise DataAccessViolation("rule target must be an exact existing non-symlink realpath")
            if self.root not in target.parents and not rule.allow_external:
                raise DataAccessViolation("rule escapes dataset root")
            if self.root in target.parents and target in (self.root / p for p in self.FORBIDDEN_MAPPING_PATHS):
                raise DataAccessViolation("mapping-revealing rule rejected")
            relative = target.relative_to(self.root) if self.root in target.parents else None
            if relative is not None and "holdout" in relative.parts and rule.numeric_allowed:
                raise DataAccessViolation("holdout numeric rule rejected")
            self.rules[target] = rule

    def load_policy_addendum(self, path: Path) -> dict[str, Any]:
        """Load a hash-pinned non-role prior addendum without relaxing base policy."""
        addendum = self.read_json(path, purpose="load exact non-role prior addendum")
        if addendum.get("schema") != "biospur-phase2r-data-access-policy-addendum-v1":
            raise DataAccessViolation("unexpected addendum schema")
        if not addendum.get("does_not_modify_base_policy") or not addendum.get("does_not_relax_existing_forbidden_paths"):
            raise DataAccessViolation("addendum attempts to relax base policy")
        base_payload = self.read_bytes(self.root / self.POLICY_NAME, purpose="verify addendum base-policy binding")
        if _sha_bytes(base_payload) != addendum.get("base_policy_sha256"):
            raise DataAccessViolation("addendum base-policy hash mismatch")
        rules = []
        for entry in addendum.get("new_exact_allowlist", []):
            if entry.get("access_class") != AccessClass.PRETRUTH_NON_ROLE_PRIOR or entry.get("contains_body_role") is not False:
                raise DataAccessViolation("addendum contains a non-permitted class")
            target = Path(entry["realpath"])
            rules.append(Rule(
                target,
                AccessClass.PRETRUTH_NON_ROLE_PRIOR,
                tuple(entry.get("decoded_field_classes", ())),
                False,
                expected_sha256=entry["sha256"],
                allow_external=self.root not in target.parents,
            ))
        if len(rules) != 2:
            raise DataAccessViolation("operator prior addendum must bind exactly two projections")
        self.add_exact_rules(rules)
        return addendum

    def enable_sealed_mapping_reveal(self, target: Path, expected_sha256: str, *, freeze_validated: bool) -> None:
        """Enable one exact mapping file only after an immutable candidate freeze."""
        if not freeze_validated:
            raise DataAccessViolation("sealed mapping reveal requires validated candidate freeze")
        resolved = Path(target).resolve(strict=True)
        canonical = self.root / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json"
        if resolved != canonical or resolved.is_symlink():
            raise DataAccessViolation("non-canonical sealed mapping target")
        if self.policy is None or self.policy.get("ground_truth_commitment_sha256") != expected_sha256:
            raise DataAccessViolation("mapping commitment does not match base policy")
        self.stage = "PHASE2R_TRUTH_REVEAL"
        self.rules[resolved] = Rule(resolved, AccessClass.SEALED_MAPPING, ("node_to_body_truth",), False, expected_sha256)

    def read_bytes(
        self,
        path: Path,
        *,
        purpose: str,
        numeric_measurements: int = 0,
        arrays: int = 0,
        factors: int = 0,
    ) -> bytes:
        requested, resolved = self._resolve_request(path)
        if resolved is None:
            self._deny(requested, None, purpose, "DENIED_MISSING_BEFORE_OPEN")
        if requested != resolved or requested.is_symlink():
            self._deny(requested, resolved, purpose, "DENIED_NOT_EXACT_REALPATH")
        rule = self.rules.get(resolved)
        if rule is None:
            self._deny(requested, resolved, purpose, "DENIED_NOT_LITERAL_ALLOWLIST")
        if self.root not in resolved.parents and not rule.allow_external:
            self._deny(requested, resolved, purpose, "DENIED_REALPATH_ESCAPE")
        if (numeric_measurements or arrays or factors) and not rule.numeric_allowed:
            self._deny(requested, resolved, purpose, "DENIED_NUMERIC_CLASS")
        payload = resolved.read_bytes()
        if rule.expected_sha256 is not None and _sha_bytes(payload) != rule.expected_sha256:
            self._record(
                requested=requested,
                resolved=resolved,
                purpose=purpose,
                allowed=True,
                rule=rule,
                payload=payload,
                numeric=numeric_measurements,
                arrays=arrays,
                factors=factors,
                reason="ALLOWED_READ_IDENTITY_MISMATCH",
            )
            raise DataAccessViolation("allowed path content hash mismatch")
        self._record(
            requested=requested,
            resolved=resolved,
            purpose=purpose,
            allowed=True,
            rule=rule,
            payload=payload,
            numeric=numeric_measurements,
            arrays=arrays,
            factors=factors,
        )
        self.counts["allowed_reads"] += 1
        self.counts["payload_bytes_read"] += len(payload)
        self.counts["numeric_measurement_decode_count"] += numeric_measurements
        self.counts["array_materialization_count"] += arrays
        self.counts["estimator_factor_consumption_count"] += factors
        return payload

    def read_json(self, path: Path, *, purpose: str) -> Any:
        return json.loads(self.read_bytes(path, purpose=purpose))

    def hash_allowed(self, path: Path, *, purpose: str) -> dict[str, Any]:
        payload = self.read_bytes(path, purpose=purpose)
        return {"sha256": _sha_bytes(payload), "bytes": len(payload), "realpath": str(Path(path).resolve())}

    def record_consumption(
        self,
        path: Path,
        *,
        purpose: str,
        numeric_measurements: int,
        arrays: int,
        factors: int,
    ) -> None:
        """Record post-decode consumption without reopening the authorized bytes."""
        requested, resolved = self._resolve_request(path)
        rule = self.rules.get(resolved) if resolved is not None else None
        if resolved is None or requested != resolved or rule is None or not rule.numeric_allowed:
            self._deny(requested, resolved, purpose, "DENIED_CONSUMPTION_WITHOUT_NUMERIC_RULE")
        self._record(
            requested=requested,
            resolved=resolved,
            purpose=purpose,
            allowed=True,
            rule=rule,
            payload=None,
            numeric=numeric_measurements,
            arrays=arrays,
            factors=factors,
            reason="AUTHORIZED_POST_DECODE_CONSUMPTION_ACCOUNTING",
        )
        self.counts["numeric_measurement_decode_count"] += numeric_measurements
        self.counts["array_materialization_count"] += arrays
        self.counts["estimator_factor_consumption_count"] += factors

    def register_promoted_phase2_windows(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for action in plan.get("actions", []):
            role = action.get("data_role")
            relative_dir = Path(action["relative_dir"])
            if role != "PHASE2_CALIBRATION":
                continue
            if ".." in relative_dir.parts or relative_dir.is_absolute():
                raise DataAccessViolation("invalid action relative path")
            manifest = (self.root / relative_dir / "rep_01/manifest/CAPTURE_MANIFEST.json").resolve(strict=True)
            raw = (self.root / relative_dir / "rep_01/raw/fusion_host_raw.cobs.bin").resolve(strict=True)
            self.add_exact_rules((
                Rule(manifest, AccessClass.PRETRUTH_METADATA, ("action_manifest",), False),
                Rule(raw, AccessClass.PRETRUTH_MEASUREMENT, ("imu", "uwb", "measurement_time"), True),
            ))
            rows.append({"action_id": action["action_id"], "relative_dir": relative_dir.as_posix(), "manifest": str(manifest), "raw": str(raw)})
        if len(rows) != 19:
            raise DataAccessViolation(f"expected exactly 19 Phase 2 windows, got {len(rows)}")
        return rows

    def summary(self) -> dict[str, Any]:
        return {"schema": "biospur-phase2r-access-summary-v1", "worker": self.worker, **self.counts}

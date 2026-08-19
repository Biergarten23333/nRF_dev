#!/usr/bin/env python3
"""Emit deterministic synthetic qualification payload without report claims."""

from __future__ import annotations

import hashlib
import json

from biospur_fusion.heading_anchor_audit_v2.qualification import (
    run_fault_injections_and_negative_controls,
    run_gauge_equivariance,
    run_serialization_and_validation,
)


def main() -> int:
    payload = {
        "schema": "biospur.phase3r26c_r1.synthetic_qualification_snapshot.v1",
        "fault_injections": run_fault_injections_and_negative_controls(),
        "gauge_equivariance": run_gauge_equivariance(),
        "serialization": run_serialization_and_validation(),
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    print(json.dumps({
        "payload": payload,
        "canonical_sha256": hashlib.sha256(encoded).hexdigest(),
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

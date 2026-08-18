from __future__ import annotations

import re


SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def build_publication_envelope(*, run_id: str, implementation_sha: str,
                               attestation_sha: str, remote_sha: str,
                               scientific_closure_sha256: str,
                               protected_porcelain_sha256: str,
                               final_verdict: str) -> dict:
    payload = {
        "schema": "biospur-phase3r2-publication-envelope-v1", "run_id": run_id,
        "implementation_sha": implementation_sha, "attestation_sha": attestation_sha,
        "remote_sha": remote_sha, "scientific_closure_sha256": scientific_closure_sha256,
        "protected_porcelain_sha256": protected_porcelain_sha256,
        "final_verdict": final_verdict,
    }
    validate_publication_envelope(payload)
    return payload


def validate_publication_envelope(payload: dict) -> None:
    required = {"schema", "run_id", "implementation_sha", "attestation_sha", "remote_sha",
                "scientific_closure_sha256", "protected_porcelain_sha256", "final_verdict"}
    if set(payload) != required:
        raise ValueError("publication envelope schema mismatch")
    for key in ("implementation_sha", "attestation_sha", "remote_sha"):
        if not SHA1.fullmatch(payload[key]): raise ValueError(f"invalid {key}")
    for key in ("scientific_closure_sha256", "protected_porcelain_sha256"):
        if not SHA256.fullmatch(payload[key]): raise ValueError(f"invalid {key}")
    if "PENDING" in "|".join(str(value) for value in payload.values()):
        raise ValueError("external publication envelope cannot contain PENDING")

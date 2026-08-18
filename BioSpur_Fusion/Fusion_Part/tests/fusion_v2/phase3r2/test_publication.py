from __future__ import annotations

import pytest

from BioSpur_Fusion.Fusion_Part.tools.fusion_v2.phase3r2.publication import (
    build_publication_envelope, validate_publication_envelope,
)


def test_publication_generator_and_validator_fixture():
    payload = build_publication_envelope(
        run_id="fixture", implementation_sha="1"*40, attestation_sha="2"*40,
        remote_sha="2"*40, scientific_closure_sha256="3"*64,
        protected_porcelain_sha256="4"*64, final_verdict="LIMITED_FIXTURE",
    )
    validate_publication_envelope(payload)
    with pytest.raises(ValueError): validate_publication_envelope({**payload, "remote_sha": "PENDING"})
    with pytest.raises(ValueError): validate_publication_envelope({**payload, "unexpected": 1})

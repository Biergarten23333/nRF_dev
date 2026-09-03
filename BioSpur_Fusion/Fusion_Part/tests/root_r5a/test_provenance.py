from __future__ import annotations

from biospur_fusion.root_r5a.constants import ROOT_R4_MANIFEST_PAYLOAD_SHA256, TIME_OFFSET_BOUND_S
from biospur_fusion.root_r5a.provenance import canonical_json_payload_sha256, resolve_root_r4


def test_exact_root_r4_manifest_resolves():
    root, manifest = resolve_root_r4()
    assert root.name == "biospur_c1_uwb_imu_root_r4_20260824T100721Z"
    assert manifest["manifest_payload_sha256"] == ROOT_R4_MANIFEST_PAYLOAD_SHA256
    assert canonical_json_payload_sha256(manifest) == ROOT_R4_MANIFEST_PAYLOAD_SHA256


def test_time_bound_was_predeclared_and_small():
    assert TIME_OFFSET_BOUND_S == 0.020

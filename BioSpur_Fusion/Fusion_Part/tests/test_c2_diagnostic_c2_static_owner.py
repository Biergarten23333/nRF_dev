from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.diagnostic_c2_static_owner import (
    DiagnosticC2StaticOwner, DiagnosticUncalibratedRangeModel, _SOURCE_HASHES,
    _stable_json,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import NODES, _clock_models_document
from biospur_fusion.root_r3.estimator import RootFilterConfig


def test_sealed_factory_constructs_exact_nonpromotable_static_owner():
    owner = DiagnosticC2StaticOwner.from_sealed_archives()
    assert set(owner.clocks) == set(NODES)
    assert owner.anchors_m.shape == (8, 3)
    assert np.linalg.matrix_rank(owner.anchors_m - owner.anchors_m.mean(0)) == 3
    assert owner.root_config == RootFilterConfig()
    assert owner.range_model == DiagnosticUncalibratedRangeModel()
    assert owner.product_ready is owner.scientific_pass is False
    assert owner.qualification == "DIAGNOSTIC_ROOT_ONLY_NON_PROMOTABLE"
    assert _SOURCE_HASHES["root_r3/estimator.py"] == (
        "516e41b9ab62df00dd3486cf4aa944e5055e800a84c1b0159458f824cf29fa23"
    )
    estimator = Path("src/biospur_fusion/root_r3/estimator.py").read_bytes()
    assert hashlib.sha256(estimator).hexdigest() == _SOURCE_HASHES["root_r3/estimator.py"]


def test_static_owner_arrays_and_maps_are_immutable_and_nonaliased():
    first = DiagnosticC2StaticOwner.from_sealed_archives()
    second = DiagnosticC2StaticOwner.from_sealed_archives()
    assert not np.shares_memory(first.anchors_m, second.anchors_m)
    with pytest.raises(ValueError): first.anchors_m[0, 0] = 1.0
    with pytest.raises(TypeError): first.clocks["OTHER"] = next(iter(first.clocks.values()))
    with pytest.raises(FrozenInstanceError): first.tag_delay_m = 10.0
    with pytest.raises(FrozenInstanceError): first.digest = "0" * 64
    first.validate_integrity()
    assert first.digest == second.digest


def test_constructor_is_private_and_digest_binds_selected_values():
    with pytest.raises(RuntimeError, match="private"):
        DiagnosticC2StaticOwner(object(), anchors_m=np.zeros((8, 3)),
            anchor_delay_m=np.zeros(8), tag_delay_m=0.0, clocks={},
            artifact_sha256={}, artifact_stat={})
    owner = DiagnosticC2StaticOwner.from_sealed_archives()
    assert len(owner.digest) == 64
    assert asdict(owner.root_config)["fixed_lag_s"] == 0.10


def test_sealed_factory_rejects_foreign_estimator_source_hash(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.diagnostic_c2_static_owner as module

    canonical_sha = module._sha

    def reject_current_estimator(payload: bytes) -> str:
        digest = canonical_sha(payload)
        if digest == _SOURCE_HASHES["root_r3/estimator.py"]:
            return "0" * 64
        return digest

    monkeypatch.setattr(module, "_sha", reject_current_estimator)
    with pytest.raises(ValueError, match="root_r3/estimator.py"):
        DiagnosticC2StaticOwner.from_sealed_archives()


def test_sealed_documents_expose_exact_node_and_anchor_inventory():
    owner = DiagnosticC2StaticOwner.from_sealed_archives()
    assert tuple(sorted(owner.clocks)) == tuple(sorted(NODES))
    assert np.isfinite(owner.anchor_delay_m).all()
    assert np.isfinite(owner.tag_delay_m)


def test_stable_reader_rejects_missing_symlink_and_hash(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError): _stable_json(missing, "0" * 64)
    target = tmp_path / "target.json"; target.write_text("{}")
    link = tmp_path / "link.json"; link.symlink_to(target)
    with pytest.raises(OSError): _stable_json(link, "0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        _stable_json(target, "0" * 64)


def test_clock_semantics_reject_foreign_source_node_and_support(tmp_path):
    canonical = Path("logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json")
    document = json.loads(canonical.read_text())
    source_sha = document["source_sha256"]
    with pytest.raises(ValueError, match="source hash"):
        _clock_models_document(document, source_sha256="0" * 64)
    foreign = json.loads(json.dumps(document))
    foreign["models"]["FOREIGN"] = foreign["models"].pop(next(iter(foreign["models"])))
    models = _clock_models_document(foreign, source_sha256=source_sha)
    assert set(models) != set(NODES)
    owner = DiagnosticC2StaticOwner.from_sealed_archives()
    row = next(iter(owner.clocks.values()))
    with pytest.raises(ValueError, match="outside sealed"):
        row.link_time_ns(event_boot_epoch=row.boot_epoch,
                         strobe_us=row.first_timer_us - 1, t_round_us=0.0)

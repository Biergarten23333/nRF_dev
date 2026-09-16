from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from biospur_fusion.v0.c2_progressive.action00_orientation_runtime import (
    AUTHORITY_RELATIVE,
    Action00OrientationRuntime,
)
from biospur_fusion.v0.c2_progressive.architecture_guard import ClassAGuardViolation
from biospur_fusion.v0.c2_progressive.range_reader import DecodedAction


WORKSPACE = Path(__file__).resolve().parents[1]
SETTINGS = Path(
    "logs/c2_basis_progressive_20260829T102836Z/"
    "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
)
INITIAL = Path(
    "logs/c2_basis_progressive_20260829T102836Z/"
    "P1_FRONTEND/P1_INITIAL_STILL_STOCHASTIC_STATE.json"
)
SEAL = Path(
    "logs/c2_basis_progressive_20260829T102836Z/"
    "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
)
ACTIVATION = Path(
    "logs/c2_basis_progressive_20260829T102836Z/"
    "C2_REAL_DIAGNOSTIC_ACTIVATION_009.json"
)
OLD_SOURCE_DELTA = Path(
    "logs/c2_basis_progressive_20260829T102836Z/"
    "C2_REAL_DIAGNOSTIC_ACTIVATION_009_AUTHORIZED_SOURCE_DELTA_001_RUNTIME_BUGFIX_002.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs() -> tuple[dict, dict]:
    settings = json.loads((WORKSPACE / SETTINGS).read_text())["effective_settings"]
    initial = json.loads((WORKSPACE / INITIAL).read_text())
    return settings, initial


def _copy_authority_root(tmp_path: Path, mutate=None) -> tuple[Path, str]:
    authority = json.loads((WORKSPACE / AUTHORITY_RELATIVE).read_text())
    paths = {row["path"] for row in authority["source_files"]}
    paths.update(row["path"] for row in authority["parent_chain"])
    paths.update(row["path"] for row in authority["focused_gates"])
    paths.add(authority["nonhinge_source_delta"]["path"])
    paths.add(authority["initial_stochastic_state"]["path"])
    for relative in paths:
        source = WORKSPACE / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if relative.startswith("logs/"):
            target.chmod(0o444)
    initial_path = tmp_path / authority["initial_stochastic_state"]["path"]
    initial_stat = initial_path.stat()
    authority["initial_stochastic_state"]["stat_identity"] = [
        initial_stat.st_dev,
        initial_stat.st_ino,
        initial_stat.st_size,
        initial_stat.st_mtime_ns,
    ]
    if mutate is not None:
        mutate(authority, tmp_path)
    target = tmp_path / AUTHORITY_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(authority, indent=2) + "\n")
    target.chmod(0o444)
    return tmp_path, _sha(target)


def test_valid_canonical_construction_is_no_raw_and_role_bound() -> None:
    settings, initial = _inputs()
    authority_sha = _sha(WORKSPACE / AUTHORITY_RELATIVE)
    runtime = Action00OrientationRuntime(
        root=WORKSPACE,
        expected_authority_sha256=authority_sha,
        settings=settings,
        initial_stochastic_state=initial,
    )
    assert runtime.authority_digest == authority_sha
    assert runtime._consumed is False


def test_authority_sha_and_foreign_role_are_rejected(tmp_path: Path) -> None:
    settings, initial = _inputs()
    with pytest.raises(RuntimeError, match="not approved"):
        Action00OrientationRuntime(
            root=WORKSPACE,
            expected_authority_sha256="0" * 64,
            settings=settings,
            initial_stochastic_state=initial,
        )

    root, digest = _copy_authority_root(
        tmp_path,
        lambda authority, _: authority.__setitem__("role", "FOREIGN"),
    )
    with pytest.raises(RuntimeError, match="foreign"):
        Action00OrientationRuntime(
            root=root,
            expected_authority_sha256=digest,
            settings=settings,
            initial_stochastic_state=initial,
        )


def test_missing_source_and_self_declared_inherited_forgery_are_rejected(
    tmp_path: Path,
) -> None:
    settings, initial = _inputs()

    def remove_source(authority, _):
        authority["source_files"] = authority["source_files"][1:]

    root, digest = _copy_authority_root(tmp_path / "missing", remove_source)
    with pytest.raises(RuntimeError, match="path set"):
        Action00OrientationRuntime(
            root=root,
            expected_authority_sha256=digest,
            settings=settings,
            initial_stochastic_state=initial,
        )

    def forge_inherited(authority, root):
        relative = "src/biospur_fusion/v0/c2_progressive/calibration_posterior.py"
        path = root / relative
        path.write_bytes(path.read_bytes() + b"\n# forged\n")
        for row in authority["source_files"]:
            if row["path"] == relative:
                row["sha256"] = _sha(path)

    root, digest = _copy_authority_root(tmp_path / "forged", forge_inherited)
    with pytest.raises(RuntimeError, match="authorized nonhinge chain"):
        Action00OrientationRuntime(
            root=root,
            expected_authority_sha256=digest,
            settings=settings,
            initial_stochastic_state=initial,
        )


def test_bound_source_content_parent_and_settings_are_rejected_on_change(
    tmp_path: Path,
) -> None:
    settings, initial = _inputs()

    def alter_stage_source(_authority, root):
        path = root / "src/biospur_fusion/v0/c2_progressive/orientation.py"
        path.write_bytes(path.read_bytes() + b"\n# altered\n")

    root, digest = _copy_authority_root(tmp_path / "source", alter_stage_source)
    with pytest.raises(RuntimeError, match="source closure changed"):
        Action00OrientationRuntime(
            root=root,
            expected_authority_sha256=digest,
            settings=settings,
            initial_stochastic_state=initial,
        )

    changed_settings = json.loads(json.dumps(settings))
    changed_settings["orientation"]["sample_period_s"] = 0.01
    with pytest.raises(RuntimeError, match="settings differ"):
        Action00OrientationRuntime(
            root=WORKSPACE,
            expected_authority_sha256=_sha(WORKSPACE / AUTHORITY_RELATIVE),
            settings=changed_settings,
            initial_stochastic_state=initial,
        )


def test_foreign_action_is_rejected_before_orientation_processing() -> None:
    settings, initial = _inputs()
    runtime = Action00OrientationRuntime(
        root=WORKSPACE,
        expected_authority_sha256=_sha(WORKSPACE / AUTHORITY_RELATIVE),
        settings=settings,
        initial_stochastic_state=initial,
    )
    foreign = DecodedAction(
        action="01_foreign",
        chronological_index=1,
        interval=(0, 1),
        rows_by_node={},
        access_audit={},
        decode_audit={},
    )
    with pytest.raises(ValueError, match="foreign action"):
        runtime.process(foreign)
    assert runtime._consumed is False


def test_legacy_runtime_source_guard_remains_fail_closed() -> None:
    from biospur_fusion.v0.c2_progressive.pipeline_runtime import C2PipelineRuntime

    settings, initial = _inputs()
    with pytest.raises(ClassAGuardViolation, match="FORGED_PREFIT_SEAL"):
        C2PipelineRuntime(
            settings,
            initial,
            prefit_registry_seal_path=WORKSPACE / SEAL,
            real_fit_activation_path=WORKSPACE / ACTIVATION,
            real_diagnostic_source_delta_path=WORKSPACE / OLD_SOURCE_DELTA,
            execution_role="REAL_DIAGNOSTIC",
        )

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from biospur_fusion.root_r6a2b import real_shadow
from biospur_fusion.root_r6a2b.real_profile import (
    EXPECTED_AUTHORITY_COUNTS,
    EXPECTED_NODE_FAMILIES,
    PROFILE_SCHEMA,
    assert_profile_unchanged,
    profile_checksum,
    seal_profile,
    validate_real_profile,
)


FUSION = Path(__file__).resolve().parents[2]
CAPTURE = "v47_ten_node_body_calibration_20260814_093601"
BOOTS = {
    "BSF1120": 0, "BSF31CC": 1, "BSF3C79": 0, "BSF44AD": 0,
    "BSF6C53": 0, "BSF8BC4": 0, "BSFAA61": 0, "BSFB165": 0,
    "BSFC2CC": 1, "BSFEC35": 0,
}
ACTION = {"role": "DEVELOPMENT_ACTION", "sample_access_authorized_in_task": True}


def _valid_profile():
    slots = []
    ordinal = 0
    for authority, count in EXPECTED_AUTHORITY_COUNTS.items():
        for _ in range(count):
            slots.append({
                "slot_id": f"slot:{ordinal}", "authority_class": authority,
                "value": [float(ordinal)], "status": "QUALIFIED",
            })
            ordinal += 1
    return seal_profile({
        "schema": PROFILE_SCHEMA, "profile_kind": "REAL_SUBJECT_SESSION",
        "qualification_verdict": "PASS", "frozen": True,
        "binding": {
            "capture_id": CAPTURE, "session_id": CAPTURE,
            "boot_epochs": dict(BOOTS), "hardware_families": dict(EXPECTED_NODE_FAMILIES),
        },
        "slots": slots,
    })


def _validate(profile, action=ACTION):
    return validate_real_profile(
        profile, expected_capture_id=CAPTURE, expected_session_id=CAPTURE,
        expected_boot_epochs=BOOTS, action_authority=action,
    )


def test_valid_profile_contract_passes():
    result = _validate(_valid_profile())
    assert result.authorized
    assert result.failures == ()


@pytest.mark.parametrize("mutation,expected", [
    (lambda profile: profile["slots"][0].update(value=None), "PROFILE_REQUIRED_FIELDS_NULL_OR_UNQUALIFIED"),
    (lambda profile: profile["binding"]["hardware_families"].update(BSF31CC="COMMON_NINE_V0_20_PCB17"), "HARDWARE_FAMILY_BINDING_MISMATCH"),
    (lambda profile: profile["slots"][0].update(authority_class="ESTIMATE_WITH_PRIOR"), "PROFILE_AUTHORITY_ACCOUNTING_MISMATCH"),
    (lambda profile: profile.update(note="SYNTHETIC_TEST_ONLY"), "SYNTHETIC_OR_PLACEHOLDER_PROFILE"),
])
def test_profile_mutations_fail_closed(mutation, expected):
    profile = _valid_profile()
    mutation(profile)
    profile["profile_checksum_sha256"] = profile_checksum(profile)
    assert expected in _validate(profile).failures


def test_absent_and_bad_checksum_fail_closed():
    assert _validate(None).failures == ("PROFILE_ABSENT",)
    profile = _valid_profile()
    profile["slots"][0]["value"] = [999.0]
    assert "PROFILE_CHECKSUM_MISMATCH" in _validate(profile).failures


@pytest.mark.parametrize("role,failure", [
    ("CALIBRATION", "ACTION_NOT_DEVELOPMENT"),
    ("HELD_OUT", "ACTION_NOT_DEVELOPMENT"),
])
def test_calibration_and_heldout_cannot_be_actions(role, failure):
    result = _validate(_valid_profile(), {"role": role, "sample_access_authorized_in_task": False})
    assert failure in result.failures
    assert ("CALIBRATION_AS_ACTION_MISUSE" if role == "CALIBRATION" else "HELD_OUT_ACTION_MISUSE") in result.failures


def test_static_profile_freeze_detects_change():
    profile = _valid_profile()
    before = profile_checksum(profile)
    assert_profile_unchanged(profile, before)
    changed = copy.deepcopy(profile)
    changed["slots"][0]["value"] = [123.0]
    with pytest.raises(RuntimeError, match="STATIC_CALIBRATION_CHANGED_DURING_ACTION"):
        assert_profile_unchanged(changed, before)


def test_historical_real_runner_refuses_before_payload(monkeypatch, tmp_path):
    (tmp_path / "REAL_WINDOW_SELECTION.json").write_text(
        '{"schema":"biospur-root-r6a2b-real-window-selection-v1"}\n', encoding="utf-8"
    )
    opened = False

    def payload(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("payload must not open")

    monkeypatch.setattr(real_shadow, "_load_selected_payloads", payload)
    with pytest.raises(RuntimeError, match="PROFILE_ABSENT"):
        real_shadow.run_bounded_real_shadow(FUSION, tmp_path)
    assert not opened


def test_authority_ledger_is_metadata_only_and_isolates_roles(tmp_path):
    tool = FUSION / "tools/run_root_r6a2b_r1_calibration_first.py"
    spec = importlib.util.spec_from_file_location("root_r6a2b_r1_tool", tool)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    path = module.authority(tmp_path)
    ledger = module.load(path)
    assert ledger["numeric_sample_arrays_opened_by_this_authority_step"] is False
    assert ledger["counts"] == {
        "total": 78, "by_capture": {"C1": 18, "C2": 22, "C3": 38},
        "by_role": {"CALIBRATION": 14, "DEVELOPMENT_ACTION": 46, "HELD_OUT": 18},
        "sample_access_authorized_now": 12,
    }
    held = [row for row in ledger["rows"] if row["role"] == "HELD_OUT"]
    assert held and all(row["sample_access_authorized_in_task"] is False for row in held)
    previous = {row["previous_window"]: row for row in ledger["previous_r6a2b_windows"]}
    assert previous["PREVIOUS_WINDOW_A"]["actual_action_identifier"] == "initial_still"
    assert previous["PREVIOUS_WINDOW_A"]["actual_attempt_number"] == 1
    assert previous["PREVIOUS_WINDOW_B"]["actual_action_identifier"] == "arms"

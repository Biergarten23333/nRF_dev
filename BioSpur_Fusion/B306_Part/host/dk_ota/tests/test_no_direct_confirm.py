#!/usr/bin/env python3
"""Static safety gate: the generated updater must hand confirmation to v32."""

from pathlib import Path


def main() -> int:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "apply_idempotent_state_machine.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "ota_confirm_active",
        "OTA_ACTION:confirm_only",
        "confirm_only_readback_passed",
        'zcbor_tstr_put_lit(zse, "confirm")',
    )
    for token in forbidden:
        if token in source:
            raise AssertionError(f"direct-confirm token remains: {token}")
    required = (
        "OTA_ACTION:handoff_app_roundtrip_confirm",
        "active=1 confirmed=0 updater_confirm=0",
        "active_unconfirmed_app_confirmation_required",
    )
    for token in required:
        if token not in source:
            raise AssertionError(f"app-confirm handoff token absent: {token}")
    print("OTA_NO_DIRECT_CONFIRM_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

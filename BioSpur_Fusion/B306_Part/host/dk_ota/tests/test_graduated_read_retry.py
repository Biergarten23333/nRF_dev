#!/usr/bin/env python3
"""Static gate for retry bounds, timeout defense, and zero-retry writes."""

from pathlib import Path


def main() -> int:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "apply_graduated_read_retry.py"
    ).read_text(encoding="utf-8")
    required = (
        "OTA_READ_RETRIES 2U",
        "BT_LE_CONN_PARAM(6, 9, 0, 2000)",
        "OTA_SUPERVISION_TIMEOUT_UNITS 2000U",
        "param->timeout = OTA_SUPERVISION_TIMEOUT_UNITS",
        "ota_read_image_state_retrying",
        "ota_prime_link_retrying",
        "OTA_ERASE_MAX_ATTEMPTS 1U",
        "OTA_ERASE hard_stop",
        "k_sem_give(&ota_sem)",
    )
    for token in required:
        if token not in source:
            raise AssertionError(f"hardening token absent: {token}")
    print("OTA_GRADUATED_READ_RETRY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Sequential deployment-only relay8.2 OTA using the proven relay8.1 runner."""

from __future__ import annotations

import relay8_1_batch_ota as runner


# Keep the established transport, readiness, ordering, zero-write-retry, and
# composed-idle behavior byte-for-byte.  Only the source/destination image
# contracts change for this campaign.
runner.OLD_MARKER = "tag-fusion-link-relay8.1"
runner.OLD_HASH = "d400780640816617ecd8ac53a86ece4a157cf17f1d17e81613f1d965402f3da5"
runner.NEW_MARKER = "tag-fusion-link-relay8.2"
runner.NEW_HASH = "dacecc59e5b6fd8d1197e2f6ae57cb2673f1113f4f7902f81d64819190080d3f"


if __name__ == "__main__":
    raise SystemExit(runner.main())

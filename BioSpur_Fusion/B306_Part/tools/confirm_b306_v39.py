#!/usr/bin/env python3
"""Earn B306-v39 confirmation using the frozen two-command round trip."""
import sys
import confirm_b306_v32
confirm_b306_v32.B306_MARKER = "b306-imu-relay-v39"
confirm_b306_v32.MASTER_MARKER = "dk-fusion-imu-relay-v33"
if __name__ == "__main__":
    if "--ready-count" not in sys.argv and "--target-only" not in sys.argv:
        sys.argv.extend(("--ready-count", "10", "--ready-timeout-s", "60"))
    raise SystemExit(confirm_b306_v32.main())

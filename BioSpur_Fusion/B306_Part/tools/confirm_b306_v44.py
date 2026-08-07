#!/usr/bin/env python3
"""Earn B306-v44 confirmation using the frozen two-command round trip.

TARGET-ONLY by default (trap: a fleet-wide ready count once rolled back nine
good images because one board was quarantined).
"""
import sys
import confirm_b306_v32

confirm_b306_v32.B306_MARKER = "b306-imu-relay-v44"
confirm_b306_v32.MASTER_MARKER = "dk-fusion-imu-relay-v36"

if __name__ == "__main__":
    if "--ready-count" not in sys.argv and "--target-only" not in sys.argv:
        sys.argv.append("--target-only")
    raise SystemExit(confirm_b306_v32.main())

#!/usr/bin/env python3
"""Earn B306-v43 confirmation using the frozen two-command round trip.

v43 defaults to TARGET-ONLY preflight, unlike every earlier confirm tool.

Trap 15.3: a per-target operation must never wait on a fleet-wide condition.
The v41 rollout coupled confirmation to `ready=10`; one quarantined board held
the count at nine, and nine successfully uploaded images were never confirmed
and were all rolled back. Confirmation depends on the target's own identity,
bridge readiness and round trip -- nothing else. Pass --ready-count explicitly
if a fleet gate is genuinely wanted.
"""
import sys
import confirm_b306_v32

confirm_b306_v32.B306_MARKER = "b306-imu-relay-v43"
# S1 (spacing_default_20260807) advanced the DK to v36, which boots with the
# derived connection spacing already applied instead of the 7,500 us baseline.
# The B306 image is untouched -- only the marker the confirm tool expects to see
# on the Master moves, and it must move with the deployed DK or every
# confirmation fails on a marker mismatch.
confirm_b306_v32.MASTER_MARKER = "dk-fusion-imu-relay-v36"

if __name__ == "__main__":
    if "--ready-count" not in sys.argv and "--target-only" not in sys.argv:
        sys.argv.append("--target-only")
    raise SystemExit(confirm_b306_v32.main())

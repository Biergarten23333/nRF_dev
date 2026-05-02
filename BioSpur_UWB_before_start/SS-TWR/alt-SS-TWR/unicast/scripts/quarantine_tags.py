#!/usr/bin/env python3
"""
Quarantine one or more Tags so they stay powered-on but do not interfere with
anchor sweep / other workflows.

Implementation uses the existing master-control protocol:
- switches to RECV if needed
- device kind tag
- ota_target name <BSxxxx>
- cmd MODE AOTA
- cmd STREAM OFF (best-effort; older firmware may not support it)

This script is intentionally non-interactive and prints progress to stdout.
"""

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description="Quarantine multiple Tag BLE targets (MODE AOTA + STREAM OFF best-effort).")
    ap.add_argument("--port", required=True, help="52840 CDC serial port (/dev/serial/by-id/...)")
    ap.add_argument(
        "--tags",
        required=True,
        help="Comma/space separated list, e.g. 'BSF66F,BS2DCE,BSDC91'",
    )
    args = ap.parse_args()

    sys.path.insert(0, "scripts")
    from run_autopos_sweep_loop import open_port, quarantine_tag_for_sweep  # noqa: E402

    tags = [t for t in __import__("re").split(r"[,\s]+", args.tags.strip()) if t and t != "-"]
    if not tags:
        print("no tags specified", flush=True)
        return 0

    # Best-effort: keep going even if a single tag fails to quarantine.
    rc = 0
    ser = open_port(args.port, 20.0)
    ser.timeout = 0.3
    try:
        for tag in tags:
            print(f"=== QUIET {tag} ===", flush=True)
            ok = False
            # A small retry helps if the device is mid-discovery.
            for attempt in range(1, 4):
                ser, ok = quarantine_tag_for_sweep(ser, sys.stdout, args.port, tag, True, True, 1)
                if ok:
                    break
                print(f"QUIET_RETRY tag={tag} attempt={attempt}/3", flush=True)
                time.sleep(0.4)
            if not ok:
                rc = 1
                print(f"QUIET_FAIL tag={tag}", flush=True)
            else:
                print(f"QUIET_OK tag={tag}", flush=True)
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

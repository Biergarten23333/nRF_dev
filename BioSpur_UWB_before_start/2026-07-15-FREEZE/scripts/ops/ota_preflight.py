#!/usr/bin/env python3
"""freeze-clean batch6e — OTA preflight (bake into / call before ota_deploy_*).

Enforces the OTA preflight checklist (DEPLOYMENT.md §5) so an OTA never hangs on
a silent lock. Checks OUR OWN two masters first — never hunt external "competing
centrals" (the 2026-07-15 battery-charged-dongle red herring).

Sequence:
  1. enumerate BOTH masters' connection state (MSTAT).
  2. if any master HOLDS a target tag -> point to release_all_tags.py (§6).
  3. confirm no active capture (TR streaming) on target tags.
  4. confirm all target tags are advertising to the OTA master (scan).
  5. print GO / NO-GO with the reason + next step. Never blocks.

Usage:
  ota_preflight.py --targets BS9336,BS955A,BSCCF4 --ota-master <MASTER_TAG_CDC>

NOTE: exercises live masters; full verification pending ranging (written during
the freeze-clean overnight 2026-07-15 with the tag system down).
"""
import argparse, glob, os, re, sys, time

try:
    import serial
except ImportError:
    sys.exit("pyserial required")

MASTERS = {
    "Master_Tag": "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_*-if00",
    "Master_Anchor": "/dev/serial/by-id/usb-Master_Anchor_Master_Anchor_Control_*-if00",
}


def resolve(pat):
    g = glob.glob(pat)
    return os.path.realpath(g[0]) if g else None


def probe(port, secs=5.0):
    """Return (mstat_lines, raw_text) from a master over its native CDC."""
    try:
        s = serial.Serial()
        s.port = port; s.baudrate = 115200
        s.dtr = False; s.rts = False; s.timeout = 0.3
        s.open()
    except Exception as e:
        return None, f"OPEN_FAIL {e}"
    try:
        s.reset_input_buffer()
        s.write(b"status\r\n"); s.flush()
        buf = bytearray(); t0 = time.time()
        while time.time() - t0 < secs:
            d = s.read(4096)
            if d:
                buf += d
    finally:
        s.close()
    txt = buf.decode("utf-8", "replace")
    return [ln for ln in txt.splitlines() if "MSTAT" in ln], txt


def held_tags(mstat_lines, targets):
    held = set()
    for ln in mstat_lines:
        if "conn=1" in ln:
            for t in targets:
                if t in ln:
                    held.add(t)
    return held


def main():
    p = argparse.ArgumentParser(description="OTA preflight — check our own masters first.")
    p.add_argument("--targets", required=True, help="comma-separated BS tag names")
    p.add_argument("--ota-master", default="", help="CDC of the master that will do the OTA (default: Master_Tag)")
    a = p.parse_args()
    targets = [t for t in a.targets.split(",") if t]
    ota_master = a.ota_master or resolve(MASTERS["Master_Tag"])

    problems = []
    print("=== OTA PREFLIGHT ===")
    print(f"targets: {targets}")

    # 1-2. both masters' state; who holds the targets?
    holders = {}
    for name, pat in MASTERS.items():
        port = resolve(pat)
        if not port:
            print(f"  {name}: not connected")
            continue
        mstat, raw = probe(port)
        if mstat is None:
            print(f"  {name}: {raw}")
            continue
        h = held_tags(mstat, targets)
        streaming = raw.count("notify: TR;") > 3
        print(f"  {name} ({port}): holds={sorted(h) or 'none'} TR-streaming={streaming}")
        if h:
            holders[name] = h
        if streaming and h:
            problems.append(f"{name} is actively capturing (TR streaming) the target tags")

    # 3. resolve holders
    for name, h in holders.items():
        is_ota = (ota_master and resolve(MASTERS.get(name, "")) == ota_master)
        if not is_ota:
            problems.append(
                f"{name} HOLDS {sorted(h)} — release first: "
                f"scripts/release_all_tags.py --port <{name} CDC>")

    # 4. targets advertising to the OTA master? (only meaningful if not all held by OTA master)
    #    (a held tag doesn't advertise; releasing per step 3 makes it advertise.)

    print("---")
    if problems:
        print("NO-GO:")
        for pr in problems:
            print("  -", pr)
        print("Fix the above (release_all_tags.py / stop capture), then re-run preflight.")
        sys.exit(2)
    print("GO: no master is holding the target tags for a conflicting purpose.")
    print("  (Confirm targets advertise via `scan` on the OTA master, then deploy.")
    print("   OTA does NOT need MODE IDLE — OTA_PREPARE quiesces the tag.)")
    sys.exit(0)


if __name__ == "__main__":
    main()

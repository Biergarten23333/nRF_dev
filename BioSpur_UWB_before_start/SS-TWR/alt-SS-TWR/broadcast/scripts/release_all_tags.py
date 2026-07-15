#!/usr/bin/env python3
"""freeze-clean batch6d — the universal OTA-lock escape hatch.

The ONLY hard OTA-lock is a tag HELD CONNECTED by a master (a connected BLE
peripheral does not advertise, so the OTA master's scan sees nothing). This
script performs the atomic unstick on a holding master, using only existing
master console verbs:

    oneshot clear   -> drop any armed re-send that re-locks tags on reconnect
    scan            -> master_set_scan_only_mode: auto_connect_enabled=false,
                       disconnect ALL peers, restart discovery WITHOUT re-grab

After `scan`, held tags re-advertise instantly (uwb_tag_ble.c:1368) and the
master observes them but does not re-connect. Ref: OTA_BLOCKER_REPORT.md Q6.

Usage:
    release_all_tags.py --port <MASTER_CDC>            # one master
    release_all_tags.py --both                          # both B120 masters by by-id
    release_all_tags.py --port <CDC> --expect BS9336,BS955A,BSCCF4

NOTE: verify against live hardware (a master actually holding tags) is pending —
ranging was down when this was written (freeze-clean overnight 2026-07-15).
"""
import argparse, glob, os, re, sys, time

try:
    import serial
except ImportError:
    sys.exit("pyserial required")

MASTER_BYID = {
    "tag": "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_*-if00",
    "anchor": "/dev/serial/by-id/usb-Master_Anchor_Master_Anchor_Control_*-if00",
}


def resolve(pattern_or_port):
    if os.path.exists(pattern_or_port):
        return os.path.realpath(pattern_or_port)
    g = glob.glob(pattern_or_port)
    return os.path.realpath(g[0]) if g else None


def open_master(port):
    s = serial.Serial()
    s.port = port; s.baudrate = 115200
    s.dtr = False; s.rts = False           # never DTR-reset the B120 native CDC
    s.timeout = 0.3; s.write_timeout = 3.0
    s.open()
    return s


def send(s, cmd, wait=1.5):
    s.reset_input_buffer()
    s.write((cmd + "\r\n").encode()); s.flush()
    buf = bytearray(); t0 = time.time()
    while time.time() - t0 < wait:
        d = s.read(4096)
        if d:
            buf += d
    return buf.decode("utf-8", "replace")


def release(port, expect):
    print(f"[release] master {port}")
    try:
        s = open_master(port)
    except Exception as e:
        print(f"  FAIL open: {e}")
        return False
    try:
        send(s, "oneshot clear", 1.0)
        send(s, "scan", 3.0)               # disconnect all + stop auto-connect
        time.sleep(4.0)                     # let held tags re-advertise
        st = send(s, "status", 3.0)
        held = [ln for ln in st.splitlines() if "MSTAT" in ln and "conn=1" in ln]
        scan_txt = send(s, "", 5.0)         # observe SCAN hits (tags re-advertising)
        seen = sorted(set(re.findall(r"bs=(BS[0-9A-Fa-f]+)", scan_txt)))
    finally:
        s.close()
    print(f"  after scan: masters still holding conn=1 peers: {len(held)} (want 0)")
    print(f"  tags now re-advertising (SCAN hits): {seen or '(none seen in window)'}")
    ok = (len(held) == 0)
    if expect:
        missing = [t for t in expect if t not in seen]
        if missing:
            print(f"  WARNING: expected tags not yet seen advertising: {missing}")
    print(f"  => {'RELEASED' if ok else 'STILL HOLDING (investigate)'}")
    return ok


def main():
    p = argparse.ArgumentParser(description="Release all tags held by a master (OTA-lock escape hatch).")
    p.add_argument("--port", help="master CDC port (device or by-id glob)")
    p.add_argument("--both", action="store_true", help="both B120 masters (by-id)")
    p.add_argument("--expect", default="", help="comma-separated BS tags expected to re-advertise")
    a = p.parse_args()
    expect = [t for t in a.expect.split(",") if t]
    targets = []
    if a.both:
        targets = [resolve(MASTER_BYID["tag"]), resolve(MASTER_BYID["anchor"])]
    elif a.port:
        targets = [resolve(a.port)]
    else:
        p.error("give --port or --both")
    targets = [t for t in targets if t]
    if not targets:
        sys.exit("no master CDC found")
    ok = all(release(t, expect) for t in targets)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

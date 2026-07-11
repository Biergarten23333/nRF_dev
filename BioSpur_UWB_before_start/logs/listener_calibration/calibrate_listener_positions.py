#!/usr/bin/env python3
"""Multilaterate the 3D positions of the UWB listeners in the AutoPos anchor frame.

The 7 listeners (LB, LE, LF, LA, LCCF4, L9336, L955A) are normally passive CIR
receivers. This tool drives the USB-CDC command interface added to the listener
firmware (MODE_TAG / MODE_LISTEN / MODE_IDLE / MODE_QUERY, 460800 baud) to switch
each listener, one at a time, into a broadcast Alt-SS-TWR *tag* that ranges to the
8 anchors and emits per-anchor ranges as ``LTAG`` lines. The host reads those
ranges, loads the anchor layout from the freshest AutoPos solve, and least-squares
multilaterates each listener's (x, y, z).

Runs start-to-finish with ZERO human input (antenna-coupling safe -- listeners are
silent during AutoPos):

  Phase 0   Silence wand tags: Master_Tag <- "cmd_all MODE IDLE" (BLE), then verify
            (listeners in MODE_LISTEN see no 0xb136/0xb15a/0xb1f4 LPD lines for 5 s)
  Phase 1   MODE_IDLE every listener (radio off -> AutoPos-safe)
  Phase 1b  AutoPos 8-anchor sweep: subprocess run_autopos_sweep_loop.py (the SAME
            driver the Flutter UI shells out to) on the anchor master -> summary.json,
            then the PRODUCTION v4-io solver chain (build_pairs.py + run_v4io_solve.py)
            -> layout_v4io.json. subprocess.run blocking == wait-for-completion.
  Phase 2   Per listener: MODE_TAG, collect ranges ~30 s, multilaterate, MODE_IDLE
  Phase 3a  Re-enable wand tags: Master_Tag <- "cmd_all MODE RUN", verify LPD returns
  Phase 3b  MODE_LISTEN every listener, verify CIR capture resumes

Any interface that can't be found falls back to a manual prompt for THAT step only.
--dry-run prints every state-changing command without sending it (review before a
live run). --skip-autopos reuses the freshest existing layout instead of sweeping.

CRITICAL (firmware design): the listener transmits from an ON-AIR tag address in
0xB1C1..0xB1C7 (the anchors only answer polls whose src is a tag, 0xB100..0xB1FF).
The 0xc001..0xc007 values are host-facing *labels* echoed in ``MODE=TAG src=`` and
``LTAG;src=`` -- they identify the listener without colliding with the wand tags.

Masters: wand tags = usb-Master_Tag_* (115200); AutoPos = usb-Master_Anchor_*
(J-Link SNR 960148546; tag master J-Link SNR 1050070698).

Outputs: logs/listener_calibration/listener_positions.json (+ raw LTAG logs under
raw/, + the AutoPos sweep/solve under autopos_<stamp>/).

Usage:
  python3 logs/listener_calibration/calibrate_listener_positions.py --dry-run
  python3 logs/listener_calibration/calibrate_listener_positions.py [--sw-sets 250] \
      [--ports p1,..] [--layout PATH] [--capture-seconds 30] [--skip-autopos] \
      [--wand-master PORT] [--anchor-master PORT]

All paths stay inside the repo per project convention.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial required: pip install pyserial")

try:
    import numpy as np
    from scipy.optimize import least_squares
except ImportError:
    sys.exit("numpy + scipy required: pip install numpy scipy")

# ---------------------------------------------------------------------------
# Static configuration
# ---------------------------------------------------------------------------
BAUD = 460800              # listener USB-CDC (J-Link VCOM)
MASTER_BAUD = 115200       # BLE master control ports (nominal CDC baud)

# BLE master control ports (product strings were renamed Master_*_Control_*, so
# match broadly). Master_Tag drives the wand tags; Master_Anchor drives AutoPos.
MASTER_TAG_GLOB = "/dev/serial/by-id/usb-Master_Tag_*-if00"
MASTER_ANCHOR_GLOB = "/dev/serial/by-id/usb-Master_Anchor_*-if00"

# --dry-run: when True, no bytes are ever written to any master/listener port;
# every command that WOULD be sent is printed instead. (Listener reads are still
# performed for discovery, but no mode is changed.)
DRY_RUN = False

# host-facing label (printed by firmware) -> listener short name.
LABEL_TO_NAME = {
    0xC001: "LB",
    0xC002: "LE",
    0xC003: "LF",
    0xC004: "LA",
    0xC005: "LCCF4",
    0xC006: "L9336",
    0xC007: "L955A",
}
NAME_TO_LABEL = {v: k for k, v in LABEL_TO_NAME.items()}
# on-air tag address (for reference / reporting only)
NAME_TO_ONAIR = {
    "LB": 0xB1C1, "LE": 0xB1C2, "LF": 0xB1C3, "LA": 0xB1C4,
    "LCCF4": 0xB1C5, "L9336": 0xB1C6, "L955A": 0xB1C7,
}
# deterministic calibration order (task 5c)
LISTENER_ORDER = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]

# on-air wand-tag short addresses whose polls we watch for during silence checks
WAND_TAG_ADDRS = {0xB136, 0xB15A, 0xB1F4}

# physical plausibility gate for a single anchor range (mm)
RANGE_MIN_MM = 500.0
RANGE_MAX_MM = 7000.0

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(REPO_ROOT, "logs", "listener_calibration")
RAW_DIR = os.path.join(OUT_DIR, "raw")

LTAG_RE = re.compile(r"LTAG;src=0x([0-9a-fA-F]+)((?:;a\d+=-?\d+)+)")
MODE_RE = re.compile(r"MODE=([A-Z]+)\s+src=0x([0-9a-fA-F]+)")
# LPD/LRD passive lines start "LPD;1;..." / "LRD;1;..."; src addr is field index 8
# (0-based) in the ';'-delimited row -- see print_lpd() in the listener firmware.
LPD_SRC_FIELD = 8


# ---------------------------------------------------------------------------
# Serial helpers
# ---------------------------------------------------------------------------
class Listener:
    def __init__(self, name, port, ser):
        self.name = name
        self.port = port
        self.ser = ser

    def send(self, cmd):
        if DRY_RUN:
            print(f"  [DRY-RUN] listener {self.name} ({self.port.split('_')[-1]}) <- {cmd!r}")
            return
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        self.ser.flush()

    def read_lines(self, seconds, sink=None):
        """Yield decoded lines for up to `seconds`; optionally append to a sink list."""
        deadline = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < deadline:
            chunk = self.ser.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace").strip("\r")
                    if sink is not None:
                        sink.append(line)
                    yield line
            else:
                time.sleep(0.005)


def open_port(port):
    return serial.Serial(port, BAUD, timeout=0.1)


def query_mode(ser, timeout=2.0):
    """Send MODE_QUERY, return (mode, label_int) or (None, None)."""
    ser.reset_input_buffer()
    ser.write(b"MODE_QUERY\n")
    ser.flush()
    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(512)
        if not chunk:
            continue
        buf += chunk
        for raw in buf.split(b"\n"):
            m = MODE_RE.search(raw.decode("utf-8", "replace"))
            if m:
                return m.group(1), int(m.group(2), 16)
    return None, None


def discover_listeners(explicit_ports=None):
    """Probe candidate serial ports; return {name: Listener} for those that answer."""
    if explicit_ports:
        candidates = list(explicit_ports)
    else:
        candidates = sorted(glob.glob("/dev/serial/by-id/usb-SEGGER_J-Link_*-if00"))
        candidates += sorted(glob.glob("/dev/ttyACM*"))
    seen = {}
    found = {}
    for port in candidates:
        real = os.path.realpath(port)
        if real in seen:
            continue
        seen[real] = True
        try:
            ser = open_port(port)
        except (serial.SerialException, OSError) as exc:
            print(f"  [skip] {port}: {exc}")
            continue
        # retry: a listener flooding LPD/CIR or just rebooted can miss one query
        mode = label = None
        for _ in range(3):
            mode, label = query_mode(ser)
            if label in LABEL_TO_NAME:
                break
        if label is None or label not in LABEL_TO_NAME:
            ser.close()
            continue
        name = LABEL_TO_NAME[label]
        if name in found:
            print(f"  [warn] {name} seen twice (0x{label:04x}); keeping first")
            ser.close()
            continue
        found[name] = Listener(name, port, ser)
        print(f"  [ok]   {name:6s} <- {port}  (MODE={mode}, label=0x{label:04x})")
    return found


def expect_mode(lst, want, timeout=3.0):
    """Verify a listener reports the wanted mode; returns True/False."""
    mode, label = query_mode(lst.ser, timeout=timeout)
    ok = (mode == want)
    if not ok:
        print(f"  [warn] {lst.name}: expected MODE={want}, got MODE={mode}")
    return ok


# ---------------------------------------------------------------------------
# BLE master control link (wand-tag master + anchor/AutoPos master)
# ---------------------------------------------------------------------------
class MasterLink:
    """A serial link to a BLE master control device (115200). Honors DRY_RUN:
    when set, commands are printed, never written, and reads return ''."""

    def __init__(self, role, port):
        self.role = role      # "tag" or "anchor"
        self.port = port
        self.ser = None

    def open(self):
        if DRY_RUN or self.port is None:
            return self
        self.ser = serial.Serial(self.port, MASTER_BAUD, timeout=0.2,
                                 write_timeout=5.0)
        time.sleep(0.6)       # USB CDC settle
        return self

    def close(self):
        if self.ser is not None:
            self.ser.close()
            self.ser = None

    def _reopen(self):
        try:
            if self.ser is not None:
                self.ser.close()
        except (OSError, serial.SerialException):
            pass
        time.sleep(1.5)          # let the CDC device re-enumerate
        for _ in range(20):
            try:
                self.ser = serial.Serial(self.port, MASTER_BAUD, timeout=0.2,
                                         write_timeout=5.0)
                time.sleep(0.6)
                return True
            except (OSError, serial.SerialException):
                time.sleep(0.5)
        return False

    def cmd(self, text, wait_s=0.6):
        """Send one command line; return whatever the master echoes within wait_s.
        Resilient to the USB-CDC re-enumeration that `conn` can trigger: on an I/O
        error it re-opens the by-id port and retries the write once."""
        short = self.port.split("/")[-1] if self.port else "(none)"
        if DRY_RUN:
            print(f"  [DRY-RUN] master:{self.role} ({short}) <- {text!r}")
            return ""
        if self.ser is None:
            raise RuntimeError(f"master:{self.role} not open")
        print(f"  [master:{self.role}] <- {text}")
        for attempt in (1, 2):
            try:
                self.ser.reset_input_buffer()
                self.ser.write((text + "\n").encode())
                self.ser.flush()
                return self._read(wait_s)
            except (OSError, serial.SerialException):
                if attempt == 1 and self._reopen():
                    continue
                print(f"  [warn] master:{self.role} port error on {text!r}")
                return ""

    def _read(self, wait_s):
        buf = b""
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            try:
                c = self.ser.read(4096)
            except (OSError, serial.SerialException):
                break
            if c:
                buf += c
            else:
                time.sleep(0.005)
        return buf.decode("utf-8", "replace")

    def read_until(self, pred, timeout_s, sink=None):
        """Read lines until pred(line) is True or timeout. Returns (matched, lines)."""
        if DRY_RUN or self.ser is None:
            return False, []
        lines, buf = [], b""
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            c = self.ser.read(4096)
            if not c:
                time.sleep(0.005)
                continue
            buf += c
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip("\r")
                lines.append(line)
                if sink is not None:
                    sink.append(line)
                if pred(line):
                    return True, lines
        return False, lines


def find_master_port(glob_pat, explicit=None):
    if explicit:
        return explicit
    hits = sorted(glob.glob(glob_pat))
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Wand-tag silencing (Phase 0 / Phase 3a)
# ---------------------------------------------------------------------------
# The wand tags (BSCCF4/BS9336/BS955A) are controlled over BLE via the tag master
# (the "Master_Tag_Control" CDC-ACM bridge), NOT over the listener USB CDC. The
# master relays a raw ASCII line over BLE (Nordic UART) to every connected tag:
#   "cmd_all MODE IDLE"  -> each wand switches positioning mode IDLE, disables its
#                           TDMA schedule, and stops emitting UWB polls.
#   "cmd_all MODE RUN"   -> re-arms ranging.
# The master CDC baud is 115200 (nominal). Verified against run_recv_tdma_capture.py
# (send_cmd) + apps/tag/src/uwb_tag_ble.c (MODE IDLE/RUN parser). If the master port
# is absent the operator is asked to silence/re-enable manually.
WAND_MASTER_GLOB = MASTER_TAG_GLOB
# Wand-tag roster (BS names) + TDMA profile/rate for RESUME. Silencing a tag with
# MODE IDLE drops it from the master's TDMA roster, so `cmd_all MODE RUN` alone
# does NOT bring it back -- the roster must be rebuilt and the tags re-linked with
# `conn`. This exact sequence was verified live to restore 3-tag ranging (0 -> ~35
# TR/s). Matches run_recv_tdma_capture.py (profile "motion", tr_hz 10).
WAND_ROSTER = ("BS9336", "BS955A", "BSCCF4")
WAND_TDMA_PROFILE = "motion"
WAND_TR_HZ = 10


def wand_tags_command(action, master_port=None):
    """action in {'silence','enable'}. Returns True if issued (or would be, in
    --dry-run); False only if the master port genuinely can't be found in a live run."""
    port = find_master_port(WAND_MASTER_GLOB, master_port)
    if port is None and not DRY_RUN:
        return False
    if action == "silence":
        # Tags are already connected + ranging; cmd_all reaches them directly. Do
        # NOT send conn/mode recv first -- those restart discovery and momentarily
        # drop the tags, so the following cmd_all reaches nobody (observed).
        cmds = [("cmd_all MODE IDLE", 0.6)]
    else:
        # Full TDMA re-enroll (see WAND_ROSTER note). conn re-links the tags into
        # the rebuilt roster and may re-enumerate the CDC port (MasterLink.cmd
        # transparently re-opens); MODE RUN then starts ranging.
        cmds = [("device kind tag", 1.0), ("tdma hold 1", 0.6), ("tdma clear", 1.2),
                (f"tdma freq {WAND_TDMA_PROFILE} {WAND_TR_HZ}", 0.6)]
        cmds += [(f"tdma roster {bs} {WAND_TDMA_PROFILE}", 0.6) for bs in WAND_ROSTER]
        cmds += [("conn", 3.0), ("tdma hold 0", 0.8), ("cmd_all MODE RUN", 1.0)]
    link = MasterLink("tag", port)
    try:
        link.open()
        for c, w in cmds:
            link.cmd(c, w)
        return True
    except (serial.SerialException, OSError) as exc:
        print(f"  [warn] wand master {port} write failed: {exc}")
        return False
    finally:
        link.close()


def silence_wand_tags(listeners, master_port=None, retries=3):
    """Silence wand tags and confirm. ZERO human input: auto-retries the BLE
    command up to `retries` times (tags can take a few seconds to drop their TDMA
    schedule over BLE), then warns and continues if still heard."""
    print("\n== Phase 0: silence wand tags ==")
    if DRY_RUN:
        wand_tags_command("silence", master_port)
        verify_wand_silence(listeners)
        return True
    for attempt in range(1, retries + 1):
        if not wand_tags_command("silence", master_port):
            print("  [warn] wand master not found; cannot silence programmatically")
            break
        time.sleep(2.0)      # let MODE IDLE propagate over BLE + TDMA wind-down
        offenders = verify_wand_silence(listeners)
        if not offenders:
            return True
        print(f"  [retry {attempt}/{retries}] tags still heard: "
              f"{ {n: [hex(s) for s in v] for n, v in offenders.items()} }")
    print("  [WARN] wand tags NOT fully silenced -- continuing (skip-AutoPos runs "
          "tolerate this as listener-ranging contention only).")
    return False


def reenable_wand_tags(listeners, master_port=None):
    print("\n== Phase 3a: re-enable wand tags ==")
    if not wand_tags_command("enable", master_port):
        input("  [fallback] wand master not found -- re-enable wand tags NOW "
              "via the BLE master, then press Enter... ")
    verify_wand_active(listeners)


def _lpd_src_addrs(line):
    """Return the src short address of an LPD/LRD passive row, else None."""
    if not (line.startswith("LPD;") or line.startswith("LRD;")):
        return None
    parts = line.split(";")
    if len(parts) <= LPD_SRC_FIELD:
        return None
    tok = parts[LPD_SRC_FIELD]
    try:
        return int(tok, 16) if tok.lower().startswith("0x") else int(tok)
    except ValueError:
        return None


def verify_wand_silence(listeners, watch_s=5.0):
    """Listeners in MODE_LISTEN: confirm NO wand-tag polls arrive for watch_s.

    (MODE_IDLE would have the radio off and could not observe anything, so the
    check is done in LISTEN. Anchor beacons are fine; wand-tag src is not.)"""
    if DRY_RUN:
        print("  [DRY-RUN] would put listeners in MODE_LISTEN and confirm no "
              "wand-tag (0xb136/0xb15a/0xb1f4) LPD lines for 5 s")
        return
    for lst in listeners.values():
        lst.send("MODE_LISTEN")
    time.sleep(0.3)
    offenders = {}
    deadline = time.monotonic() + watch_s
    while time.monotonic() < deadline:
        for lst in listeners.values():
            for line in lst.read_lines(0.2):
                src = _lpd_src_addrs(line)
                if src in WAND_TAG_ADDRS:
                    offenders.setdefault(lst.name, set()).add(src)
    if not offenders:
        print("  [ok] no wand-tag polls heard -- tags are silent.")
    return offenders


def verify_wand_active(listeners, watch_s=10.0):
    """Confirm wand-tag polls resume (LPD src=0xb136/0xb15a/0xb1f4) within watch_s."""
    if DRY_RUN:
        print("  [DRY-RUN] would confirm wand-tag LPD lines reappear within 10 s")
        return
    print(f"  verifying wand tags active: watching up to {watch_s:.0f}s ...")
    for lst in listeners.values():
        lst.send("MODE_LISTEN")
    time.sleep(0.3)
    seen = set()
    deadline = time.monotonic() + watch_s
    while time.monotonic() < deadline and seen != WAND_TAG_ADDRS:
        for lst in listeners.values():
            for line in lst.read_lines(0.2):
                src = _lpd_src_addrs(line)
                if src in WAND_TAG_ADDRS:
                    seen.add(src)
    if seen:
        print(f"  [ok] wand-tag polls seen again: {[hex(s) for s in seen]}")
    else:
        print("  [WARN] no wand-tag polls seen -- tags may not have re-enabled.")


# ---------------------------------------------------------------------------
# Anchor layout + multilateration
# ---------------------------------------------------------------------------
def find_layout(explicit=None):
    if explicit:
        return explicit
    candidates = []
    for pat in ("logs/autopos_diagnostic_20260710/layout*.json",
                "solver/outputs/layout*.json",
                "logs/autopos_diagnostic_*/layout*.json",
                "data/anchor_layout_ah_runtime.json"):
        candidates += glob.glob(os.path.join(REPO_ROOT, pat))
    if not candidates:
        return None
    # freshest by mtime
    return max(candidates, key=os.path.getmtime)


def load_anchor_layout(path):
    """Return {anchor_id(0..7): np.array([x,y,z]) mm}. Handles the v4-io list form
    [{id,label,x_mm,y_mm,z_mm}] and a label/id-keyed dict form."""
    with open(path) as f:
        data = json.load(f)
    raw = data.get("anchors", data)
    anchors = {}
    items = raw.items() if isinstance(raw, dict) else ((None, a) for a in raw)
    for key, a in items:
        if "id" in a:
            aid = int(a["id"])
        elif isinstance(key, str) and key[:1].upper() in "ABCDEFGH" and len(key) == 1:
            aid = "ABCDEFGH".index(key.upper())
        elif "label" in a and str(a["label"])[:1].upper() in "ABCDEFGH":
            aid = "ABCDEFGH".index(str(a["label"])[0].upper())
        else:
            continue
        anchors[aid] = np.array([float(a["x_mm"]), float(a["y_mm"]),
                                 float(a["z_mm"])])
    return anchors


# ---------------------------------------------------------------------------
# AutoPos trigger (Phase 1b) -- the 8-anchor inter-anchor sweep + v4-io solve.
# ---------------------------------------------------------------------------
# There is NO single "run AutoPos" serial command: the sweep is a host-driven
# loop over masters A..H (autopos round <M> <sets> -> autopos apply -> count SW-
# lines until SWEEP_DONE) and the layout is solved OFFLINE. This is exactly what
# the Flutter "AutoPos" button does -- it shells out to run_autopos_sweep_loop.py.
# So we subprocess the SAME proven runner (blocking = wait-for-completion), then
# run the PRODUCTION v4-io solver chain that produced layout_full8.json.
AUTOPOS_SWEEP = "SS-TWR/alt-SS-TWR/broadcast/scripts/run_autopos_sweep_loop.py"
AUTOPOS_V4IO_DIR = "logs/autopos_diagnostic_20260710/code"


def autopos_timeout_for(sw_sets):
    """Match the runner's per-round safety ceiling: max(480, 360 + 15*sets)."""
    return max(480, 360 + 15 * int(sw_sets))


def run_autopos_v4io(anchor_master_port, out_dir, sw_sets, timeout_s):
    """Full 8-anchor AutoPos: sweep (subprocess, blocks) -> v4-io offline solve.
    Returns the produced layout path, or None on failure/dry-run."""
    import subprocess

    if not DRY_RUN:
        os.makedirs(out_dir, exist_ok=True)
    summary = os.path.join(out_dir, "summary.json")
    pairs = os.path.join(out_dir, "pairs_all.csv")
    layout = os.path.join(out_dir, "layout_v4io.json")
    steps = [
        ("sweep", [sys.executable, os.path.join(REPO_ROOT, AUTOPOS_SWEEP),
                   "--port", anchor_master_port, "--order", "ABCDEFGH",
                   "--sw-sets", str(sw_sets), "--prewarm-sw-sets", "0",
                   "--round-retries", "0", "--timeout-s", str(timeout_s),
                   "--out-dir", out_dir]),
        ("build_pairs", [sys.executable,
                         os.path.join(REPO_ROOT, AUTOPOS_V4IO_DIR, "build_pairs.py"),
                         summary, pairs]),
        ("v4io_solve", [sys.executable,
                        os.path.join(REPO_ROOT, AUTOPOS_V4IO_DIR, "run_v4io_solve.py"),
                        pairs, layout]),
    ]
    print("  AutoPos v4-io chain (each subprocess blocks to completion):")
    for _, cmd in steps:
        print("    $ " + " ".join(cmd))
    if DRY_RUN:
        print(f"  [DRY-RUN] would run the 3 steps above, then read {layout}")
        return None
    for name, cmd in steps:
        rc = subprocess.run(cmd, cwd=REPO_ROOT).returncode
        if rc != 0:
            print(f"  [warn] AutoPos {name} exited {rc}; aborting AutoPos")
            return None
    return layout if os.path.exists(layout) else None


def multilaterate(anchors, ranges_mm):
    """ranges_mm: {anchor_id: range_mm}. Returns (pos[3], rms_mm, n_used)."""
    ids = [i for i, r in ranges_mm.items()
           if i in anchors and RANGE_MIN_MM <= r <= RANGE_MAX_MM]
    if len(ids) < 4:
        return None, None, len(ids)
    A = np.array([anchors[i] for i in ids])
    r = np.array([ranges_mm[i] for i in ids])

    def resid(p):
        return np.linalg.norm(A - p, axis=1) - r

    centroid = A.mean(axis=0)
    best = None
    # multi-start over z to escape the coplanar-anchor z-mirror ambiguity
    for zc in (centroid[2], 300.0, 1200.0, 2300.0):
        x0 = np.array([centroid[0], centroid[1], zc])
        try:
            sol = least_squares(resid, x0, loss="huber", f_scale=50.0)
        except Exception:
            continue
        rms = float(np.sqrt(np.mean(resid(sol.x) ** 2)))
        if best is None or rms < best[1]:
            best = (sol.x, rms)
    if best is None:
        return None, None, len(ids)
    return best[0], best[1], len(ids)


# ---------------------------------------------------------------------------
# Phase 2: per-listener tag capture
# ---------------------------------------------------------------------------
def capture_ranges(lst, seconds, raw_sink):
    """Collect LTAG lines from a MODE_TAG listener. Returns (per_anchor_lists, n_polls)."""
    per_anchor = {i: [] for i in range(8)}
    n_polls = 0
    for line in lst.read_lines(seconds, sink=raw_sink):
        m = LTAG_RE.search(line)
        if not m:
            continue
        n_polls += 1
        for tok in m.group(2).split(";"):
            if not tok:
                continue
            k, _, v = tok.partition("=")
            if not k.startswith("a"):
                continue
            try:
                aid = int(k[1:])
                val = int(v)
            except ValueError:
                continue
            if val > 0:
                per_anchor.setdefault(aid, []).append(val)
    return per_anchor, n_polls


def solve_listener(lst, anchors, seconds, warmup=5.0):
    raw = []
    print(f"\n-- {lst.name}: MODE_TAG, warmup {warmup:.0f}s + capture {seconds:.0f}s --")
    lst.send("MODE_TAG")
    time.sleep(0.3)
    for line in lst.read_lines(1.0):
        if line.startswith("MODE=TAG"):
            print(f"   {line}")
            break
    # warmup: let the listener appear in anchor responder scheduling
    list(lst.read_lines(warmup))
    per_anchor, n_polls = capture_ranges(lst, seconds, raw)

    # robust per-anchor range = median of plausible samples
    ranges_mm = {}
    for aid, vals in per_anchor.items():
        good = [v for v in vals if RANGE_MIN_MM <= v <= RANGE_MAX_MM]
        if good:
            ranges_mm[aid] = float(np.median(good))
    n_ranges = sum(len(v) for v in per_anchor.values())

    # persist raw capture
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(os.path.join(RAW_DIR, f"{lst.name}_ltag.log"), "w") as f:
        f.write("\n".join(raw))

    pos, rms, n_used = multilaterate(anchors, ranges_mm)
    result = {
        "tag_address": f"0x{NAME_TO_LABEL[lst.name]:04x}",
        "on_air_address": f"0x{NAME_TO_ONAIR[lst.name]:04x}",
        "n_polls": n_polls,
        "n_ranges": n_ranges,
        "anchors_used": n_used,
        "per_anchor_median_mm": {str(k): round(v, 1) for k, v in sorted(ranges_mm.items())},
    }
    if pos is not None:
        result["position_mm"] = [round(float(x), 1) for x in pos]
        result["rms_mm"] = round(rms, 1)
        result["status"] = "OK"
        print(f"   {lst.name} position: ({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}) mm, "
              f"RMS {rms:.0f} mm, {n_used} anchors, {n_polls} polls")
    else:
        result["position_mm"] = None
        result["rms_mm"] = None
        result["status"] = "SKIPPED" if n_polls == 0 else "INSUFFICIENT_ANCHORS"
        print(f"   [WARN] {lst.name}: {result['status']} "
              f"(n_polls={n_polls}, anchors_with_range={len(ranges_mm)})")
    lst.send("MODE_IDLE")
    time.sleep(2.0)   # TDMA settle before next listener
    return result


# ---------------------------------------------------------------------------
# Main sequence
# ---------------------------------------------------------------------------
def main():
    global DRY_RUN
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ports", help="comma-separated listener serial ports (else auto-scan)")
    ap.add_argument("--layout", help="anchor layout JSON override (else the AutoPos v4-io solve)")
    ap.add_argument("--capture-seconds", type=float, default=30.0)
    ap.add_argument("--warmup-seconds", type=float, default=5.0)
    ap.add_argument("--wand-master", help="wand-tag BLE master port (else auto usb-Master_Tag_*)")
    ap.add_argument("--anchor-master", help="anchor/AutoPos BLE master port (else auto usb-Master_Anchor_*)")
    ap.add_argument("--sw-sets", type=int, default=250,
                    help="AutoPos sweep sets per master A..H (overnight v4-io used 250)")
    ap.add_argument("--autopos-timeout", type=int, default=None,
                    help="per-round AutoPos timeout seconds (default: max(480,360+15*sets))")
    ap.add_argument("--skip-autopos", action="store_true",
                    help="do NOT run AutoPos; use the freshest existing layout")
    ap.add_argument("--dry-run", action="store_true",
                    help="print every state-changing command that WOULD be sent; change nothing")
    args = ap.parse_args()

    DRY_RUN = args.dry_run
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUT_DIR, exist_ok=True)
    if DRY_RUN:
        print("=== DRY RUN: no MODE change / master command / AutoPos will be sent. ===")
        print("    (MODE_QUERY identity probes ARE sent during discovery -- they are")
        print("     read-only and change no state.)\n")

    # --- Discover listeners (MODE_QUERY is a read-only identity probe) ---
    print("== Discover listeners ==")
    listeners = discover_listeners(args.ports.split(",") if args.ports else None)
    if not listeners:
        sys.exit("No listeners found. Check ports/baud; is the firmware flashed?")
    ordered = {n: listeners[n] for n in LISTENER_ORDER if n in listeners}
    missing = [n for n in LISTENER_ORDER if n not in listeners]
    print(f"Found {len(ordered)}/7 listeners: {list(ordered)}"
          + (f"  (missing: {missing})" if missing else ""))
    if len(ordered) < 6:
        print("[warn] fewer than 6 listeners -- continuing anyway.")

    wand_port = find_master_port(WAND_MASTER_GLOB, args.wand_master)
    anchor_port = find_master_port(MASTER_ANCHOR_GLOB, args.anchor_master)
    print(f"wand-tag master:  {wand_port or '(not found -> manual fallback)'}")
    print(f"anchor master:    {anchor_port or '(not found -> manual fallback)'}")

    # Phase 0: silence wand tags + verify.
    silence_wand_tags(ordered, args.wand_master)

    # Phase 1: idle all listeners (AutoPos antenna-coupling safe).
    print("\n== Phase 1: idle all listeners ==")
    for lst in ordered.values():
        lst.send("MODE_IDLE")
    if not DRY_RUN:
        time.sleep(0.5)
        for lst in ordered.values():
            expect_mode(lst, "IDLE")
    print("All listeners idle. Safe to run AutoPos.")

    # Phase 1b: AutoPos 8-anchor sweep + v4-io solve (subprocess, blocks to done).
    print("\n== Phase 1b: AutoPos 8-anchor sweep + v4-io solve ==")
    if args.skip_autopos:
        print("  --skip-autopos: using freshest existing layout")
        layout_path = find_layout(args.layout)
    elif anchor_port is None and not DRY_RUN:
        input("  [fallback] anchor master not found -- run the 8-anchor AutoPos "
              "manually, then press Enter... ")
        layout_path = find_layout(args.layout)
    else:
        tmo = args.autopos_timeout or autopos_timeout_for(args.sw_sets)
        apos_dir = os.path.join(OUT_DIR, f"autopos_{stamp}")
        produced = run_autopos_v4io(anchor_port or "<anchor-master-port>", apos_dir,
                                    args.sw_sets, tmo)
        layout_path = produced or (args.layout and find_layout(args.layout)) or find_layout(None)
        if produced is None and not DRY_RUN:
            print(f"  [warn] AutoPos produced no layout; falling back to freshest existing")

    # Load layout for Phase 2 (skip the load in dry-run).
    if DRY_RUN:
        anchors = {}
        print(f"  [DRY-RUN] would multilaterate against: "
              f"{layout_path or '(the freshly produced v4-io layout)'}")
    else:
        if not layout_path or not os.path.exists(layout_path):
            sys.exit("No anchor layout available for multilateration (pass --layout).")
        anchors = load_anchor_layout(layout_path)
        print(f"Using anchor layout: {os.path.relpath(layout_path, REPO_ROOT)} "
              f"({len(anchors)} anchors)")

    # Phase 2: tag-capture each listener sequentially.
    print("\n== Phase 2: tag-capture listeners (sequential) ==")
    results = {}
    for name in LISTENER_ORDER:
        if name not in ordered:
            results[name] = {"tag_address": f"0x{NAME_TO_LABEL[name]:04x}",
                             "status": "SKIPPED", "position_mm": None}
            continue
        if DRY_RUN:
            print(f"  [DRY-RUN] {name} ({ordered[name].port.split('_')[-1]}): "
                  f"MODE_TAG -> capture {args.capture_seconds:.0f}s LTAG -> "
                  f"multilaterate -> MODE_IDLE")
            results[name] = {"tag_address": f"0x{NAME_TO_LABEL[name]:04x}",
                             "status": "DRY_RUN", "position_mm": None}
            continue
        results[name] = solve_listener(ordered[name], anchors,
                                       args.capture_seconds, args.warmup_seconds)

    # Phase 3a: re-enable wand tags + verify.
    reenable_wand_tags(ordered, args.wand_master)

    # Phase 3b: restore listener mode + verify CIR resumes.
    print("\n== Phase 3b: restore listeners to MODE_LISTEN ==")
    for lst in ordered.values():
        lst.send("MODE_LISTEN")
    if DRY_RUN:
        print("  [DRY-RUN] would verify CIR/passive capture resumes on each listener")
    else:
        time.sleep(0.5)
        for lst in ordered.values():
            expect_mode(lst, "LISTEN")
            cir_ok = any(l[:4] in ("LCIR", "LPD;", "LRD;", "LSTA")
                         for l in lst.read_lines(10.0))
            print(f"  {lst.name}: capture {'resumed' if cir_ok else 'NOT seen (warn)'}")
        print("Full system restored. CIR capture ready.")

    # Save results (skip in dry-run).
    if not DRY_RUN:
        out = {
            "anchor_layout_source": os.path.relpath(layout_path, REPO_ROOT),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "capture_seconds": args.capture_seconds,
            "sw_sets": args.sw_sets,
            "listeners": results,
        }
        out_path = os.path.join(OUT_DIR, "listener_positions.json")
        with open(out_path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {os.path.relpath(out_path, REPO_ROOT)}")

    for lst in ordered.values():
        lst.ser.close()
    if DRY_RUN:
        print("\n=== DRY RUN complete: nothing was changed. ===")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Overnight multistatic CIR capture v2 -- 7 co-located UWB listeners.

Records every listener's USB-CDC stream (CIR + scalar) to raw/<name>.log for
--duration-hours.  Designed for 10+ hours unattended:
  * no unbounded memory growth (lines stream straight to disk; only counters kept)
  * every file write guarded (disk-full / IO errors degrade one listener, not all)
  * a dead port is reported and periodically re-opened (USB re-enumeration safe)
  * a silent listener is warned about but its port is kept open
  * Ctrl+C / SIGTERM -> clean flush + per-listener summary

Listeners and anchors were NOT moved since calibration; only the wand rotated.
No hardware or firmware is touched here -- pure passive capture.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import serial

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW_DIR = os.path.join(HERE, "raw")
CAL_DIR = "logs/system_calibration_20260710_233443"

# Verified listener SNRs (2026-07-11, all MODE_LISTEN).  LA may be absent -> handled.
LISTENER_SNR = {
    "LB": "760184545", "LE": "760184767", "LF": "760184964", "LA": "760184753",
    "LCCF4": "760184784", "L9336": "760186071", "L955A": "760186081",
}
BAUD = 460800
REOPEN_EVERY_S = 30.0        # dead-port re-open cadence
SILENCE_WARN_S = 60.0        # warn if no new lines for this long


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def hms():
    return datetime.now().strftime("%H:%M:%S")


class ListenerCapture:
    """One listener: dedicated reader thread, own file handle, own counters."""

    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.path = os.path.join(RAW_DIR, f"{name}.log")
        self.lines = 0
        self.cir = 0
        self.last_rx = time.time()
        self.state = "init"          # init|ok|silent|dead|write_fail
        self._fh = None
        self._ser = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def join(self, t=3.0):
        self._thread.join(timeout=t)

    def _open_serial(self):
        try:
            return serial.Serial(self.port, BAUD, timeout=0.5)
        except Exception as e:                                   # noqa: BLE001
            print(f"[{hms()}] ERROR {self.name}: open failed: {e}", flush=True)
            return None

    def _run(self):
        try:
            self._fh = open(self.path, "a", buffering=1 << 16)
        except Exception as e:                                   # noqa: BLE001
            print(f"[{hms()}] ERROR {self.name}: cannot open log {self.path}: {e}",
                  flush=True)
            self.state = "write_fail"
            return
        buf = b""
        next_reopen = 0.0
        while not self._stop.is_set():
            if self._ser is None:
                if time.time() < next_reopen:
                    time.sleep(0.5)
                    continue
                self._ser = self._open_serial()
                if self._ser is None:
                    self.state = "dead"
                    next_reopen = time.time() + REOPEN_EVERY_S
                    continue
                if self.state in ("dead", "init"):
                    if self.state == "dead":
                        print(f"[{hms()}] RECOVERED {self.name}: port re-opened",
                              flush=True)
                    self.state = "ok"
            try:
                d = self._ser.read(8192)
            except Exception as e:                               # noqa: BLE001
                print(f"[{hms()}] ERROR {self.name}: read failed ({e}); "
                      f"will re-open", flush=True)
                self._close_serial()
                self.state = "dead"
                next_reopen = time.time() + REOPEN_EVERY_S
                continue
            if not d:
                continue
            self.last_rx = time.time()
            buf += d
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip("\r")
                if not line:
                    continue
                if not self._write(line):
                    return                                        # write_fail -> stop
                self.lines += 1
                if line.startswith("LCIRD"):
                    self.cir += 1
        self._flush()
        self._close_serial()
        self._close_file()

    def _write(self, line):
        try:
            self._fh.write(line + "\n")
            return True
        except Exception as e:                                   # noqa: BLE001
            print(f"[{hms()}] ERROR {self.name}: write failed ({e}); "
                  f"stopping this listener (others continue)", flush=True)
            self.state = "write_fail"
            self._close_serial()
            self._close_file()
            return False

    def _flush(self):
        try:
            if self._fh:
                self._fh.flush()
                os.fsync(self._fh.fileno())
        except Exception:                                        # noqa: BLE001
            pass

    def _close_serial(self):
        try:
            if self._ser:
                self._ser.close()
        except Exception:                                        # noqa: BLE001
            pass
        self._ser = None

    def _close_file(self):
        try:
            if self._fh:
                self._fh.close()
        except Exception:                                        # noqa: BLE001
            pass


def discover_ports():
    found = {}
    for name, snr in LISTENER_SNR.items():
        g = glob.glob(f"/dev/serial/by-id/usb-SEGGER_J-Link_000{snr}-if00")
        if g:
            found[name] = g[0]
        else:
            print(f"[{hms()}] WARNING: listener {name} (SNR {snr}) port not found "
                  f"-- continuing without it", flush=True)
    return found


def write_metadata(start_iso, duration_h, listeners_present):
    meta = {
        "start_time": start_iso,
        "capture_type": "overnight_cir_v2",
        "duration_hours": duration_h,
        "listeners_present": listeners_present,
        "system_calibration": CAL_DIR + "/",
        "anchor_layout": CAL_DIR + "/anchor_layout.json",
        "listener_positions": CAL_DIR + "/listener_positions.json",
        "wand_positions": "logs/overnight_radar_20260711/wand_recapture/"
                          "wand_positions_updated.json",
        "wand_address_map": "logs/overnight_radar_20260711/wand_recapture/"
                            "wand_address_map.json",
        "physical_changes": [
            "wand rotated 20-30cm from metal pole to reduce coupling",
            "listeners relocated to 3 heights (ceiling/mid/floor)",
            "co-located listeners removed from anchor proximity",
        ],
        "known_issues": [
            "z-axis inversion in anchor layout -- fix in analysis",
            "LCCF4 ~24% CIR parse rate -- UART bandwidth limit",
        ],
    }
    path = os.path.join(HERE, "metadata.json")
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-hours", type=float, default=10.0)
    ap.add_argument("--heartbeat-seconds", type=float, default=60.0)
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    ports = discover_ports()
    if not ports:
        print(f"[{hms()}] FATAL: no listener ports found -- aborting", flush=True)
        sys.exit(1)

    start = time.time()
    start_iso = now_iso()
    end = start + args.duration_hours * 3600.0
    meta_path = write_metadata(start_iso, args.duration_hours, sorted(ports))
    print(f"[{hms()}] OVERNIGHT v2 START -- {len(ports)}/7 listeners "
          f"({', '.join(sorted(ports))})", flush=True)
    print(f"[{hms()}] duration={args.duration_hours}h  end~"
          f"{datetime.fromtimestamp(end).strftime('%H:%M')}  "
          f"raw={os.path.relpath(RAW_DIR, REPO)}  metadata={os.path.basename(meta_path)}",
          flush=True)

    caps = {name: ListenerCapture(name, port) for name, port in sorted(ports.items())}
    for c in caps.values():
        c.start()

    stop_flag = threading.Event()

    def _sig(_signo, _frame):
        print(f"\n[{hms()}] signal received -- shutting down", flush=True)
        stop_flag.set()
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    prev = {name: 0 for name in caps}
    try:
        while not stop_flag.is_set() and time.time() < end:
            stop_flag.wait(args.heartbeat_seconds)
            if stop_flag.is_set():
                break
            now = time.time()
            parts = []
            for name, c in caps.items():
                delta = c.lines - prev[name]
                prev[name] = c.lines
                silent = (now - c.last_rx) > SILENCE_WARN_S
                if silent and c.state == "ok":
                    print(f"[{hms()}] WARNING {name}: no data for "
                          f"{int(now - c.last_rx)}s (port kept open)", flush=True)
                flag = "" if c.state == "ok" else f"!{c.state}"
                parts.append(f"{name}: {c.lines} lines ({c.cir} CIR) +{delta}{flag}")
            elapsed_h = (now - start) / 3600.0
            print(f"[{hms()}] +{elapsed_h:4.2f}h | " + " | ".join(parts), flush=True)
            # periodic durability flush
            for c in caps.values():
                c._flush()
    finally:
        for c in caps.values():
            c.stop()
        for c in caps.values():
            c.join()
        dur_h = (time.time() - start) / 3600.0
        print(f"\n[{hms()}] ===== OVERNIGHT v2 SUMMARY (ran {dur_h:.2f}h) =====",
              flush=True)
        total = 0
        for name, c in caps.items():
            total += c.lines
            print(f"  {name:7s} {c.lines:9d} lines  {c.cir:8d} CIR  "
                  f"({100.0 * c.cir / c.lines if c.lines else 0:4.1f}% CIR)  "
                  f"state={c.state}  -> {os.path.relpath(c.path, REPO)}", flush=True)
        print(f"  TOTAL   {total:9d} lines across {len(caps)} listeners", flush=True)
        print(f"[{hms()}] done.", flush=True)


if __name__ == "__main__":
    main()

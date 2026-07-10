#!/usr/bin/env python3
"""Cold-start thermal characterization — 6-listener serial capture.

Structurally identical to overnight_radar_20260710/capture_overnight.py
(one robust thread per port, raw line-for-line logging), with two additions
needed for the cold-start experiment:

  1. A t=0 power-on marker (prompted, or assumed-now via --no-prompt when
     launched from run_coldstart.sh) recorded to metadata.json.
  2. A live CFO heartbeat every 15 s so the operator can watch the DW1000
     clock-frequency-offset curve settle in real time while shooting the IR
     camera.

CFO is the temperature proxy (the on-chip TC_SARL sensor is NOT read — doing
so wedges the SPI clock domain). For each listener we lock onto the FIRST TX
source we see and track only that source's frames: rxtofs is the receiver's
time-tracking offset *relative to that transmitter*, so mixing sources would
add discrete per-transmitter clock steps on top of the thermal drift we care
about. cfo_ppm = rxtofs / ttcki * 1e6 (rxtofs is already sign-extended to
32-bit signed by the firmware; ttcki is the tracking interval).

Raw logs are written byte-for-byte exactly as received (including garbled
LCCF4 lines) — this is a capture layer, not a parser. All CFO parsing here is
best-effort and only feeds the live display; the authoritative parse is
analyze_coldstart.py.
"""
import os
import sys
import time
import json
import signal
import argparse
import threading
import statistics
from collections import deque

import serial

BAUD = 460800
READ_CHUNK = 4096
HEARTBEAT_PERIOD_S = 15          # watch the CFO curve live (not 60)
CFO_WINDOW_S = 15.0              # heartbeat median is over the last 15 s of frames
SILENCE_WARN_PERIOD_S = 30
ERROR_LOG_RATE_LIMIT_S = 30
RECONNECT_BACKOFF_S = 2.0

# Default output root = this script's own directory, so runs land next to the
# scripts wherever the thermal_characterization/ folder is moved (it lives in the
# repo under BioSpur_UWB_before_start/). Override with --root or --run-dir.
DEFAULT_ROOT = os.path.dirname(os.path.abspath(__file__))

# SNR -> node name (verified this session; identical map to the overnight run).
# by-id paths are stable across ttyACM renumbering, which matters here because
# the target nRF re-enumerates when it cold-boots at t=0.
NODES = {
    "LB": "760184545",
    "LE": "760184767",
    "LF": "760184964",
    "LCCF4": "760184784",
    "L9336": "760186071",
    "L955A": "760186081",
}
NODE_ORDER = ["LB", "LE", "LF", "LCCF4", "L9336", "L955A"]
BY_ID_TEMPLATE = "/dev/serial/by-id/usb-SEGGER_J-Link_{snr:012d}-if00"


def extract_cfo(line_str):
    """Best-effort CFO from an LPD/LRD line. Returns (src_hex_str, cfo_ppm) or None.

    Field layout (verbatim from UWB_listener/src/main.c, verified in step0_parse):
      p[0]=LPD/LRD ... p[8]=src(hex) ... then kv tokens rcph=/rxtofs=/ttcki=/agc=.
    rxtofs is printed with %d (already sign-extended 19->32 in firmware); ttcki
    with %lu. Scanning from p[20:] finds the kv tokens for both LPD (has an extra
    mask field at p[20]) and LRD (no mask) without special-casing.
    """
    if not (line_str.startswith("LPD") or line_str.startswith("LRD")):
        return None
    p = line_str.split(";")
    if len(p) < 21:
        return None
    try:
        src = p[8]
        rxtofs = None
        ttcki = None
        for t in p[20:]:
            if t.startswith("rxtofs="):
                rxtofs = int(t.split("=", 1)[1])
            elif t.startswith("ttcki="):
                ttcki = int(t.split("=", 1)[1])
        if rxtofs is None or ttcki is None or ttcki == 0:
            return None
        cfo = rxtofs / ttcki * 1e6
        if not (-100.0 < cfo < 100.0):    # reject garbled-line nonsense
            return None
        return src, cfo
    except (ValueError, IndexError):
        return None


def worker(name, port_path, log_path, st, lock, stop_event):
    """One thread per listener: reconnecting raw capture + live CFO extraction."""
    try:
        fh = open(log_path, "ab", buffering=0)
    except OSError as e:
        print(f"[ERROR] {name}: cannot open log file {log_path}: {e}", file=sys.stderr)
        with lock:
            st[name]["dead"] = True
        return

    ser = None
    buf = b""
    last_write_err = 0.0
    try:
        while not stop_event.is_set():
            if ser is None:
                try:
                    ser = serial.Serial(port_path, BAUD, timeout=1.0)
                    print(f"[INFO] {name}: opened {port_path}", flush=True)
                    with lock:
                        st[name]["dead"] = False
                except serial.SerialException as e:
                    # Port not present yet (target still cold-booting) or yanked.
                    # Back off and retry rather than giving up on the port.
                    with lock:
                        st[name]["dead"] = True
                    if stop_event.wait(RECONNECT_BACKOFF_S):
                        break
                    continue

            try:
                chunk = ser.read(READ_CHUNK)
            except serial.SerialException as e:
                print(f"[WARN] {name}: serial error, will reconnect: {e}", file=sys.stderr)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                with lock:
                    st[name]["dead"] = True
                continue

            if not chunk:
                continue

            now = time.time()
            with lock:
                st[name]["last_data_time"] = now

            buf += chunk
            if b"\n" not in buf:
                continue
            parts = buf.split(b"\n")
            buf = parts[-1]
            for raw in parts[:-1]:
                is_cir = b"LCIRD" in raw
                try:
                    fh.write(raw + b"\n")
                except OSError as e:
                    if now - last_write_err > ERROR_LOG_RATE_LIMIT_S:
                        print(f"[ERROR] {name}: write failed (disk full?): {e}",
                              file=sys.stderr)
                        last_write_err = now

                cfo_res = None
                if not is_cir:
                    cfo_res = extract_cfo(raw.decode("ascii", "ignore"))
                with lock:
                    s = st[name]
                    s["lines"] += 1
                    if is_cir:
                        s["cir"] += 1
                    if cfo_res is not None:
                        src, cfo = cfo_res
                        if s["src_lock"] is None:
                            s["src_lock"] = src
                        if src == s["src_lock"]:
                            s["cfo"].append((now, cfo))
    finally:
        if buf:
            try:
                fh.write(buf)
            except OSError:
                pass
        try:
            fh.close()
        except Exception:
            pass
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def _window_median_cfo(samples, now, window_s):
    """Median CFO over samples (deque of (ts, cfo)) within the last window_s."""
    vals = [c for (ts, c) in samples if now - ts <= window_s]
    if not vals:
        return None
    return statistics.median(vals)


def heartbeat_loop(st, lock, stop_event, t0):
    while not stop_event.wait(HEARTBEAT_PERIOD_S):
        now = time.time()
        elapsed = now - t0
        mm, ss = divmod(int(max(0, elapsed)), 60)
        parts = []
        with lock:
            for name in NODE_ORDER:
                s = st[name]
                med = _window_median_cfo(s["cfo"], now, CFO_WINDOW_S)
                if s["dead"]:
                    parts.append(f"{name} DEAD")
                elif med is None:
                    parts.append(f"{name} cfo=  n/a  ")
                else:
                    parts.append(f"{name} cfo={med:+6.2f}ppm")
                if (not s["dead"]
                        and (now - s["last_data_time"]) > SILENCE_WARN_PERIOD_S
                        and (now - s["last_warn_time"]) > SILENCE_WARN_PERIOD_S):
                    print(f"[WARN] {name}: silent for "
                          f"{int(now - s['last_data_time'])}s", file=sys.stderr)
                    s["last_warn_time"] = now
        print(f"[{mm:02d}:{ss:02d}] " + " | ".join(parts), flush=True)


def print_summary(st, lock, t0):
    elapsed = time.time() - t0
    print("=" * 72, flush=True)
    print(f"COLD-START CAPTURE SUMMARY  (elapsed {elapsed/60:.1f} min)", flush=True)
    with lock:
        for name in NODE_ORDER:
            s = st[name]
            status = "DEAD" if s["dead"] else "OK"
            med = _window_median_cfo(s["cfo"], time.time(), CFO_WINDOW_S)
            cfo_txt = f"{med:+.2f}ppm" if med is not None else "n/a"
            print(f"  {name:<7} {s['lines']:>9} lines  {s['cir']:>7} CIR  "
                  f"src={s['src_lock'] or '-':<8} last_cfo={cfo_txt:<10} [{status}]",
                  flush=True)
    print("=" * 72, flush=True)


def main():
    ap = argparse.ArgumentParser(description="Cold-start CFO capture (6 listeners).")
    ap.add_argument("--duration-min", type=float, default=90.0,
                    help="capture duration in minutes (default 90)")
    ap.add_argument("--run-dir", default=None,
                    help="run directory (default: <root>/run_<timestamp>)")
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help=f"root for auto run dir (default {DEFAULT_ROOT})")
    ap.add_argument("--no-prompt", action="store_true",
                    help="skip the power-on prompt; take t=0 = now "
                         "(used when launched from run_coldstart.sh)")
    args = ap.parse_args()

    run_dir = args.run_dir or os.path.join(
        args.root, "run_" + time.strftime("%Y%m%d_%H%M%S"))
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    now0 = time.time()
    st = {
        name: {"lines": 0, "cir": 0, "dead": True,
               "last_data_time": now0, "last_warn_time": 0.0,
               "src_lock": None, "cfo": deque(maxlen=8000)}
        for name in NODE_ORDER
    }
    lock = threading.Lock()
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    # --- t=0 power-on marker ---
    if args.no_prompt:
        print("[INFO] --no-prompt: assuming nodes powered on now; t=0 = launch.",
              flush=True)
    else:
        print("\n>>> Power on all nodes NOW, then press Enter <<<")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] aborted before t=0.", flush=True)
            return
    t0 = time.time()

    port_paths = {n: BY_ID_TEMPLATE.format(snr=int(NODES[n])) for n in NODE_ORDER}
    meta = {
        "experiment": "coldstart_thermal_characterization",
        "t0_epoch": t0,
        "t0_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
        "duration_min": args.duration_min,
        "baud": BAUD,
        "cfo_proxy": "cfo_ppm = rxtofs / ttcki * 1e6",
        "nodes": {n: {"snr": NODES[n], "port": port_paths[n]} for n in NODE_ORDER},
        "note": "now_ms in the logs is device uptime == seconds since power-on; "
                "analysis uses now_ms as the time axis, t0_epoch is for record.",
    }
    with open(os.path.join(run_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    threads = []
    for name in NODE_ORDER:
        t = threading.Thread(
            target=worker,
            args=(name, port_paths[name], os.path.join(raw_dir, f"{name}.log"),
                  st, lock, stop_event),
            daemon=True, name=f"worker-{name}")
        t.start()
        threads.append(t)

    hb = threading.Thread(target=heartbeat_loop, args=(st, lock, stop_event, t0),
                          daemon=True, name="heartbeat")
    hb.start()

    dur_s = args.duration_min * 60.0
    print(f"[{time.strftime('%H:%M:%S')}] cold-start capture running: "
          f"{len(NODE_ORDER)} listeners -> {raw_dir}", flush=True)
    print(f"[INFO] will run {args.duration_min:.0f} min; Ctrl+C to stop early.",
          flush=True)

    try:
        while not stop_event.is_set():
            if time.time() - t0 >= dur_s:
                print(f"\n[INFO] duration {args.duration_min:.0f} min reached; "
                      f"stopping.", flush=True)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, shutting down...", flush=True)
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        hb.join(timeout=2)
        meta["t_end_epoch"] = time.time()
        meta["elapsed_min"] = round((time.time() - t0) / 60.0, 3)
        with open(os.path.join(run_dir, "metadata.json"), "w") as f:
            json.dump(meta, f, indent=2)
        print_summary(st, lock, t0)
        print(f"[INFO] run dir: {run_dir}", flush=True)
        print(f"RUN_DIR={run_dir}", flush=True)


if __name__ == "__main__":
    main()

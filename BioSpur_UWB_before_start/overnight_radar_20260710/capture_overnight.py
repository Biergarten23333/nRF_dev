#!/usr/bin/env python3
"""Overnight 6-listener CIR capture, robust to unattended 8+ hour operation.

One thread per port, independent try/except around every serial read and every
file write. A dead port (USB disconnect) closes only that port's thread; the
other 5 keep running. A silent port (no data, but still connected) is left
alone -- the firmware's own 15s RX-stall watchdog is the thing that recovers
it, not this script. Lines are written to disk exactly as received, including
garbled/truncated ones (e.g. LCCF4's high-rate CIR dumps) -- this is a raw
capture layer, not a parser.
"""
import os
import sys
import time
import signal
import threading
import serial

BAUD = 460800
# Output dir is overridable so a new run never appends onto a prior run's logs
# (the 2026-07-09 run's anchorless data must stay untouched). Files open in
# append mode, so pointing at a fresh dir per run is the safe default.
OUT_DIR = os.environ.get("BIOSPUR_OVERNIGHT_OUT",
                         "/mnt/nrf_ssd/overnight_radar_20260709/raw")
HEARTBEAT_PERIOD_S = 60
SILENCE_WARN_PERIOD_S = 60
READ_CHUNK = 4096
ERROR_LOG_RATE_LIMIT_S = 30

# SNR -> node name, verified this session via docs/broadcast_tag_inventory.md,
# an independent explore-agent cross-check, and successful individual
# flash+verify of each physical board. by-id paths are stable across ttyACM
# renumbering (unlike raw /dev/ttyACMnn), which matters for an unattended run.
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


def worker(name, port_path, log_path, stats, stats_lock, stop_event):
    try:
        fh = open(log_path, "ab", buffering=0)
    except OSError as e:
        print(f"[ERROR] {name}: cannot open log file {log_path}: {e}", file=sys.stderr)
        with stats_lock:
            stats[name]["dead"] = True
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
                except serial.SerialException as e:
                    print(f"[ERROR] {name}: cannot open {port_path}: {e}", file=sys.stderr)
                    with stats_lock:
                        stats[name]["dead"] = True
                    return

            try:
                chunk = ser.read(READ_CHUNK)
            except serial.SerialException as e:
                print(f"[ERROR] {name}: serial exception, closing this port only: {e}", file=sys.stderr)
                try:
                    ser.close()
                except Exception:
                    pass
                with stats_lock:
                    stats[name]["dead"] = True
                return

            if not chunk:
                continue

            with stats_lock:
                stats[name]["last_data_time"] = time.time()

            buf += chunk
            if b"\n" not in buf:
                continue
            parts = buf.split(b"\n")
            buf = parts[-1]           # incomplete remainder, kept for next read
            complete_lines = parts[:-1]

            for line in complete_lines:
                is_cir = b"LCIRD" in line
                with stats_lock:
                    stats[name]["lines"] += 1
                    if is_cir:
                        stats[name]["cir"] += 1
                try:
                    fh.write(line + b"\n")
                except OSError as e:
                    now = time.time()
                    if now - last_write_err > ERROR_LOG_RATE_LIMIT_S:
                        print(f"[ERROR] {name}: write failed (disk full?): {e} "
                              f"-- continuing capture, data for this period is lost",
                              file=sys.stderr)
                        last_write_err = now
    finally:
        # flush any trailing partial line so the last few bytes aren't lost
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


def heartbeat_loop(stats, stats_lock, stop_event):
    while not stop_event.wait(HEARTBEAT_PERIOD_S):
        now = time.time()
        parts = []
        with stats_lock:
            for name in NODE_ORDER:
                s = stats[name]
                tag = " [DEAD]" if s["dead"] else ""
                parts.append(f"{name}: {s['lines']} lines ({s['cir']} CIR){tag}")
                if not s["dead"] and (now - s["last_data_time"]) > SILENCE_WARN_PERIOD_S:
                    if now - s["last_warn_time"] > SILENCE_WARN_PERIOD_S:
                        silent_s = int(now - s["last_data_time"])
                        print(f"[WARNING] {name}: silent for {silent_s}s -- "
                              f"port left open, relying on firmware's 15s RX-stall "
                              f"self-heal (not reopening from this script)",
                              file=sys.stderr)
                        s["last_warn_time"] = now
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] " + " | ".join(parts), flush=True)


def print_summary(stats, stats_lock, start_time):
    elapsed = time.time() - start_time
    print("=" * 70, flush=True)
    print(f"FINAL SUMMARY  (runtime {elapsed/3600:.2f}h)", flush=True)
    with stats_lock:
        for name in NODE_ORDER:
            s = stats[name]
            status = "DEAD" if s["dead"] else "OK"
            print(f"  {name:<7} {s['lines']:>8} lines  {s['cir']:>7} CIR  [{status}]", flush=True)
    print("=" * 70, flush=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    now = time.time()
    stats = {
        name: {"lines": 0, "cir": 0, "dead": False,
               "last_data_time": now, "last_warn_time": 0.0}
        for name in NODE_ORDER
    }
    stats_lock = threading.Lock()
    stop_event = threading.Event()

    def handle_term(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_term)

    threads = []
    for name in NODE_ORDER:
        snr = NODES[name]
        port_path = BY_ID_TEMPLATE.format(snr=int(snr))
        log_path = os.path.join(OUT_DIR, f"{name}.log")
        t = threading.Thread(
            target=worker,
            args=(name, port_path, log_path, stats, stats_lock, stop_event),
            daemon=True,
            name=f"worker-{name}",
        )
        t.start()
        threads.append(t)

    hb_thread = threading.Thread(
        target=heartbeat_loop, args=(stats, stats_lock, stop_event),
        daemon=True, name="heartbeat",
    )
    hb_thread.start()

    print(f"[{time.strftime('%H:%M:%S')}] capture_overnight started: "
          f"{len(NODE_ORDER)} ports -> {OUT_DIR}", flush=True)

    start_time = time.time()
    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, shutting down...", flush=True)
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        hb_thread.join(timeout=2)
        print_summary(stats, stats_lock, start_time)


if __name__ == "__main__":
    main()

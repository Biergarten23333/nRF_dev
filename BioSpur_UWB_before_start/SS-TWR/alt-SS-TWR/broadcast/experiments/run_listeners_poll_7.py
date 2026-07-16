#!/usr/bin/env python3
"""Launch all 7 co-located UWB poll-listeners concurrently for a fixed window, each
producing its own lpd.csv/lrd.csv/lcir*.csv (same format as the single-listener
overnight run) via scripts/capture_uwb_poll_listener.py @460800.

Per listener: out-dir <OUT>/listener/<Lname>/  (script adds listener_<ts>/ inside).
Supervised: if a listener's serial drops before the window ends, relaunch it for the
remaining time (new timestamped subdir) so coverage self-heals. Independent of the tag
master port -> runs safely alongside the power sweep. Writes manifest.json + a run log.

Env: LSN_DUR (seconds, default 5100), LSN_OUT (output root, required in practice).
"""
import os, sys, time, json, glob, subprocess, threading, signal

BCAST = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast"
CAP = os.path.join(BCAST, "scripts", "capture_uwb_poll_listener.py")
BAUD = "460800"
# all 7 MODE_LISTEN, CIR-enabled (2026-07-11 fleet); anchor-side + wand-side co-located
LISTENER_SNR = {
    "LB": "760184545", "LE": "760184767", "LF": "760184964", "LA": "760184753",
    "LCCF4": "760184784", "L9336": "760186071", "L955A": "760186081",
}
DUR = int(float(os.environ.get("LSN_DUR", "5100")))
OUT_ROOT = os.environ.get("LSN_OUT", os.path.join(BCAST, "logs", "listener_poll_7"))
LISTEN_DIR = os.path.join(OUT_ROOT, "listener")
os.makedirs(LISTEN_DIR, exist_ok=True)
RUN_LOG = os.path.join(OUT_ROOT, "listeners_run_log.txt")
_loglock = threading.Lock()


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with _loglock, open(RUN_LOG, "a") as f:
        f.write(line + "\n")


def resolve_port(snr):
    link = f"/dev/serial/by-id/usb-SEGGER_J-Link_000{snr}-if00"
    return os.path.realpath(link) if os.path.exists(link) else None


def run_one(name, snr, deadline):
    outdir = os.path.join(LISTEN_DIR, name)
    os.makedirs(outdir, exist_ok=True)
    attempt = 0
    while time.time() < deadline - 20:
        port = resolve_port(snr)
        if port is None:
            log(f"{name} SNR={snr} port MISSING; retry in 5s")
            time.sleep(5)
            continue
        remaining = int(deadline - time.time())
        attempt += 1
        capout = open(os.path.join(outdir, f"_caplog_{attempt}.txt"), "w")
        log(f"{name} start attempt#{attempt} port={port} dur={remaining}s")
        try:
            p = subprocess.Popen(
                [sys.executable, "-u", CAP, "--port", port, "--baud", BAUD,
                 "--duration", str(remaining), "--out-dir", outdir],
                cwd=BCAST, stdout=capout, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)
            rc = p.wait()
        except Exception as e:
            log(f"{name} exception: {e}")
            rc = -1
        finally:
            capout.close()
        if time.time() >= deadline - 20:
            log(f"{name} done (rc={rc})")
            return
        log(f"{name} exited early rc={rc}; relaunch for remaining window")
        time.sleep(2)


def main():
    log(f"=== 7-LISTENER POLL CAPTURE START dur={DUR}s out={OUT_ROOT} ===")
    # resolve + manifest
    manifest = {"baud": int(BAUD), "duration_s": DUR, "ts_start": time.strftime("%Y-%m-%d %H:%M:%S"),
                "listeners": {}}
    for name, snr in LISTENER_SNR.items():
        manifest["listeners"][name] = {"snr": snr, "port": resolve_port(snr)}
    with open(os.path.join(OUT_ROOT, "listener_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    missing = [n for n, v in manifest["listeners"].items() if v["port"] is None]
    if missing:
        log(f"WARNING: missing at start: {missing}")

    deadline = time.time() + DUR
    threads = []
    for name, snr in LISTENER_SNR.items():
        t = threading.Thread(target=run_one, args=(name, snr, deadline), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.3)  # stagger serial opens
    for t in threads:
        t.join()

    # tally
    tally = {}
    for name in LISTENER_SNR:
        rows = 0
        for lpd in glob.glob(os.path.join(LISTEN_DIR, name, "listener_*", "lpd.csv")):
            try:
                rows += max(0, sum(1 for _ in open(lpd)) - 1)
            except Exception:
                pass
        tally[name] = rows
    log(f"=== 7-LISTENER DONE lpd_rows_per_listener={json.dumps(tally)} ===")


if __name__ == "__main__":
    main()

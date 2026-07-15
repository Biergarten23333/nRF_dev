#!/usr/bin/env python3
"""180 deg Antenna Flip Experiment -- CAPTURE.

A/B test of DWM1001C PCB-antenna directionality as a per-anchor range-bias source.
Wand on a mic stand, T-plane vertical, at room center.  Phase 1: tags 9336 & 955A
face the ADHE wall, CCF4 (antenna mounted 180 deg opposite) faces the BCFG wall.
Flip the whole wand 180 deg and repeat as Phase 2.

HOW THE 3 TAGS REACH THE HOST
------------------------------
On this rig the wand tags do NOT expose a directly-connectable BLE-NUS server to
the PC: they are already owned by the B120 "master" board, which is the BLE central
and holds a NUS link to each tag inside its TDMA scheduler.  The master forwards
every tag's per-anchor ranging report over its own USB CDC serial console as

    [RECV] BS9336 notify: TR;<ver>;<sweep>;<plan>;<pmode>;<active_mask>;<valid_mask>;
           <raw_mm,...>;<filt_mm,...>;<q,...>;<statuses>;<trailer>

That master serial stream *is* the BLE pipeline (the same one used by
scripts_reserve_nomore_change/run_recv_tdma_capture.py and the wand recapture).  A
second BLE central (bleak) cannot connect to the tags while the master owns them,
so this script OBSERVES the master serial stream rather than opening its own BLE
links.  It does not reconfigure the rig -- it only times the flip and logs TR.

PREREQUISITE
------------
A wand TR session must already be streaming all 3 tags.  If `--preflight-s` sees
no TR, start one first, e.g.:

    python3 scripts_reserve_nomore_change/run_recv_tdma_capture.py \
        --caliwand-mode --targets BS9336,BS955A,BSCCF4 \
        --duration 400 --skip-anchor-preflight --reuse-tag-links

then run this script in a second terminal.

OUTPUTS (under experiments/antenna_flip_180/)
    raw_tr.log     every TR line, prefixed  "<elapsed_s>\t<wall_iso>\t<line>"
    metadata.json  phase offsets, orientations, resolved port, per-tag TR counts
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

try:
    import serial
    from serial import SerialException
except ImportError:  # pragma: no cover
    sys.exit("pyserial required: pip install pyserial --break-system-packages")

HERE = os.path.dirname(os.path.abspath(__file__))

# Master CDC console glob candidates, most-specific first.  The BioSpur BLE
# Control CDC is the current wand master; Master_Tag / BioSpur Central are the
# historical names for the same central role.
PORT_GLOBS = [
    "/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_*-if00",
    "/dev/serial/by-id/usb-Master_Tag_*-if00",
    "/dev/serial/by-id/usb-BioSpur_BioSpur_Central_*-if00",
]

# "<...>BSxxxx notify: TR;..." -- we only need the tag name to route counts; the
# analysis script does the full field parse.  Kept deliberately loose.
TR_LINE_RE = re.compile(r"(?P<name>BS[0-9A-Fa-f]{4}) notify: TR;")

DEFAULT_TARGETS = ["BS9336", "BS955A", "BSCCF4"]


def resolve_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    for pattern in PORT_GLOBS:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    sys.exit(
        "No master serial port found. Looked for:\n  "
        + "\n  ".join(PORT_GLOBS)
        + "\nPass --port /dev/serial/by-id/... explicitly."
    )


def open_serial_with_retry(port: str, baud: int, retries: int = 240,
                           settle_s: float = 0.25) -> "serial.Serial":
    last = None
    for _ in range(retries):
        try:
            # Plain open (no DTR toggling): the B120 CDC console tolerates it and
            # keeps its resident session -- verified it does NOT reset the master.
            return serial.Serial(port, baud, timeout=0.2)
        except (SerialException, OSError) as exc:
            last = exc
            time.sleep(settle_s)
    raise last if last is not None else RuntimeError("serial open failed")


def bar(frac: float, width: int = 30) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "#" * filled + "." * (width - filled)


def banner(lines: list[str]) -> None:
    w = max(len(s) for s in lines) + 4
    print("\n" + "=" * w)
    for s in lines:
        print("  " + s)
    print("=" * w + "\n", flush=True)


def run_segment(ser, port, baud, targets, base_offset, duration, label,
                orient_lines, raw_log_path, append):
    """Capture ONE phase segment (manual staging). Writes lines with elapsed =
    base_offset + segment_time so both segments share one aligned timeline
    (phase1 -> 0..p1, phase2 -> 140..260). Returns (ser, counts, tr_total, reconnects, complete)."""
    counts = {t: 0 for t in targets}
    tr_total = 0
    reconnects = 0
    banner([f">>> {label.upper()} CAPTURING -- DO NOT TOUCH <<<"] + orient_lines
            + [f"hold still for {duration:.0f}s"])
    t0 = time.monotonic()
    last_status = -1.0
    pending = ""
    mode = "a" if append else "w"
    log = open(raw_log_path, mode, encoding="utf-8")
    try:
        while True:
            el = time.monotonic() - t0
            if el >= duration:
                break
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except (SerialException, OSError) as exc:
                reconnects += 1
                log.write(f"# [RECONNECT {reconnects}] seg={label} t={base_offset + el:.3f} {exc}\n")
                log.flush()
                print(f"\n[reconnect {reconnects}] serial dropped at seg-t={el:.1f}s: {exc}; reopening...",
                      flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = open_serial_with_retry(port, baud)
                continue
            if chunk:
                pending += chunk.decode("utf-8", "replace")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line:
                        continue
                    m = TR_LINE_RE.search(line)
                    if not m:
                        continue
                    el2 = time.monotonic() - t0
                    log.write(f"{base_offset + el2:.3f}\t"
                              f"{datetime.now().isoformat(timespec='milliseconds')}\t{line}\n")
                    tr_total += 1
                    nm = m.group("name").upper()
                    if nm in counts:
                        counts[nm] += 1
                log.flush()
            if el - last_status >= 1.0:
                last_status = el
                frac = el / duration
                remain = duration - el
                tc = " ".join(f"{t.replace('BS',''):>4}:{counts[t]:4d}" for t in targets)
                sys.stdout.write(f"\r    {label} [{bar(frac)}] {remain:4.0f}s left  TR {tc}   ")
                sys.stdout.flush()
            if not chunk:
                time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n\n[interrupted] finalizing segment...", flush=True)
    finally:
        el = time.monotonic() - t0
        log.flush()
        log.close()
    complete = el >= duration
    return ser, counts, tr_total, reconnects, complete


def write_meta_file(meta_path, wall0, p1, buf, p2, targets, port, baud,
                    raw_log_path, seg_counts, complete_map):
    """Write/merge metadata.json. seg_counts: {tag: {phase1:n, phase2:n}} merged
    across segment runs; complete_map: {'phase1':bool,'phase2':bool}."""
    prev = {}
    if os.path.isfile(meta_path):
        try:
            prev = json.load(open(meta_path))
        except Exception:
            prev = {}
    counts = prev.get("tr_counts", {})
    for t, d in seg_counts.items():
        counts.setdefault(t, {})
        for ph, n in d.items():
            counts[t][ph] = n
    cmap = prev.get("segments_complete", {})
    cmap.update(complete_map)
    total = p1 + buf + p2
    meta = {
        "experiment": "antenna_flip_180",
        "date": prev.get("date") or wall0.isoformat(timespec="seconds"),
        "phase1": {"start_s": 0.0, "end_s": p1, "orientation": "9336_955A_face_ADHE"},
        "buffer": {"start_s": p1, "end_s": p1 + buf, "orientation": "turning_180"},
        "phase2": {"start_s": p1 + buf, "end_s": total, "orientation": "9336_955A_face_BCFG"},
        "wand_pose": "T-plane vertical, room center",
        "tags": targets,
        "note": "CCF4 antenna is mounted 180 deg opposite to 9336/955A",
        "capture_mode": "manual_segments",
        "port": port,
        "baud": baud,
        "raw_tr_log": os.path.basename(raw_log_path),
        "range_field": "raw (TR field 7), status=='O' only",
        "tr_counts": counts,
        "segments_complete": cmap,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None, help="Master CDC serial port (auto-detected if omitted)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--targets", default=",".join(DEFAULT_TARGETS),
                    help="Comma-separated BS names expected to stream TR")
    ap.add_argument("--phase1-s", type=float, default=120.0)
    ap.add_argument("--buffer-s", type=float, default=20.0)
    ap.add_argument("--phase2-s", type=float, default=120.0)
    ap.add_argument("--phase", choices=["both", "1", "2"], default="both",
                    help="'both' = automatic p1->buffer->p2 sequence; "
                         "'1'/'2' = capture ONE phase segment (manual staging). "
                         "Phase 1 truncates raw_tr.log; phase 2 appends with a +140s offset.")
    ap.add_argument("--preflight-s", type=float, default=15.0,
                    help="Seconds to confirm all target tags are streaming TR before starting")
    ap.add_argument("--allow-missing", action="store_true",
                    help="Proceed even if some target tags are not seen in preflight")
    ap.add_argument("--no-prompt", action="store_true",
                    help="Skip the ENTER-to-start prompt (start immediately after preflight)")
    ap.add_argument("--out-dir", default=HERE, help="Output directory")
    args = ap.parse_args()

    targets = [t.strip().upper() for t in args.targets.split(",") if t.strip()]
    port = resolve_port(args.port)
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    raw_log_path = os.path.join(out_dir, "raw_tr.log")
    meta_path = os.path.join(out_dir, "metadata.json")

    p1, buf, p2 = args.phase1_s, args.buffer_s, args.phase2_s
    total = p1 + buf + p2

    banner([
        "=== ANTENNA FLIP 180 deg EXPERIMENT ===",
        "",
        "Position wand: T-plane VERTICAL, room center",
        "9336 & 955A  ->  ADHE wall",
        "CCF4         ->  BCFG wall",
        "",
        f"port    : {port}",
        f"targets : {', '.join(targets)}",
        f"timing  : phase1={p1:.0f}s  buffer={buf:.0f}s  phase2={p2:.0f}s  total={total:.0f}s",
    ])

    ser = open_serial_with_retry(port, args.baud)

    # ---- preflight: confirm every target tag is actually streaming TR ----
    print(f">>> PREFLIGHT: listening {args.preflight_s:.0f}s for TR from {len(targets)} tags ...",
          flush=True)
    seen: dict[str, int] = {t: 0 for t in targets}
    other: dict[str, int] = {}
    pending = ""
    t_pf = time.monotonic()
    while time.monotonic() - t_pf < args.preflight_s:
        try:
            chunk = ser.read(ser.in_waiting or 1)
        except (SerialException, OSError):
            ser = open_serial_with_retry(port, args.baud)
            continue
        if not chunk:
            continue
        pending += chunk.decode("utf-8", "replace")
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            m = TR_LINE_RE.search(line)
            if not m:
                continue
            nm = m.group("name").upper()
            if nm in seen:
                seen[nm] += 1
            else:
                other[nm] = other.get(nm, 0) + 1
    for t in targets:
        state = "OK " if seen[t] > 0 else "-- "
        print(f"    [{state}] {t}: {seen[t]} TR", flush=True)
    if other:
        print(f"    (also heard non-target: {other})", flush=True)
    missing = [t for t in targets if seen[t] == 0]
    if missing and not args.allow_missing:
        sys.exit(
            f"\nMissing TR from {missing}. Is the wand session streaming all 3 tags?\n"
            "Start one (see this file's header) or pass --allow-missing to proceed anyway."
        )

    if not args.no_prompt:
        try:
            input("\nPress ENTER when ready to START the timed sequence... ")
        except (EOFError, KeyboardInterrupt):
            print("\naborted before start")
            return 1

    # ---- manual single-segment mode ----
    if args.phase in ("1", "2"):
        wall0 = datetime.now()
        if args.phase == "1":
            base, dur, label = 0.0, p1, "phase1"
            orient = ["9336 & 955A -> ADHE wall,  CCF4 -> BCFG wall"]
            append = False
        else:
            base, dur, label = p1 + buf, p2, "phase2"
            orient = ["9336 & 955A -> BCFG wall,  CCF4 -> ADHE wall"]
            append = True
        ser, seg_counts, tr_total, reconnects, complete = run_segment(
            ser, port, args.baud, targets, base, dur, label, orient, raw_log_path, append)
        write_meta_file(meta_path, wall0, p1, buf, p2, targets, port, args.baud,
                        raw_log_path, {t: {label: seg_counts[t]} for t in targets},
                        {label: complete})
        try:
            ser.close()
        except Exception:
            pass
        nxt = ("Now FLIP the wand 180 deg, then run:  capture.py --phase 2"
               if args.phase == "1" else
               "Both phases captured. Now run:  analyze.py")
        banner([f">>> {label.upper()} DONE <<<",
                f"complete : {complete}   TR lines: {tr_total}   reconnects: {reconnects}",
                f"raw log  : {raw_log_path} ({'append' if append else 'fresh'})",
                nxt])
        print("Per-tag TR counts this segment:")
        for t in targets:
            print(f"  {t}: {seg_counts[t]}")
        return 0 if complete else 2

    # ---- timed capture ----
    wall0 = datetime.now()
    t0 = time.monotonic()
    counts = {t: {"phase1": 0, "buffer": 0, "phase2": 0, "pre": 0, "post": 0} for t in targets}
    tr_total = 0
    reconnects = 0

    def phase_of(el: float) -> str:
        if el < p1:
            return "phase1"
        if el < p1 + buf:
            return "buffer"
        if el < total:
            return "phase2"
        return "post"

    # metadata written up-front so analysis has phase offsets even if interrupted
    def write_meta(complete: bool) -> None:
        meta = {
            "experiment": "antenna_flip_180",
            "date": wall0.isoformat(timespec="seconds"),
            "phase1": {"start_s": 0.0, "end_s": p1, "orientation": "9336_955A_face_ADHE"},
            "buffer": {"start_s": p1, "end_s": p1 + buf, "orientation": "turning_180"},
            "phase2": {"start_s": p1 + buf, "end_s": total, "orientation": "9336_955A_face_BCFG"},
            "wand_pose": "T-plane vertical, room center",
            "tags": targets,
            "note": "CCF4 antenna is mounted 180 deg opposite to 9336/955A",
            "port": port,
            "baud": args.baud,
            "raw_tr_log": os.path.basename(raw_log_path),
            "range_field": "raw (TR field 7), status=='O' only",
            "tr_counts": counts,
            "tr_total": tr_total,
            "reconnects": reconnects,
            "capture_complete": complete,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    write_meta(False)

    announced = ""
    last_status = 0.0
    pending = ""

    def announce(el: float) -> str:
        ph = phase_of(el)
        return ph

    banner([">>> PHASE 1 CAPTURING -- DO NOT TOUCH <<<",
            "9336 & 955A -> ADHE wall,  CCF4 -> BCFG wall",
            f"hold still for {p1:.0f}s"])
    cur_phase = "phase1"

    log = open(raw_log_path, "w", encoding="utf-8")
    try:
        while True:
            el = time.monotonic() - t0
            if el >= total:
                break

            # --- serial read (transparent reconnect) ---
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except (SerialException, OSError) as exc:
                reconnects += 1
                mark = f"# [RECONNECT {reconnects}] t={el:.3f} {exc}\n"
                log.write(mark)
                log.flush()
                print(f"\n[reconnect {reconnects}] serial dropped at t={el:.1f}s: {exc}; reopening...",
                      flush=True)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = open_serial_with_retry(port, args.baud)
                continue

            if chunk:
                pending += chunk.decode("utf-8", "replace")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line:
                        continue
                    m = TR_LINE_RE.search(line)
                    if not m:
                        continue
                    el2 = time.monotonic() - t0
                    ph = phase_of(el2)
                    log.write(f"{el2:.3f}\t{datetime.now().isoformat(timespec='milliseconds')}\t{line}\n")
                    tr_total += 1
                    nm = m.group("name").upper()
                    if nm in counts:
                        counts[nm][ph] = counts[nm].get(ph, 0) + 1
                log.flush()

            # --- phase-transition banners ---
            ph = phase_of(el)
            if ph != cur_phase:
                if ph == "buffer":
                    banner([">>> TURN 180 deg NOW  (buffer) <<<",
                            "9336 & 955A -> BCFG wall,  CCF4 -> ADHE wall",
                            f"you have {buf:.0f}s"])
                elif ph == "phase2":
                    banner([">>> PHASE 2 CAPTURING -- DO NOT TOUCH <<<",
                            "9336 & 955A -> BCFG wall,  CCF4 -> ADHE wall",
                            f"hold still for {p2:.0f}s"])
                cur_phase = ph

            # --- 1 Hz status line ---
            if el - last_status >= 1.0:
                last_status = el
                if cur_phase == "buffer":
                    remain = (p1 + buf) - el
                    sys.stdout.write(f"\r    >>> TURN 180 deg NOW <<<  phase 2 in {remain:4.0f}s   ")
                else:
                    if cur_phase == "phase1":
                        frac = el / p1
                        remain = p1 - el
                    else:
                        frac = (el - p1 - buf) / p2
                        remain = total - el
                    tc = " ".join(f"{t.replace('BS',''):>4}:{sum(counts[t].values()):4d}" for t in targets)
                    sys.stdout.write(f"\r    {cur_phase} [{bar(frac)}] {remain:4.0f}s left  TR {tc}   ")
                sys.stdout.flush()

            if not chunk:
                time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n\n[interrupted] finalizing what we have...", flush=True)
    finally:
        el = time.monotonic() - t0
        complete = el >= total
        write_meta(complete)
        log.flush()
        log.close()
        try:
            ser.close()
        except Exception:
            pass

    banner([">>> DONE <<<",
            f"elapsed  : {el:.0f}s / {total:.0f}s   complete={complete}",
            f"TR lines : {tr_total}   reconnects: {reconnects}",
            f"raw log  : {raw_log_path}",
            f"metadata : {meta_path}"])
    print("Per-tag TR counts by phase:")
    for t in targets:
        c = counts[t]
        print(f"  {t}: phase1={c['phase1']}  buffer={c['buffer']}  phase2={c['phase2']}")
    print("\nNext: python3 experiments/antenna_flip_180/analyze.py", flush=True)
    return 0 if el >= total else 2


if __name__ == "__main__":
    raise SystemExit(main())

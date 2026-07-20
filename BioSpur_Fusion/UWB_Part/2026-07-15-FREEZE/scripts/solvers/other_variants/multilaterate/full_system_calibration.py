#!/usr/bin/env python3
"""Full-system UWB calibration -- one command, zero human input, start to finish.

Positions EVERYTHING in the AutoPos anchor frame and emits a single system_config.json:
anchors (AutoPos v4-io), the 7 listeners (listener tag mode), and the 3 wand tags.

  PHASE 1  AutoPos: silence wand tags + idle all listeners, run the 8-anchor
           inter-anchor sweep (subprocess run_autopos_sweep_loop.py -- the same
           driver the Flutter UI shells out to), solve with the production v4-io
           chain, verify self-consistency RMS.
  PHASE 2  Listener positions: each listener -> MODE_TAG, 30 s of LTAG ranges,
           multilaterate against the Phase-1 layout.
  PHASE 3  Wand-tag capture: restore listeners to MODE_LISTEN, resume the wand
           tags, then capture 120 s of listener data (CIR + scalar) AND the
           Master_Tag TR stream concurrently.
  PHASE 4  Wand-tag positions: multilaterate each wand tag from its own per-anchor
           ranges in the TR stream (passive listeners carry NO range field -- an
           LPD line only proves the wand was HEARD, not how far; the wand's TR
           report is the only metric range source), then cross-check tag-to-tag
           distances against the caliper truth (670 / 660 / 709 mm).
  PHASE 5  System summary + system_config.json.

A phase that fails is logged and skipped; the pipeline continues where it can
(e.g. Phase 2 still runs on a >=6-anchor partial layout). --dry-run prints the
whole sequence without sending any state-changing command.

Reuses logs/listener_calibration/calibrate_listener_positions.py as the engine.
All outputs under logs/system_calibration_<timestamp>/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate_listener_positions as cal  # engine: Listener/MasterLink/solve/etc.

try:
    import numpy as np
except ImportError:
    sys.exit("numpy + scipy required: pip install numpy scipy")

REPO_ROOT = cal.REPO_ROOT

# Wand tags: BS name <-> on-air short address <-> caliper short label.
WAND_NAME_TO_ADDR = {"BSCCF4": 0xB1F4, "BS9336": 0xB136, "BS955A": 0xB15A}
WAND_SHORT = {"BSCCF4": "CCF4", "BS9336": "9336", "BS955A": "955A"}
WAND_NAMES = ["BSCCF4", "BS9336", "BS955A"]

# Rigid-wand caliper truth (mm) between the 3 tag antennas.
CALIPER_MM = {("CCF4", "9336"): 670.0, ("CCF4", "955A"): 660.0, ("9336", "955A"): 709.0}
CALIPER_TOL_MM = 50.0

LAYOUT_RMS_LIMIT_MM = 100.0     # Phase 1e self-consistency gate
LISTENER_RMS_LIMIT_MM = 200.0   # Phase 2d gate

# Master_Tag TR notification: "<...>BS9336 notify: TR;ver;sweep;plan;pmode;
# active_mask;valid_mask;raws;ranges;qs;statuses..." (subset of run_recv_tdma_capture
# TR_RANGE_RE). ranges/statuses align to the active_mask set bits (ascending id).
TR_RE = re.compile(
    r"(?P<name>BS[0-9A-Fa-f]+) notify: TR;"
    r"(?P<ver>[1234]);(?P<sweep>\d+);(?P<plan>[A-Za-z0-9_]+);(?P<pmode>\d+);"
    r"(?P<active_mask>[0-9A-Fa-f]+);(?P<valid_mask>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);(?P<ranges>\d+(?:,\d+)*);"
    r"(?P<qs>\d+(?:,\d+)*);(?P<statuses>[ORTEPL]+)"
)


def log(msg):
    print(msg, flush=True)


def mask_anchor_ids(mask_hex):
    m = int(mask_hex, 16)
    return [i for i in range(cal.UWB_MAX_ANCHORS if hasattr(cal, "UWB_MAX_ANCHORS") else 8)
            if m & (1 << i)]


def parse_tr_line(line):
    """Return (bs_name, {anchor_id: range_mm}) for OK anchors, or None."""
    mobj = TR_RE.search(line)
    if not mobj:
        return None
    ids = mask_anchor_ids(mobj.group("active_mask"))
    ranges = [int(x) for x in mobj.group("ranges").split(",")]
    statuses = mobj.group("statuses")
    out = {}
    for k, aid in enumerate(ids):
        if k < len(ranges) and k < len(statuses) and statuses[k] == "O":
            r = ranges[k]
            if cal.RANGE_MIN_MM <= r <= cal.RANGE_MAX_MM:
                out[aid] = r
    return mobj.group("name"), out


# ---------------------------------------------------------------------------
# Concurrent stream capture (Phase 3e)
# ---------------------------------------------------------------------------
def _reader_to_file(ser, fh, stop_evt, tr_sink=None):
    buf = b""
    while not stop_evt.is_set():
        try:
            c = ser.read(8192)
        except (OSError, cal.serial.SerialException):
            break
        if not c:
            time.sleep(0.002)
            continue
        buf += c
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", "replace").strip("\r")
            fh.write(line + "\n")
            if tr_sink is not None and "notify: TR;" in line:
                tr_sink.append(line)


def capture_all(listeners, master_link, out_raw_dir, duration_s):
    """Capture `duration_s` of every listener + the Master_Tag TR stream at once.
    Returns (per_listener_line_counts, tr_lines)."""
    os.makedirs(out_raw_dir, exist_ok=True)
    stop = threading.Event()
    threads, files = [], []
    tr_lines = []

    for lst in listeners.values():
        fh = open(os.path.join(out_raw_dir, f"{lst.name}.log"), "w")
        files.append(fh)
        t = threading.Thread(target=_reader_to_file, args=(lst.ser, fh, stop),
                             daemon=True)
        threads.append(t)
        t.start()
    if master_link is not None and master_link.ser is not None:
        fh = open(os.path.join(out_raw_dir, "wand_tr.log"), "w")
        files.append(fh)
        t = threading.Thread(target=_reader_to_file,
                             args=(master_link.ser, fh, stop, tr_lines), daemon=True)
        threads.append(t)
        t.start()

    time.sleep(duration_s)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)
    counts = {}
    for fh in files:
        fh.flush()
        fh.close()
    for lst in listeners.values():
        p = os.path.join(out_raw_dir, f"{lst.name}.log")
        with open(p) as f:
            counts[lst.name] = sum(1 for _ in f)
    return counts, tr_lines


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------
def phase1_autopos(ordered, out_dir, anchor_port, args):
    """Returns (layout_path, anchors_dict, rms_mm, ok)."""
    log("\n########## PHASE 1: AutoPos (anchors only) ##########")
    # 1a silence wand tags, 1b idle listeners
    cal.silence_wand_tags(ordered, args.wand_master)
    log("\n-- Phase 1b: idle all listeners --")
    for lst in ordered.values():
        lst.send("MODE_IDLE")
    if not cal.DRY_RUN:
        time.sleep(0.5)
        for lst in ordered.values():
            cal.expect_mode(lst, "IDLE")

    # 1c+1d sweep + solve
    log("\n-- Phase 1c/1d: AutoPos sweep + v4-io solve --")
    if args.skip_autopos:
        log("  --skip-autopos: using existing layout")
        layout_path = args.layout or cal.find_layout(None)
    elif anchor_port is None and not cal.DRY_RUN:
        log("  [warn] anchor master not found; using freshest existing layout")
        layout_path = args.layout or cal.find_layout(None)
    else:
        tmo = args.autopos_timeout or cal.autopos_timeout_for(args.sw_sets)
        produced = cal.run_autopos_v4io(anchor_port or "<anchor-master-port>",
                                        os.path.join(out_dir, "autopos"),
                                        args.sw_sets, tmo)
        layout_path = produced or args.layout or cal.find_layout(None)

    if cal.DRY_RUN:
        log(f"  [DRY-RUN] would verify layout + save {out_dir}/anchor_layout.json")
        return layout_path, {}, None, True

    # 1e verify
    if not layout_path or not os.path.exists(layout_path):
        log("  [FAIL] no layout produced or found")
        return None, {}, None, False
    anchors = cal.load_anchor_layout(layout_path)
    try:
        with open(layout_path) as f:
            rms = float(json.load(f).get("stats", {}).get("inter_anchor_pair_rms_mm", -1))
    except (ValueError, OSError):
        rms = -1.0
    log(f"  layout: {os.path.relpath(layout_path, REPO_ROOT)}  "
        f"anchors={len(anchors)}/8  self-consistency RMS={rms:.1f}mm")
    ok = len(anchors) >= 6
    if len(anchors) < 8:
        log(f"  [warn] only {len(anchors)}/8 anchors solved")
    if rms >= 0 and rms >= LAYOUT_RMS_LIMIT_MM:
        log(f"  [warn] self-consistency RMS {rms:.1f} >= {LAYOUT_RMS_LIMIT_MM:.0f}mm "
            f"(continuing; positions will inherit this error)")

    # 1f save canonical anchor_layout.json copy
    dst = os.path.join(out_dir, "anchor_layout.json")
    with open(layout_path) as s, open(dst, "w") as d:
        d.write(s.read())
    log(f"  saved {os.path.relpath(dst, REPO_ROOT)}")
    return layout_path, anchors, rms, ok


def phase2_listeners(ordered, anchors, out_dir, args):
    log("\n########## PHASE 2: listener positioning ##########")
    cal.RAW_DIR = os.path.join(out_dir, "raw")   # engine writes LTAG logs here
    results = {}
    for name in cal.LISTENER_ORDER:
        if name not in ordered:
            results[name] = {"tag_address": f"0x{cal.NAME_TO_LABEL[name]:04x}",
                             "status": "SKIPPED", "position_mm": None, "rms_mm": None}
            continue
        if cal.DRY_RUN:
            log(f"  [DRY-RUN] {name}: MODE_TAG -> 30s -> multilaterate -> MODE_IDLE")
            results[name] = {"tag_address": f"0x{cal.NAME_TO_LABEL[name]:04x}",
                             "status": "DRY_RUN", "position_mm": None, "rms_mm": None}
            continue
        try:
            results[name] = cal.solve_listener(ordered[name], anchors,
                                               args.capture_seconds, args.warmup_seconds)
        except Exception as exc:                       # noqa: BLE001 - never abort
            log(f"  [WARN] {name} positioning failed: {exc}")
            results[name] = {"tag_address": f"0x{cal.NAME_TO_LABEL[name]:04x}",
                             "status": "ERROR", "position_mm": None, "rms_mm": None}
    # 2d verify
    for name, r in results.items():
        if r.get("status") == "OK" and (r.get("rms_mm") or 0) > LISTENER_RMS_LIMIT_MM:
            log(f"  [warn] {name} RMS {r['rms_mm']}mm > {LISTENER_RMS_LIMIT_MM:.0f}mm")
    out = os.path.join(out_dir, "listener_positions.json")
    if not cal.DRY_RUN:
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        log(f"  saved {os.path.relpath(out, REPO_ROOT)}")
    return results


def _watch_lpd_presence(listeners, watch_s):
    """Return {listener: set(wand_addrs seen)} over watch_s in MODE_LISTEN."""
    seen = {n: set() for n in listeners}
    deadline = time.monotonic() + watch_s
    while time.monotonic() < deadline:
        for lst in listeners.values():
            for line in lst.read_lines(0.2):
                src = cal._lpd_src_addrs(line)
                if src in cal.WAND_TAG_ADDRS:
                    seen[lst.name].add(src)
    return seen


def phase3_capture(ordered, out_dir, wand_master, args):
    log("\n########## PHASE 3: wand-tag capture ##########")
    # 3a restore listeners to LISTEN
    log("-- Phase 3a: restore listeners to MODE_LISTEN --")
    for lst in ordered.values():
        lst.send("MODE_LISTEN")
    if cal.DRY_RUN:
        log("  [DRY-RUN] 3b verify CIR resumes; 3c cmd_all MODE RUN; 3d verify wand "
            "LPD; 3e capture %ds -> raw/{listener}.log + raw/wand_tr.log" % args.tag_capture_s)
        return {}, [], {}
    time.sleep(0.5)

    # 3b verify capture resumed (LCIRD for CIR builds, else LPD/LRD/LSTAT)
    for lst in ordered.values():
        resumed = any(l[:4] in ("LCIR", "LPD;", "LRD;", "LSTA")
                      for l in lst.read_lines(10.0))
        log(f"  3b {lst.name}: capture {'resumed' if resumed else 'NOT seen (warn)'}")

    # 3c resume wand tags
    log("-- Phase 3c: resume wand tags --")
    if not cal.wand_tags_command("enable", wand_master):
        log("  [warn] wand master not found; wand tags may already be running")

    # 3d verify wand tags visible (>=2 of 3 addrs on each listener within 10s)
    log("-- Phase 3d: verify wand tags visible --")
    seen = _watch_lpd_presence(ordered, 10.0)
    for name, addrs in seen.items():
        tag = "ok" if len(addrs) >= 2 else "WARN (<2 wand tags heard)"
        log(f"  3d {name}: {sorted(hex(a) for a in addrs)}  {tag}")

    # 3e concurrent 120 s capture: all listeners + Master_Tag TR stream
    log(f"-- Phase 3e: capturing {args.tag_capture_s}s of listener + wand-TR data --")
    tag_port = cal.find_master_port(cal.WAND_MASTER_GLOB, wand_master)
    mlink = cal.MasterLink("tag", tag_port)
    tr_lines = []
    try:
        mlink.open()               # for the TR stream (wand positioning source)
    except (OSError, cal.serial.SerialException) as exc:
        log(f"  [warn] Master_Tag open failed ({exc}); capturing listeners only, "
            f"no wand TR (Phase 4 will have no data)")
        mlink.ser = None
    try:
        counts, tr_lines = capture_all(ordered, mlink, os.path.join(out_dir, "raw"),
                                       args.tag_capture_s)
    finally:
        mlink.close()
    for name, n in counts.items():
        log(f"  3e {name}: {n} lines captured")
    log(f"  3e wand TR lines: {len(tr_lines)}")
    return counts, tr_lines, seen


def phase4_wands(tr_lines, anchors, out_dir):
    log("\n########## PHASE 4: wand-tag positioning ##########")
    if cal.DRY_RUN:
        log("  [DRY-RUN] would multilaterate each wand from TR per-anchor ranges + "
            "caliper cross-check")
        return {}, {}
    # 4a aggregate per-wand per-anchor ranges (median over the capture)
    per_wand = {n: {} for n in WAND_NAMES}
    for line in tr_lines:
        parsed = parse_tr_line(line)
        if not parsed:
            continue
        name, ranges = parsed
        if name not in per_wand:
            continue
        for aid, r in ranges.items():
            per_wand[name].setdefault(aid, []).append(r)

    # 4b multilaterate each wand
    wand_pos = {}
    for name in WAND_NAMES:
        med = {a: float(np.median(v)) for a, v in per_wand[name].items() if v}
        n_obs = sum(len(v) for v in per_wand[name].values())
        pos, rms, n_used = cal.multilaterate(anchors, med)
        entry = {"on_air_address": f"0x{WAND_NAME_TO_ADDR[name]:04x}",
                 "n_range_obs": n_obs, "anchors_used": n_used, "data_source": "wand_tr"}
        if pos is not None:
            entry.update(position_mm=[round(float(x), 1) for x in pos],
                         rms_mm=round(rms, 1), status="OK")
            log(f"  {name}: ({pos[0]:.0f},{pos[1]:.0f},{pos[2]:.0f})mm  "
                f"RMS={rms:.0f}mm  anchors={n_used}  obs={n_obs}")
        else:
            entry.update(position_mm=None, rms_mm=None,
                         status="INSUFFICIENT" if n_obs else "NO_DATA")
            log(f"  [WARN] {name}: {entry['status']} (anchors_with_range={len(med)})")
        wand_pos[name] = entry

    # 4c caliper cross-check (tag-to-tag distance via wand positions)
    caliper = {}
    short_pos = {WAND_SHORT[n]: wand_pos[n].get("position_mm") for n in WAND_NAMES}
    for (a, b), truth in CALIPER_MM.items():
        pa, pb = short_pos.get(a), short_pos.get(b)
        if pa and pb:
            meas = float(np.linalg.norm(np.array(pa) - np.array(pb)))
            caliper[f"{a}_{b}"] = {"measured_mm": round(meas, 1), "caliper_mm": truth,
                                   "delta_mm": round(meas - truth, 1),
                                   "pass": abs(meas - truth) <= CALIPER_TOL_MM}
            v = caliper[f"{a}_{b}"]
            log(f"  caliper {a}-{b}: measured={v['measured_mm']}mm truth={truth}mm "
                f"delta={v['delta_mm']}mm {'PASS' if v['pass'] else 'FAIL'}")
        else:
            caliper[f"{a}_{b}"] = {"measured_mm": None, "caliper_mm": truth,
                                   "delta_mm": None, "pass": None}
    out = os.path.join(out_dir, "wand_positions.json")
    with open(out, "w") as f:
        json.dump({"wand_tags": wand_pos, "caliper_check": caliper}, f, indent=2)
    log(f"  saved {os.path.relpath(out, REPO_ROOT)}")
    return wand_pos, caliper


def phase5_summary(out_dir, ts, layout_path, anchors, layout_rms, listener_res,
                   wand_pos, caliper, capture_counts):
    log("\n########## PHASE 5: system summary ##########")
    n_anchor = len(anchors)
    listener_ok = {k: v for k, v in listener_res.items() if v.get("status") == "OK"}
    l_rms = [v["rms_mm"] for v in listener_ok.values() if v.get("rms_mm") is not None]
    wand_ok = {k: v for k, v in wand_pos.items() if v.get("status") == "OK"}
    caliper_pass = all(v.get("pass") for v in caliper.values() if v.get("pass") is not None) \
        and any(v.get("pass") is not None for v in caliper.values())

    # channel matrix: CIR channels = listeners whose capture emitted LCIRD; scalar =
    # listeners emitting any passive ranging line.
    cir_ch = sum(1 for n, c in capture_counts.items() if c > 0
                 and _log_has(out_dir, n, "LCIRD"))
    scalar_ch = sum(1 for n, c in capture_counts.items() if c > 0)

    ready = (n_anchor == 8 and len(listener_ok) == 7 and len(wand_ok) == 3)

    anchor_block = {}
    for aid, p in sorted(anchors.items()):
        anchor_block["ABCDEFGH"[aid]] = {"id": aid, "position_mm": [round(float(x), 1) for x in p]}

    cfg = {
        "timestamp": ts,
        "anchor_layout": {"source": os.path.relpath(layout_path, REPO_ROOT) if layout_path else None,
                          "n_solved": n_anchor, "self_consistency_rms_mm": layout_rms,
                          "anchors": anchor_block},
        "listener_positions": {k: {"position_mm": v.get("position_mm"),
                                   "rms_mm": v.get("rms_mm"), "status": v.get("status")}
                               for k, v in listener_res.items()},
        "wand_tag_positions": {k: {"position_mm": v.get("position_mm"),
                                   "rms_mm": v.get("rms_mm"), "status": v.get("status")}
                               for k, v in wand_pos.items()},
        "wand_caliper_check": caliper,
        "channel_matrix": {"cir_channels": cir_ch, "scalar_channels": scalar_ch,
                           "all_positions_known": ready},
        "system_ready": ready,
    }
    out = os.path.join(out_dir, "system_config.json")
    with open(out, "w") as f:
        json.dump(cfg, f, indent=2)

    med_rms = round(float(np.median(l_rms)), 1) if l_rms else None
    rms_str = f", RMS = {layout_rms:.0f}mm" if (layout_rms and layout_rms >= 0) else ""
    log("\n" + "=" * 60)
    log("SYSTEM CALIBRATION SUMMARY")
    log("=" * 60)
    log(f"  Anchor layout:      {n_anchor}/8 solved{rms_str}")
    log(f"  Listener positions: {len(listener_ok)}/7 solved, median RMS = "
        f"{med_rms if med_rms is not None else 'n/a'}mm")
    log(f"  Wand positions:     {len(wand_ok)}/3 solved, caliper check "
        f"{'PASS' if caliper_pass else 'FAIL'}")
    log(f"  Channels:           {cir_ch} CIR + {scalar_ch} scalar")
    log(f"  SYSTEM READY:       {'YES' if ready else 'NO'}")
    log("=" * 60)
    log(f"  {os.path.relpath(out, REPO_ROOT)}")
    return cfg


def _log_has(out_dir, name, token):
    p = os.path.join(out_dir, "raw", f"{name}.log")
    if not os.path.exists(p):
        return False
    with open(p) as f:
        for line in f:
            if line.startswith(token):
                return True
    return False


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sw-sets", type=int, default=250, help="AutoPos sets per round")
    ap.add_argument("--tag-capture-s", type=float, default=120.0, help="wand-capture seconds")
    ap.add_argument("--capture-seconds", type=float, default=30.0, help="per-listener tag capture")
    ap.add_argument("--warmup-seconds", type=float, default=5.0)
    ap.add_argument("--autopos-timeout", type=int, default=None)
    ap.add_argument("--skip-autopos", action="store_true", help="skip Phase 1; use --layout")
    ap.add_argument("--layout", help="existing anchor layout for --skip-autopos")
    ap.add_argument("--ports", help="comma-separated listener ports (else auto-scan)")
    ap.add_argument("--wand-master", help="wand-tag master port (else auto)")
    ap.add_argument("--anchor-master", help="anchor master port (else auto)")
    ap.add_argument("--dry-run", action="store_true", help="print sequence; change nothing")
    args = ap.parse_args()

    cal.DRY_RUN = args.dry_run
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REPO_ROOT, "logs", f"system_calibration_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    if cal.DRY_RUN:
        log("=== DRY RUN: no state-changing command will be sent "
            "(MODE_QUERY discovery probes are read-only). ===\n")
    log(f"Output dir: {os.path.relpath(out_dir, REPO_ROOT)}")

    # discover
    log("\n== Discover listeners ==")
    listeners = cal.discover_listeners(args.ports.split(",") if args.ports else None)
    if not listeners:
        sys.exit("No listeners found. Is the firmware flashed?")
    ordered = {n: listeners[n] for n in cal.LISTENER_ORDER if n in listeners}
    log(f"Found {len(ordered)}/7 listeners: {list(ordered)}")
    anchor_port = cal.find_master_port(cal.MASTER_ANCHOR_GLOB, args.anchor_master)
    wand_port = cal.find_master_port(cal.WAND_MASTER_GLOB, args.wand_master)
    log(f"anchor master: {anchor_port or '(not found)'}")
    log(f"wand master:   {wand_port or '(not found)'}")

    layout_path = anchors = layout_rms = None
    listener_res, wand_pos, caliper, counts = {}, {}, {}, {}

    try:
        layout_path, anchors, layout_rms, ok1 = phase1_autopos(ordered, out_dir,
                                                               anchor_port, args)
    except Exception as exc:                          # noqa: BLE001
        log(f"[PHASE 1 ERROR] {exc}; continuing with freshest existing layout")
        layout_path = args.layout or cal.find_layout(None)
        anchors = cal.load_anchor_layout(layout_path) if (layout_path and not cal.DRY_RUN) else {}

    if not cal.DRY_RUN and not anchors:
        anchors = cal.load_anchor_layout(layout_path) if layout_path and os.path.exists(layout_path) else {}
    if not cal.DRY_RUN and len(anchors) < 4:
        log("[abort-phase2/4] fewer than 4 anchors known -- cannot multilaterate anything.")
    else:
        try:
            listener_res = phase2_listeners(ordered, anchors, out_dir, args)
        except Exception as exc:                      # noqa: BLE001
            log(f"[PHASE 2 ERROR] {exc}")

    try:
        counts, tr_lines, _ = phase3_capture(ordered, out_dir, args.wand_master, args)
    except Exception as exc:                          # noqa: BLE001
        log(f"[PHASE 3 ERROR] {exc}")
        counts, tr_lines = {}, []

    try:
        if not cal.DRY_RUN and len(anchors) >= 4:
            wand_pos, caliper = phase4_wands(tr_lines, anchors, out_dir)
        elif cal.DRY_RUN:
            phase4_wands([], {}, out_dir)
    except Exception as exc:                          # noqa: BLE001
        log(f"[PHASE 4 ERROR] {exc}")

    if not cal.DRY_RUN:
        try:
            phase5_summary(out_dir, ts, layout_path, anchors or {}, layout_rms,
                           listener_res, wand_pos, caliper, counts)
        except Exception as exc:                      # noqa: BLE001
            log(f"[PHASE 5 ERROR] {exc}")
    else:
        log("\n[DRY-RUN] would write system_config.json + print the one-page summary")

    for lst in ordered.values():
        lst.ser.close()
    if cal.DRY_RUN:
        log("\n=== DRY RUN complete: nothing was changed. ===")


if __name__ == "__main__":
    main()

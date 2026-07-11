#!/usr/bin/env python3
"""Re-capture wand tag positions after the wand was physically rotated on its mount.

Anchors and listeners have NOT moved -> reuse the existing calibration layout.
Only the 3 wand tags moved, and their on-air addresses changed after the
calibration's tag restore (identity-derived 0xb136/5a/f4 -> slot-sequential
0xb102/03/04).  This script, from ONE stationary capture:

  1c. Determines the name<->address mapping FROM LIVE DATA (no assumption):
      each named tag's position (from its TR per-anchor ranges) gives its
      distance to every listener; the on-air address a listener hears with the
      strongest first-path power is the NEAREST tag.  Matched across all 7
      listeners (rank concordance) + cross-checked against the roster-order
      convention.  The master never prints the on-air address, so this
      correlation is the only live-data route.
  1d/1e. Multilaterates EACH wand tag INDEPENDENTLY from its own TR per-anchor
      ranges against the existing anchor layout.  No rigid-body constraint, no
      caliper, no pairwise check -- 3 independent tags, period.

Outputs:
  wand_address_map.json
  wand_positions_updated.json
  mapping_evidence.json   (power/distance table + all 6 permutation scores)
  tr_capture.log          (raw TR lines, provenance)
"""
from __future__ import annotations
import glob
import itertools
import json
import math
import os
import re
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "logs", "listener_calibration"))
import calibrate_listener_positions as cal  # noqa: E402  (multilaterate, load_anchor_layout, constants)

CAL_DIR = os.path.join(REPO, "logs", "system_calibration_20260710_233443")
ANCHOR_LAYOUT = os.path.join(CAL_DIR, "anchor_layout.json")
LISTENER_POS = os.path.join(CAL_DIR, "listener_positions.json")

# Listener SNR map (verified 2026-07-11, all MODE_LISTEN).
LISTENER_SNR = {
    "LB": "760184545", "LE": "760184767", "LF": "760184964", "LA": "760184753",
    "LCCF4": "760184784", "L9336": "760186071", "L955A": "760186081",
}
LISTENER_BAUD = 460800
MASTER_TAG_BAUD = 115200

# TR notify: "<..>BSxxxx notify: TR;ver;sweep;plan;pmode;active_mask;valid_mask;
# raws;ranges;qs;statuses..."  ranges/statuses align to active_mask set bits.
TR_RE = re.compile(
    r"(?P<name>BS[0-9A-Fa-f]+) notify: TR;"
    r"(?P<ver>[1234]);(?P<sweep>\d+);(?P<plan>[A-Za-z0-9_]+);(?P<pmode>\d+);"
    r"(?P<active_mask>[0-9A-Fa-f]+);(?P<valid_mask>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);(?P<ranges>\d+(?:,\d+)*);"
    r"(?P<qs>\d+(?:,\d+)*);(?P<statuses>[ORTEPL]+)"
)
MAX_ANCHORS = 8
# Roster order used by the calibration resume (wand_tags_command "enable").
# NOT trusted as ground truth -- only a cross-check for the empirical result.
ROSTER_CONVENTION = {"BS9336": "0xb102", "BS955A": "0xb103", "BSCCF4": "0xb104"}


def log(m):
    print(m, flush=True)


def mask_ids(mask_hex):
    m = int(mask_hex, 16)
    return [i for i in range(MAX_ANCHORS) if m & (1 << i)]


def parse_tr(line):
    mo = TR_RE.search(line)
    if not mo:
        return None
    ids = mask_ids(mo.group("active_mask"))
    ranges = [int(x) for x in mo.group("ranges").split(",")]
    st = mo.group("statuses")
    out = {}
    for k, aid in enumerate(ids):
        if k < len(ranges) and k < len(st) and st[k] == "O":
            r = ranges[k]
            if cal.RANGE_MIN_MM <= r <= cal.RANGE_MAX_MM:
                out[aid] = r
    return mo.group("name"), out


# DW1000 device-time unit -> mm: c(mm/s) * 15.65ps.
MM_PER_TS_UNIT = 299702547000.0 * 15.65e-12   # ~4.69 mm/unit
TS_WRAP = 1 << 32                             # rx_ts is printed as 32-bit
SWEEP_MS = 15                                 # poll->response same-sweep window


def anchor_id_of(addr_hex):
    """0xa10X -> anchor id X (0..7), or None."""
    try:
        v = int(addr_hex, 16)
    except ValueError:
        return None
    if 0xA100 <= v <= 0xA1FF:
        aid = v & 0x0F
        return aid if aid <= 7 else None
    return None


# --------------------------------------------------------------------------
def capture(seconds, tr_log_path):
    """Concurrent read of Master_Tag + 7 listeners for `seconds`.

    Collects, per listener: geometric poll->response gaps
    gaps[listener][addr][anchor_id] = [Δmm]   (rx_ts(anchor resp) - rx_ts(poll)),
    and lpd_count[listener][addr] = #polls heard (co-location evidence).
    Returns (tr_ranges, gaps, lpd_count, tr_count, listener_ok)."""
    import serial

    ports = {}
    tag_glob = glob.glob("/dev/serial/by-id/usb-Master_Tag_*-if00")
    if not tag_glob:
        raise SystemExit("Master_Tag port not found")
    ports["__TAG__"] = (tag_glob[0], MASTER_TAG_BAUD)
    for name, snr in LISTENER_SNR.items():
        g = glob.glob(f"/dev/serial/by-id/usb-SEGGER_J-Link_000{snr}-if00")
        if g:
            ports[name] = (g[0], LISTENER_BAUD)
        else:
            log(f"  [warn] listener {name} port (SNR {snr}) not found -- skipped")

    tr_ranges = {}                        # name -> {aid: [mm,...]}
    gaps = {}                             # listener -> {addr: {anchor_id: [Δmm]}}
    lpd_count = {}                        # listener -> {addr: n}
    tr_count = [0]
    stop = threading.Event()
    lock = threading.Lock()
    handles = []
    tr_fh = open(tr_log_path, "w")

    def reader(name, dev, baud):
        try:
            ser = serial.Serial(dev, baud, timeout=0.2)
        except Exception as e:               # noqa: BLE001
            log(f"  [warn] open {name} failed: {e}")
            return
        handles.append(ser)
        buf = b""
        is_tag = (name == "__TAG__")
        recent = {}                          # addr -> (now_ms, rx_ts) most-recent poll
        while not stop.is_set():
            try:
                d = ser.read(8192)
            except Exception:                # noqa: BLE001
                break
            if not d:
                continue
            buf += d
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip("\r")
                if not line:
                    continue
                if is_tag:
                    if "notify: TR;" in line:
                        tr_fh.write(line + "\n")
                        r = parse_tr(line)
                        if r:
                            nm, d2 = r
                            with lock:
                                tr_count[0] += 1
                                dd = tr_ranges.setdefault(nm, {})
                                for aid, mm in d2.items():
                                    dd.setdefault(aid, []).append(mm)
                    continue
                if line.startswith("LPD;"):          # tag poll heard by listener
                    p = line.split(";")
                    if len(p) < 11 or p[9].lower() != "0xffff":
                        continue
                    try:
                        v = int(p[8], 16)
                    except ValueError:
                        continue
                    if not (0xB100 <= v <= 0xB1FF):
                        continue
                    addr = p[8].lower()
                    recent[addr] = (int(p[4]), int(p[10]))    # now_ms, rx_ts
                    with lock:
                        lpd_count.setdefault(name, {})[addr] = \
                            lpd_count.setdefault(name, {}).get(addr, 0) + 1
                elif line.startswith("LRD;"):        # anchor response to a tag poll
                    p = line.split(";")
                    if len(p) < 11:
                        continue
                    aid = anchor_id_of(p[8])
                    if aid is None:
                        continue
                    dst = p[9].lower()
                    pr = recent.get(dst)
                    if not pr:
                        continue
                    now_r, rx_r = int(p[4]), int(p[10])
                    if 0 <= now_r - pr[0] <= SWEEP_MS:         # same sweep
                        dlt = (rx_r - pr[1]) % TS_WRAP
                        if dlt < TS_WRAP // 2:                 # positive gap
                            with lock:
                                gaps.setdefault(name, {}).setdefault(dst, {}) \
                                    .setdefault(aid, []).append(dlt * MM_PER_TS_UNIT)

    threads = [threading.Thread(target=reader, args=(n, d, b), daemon=True)
               for n, (d, b) in ports.items()]
    for t in threads:
        t.start()
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(1.0)
        el = int(time.time() - t0)
        if el and el % 20 == 0:
            with lock:
                log(f"    [{el:3d}s] TR={tr_count[0]}  polls="
                    + " ".join(f"{ln}:{sum(lpd_count.get(ln, {}).values())}"
                               for ln in LISTENER_SNR))
    stop.set()
    for t in threads:
        t.join(timeout=2.0)
    for h in handles:
        try:
            h.close()
        except Exception:                    # noqa: BLE001
            pass
    tr_fh.close()
    listener_ok = [n for n in LISTENER_SNR if n in ports]
    return tr_ranges, gaps, lpd_count, tr_count[0], listener_ok


# --------------------------------------------------------------------------
def load_listener_positions(path):
    d = json.load(open(path))
    out = {}
    for name, v in d.items():
        if isinstance(v, dict) and v.get("position_mm"):
            out[name] = np.array([float(x) for x in v["position_mm"]])
    return out


def solve_positions(tr_ranges, anchors):
    """Independent multilateration per named tag from median per-anchor ranges."""
    positions = {}
    for name, per_anchor in sorted(tr_ranges.items()):
        med = {aid: float(np.median(v)) for aid, v in per_anchor.items() if v}
        pos, rms, n_used = cal.multilaterate(anchors, med)
        positions[name] = {
            "median_ranges_mm": {str(k): round(v, 1) for k, v in sorted(med.items())},
            "position_mm": None if pos is None else [round(float(x), 1) for x in pos],
            "rms_mm": None if rms is None else round(float(rms), 1),
            "n_ranges": int(n_used),
        }
    return positions


def determine_mapping(positions, gaps, lpd_count, min_gap_samples=5):
    """Geometric matcher (power-independent).  For each (listener, anchor) the
    poll->response gap Δ = d(tag,anchor) + reply_slot(anchor) + d(anchor,listener)
    - d(tag,listener).  Differencing two tags at the SAME (listener,anchor)
    cancels the fixed reply+anchor-listener terms, leaving
    Δ_i - Δ_j = [d(name_i,anchor) - d(name_j,anchor)] - C,  C an anchor-independent
    per-(pair,listener) constant.  Score each permutation by the residual of that
    identity against the TR-known per-anchor ranges (fit + remove C per pair)."""
    names = [n for n in positions if positions[n]["position_mm"]]
    addrs = sorted({a for d in gaps.values() for a in d})
    log(f"  addresses heard: {addrs}")
    log(f"  named tags solved: {names}")

    # median gap per (listener, addr, anchor)
    G = {}
    for ln, ad in gaps.items():
        G[ln] = {}
        for a, an in ad.items():
            G[ln][a] = {A: float(np.median(v)) for A, v in an.items()
                        if len(v) >= min_gap_samples}
    ranges = {n: positions[n]["median_ranges_mm"] for n in names}  # str-keyed

    def score(assign):
        tot, nc = 0.0, 0
        for ln in G:
            for ni, nj in itertools.combinations(names, 2):
                ai, aj = assign[ni], assign[nj]
                Ai, Aj = G[ln].get(ai, {}), G[ln].get(aj, {})
                common = [A for A in Ai if A in Aj
                          and str(A) in ranges[ni] and str(A) in ranges[nj]]
                if len(common) < 2:
                    continue
                M = np.array([Ai[A] - Aj[A] for A in common])
                P = np.array([ranges[ni][str(A)] - ranges[nj][str(A)] for A in common])
                C = float(np.mean(P - M))
                res = (P - C) - M
                tot += float(np.sum(res * res))
                nc += len(common)
        return (tot / nc if nc else 1e18), nc

    results = []
    for perm in itertools.permutations(addrs, len(names)):
        a = dict(zip(names, perm))
        s, nc = score(a)
        results.append((s, nc, a))
    results.sort(key=lambda x: x[0])
    best = results[0]
    mapping = best[2]
    ratio = (results[1][0] / best[0]) if best[0] > 0 else float("inf")

    # co-location evidence: at listener L<tag>, which address is heard most?
    coloc = {}
    for ln, d in lpd_count.items():
        if d:
            top = max(d, key=d.get)
            coloc[ln] = {"top_address": top, "counts": dict(sorted(d.items()))}

    agree = (mapping == {n: ROSTER_CONVENTION.get(n) for n in mapping})
    evidence = {
        "method": "geometric poll->response rx_ts gap matched to TR per-anchor "
                  "ranges (power-independent); anchor reply-slot term cancels via "
                  "tag differencing",
        "addresses_heard": addrs,
        "median_gap_mm": {ln: {a: {str(A): round(v, 1) for A, v in an.items()}
                               for a, an in ad.items()} for ln, ad in G.items()},
        "tr_median_ranges_mm": ranges,
        "permutation_scores": [
            {"mapping": p, "mean_sq_residual_mm2": round(s, 1), "n_constraints": nc}
            for s, nc, p in results
        ],
        "best_rms_mm": round(best[0] ** 0.5, 1),
        "runner_up_ratio": round(ratio, 2),
        "colocation_lpd_counts": coloc,
        "roster_convention": ROSTER_CONVENTION,
        "agrees_with_roster_convention": agree,
    }
    return mapping, evidence


# --------------------------------------------------------------------------
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--min-gap-samples", type=int, default=5)
    args = ap.parse_args()

    anchors = cal.load_anchor_layout(ANCHOR_LAYOUT)
    listener_pos = load_listener_positions(LISTENER_POS)
    log(f"anchors: {len(anchors)}   listeners w/ position: {len(listener_pos)}")

    tr_log = os.path.join(HERE, "tr_capture.log")
    log(f"== capturing {args.seconds:.0f}s (wand must be STATIONARY) ==")
    tr_ranges, gaps, lpd_count, tr_count, listener_ok = capture(args.seconds, tr_log)
    log(f"  TR reports: {tr_count}   listeners captured: {listener_ok}")

    positions = solve_positions(tr_ranges, anchors)
    for name, r in positions.items():
        log(f"  {name}: pos={r['position_mm']} rms={r['rms_mm']}mm "
            f"n_ranges={r['n_ranges']}")

    mapping, evidence = determine_mapping(positions, gaps, lpd_count,
                                          min_gap_samples=args.min_gap_samples)
    log(f"  MAPPING (geometric): {mapping}")
    log(f"  best_rms={evidence['best_rms_mm']}mm  runner_up_ratio="
        f"{evidence['runner_up_ratio']}x  agrees_with_roster="
        f"{evidence['agrees_with_roster_convention']}")
    log("  co-location LPD top-address per listener:")
    for ln, c in evidence["colocation_lpd_counts"].items():
        log(f"    {ln}: top={c['top_address']}  counts={c['counts']}")

    # ----- write outputs -----
    addr_map = []
    for name in sorted(positions):
        addr = mapping.get(name)
        addr_map.append({"tag_name": name, "address": addr})
    json.dump({"mapping": mapping,
               "by_tag": {m["tag_name"]: m["address"] for m in addr_map},
               "evidence": evidence},
              open(os.path.join(HERE, "wand_address_map.json"), "w"), indent=2)

    updated = []
    for name in sorted(positions):
        r = positions[name]
        updated.append({
            "tag_name": name,
            "address": mapping.get(name),
            "position_mm": r["position_mm"],
            "rms_mm": r["rms_mm"],
            "n_ranges": r["n_ranges"],
        })
    json.dump({"capture_seconds": args.seconds,
               "tr_reports": tr_count,
               "anchor_layout": os.path.relpath(ANCHOR_LAYOUT, REPO),
               "note": "3 independent tags; no rigid-body / caliper constraint",
               "tags": updated},
              open(os.path.join(HERE, "wand_positions_updated.json"), "w"), indent=2)
    json.dump(evidence, open(os.path.join(HERE, "mapping_evidence.json"), "w"), indent=2)
    log("  wrote wand_address_map.json, wand_positions_updated.json, mapping_evidence.json")


if __name__ == "__main__":
    main()

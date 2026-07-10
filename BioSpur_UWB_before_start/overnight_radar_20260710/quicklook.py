#!/usr/bin/env python3
"""Morning-after summary for the overnight 6-listener CIR capture.

Run this AFTER the overnight capture has stopped. Reads the 6 raw per-listener
log files (written as-is by capture_overnight.py, garbled lines included) and
produces a single report: per-listener health + a cross-listener channel
coverage matrix.

Firmware note baked into this script's design (SS-TWR/alt-SS-TWR/broadcast/
UWB_listener/src/main.c): CIR (LCIRM/LCIRD/LCIRE) is only captured for POLL
frames -- i.e. only for the 3 wand tags, never for the 8 anchors' SS-TWR
response frames (capture_to_ring() only calls print_full_cir() when
is_poll is true). So a "TX source x listener" matrix restricted to CIR
frames is naturally an all-zero-anchor-rows 11x6 table by firmware design,
not a fault. This script reports two separate matrices instead of forcing
that distinction into one: (1) ANY-FRAME reception coverage (LPD+LRD
combined, all 11 sources) -- the real "did every source reach every
listener" check -- and (2) CIR-frame coverage (poll/tag sources only,
derived by joining each LCIRM's accepted_polls counter against the LPD
record with the same seq_count in the same file, since LCIRM itself does
not carry the src address).
"""
import os
import re
import sys

RAW_DIR = os.environ.get("BIOSPUR_OVERNIGHT_OUT",
                        "/mnt/nrf_ssd/overnight_radar_20260709/raw")
REPORT_PATH = os.path.join(os.path.dirname(RAW_DIR.rstrip("/")),
                          "quicklook_report.txt")
NODE_ORDER = ["LB", "LE", "LF", "LCCF4", "L9336", "L955A"]
GAP_THRESHOLD_MS = 60_000

LPD_RE = re.compile(
    r'^LPD;1;(?P<id>\d+);(?P<near>\d+);(?P<now_ms>\d+);(?P<seq_count>\d+);'
    r'(?P<seq>\d+);(?P<peer_id>\d+);(?P<src>0x[0-9a-fA-F]{4});(?P<dst>0x[0-9a-fA-F]{4});'
    r'(?P<rx_ts>\d+);(?P<carrier>-?\d+);(?P<fp_index>\d+);(?P<fp1>\d+);(?P<fp2>\d+);'
    r'(?P<fp3>\d+);(?P<cir>\d+);(?P<rxpacc>\d+);(?P<stdnoise>\d+);(?P<frame_len>\d+);'
    r'(?P<mask>0x[0-9a-fA-F]{2});rcph=(?P<rcph>\d+);rxtofs=(?P<rxtofs>-?\d+);'
    r'ttcki=(?P<ttcki>\d+);agc=(?P<agc>\d+)$'
)
LRD_RE = re.compile(
    r'^LRD;1;(?P<id>\d+);(?P<near>\d+);(?P<now_ms>\d+);(?P<seq_count>\d+);'
    r'(?P<seq>\d+);(?P<peer_id>\d+);(?P<src>0x[0-9a-fA-F]{4});(?P<dst>0x[0-9a-fA-F]{4});'
    r'(?P<rx_ts>\d+);(?P<carrier>-?\d+);(?P<fp_index>\d+);(?P<fp1>\d+);(?P<fp2>\d+);'
    r'(?P<fp3>\d+);(?P<cir>\d+);(?P<rxpacc>\d+);(?P<stdnoise>\d+);(?P<frame_len>\d+);'
    r'rcph=(?P<rcph>\d+);rxtofs=(?P<rxtofs>-?\d+);ttcki=(?P<ttcki>\d+);agc=(?P<agc>\d+)$'
)
LSTAT_RE = re.compile(
    r'^LSTAT;1;(?P<id>\d+);(?P<near>\d+);(?P<good_frames>\d+);(?P<accepted_polls>\d+);'
    r'(?P<ignored_nonpoll>\d+);(?P<ignored_poll_mask>\d+);(?P<bad_header>\d+);'
    r'(?P<too_long>\d+);(?P<rx_errors>\d+);(?P<full_cir_captures>\d+);'
    r'(?P<last_status>0x[0-9a-fA-F]{8});(?P<last_src>0x[0-9a-fA-F]{4});'
    r'(?P<last_dst>0x[0-9a-fA-F]{4});(?P<last_code>0x[0-9a-fA-F]{2});(?P<ring_drops>\d+);'
    r'(?P<self_recover>\d+);(?P<rx_enable_failures>\d+);(?P<fps>\d+);'
    r'evc_fcg=(?P<evc_fcg>\d+);evc_fce=(?P<evc_fce>\d+);evc_ovr=(?P<evc_ovr>\d+);'
    r'evc_sto=(?P<evc_sto>\d+)$'
)
LCIRM_RE = re.compile(
    r'^LCIRM;1;(?P<id>\d+);(?P<near>\d+);(?P<accepted_polls>\d+);(?P<seq>\d+);'
    r'(?P<tag_id>\d+);(?P<mask>0x[0-9a-fA-F]{2});'
)
LCIRD_STRICT_RE = re.compile(r'^LCIRD;1;\d+;\d+;\d+;[0-9A-Fa-f]+$')
LCIRE_RE = re.compile(r'^LCIRE;1;(?P<accepted_polls>\d+);(?P<acc_len>\d+)$')


def analyze_listener(name, path):
    result = {
        "name": name, "path": path, "exists": os.path.exists(path),
        "total_lines": 0, "cir_lines": 0, "lpd_lines": 0, "lrd_lines": 0,
        "lstat_lines": 0, "lcirm_lines": 0, "lcird_well_formed": 0,
        "lcird_total": 0, "lcire_lines": 0,
        "src_seen": set(), "first_now_ms": None, "last_now_ms": None,
        "gaps": [], "last_lstat": None,
        "cir_src_by_accepted_polls": {},  # accepted_polls -> src (from LPD join)
        "cir_events_by_src": {},          # src -> count of complete LCIRM..LCIRE groups
    }
    if not result["exists"]:
        return result

    now_ms_stream = []
    lpd_seq_to_src = {}

    with open(path, "rb") as f:
        for raw in f:
            line = raw.decode(errors="replace").rstrip("\n").rstrip("\r")
            if not line:
                continue
            result["total_lines"] += 1
            if "LCIRD" in line:
                result["cir_lines"] += 1

            if line.startswith("LPD;1;"):
                m = LPD_RE.match(line)
                if m:
                    result["lpd_lines"] += 1
                    result["src_seen"].add(m.group("src"))
                    now_ms = int(m.group("now_ms"))
                    now_ms_stream.append(now_ms)
                    lpd_seq_to_src[m.group("seq_count")] = m.group("src")
            elif line.startswith("LRD;1;"):
                m = LRD_RE.match(line)
                if m:
                    result["lrd_lines"] += 1
                    result["src_seen"].add(m.group("src"))
                    now_ms_stream.append(int(m.group("now_ms")))
            elif line.startswith("LSTAT;1;"):
                m = LSTAT_RE.match(line)
                if m:
                    result["lstat_lines"] += 1
                    result["last_lstat"] = m.groupdict()
            elif line.startswith("LCIRM;1;"):
                m = LCIRM_RE.match(line)
                if m:
                    result["lcirm_lines"] += 1
                    src = lpd_seq_to_src.get(m.group("accepted_polls"))
                    if src:
                        result["cir_src_by_accepted_polls"][m.group("accepted_polls")] = src
            elif line.startswith("LCIRD;1;"):
                result["lcird_total"] += 1
                if LCIRD_STRICT_RE.match(line):
                    result["lcird_well_formed"] += 1
            elif line.startswith("LCIRE;1;"):
                m = LCIRE_RE.match(line)
                if m:
                    result["lcire_lines"] += 1
                    src = result["cir_src_by_accepted_polls"].get(m.group("accepted_polls"))
                    key = src if src else "unknown"
                    result["cir_events_by_src"][key] = result["cir_events_by_src"].get(key, 0) + 1

    if now_ms_stream:
        now_ms_stream.sort()
        result["first_now_ms"] = now_ms_stream[0]
        result["last_now_ms"] = now_ms_stream[-1]
        for a, b in zip(now_ms_stream, now_ms_stream[1:]):
            if b - a > GAP_THRESHOLD_MS:
                result["gaps"].append((a, b, b - a))

    return result


def fmt_duration_ms(ms):
    if ms is None:
        return "n/a"
    s = ms / 1000.0
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    return f"{h}h{m:02d}m{sec:02d}s"


def build_report(results):
    lines = []
    w = lines.append

    w("=" * 78)
    w("OVERNIGHT CIR CAPTURE -- QUICKLOOK REPORT")
    w("=" * 78)
    w("")

    all_src = set()
    for r in results.values():
        all_src |= r["src_seen"]

    w(f"Distinct TX sources seen across the fleet: {len(all_src)} (expect 11: 8 anchors + 3 tags)")
    w(f"  sources: {', '.join(sorted(all_src)) if all_src else '(none)'}")
    w("")

    w("-" * 78)
    w("PER-LISTENER SUMMARY")
    w("-" * 78)
    dead_listeners = []
    for name in NODE_ORDER:
        r = results[name]
        w(f"\n[{name}]  ({r['path']})")
        if not r["exists"]:
            w("  FILE MISSING -- this listener produced no log at all")
            dead_listeners.append(name)
            continue
        w(f"  total lines        : {r['total_lines']}")
        w(f"  scalar LPD lines   : {r['lpd_lines']}")
        w(f"  scalar LRD lines   : {r['lrd_lines']}")
        w(f"  LSTAT lines        : {r['lstat_lines']}")
        w(f"  CIR-tagged lines   : {r['cir_lines']}  "
          f"(LCIRM={r['lcirm_lines']} LCIRD={r['lcird_total']} LCIRE={r['lcire_lines']})")
        if r["lcird_total"] > 0:
            rate = 100.0 * r["lcird_well_formed"] / r["lcird_total"]
            w(f"  LCIRD parse rate   : {r['lcird_well_formed']}/{r['lcird_total']} well-formed ({rate:.1f}%)")
        else:
            w("  LCIRD parse rate   : n/a (no LCIRD lines)")
        w(f"  distinct TX src    : {len(r['src_seen'])}  {sorted(r['src_seen'])}")
        if not r["src_seen"]:
            dead_listeners.append(name)
        span = None
        if r["first_now_ms"] is not None:
            span = r["last_now_ms"] - r["first_now_ms"]
        w(f"  device-uptime span : first={r['first_now_ms']}ms last={r['last_now_ms']}ms "
          f"duration={fmt_duration_ms(span)}")
        if r["gaps"]:
            w(f"  GAPS >60s          : {len(r['gaps'])} found")
            for a, b, d in r["gaps"][:10]:
                w(f"      gap {d/1000:.1f}s  ({a}ms -> {b}ms)")
            if len(r["gaps"]) > 10:
                w(f"      ... and {len(r['gaps']) - 10} more")
        else:
            w("  GAPS >60s          : none")
        if r["last_lstat"]:
            s = r["last_lstat"]
            w(f"  last LSTAT         : good_frames={s['good_frames']} accepted_polls={s['accepted_polls']} "
              f"ring_drops={s['ring_drops']} self_recover={s['self_recover']} "
              f"rx_enable_failures={s['rx_enable_failures']}")
            w(f"  last EVC counters  : evc_fcg={s['evc_fcg']} evc_fce={s['evc_fce']} "
              f"evc_ovr={s['evc_ovr']} evc_sto={s['evc_sto']}  "
              f"(12-bit HW counters, wrap at 4095 -- these are a snapshot, not a night total)")
        else:
            w("  last LSTAT         : none found")

    w("")
    w("-" * 78)
    w("CROSS-LISTENER CHANNEL MATRIX 1/2 -- ANY-FRAME reception coverage (LPD+LRD, all 11 sources)")
    w("-" * 78)
    src_cols = sorted(all_src)
    header = "  RX\\TX".ljust(9) + "".join(s[2:].rjust(8) for s in src_cols)
    w(header)
    silent_sources = set(all_src)
    for name in NODE_ORDER:
        r = results[name]
        row = name.ljust(9)
        for s in src_cols:
            hit = "Y" if s in r["src_seen"] else "."
            if s in r["src_seen"]:
                silent_sources.discard(s)
            row += hit.rjust(8)
        w(row)
    w("")
    if silent_sources:
        w(f"  TX sources seen by 0 listeners (silent all night): {sorted(silent_sources)}")
    else:
        w("  Every observed TX source was heard by at least one listener.")
    if dead_listeners:
        w(f"  Listeners that saw 0 TX sources all night (DEAD): {dead_listeners}")
    else:
        w("  Every listener heard at least one TX source.")

    w("")
    w("-" * 78)
    w("CROSS-LISTENER CHANNEL MATRIX 2/2 -- CIR-frame coverage (poll/tag sources only -- see")
    w("module docstring: CIR is only captured for poll frames, so anchors are structurally 0)")
    w("-" * 78)
    cir_src_cols = sorted({s for r in results.values() for s in r["cir_events_by_src"] if s != "unknown"})
    if cir_src_cols:
        header = "  RX\\TX".ljust(9) + "".join(s[2:].rjust(8) for s in cir_src_cols)
        w(header)
        for name in NODE_ORDER:
            r = results[name]
            row = name.ljust(9)
            for s in cir_src_cols:
                row += str(r["cir_events_by_src"].get(s, 0)).rjust(8)
            unk = r["cir_events_by_src"].get("unknown", 0)
            if unk:
                row += f"   (+{unk} unresolved-src CIR events)"
            w(row)
    else:
        w("  No CIR events with a resolvable source were found.")

    w("")
    w("=" * 78)
    return "\n".join(lines)


def main():
    results = {name: analyze_listener(name, os.path.join(RAW_DIR, f"{name}.log"))
               for name in NODE_ORDER}
    report = build_report(results)
    print(report)
    try:
        with open(REPORT_PATH, "w") as f:
            f.write(report + "\n")
        print(f"\n[saved to {REPORT_PATH}]")
    except OSError as e:
        print(f"\n[ERROR] could not save report to {REPORT_PATH}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

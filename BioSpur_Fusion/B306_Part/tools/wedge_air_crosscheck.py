#!/usr/bin/env python3
"""Cross-check BLE silence against UWB tag activity, using the listener array.

WHY THIS EXISTS
---------------
The listener array has been running alongside every fleet run in this campaign
and was never used to diagnose the wedge. That was a real miss: the listeners are
an observer OUTSIDE the subsystem under test, which is exactly what v43 and v44
lacked -- both put every mark inside the Bluetooth host, so neither could see a
fault below it.

A B306 that has wedged keeps its UWB tag transmitting; a B306 that has lost power
does not. So one question separates the two, and the data to answer it was
already on disk:

    at the moment BLE went silent, was that board's tag still on the air?

    tag still transmitting  -> WEDGE     (board alive, .noinit intact, SWD-readable)
    tag stopped too         -> POWER LOSS (brownout/depletion, .noinit gone)

CLOCK ALIGNMENT
---------------
Each listener stamps records with its own free-running `listener_t_ms`. The
.jsonl carries `arrival_epoch_ns` but is 3-6 GB per listener, far too big to
scan. Instead the first and last .jsonl records give two (t_ms, epoch) pairs per
listener, which fix a linear map; the 500 MB raw log is then scanned with that
map applied. Both clocks are real-time, so over an 8 h run the residual is a few
seconds -- irrelevant against the minutes-scale question being asked.

POLL COUNTS ARE NOT COMPARABLE BETWEEN TAGS. They vary by an order of magnitude
with listener geometry (1422 vs 62813 in the same window). Only presence/absence
and last-seen are used.
"""
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

BUCKET_S = 60
BEFORE_S = 300      # window before the silence event
AFTER_S = 600       # window after -- long enough to outlast a reconnect flap


def listener_clock(jsonl):
    """Two (t_ms, epoch_s) samples -> (slope, intercept) mapping t_ms to epoch."""
    def pick(line):
        try:
            o = json.loads(line)
        except Exception:
            return None
        # listener_t_ms lives under "fields" in the per-listener .jsonl and at
        # top level in merged_index.jsonl. Reading only the top level silently
        # yields None for every record, which calibrates nothing and -- if the
        # caller skips quietly -- prints a full table of INSUFFICIENT that looks
        # like a result instead of a failure. Accept both, and let the caller
        # raise if neither is present.
        t = o.get("listener_t_ms")
        if t is None:
            t = (o.get("fields") or {}).get("listener_t_ms")
        e = o.get("arrival_epoch_ns")
        return (t, e / 1e9) if (t and e) else None

    first = last = None
    with open(jsonl, "rb") as fh:
        for _ in range(400):
            l = fh.readline()
            if not l:
                break
            first = first or pick(l.decode("utf-8", "replace"))
        fh.seek(max(0, os.path.getsize(jsonl) - 400_000))
        fh.readline()
        for l in fh:
            p = pick(l.decode("utf-8", "replace"))
            if p:
                last = p
    if not (first and last) or last[0] == first[0]:
        return None
    slope = (last[1] - first[1]) / (last[0] - first[0])
    return slope, first[1] - slope * first[0]


def tag_map(fusion_glob):
    """node -> 0xb100 + logical, read from FUSION_UWB records."""
    m = {}
    for p in sorted(glob.glob(fusion_glob))[:1]:      # first hour is enough
        with open(p, "rb") as fh:
            for line in fh:
                if b"FUSION_UWB" not in line:
                    continue
                s = line.decode("utf-8", "replace")
                g = re.search(r"name=([A-Z0-9]+) .*?logical=(\d+)", s)
                if g and g.group(1) not in m:
                    m[g.group(1)] = 0xB100 + int(g.group(2))
                if len(m) >= 10:
                    return m
    return m


def air_timeline(listener_dir):
    """(tag -> {epoch_bucket: polls}) across every listener that hears polls."""
    hist = defaultdict(lambda: defaultdict(int))
    for raw in sorted(glob.glob(os.path.join(listener_dir, "listeners", "*.raw.log"))):
        js = raw.replace(".raw.log", ".jsonl")
        if not os.path.exists(js):
            continue
        # Two of the seven units are not poll receivers at all -- one transmits
        # the beacon (LBTX only), one hears beacons (LBFAST/LBD only). Those
        # legitimately contribute nothing and are skipped WITH A NOTE.
        #
        # A unit that does carry LPD records but cannot be calibrated is a
        # different matter entirely and must be fatal: it would contribute zero
        # polls, and enough of those turn "the tool is broken" into "the tags
        # were never heard", which reads as evidence pointing the opposite way.
        n_lpd = int(subprocess.run(["grep", "-ac", "^LPD", raw],
                                   capture_output=True).stdout or b"0")
        if n_lpd == 0:
            print(f"    {os.path.basename(raw)}: no LPD records, "
                  f"not a poll receiver -- skipped", file=sys.stderr)
            continue
        cal = listener_clock(js)
        if cal is None:
            raise RuntimeError(f"clock calibration failed for {js} ({n_lpd} LPD "
                               f"records present) -- refusing to report a "
                               f"timeline that silently omits it")
        slope, inter = cal
        # grep is an order of magnitude faster than filtering in Python here
        pr = subprocess.Popen(["grep", "-a", "^LPD", raw], stdout=subprocess.PIPE)
        n = 0
        for bline in pr.stdout:
            f = bline.split(b";")
            if len(f) < 10:
                continue
            try:
                t_ms = int(f[4])
                src = int(f[8], 16)
            except ValueError:
                continue
            ep = slope * t_ms + inter
            hist[src][int(ep // BUCKET_S)] += 1
            n += 1
        pr.wait()
        print(f"    {os.path.basename(raw)}: {n} polls", file=sys.stderr)
    return hist


def polls(hist, tag, lo, hi):
    h = hist.get(tag, {})
    return sum(v for b, v in h.items() if lo // BUCKET_S <= b < hi // BUCKET_S)


def run(label, events, fusion_glob, listener_dir):
    print(f"\n{'='*78}\n{label}\n{'='*78}")
    tags = tag_map(fusion_glob)
    if not tags:
        print("  INSUFFICIENT: no node->tag mapping"); return []
    print(f"  tag map: " + ", ".join(f"{k}=0x{v:04x}" for k, v in sorted(tags.items())))
    print("  building air timeline...", file=sys.stderr)
    hist = air_timeline(listener_dir)

    ev = []
    for line in open(events):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("kind") == "DATA_PLANE_SILENT" and e.get("node"):
            ev.append(e)
    # one verdict per node per episode: collapse repeats inside 10 min
    seen = {}
    keep = []
    for e in ev:
        n = e["node"]
        ep = _epoch(e)
        if ep is None or (n in seen and ep - seen[n] < 600):
            if n in seen:
                seen[n] = ep
            continue
        seen[n] = ep
        keep.append((n, ep, e["wall"][11:19]))

    print(f"\n  {'node':9} {'silence':>9} {'polls -5min':>12} {'polls +10min':>13}  verdict")
    out = []
    for n, ep, wall in keep:
        tag = tags.get(n)
        if tag is None:
            print(f"  {n:9} {wall:>9} {'':>12} {'':>13}  INSUFFICIENT (no tag)")
            continue
        b = polls(hist, tag, ep - BEFORE_S, ep)
        a = polls(hist, tag, ep, ep + AFTER_S)
        # Normalise against what the SAME tag was doing before, scaled for the
        # unequal window lengths: ratio 1.0 means "transmitting exactly as much
        # as it was". Raw counts must never be compared between tags -- listener
        # geometry spreads them over an order of magnitude.
        #
        # The first cut of this test used `a >= 0.2 * b * 2`, which put a
        # brownout at 0.31 on the WEDGE side of the line and labelled a
        # power-cycling board a wedge -- the third time tonight that BSF6C53
        # fooled a too-loose criterion. The observed clusters are far apart
        # (0.94-1.09 / 0.31 / 0.12), so the bands can be strict.
        exp = b * (AFTER_S / BEFORE_S)
        ratio = (a / exp) if exp else None
        if b == 0:
            v = "INSUFFICIENT (tag unheard before)"
        elif ratio >= 0.70:
            v = f"WEDGE -- tag unaffected (ratio {ratio:.2f})"
        elif ratio >= 0.15:
            v = f"INTERMITTENT -- reboot cycling (ratio {ratio:.2f})"
        else:
            v = f"POWER LOSS -- tag dying (ratio {ratio:.2f})"
        print(f"  {n:9} {wall:>9} {b:12d} {a:13d}  {v}")
        out.append({"node": n, "wall": wall, "epoch": ep, "tag": f"0x{tag:04x}",
                    "polls_before": b, "polls_after": a, "verdict": v})
    return out


def _epoch(e):
    w = e.get("wall")
    if not w:
        return None
    import datetime
    try:
        return datetime.datetime.fromisoformat(w).timestamp()
    except Exception:
        return None


if __name__ == "__main__":
    R = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion"
    jobs = [
        ("N8  v44_fleet_20260807 (live)",
         f"{R}/UWB_Part/logs/v44_fleet_20260807/I_RUN/events.jsonl",
         f"{R}/UWB_Part/logs/v44_fleet_20260807/I_RUN/fusion_h0*.log",
         f"{R}/UWB_Part/logs/v44_fleet_20260807/I_LISTENERS"),
        ("N7  daylight_20260807",
         f"{R}/UWB_Part/logs/daylight_20260807/B_RUN/events.jsonl",
         f"{R}/UWB_Part/logs/daylight_20260807/B_RUN/fusion_h0*.log",
         f"{R}/UWB_Part/logs/daylight_20260807/B_LISTENERS"),
        ("N5  v43_selfcapture_20260807",
         f"{R}/B306_Part/logs/v43_selfcapture_20260807/B5_RUN/events.jsonl",
         f"{R}/B306_Part/logs/v43_selfcapture_20260807/B5_RUN/fusion_h0*.log",
         f"{R}/B306_Part/logs/v43_selfcapture_20260807/B5_LISTENERS"),
    ]
    allout = {}
    for label, ev, fg, ld in jobs:
        try:
            allout[label] = run(label, ev, fg, ld)
        except Exception as exc:
            print(f"\n{label}: FAILED {exc!r}")
    dest = f"{R}/UWB_Part/logs/v44_fleet_20260807/J_WEDGE/air_crosscheck.json"
    json.dump(allout, open(dest, "w"), indent=1)
    print(f"\nwrote {dest}")

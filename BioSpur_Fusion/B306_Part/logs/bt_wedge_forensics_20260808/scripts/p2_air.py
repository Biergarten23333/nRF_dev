#!/usr/bin/env python3
"""Independent recompute of the listener air timeline (§2.2 criterion 5).

Per run: for every UWB tag address, polls per BUCKET_S seconds of host epoch,
summed over every listener that actually receives polls. Written to
cache/air_<run>.parquet as (tag, bucket, n).

Clock: each listener free-runs `listener_t_ms`. The .jsonl carries
`arrival_epoch_ns` per record, so head+tail of the .jsonl fix a two-point
affine map t_ms -> epoch. Residual over a 6 h run is seconds; the questions
asked of this timeline are minutes-scale.

Guard, kept from the earlier tool because it caught a real inverted result:
a listener with zero LPD records is *not a poll receiver* and is skipped with
a note; a listener that HAS LPD records but cannot be calibrated is fatal,
because silently dropping it would look like "the tags were never heard".
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE  # noqa: E402

import pyarrow as pa            # noqa: E402
import pyarrow.parquet as pq    # noqa: E402

BUCKET_S = 10
TMS = re.compile(rb'"listener_t_ms":(\d+)')
EPO = re.compile(rb'"arrival_epoch_ns":(\d+)')


def pairs_from(chunk):
    out = []
    for line in chunk.split(b"\n"):
        a, b = TMS.search(line), EPO.search(line)
        if a and b:
            out.append((int(a.group(1)), int(b.group(1)) / 1e9))
    return out


def calibrate(jsonl):
    sz = os.path.getsize(jsonl)
    with open(jsonl, "rb") as fh:
        head = pairs_from(fh.read(1 << 21))
        if sz > (1 << 22):
            fh.seek(sz - (1 << 21))
            fh.readline()
            tail = pairs_from(fh.read())
        else:
            tail = head
    if not head or not tail:
        return None
    t0, e0 = head[0]
    t1, e1 = tail[-1]
    if t1 == t0:
        return None
    slope = (e1 - e0) / (t1 - t0)
    return slope, e0 - slope * t0, (t1 - t0) / 1000.0


def build(run):
    cfg = RUNS[run]
    ld = os.path.join(cfg["listeners"], "listeners")
    if not os.path.isdir(ld):
        print(f"{run}: INSUFFICIENT -- no listener dir {ld}")
        return
    hist = defaultdict(int)
    notes = []
    for raw in sorted(f for f in os.listdir(ld) if f.endswith(".raw.log")):
        rp = os.path.join(ld, raw)
        jp = rp.replace(".raw.log", ".jsonl")
        n_lpd = int(subprocess.run(["grep", "-ac", "^LPD", rp],
                                   capture_output=True).stdout or b"0")
        if n_lpd == 0:
            notes.append(f"{raw}: 0 LPD -- not a poll receiver, skipped")
            continue
        if not os.path.exists(jp):
            raise RuntimeError(f"{raw} has {n_lpd} LPD records but no .jsonl "
                               f"to calibrate against")
        cal = calibrate(jp)
        if cal is None:
            raise RuntimeError(f"clock calibration failed for {jp} "
                               f"({n_lpd} LPD present)")
        slope, inter, span = cal
        pr = subprocess.Popen(["grep", "-a", "^LPD", rp], stdout=subprocess.PIPE)
        n = 0
        for bl in pr.stdout:
            f = bl.split(b";")
            if len(f) < 10:
                continue
            try:
                t_ms = int(f[4]); src = int(f[8], 16)
            except ValueError:
                continue
            hist[(src, int((slope * t_ms + inter) // BUCKET_S))] += 1
            n += 1
        pr.wait()
        notes.append(f"{raw}: {n} polls, ppm={1e6*(slope-1e-3)/1e-3:+.0f}, span={span/3600:.2f}h")
    if not hist:
        print(f"{run}: INSUFFICIENT -- no polls decoded")
        return
    tags, bks, ns = zip(*[(k[0], k[1], v) for k, v in hist.items()])
    pq.write_table(pa.table({"tag": pa.array(tags, pa.int32()),
                             "bucket": pa.array(bks, pa.int64()),
                             "n": pa.array(ns, pa.int32())}),
                   os.path.join(CACHE, f"air_{run}.parquet"), compression="zstd")
    json.dump({"bucket_s": BUCKET_S, "notes": notes},
              open(os.path.join(CACHE, f"air_{run}_notes.json"), "w"), indent=1)
    print(f"{run}: {len(set(tags))} tags, {sum(ns)} polls")
    for x in notes:
        print("   ", x)


if __name__ == "__main__":
    for r in (sys.argv[1:] or ["N5", "N7", "N8"]):
        build(r)

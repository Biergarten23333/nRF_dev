#!/usr/bin/env python3
"""One streaming pass per run over fusion_h*.log -> parquet caches.

Never loads a whole file. Emits, per run:
  recs_<run>.parquet   IMU + UWB delivery records (the two data streams)
  tlm_<run>.parquet    FUSION_TELEMETRY, all counters, dynamic columns
  qos_<run>.parquet    FUSION_QOS (master-side link quality)
  que_<run>.parquet    FUSION_QUEUE (node-side publisher/queue counters)
  pol_<run>.parquet    FUSION_POOL (node-side net_buf pools, 8 columns)
  ctl_<run>.jsonl      everything else, verbatim key/values
  types_<run>.json     histogram of every record type seen, whole files

`channel.log` is deliberately not read (duplicate of fusion_h*, unrotated).
"""
import json
import os
import sys
import time
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, POOL_HASH, kv, num, fusion_logs  # noqa: E402

import pyarrow as pa            # noqa: E402
import pyarrow.parquet as pq    # noqa: E402

CHUNK = 400_000

REC_SCHEMA = pa.schema([
    ("node", pa.string()), ("kind", pa.int8()),
    ("t_host", pa.float64()), ("t_mono", pa.float64()),
    ("master_ms", pa.int64()), ("node_us", pa.int64()),
    ("seq", pa.int64()), ("aux", pa.int64()), ("nbyte", pa.int32()),
])
KIND = {"FUSION_IMU": 0, "FUSION_UWB": 1}

CTL_TYPES = {
    "FUSION_COMMAND_TX", "FUSION_COMMAND_REJECT", "FUSION_REPLY",
    "FUSION_HEALTH", "FUSION_SCAN_STARTED", "FUSION_SCAN_STOPPED",
    "FUSION_STALL_READ_START", "FUSION_STALL_READ", "FUSION_STALL_READ_DONE",
    "FUSION_STALL_POOLS", "FUSION_MASTER_POOL",
    "FUSION_RECONNECT_START", "FUSION_RECONNECT_DISCONNECTED",
    "FUSION_RECONNECT_DONE", "FUSION_CONNECTED", "FUSION_DISCONNECTED",
}


class ParquetSink:
    def __init__(self, path, schema=None):
        self.path = path
        self.schema = schema
        self.w = None
        self.rows = 0

    def write_table(self, tbl):
        if self.w is None:
            self.schema = tbl.schema
            self.w = pq.ParquetWriter(self.path, self.schema, compression="zstd")
        else:
            tbl = tbl.cast(self.schema) if tbl.schema != self.schema else tbl
        self.w.write_table(tbl)
        self.rows += tbl.num_rows

    def close(self):
        if self.w:
            self.w.close()


class DictSink:
    """Accumulates dicts with a union schema, flushes on close (1 Hz streams
    are small: <200 k rows even for the longest run)."""

    def __init__(self, path):
        self.path = path
        self.rows = []

    def add(self, d):
        self.rows.append(d)

    def close(self):
        if not self.rows:
            return 0
        cols = []
        for r in self.rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        data = {}
        for c in cols:
            vals = [r.get(c) for r in self.rows]
            if all(v is None or isinstance(v, (int, bool)) for v in vals):
                data[c] = pa.array(vals, type=pa.int64())
            elif all(v is None or isinstance(v, (int, float, bool)) for v in vals):
                data[c] = pa.array(vals, type=pa.float64())
            else:
                data[c] = pa.array([None if v is None else str(v) for v in vals],
                                   type=pa.string())
        pq.write_table(pa.table(data), self.path, compression="zstd")
        return len(self.rows)


def extract(run):
    cfg = RUNS[run]
    logs = fusion_logs(cfg["run"])
    if not logs:
        print(f"{run}: INSUFFICIENT -- no fusion_h*.log under {cfg['run']}")
        return
    t0 = time.time()
    types = collections.Counter()
    rec = ParquetSink(os.path.join(CACHE, f"recs_{run}.parquet"), REC_SCHEMA)
    tlm, qos, que, pol = (DictSink(os.path.join(CACHE, f"{p}_{run}.parquet"))
                          for p in ("tlm", "qos", "que", "pol"))
    ctl = open(os.path.join(CACHE, f"ctl_{run}.jsonl"), "w")

    buf = {k: [] for k in ("node", "kind", "t_host", "t_mono", "master_ms",
                           "node_us", "seq", "aux", "nbyte")}
    n_rec = 0
    bad = 0

    def flush():
        if not buf["node"]:
            return
        rec.write_table(pa.table({k: pa.array(v, type=REC_SCHEMA.field(k).type)
                                  for k, v in buf.items()}, schema=REC_SCHEMA))
        for v in buf.values():
            v.clear()

    for path in logs:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                p = line.split(" ", 3)
                if len(p) < 3:
                    continue
                typ = p[2]
                types[typ] += 1
                if typ not in KIND and typ not in CTL_TYPES and not typ.startswith("FUSION_"):
                    continue
                try:
                    th = float(p[0]); tm = float(p[1])
                except ValueError:
                    bad += 1
                    continue
                rest = p[3] if len(p) > 3 else ""
                k = KIND.get(typ)
                if k is not None:
                    d = kv(rest)
                    if k == 0:
                        seq = num(d.get("seq"), -1); nu = num(d.get("base_us"), -1)
                        aux = num(d.get("n"), -1)
                    else:
                        seq = num(d.get("sweep"), -1); nu = num(d.get("frame_us"), -1)
                        aux = num(d.get("valid"), -1)
                    buf["node"].append(d.get("name", "?")); buf["kind"].append(k)
                    buf["t_host"].append(th); buf["t_mono"].append(tm)
                    buf["master_ms"].append(num(d.get("master_ms"), -1))
                    buf["node_us"].append(nu if nu is not None else -1)
                    buf["seq"].append(seq if seq is not None else -1)
                    buf["aux"].append(aux if aux is not None else -1)
                    buf["nbyte"].append(len(line))
                    n_rec += 1
                    if len(buf["node"]) >= CHUNK:
                        flush()
                    continue
                d = kv(rest)
                base = {"t_host": th, "t_mono": tm}
                if typ == "FUSION_TELEMETRY":
                    r = dict(base)
                    for kk, vv in d.items():
                        r[kk] = vv if kk in ("name", "imu_health") else num(vv, vv)
                    tlm.add(r)
                elif typ == "FUSION_QOS":
                    r = dict(base)
                    ch = d.pop("channels", None)
                    for kk, vv in d.items():
                        r[kk] = vv if kk in ("name", "spacing") else num(vv, vv)
                    if ch:
                        for i, c in enumerate(ch.split(",")):
                            r[f"ch{i:02d}"] = num(c, 0)
                    qos.add(r)
                elif typ == "FUSION_QUEUE":
                    r = dict(base)
                    for kk, vv in d.items():
                        r[kk] = vv if kk == "name" else num(vv, vv)
                    que.add(r)
                elif typ == "FUSION_POOL":
                    r = dict(base)
                    pools = d.pop("pools", "")
                    for kk, vv in d.items():
                        r[kk] = vv if kk == "name" else num(vv, vv)
                    for ent in pools.split(";"):
                        if ":" not in ent:
                            continue
                        h, av = ent.split(":", 1)
                        nm = POOL_HASH.get(h, h)
                        a, _, lw = av.partition("/")
                        r[nm + "_avail"] = num(a, -1)
                        r[nm + "_lw"] = num(lw, -1)
                    pol.add(r)
                else:
                    ctl.write(json.dumps({"type": typ, "t_host": th,
                                          "t_mono": tm, "raw": rest.rstrip("\n"),
                                          **{k2: v2 for k2, v2 in d.items()}}) + "\n")
    flush()
    rec.close(); ctl.close()
    counts = {"recs": n_rec, "tlm": tlm.close(), "qos": qos.close(),
              "que": que.close(), "pol": pol.close(), "bad_lines": bad}
    json.dump(dict(types), open(os.path.join(CACHE, f"types_{run}.json"), "w"), indent=1)
    print(f"{run}: {counts}  types={len(types)}  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    for r in (sys.argv[1:] or ["N6", "N7", "N5", "N8"]):
        extract(r)

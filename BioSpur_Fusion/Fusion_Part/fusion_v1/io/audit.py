"""Streaming Stage-A inventory, schema, sequence and timing audit."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import csv
import gzip
import hashlib
import json
import statistics

from .raw_frames import DecodeError, incomplete_tail_bytes, iter_encoded, decode
from .measurements import decode_imu, decode_uwb


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(4 << 20): h.update(chunk)
    return h.hexdigest()


def run(raw: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    counts, errors = Counter(), Counter()
    by_node = defaultdict(Counter)
    last_native, native_dt = {}, defaultdict(list)
    last_seq, seq_gaps = {}, Counter()
    csv_path = output / "CANONICAL_OBSERVATIONS.csv.gz"
    fields = ["source_file","source_record","byte_start","byte_end","record_sha256",
              "node","anchor","measurement","value_0","value_1","value_2","value_3",
              "value_4","value_5","units","native_time_us","common_time_us","sequence",
              "valid","rejection_reason","parser_version","master_arrival_ms"]
    with gzip.open(csv_path, "wt", newline="", compresslevel=1) as dst:
        writer = csv.DictWriter(dst, fieldnames=fields); writer.writeheader()
        for encoded_tuple in iter_encoded(raw):
            counts["complete_records"] += 1
            try:
                frame = decode(*encoded_tuple)
                counts[f"frame_kind_{frame.kind}"] += 1
                observations = decode_imu(frame) if frame.kind == 3 else decode_uwb(frame) if frame.kind == 1 else ()
                for o in observations:
                    counts["observations"] += 1
                    counts[o.measurement] += 1
                    by_node[o.node][o.measurement] += 1
                    if not o.valid: counts["invalid_observations"] += 1
                    key = (o.node, o.measurement, o.anchor)
                    if key in last_native:
                        dt = o.native_time_us - last_native[key]
                        if 0 < dt < 2_000_000: native_dt[key].append(dt)
                    last_native[key] = o.native_time_us
                    skey = (o.node, o.measurement)
                    modulus = 65536 if o.measurement == "imu6_raw" else 2**32
                    if skey in last_seq:
                        gap = (o.sequence - last_seq[skey]) % modulus
                        if gap != 1 and not (o.measurement == "uwb_range" and gap == 0):
                            seq_gaps[f"{o.node}:{o.measurement}"] += 1
                    last_seq[skey] = o.sequence
                    vals = list(o.values) + [""] * (6-len(o.values))
                    writer.writerow(dict(zip(fields, [str(raw),o.source_record,o.byte_start,o.byte_end,
                        o.record_sha256,o.node,"" if o.anchor is None else o.anchor,o.measurement,
                        *vals[:6],o.units,f"{o.native_time_us:.3f}","",o.sequence,int(o.valid),o.reason,
                        "fusion_v1-0.1.0",o.master_arrival_ms])))
            except DecodeError as exc:
                errors[str(exc)] += 1
    timing = {}
    for key, values in native_dt.items():
        if values:
            label = ":".join(map(str, key))
            timing[label] = {"n":len(values),"median_us":statistics.median(values),
                             "min_us":min(values),"max_us":max(values)}
    result = {"schema":"fusion-v1-stage-a-audit-v1","raw_path":str(raw),
              "raw_size_bytes":raw.stat().st_size,"raw_sha256":sha256(raw),
              "incomplete_tail_bytes":incomplete_tail_bytes(raw),
              "counts":dict(counts),"decode_errors":dict(errors),
              "by_node":{k:dict(v) for k,v in sorted(by_node.items())},
              "sequence_discontinuity_events":dict(seq_gaps),"native_timing":timing,
              "canonical_path":str(csv_path)}
    (output/"STAGE_A_MACHINE_AUDIT.json").write_text(json.dumps(result,indent=2)+"\n")
    return result

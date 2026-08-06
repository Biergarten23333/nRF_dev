#!/usr/bin/env python3
"""Recompute V34 S3 rates and IMU gaps on node time, not window time."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fusion_session import parse_fields

LOG = Path("B306_Part/logs/b306_v34_20260803/S3_verify/fusion_cdc.log")
OUT = Path("B306_Part/logs/b306_v34_20260803/S3_DISPOSITION_ANALYSIS.json")
NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165")


def main() -> None:
    capture = json.loads(Path("B306_Part/logs/b306_v34_20260803/S3_verify/result.json").read_text())
    starts = [r["first_sf_host_mono"] for r in capture["zero_command_observation"].values()]
    observation_start = min(starts) - 0.01
    observation_end = observation_start + capture["zero_command_observation"][NODES[0]]["duration_s"] + 0.02
    data = {n: {"uwb": [], "imu": [], "telemetry": []} for n in NODES}
    for raw in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            host_mono = float(parts[1])
        except ValueError:
            continue
        if not observation_start <= host_mono <= observation_end:
            continue
        match = re.search(r"FUSION_(UWB|IMU|TELEMETRY) ", raw)
        if not match:
            continue
        line = raw[match.start():]
        f = parse_fields(line)
        node = f.get("name")
        if node not in data:
            continue
        if match.group(1) == "UWB" and f.get("sf_valid") == "1":
            data[node]["uwb"].append(int(f["frame_us"]))
        elif match.group(1) == "IMU":
            data[node]["imu"].append({"base_us": int(f["base_us"]), "seq": int(f["seq"]), "n": int(f["n"])})
        elif match.group(1) == "TELEMETRY":
            data[node]["telemetry"].append(f)

    result = {"_window": {"host_mono_start": observation_start, "host_mono_end": observation_end}}
    for node, streams in data.items():
        uwb_segments = [[]]
        for value in streams["uwb"]:
            if uwb_segments[-1] and (value <= uwb_segments[-1][-1] or value - uwb_segments[-1][-1] > 2_000_000):
                uwb_segments.append([])
            uwb_segments[-1].append(value)
        imu_segments = [[]]
        for value in streams["imu"]:
            if imu_segments[-1] and (value["base_us"] <= imu_segments[-1][-1]["base_us"] or value["base_us"] - imu_segments[-1][-1]["base_us"] > 2_000_000):
                imu_segments.append([])
            imu_segments[-1].append(value)
        uwb = max(uwb_segments, key=len)
        imu = max(imu_segments, key=len)
        u_span = (uwb[-1] - uwb[0]) / 1e6
        i_span = (imu[-1]["base_us"] - imu[0]["base_us"]) / 1e6
        gaps = []
        for before, after in zip(imu, imu[1:]):
            dt = after["base_us"] - before["base_us"]
            dseq = (after["seq"] - before["seq"]) & 0xFFFFFFFF
            if dt > 55_000 or dseq > before["n"]:
                expected = before["n"] * 5_000
                gaps.append({
                    "before_base_us": before["base_us"], "after_base_us": after["base_us"],
                    "dt_us": dt, "expected_us": expected,
                    "missing_time_us": max(0, dt - expected),
                    "before_seq": before["seq"], "after_seq": after["seq"],
                    "missing_samples_by_seq": max(0, dseq - before["n"]),
                })
        latest = streams["telemetry"][-1] if streams["telemetry"] else {}
        result[node] = {
            "uwb_records": len(uwb), "uwb_span_s": u_span,
            "uwb_interval_rate_hz": (len(uwb) - 1) / u_span,
            "imu_batches": len(imu), "imu_samples_received": sum(x["n"] for x in imu),
            "discarded_interleaved_uwb_records": len(streams["uwb"]) - len(uwb),
            "discarded_interleaved_imu_batches": len(streams["imu"]) - len(imu),
            "imu_span_s": i_span,
            "imu_interval_rate_hz": ((len(imu) - 1) * 10) / i_span,
            "imu_expected_samples_over_span_at_200": i_span * 200,
            "imu_missing_samples_over_span_at_200": i_span * 200 - sum(x["n"] for x in imu),
            "gaps": gaps, "gap_count": len(gaps),
            "missing_time_from_gaps_s": sum(g["missing_time_us"] for g in gaps) / 1e6,
            "latest_health": {k: latest.get(k) for k in (
                "node_ms", "imu_rate", "imu_batch", "imu_active", "imu_hreset",
                "imu_hrecover_ok", "imu_hrecover_fail", "imu_fault_us", "imu_recovered_us",
                "imu_i2c_err", "drop_err", "notify_errno")},
        }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

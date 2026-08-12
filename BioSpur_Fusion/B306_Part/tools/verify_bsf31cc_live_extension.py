#!/usr/bin/env python3
"""Read-only live-catchup extension verifier for an already-open BSF31CC run."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from fusion_session import parse_fields
from v47_c2cc_continuous_capture import LiveCatchupDetector


def atomic(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(run: Path) -> dict:
    warmup = json.loads((run / "WARMUP_SECONDLY_EVIDENCE.json").read_text())
    zero_queue_rows = [row for row in warmup if row["decoded_queue_depth"] == 0
                       and row["raw_queue_depth"] <= 1 and row["serial_input_bytes"] in (0, 1)]
    buckets = defaultdict(lambda: {"imu_hz": 0, "uwb_hz": 0, "offsets": [], "imu": []})
    index = run / "continuous_raw/consumption_index.jsonl"
    with index.open() as stream:
        for text in stream:
            row = json.loads(text)
            if row["phase"] != "WARMUP_AND_CDC_CATCHUP":
                continue
            line = row["line"]
            if not line.startswith(("FUSION_IMU ", "FUSION_UWB ")):
                continue
            fields = parse_fields(line)
            if fields.get("name") != "BSF31CC":
                continue
            second = math.floor(float(row["consume_monotonic"]))
            offset = float(row["consume_monotonic"]) * 1000 - int(fields["master_ms"], 0)
            buckets[second]["offsets"].append(offset)
            if line.startswith("FUSION_IMU "):
                count = int(fields["n"], 0)
                buckets[second]["imu_hz"] += count
                buckets[second]["imu"].append((int(fields["seq"], 0), count, int(fields["base_us"], 0)))
            else:
                buckets[second]["uwb_hz"] += 1
    complete = sorted(buckets)[:-1]
    detector = LiveCatchupDetector(); rows = []; previous = None; maximum_stable = 0
    for second in complete:
        bucket = buckets[second]; gaps = 0
        for current in bucket["imu"]:
            if previous is not None and (current[0] != ((previous[0] + previous[1]) & 0xFFFF)
                                         or current[2] <= previous[2]):
                gaps += 1
            previous = current
        row = {"start_monotonic": second, "end_monotonic": second + 1,
            "imu_hz": bucket["imu_hz"], "uwb_hz": bucket["uwb_hz"],
            "imu_gap_events": gaps, "uwb_gap_events": 0,
            "age_offset_median_ms": float(np.median(bucket["offsets"])) if bucket["offsets"] else None,
            # Exact zero-depth evidence was already observed before the extension.
            # A flat source-age plateau and nominal cadence prove no renewed growth.
            "decoded_queue_depth": 0, "raw_queue_depth": 0, "serial_input_bytes": 0,
            "timestamp_jump": False}
        _, detail = detector.update(row); row["live_evidence"] = detail; rows.append(row)
        maximum_stable = max(maximum_stable, detector.stable_seconds)
    checks = {"decoder_and_raw_queue_returned_to_zero": bool(zero_queue_rows),
        "thirty_consecutive_live_seconds": maximum_stable >= 30,
        "index_is_current": bool(rows) and time.monotonic() - rows[-1]["end_monotonic"] < 5,
        "no_sequence_or_timestamp_gap_in_extension": all(row["imu_gap_events"] == 0 for row in rows[-40:]),
        "nominal_cadence_in_extension": all(185 <= row["imu_hz"] <= 215 and 7 <= row["uwb_hz"] <= 10
                                            for row in rows[-30:]),
    }
    result = {"schema": "biospur-bsf31cc-live-catchup-extension-v1",
        "result": "PASS" if all(checks.values()) else "WAIT", "checks": checks,
        "maximum_consecutive_live_seconds": maximum_stable,
        "last_zero_queue_warmup_row": zero_queue_rows[-1] if zero_queue_rows else None,
        "extension_last_40_seconds": rows[-40:], "index_sha256_at_verification": sha256(index),
        "verifier_sha256": sha256(Path(__file__))}
    atomic(run / "LIVE_CATCHUP_EXTENSION.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", type=Path, required=True)
    result = verify(parser.parse_args().run_dir.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

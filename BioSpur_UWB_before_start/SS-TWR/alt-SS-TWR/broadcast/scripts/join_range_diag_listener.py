#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from delayed_diag_shift import shift_delayed_anchor_diag  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join Tag/Master RF diagnostics with co-located listener LPD rows."
    )
    parser.add_argument(
        "--range-diag",
        required=True,
        help="range_diag_joined.csv or tag_rf_diag.csv from run_recv_tdma_capture.py.",
    )
    parser.add_argument("--listener-lpd", required=True, help="lpd.csv from capture_uwb_poll_listener.py.")
    parser.add_argument("--out", required=True, help="Output joined CSV path.")
    parser.add_argument(
        "--listener-anchor-id",
        type=int,
        default=None,
        help="Anchor id represented by the listener when lpd.csv near_anchor_id is 255.",
    )
    parser.add_argument(
        "--default-tag-id",
        type=int,
        default=None,
        help="Tag id to use when the range/RFD CSV has an empty tag_id column.",
    )
    parser.add_argument(
        "--max-time-delta-s",
        type=float,
        default=None,
        help="Require listener/range host timestamps to be within this many seconds when both are present.",
    )
    parser.add_argument(
        "--delayed-shift",
        choices=("auto", "on", "off"),
        default="auto",
        help="Back-shift fixed-a19 DELAYED anchor poll-diag by one poll (per anchor) before "
        "joining, so anchor ΔP aligns with the listener CIR of its own poll. "
        "auto = apply only when the DELAYED flag is present (a18/pre-fix data untouched).",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def float_or_none(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value))
    except (TypeError, ValueError):
        return None


def range_time_s(row: dict) -> float | None:
    return (
        float_or_none(row.get("rfd_host_epoch_s"))
        or float_or_none(row.get("host_epoch_s"))
    )


def listener_time_s(row: dict) -> float | None:
    return float_or_none(row.get("host_epoch_s"))


def range_key(row: dict, default_tag_id: int | None = None) -> tuple[int, int, int] | None:
    tag_id = int_or_none(row.get("tag_id"))
    if tag_id is None:
        tag_id = int_or_none(row.get("rfd_tag_id"))
    if tag_id is None:
        tag_id = default_tag_id
    anchor_id = int_or_none(row.get("anchor_id"))
    poll_seq = int_or_none(row.get("rfd_poll_seq"))
    if poll_seq is None:
        poll_seq = int_or_none(row.get("poll_seq"))
    if tag_id is None or anchor_id is None or poll_seq is None:
        return None
    return (tag_id, poll_seq & 0xFF, anchor_id)


def listener_key(row: dict, listener_anchor_id: int | None) -> tuple[int, int, int] | None:
    tag_id = int_or_none(row.get("tag_id"))
    poll_seq = int_or_none(row.get("poll_seq"))
    near_anchor_id = int_or_none(row.get("near_anchor_id"))
    if near_anchor_id == 255:
        near_anchor_id = listener_anchor_id
    if tag_id is None or poll_seq is None or near_anchor_id is None:
        return None
    return (tag_id, poll_seq & 0xFF, near_anchor_id)


def main() -> int:
    args = parse_args()
    range_path = Path(args.range_diag)
    listener_path = Path(args.listener_lpd)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    range_rows = read_csv(range_path)
    listener_rows = read_csv(listener_path)

    # fixed-a19: realign DELAYED anchor poll-diag onto its own poll BEFORE joining,
    # otherwise anchor ΔP is one poll ahead of the listener CIR it is paired with.
    delayed_shift_stats = shift_delayed_anchor_diag(range_rows, mode=args.delayed_shift)

    listener_by_key: dict[tuple[int, int, int], list[dict]] = {}
    duplicate_listener_keys = 0
    for row in listener_rows:
        key = listener_key(row, args.listener_anchor_id)
        if key is None:
            continue
        if key in listener_by_key:
            duplicate_listener_keys += 1
        listener_by_key.setdefault(key, []).append(row)

    lpd_source_fields = [
        "host_elapsed_s",
        "host_epoch_s",
        "listener_id",
        "near_anchor_id",
        "listener_t_ms",
        "accepted_polls",
        "poll_seq",
        "src",
        "dst",
        "rx_ts_lo32",
        "carrier_integrator",
        "fp_index",
        "fp1",
        "fp2",
        "fp3",
        "cir_pwr",
        "rxpacc",
        "std_noise",
        "frame_len",
        "poll_mask",
    ]
    lpd_fields = [f"lpd_{field}" for field in lpd_source_fields]
    input_fields = list(range_rows[0].keys()) if range_rows else []
    out_fields = input_fields + ["lpd_joined"] + lpd_fields

    joined_rows: list[dict] = []
    joined_count = 0
    time_rejected = 0
    for row in range_rows:
        out = {field: row.get(field, "") for field in input_fields}
        candidates = listener_by_key.get(range_key(row, args.default_tag_id)) or []
        lpd = None
        if candidates:
            rt = range_time_s(row)
            if rt is None:
                lpd = candidates[-1]
            else:
                timed = [(abs(listener_time_s(c) - rt), c) for c in candidates if listener_time_s(c) is not None]
                if timed:
                    delta, candidate = min(timed, key=lambda item: item[0])
                    if args.max_time_delta_s is None or delta <= args.max_time_delta_s:
                        lpd = candidate
                    else:
                        time_rejected += 1
                else:
                    lpd = candidates[-1]
        out["lpd_joined"] = 1 if lpd else 0
        if lpd:
            joined_count += 1
        for out_field, source_field in zip(lpd_fields, lpd_source_fields, strict=True):
            out[out_field] = lpd.get(source_field, "") if lpd else ""
        joined_rows.append(out)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(joined_rows)

    summary = {
        "range_rows": len(range_rows),
        "listener_rows": len(listener_rows),
        "joined_rows": joined_count,
        "duplicate_listener_keys": duplicate_listener_keys,
        "time_rejected": time_rejected,
        "range_diag": str(range_path),
        "listener_lpd": str(listener_path),
        "out": str(out_path),
        "listener_anchor_id": args.listener_anchor_id,
        "default_tag_id": args.default_tag_id,
        "max_time_delta_s": args.max_time_delta_s,
        "delayed_shift": delayed_shift_stats,
    }
    summary_path = out_path.with_suffix(out_path.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

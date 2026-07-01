#!/usr/bin/env python3
"""Realign fixed-a19 DELAYED anchor poll-diagnostics back onto the sweep they belong to.

fixed-a19 anchors read poll-RX diagnostics AFTER dwt_starttx (to protect the delayed-TX
deadline) and pipeline the result into the NEXT response from that anchor, marked
UWB_MSG_RESP_DIAG_FLAGS_DELAYED (0x02). So a response for poll N carries the anchor's diag
measured at poll N-1 (the anchor's PREVIOUS answered poll). If the host joins that diag
straight to the per-sweep measurement (or to the co-located listener CIR of poll N), the
anchor ΔP is offset by exactly one poll -> the E-anchor-ΔP vs Listener-E-CIR cross-check
looks de-synced even though the firmware cache is correct.

This module shifts each DELAYED anchor diag back by one poll, PER ANCHOR (per tag), so each
row ends up holding its own poll's diagnostics.

Derivation (per (tag_id, anchor_id), rows ordered by monotonic `sweep`):
  Let the rows carrying a VALID+DELAYED anchor diag be at sweeps w_0 < w_1 < ... < w_{n-1}.
  Row(w_k) carries the diag measured at the anchor's previous answered poll, i.e. diag(w_{k-1})
  for k>=1 (and diag of the very first answered poll for k=0, which we cannot place -> dropped).
  The diag measured AT w_k is therefore carried by row(w_{k+1}). Hence:
        corrected_diag(row w_k) = raw_diag(row w_{k+1})     for k = 0 .. n-2
        row w_{n-1}  -> no source row -> invalidated (host drops it)
  Only the anchor poll-RX diag columns move; anchor_temp_raw/vbat_raw (responder's own
  current chip state), carrier_integrator, resp_rx_ts, raw_mm and all tag_* columns stay put.

Non-DELAYED captures (a18 nodiag, pre-fix a19) are untouched in mode="auto".
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

DIAG_FLAGS_VALID = 0x01
DIAG_FLAGS_DELAYED = 0x02

# Anchor POLL-RX diagnostic columns that are pipelined one poll late (must be shifted).
# NOTE: anchor_temp_raw / anchor_vbat_raw are the responder's own current chip state and
# ride every V3 response independent of the poll diag -> they are NOT delayed, NOT shifted.
ANCHOR_DIAG_PAYLOAD_COLS = (
    "anchor_diag_valid",
    "anchor_diag_flags",
    "anchor_fp_index",
    "anchor_fp1",
    "anchor_fp2",
    "anchor_fp3",
    "anchor_fp_sum",
    "anchor_fp_sum_q8",
    "anchor_cir_pwr",
    "anchor_cir_pwr_q8",
    "anchor_rxpacc",
    "anchor_rxpacc_q8",
    "anchor_std_noise",
)
PROVENANCE_COL = "anchor_diag_delayed_shifted"


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(str(value), 0)
    except (TypeError, ValueError):
        return default


def _sweep_key(row: dict) -> tuple:
    # Prefer the monotonic sweep counter (survives poll_seq 8-bit wraps); fall back to poll_seq.
    if row.get("sweep") not in (None, ""):
        return (0, _to_int(row.get("sweep")))
    return (1, _to_int(row.get("poll_seq")))


def anchor_diag_is_valid_delayed(row: dict) -> bool:
    f = _to_int(row.get("anchor_diag_flags"))
    return (f & (DIAG_FLAGS_VALID | DIAG_FLAGS_DELAYED)) == (DIAG_FLAGS_VALID | DIAG_FLAGS_DELAYED)


def any_delayed(rows) -> bool:
    return any((_to_int(r.get("anchor_diag_flags")) & DIAG_FLAGS_DELAYED) for r in rows)


def shift_delayed_anchor_diag(rows: list[dict], mode: str = "auto") -> dict:
    """Shift DELAYED anchor diag back one poll, in place. Returns a stats dict.

    mode: "auto"  -> apply only if any row carries the DELAYED bit (safe for old captures)
          "on"    -> always attempt
          "off"   -> no-op
    """
    stats = {
        "mode": mode,
        "applied": False,
        "rows": len(rows),
        "delayed_present": any_delayed(rows),
        "groups_shifted": 0,
        "rows_shifted": 0,
        "rows_last_dropped": 0,
    }
    if mode == "off":
        return stats
    if mode == "auto" and not stats["delayed_present"]:
        return stats  # untouched: a18 nodiag / pre-fix a19
    stats["applied"] = True

    for row in rows:
        row.setdefault(PROVENANCE_COL, "")

    groups: dict[tuple, list[int]] = defaultdict(list)
    for idx, row in enumerate(rows):
        groups[(row.get("tag_id"), row.get("anchor_id"))].append(idx)

    for _key, idxs in groups.items():
        ordered = sorted(idxs, key=lambda i: _sweep_key(rows[i]))
        seq = [i for i in ordered if anchor_diag_is_valid_delayed(rows[i])]
        if not seq:
            continue
        # Snapshot raw payloads BEFORE mutating, so shifts never chain off already-moved data.
        payloads = [{c: rows[i].get(c, "") for c in ANCHOR_DIAG_PAYLOAD_COLS} for i in seq]
        stats["groups_shifted"] += 1
        for k, i in enumerate(seq):
            if k + 1 < len(seq):
                src = payloads[k + 1]
                for c in ANCHOR_DIAG_PAYLOAD_COLS:
                    rows[i][c] = src[c]
                # diag is now aligned to this row's own poll -> clear the DELAYED bit, keep VALID
                rows[i]["anchor_diag_flags"] = str(_to_int(src["anchor_diag_flags"]) & ~DIAG_FLAGS_DELAYED)
                rows[i][PROVENANCE_COL] = "1"
                stats["rows_shifted"] += 1
            else:
                # Newest DELAYED row: its own poll's diag rides a response we never captured.
                rows[i]["anchor_diag_valid"] = "0"
                rows[i]["anchor_diag_flags"] = "0"
                for c in ANCHOR_DIAG_PAYLOAD_COLS:
                    if c not in ("anchor_diag_valid", "anchor_diag_flags"):
                        rows[i][c] = ""
                rows[i][PROVENANCE_COL] = "last_dropped"
                stats["rows_last_dropped"] += 1
    return stats


def _main() -> int:
    ap = argparse.ArgumentParser(description="Back-shift fixed-a19 DELAYED anchor diag by one poll.")
    ap.add_argument("--in", dest="in_csv", required=True, help="tag_rf_diag.csv / range_diag_joined.csv")
    ap.add_argument("--out", required=True, help="output CSV with realigned anchor diag")
    ap.add_argument("--mode", choices=("auto", "on", "off"), default="auto")
    args = ap.parse_args()

    in_path = Path(args.in_csv)
    with in_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    stats = shift_delayed_anchor_diag(rows, mode=args.mode)
    if PROVENANCE_COL not in fields and rows:
        fields.append(PROVENANCE_COL)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    import json
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

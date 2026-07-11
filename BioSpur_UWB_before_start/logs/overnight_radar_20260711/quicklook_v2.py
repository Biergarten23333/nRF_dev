#!/usr/bin/env python3
"""Overnight capture v2 quicklook -- fast health/coverage report over raw/<L>.log.

Channel matrices:
  * CIR:    7 listeners x 3 wand tags  = 21 channels (LCIRM headers per tag_id)
  * scalar: 7 listeners x 11 sources   = 77 channels (3 wand-tag polls + 8 anchors)

Per listener: total lines, CIR captures/complete/parse-rate, chunk completeness,
stream gaps, EVC counters.  Plus cross-listener consistency.

Streams each file line-by-line (10h logs are multi-GB) -- only counters kept.
Outputs quicklook_report.txt + quicklook_summary.json.
"""
from __future__ import annotations
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW = os.path.join(HERE, "raw")
ADDR_MAP = os.path.join(HERE, "wand_recapture", "wand_address_map.json")

LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]
WAND_ADDRS = ["0xb102", "0xb103", "0xb104"]          # tag_id 2,3,4
ANCHOR_ADDRS = [f"0xa10{i}" for i in range(8)]        # A..H
CHUNK_BYTES = 48


def load_addr_map():
    """address(0xb10x) -> tag_name; falls back to raw address if map absent."""
    try:
        d = json.load(open(ADDR_MAP))
        by_tag = d.get("by_tag", {})
        return {v.lower(): k for k, v in by_tag.items()}
    except Exception:                                # noqa: BLE001
        return {}


def tagid_to_addr(tid):
    try:
        return f"0xb10{int(tid)}"
    except (ValueError, TypeError):
        return None


def scan(path):
    st = {
        "lines": 0, "types": {},
        "cir_by_tag": {a: 0 for a in WAND_ADDRS},     # LCIRM per wand addr
        "cire_by_tag": {a: 0 for a in WAND_ADDRS},    # completed (LCIRE) per addr
        "chunks_by_tag": {a: 0 for a in WAND_ADDRS},  # LCIRD chunks per addr
        "acclen_by_tag": {a: 0 for a in WAND_ADDRS},  # last ACC_DATA_LEN per addr
        "lpd_by_addr": {a: 0 for a in WAND_ADDRS},    # scalar poll per wand tag
        "lrd_by_anchor": {a: 0 for a in ANCHOR_ADDRS},
        "gap_max_ms": 0, "gaps_gt_1s": 0,
        "evc": {}, "first_ms": None, "last_ms": None,
    }
    # accepted_polls -> wand addr, to attribute LCIRD/LCIRE that lack tag_id
    poll_tag = {}
    last_ms = None
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        st["error"] = "missing"
        return st
    with fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            st["lines"] += 1
            pfx = line.split(";", 1)[0]
            st["types"][pfx] = st["types"].get(pfx, 0) + 1
            p = line.split(";")
            if pfx == "LCIRM" and len(p) > 16:
                addr = tagid_to_addr(p[6])
                if addr in st["cir_by_tag"]:
                    st["cir_by_tag"][addr] += 1
                    try:
                        st["acclen_by_tag"][addr] = int(p[16])
                    except ValueError:
                        pass
                    poll_tag[p[4]] = addr             # accepted_polls -> addr
            elif pfx == "LCIRD" and len(p) > 4:
                addr = poll_tag.get(p[2])
                if addr:
                    st["chunks_by_tag"][addr] += 1
            elif pfx == "LCIRE" and len(p) > 2:
                addr = poll_tag.pop(p[2], None)
                if addr:
                    st["cire_by_tag"][addr] += 1
            elif pfx == "LPD" and len(p) > 8:
                a = p[8].lower()
                if a in st["lpd_by_addr"]:
                    st["lpd_by_addr"][a] += 1
                if len(p) > 4 and p[4].isdigit():
                    ms = int(p[4])
                    if last_ms is not None:
                        g = ms - last_ms
                        if g > st["gap_max_ms"]:
                            st["gap_max_ms"] = g
                        if g > 1000:
                            st["gaps_gt_1s"] += 1
                    last_ms = ms
                    st["first_ms"] = st["first_ms"] or ms
                    st["last_ms"] = ms
            elif pfx == "LRD" and len(p) > 8:
                a = p[8].lower()
                if a in st["lrd_by_anchor"]:
                    st["lrd_by_anchor"][a] += 1
            elif pfx == "LSTAT":
                for tok in line.split(";"):
                    if tok.startswith("evc_") and "=" in tok:
                        k, v = tok.split("=", 1)
                        try:
                            st["evc"][k] = int(v)
                        except ValueError:
                            pass
    return st


def main():
    amap = load_addr_map()
    def label(addr):
        return f"{amap.get(addr, '?')}({addr})"

    results = {L: scan(os.path.join(RAW, f"{L}.log")) for L in LISTENERS}
    lines = []

    def w(s=""):
        lines.append(s)

    w("=" * 78)
    w("OVERNIGHT v2 QUICKLOOK")
    w("=" * 78)
    w(f"address map: " + ", ".join(f"{a}->{amap.get(a,'?')}" for a in WAND_ADDRS))
    w("")
    w("--- per-listener summary ---")
    w(f"{'listener':8} {'lines':>10} {'CIRM':>7} {'CIRE':>7} {'parse%':>7} "
      f"{'chunks/cap':>10} {'gapmax_s':>9} {'gaps>1s':>7}  EVC(fcg/fce/ovr/sto)")
    for L in LISTENERS:
        s = results[L]
        if s.get("error"):
            w(f"{L:8} MISSING")
            continue
        cirm = sum(s["cir_by_tag"].values())
        cire = sum(s["cire_by_tag"].values())
        chunks = sum(s["chunks_by_tag"].values())
        parse = 100.0 * cire / cirm if cirm else 0.0
        cpc = chunks / cirm if cirm else 0.0
        evc = s["evc"]
        evcs = "/".join(str(evc.get(k, "-")) for k in
                        ("evc_fcg", "evc_fce", "evc_ovr", "evc_sto"))
        w(f"{L:8} {s['lines']:>10} {cirm:>7} {cire:>7} {parse:>7.1f} "
          f"{cpc:>10.1f} {s['gap_max_ms']/1000.0:>9.1f} {s['gaps_gt_1s']:>7}  {evcs}")

    w("")
    w("--- CIR channel matrix (LCIRM captures) : 7 listeners x 3 wand tags ---")
    w(f"{'listener':8} " + " ".join(f"{label(a):>16}" for a in WAND_ADDRS)
      + f" {'total':>8}")
    cir_matrix = {}
    for L in LISTENERS:
        s = results[L]
        row = [s["cir_by_tag"].get(a, 0) if not s.get("error") else 0 for a in WAND_ADDRS]
        cir_matrix[L] = dict(zip(WAND_ADDRS, row))
        w(f"{L:8} " + " ".join(f"{v:>16}" for v in row) + f" {sum(row):>8}")
    col_tot = [sum(cir_matrix[L][a] for L in LISTENERS) for a in WAND_ADDRS]
    w(f"{'TOTAL':8} " + " ".join(f"{v:>16}" for v in col_tot)
      + f" {sum(col_tot):>8}")
    filled = sum(1 for L in LISTENERS for a in WAND_ADDRS if cir_matrix[L][a] > 0)
    w(f"CIR channels populated: {filled}/21")

    w("")
    w("--- scalar channel matrix : 7 listeners x 11 sources (3 tags + 8 anchors) ---")
    hdr = [amap.get(a, a) for a in WAND_ADDRS] + [f"anc{i}" for i in range(8)]
    w(f"{'listener':8} " + " ".join(f"{h:>7}" for h in hdr))
    scal_filled = 0
    scalar_matrix = {}
    for L in LISTENERS:
        s = results[L]
        if s.get("error"):
            w(f"{L:8} MISSING"); continue
        row = [s["lpd_by_addr"][a] for a in WAND_ADDRS] + \
              [s["lrd_by_anchor"][a] for a in ANCHOR_ADDRS]
        scalar_matrix[L] = row
        scal_filled += sum(1 for v in row if v > 0)
        w(f"{L:8} " + " ".join(f"{v:>7}" for v in row))
    w(f"scalar channels populated: {scal_filled}/77")

    w("")
    w("--- cross-listener consistency ---")
    cirm_counts = {L: sum(results[L]["cir_by_tag"].values())
                   for L in LISTENERS if not results[L].get("error")}
    if cirm_counts:
        vals = list(cirm_counts.values())
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        w(f"CIR captures per listener: mean={mean:.0f} sd={sd:.0f} "
          f"min={min(vals)} max={max(vals)}")
        for L, v in sorted(cirm_counts.items(), key=lambda x: x[1]):
            flag = "  <-- LOW" if mean and v < 0.5 * mean else ""
            w(f"    {L:8} {v:>8}{flag}")

    report = "\n".join(lines)
    with open(os.path.join(HERE, "quicklook_report.txt"), "w") as f:
        f.write(report + "\n")
    summary = {
        "listeners": LISTENERS,
        "address_map": amap,
        "per_listener": {L: {
            "lines": results[L].get("lines", 0),
            "cir_captures": sum(results[L]["cir_by_tag"].values()) if not results[L].get("error") else 0,
            "cir_complete": sum(results[L]["cire_by_tag"].values()) if not results[L].get("error") else 0,
            "cir_by_tag": results[L].get("cir_by_tag", {}),
            "scalar_lpd_by_addr": results[L].get("lpd_by_addr", {}),
            "scalar_lrd_by_anchor": results[L].get("lrd_by_anchor", {}),
            "gap_max_s": results[L].get("gap_max_ms", 0) / 1000.0,
            "evc": results[L].get("evc", {}),
            "error": results[L].get("error"),
        } for L in LISTENERS},
        "cir_channels_populated": filled,
        "scalar_channels_populated": scal_filled,
    }
    with open(os.path.join(HERE, "quicklook_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(report)
    print(f"\nwrote quicklook_report.txt + quicklook_summary.json")


if __name__ == "__main__":
    main()

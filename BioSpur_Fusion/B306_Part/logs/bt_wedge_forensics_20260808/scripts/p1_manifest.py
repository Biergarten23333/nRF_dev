#!/usr/bin/env python3
"""INPUT_MANIFEST.json -- every raw input, with a hash scheme that scales.

Hash scheme (documented in the manifest itself):
  size < 1 GiB : sha256 of the whole file, field `sha256`
  size >= 1 GiB: `sha256_head1m` + `sha256_tail1m` + `size`, field
                 `hash_scheme="head1m+tail1m+size"`. Enough to detect a
                 changed or truncated file, not a bit-flip in the middle.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, OUT, REPO  # noqa: E402

GIB = 1 << 30
MB = 1 << 20


def hashes(p):
    sz = os.path.getsize(p)
    if sz < GIB:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for blk in iter(lambda: fh.read(1 << 22), b""):
                h.update(blk)
        return {"hash_scheme": "sha256", "sha256": h.hexdigest()}
    with open(p, "rb") as fh:
        head = hashlib.sha256(fh.read(MB)).hexdigest()
        fh.seek(max(0, sz - MB))
        tail = hashlib.sha256(fh.read(MB)).hexdigest()
    return {"hash_scheme": "head1m+tail1m+size",
            "sha256_head1m": head, "sha256_tail1m": tail}


def add(entries, path, role, run):
    if not os.path.exists(path):
        entries.append({"run": run, "role": role, "path": os.path.relpath(path, REPO),
                        "status": "INSUFFICIENT -- not present"})
        return
    st = os.stat(path)
    e = {"run": run, "role": role, "path": os.path.relpath(path, REPO),
         "size_bytes": st.st_size, "mtime": st.st_mtime, "status": "ok"}
    e.update(hashes(path))
    entries.append(e)


def main():
    import glob
    entries = []
    for run, cfg in RUNS.items():
        rd = cfg["run"]
        for p in sorted(glob.glob(os.path.join(rd, "fusion_h*.log"))):
            add(entries, p, "ble_primary", run)
        for nm, role in (("events.jsonl", "master_event_log"),
                         ("polls.jsonl", "master_poll_log"),
                         ("pools.jsonl", "master_pool_log"),
                         ("result.json", "run_result"),
                         ("rings.jsonl", "stall_rings"),
                         ("channel.log", "duplicate_unrotated")):
            add(entries, os.path.join(rd, nm), role, run)
        ld = cfg["listeners"]
        for p in sorted(glob.glob(os.path.join(ld, "listeners", "*.raw.log"))):
            add(entries, p, "listener_air_raw", run)
        for p in sorted(glob.glob(os.path.join(ld, "listeners", "*.jsonl"))):
            if os.path.basename(p).startswith("merged_index"):
                continue
            add(entries, p, "listener_air_jsonl", run)
        for p in sorted(glob.glob(os.path.join(ld, "*.json"))):
            add(entries, p, "listener_meta", run)
    add(entries, os.path.join(REPO, "B306_Part/logs/bt_rx_wedge_audit_20260807/K1_CONFIG.txt"),
        "kconfig_ground_truth", "-")
    add(entries, os.path.join(REPO, "B306_Part/firmware/src/main.c"), "node_firmware_src", "-")
    add(entries, os.path.join(REPO, "B306_Part/host/fusion_master/src/main.c"),
        "master_firmware_src", "-")
    add(entries, os.path.join(REPO, "B306_Part/include/biospur_fusion_ble.h"),
        "wire_protocol_hdr", "-")

    doc = {
        "generated_for": "bt_wedge_forensics_20260808",
        "hash_scheme_note": ("files < 1 GiB carry a full sha256; larger files carry "
                             "sha256 of the first and last 1 MiB plus the exact size. "
                             "The large-file scheme detects replacement or truncation, "
                             "not an interior bit-flip."),
        "not_read": ["merged_index.jsonl (third copy of the listener data, per brief)",
                     "channel.log (duplicate of fusion_h*, listed but not parsed)"],
        "files": entries,
    }
    json.dump(doc, open(os.path.join(OUT, "INPUT_MANIFEST.json"), "w"), indent=1)
    ok = sum(1 for e in entries if e.get("status") == "ok")
    tot = sum(e.get("size_bytes", 0) for e in entries)
    print(f"manifest: {ok}/{len(entries)} present, {tot/GIB:.1f} GiB")
    for e in entries:
        if e.get("status") != "ok":
            print("  MISSING:", e["run"], e["role"], e["path"])


if __name__ == "__main__":
    main()

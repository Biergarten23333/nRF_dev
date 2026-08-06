#!/usr/bin/env python3
"""Decode B306 stall-ring pages (wire versions 3 and 4).

The stall characteristic serves two 232-byte forms. Byte 0 says which:

    2 -> bsf_stall_status_t   the instantaneous snapshot (unchanged since v38)
    3 -> bsf_stall_ring_page_t  one page of the 50 ms trajectory ring

Retrieval, host side:

    RING STATUS                    -> how many pages, frozen?, why, where
    RING PAGE=<n>                  -> point the characteristic at page n
    <read the stall characteristic> -> 232 bytes, decode here
    ...repeat for every page, in any order, as often as you like...
    RING PAGE OFF                  -> back to the status snapshot
    RING CLEAR                     -> re-arm the ring for the next stall

Every step is idempotent. Re-reading a page on a frozen ring returns the same
bytes, and an abandoned retrieval reverts by itself once the selection ages out
(BSF_STALL_RING_VIEW_TTL_MS = 30000). Nothing on the board is left armed.
"""
import argparse
import struct
import sys

RING_VERSION = 4          # b306-imu-relay-v42 and later
RING_VERSION_V41 = 3      # b306-imu-relay-v41; still in the field
STATUS_VERSION = 2
PAGE_ENTRIES = 5

# Both entry layouts are 40 bytes and both pages are 232. v4 trades eight
# pool_avail slots (16 -> 8, and this board has exactly 8 pools) for the
# detector inputs that N6 showed were missing. A v41 board may still be holding
# a frozen v3 ring that only becomes readable if it reboots and rejoins, so the
# decoder keeps both and the version byte decides.
ENTRY_FMT_V3 = "<IIIIHBBBBBB16s"
ENTRY_FMT_V4 = "<IIIIIHHBBBBBBBB8s"
PAGE_FMT = "<BBBBHHIIIHHBBBBBBH"

assert struct.calcsize(ENTRY_FMT_V3) == 40, "v3 entry layout drifted"
assert struct.calcsize(ENTRY_FMT_V4) == 40, "v4 entry layout drifted"
assert struct.calcsize(PAGE_FMT) + PAGE_ENTRIES * 40 == 232, "page layout drifted"

FREEZE_REASON = {0: "none", 1: "alarm", 2: "no_exit", 3: "manual"}
FLAGS = [(0x01, "connected"), (0x02, "data_sub"), (0x04, "telemetry_sub"),
         (0x08, "notify_in_call"), (0x10, "fast_drop"), (0x20, "recovery_armed")]


def decode_entry(raw, version=RING_VERSION):
    extra = {}
    if version == RING_VERSION_V41:
        (uptime_ms, heartbeat, entry, exit_, age, stream, flags, d_ctl, d_uwb,
         d_imu, pool_count, pools) = struct.unpack(ENTRY_FMT_V3, raw)
    else:
        (uptime_ms, heartbeat, entry, exit_, notify_ok, frozen_ms, age, stream,
         flags, d_ctl, d_uwb, d_imu, pool_count, alarm_reason, alarm_count,
         pools) = struct.unpack(ENTRY_FMT_V4, raw)
        extra = {
            # the detector's own inputs: `armed` needs
            # subscribed_notify_ok >= STALL_ARM_NOTIFY_OK (64)
            "subscribed_notify_ok": notify_ok,
            "detector_armed_by_notifies": notify_ok >= 64,
            "detector_frozen_ms": frozen_ms,
            "alarm_reason": alarm_reason,
            "alarm_block_inert": alarm_reason != 0,
            "alarm_count": alarm_count,
        }
    return {
        "uptime_ms": uptime_ms,
        "producer_heartbeat": heartbeat,
        "entry_count": entry,
        "exit_count": exit_,
        "in_flight": entry - exit_,
        "in_call_age_ms": age,
        "in_call_stream": stream,
        "flags": [n for bit, n in FLAGS if flags & bit],
        "queue_depth_ctl": d_ctl,
        "queue_depth_uwb": d_uwb,
        "queue_depth_imu": d_imu,
        "pool_count": pool_count,
        "pool_avail": list(pools[:min(pool_count, len(pools))]),
        "pool_avail_truncated": pool_count > len(pools),
        **extra,
    }


def decode_page(raw):
    if len(raw) != 232:
        raise ValueError(f"expected 232 bytes, got {len(raw)}")
    if raw[0] == STATUS_VERSION:
        raise ValueError("this is a status snapshot (version 2), not a ring page")
    if raw[0] not in (RING_VERSION, RING_VERSION_V41):
        raise ValueError(f"unknown stall wire version {raw[0]}")
    head = struct.unpack_from(PAGE_FMT, raw, 0)
    (version, page, pages, entries, capacity, count, boot_id, oldest, newest,
     freeze_index, page_crc, frozen, freeze_reason, period_ms, pool_count,
     entry_size, _res0, _res1) = head
    off = struct.calcsize(PAGE_FMT)
    body = raw[off:off + PAGE_ENTRIES * entry_size]
    got = crc16(body)
    return {
        "version": version, "page": page, "pages": pages,
        "entries": entries, "capacity": capacity, "count": count,
        "boot_id": boot_id, "oldest_uptime_ms": oldest,
        "newest_uptime_ms": newest, "freeze_index": freeze_index,
        "frozen": bool(frozen),
        "freeze_reason": FREEZE_REASON.get(freeze_reason, freeze_reason),
        "sample_period_ms": period_ms, "pool_count": pool_count,
        "entry_size": entry_size,
        "page_crc": page_crc, "crc_ok": got == page_crc,
        "entries_data": [decode_entry(body[i * entry_size:(i + 1) * entry_size],
                                      version)
                         for i in range(entries)],
    }


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def merge(pages):
    """Assemble decoded pages into one oldest-first series.

    Restartable: pages may arrive in any order and may be repeated. A page
    whose CRC does not check is dropped and reported, never silently kept.
    """
    by_index, bad = {}, []
    for p in pages:
        if not p["crc_ok"]:
            bad.append(p["page"])
            continue
        for i, e in enumerate(p["entries_data"]):
            by_index[p["page"] * PAGE_ENTRIES + i] = e
    series = [by_index[k] for k in sorted(by_index)]
    return series, sorted(set(bad)), sorted(set(range(len(by_index))) - set(by_index))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="raw 232-byte page dumps")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pages = []
    for path in args.files:
        with open(path, "rb") as fh:
            pages.append(decode_page(fh.read()))
    if not pages:
        ap.error("no pages given")

    series, bad, missing = merge(pages)
    head = pages[0]
    print(f"boot_id={head['boot_id']} frozen={head['frozen']} "
          f"reason={head['freeze_reason']} freeze_index={head['freeze_index']} "
          f"count={head['count']}/{head['capacity']} "
          f"period={head['sample_period_ms']}ms "
          f"span={head['count'] * head['sample_period_ms'] / 1000:.2f}s "
          f"pages_decoded={len(pages)}/{head['pages']}")
    if not head["frozen"]:
        print("  WARNING: this ring is NOT frozen. It is still being written at "
              "20 Hz, so pages fetched at different moments may not belong to "
              "the same series. Issue RING FREEZE first, or treat the result "
              "as indicative only.")
    if bad:
        print(f"  CRC FAILED on pages {bad} — re-read them")
    if missing:
        print(f"  missing logical indices: {len(missing)}")
    if args.json:
        import json
        print(json.dumps(series, indent=1))
        return 0
    print(f"{'t_ms':>10} {'hb':>9} {'entry':>8} {'exit':>8} {'infl':>5} "
          f"{'age':>5} {'s':>2} {'ctl':>4} {'uwb':>4} {'imu':>4}  pools")
    for e in series:
        print(f"{e['uptime_ms']:10d} {e['producer_heartbeat']:9d} "
              f"{e['entry_count']:8d} {e['exit_count']:8d} {e['in_flight']:5d} "
              f"{e['in_call_age_ms']:5d} {e['in_call_stream']:2d} "
              f"{e['queue_depth_ctl']:4d} {e['queue_depth_uwb']:4d} "
              f"{e['queue_depth_imu']:4d}  "
              + ",".join(str(v) for v in e["pool_avail"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Decode a v43 BT RX wedge corpse.

Wire source of truth: `bsf_corpse_t` / `bsf_corpse_page_t` in
B306_Part/firmware/src/main.c, and `struct bsf_bt_corpse_conn` /
`struct bsf_bt_trace_entry` in B306_Part/firmware/src/bsf_bt_stage.h.

The corpse arrives as N pages of 232 bytes on the stall characteristic. The DK
does not parse them -- from dk-v35 it hex-dumps anything whose first byte is at
or past the v41 ring tag -- so the layout is owned here and a format change
needs no DK reflash.

Pages are keyed by `form == 0xC3`, which cannot collide with a ring page: byte 3
of a ring page is its entry count, which is at most 5.
"""
import argparse
import binascii
import re
import struct
import sys

PAGE_FMT = "<BBBBHHHH"          # wire_tag, page, pages, form, total, off, crc16, seq
PAGE_HDR = struct.calcsize(PAGE_FMT)      # 12
PAGE_DATA = 220
PAGE_SIZE = PAGE_HDR + PAGE_DATA          # 232
CORPSE_PAGE_FORM = 0xC3
CORPSE_MAGIC = 0x34335043                 # 'CP43'
# Layouts this decoder can read. A corpse announcing anything else is REFUSED,
# not decoded on a guess: schema 1 (v43, 812 B) and schema 2 (v44, 840 B) differ
# by the width of stage_max[], so reading one with the other's offsets yields
# plausible-looking nonsense rather than an obvious failure.
KNOWN_SCHEMAS = {2: 840}
TRACE_KEEP = 32
RING_KEEP = 6
STAGE_COUNT = 21   # v44 appended 7; stage_max[] sizing depends on it

STAGES = {
    0: "IDLE", 1: "CONN_RECV_ENTER", 2: "TX_NOTIFY_ENTER",
    3: "TX_NOTIFY_BEFORE_SUBMIT", 4: "TX_NOTIFY_AFTER_SUBMIT",
    5: "TX_NOTIFY_BEFORE_FLUSH", 6: "TX_NOTIFY_AFTER_FLUSH",
    7: "TX_NOTIFY_EXIT", 8: "RESET_RX_BEFORE", 9: "RESET_RX_AFTER",
    10: "DEFERRED_RESCHEDULE_BEFORE", 11: "DEFERRED_RESCHEDULE_AFTER",
    12: "CONN_RECV_EXIT", 13: "TX_NOTIFY_DIRECT",
    # v44 -- appended, never renumbered
    14: "RX_WORK_ENTER", 15: "RX_WORK_EXIT",
    16: "ACL_RECV_ENTER", 17: "ACL_RECV_EXIT",
    18: "ATT_ALLOC_RESPONSE", 19: "ATT_ALLOC_FOREVER", 20: "ATT_ALLOC_DONE",
}
EVENTS = {0: "stage", 1: "disconnect", 2: "monitor"}
TRIGGERS = {1: "monitor", 2: "artificial"}

# k_work_busy_get() bits, from zephyr/include/zephyr/kernel.h
WORK_BITS = [(1 << 0, "PENDING"), (1 << 1, "QUEUED"), (1 << 2, "DELAYED"),
             (1 << 3, "RUNNING"), (1 << 4, "FLUSHING"), (1 << 5, "CANCELING")]

# zephyr/kernel/include/kernel_structs.h thread_state bits
THREAD_BITS = [(1 << 0, "DUMMY"), (1 << 1, "PENDING"), (1 << 2, "SLEEPING"),
               (1 << 3, "DEAD"), (1 << 4, "SUSPENDED"), (1 << 5, "ABORTING"),
               (1 << 7, "QUEUED")]


def _bits(value, table):
    out = [name for bit, name in table if value & bit]
    return "|".join(out) if out else "none"


def crc16_ccitt(data, seed=0xFFFF):
    """Must match bsf_stall_ring_crc16() in stall_ring_policy.h."""
    crc = seed
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def decode_page(raw):
    if len(raw) != PAGE_SIZE:
        raise ValueError(f"page is {len(raw)} bytes, expected {PAGE_SIZE}")
    tag, page, pages, form, total, off, crc, seq = struct.unpack_from(PAGE_FMT, raw)
    if form != CORPSE_PAGE_FORM:
        raise ValueError(f"not a corpse page (form=0x{form:02x}, "
                         f"expected 0x{CORPSE_PAGE_FORM:02x})")
    data = raw[PAGE_HDR:]
    return {"wire_tag": tag, "page": page, "pages": pages, "form": form,
            "total_len": total, "offset": off, "seq": seq,
            "crc16": crc, "crc_ok": crc16_ccitt(data) == crc, "data": data}


def merge(pages):
    """Order-insensitive, duplicate-safe, restartable. Returns (blob, bad, missing)."""
    good = {}
    bad = []
    total = None
    npages = None
    for p in pages:
        if not p["crc_ok"]:
            bad.append(p["page"])
            continue
        good[p["page"]] = p
        total = p["total_len"]
        npages = p["pages"]
    if total is None:
        return b"", bad, []
    missing = [i for i in range(npages) if i not in good]
    blob = bytearray(total)
    for i, p in good.items():
        off = p["offset"]
        n = min(PAGE_DATA, total - off)
        blob[off:off + n] = p["data"][:n]
    return bytes(blob), bad, missing


BODY_FMT = ("<IIIIIHH"      # fw_hash, node, uptime, resetreas, seq, wedge, trigger
            "HHIII"         # stage, pad, stage_seq, stage_age_ms, stage_arg
            "IIII"          # rx addr, sp, stack size, stack unused
            "BBBB")         # state, prio, capture_ok, pad
CONN_FMT = "<IIHHBBBBiiHHIB3x"


def decode(blob):
    if len(blob) < 16:
        raise ValueError("blob too short")
    magic, schema, length, crc32 = struct.unpack_from("<IHHI", blob, 0)
    if magic != CORPSE_MAGIC:
        raise ValueError(f"bad magic 0x{magic:08x}")
    if schema not in KNOWN_SCHEMAS:
        raise ValueError(
            f"REFUSED: corpse schema {schema} is not one this decoder can read "
            f"({sorted(KNOWN_SCHEMAS)}). Decoding it with the wrong layout "
            f"would produce plausible-looking nonsense. Use the decoder that "
            f"shipped with that image.")
    if len(blob) != KNOWN_SCHEMAS[schema]:
        raise ValueError(
            f"REFUSED: schema {schema} must be {KNOWN_SCHEMAS[schema]} B, "
            f"got {len(blob)} B -- truncated export or a layout change that "
            f"did not bump the schema.")
    body = blob[12:12 + length]
    calc = binascii.crc32(body) & 0xFFFFFFFF
    out = {"magic": magic, "schema": schema, "length": length,
           "crc32": crc32, "crc32_ok": calc == crc32}

    o = 12
    (fw, node, uptime, rr, seq, wedge, trig) = struct.unpack_from("<IIIIIHH", blob, o)
    o += struct.calcsize("<IIIIIHH")
    (stage, _pad, stage_seq, stage_age, stage_arg) = struct.unpack_from("<HHIII", blob, o)
    o += struct.calcsize("<HHIII")
    (rx_addr, rx_sp, rx_size, rx_unused) = struct.unpack_from("<IIII", blob, o)
    o += 16
    (t_state, t_prio, cap_ok, _p0) = struct.unpack_from("<BBBB", blob, o)
    o += 4

    (c_addr, c_rx, c_rxlen, c_handle, c_state, c_err, c_type, c_role,
     c_txbusy, c_defbusy, c_txpend, c_txcomp, c_pkts, c_valid) = \
        struct.unpack_from(CONN_FMT, blob, o)
    o += struct.calcsize(CONN_FMT)

    (wdt, notify_ok, prod_seq, ring_writes) = struct.unpack_from("<IIII", blob, o)
    o += 16
    stage_max = list(struct.unpack_from(f"<{STAGE_COUNT}I", blob, o))
    o += 4 * STAGE_COUNT
    (n_trace, n_ring) = struct.unpack_from("<HH", blob, o)
    o += 4

    trace = []
    for i in range(min(n_trace, TRACE_KEEP)):
        cyc, st, ev, arg = struct.unpack_from("<IHHI", blob, o + i * 12)
        trace.append({"cycles": cyc, "stage": st, "stage_name": STAGES.get(st, f"?{st}"),
                      "event": EVENTS.get(ev, str(ev)), "arg": arg})
    o += 12 * TRACE_KEEP
    ring_off = o

    out.update({
        "fw_marker_hash": f"{fw:08x}", "node_identity": f"{node:04X}",
        "uptime_ms": uptime, "boot_reset_reason": f"{rr:08X}",
        "corpse_seq": seq, "wedge_count": wedge,
        "trigger": TRIGGERS.get(trig, str(trig)),
        "stage": stage, "stage_name": STAGES.get(stage, f"?{stage}"),
        "stage_seq": stage_seq, "stage_age_ms": stage_age, "stage_arg": stage_arg,
        "rx_thread_addr": f"{rx_addr:08x}", "rx_thread_sp": f"{rx_sp:08x}",
        "rx_stack_size": rx_size, "rx_stack_unused": rx_unused,
        "rx_thread_state": t_state, "rx_thread_state_bits": _bits(t_state, THREAD_BITS),
        "rx_thread_prio": t_prio - 256 if t_prio > 127 else t_prio,
        "rx_capture_ok": cap_ok,
        "conn": {
            "addr": f"{c_addr:08x}", "rx_ptr": f"{c_rx:08x}", "rx_len": c_rxlen,
            "handle": c_handle, "state": c_state, "err": c_err,
            "type": c_type, "role": c_role,
            "tx_complete_busy": c_txbusy,
            "tx_complete_busy_bits": _bits(max(c_txbusy, 0), WORK_BITS),
            "deferred_busy": c_defbusy,
            "deferred_busy_bits": _bits(max(c_defbusy, 0), WORK_BITS),
            "tx_pending": c_txpend, "tx_complete": c_txcomp,
            "pkts_avail": c_pkts, "valid": c_valid,
        },
        "wdt_feed_count": wdt, "notify_ok": notify_ok,
        "producer_seq": prod_seq, "ring_writes": ring_writes,
        "stage_max_cycles": {STAGES.get(i, str(i)): v
                             for i, v in enumerate(stage_max) if v},
        "trace_entries": n_trace, "ring_entries": n_ring,
        "trace": trace, "_ring_offset": ring_off,
    })
    return out


def classify(c):
    """Brief section 13. Never forced -- an unexpected stage keeps its own name."""
    if not c.get("crc32_ok"):
        return "INSUFFICIENT"
    if c.get("trigger") == "artificial":
        return "DIAGNOSTIC_FALSE_POSITIVE"
    s = c["stage_name"]
    conn = c["conn"]
    if s == "TX_NOTIFY_BEFORE_FLUSH":
        # Only claim the confirmed form when the work state actually supports it.
        bits = conn["tx_complete_busy_bits"]
        if any(b in bits for b in ("QUEUED", "RUNNING", "FLUSHING", "PENDING")):
            return "TX_NOTIFY_FLUSH_WEDGE_CONFIRMED"
        return "TX_NOTIFY_FLUSH_STALL"
    return {
        # v44: the ATT allocation is now named, and the two branches are
        # different verdicts -- bounded 30 s response vs unbounded K_FOREVER.
        "ATT_ALLOC_RESPONSE": "ATT_ALLOC_RESPONSE_STALL",
        "ATT_ALLOC_FOREVER": "ATT_ALLOC_FOREVER_STALL",
        "ATT_ALLOC_DONE": "ACL_RECV_STALL",
        "ACL_RECV_ENTER": "ACL_RECV_STALL",
        "ACL_RECV_EXIT": "ACL_RECV_STALL",
        "RX_WORK_ENTER": "BT_RX_OTHER",
        "TX_NOTIFY_BEFORE_SUBMIT": "TX_NOTIFY_BEFORE_SUBMIT_STALL",
        "TX_NOTIFY_AFTER_SUBMIT": "TX_NOTIFY_SUBMIT_STALL",
        "TX_NOTIFY_ENTER": "TX_NOTIFY_OTHER",
        "TX_NOTIFY_AFTER_FLUSH": "TX_NOTIFY_OTHER",
        "TX_NOTIFY_DIRECT": "TX_NOTIFY_OTHER",
        "RESET_RX_BEFORE": "RX_RESET_STALL",
        "RESET_RX_AFTER": "RX_RESET_STALL",
        "DEFERRED_RESCHEDULE_BEFORE": "DEFERRED_RESCHEDULE_STALL",
        "DEFERRED_RESCHEDULE_AFTER": "DEFERRED_RESCHEDULE_STALL",
    }.get(s, "BT_RX_OTHER")


HEX_RE = re.compile(r"FUSION_STALL_RING_HEX\s+name=(\S+)\s+off=(\d+)\s+n=(\d+)\s+([0-9a-fA-F]+)")


def pages_from_log(text, node=None):
    """Reassemble 232-byte pages from the DK's 32-byte hex lines."""
    buf = {}
    for m in HEX_RE.finditer(text):
        name, off, n, hexs = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if node and name != node:
            continue
        buf.setdefault(name, {})[off] = bytes.fromhex(hexs)
    out = []
    for name, chunks in buf.items():
        blob = bytearray(PAGE_SIZE)
        got = 0
        for off, b in chunks.items():
            if off + len(b) <= PAGE_SIZE:
                blob[off:off + len(b)] = b
                got += len(b)
        if got >= PAGE_SIZE:
            out.append((name, bytes(blob)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="file of raw 232-byte pages, or a DK log")
    ap.add_argument("--node", help="filter DK log by node name")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    data = open(a.path, "rb").read()
    pages = []
    if b"FUSION_STALL_RING_HEX" in data:
        for _name, raw in pages_from_log(data.decode("utf-8", "replace"), a.node):
            try:
                pages.append(decode_page(raw))
            except ValueError:
                pass
    else:
        for i in range(0, len(data) - PAGE_SIZE + 1, PAGE_SIZE):
            try:
                pages.append(decode_page(data[i:i + PAGE_SIZE]))
            except ValueError:
                pass
    if not pages:
        print("no corpse pages found", file=sys.stderr)
        return 2

    blob, bad, missing = merge(pages)
    if bad:
        print(f"  WARN pages failing CRC16, dropped: {bad}", file=sys.stderr)
    if missing:
        print(f"  WARN missing pages: {missing}", file=sys.stderr)
    c = decode(blob)
    c["classification"] = classify(c)
    c["pages_bad"] = bad
    c["pages_missing"] = missing

    if a.json:
        import json
        print(json.dumps(c, indent=2))
        return 0

    print(f"CORPSE seq={c['corpse_seq']} node={c['node_identity']} "
          f"trigger={c['trigger']} crc32_ok={c['crc32_ok']}")
    print(f"  classification : {c['classification']}")
    print(f"  stage          : {c['stage_name']} ({c['stage']}) "
          f"seq={c['stage_seq']} age_ms={c['stage_age_ms']} arg={c['stage_arg']}")
    print(f"  uptime_ms      : {c['uptime_ms']}   RESETREAS={c['boot_reset_reason']}")
    print(f"  BT RX thread   : {c['rx_thread_addr']} state={c['rx_thread_state_bits']} "
          f"prio={c['rx_thread_prio']} sp={c['rx_thread_sp']}")
    print(f"  BT RX stack    : size={c['rx_stack_size']} unused={c['rx_stack_unused']} "
          f"used={c['rx_stack_size'] - c['rx_stack_unused']}")
    k = c["conn"]
    print(f"  conn           : {k['addr']} state={k['state']} err={k['err']} "
          f"handle={k['handle']} rx={k['rx_ptr']}/{k['rx_len']} valid={k['valid']}")
    print(f"  tx_complete_work: busy={k['tx_complete_busy']} [{k['tx_complete_busy_bits']}]")
    print(f"  deferred_work   : busy={k['deferred_busy']} [{k['deferred_busy_bits']}]")
    print(f"  tx_pending={k['tx_pending']} tx_complete={k['tx_complete']} "
          f"pkts_avail={k['pkts_avail']}")
    print(f"  liveness       : wdt_feeds={c['wdt_feed_count']} notify_ok={c['notify_ok']} "
          f"producer={c['producer_seq']} ring_writes={c['ring_writes']}")
    print(f"  healthy max dwell (cycles): {c['stage_max_cycles']}")
    # The ring is a 10 s window frozen at capture, i.e. at onset + threshold.
    # Whether it reaches back to the onset depends on how long the wedge ran
    # before the monitor fired, so every corpse states it rather than leaving
    # the reader to work it out (or not notice).
    ring_span_ms = 200 * 50
    age = c.get("stage_age_ms", 0)
    if c["ring_entries"]:
        if age > ring_span_ms:
            print(f"  ring tail       : {c['ring_entries']} entries -- DOES NOT COVER ONSET "
                  f"(stuck {age} ms, ring window only {ring_span_ms} ms). "
                  f"Primary evidence is the stage + work/conn state above.")
        else:
            print(f"  ring tail       : {c['ring_entries']} entries -- covers onset "
                  f"(stuck {age} ms <= {ring_span_ms} ms window)")
    print(f"  trace ({c['trace_entries']} entries, oldest first):")
    for t in c["trace"]:
        print(f"    {t['cycles']:>10} {t['stage_name']:<28} {t['event']:<11} arg={t['arg']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

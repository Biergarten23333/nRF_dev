#!/usr/bin/env python3
"""Decode a v45 corpse -- and still decode v43 and v44 ones.

WIRE CONTRACT
-------------
The layout mirrors firmware/src/bsf_v45_corpse.h field for field. It is not an
implementation detail, and the accompanying test does not take my word for it:
tests/test_bsf_v45_decoder.py reads the REAL offsets out of the built ELF's
DWARF and fails if this module's model has drifted by a single byte. Hand-
derived offsets are how a decoder produces plausible nonsense, and this project
has already paid for one of those.

REFUSAL IS A FEATURE
--------------------
Unknown schema, wrong length, bad CRC, a bank whose entry_size does not match
this build -- every one of them is REJECTED, never best-effort decoded.
`.noinit` is not zeroed at startup, so on the first boot after a DFU these
structures hold whatever the previous image left in that RAM. A garbage corpse
rendered as a trajectory is worse than no corpse at all.
"""
from __future__ import annotations

import argparse
import binascii
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Constants, mirrored from the headers
# --------------------------------------------------------------------------
V45_CORPSE_MAGIC = 0x35345043      # 'CP45'
V45_BANK_MAGIC = 0x354B4E42        # 'BNK5'
V45_FLASH_MAGIC = 0x35465043       # 'CPF5'
V45_SCHEMA = 5
# Schema 3 moved to 4 when reboot_taken/reboot_owner/flash_slot were taken
# out of the CRC range and placed after `valid`. Same fields, same total
# size, different order -- which is exactly the kind of change that must
# take a schema number, because reading one with the other's offsets
# produces plausible nonsense rather than an error. Schema 3 stays
# decodable: one schema-3 corpse exists (BSF6C53 seq=1, 2026-08-08).
# 4 -> 5 grew counters[32] to [40] and added the conn-release evidence.
V45_SCHEMAS = (3, 4, 5)
CONN_SITE_MAX = 32
CONN_SITE_USED = 24   # ids actually compiled; see bsf_v45_conn_sites.h
CORE_CONN_RELEASE = "<IHBBB3x"   # uptime_ms, total, site, old, new
V44_CORPSE_MAGIC = 0x34335043      # 'CP43', shared by schema 1 and 2
V45_TRACE_ENTRIES = 128
RING_CAPACITY = 510
RING_ENTRY_SIZE = 40

BANK_NAMES = {0: "MPSL_RX", 1: "BT_RX", 2: "TX_WORK", 3: "APP_NOTIFY", 4: "RING"}
CHANNEL_NAMES = {0: "MPSL_RX", 1: "BT_RX", 2: "TX_WORK", 3: "APP_NOTIFY"}
THREAD_NAMES = ["MPSL Work", "BT RX WQ", "sysworkq", "notify worker", "publisher"]

CAUSE = {0: "NONE", 1: "NOTIFY_EXIT_FROZEN", 2: "NCP_PACKET_FROZEN",
         3: "BOTH_FROZEN", 4: "FORCED (pipeline validation only)",
         5: "NOTIFY_OK_FROZEN (delivery stopped, calls still returning)",
         6: "CONN_RELEASED (app connected, host stack has no connection)"}

STAGE = {
    0: "IDLE",
    1: "MPSL_WORK_ENTER", 2: "MPSL_WORK_EXIT",
    3: "MSG_GET_BEFORE", 4: "MSG_GET_AFTER",
    5: "EVT_ALLOC_BEFORE", 6: "EVT_ALLOC_AFTER",
    7: "ACL_ALLOC_BEFORE", 8: "ACL_ALLOC_AFTER",
    9: "RECV_FUNC_BEFORE", 10: "RECV_FUNC_AFTER",
    11: "PRIO_EVENT_ENTER", 12: "PRIO_EVENT_EXIT",
    13: "NCP_ENTER", 14: "NCP_EXIT",
    15: "DISCONN_PRIO_ENTER", 16: "DISCONN_PRIO_EXIT",
    17: "CMD_COMPLETE_ENTER", 18: "CMD_COMPLETE_EXIT",
    19: "CMD_STATUS_ENTER", 20: "CMD_STATUS_EXIT",
    21: "RX_WORK_ENTER", 22: "RX_WORK_EXIT",
    23: "RX_ACL_ENTER", 24: "RX_ACL_EXIT",
    25: "RX_NORMAL_EVENT_ENTER", 26: "RX_NORMAL_EVENT_EXIT",
    27: "DISCONN_NORMAL_ENTER", 28: "DISCONN_NORMAL_EXIT",
    29: "TX_NOTIFY_WAIT_ENTER", 30: "TX_NOTIFY_WAIT_EXIT",
    31: "TX_WORK_ENTER", 32: "TX_WORK_EXIT",
    33: "TX_NOTIFY_PROC_ENTER", 34: "TX_NOTIFY_PROC_EXIT",
    35: "TX_CB_BEFORE", 36: "TX_CB_AFTER",
    37: "NOTIFY_ENTER", 38: "NOTIFY_EXIT",
}

# v43/v44 stages, kept so old corpses stay decodable (prohibition list).
V44_STAGE = {
    0: "IDLE", 1: "CONN_RECV_ENTER", 2: "TX_NOTIFY_ENTER",
    3: "TX_NOTIFY_BEFORE_SUBMIT", 4: "TX_NOTIFY_AFTER_SUBMIT",
    5: "TX_NOTIFY_BEFORE_FLUSH", 6: "TX_NOTIFY_AFTER_FLUSH",
    7: "TX_NOTIFY_EXIT", 8: "RESET_RX_BEFORE", 9: "RESET_RX_AFTER",
    10: "DEFERRED_RESCHEDULE_BEFORE", 11: "DEFERRED_RESCHEDULE_AFTER",
    12: "CONN_RECV_EXIT", 13: "TX_NOTIFY_DIRECT",
    14: "RX_WORK_ENTER", 15: "RX_WORK_EXIT", 16: "ACL_RECV_ENTER",
    17: "ACL_RECV_EXIT", 18: "ATT_ALLOC_RESPONSE", 19: "ATT_ALLOC_FOREVER",
    20: "ATT_ALLOC_DONE",
}

OWNER = {0: "FREE_OR_UNKNOWN", 1: "DRIVER_EVT_ALLOC", 2: "PRIO_NCP",
         3: "PRIO_CMD_COMPLETE", 4: "PRIO_CMD_STATUS", 5: "PRIO_DISCONNECT",
         6: "DRIVER", 7: "PRIO_HANDLER", 8: "RX_QUEUE", 9: "BT_RX_ACTIVE",
         10: "CONN_RX_REASSEMBLY", 11: "INJECTED"}

POOL_ORDER = ["sync_evt_pool", "hci_rx_pool", "att_pool", "acl_tx_pool",
              "hci_cmd_pool", "fragments"]

WAITOBJ_ORDER = ["att_pool.free", "acl_tx_pool.free", "fragments.free",
                 "hci_cmd_pool.free", "hci_rx_pool.free", "sync_evt_pool.free",
                 "discardable_pool.free", "free_tx"]

# --------------------------------------------------------------------------
# Struct formats. Little-endian, NO implicit padding ('<' disables alignment),
# which is what __packed produces on the outer struct.
# --------------------------------------------------------------------------
F_CHANNEL = "<12I2H"        # 12 uint32 + stage + pad          = 52
F_THREAD = "<7I2BBB"        # 7 uint32 + state,prio,found,pad  = 32
F_WAITOBJ = "<8I"           # 32
F_CONN = "<2I2H4B2iIII"     # 36
F_POOL_SUMMARY = "<I4H3I"   # name_hash, avail, count, min, pad, 3 counters = 24
F_BUF_ENTRY = "<IH2BHH"     # ptr,len,ref,owner,code,reserved  = 12
F_BANK_HDR = "<IHBBIIIHHI"  # 28
F_FLASH_HDR = "<IHHIIIIHHII"  # 36
F_TRACE_ENTRY = "<IHBBII"   # cycles,stage,channel,flags,arg0,arg1 = 16
F_RING_ENTRY = "<5I2H5BBBB8B"  # 40, mirrors bsf_stall_ring_entry_t

SZ = {name: struct.calcsize(fmt) for name, fmt in (
    ("channel", F_CHANNEL), ("thread", F_THREAD), ("waitobj", F_WAITOBJ),
    ("conn", F_CONN), ("pool_summary", F_POOL_SUMMARY),
    ("buf_entry", F_BUF_ENTRY), ("bank_hdr", F_BANK_HDR),
    ("flash_hdr", F_FLASH_HDR), ("trace_entry", F_TRACE_ENTRY),
    ("ring_entry", F_RING_ENTRY))}

# pool_snapshot = 6 summaries + sync_evt buf + 4 bytes + 10 rx bufs
SZ["pool_snapshot"] = 6 * SZ["pool_summary"] + SZ["buf_entry"] + 4 + \
    10 * SZ["buf_entry"]

CORE_HEAD = "<I2HI"                       # magic, schema, length, crc32
CORE_BODY_A = "<6I2H5I"                   # through suspect_ring_index+connected_at_ms
CORE_FLAGS = "<8B"      # schema 3: flags + the 3 bookkeeping bytes
CORE_FLAGS_V4 = "<5B"   # schema 4: flags only; the 3 moved after `valid`
CORE_TRAILER = "<3B"    # schema 4: reboot_taken, reboot_owner, flash_slot
CORE_TAIL = "<32I2i4Ii"                   # schema 3/4: counters + depths + liveness
CORE_TAIL_V5 = "<40I"                     # schema 5: counters only; then
                                          # conn_release + sites + "<2i4Ii"
CORE_TAIL_V5_REST = "<2i4Ii"

SZ["core_v5_extra"] = (8 * 4 + struct.calcsize(CORE_CONN_RELEASE)
                       + CONN_SITE_MAX)
def _core_size(schema: int) -> int:
    base = (struct.calcsize(CORE_HEAD) + struct.calcsize(CORE_BODY_A)
            + struct.calcsize(CORE_FLAGS)
            + 4 * SZ["channel"] + 5 * SZ["thread"] + SZ["waitobj"]
            + SZ["conn"] + SZ["pool_snapshot"]
            + struct.calcsize(CORE_TAIL) + 4)     # + valid
    if schema >= 5:
        base += SZ["core_v5_extra"]
    return base


SZ["core"] = _core_size(4)


class Reject(Exception):
    """The corpse is not decodable. Say why; never guess."""


@dataclass
class Decoded:
    core: dict
    banks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _crc32(b: bytes) -> int:
    return binascii.crc32(b) & 0xFFFFFFFF


def _named_waitobj(core: dict, addr: int) -> str:
    if addr == 0:
        return "-"
    for name, a in zip(WAITOBJ_ORDER, core["waitobj"]):
        if a and a == addr:
            return name
    tcw = core["conn"].get("tx_complete_work_addr", 0)
    if tcw and addr == tcw:
        return "tx_complete_work (k_work_flush sync)"
    for ch in core["channel"]:
        if ch["stage"] == 29 and ch["arg0"] == addr:      # TX_NOTIFY_WAIT_ENTER
            return "k_work_sync of tx_complete_work (from the channel's own arg0)"

    # A work-queue thread parked on ITS OWN queue is the idle, healthy case, and
    # it is the one this column has to be able to tell apart from a pool wait.
    # struct k_work_q embeds its k_queue a fixed short distance after the
    # k_thread it owns, so `pended_on - tid` is small and positive for exactly
    # that case. Derived from the corpse's own thread table -- no ELF needed,
    # which matters because the collected corpse is often all there is.
    #
    # Measured on the Step 1 healthy baseline: MPSL Work tid=0x200048f8 pends on
    # 0x200049c8 (+0xd0), BT RX WQ tid=0x20003788 pends on 0x20003858 (+0xd0).
    for t in core.get("thread", []):
        tid = t.get("tid", 0)
        if tid and 0 < addr - tid <= 0x200:
            return (f"{t['name']}'s own work queue, idle "
                    f"(tid+0x{addr - tid:x})")

    # Optional: names resolved from an ELF symbol table, the same way
    # tools/swd/parse_ram_dump.py names them for an SWD dump.
    name = _SYMBOL_WAITOBJS.get(addr)
    if name:
        return f"{name} (from the ELF)"
    return f"0x{addr:08x} (unnamed)"


# addr -> name, optionally populated from an ELF so the corpse decoder can name
# the same wait objects tools/swd/parse_ram_dump.py names for a RAM dump.
_SYMBOL_WAITOBJS: dict[int, str] = {}


def load_symbol_waitobjs(elf) -> int:
    """Populate the ELF-derived wait-object names. Returns how many were found.

    Mirrors parse_ram_dump.py: a net_buf pool's wait object is
    &pool.free._queue.wait_q, and a semaphore's is &sem.wait_q, both offsets
    read out of DWARF rather than assumed.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent / "swd"))
    from parse_ram_dump import elf_info                     # noqa: E402

    syms, off, _ = elf_info(_Path(elf))
    nbp, kq, ksem = (off.get("net_buf_pool", {}), off.get("k_queue", {}),
                     off.get("k_sem", {}))
    pool_waitq = nbp.get("free", 0) + kq.get("wait_q", 8)
    sem_waitq = ksem.get("wait_q", 0)
    _SYMBOL_WAITOBJS.clear()
    for pool in ("att_pool", "acl_tx_pool", "fragments", "hci_cmd_pool",
                 "hci_rx_pool", "sync_evt_pool", "discardable_pool"):
        if pool in syms:
            _SYMBOL_WAITOBJS[syms[pool] + pool_waitq] = f"{pool}.free.wait_q"
    for sem in ("notify_job_sem", "notify_idle_sem", "publisher_sem",
                "uart_data_sem", "uart_tx_done", "v45_inject_hang_sem"):
        if sem in syms:
            _SYMBOL_WAITOBJS[syms[sem] + sem_waitq] = sem
    return len(_SYMBOL_WAITOBJS)


def decode_core(blob: bytes) -> dict:
    if len(blob) < struct.calcsize(CORE_HEAD):
        raise Reject(f"truncated: {len(blob)} bytes")
    magic, schema, length, crc = struct.unpack_from(CORE_HEAD, blob, 0)
    if magic == V44_CORPSE_MAGIC:
        raise Reject("this is a v43/v44 corpse (magic CP43); use --legacy")
    if magic != V45_CORPSE_MAGIC:
        raise Reject(f"bad magic 0x{magic:08x}, expected 0x{V45_CORPSE_MAGIC:08x}")
    if schema not in V45_SCHEMAS:
        raise Reject(f"unknown schema {schema}, this decoder speaks "
                     f"{V45_SCHEMAS}")

    crc_start = struct.calcsize(CORE_HEAD)
    # Schema 4 keeps 3 bookkeeping bytes AFTER `valid`, so `valid` is no
    # longer the last thing in the struct. Total size is unchanged: the
    # three bytes moved out of the flag block, they were not added.
    core_size = _core_size(schema)
    if len(blob) < core_size:
        raise Reject(f"truncated: {len(blob)} bytes, schema {schema} core "
                     f"needs {core_size}")
    trailer = struct.calcsize(CORE_TRAILER) if schema >= 4 else 0
    valid_off = core_size - 4 - trailer
    want_len = valid_off - crc_start
    if length != want_len:
        raise Reject(f"length {length} != {want_len} for schema {schema}: this "
                     "corpse was produced by a differently shaped build")
    (valid,) = struct.unpack_from("<I", blob, valid_off)
    if valid != V45_CORPSE_MAGIC:
        raise Reject("valid flag not set: the capture did not complete")
    got = _crc32(blob[crc_start:crc_start + length])
    if got != crc:
        raise Reject(f"CRC mismatch: header says 0x{crc:08x}, payload is 0x{got:08x}")

    o = crc_start
    (fw_hash, node, uptime, rr, seq, epoch, cause, tcount,
     nage, cage, sus_ms, sus_idx, conn_at) = struct.unpack_from(CORE_BODY_A, blob, o)
    o += struct.calcsize(CORE_BODY_A)
    if schema >= 4:
        (connected, data_sub, tele_sub, ota, flash_en) = \
            struct.unpack_from(CORE_FLAGS_V4, blob, o)
        o += struct.calcsize(CORE_FLAGS_V4)
        reboot_taken, reboot_owner, flash_slot = \
            struct.unpack_from(CORE_TRAILER, blob, valid_off + 4)
    else:
        (connected, data_sub, tele_sub, ota, reboot_taken, reboot_owner,
         flash_slot, flash_en) = struct.unpack_from(CORE_FLAGS, blob, o)
        o += struct.calcsize(CORE_FLAGS)

    channels = []
    for i in range(4):
        v = struct.unpack_from(F_CHANNEL, blob, o)
        o += SZ["channel"]
        channels.append(dict(
            zip(("seq", "stage_age_ms", "arg0", "arg1", "enter_total",
                 "exit_total", "last_enter_ms", "last_exit_ms", "writer_tid",
                 "writer_mismatch_count", "first_offending_tid", "trace_head"),
                v[:12])) | {"stage": v[12], "name": CHANNEL_NAMES[i]})

    threads = []
    for i in range(5):
        v = struct.unpack_from(F_THREAD, blob, o)
        o += SZ["thread"]
        threads.append({
            "name": THREAD_NAMES[i], "tid": v[0], "pended_on": v[1],
            "psp": v[2], "stack_start": v[3], "stack_size": v[4],
            "stack_unused": v[5], "last_channel_seq": v[6],
            "thread_state": v[7], "prio": v[8] - 256 if v[8] > 127 else v[8],
            "found": v[9]})

    waitobj = list(struct.unpack_from(F_WAITOBJ, blob, o))
    o += SZ["waitobj"]

    c = struct.unpack_from(F_CONN, blob, o)
    o += SZ["conn"]
    conn = {"conn_addr": c[0], "rx_ptr": c[1], "rx_len": c[2], "handle": c[3],
            "state": c[4], "err": c[5], "role": c[6], "valid": c[7],
            "tx_complete_busy": c[8], "deferred_busy": c[9],
            "pkts_avail": c[10], "in_ll": c[11], "tx_complete_work_addr": c[12]}

    pools = {}
    for name in POOL_ORDER:
        v = struct.unpack_from(F_POOL_SUMMARY, blob, o)
        o += SZ["pool_summary"]
        pools[name] = {"name_hash": v[0], "avail": v[1], "buf_count": v[2],
                       "true_min_avail": v[3], "alloc_attempts": v[5],
                       "alloc_successes": v[6], "releases": v[7]}
    b = struct.unpack_from(F_BUF_ENTRY, blob, o)
    o += SZ["buf_entry"]
    sync_buf = {"ptr": b[0], "len": b[1], "ref": b[2], "owner": b[3], "code": b[4]}
    sync_owner, sync_evt_code, rx_entries, _ = struct.unpack_from("<4B", blob, o)
    o += 4
    rx_bufs = []
    for i in range(10):
        v = struct.unpack_from(F_BUF_ENTRY, blob, o)
        o += SZ["buf_entry"]
        if i < rx_entries:
            rx_bufs.append({"ptr": v[0], "len": v[1], "ref": v[2],
                            "owner": v[3], "code": v[4]})

    if schema >= 5:
        counters = list(struct.unpack_from(CORE_TAIL_V5, blob, o))
        o += struct.calcsize(CORE_TAIL_V5)
        cr_uptime, cr_total, cr_site, cr_old, cr_new = \
            struct.unpack_from(CORE_CONN_RELEASE, blob, o)
        o += struct.calcsize(CORE_CONN_RELEASE)
        site_counts = list(struct.unpack_from(f"<{CONN_SITE_MAX}B", blob, o))
        o += CONN_SITE_MAX
        tail = struct.unpack_from(CORE_TAIL_V5_REST, blob, o)
        conn_release = {"uptime_ms": cr_uptime, "total": cr_total,
                        "site": cr_site, "old_state": cr_old,
                        "new_state": cr_new}
        # Defensive: a site id past the compiled set, or a count at such an
        # index, means this .noinit was never initialised by the firmware --
        # it is uninitialised RAM wearing the shape of evidence. Say so
        # instead of naming a line that did nothing.
        impossible = [i for i, v in enumerate(site_counts)
                      if v and i >= CONN_SITE_USED]
        if impossible or cr_site >= CONN_SITE_USED:
            conn_release["SUSPECT"] = (
                f"site id(s) beyond the compiled set of {CONN_SITE_USED}: "
                f"{impossible or cr_site} -- treat this conn evidence as "
                "UNINITIALISED, not as a finding")
    else:
        tail = struct.unpack_from(CORE_TAIL, blob, o)
        counters = list(tail[:32])
        tail = tail[32:]
        conn_release = None
        site_counts = []

    core = {
        "schema": schema, "fw_marker_hash": fw_hash, "node_identity": node,
        "uptime_ms": uptime, "boot_reset_reason": rr, "corpse_seq": seq,
        "epoch": epoch, "trigger_cause": cause,
        "trigger_cause_name": CAUSE.get(cause, f"?{cause}"),
        "trigger_count": tcount, "notify_exit_age_ms": nage,
        "ncp_packet_age_ms": cage, "suspect_start_ms": sus_ms,
        "suspect_ring_index": sus_idx, "connected_at_ms": conn_at,
        "connected": connected, "data_subscribed": data_sub,
        "telemetry_subscribed": tele_sub, "ota_active": ota,
        "reboot_taken": reboot_taken, "reboot_owner": reboot_owner,
        "flash_slot": None if flash_slot == 0xFF else flash_slot,
        "flash_enabled": flash_en,
        "channel": channels, "thread": threads, "waitobj": waitobj,
        "conn": conn, "pools": pools, "sync_evt_buf": sync_buf,
        "sync_evt_last_owner": sync_owner, "sync_evt_last_evt_code": sync_evt_code,
        "hci_rx_buf": rx_bufs, "counters": counters,
        "conn_release": conn_release, "conn_site_count": site_counts,
        # `tail` is the 7-element remainder after the counters in BOTH
        # schema branches, so these are 0-based, not 32-based.
        "tx_pending_depth": tail[0], "tx_complete_depth": tail[1],
        "wdt_feed_count": tail[2], "producer_seq": tail[3],
        "publisher_count": tail[4], "notify_timeout_drop_total": tail[5],
        "tx_complete_busy": tail[6],
    }
    return core


def decode_bank(blob: bytes, off: int, corpse_seq: int):
    v = struct.unpack_from(F_BANK_HDR, blob, off)
    magic, schema, bank, entry_size, length, crc, seq, entries, head, valid = v
    if magic != V45_BANK_MAGIC:
        raise Reject(f"bank at {off}: bad magic 0x{magic:08x}")
    if schema not in V45_SCHEMAS:
        raise Reject(f"bank {bank}: unknown schema {schema}")
    if valid != V45_BANK_MAGIC:
        raise Reject(f"bank {bank}: valid flag not set")
    if seq != corpse_seq:
        raise Reject(f"bank {bank}: corpse_seq {seq} != CORE's {corpse_seq}")

    want_entry = RING_ENTRY_SIZE if bank == 4 else SZ["trace_entry"]
    if entry_size != want_entry:
        raise Reject(f"bank {bank}: entry_size {entry_size} != {want_entry}; "
                     "produced by a differently shaped build")
    body = off + SZ["bank_hdr"]
    payload = blob[body:body + length]
    if len(payload) != length:
        raise Reject(f"bank {bank}: truncated ({len(payload)} of {length})")
    if _crc32(payload) != crc:
        raise Reject(f"bank {bank}: CRC mismatch")

    out = {"bank": bank, "name": BANK_NAMES.get(bank, f"?{bank}"),
           "entries": entries, "head": head, "entry_size": entry_size,
           "length": length}
    if bank == 4:
        n = min(entries, length // RING_ENTRY_SIZE)
        out["ring"] = [{"uptime_ms": struct.unpack_from("<I", payload, i * 40)[0],
                        "producer_heartbeat": struct.unpack_from("<I", payload, i * 40 + 4)[0],
                        "entry_count": struct.unpack_from("<I", payload, i * 40 + 8)[0],
                        "exit_count": struct.unpack_from("<I", payload, i * 40 + 12)[0]}
                       for i in range(n)]
    else:
        total = length // SZ["trace_entry"]
        n = min(entries, total)
        # Oldest-first: the ring is written head-forward and wraps.
        order = ([(head - n + i) % total for i in range(n)] if entries >= total
                 else list(range(n)))
        tr = []
        for idx in order:
            cyc, stg, ch, flags, a0, a1 = struct.unpack_from(
                F_TRACE_ENTRY, payload, idx * SZ["trace_entry"])
            tr.append({"cycles": cyc, "stage": stg,
                       "stage_name": STAGE.get(stg, f"?{stg}"),
                       "channel": ch, "arg0": a0, "arg1": a1})
        out["trace"] = tr
    return out, body + length


def decode_image(blob: bytes) -> Decoded:
    core = decode_core(blob)
    d = Decoded(core=core)
    # Schema-aware: the core grew at schema 5, so a fixed SZ["core"] would look
    # for the first bank 76 bytes short and find none -- which presents as "this
    # corpse has no banks" rather than as a decoder bug.
    off = _core_size(core["schema"])
    while off + SZ["bank_hdr"] <= len(blob):
        (magic,) = struct.unpack_from("<I", blob, off)
        if magic != V45_BANK_MAGIC:
            break
        bank, off = decode_bank(blob, off, core["corpse_seq"])
        d.banks.append(bank)
    if off < len(blob):
        d.warnings.append(f"{len(blob) - off} trailing bytes not decoded")
    return d


def decode_flash_slot(blob: bytes) -> Decoded:
    v = struct.unpack_from(F_FLASH_HDR, blob, 0)
    magic, schema, slot, length, crc, seq, uptime, tkeep, rkeep, collected, valid = v
    if magic != V45_FLASH_MAGIC:
        raise Reject(f"flash slot: bad magic 0x{magic:08x} (erased or never written)")
    if schema not in V45_SCHEMAS:
        raise Reject(f"flash slot: unknown schema {schema}")
    if valid != V45_FLASH_MAGIC:
        raise Reject("flash slot: valid flag not set -- a brownout during the "
                     "write leaves exactly this, and it is rejected on purpose")
    body = blob[SZ["flash_hdr"]:SZ["flash_hdr"] + length]
    if len(body) != length:
        raise Reject(f"flash slot: truncated ({len(body)} of {length})")
    if _crc32(body) != crc:
        raise Reject("flash slot: CRC mismatch")
    d = decode_image(body)
    d.notes.append(
        f"flash container slot={slot} collected={collected} "
        f"TRUNCATED to {tkeep} trace entries/channel and {rkeep} ring entries "
        "-- the .noinit banks are complete, this one cannot be (8 KiB slot)")
    return d


def decode_legacy(blob: bytes) -> dict:
    """v43 (schema 1) / v44 (schema 2). Kept decodable, as required."""
    magic, schema, length, crc = struct.unpack_from(CORE_HEAD, blob, 0)
    if magic != V44_CORPSE_MAGIC:
        raise Reject(f"not a v43/v44 corpse: magic 0x{magic:08x}")
    if schema not in (1, 2):
        raise Reject(f"v43/v44 decoder speaks schema 1 and 2, not {schema}")
    crc_start = struct.calcsize(CORE_HEAD)
    got = _crc32(blob[crc_start:crc_start + length])
    if got != crc:
        raise Reject(f"CRC mismatch: header 0x{crc:08x}, payload 0x{got:08x}")
    (fw_hash, node, uptime, rr, seq) = struct.unpack_from("<5I", blob, crc_start)
    wedge, trigger, stage, _pad = struct.unpack_from("<4H", blob, crc_start + 20)
    return {"schema": schema, "fw_marker_hash": fw_hash, "node_identity": node,
            "uptime_ms": uptime, "boot_reset_reason": rr, "corpse_seq": seq,
            "wedge_count": wedge, "trigger": trigger, "stage": stage,
            "stage_name": V44_STAGE.get(stage, f"?{stage}")}


# --------------------------------------------------------------------------
# Human-readable report, organised around the section 15 decision table
# --------------------------------------------------------------------------
def verdict(core: dict) -> str:
    th = {t["name"]: t for t in core["thread"]}
    ch = {c["name"]: c for c in core["channel"]}
    mpsl, btrx, txw = ch["MPSL_RX"], ch["BT_RX"], ch["TX_WORK"]
    sync = core["pools"]["sync_evt_pool"]
    sbuf = core["sync_evt_buf"]
    pended = th["MPSL Work"]["pended_on"]
    sync_wq = core["waitobj"][WAITOBJ_ORDER.index("sync_evt_pool.free")]
    rx_wq = core["waitobj"][WAITOBJ_ORDER.index("hci_rx_pool.free")]

    if pended and pended == sync_wq and sync["avail"] == 0 and sbuf["ref"] == 1:
        who = OWNER.get(core["sync_evt_last_owner"], "?")
        base = (f"SINGLETON sync_evt BUFFER HELD; the inlet is blocked. "
                f"last owner = {who}")
        if core["sync_evt_last_owner"] == 2 and mpsl["enter_total"] > mpsl["exit_total"]:
            return base + " -- and NCP entered without exiting: the fault is " \
                          "inside NCP / bt_conn_tx_notify(false)."
        return base + "."
    if pended and pended == rx_wq:
        held = [b for b in core["hci_rx_buf"] if b["ref"]]
        return (f"TRUE hci_rx_pool EXHAUSTION; {len(held)} buffer(s) held. "
                "Per-buffer owners name the holders.")
    if (mpsl["enter_total"] == mpsl["exit_total"]
            and btrx["enter_total"] > btrx["exit_total"]):
        return ("MPSL enters and exits are balanced, BT RX entered without "
                "exiting: ordinary host RX-workqueue blockage. Compare that "
                "thread's pended_on with the recorded flush sync object.")
    if core["counters"][10] and txw["enter_total"] == 0:
        return ("NCP advances but tx_complete_work NEVER ENTERED: a system-"
                "workqueue scheduling fault.")
    notify = ch["APP_NOTIFY"]
    if notify["enter_total"] > notify["exit_total"]:
        return ("notify ENTER > EXIT with everything upstream healthy: the "
                "blockage is in the app/ATT/TX path.")
    if (mpsl["enter_total"] == mpsl["exit_total"]
            and core["counters"][2] == 0):
        return ("MPSL healthy and IDLE with no message available and every "
                "watermark frozen: the SDC stopped delivering. That is BELOW "
                "the patchable layer -- escalate with the K1 Nordic "
                "known-issue list and this corpse as the evidence pack.")
    return "No decision-table row matched. Report the full dump."


def report(d: Decoded) -> str:
    c = d.core
    L = []
    L.append("=" * 74)
    L.append(f"B306 v45 CORPSE  node=0x{c['node_identity']:04x}  "
             f"seq={c['corpse_seq']}  schema={c['schema']}")
    L.append("=" * 74)
    L.append(f"cause            : {c['trigger_cause_name']}")
    L.append(f"trigger #        : {c['trigger_count']} this power cycle "
             f"(reboot_taken={c['reboot_taken']} owner={c['reboot_owner']})")
    L.append(f"uptime at capture: {c['uptime_ms'] / 1000:.3f} s   "
             f"epoch={c['epoch']}  reset_reason=0x{c['boot_reset_reason']:08x}")
    L.append(f"notify_exit age  : {c['notify_exit_age_ms']} ms")
    L.append(f"ncp_packet age   : {c['ncp_packet_age_ms']} ms")
    L.append(f"suspicion began  : {c['suspect_start_ms']} ms "
             f"(ring index {c['suspect_ring_index']})")
    L.append(f"link             : connected={c['connected']} "
             f"data_sub={c['data_subscribed']} tele_sub={c['telemetry_subscribed']} "
             f"ota={c['ota_active']}")
    L.append(f"flash            : enabled={c['flash_enabled']} slot={c['flash_slot']}")
    L.append("")
    L.append(">>> VERDICT: " + verdict(c))
    L.append("")

    L.append("-- channels " + "-" * 62)
    for ch in c["channel"]:
        mism = ""
        if ch["writer_mismatch_count"]:
            mism = (f"  *** {ch['writer_mismatch_count']} FOREIGN WRITES, "
                    f"first offender 0x{ch['first_offending_tid']:08x} -- "
                    f"this channel is CONTAMINATED and its stage is not "
                    f"trustworthy ***")
        L.append(f"  {ch['name']:<11} stage={STAGE.get(ch['stage'], ch['stage']):<22} "
                 f"age={ch['stage_age_ms']:>8} ms  seq={ch['seq']}")
        L.append(f"              enter={ch['enter_total']} exit={ch['exit_total']} "
                 f"(delta {ch['enter_total'] - ch['exit_total']})  "
                 f"writer=0x{ch['writer_tid']:08x}{mism}")

    L.append("-- threads " + "-" * 63)
    for t in c["thread"]:
        if not t["found"]:
            L.append(f"  {t['name']:<15} NOT FOUND")
            continue
        used = t["stack_size"] - t["stack_unused"]
        L.append(f"  {t['name']:<15} tid=0x{t['tid']:08x} state=0x{t['thread_state']:02x} "
                 f"prio={t['prio']:<4} stack {used}/{t['stack_size']}")
        L.append(f"                  pended_on = {_named_waitobj(c, t['pended_on'])}")

    L.append("-- pools " + "-" * 65)
    for name, p in c["pools"].items():
        L.append(f"  {name:<16} avail={p['avail']}/{p['buf_count']}  "
                 f"true_min={p['true_min_avail']}  allocs={p['alloc_successes']}")
    s = c["sync_evt_buf"]
    L.append(f"  sync_evt buffer  ptr=0x{s['ptr']:08x} ref={s['ref']} len={s['len']} "
             f"owner={OWNER.get(s['owner'], s['owner'])} evt=0x{s['code']:02x}")
    if s["ref"] == 0:
        L.append("                   (ref==0, so the owner field is STALE and "
                 "must be ignored)")
    for i, b in enumerate(c["hci_rx_buf"]):
        if b["ref"]:
            L.append(f"  hci_rx[{i}] ptr=0x{b['ptr']:08x} ref={b['ref']} "
                     f"len={b['len']} owner={OWNER.get(b['owner'], b['owner'])}")

    L.append("-- completion shadow " + "-" * 53)
    L.append(f"  tx_pending depth  = {c['tx_pending_depth']}")
    L.append(f"  tx_complete depth = {c['tx_complete_depth']}")
    L.append(f"  tx_complete_busy  = {c['tx_complete_busy']}  "
             f"pkts_avail={c['conn']['pkts_avail']} in_ll={c['conn']['in_ll']}")
    L.append(f"  watchdog feeds    = {c['wdt_feed_count']}  "
             f"(the system workqueue reached the tail of its handler that "
             f"many times)")

    L.append("-- banks " + "-" * 65)
    for b in d.banks:
        L.append(f"  {b['name']:<11} entries={b['entries']:<5} "
                 f"len={b['length']}")
        for e in (b.get("trace") or [])[-8:]:
            L.append(f"      {e['stage_name']:<24} arg0=0x{e['arg0']:08x} "
                     f"arg1=0x{e['arg1']:08x}")
    for w in d.warnings:
        L.append(f"WARNING: {w}")
    for n in d.notes:
        L.append(f"NOTE: {n}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path, help="raw corpse image (binary)")
    ap.add_argument("--flash", action="store_true",
                    help="input is a flash slot container, not a .noinit image")
    ap.add_argument("--legacy", action="store_true",
                    help="input is a v43/v44 corpse")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    blob = args.path.read_bytes()
    try:
        if args.legacy:
            out = decode_legacy(blob)
            print(json.dumps(out, indent=2) if args.json else out)
            return 0
        d = decode_flash_slot(blob) if args.flash else decode_image(blob)
    except Reject as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"core": d.core,
                          "banks": [{k: v for k, v in b.items() if k != "trace"}
                                    for b in d.banks],
                          "warnings": d.warnings, "notes": d.notes}, indent=2))
    else:
        print(report(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Decoder tests for the v45 corpse (brief section 12).

THE ONE THAT MATTERS
--------------------
A decoder's struct model is normally hand-derived from a header, and that is
exactly how a decoder comes to produce plausible nonsense. So this test does not
trust the model: it reads the REAL sizes and offsets out of the built ELF's
DWARF and compares them. If a field is added, reordered or repadded and the
decoder is not updated, this fails -- at build-gate time, not six months later
in front of a corpse nobody can read.

The rest are refusal tests. `.noinit` is not zeroed at startup, so on the first
boot after a DFU these structures hold whatever the previous image left there. A
corpse that fails ANY check must be rejected, never best-effort decoded.
"""
import struct
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]           # .../B306_Part
sys.path.insert(0, str(root / "tools"))

import bsf_v45_corpse_decode as dec                  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    return cond


# ---------------------------------------------------------------------------
# 1. The decoder's model vs the real ELF
# ---------------------------------------------------------------------------
def elf_layout(elf: Path):
    """{struct_name: (size, {member: offset})} straight out of DWARF."""
    from elftools.elf.elffile import ELFFile

    want = {"bsf_v45_core_t", "bsf_v45_channel_summary", "bsf_v45_thread_snapshot",
            "bsf_v45_waitobj_table", "bsf_v45_conn_snapshot",
            "bsf_v45_pool_snapshot", "bsf_v45_pool_summary",
            "bsf_v45_buf_entry", "bsf_v45_bank_header_t", "bsf_v45_flash_header_t",
            "bsf_v45_trace_entry", "bsf_stall_ring_entry_t"}
    out = {}
    with elf.open("rb") as f:
        e = ELFFile(f)
        if not e.has_dwarf_info():
            return out
        dw = e.get_dwarf_info()
        for cu in dw.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag not in ("DW_TAG_structure_type", "DW_TAG_typedef"):
                    continue
                name = die.attributes.get("DW_AT_name")
                if name is None:
                    continue
                name = name.value.decode()
                if name not in want:
                    continue
                sdie = die
                if die.tag == "DW_TAG_typedef":
                    try:
                        sdie = die.get_DIE_from_attribute("DW_AT_type")
                    except Exception:
                        continue
                    if sdie.tag != "DW_TAG_structure_type":
                        continue
                sz = sdie.attributes.get("DW_AT_byte_size")
                if sz is None:
                    continue
                members = {}
                for ch in sdie.iter_children():
                    if ch.tag != "DW_TAG_member":
                        continue
                    mn = ch.attributes.get("DW_AT_name")
                    mo = ch.attributes.get("DW_AT_data_member_location")
                    if mn is not None and mo is not None:
                        members[mn.value.decode()] = mo.value
                if name not in out or members:
                    out[name] = (sz.value, members)
    return out


builds = sorted((root / "builds").glob("b306-imu-relay-v45-*/firmware/zephyr/zephyr.elf"))
if not builds:
    print("v45 decoder: SKIP (no v45 build to check the layout against)")
    raise SystemExit(0)

# Merge across every v45 build. A struct only gets a DWARF entry if something
# references it, and bsf_v45_flash_header_t is only referenced when
# BSF_CORPSE_FLASH_ENABLED=1 -- which the default build is not. Checking only
# the first ELF would silently skip it, and a silently skipped layout check is
# worth nothing.
layout = {}
for elf in builds:
    for name, val in elf_layout(elf).items():
        if name not in layout or (val[1] and not layout[name][1]):
            layout[name] = val
if not layout:
    fails.append("could not read DWARF from the v45 ELF -- the layout check is "
                 "the point of this test and cannot be silently skipped")
else:
    expect = {
        "bsf_v45_core_t": dec.SZ["core"],
        "bsf_v45_channel_summary": dec.SZ["channel"],
        "bsf_v45_thread_snapshot": dec.SZ["thread"],
        "bsf_v45_waitobj_table": dec.SZ["waitobj"],
        "bsf_v45_conn_snapshot": dec.SZ["conn"],
        "bsf_v45_pool_summary": dec.SZ["pool_summary"],
        "bsf_v45_buf_entry": dec.SZ["buf_entry"],
        "bsf_v45_pool_snapshot": dec.SZ["pool_snapshot"],
        "bsf_v45_bank_header_t": dec.SZ["bank_hdr"],
        "bsf_v45_flash_header_t": dec.SZ["flash_hdr"],
        "bsf_v45_trace_entry": dec.SZ["trace_entry"],
        "bsf_stall_ring_entry_t": dec.SZ["ring_entry"],
    }
    for name, want in expect.items():
        if name not in layout:
            fails.append(f"{name} not found in DWARF")
            continue
        got = layout[name][0]
        check(got == want,
              f"{name}: firmware says {got} bytes, decoder models {want}")

    # Field offsets inside the CORE -- the ones a wrong model silently shifts.
    if "bsf_v45_core_t" in layout:
        members = layout["bsf_v45_core_t"][1]
        model = {
            "magic": 0, "schema": 4, "length": 6, "crc32": 8,
            "fw_marker_hash": struct.calcsize(dec.CORE_HEAD),
        }
        base = struct.calcsize(dec.CORE_HEAD) + struct.calcsize(dec.CORE_BODY_A)
        model["connected"] = base
        model["channel"] = base + struct.calcsize(dec.CORE_FLAGS)
        model["thread"] = model["channel"] + 4 * dec.SZ["channel"]
        model["waitobj"] = model["thread"] + 5 * dec.SZ["thread"]
        model["conn"] = model["waitobj"] + dec.SZ["waitobj"]
        model["pools"] = model["conn"] + dec.SZ["conn"]
        model["counters"] = model["pools"] + dec.SZ["pool_snapshot"]
        model["valid"] = dec.SZ["core"] - 4
        for f, off in model.items():
            if f in members:
                check(members[f] == off,
                      f"bsf_v45_core_t.{f}: firmware offset {members[f]}, "
                      f"decoder models {off}")


# ---------------------------------------------------------------------------
# 2. Round trip: build a synthetic corpse the way the firmware does, decode it
# ---------------------------------------------------------------------------
def build_core(*, seq=7, cause=1, schema=dec.V45_SCHEMA,
               node=0xB102, break_crc=False, unset_valid=False,
               mismatch=0, sync_ref=1, sync_avail=0,
               mpsl_pended=0, sync_wq=0x20001000):
    body = bytearray()
    body += struct.pack(dec.CORE_BODY_A,
                        0xDEADBEEF, node, 123456, 0x04, seq, 3,
                        cause, 1,
                        20000, 20000, 1500, 42, 1000)
    body += struct.pack(dec.CORE_FLAGS, 1, 1, 1, 0, 1, 3, 0xFF, 0)
    for i in range(4):
        enter, exit_ = (5, 4) if i == 0 else (5, 5)
        body += struct.pack(dec.F_CHANNEL,
                            100 + i, 20000, 0, 0, enter, exit_, 900, 800,
                            0x20002000 + i, mismatch if i == 0 else 0,
                            0xCAFE if mismatch and i == 0 else 0, 128,
                            5 if i == 0 else 0, 0)
    for i in range(5):
        pended = mpsl_pended if i == 0 else 0
        body += struct.pack(dec.F_THREAD,
                            0x20003000 + i, pended, 0x20004000, 0x20005000,
                            1024, 400, 100 + i, 0x02, 6, 1, 0)
    body += struct.pack(dec.F_WAITOBJ, 0x1001, 0x1002, 0x1003, 0x1004,
                        0x1005, sync_wq, 0x1007, 0x1008)
    body += struct.pack(dec.F_CONN, 0x20006000, 0, 0, 0x0001, 2, 0, 1, 1,
                        0, 0, 6, 1, 0x20007000)
    pools = [("sync_evt_pool", sync_avail, 1), ("hci_rx_pool", 10, 10),
             ("att_pool", 0, 8), ("acl_tx_pool", 0, 8),
             ("hci_cmd_pool", 2, 2), ("fragments", 1, 1)]
    for name, avail, count in pools:
        h = 2166136261
        for b in name.encode():
            h = ((h ^ b) * 16777619) & 0xFFFFFFFF
        body += struct.pack(dec.F_POOL_SUMMARY, h, avail, count, 0, 0,
                            0, 1000, 990)
    body += struct.pack(dec.F_BUF_ENTRY, 0x20008000, 30, sync_ref, 2, 0x13, 0)
    body += struct.pack("<4B", 2, 0x13, 0, 0)
    for _ in range(10):
        body += struct.pack(dec.F_BUF_ENTRY, 0, 0, 0, 0, 0, 0)
    counters = [0] * 32
    counters[10] = 50000        # ncp_event_count
    counters[2] = 12345         # msg_get_ok
    body += struct.pack(dec.CORE_TAIL, *counters, 1, 0, 5400, 900000,
                        800000, 3, 0)

    length = len(body)
    crc = dec._crc32(bytes(body)) ^ (0xFFFFFFFF if break_crc else 0)
    head = struct.pack(dec.CORE_HEAD, dec.V45_CORPSE_MAGIC, schema, length, crc)
    valid = struct.pack("<I", 0 if unset_valid else dec.V45_CORPSE_MAGIC)
    return head + bytes(body) + valid


def build_bank(bank, seq, entries=4):
    esz = dec.RING_ENTRY_SIZE if bank == 4 else dec.SZ["trace_entry"]
    total = dec.RING_CAPACITY if bank == 4 else dec.V45_TRACE_ENTRIES
    payload = bytearray(total * esz)
    if bank != 4:
        for i in range(entries):
            struct.pack_into(dec.F_TRACE_ENTRY, payload, i * esz,
                             1000 + i, 1 + i, bank, 0, 0xAA00 + i, 0xBB00 + i)
    hdr = struct.pack(dec.F_BANK_HDR, dec.V45_BANK_MAGIC, dec.V45_SCHEMA,
                      bank, esz, len(payload), dec._crc32(bytes(payload)),
                      seq, entries, entries % total, dec.V45_BANK_MAGIC)
    return hdr + bytes(payload)


core = build_core()
check(len(core) == dec.SZ["core"],
      f"synthetic core is {len(core)} bytes, model says {dec.SZ['core']}")

image = core + b"".join(build_bank(b, 7) for b in range(5))
d = dec.decode_image(image)
check(d.core["corpse_seq"] == 7, "seq round-trips")
check(d.core["trigger_cause_name"].startswith("NOTIFY_EXIT"), "cause decodes")
check(d.core["node_identity"] == 0xB102, "node identity round-trips")
check(len(d.banks) == 5, f"all five banks decode (got {len(d.banks)})")
check(d.banks[0]["name"] == "MPSL_RX", "bank 0 is MPSL_RX")
check(d.banks[4]["name"] == "RING", "bank 4 is RING")
check(d.core["pools"]["sync_evt_pool"]["buf_count"] == 1,
      "sync_evt_pool is modelled as the singleton")


# ---------------------------------------------------------------------------
# 3. Refusal tests
# ---------------------------------------------------------------------------
def rejects(blob, why, flash=False, legacy=False):
    try:
        if flash:
            dec.decode_flash_slot(blob)
        elif legacy:
            dec.decode_legacy(blob)
        else:
            dec.decode_image(blob)
    except dec.Reject:
        return True
    fails.append(f"should have REJECTED: {why}")
    return False


rejects(build_core(break_crc=True), "corrupt CRC")
rejects(build_core(unset_valid=True), "valid flag not written (capture "
                                      "interrupted)")
rejects(build_core(schema=99), "unknown schema")
rejects(build_core()[:100], "truncated core")
bad_len = bytearray(build_core())
struct.pack_into("<H", bad_len, 6, 999)
rejects(bytes(bad_len), "length that does not match the schema")

# A bank whose corpse_seq disagrees with the CORE: two different captures
# spliced together, which is exactly what a partial overwrite looks like.
rejects(core + build_bank(0, 999), "bank corpse_seq != CORE corpse_seq")

# A bank produced by a differently shaped build.
b = bytearray(build_bank(0, 7))
struct.pack_into("<B", b, 7, 99)                 # entry_size
rejects(core + bytes(b), "bank entry_size from another build")

b = bytearray(build_bank(0, 7))
b[-1] ^= 0xFF                                     # corrupt the payload tail
rejects(core + bytes(b), "bank CRC mismatch")

# v43/v44 corpse offered to the v45 decoder.
legacy = struct.pack(dec.CORE_HEAD, dec.V44_CORPSE_MAGIC, 2, 100, 0) + b"\0" * 200
rejects(legacy, "a v43/v44 corpse must be refused by the v45 path, with a "
                "message that says which decoder to use")

# ...and accepted by the legacy path, because old corpses stay decodable.
lbody = struct.pack("<5I", 0xAAAA, 0xB101, 5000, 4, 3) + struct.pack("<4H", 3, 1, 19, 0)
lbody += b"\0" * 40
lcrc = dec._crc32(lbody)
lblob = struct.pack(dec.CORE_HEAD, dec.V44_CORPSE_MAGIC, 2, len(lbody), lcrc) + lbody
try:
    out = dec.decode_legacy(lblob)
    check(out["schema"] == 2 and out["stage_name"] == "ATT_ALLOC_FOREVER",
          "a v44 corpse still decodes, stage 19 = ATT_ALLOC_FOREVER")
except dec.Reject as e:
    fails.append(f"v44 corpse must still decode: {e}")


# ---------------------------------------------------------------------------
# 4. Flash container, including the brownout partial
# ---------------------------------------------------------------------------
body = core + b"".join(build_bank(b, 7, entries=4) for b in range(5))
fh = struct.pack(dec.F_FLASH_HDR, dec.V45_FLASH_MAGIC, dec.V45_SCHEMA, 0,
                 len(body), dec._crc32(body), 7, 12345, 32, 48, 0,
                 dec.V45_FLASH_MAGIC)
try:
    fd = dec.decode_flash_slot(fh + body)
    check(any("TRUNCATED" in n for n in fd.notes),
          "the flash container must DECLARE its truncation, not hide it")
except dec.Reject as e:
    fails.append(f"valid flash slot must decode: {e}")

partial = struct.pack(dec.F_FLASH_HDR, dec.V45_FLASH_MAGIC, dec.V45_SCHEMA, 0,
                      len(body), dec._crc32(body), 7, 12345, 32, 48, 0, 0)
rejects(partial + body, "a brownout mid-write leaves valid=0 and must be "
                        "rejected, never half-decoded", flash=True)
rejects(b"\xff" * 200, "an erased flash slot", flash=True)


# ---------------------------------------------------------------------------
# 5. pended_on naming, writer-mismatch reporting, and the decision table
# ---------------------------------------------------------------------------
wedged = build_core(cause=1, sync_ref=1, sync_avail=0,
                    mpsl_pended=0x20001000, sync_wq=0x20001000)
d2 = dec.decode_image(wedged + b"".join(build_bank(b, 7) for b in range(5)))
v = dec.verdict(d2.core)
check("SINGLETON" in v and "PRIO_NCP" in v,
      f"the singleton-held row of the decision table must fire; got: {v}")
txt = dec.report(d2)
check("sync_evt_pool.free" in txt,
      "pended_on must be printed as a NAME when it matches a known wait object")

contaminated = build_core(mismatch=17)
d3 = dec.decode_image(contaminated + b"".join(build_bank(b, 7) for b in range(5)))
txt3 = dec.report(d3)
check("FOREIGN WRITES" in txt3 and "CONTAMINATED" in txt3,
      "a channel with foreign writes must be reported as untrustworthy, "
      "loudly -- that is the v44 failure this whole design exists to avoid")

# A stale owner on a freed buffer must be called out, not read as evidence.
freed = build_core(sync_ref=0, sync_avail=1)
txt4 = dec.report(dec.decode_image(
    freed + b"".join(build_bank(b, 7) for b in range(5))))
check("STALE" in txt4,
      "with ref==0 the sync_evt owner field is stale and the report must say so")

# The below-the-patchable-layer row.
idle = bytearray(build_core())
# zero msg_get_ok (counters[2]) so the SDC-stopped row can match
off = (struct.calcsize(dec.CORE_HEAD) + struct.calcsize(dec.CORE_BODY_A)
       + struct.calcsize(dec.CORE_FLAGS) + 4 * dec.SZ["channel"]
       + 5 * dec.SZ["thread"] + dec.SZ["waitobj"] + dec.SZ["conn"]
       + dec.SZ["pool_snapshot"])
struct.pack_into("<I", idle, off + 2 * 4, 0)      # counters[2] = msg_get_ok
struct.pack_into("<I", idle, off + 10 * 4, 0)     # counters[10] = ncp_event_count
for i in range(4):
    coff = (struct.calcsize(dec.CORE_HEAD) + struct.calcsize(dec.CORE_BODY_A)
            + struct.calcsize(dec.CORE_FLAGS) + i * dec.SZ["channel"])
    struct.pack_into("<2I", idle, coff + 4 * 4, 5, 5)   # enter == exit
blen = dec.SZ["core"] - 4 - struct.calcsize(dec.CORE_HEAD)
struct.pack_into("<I", idle, 8,
                 dec._crc32(bytes(idle[struct.calcsize(dec.CORE_HEAD):
                                       struct.calcsize(dec.CORE_HEAD) + blen])))
d5 = dec.decode_image(bytes(idle) + b"".join(build_bank(b, 7) for b in range(5)))
v5 = dec.verdict(d5.core)
check("BELOW" in v5 or "SDC" in v5,
      f"MPSL idle with nothing to fetch must escalate below the patchable "
      f"layer; got: {v5}")


if fails:
    print("v45 decoder: FAIL")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("v45 decoder: PASS")

#!/usr/bin/env python3
"""Read net_buf pool occupancy out of a RAM dump. F3b's actual instrument.

WHY THIS IS A SEPARATE TOOL
---------------------------
parse_ram_dump.py knows pool ADDRESSES -- it needs them to turn a `pended_on`
pointer into "hci_rx_pool.free.wait_q". It has never read pool CONTENTS, so the
F3b question ("is the well-used pool leaking buffers?") had no instrument at
all. The RAM was sampled on 2026-08-09 with nothing able to read it.

WHAT IT REPORTS, AND THE ONE NUMBER THAT MATTERS
------------------------------------------------
Zephyr initialises BOTH `avail_count` and `uninit_count` to `buf_count`.
`avail_count` (atomic_t, 4 bytes -- NOT the 16 bits its neighbours use) is
decremented on alloc and incremented on free. `uninit_count` is decremented the
first time each buffer is carved out of the pool's storage and never goes back
up. So an uninit buffer is ALSO an available one, and the two must not be
subtracted from each other:

    outstanding = buf_count - avail_count          <- the leak number
    ever_used   = buf_count - uninit_count         <- the freshness number

THIS COMMENT IS THE CORRECTED ONE. The first version of this file subtracted
both and read avail_count as int16; every used pool came out with a negative
"outstanding" and the tool printed IMPOSSIBLE across the board. It was caught
on its first run by the runbook rule that says a new reading script gets one
hand-check against raw data before it is believed -- which is the entire reason
that rule exists, and this is its first catch.

FRESHNESS IS PART OF THE READING. A pool whose uninit_count still equals
buf_count has never been used, and on such a pool this tool can prove nothing
-- a reset wipes the usage history, so a dump taken after one answers a
question about a board that no longer exists. That case is reported as
NO EVIDENCE, never as "no leak".
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

RAM_BASE = 0x20000000
POOLS = ("att_pool", "acl_tx_pool", "fragments", "hci_cmd_pool", "hci_rx_pool",
         "sync_evt_pool", "discardable_pool", "num_complete_pool")


def elf_pools(elf: Path):
    from elftools.elf.elffile import ELFFile

    with elf.open("rb") as fh:
        e = ELFFile(fh)
        syms = {}
        for sec in e.iter_sections():
            if not hasattr(sec, "iter_symbols"):
                continue
            for s in sec.iter_symbols():
                if s.name:
                    syms.setdefault(s.name, s["st_value"])
        off, size = {}, None
        if e.has_dwarf_info():
            for cu in e.get_dwarf_info().iter_CUs():
                for die in cu.iter_DIEs():
                    if (die.tag == "DW_TAG_structure_type" and
                            die.attributes.get("DW_AT_name") and
                            die.attributes["DW_AT_name"].value == b"net_buf_pool"):
                        size = die.attributes.get("DW_AT_byte_size")
                        size = size.value if size else None
                        for ch in die.iter_children():
                            n = ch.attributes.get("DW_AT_name")
                            m = ch.attributes.get("DW_AT_data_member_location")
                            if n and m:
                                off[n.value.decode()] = m.value
        return syms, off, size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dump", type=Path)
    ap.add_argument("--elf", type=Path, required=True)
    args = ap.parse_args()

    ram = args.dump.read_bytes()
    syms, off, _ = elf_pools(args.elf)

    need = ("avail_count", "uninit_count", "buf_count")
    missing = [n for n in need if n not in off]
    if missing:
        print(f"net_buf_pool DWARF is missing {missing}; cannot read pools",
              file=sys.stderr)
        return 2

    def u16(addr):
        return struct.unpack_from("<h", ram, addr - RAM_BASE)[0]

    def u32(addr):
        return struct.unpack_from("<i", ram, addr - RAM_BASE)[0]

    print(f"RAM dump {args.dump.name}: {len(ram)} B at 0x{RAM_BASE:08x}")
    print(f"net_buf_pool offsets: " +
          " ".join(f"{n}=+{off[n]}" for n in need))
    print()
    print(f"{'pool':<20}{'buf':>5}{'avail':>7}{'uninit':>8}"
          f"{'used':>6}{'out':>5}  verdict")
    print("-" * 74)

    verdicts = []
    for name in POOLS:
        if name not in syms:
            continue
        base = syms[name]
        buf = u16(base + off["buf_count"])
        avail = u32(base + off["avail_count"])
        uninit = u16(base + off["uninit_count"])
        out = buf - avail
        used = buf - uninit
        if out < 0 or out > buf:
            v = "IMPOSSIBLE -- offsets or dump wrong"
        elif used == 0:
            v = "NO EVIDENCE (never used)"
        elif out == 0:
            v = f"clean ({used} ever carved, all returned)"
        else:
            v = f"{out} OUTSTANDING"
        verdicts.append((name, buf, avail, uninit, out, used, v))
        print(f"{name:<20}{buf:>5}{avail:>7}{uninit:>8}{used:>6}{out:>5}  {v}")

    print()
    touched = [v for v in verdicts if v[5] > 0]
    if not touched:
        print("EVERY pool is untouched. This dump cannot answer a leak "
              "question -- it is from a board whose usage history was reset.")
        return 0
    leaked = [v for v in touched if v[4] > 0]
    depth = max(v[5] for v in touched)
    print(f"{len(touched)} pool(s) carry usage history; "
          f"{len(leaked)} with buffers outstanding; "
          f"deepest pool ever reached {depth} buffer(s).")
    for name, _, _, _, out, _, _ in leaked:
        print(f"  {name}: {out} outstanding at sample time")
    if depth <= 2:
        print("CAUTION: no pool was ever driven past 2 buffers. This is a "
              "lightly-used board, so 'no leak' here is a weak statement -- "
              "it cannot speak for a pool under real load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

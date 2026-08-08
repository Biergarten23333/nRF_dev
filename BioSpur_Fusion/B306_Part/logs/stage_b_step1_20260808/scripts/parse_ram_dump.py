#!/usr/bin/env python3
"""Walk a raw 256 KiB RAM dump against the ELF and print every thread's state.

WHY THIS EXISTS BEFORE THE WEDGED-BOARD SESSION
-----------------------------------------------
The wedged-board dump is one shot. If this parsing does not work on a healthy
board today, it will not work on the corpse tomorrow, and there will be no
second chance to find out. So G3 runs it on a healthy dump and requires sane
output: every thread in a plausible state, and nothing pended on a Bluetooth
pool.

WHAT IT READS, AND WHY THAT AND NOT THE CORPSE
----------------------------------------------
The v45 corpse is the *node's own* account of the wedge, captured by the
detector. This is the independent one: raw memory, walked with symbols from the
ELF, owing nothing to any firmware code having run correctly. If the detector
never fires -- which is the scenario that ends Stage B -- this is the only
evidence there is.

`_kernel.threads` is the head of Zephyr's all-threads list (CONFIG_THREAD_MONITOR
is on), each `struct k_thread` linked by `next_thread`. From each we take
`base.thread_state`, `base.pended_on`, `base.prio`, `callee_saved.psp`, the
name, and the stack bounds -- the same fields the corpse carries, so the two can
be cross-checked against each other.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

RAM_BASE = 0x20000000
RAM_SIZE = 0x40000

THREAD_STATE_BITS = [
    (1 << 0, "DUMMY"), (1 << 1, "PENDING"), (1 << 2, "PRESTART"),
    (1 << 3, "DEAD"), (1 << 4, "SUSPENDED"), (1 << 5, "ABORTING"),
    (1 << 6, "SUSPENDING"), (1 << 7, "QUEUED"),
]


class Dump:
    def __init__(self, blob: bytes, base: int = RAM_BASE):
        self.blob, self.base = blob, base

    def __contains__(self, addr: int) -> bool:
        return self.base <= addr < self.base + len(self.blob)

    def u32(self, addr: int) -> int:
        if addr not in self:
            raise KeyError(f"0x{addr:08x} outside the dump")
        return struct.unpack_from("<I", self.blob, addr - self.base)[0]

    def u8(self, addr: int) -> int:
        if addr not in self:
            raise KeyError(f"0x{addr:08x} outside the dump")
        return self.blob[addr - self.base]

    def cstr(self, addr: int, maxlen: int = 32) -> str:
        if addr not in self:
            return ""
        raw = self.blob[addr - self.base: addr - self.base + maxlen]
        return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def elf_info(elf: Path):
    """symbol table + the DWARF offsets we need inside struct k_thread."""
    from elftools.elf.elffile import ELFFile

    syms, offsets, sizes = {}, {}, {}
    with elf.open("rb") as f:
        e = ELFFile(f)
        for sec in e.iter_sections():
            if sec.header["sh_type"] != "SHT_SYMTAB":
                continue
            for s in sec.iter_symbols():
                if s.name and s.entry["st_value"]:
                    syms.setdefault(s.name, s.entry["st_value"])

        if not e.has_dwarf_info():
            return syms, offsets, sizes
        dw = e.get_dwarf_info()

        def members(die):
            out = {}
            for ch in die.iter_children():
                if ch.tag != "DW_TAG_member":
                    continue
                n = ch.attributes.get("DW_AT_name")
                o = ch.attributes.get("DW_AT_data_member_location")
                if n is not None and o is not None:
                    out[n.value.decode()] = o.value
            return out

        want = {"k_thread", "_thread_base", "_callee_saved", "_thread_stack_info",
            "net_buf_pool", "k_queue", "k_sem"}
        for cu in dw.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_structure_type":
                    continue
                nm = die.attributes.get("DW_AT_name")
                if nm is None:
                    continue
                nm = nm.value.decode()
                if nm in want:
                    m = members(die)
                    if m:
                        offsets[nm] = m
                        sz = die.attributes.get("DW_AT_byte_size")
                        if sz is not None:
                            sizes[nm] = sz.value
    return syms, offsets, sizes


def state_names(bits: int) -> str:
    if bits == 0:
        return "RUNNING/READY"
    got = [n for m, n in THREAD_STATE_BITS if bits & m]
    return "|".join(got) if got else f"0x{bits:02x}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dump", type=Path)
    ap.add_argument("--elf", type=Path, required=True)
    ap.add_argument("--expect-healthy", action="store_true",
                    help="G3 mode: exit non-zero unless every thread is sane "
                         "and nothing is pended on a Bluetooth pool")
    args = ap.parse_args()

    blob = args.dump.read_bytes()
    if len(blob) != RAM_SIZE:
        print(f"[warn] dump is {len(blob)} B, expected {RAM_SIZE}", file=sys.stderr)
    d = Dump(blob)
    syms, off, sizes = elf_info(args.elf)

    for need in ("k_thread", "_thread_base"):
        if need not in off:
            print(f"REJECTED: no DWARF for struct {need}", file=sys.stderr)
            return 2
    kt, tb = off["k_thread"], off["_thread_base"]
    cs = off.get("_callee_saved", {})
    si = off.get("_thread_stack_info", {})

    if "_kernel" not in syms:
        print("REJECTED: no _kernel symbol in the ELF", file=sys.stderr)
        return 2

    # _kernel.threads -- the head of the all-threads list.
    kernel = syms["_kernel"]
    threads_off = None
    for cu_name, m in off.items():
        if cu_name == "z_kernel" and "threads" in m:
            threads_off = m["threads"]
    if threads_off is None:
        from elftools.elf.elffile import ELFFile
        with args.elf.open("rb") as f:
            e = ELFFile(f)
            for cu in e.get_dwarf_info().iter_CUs():
                for die in cu.iter_DIEs():
                    if die.tag != "DW_TAG_structure_type":
                        continue
                    nm = die.attributes.get("DW_AT_name")
                    if nm is None or nm.value != b"z_kernel":
                        continue
                    for ch in die.iter_children():
                        if ch.tag == "DW_TAG_member" and \
                           ch.attributes.get("DW_AT_name") and \
                           ch.attributes["DW_AT_name"].value == b"threads":
                            threads_off = ch.attributes["DW_AT_data_member_location"].value
                    if threads_off is not None:
                        break
                if threads_off is not None:
                    break
    if threads_off is None:
        print("REJECTED: could not locate _kernel.threads via DWARF",
              file=sys.stderr)
        return 2

    # Wait-object addresses worth naming.
    #
    # DERIVED, not guessed. A thread blocked in net_buf_alloc() pends in
    # k_lifo_get(&pool->free, ...), and k_queue_get() pends on &queue->wait_q,
    # so the address is:
    #     &pool  +  offsetof(net_buf_pool, free)
    #            +  offsetof(k_lifo, _queue)      (0)
    #            +  offsetof(k_queue, wait_q)
    # On this build that comes to pool+8. It was originally written as "+8 and
    # maybe +12", which is exactly the sort of hedge that produces a decoder
    # nobody can trust -- so it is read out of DWARF instead.
    nbp = off.get("net_buf_pool", {})
    kq = off.get("k_queue", {})
    ksem = off.get("k_sem", {})
    pool_waitq = nbp.get("free", 0) + kq.get("wait_q", 8)
    sem_waitq = ksem.get("wait_q", 0)

    named_waits = {}
    for pool in ("att_pool", "acl_tx_pool", "fragments", "hci_cmd_pool",
                 "hci_rx_pool", "sync_evt_pool", "discardable_pool"):
        if pool in syms:
            named_waits[syms[pool] + pool_waitq] = f"{pool}.free.wait_q"
    for sem in ("notify_job_sem", "notify_idle_sem", "publisher_sem",
                "uart_data_sem", "uart_tx_done"):
        if sem in syms:
            named_waits[syms[sem] + sem_waitq] = sem

    print(f"RAM dump {args.dump.name}: {len(blob)} B at 0x{RAM_BASE:08x}")
    print(f"ELF {args.elf.name}: _kernel=0x{kernel:08x} "
          f"threads@+0x{threads_off:x}  sizeof(k_thread)={sizes.get('k_thread','?')}")
    print()
    hdr = (f"{'thread':<22} {'tid':<10} {'state':<22} {'prio':>4} "
           f"{'pended_on':<12} {'stack used/size':>16}")
    print(hdr)
    print("-" * len(hdr))

    problems, count = [], 0
    cur = d.u32(kernel + threads_off)
    seen = set()
    while cur and cur in d and cur not in seen and count < 64:
        seen.add(cur)
        count += 1
        base = cur + kt.get("base", 0)
        st = d.u8(base + tb["thread_state"])
        # `prio` lives in an ANONYMOUS union with `preempt`, so DWARF exposes no
        # member for it and tb["prio"] would KeyError -- at the bench, on the one
        # dump that matters. It sits immediately after thread_state on
        # little-endian (prio, then sched_locked, aliased as uint16 preempt).
        prio_off = tb.get("prio", tb["thread_state"] + 1)
        prio = d.u8(base + prio_off)
        prio = prio - 256 if prio > 127 else prio
        pended = d.u32(base + tb["pended_on"])
        name = ""
        if "name" in kt:
            name = d.cstr(cur + kt["name"])
        psp = d.u32(cur + kt["callee_saved"] + cs["psp"]) if "callee_saved" in kt and "psp" in cs else 0

        used = size = 0
        if "stack_info" in kt and si:
            s_start = d.u32(cur + kt["stack_info"] + si.get("start", 0))
            size = d.u32(cur + kt["stack_info"] + si.get("size", 4))
            if psp and s_start:
                used = (s_start + size) - psp

        pname = named_waits.get(pended, f"0x{pended:08x}" if pended else "-")
        print(f"{name or '(unnamed)':<22} 0x{cur:08x} {state_names(st):<22} "
              f"{prio:>4} {pname:<12} {used:>7}/{size:<8}")

        if st & (1 << 3):
            problems.append(f"{name}: DEAD")
        if st & (1 << 5):
            problems.append(f"{name}: ABORTING")
        if pended and pname.endswith(".wait_q"):
            problems.append(f"{name}: PENDED ON A BLUETOOTH POOL ({pname})")
        if size and used > size:
            problems.append(f"{name}: stack overflow ({used} > {size})")

        cur = d.u32(cur + kt["next_thread"]) if "next_thread" in kt else 0

    print(f"\n{count} threads walked.")

    # The .noinit landmarks the corpse lives in.
    print("\n-- v45 .noinit landmarks -------------------------------------")
    for sym, note in (("stall_ring", "trajectory ring"),
                      ("bsf_v45_ch", "four trace channels"),
                      ("bsf_v45_core", "corpse CORE"),
                      ("bsf_v45_bank", "bank headers"),
                      ("retained_corpse", "v43/v44 corpse"),
                      ("retained_stall", "retained stall state")):
        if sym not in syms:
            print(f"  {sym:<18} NOT IN ELF")
            continue
        a = syms[sym]
        inside = a in d
        extra = ""
        if inside:
            try:
                extra = f"first word 0x{d.u32(a):08x}"
                if sym == "stall_ring":
                    extra += f", magic {'OK' if d.u32(a) == 0x52334236 else 'absent'}"
                if sym == "bsf_v45_core":
                    m = d.u32(a)
                    extra += f", magic {'CP45' if m == 0x35345043 else 'absent'}"
            except KeyError:
                extra = "unreadable"
        print(f"  {sym:<18} 0x{a:08x} {'in dump' if inside else 'OUTSIDE DUMP':<12} "
              f"{note:<22} {extra}")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
    if args.expect_healthy:
        if problems:
            print("\nG3 healthy-board check: FAIL")
            return 1
        if count < 8:
            print(f"\nG3 healthy-board check: FAIL (only {count} threads walked; "
                  "the list walk is not working)")
            return 1
        print("\nG3 healthy-board check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

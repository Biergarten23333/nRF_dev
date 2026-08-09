#!/usr/bin/env python3
"""Source contract: state that protects the corpse must outlive the reboot.

THE RULE
--------
Anything that gates, guards, indexes or describes a `.noinit` corpse object must
itself survive a reboot -- i.e. live in `.noinit`, or be provably re-derived
before anything can read or corrupt what it protects.

Evidence in `.noinit` with its guard in `.bss` is the mismatch, and it is the
same thinking error as putting bookkeeping inside the CRC range.

WHY IT EXISTS
-------------
Three instances of one bug class were found one at a time:

  1. reboot_taken / reboot_owner written after the CRC   (schema 3 -> 4)
  2. flash_slot written after the CRC at early boot      (schema 3 -> 4)
  3. bsf_v45_frozen in .bss, zeroed at boot, while the trace storage it gates
     is in .noinit -- so the BLE stack, started 32 lines before
     bsf_v45_init(), overwrote the frozen banks and every bank CRC failed.

The third one is what says the sweep was never done. This test is the sweep,
kept honest: EVERY v45/corpse/ring RAM object must be classified below. An
unclassified symbol is a failure, so adding a new guard without declaring its
lifetime cannot pass silently -- which is exactly how the third instance arrived.

CLASSES
  EVIDENCE  the corpse itself and its payload            -> must be .noinit
  GUARD     gates/indexes/describes EVIDENCE             -> must be .noinit
  DERIVED   may be .bss, but ONLY with a stated re-derivation point that
            happens before anything can read or corrupt what it protects
  TRANSIENT unrelated to corpse integrity                -> anywhere

Run:  test_v45_lifetime_contract.py [--elf <zephyr.elf>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# name -> (class, note). Every RAM symbol matching SYMBOL_RE must appear here.
CLASSIFY: dict[str, tuple[str, str]] = {
    # --- the corpse and its payload ---------------------------------------
    "bsf_v45_core":        ("EVIDENCE", "the corpse CORE"),
    "bsf_v45_bank":        ("EVIDENCE", "bank headers"),
    "bsf_v45_ch":          ("EVIDENCE", "trace channel storage = the bank payload"),
    "stall_ring":          ("EVIDENCE", "trajectory ring, incl. its own frozen/reason/fidx"),
    "retained_corpse":     ("EVIDENCE", "v43/v44 corpse"),
    "retained_stall":      ("EVIDENCE", "retained stall diagnostics"),
    "retained_reboot":     ("EVIDENCE", "the one-per-power-cycle reboot budget"),
    # R4/A2. EVIDENCE, not TRANSIENT: a transition recorded microseconds
    # before a reset must still name the line afterwards, which is the whole
    # point. Both are .noinit and captured by value into the corpse.
    "bsf_v45_conn_rel":    ("EVIDENCE", "which bt_conn_set_state site fired"),
    "bsf_v45_conn_site_count": ("EVIDENCE", "per-site transition counts"),
    # The watchdog witness. EVIDENCE, not TRANSIENT: its entire purpose is to
    # be read AFTER a reset that left no corpse, so a `.bss` copy would be
    # zeroed by exactly the event it exists to record -- the same lifetime
    # trap that cost this project bsf_v45_frozen and v45_flash_slot_next.
    "bsf_v45_dog":         ("EVIDENCE", "watchdog-reset witness: dwell state at "
                            "the last detector tick, promoted on a DOG boot"),

    # --- guards: must survive ---------------------------------------------
    # THE gate on every trace write into bsf_v45_ch (bsf_v45_trace.h:291,339,
    # 349). It is allowed to stay in .bss ONLY because it is re-derived from the
    # CRC-validated CORE at PRE_KERNEL_1, before any thread exists and therefore
    # before any trace write is possible. That claim is not taken on trust --
    # DERIVED_REQUIRES below makes the test fail if the SYS_INIT hook that makes
    # it true is missing or moved to a later init level.
    "bsf_v45_frozen":      ("DERIVED", "re-derived by bsf_v45_early_init() at "
                                       "PRE_KERNEL_1, before any trace writer runs"),
    # ELIMINATED, not moved: the slot is now derived from corpse_seq, which
    # lives in .noinit with the corpse. Kept here so that reintroducing a .bss
    # slot counter under this name fails instead of silently regressing.
    "v45_flash_slot_next": ("GUARD", "indexes which flash slot the next corpse "
                                     "is persisted to. In .bss it restarts at 0 "
                                     "every boot, so the 2-slot rotation is dead "
                                     "and each boot overwrites slot 0."),

    # --- derived before use ------------------------------------------------
    "v45_corpse_present":  ("DERIVED", "re-derived from bsf_v45_core_validate()"),
    "corpse_present":      ("DERIVED", "re-derived from the v43/v44 corpse"),
    "corpse_pages_total":  ("DERIVED", "re-derived with corpse_present"),
    "v45_thread":          ("DERIVED", "re-derived by v45_find_threads()"),
    "v45_ring":            ("DERIVED", "re-bound by bsf_v45_init()"),
    "v45_ring_lock":       ("DERIVED", "re-bound by bsf_v45_init()"),
    "v45_budget_take":     ("DERIVED", "re-bound by bsf_v45_init()"),
    "v45_det":             ("DERIVED", "detector dwell/arm state; the one-shot it "
                                       "appears to hold (trigger_count) is really "
                                       "guarded by retained_reboot, which is .noinit"),
    "stall_ring_boot_result": ("DERIVED", "set by the ring's own boot path"),
    "v45_pools_seeded":    ("DERIVED", "re-seeded on first use"),
    "v45_true_min_avail":  ("DERIVED", "re-seeded; captured BY VALUE into the corpse"),

    # --- transient / captured by value at capture time ---------------------
    "bsf_v45_cnt":         ("TRANSIENT", "live counters, copied into the corpse"),
    "v45_alloc_successes": ("TRANSIENT", "live counters, copied into the corpse"),
    "v45_rx_owner":        ("TRANSIENT", "live owner table, read at capture"),
    "v45_sync_evt_owner":  ("TRANSIENT", "read at capture"),
    "v45_sync_evt_evt_code": ("TRANSIENT", "read at capture"),
    "v45_view":            ("TRANSIENT", "export page selection, TTL-bounded"),
    "corpse_view":         ("TRANSIENT", "export page selection, TTL-bounded"),
    "stall_ring_view":     ("TRANSIENT", "export page selection, TTL-bounded"),
    "v45_epoch":           ("TRANSIENT", "connection incarnation"),
    "v45_connected_at_ms": ("TRANSIENT", "connection incarnation"),
    "v45_exit_base":       ("TRANSIENT", "per-epoch notify-exit baseline"),
    "v45_force_trigger":   ("TRANSIENT", "one-shot force latch"),
    "corpse_force_trigger": ("TRANSIENT", "one-shot force latch"),
    "v45_ota_active":      ("TRANSIENT", "OTA suppression window"),
    "v45_ota_last_ms":     ("TRANSIENT", "OTA suppression window"),
    "v45_monitor_work":    ("TRANSIENT", "work item"),
    "v45_reboot_work":     ("TRANSIENT", "work item"),
    "v45_dfu_callback":    ("TRANSIENT", "DFU hook"),
    "v45_leaked_sync_evt": ("TRANSIENT", "fault injection state (validation only)"),
    "v45_inject_hang":     ("TRANSIENT", "fault injection state (validation only)"),
    "v45_inject_hang_arm": ("TRANSIENT", "fault injection work (validation only)"),
    "v45_inject_hang_sem": ("TRANSIENT", "fault injection sem (validation only)"),
    # Blindness bookkeeping. TRANSIENT, and the reasoning matters: these do not
    # guard the corpse, they report on the detector. Losing them to a reboot
    # restarts the blind timer, which is harmless because a reboot re-derives
    # corpse presence anyway. If one of these ever gates whether evidence may be
    # overwritten, it must be reclassified GUARD.
    "v45_blind_since_ms":  ("TRANSIENT", "when the detector went blind; reporting only"),
    "v45_blind_ticks":     ("TRANSIENT", "monitor passes spent blind; reporting only"),
    "v45_blind_discards":  ("TRANSIENT", "count of FORCED artefacts dropped on TTL"),
    "stall_detector":      ("TRANSIENT", "v41 stall detector, separate mechanism"),
    "stall_recovery_pending": ("TRANSIENT", "v41 stall recovery"),
    "stall_recovery_work": ("TRANSIENT", "v41 stall recovery"),
    "stall_alarm_reason":  ("TRANSIENT", "v41 stall alarm"),
    "stall_alarm_count":   ("TRANSIENT", "v41 stall alarm"),
    "stall_status":        ("TRANSIENT", "v41 stall status scratch"),
    "stall_ring_timer":    ("TRANSIENT", "ring sampling timer"),
    "stall_ring_lock":     ("TRANSIENT", "spinlock"),
    "frozen_observations": ("TRANSIENT", "ring bookkeeping counter"),
    "ring_dropped_bytes":  ("TRANSIENT", "UART ring, unrelated to the corpse"),
    "uart_ring":           ("TRANSIENT", "UART ring, unrelated to the corpse"),
    "_ring_buffer_data_uart_ring": ("TRANSIENT", "UART ring buffer storage"),
    "tag_reset_detected":  ("TRANSIENT", "tag liveness"),
    "fusion_stall_uuid":   ("TRANSIENT", "GATT UUID constant"),
}

SYMBOL_RE = re.compile(r"v45|corpse|stall|retained|ring|frozen")
MUST_BE_NOINIT = {"EVIDENCE", "GUARD"}

# A DERIVED classification is a CLAIM that something re-establishes the value
# before it can matter. For the ones where that claim is load-bearing, the
# mechanism is checked in the source, so "DERIVED" cannot be used to wave a real
# lifetime failure through. symbol -> (file, regex that must match, why).
# symbol -> (elf_symbol_that_must_exist, file, regex, why)
#
# BOTH halves are needed. Checking only the source lets an OLD ELF pass against
# NEW source -- which it did, silently, the first time this was written: the
# known-broken b306-v45s4-val reported PASS. The ELF symbol proves the mechanism
# is in THIS image; the source regex proves it runs at the right init level.
DERIVED_REQUIRES = {
    "bsf_v45_frozen": (
        "bsf_v45_early_init",
        "firmware/src/bsf_v45.c",
        r"SYS_INIT\(\s*bsf_v45_early_init\s*,\s*PRE_KERNEL_1",
        "the freeze must be re-derived at PRE_KERNEL_1; at any later init "
        "level the BLE stack can write the frozen banks first, which is "
        "exactly the failure this test exists to catch",
    ),
}


def ram_objects(elf: Path):
    from elftools.elf.elffile import ELFFile

    with elf.open("rb") as f:
        e = ELFFile(f)
        secs = [(s.name, s.header["sh_addr"],
                 s.header["sh_addr"] + s.header["sh_size"])
                for s in e.iter_sections()
                if s.header["sh_addr"] and s.header["sh_size"]]

        def sec_of(a):
            hit = [n for n, lo, hi in secs if lo <= a < hi]
            return hit[0] if hit else "?"

        out = {}
        for s in e.iter_sections():
            if s.header["sh_type"] != "SHT_SYMTAB":
                continue
            for sym in s.iter_symbols():
                n, a = sym.name, sym.entry["st_value"]
                if not n or not a:
                    continue
                if sym.entry["st_info"]["type"] != "STT_OBJECT":
                    continue
                if not (0x20000000 <= a < 0x20040000):
                    continue
                if SYMBOL_RE.search(n):
                    out[n] = (sec_of(a), a, sym.entry["st_size"])
        return out


def elf_symbol_names(elf: Path) -> set[str]:
    from elftools.elf.elffile import ELFFile

    names = set()
    with elf.open("rb") as f:
        for s in ELFFile(f).iter_sections():
            if s.header["sh_type"] != "SHT_SYMTAB":
                continue
            for sym in s.iter_symbols():
                if sym.name:
                    names.add(sym.name)
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf", type=Path)
    args = ap.parse_args()

    elf = args.elf
    if elf is None:
        cands = sorted(ROOT.glob("builds/*v45*/firmware/zephyr/zephyr.elf"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            print("v45 lifetime contract: FAIL (no v45 build; pass --elf)")
            return 1
        elf = cands[0]
    print(f"ELF {elf}")

    objs = ram_objects(elf)
    unclassified, violations, ok = [], [], []

    # Check the DERIVED claims that carry weight.
    all_syms = elf_symbol_names(elf)
    for sym, (need_sym, src, pattern, why) in DERIVED_REQUIRES.items():
        if sym not in objs:
            continue
        if need_sym not in all_syms:
            violations.append((sym, "DERIVED", objs[sym][0], objs[sym][1],
                               f"claims DERIVED, but THIS ELF has no "
                               f"{need_sym}() -- the mechanism is absent from "
                               f"the image. {why}"))
            continue
        text = (ROOT / src).read_text()
        if not re.search(pattern, text):
            violations.append((sym, "DERIVED", objs[sym][0], objs[sym][1],
                               f"claims DERIVED but {src} has no match for "
                               f"/{pattern}/ -- {why}"))
    for name, (sec, addr, size) in sorted(objs.items()):
        cls = CLASSIFY.get(name)
        if cls is None:
            unclassified.append((name, sec, addr))
            continue
        kind, note = cls
        if kind in MUST_BE_NOINIT and sec != "noinit":
            violations.append((name, kind, sec, addr, note))
        else:
            ok.append((name, kind, sec, addr))

    print(f"\n{len(objs)} corpse-related RAM objects, "
          f"{len(ok)} conforming, {len(violations)} violating, "
          f"{len(unclassified)} unclassified\n")

    for name, kind, sec, addr, note in violations:
        print(f"  !! {kind:<9} {name:<22} 0x{addr:08x} in .{sec} -- must be .noinit")
        print(f"     {note}")
    for name, sec, addr in unclassified:
        print(f"  ?? UNCLASSIFIED {name:<22} 0x{addr:08x} in .{sec}")
        print("     classify it in CLASSIFY[] as EVIDENCE / GUARD / DERIVED / "
              "TRANSIENT")

    if violations or unclassified:
        print(f"\nv45 lifetime contract: FAIL -- {len(violations)} lifetime "
              f"violation(s), {len(unclassified)} unclassified")
        return 1
    print("v45 lifetime contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

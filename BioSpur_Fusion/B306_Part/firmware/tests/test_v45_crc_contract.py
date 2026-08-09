#!/usr/bin/env python3
"""Source contract: nothing outside capture may write a CRC-covered field.

WHY THIS TEST EXISTS
--------------------
The v45 corpse protects itself with a crc32 over [fw_marker_hash .. valid),
computed at the end of v45_capture() with `valid` written last. Two places then
wrote fields INSIDE that range afterwards and did not refresh the CRC:

  * the reboot path set reboot_taken/reboot_owner just before rebooting;
  * bsf_v45_flash_persist_pending() set flash_slot at early boot, before
    bsf_v45_init() validates.

Either one made the corpse fail its own validator on the next boot, so
bsf_v45_init() memset it and the board came back reporting present=0 -- which
looks exactly like a detector that never fired. Measured on BSF6C53 2026-08-08;
see logs/stage_b_step2_20260808/T1_CORPSE_FORCE.md.

Schema 4 moved those three fields after `valid`. This test is what stops the
next person putting a mutable field back under the CRC: it reads the real field
offsets from DWARF in the real build, finds every write to bsf_v45_core in the
source, and fails if a write outside capture time lands inside the CRC range.

Run:  test_v45_crc_contract.py [--elf <zephyr.elf>] [--src <bsf_v45.c>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent            # B306_Part/

# Functions that run INSIDE the capture, before crc32 is computed. Writes from
# these are evidence being assembled and are supposed to be under the CRC.
# Keeping this list explicit rather than inferred is deliberate: adding a name
# here is a decision someone has to make on purpose, and the test below checks
# each one is genuinely reached from v45_capture().
CAPTURE_TIME_FUNCS = {"v45_capture", "v45_snapshot_counters"}

FUNC_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ \*]*\b(\w+)\s*\(")
WRITE_RE = re.compile(r"\bbsf_v45_core\.(\w+)\s*(?:\[[^\]]*\]\s*)?=(?!=)")
MEMSET_RE = re.compile(r"\bmemset\s*\(\s*&bsf_v45_core\b")


# `valid`, `crc32` and `fw_marker_hash` alone are NOT distinctive -- the v43/v44
# corpse and the flash header share them, and matching loosely picked the wrong
# struct (valid at +836 instead of +940) and then declared the fields under test
# "not in the struct", which reads like a pass-adjacent failure. Key on fields
# only the v45 core has, and refuse to guess if more than one struct matches.
CORE_FINGERPRINT = {"valid", "crc32", "fw_marker_hash", "corpse_seq",
                    "notify_exit_age_ms", "ncp_packet_age_ms",
                    "suspect_ring_index"}


def core_offsets(elf: Path) -> dict[str, int]:
    from elftools.elf.elffile import ELFFile

    hits = []
    with elf.open("rb") as f:
        for cu in ELFFile(f).get_dwarf_info().iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag != "DW_TAG_structure_type":
                    continue
                members = {}
                for ch in die.iter_children():
                    if ch.tag == "DW_TAG_member" and \
                       "DW_AT_name" in ch.attributes and \
                       "DW_AT_data_member_location" in ch.attributes:
                        members[ch.attributes["DW_AT_name"].value.decode()] = \
                            ch.attributes["DW_AT_data_member_location"].value
                if CORE_FINGERPRINT <= set(members):
                    size = die.attributes.get("DW_AT_byte_size")
                    hits.append((size.value if size else -1, members))
    uniq = {s: m for s, m in hits}
    if not uniq:
        raise SystemExit("REJECTED: no bsf_v45_core layout in the ELF's DWARF")
    if len(uniq) > 1:
        raise SystemExit(f"REJECTED: {len(uniq)} structs match the v45 core "
                         f"fingerprint (sizes {sorted(uniq)}); refusing to guess")
    size, members = next(iter(uniq.items()))
    print(f"struct bsf_v45_core: {size} bytes, {len(members)} members")
    return members


def writes_by_function(src: Path) -> list[tuple[int, str, str]]:
    """(line, enclosing function, field) for every bsf_v45_core.<field> = ..."""
    out, current, depth = [], "<file scope>", 0
    for n, line in enumerate(src.read_text().splitlines(), 1):
        stripped = line.rstrip()
        if depth == 0:
            m = FUNC_RE.match(stripped)
            if m and stripped.endswith(("{", ")", ",")):
                current = m.group(1)
        depth += stripped.count("{") - stripped.count("}")
        depth = max(depth, 0)
        for w in WRITE_RE.finditer(stripped):
            out.append((n, current, w.group(1)))
        if MEMSET_RE.search(stripped):
            out.append((n, current, "<memset>"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf", type=Path)
    ap.add_argument("--src", type=Path, default=ROOT / "firmware/src/bsf_v45.c")
    args = ap.parse_args()

    elf = args.elf
    if elf is None:
        cands = sorted(ROOT.glob("builds/*v45*/firmware/zephyr/zephyr.elf"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            print("REJECTED: no v45 build found; pass --elf", file=sys.stderr)
            return 2
        elf = cands[0]

    off = core_offsets(elf)
    crc_start, crc_end = off["fw_marker_hash"], off["valid"]
    print(f"ELF   {elf.relative_to(ROOT) if elf.is_relative_to(ROOT) else elf}")
    print(f"CRC covers [+{crc_start} .. +{crc_end})  "
          f"(fw_marker_hash .. valid), {crc_end - crc_start} bytes")

    # The capture-time allowlist must not be fiction.
    src_text = args.src.read_text()
    for fn in CAPTURE_TIME_FUNCS - {"v45_capture"}:
        if not re.search(rf"\b{fn}\s*\(", src_text.split("v45_capture", 1)[-1]):
            print(f"REJECTED: {fn} is on the capture-time allowlist but is not "
                  "called from v45_capture()", file=sys.stderr)
            return 2

    violations, outside = [], []
    for line, func, field in writes_by_function(args.src):
        if func in CAPTURE_TIME_FUNCS:
            continue
        if field == "<memset>":
            # A whole-struct memset is a reset, not a mutation of a live
            # corpse: bsf_v45_init() only does it once validation has already
            # failed, so there is nothing left to invalidate.
            outside.append((line, func, field, "whole-struct reset, allowed"))
            continue
        if field not in off:
            violations.append((line, func, field, "field not in the struct"))
            continue
        pos = off[field]
        if crc_start <= pos < crc_end:
            violations.append((line, func, field,
                               f"+{pos} is INSIDE the CRC range"))
        else:
            outside.append((line, func, field, f"+{pos}, outside"))

    print("\nwrites outside capture time:")
    for line, func, field, note in sorted(outside + violations):
        mark = "!!" if (line, func, field, note) in violations else "ok"
        print(f"  {mark} {args.src.name}:{line:<5} {func:<32} {field:<18} {note}")

    if violations:
        print(f"\nv45 CRC contract: FAIL -- {len(violations)} write(s) to a "
              "CRC-covered field outside capture time.")
        print("Move the field after `valid` (bookkeeping) or move the write "
              "into v45_capture() before the crc32 (evidence).")
        return 1
    print("\nv45 CRC contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

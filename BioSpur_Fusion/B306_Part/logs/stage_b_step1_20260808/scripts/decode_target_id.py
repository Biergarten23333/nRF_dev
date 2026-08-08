#!/usr/bin/env python3
"""Turn an id_target.jlink log into a target verdict.

INFO.PART says which CHIP. It does not say which BOARD, and there are ten of
them. So this also folds FICR.DEVICEID[0..1] through the firmware's own
identity function -- `bsl_identity_from_ficr()`,
B306_Part/include/biospur_link.h:314-320 -- and prints the BSFxxxx name the
board would advertise. Same read, two extra words, and the difference between
"an nRF52840" and "the one I meant".

Usage:
    decode_target_id.py <id_target log>  [--expect BSF6C53]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PART = {0x52840: ("nRF52840", "B306 / NINA-B306  -- CORRECT PAD SET"),
        0x52833: ("nRF52833", "unexpected part on this board"),
        0x52832: ("nRF52832", "DWM1001C  -- WRONG PAD SET, move the probe"),
        0x52820: ("nRF52820", "unexpected part on this board"),
        0x52811: ("nRF52811", "unexpected part on this board"),
        0x52810: ("nRF52810", "unexpected part on this board")}


def identity_from_ficr(d0: int, d1: int) -> int:
    """Byte-for-byte the firmware's bsl_identity_from_ficr()."""
    folded = (d0 ^ d1 ^ (d0 >> 16) ^ ((d1 << 1) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return ((folded >> 16) ^ folded) & 0xFFFF


def words_from_log(text: str) -> dict[int, int]:
    """J-Link prints `<addr> = <w0> <w1> ...` for mem32."""
    out: dict[int, int] = {}
    for line in text.splitlines():
        m = re.match(r"\s*([0-9A-Fa-f]{8})\s*=\s*((?:[0-9A-Fa-f]{8}\s*)+)", line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        for i, w in enumerate(m.group(2).split()):
            out[addr + 4 * i] = int(w, 16)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path)
    ap.add_argument("--expect", default="BSF6C53")
    args = ap.parse_args()

    w = words_from_log(args.log.read_text(errors="replace"))
    if 0x10000100 not in w:
        print("REJECTED: no FICR.INFO.PART (0x10000100) in the log. Did the "
              "connect succeed?", file=sys.stderr)
        return 2

    part = w[0x10000100]
    name, note = PART.get(part, (f"unknown 0x{part:x}", "NOT a recognised nRF52"))
    print(f"FICR.INFO.PART    0x{part:05x}   {name}")
    print(f"                  {note}")
    for label, addr in (("INFO.VARIANT", 0x10000104), ("INFO.PACKAGE", 0x10000108),
                        ("INFO.RAM  KiB", 0x1000010C), ("INFO.FLASH KiB", 0x10000110)):
        if addr in w:
            v = w[addr]
            pretty = f"0x{v:08x}"
            if "KiB" in label:
                pretty += f"  ({v} KiB)" if v < 0x10000 else ""
            if label == "INFO.VARIANT":
                try:
                    pretty += "  " + bytes.fromhex(f"{v:08x}").decode("ascii")
                except Exception:
                    pass
            print(f"{label:<17} {pretty}")

    ok = part == 0x52840
    if 0x10000060 in w and 0x10000064 in w:
        d0, d1 = w[0x10000060], w[0x10000064]
        ident = identity_from_ficr(d0, d1)
        board = f"BSF{ident:04X}"
        print(f"\nFICR.DEVICEID     0x{d0:08x} 0x{d1:08x}")
        print(f"derived identity  0x{ident:04X}  ->  {board}")
        if args.expect:
            if board.upper() == args.expect.upper():
                print(f"BOARD MATCH       {board} is the expected board")
            else:
                print(f"BOARD MISMATCH    got {board}, expected {args.expect}")
                ok = False
    else:
        print("\n[warn] DEVICEID not in the log -- chip identified, BOARD NOT "
              "verified")

    print()
    if ok:
        print("TARGET_ID PASS -- nRF52840, correct pad set, expected board")
        return 0
    print("TARGET_ID FAIL -- do not proceed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

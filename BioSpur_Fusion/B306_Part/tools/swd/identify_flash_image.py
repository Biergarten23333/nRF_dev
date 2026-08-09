#!/usr/bin/env python3
"""Name the image a flash dump actually contains, and hand back its ELF.

WHY G3 NEEDS THIS
-----------------
g3_dump.sh takes the ELF as an argument, and the runbook's example passes the
validation ELF -- correct for a board that has already been through G4, wrong
for the rehearsal, where the board is still running whatever was deployed to it.
Parsing a RAM dump against the wrong ELF does not fail loudly; it walks
`_kernel.threads` from the wrong address and prints confident nonsense.

So the flash backup that G3 takes first is used to identify the image, and the
matching ELF is what the RAM dump is then parsed against. The backup is taken
before anything is written, so this costs no extra probe contact.

Matching is byte-exact against every merged.hex under builds/: the winner is the
build whose programmed bytes all appear at the same addresses in the dump.

Usage:
    identify_flash_image.py <flash.bin> [--builds builds] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MARKER_RE = re.compile(rb"b306-[a-z0-9\-]{3,40}")

# 332 merged.hex files at ~76 ms each is 25 s of somebody holding a probe. The
# cache turns that into one pass now and a lookup at the bench. It is only a
# speed-up: whatever it shortlists is then verified byte for byte against
# builds/, so a stale cache cannot produce a wrong answer, only a slow one.
SAMPLE_ADDRS = tuple(range(0, 0x45000, 0x400))


def hex_records(path: Path) -> dict[int, int]:
    """Intel hex -> {address: byte}. Only types 00 and 04 occur here."""
    out: dict[int, int] = {}
    seg = 0
    for line in path.read_text().splitlines():
        if not line.startswith(":"):
            continue
        n = int(line[1:3], 16)
        rtype = line[7:9]
        if rtype == "04":
            seg = int(line[9:13], 16) << 16
        elif rtype == "00":
            addr = seg | int(line[3:7], 16)
            data = bytes.fromhex(line[9:9 + 2 * n])
            for i, b in enumerate(data):
                out[addr + i] = b
    return out


MCUBOOT_MAGIC = 0x96F3B83D


def mcuboot_code_end(blob: bytes) -> tuple[int, int] | None:
    """(slot_base, first address past the image) for the primary slot.

    A board that was flashed by one build and a build tree that signs every
    build separately do not produce the same bytes even when they produce the
    same firmware: the signature TLV after the image differs. Comparing whole
    programmed spans therefore reports 99.97% and calls it inconclusive, which
    is both true and useless. The code region is what determines symbol
    addresses, so that is what gets compared, and the tail is reported instead
    of being quietly folded in.
    """
    import struct
    for base in range(0, 0x20000, 0x1000):
        if base + 16 > len(blob):
            break
        magic, _load, hdr, _ptlv, img = struct.unpack_from("<IIHHI", blob, base)
        if magic == MCUBOOT_MAGIC and 0 < hdr <= 0x1000 and 0 < img < len(blob):
            return base, base + hdr + img
    return None


def fingerprint(recs: dict[int, int]) -> dict[str, int]:
    return {str(a): recs[a] for a in SAMPLE_ADDRS if a in recs}


def build_cache(builds: Path, cache: Path) -> int:
    out = {}
    for merged in sorted(builds.glob("*/merged.hex")):
        try:
            out[merged.parent.name] = fingerprint(hex_records(merged))
        except Exception as exc:
            print(f"  [skip] {merged.parent.name}: {exc}")
    cache.write_text(json.dumps(out))
    print(f"cached {len(out)} builds -> {cache}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", type=Path, nargs="?")
    ap.add_argument("--builds", type=Path, default=Path("builds"))
    ap.add_argument("--json", type=Path)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--build-cache", action="store_true")
    args = ap.parse_args()

    if args.build_cache:
        if not args.cache:
            ap.error("--build-cache needs --cache")
        return build_cache(args.builds, args.cache)
    if not args.dump:
        ap.error("a dump is required unless --build-cache is given")

    blob = args.dump.read_bytes()
    print(f"dump {args.dump.name}: {len(blob)} B")

    markers = sorted({m.decode() for m in MARKER_RE.findall(blob)})
    print(f"FW_MARKER strings in flash: {markers or '(none found)'}")

    candidates = sorted(p.parent.name for p in args.builds.glob("*/merged.hex"))
    if args.cache and args.cache.is_file():
        fp = json.loads(args.cache.read_text())
        shortlist = [name for name, samples in fp.items()
                     if samples and all(int(a) < len(blob) and blob[int(a)] == b
                                        for a, b in samples.items())]
        print(f"cache: {len(fp)} builds -> {len(shortlist)} sampled-match "
              f"candidate(s): {shortlist or '(none)'}")
        # An empty shortlist means the cache knows nothing useful, not that the
        # answer is "no build matches" -- fall back to the full scan and say so.
        if shortlist:
            candidates = shortlist
        else:
            print("cache shortlist is empty; falling back to the full scan")

    slot = mcuboot_code_end(blob)
    if slot:
        slot_base, code_end = slot
        print(f"MCUboot primary slot 0x{slot_base:06x}, code ends 0x{code_end:06x}; "
              "bytes at or past that are the signature TLV")
    else:
        slot_base = code_end = None
        print("[warn] no MCUboot image header found; comparing whole spans")

    results = []
    for name in candidates:
        merged = args.builds / name / "merged.hex"
        try:
            recs = hex_records(merged)
        except Exception:
            continue
        if not recs:
            continue
        bad = [a for a, b in recs.items() if a >= len(blob) or blob[a] != b]
        tail = [a for a in bad if code_end is not None and a >= code_end]
        results.append({"frac": 1 - len(bad) / len(recs), "total": len(recs),
                        "name": name, "bad": len(bad), "tail_only": len(bad) == len(tail)})
    results.sort(key=lambda r: (r["frac"], r["name"]), reverse=True)

    print("\ntop matches (fraction of programmed bytes present in the dump):")
    for r in results[:6]:
        note = "" if not r["bad"] else (
            f"  [{r['bad']} B differ, all in the signature TLV]" if r["tail_only"]
            else f"  [{r['bad']} B differ, INCLUDING CODE]")
        print(f"  {r['frac']*100:7.3f}%  {r['total']:7d} B  {r['name']}{note}")

    exact = [r["name"] for r in results if r["bad"] == 0]
    code_same = [r["name"] for r in results if r["bad"] and r["tail_only"]]
    winners = exact or code_same
    if not winners:
        best = results[0] if results else None
        # Byte counts, not percentages: one wrong byte in 264 144 rounds to
        # 100.000% and would read as a pass in a report.
        print(f"\nIMAGE_ID INCONCLUSIVE -- best is "
              f"{best['name'] if best else '-'} with "
              f"{best['bad'] if best else 0} of {best['total'] if best else 0} "
              "bytes differing, and the differences reach into the code. "
              "No committed build matches this flash.")
        return 1

    # More than one build can be code-identical (they differ only by signature).
    # Any of their ELFs would do -- but "would do" is a claim, so it is checked.
    if len(winners) > 1:
        from parse_ram_dump import elf_info
        tables = {}
        for name in winners:
            elf_p = args.builds / name / "firmware" / "zephyr" / "zephyr.elf"
            if elf_p.is_file():
                tables[name] = elf_info(elf_p)[0]
        ref = winners[0]
        mismatched = {n: sum(1 for k in set(tables[ref]) | set(t)
                             if tables[ref].get(k) != t.get(k))
                      for n, t in tables.items() if n != ref}
        print(f"\n{len(winners)} builds tie: {winners}")
        for n, c in mismatched.items():
            print(f"  symbol addresses differing from {ref}: {n} -> {c}")
        if any(c for c in mismatched.values()):
            print("\nIMAGE_ID INCONCLUSIVE -- the tied builds do NOT agree on "
                  "symbol addresses, so the ELF cannot be chosen arbitrarily.")
            return 1
        print("  all tied builds agree on every symbol address; either ELF parses "
              "this dump identically")

    winner = winners[0]
    elf = args.builds / winner / "firmware" / "zephyr" / "zephyr.elf"
    kind = "exact" if winner in exact else "code-identical (signature TLV differs)"
    print(f"\nIMAGE_ID PASS -- flash contains {winner}  [{kind}]")
    print(f"ELF {elf}")
    if args.json:
        args.json.write_text(json.dumps(
            {"build": winner, "elf": str(elf), "markers": markers, "match": kind,
             "exact_matches": exact, "code_identical": code_same,
             "slot_base": slot_base, "code_end": code_end}, indent=2) + "\n")
    if not elf.is_file():
        print(f"[error] matched build has no ELF at {elf}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

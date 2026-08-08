#!/usr/bin/env python3
"""The section 15 gate: "flash corpse partition overlaps nothing".

This is not a review; it is arithmetic over the map Partition Manager actually
generated. The failure mode it guards against is specific and expensive: a
corpse partition that lands inside mcuboot_secondary would sit on MCUboot's swap
trailer and could corrupt a STAGED OTA IMAGE -- a brick risk across a ten-node
fleet, and one that would only show up on the next update.

It checks BOTH maps:
  * the DEFAULT pm_static.yml, where the assertion is the negative one -- that
    the map tiles the whole 1 MiB with zero bytes free, which is why flash
    persistence ships disabled (CONTEXT_AUDIT item 11);
  * the OPTIONAL pm_static_v45_corpse.yml, where the corpse partition must
    exist, be sector-aligned, hold two erasable slots, and overlap nothing.

Leaf partitions only. mcuboot_primary / mcuboot_primary_app are SPANS -- they
deliberately contain app and mcuboot_pad, and calling that an overlap would be
a false positive that trains people to ignore this test.
"""
import sys
from pathlib import Path

fw = Path(__file__).resolve().parents[1]
FLASH_SIZE = 0x100000
SECTOR = 0x1000
SPANS = {"mcuboot_primary", "mcuboot_primary_app", "mcuboot_secondary_pad"}

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def load(path: Path):
    """Minimal reader for the flat two-level YAML Partition Manager emits.

    Deliberately not `import yaml`: this must run in whatever interpreter the
    build gate happens to have, and the format here is a fixed shape we control.
    """
    parts, cur = {}, None
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" ") and raw.rstrip().endswith(":"):
            cur = raw.rstrip()[:-1]
            parts[cur] = {}
            continue
        if cur and raw.startswith("  ") and ":" in raw:
            k, _, v = raw.strip().partition(":")
            v = v.strip()
            if v.startswith("0x"):
                try:
                    parts[cur][k] = int(v, 16)
                except ValueError:
                    pass
            elif k == "region":
                parts[cur][k] = v
    # flash_primary ONLY. pm_static.yml also describes sram_primary, and
    # summing RAM into a flash coverage total is how this test first reported
    # 0x140000 of a 0x100000 device.
    return {k: v for k, v in parts.items()
            if "address" in v and "size" in v
            and v.get("region", "flash_primary") == "flash_primary"}


def leaves(parts):
    return {k: v for k, v in parts.items() if k not in SPANS}


def overlaps(parts):
    items = sorted(((v["address"], v["address"] + v["size"], k)
                    for k, v in parts.items()))
    bad = []
    for (a0, a1, an), (b0, b1, bn) in zip(items, items[1:]):
        if b0 < a1:
            bad.append(f"{an} [0x{a0:x},0x{a1:x}) overlaps {bn} [0x{b0:x},0x{b1:x})")
    return bad, items


# --------------------------------------------------------------------------
# 1. The DEFAULT map: prove there is no free flash
# --------------------------------------------------------------------------
default = leaves(load(fw / "pm_static.yml"))
bad, items = overlaps(default)
check(not bad, f"default map overlaps: {bad}")
covered = sum(b - a for a, b, _ in items)
check(covered == FLASH_SIZE,
      f"the default map covers 0x{covered:x} of 0x{FLASH_SIZE:x}; the audit's "
      "claim of ZERO free bytes is what makes flash persistence undeployable, "
      "so if this ever changes the audit needs revisiting")
check("bsf_corpse_partition" not in default,
      "the default map must NOT carry a corpse partition -- enabling it "
      "requires an SWD reflash of MCUboot and is never a side effect")

# --------------------------------------------------------------------------
# 2. The OPTIONAL map: the corpse partition overlaps nothing
# --------------------------------------------------------------------------
opt_path = fw / "pm_static_v45_corpse.yml"
opt = leaves(load(opt_path))
bad, items = overlaps(opt)
check(not bad, f"corpse-overlay map overlaps: {bad}")

check("bsf_corpse_partition" in opt, "the overlay must define bsf_corpse_partition")
if "bsf_corpse_partition" in opt:
    c = opt["bsf_corpse_partition"]
    start, size = c["address"], c["size"]
    end = start + size
    check(start % SECTOR == 0, f"corpse start 0x{start:x} is not sector aligned")
    check(size % SECTOR == 0, f"corpse size 0x{size:x} is not a sector multiple")
    check(size >= 2 * SECTOR,
          "two ROTATING slots need at least two erase blocks; a slot smaller "
          "than a sector cannot be erased independently, which would defeat "
          "the second-trigger case entirely")
    check(size == 0x4000, f"corpse partition is 0x{size:x}, expected 0x4000 (16 KiB)")
    check(end <= FLASH_SIZE, f"corpse runs past the end of flash (0x{end:x})")

    for name in ("mcuboot", "mcuboot_secondary", "app"):
        if name not in opt:
            fails.append(f"{name} missing from the overlay")
            continue
        o = opt[name]
        o0, o1 = o["address"], o["address"] + o["size"]
        check(end <= o0 or start >= o1,
              f"corpse [0x{start:x},0x{end:x}) overlaps {name} [0x{o0:x},0x{o1:x})")

# The slots must stay the same size, or boot_slots_compatible() rejects the
# pair and OTA stops working (swap-using-move accepts pri == sec, or pri ==
# sec + 1; equal is the unambiguous choice).
if "mcuboot_primary" in load(opt_path) and "mcuboot_secondary" in opt:
    pri = load(opt_path)["mcuboot_primary"]["size"]
    sec = opt["mcuboot_secondary"]["size"]
    check(pri == sec,
          f"slot sizes diverged: primary 0x{pri:x} vs secondary 0x{sec:x}. "
          "swap-using-move needs them equal (or primary one sector larger); "
          "anything else prints 'Cannot upgrade: not a compatible amount of "
          "sectors' and silently ends OTA support")

# --------------------------------------------------------------------------
# 3. If a flash-enabled build exists, check the map PM actually generated
# --------------------------------------------------------------------------
# Any v45 build whose generated map carries the corpse partition -- the glob
# was once "-flash*" only, which silently stopped covering the Stage B
# validation build the moment it was named "-val-corpse".
_gens = [g for g in sorted((fw.parents[0] / "builds").glob(
    "b306-imu-relay-v45-*/partitions.yml"))
    if "bsf_corpse_partition" in g.read_text()]
check(bool(_gens),
      "no generated map with a corpse partition was found; the overlay has "
      "then only been checked as text, never as something PM actually emitted")
for gen in _gens:
    got = leaves(load(gen))
    bad, _ = overlaps(got)
    check(not bad, f"{gen}: generated map overlaps: {bad}")
    check("bsf_corpse_partition" in got,
          f"{gen}: the build did not materialise bsf_corpse_partition")
    if "bsf_corpse_partition" in got and "bsf_corpse_partition" in opt:
        check(got["bsf_corpse_partition"]["address"]
              == opt["bsf_corpse_partition"]["address"],
              f"{gen}: PM placed the corpse partition somewhere else")

if fails:
    print("v45 partition overlap: FAIL")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("v45 partition overlap: PASS")

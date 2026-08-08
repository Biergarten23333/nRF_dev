#!/usr/bin/env python3
"""Self-test for parse_ram_dump.py, run BEFORE the probe is ever attached.

The wedged-board dump is one shot. This synthesises a RAM image with a real
thread list -- laid out at the REAL struct offsets read from the REAL ELF -- and
checks the parser walks it, names the threads, decodes the states, resolves
`pended_on` to a named Bluetooth pool, and finds every .noinit landmark.

It cannot prove the parser handles real data. It does prove the parser's model
of struct k_thread, struct net_buf_pool and struct k_queue matches this build,
which is the part that would otherwise be discovered wrong at the bench.
"""
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse_ram_dump as P  # noqa: E402

BASE, SIZE = P.RAM_BASE, P.RAM_SIZE
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


builds = sorted((HERE.parents[1] / "builds").glob(
    "b306-imu-relay-v45-*/firmware/zephyr/zephyr.elf"))
if not builds:
    print("parse_ram_dump self-test: SKIP (no v45 build)")
    raise SystemExit(0)
ELF = builds[-1]

syms, off, sizes = P.elf_info(ELF)
for need in ("k_thread", "_thread_base", "_callee_saved", "_thread_stack_info",
             "net_buf_pool", "k_queue"):
    check(need in off, f"no DWARF for struct {need}")
if fails:
    print("parse_ram_dump self-test: FAIL")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)

kt, tb = off["k_thread"], off["_thread_base"]
cs, si = off["_callee_saved"], off["_thread_stack_info"]
pool_waitq = off["net_buf_pool"].get("free", 0) + off["k_queue"].get("wait_q", 8)

# The offsets this build actually has. Pinned so a Zephyr bump that moves them
# fails here rather than at the bench.
check(kt["next_thread"] > 0, "k_thread.next_thread missing")
check(tb["thread_state"] > 0, "_thread_base.thread_state missing")
check("prio" not in tb,
      "prio is suddenly a named DWARF member -- the fallback in parse_ram_dump "
      "assumes it is inside the anonymous union; re-check it")
check(cs["psp"] == 32, f"_callee_saved.psp moved to {cs['psp']}")
check(pool_waitq == 8, f"net_buf_pool free wait_q offset is {pool_waitq}, was 8")

ram = bytearray(SIZE)


def w32(a, v):
    struct.pack_into("<I", ram, a - BASE, v & 0xFFFFFFFF)


def w8(a, v):
    ram[a - BASE] = v & 0xFF


def wstr(a, s):
    ram[a - BASE:a - BASE + len(s) + 1] = s.encode() + b"\0"


NAMES = ["MPSL Work", "BT RX WQ", "sysworkq",
         "notify_worker_thread_id", "publisher_thread_id"]
ADDRS = [0x20030000 + i * 0x200 for i in range(len(NAMES))]
STACK, STACK_SZ = 0x20020000, 0x800

for i, (a, nm) in enumerate(zip(ADDRS, NAMES)):
    w32(a + kt["next_thread"], ADDRS[i + 1] if i + 1 < len(ADDRS) else 0)
    wstr(a + kt["name"], nm)
    # Thread 0 is the wedge shape: PENDING (BIT(1)) on the sync_evt free wait_q.
    w8(a + kt["base"] + tb["thread_state"], (1 << 1) if i == 0 else 0)
    w8(a + kt["base"] + tb["thread_state"] + 1, (256 - 6) & 0xFF if i == 0 else 8)
    w32(a + kt["base"] + tb["pended_on"],
        (syms["sync_evt_pool"] + pool_waitq) if i == 0 else 0)
    w32(a + kt["callee_saved"] + cs["psp"], STACK + STACK_SZ - 0x140 + i * 16)
    w32(a + kt["stack_info"] + si["start"], STACK)
    w32(a + kt["stack_info"] + si["size"], STACK_SZ)

# _kernel.threads
from elftools.elf.elffile import ELFFile  # noqa: E402
toff = None
with ELF.open("rb") as f:
    for cu in ELFFile(f).get_dwarf_info().iter_CUs():
        for die in cu.iter_DIEs():
            if die.tag != "DW_TAG_structure_type":
                continue
            n = die.attributes.get("DW_AT_name")
            if n is None or n.value != b"z_kernel":
                continue
            for ch in die.iter_children():
                if (ch.tag == "DW_TAG_member"
                        and ch.attributes.get("DW_AT_name")
                        and ch.attributes["DW_AT_name"].value == b"threads"):
                    toff = ch.attributes["DW_AT_data_member_location"].value
        if toff is not None:
            break
check(toff is not None, "could not locate z_kernel.threads")
if toff is None:
    print("parse_ram_dump self-test: FAIL")
    raise SystemExit(1)

w32(syms["_kernel"] + toff, ADDRS[0])
w32(syms["stall_ring"], 0x52334236)       # ring magic
w32(syms["bsf_v45_core"], 0x35345043)     # 'CP45'

synth = HERE / ".synth_ram.bin"
synth.write_bytes(ram)
try:
    r = subprocess.run([sys.executable, str(HERE / "parse_ram_dump.py"),
                        str(synth), "--elf", str(ELF)],
                       capture_output=True, text=True)
    out = r.stdout

    check("MPSL Work" in out, "MPSL Work not walked")
    check("publisher_thread_id" in out, "list walk stopped early")
    check("5 threads walked." in out, f"expected 5 threads, got:\n{out}")
    check("PENDING" in out, "thread_state PENDING not decoded")
    check("sync_evt_pool.free.wait_q" in out,
          "pended_on was not resolved to the named Bluetooth pool -- this is "
          "the single most important thing this parser does")
    check("PENDED ON A BLUETOOTH POOL" in out,
          "the wedge shape was not flagged as a problem")
    check("magic OK" in out, "stall_ring magic not recognised")
    check("magic CP45" in out, "bsf_v45_core magic not recognised")
    for sym in ("stall_ring", "bsf_v45_ch", "bsf_v45_core", "bsf_v45_bank"):
        check(f"{sym}" in out and "OUTSIDE DUMP" not in
              out.split(sym)[1].split("\n")[0],
              f"{sym} not locatable inside a 256 KiB RAM dump")
    # -6 is a cooperative priority; the anonymous-union fallback must find it.
    check(" -6 " in out, "cooperative priority not decoded via the prio fallback")

    # And the healthy-board gate must FAIL on this deliberately-wedged image.
    r2 = subprocess.run([sys.executable, str(HERE / "parse_ram_dump.py"),
                         str(synth), "--elf", str(ELF), "--expect-healthy"],
                        capture_output=True, text=True)
    check(r2.returncode != 0,
          "--expect-healthy must FAIL on a dump with a thread pended on a "
          "Bluetooth pool, or G3 would pass on a wedged board")
finally:
    synth.unlink(missing_ok=True)

if fails:
    print("parse_ram_dump self-test: FAIL")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("parse_ram_dump self-test: PASS")

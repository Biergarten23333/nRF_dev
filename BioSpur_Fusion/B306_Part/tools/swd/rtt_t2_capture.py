#!/usr/bin/env python3
"""Hold RTT open across a T2 re-run and keep everything it says, verbatim.

WHY THIS EXISTS
---------------
ANSWERED 2026-08-09. This script was written believing a `V45 HANG` re-run had
caused an unexplained SOFTWARE reset (`reset_reason=4`) with no surviving
corpse. It ran, and the capture disproved its own premise: the board rebooted
once inside the window and the new boot reported `reset_reason=0x00000002` --
RESETREAS.DOG, the watchdog. The `reset_reason=4` that started the hunt was the
PREVIOUS boot's SREQ, still being reported by telemetry because nothing had
reset the board since; it was attributed to T2 in error.

There was never an unexplained software reset. There is a watchdog that beats
the detector to the corpse, because the system workqueue feeds the dog and
ticks the detector, and `V45 HANG` stalls that queue. Fixed on the watchdog
side (12 s dwell, one feed inside the capture, a `.noinit` witness); the
injection is deliberately unchanged, because it is the only thing that
reproduces a syswq death.

An unexplained reset is fatal to Stage C in a specific way: it takes the corpse
with it and then looks exactly like "the detector never fired". The log is the
only thing left that can name the caller, and the log only exists if somebody is
attached to RTT while it happens.

RULES FOLLOWED HERE
  * reset_target=False. Resetting to observe a reset is self-defeating.
  * At most `--attempts` tries. If it does not reproduce, that is recorded as
    NOT REPRODUCED -- it is a result, not a reason to keep going.
  * The raw capture is written byte-for-byte to rtt_raw.bin alongside the
    decoded text. Nothing is filtered on the way in.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

FUSION_RTT_SERIAL = 1050070698          # the nRF5340 DK probe on BSF6C53
DEVICE = "nRF52840_xxAA"
RTT_SEARCH = [0x20000000, 0x20040000]   # let pylink find the CB in RAM


def bench(label: str, outdir: Path, *cmds: str, after: float = 0.0) -> str:
    argv = [sys.executable, str(ROOT / "tools/v45_bench.py"),
            "--outdir", str(outdir), "--node", "BSF6C53", "--label", label,
            "cmd", *cmds, "--observe-before", "1",
            "--observe-after", str(after), "--timeout", "8"]
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.stdout + p.stderr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--attempts", type=int, default=1)
    ap.add_argument("--window", type=float, default=40.0)
    args = ap.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    import pylink
    jl = pylink.JLink()
    jl.open(serial_no=FUSION_RTT_SERIAL)
    jl.set_tif(pylink.enums.JLinkInterfaces.SWD)
    jl.connect(DEVICE, speed=4000, verbose=False)
    jl.rtt_start()                       # NO reset_target
    time.sleep(0.5)

    raw = bytearray()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            try:
                b = jl.rtt_read(0, 4096)
            except Exception:
                b = None
            if b:
                raw.extend(bytes(b))
            else:
                time.sleep(0.01)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    print("  RTT attached (reset_target=False)")

    reproduced = False
    try:
        for i in range(1, args.attempts + 1):
            mark = f"\n===== T2 ATTEMPT {i} =====\n".encode()
            raw.extend(mark)
            print(f"  attempt {i}/{args.attempts}: V45 HANG")
            bench(f"t2r{i}_hang", args.outdir, "V45 HANG")
            time.sleep(args.window)
            out = bench(f"t2r{i}_status", args.outdir, "V45 STATUS", "STATUS")
            m = re.search(r"up_ms=(\d+)", out)
            up_ms = int(m.group(1)) if m else -1
            pres = "present=1" in out
            #
            # UNITS. `up_ms` is MILLISECONDS off the wire; `args.window` is
            # SECONDS. This comparison was written as `up < args.window + 15`
            # and so tested 23409 < 55, which is false -- it reported NOT
            # REPRODUCED for a run where the board had demonstrably rebooted
            # 16.6 s into the window. Both operands carry their unit in the
            # name now, and the conversion is explicit and on its own line.
            #
            reset_floor_ms = (args.window + 15.0) * 1000.0
            print(f"    after {args.window:.0f}s: up_ms={up_ms} "
                  f"(reset if < {reset_floor_ms:.0f}) corpse_present={pres}")
            if 0 <= up_ms < reset_floor_ms:
                print("    *** BOARD RESET during the window -- REPRODUCED")
                reproduced = True
                break
            bench(f"t2r{i}_off", args.outdir, "V45 HANG OFF")
    finally:
        stop.set(); t.join(timeout=3)
        try:
            jl.rtt_stop(); jl.close()
        except Exception:
            pass

    (args.outdir / "rtt_raw.bin").write_bytes(bytes(raw))
    txt = raw.decode("utf-8", errors="replace")
    (args.outdir / "rtt_capture.txt").write_text(txt)
    print(f"  RTT captured {len(raw)} bytes -> rtt_raw.bin / rtt_capture.txt")

    hits = [l for l in txt.splitlines()
            if re.search(r"Fatal error|Disconnecting|ATT Timeout|WEDGE|STALL "
                         r"RECOVERY|rebooting|<err>|E: ", l)]
    if hits:
        print("  LINES THAT NAME A REBOOT PATH:")
        for l in hits[:20]:
            print("    " + l.strip()[:160])
    else:
        print("  (no reboot-naming line in the capture)")
    print(f"  T2_REPRODUCED={'yes' if reproduced else 'NO -- recorded as not reproduced'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

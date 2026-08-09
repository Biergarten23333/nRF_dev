#!/usr/bin/env python3
"""Passive witness on the Fusion Master CDC -- legs 1 and 2 of the G2 proof.

WHY THIS EXISTS
---------------
g2_noreset.sh states the proof is three-way and that two legs live outside the
script: the master must show no disconnect, and the operator must see no
re-connect LED blink. Neither is observable from a headless session, and a gate
whose evidence is "someone was watching" is not evidence.

BSF6C53 is connected to the Fusion Master and reports its own telemetry once a
second, which carries exactly the two fields G2 asks for:

    FUSION_TELEMETRY name=BSF6C53 node_ms=<uptime> ... reset_reason=<n> ...

`node_ms` is the node's own uptime. A reset restarts it near zero; nothing else
does. `reset_reason` is latched at boot and cannot change without one. So the
master leg and the uptime leg both become measurements, and the LED leg -- which
is only a human-visible proxy for the same reconnect -- is subsumed by watching
the link itself.

IT ONLY READS. dtr and rts are held low exactly as `LineChannel._open()` in
tools/fusion_session.py does, so opening the port cannot reboot the master.
Verified: the first open replayed the master's buffered boot banner while
`master_ms` kept counting from an earlier boot, i.e. a log flush, not a reset.
`--settle` exists to discard that replay.

Usage:
    link_witness.py watch  --seconds 40 --out w.jsonl [--run "cmd ..."]
    link_witness.py report w.jsonl --node BSF6C53 [--allow-disconnect]
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fusion_host_binary import FrameError, FrameStreamDecoder, frame_to_line  # noqa: E402

DEFAULT_PORT = "/dev/serial/by-id/usb-BioSpur_BioSpur_Fusion_Master_8D3AC42D4D90FAE8-if00"
TELEM_RE = re.compile(r"^FUSION_TELEMETRY\b.*\bname=(\S+)")
FIELD_RE = re.compile(r"(\w+)=(-?\d+)\b")


class Witness:
    """Reads the master's CDC in a thread so a J-Link run can happen mid-window."""

    def __init__(self, port: str, out: Path, settle: float):
        import serial

        self.out = out
        self.settle = settle
        self.rows: list[dict] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        dev = serial.Serial()
        dev.port = port
        dev.baudrate = 115200
        dev.timeout = 0.10
        dev.dtr = False
        dev.rts = False
        dev.open()
        dev.dtr = False
        dev.rts = False
        self.dev = dev

    def _emit(self, row: dict) -> None:
        # mark() is called from the main thread while _loop() writes from the
        # reader thread; one lock keeps their lines from interleaving.
        with self._lock:
            self._emit_locked(row)

    def _emit_locked(self, row: dict) -> None:
        self.rows.append(row)
        # Written and flushed as it arrives, not buffered to exit. A 15-minute
        # soak whose file only appears at the end cannot be watched while it
        # runs, which is most of the point of watching.
        self._fh.write(json.dumps(row) + "\n")
        self._fh.flush()

    def _loop(self) -> None:
        decoder = FrameStreamDecoder()
        settle_until = time.monotonic() + self.settle
        while not self._stop.is_set():
            raw = self.dev.read(4096)
            if not raw:
                continue
            stamp = time.time()
            frames = decoder.feed(raw)
            if time.monotonic() < settle_until:
                continue          # replayed backlog, not live
            for frame in frames:
                try:
                    line = frame_to_line(frame)
                except FrameError as exc:
                    line = f"FRAME_ERROR {exc}"
                self._emit({"t": stamp, "line": line})

    def __enter__(self):
        self._fh = self.out.open("w")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def mark(self, text: str) -> None:
        self._emit({"t": time.time(), "line": f"#MARK {text}"})

    def __exit__(self, *exc):
        self._stop.set()
        self.thread.join(timeout=3.0)
        self.dev.close()
        self._fh.close()


def watch(args) -> int:
    with Witness(args.port, args.out, args.settle) as w:
        time.sleep(args.settle + 0.5)
        time.sleep(args.before)
        rc = 0
        if args.run:
            w.mark(f"RUN_BEGIN {args.run}")
            proc = subprocess.run(shlex.split(args.run))
            rc = proc.returncode
            w.mark(f"RUN_END rc={rc}")
            print(f"[witness] inner command rc={rc}", flush=True)
        else:
            time.sleep(max(0.0, args.seconds - args.before))
        time.sleep(args.after)
    print(f"[witness] {len(w.rows)} records -> {args.out}")
    return rc


def report(args) -> int:
    rows = [json.loads(x) for x in args.file.read_text().splitlines() if x.strip()]
    node = args.node
    marks = [r for r in rows if r["line"].startswith("#MARK")]
    telem = []
    for r in rows:
        m = TELEM_RE.match(r["line"])
        if m and m.group(1) == node:
            f = dict(FIELD_RE.findall(r["line"]))
            telem.append({"t": r["t"], **{k: int(v) for k, v in f.items()}})
    disc = [r for r in rows
            if r["line"].startswith("FUSION_DISCONNECTED") and f"name={node}" in r["line"]]
    conn = [r for r in rows
            if r["line"].startswith("FUSION_CONNECTED") and f"name={node}" in r["line"]]

    print(f"node                 {node}")
    print(f"records              {len(rows)}")
    for mk in marks:
        print(f"  mark               {mk['t']:.3f}  {mk['line'][6:]}")
    print(f"telemetry records    {len(telem)}")
    if len(telem) < 2:
        print("\nLINK_WITNESS INCONCLUSIVE -- fewer than two telemetry records "
              f"for {node}; the node was not reporting, so the link cannot "
              "witness anything")
        return 2

    first, last = telem[0], telem[-1]
    wall = last["t"] - first["t"]
    node_d = last["node_ms"] - first["node_ms"]
    print(f"node_ms              {first['node_ms']} -> {last['node_ms']}   "
          f"delta={node_d} ms over wall={wall*1000:.0f} ms")
    print(f"reset_reason         {first.get('reset_reason')} -> {last.get('reset_reason')}")
    print(f"watchdog_feeds       {first.get('watchdog_feeds')} -> {last.get('watchdog_feeds')}")
    print(f"disconnects          {len(disc)}    reconnects {len(conn)}")
    for r in disc + conn:
        print(f"  {r['t']:.3f}  {r['line'][:120]}")

    # A reset is the ONLY thing that can make node_ms go backwards, and the only
    # thing that can change reset_reason. A disconnect does neither -- which is
    # exactly why the two are judged separately here.
    fails = []
    regress = [(telem[i]["node_ms"], telem[i + 1]["node_ms"])
               for i in range(len(telem) - 1)
               if telem[i + 1]["node_ms"] < telem[i]["node_ms"]]
    if regress:
        fails.append(f"node_ms went BACKWARDS {regress[:3]} -- THE TARGET RESET")
    if first.get("reset_reason") != last.get("reset_reason"):
        fails.append("reset_reason changed -- THE TARGET RESET")
    if last.get("watchdog_feeds", 0) < first.get("watchdog_feeds", 0):
        fails.append("watchdog_feeds went backwards -- THE TARGET RESET")
    # Uptime must track wall clock. Zephyr's uptime on nRF is driven by the RTC,
    # which keeps counting while the debugger holds the core, so even a long
    # halt should not make node_ms fall behind -- but --halt-ms exists so that
    # if it does, a known halt is not confused with a stall. Advancing MORE than
    # wall clock is impossible either way and stays a hard failure.
    if wall > 2.0:
        expected = wall * 1000
        if node_d > expected + args.skew_ms:
            fails.append(f"node_ms advanced {node_d} ms in {expected:.0f} ms of "
                         "wall clock -- uptime ran fast, which cannot happen")
        elif node_d < expected - args.skew_ms - args.halt_ms:
            fails.append(f"node_ms advanced only {node_d} ms while {expected:.0f} ms "
                         f"of wall clock passed (halt allowance {args.halt_ms:.0f} ms)"
                         " -- uptime did not track")
    if disc and not args.allow_disconnect:
        fails.append(f"{len(disc)} BLE disconnect(s) -- link was not continuous")
    elif disc:
        print("\n[note] disconnect(s) present and allowed for this gate: a halt "
              "longer than the BLE supervision timeout drops the link without "
              "resetting the node. node_ms above is what separates the two.")

    print()
    if fails:
        print("LINK_WITNESS FAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"LINK_WITNESS PASS -- {node} did not reset: uptime advanced "
          f"{node_d} ms across the window, reset_reason unchanged"
          + (", link continuous" if not disc else ", link dropped but node survived"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch")
    w.add_argument("--port", default=DEFAULT_PORT)
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("--settle", type=float, default=4.0,
                   help="discard this much replayed CDC backlog after opening")
    w.add_argument("--before", type=float, default=6.0, help="baseline before --run")
    w.add_argument("--after", type=float, default=12.0, help="observation after --run")
    w.add_argument("--seconds", type=float, default=20.0, help="used when --run is absent")
    w.add_argument("--run", help="command to execute inside the window")
    w.set_defaults(func=watch)

    r = sub.add_parser("report")
    r.add_argument("file", type=Path)
    r.add_argument("--node", default="BSF6C53")
    r.add_argument("--allow-disconnect", action="store_true",
                   help="a halt longer than the supervision timeout drops the "
                        "link; that is not a reset and G3 expects it")
    r.add_argument("--skew-ms", type=float, default=1500.0)
    r.add_argument("--halt-ms", type=float, default=0.0,
                   help="known debugger halt inside the window, in ms")
    r.set_defaults(func=report)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bench instrument for Stage B Step 2 -- drive one node and record everything.

WHY THIS AND NOT A PILE OF ONE-OFF SCRIPTS
------------------------------------------
Every test in Step 2 is the same shape: hold the Fusion Master CDC open, record
the entire decoded stream with host timestamps, issue vendor commands at known
instants, and afterwards measure things like "how long from the leak command to
export cessation" or "was the trigger dwell 20 s". That measurement is only as
good as the timebase, so one tool owns the channel and every command and reply
lands in one ordered log with one clock.

It deliberately reuses ThreadedLineChannel + decode_guard -- exactly what
tools/v45_corpse_collect.py uses -- rather than opening the port a second way.

The channel's own log already timestamps every line as

    <host_epoch> <monotonic> FUSION_TX  <line>
    <host_epoch> <monotonic> FUSION_RX  <line>

so that file IS the record. Nothing here re-derives it.

Usage:
    v45_bench.py --outdir DIR --node BSF6C53 observe --seconds 30
    v45_bench.py --outdir DIR --node BSF6C53 cmd "V45 STATUS" [--cmd ...]
    v45_bench.py --outdir DIR --node BSF6C53 cmd "V45 LEAK" --observe-after 90
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPLY_RE_TMPL = r"FUSION_REPLY\b.*\bname={node}\b.*\bsource=B306\b.*\btext=(?P<text>.*)$"


class Bench:
    def __init__(self, node: str, outdir: Path, port: str | None = None,
                 label: str = "bench"):
        from async_line_channel import ThreadedLineChannel
        from coldstart_fusion_control import decode_guard
        from fusion_session import resolve_fusion_port

        outdir.mkdir(parents=True, exist_ok=True)
        self.node = node
        self.outdir = outdir
        self.logpath = outdir / f"fusion_cdc_{label}.log"
        self.logf = self.logpath.open("a", encoding="utf-8", buffering=1)
        self.ch = ThreadedLineChannel(
            resolve_fusion_port(port), self.logf, "FUSION",
            decoded_queue_records=262144, backlog_red_records=32768,
            raw_backlog_red_bytes=32768, stall_red_s=2)
        self.ch.transport_mode = "binary"
        self.ch.text_pending.clear()
        decode_guard(self.ch, 20)
        self.pending: list[str] = []
        self.reply_re = re.compile(REPLY_RE_TMPL.format(node=re.escape(node)))

    # -- primitives ---------------------------------------------------------
    def expect(self, pattern: str, timeout: float) -> str | None:
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, held in enumerate(self.pending):
                if rx.search(held):
                    return self.pending.pop(i)
            line = self.ch.read(min(deadline, time.monotonic() + 0.5))
            if line is None:
                continue
            if rx.search(line):
                return line
            self.pending.append(line)
            if len(self.pending) > 8192:
                del self.pending[:4096]
        return None

    def observe(self, seconds: float) -> None:
        """Drain into the log for a fixed window. The log is the record."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            line = self.ch.read(min(deadline, time.monotonic() + 0.5))
            if line is not None:
                self.pending.append(line)
                if len(self.pending) > 8192:
                    del self.pending[:4096]

    def command(self, cmd: str, timeout: float = 8.0) -> dict:
        t0 = time.time()
        self.ch.send(f"{self.node} {cmd}")
        line = self.expect(self.reply_re.pattern, timeout)
        t1 = time.time()
        out = {"cmd": cmd, "sent_at": t0, "reply_at": t1 if line else None,
               "latency_s": (t1 - t0) if line else None, "reply": line,
               "text": None, "fields": {}}
        if line:
            m = self.reply_re.search(line)
            if m:
                out["text"] = m.group("text").strip()
                out["fields"] = {k: v for k, v in
                                 re.findall(r"(\w+)=(\S+)", out["text"])}
        return out

    def close(self) -> None:
        try:
            self.ch.close()
        finally:
            self.logf.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--node", default="BSF6C53")
    ap.add_argument("--port", default=None)
    ap.add_argument("--label", default="bench")
    sub = ap.add_subparsers(dest="mode", required=True)

    o = sub.add_parser("observe")
    o.add_argument("--seconds", type=float, required=True)
    o.add_argument("--before", type=float, default=0.0)

    c = sub.add_parser("cmd")
    c.add_argument("commands", nargs="+")
    c.add_argument("--observe-before", type=float, default=3.0)
    c.add_argument("--observe-after", type=float, default=5.0)
    c.add_argument("--gap", type=float, default=1.0)
    c.add_argument("--timeout", type=float, default=8.0)

    args = ap.parse_args()
    b = Bench(args.node, args.outdir, args.port, args.label)
    results = []
    try:
        if args.mode == "observe":
            b.observe(args.before)
            b.observe(args.seconds)
        else:
            b.observe(args.observe_before)
            for cmd in args.commands:
                r = b.command(cmd, args.timeout)
                results.append(r)
                print(json.dumps({k: r[k] for k in
                                  ("cmd", "latency_s", "text", "fields")},
                                 indent=2), flush=True)
                b.observe(args.gap)
            b.observe(args.observe_after)
    finally:
        b.close()
    if results:
        (args.outdir / f"commands_{args.label}.json").write_text(
            json.dumps(results, indent=2) + "\n")
    print(f"[bench] log -> {b.logpath}")
    return 0 if all(r["reply"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

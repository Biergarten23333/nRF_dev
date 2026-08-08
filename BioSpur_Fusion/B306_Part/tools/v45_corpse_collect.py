#!/usr/bin/env python3
"""Collect a v45 corpse over the EXISTING vendor channel.

MASTER FIRMWARE IS FROZEN AT dk-v36 AND THIS SCRIPT DOES NOT CHANGE THAT.
--------------------------------------------------------------------------
Everything here rides machinery that already exists: an opaque command string
to a named node, and an opaque fixed-length read of the stall characteristic
that the DK hex-dumps without parsing. The node added opcodes (`V45 STATUS`,
`V45 PAGE=`, `V45 ACK=`); the master neither knows nor cares. If this script
ever needs the master to understand something, that is a HARD GATE FAILURE to
report, not a licence to bump dk versions.

THE RULES THIS SCRIPT ENFORCES, AND WHY
---------------------------------------
1. On any reconnect, query corpse status FIRST. A node that self-reset carries
   its corpse in `.noinit`, which the next power cycle destroys -- and with
   BSF_CORPSE_FLASH_ENABLED=0 (the default; see CONTEXT_AUDIT item 11) `.noinit`
   is the ONLY copy. The N8 run lost three corpses to exactly this window.
2. ACK-clear ONLY after every CRC verifies and the evidence file is on disk.
   An unverified clear is how a corpse gets lost twice.
3. A V45_WEDGE self-reset is EXPECTED for at least 60 s. No quarantine, no
   removal from the expected set, no alarm beyond a log line. A harness that
   treats the recovery as a failure will drop the node that just produced the
   evidence.
4. Every trigger goes into the run ledger with cause and both watermark ages,
   so the rate statistics stay comparable to v43/v44. v45 adds no periodic
   inbound traffic and changes nothing before a trigger -- that invariant is
   what makes the comparison legitimate, and it is asserted in the report.
"""
from __future__ import annotations

import argparse
import binascii
import json
import re
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bsf_v45_corpse_decode as dec          # noqa: E402

PAGE_FMT = "<BBBBHHHH"                       # wire_tag,page,pages,form,total,off,crc16,seq
PAGE_HDR = struct.calcsize(PAGE_FMT)         # 12
PAGE_DATA = 220
PAGE_SIZE = PAGE_HDR + PAGE_DATA             # 232
V45_PAGE_FORM = 0xC5
REJOIN_GRACE_S = 60.0                        # measured rejoin is ~20.7 s


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def crc16(data: bytes) -> int:
    """Mirror of bsf_stall_ring_crc16() -- CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class CollectError(Exception):
    pass


class Collector:
    """Drives one node's retrieval. Transport is injected, so this is testable."""

    def __init__(self, node: str, send, expect, log=print):
        self.node = node
        self._send = send        # send(command_string) -> None
        self._expect = expect    # expect(pattern, timeout_s) -> matched line or None
        self.log = log

    # -- primitives --------------------------------------------------------
    def command(self, cmd: str, timeout: float = 6.0) -> str:
        self._send(f"{self.node} {cmd}")
        line = self._expect(rf"FUSION_CONTROL_REPLY .*name={self.node}\b.*", timeout)
        if line is None:
            raise CollectError(f"{self.node}: no reply to {cmd!r}")
        return line

    def status(self) -> dict:
        line = self.command("V45 STATUS")
        f = dict(re.findall(r"(\w+)=(\S+)", line))
        return {k: int(v) for k, v in f.items()
                if k in ("present", "seq", "cause", "len", "pages", "core",
                         "ch", "ring", "flash") and v.isdigit()}

    def read_page(self, n: int, retries: int = 3) -> bytes:
        """One 220-byte slice. Idempotent on the node, so retrying is free."""
        for attempt in range(retries):
            self.command(f"V45 PAGE={n}")
            line = self._expect(rf"FUSION_STALL_READ .*name={self.node}\b.*", 6.0)
            if line is None:
                continue
            m = re.search(r"hex=([0-9a-fA-F]+)", line)
            if not m:
                continue
            raw = binascii.unhexlify(m.group(1))
            if len(raw) != PAGE_SIZE:
                self.log(f"  page {n}: unexpected length {len(raw)}, retrying")
                continue
            tag, page, pages, form, total, off, c16, seq = struct.unpack_from(
                PAGE_FMT, raw, 0)
            body = raw[PAGE_HDR:]
            if form != V45_PAGE_FORM:
                self.log(f"  page {n}: form 0x{form:02x} is not a v45 page "
                         "(the selection may have aged out); retrying")
                continue
            if page != n:
                self.log(f"  page {n}: node returned page {page}; retrying")
                continue
            if crc16(body) != c16:
                self.log(f"  page {n}: CRC16 mismatch (attempt {attempt + 1})")
                continue
            return body
        raise CollectError(f"{self.node}: page {n} unreadable after {retries} tries")

    # -- the whole retrieval ----------------------------------------------
    def collect(self, outdir: Path) -> dict | None:
        st = self.status()
        if not st.get("present"):
            return None
        total, pages, seq = st["len"], st["pages"], st["seq"]
        self.log(f"{self.node}: corpse seq={seq} cause={st['cause']} "
                 f"{total} B in {pages} pages")

        blob = bytearray()
        for n in range(pages):
            blob += self.read_page(n)
        blob = bytes(blob[:total])

        # Verify BEFORE anything is cleared. A decode failure here means the
        # corpse stays on the node and is offered again on the next reconnect.
        try:
            decoded = dec.decode_image(blob)
        except dec.Reject as e:
            raise CollectError(f"{self.node}: corpse seq={seq} failed to "
                               f"decode ({e}) -- NOT acknowledged, retained "
                               "on the node for the next reconnect")
        if decoded.core["corpse_seq"] != seq:
            raise CollectError(f"{self.node}: seq mismatch "
                               f"{decoded.core['corpse_seq']} != {seq}")

        outdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = outdir / f"{self.node}_v45corpse_seq{seq}_{stamp}"
        raw_path = base.with_suffix(".bin")
        raw_path.write_bytes(blob)
        agg = binascii.crc32(blob) & 0xFFFFFFFF
        base.with_suffix(".txt").write_text(dec.report(decoded) + "\n")
        meta = {
            "node": self.node, "seq": seq, "wall": wall(),
            "bytes": len(blob), "pages": pages,
            "aggregate_crc32": f"{agg:08x}",
            "trigger_cause": decoded.core["trigger_cause"],
            "trigger_cause_name": decoded.core["trigger_cause_name"],
            "notify_exit_age_ms": decoded.core["notify_exit_age_ms"],
            "ncp_packet_age_ms": decoded.core["ncp_packet_age_ms"],
            "suspect_start_ms": decoded.core["suspect_start_ms"],
            "uptime_ms": decoded.core["uptime_ms"],
            "verdict": dec.verdict(decoded.core),
            "banks": [{"name": b["name"], "entries": b["entries"]}
                      for b in decoded.banks],
        }
        base.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
        self.log(f"{self.node}: wrote {raw_path.name} crc32={agg:08x}")
        self.log(f"{self.node}: VERDICT {meta['verdict']}")

        # ONLY NOW.
        reply = self.command(f"V45 ACK={seq}")
        if "ACK ok" not in reply:
            self.log(f"{self.node}: ACK refused ({reply.strip()}); the corpse "
                     "stays on the node and will be offered again")
            meta["acked"] = False
        else:
            meta["acked"] = True
        base.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
        return meta


def append_ledger(path: Path, entry: dict) -> None:
    """One line per trigger, so the v43/v44/v45 rate comparison stays honest."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--nodes", nargs="+", required=True)
    ap.add_argument("--port", default=None, help="Fusion Master CDC")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="seconds to keep watching (0 = one sweep and exit)")
    ap.add_argument("--ledger", type=Path, default=None)
    args = ap.parse_args()

    from async_line_channel import ThreadedLineChannel      # noqa: E402
    from coldstart_fusion_control import decode_guard       # noqa: E402
    from fusion_session import resolve_fusion_port          # noqa: E402

    args.outdir.mkdir(parents=True, exist_ok=True)
    ledger = args.ledger or (args.outdir / "v45_trigger_ledger.jsonl")
    logf = (args.outdir / "fusion_cdc.log").open("a", encoding="utf-8", buffering=1)
    ch = ThreadedLineChannel(resolve_fusion_port(args.port), logf, "FUSION",
                             decoded_queue_records=262144,
                             backlog_red_records=32768,
                             raw_backlog_red_bytes=32768, stall_red_s=2)
    ch.transport_mode = "binary"
    ch.text_pending.clear()
    decode_guard(ch, 20)

    pending: list[str] = []

    def expect(pattern: str, timeout: float):
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for i, held in enumerate(pending):
                if rx.search(held):
                    return pending.pop(i)
            line = ch.read(min(deadline, time.monotonic() + 0.5))
            if not line:
                continue
            if rx.search(line):
                return line
            pending.append(line)
            if len(pending) > 4096:
                del pending[:2048]
        return None

    # Nodes that self-reset are EXPECTED back. Never quarantine them.
    grace: dict[str, float] = {}
    results = []
    end = time.monotonic() + args.duration
    try:
        while True:
            for node in args.nodes:
                if grace.get(node, 0.0) > time.monotonic():
                    continue
                try:
                    c = Collector(node, ch.send, expect)
                    meta = c.collect(args.outdir)
                except CollectError as e:
                    print(f"  {e}")
                    continue
                if meta:
                    results.append(meta)
                    append_ledger(ledger, meta)
                    grace[node] = time.monotonic() + REJOIN_GRACE_S
                    print(f"  {node}: V45_WEDGE reset is EXPECTED for the next "
                          f"{REJOIN_GRACE_S:.0f} s -- not a fault, not a "
                          "quarantine")
            if time.monotonic() >= end:
                break
            time.sleep(5.0)
    finally:
        ch.close()
        logf.close()

    print(json.dumps({"collected": len(results),
                      "nodes": sorted({r["node"] for r in results}),
                      "ledger": str(ledger)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""4.5 long run — single owner of the Fusion CDC.

Records everything raw (hourly files), maintains only the light live state
needed to act, and does the scheduled bounded polling. The full yield ladder is
computed offline from the raw archive rather than live, so a heavy analysis
cannot perturb or fall behind the capture it is measuring.

Abort conditions are exactly three, per section 4.5:
  * all nodes gone
  * Fusion Master down
  * disk near full
Everything else — resets, relocks, disconnects, UART restarts, dropouts,
battery deaths — is a measured sample and never stops the run.
"""
import json
import os
import re
import shutil
import signal
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/tools")
from async_line_channel import ThreadedLineChannel            # noqa: E402
from coldstart_fusion_control import decode_guard             # noqa: E402
from fusion_session import parse_fields, parse_reply, resolve_fusion_port  # noqa: E402

NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165")

SILENT_THRESHOLD_S = 2.0        # matches the established DATA_PLANE_SILENT rule
SILENT_READ_EVERY_S = 5.0       # re-read the status characteristic while silent
STALL_READS_BEFORE_RECONNECT = 8  # 8 x 5 s spans the 30 s ATT timeout, then escalate
POLL_CYCLE_S = 300.0            # full per-node query sweep
CORPSE_SWEEP_S = 90.0           # dedicated, cheap: one CORPSE STATUS per node
SEND_SPACING_S = 0.15           # keep the command plane from bursting
DISK_ABORT_FREE_BYTES = 8 * 1024 ** 3
MASTER_DOWN_S = 120.0           # no decodable line at all for this long

PER_NODE_QUERIES = [
    ("CORPSE STATUS", "CORPSE present="),
    ("STALL READ", "FUSION_STALL_READ"),
    ("STALL STATUS", "STALL "),
    ("COUNTERS", "CTR1 "),
    ("STACKS", "STACKS "),
    ("QUEUE PUB HIST=0", "QUEUE PUB HIST p=0 "),
    ("QUEUE PUB HIST=1", "QUEUE PUB HIST p=1 "),
    ("QUEUE PUB HIST=2", "QUEUE PUB HIST p=2 "),
    ("QUEUE PUB HIST=3", "QUEUE PUB HIST p=3 "),
]

RING_STATUS_RE = re.compile(r"\btext=RING .*?\bpages=(\d+)")
CORPSE_STATUS_RE = re.compile(
    r"text=CORPSE present=(\d+) seq=(\d+) pages=(\d+)")
CONT_RE = re.compile(r"^\s*pool\d+=[0-9a-f]+:\d+/\d+(\s+pool\d+=[0-9a-f]+:\d+/\d+)*\s*$")


def wall():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class Run:
    def __init__(self, out_dir, duration_s):
        self.dir = out_dir
        self.duration_s = duration_s
        self.t0 = None
        self.hour = -1
        self.raw = None
        self.events = (out_dir / "events.jsonl").open("x", encoding="utf-8", buffering=1)
        self.polls = (out_dir / "polls.jsonl").open("x", encoding="utf-8", buffering=1)
        self.pools = (out_dir / "pools.jsonl").open("x", encoding="utf-8", buffering=1)
        # The primary deliverable gets its own file so it can never be lost in
        # the noise of a multi-hour raw archive.
        self.rings = (out_dir / "rings.jsonl").open("x", encoding="utf-8", buffering=1)
        self.counts = defaultdict(Counter)
        self.last_uwb = {}
        self.last_imu = {}
        self.last_any = {}
        self.silent = set()
        self.counts_ok = set(NODES)
        self.first_seen = {}
        self.silent_last_read = {}
        # 5.1 escalation, once per silence episode per node:
        #   0 read status -> 1 RECONNECT -> 2 retrieve ring -> 3 done
        self.stall_stage = {}
        self.stall_reads = defaultdict(int)
        self.ring_pages_wanted = {}
        self.corpse_pages_wanted = {}
        self.corpse_seq = {}
        self.corpses_seen = {}
        self.stop_reason = None

    # --- raw archive, rotated hourly so a long run stays tractable ---
    def raw_line(self, line):
        h = int((time.monotonic() - self.t0) // 3600)
        if h != self.hour:
            if self.raw:
                self.raw.close()
            self.hour = h
            self.raw = (self.dir / f"fusion_h{h:02d}.log").open(
                "a", encoding="utf-8", buffering=1)
        self.raw.write(f"{time.time():.6f} {time.monotonic():.6f} {line}\n")

    def event(self, kind, **kw):
        rec = {"t": round(time.monotonic() - self.t0, 3), "wall": wall(),
               "kind": kind, **kw}
        self.events.write(json.dumps(rec, sort_keys=True) + "\n")
        print(f"[{rec['t']:9.1f}s] {kind} "
              + " ".join(f"{k}={v}" for k, v in kw.items() if k != "detail"),
              flush=True)

    def close(self):
        for f in (self.raw, self.events, self.polls, self.pools, self.rings):
            if f:
                try:
                    f.close()
                except Exception:
                    pass


def main():
    out_dir = Path(sys.argv[1])
    duration_s = float(sys.argv[2]) if len(sys.argv) > 2 else 6 * 3600.0
    out_dir.mkdir(parents=True, exist_ok=False)

    summary = {"status": "RUNNING", "started_wall": wall(),
               "duration_requested_s": duration_s, "nodes": list(NODES)}
    run = Run(out_dir, duration_s)
    stop = {"flag": False}

    def on_sig(_s, _f):
        stop["flag"] = True
        run.stop_reason = "signal"
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    with (out_dir / "channel.log").open("x", encoding="utf-8", buffering=1) as chlog:
        port = resolve_fusion_port(None)
        summary["port"] = port
        ch = ThreadedLineChannel(port, chlog, "FUSION",
                                 decoded_queue_records=1048576,
                                 backlog_red_records=131072,
                                 raw_backlog_red_bytes=131072, stall_red_s=2)
        started = None
        pool_pending = None
        try:
            ch.transport_mode = "binary"
            ch.text_pending.clear()
            summary["guard"] = decode_guard(ch, 30)
            started = run.t0 = time.monotonic()
            deadline = started + duration_s
            next_poll = started + 60.0
            # First corpse sweep early: a board that wedged and reset
            # during the rollout is already holding one.
            next_corpse = started + 20.0
            next_tick = started + 60.0
            last_line_at = started
            pending = []              # queued (node, cmd, prefix, deadline)
            next_send = started

            run.event("RUN_OPEN", port=port, duration_s=duration_s)

            while time.monotonic() < deadline and not stop["flag"]:
                now = time.monotonic()

                # --- scheduled per-node sweep -------------------------------
                if now >= next_poll:
                    for n in NODES:
                        for cmd, prefix in PER_NODE_QUERIES:
                            pending.append([n, cmd, prefix, None])
                    next_poll = now + POLL_CYCLE_S
                    run.event("POLL_CYCLE_QUEUED", queries=len(pending))

                # --- silence handling: the board recovers itself ----------
                # v43 REPLACES the 5.1 escalation. RECONNECT is not issued at
                # all: it was shown to remove a board permanently from the fleet
                # while adding nothing, and the ring it was meant to reach is
                # now carried inside the corpse anyway. On a wedge the board
                # captures its own corpse, resets itself, comes back, and hands
                # the corpse over on the next CORPSE STATUS sweep.
                #
                # One STALL READ per episode is still issued, purely for the
                # record: it is the only way to capture the status-snapshot form
                # at the moment of silence, and it is bounded by the stack's own
                # ATT timeout. Nothing escalates past it.
                for n in list(run.silent):
                    if now - run.silent_last_read.get(n, 0) < SILENT_READ_EVERY_S:
                        continue
                    run.silent_last_read[n] = now
                    if run.stall_stage.get(n, 0) == 0:
                        pending.insert(0, [n, "STALL READ", "FUSION_STALL_READ", None])
                        run.stall_stage[n] = 1
                        run.event("STALL_SEQ_READ_SENT", node=n)

                # --- corpse sweep and retrieval -----------------------------
                if now >= next_corpse:
                    for n in NODES:
                        pending.append([n, "CORPSE STATUS", "CORPSE present=", None])
                    next_corpse = now + CORPSE_SWEEP_S

                for n, want in list(run.corpse_pages_wanted.items()):
                    if not want:
                        continue
                    page = want.pop(0)
                    # select, then read: the page is served by the ordinary
                    # 232-byte read of the stall characteristic, and dk-v35
                    # hex-dumps it without parsing.
                    pending.append([n, f"CORPSE PAGE={page}", "CORPSE PAGE", None])
                    pending.append([n, "STALL READ", "FUSION_STALL_RING", None])
                    if not want:
                        seq = run.corpse_seq.get(n)
                        # ACK LAST, and only after every page has been asked
                        # for. Only a positive ACK clears the board's valid
                        # marker, so an interrupted walk simply retries on the
                        # next sweep instead of losing the corpse.
                        pending.append([n, f"CORPSE ACK={seq}", "CORPSE ACK", None])
                        pending.append([n, "CORPSE PAGE OFF", "CORPSE PAGE OFF", None])
                        run.corpse_pages_wanted.pop(n, None)
                        run.event("CORPSE_WALK_COMPLETE", node=n, seq=seq)

                if pending and now >= next_send:
                    n, cmd, prefix, _ = pending.pop(0)
                    ch.send(f"{n} {cmd}")
                    run.polls.write(json.dumps(
                        {"t": round(now - run.t0, 3), "wall": wall(),
                         "sent": f"{n} {cmd}"}, sort_keys=True) + "\n")
                    next_send = now + SEND_SPACING_S

                line = ch.read(min(deadline, now + 0.25))
                now = time.monotonic()
                if line:
                    last_line_at = now
                    run.raw_line(line)
                    kind = line.split(" ", 1)[0]
                    f = parse_fields(line)
                    name = f.get("name")
                    if name in run.counts_ok:
                        run.last_any[name] = now
                    # `RING boot=.. count=..  pages=N ..` tells us how many
                    # pages to fetch. Queue them all; retrieval is idempotent.
                    m_corpse = CORPSE_STATUS_RE.search(line)
                    if m_corpse and name in run.counts_ok:
                        present, seq, pages = (int(m_corpse.group(1)),
                                               int(m_corpse.group(2)),
                                               int(m_corpse.group(3)))
                        if present and run.corpses_seen.get(name) != seq:
                            run.corpses_seen[name] = seq
                            run.corpse_seq[name] = seq
                            run.corpse_pages_wanted[name] = list(range(pages))
                            run.event("CORPSE_PRESENT", node=name, seq=seq,
                                      pages=pages, detail=line.strip())

                    m_ring = RING_STATUS_RE.search(line)
                    if m_ring and name in run.counts_ok:
                        pages = int(m_ring.group(1))
                        if name not in run.ring_pages_wanted:
                            run.ring_pages_wanted[name] = list(range(pages))
                            run.event("RING_STATUS", node=name, pages=pages,
                                      detail=line.strip())

                    # A pool record arrives as a header plus ` poolN=` lines, so
                    # continuations must be joined before anything else claims
                    # them. Checked first because they match no record kind.
                    if pool_pending is not None and CONT_RE.match(line):
                        pool_pending["raw"] += " " + line.strip()
                    else:
                        if pool_pending is not None:
                            run.pools.write(
                                json.dumps(pool_pending, sort_keys=True) + "\n")
                            pool_pending = None
                        if kind in ("FUSION_POOL", "FUSION_MASTER_POOL"):
                            pool_pending = {"t": round(now - run.t0, 3),
                                            "wall": wall(), "raw": line}
                        elif name in run.counts_ok and kind == "FUSION_UWB":
                            run.last_uwb[name] = now
                            run.counts[name]["uwb"] += 1
                        elif name in run.counts_ok and kind == "FUSION_IMU":
                            run.last_imu[name] = now
                            try:
                                run.counts[name]["imu"] += int(f.get("n", "0"), 0)
                            except ValueError:
                                pass
                        elif kind in ("FUSION_STALL_RING", "FUSION_STALL_RING_HEX"):
                            run.rings.write(json.dumps(
                                {"t": round(now - run.t0, 3), "wall": wall(),
                                 "node": name, "raw": line},
                                sort_keys=True) + "\n")
                            if kind == "FUSION_STALL_RING":
                                run.event("RING_PAGE_RECEIVED", node=name,
                                          page=f.get("page"), pages=f.get("pages"),
                                          entries=f.get("entries"))
                        elif kind.startswith("FUSION_RECONNECT"):
                            run.event("RECONNECT_" + kind.split("_", 1)[1],
                                      node=name,
                                      down_interval_ms=f.get("down_interval_ms"),
                                      bridge_interval_ms=f.get("bridge_interval_ms"),
                                      detail=line.strip())
                        elif kind.startswith("FUSION_STALL") \
                                or kind == "FUSION_REPLY" \
                                or kind.startswith("FUSION_COMMAND_REJECT"):
                            run.polls.write(json.dumps(
                                {"t": round(now - run.t0, 3), "wall": wall(),
                                 "line": line}, sort_keys=True) + "\n")
                        if kind in ("FUSION_CONNECTED", "FUSION_DISCONNECTED",
                                    "FUSION_BRIDGE_READY"):
                            run.event(kind, node=name, detail=line)

                # --- DATA_PLANE_SILENT transitions --------------------------
                for n in NODES:
                    if n not in run.last_any:
                        continue        # never seen at all — absence, not silence
                    if now - run.last_any[n] > 30.0:
                        if n in run.silent:
                            run.silent.discard(n)
                            run.event("NODE_GONE", node=n)
                        continue        # link itself is down; not a data stall
                    qu = now - run.last_uwb.get(n, run.t0)
                    qi = now - run.last_imu.get(n, run.t0)
                    if qu > SILENT_THRESHOLD_S and qi > SILENT_THRESHOLD_S:
                        if n not in run.silent:
                            run.silent.add(n)
                            run.silent_last_read[n] = 0.0
                            run.event("DATA_PLANE_SILENT", node=n,
                                      uwb_s=round(qu, 2), imu_s=round(qi, 2))
                    elif n in run.silent:
                        run.silent.discard(n)
                        run.event("DATA_PLANE_RESUMED", node=n)

                # --- abort conditions: exactly three ------------------------
                if now - last_line_at > MASTER_DOWN_S:
                    run.stop_reason = "ABORT_FUSION_MASTER_DOWN"
                    break
                # Depletion means nothing is arriving from any node, not that
                # every node sits in `silent` — a node whose link drops is
                # removed from that set, so the old test could never reach the
                # full roster and the abort would never fire.
                if now - started > 300 and run.last_any and \
                        all(now - v > 300 for v in run.last_any.values()):
                    run.stop_reason = "ABORT_ALL_NODES_GONE"
                    break
                if now >= next_tick:
                    free = shutil.disk_usage(str(out_dir)).free
                    if free < DISK_ABORT_FREE_BYTES:
                        run.stop_reason = "ABORT_DISK_NEAR_FULL"
                        break
                    # `live` must mean delivering, not merely "not flagged
                    # silent" — a node never seen is absent, not live, and
                    # counting it inflates the roster in the overnight log.
                    delivering = sorted(
                        n for n in NODES
                        if now - run.last_uwb.get(n, -1e9) < 30.0
                        or now - run.last_imu.get(n, -1e9) < 30.0)
                    linked = sorted(n for n in NODES
                                    if now - run.last_any.get(n, -1e9) < 30.0)
                    run.event("TICK", elapsed_min=round((now - started) / 60, 1),
                              delivering=len(delivering), linked=len(linked),
                              nodes=",".join(delivering) or "none",
                              silent=sorted(run.silent),
                              free_gb=round(free / 1024 ** 3, 1))
                    next_tick = now + 60.0

            summary["status"] = "COMPLETE" if not run.stop_reason else run.stop_reason
        except KeyboardInterrupt:
            summary["status"] = "INTERRUPTED"; summary["stop_reason"] = "KeyboardInterrupt"
        except BaseException as exc:
            summary["status"] = "FAILED"
            summary["stop_reason"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if summary["status"] == "RUNNING":
                summary["status"] = "INTERRUPTED"
                summary.setdefault("stop_reason", "closeout without a terminal status")
            if run.stop_reason:
                summary.setdefault("stop_reason", run.stop_reason)
            if started is not None:
                summary["duration_actual_s"] = round(time.monotonic() - started, 3)
            summary["ended_wall"] = wall()
            summary["live_counts"] = {n: dict(c) for n, c in run.counts.items()}
            summary["silent_at_end"] = sorted(run.silent)
            if pool_pending is not None:
                run.pools.write(json.dumps(pool_pending, sort_keys=True) + "\n")
            summary["health"] = ch.health_snapshot()
            ch.close()
            run.close()
            (out_dir / "result.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

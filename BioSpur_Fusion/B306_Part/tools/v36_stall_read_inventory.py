#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS
from fusion_session import parse_fields, resolve_fusion_port

root = Path(sys.argv[1]); root.mkdir(exist_ok=False)
out = {"status": "RUNNING", "nodes": {}}
with (root / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
    ch = ThreadedLineChannel(resolve_fusion_port(None), log, "FUSION",
        decoded_queue_records=131072, backlog_red_records=16384,
        raw_backlog_red_bytes=16384, stall_red_s=2)
    try:
        ch.transport_mode = "binary"; ch.text_pending.clear()
        out["guard"] = decode_guard(ch, 15)
        for node in SLOTS:
            ch.send(f"{node} STALL READ")
            deadline = time.monotonic() + 8; row = None
            while time.monotonic() < deadline:
                line = ch.read(deadline)
                if line and line.startswith(f"FUSION_STALL_READ name={node} "):
                    row = parse_fields(line); row["raw"] = line; break
            if row is None: raise RuntimeError(f"{node} STALL READ timeout")
            if row.get("att_err") != "0" or row.get("parse") == "fail":
                raise RuntimeError(f"{node} STALL READ invalid: {row}")
            if row.get("len") != "100" or row.get("armed") != "1":
                raise RuntimeError(f"{node} status not sane/armed: {row}")
            out["nodes"][node] = row
        out["status"] = "PASS"
    except KeyboardInterrupt:
        # Not an Exception subclass, so the handler below never saw it and the
        # closeout wrote the initial RUNNING.
        out["status"] = "INTERRUPTED"; out["stop_reason"] = "KeyboardInterrupt"
    except Exception as exc:
        out["status"] = "FAIL"; out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # The writer runs on every exit path, so a status left at RUNNING would
        # make a stopped run look live. No exit path may leave RUNNING on disk.
        if out["status"] == "RUNNING":
            out["status"] = "INTERRUPTED"
            out.setdefault("stop_reason", "closeout reached without a terminal status")
        out["host_health"] = ch.health_snapshot(); ch.close()
        (root / "result.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
raise SystemExit(0 if out["status"] == "PASS" else 2)

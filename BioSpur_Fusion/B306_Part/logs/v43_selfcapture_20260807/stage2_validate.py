#!/usr/bin/env python3
"""Stage 2 -- single-board validation of the v43 self-capture pipeline.

Exercises every link the field run depends on, on a healthy board, before the
fleet OTA:

  CORPSE FORCE -> monitor fires -> corpse captured -> CRC32 -> retained .noinit
  -> sys_reboot -> BLE reconnect -> CORPSE STATUS -> CORPSE PAGE=n
  -> dual-form 232 B read -> dk-v35 raw hex dump -> bt_corpse_decode.py
  -> CORPSE ACK -> valid marker cleared

The forced trigger validates the RECORDER AND RECOVERY PIPELINE ONLY. It does
not reproduce the BLE failure and is never reported as one -- the decoder
classifies it DIAGNOSTIC_FALSE_POSITIVE on the trigger field alone.

It also harvests the per-stage maximum dwell measured on the real path, which
is what justifies the monitor's 5 s threshold from measurement rather than
assumption.
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
sys.path.insert(0, str(ROOT / "B306_Part/tools"))
sys.path.insert(0, str(ROOT / "UWB_Part/logs/deploy_20260806"))
from b_fusion_ops import ThreadedLineChannel, resolve_fusion_port, decode_guard  # noqa
import bt_corpse_decode as bcd  # noqa

CYC_PER_SEC = 64_000_000  # nRF52840 k_cycle_get_32() runs at 64 MHz

node = sys.argv[1]
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=False)
res = {"node": node, "status": "RUNNING", "steps": []}


def note(step, ok, **kw):
    rec = {"step": step, "ok": bool(ok), **kw}
    res["steps"].append(rec)
    flag = "ok  " if ok else "FAIL"
    extra = " ".join(f"{k}={v}" for k, v in kw.items() if k != "raw")
    print(f"  {flag} {step:<28} {extra}"[:200], flush=True)
    return ok


with (out / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
    ch = ThreadedLineChannel(resolve_fusion_port(None), log, "FUSION",
                             decoded_queue_records=262144,
                             backlog_red_records=32768,
                             raw_backlog_red_bytes=32768, stall_red_s=2)
    try:
        ch.transport_mode = "binary"
        ch.text_pending.clear()
        res["guard"] = decode_guard(ch, 20.0)

        def ask(cmd, want, timeout=25.0):
            ch.send(f"{node} {cmd}")
            dl = time.monotonic() + timeout
            while time.monotonic() < dl:
                line = ch.read(dl)
                if not line or line.startswith("FUSION_COMMAND_TX"):
                    continue
                if want in line and node in line:
                    return line
            return None

        def wait_back(timeout=180.0):
            """Poll PING until the board answers again after its self-reset."""
            dl = time.monotonic() + timeout
            while time.monotonic() < dl:
                r = ask("PING", "PONG", timeout=10.0)
                if r:
                    return r
            return None

        # --- 1. baseline -------------------------------------------------
        st = ask("STATUS", "STATUS fw=")
        note("baseline STATUS", st is not None and "v43" in (st or ""), raw=st)
        res["baseline_status"] = st

        cs = ask("CORPSE STATUS", "CORPSE present=")
        res["corpse_before"] = cs
        pre_present = re.search(r"present=(\d+)", cs or "")
        note("corpse absent before force", bool(pre_present) and pre_present.group(1) == "0",
             raw=cs)

        # --- 2. force the trigger ----------------------------------------
        f = ask("CORPSE FORCE", "CORPSE FORCE armed")
        note("CORPSE FORCE accepted", f is not None, raw=f)
        t_force = time.monotonic()

        # --- 3. it must disappear and come back on its own ---------------
        back = wait_back(180.0)
        res["reconnect_s"] = round(time.monotonic() - t_force, 2)
        note("board self-reset and reconnected", back is not None,
             elapsed_s=res["reconnect_s"], raw=back)

        # --- 4. the corpse must have survived the reset ------------------
        cs2 = ask("CORPSE STATUS", "CORPSE present=", timeout=30.0)
        res["corpse_after"] = cs2
        m = re.search(r"present=(\d+) seq=(\d+) pages=(\d+) len=(\d+)", cs2 or "")
        note("corpse retained across reset", bool(m) and m.group(1) == "1", raw=cs2)
        if not m or m.group(1) != "1":
            res["status"] = "FAIL"
            raise SystemExit(json.dump(res, (out / "result.json").open("w"), indent=2))
        seq, npages, clen = int(m.group(2)), int(m.group(3)), int(m.group(4))
        res.update(corpse_seq=seq, corpse_pages=npages, corpse_len=clen)

        # --- 5. walk every page ------------------------------------------
        pages = []
        for p in range(npages):
            sel = ask(f"CORPSE PAGE={p}", "CORPSE PAGE ok")
            if not sel:
                note(f"select page {p}", False)
                break
            ch.send(f"{node} STALL READ")
            dl = time.monotonic() + 40.0
            hexes = {}
            while time.monotonic() < dl:
                ln = ch.read(dl)
                if not ln or node not in ln or ln.startswith("FUSION_COMMAND_TX"):
                    continue
                mm = re.search(r"FUSION_STALL_RING_HEX name=\S+ off=(\d+) n=(\d+) ([0-9a-f]+)", ln)
                if mm:
                    hexes[int(mm.group(1))] = mm.group(3)
                if sum(len(v) // 2 for v in hexes.values()) >= 232:
                    break
            blob = b"".join(bytes.fromhex(hexes[o]) for o in sorted(hexes))
            if len(blob) != 232:
                note(f"page {p} bytes", False, got=len(blob))
                break
            try:
                pg = bcd.decode_page(blob)
            except ValueError as exc:
                note(f"page {p} decode", False, err=str(exc))
                break
            note(f"page {p}", pg["crc_ok"], crc_ok=pg["crc_ok"], off=pg["offset"])
            pages.append(pg)
        res["pages_fetched"] = len(pages)
        note("all pages fetched", len(pages) == npages, got=len(pages), want=npages)

        # --- 6. decode ----------------------------------------------------
        if len(pages) == npages:
            blob, bad, missing = bcd.merge(pages)
            (out / "corpse.bin").write_bytes(blob)
            c = bcd.decode(blob)
            c["classification"] = bcd.classify(c)
            res["corpse"] = c
            note("corpse CRC32", c["crc32_ok"], crc32_ok=c["crc32_ok"])
            note("classified as pipeline test",
                 c["classification"] == "DIAGNOSTIC_FALSE_POSITIVE",
                 classification=c["classification"], trigger=c["trigger"])
            note("BT RX thread was located", c["rx_capture_ok"] == 1,
                 addr=c["rx_thread_addr"], state=c["rx_thread_state_bits"])
            note("BT RX stack measured", c["rx_stack_size"] > 0,
                 size=c["rx_stack_size"], unused=c["rx_stack_unused"],
                 used=c["rx_stack_size"] - c["rx_stack_unused"])
            note("conn fields captured", c["conn"]["valid"] == 1,
                 state=c["conn"]["state"],
                 tx_busy=c["conn"]["tx_complete_busy_bits"],
                 deferred=c["conn"]["deferred_busy_bits"],
                 pkts=c["conn"]["pkts_avail"])

            # healthy stage dwell -> the 5 s threshold's margin
            mx = {k: v for k, v in c["stage_max_cycles"].items()}
            mx_ms = {k: round(v * 1000.0 / CYC_PER_SEC, 4) for k, v in mx.items()}
            res["stage_max_ms"] = mx_ms
            worst = max(mx_ms.values()) if mx_ms else 0.0
            res["stage_max_worst_ms"] = worst
            res["threshold_margin_x"] = round(5000.0 / worst, 1) if worst else None
            note("healthy dwell measured", bool(mx_ms),
                 worst_ms=worst, margin_x=res["threshold_margin_x"])

        # --- 7. ACK clears it, and only a correct ACK ---------------------
        badack = ask(f"CORPSE ACK={seq + 999}", "CORPSE ACK")
        note("wrong ACK is refused", "REJECT" in (badack or ""), raw=badack)
        ack = ask(f"CORPSE ACK={seq}", "CORPSE ACK")
        note("correct ACK accepted", "ok" in (ack or ""), raw=ack)
        cs3 = ask("CORPSE STATUS", "CORPSE present=")
        m3 = re.search(r"present=(\d+)", cs3 or "")
        note("valid marker cleared", bool(m3) and m3.group(1) == "0", raw=cs3)

        res["status"] = "PASS" if all(s["ok"] for s in res["steps"]) else "FAIL"
    except Exception as exc:                              # noqa: BLE001
        res["status"] = "ERROR"
        res["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  ERROR {res['error']}", flush=True)
    finally:
        try:
            ch.close()
        except Exception:                                 # noqa: BLE001
            pass

(out / "result.json").write_text(json.dumps(res, indent=2))
print(f"STAGE2 {res['status']}")
sys.exit(0 if res["status"] == "PASS" else 1)

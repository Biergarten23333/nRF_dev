#!/usr/bin/env python3
"""Assert -- and if necessary rebuild -- Fusion Master connection spacing.

LAYER 2 of 3 (batch spacing_default_20260807).

Flashing the DK replays its boot path, and every single-board OTA flashes the DK
twice: once to the updater image and once back to canonical. Before dk-v36 the
Fusion Master's boot path actively applied the 7,500 us comparison baseline over
a controller whose Kconfig default was already the correct 5,000. It never
failed loudly -- boards still connect and still deliver -- so the only symptom
was a wrong connection schedule for an entire window. It cost a window once and
fired twice more in a single night.

This helper exists so that a restore which leaves spacing wrong cannot be
expressed: it is called from inside the restore step, not left to the caller to
remember.

IDEMPOTENT AND VERSION-AGNOSTIC. It reads the state first and only sends
`SPACING ON` if the state is actually wrong. That matters because the two DK
generations behave differently and both must pass:

  * dk-v35 and earlier boot to OFF/7500  -> the command is needed, and applying
    it bumps the generation.
  * dk-v36 and later boot to the derived value already -> the command would be
    answered UNCHANGED and would NOT bump the generation, so requiring a
    generation *increase* would spuriously fail the correct image.

The contract is therefore on the STATE, not on the transition: mode ON, the
expected microseconds, and a positive generation.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from async_line_channel import ThreadedLineChannel          # noqa: E402
from coldstart_fusion_control import decode_guard           # noqa: E402
from fusion_session import resolve_fusion_port              # noqa: E402

# Derived, and pinned to the firmware's own derivation:
#   spacing_us = connection_interval_us / connection_count = 50_000 / 10
# Kept as the two inputs rather than the product so a node-count or interval
# change here is as visible as it is in the firmware.
FUSION_CONN_INTERVAL_US = 50_000
FUSION_PEERS = 10
EXPECTED_SPACING_US = FUSION_CONN_INTERVAL_US // FUSION_PEERS

STATUS_RE = re.compile(
    r"FUSION_SPACING state=(\S+) mode=(\S+) applied_us=(\d+) generation=(\d+) "
    r"transition=(\d+) failed=(\d+)")


def _read_status(ch, timeout_s):
    ch.send("SPACING STATUS")
    dl = time.monotonic() + timeout_s
    last = time.monotonic()
    while time.monotonic() < dl:
        now = time.monotonic()
        if now - last >= 5.0:
            ch.send("SPACING STATUS")       # idempotent read-only re-ask
            last = now
        line = ch.read(min(dl, now + 1.0))
        if not line:
            continue
        m = STATUS_RE.search(line)
        if m:
            return {"state": m.group(1), "mode": m.group(2),
                    "applied_us": int(m.group(3)), "generation": int(m.group(4)),
                    "transition": int(m.group(5)), "failed": int(m.group(6)),
                    "raw": line.strip()}
    return None


def _ok(st):
    return (st is not None and st["mode"] == "ON"
            and st["applied_us"] == EXPECTED_SPACING_US
            and st["generation"] > 0 and st["transition"] == 0
            and st["failed"] == 0)


def _resolve_with_retry(port, timeout_s, res):
    """Wait for the Fusion Master CDC to re-enumerate.

    Required because the first caller of this helper is the restore step, which
    has just reset the DK over J-Link. The USB device disappears and comes back
    a few seconds later, so resolving the port immediately fails with
    "found []" -- which is what the first hardware run of this code did. The
    existing transaction tool papers over the same window with a fixed
    time.sleep(25) before its confirm step; a bounded poll is both faster when
    the device is quick and safer when it is slow.
    """
    if port:
        return port
    deadline = time.monotonic() + timeout_s
    attempts = 0
    last = None
    while time.monotonic() < deadline:
        attempts += 1
        try:
            resolved = resolve_fusion_port(None)
            res["port_wait_attempts"] = attempts
            return resolved
        except Exception as exc:                              # noqa: BLE001
            last = exc
            time.sleep(1.0)
    res["port_wait_attempts"] = attempts
    raise RuntimeError(f"Fusion Master CDC did not re-enumerate within "
                       f"{timeout_s}s: {last}")


def ensure_spacing(out_dir, timeout_s=120.0, port=None, port_wait_s=45.0):
    """Return a result dict. status is PASS only when the state is correct."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {"status": "RUNNING", "expected_us": EXPECTED_SPACING_US,
           "interval_us": FUSION_CONN_INTERVAL_US, "peers": FUSION_PEERS}
    with (out_dir / "spacing_cdc.log").open("a", encoding="utf-8",
                                            buffering=1) as log:
        ch = None
        try:
            # Inside the try: this function's contract is to RETURN a result
            # dict, never to raise, so a CDC that never comes back must become
            # status=ERROR like every other failure rather than escaping.
            ch = ThreadedLineChannel(_resolve_with_retry(port, port_wait_s, res),
                                     log, "FUSION", decoded_queue_records=262144,
                                     backlog_red_records=32768,
                                     raw_backlog_red_bytes=32768, stall_red_s=2)
            ch.transport_mode = "binary"
            ch.text_pending.clear()
            decode_guard(ch, min(20.0, timeout_s))

            before = _read_status(ch, min(30.0, timeout_s))
            res["before"] = before
            if _ok(before):
                # dk-v36 boots correct: nothing to do, and sending the command
                # would only produce an UNCHANGED with no generation bump.
                res["action"] = "none_already_correct"
                res["after"] = before
                res["status"] = "PASS"
                return res

            res["action"] = "rebuilt"
            ch.send("SPACING ON")
            dl = time.monotonic() + timeout_s
            after = None
            while time.monotonic() < dl:
                after = _read_status(ch, min(15.0, max(1.0, dl - time.monotonic())))
                if _ok(after):
                    break
            res["after"] = after
            res["status"] = "PASS" if _ok(after) else "FAIL"
            if res["status"] == "FAIL":
                res["error"] = (
                    f"spacing not ON/{EXPECTED_SPACING_US}/gen>0 within "
                    f"{timeout_s}s: {after}")
            return res
        except Exception as exc:                              # noqa: BLE001
            res["status"] = "ERROR"
            res["error"] = f"{type(exc).__name__}: {exc}"
            return res
        finally:
            try:
                if ch is not None:
                    ch.close()
            except Exception:                                 # noqa: BLE001
                pass
            (out_dir / "spacing_result.json").write_text(
                json.dumps(res, indent=2, sort_keys=True) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir")
    ap.add_argument("--timeout-s", type=float, default=120.0)
    ap.add_argument("--port")
    ap.add_argument("--port-wait-s", type=float, default=45.0)
    a = ap.parse_args()
    r = ensure_spacing(a.out_dir, a.timeout_s, a.port, a.port_wait_s)
    print(json.dumps(r, indent=2, sort_keys=True))
    return 0 if r["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

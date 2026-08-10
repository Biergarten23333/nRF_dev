#!/usr/bin/env python3
"""Cross-language round trip for the stall-ring page.

The pages under test are emitted by the firmware's own policy header compiled
natively (`test_stall_ring_policy --emit-page N`), so this checks the decoder
against the real struct layout rather than against a Python restatement of it.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "tools"))
import stall_ring_decode as srd  # noqa: E402

tests_dir = root / "firmware/tests"
binary = Path(tempfile.gettempdir()) / "bsf_stall_ring_policy_test"

subprocess.run(["bash", "run_stall_ring_policy_test.sh"], cwd=tests_dir,
               check=True, stdout=subprocess.DEVNULL)


def page(n):
    out = subprocess.run([str(binary), "--emit-page", str(n)],
                         check=True, capture_output=True).stdout
    assert len(out) == 232, f"page {n} was {len(out)} bytes, expected 232"
    return srd.decode_page(out)


pages = [page(n) for n in range(72)]

# --- header agreement
p0 = pages[0]
assert p0["version"] == 4, p0["version"]
assert p0["capacity"] == 510 and p0["count"] == 360, p0
assert p0["pages"] == 72 and p0["entries"] == 5
assert p0["entry_size"] == 40 and p0["sample_period_ms"] == 50
assert p0["frozen"] is True and p0["freeze_reason"] == "alarm", p0
assert all(p["crc_ok"] for p in pages), "every page CRC must verify"
print(f"  ok   72 pages decode, all CRCs verify, {p0['count']} entries held")

# --- the series is contiguous, ordered, and 50 ms apart
series, bad, missing = srd.merge(pages)
assert not bad and not missing, (bad, missing)
assert len(series) == 360, len(series)
steps = {b["uptime_ms"] - a["uptime_ms"] for a, b in zip(series, series[1:])}
assert steps == {50}, f"expected a uniform 50 ms grid, got {sorted(steps)}"
print("  ok   merged series is 360 entries on a uniform 50 ms grid")

# --- restartable: shuffled, duplicated, partial input all merge correctly
shuffled = pages[7:] + pages[:7] + pages[3:5]
again, bad, missing = srd.merge(shuffled)
assert again == series, "page order and duplicates must not change the result"
partial, bad, missing = srd.merge(pages[:10])
assert len(partial) == 50 and not bad and not missing
print("  ok   merge is order-insensitive, duplicate-safe and restartable")

# --- a corrupted page is dropped, never silently accepted
raw = subprocess.run([str(binary), "--emit-page", "0"], check=True,
                     capture_output=True).stdout
corrupt = bytearray(raw)
corrupt[40] ^= 0xFF  # first byte of the first entry
decoded = srd.decode_page(bytes(corrupt))
assert decoded["crc_ok"] is False, "a flipped payload byte must fail the CRC"
kept, bad, _ = srd.merge([decoded] + pages[1:])
assert bad == [0] and len(kept) == 355, (bad, len(kept))
print("  ok   a corrupted page is reported and dropped, not merged")

# --- the transition is actually visible in the data, which is the whole point
frozen_at = p0["freeze_index"]
assert frozen_at == 360, frozen_at
tail = series[-100:]          # the 5.0 s after the modelled onset
head = series[:260]           # healthy run-in
assert all(e["exit_count"] == tail[0]["exit_count"] for e in tail), \
    "publisher exits must be flat across the stall"
assert tail[-1]["producer_heartbeat"] > tail[0]["producer_heartbeat"], \
    "producers must still be advancing across the stall"
assert head[-1]["queue_depth_imu"] == 0 and tail[-1]["queue_depth_imu"] > 50, \
    "the queue must be seen filling"
assert head[0]["pool_avail"][0] == 8 and tail[-1]["pool_avail"][0] == 0, \
    "pool availability must be resolvable per 50 ms sample"
# H1: the detector's inputs must survive the round trip, or a retrieved ring
# still cannot say why the detector stayed quiet.
assert head[0]["subscribed_notify_ok"] == 4096 and head[0]["detector_armed_by_notifies"]
assert "detector_frozen_ms" in tail[-1] and "alarm_block_inert" in tail[-1]
assert not head[0]["pool_avail_truncated"]
print("  ok   E3's discriminators are all resolvable in the decoded series")

# --- a v3 page from a v41 board must still decode (BSF44AD may yet return)
v3 = bytearray(raw)
v3[0] = 3
p_v3 = srd.decode_page(bytes(v3))
assert p_v3["version"] == 3 and len(p_v3["entries_data"]) == p_v3["entries"]
assert "subscribed_notify_ok" not in p_v3["entries_data"][0], \
    "a v3 page must not invent v4 fields"
assert p_v3["entries_data"][0]["uptime_ms"] > 0, "v3 entries still decode"
print("  ok   a v3 page from a v41 board still decodes")

# --- a status snapshot must be refused, not misparsed
try:
    srd.decode_page(bytes([2]) + bytes(231))
except ValueError as exc:
    assert "status snapshot" in str(exc), exc
else:
    raise AssertionError("a version-2 read must not decode as a ring page")
print("  ok   a status snapshot is refused rather than misparsed")

print("stall ring decoder: PASS")

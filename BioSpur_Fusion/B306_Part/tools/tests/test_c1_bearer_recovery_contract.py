#!/usr/bin/env python3
"""Source contract for the C1 bearer-recovery changes (dk-v33, b306-v39)."""
from pathlib import Path

root = Path(__file__).resolve().parents[2]
dk = (root / "host/fusion_master/src/main.c").read_text()
fw = (root / "firmware/src/main.c").read_text()
hdr = (root / "include/biospur_fusion_ble.h").read_text()
cmake = (root / "firmware/CMakeLists.txt").read_text()

# --- C1: the read must resolve before the stack's 30 s ATT transaction timeout
assert "#define STALL_READ_TIMEOUT_MS 25000u" in dk
assert "#define ATT_TRANSACTION_TIMEOUT_MS 30000u" in dk
assert "FUSION_STALL_READ_BEARER_WARNING" in dk, \
    "a 25 s abort must state that the bearer is still on course to be torn down"
assert "cancel_does_not_stop_att_timer" in dk

# --- C2: forced reconnect, per-peer, single idempotent cleanup
assert '"RECONNECT"' in dk
assert "struct reconnect_probe" in dk
assert "reconnect_probe_finish" in dk
# exactly one place clears the probe, and every terminal path routes through it
assert dk.count("reconnect_probe.active = false;") == 1, \
    "cleanup must be idempotent and single-sited"
# Every terminal outcome string exists and reaches cleanup. Some are passed
# through a ternary, so assert the outcome vocabulary plus the number of
# cleanup call sites rather than one literal call form each.
for outcome in ('"timeout"', '"disconnect_error"', '"reconnected"',
                '"reconnected_read_error"'):
    assert outcome in dk, outcome
assert dk.count("reconnect_probe_finish(") >= 4, \
    "each terminal path must route through the single cleanup"
# a second probe is rejected, never queued -- this is what keeps it per-peer
assert "FUSION_RECONNECT_REJECT reason=probe_active" in dk
# only the named peer's connection is touched
assert "bt_conn_disconnect(peer->conn," in dk
# the three instants and the interval that nobody has measured
for field in ("disconnect_ms=", "connect_ms=", "bridge_ms=",
              "down_interval_ms=", "bridge_interval_ms="):
    assert field in dk, field
# after reconnect it must read the status characteristic automatically
assert "FUSION_RECONNECT_VERIFY" in dk
assert "start_stall_read(peer)" in dk

# --- lifecycle hooks are wired, and the disconnect is stamped before the slot
# is released (release_peer clears the name we key on)
assert "reconnect_probe_note_disconnect(peer->name);" in dk
assert "reconnect_probe_note_connected(peer->name);" in dk
assert "reconnect_probe_note_bridge_ready(peer);" in dk
d_note = dk.index("reconnect_probe_note_disconnect(peer->name);")
d_rel = dk.index("release_peer(peer);", dk.index("static void disconnected("))
assert d_note < d_rel, "must timestamp the disconnect before release_peer()"

# --- C3: low_water is a windowed minimum, not since-boot, wire unchanged
assert "atomic_set(&pool_low_water[count]" in fw, \
    "the window must be re-armed atomically at the current level"
assert "minimum available since" in fw or "since\n\t\t * the previous record" in fw
assert "_Static_assert(sizeof(bsf_ble_pool_usage_t) == 140u," in hdr, \
    "kind-8 payload size must not drift"
assert "uint16_t low_water;" in hdr

# --- markers advanced together. The DK marker is C1's own artifact and is
# pinned; the B306 marker only has to be at or past v39, so a later round can
# supersede the image without this contract going stale (E1 shipped v40).
import re
# The DK marker only has to be at or past v33, so a later round can supersede
# C1's image without this contract going stale. N6 shipped dk-v34, which adds
# the raw ring-page dump; every C1 behaviour below is still asserted.
_dk = re.search(r'FUSION_MASTER_MARKER "dk-fusion-imu-relay-v(\d+)"', dk)
assert _dk is not None and int(_dk.group(1)) >= 33, \
    "the C1 master changes must still be carried by the current DK image"
b306 = re.search(r'set\(BSF_FW_MARKER "b306-imu-relay-v(\d+)"\)', cmake)
assert b306 is not None and int(b306.group(1)) >= 39, \
    "the C1 firmware changes must still be carried by the current image"

# --- RESETREAS: retracted, not "fixed". It is not in the stall status struct,
# and the host decoder already emits it from the telemetry record.
stall_struct = hdr[hdr.index("#define BSF_STALL_STATUS_VERSION"):
                   hdr.index("} bsf_stall_status_t;")]
assert "reset_reason" not in stall_struct, \
    "reset_reason is a telemetry field; adding it to FUSION_STALL_READ would not compile"
dec = (root / "tools/fusion_host_binary.py").read_text()
assert "reset_reason" in dec, "the host decoder already surfaces reset_reason"

print("C1 bearer-recovery contract: PASS")

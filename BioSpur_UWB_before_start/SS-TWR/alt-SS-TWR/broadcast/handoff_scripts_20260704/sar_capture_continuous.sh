#!/usr/bin/env bash
# Continuous single-session capture (ARCHITECTURE A: chunk boundaries NEVER touch the radio).
# Replaces roto_sar_overnight.sh's per-chunk kill/relaunch (which reset the controller + restarted
# listeners every 15 min -> anchors dropped responder mode -> 22/36 chunks lost).
#
# What changed vs the old driver:
#   * PREFLIGHT EXACTLY ONCE at session start (force anchor responders) + assert 8/8 respond (fail
#     fast in ~2-3 min instead of a 50-min post-mortem).
#   * recv (tag driver) + all 6 listeners launched ONCE for the whole session. No per-chunk restart,
#     no --controller-reset per chunk, no listener re-flash, no anchor mode commands at boundaries.
#   * "Chunks" are wall-clock windows only: boundaries written to chunk_manifest.json; analysis
#     splits by timestamp (recv/listener CSVs carry host_epoch_s).
#   * Live health to STDOUT every 10 s (ge7/ge8 + per-listener CIR frame counts) so you can see
#     session health immediately.
#   * Periodic `sync` every 10 s -> bounds power-loss data loss to ~10 s without editing the
#     3920-line recv (listener already flushes per line; sync forces OS buffers to disk).
#   * tag_roster.json written from the runtime CFG (single source of truth for tag_id<->name).
#
# NOT implemented this run (deliberately, per spec): the ge7<threshold-for-60s targeted single-anchor
# recovery WATCHDOG. It is the replacement recovery mechanism for the old chunk-restart and is a
# PREREQUISITE before the next UNATTENDED overnight (9 h). A 50-min attended dry run does not need it
# (you are present; abort+restart is cheap). See OVERNIGHT-PREREQ note at the end of this file.
#
# The radio-touching commands (restore_anchors, recv launch, listener launch) are COPIED VERBATIM
# from the working roto_sar_overnight.sh; only the orchestration (once, not per-chunk) changed.
#
# Usage:
#   verification dry run (single tag, 3x15min):  bash sar_capture_continuous.sh
#   custom:  TARGETS=BSCCF4 CHUNK_S=900 N_CHUNKS=3 TRHZ=5 bash sar_capture_continuous.sh
# Touch <BASE>/STOP to halt early.
set -u
cd "$(dirname "$0")/../../../.." 2>/dev/null || true   # -> repo root (broadcast is .../SS-TWR/alt-SS-TWR/broadcast)
ROOT=SS-TWR/alt-SS-TWR/broadcast
TS=$(date +%Y%m%d_%H%M%S)
TARGETS="${TARGETS:-BSCCF4}"       # SINGLE-TAG EXCLUSIVE for the dry run (one wand tag @ 5 Hz)
TRHZ="${TRHZ:-5}"
CHUNK_S="${CHUNK_S:-900}"          # nominal chunk window (s) — a MARKER only, not a radio event
N_CHUNKS="${N_CHUNKS:-3}"
MARGIN_S=90
SESSION_S=$(( CHUNK_S * N_CHUNKS ))
BASE="$ROOT/logs/verify_dryrun_${TS}"
mkdir -p "$BASE"
LOG="$BASE/driver.log"

MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
MA=/dev/serial/by-id/usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00
CIRHEX="$ROOT/build-uwb-listener-poll-diag-cirprobe_gen_20260704/zephyr/zephyr.hex"
declare -A SN=( [LCCF4]=760184784 [L9336]=760186071 [L955A]=760186081 [LB]=760184545 [LE]=760184767 [LF]=760184964 )
LISTENERS="LCCF4 L9336 L955A LB LE LF"
RESP_TARGETS=BS9336,BS955A,BSCCF4   # the 3 responder-capable anchors (as in the working driver)

say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
free_ports(){ for pid in $(pgrep -f "capture_uwb_poll_listener|run_recv_tdma_capture" 2>/dev/null); do kill "$pid" 2>/dev/null; done; sleep 3; }

say "CONTINUOUS CAPTURE (arch A) base=$BASE targets=$TARGETS tr=${TRHZ}Hz session=${SESSION_S}s (${N_CHUNKS}x${CHUNK_S}s markers)"
free_ports

# ---------------- PREFLIGHT ONCE: force responders, then ASSERT 8/8 ----------------
say "PREFLIGHT (once): force anchor responders + smoke test"
timeout 140 python3 "$ROOT/scripts/restore_and_smoke_test_anchor_responder.py" \
  --anchor-port "$MA" --tag-port "$MT" --targets "$RESP_TARGETS" --duration 15 --tr-hz 10 \
  --out-dir "$BASE/preflight" > "$BASE/preflight.log" 2>&1
free_ports
# assert: the smoke test must show healthy anchor coverage before we commit to a long session
PF_GE7=$(grep -oE "ge7=[0-9]+(\.[0-9]+)?%" "$BASE/preflight.log" | tail -1 | grep -oE "[0-9]+" | head -1)
PF_GE8=$(grep -oE "ge8=[0-9]+(\.[0-9]+)?%" "$BASE/preflight.log" | tail -1 | grep -oE "[0-9]+" | head -1)
PF_GE7=${PF_GE7:-0}; PF_GE8=${PF_GE8:-0}
say "PREFLIGHT result: ge7=${PF_GE7}% ge8=${PF_GE8}% (assert ge7>=90 && ge8>=80)"
if [ "$PF_GE7" -lt 90 ] || [ "$PF_GE8" -lt 80 ]; then
  say "PREFLIGHT ASSERT FAILED (anchors not all responding). Aborting BEFORE the long session."
  say "  -> check anchor power/BLE, re-run. (This is the fail-fast you asked for: known in ~3 min.)"
  exit 2
fi
say "PREFLIGHT OK -> anchors responding. Committing to continuous session (NO further radio touches at boundaries)."

# ---------------- LAUNCH recv ONCE (drives the tag(s) for the whole session) ----------------
mkdir -p "$BASE/recv_parent"
python3 "$ROOT/scripts/run_recv_tdma_capture.py" --port "$MT" \
  --targets "$TARGETS" --duration $(( SESSION_S + MARGIN_S )) --tr-hz "$TRHZ" \
  --controller-reset-snr 1050070698 --tag-cir compact --skip-anchor-preflight \
  --legacy-skip-link-ready-wait --no-silence-non-target-tags \
  --out-dir "$BASE/recv" --out-dir-exact > "$BASE/recv.log" 2>&1 &
RPID=$!
live=0
for i in $(seq 1 24); do
  sleep 5
  grep -qE "ge7=[1-9][0-9]?%|ge7=100%" "$BASE/recv.log" 2>/dev/null && { live=1; say "ranging live ~$((i*5))s"; break; }
  kill -0 $RPID 2>/dev/null || { say "recv died during setup -> abort"; free_ports; exit 3; }
done
[ "$live" -ne 1 ] && { say "no ranging came up -> abort"; kill $RPID 2>/dev/null; free_ports; exit 3; }

# single source of truth for tag_id<->name, from the runtime CFG the master just emitted
python3 "$ROOT/scripts/tag_roster.py" "$BASE" --write > /dev/null 2>&1 && say "wrote tag_roster.json: $(python3 -c "import json;print(json.load(open('$BASE/tag_roster.json'))['by_id'])" 2>/dev/null)"

# ---------------- LAUNCH 6 listeners ONCE for the whole session ----------------
declare -A LPID
for L in $LISTENERS; do
  python3 "$ROOT/scripts/capture_uwb_poll_listener.py" \
    --port /dev/serial/by-id/usb-SEGGER_J-Link_000${SN[$L]}-if00 --baud 460800 \
    --duration "$SESSION_S" --out-dir "$BASE/$L" > "$BASE/$L.log" 2>&1 &
  LPID[$L]=$!
done
SESSION_START=$(date +%s)
say "listeners up; SESSION_START=$SESSION_START. Chunk markers (wall-clock, radio untouched):"
python3 - "$BASE" "$SESSION_START" "$CHUNK_S" "$N_CHUNKS" <<'PY'
import json,sys
base,start,cs,n=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4])
man={"session_start_epoch":start,"chunk_seconds":cs,"n_chunks":n,
     "chunks":[{"chunk":k+1,"start_epoch":start+k*cs,"end_epoch":start+(k+1)*cs} for k in range(n)],
     "note":"chunks are wall-clock windows only; radio ran continuously. Mark the WALK window "
            "start/stop (chunk 2) to +-2s and record here as walk_start_epoch/walk_stop_epoch."}
json.dump(man,open(f"{base}/chunk_manifest.json","w"),indent=2)
print("  "+json.dumps(man["chunks"]))
PY

# ---------------- MONITOR: live health every 10s + periodic sync. NO radio activity. ----------------
say "=== LIVE HEALTH (every 10s) — ge7/ge8 + per-listener CIR frame counts ==="
while kill -0 $RPID 2>/dev/null; do
  [ -f "$BASE/STOP" ] && { say "STOP file -> halting session"; kill $RPID "${LPID[@]}" 2>/dev/null; break; }
  now=$(date +%s); el=$((now-SESSION_START)); mk=$(( el/CHUNK_S + 1 ))
  GE7=$(grep -oE "ge7=[0-9]+%" "$BASE/recv.log" 2>/dev/null | tail -1)
  GE8=$(grep -oE "ge8=[0-9]+%" "$BASE/recv.log" 2>/dev/null | tail -1)
  H="[+${el}s mark${mk}] ${GE7:-ge7=?} ${GE8:-ge8=?} |"
  for L in $LISTENERS; do
    D=$(ls -d "$BASE/$L"/listener_* 2>/dev/null | head -1)
    n=$(($(wc -l < "$D/lcirm.csv" 2>/dev/null || echo 1)-1)); [ "$n" -lt 0 ] && n=0
    H="$H $L=$n"
  done
  echo "$H" | tee -a "$LOG"
  sync                       # flush OS buffers to disk (durability ~10s; no radio touch)
  sleep 10
done
wait $RPID 2>/dev/null; wait "${LPID[@]}" 2>/dev/null
sync
say "SESSION DONE base=$BASE. Next: paste $BASE back for acceptance (zero responder drops, preflight-once),"
say "  then verify_rate.py + verify_motion.py. Record walk_start/stop_epoch in chunk_manifest.json."

# ============================ OVERNIGHT-PREREQ (do before the next unattended 9h run) ============================
# Implement the decoupled health watchdog (spec item 3): a background loop that, INDEPENDENT of chunk
# markers, watches ge7 from recv.log; if ge7<THRESH sustained >60s, run a TARGETED single-anchor
# responder restore on the ANCHOR port only (MA) — never the tag port (MT, owned by recv) — and log a
# WATCHDOG_RECOVER event with the anchor id + before/after ge7. Concurrency caveat to resolve on the
# bench: restore_and_smoke_test uses BOTH MT and MA; a watchdog that fires mid-session must use an
# anchor-only recovery path (MA only) so it does not collide with the running recv on MT. Until that
# path is validated live, do NOT run unattended overnight with this driver.

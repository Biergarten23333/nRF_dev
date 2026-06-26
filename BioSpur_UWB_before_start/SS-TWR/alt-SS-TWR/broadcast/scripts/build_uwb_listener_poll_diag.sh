#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="build-uwb-listener-poll-diag-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
WEST_BIN="${WEST_BIN:-west}"
HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"
export PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"

listener_id="${APP_LISTENER_ID:-255}"
near_anchor_id="${APP_LISTENER_NEAR_ANCHOR_ID:-255}"
cir_capture="${APP_LISTENER_CIR_CAPTURE_ENABLE:-0}"
cir_period="${APP_LISTENER_CIR_SAMPLE_PERIOD:-10}"
post_cir_idle_ms="${APP_LISTENER_POST_CIR_IDLE_MS:-12}"

(cd "$NCS_ROOT" && "$WEST_BIN" build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s "$repo_root/UWB_listener" \
  -d "$repo_root/$build_dir" \
  --pristine=always \
  -- \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DAPP_LISTENER_ID="$listener_id" \
  -DAPP_LISTENER_NEAR_ANCHOR_ID="$near_anchor_id" \
  -DAPP_LISTENER_CIR_CAPTURE_ENABLE="$cir_capture" \
  -DAPP_LISTENER_CIR_SAMPLE_PERIOD="$cir_period" \
  -DAPP_LISTENER_POST_CIR_IDLE_MS="$post_cir_idle_ms")

python3 "$repo_root/scripts/write_build_source.py" \
  --build-dir "$repo_root/$build_dir" \
  --source "scripts/build_uwb_listener_poll_diag.sh" \
  --command "$0 $*"

echo "Built generic co-located listener: $build_dir/merged.hex"

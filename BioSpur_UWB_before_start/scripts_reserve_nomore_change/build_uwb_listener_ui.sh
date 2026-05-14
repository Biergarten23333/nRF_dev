#!/usr/bin/env bash
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
build_dir="build-uwb-listener-ui-${stamp}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
conf="$repo_root/configs/uwb_listener_ui.conf"

west build \
  -b decawave_dwm1001_dev \
  -s "$repo_root/UWB_listener" \
  -d "$repo_root/$build_dir" \
  --pristine=always \
  -- \
  -DUWB_listener_EXTRA_CONF_FILE="$conf" \
  -DAPP_LISTENER_UF_TRACE_ENABLE=1 \
  -DAPP_LISTENER_UL_TRACE_ENABLE=1 \
  -DAPP_LISTENER_STATUS_PRINT_ENABLE=0 \
  -DAPP_LISTENER_DEBUG_PRINT_ENABLE=0

python3 "$repo_root/scripts/write_build_source.py" \
  --build-dir "$repo_root/$build_dir" \
  --source "scripts/build_uwb_listener_ui.sh" \
  --command "$0 $*"

echo "Built listener UI: $build_dir/merged.hex"

#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: flash_anchor_auto.sh <build_dir_or_hex_path> [snr]

Examples:
  scripts/flash_anchor_auto.sh build-anchor-A-master
  scripts/flash_anchor_auto.sh build-anchor-H-tag/zephyr/zephyr.hex
  scripts/flash_anchor_auto.sh build-anchor-E-worker 760185904
EOF
  exit 1
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  usage
fi

input_path="$1"
override_snr="${2:-}"

case "$input_path" in
  *.hex)
    hex_path="$(realpath "$input_path")"
    build_root="$(dirname "$(dirname "$hex_path")")"
    ;;
  *)
    build_root="$(realpath "$input_path")"
    # Support both non-sysbuild builds (zephyr/*) and sysbuild outputs
    # where merged.hex may live at the build root and the app image is under anchor/zephyr/*.
    if [ -f "$build_root/merged.hex" ]; then
      # Sysbuild top-level merged image (MCUboot + app). Preferred for direct flashing.
      hex_path="$build_root/merged.hex"
    elif [ -f "$build_root/anchor/zephyr/zephyr.hex" ]; then
      hex_path="$build_root/anchor/zephyr/zephyr.hex"
    elif [ -f "$build_root/anchor/zephyr/merged.hex" ]; then
      hex_path="$build_root/anchor/zephyr/merged.hex"
    elif [ -f "$build_root/zephyr/zephyr.hex" ]; then
      hex_path="$build_root/zephyr/zephyr.hex"
    elif [ -f "$build_root/zephyr/merged.hex" ]; then
      hex_path="$build_root/zephyr/merged.hex"
    else
      echo "Could not find a flashable hex under: $build_root" >&2
      echo "Tried: merged.hex, anchor/zephyr/{zephyr,merged}.hex, zephyr/{zephyr,merged}.hex" >&2
      exit 1
    fi
    ;;
esac

infer_role() {
  local path base
  path="$1"
  base="$(basename "$path")"

  if [[ "$path" =~ build-anchor-([A-Ha-h]) ]]; then
    echo "${BASH_REMATCH[1]^^}"
    return 0
  fi

  if [[ "$base" =~ ^build-anchor-([A-Ha-h])$ ]]; then
    echo "${BASH_REMATCH[1]^^}"
    return 0
  fi

  if [[ "$base" =~ ^build-anchor-([A-Ha-h])-(master|worker|tag|matrix|master-full|safe|fast)$ ]]; then
    echo "${BASH_REMATCH[1]^^}"
    return 0
  fi

  return 1
}

infer_family() {
  local path base
  path="$1"
  base="$(basename "$path")"

  if [[ "$base" =~ ^build-anchor-[A-Ha-h]-(master|worker|tag|matrix|master-full|safe|fast)$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  if [[ "$path" =~ build-anchor-[A-Ha-h]-(master|worker|tag|matrix|master-full|safe|fast) ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  echo "unknown"
  return 0
}

if [ -n "$override_snr" ]; then
  snr="$override_snr"
else
  role="$(infer_role "$build_root" || true)"
  case "${role:-}" in
    A) snr=760184781 ;;
    B) snr=760185876 ;;
    C) snr=760185878 ;;
    D) snr=760184974 ;;
    E) snr=760185904 ;;
    F) snr=760186124 ;;
    G) snr=760185889 ;;
    H) snr=760184753 ;;
    *)
      echo "Could not infer anchor role from path: $input_path" >&2
      echo "Pass the J-Link serial as the second argument." >&2
      exit 1
      ;;
  esac
fi

family="$(infer_family "$build_root")"

echo "[flash-anchor-auto] role=${role:-override} family=$family snr=$snr hex=$hex_path"
"$(dirname "$0")/reset_then_flash.sh" "$snr" "$hex_path"

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
state_path="${repo_root}/data/anchor_flash_state.json"
python3 - "$state_path" "${role:-}" "$family" "$snr" "$hex_path" "$build_root" <<'PY'
import json
import os
import sys
from datetime import datetime
from pathlib import Path

state_path = Path(sys.argv[1])
role = sys.argv[2]
family = sys.argv[3]
snr = sys.argv[4]
hex_path = sys.argv[5]
build_root = sys.argv[6]

state_path.parent.mkdir(parents=True, exist_ok=True)
state = {"anchors": {}, "history": []}
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {"anchors": {}, "history": []}

entry = {
    "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "role": role if role else None,
    "family": family,
    "snr": snr,
    "hex": os.path.realpath(hex_path),
    "build_root": os.path.realpath(build_root),
}
if role:
    state.setdefault("anchors", {})[role] = entry
state.setdefault("history", []).append(entry)
if len(state["history"]) > 200:
    state["history"] = state["history"][-200:]

state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
print(f"[flash-anchor-auto] state-updated {state_path}")
PY

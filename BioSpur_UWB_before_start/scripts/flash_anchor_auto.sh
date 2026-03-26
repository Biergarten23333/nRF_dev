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
    if [ -f "$build_root/zephyr/zephyr.hex" ]; then
      hex_path="$build_root/zephyr/zephyr.hex"
    elif [ -f "$build_root/zephyr/merged.hex" ]; then
      hex_path="$build_root/zephyr/merged.hex"
    else
      echo "Could not find zephyr.hex or merged.hex under: $build_root" >&2
      exit 1
    fi
    ;;
esac

infer_role() {
  local path base
  path="$1"
  base="$(basename "$path")"

  if [[ "$path" =~ build-anchor-([A-H]) ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  if [[ "$base" =~ ^build-anchor-([A-H])$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  if [[ "$base" =~ ^build-anchor-([A-H])-(master|worker|tag|matrix|master-full|safe|fast)$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  return 1
}

if [ -n "$override_snr" ]; then
  snr="$override_snr"
else
  role="$(infer_role "$build_root" || true)"
  case "${role:-}" in
    A) snr=760186071 ;;
    B) snr=760185876 ;;
    C) snr=760185878 ;;
    D) snr=760186081 ;;
    E) snr=760185904 ;;
    F) snr=760186124 ;;
    G) snr=760185889 ;;
    H) snr=760186121 ;;
    *)
      echo "Could not infer anchor role from path: $input_path" >&2
      echo "Pass the J-Link serial as the second argument." >&2
      exit 1
      ;;
  esac
fi

echo "[flash-anchor-auto] role=${role:-override} snr=$snr hex=$hex_path"
exec "$(dirname "$0")/reset_then_flash.sh" "$snr" "$hex_path"

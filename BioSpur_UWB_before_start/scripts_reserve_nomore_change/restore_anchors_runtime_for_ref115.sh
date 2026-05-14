#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: restore_anchors_runtime_for_ref115.sh [family] [--dry-run]

family:
  tag   (default, recommended for Ref115 calibration capture)
  safe
  fast
  worker

Examples:
  scripts/restore_anchors_runtime_for_ref115.sh
  scripts/restore_anchors_runtime_for_ref115.sh safe
  scripts/restore_anchors_runtime_for_ref115.sh tag --dry-run
EOF
  exit 1
}

family="tag"
dry_run=0

for arg in "$@"; do
  case "$arg" in
    tag|safe|fast|worker)
      family="$arg"
      ;;
    --dry-run)
      dry_run=1
      ;;
    *)
      usage
      ;;
  esac
done

anchors=(A B C D E F G H)
declare -A anchor_snr=(
  [A]=760184781
  [B]=760185876
  [C]=760185878
  [D]=760186081
  [E]=760185904
  [F]=760186124
  [G]=760185889
  [H]=760186121
)

resolve_build_dir() {
  local anchor="$1"
  local fam="$2"
  local upper="build-anchor-${anchor}-${fam}"
  local lower="build-anchor-${anchor,,}-${fam}"
  if [ -d "$upper" ]; then
    echo "$upper"
    return 0
  fi
  if [ -d "$lower" ]; then
    echo "$lower"
    return 0
  fi
  return 1
}

for anchor in "${anchors[@]}"; do
  build_dir="$(resolve_build_dir "$anchor" "$family" || true)"
  if [ -z "$build_dir" ]; then
    echo "[restore-anchors] missing build dir: build-anchor-${anchor}-${family} (or lowercase variant)" >&2
    echo "Build it first, then rerun." >&2
    exit 2
  fi
  if [ ! -d "$build_dir" ]; then
    echo "[restore-anchors] missing build dir: $build_dir" >&2
    echo "Build it first, then rerun." >&2
    exit 2
  fi
  if [ ! -f "$build_dir/zephyr/zephyr.hex" ] && [ ! -f "$build_dir/zephyr/merged.hex" ]; then
    echo "[restore-anchors] missing image in: $build_dir" >&2
    exit 3
  fi
done

echo "[restore-anchors] target family=${family} dry_run=${dry_run}"
echo "[restore-anchors] expected responder signature: SS-TWR responder ready ... allow_tag_polls=1"
echo "[restore-anchors] expected to avoid before Ref115 capture: Anchor master ready ... / allow_tag_polls=0"

for anchor in "${anchors[@]}"; do
  build_dir="$(resolve_build_dir "$anchor" "$family")"
  cmd=(scripts/flash_anchor_auto.sh "$build_dir" "${anchor_snr[$anchor]}")
  if [ "$dry_run" -eq 1 ]; then
    echo "[dry-run] ${cmd[*]}"
  else
    echo "[restore-anchors] flashing $anchor via $build_dir"
    "${cmd[@]}"
  fi
done

echo "[restore-anchors] done"

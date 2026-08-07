#!/usr/bin/env bash
#
# Apply / verify / revert the v43 BT-host stage-instrumentation patch.
#
# WHY THIS SCRIPT EXISTS  (brief section 4)
# -----------------------------------------
# ~/ncs/v2.8.0 is a SHARED SDK install that lives outside this repository.
# Editing subsys/bluetooth/host/conn.c in place would:
#   * affect every other project built against that SDK,
#   * not be captured by this repository's git, and
#   * be silently lost on an SDK reinstall or update.
#
# That is the same provenance failure that left v33-v41 unrecoverable, one level
# deeper, and it would make a deployed image unreproducible by construction. So
# the modification lives in the repository as a patch file, and the build refuses
# to proceed unless the SDK matches one of two hashes we know exactly.
#
# THREE STATES, AND ONLY THREE
#   current == PRISTINE_SHA  -> not applied  (apply: allowed; verify: fails)
#   current == PATCHED_SHA   -> applied      (apply: no-op;  verify: passes)
#   anything else            -> REFUSE. The SDK moved under us, or someone
#                               hand-edited it. Never guess; never force.
#
# Usage:  host_patch.sh {apply|verify|revert|status}
set -euo pipefail

SDK_ZEPHYR="${ZEPHYR_BASE:-/home/zekaixiao/ncs/v2.8.0/zephyr}"
TARGET="${SDK_ZEPHYR}/subsys/bluetooth/host/conn.c"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${HERE}/ncs-v2.8.0-bt-conn-stage-trace.patch"

# Captured up front: the file loops below use `set -- $spec` to split fields,
# which overwrites the positional parameters and would otherwise eat the
# subcommand. That cost one confusing "usage:" failure.
CMD="${1:-status}"

# v44 patches THREE host files, not one. Each is gated independently: the
# target set is only "pristine" or "patched" if EVERY file matches, so a
# half-applied tree -- which is what an interrupted patch leaves -- is neither,
# and the script refuses rather than guessing.
FILES=(
  "subsys/bluetooth/host/conn.c     b315e62fd5c63ef5dffc7d54d6d5313dfbd727dba0daa5344294bba962451bb5 fb39fbd26e31eb91b64d39a22f5647785e4e5c41ad69bf2965358037ac3b7794"
  "subsys/bluetooth/host/hci_core.c 329107858d42b535477fb7bd9bc12aef2f66348baa125773813e73385a80c193 00d03a1d44d5febab0836d6ceb4dde6029a65dc685930d2216c15737eab0d9b4"
  "subsys/bluetooth/host/att.c      2f6b969a4fd6ece75d4482e61b7260a495d812fa48353cfed7b1cd5b941fdb64 d2bead08f070ff559ea4d202f560d746f3819d2ea2bee965b37dda2b5899bccc"
)
PATCH_SHA=5d709d5d72e34e30aa2be0509d3b8632777a144916c43349656126c083f13b7c

die() { echo "HOST_PATCH_FAIL $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

for spec in "${FILES[@]}"; do
  set -- $spec
  [ -f "$SDK_ZEPHYR/$1" ] || die "target_missing path=$SDK_ZEPHYR/$1"
done
[ -f "$PATCH" ]  || die "patch_missing path=$PATCH"

got_patch="$(sha "$PATCH")"
[ "$got_patch" = "$PATCH_SHA" ] || \
  die "patch_hash_mismatch expected=$PATCH_SHA got=$got_patch"

state() {
  local n_pristine=0 n_patched=0 n=0 s
  for spec in "${FILES[@]}"; do
    set -- $spec
    n=$((n+1))
    s="$(sha "$SDK_ZEPHYR/$1")"
    [ "$s" = "$2" ] && n_pristine=$((n_pristine+1))
    [ "$s" = "$3" ] && n_patched=$((n_patched+1))
  done
  if   [ "$n_pristine" = "$n" ]; then echo pristine
  elif [ "$n_patched"  = "$n" ]; then echo patched
  else echo "unknown:pristine=$n_pristine/$n,patched=$n_patched/$n"
  fi
}

case "$CMD" in
  status)
    echo "HOST_PATCH_STATUS state=$(state) files=${#FILES[@]}"
    ;;

  apply)
    case "$(state)" in
      patched)
        echo "HOST_PATCH_APPLY state=already_applied files=${#FILES[@]}"
        ;;
      pristine)
        patch -s -p1 -d "$SDK_ZEPHYR" < "$PATCH" || die "patch_apply_failed"
        [ "$(state)" = "patched" ] || die "post_apply_state=$(state)"
        echo "HOST_PATCH_APPLY state=applied files=${#FILES[@]}"
        ;;
      *)
        die "refuse_apply state=$(state) reason=target_is_neither_pristine_nor_patched"
        ;;
    esac
    ;;

  verify)
    s="$(state)"
    [ "$s" = "patched" ] || die "verify state=$s expected=patched"
    echo "HOST_PATCH_VERIFY ok files=${#FILES[@]}"
    ;;

  revert)
    case "$(state)" in
      pristine) echo "HOST_PATCH_REVERT state=already_pristine" ;;
      patched)
        patch -s -R -p1 -d "$SDK_ZEPHYR" < "$PATCH" || die "revert_failed"
        [ "$(state)" = "pristine" ] || die "post_revert_state=$(state)"
        echo "HOST_PATCH_REVERT state=reverted files=${#FILES[@]}"
        ;;
      *) die "refuse_revert state=$(state)" ;;
    esac
    ;;

  *) die "usage: host_patch.sh {apply|verify|revert|status}" ;;
esac

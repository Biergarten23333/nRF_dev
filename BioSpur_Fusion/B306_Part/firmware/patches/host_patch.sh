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

PRISTINE_SHA=b315e62fd5c63ef5dffc7d54d6d5313dfbd727dba0daa5344294bba962451bb5
PATCHED_SHA=2166af0c32756d475ded8997239a05a9ab23bad7c8e68c5cafc98d7253dfdbbe
PATCH_SHA=d5ae1c27076d9d94c6f5349ac1aee6452b9ae2a70d7d19d38c7afe057ff56eb9

die() { echo "HOST_PATCH_FAIL $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

[ -f "$TARGET" ] || die "target_missing path=$TARGET"
[ -f "$PATCH" ]  || die "patch_missing path=$PATCH"

got_patch="$(sha "$PATCH")"
[ "$got_patch" = "$PATCH_SHA" ] || \
  die "patch_hash_mismatch expected=$PATCH_SHA got=$got_patch"

state() {
  local s; s="$(sha "$TARGET")"
  case "$s" in
    "$PRISTINE_SHA") echo pristine ;;
    "$PATCHED_SHA")  echo patched ;;
    *)               echo "unknown:$s" ;;
  esac
}

case "${1:-status}" in
  status)
    echo "HOST_PATCH_STATUS state=$(state) target=$TARGET"
    ;;

  apply)
    case "$(state)" in
      patched)
        echo "HOST_PATCH_APPLY state=already_applied sha=$PATCHED_SHA"
        ;;
      pristine)
        patch -s -p1 -d "$SDK_ZEPHYR" < "$PATCH" || die "patch_apply_failed"
        got="$(sha "$TARGET")"
        [ "$got" = "$PATCHED_SHA" ] || \
          die "post_apply_hash_mismatch expected=$PATCHED_SHA got=$got"
        echo "HOST_PATCH_APPLY state=applied sha=$PATCHED_SHA"
        ;;
      *)
        die "refuse_apply state=$(state) reason=target_is_neither_pristine_nor_patched"
        ;;
    esac
    ;;

  verify)
    s="$(state)"
    [ "$s" = "patched" ] || die "verify state=$s expected=patched"
    echo "HOST_PATCH_VERIFY ok sha=$PATCHED_SHA"
    ;;

  revert)
    case "$(state)" in
      pristine) echo "HOST_PATCH_REVERT state=already_pristine" ;;
      patched)
        patch -s -R -p1 -d "$SDK_ZEPHYR" < "$PATCH" || die "revert_failed"
        got="$(sha "$TARGET")"
        [ "$got" = "$PRISTINE_SHA" ] || \
          die "post_revert_hash_mismatch expected=$PRISTINE_SHA got=$got"
        echo "HOST_PATCH_REVERT state=reverted sha=$PRISTINE_SHA"
        ;;
      *) die "refuse_revert state=$(state)" ;;
    esac
    ;;

  *) die "usage: host_patch.sh {apply|verify|revert|status}" ;;
esac

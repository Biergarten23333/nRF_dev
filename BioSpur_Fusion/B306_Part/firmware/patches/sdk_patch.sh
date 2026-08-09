#!/usr/bin/env bash
#
# Apply / verify / revert the v45 SDK instrumentation patch set.
#
# WHY THIS SCRIPT EXISTS  (unchanged from host_patch.sh, and still true)
# ----------------------------------------------------------------------
# ~/ncs/v2.8.0 is a SHARED SDK install that lives outside this repository.
# Editing it in place would affect every other project built against that SDK,
# would not be captured by this repository's git, and would be silently lost on
# an SDK reinstall. That is the provenance failure that left v33-v41
# unrecoverable, one level deeper, and it would make a deployed image
# unreproducible by construction. So the modification lives in the repository as
# a patch file, and the build refuses to proceed unless the SDK matches hashes
# we know exactly.
#
# WHAT IS NEW IN v45
#   * R4 (2026-08-09): SEVEN files. adv.c joins for the bt_conn_set_state site
#     ids, and zephyr/include/zephyr/bluetooth/conn.h carries
#     BSF_V45_SDK_PATCH_VERSION so an unpatched SDK is a COMPILE FAILURE in the
#     application rather than a silent fallback to no-op marks. The old
#     __has_include no-op fallbacks are gone: the SDK units now #error.
#     This patch supersedes ncs-v2.8.0-bsf-v45-instrumentation.patch, which is
#     kept for provenance and no longer applied.
#
#   * FIVE files, not three.
#   * TWO roots: $ZEPHYR_BASE and its sibling nrf/. The controller's HCI driver
#     -- the MPSL Work inlet, and the one place a stalled receive path is
#     visible at all -- lives in nrf/, so the single-root assumption had to go.
#   * This patch SUPERSEDES ncs-v2.8.0-bt-conn-stage-trace.patch: it is
#     pristine -> v45 and CARRIES the v43/v44 marks unchanged inside it. The old
#     patch file is kept in the repository for provenance and is no longer
#     applied. v43/v44 enums, storage and decoder support are untouched, as the
#     prohibition list requires.
#
# THREE STATES, AND ONLY THREE
#   every file == PRISTINE_SHA  -> not applied  (apply: allowed; verify: fails)
#   every file == PATCHED_SHA   -> applied      (apply: no-op;  verify: passes)
#   anything else               -> REFUSE. The SDK moved under us, someone
#                                  hand-edited it, or an earlier patch is still
#                                  applied. Never guess; never force.
#
# Section 14 requires apply / verify / revert / re-apply to all PASS in
# CI-style scripting. `selftest` does exactly that round trip and is what the
# build gate runs.
#
# Usage:  sdk_patch.sh {apply|verify|revert|status|selftest}
set -euo pipefail

SDK_ZEPHYR="${ZEPHYR_BASE:-/home/zekaixiao/ncs/v2.8.0/zephyr}"
SDK_ROOT="$(cd "${SDK_ZEPHYR}/.." && pwd)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${HERE}/ncs-v2.8.0-bsf-v46-nonblocking-hci.patch"

# Captured up front: the loops below use `set -- $spec`, which overwrites the
# positional parameters and would otherwise eat the subcommand. That cost one
# confusing "usage:" failure the last time round.
CMD="${1:-status}"

# path-relative-to-SDK_ROOT   pristine_sha   patched_sha
FILES=(
  "zephyr/subsys/bluetooth/host/conn.c           b315e62fd5c63ef5dffc7d54d6d5313dfbd727dba0daa5344294bba962451bb5 3c053a47d489f9ddd53dd2c4178811c5ad35d48978690c1352d9dc2631afa6e4"
  "zephyr/subsys/bluetooth/host/hci_core.c       329107858d42b535477fb7bd9bc12aef2f66348baa125773813e73385a80c193 76468edd61d6a26c9a57f23aafce261c473093ff643b0585a656da48b2d8abac"
  "zephyr/subsys/bluetooth/host/att.c            2f6b969a4fd6ece75d4482e61b7260a495d812fa48353cfed7b1cd5b941fdb64 d2bead08f070ff559ea4d202f560d746f3819d2ea2bee965b37dda2b5899bccc"
  "zephyr/lib/net_buf/buf.c                      b4d233169d6453e482974f810728dcfe1634e000cc32836062a9d6aecea56fe0 94759ac1b2551eba4555f5fb8a9104748d0b53dc67d4ca544ee8b9b82dd7653d"
  "nrf/subsys/bluetooth/controller/hci_driver.c  8d4ca5840769cd3ce39d5fbc8a55bb819db379ced323944a25fae6fce8453c60 4f1c46914c18a801608726fbd1eb92dad510099273f120c28291ab3de3c882dd"
  "zephyr/subsys/bluetooth/host/adv.c            1e3ad57ec125a2281c5fdab12bcb8cf7c9f6eb830ebc8e2c74938919c25541f8 2e8ca52676dc0cd9685da2b420304cbca1c920ee82ef27d59c5fd2c33ca03657"
  "zephyr/include/zephyr/bluetooth/conn.h        dd4babab1b8b92eabee130da4e59837719d5f2b6c5f599483c47e525cabece4e 4b21911bede8377df6612e02295e7d7384e73bd599561c2d3051d2b5a952ebdc"
  "zephyr/subsys/bluetooth/host/buf.c            fcefd062fa7696e32a8cfb394c63b845415f600830dcc3dcff072d996fde12a0 7161ce93d2ae446425a73ba64e291464b6ef172fee240ca9f1b5f14df3839028"
  "zephyr/include/zephyr/bluetooth/buf.h         e62cf489c0698c0ca8f63e0551c766fba5a92930f96618248e68343709385db7 fe53ea94bc8d40570dff35f9de5d1b857a69aada95c2a0deeb7f79f238c8378d"
)
PATCH_SHA=ff35373425c7eee54d1f10226bd65ed8042dfb6073b8ebd8cacd53cf5f39921b

die() { echo "SDK_PATCH_FAIL $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

for spec in "${FILES[@]}"; do
  set -- $spec
  [ -f "$SDK_ROOT/$1" ] || die "target_missing path=$SDK_ROOT/$1"
done
[ -f "$PATCH" ] || die "patch_missing path=$PATCH"

got_patch="$(sha "$PATCH")"
[ "$got_patch" = "$PATCH_SHA" ] || \
  die "patch_hash_mismatch expected=$PATCH_SHA got=$got_patch"

state() {
  local n_pristine=0 n_patched=0 n=0 s
  for spec in "${FILES[@]}"; do
    set -- $spec
    n=$((n+1))
    s="$(sha "$SDK_ROOT/$1")"
    [ "$s" = "$2" ] && n_pristine=$((n_pristine+1))
    [ "$s" = "$3" ] && n_patched=$((n_patched+1))
  done
  if   [ "$n_pristine" = "$n" ]; then echo pristine
  elif [ "$n_patched"  = "$n" ]; then echo patched
  else echo "unknown:pristine=$n_pristine/$n,patched=$n_patched/$n"
  fi
}

do_apply() {
  case "$(state)" in
    patched)  echo "SDK_PATCH_APPLY state=already_applied files=${#FILES[@]}" ;;
    pristine)
      patch -s -p1 -d "$SDK_ROOT" < "$PATCH" || die "patch_apply_failed"
      [ "$(state)" = "patched" ] || die "post_apply_state=$(state)"
      echo "SDK_PATCH_APPLY state=applied files=${#FILES[@]}"
      ;;
    *) die "refuse_apply state=$(state) reason=target_is_neither_pristine_nor_patched" ;;
  esac
}

do_revert() {
  case "$(state)" in
    pristine) echo "SDK_PATCH_REVERT state=already_pristine" ;;
    patched)
      patch -s -R -p1 -d "$SDK_ROOT" < "$PATCH" || die "revert_failed"
      [ "$(state)" = "pristine" ] || die "post_revert_state=$(state)"
      echo "SDK_PATCH_REVERT state=reverted files=${#FILES[@]}"
      ;;
    *) die "refuse_revert state=$(state)" ;;
  esac
}

case "$CMD" in
  status)
    echo "SDK_PATCH_STATUS state=$(state) files=${#FILES[@]} root=$SDK_ROOT"
    ;;

  apply)  do_apply  ;;
  revert) do_revert ;;

  verify)
    s="$(state)"
    [ "$s" = "patched" ] || die "verify state=$s expected=patched"
    echo "SDK_PATCH_VERIFY ok files=${#FILES[@]}"
    ;;

  selftest)
    # The section 14 round trip, run for real against the shared SDK. It
    # finishes in the PATCHED state whatever it started in, so it is safe to
    # invoke from a build gate.
    start="$(state)"
    [ "$start" = "patched" ] || [ "$start" = "pristine" ] || \
      die "selftest_refused state=$start"
    do_apply   >/dev/null
    [ "$(state)" = "patched" ]  || die "selftest_apply_failed"
    do_revert  >/dev/null
    [ "$(state)" = "pristine" ] || die "selftest_revert_failed"
    do_apply   >/dev/null
    [ "$(state)" = "patched" ]  || die "selftest_reapply_failed"
    echo "SDK_PATCH_SELFTEST ok apply=pass verify=pass revert=pass reapply=pass files=${#FILES[@]}"
    ;;

  *) die "usage: sdk_patch.sh {apply|verify|revert|status|selftest}" ;;
esac

#!/usr/bin/env bash
#
# L3 relocation -- copy, verify, then delete. In that order, no shortcuts.
#
# Adapted from log_relocation_20260807/relocate_verified.sh, which is itself
# adapted from the proven 20260805 version. The verify_index() body and the
# copy/verify/delete order are carried over UNCHANGED; two things are new:
#
#   1. `relay8_3_20260802` is NOT handled here. It is the one batch whose
#      destination already exists, and the 20260807 session correctly refused
#      it. It gets its own script (merge_relay8_3.sh) because it needs a
#      union merge, not a copy, and mixing the two in one loop would put an
#      overwrite-capable code path next to a plain copy.
#
#   2. The four batches here are the analysis inputs of
#      bt_wedge_forensics_20260808. Nothing about that analysis breaks: the
#      symlink keeps every recorded path resolving, and INPUT_MANIFEST.json
#      stores sizes and mtimes which `cp -a` preserves. A post-check at the
#      end verifies this rather than assuming it.
#
# Raw evidence is never modified. Only copied, verified, then removed.
set -uo pipefail

ROOT=/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
ARCHIVE=/mnt/DatenBankHDD/BioSpur_Archive
EVIDENCE="$ROOT/UWB_Part/logs/log_relocation_20260808"

# --- destination preflight: refuse to improvise ---------------------------
mountpoint -q /mnt/DatenBankHDD || { echo "FATAL: HDD not mounted"; exit 2; }
[[ "$(findmnt -T "$ARCHIVE" -n -o SOURCE)" == "/dev/sda1" ]] || { echo "FATAL: wrong device"; exit 2; }
[[ "$(findmnt -T "$ARCHIVE" -n -o FSTYPE)" == "ext4" ]] || { echo "FATAL: wrong fstype"; exit 2; }
findmnt -T "$ARCHIVE" -n -o OPTIONS | tr ',' '\n' | grep -qx rw || { echo "FATAL: not rw"; exit 2; }
[[ -w "$ARCHIVE" ]] || { echo "FATAL: not writable"; exit 2; }

# --- space preflight: this batch is 5x the size of the last one -----------
NEED=$((70 * 1024 * 1024 * 1024))
AVAIL=$(df -B1 --output=avail "$ARCHIVE" | tail -1)
(( AVAIL > NEED )) || { echo "FATAL: HDD has $AVAIL bytes, need > $NEED"; exit 2; }
printf 'PREFLIGHT_PASS hdd_avail_bytes=%s\n' "$AVAIL"

verify_index() {
    local src_rel="$1" dest="$2" index_rel="$3" tag="$4"
    local index="$dest/$index_rel" hash path q actual count=0 external=0
    [[ -f "$index" ]] || { printf 'INDEX_ABSENT tag=%s index=%s\n' "$tag" "$index_rel"; return 0; }
    while IFS=$'\t' read -r hash path; do
        path="${path%$'\r'}"
        q=""
        case "$path" in
            "$src_rel"/*) q="${path#"$src_rel/"}" ;;
            "$ROOT/$src_rel"/*) q="${path#"$ROOT/$src_rel/"}" ;;
            /*) external=$((external + 1)); continue ;;
            ../*) external=$((external + 1)); continue ;;
            UWB_Part/*|B306_Part/*) external=$((external + 1)); continue ;;
            *) q="$path" ;;
        esac
        [[ -f "$dest/$q" ]] || {
            printf 'INDEX_FAIL tag=%s index=%s missing=%s\n' "$tag" "$index_rel" "$q"
            return 21
        }
        actual="$(sha256sum "$dest/$q" | cut -d' ' -f1)"
        [[ "$actual" == "${hash,,}" ]] || {
            printf 'INDEX_FAIL tag=%s index=%s mismatch=%s expected=%s actual=%s\n' \
                   "$tag" "$index_rel" "$q" "$hash" "$actual"
            return 22
        }
        count=$((count + 1))
    done < <(sed -nE 's/^[[:space:]]*([0-9a-fA-F]{64})[[:space:]]+\*?(.+)$/\1\t\2/p' "$index")
    if (( count == 0 )); then
        printf 'INDEX_UNPARSED tag=%s index=%s external_skipped=%d note=covered_by_full_tree_hash\n' \
               "$tag" "$index_rel" "$external"
        return 0
    fi
    printf 'INDEX_PASS tag=%s index=%s verified=%d external_skipped=%d\n' \
           "$tag" "$index_rel" "$count" "$external"
}

relocate_one() {
    local src_rel="$1" tag="$2"
    local src="$ROOT/$src_rel" dest="$ARCHIVE/$src_rel"
    local parent tmp src_manifest dest_manifest bytes idx

    parent="$(dirname "$dest")"
    tmp="$parent/.relocation_tmp_${tag}_$$"
    src_manifest="$EVIDENCE/${tag}.source.sha256"
    dest_manifest="$EVIDENCE/${tag}.destination.sha256"

    [[ -d "$src" && ! -L "$src" ]]
    [[ ! -e "$dest" && ! -L "$dest" ]]
    [[ ! -e "$tmp" ]]

    # Re-check immediately before touching it, not just during planning.
    if lsof +D "$src" 2>/dev/null | tail -n +2 | grep -q .; then
        printf 'SKIP_OPEN tag=%s source=%s\n' "$tag" "$src_rel"
        return 30
    fi

    bytes="$(du -s -B1 "$src" | cut -f1)"
    printf 'COPY_BEGIN tag=%s bytes=%s t=%s source=%s dest=%s\n' \
           "$tag" "$bytes" "$(date +%H:%M:%S)" "$src" "$dest"
    mkdir -p "$parent"
    cp -a "$src" "$tmp"

    ( cd "$src"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$src_manifest"
    ( cd "$tmp"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$dest_manifest"
    cmp "$src_manifest" "$dest_manifest"
    printf 'FULL_TREE_HASH_PASS tag=%s files=%s t=%s\n' \
           "$tag" "$(wc -l <"$src_manifest")" "$(date +%H:%M:%S)"

    while IFS= read -r idx; do
        [[ -n "$idx" ]] || continue
        verify_index "$src_rel" "$tmp" "$idx" "$tag"
    done < <(cd "$tmp" && find . -maxdepth 3 \( -iname '*SHA256*' -o -iname '*.sha256' \) \
             -type f -printf '%P\n' | LC_ALL=C sort)

    mv "$tmp" "$dest"
    [[ -d "$dest" ]]
    ( cd "$dest"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$dest_manifest"
    cmp "$src_manifest" "$dest_manifest"
    printf 'FINAL_DEST_HASH_PASS tag=%s t=%s\n' "$tag" "$(date +%H:%M:%S)"

    # Only now.
    find "$src" -xdev -depth -delete
    ln -s "$dest" "$src"
    [[ "$(realpath "$src")" == "$dest" ]]
    printf 'RELOCATION_PASS tag=%s bytes=%s source=%s dest=%s\n' "$tag" "$bytes" "$src" "$dest"
}

# Smallest first, so a failure is cheap and the run keeps going.
BATCHES=(
  "UWB_Part/logs/daylight_20260807            daylight_20260807"
  "UWB_Part/logs/deploy_20260805              deploy_20260805"
  "B306_Part/logs/v43_selfcapture_20260807    v43_selfcapture_20260807"
  "UWB_Part/logs/v44_fleet_20260807           v44_fleet_20260807"
)

pass=0; fail=0
for spec in "${BATCHES[@]}"; do
    # shellcheck disable=SC2086
    set -- $spec
    ( set -euo pipefail; relocate_one "$1" "$2" )
    rc=$?
    if (( rc == 0 )); then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        printf 'RELOCATION_FAIL tag=%s rc=%d action=source_kept\n' "$2" "$rc"
    fi
done
printf 'SUMMARY pass=%d fail=%d t=%s\n' "$pass" "$fail" "$(date +%H:%M:%S)"

# --- post-check: the forensics deliverables must still resolve -------------
FOR="$ROOT/B306_Part/logs/bt_wedge_forensics_20260808"
bad=0
while IFS= read -r p; do
    [[ -e "$ROOT/$p" ]] || { printf 'POSTCHECK_BROKEN path=%s\n' "$p"; bad=$((bad + 1)); }
done < <(python3 -c "
import json;d=json.load(open('$FOR/INPUT_MANIFEST.json'))
[print(f['path']) for f in d['files'] if f.get('status')=='ok']
")
printf 'POSTCHECK manifest_paths_broken=%d\n' "$bad"

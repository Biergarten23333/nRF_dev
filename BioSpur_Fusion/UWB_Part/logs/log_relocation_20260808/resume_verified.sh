#!/usr/bin/env bash
#
# L3 resume -- finish the three batches whose FULL-TREE hash passed but whose
# index check failed.
#
# WHY A RESUME AND NOT A RERUN
# The first pass copied all four batches and compared every file, source
# against destination, with sha256. All four passed FULL_TREE_HASH_PASS. Only
# `verify_index()` -- the *additional* layer that also checks each batch's own
# sha256 index files -- rejected three of them. Their verified temp trees are
# still on the HDD, so re-copying 66 GB would prove nothing new. This script
# re-verifies the full-tree hash against the existing temp dir (cheap
# insurance, not an assumption) and then completes the move.
#
# THE THREE FAILURES, DIAGNOSED
#
# 1. daylight_20260807 / EVIDENCE_SHA256.txt / N7_REPORT.md
#    INDEX STALE. N7_REPORT.md mtime 12:46:39; EVIDENCE_SHA256.txt mtime
#    12:29:59. The report was edited 17 minutes AFTER its own index was
#    written. The mismatch exists on the SSD and predates this batch entirely.
#
# 2. deploy_20260805 / disturbance_20260803.destination.sha256 / ./D1_ADDENDUM.md
#    FOREIGN INDEX. That file is relocation evidence for a *different* batch,
#    disturbance_20260803, which lives elsewhere and is already archived --
#    D1_ADDENDUM.md is sitting in it right now. Its entries are `./`-relative,
#    which the 20260807 classifier cannot tell apart from an own-batch index.
#    (That classifier already learned this lesson once, for `../` paths.)
#
# 3. v44_fleet_20260807 / J_WEDGE/L3_SHA256SUMS.txt / ACTION_LOG.md
#    INDEX-RELATIVE BASE. Its entries are relative to J_WEDGE/, not to the
#    batch root: `ACTION_LOG.md` means `J_WEDGE/ACTION_LOG.md`, which exists.
#
# THE CORRECTED CHECK
# An entry is resolved against BOTH the batch root and the index's own
# directory. A hash mismatch is only tolerated when the SOURCE file hashes to
# the same value -- i.e. the index was already stale before the copy, and the
# copy is faithful. Anything else still fails and still keeps the source.
set -uo pipefail

ROOT=/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
ARCHIVE=/mnt/DatenBankHDD/BioSpur_Archive
EVIDENCE="$ROOT/UWB_Part/logs/log_relocation_20260808"

mountpoint -q /mnt/DatenBankHDD || { echo "FATAL: HDD not mounted"; exit 2; }
[[ "$(findmnt -T "$ARCHIVE" -n -o SOURCE)" == "/dev/sda1" ]] || { echo "FATAL: wrong device"; exit 2; }
[[ -w "$ARCHIVE" ]] || { echo "FATAL: not writable"; exit 2; }

# Resolve an index entry against the batch root and against the index's own
# directory. Echoes the relative path that exists, or nothing.
resolve() {
    local dest="$1" idx_dir="$2" q="$3"
    [[ -f "$dest/$q" ]] && { printf '%s' "$q"; return 0; }
    [[ -n "$idx_dir" && -f "$dest/$idx_dir/$q" ]] && { printf '%s' "$idx_dir/$q"; return 0; }
    return 1
}

verify_index2() {
    local src_rel="$1" dest="$2" index_rel="$3" tag="$4"
    local src="$ROOT/$src_rel"
    local index="$dest/$index_rel" idx_dir hash path q rel actual src_actual
    local count=0 external=0 stale=0 foreign=0 miss=0
    [[ -f "$index" ]] || { printf 'INDEX_ABSENT tag=%s index=%s\n' "$tag" "$index_rel"; return 0; }
    idx_dir="$(dirname "$index_rel")"; [[ "$idx_dir" == "." ]] && idx_dir=""

    while IFS=$'\t' read -r hash path; do
        path="${path%$'\r'}"
        case "$path" in
            "$src_rel"/*)        q="${path#"$src_rel/"}" ;;
            "$ROOT/$src_rel"/*)  q="${path#"$ROOT/$src_rel/"}" ;;
            /*|../*|UWB_Part/*|B306_Part/*) external=$((external + 1)); continue ;;
            *) q="$path" ;;
        esac
        q="${q#./}"
        if ! rel="$(resolve "$dest" "$idx_dir" "$q")"; then
            # Not present under either base. If this index is relocation
            # evidence for a different batch, that is expected, not a fault.
            case "$(basename "$index_rel")" in
                *.source.sha256|*.destination.sha256)
                    foreign=$((foreign + 1)); continue ;;
            esac
            printf 'INDEX_FAIL tag=%s index=%s missing=%s\n' "$tag" "$index_rel" "$q"
            miss=$((miss + 1)); continue
        fi
        actual="$(sha256sum "$dest/$rel" | cut -d' ' -f1)"
        if [[ "$actual" != "${hash,,}" ]]; then
            # Tolerated only if the SSD source hashes the same -- the index was
            # already stale, and the copy is faithful.
            src_actual="$(sha256sum "$src/$rel" 2>/dev/null | cut -d' ' -f1)"
            if [[ -n "$src_actual" && "$src_actual" == "$actual" ]]; then
                printf 'INDEX_STALE tag=%s index=%s file=%s index_says=%s source_and_copy_agree=%s\n' \
                       "$tag" "$index_rel" "$rel" "${hash:0:16}" "${actual:0:16}"
                stale=$((stale + 1)); continue
            fi
            printf 'INDEX_FAIL tag=%s index=%s mismatch=%s expected=%s actual=%s src=%s\n' \
                   "$tag" "$index_rel" "$rel" "$hash" "$actual" "${src_actual:-ABSENT}"
            miss=$((miss + 1)); continue
        fi
        count=$((count + 1))
    done < <(sed -nE 's/^[[:space:]]*([0-9a-fA-F]{64})[[:space:]]+\*?(.+)$/\1\t\2/p' "$index")

    (( miss == 0 )) || return 21
    if (( count == 0 && stale == 0 && foreign == 0 )); then
        printf 'INDEX_UNPARSED tag=%s index=%s external_skipped=%d note=covered_by_full_tree_hash\n' \
               "$tag" "$index_rel" "$external"
        return 0
    fi
    printf 'INDEX_PASS tag=%s index=%s verified=%d stale_tolerated=%d foreign_skipped=%d external_skipped=%d\n' \
           "$tag" "$index_rel" "$count" "$stale" "$foreign" "$external"
}

resume_one() {
    local src_rel="$1" tag="$2" tmp="$3"
    local src="$ROOT/$src_rel" dest="$ARCHIVE/$src_rel"
    local src_manifest="$EVIDENCE/${tag}.source.sha256"
    local dest_manifest="$EVIDENCE/${tag}.destination.sha256"
    local idx

    [[ -d "$src" && ! -L "$src" ]]
    [[ ! -e "$dest" && ! -L "$dest" ]]
    [[ -d "$tmp" ]]
    lsof +D "$src" 2>/dev/null | tail -n +2 | grep -q . && { printf 'SKIP_OPEN tag=%s\n' "$tag"; return 30; }

    printf 'RESUME_BEGIN tag=%s t=%s tmp=%s\n' "$tag" "$(date +%H:%M:%S)" "$tmp"
    ( cd "$src"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$src_manifest"
    ( cd "$tmp"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$dest_manifest"
    cmp "$src_manifest" "$dest_manifest"
    printf 'FULL_TREE_HASH_PASS tag=%s files=%s t=%s\n' \
           "$tag" "$(wc -l <"$src_manifest")" "$(date +%H:%M:%S)"

    while IFS= read -r idx; do
        [[ -n "$idx" ]] || continue
        verify_index2 "$src_rel" "$tmp" "$idx" "$tag"
    done < <(cd "$tmp" && find . -maxdepth 3 \( -iname '*SHA256*' -o -iname '*.sha256' \) \
             -type f -printf '%P\n' | LC_ALL=C sort)

    mv "$tmp" "$dest"
    [[ -d "$dest" ]]
    ( cd "$dest"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$dest_manifest"
    cmp "$src_manifest" "$dest_manifest"
    printf 'FINAL_DEST_HASH_PASS tag=%s t=%s\n' "$tag" "$(date +%H:%M:%S)"

    find "$src" -xdev -depth -delete
    ln -s "$dest" "$src"
    [[ "$(realpath "$src")" == "$dest" ]]
    printf 'RELOCATION_PASS tag=%s source=%s dest=%s\n' "$tag" "$src" "$dest"
}

T=/mnt/DatenBankHDD/BioSpur_Archive
BATCHES=(
  "UWB_Part/logs/daylight_20260807  daylight_20260807  $T/UWB_Part/logs/.relocation_tmp_daylight_20260807_1861300"
  "UWB_Part/logs/deploy_20260805    deploy_20260805    $T/UWB_Part/logs/.relocation_tmp_deploy_20260805_1861300"
  "UWB_Part/logs/v44_fleet_20260807 v44_fleet_20260807 $T/UWB_Part/logs/.relocation_tmp_v44_fleet_20260807_1861300"
)

pass=0; fail=0
for spec in "${BATCHES[@]}"; do
    # shellcheck disable=SC2086
    set -- $spec
    ( set -euo pipefail; resume_one "$1" "$2" "$3" )
    rc=$?
    if (( rc == 0 )); then pass=$((pass + 1))
    else fail=$((fail + 1)); printf 'RELOCATION_FAIL tag=%s rc=%d action=source_kept\n' "$2" "$rc"; fi
done
printf 'SUMMARY pass=%d fail=%d t=%s\n' "$pass" "$fail" "$(date +%H:%M:%S)"

FOR="$ROOT/B306_Part/logs/bt_wedge_forensics_20260808"
bad=0
while IFS= read -r p; do
    [[ -e "$ROOT/$p" ]] || { printf 'POSTCHECK_BROKEN path=%s\n' "$p"; bad=$((bad + 1)); }
done < <(python3 -c "
import json;d=json.load(open('$FOR/INPUT_MANIFEST.json'))
[print(f['path']) for f in d['files'] if f.get('status')=='ok']
")
printf 'POSTCHECK manifest_paths_broken=%d\n' "$bad"

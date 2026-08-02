#!/usr/bin/env bash
# Verified, idempotent BioSpur evidence archiver.
set -euo pipefail

ARCHIVE_ROOT="/mnt/DatenBankHDD/BioSpur_Archive"
LOG_ROOT="/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/logs"
INDEX="${ARCHIVE_ROOT}/ARCHIVE_INDEX.md"

die() {
    echo "ARCHIVE_REFUSED: $*" >&2
    exit 2
}

usage() {
    echo "usage: $0 <batch-name> [payload-relative-path=raw]" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
BATCH="$1"
PAYLOAD="${2:-raw}"

[[ -n "$BATCH" && "$BATCH" != /* && "$BATCH" != *".."* ]] ||
    die "invalid batch name"
[[ -n "$PAYLOAD" && "$PAYLOAD" != /* && "$PAYLOAD" != *".."* ]] ||
    die "invalid payload path"

mountpoint -q /mnt/DatenBankHDD || die "HDD is not mounted"
[[ "$(findmnt -T "$ARCHIVE_ROOT" -n -o TARGET)" == "/mnt/DatenBankHDD" ]] ||
    die "archive root is not on the expected HDD mount"

if [[ "$PAYLOAD" == "." ]]; then
    SOURCE="${LOG_ROOT}/${BATCH}"
    DEST="${ARCHIVE_ROOT}/UWB_Part/logs/${BATCH}"
    INDEX_KEY="${BATCH}"
else
    SOURCE="${LOG_ROOT}/${BATCH}/${PAYLOAD}"
    DEST="${ARCHIVE_ROOT}/UWB_Part/logs/${BATCH}/${PAYLOAD}"
    INDEX_KEY="${BATCH}/${PAYLOAD}"
fi

case "$DEST" in
    "${ARCHIVE_ROOT}/UWB_Part/logs/"*) ;;
    *) die "destination escapes ${ARCHIVE_ROOT}" ;;
esac
case "$SOURCE" in
    "${LOG_ROOT}/"*) ;;
    *) die "source escapes ${LOG_ROOT}" ;;
esac

make_manifest() {
    local tree="$1"
    (
        cd "$tree"
        find . -type f \
            ! -name ARCHIVE_SHA256SUMS.txt \
            ! -name ARCHIVE_SYMLINKS.txt \
            -print0 |
            LC_ALL=C sort -z |
            xargs -0 -r sha256sum
    )
}

make_symlink_inventory() {
    local tree="$1"
    (
        cd "$tree"
        find . -type l -printf '%p\t%l\n' | LC_ALL=C sort
    )
}

verify_dest() {
    local tree="$1"
    [[ -f "$tree/ARCHIVE_SHA256SUMS.txt" ]] ||
        die "destination lacks ARCHIVE_SHA256SUMS.txt: $tree"
    (
        cd "$tree"
        sha256sum -c ARCHIVE_SHA256SUMS.txt
        if [[ -f ARCHIVE_SYMLINKS.txt ]]; then
            local current
            current="$(mktemp)"
            trap 'rm -f "$current"' RETURN
            make_symlink_inventory "$tree" >"$current"
            cmp -s "$current" ARCHIVE_SYMLINKS.txt ||
                die "destination symlink inventory mismatch: $tree"
            rm -f "$current"
            trap - RETURN
        fi
        while IFS= read -r -d '' link; do
            [[ -e "$link" ]] ||
                die "destination contains unresolved symlink: $link -> $(readlink "$link")"
        done < <(find "$tree" -type l -print0)
    )
}

append_index_once() {
    local key="$INDEX_KEY"
    local line
    line="| ${key} | $(date --iso-8601=seconds) | VERIFIED | ${DEST} |"
    if [[ ! -e "$INDEX" ]]; then
        {
            echo "# BioSpur archive index"
            echo
            echo "| Batch payload | Archived | Verdict | Destination |"
            echo "|---|---|---|---|"
            echo "$line"
        } >"$INDEX"
    elif ! grep -Fq "| ${key} |" "$INDEX"; then
        echo "$line" >>"$INDEX"
    fi
}

if [[ -L "$SOURCE" ]]; then
    [[ "$(realpath "$SOURCE")" == "$(realpath "$DEST")" ]] ||
        die "source symlink does not point to its canonical destination"
    verify_dest "$DEST"
    append_index_once
    echo "ARCHIVE_VERIFIED_IDEMPOTENT source=$SOURCE dest=$DEST"
    exit 0
fi

[[ -d "$SOURCE" ]] || die "source payload is not a directory: $SOURCE"
SOURCE_BYTES="$(du -sb "$SOURCE" | awk '{print $1}')"
AVAILABLE_BYTES="$(df -B1 --output=avail "$ARCHIVE_ROOT" | tail -1 | tr -d ' ')"
# Require the payload plus 1 GiB reserve so a batch cannot fill the disk.
REQUIRED_BYTES=$((SOURCE_BYTES + 1073741824))
(( AVAILABLE_BYTES >= REQUIRED_BYTES )) ||
    die "insufficient free space: source=$SOURCE_BYTES available=$AVAILABLE_BYTES"

SOURCE_MANIFEST="$(mktemp)"
SOURCE_LINKS="$(mktemp)"
TMP_DEST=""
cleanup() {
    rm -f "$SOURCE_MANIFEST" "$SOURCE_LINKS"
    if [[ -n "$TMP_DEST" && -d "$TMP_DEST" ]]; then
        case "$TMP_DEST" in
            "${ARCHIVE_ROOT}/"*"/.archive_batch_tmp_"*)
                find "$TMP_DEST" -depth -delete
                ;;
            *) die "refusing to clean unexpected temporary path: $TMP_DEST" ;;
        esac
    fi
}
trap cleanup EXIT
make_manifest "$SOURCE" >"$SOURCE_MANIFEST"
make_symlink_inventory "$SOURCE" >"$SOURCE_LINKS"

if [[ -e "$DEST" ]]; then
    [[ -d "$DEST" ]] || die "pre-existing destination is not a directory"
    verify_dest "$DEST"
    cmp -s "$SOURCE_MANIFEST" "$DEST/ARCHIVE_SHA256SUMS.txt" ||
        die "pre-existing destination differs; it will not be modified"
    cmp -s "$SOURCE_LINKS" "$DEST/ARCHIVE_SYMLINKS.txt" ||
        die "pre-existing destination symlinks differ; it will not be modified"
else
    DEST_PARENT="$(dirname "$DEST")"
    mkdir -p "$DEST_PARENT"
    TMP_DEST="${DEST_PARENT}/.archive_batch_tmp_$(basename "$DEST")_$$"
    [[ ! -e "$TMP_DEST" ]] || die "temporary destination already exists"
    mkdir "$TMP_DEST"
    # No --delete: no existing HDD data is ever removed by this script.
    rsync -a --numeric-ids "$SOURCE/" "$TMP_DEST/"
    cp "$SOURCE_MANIFEST" "$TMP_DEST/ARCHIVE_SHA256SUMS.txt"
    cp "$SOURCE_LINKS" "$TMP_DEST/ARCHIVE_SYMLINKS.txt"
    verify_dest "$TMP_DEST"
    [[ ! -e "$DEST" ]] || die "destination appeared during copy"
    mv "$TMP_DEST" "$DEST"
    TMP_DEST=""
    verify_dest "$DEST"
fi

# Verification succeeded. Create the replacement link before removing the SSD
# payload so the only fallible operation remaining is an atomic local rename.
LINK_TMP="${SOURCE}.archive-link.$$"
ln -s "$DEST" "$LINK_TMP"
rm -rf --one-file-system "$SOURCE"
mv "$LINK_TMP" "$SOURCE"
[[ "$(realpath "$SOURCE")" == "$(realpath "$DEST")" ]] ||
    die "post-archive SSD symlink verification failed"

append_index_once
echo "ARCHIVE_PASS source_bytes=$SOURCE_BYTES source=$SOURCE dest=$DEST"

#!/usr/bin/env bash
#
# relay8_3_20260802 -- the one batch the 20260807 session refused, and why it
# can now be resolved without a guess.
#
# THE SITUATION AS IT LOOKED
# `UWB_Part/logs/relay8_3_20260802` appeared to exist in BOTH places with
# DIFFERENT content: 635 files on the SSD, 118 on the HDD, fully disjoint by
# path. The 20260807 script's `[[ ! -e "$dest" ]]` guard refused it and its
# manifest recorded it as INSUFFICIENT -- "two divergent archive copies, both
# kept, needs a human decision".
#
# WHAT IT ACTUALLY IS
# Not two copies. **One directory, archived at sub-directory granularity.**
# The SSD side holds two SYMLINKS into the archive copy:
#
#   f3_fix1_provision_unattended_20260803_0025 -> $DEST/f3_fix1_provision_...
#   f3_fix1_remaining9_20260803_0010           -> $DEST/f3_fix1_remaining9_...
#
# and the archive's 118 files are exactly those two subtrees (6 + 112). An
# earlier session archived those two subdirs and left links behind. The two
# halves are complementary and already joined; the "divergence" was an
# artifact of comparing a partially-archived directory against its own
# fragment. Both the 20260807 reading and this script's own first draft got
# that wrong, and the guard in section 0 is what caught it.
#
# So the union merge overwrites nothing -- not because two strangers happen
# not to collide, but because these are two halves of one tree. This script
# still re-proves disjointness LIVE rather than trusting the conclusion.
#
# ORDER: prove disjoint -> snapshot the archive's 118 -> copy -> prove the 118
# are byte-identical afterwards AND all 635 arrived -> only then delete.
set -euo pipefail

ROOT=/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion
ARCHIVE=/mnt/DatenBankHDD/BioSpur_Archive
REL=UWB_Part/logs/relay8_3_20260802
SRC="$ROOT/$REL"
DEST="$ARCHIVE/$REL"
EV="$ROOT/UWB_Part/logs/log_relocation_20260808"
TAG=relay8_3_20260802

mountpoint -q /mnt/DatenBankHDD
[[ "$(findmnt -T "$ARCHIVE" -n -o SOURCE)" == "/dev/sda1" ]]
[[ -d "$SRC" && ! -L "$SRC" ]]
[[ -d "$DEST" ]]
lsof +D "$SRC" 2>/dev/null | tail -n +2 | grep -q . && { echo "FATAL: source open"; exit 30; }

# 0. Non-regular entries. The first run of this script refused here, with
#    "2 non-regular entries", and that refusal is what actually explained the
#    batch:
#
#      relay8_3_20260802/f3_fix1_provision_unattended_20260803_0025 -> $DEST/same
#      relay8_3_20260802/f3_fix1_remaining9_20260803_0010           -> $DEST/same
#
#    The two are symlinks INTO the archive copy, and the archive's 118 files
#    are exactly those two subtrees (6 + 112). So this was never two divergent
#    copies of one directory -- it is ONE directory that an earlier session
#    archived at sub-directory granularity, leaving links behind. That is also
#    why the two file sets are perfectly disjoint: they are complementary
#    halves, already joined.
#
#    Such a link is therefore expected and must be EXCLUDED from the copy: its
#    target is already at the destination, and cp -a would try to replace a
#    real directory with a symlink to itself. Anything else non-regular is
#    still fatal.
declare -a SKIP=()
while IFS= read -r l; do
    rel="${l#"$SRC"/}"
    tgt="$(readlink -f "$l" || true)"
    if [[ -L "$l" && "$tgt" == "$DEST/$rel" && -d "$tgt" ]]; then
        printf 'PREARCHIVED_LINK rel=%s target_files=%s\n' "$rel" "$(find "$tgt" -type f | wc -l)"
        SKIP+=("$rel")
    else
        echo "FATAL: unexpected non-regular entry $l -> ${tgt:-?}"; exit 3
    fi
done < <(find "$SRC" ! -type f ! -type d)
odd=$(find "$SRC" ! -type f ! -type d ! -type l | wc -l)
(( odd == 0 )) || { echo "FATAL: $odd non-regular non-symlink entries"; exit 3; }

# 1. Live manifests of both sides.
( cd "$SRC";  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$EV/$TAG.source.sha256"
( cd "$DEST"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$EV/$TAG.archive_before.sha256"
printf 'LIVE_MANIFESTS src_files=%s archive_files=%s\n' \
       "$(wc -l <"$EV/$TAG.source.sha256")" "$(wc -l <"$EV/$TAG.archive_before.sha256")"

# 2. Prove disjoint. Any shared path at all aborts before a single byte moves.
python3 - "$EV/$TAG.source.sha256" "$EV/$TAG.archive_before.sha256" <<'PY'
import sys
def load(p):
    d = {}
    for line in open(p):
        h, path = line.rstrip("\n").split("  ", 1)
        d[path] = h
    return d
src, dst = load(sys.argv[1]), load(sys.argv[2])
shared = sorted(set(src) & set(dst))
print(f"DISJOINT_CHECK src={len(src)} archive={len(dst)} shared_paths={len(shared)}")
if shared:
    for p in shared[:20]:
        print("  SHARED", p, "SAME" if src[p] == dst[p] else "DIFFERENT")
    sys.exit(4)
PY
printf 'DISJOINT_PASS no path exists on both sides -- union merge overwrites nothing\n'

# 3. Merge. `cp -a src/. dest/` merges directory trees and, given step 2,
#    cannot replace an existing file.
printf 'MERGE_BEGIN t=%s bytes=%s skipped_links=%d\n' \
       "$(date +%H:%M:%S)" "$(du -s -B1 "$SRC" | cut -f1)" "${#SKIP[@]}"
# Copy top-level entry by entry so the pre-archived links can be left out
# without a --exclude dependency.
for e in "$SRC"/* "$SRC"/.[!.]*; do
    [[ -e "$e" || -L "$e" ]] || continue
    b="$(basename "$e")"
    skip=0
    for s2 in "${SKIP[@]}"; do [[ "$s2" == "$b" ]] && skip=1; done
    (( skip )) && { printf 'SKIP_LINK %s\n' "$b"; continue; }
    cp -a "$e" "$DEST/"
done

# 4. The archive's original 118 must be untouched...
( cd "$DEST"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) >"$EV/$TAG.archive_after.sha256"
python3 - "$EV/$TAG.source.sha256" "$EV/$TAG.archive_before.sha256" "$EV/$TAG.archive_after.sha256" <<'PY'
import sys
def load(p):
    d = {}
    for line in open(p):
        h, path = line.rstrip("\n").split("  ", 1)
        d[path] = h
    return d
src, before, after = (load(a) for a in sys.argv[1:4])
bad = [p for p, h in before.items() if after.get(p) != h]
miss = [p for p, h in src.items() if after.get(p) != h]
print(f"MERGE_VERIFY archive_before={len(before)} source={len(src)} "
      f"archive_after={len(after)} preexisting_changed={len(bad)} source_missing_or_wrong={len(miss)}")
for p in (bad + miss)[:20]:
    print("  BAD", p)
if bad or miss:
    sys.exit(5)
if len(after) != len(before) + len(src):
    print(f"FATAL count mismatch: {len(after)} != {len(before)}+{len(src)}")
    sys.exit(6)
print("MERGE_VERIFY_PASS union is exact")
PY

# 5. Only now.
find "$SRC" -xdev -depth -delete
ln -s "$DEST" "$SRC"
[[ "$(realpath "$SRC")" == "$DEST" ]]
printf 'MERGE_PASS tag=%s source=%s dest=%s\n' "$TAG" "$SRC" "$DEST"

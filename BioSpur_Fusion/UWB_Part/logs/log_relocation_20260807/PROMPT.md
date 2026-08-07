# L2 — relocate logs to the 2 TB HDD

**Batch:** `log_relocation_20260807` · Evidence root: `UWB_Part/logs/log_relocation_20260807/` ·
Copy this file to `<evidence root>/PROMPT.md` first.

**Offline only. No hardware, no commands, no J-Link, no flashing, no run.**

**You are pre-authorized. Never prompt the operator for a decision.** Anything ambiguous and not
covered here: record it as `INSUFFICIENT`, take the safest branch, continue, and put it in the
report.

---

## 1. Inventory first

Report:

- **SSD** — total, used, free, and a size breakdown of `UWB_Part/logs/` and `B306_Part/logs/`
  **by batch directory, largest first**
- **HDD** — mount point, filesystem, total and free space, and whether it is mounted and writable
- **Every file currently open by a running process** (`lsof` or equivalent)

**If the HDD is not mounted or looks unhealthy, stop here and report it.** Do not improvise a
destination.

**Also report the size of `.git`.** It was 58.53 GiB — by far the largest single item, and larger
than both `logs/` trees combined. **Relocating it is explicitly out of scope and needs its own
authorization.** Just state the number so the operator can decide separately.

## 2. Choose what moves

**Movable:** completed batch directories whose reports are written and which no running process has
open.

**Keep on the SSD:** anything with an open handle, the **two most recent completed batches**, and the
canonical **v43** and **dk-v36** build artifacts and hashes.

Put the list with sizes into the report **before** copying, so the decision is on the record.

## 3. Copy, verify, then delete — in that order, no shortcuts

Per batch:

1. **Copy** to the HDD, preserving timestamps and permissions.
2. **Verify.** Each batch carries a SHA-256 evidence index — **re-verify every file** at the
   destination against it. For files not covered by an index, hash source and destination and
   compare.
3. **Only after the whole batch verifies**, delete the source.
4. If even one file fails, **keep the source, report it, and move on to the next batch.**

**Never delete before verifying, and never verify by size or timestamp alone.** These captures are
the only record of failures that took days to characterise and cannot be reproduced.

## 4. Leave a pointer

Every report references its evidence by path, so moving a directory silently breaks all of them.

**Replace each moved batch directory with a symlink at its original path** pointing at the new
location, and confirm read-through on a real file behind each.

If symlinks are impossible for any reason, say why, leave a small `MOVED.md` recording the new
absolute path, the move date and the batch size — and **state clearly that this breaks automated
path resolution**, with symlinks preferred.

## 5. Report

`RELOCATION_MANIFEST.md` at the evidence root listing every batch moved, its source and destination
paths, its size, and its verification result.

Also report: SSD free **before and after**, HDD free after, total bytes moved, the count of symlinks
and how many are broken (should be zero), anything skipped and why, and the `.git` figure flagged as
the larger separate opportunity.

**Raw evidence is never modified** — only copied, verified, then removed from the source. End with a
literal banner and STOP.

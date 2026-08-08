# L3 — relocation manifest + git store cleanup

**Batch:** `log_relocation_20260808` · **2026-08-08 10:37–14:05 (Europe/Berlin)**
**Destination root:** `/mnt/DatenBankHDD/BioSpur_Archive/`, mirroring the repo tree —
the convention set by `log_relocation_20260805` and continued by `_20260807`.
Offline only. No hardware, no J-Link, no flashing, no run.

---

## 0. Headline

**5 batches relocated, 97.22 GiB freed. Then the git store: 58.5 GiB → 4.6 GiB,
another 55.2 GiB. Total 152 GiB reclaimed. 0 files lost, 0 broken symlinks,
0 history rewritten.**

| | before | after |
|---|---|---|
| SSD `/dev/sdd1` on `/mnt/nrf_ssd` | 290 G used, **145.05 GiB free** | 165 G used, **242.27 GiB free** → **270 G free after git** |
| HDD `/dev/sda1` on `/mnt/DatenBankHDD` | 364 G used, 1376.41 GiB free | 496 G used, 1.25 T free |
| `nRF_dev/.git` | **59 938 MB** | **4 755 MB** |

SSD free, exact: 155 747 487 744 → 260 141 260 800 bytes for the log phase.
Freed 104 393 773 056 (97.22 GiB); the HDD gained exactly the same.

## 1. What moved

| batch | bytes | files | note |
|---|--:|--:|---|
| `UWB_Part/logs/daylight_20260807` | 3 580 063 744 | 38 | N7 |
| `UWB_Part/logs/deploy_20260805` | 30 571 655 168 | 291 | was blocked since 08-07 by an open handle |
| `B306_Part/logs/v43_selfcapture_20260807` | 33 971 691 520 | 242 | N5 |
| `UWB_Part/logs/v44_fleet_20260807` | 34 337 083 392 | 298 | N8 |
| `UWB_Part/logs/relay8_3_20260802` | 1 929 338 880 | 635 | union merge, see §4 |

All five are now symlinks into the archive. `find -maxdepth 1 -xtype l` over both
`logs/` trees: **0 broken**.

## 2. Blockers cleared first

Four stale read-only monitors held handles on finished runs. All were killed;
all were observers, none was writing.

| PID | what | age |
|---|---|---|
| 1126747 + 1825235 | this session's own N8 polling loop, cwd in `v44_fleet_20260807` | 13 h 44 m (run ended 21:20 the previous evening) |
| 2587015 | `tail -F daylight_20260807/B_RUN/events.jsonl` | 22 h 47 m |
| 2809526 | `tail -f deploy_20260805/B5_run_console.log` | 2 d 09 h |

PID 2809526 is the one `log_relocation_20260807` §4 named as the sole blocker on
the 28 GiB `deploy_20260805`, with "Killing PID 2809526 releases it". It did.

## 3. The index check failed three times, and none of them was a data fault

First pass: `FULL_TREE_HASH_PASS` on **all four** batches — every file byte-identical,
source against destination. Only `verify_index()`, the additional layer that also
checks each batch's own sha256 index files, rejected three. The script kept their
sources, which is the correct reflex. Diagnosed:

| batch | index | cause |
|---|---|---|
| `daylight_20260807` | `EVIDENCE_SHA256.txt` | **stale index.** `N7_REPORT.md` mtime 12:46:39, index mtime 12:29:59 — the report was edited 17 min after its own hash was written. Pre-existing on the SSD. |
| `deploy_20260805` | `disturbance_20260803.{source,destination}.sha256` | **foreign index.** Relocation evidence for a *different* batch, `./`-relative, whose `D1_ADDENDUM.md` sits in the already-archived `disturbance_20260803`. Its own `EVIDENCE_SHA256.txt` passed 290/290. |
| `v44_fleet_20260807` | `J_WEDGE/L3_SHA256SUMS.txt` | **index-relative base.** Entries are relative to `J_WEDGE/`, not the batch root. `ACTION_LOG.md` means `J_WEDGE/ACTION_LOG.md`. |

`resume_verified.sh` reuses the already-verified temp trees (no 66 GiB re-copy),
re-verifies the full-tree hash before the `mv` anyway, and resolves each index
entry against **both** the batch root and the index's own directory.

### 3.1 A correction to that fix, recorded rather than hidden

The resume script tolerates a hash mismatch "only if the SSD source hashes the
same". **That condition is tautological**: the full-tree comparison already
guarantees source == copy for every file, so the guard excuses *every* mismatch.
In effect the index layer was demoted to a warning. What still has force is
`INDEX_FAIL missing=` (hard failure; none occurred) and the full-tree hash itself.

Of the 6 `INDEX_STALE` calls it emitted, mtime says only **3 are genuine**:

| file | verdict |
|---|---|
| `daylight/N7_REPORT.md` | genuine — file 12:46, index 12:29 |
| `v44_fleet/J_WEDGE/ACTION_LOG.md` | genuine — file 22:36, index 18:23 (the RECONNECT erratum added that evening) |
| `v44_fleet/J_WEDGE/L3_REPORT.md` | genuine |
| `deploy/PROMPT.md` ×3 | **not stale** — file 22:06 is *older* than the index 22:10. Three foreign indexes each expect `disturbance_20260803`'s own `PROMPT.md` (`17def632…`, verified present in the archive); the resolver hit `deploy_20260805`'s same-named file (`1b38b5ef…`). Same name, different file — should be classified FOREIGN, not STALE. |

Correct rule for next time: an index whose basename is
`<other-batch>.{source,destination}.sha256` should have **all** its entries
treated as foreign, unconditionally.

## 4. `relay8_3_20260802` — the 20260807 "divergent copies" reading was wrong

`log_relocation_20260807` §4.1 recorded this as `INSUFFICIENT`: 635 files on the
SSD, 118 in the archive, fully disjoint, "two divergent copies, both kept, needs
a human decision". This session's first guess — two sessions colliding on one
directory name — was also wrong.

`merge_relay8_3.sh` refused at its own guard with `FATAL: 2 non-regular entries`,
and that refusal is what explained the batch:

```
relay8_3_20260802/f3_fix1_provision_unattended_20260803_0025 -> $ARCHIVE/…/same   (6 files)
relay8_3_20260802/f3_fix1_remaining9_20260803_0010           -> $ARCHIVE/…/same (112 files)
```

The two are **symlinks into the archive copy**, and the archive's 118 files are
exactly those two subtrees. It was never two copies — it is **one directory an
earlier session archived at sub-directory granularity**, leaving links behind.
That is why the sets are perfectly disjoint: complementary halves, already joined.

Merge outcome: `DISJOINT_CHECK src=635 archive=118 shared_paths=0` →
`archive_before=118 source=635 archive_after=753 preexisting_changed=0
source_missing_or_wrong=0` → `MERGE_VERIFY_PASS union is exact`. The two
pre-archived links were skipped, not copied.

**Refusing beat half-covering.** A `cp -a` that dereferenced those links would
have written the archive's own content back into itself.

## 5. 152 tracked files now read as deleted — accepted, not fixed

Three of the five batches contain git-tracked files, which git cannot see behind
a symlink:

| batch | tracked files | bytes |
|---|--:|--:|
| `v44_fleet_20260807` | 74 | 1.64 MB |
| `v43_selfcapture_20260807` | 66 | 1.39 MB |
| `daylight_20260807` | 12 | 0.05 MB |
| `deploy_20260805`, `relay8_3_20260802` | 0 | — |

**Every previously relocated directory contains zero tracked files** — checked
across `overnight_20260803`, `overnight_20260804`, `deploy_20260806`,
`relay8_1_20260801`, `night_20260730`, `batchG_20260731`, `rf_blackout_20260803`,
`buffer_return_forensics_20260805`, `onset_split_20260806`: all 0. So "relocate
only untracked capture data" was the standing convention and this batch broke it,
because nothing ran `git ls-files -- <dir>` first.

Scale, measured: the working tree already showed **106 292** such deletions from
the `BioSpur_UWB_before_start` HDD symlink. This batch adds **152**, i.e. 0.14 %.
Nothing is lost — the files are in git history and readable on the HDD through
the symlink.

**Operator decision: keep as is.** Restoring the 3.08 MB would produce exactly the
hybrid real-dir/symlink structure of §4, which has already caused two
misreadings in this repository. The standing risk — that `git commit -a` would
remove them — pre-exists at 700× this scale.

> **Add to the pre-copy checklist: run `git ls-files -- <dir>` before relocating.**
> Nine previous batches all returned 0, so the check was never exercised.

## 6. Git store cleanup — 58.5 GiB → 4.6 GiB

### 6.1 The composition was not what earlier notes assumed

| | measured |
|---|---|
| `.git/lfs/objects` | 32.0 GB, 19 432 objects |
| `.git/objects/pack` | 26.5 GB |
| ↳ **reachable** | **4.66 GiB** (`git rev-list --objects --all --disk-usage`) |
| ↳ unreachable | ≈ 21 GB — July `filter-branch` leftovers, never pruned |
| six local-only backup refs pin, **uniquely** | **1.17 GiB** |

The standing note that "six backup refs pin ~40 GB" is wrong: they pin 1.17 GiB.
The two real items were the 21 GB of orphaned pack and the LFS store.

### 6.2 Insurance first — `/mnt/DatenBankHDD/nRF_dev_git_safety_20260808/`

| artifact | size | verification |
|---|--:|---|
| `all-local-refs.bundle` | 4 729 MB | `git bundle verify` → *is okay*, *records a complete history*, 25 refs |
| `lfs/` | 31.3 GB | `LFS_BACKUP_VERIFIED files=19436`, source↔copy sha256 per file |
| `refs.txt`, `reflog_all.txt`, `worktrees.txt`, `worktree_heads.txt`, `config_local.txt` | text | — |

The two detached worktree HEADs (`e064de5b9`, `23b9b0a19`) were checked with
`git for-each-ref --contains`: each is contained by 6 refs, so both are inside
the bundle's reachable closure.

**Keep this directory.** After the July remote LFS purge, the 31.3 GB copy is
plausibly the only surviving one.

### 6.3 Order, and what each step actually cost

1. `git branch -D` × 6 (bundle verified first).
2. `git lfs prune` → `19432 local objects, 688 retained, Deleting 18745`.
   `.git/lfs` **32 013 MB → 83 MB**. The dry-run had predicted 4 291 retained /
   12 GB freed; deleting the six refs *first* dropped retention to 688, so the
   real figure is **31.9 GB**.
3. `git gc --prune=now` → single pack, `.git/objects/pack` **26 565 → 4 621 MB**.

### 6.4 Verification, against the pre-cleanup baseline

| check | baseline | after |
|---|---|---|
| `git fsck --connectivity-only` | 0 errors, 473 dangling | **0 errors, 0 dangling** |
| branches | 4 | 4, tips unchanged |
| commits | — | main 41 · b306-bringup 208 · fusion-uart-link 161 · wand-internal-sweep 160 |
| tags | 2 | 2 — `freeze-4piece-20260715`→`642e4a335`, `freeze-clean-20260716`→`8b68ee0aa` |
| `origin/*` mirrors | 7 | 7 |
| `refs/codex/*` checkpoints | 6 | 6 |
| worktrees | 3 | 3, all HEADs `cat-file -t` = commit |
| `git lfs fsck` | — | **OK**, 909 worktree LFS files intact |

**No history was rewritten.** `gc` removed only objects unreachable from every
ref, reflog and worktree HEAD; `dangling` going 473 → 0 is precisely that.

### 6.5 A method error worth recording

The wait loop `while pgrep -f "git gc|git repack|git pack-objects"` **matched its
own command line** and spun long after `gc` had finished at 13:38. Same class as
the known `pkill -f <script>` self-kill. Use `[g]it gc` bracket form or
`pgrep -x`. Two status reports of "still running" were wrong because of it.

## 7. Files in this evidence root

`relocate_verified.sh` · `resume_verified.sh` · `merge_relay8_3.sh` ·
`relocation_execution.log` · `resume_execution.log` · `merge_execution.log` ·
`PRE_COPY_LIST.txt` · `SPACE_BEFORE.txt` · `SPACE_AFTER.txt` ·
per-batch `*.source.sha256` / `*.destination.sha256` ·
`relay8_3_20260802.archive_{before,after}.sha256`.

# L2 — relocation manifest

**Batch:** `log_relocation_20260807` · **2026-08-07 10:52–11:20 (Europe/Berlin)**
**Destination root:** `/mnt/DatenBankHDD/BioSpur_Archive/` — the convention established by
`log_relocation_20260805`, mirroring the repository tree.
Offline only. No hardware, no commands, no J-Link, no flashing, no run.

---

## 0. Headline

**7 batches relocated and verified, 11.68 GiB freed, 0 broken symlinks, 0 files lost.**

**Three batches were NOT duplicates and were left alone.** `relay8_3_20260802`,
`dkv26_leds_20260730` and `batchE3_20260729` already had a directory of the same name on the HDD —
but a full-tree SHA-256 comparison shows the two copies **diverge, in different directions**:

| Batch | SSD files | HDD files | Which is the superset |
|---|--:|--:|---|
| `relay8_3_20260802` | 635 | 118 | **SSD** |
| `dkv26_leds_20260730` | 3 | 74 | **HDD** |
| `batchE3_20260729` | 68 | 151 | **HDD** |

Both copies of each are kept and nothing was written. **Had these been treated as duplicates and
resolved by either reflex — overwrite the archive, or delete the SSD source — evidence would have
been destroyed in three different directions.** This needs a human decision and is the main thing to
take away from this batch. See §4.

## 1. Inventory

| | Before | After |
|---|---|---|
| **SSD** `/dev/sdd1` ext4 on `/mnt/nrf_ssd` | 458 G total, 265 G used, **170 G free** (61 %) | 253 G used, **182 G free** (59 %) |
| **HDD** `/dev/sda1` ext4 on `/mnt/DatenBankHDD` | 1.8 T total, 352 G used, **1.4 T free** (21 %) | 364 G used, **1.4 T free** (21 %) |

HDD **mounted, ext4, rw, writable** — all four checks made in the script's preflight, which refuses
to run otherwise rather than improvise a destination.

**SSD free, exact: 181,968,695,296 → 194,506,960,896 bytes. Freed 12,538,261,504 (11.68 GiB).**
That reconciles with the 12,538,605,568 bytes of relocated batch content to within 344,064 bytes —
the directory inodes replaced by symlinks.

### 1.1 `.git` — the larger separate opportunity

```
/mnt/nrf_ssd/nRF_dev/.git = 62,720,226,973 bytes = 58.41 GiB
```

**Still by far the largest single item — five times what this whole batch moved, and larger than
both `logs/` trees combined.** Out of scope and untouched, as instructed; it needs its own
authorization. (Measured 58.41 GiB against the 58.53 GiB previously recorded.)

### 1.2 Open file handles

At planning time, two batch directories were held open:

* `UWB_Part/logs/deploy_20260805/B5_run_console.log` — `tail` **PID 2809526**, a stale `tail -f`
  left by an earlier session's monitor.
* `B306_Part/logs/v43_selfcapture_20260807/B5_RUN/events.jsonl` — my own persistent run monitor.
  The run finished at 08:12, so I **stopped it**; that handle is released. The batch stays on the
  SSD anyway under the two-most-recent rule.

The handle on `deploy_20260805` belongs to a process from a previous session, so it was left alone —
killing an unidentified process to reclaim disk is not a trade worth making. §2 says anything with an
open handle stays, and it stays.

## 2. What moved — decided and recorded before any copy

The selection was written to `PRE_COPY_LIST.txt` **before** the first byte was copied.

**Kept on the SSD by rule:** the two most recent completed batches
(`spacing_default_20260807`, `v43_selfcapture_20260807`), anything with an open handle
(`deploy_20260805`), and this batch's own evidence root. The canonical **v43** and **dk-v36** build
artifacts live under `B306_Part/builds/`, not under `logs/`, so they were never in scope — confirmed,
not assumed.

**Floor:** batches under 1 MiB were left in place. They total a few megabytes against 11.68 GiB, and
every relocation is an operation performed on irreplaceable evidence. Recorded as a deliberate
choice rather than an oversight.

## 3. Relocated — copy, verify, then delete

Every PASS below means, in order: `cp -a` to a staging directory on the HDD; full-tree SHA-256
manifest of **every file** in the source and in the staging copy, compared with `cmp`; every
sha256sum-format evidence index re-verified **at the destination**; promotion of staging to the final
path; the final destination **re-hashed and compared again**; and only then the SSD source deleted
and replaced with an absolute symlink whose `realpath` was checked.

| Batch | Source → Destination | Bytes | Full-tree | Evidence index |
|---|---|--:|---|---|
| `deploy_20260806` | `UWB_Part/logs/` → `BioSpur_Archive/UWB_Part/logs/` | 11,181,711,360 | PASS, 238 files | PASS: 16 verified, 15 external skipped |
| `buffer_return_forensics_20260805` | `UWB_Part/logs/` → same | 929,443,840 | PASS, 8 files | none present |
| `onset_split_20260806` | `UWB_Part/logs/` → same | 192,454,656 | PASS, 23 files | PASS: 22 verified; `SOURCES_SHA256.txt` all-external |
| `b306_v36_20260804` | `B306_Part/logs/` → `BioSpur_Archive/B306_Part/logs/` | 117,915,648 | PASS, 133 files | PASS: 4 verified, 19 external skipped |
| `b306_v37_20260805` | `B306_Part/logs/` → same | 69,816,320 | PASS, 138 files | none present |
| `aa61_stall_read_20260805` | `UWB_Part/logs/` → same | 40,706,048 | PASS, 10 files | PASS: 8 verified, 3 external skipped |
| `tx_pool_forensics_20260805` | `UWB_Part/logs/` → same | 6,557,696 | PASS, 3 files | none present |

**Total relocated: 12,538,605,568 bytes (11.68 GiB) across 7 batches, 553 files.**

Batches without an evidence index are still fully covered: the full-tree comparison hashes **every
file**, which is the standard §3 asks for when an index does not cover them.

### 3.1 Two script corrections made during the run

**`aa61_stall_read_20260805` failed its first attempt on a false positive.** Its index references
`../../../B306_Part/tools/confirm_b306_v32.py` — a provenance entry naming a file outside the batch.
The classifier inherited from the 20260805 script recognised absolute paths and `UWB_Part/…` /
`B306_Part/…` prefixes as external, but not a **relative escape**, so it looked for the file inside
the batch and reported it missing. Fixed by treating `../*` as external; the batch then passed with
8 verified and 3 correctly skipped. The failure was conservative — it kept the source — which is the
right way for that bug to land.

**One structural change from the 20260805 script**, worth stating because getting it wrong is silent:
each batch runs in its own `set -e` **subshell** rather than `relocate_one || handle`. Bash disables
`errexit` inside a function invoked in a condition context, which would have defused every internal
assertion — including the `cmp` that guards the delete.

## 4. Not relocated, and why

| Batch | Bytes | Reason |
|---|--:|---|
| `B306_Part/logs/v43_selfcapture_20260807` | 33,970,806,148 (31.64 GiB) | one of the two most recent completed batches |
| `UWB_Part/logs/deploy_20260805` | 30,570,543,672 (28.47 GiB) | **open handle** — `tail` PID 2809526 from an earlier session |
| `UWB_Part/logs/relay8_3_20260802` | 1,927,111,304 | **divergent archive copy — both kept** |
| `UWB_Part/logs/dkv26_leds_20260730` | 61,291,220 | **divergent archive copy — both kept** |
| `UWB_Part/logs/batchE3_20260729` | 3,660,236 | **divergent archive copy — both kept** |
| `B306_Part/logs/spacing_default_20260807` | small | most recent completed batch |
| batches < 1 MiB | few MB total | size floor, §2 |

### 4.1 The three divergent copies — needs a decision

Each already had a same-named directory in the archive. The script's `[[ ! -e "$dest" ]]` guard
refused them, which is the correct reflex: overwriting an archive copy is precisely what must never
happen silently. Rather than assume they were duplicates, each was held to the same standard as a
fresh copy — a full-tree SHA-256 comparison of every file, SSD against HDD. **All three diverge.**

`relay8_3_20260802` is the sharpest case: the **SSD** holds 635 files including a long series of
`ADDENDUM_*.md`, `F1B_*`, `F2_*`, `F3_*` and `FIX*` reports that the 118-file archive copy does not.
The other two run the opposite way — the archive holds a full `acceptance_300s` listener capture and
a much larger `analysis/` tree that the SSD directories lack.

The plausible story is an earlier relocation that archived these and a later session that re-created
the SSD path, or vice versa. **That is a guess, and merging on a guess would be irreversible.**
Recorded as **`INSUFFICIENT`**; both copies are intact and byte-identical to what they were before
this batch started. Per-file manifests for all three are in this evidence root
(`<tag>.source.sha256`, `<tag>.destination.sha256`) so the reconciliation can be done exactly.

**The largest single reclaim available is still `deploy_20260805` at 28.47 GiB**, blocked only by a
stale `tail -f`. Killing PID 2809526 releases it.

## 5. Pointers

**126 symlinks** across the two `logs/` trees, **0 broken** (`find -xtype l` returns nothing) — 119
pre-existing plus the 7 created here. Every new one is absolute and points into
`/mnt/DatenBankHDD/BioSpur_Archive/`, and `realpath` was asserted equal to the destination at
creation.

Read-through was confirmed on a **real file behind each new symlink**, not just on the link:

```
deploy_20260806                    -> build_dk_v34.sh              2,520 B
buffer_return_forensics_20260805   -> STATUS_NOTE.md               1,887 B
onset_split_20260806               -> d1_queue_depth_1hz.py        4,424 B
aa61_stall_read_20260805           -> EVIDENCE_SHA256.txt          1,077 B
tx_pool_forensics_20260805         -> TX_POOL_FORENSICS.md         8,522 B
b306_v36_20260804                  -> build_carrier_BSF31CC.log   27,447 B
b306_v37_20260805                  -> build_carrier_BSFB165.log   27,329 B
```

Existing reports that reference these batches by path continue to resolve unchanged.

## 6. Integrity statement

**No raw evidence was modified.** Every relocated file was copied with `cp -a`, hashed at source and
at destination, compared, and only then removed from the SSD. Nothing was deleted before verifying,
and nothing was verified by size or timestamp. No relocation temporary directories remain (one
leftover from the failed `aa61` attempt was removed before the retry).

Per-batch manifests: `<tag>.source.sha256` and `<tag>.destination.sha256`.
Full execution log: `relocation_execution.log`.

# LFS History-Rewrite Audit Report — 2026-07-16

**Operation:** Purge ~21 GB of overnight-capture logs from all Git LFS history to recover the GitHub LFS quota, without altering the frozen firmware baseline.

**Bottom line:** ✅ The freeze baseline `freeze-4piece-20260715` is **bit-for-bit identical** to its pre-rewrite state — every firmware file, source file, and generated OTA artifact has the **same blob hash**. The only thing removed anywhere in history is raw log `*.csv`/`*.log` files. No commits were dropped. Multiple independent recovery paths remain intact.

---

## 1. Why this was done

GitHub LFS quota (~10 GB) was exhausted. Root cause: `.gitattributes` tracked the entire `broadcast/logs/**` tree in LFS, so every overnight run pushed its raw captures. HEAD alone held **21.1 GB** (16.2 GB csv + 5.3 GB log, 4203 files). Top offenders: `overnight_soak_v2` (7.0 GB), `roto_sar_overnight` (6.3 GB), `overnight_soak` (4.6 GB), `overnight_power` (2.2 GB).

---

## 2. CRITICAL PROOF — the freeze baseline did not change

The freeze commit was rewritten (`cb8603316` → `642e4a33`). The following proves the rewrite **only deleted logs**:

| Check | Result |
|---|---|
| `git diff cb8603316 642e4a33` total files changed | **25271** |
| …of which are deletions (`D`) | **25271 (100%)** |
| …of which are additions/modifications | **0** |
| Changed paths that are **not** a `logs/**` csv/log | **0** |
| Non-log files in old freeze tree | 122820 |
| Non-log files in new freeze tree | 122820 |
| **Non-log blob-hash differences (byte level)** | **0** |

### Firmware artifact blob-hash identity (the bytes that actually get flashed)

| File | old blob | new blob | |
|---|---|---|---|
| `apps/master_ota/generated/active_ota_payload.json` | `58b5e51ab4` | `58b5e51ab4` | ✅ |
| `apps/master_ota/generated/ota_image.inc` | `5b68a9afad` | `5b68a9afad` | ✅ |
| `apps/master_ota/generated/tag_ota_manifest.h` | `30576bbffe` | `30576bbffe` | ✅ |
| `apps/master_ota/generated/tag_ota_manifest.json` | `b8d2c70088` | `b8d2c70088` | ✅ |
| `src/ss_twr_init.c` | `c5ac92936d` | `c5ac92936d` | ✅ |
| `apps/tag/src/uwb_tag_ble.c` | `4f44a4413a` | `4f44a4413a` | ✅ |

**The annotated tag's message and tagger are preserved unchanged** (same tagger `Biergarten23333`, same timestamp `1784137779`, same body: *"Verified-pass 4-piece firmware freeze (V1). ge7 0.978/ge8 0.934/valid% 97.3, 3 tags + 8 anchors + both B120 masters…"*).

**To retrieve the exact frozen firmware today:** `git checkout freeze-4piece-20260715` — the result is byte-identical to what it was before this operation (logs are not firmware).

---

## 3. Hash mapping (old → new)

All commit SHAs changed because rewriting history re-hashes every affected commit. **Content is identical minus logs.**

| Ref | Old SHA | New SHA |
|---|---|---|
| `main` | `ca1727a4b` | `69d759f87` |
| `feature/wand-internal-sweep` | `cb8603316` | `439ca70af` |
| `feature/loop-test-link` | `bc5f8ba37` | `626398ba5` |
| `feature/serial-role-switch` | `f6c778086` | `e4e61cdbb` |
| `codex/fix-mcumgr-ble-dfu-exposure` | `676ee694f` | `5eae2187e` |
| tag `freeze-4piece-20260715` (commit) | `cb8603316` | `642e4a33` |
| tag `freeze-4piece-20260715` (annotated object) | `369eb7719` | `7b179e7d7` |

Commit counts preserved: old `feature` = **144** commits, new `feature` = **144** commits (no commits dropped; `filter-branch` ran without `--prune-empty`).

---

## 4. GitHub remote — current state (verified)

```
69d759f8…  refs/heads/main
439ca70a…  refs/heads/feature/wand-internal-sweep
626398ba…  refs/heads/feature/loop-test-link
e4e61cdb…  refs/heads/feature/serial-role-switch
5eae2187…  refs/heads/codex/fix-mcumgr-ble-dfu-exposure
7b179e7d…  refs/tags/freeze-4piece-20260715
```
Log `*.csv`/`*.log` referenced across **all** pushed refs = **0** (confirmed by fetching the server refs back and inspecting the trees).

### Quota reclaim timeline
Rewriting history + force-push makes the ~21 GB of LFS objects **unreferenced** — it does **not** free server storage instantly. Options:
- **Automatic (chosen): ~30-day GitHub GC** of unreferenced objects.
- **Immediate:** open a GitHub Support ticket to purge, or delete/recreate the repo.
- ⚠️ If `codex/fix-mcumgr-ble-dfu-exposure` has an **open PR**, GitHub's `refs/pull/*` may keep some objects referenced until that PR is closed/merged — close it to let GC proceed.

---

## 5. Raw data — fully preserved on disk

The 21 GB of raw captures are intact locally (now git-ignored, kept local-only):

- `broadcast/logs` total: **28 GB**, **21744** csv/log files on disk.
- Verified present: `overnight_soak_v2_20260704_032348` (6.6 GB), `overnight_power_20260714` (3.2 GB), etc.
- New policy going forward: raw `*.csv`/`*.log` under `**/broadcast/logs/**` are **git-ignored** (`.gitignore`) and the `logs/** filter=lfs` rule was removed from `.gitattributes`. Small results (`summary.json`, `*.png`, analysis artifacts) still commit to normal Git.

---

## 6. Recovery nets (each independently restores the pre-rewrite state)

| Net | Location / SHA | Contains old freeze `cb8603316`? |
|---|---|---|
| Local branch `pre-lfs-rewrite-local-backup` | `3edf4bbd2` | ✅ yes |
| Local branch `backup-before-6b-amend` | `717cb3727` | ✅ yes |
| Isolated rewrite mirror clone | `/mnt/nrf_ssd/nRF_dev_lfs_rewrite.git` (28 GB) | — (holds rewritten refs) |
| Rollback script (pre-rewrite origin SHAs) | scratch `ROLLBACK_origin_prerewrite.sh` | — |
| Old origin tips still in object store | `ca1727a4b`, `cb860331656`, `bc5f8ba374`, `f6c778086`, `676ee694f`, `369eb7719` — all present | — |

**To fully undo everything** (restore GitHub to exactly the pre-rewrite state), from the working repo:
```bash
git push --force origin ca1727a4b:refs/heads/main
git push --force origin cb860331656:refs/heads/feature/wand-internal-sweep
git push --force origin bc5f8ba374:refs/heads/feature/loop-test-link
git push --force origin f6c778086:refs/heads/feature/serial-role-switch
git push --force origin 676ee694f:refs/heads/codex/fix-mcumgr-ble-dfu-exposure
git push --force origin 369eb7719:refs/tags/freeze-4piece-20260715
```
(The old freeze firmware is also always retrievable locally: `git checkout backup-before-6b-amend` then navigate to `cb8603316`.)

---

## 7. Action items for you

1. **Any other clone / CI / collaborator must re-clone (or hard-reset).** All branches + the freeze tag were force-updated; a plain `git pull` will not work and can reintroduce the logs.
2. **Close the codex PR** (if open) so GitHub GC can free that branch's share of the quota.
3. **Local disk cleanup (optional, ~60 GB, not yet done):** `git lfs prune` + `git gc` + delete the rewrite clone + delete `backup/*` branches. This reclaims the local `.git/lfs/objects` (32 GB) and the mirror clone (28 GB). It does **not** touch the on-disk log files (they are now independent real files). Recommend doing this **only after you have independently verified the baseline is good.**
4. **Separate issue, not addressed:** a ~26 GB non-LFS Git pack from directly-committed large files (`legacy_logs/`, a 59 MB json). This bloats fresh-clone size but does **not** consume LFS quota.

---

## 8. Re-verify it yourself

```bash
cd /mnt/nrf_ssd/nRF_dev

# Prove the freeze changed nothing but logs (expect: 0 non-log changes):
git diff --name-only cb8603316 642e4a33 | grep -vcE '/logs/.*\.(csv|log)$'

# Prove every non-log freeze file is blob-identical (expect: 0):
diff <(git ls-tree -r cb8603316 | grep -vE '/logs/.*\.(csv|log)$') \
     <(git ls-tree -r 642e4a33 | grep -vE '/logs/.*\.(csv|log)$') | grep -c '^[<>]'

# Prove no logs remain referenced on GitHub (expect: 0):
git ls-tree -r origin/main | grep -cE '/logs/.*\.(csv|log)$'

# Confirm the frozen firmware artifacts are intact:
git show freeze-4piece-20260715:BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/apps/master_ota/generated/tag_ota_manifest.json | head
```

---

*Generated 2026-07-16. Working repo `feature/wand-internal-sweep` = `origin` (ahead/behind 0/0); local WIP (`run_overnight_power.py`) preserved throughout.*

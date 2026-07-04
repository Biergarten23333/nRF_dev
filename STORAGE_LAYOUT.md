# Repository Storage Layout

Physical storage of this repository as of 2026-07-04 (drive consolidation).

## Live repository (active working copy)
- Canonical path: `/home/zekaixiao/Documents/nRF_dev` — this is a **symlink** to
  `/mnt/nrf_ssd/nRF_dev`.
- Physical drive: **Samsung SSD 850 EVO 500GB** (`/dev/sdd1`, ext4 label `NRF_SSD`,
  mounted at `/mnt/nrf_ssd` via fstab by UUID).
- `.git` is a **self-contained real directory** on this SSD (working tree + object DB
  together). There is **no** external `gitdir:` redirect anymore — the live repo has no
  dependency on any other drive.
- All hardcoded absolute paths `/home/zekaixiao/Documents/nRF_dev/...` resolve
  transparently through the symlink, so scripts and CMake/build caches keep working with
  no path edits and no rebuilds.

## Background
Previously the working tree lived on the 250 GB system SSD (`/`) and `.git` was redirected
via a `gitdir:` pointer to the 2 TB HDD to save space. On 2026-07-04 the whole repository
was consolidated onto the 500 GB SSD and the split `.git` was collapsed back into a normal,
self-contained repository.

## Backups (archival — not used by active work)
- **2 TB HDD** `/mnt/DatenBankHDD`:
  - `git-repos/nRF_dev.git` — archival copy of the git object DB (the former live git dir).
    **Kept** as a backup; the live repo does not point at it.
  - `nRF_dev/` — working-tree snapshot (its `.git` is a `gitdir:` pointer to the archival
    DB above, so that pair is self-consistent for recovery).
- **GitHub** `origin` — <https://github.com/Biergarten23333/nRF_dev.git> (LFS enabled).

## Toolchain (unchanged)
- NCS toolchain at `/home/zekaixiao/ncs` is **not** part of this repo and stayed on the
  250 GB system SSD.

# BioSpur HDD archive closeout

Date: 2026-07-30  
Mount: `/mnt/DatenBankHDD`  
Writable namespace: `/mnt/DatenBankHDD/BioSpur_Archive/`

## Read-only preflight

The first HDD action was read-only. `/dev/sda1` is `ext4`, capacity
1,967,845,998,592 bytes, with 1,748,336,873,472 bytes available before this
archive. The FAT32 4 GiB file limit does not apply.

Pre-existing top-level content was cataloged in
`/mnt/DatenBankHDD/EXISTING_CONTENT_INDEX.md`: `.Trash-1000`,
`Analysis_Archive` (16 GiB), the initially empty `BioSpur_Archive`,
`git-repos` (31 GiB), `lost+found`, `nRF_dev` (46 GiB), and the protected
`nRF_dev_cold_storage` (20 GiB). All are hands-off except the designated
archive namespace and the explicitly requested root-level catalog.

## Archive mechanism

`B306_Part/tools/archive_batch.sh` enforces:

- source below this workspace's `UWB_Part/logs`;
- destination below `BioSpur_Archive/UWB_Part/logs`;
- mounted-HDD and per-batch free-space checks with a 1 GiB reserve;
- no overwrite, delete, move, or rename of a pre-existing HDD destination;
- rsync to a new temporary destination, destination-side SHA-256 verification,
  symlink-inventory verification, and unresolved-link rejection;
- SSD replacement by symlink only after verification;
- idempotent re-run that verifies the existing destination without copying or
  duplicating it;
- one verified line per payload in `BioSpur_Archive/ARCHIVE_INDEX.md`.

The first LED payload was immediately run twice: the second run reported
`ARCHIVE_VERIFIED_IDEMPOTENT` and did not duplicate the data or index line.

## Archived content

The index contains 22 verified entries. It includes the accepted Batch E3 raw
tree, the complete cold-start raw/derived/report/deploy tree, the Golden
baseline, the Tag self-confirm batch, all other explicitly closed historical
measurement/build batches, and the DK-v26 deployment, failed 0-second
preflight, valid 300-second capture, token wait, post-capture clear evidence,
and final report.

The Golden baseline exposed a useful archive-gate defect on its first copy:
its relative links to `report`, `derived`, and deployment evidence had not yet
been mirrored. The SSD Golden directory was restored as an entity and left
untouched while those dependencies were archived. Its only subsequent hash
change was the authorized DK-v26 LED-page append to
`COLD_START_RUNBOOK.md`; the old and new SHA values are preserved in
`GOLDEN_DKV26_RUNBOOK_AMENDMENT.md`. The failed new HDD copy was removed,
the corrected copy was recreated, all links resolved, and all 150 entries in
the Golden dereferencing `SHA256SUMS.txt` passed before the SSD directory was
replaced by a symlink.

The archive currently occupies 4,763,948,782 logical bytes (4.5 GiB). The HDD
has 1,743,570,456,576 bytes available after the run.

## Build-intermediate cleanup

Before deletion, the exact list was printed:

| Intermediate tree | Logical bytes |
|---|---:|
| `B306_Part/builds/dk-fusion-imu-relay-v26-pass1` | 25,345,733 |
| `B306_Part/builds/dk-fusion-imu-relay-v26-pass2` | 25,345,693 |

The canonical v26 `zephyr.bin`, `zephyr.hex`, and `merged.hex` were first
copied to `B306_Part/artifacts/dk-fusion-imu-relay-v26/`. Their manifest
passed, pass1/pass2 hashes were identical, and a second verified artifact copy
was placed below `BioSpur_Archive/B306_Part/artifacts/`. Only then were the
two object/CMake trees deleted, freeing 50,691,426 logical bytes.

## Cleanliness proof

- Archived regular payload removed from SSD: 4,762,679,964 bytes net of the
  replacement symlink objects.
- Build intermediates removed: 50,691,426 bytes.
- Combined conservative logical SSD space freed: **4,813,371,390 bytes**.
- No broken SSD-to-archive symlink exists.
- A full post-run scan found zero new files outside `BioSpur_Archive`, except
  the explicitly requested `/mnt/DatenBankHDD/EXISTING_CONTENT_INDEX.md`.
- Four pre-existing sample files, one each from `Analysis_Archive`,
  `git-repos`, `nRF_dev`, and `nRF_dev_cold_storage`, retained byte-identical
  SHA-256 values.
- No J-Link flash/debug process was left running. The DK's passive CDC drain
  reader remains active so normal high-rate output does not intentionally
  relatch LED4 after the accepted capture.

`AGENTS.md` now makes the HDD namespace restriction, raw-evidence retention,
verified archive-then-symlink workflow, and accepted-batch automatic archive
permanent.

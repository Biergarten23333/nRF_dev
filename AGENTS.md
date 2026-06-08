# Agent notes — nRF_dev

## ⚠️ The `.git` directory is RELOCATED (read before any git operation)

This repo's git directory does **not** live in this folder. On 2026-06-07 it was
moved off the root SSD (it had grown to ~26 GB) onto the 1.8 TB HDD.

- **Working tree:** `/home/zekaixiao/Documents/nRF_dev`
- **Actual git dir:** `/mnt/DatenBankHDD/git-repos/nRF_dev.git`
- The `.git` in this folder is a **pointer file**, not a directory. Its contents:
  ```
  gitdir: /mnt/DatenBankHDD/git-repos/nRF_dev.git
  ```
- `core.worktree` is set inside the relocated config to point back here, so
  ordinary commands (`git status`, `git add`, `git commit`, `git push`,
  `git pull`) work normally from this directory — **as long as the HDD is mounted.**

### Before committing / pushing: make sure the HDD is mounted

All history and objects live on `/mnt/DatenBankHDD`. If that drive is not mounted,
every git command here will fail (e.g. "not a git repository" or a broken gitdir).

Check it first:
```bash
mountpoint -q /mnt/DatenBankHDD && echo "HDD mounted — git OK" || echo "HDD NOT mounted — mount it before any git command"
# sanity check that git resolves the relocated dir:
git rev-parse --git-dir   # -> /mnt/DatenBankHDD/git-repos/nRF_dev.git
```

If the drive is not mounted, mount it (then retry the git command):
```bash
sudo mount /dev/sda1 /mnt/DatenBankHDD
```

### Do NOT
- Do **not** delete or overwrite the `.git` pointer file in this folder.
- Do **not** run `git init` here — it would create a new, empty local `.git` and
  detach this working tree from its real history on the HDD.
- Do **not** assume `.git` is a normal directory; it is a one-line pointer file.

### If git ever stops recognizing this folder
Verify the pointer file still reads `gitdir: /mnt/DatenBankHDD/git-repos/nRF_dev.git`
and that the HDD is mounted. To recreate the pointer if it is lost:
```bash
printf 'gitdir: /mnt/DatenBankHDD/git-repos/nRF_dev.git\n' > /home/zekaixiao/Documents/nRF_dev/.git
git --git-dir=/mnt/DatenBankHDD/git-repos/nRF_dev.git config core.worktree /home/zekaixiao/Documents/nRF_dev
```

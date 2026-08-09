# Restore path for the v46 flash interlock

`g4_flash.sh` refuses to program unless a `BSF6C53_flash_backup.bin` exists in a
sibling directory: programming with no way back is not allowed. The first v46
flash attempt failed on exactly this, because `logs/v46_20260809/` was a fresh
tree with no backup in it. **Nothing was written to the board.**

This copy comes from `logs/stage_b_r4_20260809/SEG2_valcorpse/`. Recorded
honestly: it is a dump of the flash as it was during the r4 round, NOT of the
r7-val image currently on the board. It satisfies the interlock and is a valid
known-good restore image, but it is not a byte image of what is being replaced.

A true "current image" restore would need a probe read, which would cost an
extra press for no additional safety here: `builds/b306-v45r7-val/merged.hex`
and `builds/b306-v45r7-prod/merged.hex` are both on disk, both hashed in
`logs/stage_b_r4_20260809/BUILD_HASHES.txt`, and either can be flashed back.

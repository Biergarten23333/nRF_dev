# Fleet OTA — ABORTED by operator. Root cause of the failures identified.

## Result at abort (BSFEC35 was mid-flight)

| board | txn rc | content check (`V45 GUARD`) | on v46r2? |
|---|---|---|---|
| BSF6C53 | 0 | answers | **YES** (earlier, single-board) |
| BSFAA61 | 0 | answers | **YES** |
| BSFC2CC | 0 | answers | **YES** |
| BSF8BC4 | **2** | **answers** | **YES — the tool reported failure and was WRONG** |
| BSF1120 | 2 | no | no |
| BSF31CC | 2 | no | no |
| BSF3C79 | 2 | no | no |
| BSF44AD | 2 | no | no |
| BSFB165 | 2 | no | no |
| BSFEC35 | — | — | aborted mid-flight |

**The content check earned its place immediately.** BSF8BC4 returned `rc=2` --
failure -- while demonstrably running v46r2. Any rollout trusting the
transaction's return code would have mis-reported that board. Only the content
check (`V45 GUARD`, a command that exists solely in v46r2) told the truth.

## ROOT CAUSE — a race in the confirmation step, not in OTA

**The transfer works.** Every failed board shows `hash=match` in the updater's
own verdict: the image arrived and verified. Four boards ended up correctly on
v46r2. The v43/v44 rollouts used this same machinery.

What fails is confirmation. `confirm_b306_v32.py:150`:

```python
if f"fw={B306_MARKER}" not in str(ping["text"]):
    raise SessionError(f"B306 marker mismatch: {ping['text']}")
```

It waits up to 180 s for the board to ANSWER, then checks the marker **exactly
once, with no retry**. Measured on BSF1120: updater finished 01:09:59, DK
restored 01:10:07, confirm queried 01:10:47 -- 48 s later -- and the board
answered while still on v44. The tool aborted on the spot.

That abort is fatal, because confirmation is the only thing preventing
rollback:

    image staged -> board reboots into it in test mode -> never confirmed
    -> MCUboot / the app's BOOT_CONFIRM timeout rolls it back to v44

`active=1 confirmed=0` on every failed board is exactly that state. A board
that reboots a few seconds slower than the one-shot check loses. BSF8BC4 won
the race after the tool had already given up -- which is why it is on v46r2
with `rc=2`.

### Why this surfaced now and not during v43/v44

The whole pipeline keys on the firmware marker, and `BSF_FW_MARKER` had NOT
changed across v46 -- it stayed `b306-imu-relay-v45`. Every marker check was
therefore comparing a string to itself and passing vacuously. Bumping it to
`b306-imu-relay-v46` made the pipeline perform a real comparison for the first
time, and the real comparison exposed a race that had been present all along,
masked.

### The fix (not applied -- session aborted)

Poll the marker until it changes or a deadline expires, instead of sampling it
once. A few lines in `confirm_b306_v32.py`. The 180 s bridge-ready wait already
exists; the marker check simply needs the same treatment.

## Rig state at abort

- batch killed; nothing running
- **SDK patch applied and verified**, `files=9` -- the shared install is not
  left reverted
- **Fusion Master DK restored on all 9 transactions** to
  `dk-fusion-imu-relay-v36`; the restore gate verified image and live DK marker
  every time
- no board bricked; boards not on v46r2 are running v44 and working
- BSF6C53 on v46r2, confirmed, guard armed, `unk_sreq=0`

## Process failure worth recording

The answer was in `confirm_b306_v32.py` from the start. Five stale constants
were found one failure at a time across several hours instead of by reading the
confirmation path end to end before starting. That is why this took a night
rather than an hour.

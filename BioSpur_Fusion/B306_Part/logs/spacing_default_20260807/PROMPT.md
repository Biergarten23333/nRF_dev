# S1 — make spacing impossible to lose

**Batch:** `spacing_default_20260807` · Evidence root: `B306_Part/logs/spacing_default_20260807/` ·
Copy this file to `<evidence root>/PROMPT.md` first.

**Offline only while the nine boards charge. No hardware beyond what a normal DK reflash needs, no
B306 OTA, no run.** BSF44AD is on v43, fully charged, with a fresh reboot budget — **do not disturb
it.**

**You are pre-authorized. Do not prompt for a decision.** Anything ambiguous and not covered:
record it as `INSUFFICIENT`, take the safest branch, continue, and put it in the report.

---

## 1. The trap this closes

**Flashing the Fusion Master DK wipes its runtime configuration.** Connection spacing returns to the
default `OFF / 7500`; the working value is `ON / 5000` with a positive generation.

Every single-board OTA flashes the DK to the updater image and then restores it to canonical —
**so both halves of that sequence wipe spacing, and even a one-board OTA costs a rebuild.**

**It does not fail loudly.** Boards still connect, still deliver, and the capture still runs. The
only symptom is that the connection schedule is wrong for the whole window — and a window has
already been invalidated exactly this way. **It fired twice last night alone**, and both times it was
caught by a human checking, not by anything structural.

## 2. Why `ON / 5000` is the right value, and why 5000 must not be hard-coded

The number is derived, not chosen:

```
spacing_us = connection_interval / connection_count = 50,000 µs / 10 = 5,000 µs
```

Ten connections at 5 ms each fill exactly one 50 ms connection interval — evenly spread, no overlap.
**That is the whole point of the feature.**

`OFF / 7500` looks like an unimplemented factory default with a leftover number attached, and
**nothing in the project's record shows `OFF` having an operational use.** Confirm that from source
before relying on it.

**But 5,000 is only correct for ten nodes at a 50 ms interval.** If the node count changes — the
20-node expansion is on the roadmap — or the connection interval changes, **5,000 becomes wrong in
exactly the same silent way**: no error, no failed connection, just a degraded schedule nobody
notices. **So derive it; do not write the literal.**

## 3. What to build — three layers, because each alone leaks

### 3.1 Firmware default (dk-v36)

Make the DK come up with spacing correct **by derivation, not by a literal**: compute it from the
configured connection interval and the intended connection count at startup, and apply it without
needing a command.

**First, check what actually depends on `OFF`.** The most likely case is the updater image, which may
simply not implement spacing at all — which is why a restore lands in the default state. **Report what
you find; if some path genuinely needs `OFF`, say so and do not force the default on that path.**

Add a startup assertion or a logged warning if the derived value and the configured connection count
are inconsistent, so a future node-count change surfaces at boot rather than in a ruined window.

This layer covers **every** path, including a human flashing the DK by hand.

### 3.2 Tooling — fold the rebuild into the restore step

**The DK restore step must itself rebuild spacing and confirm a positive generation.** Not a separate
call the caller has to remember — part of the restore, so a restore that leaves spacing wrong is
impossible to express.

This layer covers everything the tools do, including the updater/restore cycle inside each OTA
transaction.

### 3.3 Keep the pre-window assertion

**Do not remove it.** It is the only thing that has ever actually caught this, twice last night. The
other two layers make it a backstop instead of the sole defence, which is what it should have been
all along.

**Any one layer alone leaks:** a corrected default is bypassed by an old image, corrected tooling is
bypassed by a manual flash, and the assertion alone means it stays a matter of somebody checking.

## 4. Build and verify

Advance the DK marker rather than reusing dk-v35 — its hashes are published, and two byte sequences
under one marker is what retired v19. Add the matching confirm tool; leave `SUPERSEDED.txt` in the
old build dir and remove the stale one rather than leaving it as a trap.

Measure FLASH and RAM against dk-v35 and report the deltas. **DK RAM has been the tightest resource
in the system** — it was 67.67 % at dk-v33; report where it stands now.

Two pristine builds, byte-identical on the unsigned application. Gate against the frozen file; the
signed artifact is not byte-reproducible, so never verify by rebuilding.

**Verify on hardware, since the DK is reflashable and this is cheap:** flash it, and confirm spacing
comes up `ON / 5000 / generation positive` **without any command**. Then run one restore cycle and
confirm it is still correct afterwards. That second check is the one that matters — it is the exact
sequence that has been wiping it.

## 5. Do not touch anything else

**No B306 firmware change. No OTA. No run.** The nine boards are charging and BSF44AD is a
fully-charged v43 board with a fresh reboot budget that tonight's run will use.

**Do not enable `CONFIG_BT_CONN_TX_NOTIFY_WQ`**, resize buffers, alter flow control or change the RX
stack. The BT RX wedge mechanism is still not on record, and tonight is an exposure night on an
unchanged B306 image.

## 6. Deliverable

`SPACING_DEFAULT.md`: what depends on `OFF` if anything, the derivation and where it now lives, the
three layers as implemented, the hardware verification including the restore cycle, capacity
against dk-v35, and hashes.

Then **STOP.** Tonight's run is a separate batch: ten boards, unchanged v43, maximum exposure.

**End with a literal banner.**

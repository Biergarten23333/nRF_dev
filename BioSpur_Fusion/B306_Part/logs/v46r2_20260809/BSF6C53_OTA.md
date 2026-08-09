# Part 2 — OTA onto BSF6C53: BLOCKED AT PREFLIGHT, nothing transmitted

## Outcome

**The OTA did not run. The board was not touched.** BSF6C53 is still on
`b306-v46-val`, healthy, guard armed. No updater was flashed to any DK, no
payload was sent, and no board state changed.

## How far it got

1. Payload built and hashed: `b306-v46r2-val` signed image
   `fc1477e92834b860…`
2. **Updater built successfully** — `dk-ota-b306-v46r2-BSF6C53`,
   merged.hex `c50172318ed49300…`, using the documented recipe in
   `host/dk_ota/README.md`.
3. Artifact hash gate: **passed** on the second attempt. Both DK restore
   hashes (`v28_merged`, `v28_bin`) matched the tool's expected constants; the
   first failure was my own error, passing the app `.bin` where the gate wants
   `merged.hex`.
4. **Target preflight FAILED**, and this is the blocker:

```
ERROR: master marker mismatch:
FUSION_MASTER_STATUS marker=dk-fusion-imu-relay-v36 ...
expected: dk-fusion-imu-relay-v28
```

## Why I stopped instead of working around it

The mismatch itself is trivial — the tool's `--master-marker` default is v32-era
(`v28`) and the rig runs `v36`. Overriding it is one flag.

**What is not trivial is `--restore-build`.** The transaction flashes an updater
onto the OTA DK and then restores "the canonical DK image" from
`--restore-build`, whose default is `dk-fusion-imu-relay-v28` with pinned
SHAs. If the rig's DK should now carry v36, running with the default would
**downgrade a DK as a side effect of a B306 OTA** — and the tool would report
success, because the restore hashes it checks are the v28 ones it was told to
expect.

I cannot verify from here which image the OTA DK is supposed to end on. That
makes this a documented-procedure question, not a parameter to guess. The
project's own rule applies: read the instructional document first, never
reverse-engineer a hardware procedure by probing.

So this is reported as a finding rather than worked around.

## What is needed to unblock

One of:
- the correct `--master-marker` **and** `--restore-build` / restore SHAs for the
  current rig, or
- confirmation that `dk-fusion-imu-relay-v28` is still the canonical OTA-DK
  restore image despite the Fusion Master running v36.

Then the same command runs unchanged.

## Note on the DFU self-check

C4's question — "did BSF6C53's DFU path survive four SWD flashes?" — is
**still unanswered**. Nothing here tested it; the run stopped before any BLE
transfer. The other nine boards have never been SWD-touched, so this question
was always specific to BSF6C53 and remains open.

## A real finding from the updater build

The updater would not compile against the patched SDK:

```
zephyr/subsys/bluetooth/host/conn.c:96:2: error: "BSF v45: bsf_v45_trace.h is
not reachable from this translation unit..."
```

`~/ncs/v2.8.0` is a **shared** install. The R4 change that replaced
`__has_include` no-op fallbacks with hard `#error`s — correct, because a silent
no-op meant corpses that were empty — has the side effect that **any project
without `bsf_v45_trace.h` on its include path can no longer build against this
SDK.** The DK updater is exactly such a project.

Worked around cleanly: `sdk_patch.sh revert` → build updater → `apply` →
`verify ok files=9`. The updater is host tooling and correctly builds against a
pristine SDK. But this is now a standing constraint on the shared install and
belongs in the patch manager's documentation, not in one engineer's head.

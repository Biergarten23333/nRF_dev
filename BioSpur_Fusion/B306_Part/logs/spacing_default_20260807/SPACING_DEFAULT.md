# S1 — make spacing impossible to lose

**Batch:** `spacing_default_20260807` · Evidence root: `B306_Part/logs/spacing_default_20260807/`
**2026-08-07 09:50–10:40.** Offline plus DK reflashes only. **No B306 firmware change, no OTA, no
run.** BSF44AD was never addressed and nothing was written to it. It stopped advertising during the
DK verification window; **§6.1 is corrected — that is explained by charging, not a fault.**

---

## 0. Headline — the diagnosis was wrong, and the real one is worse

The trap was understood as *"flashing the DK wipes its runtime configuration."* **It does not.**

`B306_Part/host/fusion_master/prj.conf:22` has carried
`CONFIG_BT_CTLR_SDC_CENTRAL_ACL_EVENT_SPACING_DEFAULT=5000` all along, and the built dk-v35 `.config`
confirms it. **The DK's controller boots with the correct 5,000 µs already applied.** Then
`main.c:3294` ran

```c
err = spacing_apply(SPACING_MODE_OFF);   /* 7,500 us */
```

as part of Bluetooth bring-up. **The application was actively overwriting a correct controller
default with the wrong value on every single boot.** Nothing was lost; something was broken, on
purpose, by code that read as initialisation.

That explains every observation that "wiping" did not:

* it happens on **every** boot, not only after a flash;
* both halves of an OTA reset it, because both halves end in a boot of an image that does this;
* the 7,500 in `SPACING_OFF_US` matches the *upstream* Kconfig default (`nrf/…/Kconfig:181`) that
  this project had already overridden — the code was re-imposing a default the config had rejected.

The earlier confusion is traceable: the 7500 seen in a `.config` dump during the BT-RX audit was the
**B306's** config, a different project. The DK's has been 5000.

## 1. What depends on `OFF` — nothing

Checked from source before relying on it:

| Question | Answer |
|---|---|
| Does the updater (`dk_ota`) implement spacing? | **No.** No reference to `spacing` or `acl_event_spacing` anywhere in `B306_Part/host/dk_ota/`. It never touches the VS command. |
| Does any tool or script send `SPACING OFF`? | **No.** Zero hits across `B306_Part/tools/`, the deploy scripts and the run drivers. |
| Is it documented as operational? | **No.** `docs/ble_protocol.md:283` calls it *"the explicit 7500 us baseline"* — a comparison mode. |

**Verdict: nothing depends on `OFF`.** The `SPACING OFF` command is retained so an operator can still
select the baseline deliberately, but it is no longer the boot state.

*Documentation defect found in passing:* the same line claims `SPACING ON` "applies 10000 us". The
code says 5,000 and has for some time. The doc is stale and is corrected in this batch.

## 2. The derivation, and where it now lives

```
spacing_us = connection_interval_us / connection_count = 50,000 / 10 = 5,000
```

Ten connections of one spacing each tile exactly one 50 ms interval — anchors at
`0, 5000, …, 45000`, no overlap, no hole. **That is the whole point of the feature**, and the failing
state shows it plainly: `OFF` puts anchors at `0, 7500, …, 67500`, so the last four land **beyond the
50,000 µs interval** and connections 7–10 collide with the next interval's 1–4.

`5,000` is now computed, never written (`host/fusion_master/src/main.c`):

```c
#define FUSION_CONN_INTERVAL_UNITS 40u                       /* 1.25 ms units */
#define FUSION_CONN_INTERVAL_US (FUSION_CONN_INTERVAL_UNITS * 1250u)
#define SPACING_ON_US (FUSION_CONN_INTERVAL_US / MAX_FUSION_PEERS)
```

Both `bt_le_conn_param` structs now use `FUSION_CONN_INTERVAL_UNITS` instead of a bare `40`, so the
interval the DK *requests* and the interval the spacing is *derived from* cannot drift apart. Two
build-time assertions carry the rest:

* `FUSION_CONN_INTERVAL_US % MAX_FUSION_PEERS == 0` — if the interval stops dividing evenly there is
  **no** correct spacing value, and the build says so rather than truncating.
* `CONFIG_BT_CTLR_SDC_CENTRAL_ACL_EVENT_SPACING_DEFAULT == SPACING_ON_US` — the controller default
  and the derivation are pinned together, so the 20-node expansion cannot fix one and forget the
  other.

The boot path also emits its own working:

```
FUSION_SPACING_DERIVED interval_units=40 interval_us=50000 peers=10 spacing_us=5000
                       kconfig_default_us=5000 source=boot_default
```

## 3. The three layers

### 3.1 Layer 1 — firmware default (dk-v36)

Boot applies the **derived** value. Applying it, rather than simply deleting the `OFF` call, is
deliberate: deleting it would leave the controller correct at 5,000 while `FUSION_SPACING` still
reported `OFF`. **That is worse than the bug being fixed — an instrument that lies.** Applying keeps
reported state and controller state in agreement.

Covers every path, including a human flashing the DK by hand.

### 3.2 Layer 2 — the rebuild is inside the restore

`B306_Part/tools/fusion_spacing.py` (`ensure_spacing`) plus a single `restore_master()` helper in
`v32_ota_board_transaction.py`. Both restore sites — the normal one and the emergency rollback in
`finally:` — now go through it, so **a restore that leaves spacing wrong cannot be expressed.**

Two properties that matter more than they look:

* **It asserts on STATE, not on a transition.** dk-v36 boots correct, so `SPACING ON` answers
  `UNCHANGED` and does *not* bump the generation. A check that required a generation *increase*
  would fail the correct image. The contract is mode `ON`, the derived microseconds, generation > 0.
* **A spacing failure never fails the transaction.** By the time the restore runs, the B306 image is
  written and confirmed. Refusing a real deployment over a schedule that one command fixes would
  trade a success for a cosmetic failure. It is recorded loudly instead.

### 3.3 Layer 3 — the pre-window assertion, untouched

`b_fusion_ops.py spacing_contract()` is **not** removed or weakened. It is the only layer that has
ever actually caught this, twice in one night. Layers 1 and 2 demote it from sole defence to backstop.

Pinned by `host/fusion_master/tests/test_spacing_derivation.py`, which asserts all three and fails if
the derivation becomes a literal, if a restore site goes back to a bare `flash()`, or if the
pre-window assertion disappears.

## 4. Hardware verification

Four tests on the real DK. **No B306 was written to; no updater image was flashed.**

| # | Test | Result |
|---|---|---|
| H2 | Flash dk-v36, then read state with **no command sent** | `mode=ON applied_us=5000 generation=1`, anchors `0…45000` — **correct out of the box** |
| H3 | One restore cycle through the new `restore_master()` | **PASS**, `action=none_already_correct` — Layer 1 had it right, Layer 2 verified and correctly did nothing |
| H4a | Flash **dk-v35** (the old image) and read it | `mode=OFF applied_us=7500`, anchors `0…67500` — the failure reproduced |
| H4b | Run the same restore path against that old image | **PASS**, `action=rebuilt`, `mode=ON applied_us=5000 generation=2` |

H4 is the one that proves the design rather than the implementation: **Layer 2 rescues a DK that
predates Layer 1**, which is precisely the "old image bypasses the corrected default" leak.

### 4.1 A bug the hardware run found

The first H3 attempt failed:

```
WARNING: spacing rebuild raised after restore restore:
         expected one BioSpur Fusion Master 2FE3:10F4, found []
```

The restore resets the DK over J-Link, so its USB CDC disappears and returns a few seconds later;
`ensure_spacing` resolved the port immediately and found nothing. The existing transaction tool hides
the same window behind a fixed `time.sleep(25)` before its confirm step. Fixed with a bounded poll
(`_resolve_with_retry`, 45 s) — faster when the device is quick, safer when it is slow — and the
resolve was moved inside the `try` so the helper honours its contract of returning a result dict
rather than raising.

**This would not have been found offline.** It is the entire reason §4 of the brief asks for the
restore cycle specifically.

## 5. Capacity and hashes

| | dk-v35 | **dk-v36** | Δ |
|---|--:|--:|--:|
| FLASH | 182,052 B (17.36 %) | **182,208 B (17.38 %)** | **+156 B** |
| RAM | 177,392 B (67.67 %) | **177,392 B (67.67 %)** | **0** |

**DK RAM is unchanged** — still 67.67 %, exactly where it stood at dk-v33. The tightest resource in
the system did not move.

| Artifact | SHA-256 | |
|---|---|---|
| dk-v36 unsigned app | `59bd57b80d762f5c3d9af9b0d0d303d288584f6f06f5baf5349a3cf3c5628b47` | reproducible |
| dk-v36 merged | `7a7d02cdae13b4450ffea0cb2a46607d481f3760a95e6c38d4c9dd03a2290b56` | reproducible |

Two pristine builds agree on the unsigned application. Verification is against the **frozen file**,
never a rebuild.

**Marker hygiene:** advanced to `dk-fusion-imu-relay-v36` rather than reusing v35, whose hashes are
published — two byte sequences under one marker is what retired v19. `SUPERSEDED.txt` written into
both dk-v35 build directories. There is no separate DK confirm tool to retire: the DK marker is
pinned *inside* `confirm_b306_v43.py`, which has been repinned to v36 — **required**, or every future
B306 confirmation fails on a Master marker mismatch.

## 6. State as left

* **Fusion Master: dk-v36**, spacing `ON / 5000 / generation 1`, applied at boot with no command.
* **BSF44AD: not advertising as of 10:50 — needs a physical check before tonight. See §6.1.**
  Nothing was written to it and it was never addressed.
* Nine boards still charging.
* **No B306 firmware change. No OTA. No run.** `CONFIG_BT_CONN_TX_NOTIFY_WQ` still `n`, buffers, flow
  control and RX stack all unchanged — tonight is an exposure night on an unchanged B306 image.

### 6.1 BSF44AD stopped advertising — CORRECTED: explained by charging

**This section originally called for a physical check and listed a possible BT RX wedge. That was
wrong, and the correction comes from hardware knowledge I did not have.**

**Charging pulls the AP2112K's `EN` low, which shuts the module down.** A board on the charging POGO
is not powered — it is off. So:

* **`count=0` and "BSF44AD is not advertising" carry no information about its health.** It answered
  PING on v43 at 10:05 with `up_ms=295292`; it was subsequently put on charge, and everything after
  that is the expected behaviour of a powered-down board. **No fault is indicated.** The three
  possibilities originally listed here (battery / BT RX wedge / physical) were all speculation built
  on a false premise.
* The five DK reflashes are likewise exonerated — they were never a plausible cause.

**The consequence that actually matters is the opposite one: `EN` low means the 3V3 rail drops,
which means `.noinit` is erased by every charge cycle.**

Two things follow, and the second is a standing operational rule:

1. **Reboot budgets self-clear.** §5.3's concern that BSFAA61 would enter the next run with a spent
   budget (`reboot_owner=2`, consumed by Stage 2's `CORPSE FORCE`) is **void** — one charge cycle
   resets it. Every board comes off the dock with a fresh budget, and no pre-run budget sweep is
   needed. This also removes the motivation for the "artificial trigger should refund the budget"
   change proposed in §5.3: on real hardware the budget is refunded by the charger.
2. **A corpse cannot survive charging.** The retained region is `.noinit`, which survives
   `sys_reboot()` and the watchdog but not a power removal — and charging *is* a power removal.
   So: **any board that wedges, self-resets and returns must have its corpse read before it next
   goes on the dock.** The run driver already polls `CORPSE STATUS` every 90 s and only clears on a
   positive ACK, so an in-run corpse is collected automatically; the exposure is a board that wedges
   near the end of a run and is docked before the sweep reaches it.

This also matches what was measured after the operator's full power cycle of BSF44AD earlier the same
morning: `RING boot=1 init=cold`, `reboot_owner=0` — the cold-boot path, exactly as a charge cycle
would produce.

## 7. Follow-up

The stale `SPACING ON … applies 10000 us` sentence in `docs/ble_protocol.md` is corrected in this
batch. If the fleet ever moves to 20 nodes, the two `BUILD_ASSERT`s and the derivation will force the
decision at compile time instead of silently halving the schedule — which is the entire point.

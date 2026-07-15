# Listener Firmware Freeze Audit — the tag-capture-switch listener

**Date:** 2026-07-15 · **Type:** read-only source audit (no build/flash). Every claim carries `file:line` relative to the broadcast tree root `SS-TWR/alt-SS-TWR/broadcast` unless a full path is given.
**Purpose:** identify + characterize the last-good LISTENER firmware (the "tag-capture switch" variant) as the **5th** frozen firmware piece, before the operator re-flashes the whole listener fleet to one common passive image.

## VERDICT — PASSIVE GUARANTEE: 🟢 GREEN (GO)

The identified listener image is **safe to run as a zero-interference passive gateway.** In its boot-default `MODE_LISTEN` it is **pure UWB-RX (never transmits)** and it has **no Bluetooth at all** (cannot compete for master BLE connections). The tag-capture (`MODE_TAG`) mode does transmit, but it is **default-OFF, RAM-only (resets to LISTEN on every boot), and enterable only by an explicit USB-CDC command** — it cannot be triggered by garbage or survive a power cycle. Details in Task 3.

---

## TASK 1 — Listener firmware inventory

| Path | Role | Modes (file:line) | Last change |
|---|---|---|---|
| **`UWB_listener/src/main.c`** (broadcast tree) | **UWB RX-only poll-diag listener + tag-capture; USB-CDC cmd iface** | MODE_LISTEN :964→:936 · MODE_IDLE :966→:920 · **MODE_TAG :962→:949** · MODE_QUERY :968. Enum :114-116; dispatch `listener_apply_command` :960; boot default LISTEN :152/:1063 | committed clean @ **631911c3e** (2026-07-11); self-heal @ 53fb4d006 (2026-07-02) |
| `/…/BioSpur_UWB_before_start/UWB_listener/src/main.c` (repo-root sibling) | **Geiger handheld probe** (button + VCOM) — NOT a fleet listener | MODE_SCAN :1331 · MODE_GEIGER :1333 · MODE_QUERY :1335; boot default GEIGER :187/:1294. **No MODE_LISTEN/TAG/IDLE** | 2026-07-11 (631911c3e) |
| `apps/ble_listener/src/main.c` | BLE active-scan dongle (nRF52840) — streams BADV/BSTAT; **has BLE** | none (DFU-touch@1200 only) | 2026-06-29 |
| `apps/b120_ble_probe/src/main.c` | Minimal B120 BLE probe | none | 2026-04-26 |
| `apps/b120_cdc_probe/src/main.c` | Minimal B120 USB-CDC probe (`status`/`ota version`) | none | 2026-04-26 |

**Only the broadcast-tree `UWB_listener` has the LISTEN/IDLE/TAG mode set.** The repo-root `UWB_listener` is the separate Geiger probe (SCAN/GEIGER) — a different device, not the passive fleet.

**Build sets** (all via `scripts/build_uwb_listener_poll_diag.sh <tag>`, board `decawave_dwm1001_dev/nrf52832`, source = broadcast `UWB_listener`):
- **`cir1_*` (2026-07-11 02:54) — NEWEST per-unit set, `CIR_CAPTURE_ENABLE=1`:** `cir1_LA / LE / LF / LCCF4 / L9336 / L955A`. ← the overnight-CIR fleet.
- **`tagmode_*` (2026-07-10 23:38 → 07-11 00:08) — immediate predecessor, same source, pre-CIR:** `tagmode_lb_test / lb_irq / LE / LF / LCCF4 / L9336 / L955A`, plus `la_pans_erase`.
- older diag builds (respdiag / selfheal / cirprobe / evc_ttcko / thermal, 2026-06-25 → 07-10).
- `apps/ble_listener` dongle builds (`build-52840-dongle-ble-listener-*`) — separate BLE dongle, not the UWB fleet.
- repo-root `build-uwb-listener-modescan* / -rollback / -ui-autofollow` — the Geiger probe app (root tree).

---

## TASK 2 — THE tag-capture-switch build

**Identified build:** the broadcast-tree **`UWB_listener` source committed at `631911c3e`**, built with `CIR_CAPTURE_ENABLE=1` = the **`cir1_*` per-unit set (2026-07-11)**. This is the last listener firmware flashed on the fleet (all 7 listeners reflashed `CIR_CAPTURE_ENABLE=1` on 2026-07-11 for the overnight CIR run; the `tagmode_*` set the same night was the pre-CIR predecessor with the identical MODE_TAG switch).

**The tag-capture switch = `MODE_TAG`.** How it works:
- **Invocation:** host sends the ASCII line `MODE_TAG\n` over the listener's USB-CDC / J-Link VCOM console → `listener_apply_command()` (:962) → `listener_enter_tag()` (:949). Companion commands `MODE_LISTEN` / `MODE_IDLE` / `MODE_QUERY` (:964/:966/:968).
- **What it does** (:800-916, comment :731-738): re-points the 16-bit radio address to `APP_LISTENER_TAG_ADDR` (0xB1C0, :792/:97) and becomes a minimal broadcast Alt-SS-TWR **tag** — TXes a 17-byte broadcast poll every 100 ms (:1074-1082, :106), collects the 8 anchors' responses in a 12 ms window, computes per-anchor SS-TWR ranges (:752-767), and emits one **`LTAG;src=0x<label>;a0=…;a7=…`** line per cycle (:770-782, no-response anchors = −1). Purpose: let the host multilaterate the **listener's own 3D position** (position self-calibration), then switch back to passive.
- **On-air identity nuance** (:89-95): the on-air source MUST be a tag address (0xB100-0xB1FF) or the anchors reject the poll; `APP_LISTENER_TAG_ADDR`=0xB1C0 is that on-air id, while `APP_LISTENER_TAG_LABEL`=0xC000 (:100) is only a host-facing logical id echoed in `MODE=`/`LTAG;src=`.

### Capture-log evidence (which build actually ran)
The recent capture runs — `overnight_radar_20260711`, `overnight_power_20260714`, and `overnight_power_position_high_20260715` — were run on the **`cir1_*` CIR-enabled fleet, booted passive**:
- Every listener capture dir contains `lcird.csv`/`lcire.csv`/`lcirm.csv` (~48 MB CIR per unit) ⇒ `CIR_CAPTURE_ENABLE=1` ⇒ a `cir1`-family build. Line-format version field = **1** (e.g. `LSTAT;1;…`, `LPD;1;255;255;…`).
- The listeners **heard** wand-tag polls `0xb102/0xb103/0xb104` (LPD `src`) answered by anchors `0xa101..0xa107` (LRD) — i.e. they were **passive observers, transmitting nothing** during the runs (empirical confirmation of the passive guarantee). Manifest: `logs/overnight_power_position_high_20260715/listener_manifest.json` (baud 460800, dur 4800 s, 2026-07-15 09:30).
- **Caveat (honest):** the listener firmware prints its ready banner (`main.c:1061`) only at boot, and captures attach mid-stream, so **there is no build/version string inside the capture logs and no standalone flash transcript** — `scripts/flash_uwb_listener_jlink.sh` echoes only to stdout. The build→unit binding below is **strong circumstantial** (build `.source` stamps 2026-07-11 + the per-unit address map + CIR present + commit `631911c3e` message "the reflashed listener fleet"), not a recorded flash log.

**Authoritative per-unit address map** — `UWB_listener/CMakeLists.txt:26-33`, matched to each build's compiled `APP_LISTENER_TAG_ADDR` (verified in `CMakeCache.txt`):

| Unit | J-Link SNR | Flashed build (best evidence) | tag_addr |
|---|---|---|---|
| LE | 760184767 | `cir1_LE_20260711` | 0xB1C2 |
| LF | 760184964 | `cir1_LF_20260711` | 0xB1C3 |
| LA | 760184753 | `cir1_LA_20260711` | 0xB1C4 |
| LCCF4 | 760184784 | `cir1_LCCF4_20260711` | 0xB1C5 |
| L9336 | 760186071 | `cir1_L9336_20260711` | 0xB1C6 |
| L955A | 760186081 | `cir1_L955A_20260711` | 0xB1C7 |
| **LB** | 760184545 | **no `cir1_LB` exists** → `tagmode_lb_irq_20260710` (CIR=1, 0xB1C1) | 0xB1C1 |

Two footnotes: (1) the `tagmode_*` set is a **separate CIR-OFF** variant used for the position-calibration / "A+B" work (`logs/listener_calibration/calibrate_listener_positions.py` — "Phase 2: per-listener `MODE_TAG`, collect ranges ~30 s, multilaterate, `MODE_IDLE`; Phase 3b: `MODE_LISTEN`, verify CIR resumes"), except the LB dev builds which have CIR=1. (2) The **repurposed BSF66F tag** could not be pinned to a specific unit — the fleet is built from retired-tag boards (LB=ex-BS1396, LF=ex-BS7724 per `FREEZE_LISTENER_SELFHEAL_20260702.md`); LA or LCCF4 (the two added after the 2026-07-02 5-listener set) is the likeliest BSF66F host, but that is a guess, not documented.

**Flash-script footgun:** `scripts/flash_uwb_listener_jlink.sh` defaults `BIOSPUR_LISTENER_SN=760185886` — the **OFF-LIMITS legacy Geiger air-monitor** — and a non-existent default hex; real use MUST pass an explicit hex + `-SelectEmuBySN` override (pattern in `FREEZE_LISTENER_SELFHEAL_20260702.md:70-73`).

---

## TASK 3 — Characterization

### Modes (all RAM-only; **no NVS** — every boot = LISTEN)
| Mode | Enum | Enter | Behavior |
|---|---|---|---|
| **LISTEN** (boot default) | :114 | :936-947 (`MODE_LISTEN`) | **Passive RX-only** CIR/poll/response capture. Reconfigures radio to exact boot state (addr 0xB1FE, frame-filter off, RX-only). |
| **IDLE** | :115 | :920-930 (`MODE_IDLE`) | `dwt_forcetrxoff()` — radio fully off, **no TX, no RX** (AutoPos-safe). |
| **TAG** | :116 | :949-956 (`MODE_TAG`) | **Active** broadcast tag: TXes polls, ranges the 8 anchors, emits LTAG. See Task 2. |

Boot: `main()` forces `listener_mode = LISTENER_MODE_LISTEN` (:1063) after init; the static initializer is also LISTEN (:152). No `settings_*` / NVS anywhere → **a power cycle always returns to passive LISTEN.**

### Output line grammar (USB-CDC, 460800 baud)
| Line | file:line | Gate (compile flag) | Meaning |
|---|---|---|---|
| `LPD;1;…` | :375 | `POLL_DIAG_ENABLE` (:31, dflt 1) | poll-path diagnostics — one per observed tag poll (rx_ts, CFO, fp_index, fp1-3, cir, rxpacc, stdnoise, AGC, …) |
| `LRD;1;…` | :410 | `RESP_DIAG_ENABLE` (:38, dflt 1) | per-anchor-response diagnostics — makes it a full passive TWR observer |
| `LCIRM;1;…` / `LCIRD;1;…` / `LCIRE;1;…` | :460 / :486 / :500 | `CIR_CAPTURE_ENABLE` (:42, dflt **0**; fleet builds set **1**) | CIR magnitude header / hex accumulator chunks (chunked `dwt_readaccdata`) / end marker |
| `LSTAT;1;…` | :701 | `STATUS_PRINT_ENABLE` (:58, dflt 1) | 5 s status: good_frames, fps, rx_errors, self_recover, ring_drops, HW event counters (12-bit, **diff successive snapshots** for overnight) |
| `LTAG;src=0x…;a0=…` | :770 | MODE_TAG only | per-cycle per-anchor ranges (position self-calibration) |
| `MODE=LISTEN\|IDLE\|TAG src=0x…` | :929/:946/:955/:972 | always | mode-switch / MODE_QUERY confirmation the host keys on |

### 🟢 PASSIVE GUARANTEE (the go/no-go) — CONFIRMED
- **(a) MODE_LISTEN is pure RX — no TX, no TDMA, no UWB airtime.** The LISTEN loop (:1090-1128) does only: read `SYS_STATUS`, `capture_to_ring` (RX), `dwt_rxreset`/re-arm RX, drain records to UART. There is **no `dwt_starttx`/`dwt_writetxdata` on the LISTEN or IDLE path** — TX exists only in `listener_tag_poll_once()` under MODE_TAG (:800-916). Header comment :37 "RX-only; never transmits". IDLE = `dwt_forcetrxoff()` (:922, "no TX, no RX: safe during anchor AutoPos ranging").
- **(b) No BLE contention — the firmware has NO Bluetooth at all.** `grep bt_enable|bt_le_adv|CONFIG_BT` in the source = **0 hits**; `prj.conf` has no `CONFIG_BT`. Control is USB-CDC/VCOM only, "**never an ISR, never BLE**" (:149). A master's scan can never see or grab it → zero BLE competition.
- **Result: in LISTEN (and IDLE) the listener is invisible on both the UWB TX side and the BLE side.** Safe as a zero-interference passive gateway. 🟢

### Tag-capture switch — footgun assessment (LOW)
- **Default-OFF:** boot is LISTEN (:1063); MODE_TAG is never auto-entered — the ONLY setter is `listener_enter_tag()` (:949), called ONLY from the `MODE_TAG` command (:963).
- **Can't be triggered by garbage:** unknown/partial/binary lines are silently ignored (:974); over-length lines are flushed (:1018).
- **Can't persist:** RAM-only, no NVS → any reboot/power-cycle returns to passive LISTEN.
- **Residual risk:** IF a host deliberately sends `MODE_TAG`, the unit TXes polls @10 Hz (real RF airtime that would perturb AutoPos / a live capture) until `MODE_LISTEN`/`MODE_IDLE` or a reboot. Mitigation is procedural (don't send it during captures) or a build-time gate (see Task 4).

### Baud
**460800** (comment :85; the fleet's VCOM baud). Host parsers MUST open at 460800 — the 115200-vs-460800 mismatch has bitten before. `prj.conf` sets `CONFIG_UART_CONSOLE=y` + `CONFIG_UART_INTERRUPT_DRIVEN=y`; IRQ-driven RX is required for the command interface (:157-162, legacy nRF 1-byte RX register).

### Silicon-POR / RX-register concern for NEW DWM1001C units
- The RX PHY config is **fully hardcoded and identical for every unit:** `listener_config` (channel/PRF64/PLEN128/PAC8/6M8/SFD129, :219-230) applied by `listener_radio_configure()` (:242-258), which also sets a **hardcoded antenna delay 16436** (:245-246, **not** per-unit factory OTP), frame-filter off, and enables the 12-bit HW event counters. `listener_radio_full_recover()` (:276-283) replays the boot bringup.
- **rxdiag/AGC/CIR comparability: OK.** Because the RX/AGC/channel config is compile-time-fixed and uniform, a NEW DWM1001C flashed with this image reads rxdiag (fp, rxpacc, stdnoise, AGC, CIR) on the same footing as the existing units.
- **One caveat:** the antenna delay is a generic 16436, not each chip's OTP value → a NEW unit's **MODE_TAG self-position** could carry a small (~cm) per-unit antenna-delay bias. This does **not** affect passive rxdiag/CIR/AGC (antenna delay is a timestamp offset only). Flag for the position-calibration workflow, not for passive listening.

---

## TASK 4 — Freeze recommendation

**Recommend freezing:** the broadcast-tree **`UWB_listener` source @ commit `631911c3e`**, built via `scripts/build_uwb_listener_poll_diag.sh` with `APP_LISTENER_CIR_CAPTURE_ENABLE=1` — i.e. the `cir1_*` configuration. Proposed name: **`listener-freeze-20260715`** (5th frozen piece).

**Freeze AS-IS (do NOT gate MODE_TAG out) — recommended.** The passive guarantee holds and MODE_TAG is a deliberate, default-OFF, RAM-only, command-only calibration tool with a genuine use (listener position self-calibration). Gating it out would remove a useful capability to defend against a footgun that a power-cycle already neutralizes. *If* the operator wants maximum safety for an unattended freeze-clean fleet, the cheap option is a compile gate (e.g. `APP_LISTENER_ENABLE_TAG_MODE`, default 0, guarding `listener_enter_tag()` at :963) — a one-line source edit, optional, not required for the passive guarantee.

**Single common image vs per-unit IDs (decide before flashing):** the fleet builds differ ONLY in compile defs `APP_LISTENER_ID` (echoed in every LPD/LRD/LSTAT line), `APP_LISTENER_NEAR_ANCHOR_ID`, and `APP_LISTENER_TAG_ADDR` (:30-36 of the build script). Options:
- **(A) One common image** (`APP_LISTENER_ID=255`/UNKNOWN, `TAG_ADDR=0xB1C0`, `CIR=1`) flashed to all units — maximum homogeneity, but every unit reports the same ID → the **host must key units by USB port**, and only **one** unit at a time may enter MODE_TAG (shared on-air 0xB1C0). Best if the data pipeline already keys by port.
- **(B) Per-unit images from the frozen recipe** — same firmware version/commit, but each unit keeps a distinct `APP_LISTENER_ID`/`TAG_ADDR` so the data self-identifies and multiple units can calibrate. Recommended if any consumer keys on the listener ID field.
- Recommendation: **(A) if the fleet runs purely passive and the host keys by port; (B) if listener-ID in the stream matters.** Either way it is one frozen source/commit.

**Relationship to the 4-piece freeze:** **INDEPENDENT addition.** The listener is a separate device (DWM1001C/nRF52832), has no BLE, and does not participate in the tag/anchor/master TDMA or BLE topology — so it cannot affect the frozen 4-piece behavior and needs no change to `FREEZE_4PIECE_20260715.md`. Add a **listener section to `BIOSPUR_USABLE_FIRMWARE_VERSIONS.md`** and (optionally) tag `listener-freeze-20260715` when the operator confirms the version + single-vs-per-unit choice.

**Blocking question for the operator (do not guess):** confirm the target = `cir1_*` config (CIR ON) vs a CIR-OFF passive-only image, and choice (A) vs (B) above. No build/flash until then.

---

## FLEET RE-FLASH EXECUTION — 2026-07-15 (USB J-Link, single common image)

**Image:** `listener-freeze-20260715` = commit **`631911c3e`** + `CIR_CAPTURE_ENABLE=1`, generic `APP_LISTENER_ID=255` (identity keyed by SNR/port at host, not compile-time), `APP_LISTENER_TAG_ADDR=0xB1C0`.
- build dir `build-uwb-listener-poll-diag-listener-freeze-20260715`; `zephyr.hex` sha256 **`c4cff12b082b38d62f951bf7…`**
- confirmed: `CIR_CAPTURE_ENABLE=1`, `CONFIG_BT=y` count **0** (no Bluetooth). Board `decawave_dwm1001_dev/nrf52832`.
- **This collapses the old seven per-unit `cir1_*` builds into one binary.** Same image on every unit; the fleet manifest keys identity by SNR/port.

**Flash method:** `scripts/flash_listener_freeze.sh <SNR> <hex>` — HARD SNR allowlist (the 9 units; **explicitly denies 760185886 盖格**), JLink **`recover`** (CTRL-AP mass-erase + APPROTECT clear → wipes any factory Decawave PANS) → `loadfile` → fresh-session 0x0 read → VCOM boot-banner check. All 8 successes read the identical vector `0x0 = 20003E60 00002841 …` and the banner `BioSpur co-located UWB listener start id=255 … cir=1 … tag_addr=0xb1c0` + `MODE=LISTEN` (not PANS).

### Fleet deployment map + per-unit result

| SNR | Position | Height | PANS-cleared (recover) | Image verified (banner/cir=1/MODE=LISTEN/not-PANS) | Result |
|---|---|---|---|---|---|
| 760184753 | A–E anchor-pair midpoint | — | ✅ | ✅ | **PASS** |
| 760184548 | B–F anchor-pair midpoint | — | ✅ | ✅ | **PASS** |
| 760181725 | C–G anchor-pair midpoint | — | ✅ | ✅ | **PASS** |
| 760184784 | D–H anchor-pair midpoint | — | ✅ | ✅ | **PASS** |
| 760184964 | old-room-triangle vertical | LOW | ✅ | ✅ | **PASS** |
| 760184767 | old-room-triangle vertical | MID | ✅ | ✅ | **PASS** |
| 760184545 | old-room-triangle vertical | HIGH ~2.3 m | ✅ | ✅ | **PASS** |
| 760181879 | **AEDH face, between E–H, UPPER** anchor-layer height (EFGH=upper layer) | upper | ✅ (see note) | ✅ boots `MODE=LISTEN`, id=255, cir=1 | **PASS** |
| 760186115 | **BFCG face, between B–C, LOWER** anchor-layer height (ABCD=lower layer) — **BSF66F** repurposed tag | lower | ✅ | ✅ boots `MODE=LISTEN`, id=255, cir=1 | **PASS** |

**BSF66F (760186115) go/no-go — PASS (passive):** boots `MODE=LISTEN` (not `MODE=TAG`), runs the common image, and — like all units — has **no tag-active behavior available except an explicit `MODE_TAG` USB command**: no poll TX, no TDMA, no UWB airtime in LISTEN, and `CONFIG_BT=0` so it cannot be a BLE peer. All prior tag identity/behavior was wiped by the full `recover`. 🟢

### 760185886 (盖格 Geiger) — NOT touched ✅
The off-limits Geiger air monitor is connected but was **never flashed** — the flasher's allowlist rejects it (verified: `bash flash_listener_freeze.sh 760185886 …` → `[ABORT] … OFF-LIMITS 盖格`).

### Passive-guarantee spot-check — status
- All 8 flashed units are **alive on the common image** (emitting `LSTAT;1;255;…`).
- **No-TX / no-BLE:** structurally guaranteed by the image (boot-default `MODE=LISTEN` has no TX path; `CONFIG_BT=0`) and empirically confirmed historically (audit's capture-log evidence across 3 overnight runs).
- **Live "hearing" (LPD/LRD/LCIR) confirmation is PENDING** — at flash time the tag/anchor system was **not ranging** (Master_Tag 0/3 tags connected, ~0 TR/s, `conn` did not restore it), so there was no on-air traffic to hear. Re-run the spot-check once the tags are polling normally.

### Outstanding (needs operator)
1. ~~Physically reconnect the 760181879 J-Link~~ **DONE** — root cause: its J-Link OB ran **outdated firmware (Oct 2023)** which hung on the `recover` sequence (it was the only unit on that OB firmware; the prior recover never erased flash). A physical replug + reconnect auto-updated the OB firmware to Feb 2026; recover+flash then succeeded (`0x0` 20000400→20003E60). **760181879 now PASS.** Lesson: on a probe whose OB firmware JLinkExe reports it is *updating*, expect the first `recover` to fail — replug once and retry.
2. **Get the tag/anchor system ranging** (tags powered/advertising + Master_Tag holding them) so the live passive spot-check (each listener hears 0xb1xx polls + 0xa1xx responses with CIR) can be recorded.

**Status: 9/9 flashed + image-verified ✅.** `listener-freeze-20260715` is the 5th frozen firmware piece. Only remaining: the **live hearing spot-check** (each listener hears 0xb1xx polls + 0xa1xx responses) — blocked only by the tag/anchor system not currently ranging; run it once the tags are polling.

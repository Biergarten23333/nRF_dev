# CIR / Listener Overnight Readiness Audit (2026-07-09)

**Scope:** read-only code audit (no files modified/built/flashed). Covers the
6-DWM1001C-listener + 8-anchor + 3-wand-tag fleet for an overnight passive-CIR
capture run. Two rounds: (1) main audit — listener firmware ID, CIR readout,
TX-source ID, fixed-a19 status, ge8=0, USB output, overnight robustness; (2)
addendum — register-level gaps (RCPHASE/RXTOFS/AGC_STAT1/EVC_CTRL), anchor-side
CIR capability, TX-power/frame-length consistency, TDMA-based TX-ID, USB-CDC
flow control.

**Method:** parallel background code-reading agents, each required to cite
`file:line` for every claim and flag anything not directly verified. Findings
below are synthesized/merged from those reports; see git history of this repo
around 2026-07-09 for the raw per-agent transcripts if deeper drill-down is
needed later.

**Key correction to the original framing:** the DWM1001C's nRF52832 has no
native USB peripheral. What appears as "USB" on the host is UART0 bridged
through the DWM1001-DEV board's onboard J-Link debug probe's own separate
USB-to-serial bridge chip. This is consistent with the operator's "USB CDC
connected" language — the host-side CDC-ACM device is real, it's just
implemented by the J-Link probe, not by listener firmware touching a USB
stack. Fixed baud: 460800.

---

## Hardware/deployment context (operator-confirmed, load-bearing for all verdicts below)

- **8 Anchors**: firmware is BLE-OTA-managed, **FROZEN** — do not flash. USB
  CDC is physically connected (serial console path exists) — heavy data
  *could* exit that way if firmware supports it.
- **3 Wand Tags** (BSCCF4, BS9336, BS955A): BLE only, no USB/serial cable, TX-only
  sources for the multistatic radar experiment. Firmware **FROZEN**.
- **6 Listeners** (LB@B, LE@E, LF@F, LCCF4@wand, L9336@wand, L955A@wand): USB CDC
  connected, reflashable. All actionable firmware changes target listeners only.
- Wand geometry (T-shape, caliper-measured): BSCCF4 —285mm— junction —385mm—
  BS9336; junction —595mm— BS955A.
- "11 TX sources" = 8 anchors (SS-TWR response frames) + 3 wand tags (poll frames).

---

## Part 1 — Main audit status table

| # | Item | Status | Evidence (file:line) | Blocker for tonight? |
|---|---|---|---|---|
| 1 | Distinct listener firmware exists (not anchor/tag in passive mode) | Confirmed | `SS-TWR/alt-SS-TWR/broadcast/UWB_listener/src/main.c` — never calls `dwt_starttx`; own CMake target | No |
| 2 | Role selection mechanism | Compile-time `#define`/CMake cache vars, not runtime/pin | `main.c:21-42`, `CMakeLists.txt:20-30` | No, but per-listener rebuild required (see #12) |
| 3 | `dwt_readaccdata()` reads full 1016-tap / 4064-byte accumulator | Confirmed, not windowed | `main.c:394` (`print_full_cir`), `deca_regs.h:660-661` (`ACC_MEM_LEN=4064`) | No |
| 4 | Windowed/partial CIR mode | Not implemented; trivial to add (~10-20 line diff) | `main.c:349-408` | No — full accumulator already captured |
| 5 | CIR read trigger | Gated: accepted tag-poll frames only, 1-in-10 sampled, and only if `APP_LISTENER_CIR_CAPTURE_ENABLE=1` (default 0) | `main.c:220-290` | **Yes** — flag off by default, must be explicitly enabled per build |
| 6 | Raw vs converted CIR output | Raw hex I/Q bytes, no amplitude/phase conversion in firmware | `main.c:400-404` | No |
| 7 | Two diagnostics pipelines conflated | Scalar `dwt_readdiagnostics()` (always on) vs raw-accumulator `dwt_readaccdata()` (opt-in, sampled) | `main.c:472` vs `main.c:394` | Clarify expectations — `lpd.csv`'s "CIR_PWR" is a scalar register, not the raw waveform |
| 8 | CIR-capture wired on all 6 physical nodes | Only 4/6 confirmed as of latest dated evidence (LB, LCCF4, LE, LF); L9336/L955A scalar-only | `handoff_scripts_20260704/overnight_soak_v2.sh:20`, `phase_audit.py:41` | **Yes — verify/rebuild+flash 2 nodes before tonight** |
| 9 | TX source ID logged | Both a 4-bit payload nibble (`tag_id`, capped at 16) and a full 16-bit MAC src address (`src`, up to 256) logged per frame | `main.c:295-314`, `uwb_ss_twr_shared.c:147-188` | No — full-range ID already present |
| 10 | Anchor identity cap | Hard-capped at 8 by `uint8_t` bitmask width | `uwb_ss_twr_shared.h:7,100` | No (matches ≤8-anchor fleet) |
| 11 | fixed-a19 fix (post-starttx diag read) | Genuinely implemented, coexists with original bug behind build flags, both off by default | `ss_twr_resp.c:1214` (buggy path) vs `:1344` (fixed path) | Anchor-side only — see #12 |
| 12 | fixed-a19 actually flashed on the 8-anchor fleet? | **Confirmed deployed** (resolved by addendum Part 2, A5) | `apps/master_ota/generated/anchor_ota_manifest.h:3-5` | No |
| 13 | Listener's own diagnostics timing | Safe by construction — listener never transmits | 4 separate listener source copies checked | No |
| 14 | "ge8=0" root cause | Analysis-script metric (≥8-anchor ranging sweep completeness), not a firmware symbol. Root cause: anchor drops out of responder role between capture chunks. rxdblbuf hypothesis code-confirmed retracted | `AUDIT_RESULTS_20260705.md:150,211-215`; `ss_twr_init.c:4871-4872` | Anchor-fleet reliability issue, not a listener defect |
| 15 | Separate listener-specific chunk-boundary drop | Full-CIR dump blocks RX re-arm ~220ms per dump (UART-bound), self-documented as dropping concurrent frames | `main.c:509-524` | **Yes if CIR capture enabled** — real, admitted, bounded loss on ~1-in-10 polls |
| 16 | Transport | Not native USB — UART0 bridged via J-Link OB probe's USB-serial converter | `prj.conf` (no `CONFIG_USB*`) | No, naming correction only |
| 17 | Output format | ASCII text, semicolon-delimited, one `printk` line per record, no binary framing | `main.c:295,325,586` | No |
| 18 | Baud rate | Firmware fixed at 460800; host script default 115200 — mismatch unless overridden | `boards/decawave_dwm1001_dev.overlay:1-15`; `capture_uwb_poll_listener.py:122` | **Yes — trivial preflight**, pass `--baud 460800` |
| 19 | Per-frame fields present | timestamp/TX source/FP index/rxpacc/CIR_PWR/std_noise present; `max_noise` absent | `main.c:117-136,541-547` | No — minor gap |
| 20 | Bandwidth (compact mode) | 1-9 KB/s per listener at 10Hz×6, vs ~46KB/s link capacity — not a bottleneck | computed | No |
| 21 | Bandwidth (full-CIR mode) | +10.3KB/s per capture event; fine at 460800 baud, would exceed 115200 baud | computed | Tied to #18 |
| 22 | Watchdog / sleep / timeout | None configured — only a 15s RX-stall self-heal (in-place radio re-init), only reset path is kernel-panic | both `prj.conf` files | No |
| 23 | Buffer overflow risk (8+ hrs) | 128-entry ring buffer drains 4x faster than production; overflow is a counted graceful drop | `main.c:138-141,530-533` | No |
| 24 | Memory allocation | 100% static — zero `malloc`/`k_malloc` across every compiled source file | exhaustive grep | No |
| 25 | Counter/timestamp wraparound | All counters `uint32_t` (>49-day wrap); `rx_ts` wraps ~67ms but only used for local per-exchange math | `main.c:80-100,134` | No |

---

## Part 2 — Addendum status table

| # | Item | Status | Evidence (file:line) | Blocker for tonight? |
|---|---|---|---|---|
| A1 | Anchor firmware reads ACC_MEM (`dwt_readaccdata`) | Yes, in source — `anchor_cir_output_publish_full()` | `anchor_cir_output.c:223` | No |
| A2 | Anchor CIR runtime gate | 3-state (`OFF/COMPACT/FULL`), set only via BLE GATT write, defaults OFF at boot | `anchor_cir_output.h:8-12`, `anchor_ble_ctrl.c:535-582` | No |
| A3 | **Deployed anchor image has CIR output compiled OUT entirely** | Both master gates = 0 in the active OTA build's `CMakeCache.txt` — dead-code-eliminated | `build-anchor-unified-ota-fixeda19-g1200-r1000-20260701/anchor/CMakeCache.txt:69,78` | Informational — determines channel count |
| A4 | **Channel matrix verdict** | Anchors do not export CIR tonight → **6 listeners × 11 TX ≈ 60 bistatic channels**, not 140 | `apps/master_ota/generated/anchor_ota_manifest.h:3-5` | No — plan around this, not fixable tonight |
| A5 | fixed-a19 deployment status (resolves main-audit item #12) | **Confirmed deployed** — active OTA build IS the fixed-a19 image | `APP_ANCHOR_FW_MARKER="alt-bcast-fixeda19-g1200-r1000"` in same manifest/cache | No |
| A6 | Zero-risk live confirmation available tonight | BLE `VERSION` command reports compiled-in capability flags | `anchor_ble_ctrl.c:488-510` | No — optional preflight |
| F1-F4 | DIS_STXP (Smart TX Power) control | **Confirmed uncontrolled** in both anchor and tag roles — `dwt_setsmarttxpower()` has zero call sites; left at chip POR default (ON) | grep across `ss_twr_resp.c`, `ss_twr_anchor_init.c`, `ss_twr_init.c`, `uwb_bringup.c`; `deca_device.c:2228-2243` | Confirms blocker precondition #1 |
| F5-F6 | TX_FCTRL frame length across roles | **Confirmed different**: anchor→tag response = 36B; tag poll = 17B (dominant) or 13B (legacy) | `ss_twr_resp.c:1199,1210-1211,1282,1294`; `ss_twr_init.c:5034,5100-5232` | Confirms blocker precondition #2 |
| F7 | **Verdict: confirmed BLOCKER for cross-role CIR amplitude comparison** | Smart Power uncontrolled + frame lengths differ → anchor-sourced vs tag-sourced amplitude not directly comparable. No boost table in-repo to correct | — | **Yes — analysis-side, not firmware-fixable (frozen)** |
| B1 | Listener CIR register sequence (PMSC FACE/AMCE) | Entirely internal to driver's `dwt_readaccdata()`; listener never touches PMSC directly | `deca_device.c:943-951,2674-2685,2713-2714` | No |
| B2 | Windowed CIR readout | Not implemented; `accOffset` is a free driver param, `diag->firstPath` is a ready-made center — trivial ~10-20 line change | `main.c:349-418`; `deca_device.c:943,948` | No — not needed tonight |
| B3 | SPI timing, full vs windowed CIR read | Full 4064B ≈4.4ms, windowed 256B ≈0.28ms at listener's active 8MHz SPI clock — both <1% of frame interval | `uwb_port.c:159-160`; computed | No |
| D1-D3 | RCPHASE/RXTOFS (RX_TTCKO), RXTTCKI, AGC_STAT1 | **Not read at all**, anywhere in the tree | grep, zero hits | No — nice-to-have only |
| D4 | LDE_PPINDX / LDE_PPAMPL (literal registers) | Not read as those literal registers — functionally-equivalent values already read via `RX_TIME_ID` and already logged as `fp1`/`fp_index` | `deca_device.c:1006,1014` | No |
| D5 | FP_AMPL2, FP_AMPL3, STD_NOISE, CIR_PWR, RXPACC | **All logged** | `main.c:117-136,308-312,338-342` | No |
| D6 | RXOVRR (SYS_STATUS bit 20) | Read into raw hex dump but **not decoded/counted** — real gap, ~3-5 line fix | `main.c:638-644`; `deca_regs.h:276,304-305` | No — nice-to-have |
| E1-E2 | EVC_CTRL / EVC_EN | **Never set** (verified independently for listener). Addition is trivial (~5 lines): enable in `listener_radio_configure()`, report via existing 5s LSTAT print | `main.c:166-178,573-608`; `deca_device.c:3165-3216` | No — recommended, not required |
| C1 | TDMA-slot TX inference availability | **Not practically available** — listener build contains zero TDMA code, zero timing state in frame acceptance, slot map reassignable over BLE which listener can't observe | `UWB_listener/CMakeLists.txt:50-58`; `main.c:220-257`; `tag_app.c:436` | No — not worth adding |
| C2 | Wand tags' TX mode vs regular ranging tags | **Identical firmware/frame format** — no "radar"/"wand" branch exists in tag source | grep, zero hits | No, but flag: physical wand tags' flashed version last confirmed 2026-05-26, not re-verified today |
| G1-G2 | UART/CDC flow-control mechanism, RX/UART decoupling | Confirmed polling backend (`uart_poll_out`), zero interrupt/async/USB Kconfig; RX capture path makes no UART call, draining capped at 4 lines/loop | `prj.conf:1-10`; `main.c:78,455-458,522-524,639,653-657` | No |
| G3 | Slow/stalled host behavior | No RTS/CTS configured → firmware paces by wire bit-time, not host read rate. Stalled host loses bytes downstream (J-Link/host OS buffer), does not back-pressure firmware | `decawave_dwm1001_dev.overlay:13-15` | No |
| G4 | Host capture script robustness | Read loop tight/non-blocking, adequate for compact-mode bandwidth. **Gap**: file-write calls unguarded — disk-full crashes that listener's capture process | `capture_uwb_poll_listener.py:335-341` (guarded) vs `:346-347,369-372` (unguarded) | **Yes, low-probability but real** — trivial try/except fix, worth doing given this machine's SSD-capacity history |

---

## OVERNIGHT VERDICT

**6 listeners can run overnight recording CIR as-is, with two trivial pre-flight actions and one accepted limitation.**

### BLOCKING (do before starting tonight — both trivial)
1. Verify/rebuild+flash `APP_LISTENER_CIR_CAPTURE_ENABLE=1` on the 2 listener nodes not yet confirmed enabled (L9336, L955A). *Trivial — existing build script, no code change.*
2. Match host capture baud to the firmware's fixed 460800 (`--baud 460800`) for every capture session. *Trivial — one flag.*

### NOT firmware-fixable tonight (frozen anchors/tags — accept and account for in analysis)
- Anchors do not export CIR in the currently-deployed image → real channel matrix is **6×11≈60**, not 140. Adding anchor CIR export needs un-freezing + rebuilding anchor firmware — multi-day, out of scope tonight.
- Anchor-sourced vs. tag-sourced CIR amplitude are not directly comparable (Smart TX Power uncontrolled + differing frame lengths, both frozen). Treat as separate populations in analysis; no in-repo correction table exists.

### NICE-TO-HAVE (listener-only, all reflashable, none required tonight)
- EVC_CTRL health counters (good/CRC-error/overflow/SFD-timeout frames) — *trivial, ~5 lines*, free overnight diagnostic summary.
- RXOVRR decoded into a countable field instead of buried in a raw hex dump — *trivial, ~3-5 lines*.
- Windowed CIR readout (FP±32 taps vs full 1016) — *trivial-to-afternoon, ~10-20 lines*, only useful for a higher CIR sample rate than 1-in-10 later.
- Host script: wrap `raw.write`/`csv.writerow`/`flush` in try/except so a disk-full doesn't kill a listener's capture mid-run — *trivial, Python-side*.

### Confirmed non-issues
Listener overnight robustness (no watchdog/reset risk, static memory, bounded ring buffer, no flow-control stall risk), listener diagnostics-timing safety (no fixed-a19-class bug possible — listener never transmits), fixed-a19 now confirmed live on all 8 anchors, TDMA-based TX-ID cross-check correctly judged not worth building.

---

## Session handoff prompt

*(Paste this into a fresh session to resume without re-running the audit.)*

> **Context:** BioSpur UWB multistatic CIR capture project, repo
> `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`, custom DW1000/nRF52832
> (DWM1001C) firmware, no vendor PANS/DRTLS stack. Deployment for the
> overnight run: 8 anchors (A-H, BLE-OTA-managed, **frozen**, fixed-a19
> confirmed deployed) + 3 wand tags (BSCCF4/BS9336/BS955A, BLE-only,
> **frozen**, TX-only radar sources, T-shaped geometry 285/385/595mm) + 6
> listeners (LB@B, LE@E, LF@F, LCCF4/L9336/L955A@wand, USB-serial-connected
> via J-Link OB bridge at 460800 baud, reflashable).
>
> **Live listener firmware:** `SS-TWR/alt-SS-TWR/broadcast/UWB_listener/src/main.c`.
> Do not confuse with the unrelated top-level `UWB_listener/` (a separate
> LED/buzzer "autofollow" UI device, different project) or the stale
> `UWB_listener_old`/`unicast/UWB_listener` forks.
>
> **Full read-only audit completed 2026-07-09**, see
> `SS-TWR/alt-SS-TWR/broadcast/CIR_LISTENER_OVERNIGHT_READINESS_AUDIT_20260709.md`
> for the complete file:line-cited findings (25 main-audit items + 25
> addendum items). Headline conclusions:
>
> 1. Listeners are overnight-safe as-is (no watchdog/OOM/overflow risk).
> 2. Full raw-CIR capture (`dwt_readaccdata`, 1016 taps, 1-in-10 sampled) is
>    opt-in via `APP_LISTENER_CIR_CAPTURE_ENABLE` and was only confirmed
>    enabled on 4/6 physical nodes as of the last dated build evidence —
>    verify/flash L9336 and L955A before starting.
> 3. Anchors do **not** export CIR in the currently-deployed (frozen) image
>    — CIR output code exists in `anchor_cir_output.c` but both feature
>    gates are compiled to 0 in the active OTA build. Real channel matrix
>    tonight is 6 listeners × 11 TX ≈ 60 bistatic channels, not the
>    hoped-for 140.
> 4. Anchor-vs-tag CIR amplitude is **not cross-comparable**: Smart TX Power
>    (`DIS_STXP`) is left uncontrolled on both frozen firmwares, and anchor
>    response frames (36B) differ in length from tag poll frames (17B/13B)
>    — DW1000 auto-boosts short frames differently, so TX power differs by
>    role. No numeric correction table exists in-repo; must be handled as
>    separate populations in post-processing, or empirically calibrated
>    (the planned corner-reflector session is the natural place for this).
> 5. TX source ID is already fully solved — both a 4-bit payload tag_id
>    nibble and the full 16-bit MAC src address are logged per frame;
>    TDMA-slot-based inference was evaluated and correctly rejected as
>    unnecessary/impractical (listener has zero TDMA/BLE awareness).
> 6. Several DW1000 diagnostic registers (RCPHASE/RXTOFS/RXTTCKI/AGC_STAT1,
>    EVC_CTRL session counters, RXOVRR decode) are unread/unused — all
>    trivial (~5-20 line) additions, none required for tonight, all
>    documented with exact call-site sketches in the audit doc's Part 2.
> 7. One real, low-probability host-side risk: the Python capture script
>    (`scripts/capture_uwb_poll_listener.py`) has unguarded file-write calls
>    that would crash a listener's capture process on disk-full — worth a
>    trivial try/except given this machine's SSD-capacity history (see
>    memory: disk-layout-and-cpptools-cache).
>
> **Before tonight:** (a) flash/verify CIR-capture-enable on L9336 + L955A,
> (b) run all captures at `--baud 460800`. Everything else is either already
> fine or a post-hoc analysis caveat, not a firmware blocker.

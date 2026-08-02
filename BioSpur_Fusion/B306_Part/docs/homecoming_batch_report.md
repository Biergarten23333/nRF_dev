# Homecoming Batch Report

Date started: 2026-07-24  
Workspace: `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion`  
Evidence root: `B306_Part/logs/homecoming_20260724/`

## Protections and sequencing

This report follows the Homecoming Batch authorization. Path M, the physical
layer, the 96-byte UART frame, capture CI, signing key, production memory
gates, deployed wand tags, and rollback inventory remain protected. The
Master_Tag receives at most the two separately gated carrier flashes specified
by the batch. No dependent phase is treated as successful without its required
operator confirmation and observed post-action evidence.

## H0 — preflight

### Prediction registered before inspection

The expected pinned rig is:

```text
Fusion Master DK = dk-fusion-imu-relay-v7
B306             = b306-imu-relay-v15
IMU active       = 0
61               = 0000
63               = 03E8
03               = 000B, volatile
1F               = 0004
FUSION_UWB       = healthy, approximately 10 Hz
```

The expected operator facts are that probe `1050070698` remains connected to
the Master_Tag, wand tags are currently streaming, and the Fusion PCB/DK
positions are unchanged. Any mismatch is recorded and reconciled or stops
progress beyond H0.

### Git state

Both requested directory views resolve to the same parent monorepo:

```text
git root = /mnt/nrf_ssd/nRF_dev
branch   = feature/b306-bringup
HEAD     = 2b38fa5036db191cf92e5d8a45e5cae3d54bf68b
```

The worktree was already dirty. The pinned path-scoped snapshots contain 50
Fusion entries and 20 upstream-UWB entries. They are preserved verbatim in:

```text
B306_Part/logs/homecoming_20260724/h0/fusion_dirty.txt
B306_Part/logs/homecoming_20260724/h0/uwb_dirty.txt
```

No pre-existing dirty file was cleaned or overwritten.

### Rig inspection

The live remote inspection matched the prediction:

```text
DK                 dk-fusion-imu-relay-v7
bridge             count=1 name=BSF3C79 connected=1 subscribed=1
B306               fw=b306-imu-relay-v15 id=3C79
IMU                active=0 rate=200 batch=2 verify=WARN
registers          61=0000 63=03E8 03=000B 1F=0004
current UWB sample 147/147 healthy over 14.600 s = 10.000 Hz
```

The RTT buffer also contained 16 older records preceding the current
77-million-ms DK epoch; they were excluded from the rate calculation rather
than mixed into the current window. Evidence:

```text
B306_Part/logs/homecoming_20260724/h0/rig_preflight_rtt.log
```

The stable Master_Tag CDC by-id path is present, but device presence does not
substitute for the required operator statement about which target probe
`1050070698` is physically wired to.

### Operator confirmations

Recorded at `2026-07-24T16:06:37+02:00`. Prompt and verbatim response:

```text
探针 1050070698 现在仍连接在 Master_Tag B120 上吗？y
2. y
3. y
```

The numbered questions were:

1. probe `1050070698` remains on Master_Tag;
2. wand tags are currently streaming;
3. Fusion PCB and DK positions are unchanged.

H0 verdict: **PASS**. The live electronic state and all three operator facts
matched the pre-registered state of record.

## H1 — deploy existing relay1 carrier and tag payload

### Prediction registered before carrier inspection/flash

- CPUAPP and CPUNET SHA-256 will match the handover manifest exactly.
- The explicit-serial dual-core recover/flash will complete without any J-Link
  probe-selection dialog.
- After the mandatory operator-confirmed cold power cycle, the stable
  Master_Tag CDC will return and `ota show` will contain the tag boot profile,
  `wand tags: WILL HOLD BS*`, and payload marker
  `tag-fusion-link-v2-relay1`.
- The already-streaming wand tags will reattach without reconfiguration.
- BS065F OTA will install marker `tag-fusion-link-v2-relay1`. End-to-end time
  is predicted at 50–60 seconds; deviation is recorded rather than hidden.
- V-B1 Path M remains byte-identical. V-B2 relay replies retain Path-R source
  attribution. V-B3 transmission proof reaches approximately 10 Hz. V-B4
  switches M→R→M without a stuck 0-TX state or wand-tag disturbance.

Any required banner mismatch or wand reattach failure stops H1 before tag OTA.

### H1.1 carrier flash observation

The manifest inspection passed:

```text
CPUAPP  f5f504360bfea2e5b5fb13c76b40a5830f1bf3e83f01d4feec0865c47b1ce37a
CPUNET  9c17013e933dcccfdc611085b1154a6b3cc775e59b00da542f5fcf8a0ba94199
payload 3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b
```

The exact handover script selected probe `1050070698`. CPUNET and CPUAPP
`loadfile` operations completed and the fresh-session app-vector read returned
`20012760 0000DDF9`, not blank flash.

However, the first J-Link session returned:

```text
J-Link>recover
Unknown command. '?' for help.
```

The shell script failed to treat that response as an error and continued to
print its final `[ok]`. Consequently, dual-core programming is observed, but
CTRL-AP recover and NVS erasure are **NOT PROVED**. No alternate recover tool
and no second flash was attempted. Full output:

```text
B306_Part/logs/homecoming_20260724/h1/master_tag_relay1_flash.log
```

The mandatory cold-power-cycle gate remains next. Its outcome will be
inspected without retroactively treating the missing recover as successful.

### H1.1 cold boot and stop-gate verdict

The operator supplied the required literal confirmation:

```text
POWER CYCLED
```

It was received before any CDC post-flash check and recorded at
`2026-07-24T16:09:27+02:00`. The stable CDC returned as
`/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00`.
It was opened with DTR=0 and RTS=0.

The three required banner facts passed:

```text
MASTER BOOT: profile=tag ... target=TAG wand tags: WILL HOLD BS*
UART control ready: ...
OTA_BUNDLE kind=tag fw=tag-fusion-link-v2-relay1 ...
```

The boot also printed `Control mode loaded: RECV`. Because the script's
`recover` command was not executed, that observation does not prove NVS was
erased.

Wand-tag reattachment then **FAILED**. The first passive/status observation
showed no connection evidence. A non-configuring `conn` command explicitly
re-enabled connect-and-start discovery and produced:

```text
Master discovery mode: CONN & START
SCAN start ... conn_count=0 target=tag prefix=BS
Scanning for BS*
```

No candidate or `Connected[...]` line appeared during the following 20
seconds. This triggered protection #6 and the H1 stop condition. No tag OTA,
TDMA command, wand reconfiguration, or V-B test was attempted.

Evidence:

```text
B306_Part/logs/homecoming_20260724/h1/master_tag_post_coldcycle_ota_show.log
B306_Part/logs/homecoming_20260724/h1/master_tag_wand_reattach.log
B306_Part/logs/homecoming_20260724/h1/master_tag_wand_conn_probe.log
```

The initial interpretation that retired wand tags had to reattach was
superseded by the operator's H1 Recovery Directive. BS9336, BS955A, and
BSCCF4 are retired and deliberately out of service; no further action or
observation is permitted on them. The sole attach acceptance target is
BS065F.

H1 remains open for the authorized recovery completion below.

## H1 recovery — proper erase and ceremony-1 completion

### Prediction registered before touching the probe again

- The exact failure will prove that J-Link Commander v9.24a has no `recover`
  command and that `-ExitOnError` did not turn the parser's `Unknown command`
  text into a nonzero process status.
- The live script will be changed to reject error text and use the documented
  nRF5340 CTRL-AP ERASEALL registers. Sealed freeze copies remain byte
  unchanged.
- The pre-erase 32 KiB NVS image will contain non-`0xff` stale state.
- Network and application CTRL-AP ERASEALL status will reach zero.
- The complete `0xf8000..0xfffff` post-erase image will be `0xff` before any
  loadfile.
- Reflashing the unchanged SHA-verified images will survive the second
  operator-confirmed cold power cycle and reproduce all three required
  banners.
- During a 60-second watch, BS065F will either be separately observed and
  connected, observed but not connected, or never observed. Those outcomes
  will not be conflated.

The old-wand reattach clause and all old-wand disturbance clauses are void.

### Recovery forensics and live-script repair

The sealed script's exact failing path was:

```text
script:
  UWB_Part/2026-07-15-FREEZE/scripts/ops/flash_b120_master_freeze.sh
generated commander line:
  recover
tool invocation:
  JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN 1050070698 \
    -CommanderScript <temporary-command-file>
tool:
  SEGGER J-Link Commander V9.24a
error:
  J-Link>recover
  Unknown command. '?' for help.
```

The J-Link process nevertheless returned success, the shell script continued
through both `loadfile` operations, and it printed its terminal `[ok]`. This
confirms a fail-open defect: `-ExitOnError 1` does not convert this unknown
Commander command into a failing process status in the installed version.

The sealed Fusion and upstream freeze copies remain byte-identical at SHA-256
`21e5b15a716c9125b8a641e7c2bcb49593755991cb7ef4c99b8e8eb8a914a615`.
Only the live upstream script was repaired. It now:

- rejects a nonzero J-Link status and known fatal/error text;
- drives nRF5340 network and application CTRL-AP ERASEALL explicitly;
- verifies CTRL-AP IDR `0x12880000` and terminal ERASEALLSTATUS `0`;
- reads all 32 KiB at `0x000f8000` and aborts unless every byte is `0xff`
  before either image is loaded.

The pre-erase forensic image is exactly 32,768 bytes:

```text
file    B306_Part/logs/homecoming_20260724/h1_recovery/nvs_pre_erase.bin
SHA-256 2d864c0b789a43214eee8524d3182075125e5ca2cd527f3582ec87ffd94076bc
content non-FF bytes = 0; FF bytes = 32768
```

This falsified the registered prediction that stale state would be present in
that partition. It does not rehabilitate the rejected `recover`: the old
script still lacked any erase proof, and the exact tool transcript proves that
its requested operation never ran.

### Proper erase and same-image reflash

The two carrier files were rechecked immediately before the operation:

```text
CPUAPP f5f504360bfea2e5b5fb13c76b40a5830f1bf3e83f01d4feec0865c47b1ce37a
CPUNET 9c17013e933dcccfdc611085b1154a6b3cc775e59b00da542f5fcf8a0ba94199
LFRC build assertion: PASS
```

Explicit probe `1050070698` was used in every J-Link process. Network APSEL 3
and application APSEL 2 each returned CTRL-AP IDR `0x12880000`; both
ERASEALLSTATUS streams changed from `0x00000001` to `0x00000000`.

Before either `loadfile`, the complete application NVS readback passed:

```text
range   0x000f8000 + 0x8000
result  all 0xff
SHA-256 2d864c0b789a43214eee8524d3182075125e5ca2cd527f3582ec87ffd94076bc
file    B306_Part/logs/homecoming_20260724/h1_recovery/nvs_post_erase.bin
```

On the first subsequent network-core connection, J-Link reported that the
device was secured and applied its previously saved unsecure behavior, which
per its own transcript performs a further mass erase of both cores. It then
loaded CPUNET, followed by CPUAPP. Both programming operations returned
`O.K.`, and a fresh app-core session read:

```text
00000000 = 20012760 0000DDF9
```

The extra tool-initiated unsecure erase occurred after the explicit blank
proof and before either final image write; it therefore did not invalidate the
proof or reverse the required final NET-then-APP programming order. Full
evidence:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/master_tag_recovery_flash.log
```

The operation now waits at the mandatory second physical cold-power-cycle
gate. No banner, scan, OTA, or dependent validation is accepted before the
operator reports the literal `POWER CYCLED`.

This is the first real-hardware exercise of the formerly documented-as-
unverified freeze flash path. It caught both the installed-tool incompatibility
and the fail-open behavior on first contact.

### Second cold boot, banner, and first BS065F watch

The operator first described the physical action as:

```text
已断电-冷却5s-物理上电
```

and then supplied the required fixed gate token:

```text
POWER CYCLED
```

The token was received before any post-flash inspection and recorded at
`2026-07-24T16:43:21+02:00`. The stable Master_Tag CDC returned. It was opened
with DTR=0 and RTS=0, and all three required facts passed:

```text
MASTER BOOT: profile=tag mode=RECV target=TAG wand tags: WILL HOLD BS*
UART control ready: ...
OTA_BUNDLE kind=tag fw=tag-fusion-link-v2-relay1 ...
```

At elapsed 3.017 seconds, the non-configuring `conn` command started the
acceptance watch:

```text
SCAN start req: bt_ready=1 scan_running=0 connecting_slot=-1 conn_count=0
Scanning for BS*
```

The port remained open for more than 60 seconds after that request. There was
no BS065F candidate/advertisement line and no connection line. The two required
facts are therefore:

```text
master scan saw BS065F advertising: NO
master connected to BS065F:         NO
classification: never seen advertising, not "seen but not connected"
```

Evidence:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/cold_power_cycle_timestamp.txt
B306_Part/logs/homecoming_20260724/h1_recovery/master_tag_banner_bs065f_watch.log
```

Per the recovery directive, H1 now waits for an operator power cycle of the
Fusion PCB. After it returns, Path R must restore volatile JY61P RRATE to 11
and prove register `0x03 = 0x000b` before the second 60-second attach watch.

### Fusion-PCB cycle, RRATE restoration, and second BS065F watch

The operator supplied:

```text
FUSION PCB POWER CYCLED
```

before the dependent remote actions. It was recorded at
`2026-07-24T16:48:44+02:00`. The DK bridge subsequently reported the fresh
B306 instance as connected and subscribed:

```text
FUSION_LIST count=1 name=BSF3C79 rssi=-44 connected=1 subscribed=1 control=24
```

Path R, through explicitly selected DK probe `683234364`, restored the volatile
JY61P rate and immediately read it back:

```text
FUSION_COMMAND_TX ... line=BSF3C79 IMU RRATE=11
FUSION_REPLY ... text=IMU RRATE OK request=000B readback=000B volatile=1 saved=0 step=readback err=0
FUSION_REPLY ... text=IMU active=0 rate=200 batch=2 verify=WARN 61=0000 63=03E8 03=000B 1F=0004 ...
```

The second Master_Tag watch then began with `conn_count=0` and remained open
for more than 60 seconds after `Scanning for BS*`. It produced no BS065F
candidate/advertisement line and no connection line:

```text
master scan saw BS065F advertising: NO
master connected to BS065F:         NO
classification: never seen advertising, not "seen but not connected"
```

The complete Master_Tag scan transcript is:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/master_tag_bs065f_watch_after_fusion_cycle.log
```

Supporting evidence:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/fusion_pcb_power_cycle_timestamp.txt
B306_Part/logs/homecoming_20260724/h1_recovery/path_r_post_fusion_cycle_list.log
B306_Part/logs/homecoming_20260724/h1_recovery/path_r_rrate_restore.log
```

An independent current-state fact narrows the failure surface: Path R continued
to receive healthy `FUSION_UWB` records with `identity=065F` at approximately
10 Hz after the Fusion-PCB power cycle. Thus the Fusion PCB, DWM1001C-to-B306
UART/strobe path, B306 BLE link, and DK output path are alive. This does not
substitute for BS065F advertising to Master_Tag; that BLE advertisement was
never observed.

H1 recovery verdict: **STOP — BS065F still not seen after the required Fusion
PCB power cycle.** Per directive, no H1.2 tag OTA, revised image, TDMA change,
or V-B validation was attempted.

### Debts/findings filed by H1

1. The freeze flash script's `recover` command is incompatible with installed
   J-Link Commander v9.24a. The live script now uses proven nRF5340 CTRL-AP
   ERASEALL; sealed freeze copies remain untouched.
2. The old script continued past the rejected command and reported success.
   This fail-open defect is fixed in the live copy with process-status and
   error-text checks.
3. Master_Tag did not observe BS065F after loss of its old link and still did
   not observe it after a whole-Fusion-PCB cold cycle. H1 Directive #2 later
   superseded the initial classification: an independent dongle observed
   continuous BS065F advertising, and the Master subsequently decoded and
   connected to the same address. This is a transient Master scan/runtime-state
   finding, not a tag-side no-resume-advertising defect.
4. Positive record: the first real-hardware exercise of the previously
   unverified freeze flash path caught a real recovery/tooling defect on first
   contact.

## H1 Directive #2 — never-advertising discriminator

### Step 1 — independent observer

Verdict: **PASSIVE DIRECTION PASSED; ACTIVE TEST PRODUCED AN UNEXPECTED,
NON-TABLE RESULT.**

The operator inserted the known observer-only nRF52840 dongle and explicitly
put it in its Open DFU bootloader. It was identified without a volatile tty
number:

```text
boot VID:PID: 1915:521F
boot serial:  FEAE65D6DE45
application serial: 760AE3DFC3CD8F38
```

The archived observer package
`biospur_ble_listener_dongle_20260625_v2.zip` (SHA-256
`d8efb797e9b4cfa37149d1357c6dcef33cd5031e2e3b1cefeaff2ceae0f66cbe`)
was loaded through the exact bootloader by-id. It enumerated as
`BioSpur_BLE_Listener`, VID:PID `2FE3:10F3`, serial
`760AE3DFC3CD8F38`.

The 65-second passive observation is decisive. The dongle repeatedly decoded
the same random address as BS065F:

```text
BADV;1;...;C2:17:72:F1:3F:74 (random);...;TAG;BS065F;BS065F;tag95;-;1
BSTAT;1;96537;tags=1;...;unknown=1;...;adv=1260;...;scan=1
```

Therefore BS065F was advertising, another advertisement source was visible,
and the observer was not deaf. Hypothesis A ("tag silent because persisted
TDMA suppresses advertising") is falsified in hardware.

For the reverse direction, a temporary non-connectable tag-shaped advertiser
named `BSBEEF` was loaded on the dongle. `BSBEEF`, rather than the prose
example `BSTEST`, was required because the installed Master parser accepts
exactly four hexadecimal digits after `BS`. The advertiser reported
`adv_rc=0`. While the Master target was explicitly `bsbeef`, no BSBEEF
candidate appeared. This alone matches the directive's nominal "Master misses
BSTEST" branch, but the same Master simultaneously received and decoded 277
BS065F advertisements and rejected them only because the temporary target was
`bsbeef`:

```text
RECV candidate rejected: C2:17:72:F1:3F:74 (random)
  bs=BS065F target_name=bsbeef target_prefix=BS uuid=-
```

Thus "Master RF scanner is deaf" is also falsified by same-window evidence.
The temporary BSBEEF transmission/encoding test is invalid or not observable
by this Master for another reason and is not promoted to evidence against the
Master controller. After restoring `ota_target name BS065F`, the Master
immediately selected the next BS065F packet, connected, completed NUS
discovery, and reported the link ready:

```text
64.131 SCAN hit ... bs=BS065F
64.555 Connected[0] ... bs=BS065F
65.045 DISC complete[0] ...
65.045 BLE[0] link ready
```

This establishes the current state: BS065F advertises and the installed
Master can receive and connect to it. It does **not** retrospectively prove why
the two earlier 60-second watches saw nothing; their target filter was
`name=- prefix=BS`, so blaming the earlier result on a name filter would be
unsupported. The remaining finding is a transient Master scan/runtime-state
failure or an unrecorded timing/state interaction, not a tag
never-advertising defect.

Evidence:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/observer/pc_ble_preflight.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/pc_ble_usb_inventory.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/passive_65s.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/master_active_bstest_62s.log
```

The dongle identity and observer-only behavior are documented at
`BioSpur_UWB_before_start/docs/ble_listener_dongle_20260625.md:3-25`.

### Step 2 — installed-tag code root cause

The installed-image record is unambiguous:

```text
tag-fusion-link-v2-absdeadline3
```

Evidence:

- `UWB_Part/fusion-link/TASK_A_REPORT.md:20-32` names it as the installed
  validation image and shows the later, undeployed derivatives.
- `UWB_Part/logs/remote_reboot_bs065f_20260722_092007.log:15-18` contains the
  live `VERSION fw=tag-fusion-link-v2-absdeadline3` reply from BS065F.
- `UWB_Part/builds/tag-fusion-link-absdeadline3/build_info.yml:18` records the
  pristine build command and marker.

Persisted TDMA does explain automatic UWB recovery. The settings handler loads
the record and applies runtime parameters at
`UWB_Part/fusion-link/src/apps/tag/src/uwb_tag_ble.c:351-418`; a valid
slot/count/period record restores `tdma.enabled=true` at lines 763-813.
`tag_app.c:352-380` copies those parameters into the tag runtime, and
`tag_app.c:383-443` reports them and starts SS-TWR. That matches the observed
healthy 10 Hz UWB stream after a cold boot without reconfiguration.

It does **not** explain missing advertisements:

- Boot loads settings at `uwb_tag_ble.c:475-479`, then unconditionally calls
  `uwb_tag_ble_start_advertising()` at lines 503-509. There is no persisted
  TDMA/RUNNING guard; failures schedule retry work.
- `ble_disconnected()` unconditionally calls the same helper at
  `uwb_tag_ble.c:1575-1607`, again without a settings/TDMA guard.
- The helper stops and starts connectable advertising and internally retries
  `-EAGAIN` ten times at `uwb_tag_ble.c:1171-1192`. Other failures are retried
  every 250 ms by `uwb_tag_ble.c:446-458`.
- This is not merely an inference from a newer source tree. The installed
  absdeadline3 ELF contains the four relevant symbols and direct calls from
  boot, disconnect, and retry paths. Its disassembly is preserved in
  `B306_Part/logs/homecoming_20260724/h1_recovery/installed_absdeadline3_adv_disassembly.txt`.

The authoritative generated partition layout for the installed build is:

```text
settings_storage address = 0x44000
settings_storage end     = 0x46000
settings_storage size    = 0x2000 (8192 bytes)
erasable address range   = 0x44000..0x45fff
```

Evidence is
`UWB_Part/builds/tag-fusion-link-absdeadline3/partitions.yml:60-69` and
`UWB_Part/builds/tag-fusion-link-absdeadline3/pm.config:48-56`; its generated
tag configuration enables NVS/settings/settings-NVS.

### Required one-paragraph attribution

The installed code and independent RF observation support neither proposed
tag defect. Persisted RUNNING TDMA explains automatic UWB recovery but does
not suppress the unconditional boot advertising call; the dongle directly
observed BS065F advertising throughout 65 seconds. The installed Master is not
generally RF-deaf either: during the reverse test it decoded 277 BS065F
advertisements and connected immediately when the target was restored to
BS065F. The earlier two empty Master watches remain a transient Master
scan/runtime-state finding. The BSBEEF reverse stimulus was not received and
is therefore an invalid discriminator, not proof of CPUNET deafness.

### Step 3 gate

Verdict: **NOT RUN — HYPOTHESIS A FALSIFIED; SETTINGS ERASE FORBIDDEN.**

The directive permits the settings-only SWD operation only if Steps 1 and 2
both confirm hypothesis A. Step 1 directly falsifies tag silence and Step 2
contradicts the claimed settings-to-advertising causal path. Probe
`1050070698` therefore remains on Master_Tag; no tag partition was dumped or
erased and no tag reset was issued.

Proposed debt 5 is closed as **not reproduced / disproved for this image**.
Instead file a Master-side debt: two earlier scan sessions reported no BS065F
despite a later independent proof of continuous advertising; instrument scan
start/controller state and candidate counts before the next carrier image.
H1.2 OTA remains unattempted because the reverse BSBEEF discriminator produced
an unexpected result and the directive requires stopping on an unexpected
handover/test finding rather than silently continuing.

### Independent-observer software Boot/DFU follow-up

The operator requested that the dongle observer no longer require a physical
button for every reflash. The inherited listener contained a 100 ms poll of
CDC baud and attempted `GPREGRET=0xB1` followed by `NVIC_SystemReset`.
Hardware tests showed that the baud transition could be caught, but the stock
PCA10059 Open USB bootloader did not remain in DFU:

```text
v5 transition: app=1 boot=0 -> app=0 boot=0 -> app=1 boot=0
```

The source assumption was wrong for this board/bootloader combination.
Nordic's PCA10059 DFU trigger uses the board's dedicated P0.19-to-RESET
connection (`BSP_SELF_PINRESET_PIN`) and drives P0.19 low, producing the pin
reset on which the stock Open USB bootloader is configured to enter DFU. The
Fusion-local listener wrapper now registers an event-driven CDC
`SET_LINE_CODING` callback and uses that P0.19 self-reset path; the frozen UWB
tree remains unmodified.

The corrected v6 artifact was built but not deployed. The operator superseded
this path by attaching an nRF54L15 DK, which has an onboard J-Link and does not
require the dongle's physical Open-DFU button ceremony:

```text
package B306_Part/builds/dongle-ble-listener-softdfu/biospur_ble_listener_softdfu_v6.zip
SHA-256 5149a98dfef23764297315d678bed9a664c2c4bd46898d1b88dddbca786a7e5f
FLASH 100388 / 1020 KiB = 9.61% PASS
RAM    40016 / 256 KiB  = 15.26% PASS
malloc arena = 0
state: SUPERSEDED; not deployed; software self-reset not hardware-tested
```

Evidence:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/listener_softdfu_v5_software_boot_test.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/listener_softdfu_v6_build.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/listener_softdfu_v6_memory_gate.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/listener_softdfu_v6_artifact_sha256.txt
```

### nRF54L15 DK replacement observer

The replacement board was identified independently of tty numbering:

```text
board/controller SNR = 1057782457
board version        = PCA10156
NCS target           = nrf54l15dk/nrf54l15/cpuapp
```

A Fusion-local RTT-only observer was added at
`B306_Part/host/nrf54l15dk_ble_observer/`. It never connects to a peer. It
reports every new BLE address, parses the existing `BS####` name/manufacturer
formats, and prints two-second aggregate counters. The deployed marker is
`nrf54l15dk-ble-observer-v3`.

All flash/debug/RTT operations selected this board by SNR. The successful
flash invocation used `JLinkExe -USB 1057782457`, device
`nRF54L15_M33`, and a noninteractive Commander script with
`ExitOnError 1`. The output identifies `S/N: 1057782457`; no J-Link probe
selection dialog was used. The programmed artifact is:

```text
B306_Part/builds/nrf54l15dk-ble-observer-v3/merged.hex
SHA-256 c29b760c45467824eedbe066235d1589e42e21ac155e8a1e1428aee9fc3cedfd
FLASH 87216 / 1428 KiB = 5.96% PASS
RAM   42728 / 188 KiB  = 22.19% PASS
malloc arena = 0
```

Two tooling failures were not counted as flashes: the installed Python
`nrfutil` crashed on an incompatible protobuf while west nevertheless printed
a false success line, and west's J-Link runner could not import its `pylink`
dependency because `libffi.so.7` was absent. Direct J-Link programming with
the exact nRF54L15 device loader then erased, programmed, and verified the
artifact successfully.

The first active-scan image proved reception by counting 417 advertisements
from ten independent addresses and decoding `BS8251`, but later emitted one
SoftDevice Controller assert during an extended run. A passive-scan variant
received 643 advertisements from 27 addresses without an assert, but could
not decode the BS identity because the observed BS identifier arrived in the
active scan response. The deployed v3 therefore uses active scanning with a
controlled stop/restart every 58 seconds.

The final 68-second acceptance passed:

```text
OBSERVER_BOOT fw=nrf54l15dk-ble-observer-v3 board=nrf54l15dk output=RTT
OBSERVER_READY mode=active_scan connect=0 restart_s=58
...
OBS_SCAN action=stop err=0
OBS_SCAN action=start err=0
...
OBS_STAT ... adv=1259 unique=24 bs_packets=0 scan=1
```

Reception continued after the planned restart and no controller assert
occurred in this acceptance window. No BS065F advertisement was decoded in
this window. That is not evidence that BS065F is silent because the prior
Master_Tag test left BS065F connected; a connected tag is not expected to keep
connectable advertising. The result establishes that the independent
nRF54L15 observer is alive and receiving other BLE traffic. It remains
running autonomously when the RTT host detaches.

Evidence:

```text
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_inventory.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_build.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_memory_gate.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_artifact_sha256.txt
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_flash_by_snr_1057782457.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_active_restart_68s.log
```

## H1 Directive #3 — H1.2 OTA and V-B validation

Directive #3 superseded the proposed synthetic v4 advertiser test because
Master_Tag had already decoded 277 BS065F advertisements, connected, and
completed NUS discovery. The two earlier empty 60-second scan windows remain
an unexplained anomaly; full candidate-stream logging is now the standing
instrument.

### H1.2 Path-M OTA

> **2026-07-26 correction:** `CFG_STOP` is now proven broken on relay2. The
> historical preflight below briefly put the tag into approximately 64 Hz
> free-run until `MODE IDLE` followed. It did not overlap the OTA or a
> measurement window. Until relay3, `MODE IDLE` is the only stop operation and
> a complete Master TDMA reconfiguration is mandatory afterwards.

Preflight confirmed the installed marker
`tag-fusion-link-v2-absdeadline3`. Capture was idled through the existing
connection with `CFG_STOP` and `MODE IDLE`. The deployed payload was:

```text
marker  tag-fusion-link-v2-relay1
SHA-256 3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b
DFU ZIP SHA-256 63b8127638c972a5551d8c007e0386de270cba72ea02446fcd07ca357361a8ce
```

The established freeze OTA driver uploaded 207,852 bytes, performed the
pending/test/reset sequence, and returned Master_Tag to RECV. It classified
the run `D / ota_success_observed`. The sequence was sent at 41.59 seconds and
RECV was restored at 43.50 seconds. The nRF54 observer independently saw the
tag advertising after the OTA reset; Master_Tag then reconnected and returned:

```text
VERSION fw=tag-fusion-link-v2-relay1
```

The marker confirmation was started as a separate host check, so an exact
single-timeline command-to-marker duration is unavailable. The defensible
measurements are 41.59 seconds to upload/test/reset and 43.50 seconds to
Master_Tag RECV restoration; the old 50–60 second end-to-end estimate is not
promoted to a measurement.

### V-B1 through V-B5

Predictions were registered in `V_B_PREDICTIONS.md` before the V-B run. The
draft's `id=95` was corrected before execution to the established TDMA tag id
`1`; `065F` is the FICR-derived BLE identity suffix, not the TDMA id.

- **V-B1 PASS:** clean Path-M roster/rebalance returned
  `CFG_OK TAG=1 SLOT=0/10 MASK=0x0001 ... GEN=2 LIVE=1 RUN=1`, followed by
  steady `TR;2` at 10.000 Hz after the five-second epoch delay.
- **V-B2 PASS:** Path-R `TAG PING`, `TAG STATUS`, and `TAG RAW VERSION`
  each produced `RELAY_QUEUED` and a same-correlation `source=TAG` response.
  STATUS deliberately snapshots `last_status`, so a live `TR;2` is expected
  while ranging.
- **V-B3 PASS after activation:** direct
  `TAG CFG id=1 slot=0 count=10 period=10 active=9 epoch=5000` returned
  `CFG_OK ... LIVE=1 RUN=1`. The immediate 12-second prediction missed because
  its window included the requested five-second epoch delay and averaged
  6.582 Hz. The steady window measured 10.021391 Hz, with all monitored anomaly
  counter deltas zero. Direct CFG reported `MASK=0x0000`; this representation
  difference is retained because transmission proof nevertheless passed.
- **V-B4 PASS:** M→R→M applied all three configurations without a stuck/0-TX
  state. Final Path M returned `MASK=0x0001 ... GEN=4` and measured 9.9751 Hz.
- **V-B5 PASS:** H1.2 supplied the OTA-duration result: 41.59 seconds through
  upload/test/reset and 43.50 seconds until Master_Tag RECV restoration. The
  exact command-to-marker time remains unclaimed because marker verification
  ran in a separate host session.

Two host-only deviations did not change device results. The first V-B1 helper
failed to reopen CDC after the expected `mode recv` re-enumeration and stopped
before applying TDMA; the clean rerun passed. The V-B4 helper saved its raw
result and then raised a closed-file exception while appending the summary.

### Decision and end state

The post-OTA advertising/reconnect observation is the first live re-test of
the empty-window anomaly and passed. A fusion-tag
"does not resume advertising" defect is not confirmed; the debt remains "two
unexplained empty Master_Tag scan windows, cause and side unknown."

Fusion Master remains nRF52840 DK `683234364` with
`dk-fusion-imu-relay-v7`. Native USB CDC is primary: the earlier missing CDC
was caused by a bad/non-data cable, not a broken connector. RTT is a diagnostic
backup, and a custom-B306 receiver is no longer required as a workaround.

Final state: B306 `b306-imu-relay-v15` connected/subscribed, IMU stopped, UWB
running near 10 Hz, Master_Tag connected to BS065F, and no probe moved or
touched. The full result and evidence index are in:

```text
B306_Part/logs/homecoming_20260724/h1_2_ota_20260724_183622/REPORT.md
```

# Homecoming Batch, Part 2

Part 2 started on 2026-07-25. Its evidence root is
`B306_Part/logs/homecoming_20260725/`. The state entering Part 2 supersedes
the older end-state block above: B306 is `b306-imu-relay-v17`, the Fusion
Master is `dk-fusion-imu-relay-v7`, the tag is
`tag-fusion-link-v2-relay1`, UWB is healthy near 10 Hz, and the IMU is
stopped.

## Phase A — dynamic freshness from existing P1/P2 captures

### Pre-registered prediction and gate

Before running the analysis, the prediction was that the stationary
four-sample lattice would disappear on the physically excited P2 `gz`
channel. The decisive fresh-at-200-Hz gate required at most 25% identical
predecessors, run length 1 as the mode, and fewer than 20% of runs at lengths
3–5. A real latch required at least 60% identical predecessors and at least
50% of runs at lengths 3–5. The exact registration is preserved in:

```text
B306_Part/logs/homecoming_20260725/phase_a_freshness/PREDICTIONS.md
```

### Verdict — prediction falsified

**The installed JY61P does not deliver independently refreshed 200 Hz motion
samples under the current `0x1F=0x0004` configuration.** During genuine P2
yaw, `gz` had `77.22%` identical predecessors, run-length mode `4`, and
`94.74%` of runs at lengths 3–5. Its transition-derived rate was
`45.60 Hz`; the structural estimate is `200/4 = 50 Hz`.

The lattice persists under motion and cannot be explained as quantization of
a still sensor. Every accelerometer and gyro motion-window channel in all
three captures also had run-length mode 4. Accelerometer and gyro therefore
agree on the structural finding; their transition-rate lower bounds differ
because each motion excites different axes. The P2 `gz` result is decisive
because its 5–8 deg/s motion is far above one gyro LSB.

Phase A therefore requires the Phase-B bandwidth sweep. The earlier
approximately 50 Hz suspicion is **confirmed**, not retired.

Full per-capture, per-channel stationary/motion fractions, run histograms,
and rate estimates:

```text
B306_Part/logs/homecoming_20260725/phase_a_freshness/FRESHNESS_REPORT.md
B306_Part/logs/homecoming_20260725/phase_a_freshness/freshness_analysis.json
```

### P2 evidence hygiene and interpretation

The false P1-wrapper verdict was removed from
`p2_yaw_flat_61_0001_20260725_081322/summary.json`. It now says
`status=COMPLETE`, points to `p2_analysis.json`, and records why a
0.91-degree gravity change validates flat yaw instead of failing it. No
contradictory `FAILED` verdict remains in that evidence file.

P2's 3.30-degree discrepancy is attributed to the paper-line construction
and motion-window thresholding, not gyro scale error. The authoritative
same-data scale checks are P1's accelerometer-referenced ratios `1.0116` and
`0.9984`.

The two detected P2 pauses lasted 0.94 s and 0.50 s. Both are shorter than
the configured 1000 ms stationary time (`0x63=0x03E8`), so they alone do not
prove suppression across a longer stationary interval. That coverage comes
from the approximately 10-second stationary lead-in and 10-second tails:
the gyro retained small measured drift instead of being clamped to exact
zero.

## Phase B1 — chip-time characterization

Predictions were written before opening CDC in
`phase_b_chiptime/PREDICTIONS.md`. Thirty sequential reads of `0x30`–`0x33`
then established:

```text
0x30 = 0000 throughout
0x31 = 1000 throughout
0x32 = 0E02 ... 1A02 (low byte 02; high byte advances each second)
0x33 = 5 ms lattice, modulo 1000 ms
```

Unwrapped `0x33` advanced 11,650 ms over 11.600 s of host reply time. Mapping
the register replies to concurrent B306 `node_ms` through the 10 Hz UWB
records produced a short-run slope of `1.0041744` (+4174 ppm) with 11.14 ms
residual RMS. This includes non-atomic sequential register commands and BLE
latency and is not promoted to a precision oscillator measurement. It proves
the heartbeat properties needed for v18: 5 ms updates, 1000 ms wrap, near-
unity progress against the B306 clock, and a detectable sustained freeze or
backward discontinuity.

The characterization window contained 131 UWB frames over 13.003 s and every
monitored anomaly-counter delta was zero. Full results:

```text
B306_Part/logs/homecoming_20260725/phase_b_chiptime/B1_REPORT.md
B306_Part/logs/homecoming_20260725/phase_b_chiptime/b1_chiptime_analysis.json
```

## Phase B2 — measured power-up reset signature

The operator reported verbatim:

```text
FUSION PCB POWER CYCLED
```

Immediately after B306 re-attachment, the JY61P returned:

```text
30=0000 31=0000 32=2700 33=010E
```

That exact tuple is the measured Tier-1 power-up reset signature. The
power-cycle baseline also read `61=0000` and `1F=0004`. RRATE was then
restored without SAVE and verified:

```text
IMU RRATE OK request=000B readback=000B volatile=1 saved=0 step=readback err=0
IMU REG OK addr=03 readback=000B err=0
```

The IMU remained stopped, UWB remained running, and no probe was touched.
Evidence is indexed in `phase_b_chiptime/B2_REPORT.md`.

## Phase B3 — bandwidth sweep

Because Phase A confirmed that the four-sample lattice survived motion, B3
was mandatory. Predictions were registered before the first command. The
predicted modal progression was 4→2→1 for `1F=0004,0002,0000`.

The operator then supplied continuous multi-axis motion for three 30-second
captures:

| `0x1F` | Six-axis identical predecessor | Mode run | Runs 3–5 | Whole-vector rate |
|---:|---:|---:|---:|---:|
| `0004` | 74.85% | 4 | 94.71% | 50.32 Hz |
| `0002` | 1.30% | 1 | 0.00% | 197.41 Hz |
| `0000` | 0.68% | 1 | 0.00% | 198.64 Hz |

The 4→2→1 prediction was falsified: this device shows a threshold transition
from four-pull latching at `0004` to essentially fresh 200 Hz frames at both
`0002` and `0000`. Every run had zero sequence gaps, missing samples, I2C
errors, UWB/BLE/UART/relay errors, malformed records, and logger drops.

Phase C therefore uses volatile `0x1F=0x0002`. Its 98 Hz bandwidth already
delivers 197.41 Hz whole-vector freshness and matches the Nyquist edge of
the 200 Hz B306 sample rate. `0000` also refreshes at 200 Hz but exposes a
256 Hz sensor bandwidth and avoidable above-Nyquist noise/alias energy. This
is an explicit evidence-driven deviation from the pre-registered fallback
that would have selected `0000` merely because it worked.

The full per-channel table and raw evidence are in:

```text
B306_Part/logs/homecoming_20260725/phase_b_bw_sweep/B3_REPORT.md
B306_Part/logs/homecoming_20260725/phase_b_bw_sweep/bw_sweep_analysis.json
```

## Phase C — v18 deployment, receiver correction, and latency STOP

The pre-registered thresholds and transaction-shape decision are recorded in
`phase_c_health/PREDICTIONS.md`. `b306-imu-relay-v18` contains the health
subsystem and volatile `1F=0002`; no LED behavior was added. It built with
FLASH 202,272 / 499,200 bytes (40.52%) and RAM 77,068 / 262,144 bytes
(29.40%), both PASS, and an explicit zero-byte malloc arena. Its signed OTA
payload SHA-256 is:

```text
17a11fbf56fde1014d6415cc7ca82622877373193f2fb8826dd6e1911f5abbf7
```

Before OTA, v17 reported `imu_active=0`, satisfying capture exclusion.

The fast updater uploaded all 202,935 bytes and scheduled version `0.1.17`
for test. Its OS reset command succeeded. However, in the following
20-second observation plus an independent 60-second continuation, the
updater saw advertisements from other devices but never an exact
`BSF3C79` advertisement. It therefore could not reconnect and could not
produce the required `hash=match active=1 confirmed=1` proof.

The operator then physically power-cycled the Fusion PCB. A separate
verify-only client subsequently saw and connected to BSF3C79 and supplied the
strict proof:

```text
marker=b306-imu-relay-v18 hash=match active=1 confirmed=1
```

Thus v18 is installed and confirmed; the initial post-software-reset
advertising/reconnect failure did not roll it back. That reconnect anomaly is
retained as a finding.

The first schema-v3 Fusion Master, `dk-fusion-imu-relay-v8`, passed its build
gates but failed runtime acceptance after `FUSION_SCAN_STARTED`: its main-loop
heartbeat stopped and `LIST` was not processed. At the same time an independent
nRF54 observer captured 79 BSF3C79 advertisements in 20 seconds, exonerating
the peripheral. The v8-only enlarged per-call log buffer was restored to its
proven size in honestly bumped `dk-fusion-imu-relay-v9`. v9 passed with FLASH
15.73%, RAM 34.78%, malloc arena zero, then connected/subscribed and negotiated
2M PHY, DLE 251, and MTU 247. Its SHA-256 is:

```text
95b4739eb6443c7f848df1364aaeb57f5a18d1515b683341b547762452f7a9f1
```

### Transaction benchmark and mandatory stop

The v18 TIMER2 benchmark measured the old `0x34`/26-byte shape at 788 us and
the candidate `0x30`/34-byte shape at 968 us. The candidate is 180 us / 22.84%
slower than legacy, so it correctly failed the 100 us and 15% adoption gates;
the production path remains byte-identical (`imu_ext=0`).

The measured legacy result is itself 138 us / 21.23% above the inherited
approximately 650 us delta-t constant. This exceeds both pre-registered
tolerances and therefore triggers the Part-2 hard STOP. The 650 us figure was
an inherited approximate model value rather than a same-image measurement, so
the result does not by itself prove a v18 regression; it does prove that the
model constant and measured hardware value require reconciliation before a
long capture.

A final read-only snapshot after the operator's physical cycle confirmed:

```text
STATUS fw=b306-imu-relay-v18 id=3C79 ... imu=0/200Hz/N2 verify=PASS
IMU active=0 ... 61=0001 03=000B 1F=0002 ... h=1/0/1 hr=1/0 lat=788/968 ext=0
```

The reset was classified as `BOOT_RESET`, latched, and successfully recovered.
UWB remained near 10 Hz with zero transport/error-counter growth; protocol 3
had no malformed records or logger drops. The IMU remained stopped. The
Phase-C long capture and mid-capture provocation, Phase D, and Phase G were not
run. Full stop evidence:
`phase_c_health/C_LATENCY_STOP_REPORT.md`.

## Phase C-R — latency authority established and STOP cleared

The software-only two-speed experiment is complete. Its pre-registered
predictions, raw log, derived JSON, deployment evidence, and full report are
under `logs/homecoming_20260725/phase_c_rebaseline/`.

At the source-configured 400 kHz bus, N = 2, 8, 14, 20, 26, and 34-byte reads
fit `latency = 203 + 22.5*N` us with R²=1 and zero residuals. At diagnostic
100 kHz they fit `latency = 464 + 90*N` us, also with R²=1 and zero residuals.
The exact production-shaped `0x34`/26-byte read measured 788 us at 400 kHz
and 2805 us at 100 kHz. After the diagnostic section, the firmware restored
400 kHz and measured the production shape at exactly 788 us again; all
configure/restore/transfer error codes were zero.

The preregistered shortcut `a-3*b` missed its 50 us agreement gate by 8.5 us:
it yielded 135.5 and 194 us, a 58.5 us difference. This is a real prediction
failure. The cause is that three byte equivalents count 27 clocked bits but
omit START/repeated-START/STOP and remaining frequency-scaled control phases.
A joint two-speed model identifies 34.8 control-bit equivalents and one
common 116 us software-fixed term at both speeds. It predicts the separately
measured production shapes to 0 us at 400 kHz and 1 us at 100 kHz. The model
discrepancy is therefore resolved rather than suppressed.

The production decomposition is 585 us returned-data wire time, approximately
87 us addressing/control wire time, and approximately 116 us fixed software
time, total 788 us. The old 650 us inheritance is superseded. The unchanged
future-image gate is now measured-baseline delta <=100 us **and** <=15%;
at 788 us, the absolute limit remains controlling.

The unresolved pre-SCL versus post-SCL split lies inside `[0,116] us`, 2.32%
of the 0–5 ms refresh sawtooth. Same-axis accel-to-gyro burst skew is 135 us;
register-block shadow/latch behavior is unknown. With `ext=0`, Tier 1 reads
chip time in a separate 8-byte transaction every 50 ms; four unchanged checks
detect a frozen clock in approximately 150–200 ms. Its timestamp is inherited
from the preceding production-pull start, not captured at the chip-time read,
so it is heartbeat evidence and not an independent true-sample timestamp.

The installed/confirmed B306 marker is `b306-imu-relay-v18-cr1`; the DK was
restored to `dk-fusion-imu-relay-v9`, IMU was stopped, UWB remained healthy,
and probe `1050070698` was untouched. Full report:
`phase_c_rebaseline/C_R_REPORT.md`.

## Phase C acceptance — first reset recovered, unexpected second reset

The mid-capture power-cycle provocation was run over the Fusion Master's native
CDC. The 15-second pre-cycle gate passed with 1,500 IMU records, zero sequence
gaps, 150/150 healthy UWB records, and zero monitored counter deltas.

The expected disconnect occurred at `2026-07-25T08:20:54.986679Z`; reconnect
took 2.897559 seconds. B306 reported `BOOT_RESET`, `h=1/0/1`, `hr=1/0`, and
verified `61=0001`, `03=000B`, `1F=0002`. The post-reconnect IMU START passed
all volatile/no-SAVE predicates. The resumed stream then delivered 274 IMU
records / 548 samples with zero gaps and 8/8 healthy UWB records.

However, 6.744762 seconds after that START, a second
`FUSION_DISCONNECTED reason=0x08` occurred. The DK reconnected again and the
new boot independently classified/recovered `BOOT_RESET`, but IMU correctly
defaulted to stopped. The later `IMU STOP FAIL err=-120` therefore meant
“already stopped”; it was not a missing reply. The validator was corrected to
retain either STOP verdict and to reject a repeated disconnect explicitly.

The initial machine verdict was `FAILED_UNEXPECTED_SECOND_RESET`. The operator
then identified the stimulus artifact: the unreliable POGO contact briefly
re-powered and required a small twist to obtain complete power loss. One
intended ceremony therefore generated two real external power/reset
transitions. The final verdict for this run is **INVALID_STIMULUS**, not a
firmware failure and not a Phase-C PASS. A single clean provocation is rerun.
Full report:
`phase_c_powercycle_20260725_101937/PHASE_C_PROVOCATION_STOP_REPORT.md`.

Reset-source forensics narrows the second event. Both new boots report
`reset_reason=0x1`, which the nRF52840 MDK defines as pin reset; watchdog,
software reset, and lockup are `0x2`, `0x4`, and `0x8`. The only application
`sys_reboot()` is guarded by an explicit REBOOT command, absent from the raw
log. The 30-second watchdog was still being fed and cannot explain a reset
6.7 seconds after START. If the operator confirms only one physical power
action, the next suspect would therefore be the external RESET/power-integrity
path, not the v18 health subsystem. The operator's POGO explanation supplies
that external cause for this run.

### Clean provocation redo — PASS

The redo used one stable complete power interruption. The formal 15-second
pre-window carried 1,497 IMU records with zero sequence gaps, 150/150 healthy
UWB records, and zero monitored counter deltas.

Exactly one BLE disconnect occurred at `2026-07-25T08:30:37.441811Z`; the
bridge returned 7.255348 seconds later. There was no second disconnect. The
clean supply restart reported `reset_reason=0`, distinguishing it from the
invalid POGO run's `RESETPIN=0x1`.

Tier 1 reported and latched `BOOT_RESET`, `h=1/0/1`, `hr=1/0`, then restored
and verified `61=0001`, `03=000B`, `1F=0002`. The device-local fault and
recovery timestamps were 1,701,879 and 1,736,779 us, a 34,900 us recovery
interval. Across the RAM-destroying board reset, the host disconnect gap is
the conservative exclusion authority.

After an explicit host restart, the 30-second window delivered 2,994 IMU
records / 5,988 samples with zero sequence gaps, 265/265 healthy UWB records,
zero unexpected disconnects, and zero monitored counter deltas. `IMU STOP`
returned OK. Phase C's controlled provocation acceptance is **PASS**. Full
report:
`phase_c_powercycle_redo_20260725_102910/PHASE_C_PROVOCATION_PASS_REPORT.md`.

### Five-minute long acceptance — FAILED

The C-R directive did not specify the long-run duration, so five minutes was
pre-registered to match the batch's static acceptance scale. The run completed
300.012717 host seconds / 300.078 B306 seconds and stopped the IMU cleanly.

IMU delivered 29,842 records / 59,684 samples (99.473% of nominal), with zero
application sequence gaps. UWB frame delta was 3,001 at 10.000733 Hz. BLE
stayed connected and CRC, header, ring, notify-drop, malformed, and host logger
counters remained zero.

The run nevertheless failed two independent zero-error gates:

1. Sweep 2660 was `b306_missed_edge`, incrementing
   `orphan_strobe/edge/frame=1/2/1`. Hardware counters prove both edges were
   captured with no queue drop. The pulse timestamp was 271,405,608 us but the
   frame was not software-timestamped until 271,496,225 us, 90,617 us later.
   The 20 ms pairing window therefore expired a real pulse. This is UART
   parser/publish latency, not an electrical missing edge. The parser runs at
   priority 5 below the continuous IMU thread at priority 4; starvation/backlog
   is credible but not yet proven as the sole cause.
2. One separate chip-time I2C read failed at 291,398,657 us. It triggered
   class 9, marked a 40,082 us exclusion window, and recovered successfully in
   36,190 us. No recurrence followed. The periodic checker promotes one error
   directly to `I2C_BURST`, while the production fast path requires three
   consecutive errors; that threshold/name inconsistency is now evidenced.

The health system's detection/recovery behavior passed, but the formal
long-run gate is **FAILED**. Phase D was not started. Full report:
`phase_c_long_20260725_103351/PHASE_C_LONG_STOP_REPORT.md`.

## Multi-unit time alignment

**A1 verdict: no, two tags' current frame `sweep` values cannot be treated as
a common superframe index.** Master `EPOCH` is a per-recipient remaining delay
to one future deadline (`master_multi_app.c:1621-1649`), and the tag explicitly
converts it to a local deadline (`uwb_tdma.c:63-80`). The public
`uint32_t sweep` is the tag-local `ss_twr_init_sweep_count`
(`ss_twr_init.c:3371-3379`), reset to zero on initiator load
(`ss_twr_init.c:2363-2377`). It wraps modulo 2^32, or after 13.61 years at
exactly 10 Hz. A live re-CFG does not reset it; a tag reboot does; a Master
restart does not assign a replacement value.

The selected minimal correction is a common Master-assigned
`SUPERFRAME_BASE` in each CFG round. The tag retains its local counter for
maintenance and publishes `base + schedule_cycle` through the existing
four-byte `sweep` field. The 96-byte UART record therefore does not grow.
Estimated scope is 80–120 C lines plus 80–120 test lines across Master CFG,
tag parsing/runtime/output, status, wrap, reboot, and sequential-delivery
tests. It is specified here but deliberately not implemented in C-F.

The existing cr1 IMU-running capture supplied 3,001 usable strobe/index pairs.
The fit `t_TIMER2 = a + b*sweep` gives:

- `b=99,993.584473 us`, or -64.155273 ppm from nominal 100 ms;
- residual σ 97.188944 us, absolute p95 130.004376 us, and absolute maximum
  288.681128 us;
- signed range -288.681 to +257.298 us, skew -0.133, and lag-1 correlation
  -0.381.

The slope disagrees with the independently supplied TIMER2-versus-DW result
of -12.376 ppm (σ 0.512 ppm) by -51.779 ppm. TDMA phase follows the tag
scheduling clock, so the superframe route is not a bare DW-crystal ratio.
Under a zero-mean IID assumption only, offset σ would shrink to 1.774 us at
5 minutes and 0.724 us at 30 minutes. The earlier interpretation of lag-1
-0.381 was backwards: negative covariance makes the mean converge faster than
IID and its late-then-early sign is consistent with absolute-deadline phase
correction. The observed 0.289 ms maximum remains the conservative working
bound and is below the P4 `<10 ms and constant` requirement.

The archived one-hour attempt contains only 18,272 monotonic pairs / 30.991
minutes of continuous TIMER2 authority before rollover censoring. Across 26
overlapping five-minute fits, slope ranged from -64.638 to -63.694 ppm
(0.943 ppm peak-to-peak); the final window was 0.878 ppm below the first. The
pre-registered several-ppm drift prediction therefore failed, and the
available prefix shows no material threat to the 10 ms/30-minute budget.
Because the second half-hour and periodic temperature are absent, this is not
a full-hour temperature-stability proof. Sliding/piecewise host fitting remains
supported, not mandatory from this evidence alone.

The fitted residual is not one-sided (55.08% positive). The independent tag
deadline instrument is one-sided, 0–1,129.15 us with mean 14.465 us; fitting
necessarily removes its intercept/mean. There was no BLE event and only one
fixed slot in the formal capture, so neither correlation is identifiable.
Sweep-modulo mean peak-to-peak spans were 6.10 us (mod 8), 27.60 us (mod 10),
and 31.03 us (mod 35). A no-IMU segment processed by the same script had
nearly the same slope (-63.760 ppm) but σ 33.197 us and p95 34.174 us. The
threefold routine-width difference is coexistence capacity evidence, not a
causal IMU estimate, because image and session also differed.

Decision record: this hardware generation uses global-superframe/local-TIMER2
post-hoc fitting after the global-base debt is closed. Host-issued BLE sync is
rejected because CI=437.5 ms quantises arrival before USB/host jitter. UTC is a
session label only, never synchronization. Vicon/video alignment uses a sharp
motion event. The next generation should use DW3000 over-air beacon radio
timestamps for direct sub-microsecond alignment.

Assembly checklist:

- **MET AS INPUT / IMPLEMENTATION OPEN:** the current local-index defect and
  bounded global-base correction are known; implement before multi-unit
  science capture.
- **MET:** residual σ/p95/max are measured against the `<10 ms` requirement.
- **MET:** `tools/analyze_multiunit_alignment.py` was exercised unchanged on
  IMU-running and no-IMU data.

Full report and JSON:
`homecoming_20260725/multiunit_alignment_20260725/`.

## Phase C-F — structural timestamp repair and re-run PASS

`b306-imu-relay-v18-cr2` moves the UART arrival timestamp into the
`UART_RX_RDY` callback and carries it with the DMA bytes through the ring.
Parser stalls are now publication latency, not a mutation of the pairing
timestamp. The callback adds one TIMER2 capture/expansion and one eight-byte
timestamp field; parsing, CRC, pairing, allocation, logging, and BLE remain
outside it.

The complete cr1 distribution justified changing the pairing window from 20
to 50 ms: accepted p99 was 17.534 ms and maximum 17.672 ms; including the one
derived lost-frame delay changed maximum to 90.617 ms but p99 only to 17.535
ms. Fifty milliseconds leaves 32.466 ms above normal p99 and remains half the
100 ms sweep interval, so adjacent sweeps are unambiguous.

The priority inversion was not historically documented as intentional:
parser priority 5 predates IMU priority 4. cr2 deliberately retains it because
the structural timestamp repair removes the correctness dependency and a
priority shuffle would risk IMU cadence. Health class 9 is renamed
`I2C_CONSECUTIVE_FAILURES`; every failed transaction increments the I2C
counter, success clears the streak, and only three consecutive failures
escalate. Backward/frozen chip time still recovers immediately. Production
and health reads are serialized in one IMU thread and share one mutex, so the
cr1 asymmetry was not an I2C concurrency race.

The 60-second proof passed with 600/600 healthy, paired UWB records and zero
counter growth. Its p50/p90/p99/max were
14.478/14.934/17.143/17.205 ms.

The formal 300.000-second run then passed every gate: 3,000/3,000 healthy and
paired UWB records, counter-derived UWB rate 10.001 Hz, zero sequence gaps,
zero unpaired edges/frames/strobes, zero transport errors, zero unexpected
health events, no disconnect, and clean `IMU STOP OK`. The formal
strobe-to-frame p50/p90/p99/max were
14.431/14.742/17.125/17.189 ms.

The accepted IMU stream contained 59,946 samples on an exact 5,000 us
timestamp lattice, interrupted by 10 longer intervals representing 53 skipped
deadlines. Across its 299.990-second timestamp span the honest effective rate
was 199.823 Hz. The nominal setting remains 200 Hz, but the stream is not
described as losslessly fetching every refresh.

OTA installed the signed cr2 payload with SHA-256
`b4bb99173fc4911f89000576f049a975fac7acd9695b4cbed24880871c406e78`.
The updater uploaded all 204,292 bytes but its immediate post-reset verification
raced the expected disconnect and falsely ended `post_verify_failed`; restored
DK v9 native CDC independently confirmed the cr2 marker and clean status.
This verification-state-machine behavior is filed as a host-tool debt, not an
OTA failure.

The capacity interpretation remains deliberately narrow. A prior IMU-stopped
300-second run paired 2,907/2,907; cr1 first exposed coexistence interference
with IMU streaming; cr2 repaired it and passed one single-unit five-minute
run. Three simultaneous units are not certified by this result.

Full report:
`homecoming_20260725/phase_c_f/PHASE_C_F_REPORT.md`.

## Part 3 Phase A — freshness CLOSED

### Headline finding — effective freshness is bandwidth-dependent

**Unambiguous verdict:** at volatile `0x1F=0004`, genuine P2 yaw motion retained
a four-pull lattice (`gz` 77.22% identical predecessors, modal run 4, 94.74%
of runs at lengths 3–5), giving a 45.60 Hz transition lower bound and an
approximately 50 Hz structural update rate. Accelerometer and gyro agreed:
every motion-window channel had modal run 4.

The conditional bandwidth sweep had already been pre-registered and executed
with continuous multi-axis motion. Its whole-vector result was 50.32 Hz /
modal run 4 at `0004`, 197.41 Hz / modal run 1 at `0002`, and 198.64 Hz /
modal run 1 at `0000`; every device and host error delta was zero. The
pre-registered 4→2→1 prediction was falsified: this unit has a threshold
response, 4→1→1. Operational Phase-C sessions use volatile `0002`, which is
fresh at the 200 Hz pull cadence without exposing the 256 Hz bandwidth of
`0000`. This closes effective-bandwidth characterization and is a capability
number, not an assembly gate.

Evidence:
`homecoming_20260725/phase_a_freshness/` and
`homecoming_20260725/phase_b_bw_sweep/`.

This qualifies every historical capture that actually reported
`1F=0004`: the remote R4/R5/R6 static, boundary, and BLE-stress series; the
v12–v15 rate/duplicate investigations; the H2 P1/P2 auto-zero experiments;
the Phase-A motion capture; and the `0004` arm of the bandwidth sweep. Those
captures pulled at a 200 Hz schedule but contained only about 50 Hz independent
sensor updates. Their transport, timing, register, and integral observations
remain valid; claims that they represented 200 independent sensor refreshes do
not.

The auto-zero conclusion is specifically unaffected. P1/P2 compared gyro
integrals with accelerometer-derived or constructed angles. Repeating one
sample over its four pull periods gives the same time integral as applying
that value once for the full refresh period, so the observed agreement still
stands.

R4's later-withdrawn “about 50 Hz” hypothesis was substantively correct. Its
withdrawal was nevertheless required because the instrument used to support
it was invalid; a correct hypothesis does not retroactively validate bad
evidence.

The L-series fusion simulation used a synthetic 120 Hz IMU grid. Hardware at
`0004` (50.32 Hz effective) was below that basis, while selected `0002`
(197.41 Hz) is above it. The `BW=0x0002` choice is therefore what makes the
simulated fusion gains achievable on this unit: one 100 ms UWB interval now
contains about 20 independent IMU updates rather than about 5.

## Part 3 Phase A+ — alignment follow-ups

A+1 corrects the sign error in the earlier interpretation. Lag-1 residual
correlation `-0.381` makes the mean converge faster than IID because covariance
terms are negative. Its late-then-early sign is also the structural signature
of absolute-deadline scheduling: errors are corrected rather than accumulated.
The conservative measured maximum remains 0.289 ms.

A+2 found that the archived “one-hour” run contains only 18,272 monotonic
pairs / 30.991 minutes before TIMER2 rollover censorship. Twenty-six
overlapping five-minute fits ranged from -64.638 to -63.694 ppm, only
0.943 ppm peak-to-peak; the last-minus-first change was -0.878 ppm. The
pre-registered several-ppm prediction missed. The available prefix shows no
material threat to the `<10 ms` 30-minute budget, but is not a full-hour
temperature proof. Sliding/piecewise fitting stays supported and is not
mandated by these data. The likely physical contributor to the
DW-versus-schedule slope difference is the tag scheduler's RTC/LF clock rather
than the HFXO.

A+4 confirms why the old event counter cannot be common: a slot skipped for
excess sleep/spin lateness is bypassed inside the wait loop and never reaches
any `ss_twr_init_sweep_count++`, whereas completed and explicitly cut-short
sweeps do. Relay2 now publishes a scheduled-time index from one
Master-assigned `SUPERFRAME_BASE` and common deadline. Wrap, reboot fallback,
missed-event independence, and sequential CFG delivery tests pass; tag and
Master compile. The UART frame remains 96 bytes. A Master restart intentionally
ends the time-domain segment; no NVS writes are added.

The A+4-only relay2 build passes at FLASH 207,156 / 228,864 B = 90.51% and RAM
55,168 / 65,536 B = 84.18%, only +152 B FLASH and +32 B RAM from relay1.
Artifacts are staged, not deployed. D2 has only 537 B of RAM headroom to the
85% gate and must be measured on top of A+4.

Detailed A+4 report:
`homecoming_20260725/phase_a_plus/A_PLUS_4_REPORT.md`.

### A+3 controlled coexistence baseline

The one-session, one-image A/B run falsified the pre-registered 2.9×
IMU-broadening prediction. With IMU on, residual σ / absolute p95 / absolute
max / lag-1 were 99.829 / 135.154 / 279.480 µs / -0.380793. With IMU off they
were 101.719 / 132.630 / 285.932 µs / -0.380754. The σ ratio was 0.9814,
below the 1.5 causal gate. Slopes were also nearly unchanged at -64.314 and
-64.168 ppm.

Both halves contained about 3,000 healthy UWB records, every anomaly-counter
delta was zero, the IMU-on stream had zero sequence gaps, and the off half had
no IMU records. Thus data integrity passed while the causal prediction failed:
the old 97.2 versus 33.2 µs cross-image/cross-session contrast was confounded,
not independent evidence of UWB/IMU interference.

This is a headline single-unit coexistence result: the residual sigma ratio
of **0.9814** shows no measurable UWB timing penalty from 200 Hz IMU streaming
in the controlled same-image/same-session A/B. It falsifies only the earlier
cross-session approximately-3× claim. It does **not** falsify the real
parser-stall correctness failure caused by IMU-thread preemption; that was a
different subsystem and was repaired by timestamping in the UART callback.
It also says nothing about Fusion Master aggregate behavior with three or more
BLE links, which remains open until additional boards exist.

Evidence and report:
`homecoming_20260725/phase_a_plus3_20260725_121747/`.

## Part 3 Phase E1 — gyro-live acceptance

The gyro and 65.5-second behavior passed, but the zero-device-error expectation
did not. The five-minute run and independent 75-second boundary run each
incremented `imu_i2c_err` once, first visible at node 187.760 s and 29.717 s
respectively. Neither became a three-consecutive-failure health event:
`imu_hi2c`, every health class, and recovery counters stayed zero; record
sequence gaps stayed zero. Two fresh-reboot occurrences make this a real
finding rather than logger noise.

The five-minute run delivered 3,000/3,000 healthy UWB records. Pair
p50/p90/p99/max was 14.446/14.966/17.139/17.236 ms. Gyro mean/std were
`[-0.1828,-0.3912,+0.0017]` / `[0.0492,0.0413,0.0168] deg/s`, with 236 unique
triplets and no all-axis-zero triplet. The timestamp stream skipped 173
nominal 5 ms slots and measured 199.423 Hz.

The boundary run delivered 750/750 healthy UWB records; pair
p50/p90/p99/max was 14.432/14.902/17.104/17.185 ms. Across the 65.5-second
boundary, gx mean moved -2.900→-2.987 raw and gy -6.432→-6.413 versus about
0.58 LSB standard deviation. There was no discrete bias step or three-axis
clamp. Its timestamp stream skipped 11 slots and measured 199.853 Hz.

Both links reported actual `CI=50 ms`, slave latency 0. Detailed report:
`homecoming_20260725/phase_e/E1_REPORT.md`.

## Part 3 Phase E2 — full S1–S7 over native CDC PASS

The S2 amendment is deliberate and does not weaken the sensor gate. Since
`0x61`, `0x03`, and `0x1f` are intentionally volatile and no SAVE is allowed,
boot-time `verify=PASS` is not meaningful. S2 now proves that B306 is
reachable and the IMU is stopped; S6 remains the strict gate and accepted only
this readback:

```text
IMU START OK err=0 61=0001:P 03=000B:P 1F=0002:P volatile=1 saved=0
```

The first E2 attempt stopped correctly at S3 before starting the IMU.
Master_Tag's `mode recv` command intentionally rebooted the carrier and
re-enumerated its CDC device, while the host tool retained the dead file
descriptor. The live host tool was repaired to close and reopen the same stable
Master_Tag by-id path after this expected reboot. No firmware, probe, or
physical target was touched. All 12 host tests passed after the repair.

The rerun passed S1–S7. The raw log records the expected disconnect and
successful reopen:

```text
MASTER_TAG_EVENT expected_mode_recv_disconnect=...
MASTER_TAG_REOPEN port=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
```

S3 completed in one attempt and saw two real `CFG_OK ... LIVE=1 RUN=1
STATE=RUNNING` replies. S4 did not use either reply as transmission proof:
hardware frame and rise counters each increased by 277, and 42 observed UWB
records were healthy. The long node interval includes Master configuration
time, so its derived 8.392 Hz is accepted by the specified 8–12 Hz gate.

The exercised 10.003-second S7 sentinel observed 100 healthy UWB records,
1001 IMU records, zero IMU sequence gaps, and 100 frame plus 100 strobe-rise
increments (9.997 Hz). Every transport, pairing, health, logger, and relay
anomaly-counter delta was zero. The implementation's failure branch sends
`IMU STOP` before raising while leaving TDMA/UWB untouched; the success path
was followed here. Formal stop then returned `IMU STOP OK`, confirmed
`imu_active=0`, and deliberately did not clear TDMA.

Evidence:
`homecoming_20260725/phase_e/e2/fusion_session_20260725_124248/`.
The raw-log SHA-256 is
`017757b0e56fec78c12bec6fbf46c37480fa7c6e1f661681f93b974e08aedac8`.

## Part 3 Phase E1 — operator-quiet rerun; PASS after dated gate correction

The original five-minute and boundary captures are retained but reclassified
**environmentally uncontrolled** because they predated the mandatory
`E1 START` gate. Their transport and health evidence remains valid; their
static noise values do not serve as formal quiet-environment acceptance.

Both reruns were armed and draining CDC before the request, began only after
the exact `E1 START` token, and printed the completion release immediately
when each formal window closed.

The quiet five-minute window reduced acceleration-norm σ from 0.003655 to
0.000693 g (-81.0%). Gyro σ changed from
`[0.04920,0.04133,0.01685]` to `[0.03370,0.03489,0.00511] deg/s`
(-31.5%/-15.6%/-69.7%). Means remained close; controlled gz was
`+0.00020 deg/s`. Desk vibration therefore materially inflated noise but not
mean bias.

The controlled five-minute run delivered 3,000/3,000 healthy UWB records and
59,980 IMU samples at 199.922 Hz. Pair p50/p90/p99/max was
14.434/15.027/17.148/17.211 ms. It nevertheless failed the zero-health-event
gate: one recovered class-2 `CHIP_BACKWARD` event occurred 127.2 seconds into
the formal window, recovery took 36.079 ms, and one IMU sample was missing.
An earlier same-class event occurred during the armed pre-token interval,
proving recurrence. I2C errors stayed zero.

The independent controlled 75-second boundary run passed: no health or I2C
events, no sequence gaps, 750/750 healthy UWB records, and 199.960 Hz
effective IMU rate. There was no discrete bias step across 65.5 seconds.
Pair p50/p90/p99/max was 14.436/14.956/17.137/17.180 ms.

On 2026-07-25 the acceptance gate was deliberately corrected without changing
the historical record. Verdict A is the quiet measurement, UWB/transport,
65.5-second boundary, and successful recovery; it **passes**. Verdict B
retains the recovered class-2 event and its one missing sample as measured
sensor-health evidence, not a gate. This matches the C-F/F4 escalation
semantics instead of requiring a transient-free five-minute draw. Therefore
**E1 is PASSED** on the existing data; no rerun is required.

Detailed controlled report:
`homecoming_20260725/phase_e/E1_CONTROLLED_REPORT.md`.

## Part 3 Phase E3/E4 — E3 PASS after dated gate correction

The 30-minute native-CDC run completed 1,800.169 host seconds without a
disconnect. It carried 18,003/18,003 healthy UWB records at 10.0011 Hz,
179,781 IMU records / 359,562 samples with zero PC sequence gaps, and 30
board-local plus 30 live Tag-relay commands with complete correlated replies.
All transport, pairing, notify, logger, relay, and health-escalation deltas
were zero.

One isolated raw I2C transaction failed at node 1,388.094 s. It did not
escalate or create an end-to-end sequence gap.

On 2026-07-25 the gate was deliberately corrected without changing that
record. Verdict A covers transport and coexistence and **passes**: zero PC
gaps, disconnects, transport/pairing/notify/logger/relay errors, and zero
escalated health events. Verdict B records the one isolated raw I2C error with
its timestamp and non-escalation. Requiring zero raw errors contradicted the
established three-consecutive-failure escalation semantics. Therefore
**E3 is PASSED** on the existing run; no rerun is required.

Strobe-to-frame p50/p90/p99/max was
14.449/16.739/17.149/19.402 ms. IMU effective rate was 199.742 Hz.

The DK was honestly bumped to `dk-fusion-imu-relay-v10` solely to put current
CI and slave latency directly in `FUSION_BRIDGE_READY`. It passed FLASH/RAM at
15.74%/34.78%, was flashed only through explicit SNR `683234364`, and runs
with final CI 50 ms and slave latency 0.

Lower-envelope-normalized end-to-end variable latency was:

- UWB p50/p95/p99/max 28.323/49.587/85.505/182.179 ms;
- IMU p50/p95/p99/max 29.417/50.836/86.349/188.612 ms.

For all records, p95 52.021 ms is 1.040 times the final 50 ms CI and maximum
188.565 ms is 3.771 CI (IMU maximum 188.612 ms is 3.772 CI). Slave latency is
zero, so link-event skipping by the peripheral is not the source of the tail.
The roadmap's exact wording is “1 h zero logical-batch loss; end-to-end
latency <150 ms,” with no percentile. Conservatively judged as an unqualified
maximum, p95 passes but the measured maximum fails; E4 does not claim P3's
separate one-hour duration gate.

The core through p95 matches the predicted 50 ms CI-dominated spread, but the
distribution is not uniformly bounded by one CI. Decomposition places the
long tail before the DK: B306→DK IMU p95/max is 47.726/186.673 ms, whereas
DK→PC is 11.248/33.070 ms. USB is not the first latency target.

Detailed report:
`homecoming_20260725/phase_e/E3_E4_REPORT.md`.

## Part 3 Phase D — D2 staged; D1 STOP

The resolved pin table was completed before code:
`homecoming_20260725/phase_d/PIN_RESOLUTION.md`.

D2 is staged only as required. The combined `SUPERFRAME_BASE` + LED image
`tag-fusion-link-v2-relay2` passes at FLASH 207,676/228,864 B = 90.74% and
RAM 55,176/65,536 B = 84.19%. The signed payload SHA-256 is
`a052e2496a3ff330b745ae39927aaa1ff0fec9254422f0339cafb6aae100b6a8`.
The policy host test passed; Tx/Rx indicators remain driven by the DW1000
hardware LED outputs. It was not deployed.

D1's corrected B306 build passes at FLASH 40.88% and RAM 29.42%. Its signed
payload SHA-256 is
`35661ba2b2c9f14604779eb0fb7bdcbe55e3342419b37996510457483f70339e`.
The short sweep-timing check passed on the corrected implementation:
600/600 UWB records healthy at 10.0142 Hz, no UWB anomaly deltas, and
strobe-to-frame min/p50/p99/max
14.209/14.437/17.134/17.169 ms. The simultaneous overall sensor run failed
separately on one known JY61P `CHIP_BACKWARD` recovery.

State forcing then found that normal reboot transients increment strobe
orphan/queue counters and the LED-A fault latch has no clear path, so a healthy
post-reboot node can remain FAST indefinitely. D1 therefore does not yet meet
the honest-indicator acceptance.

The run also violated standing protection 7: an earlier workqueue candidate
and the corrected parser-worker bytes were both deployed under
`b306-imu-relay-v19`. This marker reuse cannot be described as an accepted
release. The batch STOP condition is active; Phase G was not started.

Detailed reports:

```text
homecoming_20260725/phase_d/D1_REPORT.md
homecoming_20260725/phase_d/D2_REPORT.md
```

## Part 3 Phase D-fix — v22 deployed; all indicator states accepted

`b306-imu-relay-v19` is permanently retired. The production build now enforces
one embedded marker and exact marker-to-signed-SHA identity. The corrected
image is `b306-imu-relay-v20`, signed SHA-256
`e0f456531a164528c184d0188e560fd7c0f04fc68b8a75be1698a29960ece540`.
It passed FLASH/RAM at 40.93%/29.42% and is installed on `BSF3C79`.

The `SENDING` UWB fault is now a five-second recent window with an explicit
ten-outcome startup exclusion. It self-heals without `COUNTERS CLEAR`.
Healthy paired frames produce 20 ms event pulses. Faults on both `SENDING` and
`PAIRED` use a grouped double-blink plus long pause, so fault and healthy event
flicker differ in kind rather than rate. The `PAIRED` IMU-health indication
deliberately remains latched until acknowledgement or reboot.

Blind physical checks passed for no UART, healthy UWB, BLE disconnected, BLE
connected with IMU stopped, BLE connected with IMU streaming, and healthy
post-reboot settling without a counter clear. The operator's final state-8
observation was `SENDING: fast flashing`, matching the approximately 10 Hz
healthy event stream. Historical startup orphan counters remained nonzero but
did not hold the new recent-window indicator in fault.

The v21 closeout added exactly one explicit indicator-input hook:
`TEST ONLY LED SENDING FAULT`. With that stimulus, the operator identified
`SENDING` as two quick flashes followed by a pause. After injection stopped,
the corrected answer list let the operator distinguish the self-healed
approximately 10 Hz event flicker from that grouped alternative. States 3 and
8 therefore passed without claiming that synthetic input exercised the
already unit-tested CRC/header/pairing detectors.

A real physical cold start then produced `h=1/0/1`, but the v21 `PAIRED`
100 ms doublet was consistently perceived as one slow flash. That State-7
failure remains recorded. v22 made exactly one correction: only `PAIRED` uses
two 250 ms flashes separated by 250 ms, then a 1.25 s pause. After a clean
single cold start, three pre-clear reads remained `h=1/0/1` and the blind
operator answer was exactly “two quick flashes then a pause.” State 7 passed,
so all eight physical indicator states and Phase D are now accepted.

`b306-imu-relay-v22` passed at FLASH 40.95% and RAM 29.42%. Its signed
SHA-256 is
`9a236d3a09fc31e38aab33d1a560bc76d80a7e0878b5a73e255a9ae77fe2263b`.
The OTA upload completed; a later read-only image-state proof reported marker
v22, digest match, `active=1`, and `confirmed=1`. The first full updater
post-reset check had terminated before reconnection and was honestly retained
as a tooling failure rather than a payload failure.

F4 closes the earlier JY61P event as a spontaneous `CHIP_BACKWARD` event:
36.438 ms recovery, one lost IMU sequence, no contemporaneous B306 reboot or
BLE reconnect. A second independent class-2 reset/recovery occurred during
F3, confirming recurrence and making the health-window instrumentation
mandatory first-class evidence for Phase G.

The subsequent all-log audit corrected the event count again: E1's controlled
capture already contained two independent class-2 events, so the complete
record is four events over 6,919.796 seconds (115.330 minutes) of captured
IMU-active exposure. The all-history quotient is one event per 28.83 minutes,
predicting 2.77 events in 80 minutes and 1.04 in 30 minutes. This is a weak
estimate: only four events, early firmware had unequal classifier coverage,
and the classification-capable subset implies an even shorter 17.61-minute
interval. The cr2 five-minute run and E3 30-minute run were genuinely
class-2-free.

Phase G no longer has a zero-health-event gate. It will publish Verdict A
(TIMER2 rollover) and Verdict B (JY61P health) separately. A class-2 event
only makes Verdict A `INCONCLUSIVE` if fault or recovery falls within the
pre-registered ±2.0-second window around the 71.58-minute boundary. One such
hit causes exactly one repeat; a second hit on the repeat has pre-registered
joint coincidence probability `5.33e-6` and opens a TIMER2→JY61P causation
investigation instead of a third run. The class-2 rate is also explicitly a
lower-bound observation. The 1 ms chip-time field resolution is not the
detection floor: with observations about 50 ms apart, a backward step below
roughly 50 ms only shortens the observed positive delta, while the two-second
class-4 test's 5% tolerance can miss an isolated forward jump below roughly
100 ms. This approximately 1–50 ms backward / under-100 ms forward blind
band is material relative to the 0–5 ms asynchronous-refresh sawtooth.
Phase G v23 must record the per-health-check
`chip_delta - elapsed_B306_interval` distribution and signed/absolute extrema
without adding a new detector. The v21 marker is assigned to the Phase-D
indicator stimulus and v22 to the physical `PAIRED` legibility correction;
Phase G moves to v23.

Detailed reports:

```text
homecoming_20260725/phase_d_fix/PHASE_D_FIX_REPORT.md
homecoming_20260725/phase_d_fix/F4_JY61P_TRIAGE.md
homecoming_20260725/phase_d_fix/JY61P_EVENT_RATE.md
homecoming_20260725/phase_d_fix/PHASE_G_GATE_AMENDMENT.md
homecoming_20260725/phase_d_fix/V21_V22_CLOSEOUT.md
```

## Part 3 Phase F — superseded and out of scope

The 2026-07-25 scope correction deletes lever-arm work, axis mapping, CAD
extraction, estimator inputs, and reference-point selection from the batch.
No pose or derivation remains pending. The historical Phase-F report is marked
`SUPERSEDED — OUT OF SCOPE`; it cannot gate raw capture, session flow, or an
acceptance run. `fusion_config.json` now describes raw-recording scope and
contains no lever-arm safety gate.

## Part 3 Phase G v23 — instrumentation built, not deployed

`b306-imu-relay-v23` now records the pre-registered per-health-check signed
chip-time residual, count, signed extrema, maximum absolute value, and a fixed
21-bin distribution. `COUNTERS CLEAR` starts a capture window and
`IMU DELTA=0/1/2` report all evidence. The values have no path into detection
or recovery, so the existing health and three-consecutive-I2C semantics are
unchanged.

The pristine build passes at FLASH 205,440/499,200 B = 41.15% and RAM
77,260/262,144 B = 29.47%. The signed payload SHA-256 is
`e96bc8d221d453e3dc929e5ab24f0939aae36bce4143aa0642d9ccc4b9e09f37`.
Marker guard, delta/wrap tests, LED test, and all 12 Python tool tests pass.
This was build-only; no OTA or rig command was issued.

Detailed report:
`homecoming_20260725/phase_d_fix/V23_BUILD_REPORT.md`.

### Pre-Phase-G reconciliation

Phase G remains **BLOCKED pending only Phase F's minimal 90-second axis
binding and an explicitly authorized v23 deployment/preflight**. Phase A and A+1
through A+3 are complete; A+4 is
implemented and built, with its deployment intentionally deferred to Phase H.
E1, E2, and E3 pass; the 2026-07-25 E1/E3 adjudication correction retains all
raw sensor-health evidence under Verdict B. E4 characterization is complete
and found an approximately 188.6 ms maximum long tail. The v23
chip-time-delta implementation/build is complete. The accelerated and real
80-minute Phase G runs must not start until the remaining physical axis
binding and v23 deployment/preflight are closed. Full item-by-item evidence is in
`homecoming_20260725/phase_d_fix/PRE_PHASE_G_STATUS.md`.

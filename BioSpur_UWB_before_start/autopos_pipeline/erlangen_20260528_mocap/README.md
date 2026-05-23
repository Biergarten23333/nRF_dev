# Erlangen 2026-05-28 MoCap Field Folder

This folder is the single entry point for the Erlangen OptiTrack validation run.

Start here:

```text
first_step.md
```

Short experiment plan:

```text
docs/experiment_plan_short.md
```

On-site solver sanity check:

```text
solver/README.md
```

Field helper commands:

```bash
bs_init
bio_ports
```

`bs_init` is expected to source `tools/erlangen_aliases.sh`, set the two
master CDC paths, set the two master J-Link SNRs, and run
`bio_setup erlangen_20260528_optitrack`.

Output data go under:

```text
captures/
```

Main short commands after setup:

```bash
bio_usb_on
static -id PORT_TEST -s 20
bio_check_latest

sweep  -id SW01
us30   -id US01
static -id ID01
roto   -id R01
wand   -id W01
```

Baseline timing:

```text
tail900 start5
A 1200 us, B 2200 us, C 3200 us, D 4200 us, E 5200 us, F 6100 us, G 7000 us, H 7900 us
```

Current capture logic:

```text
TR-only, 10 Hz, explicit target BS IDs.
No old static/roto/motion profile split.
```

## Offline Field Runbook

Assume there is no Wi-Fi during the professor/lab test. Do not depend on
online help. Use this local checklist.

One-time Linux USB power rule, preferably before going on site:

```bash
sudo tee /etc/udev/rules.d/99-biospur-usb-power.rules >/dev/null <<'EOF'
# BioSpur field testing: disable USB runtime autosuspend for CDC/J-Link devices.
ACTION=="add|change", SUBSYSTEM=="usb", ATTR{idVendor}=="1366", TEST=="power/control", ATTR{power/control}="on"
ACTION=="add|change", SUBSYSTEM=="usb", ATTR{idVendor}=="1915", TEST=="power/control", ATTR{power/control}="on"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

At the lab, after plugging the powered USB hub:

```bash
bs_init
bio_usb_on
bio_ports
```

Expected ports:

```text
Master_Anchor CDC: usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
Master_Tag CDC:    usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00
Master_Anchor SNR: 960148546
Master_Tag SNR:    1050070698
BSF66F SNR:        760186115
```

Current safe capture defaults in `tools/erlangen_aliases.sh`:

```text
BIOSPUR_RESET_MASTERS_BEFORE_CAPTURE=1
BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE=0
BIOSPUR_RESET_TAG_BEFORE_CAPTURE=1
BIOSPUR_RESET_ANCHOR_BEFORE_SWEEP=1
BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1
BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE=1
```

These mean:

```text
Reset Master_Tag by J-Link before static/roto/wand.
Do not reset Master_Anchor before tag capture; otherwise it can race Master_Tag
and grab BSF66F during boot discovery.
Reset Master_Anchor by J-Link before AutoPos sweep.
Do not force Master_Anchor through AUTOPOS before every tag capture.
Keep an already-online Master_Tag -> BSxxxx link instead of clearing it.
```

Recommended no-network test sequence:

```bash
static -id PORT_TEST -s 20
bio_check_latest

sweep -id SW01
bio_check_latest

static -id ID01 -s 120
bio_check_latest

static -id ID02 -s 120
bio_check_latest

roto -id R01 -s 120
bio_check_latest

wand -id W01 -s 120
bio_check_latest
```

Good signs during `static`:

```text
[capture] anchor_preflight=skip
[capture] tag_links=reuse
[CAPTURE] link setup passive: ready=BSF66F (1/1)
[CAPTURE] configure: TDMA CFG verified match=true
success: true
```

Bad sign and meaning:

```text
[CAPTURE] link setup passive: ready=- (0/1)
```

This means Master_Tag has not built a ready BLE/NUS link to the target. In the
2026-05-19 test this happened because the old script cleared an already-good
BSF66F link with `mode recv`. The helper now defaults to `tag_links=reuse`.

If `ready=-` still appears:

```bash
bio_ports
bio_reset_masters
static -id RETRY01 -s 20
bio_check_latest
```

If CDC/J-Link commands hang or a master CDC disappears:

```bash
bio_usb_on
bio_reset_masters
bio_ports
```

If the terminal is stuck, stop it with `Ctrl-C`, then check:

```bash
ps -eo pid,ppid,stat,etime,wchan:24,cmd | rg 'run_dual_master|run_recv_tdma|verify_all_anchor|JLinkExe'
```

There should be no old capture/J-Link process before starting the next real
capture.

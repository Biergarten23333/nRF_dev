# AutoPos + 3Tag Capture Runbook

Last verified: 2026-04-26

This document records the current dual-master workflow for:

1. AutoPos A-H anchor sweep with `Master_Anchor`.
2. 180s 3-tag calibration capture with `Master_Tag` and optional UWB listener.
3. Result checks used before feeding data into the next solver stage.

## Hardware Roles

| Role | SNR | USB CDC name | Purpose |
| --- | --- | --- | --- |
| `Master_Anchor` | `960148546` | `Master_Anchor` | Permanent BLE control plane for 8 anchors. Runs AUTOPOS/anchor commands only. |
| `Master_Tag` | `1050070698` | `Master_Tag` | Permanent BLE/NUS links to 3 tags. Runs TDMA/capture only. |
| UWB listener | `760185886` | SEGGER CDC | Passive UWB air capture. Keeps LED/buzzer UI build unless serious listener-drop debugging is needed. |

Port aliases are in `.protec/biospur_ports.env`:

```bash
source .protec/biospur_ports.env

echo "$BIOSPUR_ANCHOR_PORT"
echo "$BIOSPUR_TAG_PORT"
```

Current expected values:

```bash
BIOSPUR_ANCHOR_PORT=/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
BIOSPUR_TAG_PORT=/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00
```

## Current Build Artifacts

Keep these build directories. They are the currently useful artifacts.

| Build | Purpose |
| --- | --- |
| `build-master-control-b120-m1-master-anchor-lfrc-runtime-force-20260426_220551` | `Master_Anchor` B120 image, LFRC/internal oscillator, anchor daemon boot profile. |
| `build-master-control-b120-m1-master-tag-lfrc-bootprofile-20260426_213644` | `Master_Tag` B120 image, LFRC/internal oscillator, tag daemon boot profile. |
| `build-anchor-unified-ota-anchor-runtime-force-20260426_220551` | Anchor OTA image, runtime role force support. |
| `build-master-control-anchor-ota-anchor-runtime-force-20260426_220551` | Master-control bundle containing the anchor OTA image. |
| `build-tag-uwb-ota-joint-resp1000-cf4hostts-20260426_005215` | Tag OTA image for `BSF66F`, `BS2DCE`, `BSDC91`. |
| `build-master-ota-joint-resp1000-cf4hostts-20260426_005215` | Master-control bundle containing the tag OTA image. |
| `build-uwb-listener-ui-backup-bootprofile-20260426_214100` | Listener UI build with LED/buzzer/button/power display retained. |
| `build-uwb-listener-serial-only-backup-bootprofile-20260426_214100` | Backup listener serial-only diagnostic build. Use only if UI affects listener capture. |

Policy:

- Always use internal oscillator/LFRC B120 builds from now on.
- Do not use `nrfjprog`.
- Use explicit SNR with JLink scripts.
- Keep `.protec/noflash960148546` unless intentionally flashing `Master_Anchor` after explicit confirmation.

## Flash Commands

### Flash Master_Anchor

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

B120_SNR=960148546 \
scripts/flash_master_control_b120_m1_noninteractive.sh \
  build-master-control-b120-m1-master-anchor-lfrc-runtime-force-20260426_220551/zephyr/merged_domains.hex
```

### Flash Master_Tag

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

B120_SNR=1050070698 \
scripts/flash_master_control_b120_m1_noninteractive.sh \
  build-master-control-b120-m1-master-tag-lfrc-bootprofile-20260426_213644/zephyr/merged_domains.hex
```

### Flash UWB Listener UI Build

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

BIOSPUR_LISTENER_SN=760185886 \
scripts/flash_uwb_listener_jlink.sh \
  build-uwb-listener-ui-backup-bootprofile-20260426_214100/merged.hex
```

## OTA Commands

### OTA Anchors A-H

Use this when anchor firmware needs to be updated.

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source .protec/biospur_ports.env

python3 scripts/ota_deploy_anchor_set.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --timeout-s 420 \
  --expected-fw-marker anchor-runtime-force-20260426_220551 \
  --out-dir logs/anchor_ota_runtime_force_$(date +%Y%m%d_%H%M%S)
```

The anchor OTA script performs post-OTA handoff:

1. Return master-control from OTA to RECV if needed.
2. Re-enter AUTOPOS anchor mode.
3. Verify responder runtime state.

### OTA Three Tags

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source .protec/biospur_ports.env

python3 scripts/ota_deploy_tag_set.py \
  --port "$BIOSPUR_TAG_PORT" \
  --targets BSF66F,BS2DCE,BSDC91 \
  --out-dir logs/ota_tag_cf4hostts_$(date +%Y%m%d_%H%M%S) \
  --timeout-s 420 \
  --expected-fw-marker joint-resp1000-cf4hostts-20260426_005215
```

## Runtime Architecture

### Master_Anchor

Boot profile: `anchor_daemon`.

Expected behavior after power-up:

```text
mode autopos
device kind anchor
scan ANCHOR-*
connect 8 anchors
keep 8 anchor control links resident
idle for commands
```

AutoPos scripts should not rebuild the BLE control plane unless repair is needed. In resident mode, the script uses:

```bash
--reuse-resident-anchor-master
```

This avoids `mode recv` clean-slate/reconnect during normal AutoPos.

`RUNTIME_RESTART_REQUESTED` means the anchor restarted its UWB runtime state machine, not the whole MCU. BLE control links remain active.

Common role actions:

- Sweep prepare: `anchor role all matrix`
- Per-round master switch: `RUNTIME MASTER SWEEP <sets>` only to the current sweep master.
- Capture prepare/finalize: `anchor role all responder`

### Master_Tag

Boot profile: `tag_daemon`.

Expected behavior after power-up:

```text
mode recv
device kind tag
scan BS*
connect BSF66F / BS2DCE / BSDC91
keep 3 NUS links resident
idle for TDMA/capture commands
```

TDMA is not auto-started at boot. Capture scripts configure it explicitly.

When the capture log says:

```text
configure: reuse Master_Tag resident links
link setup passive: ready=BS2DCE,BSDC91,BSF66F (3/3)
```

it means the script reused existing BLE/NUS links and did not perform scan/connect/GATT discovery again.

## Full Process: AutoPos Then 180s 3Tag Capture

### 1. Run AutoPos A-H Sweep

Do not attach listener for the normal AutoPos run. Listener is only needed if AutoPos itself has timeout/rx diagnostics to investigate.

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source .protec/biospur_ports.env

OUT=logs/autopos_then_3tag_capture_$(date +%Y%m%d_%H%M%S)
mkdir -p "$OUT"
echo "$OUT" > /tmp/biospur_autopos_then_capture_outdir

python3 scripts/run_autopos_sweep_loop.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --sw-sets 10 \
  --prewarm-sw-sets 10 \
  --timeout-s 600 \
  --warmup-min-quality 0 \
  --quiet-tag-name - \
  --no-bootstrap-autopos-reset \
  --reuse-resident-anchor-master \
  --out-dir "$OUT/autopos"
```

Expected signs:

```text
PRECHECK: resident Master_Anchor mode; no RECV clean-slate/reconnect
anchor role all matrix runtime ... ready=8/8
SW-A ... SW-H success=true
Session finalizer: responder ok sent=8 ready=8/8
```

### 2. Run 180s 3Tag Capture With Listener

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source .protec/biospur_ports.env
OUT=$(cat /tmp/biospur_autopos_then_capture_outdir)

python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --with-listener \
  --duration 180 \
  --profiles BSF66F:static,BS2DCE:roto,BSDC91:roto \
  --static-hz 5 \
  --roto-hz 10 \
  --motion-hz 5 \
  --cm-probe-target BSF66F \
  --out-dir "$OUT/capture"
```

Expected signs:

```text
anchor responder runtime ready=8/8
configure: reuse Master_Tag resident links
link setup passive: ready=BS2DCE,BSDC91,BSF66F (3/3)
startup CM probe passed: target=BSF66F ok=8/8
cm_rate around 45/s
```

`positions_all.csv` is expected to be empty in calibration mode. Tag-side TS/position output is intentionally disabled because calibration tags do not know the final layout. V4 should consume CM/CS/CR/CF, not tag TS.

## Quick Result Analysis Command

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 - <<'PY'
import csv, json, pathlib, collections, statistics
base = pathlib.Path(open('/tmp/biospur_autopos_then_capture_outdir').read().strip())
print('BASE', base)

s = json.loads((base / 'autopos/summary.json').read_text())
print('\nAUTOPOS')
print('all_rounds_success=', all(r.get('success') for r in s.get('rounds', {}).values()))
print('total_elapsed_s=', s.get('total_elapsed_s'))
print('guard=', s.get('session_role_guard_result', {}).get('success'))
print('final_responder=', s.get('session_final_responder_result', {}).get('success'))
for m in s.get('order', []):
    r = s['rounds'][m]
    print(m, 'success=', r.get('success'), 'sw=', r.get('sw_count'), 'raw=', r.get('device_sw_count'), 'minq=', r.get('min_quality_seen'), 'switch_s=', round(r.get('switch_elapsed_s') or 0, 2))
print('warnings=', len(s.get('warnings', [])))
for w in s.get('warnings', [])[:20]:
    print(' ', w)

cs_files = list(base.glob('capture*/tag_capture_*/cs_all.csv'))
if not cs_files:
    raise SystemExit('no cs_all.csv found')
tag_dir = cs_files[0].parent
print('\nTAG_DIR', tag_dir)
for name in ['cm_all.csv', 'cs_all.csv', 'cr_all.csv', 'cf_all.csv', 'positions_all.csv']:
    p = tag_dir / name
    if p.exists():
        with p.open() as f:
            n = sum(1 for _ in f) - 1
        print(name, n)

rows = list(csv.DictReader(open(tag_dir / 'cs_all.csv')))
by = collections.defaultdict(collections.Counter)
qfs = collections.defaultdict(list)
sets = collections.defaultdict(collections.Counter)
plans = {}
for r in rows:
    tag = r['peer_name']
    plans[tag] = r.get('plan')
    st = [x.strip() for x in r['statuses'].split(',') if x.strip()]
    ok = sum(x == 'ok' for x in st)
    by[tag][ok] += 1
    qfs[tag].append(int(r['quality_flag_percent']))
    sets[tag][r['targets']] += 1

print('\nCS')
for tag in sorted(by):
    total = sum(by[tag].values())
    print(tag, 'plan=', plans[tag], 'frames=', total, 'okdist=', dict(sorted(by[tag].items())), '4ok_pct=', round(100 * by[tag][4] / total, 3), 'qf_min_mean=', (min(qfs[tag]), round(statistics.mean(qfs[tag]), 2)), 'top_sets=', sets[tag].most_common(8))

cr = list(csv.DictReader(open(tag_dir / 'cr_all.csv')))
rej = collections.defaultdict(collections.Counter)
anchor_rej = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for r in cr:
    if r.get('reason') != 'ok' or r.get('status') != 'ok':
        tag = r['peer_name']
        reason = r.get('reason') or r.get('status')
        anchor = r.get('anchor_label', '')
        rej[tag][reason] += 1
        anchor_rej[tag][anchor][reason] += 1
print('\nRejects')
for tag in sorted(set(r['peer_name'] for r in cr)):
    print(tag, dict(rej[tag]))
    for a, c in sorted(anchor_rej[tag].items()):
        print(' ', a, dict(c))

for ld in base.glob('capture*/listener/listener_*'):
    print('\nLISTENER', ld)
    for name in ['uf.csv', 'ul.csv']:
        p = ld / name
        if p.exists():
            with p.open() as f:
                n = sum(1 for _ in f) - 1
            print(name, n)
PY
```

## Last Verified Run

Run directory:

```text
logs/autopos_then_3tag_capture_20260426_230050
```

AutoPos result:

```text
success_all_rounds = true
total_elapsed_s = 134
matrix guard = true
final responder = true
A-H all success
no reconnect retry
```

AutoPos per-round min quality:

| Round | sw/raw | minq | switch |
| --- | --- | --- | --- |
| A | 11/21 | 80 | 3.22s |
| B | 10/20 | 71 | 3.21s |
| C | 11/21 | 85 | 5.72s |
| D | 11/21 | 80 | 3.46s |
| E | 12/22 | 75 | 3.22s |
| F | 11/21 | 83 | 3.21s |
| G | 11/21 | 76 | 3.22s |
| H | 10/20 | 73 | 3.22s |

Capture result:

```text
success = true
cm_all = 8125
cs_all = 2668
cr_all = 10801
cf_all = 2668
positions_all = 0 expected
startup BSF66F probe = 8/8 ok
```

CS quality:

| Tag | Plan | Frames | 4-ok | qf | Main sets |
| --- | --- | --- | --- | --- | --- |
| BSF66F | `cal_static` | 900 | 900/900 = 100% | 100 | `A,B,C,D` 450; `E,F,G,H` 450 |
| BS2DCE | `cal_roto` | 883 | 883/883 = 100% | 100 | mainly `A,D,G,H` |
| BSDC91 | `cal_roto` | 885 | 885/885 = 100% | min 98, mean 99.96 | mainly `B,D,E,F` |

Rejects:

```text
BSF66F: none
BS2DCE: raw_outlier 9
BSDC91: raw_outlier 17
```

Listener:

```text
uf.csv rows = 4752
ul.csv rows = 965
```

## Interpretation

This run is clean enough for V4 input work:

- AutoPos produced full A-H sweep data.
- Anchor role transitions worked using resident Master_Anchor links.
- Final responder was confirmed by runtime ack, 8/8.
- Master_Tag reused resident tag links, 3/3 ready.
- Static tag produced exact `ABCD` / `EFGH` coverage.
- Both ROTO tags produced 100% 4-anchor CS frames.
- Remaining issues are quality/outlier level, not workflow blockers.

Do not use old scripts that combine anchor and tag control on one master unless explicitly debugging legacy behavior.

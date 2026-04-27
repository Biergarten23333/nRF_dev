# 20260416 AutoPos Command（今日实战命令汇总）

> 工作目录固定：`/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start`
>
> 统一先执行：

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
```

## 0. 关键设备与串口

- BLE Master 控制板（nRF52840 DK）SNR：`683234364`
- Anchor J-Link SNR 映射（A-H）：
  - A: `760186071`
  - B: `760185876`
  - C: `760185878`
  - D: `760186081`
  - E: `760185904`
  - F: `760186124`
  - G: `760185889`
  - H: `760186121`
- BLE Master CDC 口（本机常用）：
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00`

## 1. 今日推荐固件（2026-04-16）

### 1.1 Anchor 统一 OTA 固件

- 目录：`build-anchor-unified-ota`
- 烧录镜像：`build-anchor-unified-ota/merged.hex`
- 2026-04-18 起已并入快 OTA 接收配置；后续通过 BLE OTA 下发这版后，Anchor 侧也会进入快 OTA 路径。

### 1.2 BLE Master all-in-one 固件（含 AUTOPOS/RECV/OTA）

- 目录：`build-master-control-allinone-20260416_093204`
- 烧录镜像：`build-master-control-allinone-20260416_093204/merged.hex`

### 1.3 Tag115（BSF66F）兼容 CM 固件（已验证可出 CM）

- 目录：`build-tag-ota-ref115-calibration-cm-mastercompat-20260416`
- 烧录镜像：`build-tag-ota-ref115-calibration-cm-mastercompat-20260416/merged.hex`
- OTA 包：`build-tag-ota-ref115-calibration-cm-mastercompat-20260416/tag/zephyr/zephyr.signed.bin`

## 2. 严禁弹窗的串口/JLink 烧录命令

> 使用 `scripts/jlink_flash_hex_by_snr.sh`，内部固定 `JLinkExe -SelectEmuBySN`，不会出现 probe selection 弹窗。

### 2.1 刷 BLE Master（683234364）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
scripts/jlink_flash_hex_by_snr.sh \
  683234364 nRF52840_xxAA \
  build-master-control-allinone-20260416_093204/merged.hex
```

### 2.2 刷 Tag115（760186115）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
scripts/jlink_flash_hex_by_snr.sh \
  760186115 nRF52832_XXAA \
  build-tag-ota-ref115-calibration-cm-mastercompat-20260416/merged.hex
```

### 2.3 一键刷 A-H 到同一个 Anchor 镜像

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
bash scripts/flash_all_anchors.sh build-anchor-unified-ota
```

## 3. Anchor OTA（通过 BLE Master 下发）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/ota_deploy_anchor_set.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --timeout-s 900 \
  --force-kill-port-owner \
  --out-dir logs/anchor_ota_all_$(date +%Y%m%d_%H%M%S)
```

- `--force-kill-port-owner`：当 CDC 被其他进程占用时，自动 kill 占用者后重试。
- 当前默认 `build-master-control-anchor-ota` 与 `build-anchor-unified-ota` 已包含快 OTA 配置。
- 若现场 Anchor 仍是旧接收端固件，则第一次升级只是“把快 OTA 接收端发下去”；从该轮完成后，后续 OTA 才会稳定处于加速状态。

## 4. Anchor 编号/角色配置（必要时）

> 当看到 `ANCHOR-U-xxxx` 而不是 `ANCHOR-A/B/...` 时，先做 provisioning。

### 4.1 单个 Anchor 示例（A）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/provision_anchor.py \
  --probe-serial 760186071 \
  --anchor-id A \
  --role responder \
  --verify
```

### 4.2 A-H 全量（按映射逐个执行）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/provision_anchor.py --probe-serial 760186071 --anchor-id A --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760185876 --anchor-id B --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760185878 --anchor-id C --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760186081 --anchor-id D --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760185904 --anchor-id E --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760186124 --anchor-id F --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760185889 --anchor-id G --role responder --verify
python3 scripts/provision_anchor.py --probe-serial 760186121 --anchor-id H --role responder --verify
```

## 5. Anchor Sweep（AUTOPOS SW-A..H）

## 5.1 100 set（推荐基准）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --verbose 1 \
  --quiet-tag-name auto \
  --quiet-tag-required \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_100sets_$(date +%Y%m%d_%H%M%S)
```

### 5.2 10 set（快速烟测）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --verbose 1 \
  --quiet-tag-name auto \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_$(date +%Y%m%d_%H%M%S)
```

说明：
- `--timeout-s` 不填时已按 `--sw-sets` 自动放大。
- `--verbose`：`0=仅SW/失败`，`1=去掉噪声后的常规`，`2=完整流`。

## 6. 由 Sweep 结果跑 V1/V2/V3-lite/V3-full

## 6.1 若 run-dir 里只有 `summary.json`，先提取 `pairs_all.csv`

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/autopos_extract_pairs_from_sweep_summary.py \
  --summary-json logs/live_autopos_sweep_loop_A_to_H_100sets_YYYYmmdd_HHMMSS/summary.json \
  --out-csv logs/live_autopos_sweep_loop_A_to_H_100sets_YYYYmmdd_HHMMSS/pairs_all.csv
```

### 6.2 从现有 sweep 重跑 V1/V2/V3-lite/V3-full（推荐）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/run_autopos_solve_v1_v2_v3_v3full_from_existing.py \
  --run-dir logs/live_autopos_sweep_loop_A_to_H_100sets_YYYYmmdd_HHMMSS \
  --pairs-csv logs/live_autopos_sweep_loop_A_to_H_100sets_YYYYmmdd_HHMMSS/pairs_all.csv
```

### 6.3 启用 “V3-full with Tag115 CM” 模式

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/run_autopos_solve_v1_v2_v3_v3full_from_existing.py \
  --run-dir logs/live_autopos_sweep_loop_A_to_H_100sets_YYYYmmdd_HHMMSS \
  --pairs-csv logs/live_autopos_sweep_loop_A_to_H_100sets_YYYYmmdd_HHMMSS/pairs_all.csv \
  --v3full-with-tag115-cm
```

## 7. V1/V2/V3/V3full 对比分析命令

> 以下路径中的 `solve_rerun_...` 用你实际新生成目录替换。

### 7.1 Pair 距离对比

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/autopos_compare_v1_v2_v3_v3full_pairs.py \
  --zero-as-missing \
  --v1 logs/.../solve_rerun_.../v1/final_pair_distances.csv \
  --v2 logs/.../solve_rerun_.../v2/v2_fused/final_pair_distances_v2.csv \
  --v3 logs/.../solve_rerun_.../v3_lite/v3_fused/final_pair_distances_v2.csv \
  --v3full logs/.../solve_rerun_.../v3_full/v3_full_fused/final_pair_distances_v3.csv \
  --out logs/.../solve_rerun_.../compare_v1_v2_v3_v3full_pairs.md
```

### 7.2 Layout 刚体对齐对比

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/autopos_compare_v1_v2_v3_v3full_layouts.py \
  --v1 logs/.../solve_rerun_.../v1/anchor_layout_v1_soft_iterative.json \
  --v2 logs/.../solve_rerun_.../v2/v2_fused/anchor_layout_v2_iterative.json \
  --v3 logs/.../solve_rerun_.../v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json \
  --v3full logs/.../solve_rerun_.../v3_full/anchor_layout_v3_full.json \
  --out logs/.../solve_rerun_.../compare_v1_v2_v3_v3full_layouts.md
```

### 7.3 导出四套 Anchor 布局到一个 markdown

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/autopos_dump_anchor_layouts.py \
  --out logs/.../solve_rerun_.../anchor_layouts_v1_v2_v3_v3full_dump.md \
  --title "Anchor Layout Dump" \
  V1=logs/.../solve_rerun_.../v1/anchor_layout_v1_soft_iterative.json \
  V2=logs/.../solve_rerun_.../v2/v2_fused/anchor_layout_v2_iterative.json \
  V3lite=logs/.../solve_rerun_.../v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json \
  V3full=logs/.../solve_rerun_.../v3_full/anchor_layout_v3_full.json
```

### 7.4 Holdout 浮动参考评估（可选）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/autopos_eval_holdout_floating_ref.py \
  --layout logs/.../solve_rerun_.../v3_full/anchor_layout_v3_full.json \
  --train-session logs/.../solve_.../floating_ref115_train \
  --holdout-session logs/.../solve_.../floating_ref115_holdout \
  --out logs/.../solve_rerun_.../eval_holdout/holdout_eval_v3full.md
```

## 8. Tag115 CM 采集命令（Sweep 后）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
python3 scripts/run_anchor_responder_then_tag_cm.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --target-name BSF66F \
  --cm-lines 200 \
  --cm-timeout-s 900 \
  --anchor-timeout-s 900 \
  --out-dir logs/tag115_cm_fresh_$(date +%Y%m%d_%H%M%S)
```

- 如果你要在直接 RECV 路径失败时才回退 AUTOPOS 角色转换：加 `--autopos-fallback`。

## 9. 常见故障一键排查

### 9.1 CDC 口被占用

```bash
lsof /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00
```

### 9.2 OTA 时自动杀占口进程

```bash
python3 scripts/ota_deploy_anchor_set.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH --timeout-s 900 \
  --force-kill-port-owner \
  --out-dir logs/anchor_ota_retry_$(date +%Y%m%d_%H%M%S)
```

### 9.3 查看可用 JLink（确认 SNR）

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
scripts/jlink_show_emulators.sh
```

---

## 10. 今日最小闭环（推荐顺序）

1. `bash scripts/flash_all_anchors.sh build-anchor-unified-ota`
2. `scripts/jlink_flash_hex_by_snr.sh 683234364 nRF52840_xxAA build-master-control-allinone-20260416_093204/merged.hex`
3. `python3 scripts/run_autopos_sweep_loop.py ... --sw-sets 100 ...`
4. `python3 scripts/autopos_extract_pairs_from_sweep_summary.py ...`
5. `python3 scripts/run_autopos_solve_v1_v2_v3_v3full_from_existing.py ...`
6. `python3 scripts/autopos_compare_v1_v2_v3_v3full_pairs.py ...`
7. `python3 scripts/autopos_compare_v1_v2_v3_v3full_layouts.py ...`

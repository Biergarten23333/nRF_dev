# H1 Observer 当前状态与推荐下一步

日期：2026-07-24  
工作区：`/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion`

## 1. 当前结论

独立 BLE observer 已从需要手动进入 Open DFU 的 nRF52840 dongle，换成
nRF54L15 DK。当前 nRF54L15 DK 已部署并运行：

```text
硬件                 nRF54L15 DK / PCA10156
J-Link SNR           1057782457
固件 marker          nrf54l15dk-ble-observer-v3
NCS target           nrf54l15dk/nrf54l15/cpuapp
输出                 SEGGER RTT
BLE 行为              active scan，不连接任何设备
扫描维护              每 58 s stop/start 一次
```

刷写始终按 SNR 锁定：

```text
JLinkExe -USB 1057782457 ... -device nRF54L15_M33 ...
```

nRF54L15 observer 的部署没有使用 J-Link Probe Selection，也没有操作
Fusion Master `683234364` 或 Master_Tag 探针 `1050070698`。

已部署镜像：

```text
B306_Part/builds/nrf54l15dk-ble-observer-v3/merged.hex
SHA-256 c29b760c45467824eedbe066235d1589e42e21ac155e8a1e1428aee9fc3cedfd
FLASH 87216 / 1428 KiB = 5.96% PASS
RAM   42728 / 188 KiB  = 22.19% PASS
malloc arena = 0
```

## 2. Observer 验证结果

### v1：接收能力得到证明，但出现一次长期扫描异常

v1 active scan 在短窗口内累计收到 417 个广播、10 个独立地址，并解析到
`BS8251`。这证明 nRF54L15 的 BLE 接收链不是“聋的”。

运行约 7 分钟时曾出现：

```text
SoftDevice Controller ASSERT: 33, 473
```

因此 v1 没有被保留为最终镜像。

### v2：passive scan 稳定，但无法识别 BS scan response

v2 passive scan 在 60 秒内收到 643 个广播、27 个独立地址，没有 controller
assert，但 `bs_packets=0`。与 v1 对照说明至少当时观察到的 BS 身份信息位于
active scan response；纯 passive scan 不足以完成 BS 身份判别。

### v3：当前部署版本

v3 恢复 active scan，并每 58 秒执行一次受控 stop/start。68 秒验收结果：

```text
OBSERVER_BOOT fw=nrf54l15dk-ble-observer-v3 board=nrf54l15dk output=RTT
OBSERVER_READY mode=active_scan connect=0 restart_s=58
...
OBS_SCAN action=stop err=0
OBS_SCAN action=start err=0
...
OBS_STAT ... adv=1259 unique=24 bs_packets=0 scan=1
```

自动重启扫描前后计数持续增长，验收窗口内没有 controller assert。

该窗口没有看到 `BS065F`。这不能解释为“BS065F 不广播”，因为此前
Master_Tag 已经连接 BS065F；处于连接状态的 tag 不应继续进行同一
connectable advertising。该结果只能证明 observer 正常接收其他 BLE
广播，不能重新判定 BS065F 的 advertising lifecycle。

68 秒验收不是长期稳定性结论。v3 的周期性重启策略目前只实际跨过了一个
restart 周期。

## 3. Fusion Master DK native CDC 恢复

2026-07-24 换用一根确认支持 USB data 的线后，原 Fusion Master DK
`683234364` 的两个 USB 身份同时正常枚举：

```text
J-Link OB
  VID:PID  1366:1025
  serial   000683234364

native application CDC
  VID:PID  2FE3:10F4
  product  BioSpur Fusion Master
  serial   8D3AC42D4D90FAE8
```

CDC 稳定路径为：

```text
/dev/serial/by-id/usb-BioSpur_BioSpur_Fusion_Master_8D3AC42D4D90FAE8-if00
```

使用 DTR=0、RTS=0 打开该稳定路径并读取 15 秒，不发送命令、不复位、不刷写，
实际收到：

```text
FUSION_MASTER marker=dk-fusion-imu-relay-v7 probe=683234364 pc=USB_CDC rtt=control+log
FUSION_MASTER_BLUETOOTH_READY
FUSION_BRIDGE_READY name=BSF3C79 rssi=-49 mtu=247 data=18 telemetry=21 control=24
```

记录统计：

```text
FUSION_UWB        177 条
FUSION_TELEMETRY   17 条
FUSION_HEALTH       1 条
malformed           0
logger_drop         0
connections         1
```

这 177 条包含主机刚打开 CDC 时排出的已有缓冲内容，不能直接作为“15 秒
吞吐率”计算；它用于证明 CDC 确实承载了完整记录，而不只是完成 USB 枚举。

链路实际完成 2M PHY、DLE 251、ATT MTU 247，并持续输出 identity `065F`
的 raw ranges、poll/strobe timestamp 和 B306 telemetry。因此原
`dk-fusion-imu-relay-v7` 的 native CDC 功能本身是正常的；此前长期没有
CDC 输出，现有证据首先指向 USB 线缆或连接质量，而不是 Fusion Master
固件缺少 CDC。

日志里出现：

```text
cdc_drop_bytes=660800
```

这是 CDC 长期未连接或没有主机读取时累计拒收的输出字节。它不代表 BLE/UWB
链路损坏；同一记录中 `malformed=0`、`logger_drop=0`，并且实时
`FUSION_UWB` 仍在继续。

当前决策：

- 保留 `683234364` 和 `dk-fusion-imu-relay-v7` 作为当前 Fusion Master。
- 后续主机数据和命令恢复以 native USB CDC 为主，RTT 只保留为诊断后备。
- 暂时不需要仅为解决 CDC 问题而把 Fusion Master 移到 custom B306
  receiver。
- 没有向 `683234364` 或计划中的 `683012410` 刷写任何新镜像。

## 4. H1 已经闭合的事实

1. Master_Tag relay1 carrier 的恢复、双核重新刷写和冷启动 banner 已完成。
2. 先前独立 nRF52840 dongle 曾连续观察到 BS065F 广播。
3. Master_Tag 随后也解码到 BS065F，并完成连接和 NUS discovery。
4. 已安装 tag 镜像记录为 `tag-fusion-link-v2-absdeadline3`。
5. 已安装 tag 代码在 boot 和 disconnect 路径都会无条件启动或重试
   advertising；persisted TDMA 不会通过代码中的显式 guard 禁止广播。
6. BS065F 的 persisted TDMA 能解释冷启动后 UWB 自动恢复约 10 Hz，但不能
   解释之前 Master 两个空扫描窗口。
7. 因为“persisted TDMA 导致 tag 静默”已被硬件观察和代码审计共同否决，
   tag settings-only SWD erase 没有执行，也不再被当前证据授权。
8. 探针 `1050070698` 仍在 Master_Tag 上，没有移动到 Fusion PCB。

## 5. 仍未闭合的事项

H1 Directive #2 要求一个双向判别：

```text
方向 A：独立 observer 能否看到 BS065F
方向 B：独立设备广播 BS 前缀身份时，Master_Tag 能否看到它
```

方向 A 已由先前 dongle 的 65 秒日志证明。方向 B 的旧测试使用临时
`BSBEEF` advertiser，但 Master 没看到该刺激；同一窗口内 Master 却收到并
解码了 277 个 BS065F 广播。因此：

- 不能说 Master RF scanner 是聋的；
- 也不能说旧 `BSBEEF` 刺激一定以 Master 可接受的格式上空口；
- 旧反向测试是一个无效或意外的 discriminator，不能作为 PASS；
- H1.2 的 tag OTA 因此尚未执行。

当前主要缺口是：用现在可靠、无需手动 Boot 的 nRF54L15 DK，重新做一次
可自证空口内容的 `BSBEEF → Master_Tag` 反向测试。

## 6. 推荐下一步

### 6.1 构建 nRF54L15 observer/advertiser v4

在 v3 基础上增加一个受控测试广播模式，marker 建议：

```text
nrf54l15dk-ble-observer-v4
```

要求：

1. 默认仍为 observer，不建立 BLE 连接。
2. RTT 命令或确定性的测试阶段可启动 60 秒 non-connectable advertising。
3. 测试身份使用 `BSBEEF`，不是 `BSTEST`。Master 的现有解析规则要求
   `BS` 后面恰好是四位十六进制数。
4. Complete Local Name 和现有 BS manufacturer-data 格式同时携带
   `BSBEEF`，避免再次出现“名字只存在于某一种 AD/scan-response 编码”
   的歧义。
5. RTT 必须打印实际提交给 controller 的逐字节 advertising data、
   scan-response data、开始返回码、停止返回码及广播持续时间。
6. v4 仍通过 FLASH ≤95%、RAM ≤85%、malloc arena 为显式有限值的门限。
7. 只能按 `1057782457` 刷写；任何未指定 SNR 的命令都禁止执行。

### 6.2 重新运行 60 秒反向判别

测试顺序：

1. 记录 Master_Tag 当前 target 和连接状态。
2. 让 Master_Tag 进入扫描状态，目标明确设为 `BSBEEF`。
3. 启动 nRF54L15 v4 的 `BSBEEF` 广播 60 秒。
4. 同时保存：
   - nRF54L15 RTT 的实际 advertising payload 和运行计数；
   - Master_Tag 完整 scan/candidate/filter/connection 日志。
5. 测试结束后停止 `BSBEEF` 广播。
6. 恢复 Master_Tag target 为 `BS065F`，确认 BS065F 链路恢复或仍保持连接。

### 6.3 判定门

#### PASS

满足以下条件：

```text
nRF54L15 报告 advertising start=0
Master_Tag 日志明确出现 BS BEEF candidate
Master_Tag 尝试或完成对该测试身份的连接
```

结论：独立观察和 Master 扫描两个方向都闭合。随后可以进入 H1.2。

#### STOP：Master 没看到 BSBEEF，但仍看到其他 BS

结论：Master 不是整体 RF-deaf，但测试刺激编码、filter 或 candidate 路径仍
有问题。保留两边原始日志，停止；不得把它写成 PASS，也不得直接开始 OTA。

#### STOP：Master 完全没有 candidate

结论：优先调查 Master_Tag CPUNET、scan start/runtime state 和 controller
计数。不得擦除 tag settings，不得移动探针，不得用 tag OTA 试错。

## 7. 反向判别 PASS 后

只有 6.3 的 PASS 成立后，推荐继续：

1. 通过现有 Path M OTA：

   ```text
   tag-fusion-link-v2-relay1
   ```

2. OTA 前明确记录 payload marker 和 SHA-256。
3. OTA 不得在 capture 期间执行。
4. 记录从 OTA 开始到 tag 重启、重新广播、Master 重连及新 marker 确认的
   端到端时间；原先 50–60 秒只是预测，不得当成实测值。
5. OTA 成功后按原批次 prompt 运行 V-B 系列。
6. wand-tag 干扰条款已作废，因为 BS9336、BS955A、BSCCF4 已退役。

## 8. 当前禁止事项

- 不擦除 DWM1001C settings partition。
- 不通过 SWD 刷写 DWM1001C。
- 不移动探针 `1050070698`。
- 不触碰 Master_Tag 的双核镜像。
- 不在反向判别闭合前执行 `tag-fusion-link-v2-relay1` OTA。
- 不把“当前 observer 没看到 BS065F”解释成 tag 静默。
- 不运行任何会弹出 J-Link Probe Selection 的命令。
- 不运行缺少显式 SNR 的 flash/debug/RTT 命令。

## 9. 关键证据

```text
B306_Part/docs/homecoming_batch_report.md
B306_Part/host/nrf54l15dk_ble_observer/src/main.c
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_inventory.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_build.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_memory_gate.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_artifact_sha256.txt
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_flash_by_snr_1057782457.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/nrf54l15dk_v3_active_restart_68s.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/passive_65s.log
B306_Part/logs/homecoming_20260724/h1_recovery/observer/dongle/master_active_bstest_62s.log
B306_Part/logs/custom_b306_fusion_master_20260724/dk_683234364_native_cdc_15s.log
```

## 10. Directive #3 supersession and completed next step

The v4 synthetic advertiser recommendation in §§6–7 was explicitly rejected
by H1 Directive #3. It was not built or deployed. Master_Tag had already
decoded 277 BS065F advertisements, connected, and completed NUS discovery, so
the previous blocker no longer existed.

H1.2 has now completed:

```text
payload marker: tag-fusion-link-v2-relay1
payload SHA-256: 3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b
OTA sequence: 41.59 s
Master_Tag RECV restored: 43.50 s
post-OTA marker: confirmed
independent post-OTA advertising: observed by nRF54L15 v3
```

V-B1 through V-B5 also passed. Path M and Path R both configure/control the
tag, steady UWB output is approximately 10 Hz, and M→R→M did not enter a
stuck/zero-TX state. V-B5 is the H1.2 timing result. The direct CFG's requested
five-second epoch explains why its immediate 12-second window averaged
6.582 Hz; after activation it measured 10.021391 Hz.

The two earlier empty Master_Tag windows remain an unexplained anomaly, not a
confirmed tag advertising defect. Full candidate-stream logging remains the
standing mitigation. No settings erase, DWM1001C SWD flash, probe move, or
Master_Tag image change occurred.

Fusion Master status is also settled: DK `683234364` and
`dk-fusion-imu-relay-v7` stay. Native USB CDC is primary; a bad/non-data cable,
not a broken DK connector, caused the earlier CDC absence. RTT is diagnostic
backup and the custom-B306 swap is not needed as a workaround.

Authoritative continuation report:

```text
B306_Part/logs/homecoming_20260724/h1_2_ota_20260724_183622/REPORT.md
```

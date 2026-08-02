# TDMA/UWB 命令语法核查

> 2026-07-26 operational override: every `CFG_STOP` occurrence below records
> historical/frozen behavior and is **not executable guidance**. Relay2 turns
> it into approximately 64 Hz free-run. Current procedure is `MODE IDLE`,
> followed by a complete Master TDMA reconfiguration before the next run.

日期：2026-07-23  
性质：只读源码/既有日志审计  
范围：

- UWB 主仓：`/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/`
- Fusion fork：`/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/UWB_Part/fusion-link/`

## 核心结论

Fusion 中继链路应复用 tag 最后一跳命令 `CFG TAG=...`，不应把
`tdma roster/rebalance/auto` 搬进 B306。后者是 Master 本地调度器
命令，tag 从未收到这些字符串。

本次调查没有运行采集脚本、打开 rig 端口或访问硬件。

## 1. Script 侧：实际命令流

| # | 问题 | 答案 | 证据 |
|---|---|---|---|
| 1.1 | `run_recv_tdma_capture.py` 的 TDMA 序列 | 默认 fresh-link 路径实际有两轮写入。第一轮预置：`tdma hold 1` → `tdma clear` → `tdma freq motion <hz>` → 对每个目标发送 `tdma roster <BSxxxx> motion`。随后重申一遍：`tdma hold 1` → `tdma clear` → `tdma freq motion <hz>` → 每 tag 的 `tdma roster ...` → `conn` → `cmd_all CFG_STOP` → CIR 命令 → `tdma hold 0` → `tdma rebalance` → `tdma show`/`status`/`device show` 验证。`<hz>` 为十进制整数，未指定时为 `10`；profile 固定为小写 `motion`。每条命令在线尾加 `\n`。 | `run_recv_tdma_capture.py:704-730, 1154-1172, 2168-2182, 2201-2275` |
| 1.2 | `tdma auto 1` 与显式 roster 路径 | 历史 demo 精确序列是 `cmd_all REBOOT`，等待 16 s，然后三个 `tdma roster <BS> motion`，最后 `tdma auto 1`；没有 `clear/freq/hold/rebalance`。`auto=1` 让调度器纳入所有 ready peer；显式模式只纳入 roster 中的 BS。`tdma clear` 会清空 profile 并关闭 auto。正式采集脚本不用 `auto`。 | `demo_start.py:43-48`; `master_multi_app.c:1096-1111, 4057-4068` |
| 1.3 | 应答、等待和重试判定 | Master 本地命令打印：`tdma hold rc=%d hold=%lu`、`tdma clear rc=%d`、`tdma roster rc=%d target=%s profile=%s`、`tdma freq rc=%d profile=%s hz=%lu`、`tdma rebalance rc=%d`。正式脚本不逐条断言这些本地回复；最终要求每个 tag 出现 `BSxxxx notify: CFG_OK ... LIVE=1`，并严格匹配 slot/count/mask/period/active/active_us。`CFG assigned` 缺失仅 warning；tag_id/GEN 不同也是 warning，因此没有“GEN 必须递增”的判定。默认最多 3 轮。demo 只数至少 3 个 `LIVE=1`，再要求 30 s 内每 tag `TR` 行数大于 50 且 ge7 ≥90%。 | `master_control/src/main.c:2494-2555`; `run_recv_tdma_capture.py:210-228, 2265-2382, 2557-2663`; `demo_start.py:20-48` |
| 1.4 | 完整 session 的其他命令 | 正式默认路径还发送 `status`、`device show`、必要时 `device kind tag`、`mode recv`、`ota_target token -1`、`ota_target name ...`、`ota_target prefix ...`、`ota_target uuid -`、`conn`、两次 `cmd_all CFG_STOP`，以及默认 `cmd_all CIR OFF`。compact 时为 `cmd_all CIR COMPACT`；full 延后为 `cmd_all CIR FULL`，结束后恢复 `cmd_all CIR OFF`。Anchor preflight 涉及 `status`、`device show`、必要时 `device kind anchor`、`mode autopos`、`autopos map <A..H> <UUID>`、`conn`、`anchor role all responder`。正式路径不发 `REBOOT`、`CFG_RUN` 或 `DIAG ...`；demo 才发 `cmd_all REBOOT`。 | `run_recv_tdma_capture.py:1817-1857, 2047-2384, 2387-2490`; `verify_all_anchor_responder_runtime.py:263-375, 493-547`; `run_autopos_sweep_loop.py:1234-1281` |

## 2. 固件侧：解析和落地

### 2.1 tag 完整命令面

入口不是命令表，而是 NUS `.received = ble_received` 后的一串
`strcmp/strncmp`。

主仓完整命令面：

- `PING`
- `STATUS`
- `TDMA_STATUS`
- `TXPWR <MAX|M3|M6|M12|POR>`
- `DIAG?`
- `DIAG <ON|OFF>`
- `CIR?`
- `CIR_STATUS`
- `CIR <OFF|COMPACT|FULL>`
- `TAG CIR <OFF|COMPACT|FULL>`
- `APOS <id> <x> <y> <z>`
- `APOS_COMMIT`
- `APOS_RESET`
- `APOS_STATUS`
- `VERSION`
- `CFG_STATUS`
- `MODE?`
- `MODE <RUN|IDLE>`
- `TDMA_SET <slot>`
- `CFG ...`
- `CFG_RUN`
- `CFG_STOP`
- `HELP`
- `OTA_STATUS`
- `OTA_PREPARE`
- `OTA_BEGIN`
- `OTA_CANCEL`
- `REBOOT`

无法识别的命令回复：

```text
UNKNOWN_CMD
```

证据：

- 主仓 `apps/tag/src/uwb_tag_ble.c:1542-2067`
- Fusion `apps/tag/src/uwb_tag_ble.c:1793-2511`

### 2.2 `tdma` 子命令实际属于 Master

`tdma roster/rebalance/auto/clear/freq` 不是 tag 命令，而是 Master
CDC 命令：

```text
tdma show
tdma clear
tdma rebalance
tdma hold <0|1>
tdma auto <0|1>
tdma roster <BSxxxx|xxxx> <motion|mmot>
tdma profile <BSxxxx|xxxx> <motion|mmot>
tdma freq <motion|mmot> <1..50>
```

- `freq` 单位为 Hz，十进制。
- help 只公布 `motion`，`mmot` 是隐藏别名。
- Master 将 sub/profile 转为小写，因此这部分不区分大小写。
- BS 参数可带 `BS` 前缀，主体为不超过 `0xFFFF` 的十六进制。
- tag 仅认识大写 `TDMA_STATUS` 和 `TDMA_SET`。

证据：

- `apps/master_control/src/main.c:338-350, 2480-2560`
- `apps/master/src/master_multi_app.c:1145-1184, 4008-4023`

### 2.3 roster → CFG → tag 开始发射

完整链条：

```text
PC: tdma roster ...
  ↓
Master 更新本地 tdma_profiles[]
  ↓
hold release / rebalance
  ↓
Master 收集 ready peers，计算 tag_id / slot / mask / count
  ↓
Master 经 BLE NUS 发送 CFG TAG=...
  ↓
tag NUS parser 保存 settings，排队 runtime update
  ↓
ranging 主循环在安全点应用地址和 schedule
  ↓
到相对 EPOCH 后进入分配的 slot 发射
```

Master 生成的精确格式：

```text
CFG TAG=%u SLOT=%u COUNT=%u MASK=0x%04X PERIOD=%u ACTIVE=%u EPOCH=%lu GEN=%u PMODE=%u AMODE=%u
```

当 `ACTIVE_US` 非零时：

```text
CFG TAG=%u SLOT=%u COUNT=%u MASK=0x%04X PERIOD=%u ACTIVE=%u ACTIVE_US=%u EPOCH=%lu GEN=%u PMODE=%u AMODE=%u
```

当前 Master 默认参数：

```text
PERIOD=10
ACTIVE=9
ACTIVE_US=0
EPOCH lead=5000 ms
motion target=10 Hz
```

所以当前使用不带 `ACTIVE_US` 的发送格式。Master 发送的 `AMODE=0`
不被 tag parser 读取。

证据：

- `apps/master/src/master_multi_app.c:1318-1389`
- `apps/master/src/master_multi_app.c:1466-1664`
- `apps/master/src/master_multi_app.c:3971-4006`
- `apps/tag/src/uwb_tag_ble.c:903-996`

### 2.4 CFG 生效时间与 0TX caveat

`CFG_OK LIVE=1` 只表示 `ss_twr_init_runtime_configure()` 接受参数并将
配置写入单个 pending struct；它不是“已经开始按新 slot TX”的确认。

pending 配置在 ranging 主循环的下一次安全检查点或 sweep 结束处应用。
应用时才：

1. 更新 `logical_tag_id`；
2. 计算 `0xB100 + logical_tag_id`；
3. 必要时调用 `dwt_setaddress16()`；
4. 替换 TDMA schedule；
5. 等待相对 `EPOCH` 到期。

`EPOCH` 是相对延迟，不是 Master/tag 共享的绝对时钟。tag 将其转换为：

```text
sync_local_ms = k_uptime_get_32() + epoch_ms
```

快速连续 live CFG 导致真实 0TX、冷重启恢复已有文档证据，但源码中
没有明确故障分支或 root-cause 注释：

```text
root cause: NOT FOUND
```

不能仅凭单 pending struct 或提前发送 ACK，就断言它们是 0TX 的原因。

证据：

- `src/ss_twr_init.c:2736-2802`
- `src/ss_twr_init.c:6019-6037`
- `src/ss_twr_init.c:6518-6550`
- `src/uwb_tdma.c:63-100`
- `2026-07-15-FREEZE/FREEZE_STATE.md:43-48`

### 2.5 CFG 应答路径

`CFG assigned[...]` 是 Master 本地打印，不是 tag ack。

tag 的成功回复：

```text
CFG_OK TAG=%u SLOT=%u/%u MASK=0x%04X PERIOD=%u ACTIVE=%u ACTIVE_US=%u GEN=%u LIVE=%u RUN=%u STATE=%s
```

失败回复：

```text
CFG_BAD
CFG_SAVE_FAIL
```

Master 将 tag notification 转印为：

```text
BSxxxx notify: <payload>
```

证据：

- `apps/tag/src/uwb_tag_ble.c:1938-1973`
- `apps/master/src/master_multi_app.c:1318-1389`
- `apps/master/src/master_multi_app.c:2151-2165`
- `apps/master/src/master_multi_app.c:2286-2317`

### 2.6 Fusion fork 与主仓 diff

主仓已有命令一个都没删：

- APOS 四条仍在，只被标为 `SCHEDULED FOR REMOVAL`。
- 两边都不存在 EKF 命令，因此不存在“删除 EKF 命令”的命令面 diff。

Fusion 新增：

```text
BSL_STATUS
TR?
CAPTURE?
CAPTURE PARAM <ci_units> <sup_units>
TR <ON|OFF>
CAPTURE <ON|OFF>
```

OTA 关键字相同，但 Fusion 会切换连接参数并 suspend/resume UART。
两仓的 Master 和 master_control 文件当前逐字节一致；分叉位于 tag。

证据：

- Fusion `apps/tag/src/uwb_tag_ble.c:1838-1957`
- Fusion `apps/tag/src/uwb_tag_ble.c:2063-2073`
- Fusion `apps/tag/src/uwb_tag_ble.c:2369-2505`

## 3. 地址与身份

### 3.1 on-air 地址来源

冷启动默认 identity 为 FICR `DEVICEID[0..1]` 的 16-bit fold：

```c
folded = deviceid0 ^ deviceid1 ^ (deviceid0 >> 16) ^ (deviceid1 << 1);
identity_code = ((folded >> 16) ^ folded) & 0xFFFF;
```

默认 `logical_tag_id` 为 `identity_code` 低 8 位。若 settings 中有
非零 `identity_code` 或 `logical_tag_id`，settings 覆盖默认值。

DW1000 短地址始终是：

```text
0xB100 + logical_tag_id
```

所以：

- free-run/首次启动通常使用 FICR 低字节或已有 NVS 值；
- slotted 模式中，Master 通过 `CFG TAG=N` 显式重写 logical tag id。

证据：

- `apps/tag/src/uwb_tag_ble.c:489-529`
- `apps/tag/src/uwb_tag_ble.c:585-659`
- `apps/tag/src/tag_app.c:419-445`
- `include/uwb_ss_twr_shared.h:81-88`

### 3.2 CFG 地址重写行为

`CFG TAG=N` 先设置：

```text
params.logical_tag_id = N
```

应用 pending 配置时：

```text
ss_twr_init_local_addr = uwb_tag_short_addr(N)
                         = 0xB100 + N
```

如果 DW1000 已配置且地址发生变化，立即调用：

```text
dwt_setaddress16(ss_twr_init_local_addr)
```

reference 表例子：

| BS | logical tag id | slot | on-air 地址 |
|---|---:|---:|---:|
| BS2DCE | 1 | 0 | `0xB101` |
| BS9336 | 2 | 2 | `0xB102` |
| BS955A | 3 | 3 | `0xB103` |
| BSCCF4 | 4 | 5 | `0xB104` |
| BSDC91 | 5 | 7 | `0xB105` |
| BSF66F | 6 | 8 | `0xB106` |

证据：

- `apps/tag/src/uwb_tag_ble.c:973-990`
- `src/uwb_ss_twr_shared.c:73-81`
- `src/ss_twr_init.c:2736-2765`
- `apps/master/src/master_multi_app.c:213-220`

### 3.3 B306 identity 与 BS065F

B306 已有稳定 identity，但不是可人工配置的 board id：

```text
FICR DEVICEID fold → identity → BLE name BSF%04X
```

B306 当前实现中没有 NVS identity key、`board_id`、serial 或 identity
override 命令。若需要人工分配板号，必须新增持久化和控制面。

tag 的 BS 号默认也来自同一 FICR fold，并允许 settings 中的非零
identity 覆盖。

对这台 `BS065F`，现有文本日志只证明它以该名称工作，没有记录
`BSL_FLAG_IDENTITY_NVS` 的解码值：

```text
BS065F 是 FICR 还是 NVS override：
NOT FOUND（既有文本日志缺失）
```

需要解码一帧 BSL `flags` 或读取设备 settings 才能闭合。

证据：

- `B306_Part/firmware/src/main.c:519-555`
- `B306_Part/include/biospur_link.h:255-274`
- Fusion `src/ss_twr_init.c:3431-3439`
- `UWB_Part/FREEZE_INTERFACE.md:196-204`

## 4. 移植约束

### 4.1 命令来源和回复路由

当前命令解析高度耦合 BLE：

1. parser 只注册为 NUS `.received` callback；
2. 收到的 `conn` 立即 `ARG_UNUSED`；
3. 所有回复进入共用 TX FIFO；
4. 最终调用 `bt_nus_send(NULL, ...)`，不是回原始连接。

Fusion 新增的 UART link 当前严格 TX-only：

- callback 只处理 `UART_TX_DONE`；
- callback 只处理 `UART_TX_ABORTED`；
- 没有 `uart_rx_enable`；
- 没有 RX buffer；
- 没有命令 framing。

因此 B306→DWM1001C 直配必须新增 UART RX，并把 parser 重构为类似：

```text
parse(line, source, reply_sink)
```

否则 UART 来源无法进入 parser，`CFG_OK` 也无法沿原路返回。

证据：

- Fusion `apps/tag/src/uwb_tag_ble.c:1677-1733`
- Fusion `apps/tag/src/uwb_tag_ble.c:1793-1815`
- Fusion `apps/tag/src/uwb_tag_ble.c:2508-2511`
- Fusion `src/biospur_uart_link.c:59-101`
- Fusion `src/biospur_uart_link.c:104-149`

### 4.2 长度与字符限制

tag 命令 buffer：

```text
192 bytes total
191 bytes payload + terminating NUL
```

更长 NUS payload 会被静默截断，不回复 `too long`。

parser 只删除尾部：

```text
\r
\n
space
tab
```

它不删除前导空格、不统一命令大小写，也没有正式字符集验证。

另一个独立限制是现有 Master PC CDC：

```text
uart_pending_line[64]
最大命令 63 字符
溢出回复 UART command too long
```

Master 内部生成 CFG 使用 `char cmd[160]`。新中继协议必须容纳完整
CFG，不能继承旧 Master CDC 的 63 字符限制。

证据：

- tag `apps/tag/src/uwb_tag_ble.c:91-95, 1542-1564`
- Master `apps/master_control/src/main.c:95-100, 3168-3187`
- Master `apps/master/src/master_multi_app.c:1329-1364`

## 附件 A：命令原文速查表

### A1. 历史 demo 的可直接照抄版本

```text
cmd_all REBOOT
tdma roster BS9336 motion
tdma roster BS955A motion
tdma roster BSCCF4 motion
tdma auto 1
```

期待结果：

| 命令 | 期待应答/判定 |
|---|---|
| `cmd_all REBOOT` | tag 回 `REBOOTING`；demo 本身不检查，固定等待 16 s。 |
| 每条 `tdma roster ...` | Master 打印 `tdma roster rc=0 target=<BS> profile=motion`。demo 不做断言。 |
| `tdma auto 1` | Master 打印 `tdma auto rc=0 enable=1`，随后为每个 tag 打 `CFG assigned[...]`；每个 tag 应回 `BSxxxx notify: CFG_OK ... LIVE=1`。demo 只要求至少三个 `LIVE=1`。 |
| 后续数据 | 30 s 内每 tag 超过 50 条 `notify: TR;...`，且至少 90% 的记录有 ≥7 个有效 anchor。 |

原文：`three_tag_demo_readiness/demo_start.py:43-48`。

### A2. 当前正式脚本的 distinct-slot 核心序列

以下是去掉重复 preseed 后的一轮最终 reassert；`tdma roster` 对每个
目标重复：

```text
tdma hold 1
tdma clear
tdma freq motion 10
tdma roster <BSxxxx> motion
conn
cmd_all CFG_STOP
cmd_all CIR OFF
tdma hold 0
tdma rebalance
tdma show
status
device show
```

最终真正到达 tag 的不是 roster 文本，而是 Master 生成的：

```text
CFG TAG=<id> SLOT=<slot> COUNT=<count> MASK=0x<4hex> PERIOD=10 ACTIVE=9 EPOCH=<relative_ms> GEN=<n> PMODE=0 AMODE=0
```

tag 确认：

```text
CFG_OK TAG=<id> SLOT=<slot>/<count> MASK=0x<4hex> PERIOD=10 ACTIVE=9 ACTIVE_US=0 GEN=<n> LIVE=1 RUN=1 STATE=RUNNING
```

注意：`LIVE=1` 是“runtime update 已排队”的返回码，不是已经观察到
首个 TX。

## 附件 B：直配移植要点

1. 最后一跳复用大写 `CFG TAG=...`；不要把 Master 的 `tdma roster/auto/rebalance` 移到 B306。
2. 必填字段是 `TAG/SLOT/COUNT/PERIOD/ACTIVE/EPOCH`；`MASK/ACTIVE_US/GEN/RUN/PMODE` 可选且有默认值。
3. Master 还发送 `AMODE=0`，但现有 tag parser 完全忽略它。
4. `EPOCH` 是相对延迟，不是跨设备绝对时间；tag 收到后转换为本地 deadline。
5. `CFG_OK LIVE=1` 仅代表接受/排队；中继若要强确认，应再设计 applied/首 TX 证据。
6. parser 目前只从 BLE NUS 进入，reply 固定走 `bt_nus_send(NULL)`；必须抽象 request source/reply sink。
7. Fusion UART 目前只有 96 B 二进制 TX，没有 RX；必须新增 UART RX framing，且不能与 BSL 数据帧混淆。
8. Fusion fork 未删 APOS/EKF 命令；APOS 仍在，EKF 从来不是命令。新增命令仅为 BSL/capture 控制。
9. B306 只有 FICR 派生 `BSFxxxx`，没有可写 board_id/NVS override；人工板号需要新机制。
10. 新链路命令上限应覆盖至少 160 B；不要继承旧 Master CDC 的 63 字符限制。

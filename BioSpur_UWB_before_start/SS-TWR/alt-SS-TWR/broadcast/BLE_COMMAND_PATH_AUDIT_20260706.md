# UWB 固件命令下发路径审计 — Tag Master → Anchor / Tag

**日期**：2026-07-06
**目的**：为「从 DWM1001C 上移除运行期 BLE」做决策，逐条判定 Tag Master 下发给 anchor/tag 的每条命令走的是 **BLE / UWB 帧内净荷 / UART**，并给出迁移影响。
**方法**：静态源码审计（只读）。覆盖 BLE GATT 服务/特征定义、命令枚举、发送端（master）到接收端（anchor/tag）的完整链路。
**路径约定**：本文件所有 `path:line` 均相对仓库根 `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`。UUID 已按 `BT_UUID_INIT_128` 小端字节序核对。

---

## 0. 一句话结论

**当前系统 100% 的运行期命令与遥测都走 BLE。** UWB 空口只承载 SS-TWR 测距本身（地址 + 序号 + POLL/RESP 操作码 + 两个时间戳），**没有任何命令搭载在 UWB 帧内**。那个「看起来像 UWB 命令通道」的 `uwb_control_proto`（PING/SWEEP/STOP/RANGE/AUTOPOS）是**孤立死代码，根本没被编译**。UART 只在 **anchor 板载**（provisioning 子集）和 **master 主机控制台**上存在。**OTA/DFU 也走运行期 BLE（SMP）——它不是「非运行期、不受影响」的，这一点与常见前提相反。**

关键角色定位（影响迁移判断）：

- **Master（Tag Master）= `master_control`**，跑在 **nRF52840DK / nRF5340DK 开发板**上（见 `apps/master_control/boards/nrf52840dk_nrf52840.overlay`），本质是一个 **BLE central**。
- **Anchor 和 Tag = DWM1001C 节点**（BLE peripheral）——即要移除运行期 BLE 的对象。
- 因此：**一旦从 DWM1001C 上拿掉 BLE，master 现有的所有下行命令就全部失去接收端**，无论 master 自己是否保留 BLE。

---

## 1. 传输层 / 角色 / 编译开关映射

| 节点 | 应用 / 源码树 | 编译进的传输 | 命令入口 |
|---|---|---|---|
| Master | `apps/master_control` = `master_control/src/main.c` + `master/src/master_multi_app.c` + `master_ota/src/main.c` | `BT_CENTRAL` + `BT_NUS_CLIENT` + `BT_DFU_SMP` + `BT_SCAN`，`BT_MAX_CONN=10` | 主机 UART 控制台 → 转成 BLE 下发 |
| Anchor | `apps/anchor`（顶层 `src/anchors/unified/*`）；**广播树** `SS-TWR/alt-SS-TWR/broadcast/…` 是超集 | `BT_PERIPHERAL`（`ANCHOR-U`）；OTA 仅在 `prj_ota.conf` overlay 里加 `MCUMGR_TRANSPORT_BT` | BLE 自定义 GATT `control` 特征 + 板载 UART |
| Tag | `apps/tag`（`uwb_tag_ble.c` + `tag_app.c`） | `BT_PERIPHERAL` + `BT_NUS` + 完整 `MCUMGR_TRANSPORT_BT` | BLE **NUS RX** 文本命令（**无 UART 命令入口**） |

### 两套源码树（重要）

- **顶层** `apps/anchor` + `src/anchors/unified`（737 行 `anchor_ble_ctrl.c`，无 CIR/超声）是精简 canonical 版。
- **广播树** `SS-TWR/alt-SS-TWR/broadcast/…`（879 行 `anchor_ble_ctrl.c`）是**当前分支实际在用的成像版**，多出 CIR 输出模式、超声、`DFU` token。

下表凡标注「广播树」的即仅存在于后者。

### Anchor 自定义 GATT 服务（master 与 anchor 双方一致）

基址 `2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f0`：

| UUID 尾 | 特征 | 属性 | 方向 |
|---|---|---|---|
| `…d3f0` | 服务 | PRIMARY | — |
| `…d3f1` | STATE | READ + NOTIFY | 遥测出 |
| `…d3f2` | ACTIVE | READ | 遥测出 |
| `…d3f3` | PENDING | READ | 遥测出 |
| **`…d3f4`** | **CONTROL** | **WRITE + WRITE_NO_RESP** | **唯一命令入口** |
| `…d3f5` | RESULT | READ + NOTIFY | 遥测出（含每扫 `SW-x,…` 结果流） |

定义见 `src/anchors/unified/anchor_ble_ctrl.c:40-59`、`:610-625`。

---

## 2. 命令全表（按传输平面分组，每行一条命令）

### 2.1 Master → **Tag**：BLE / Nordic UART Service（NUS RX 文本命令）

Tag 使用 stock NUS：RX `6E400002-…`（写，全部命令入口）、TX `6E400003-…`（通知，全部遥测）。

| 命令 / 码 | 功能 | 传输 | 发送端 file:line | 接收端 file:line | 移除 BLE 的影响 |
|---|---|---|---|---|---|
| `CFG TAG=.. SLOT=.. COUNT=.. MASK=.. PERIOD=.. ACTIVE=.. EPOCH=.. GEN=.. PMODE=.. AMODE=..` | **TDMA 时隙分配 + epoch 同步 + 定位模式 + anchor 计划**（`slot_source=MASTER`） | BLE-NUS | `apps/master/src/master_multi_app.c:1186-1198`，由 `:1430`(`master_rebalance_tdma_slots`) 触发 | `apps/tag/src/uwb_tag_ble.c:1957` → `:930`（`MASTER` 标记 `:983`）→ `src/ss_twr_init.c:3664` | **断**：核心命令。tag 无 UART、无 UWB 收命令通道 |
| `MODE RANGE\|SOLVE\|DEBUG\|AOTA` | 切定位模式 | BLE-NUS | 主机 → master `cmd` | `apps/tag/src/uwb_tag_ble.c:1861` → `:789` | **断** |
| `TDMA_SET <slot>` | 单时隙覆盖（`slot_source=SETTINGS`，落 NVS） | BLE-NUS | 同上 | `apps/tag/src/uwb_tag_ble.c:1916` → `:836` → `src/ss_twr_init.c:3646` | **断** |
| `CFG_RUN` / `CFG_STOP` | 起 / 停 TDMA 环 | BLE-NUS | 同上 | `apps/tag/src/uwb_tag_ble.c:1993`,`:1998` | **断** |
| `REBOOT` | 冷复位 | BLE-NUS | 同上 | `apps/tag/src/uwb_tag_ble.c:2064` | **断**（tag 无 UART reboot） |
| `WAND …` | wand 对测控制（`APP_TAG_WAND_MODE_ENABLE`） | BLE-NUS | 同上 | `apps/tag/src/uwb_tag_ble.c:1507` | **断**（控制面；UWB 对测本身直连） |
| `cmd` / `cmd_all` / `oneshot <raw>` | 主机透传任意串（含 `cmd_all STOP` = b66） | BLE-NUS | `apps/master_control/src/main.c:2271`/`:2278` → `apps/master/src/master_multi_app.c:3453`/`:3523` | 各对应 handler | **断** |
| 遥测：`STATUS` 文本 / `BP` 位置样本流 / `CM` 标定流 | tag **唯一**的数据输出 | BLE-notify (NUS TX) | — | 发出：`apps/tag/src/uwb_tag_ble.c:2111`/`:2205`/`:2269` | **断**：移动 tag 从此无法上报位置（只剩 USB `printk` 日志） |

### 2.2 Master → **Anchor**：BLE / 自定义 anchor-ctrl GATT（写 `…d3f4`）

接收端行号为顶层 `src/anchors/unified/anchor_ble_ctrl.c`；广播树同名文件行号略有偏移但语义一致。

| 命令 / 码 | 功能 | 传输 | 发送端 file:line | 接收端 file:line | 移除 BLE 的影响 |
|---|---|---|---|---|---|
| `R MASTER` / `R MATRIX`；`ROLE/LABEL/GEN` | 暂存 anchor 角色 / 标签 / 代数 | BLE-GATT-write | `apps/master_control/src/main.c:1481`/`:1484` → `apps/master/src/master_multi_app.c:3440` | `anchor_ble_ctrl.c:531`/`:544`/`:557` | UART 有等价（`M/X/P`,`ID`），**可存活** |
| `VALIDATE` | 校验暂存配置 | BLE-GATT-write | `apps/master_control/src/main.c:1368` | `anchor_ble_ctrl.c:394` → `:246` | **断**（UART 无） |
| `COMMIT` / `APPLY` | 写 flash + 代数 +1，触发重启 | BLE-GATT-write | `apps/master_control/src/main.c:1741` | `anchor_ble_ctrl.c:399` → `:259`（flash `:278`） | UART `SAVE` 能持久化但不升代数：**部分存活** |
| `REBOOT` | 冷复位 | BLE-GATT-write | `apps/master_control/src/main.c:1382`,`:1823` | `anchor_ble_ctrl.c:605`(`sys_reboot`) | UART `RB`/`REBOOT`：**可存活** |
| `RUNTIME MASTER\|MATRIX\|RESPONDER [FORCE\|RESTART]` | **热切角色（不重启）** | BLE-GATT-write | `apps/master_control/src/main.c:1841`,`:1487`,`:1546` | `anchor_ble_ctrl.c:409` → `:336` | **断**（UART 无热切；只能 SAVE + 重启） |
| `RUNTIME MASTER SWEEP <N>` | **有限次圆周-SAR 扫描**（roto） | BLE-GATT-write | `apps/master_control/src/main.c:1896` | `anchor_ble_ctrl.c:426-446` | **断** |
| `RESET AUTOPOS` / `RESET RESPONDER` | 一键复位角色基线 | BLE-GATT-write | `apps/master_control/src/main.c:1668`/`:1671` | `anchor_ble_ctrl.c:456`/`:466` | 可用 UART 3 步复现：**部分存活** |
| `STOP` | 停当前测距 / 流 | BLE-GATT-write | `apps/master_control/src/main.c:1784`,`:1806` | `anchor_ble_ctrl.c:474` | **断** |
| STATE / RESULT 读 + 通知（角色 / cfg_valid / busy / fw；`SW-x,…` 每扫结果流） | anchor 遥测 | BLE-read/notify | 读：`apps/master/src/master_multi_app.c:3351`/`:3361` | 发：`src/ss_twr_anchor_init.c:96`,`:366` | 推流 **断**；UART `STATUS` 仅轮询静态 |

### 2.3 Master → Tag/Anchor **OTA**：BLE / MCUmgr SMP-DFU

| 命令 / 码 | 功能 | 传输 | 发送端 file:line | 接收端 | 影响 |
|---|---|---|---|---|---|
| `OTA_PREPARE` / `OTA_BEGIN`（仅 tag） | 把 tag 切进 DFU 广告、暂停遥测 | BLE-NUS | `apps/master_ota/src/main.c:959`,`:967` | `apps/tag/src/uwb_tag_ble.c:2013-2053` | **断** |
| IMG ERASE / UPLOAD / STATE / PENDING / **OS RESET** | 擦除 → 传镜像 → 标 pending → 复位切换 | BLE-SMP-DFU（`bt_dfu_smp` → `bt_gatt_write`） | `apps/master_ota/src/main.c:1817`/`:1684`/`:1840`/`:1751`/`:1790` | 自动 SMP 服务（`MCUMGR_TRANSPORT_BT`）；anchor 侧观测 `src/anchors/unified/anchor_mcumgr_diag.c`，anchor OTA 仅 `prj_ota.conf` 构建 | **断**：SMP **只有 BLE** 传输，无 UART/shell SMP |

### 2.4 **Anchor 板载 UART**（`src/anchors/unified/uart_role_switch.c`，`zephyr,console` 串口）— 不依赖 BLE

| 命令 | file:line |
|---|---|
| `ROLE?` | `:287` |
| `STATUS` | `:291` |
| `M`·`X`·`P`·`MASTER`·`MATRIX`·`RESPONDER` | `:295-306` |
| `ID <A-H>` | `:307` |
| `ANCHOR SET <A-H>` | `:331` |
| `SAVE`·`CONFIG SAVE` | `:315` |
| `RB`·`REBOOT`（`cmd_reboot:257` 冷复位，**无 GPREGRET**） | `:319` |

→ **provisioning、持久化、重启、静态状态查询在 UART 上全部存活。**

### 2.5 **广播树专属**：CIR / 超声（`SS-TWR/alt-SS-TWR/broadcast/…`）

| 命令 / 码 | 功能 | 传输 | 接收端 file:line | 影响 |
|---|---|---|---|---|
| `RUNTIME … CIR=0\|COMPACT\|FULL` | **CIR verbose / 输出模式切换** | BLE-GATT-write（RUNTIME 子 token） | `SS-TWR/alt-SS-TWR/broadcast/src/anchors/unified/anchor_ble_ctrl.c:565-582` | **断**（UART 无 CIR） |
| CIR 数据：compact | 紧凑 CIR 特征流 | **BLE-notify**（默认 `..._BLE_ENABLE=1`） | `SS-TWR/alt-SS-TWR/broadcast/src/anchors/unified/anchor_cir_output.c:154` | **断**（数据面） |
| CIR 数据：full | 全量 CIR dump | **USB-CDC**（`..._FULL_OUTPUT_CDC_ENABLE=1`） | `SS-TWR/alt-SS-TWR/broadcast/src/anchors/unified/anchor_cir_output.c:176` | 数据面 **存活**（但无 BLE 就切不进 FULL 模式） |
| `US?` / `USON [SEC]` / `USOFF` | 超声控制 | **BLE + UART**（已镜像） | BLE `anchor_ble_ctrl.c:470-482`；UART `uart_role_switch.c:328-341`（均在广播树） | UART **存活** |
| `DFU` / `ENTER_DFU` | 静默测距、准备 OTA（**不进 bootloader**） | BLE-GATT-write | `SS-TWR/alt-SS-TWR/broadcast/src/anchors/unified/anchor_ble_ctrl.c:611` | **断** |

### 2.6 死代码：`uwb_control_proto`（UWB 空口——当前**未接线**）

`PING/START_SWEEP/STOP/SINGLE_RANGE/START_AUTOPOS` + `0x55 0x42` 组帧：仅有 build/encode 函数（`src/uwb_control_proto.c:38-108`），**无发送方、无解码器、任何 CMakeLists 都没有编译它**。SS-TWR 帧里 poll 的 10-11 字节、resp 的多余字节全部清零（`src/uwb_ss_twr_shared.c:72-89`）。

→ 它正是要建的「UWB 空口命令通道」的现成骨架，但目前是死的。

---

## 3. 四个重点问题的直接回答

1. **TDMA 时隙分配 / 调度信令 → 走 BLE。** master 用 ASCII `CFG` 串经 NUS 下发（`apps/master/src/master_multi_app.c:1186`），tag 解析入 `tdma.*`。连「epoch」都是 **BLE 下发的相对延迟**，每个 tag 转成本地时钟（`src/uwb_tdma.c:44-62`）——**UWB 帧里没有任何时隙 / epoch 字节**。

2. **OTA/DFU → 走 BLE（SMP），GPREGRET 与 anchor/tag 无关。** 标准 MCUboot「SMP-over-BLE 上传镜像 + OS reset 换 slot」。anchor/tag 固件**从不设置 GPREGRET**；唯一用 `nrf_power_gpregret_set(…, BOOTLOADER_DFU_START)` 的是**另一个应用** `SS-TWR/alt-SS-TWR/broadcast/apps/ble_listener/src/main.c:576`（USB-CDC dongle，走 Nordic Open Bootloader，与本命题无关）。GATT `DFU` token 只是静默测距、并不进 bootloader。**⚠️ 修正常见前提：这里的 DFU 恰恰是运行期 BLE 操作，会随 BLE 一起断掉。**

3. **CIR verbose / 诊断模式切换 → 走 BLE**（仅广播树）：`RUNTIME … CIR=OFF|COMPACT|FULL` 经 `…d3f4` 控制特征（`:565-582`）。数据面：compact 走 BLE 通知、full 走 USB-CDC。**模式切换命令无 UART 等价，会断。**

4. **其他配置**：anchor 角色 / 标签 / 持久化 / 重启 / 状态查询 **BLE + UART 双通道**；超声 **BLE + UART**；tag 的一切（模式 / 时隙 / run-stop / wand / 遥测 / OTA）**纯 BLE 单通道**。

---

## 4. 迁移影响总结（移除 DWM1001C 运行期 BLE）

### A. 必须重新落地到 UWB 帧内或新通道（BLE-only、运行期关键、无任何回退）

- **Tag 全部**：`CFG`（TDMA 时隙 + epoch + 模式 + anchor 计划）、`MODE`、`TDMA_SET`、`CFG_RUN/STOP`、`REBOOT`、`WAND`，以及 **tag 的全部遥测（STATUS/BP/CM）**。tag 是移动节点、无线可插，**只能靠 UWB 空口**双向通道——把 `uwb_control_proto` 接上（下行命令搭 anchor 广播帧，上行遥测搭 tag 的 poll 帧空余字节），否则 tag 退化为「仅开机配置、不可运行期控制、不能上报位置」。
- **Anchor（广播树）**：`CIR=` 模式切换、`RUNTIME` 热切 / `SWEEP N` / `FORCE`、`STOP`、`VALIDATE`、`GEN`、`DFU`，以及 RESULT / CIR-compact 推流。anchor 是固定且有线的，**这些优先补进板载 UART**（成本最低），CIR-full 已在 USB-CDC 上。

### B. 已有 UART 回退、可存活（主要是 anchor 的 provisioning 面）

- anchor 角色设定、anchor id/label、`SAVE` 持久化、`REBOOT`、`STATUS` 查询、超声 `US*`——UART 全覆盖；CIR-full dump 已走 USB-CDC。
- 核心 SS-TWR 测距是纯 UWB，**不受影响**（tag/anchor 掉 BLE 后仍按 BUILD/NVS 配置继续测距）。

### C. 可删除 / 天然不受影响

- `uwb_control_proto`（PING/SWEEP/STOP/RANGE/AUTOPOS）：当前死代码——要么删，要么**正好拿它做 A 里的 UWB 命令通道骨架**。
- 身份码（FICR `DEVICEID` 派生）、MCUboot 换 slot 机制本身：与 BLE 无关。

### D. 需要专门决策：OTA

OTA/DFU 是 **BLE-SMP 独占**，随 BLE 一起消失。无 BLE 后的固件更新只剩：

1. 保留一个专用的「BLE-OTA 窗口 / 构建」（anchor 现在就是 `prj_ota.conf` 独立构建）；
2. 有线 SWD/J-Link 重刷。tag 无线、无 SWD 便利，需重点权衡。

---

## 5. 战略提醒

Master（`master_control`）跑在开发板上、其**全部**下行能力都是 BLE。DWM1001C 一旦去 BLE，不是「少几条命令」的问题，而是 **master 与 anchor/tag 之间的整个控制平面失联**——anchor 可退到有线 UART，tag 则**必须**新建 UWB 空口双向通道才能继续运行期工作。

---

*本审计为静态源码只读分析。行号基于审计当日仓库快照，后续改动可能偏移。*

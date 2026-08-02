# Fusion Master 与 5 块 Fusion PCB 的 BLE 连接架构

状态：已在真实五板台架上完成连接层 bring-up  
日期：2026-07-26  
Fusion Master：nRF52840 DK，J-Link SNR `683234364`  
已部署镜像：`dk-fusion-imu-relay-v19`  
镜像 SHA-256：`f1a469e7d2fd33fba507fbe32387b849704c2ad6d3fbabe480f51f5d9a1d8231`

## 1. 结论先行

Fusion Master 现在可以同时维持 5 条 B306 BLE 连接。现场验收门不是
`count=5`，而是：

```text
FUSION_LIST count=5 ready=5 scanning=0 capacity=5
```

`count=5` 只说明 5 条 BLE ACL 连接存在。`ready=5` 还要求每个节点都完成
ATT MTU 交换、GATT service discovery、data notify 订阅、telemetry notify
订阅和 control characteristic discovery。只有 `ready=5` 才能开始多节点
数据采集或逐节点控制。

五节点初始化采用串行流程：

```text
扫描一个节点
  -> 建立一条连接
  -> 完成该节点全部 GATT 初始化
  -> 标记该节点 bridge_ready
  -> 恢复扫描
  -> 连接下一个节点
```

进入稳态后，5 条连接同时存在。应用不会轮流断开和重连，也没有手写一个
"节点 1 到节点 5" 的 BLE 轮询器。nRF52840 的 Bluetooth Controller 负责
安排各连接的 connection event；Fusion Master 应用负责识别连接、接收
notification、保存每节点状态并把记录复用到 USB CDC。

当前现场状态：

| BSF 名称 | DWM1001C BS 号 | B306 固件 | DWM 固件 | BLE bridge |
|---|---|---|---|---|
| `BSF3C79` | `BS065F` | `b306-imu-relay-v26` | `tag-fusion-link-v2-relay3` | ready |
| `BSFC2CC` | `BSE88E` | `b306-imu-relay-v26` | `tag-fusion-link-v2-relay3` | ready |
| `BSF44AD` | `BS6F3A` | `b306-imu-relay-v26` | `tag-fusion-link-v2-relay3` | ready |
| `BSF6C53` | `BSF8E0` | `b306-imu-relay-v26` | `tag-fusion-link-v2-relay3` | ready |
| `BSF8BC4` | `BSEFD2` | `b306-imu-relay-v26` | `tag-fusion-link-v2-relay3` | ready |

上表只记录协议路由所需的电子身份。操作者已取消左到右物理位置映射；
不按位置、尾号或单板稳定性给五块板排名。五节点只按集合门验收。
30 分钟正式 soak 和恢复后的 5 分钟集合复核均通过，详见
`../logs/pre_ramp_hardening_20260726/REPORT.md`。

## 2. 硬件和角色

每块 Fusion PCB 内有两颗独立 MCU：

```text
DWM1001C/nRF52832 -- UART range + READY strobe --> B306/nRF52840
JY61P             -- I2C IMU ------------------> B306/nRF52840
B306/nRF52840     -- BLE peripheral ----------> Fusion Master DK
```

DWM1001C 不直接连接 Fusion Master。Fusion Master 看到的五个 BLE
peripheral 全是 B306。DWM1001C 的 raw range 和 JY61P 的 IMU 数据先在各自
B306 上用同一 TIMER2 时间轴封装，然后由 B306 发 notification。

中央设备只有一台：

```text
5 x Fusion PCB/B306
        |
        | 5 independent BLE connections
        v
nRF52840 DK 683234364
        |
        | native USB CDC, VID:PID 2FE3:10F4
        v
PC capture and control tools
```

nRF54L15 在本次实验中只做被动 BLE observer，不连接任何 B306。Tag Master
和 Anchor Master 属于 UWB 控制面，不参与这五条 B306 BLE 连接。

## 3. 两种 "调度" 必须分开理解

### 3.1 应用层 bring-up 调度

Fusion Master v16 串行初始化节点。任何时刻最多只有一个节点处于
"正在连接或正在做 GATT discovery" 的状态。已经 ready 的连接保持在线并
继续接收数据。

这项串行化解决的是 Host/GATT 初始化可靠性。它不限制稳态吞吐，也不表示
Fusion Master 稳态时只服务一个节点。

相关实现：

- 最大节点数 `MAX_FUSION_PEERS=5`：
  `B306_Part/host/fusion_master/src/main.c:32-35`
- 每节点独立状态对象 `struct fusion_peer`：
  `B306_Part/host/fusion_master/src/main.c:86-118`
- 五元素 peer 表：
  `B306_Part/host/fusion_master/src/main.c:120-121`
- 一个全局 `connecting` 状态防止并发 initiate：
  `B306_Part/host/fusion_master/src/main.c:64-70`
- 当前节点完成 `bridge_ready` 后才调用 `start_scan()`：
  `B306_Part/host/fusion_master/src/main.c:1278-1305`

### 3.2 BLE Link Layer 射频调度

五条连接 ready 后，Zephyr Bluetooth Host 和 Nordic SoftDevice Controller
维持五个独立 connection handle。每条连接有自己的 connection anchor。
Controller 在一个 50 ms 周期内安排实际射频 event，并在允许的 event
长度内收发多个 Link Layer packet。

Fusion Master 应用没有给五个节点写固定 BLE slot，也不保证它们在 50 ms
周期内按 BSF 名字或连接顺序出现。连接数组的 index 是本次启动的发现顺序，
不是射频优先级、物理位置或 UWB TDMA slot。

UWB TDMA 是 DWM1001C 的另一套时间调度。BLE Controller 不读取
`SUPERFRAME_BASE`，BLE connection event 也不与 UWB slot 对齐。两套调度
只在 B306 产生的数据和时间戳中相遇。

## 4. B306 如何被识别

### 4.1 BSF 名称来源

B306 从 nRF52840 FICR `DEVICEID[0:1]` 推导 16 位 identity，然后生成：

```text
BSF%04X
```

实现位于：

- identity 推导：`B306_Part/firmware/src/main.c:1511-1518`
- 动态 BLE 名称：`B306_Part/firmware/src/main.c:1546-1550`

这个 BSF 名称标识 B306，不等于同一 PCB 上 DWM1001C 的 `BSxxxx`。

### 4.2 Advertising 内容

B306 的主 advertising packet 包含：

- General Discoverable / BR-EDR not supported flags
- Fusion service UUID `7b120001-4e77-4a71-a045-7b4d3f2a9000`

scan response 包含：

- 完整名称 `BSFxxxx`
- 固件族 manufacturer marker

定义和启动代码：

- advertising data：`B306_Part/firmware/src/main.c:337-350`
- scan response 和 `bt_le_adv_start()`：
  `B306_Part/firmware/src/main.c:1459-1471`

Fusion Master 使用 active scan，因为 service UUID 和完整名称可能来自
advertising packet 与 scan response 两个不同 PDU。Master 会把同一随机
地址的两部分信息合并，直到同时得到：

```text
has_service = true
has_name = true
```

Master 的字段解析和候选合并位于：

- 名称和 UUID 解析：`B306_Part/host/fusion_master/src/main.c:683-722`
- 候选合并：`B306_Part/host/fusion_master/src/main.c:780-817`

### 4.3 去重

Master 同时按 BLE 地址和 `BSFxxxx` 名称去重。已经在 peer 表中的设备不会
再次进入连接流程：

`B306_Part/host/fusion_master/src/main.c:742-745,795-798`

当前没有 whitelist，也没有预先固定五个 BSF。若现场出现超过五个合法
Fusion peripheral，Master 会保留先完成连接的五个。容量实验要求房间里
只有这五块 Fusion PCB，正是为了消除该不确定性。

## 5. 从上电到五连接稳定的完整时序

### 5.1 Fusion Master 启动

Master 按以下顺序启动：

1. 初始化 native USB CDC。
2. 打印固件 marker 和最大连接数。
3. 调用 `bt_enable(NULL)` 启动 Host 和 Controller。
4. 开始 active scan。
5. 主线程每 10 秒输出 health，并在容量未满时补启动扫描。

代码：`B306_Part/host/fusion_master/src/main.c:1609-1645`

v16 banner：

```text
FUSION_MASTER marker=dk-fusion-imu-relay-v16
probe=683234364 pc=USB_CDC rtt=control+log max_conn=5
```

### 5.2 扫描参数

Master 使用 Zephyr fast active scan：

```c
type     = BT_LE_SCAN_TYPE_ACTIVE
interval = BT_GAP_SCAN_FAST_INTERVAL
window   = BT_GAP_SCAN_FAST_WINDOW
```

代码：`B306_Part/host/fusion_master/src/main.c:820-845`

扫描会在两种情况下停止：

- 已经选中一个完整候选，准备发起连接。
- peer 表已经有 5 个 allocated entry。

### 5.3 为候选分配 peer

Master 从固定的五元素数组中取一个空 entry，并保存：

- `bt_conn *`
- BLE 地址
- `BSFxxxx`
- 扫描 RSSI
- discovery 状态和参数
- 三个 characteristic handle
- 两个 CCC subscription 参数
- CI、latency、supervision timeout、TX/RX PHY
- 该节点的 timestamp extension 状态
- 该节点的 packet、malformed 和 logger-drop 计数

结构定义：
`B306_Part/host/fusion_master/src/main.c:86-118`

分配和释放：
`B306_Part/host/fusion_master/src/main.c:186-210`

peer 的 discovery 和 subscription 对象不能共享。五条连接如果共用一组
`bt_gatt_discover_params` 或 `bt_gatt_subscribe_params`，后一个节点会覆盖
前一个节点仍在使用的回调状态。

### 5.4 发起连接

Master 在 `bt_conn_le_create()` 前停止扫描。初始请求参数是：

| 参数 | 请求值 |
|---|---:|
| interval minimum | 12 units = 15 ms |
| interval maximum | 24 units = 30 ms |
| peripheral latency | 0 |
| supervision timeout | 400 units = 4 s |

代码：`B306_Part/host/fusion_master/src/main.c:725-777`

这些是连接请求值，不是最后的现场值。B306 的 mcumgr connection parameter
control 随后参与参数更新。v16 的 `LIST` 读取最终值。

### 5.5 连接完成后的链路升级

`connected()` 回调成功后，Master 依次请求：

1. 2M PHY，TX 和 RX 都偏好 2M。
2. Data Length Extension，最大 Link Layer payload 251 bytes。
3. ATT MTU exchange。

代码：`B306_Part/host/fusion_master/src/main.c:1346-1400`

相关 Kconfig：

| 配置 | v16 值 |
|---|---:|
| `CONFIG_BT_MAX_CONN` | 5 |
| `CONFIG_BT_L2CAP_TX_MTU` | 247 |
| ACL RX/TX buffer size | 251 |
| ACL RX buffer count | 16 |
| ATT TX contexts | 16 |
| L2CAP TX buffers | 16 |
| ACL TX buffers | 16 |

证据：`B306_Part/host/fusion_master/prj.conf:1-18`

这些 buffer count 是 Master 共享资源，不是每连接各 16 个。

### 5.6 GATT discovery 状态机

每个 peer 有独立的六阶段状态机：

```text
DISCOVERY_SERVICE
  -> DISCOVERY_DATA_CHARACTERISTIC
  -> DISCOVERY_DATA_CCC
  -> DISCOVERY_TELEMETRY_CHARACTERISTIC
  -> DISCOVERY_TELEMETRY_CCC
  -> DISCOVERY_CONTROL_CHARACTERISTIC
```

枚举：`B306_Part/host/fusion_master/src/main.c:77-84`

发现顺序和结果：

| 阶段 | Master 动作 | 成功后保存 |
|---|---|---|
| primary service | 按 Fusion service UUID 查找 | service end handle |
| data characteristic | 按 data UUID 查找 | data value handle |
| data CCC | 枚举下一 descriptor 并核对 UUID 0x2902 | data CCC handle |
| telemetry characteristic | 按 telemetry UUID 查找 | telemetry value handle |
| telemetry CCC | 枚举下一 descriptor 并核对 UUID 0x2902 | telemetry CCC handle |
| control characteristic | 按 control UUID 查找并检查 write 属性 | control value handle |

状态机主体：
`B306_Part/host/fusion_master/src/main.c:1072-1310`

data CCC 和 telemetry CCC 都采用 "枚举 descriptor，再核对 UUID"。现场证明
对已部署 Zephyr B306 使用 UUID-filtered CCC discovery 时，第二条及后续连接
可能返回假的 `not_found`。v16 统一使用 descriptor enumeration：

- data CCC：`B306_Part/host/fusion_master/src/main.c:1128-1157`
- telemetry CCC：`B306_Part/host/fusion_master/src/main.c:1195-1237`

若 discovery 返回 `attr == NULL`，Master 不会把半初始化连接留在 peer 表中。
它主动断开该连接；disconnect callback 释放 entry 并重新扫描：

- discovery abort：`B306_Part/host/fusion_master/src/main.c:1083-1091`
- disconnect recovery：`B306_Part/host/fusion_master/src/main.c:1403-1421`

### 5.7 ready 门和下一个节点

Master 只有在以下条件全部成立后才设置：

```c
peer->bridge_ready = true;
```

条件包括：

- primary service 存在
- data characteristic 可 notify
- data CCC 已订阅
- telemetry characteristic 可 notify
- telemetry CCC 已订阅
- control characteristic 可 write

设置 ready 后，Master 打印 `FUSION_BRIDGE_READY`，读取该连接的参数，然后
恢复扫描。代码：
`B306_Part/host/fusion_master/src/main.c:1251-1305`

这就是 v16 串行 bring-up 的边界。扫描不会在 `connected()` 回调里提前恢复。

## 6. 五连接稳态

### 6.1 现场协商结果

五个 peer 的最终 `LIST` 结果如下。RSSI 是连接前最后一次扫描值，不是连续
RSSI telemetry。

| peer index | BSF | RSSI | subscribed | control | CI | latency | timeout | PHY |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 0 | `BSF3C79` | -57 dBm | 1 | 24 | 50 ms | 0 | 420 ms | 2M/2M |
| 1 | `BSF6C53` | -64 dBm | 1 | 24 | 50 ms | 0 | 420 ms | 2M/2M |
| 2 | `BSF8BC4` | -58 dBm | 1 | 24 | 50 ms | 0 | 420 ms | 2M/2M |
| 3 | `BSFC2CC` | -61 dBm | 1 | 24 | 50 ms | 0 | 420 ms | 2M/2M |
| 4 | `BSF44AD` | -63 dBm | 1 | 24 | 50 ms | 0 | 420 ms | 2M/2M |

现场记录里的单位值：

```text
interval_units=40
interval_us=50000
latency=0
timeout_units=42
phy_tx=2
phy_rx=2
```

Bluetooth supervision timeout unit 是 10 ms，所以 `42` 表示 420 ms。这个
最终值短于 Master 初始请求中的 4 s。容量测试必须逐连接记录该值，不能把
初始请求值写成实测结果。

### 6.2 Controller 如何容纳五条 50 ms 连接

每条连接每秒有 20 个 nominal connection event。五条连接合计每秒约 100 个
connection event。各 event 的 anchor point 由 Controller 管理。应用只在
GATT notification 回调被调用时看到已经收妥的 ATT value。

一个 connection event 可以携带多个 Link Layer data PDU。当前每个应用记录
都能装入一个 ATT notification：

| 记录 | 最大/固定长度 | 是否小于 ATT payload 244 B |
|---|---:|---|
| UWB kind 1 | 184 B | 是 |
| telemetry kind 2 | 235 B | 是 |
| IMU kind 3, N=2 | 40 B | 是 |
| IMU kind 3, N=5 | 82 B | 是 |
| control reply kind 4 | 最大 207 B | 是 |

共享 schema 的静态尺寸门：
`B306_Part/include/biospur_fusion_ble.h:235-253`

2M PHY、DLE 251 和 MTU 247 的作用是让这些记录免于应用层 fragmentation。
它们不会保证 notification 一定成功。B306 仍用 `drop_err` 和
`last_notify_error` 记录 `bt_gatt_notify()` 失败。

### 6.3 满负载的应用 payload 预算

当前 diagnostic wire format 在 IMU `N=2` 时，每节点大约产生：

```text
UWB:       10 records/s  * 184 B = 1,840 B/s
IMU:      100 records/s  *  40 B = 4,000 B/s
telemetry:  1 record/s   * 235 B =   235 B/s
-----------------------------------------------
total application payload              6,075 B/s
```

五节点约为 30,375 B/s application payload，外加 ATT、L2CAP 和 Link Layer
overhead。notification 数约为每节点 111/s，五节点约 555/s。容量实验要测
实际 delivered rate，不能只根据这个预算宣称通过。

这里的 6.075 kB/s 是当前 diagnostic protocol 的 wire rate。项目架构文档中
约 3.4 kB/s 的数字描述未来 100 ms logical batching，不是当前 kind 1 +
kind 3 notification 实现。

## 7. GATT service 和通信方向

共享 UUID 定义：
`B306_Part/include/biospur_fusion_ble.h:92-107`

| UUID | 属性 | 方向 | 内容 |
|---|---|---|---|
| `7b120001-...` | primary service | - | Fusion service |
| `7b120002-...` | notify | B306 -> Master | UWB、IMU、control reply |
| `7b120003-...` | notify | B306 -> Master | telemetry |
| `7b120004-...` | write / write without response | Master -> B306 | ASCII command |

B306 service 定义：
`B306_Part/firmware/src/main.c:237-297`

### 7.1 Data characteristic

data characteristic 复用三种 record：

- kind 1：UWB + B306 strobe pairing record
- kind 3：IMU batch
- kind 4：B306 或 DWM tag 的 control reply

每条 record 以以下 header 开始：

```text
version:u8
kind:u8
len:u16 little-endian
```

Master 在 notification callback 中核对 protocol version、kind 和声明长度。
然后它按 `bt_conn *` 找到对应 peer，把 `name=BSFxxxx` 写进 host log record：

`B306_Part/host/fusion_master/src/main.c:848-900`

### 7.2 Telemetry characteristic

B306 每秒生成一次 235-byte telemetry。它包含 UART、strobe、BLE notify、
IMU、watchdog、relay 和 health counters：

- schema：`B306_Part/include/biospur_fusion_ble.h:119-188`
- B306 发布：`B306_Part/firmware/src/main.c:1371-1456`
- Master 校验：`B306_Part/host/fusion_master/src/main.c:1016-1069`

telemetry 使用独立 characteristic，避免大量 data record 让 health 状态在
解析器里失去边界。

### 7.3 Control characteristic

PC 发给 DK 的语法是：

```text
BSF#### <B306 command>
```

DK 从前七个字符查找 peer，并把整行通过该 peer 的 control handle 执行
`bt_gatt_write_without_response()`：

`B306_Part/host/fusion_master/src/main.c:1471-1528`

B306 再次核对行首的 `BSF####` 是否等于自己的 FICR-derived 名称。这个二次
校验防止 Master 路由错误时另一块板执行命令：

`B306_Part/firmware/src/main.c:1307-1330`

B306 的 GATT write callback 不在 Bluetooth callback context 中执行完整
命令。它把命令复制进 control queue，由 control thread 处理：

`B306_Part/firmware/src/main.c:1334-1368`

### 7.4 Reply 和 correlation

B306 给每条命令分配 16-bit correlation。kind 4 reply 包含：

```text
source:u8        0 = B306, 1 = DWM tag
correlation:u16
text:ASCII
```

本地命令通常得到一个 `source=B306` reply。DWM relay 命令先得到：

```text
source=B306 text=RELAY_QUEUED ...
```

DWM1001C 经 UART 回答后，B306 再发送相同 correlation 的：

```text
source=TAG text=...
```

schema：
`B306_Part/include/biospur_fusion_ble.h:221-233`

### 7.5 DWM1001C relay

例如读取 DWM 版本：

```text
PC -> DK:       BSF3C79 TAG RAW VERSION
DK -> B306:     GATT write to BSF3C79 control handle
B306 -> DWM:    UART relay command VERSION
DWM -> B306:    UART relay ack
B306 -> DK:     kind 4, source=TAG
DK -> PC:       FUSION_REPLY name=BSF3C79 ... text=VERSION ...
```

五块板现场均通过该路径报告 relay3 marker。DWM UART relay frame 与 96-byte
UWB data frame使用不同 magic、type、length、correlation 和 CRC，因此 B306
parser 能在同一 UART 上区分它们。

## 8. Master 如何复用五节点数据

### 8.1 BLE callback 到统一 log queue

每个 notification callback 先用 `peer_by_conn()` 取得节点身份。callback
完成校验和定长 copy 后，把一个带 `node_name` 的 record 放进共享
`fusion_log_queue`。

queue 深度是 128：

`B306_Part/host/fusion_master/src/main.c:309`

callback 不做长文本格式化。独立 logger thread 从 queue 取记录并转换成：

```text
FUSION_UWB name=BSFxxxx ...
FUSION_TELEMETRY name=BSFxxxx ...
FUSION_IMU name=BSFxxxx ...
FUSION_REPLY name=BSFxxxx ...
```

logger：
`B306_Part/host/fusion_master/src/main.c:659-681`

queue 满时，Master 增加全局和 per-peer `logger_drop`。这两个计数必须在容量
测试中同时为零。

### 8.2 每节点计数

v16 同时维护：

- Master 全局 `received_packets`、`malformed_packets`、`logger_dropped`
- 每个 peer 的同名计数
- B306 telemetry 中的 `notify_ok`、`drop_unsub`、`drop_err`

这样可以区分：

- 某个 B306 没有成功发出 notification
- Master 收到了但 schema 校验失败
- Master 收到了但 host log queue 满
- 五节点总体正常，但固定某一节点先退化

### 8.3 每节点时间扩展

IMU kind 3 只携带 TIMER2 low 32 bits。每个 B306 有自己的 TIMER2 epoch，
所以 Master 为每个 peer 单独保存：

- 最近完整 UWB timestamp
- telemetry `timer_wrap_count`
- 最近一次已经扩展的 IMU base

字段：`B306_Part/host/fusion_master/src/main.c:107-113`

如果五个节点共用一套 low-word extension 状态，一个节点的 wrap 会污染另一
节点。v16 的扩展状态归属于 peer，跨节点不会混用。

## 9. PC 入口

### 9.1 USB CDC

Fusion Master 的主机接口：

```text
VID:PID 2FE3:10F4
Product BioSpur Fusion Master
```

PC 必须按 USB identity 找设备，不可写死 `/dev/ttyACM<n>`。DK 的 CDC TX
ring 是 16,384 bytes，RX ring 是 1,024 bytes：

`B306_Part/host/fusion_master/src/main.c:38-44`

### 9.2 RTT

RTT down-channel 0 接受与 CDC 相同的：

```text
LIST
BSF#### <command>
```

RTT 只作诊断备份。任何 J-Link 工具必须显式选择 SNR `683234364`。

### 9.3 LIST

`LIST` 输出一条 aggregate row 和五条 peer row：

```text
FUSION_LIST count=5 ready=5 scanning=0 capacity=5
FUSION_PEER index=0 name=... connected=1 subscribed=1 ...
...
```

实现：`B306_Part/host/fusion_master/src/main.c:1477-1498`

`subscribed=1` 当前等价于 `bridge_ready=1`。字段名为了兼容旧工具保留为
`subscribed`，它实际还隐含 control handle 已发现。

### 9.4 单节点 host session 过滤

`fusion_session.py` 接受目标 `BSF`。多连接下，它只把目标节点的 UWB、
telemetry、IMU、reply 和 peer row 交给该 session：

`B306_Part/tools/fusion_session.py:445-510`

`ensure_bridge()` 识别 v16 的 `FUSION_PEER` row：

`B306_Part/tools/fusion_session.py:512-548`

这防止一块板的 telemetry 被错误当成另一块板的 sentinel。它不是完整的
五节点容量采集器；容量实验仍需要一个同时保存全部节点并按 `name` 分组的
collector。

## 10. 断线和重连

B306 每块只允许一条 central 连接：

`B306_Part/firmware/prj.conf:32-37`

B306 断线后会清除 subscribed 状态并重新 advertising：

`B306_Part/firmware/src/main.c:1474-1482`

Master 断线后：

1. 打印节点名、地址、reason 和最后计数。
2. unref `bt_conn`。
3. 清空该 peer entry。
4. 恢复扫描。
5. 重新发现任何可用 Fusion node。

Master recovery：
`B306_Part/host/fusion_master/src/main.c:1403-1421`

当前没有 "原节点优先" 的 reconnect policy。若五节点之一掉线，同时出现一块
第六 Fusion node，谁先提供完整 advertising + scan response，谁可能占据
空 entry。正式五板实验通过控制现场设备集合避免这个问题。

连接断开后，per-peer TIMER2 extension 状态会清零。重连后的 IMU low word
必须等待该节点新的 full-width UWB timestamp 或 telemetry wrap count 重新
建立 epoch。

## 11. v14、v15 和 v16 的真实硬件发现

### 11.1 v14

v14 首次把单连接架构扩为五个 peer。它在每次 `connected()` 后立即恢复
扫描，因此五个连接和多个 GATT discovery 同时进行。

现场结果：

```text
count=5
ready=1
```

四个节点在 discovery 中返回 `not_found`。这个结果证明 ACL count 不能代替
bridge-ready 验收。

### 11.2 v15

v15 把 "连接 + GATT discovery" 串行化。第一个节点 ready 后再连接第二个。
第二个及以后仍在 data CCC 的 UUID-filtered discovery 失败。它们被断开并
重试，形成连接 storm。

### 11.3 v16

v16 保留串行 bring-up，并把 data CCC 改成与 telemetry CCC 相同的
descriptor enumeration + UUID validation。

现场结果：

```text
count=5
ready=5
MTU=247
DLE=251
PHY=2M/2M
final CI=50 ms on all five
```

v16 production memory gate：

| 区域 | 使用 | 容量 | 比例 | 门 | 结果 |
|---|---:|---:|---:|---:|---|
| FLASH | 168,348 B | 1,048,576 B | 16.05% | 95% | PASS |
| RAM | 136,892 B | 262,144 B | 52.22% | 85% | PASS |

Host test：17/17 PASS。

## 12. 当前已知限制

### 12.1 五连接不等于五节点满负载通过

本次结果只关闭 "Fusion Master 能否建立并初始化五条 B306 BLE 连接" 这个
问题。IMU N=2 + UWB 的五节点 5 分钟 A/B/C ramp 和 30 分钟 long run 尚未
执行。最终容量仍由 delivered notification、latency、sequence gap、
disconnect 和 drop counter 决定。

### 12.2 超长 telemetry 文本会丢失换行

v16 的 BLE binary telemetry 是完整的 235 bytes，schema 校验也通过。问题
出现在 DK 把 binary record 格式化成 CDC 文本时：

```c
#define CDC_LINE_MAX 1024
char line[CDC_LINE_MAX];
vsnprintf(line, sizeof(line), ...);
```

证据：

- buffer 长度：`B306_Part/host/fusion_master/src/main.c:35`
- 格式化和 CDC ring 写入：
  `B306_Part/host/fusion_master/src/main.c:373-399`
- telemetry 文本字段：
  `B306_Part/host/fusion_master/src/main.c:536-610`

当前 `FUSION_TELEMETRY` 文本超过 1023 个字符。`vsnprintf()` 截断时丢掉
结尾换行，下一条 `FUSION_COMMAND_TX` 或其他记录会直接粘在截断文本后。
现场已经观察到这种形式：

```text
... imu_hwin=...FUSION_COMMAND_TX target=BSF3C79 ...
```

影响范围：

- BLE notification 本身不受影响。
- UWB/IMU binary record 不受影响。
- CDC 上的 telemetry 文本不完整。
- 依赖 "每条记录必须从新行开头开始" 的 host 命令 parser 可能漏掉 reply
  前的 `FUSION_COMMAND_TX`。
- 容量实验若直接使用当前文本 telemetry，无法证明所有 telemetry counter。

正式 ramp 开始前应修复输出封装，或改用不会截断的 binary/length-framed
host transport。扩大栈上 buffer 不是可直接采用的答案，因为 CDC command
thread 只有 2048-byte stack。修复要同时通过 thread stack high-water 和
RAM gate。

### 12.3 共享 Master buffer 的压力尚未测量

ACL RX count 16、log queue 128 和 CDC TX ring 16 KB 都是五连接共享资源。
配置允许 bring-up，不等于它们能承受五节点 N=2 的持续 notification burst。
capacity ramp 的目的正是定位这些共享资源的 knee。

### 12.4 没有应用级公平队列

Fusion Master 当前使用一个共享 FIFO log queue。它不为每个节点预留固定
queue quota。若某一连接产生 burst，它可以先占据多个 FIFO entry。per-peer
drop counter 能指出受害节点，但当前实现不保证每节点公平。

### 12.5 没有 BLE 与 UWB 的共同硬件时隙

`SUPERFRAME_BASE` 统一 UWB global index；它不控制 BLE connection anchor。
BLE latency 测量仍需将每节点 TIMER2 拟合到 host arrival time。不能用相同
50 ms CI 推断五块板已经时间对齐。

## 13. 现场运维判据

五节点连接层验收使用以下顺序：

1. 读取 v16 banner，确认 `max_conn=5`。
2. 等待 `FUSION_LIST count=5 ready=5`。
3. 读取五条 `FUSION_PEER`。
4. 核对 BSF set 与计划的五块板完全一致。
5. 要求每条 peer row：
   `connected=1 subscribed=1 control!=0 phy_tx=2 phy_rx=2`。
6. 逐节点发送只读 `STATUS`，核对 reply 的 `name`。
7. 逐节点发送 `TAG RAW VERSION`，核对 DWM marker 和 BS identity。
8. 记录每条连接的 CI、latency、timeout 和 RSSI。

以下状态必须停止：

```text
count != 5
ready != 5
peer set mismatch
subscribed=0
control=0
unexpected disconnect
malformed > 0
logger_drop > 0
```

`count=5 ready=1` 属于失败，不是 "还差一点"。它表示四条连接不能传输或控制。

## 14. 当前台架端态

- Fusion Master DK `683234364` 运行
  `dk-fusion-imu-relay-v19`。
- 五块 B306 均连接、订阅完成，最终 CI 均为 50 ms。
- 五块 B306 均运行 `b306-imu-relay-v26`。
- 五块 DWM1001C 均通过 relay path 报告
  `tag-fusion-link-v2-relay3`。
- 当前 relay3 上 `CFG_STOP` 仍按坏命令处理；标准停止方式是
  `MODE IDLE` 后完整重配。relay4 增加了 epoch-invalid 明确拒绝，但在
  五板全部完成 relay4 部署和双路径计数器验收前，runbook 不解除该禁令。
- 物理位置映射已由操作者取消，不作为容量实验输入。
- IMU 没有因这次连接验证而启动。
- probe `1050070698` 未触碰，仍属于 Master_Tag。
- nRF54L15 保持 scan-only，不参与连接。
